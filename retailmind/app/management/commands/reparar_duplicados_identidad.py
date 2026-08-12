"""
Repara fichas de Producto que comparten IDENTIDAD: consolida el stock partido
entre ellas en una sola, talla a talla.

Contexto: `unificar_marcas_duplicadas` fusionó opciones de marca gemelas
('champion' / 'Champion'). Al repuntar `Producto.atributo1`, pares de fichas que
antes se distinguían por la grafía de la marca pasaron a compartir la identidad
que define `app/utils_producto_match.py` — articulo normalizado + atributo1 +
atributo2 + atributo3 + categoría + sucursal. La BD no lo impide (Producto no
tiene unique_together) pero el stock queda partido entre dos fichas del MISMO
producto físico, y la recepción escribe en la que `buscar_producto_por_identidad`
devuelve primero.

Usa el MISMO mecanismo que la pantalla /app/existencias/fusion-duplicados/
(`app/views_fusion_duplicados.py`): EGRESO en la ficha vaciada + INGRESO en la
que se conserva, ambos con concepto CORRECCION_STOCK y una `referencia_externa`
común `FUSION-<timestamp>-<A>-<B>`, vía `services.inventario_service` (stock
plano + lotes FIFO + kardex, atómico).

Seguro por diseño:
  - DRY-RUN por defecto: sin --apply NO escribe nada.
  - La ficha que se CONSERVA es la que `buscar_producto_por_identidad` elegiría
    (fecha_creacion desc, id desc). Así el stock queda donde el sistema va a
    seguir escribiendo, en vez de en una ficha que nadie vuelve a tocar.
  - Nunca mueve stock entre sucursales: la sucursal es parte de la identidad,
    así que ambas fichas son siempre de la misma.
  - Solo transfiere tallas con par exacto en la ficha que se conserva. Las que
    no tienen par se reportan y NO se tocan.
  - NO borra fichas ni toca `excluir_de_analitica` salvo que pidas
    --excluir-vaciadas (dejar la ficha fuera de dashboards puede distorsionar
    el histórico si tenía ventas).

Uso:
    python manage.py reparar_duplicados_identidad --marca 88            # dry-run
    python manage.py reparar_duplicados_identidad --marca 88 --apply
    python manage.py reparar_duplicados_identidad --marca 8 --solo H01994
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from app.models import AtributoOpcion, Producto, Producto_Talla, Sucursal
from app.utils_producto_match import normalizar_articulo

RESPONSABLE = 'cmd:reparar_duplicados_identidad'


def _talla(v):
    return (v or '').strip().upper()


def _orden_resolver(p):
    """Misma prioridad que _ORDEN_RECIENTE: fecha desc (nulls last), id desc."""
    f = p['fecha_creacion']
    return (f is not None, f, p['id'])


class Command(BaseCommand):
    help = ('Consolida el stock de fichas de Producto que comparten identidad. '
            'Dry-run por defecto; --apply para escribir.')

    def add_arguments(self, parser):
        parser.add_argument('--marca', type=int, required=True,
                            help='id de AtributoOpcion (marca) a revisar')
        parser.add_argument('--apply', action='store_true',
                            help='Escribe los movimientos (sin esto solo reporta)')
        parser.add_argument('--solo', default=None,
                            help='Limitar a un articulo concreto')
        parser.add_argument('--excluir-vaciadas', action='store_true',
                            dest='excluir_vaciadas',
                            help='Marca excluir_de_analitica=True en las fichas que '
                                 'quedan en cero (OJO: las saca de dashboards)')

    def handle(self, *args, **opts):
        from app.services.inventario_service import egresar, ingresar

        aplicar = opts['apply']
        marca = AtributoOpcion.objects.filter(id=opts['marca']).first()
        if not marca:
            self.stderr.write(self.style.ERROR('No existe AtributoOpcion id=%s'
                                               % opts['marca']))
            return

        fichas = list(Producto.objects.filter(atributo1_id=marca.id).values(
            'id', 'articulo', 'atributo2_id', 'atributo3_id', 'categoria_id',
            'sucursal_id', 'fecha_creacion', 'costo', 'sobreprecio', 'precioventa'))
        grupos = defaultdict(list)
        for p in fichas:
            grupos[(normalizar_articulo(p['articulo']), p['atributo2_id'],
                    p['atributo3_id'], p['categoria_id'], p['sucursal_id'])].append(p)
        dup = {k: v for k, v in grupos.items() if len(v) > 1}
        if opts['solo']:
            objetivo = normalizar_articulo(opts['solo'])
            dup = {k: v for k, v in dup.items() if k[0] == objetivo}

        self.stdout.write('Marca %r (id=%s): %d fichas, %d grupos con identidad duplicada'
                          % (marca.valor, marca.id, len(fichas), len(dup)))
        if not dup:
            self.stdout.write(self.style.SUCCESS('Nada que reparar.'))
            return
        self.stdout.write('Modo: %s\n' % ('APLICAR' if aplicar else 'DRY-RUN'))

        ids = [p['id'] for v in dup.values() for p in v]
        tallas = defaultdict(list)
        for t in Producto_Talla.objects.filter(producto_id__in=ids):
            tallas[t.producto_id].append(t)

        tot_uds = tot_grupos = tot_sinpar = tot_retiradas = 0
        for clave, fis in sorted(dup.items()):
            # Elegir la ficha que se conserva: la que puede ABSORBER más unidades
            # (su set de tallas cubre el stock de las otras). Consolidar hacia la
            # que elige el resolver suena natural, pero si esa ficha no tiene la
            # talla, las unidades quedan varadas: aquí eso pesa más. Empate ->
            # orden del resolver, para que el stock quede donde el sistema escribe.
            fis.sort(key=_orden_resolver, reverse=True)

            def _varadas(cand):
                """Unidades de las OTRAS fichas que no podrían moverse a `cand`
                porque esa talla no existe en ella."""
                propias = {_talla(t.talla) for t in tallas.get(cand['id'], [])}
                return sum((t.stock or 0)
                           for o in fis if o['id'] != cand['id']
                           for t in tallas.get(o['id'], [])
                           if (t.stock or 0) > 0 and _talla(t.talla) not in propias)

            # fis ya viene ordenada como el resolver, así que el índice es su
            # prioridad: empate en varadas -> gana la que el resolver elegiría.
            keep = min(fis, key=lambda c: (_varadas(c), fis.index(c)))
            resto = [p for p in fis if p['id'] != keep['id']]
            t_keep = {_talla(t.talla): t for t in tallas.get(keep['id'], [])}

            movs = []
            sin_par = []
            for b in resto:
                for tb in tallas.get(b['id'], []):
                    if (tb.stock or 0) <= 0:
                        continue
                    ta = t_keep.get(_talla(tb.talla))
                    if ta is None:
                        sin_par.append((b['id'], tb.talla, tb.stock, tb.sku))
                    else:
                        movs.append((b, tb, ta, tb.stock))

            # fichas redundantes que ya están (o quedarán) en cero: son las que
            # se pueden retirar de analítica sin perder nada.
            vacias = [b['id'] for b in resto
                      if sum(t.stock or 0 for t in tallas.get(b['id'], []))
                      == sum(c for bb, _tb, _ta, c in movs if bb['id'] == b['id'])]

            tot_grupos += 1
            self.stdout.write('%s  sucursal=%s' % (clave[0], clave[4]))
            stock_keep = sum(t.stock or 0 for t in tallas.get(keep['id'], []))
            self.stdout.write('   CONSERVA  id=%-7d creado=%-10s stock=%d'
                              % (keep['id'], str(keep['fecha_creacion'])[:10], stock_keep))
            for b in resto:
                stock_b = sum(t.stock or 0 for t in tallas.get(b['id'], []))
                self.stdout.write('   vacia     id=%-7d creado=%-10s stock=%d'
                                  % (b['id'], str(b['fecha_creacion'])[:10], stock_b))
            for b, tb, ta, cant in movs:
                tot_uds += cant
                self.stdout.write('      talla %-6s %3d und  sku %s -> sku %s'
                                  % (tb.talla, cant, tb.sku, ta.sku))
            for pid, tl, st, sku in sin_par:
                tot_sinpar += st
                self.stdout.write(self.style.WARNING(
                    '      talla %-6s %3d und  sku %s SIN PAR en la ficha que se '
                    'conserva -> NO se toca' % (tl, st, sku)))

            if vacias:
                tot_retiradas += len(vacias)
                self.stdout.write('      fichas que quedan en CERO y se pueden retirar: %s%s'
                                  % (vacias, '' if opts['excluir_vaciadas']
                                     else '  (usa --excluir-vaciadas)'))

            if not aplicar:
                continue
            if not movs:
                if vacias and opts['excluir_vaciadas']:
                    Producto.objects.filter(id__in=vacias).update(excluir_de_analitica=True)
                    self.stdout.write(self.style.SUCCESS('      -> retiradas de analitica'))
                continue

            ref = 'FUSION-%s-%s-%s' % (
                timezone.localtime().strftime('%Y%m%d%H%M%S'), keep['id'], resto[0]['id'])
            # la sucursal es parte de la identidad: ambas fichas son de la misma
            sucursal = Sucursal.objects.filter(id=clave[4]).first()
            try:
                with transaction.atomic():
                    for b, tb, ta, cant in movs:
                        egresar(tb, cant, 'CORRECCION_STOCK', RESPONSABLE,
                                sucursal_origen=sucursal,
                                precio_unitario=int(b['precioventa'] or 0),
                                observaciones=('Reparacion duplicado identidad: stock '
                                               'consolidado en ficha %s (sku %s)'
                                               % (keep['id'], ta.sku)),
                                referencia_externa=ref)
                        ingresar(ta, cant, 'CORRECCION_STOCK', RESPONSABLE,
                                 sucursal_destino=sucursal,
                                 costo_unitario=int(keep['costo'] or 0),
                                 sobreprecio_unitario=int(keep['sobreprecio'] or 0),
                                 precio_unitario=int(keep['precioventa'] or 0),
                                 observaciones=('Reparacion duplicado identidad: stock '
                                                'recibido desde ficha %s (sku %s)'
                                                % (b['id'], tb.sku)),
                                 referencia_externa=ref)
                    if opts['excluir_vaciadas']:
                        vaciadas = [b['id'] for b, _tb, _ta, _c in movs]
                        Producto.objects.filter(id__in=vaciadas).update(
                            excluir_de_analitica=True)
                self.stdout.write(self.style.SUCCESS('      -> consolidado  ref=%s' % ref))
            except Exception as e:
                self.stderr.write(self.style.ERROR(
                    '      -> FALLO, grupo revertido: %s' % e))

        self.stdout.write('\n' + '=' * 78)
        cierre = ('%d grupos, %d unidades consolidadas, %d fichas redundantes en cero'
                  % (tot_grupos, tot_uds, tot_retiradas))
        if tot_sinpar:
            cierre += ', %d unidades sin talla par (no tocadas)' % tot_sinpar
        if aplicar:
            self.stdout.write(self.style.SUCCESS('LISTO: ' + cierre))
        else:
            self.stdout.write(self.style.WARNING(
                'DRY-RUN: ' + cierre + '. Ejecuta con --apply para escribir.'))
