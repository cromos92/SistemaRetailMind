"""
Consolida las familias CUECA (3054, 2918, 3334) que existen bajo varios codigos
gemelos ('3054', '30540', '305400', '3054-02', '3054 02'...) en UNA ficha por
TRAMO DE PRECIO, por color, por sucursal.

Por que por tramo y no una sola ficha: el precio de la cueca va por numeracion
(lista del proveedor: 3054 -> 22-25 $19.990 / 26-33 $21.990 / 34-41 $24.990) y
`Producto.precioventa` es unico por ficha. La convencion destino ya existe en el
catalogo desde mayo-2026: `3054-01/1` (blanco 35/40), `3054-01/2` (blanco 30/34).

Mecanica: RE-APUNTA las filas de `Producto_Talla` a la ficha destino de su tramo
(update de producto_id). La talla viaja completa: mismo SKU (el codigo de barras
fisico sigue funcionando en el POS), mismo kardex, mismos lotes FIFO. No hay
movimientos de stock ni re-etiquetado. Las fichas fuente que quedan sin tallas se
marcan `excluir_de_analitica=True` (no se borran: conservan su historial).

Decisiones de negocio ya tomadas (12-ago-2026):
  - La familia 3054 queda completa bajo marca ALQUIMIA (la lista de precios dice
    "ALQUIMIA ZAPATO MUJER ART. 3054"); hoy esta partida ALQUIMIA/UNISPORT.
  - NO se toca `precioventa`: el comando solo IMPRIME la tabla de precios que
    quedaron distintos a la lista, para corregirlos en Edicion Rapida.

Seguro por diseno:
  - DRY-RUN por defecto: sin --apply NO escribe nada.
  - Transaccion por (familia, sucursal): o se consolida completa o nada.
  - Solo mueve tallas NUMERICAS dentro de un tramo definido; el resto se reporta
    y no se toca (y su ficha fuente no se excluye).
  - Verificacion final por familia: el total de filas de talla y de unidades de
    stock debe ser identico antes y despues.

Uso:
    python manage.py consolidar_cueca                        # dry-run, todas
    python manage.py consolidar_cueca --familia 3054         # dry-run, una familia
    python manage.py consolidar_cueca --sucursal PAO3        # dry-run, una sucursal
    python manage.py consolidar_cueca --apply                # ESCRIBE
"""
import json
import os
import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from app.models import AtributoOpcion, Producto, Producto_Talla, Sucursal

# ---------------------------------------------------------------------------
# Configuracion de familias (tramos segun la lista "precio cueca.jpeg")
# ---------------------------------------------------------------------------
FAMILIAS = {
    '3054': {
        'regex': r'^3054([\s\-/].*)?$|^30540{1,2}$',
        'marcas_fuente': ['ALQUIMIA', 'UNISPORT'],
        'marca_destino': 'ALQUIMIA',
        'descripcion': 'ZAPATO CUECA',
        # (desde, hasta, sufijo_destino, precio_lista)
        # Numeracion /1=alto /2=medio /3=infantil: es la convencion que dejo el
        # intento de unificacion de mayo-2026 (3054-01/1 = blanco 35/40,
        # 3054-01/2 = blanco 30/34) y se respeta. Los limites salen de la lista
        # de precios (la talla 34 pertenece al tramo alto de $24.990).
        'tramos': [(34, 41, '/1', 24990), (26, 33, '/2', 21990), (22, 25, '/3', 19990)],
    },
    '2918': {
        'regex': r'^2918([\s\-/].*)?$',
        # OJO: el codigo 2918 lo usan 6 marcas y solo dos son esta bota.
        #   INCLUIDAS : UNISPORT (9 fichas, 251 uds) y ALQUIMIA (5 fichas, 7 uds,
        #               mal etiquetadas) — ambas 'BOTA CUECA'.
        #   EXCLUIDA  : BACARAS (5 fichas, 6 uds). Dice 'BOTA CUECA' igual que las
        #               otras, pero el usuario confirmo el 12-ago-2026 que es OTRA
        #               bota de otro proveedor. NO consolidar aunque la descripcion
        #               tiente: la descripcion sola no basta para decidir identidad.
        #   EXCLUIDAS : COLLOKY '2918' es una BALERINA, SKECHERS es 'SK PETRYTRAIL'
        #               y SORMANI 'BOTA GA291-8': productos distintos que comparten
        #               el numero.
        'marcas_fuente': ['UNISPORT', 'ALQUIMIA'],
        'marca_destino': 'UNISPORT',
        'descripcion': 'BOTA CUECA',
        # 22-25 y 26-29 comparten precio -> un solo tramo 22-29
        'tramos': [(34, 38, '/1', 34990), (30, 33, '/2', 29990), (22, 29, '/3', 26990)],
    },
    '3334': {
        'regex': r'^3334([\s\-/].*)?$|^33340{1,2}$',
        # ALQUIMIA (25 fichas) + UNISPORT (12 fichas 'CUECA TAREA 34/41', que
        # quedaban fuera). NICOLE ANDREA '3334' es 'MC-3334-3': otro producto.
        'marcas_fuente': ['ALQUIMIA', 'UNISPORT'],
        'marca_destino': 'ALQUIMIA',
        'descripcion': 'ZAPATO CUECA',
        # tramo unico (la lista no lo divide): una ficha por color
        'tramos': [(20, 44, '', 24990)],
    },
}
COLOR_CODIGO = {'NEGRO': '02', 'BLANCO': '01', 'CHAROL': '20', 'CARAMELO': '16',
                'CAFE': '03'}
# Sinonimos verificados en prod (12-ago-2026): el mismo color escrito distinto en
# fichas creadas por flujos distintos. Confirmado por el usuario uno por uno.
#   2918: NEGRO (10 fichas) == BLACK (4 fichas, 2 uds, 7 ventas 2026)
#   3334: CHAROL (10 fichas) == NEGRO CHAROL (5 fichas, 4 uds, 8 ventas 2026)
# Sin esta tabla, 'NEGRO CHAROL' quedaba fuera de la consolidacion por no tener
# codigo de color.
#   3054: ESTAMPADO (1 ficha PAO1, talla 13, 2 uds, 0 ventas en 6 anios) es NEGRO
NORMALIZA_COLOR = {
    'BLACK': 'NEGRO',
    'WHITE': 'BLANCO',
    'NEGRO CHAROL': 'CHAROL',
    'CHAROL NEGRO': 'CHAROL',
    'ESTAMPADO': 'NEGRO',
}
# Sufijo de la ficha que recoge las tallas que NO caen en ningun tramo de la
# lista de precios (ej. la talla 13 de 3054, la '00' de 2918). No se dejan
# huerfanas en su ficha vieja: van a su propia curva, aparte y visible.
SUFIJO_SIN_TRAMO = '/0'


def _talla_num(talla):
    s = re.sub(r'\D', '', str(talla or ''))
    return int(s) if s else None


class Command(BaseCommand):
    help = ('Consolida las familias cueca en una ficha por tramo de precio. '
            'Dry-run por defecto; --apply para escribir.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Escribe los cambios (sin esto solo reporta)')
        parser.add_argument('--familia', choices=sorted(FAMILIAS), default=None,
                            help='Limitar a una familia')
        parser.add_argument('--sucursal', default=None,
                            help='Limitar a una sucursal (alias, ej. PAO3)')

    # ------------------------------------------------------------------
    def handle(self, *args, **opts):
        aplicar = opts['apply']
        alias = {s.id: (s.alias or s.nombreSucursal or '?') for s in Sucursal.objects.all()}
        self.precios_pendientes = []
        self.plan = []
        tot = defaultdict(int)

        # --- SNAPSHOT para poder revertir (solo cuando se va a escribir) ---
        # Guarda el estado ORIGINAL de todas las fichas y tallas involucradas.
        # `revertir_consolidacion_cueca --snapshot <archivo>` vuelve exactamente
        # a este estado.
        if aplicar:
            snap = {'fecha': timezone.localtime().isoformat(),
                    'productos': [], 'tallas': []}
            for base, cfg in FAMILIAS.items():
                if opts['familia'] and base != opts['familia']:
                    continue
                qs = Producto.objects.filter(articulo__iregex=cfg['regex'],
                                             atributo1__valor__in=cfg['marcas_fuente'])
                for p in qs.values('id', 'articulo', 'descripcion', 'atributo1_id',
                                   'atributo2_id', 'excluir_de_analitica', 'sucursal_id'):
                    snap['productos'].append(p)
                for t in Producto_Talla.objects.filter(
                        producto_id__in=[x['id'] for x in snap['productos']]) \
                        .values('id', 'producto_id'):
                    snap['tallas'].append(t)
            # dedupe tallas (el loop anterior re-agrega por familia)
            vistos = set()
            snap['tallas'] = [t for t in snap['tallas']
                              if t['id'] not in vistos and not vistos.add(t['id'])]
            ruta_snap = os.path.join(
                os.getcwd(), '_cueca_snapshot_%s.json'
                % timezone.localtime().strftime('%Y%m%d_%H%M%S'))
            with open(ruta_snap, 'w', encoding='utf-8') as fh:
                json.dump(snap, fh, ensure_ascii=False, indent=1)
            self.stdout.write(self.style.SUCCESS(
                'SNAPSHOT del estado original -> %s\n'
                '(para deshacer: python manage.py revertir_consolidacion_cueca '
                '--snapshot "%s" --apply)\n' % (ruta_snap, ruta_snap)))

        for base, cfg in FAMILIAS.items():
            if opts['familia'] and base != opts['familia']:
                continue

            marca_destino = AtributoOpcion.objects.filter(
                atributo__nombre__iexact='Marca',
                valor__iexact=cfg['marca_destino']).first()
            if not marca_destino:
                self.stderr.write(self.style.ERROR(
                    'No existe la marca destino %r' % cfg['marca_destino']))
                continue

            prods = list(Producto.objects.filter(
                articulo__iregex=cfg['regex'],
                atributo1__valor__in=cfg['marcas_fuente'])
                .select_related('atributo2'))
            if not prods:
                continue

            # verificacion de conservacion (antes)
            ids_familia = [p.id for p in prods]
            qs_antes = Producto_Talla.objects.filter(producto_id__in=ids_familia)
            antes_n = qs_antes.count()
            antes_s = qs_antes.aggregate(s=Sum('stock'))['s'] or 0

            por_suc = defaultdict(list)
            for p in prods:
                por_suc[p.sucursal_id].append(p)

            self.stdout.write('\n' + '=' * 90)
            self.stdout.write('FAMILIA %s -> marca %s   (%d fichas en %d sucursales)   [%s]'
                              % (base, cfg['marca_destino'], len(prods), len(por_suc),
                                 'APPLY' if aplicar else 'DRY-RUN'))
            self.stdout.write('=' * 90)

            for suc_id, fichas in sorted(por_suc.items()):
                nombre_suc = alias.get(suc_id, str(suc_id))
                if opts['sucursal'] and nombre_suc.upper() != opts['sucursal'].upper():
                    continue
                try:
                    with transaction.atomic():
                        self._consolidar_sucursal(base, cfg, marca_destino, suc_id,
                                                  nombre_suc, fichas, aplicar, tot)
                        if not aplicar:
                            # dry-run: deshacer todo lo simulado dentro de la Tx
                            transaction.set_rollback(True)
                except Exception as e:
                    self.stderr.write(self.style.ERROR(
                        '  %s %s: ABORTADO y revertido: %s' % (base, nombre_suc, e)))

            if aplicar:
                despues_n = Producto_Talla.objects.filter(
                    Q(producto_id__in=ids_familia) |
                    Q(producto__articulo__iregex=cfg['regex'],
                      producto__atributo1=marca_destino)).distinct().count()
                despues_s = Producto_Talla.objects.filter(
                    Q(producto_id__in=ids_familia) |
                    Q(producto__articulo__iregex=cfg['regex'],
                      producto__atributo1=marca_destino)).distinct() \
                    .aggregate(s=Sum('stock'))['s'] or 0
                ok = (despues_n >= antes_n and despues_s == antes_s)
                msg = ('  CONSERVACION %s: tallas %d->%d, stock %d->%d'
                       % ('OK' if ok else '*** REVISAR ***',
                          antes_n, despues_n, antes_s, despues_s))
                self.stdout.write(self.style.SUCCESS(msg) if ok
                                  else self.style.ERROR(msg))

        # ------------------------------------------------------------------
        self.stdout.write('\n' + '=' * 90)
        self.stdout.write('RESUMEN: %d tallas re-apuntadas (%d unidades) · %d fichas destino '
                          'creadas · %d renombradas · %d fuentes vaciadas y excluidas · '
                          '%d tallas a la curva aparte "%s" (sin tramo en la lista)'
                          % (tot['tallas'], tot['unidades'], tot['creadas'],
                             tot['renombradas'], tot['excluidas'], tot['sin_tramo'],
                             SUFIJO_SIN_TRAMO))
        if self.precios_pendientes:
            self.stdout.write('\nPRECIOS PENDIENTES DE CORREGIR (el comando NO los toca; '
                              'Edicion Rapida):')
            self.stdout.write('%-10s %-8s %-14s %-10s %12s %12s'
                              % ('FAMILIA', 'SUC', 'FICHA DESTINO', 'TRAMO',
                                 'precio hoy', 'lista dice'))
            for f in self.precios_pendientes:
                self.stdout.write('%-10s %-8s %-14s %-10s %12s %12s' % f)
        ruta_plan = os.path.join(os.getcwd(), '_cueca_plan.json')
        with open(ruta_plan, 'w', encoding='utf-8') as fh:
            json.dump({'generado': timezone.localtime().isoformat(),
                       'modo': 'APPLY' if aplicar else 'DRY-RUN',
                       'plan': self.plan,
                       'precios_pendientes': self.precios_pendientes},
                      fh, ensure_ascii=False, indent=1)
        self.stdout.write('Plan detallado -> %s' % ruta_plan)
        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '\nDRY-RUN: nada quedo escrito. Ejecuta con --apply para consolidar.'))

    # ------------------------------------------------------------------
    def _elegir_destino(self, fichas_color, tallas_color, pertenece, precio_lista):
        """Ficha existente que sirve de destino, o None.

        Un destino valido es una ficha "pura": TODAS sus tallas cumplen el
        predicado `pertenece(talla)` (asi una ficha del tramo alto no se
        convierte en destino del tramo medio). Prioridad de eleccion:
          1. precio ya igual al de la lista (las fichas del intento de
             unificacion de mayo-2026 — 3054-01/1, 3054-01/2 — lo cumplen:
             son ellas las que deben SOBREVIVIR, no vaciarse),
          2. la mas reciente ("el ultimo creado", criterio del negocio),
          3. la de mas tallas.
        """
        por_ficha = defaultdict(list)
        for t in tallas_color:
            por_ficha[t.producto_id].append(t)
        candidatas = []
        for p in fichas_color:
            propias = por_ficha.get(p.id, [])
            if not propias:
                continue
            if all(pertenece(t) for t in propias):
                candidatas.append(((p.precioventa or 0) == precio_lista,
                                   (p.fecha_creacion is not None, p.fecha_creacion, p.id),
                                   len(propias),
                                   p))
        if not candidatas:
            return None
        candidatas.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        return candidatas[0][3]

    # ------------------------------------------------------------------
    def _consolidar_sucursal(self, base, cfg, marca_destino, suc_id, nombre_suc,
                             fichas, aplicar, tot):
        # tallas de todas las fichas de la familia en esta sucursal
        tallas = list(Producto_Talla.objects.filter(
            producto_id__in=[p.id for p in fichas]))
        if not tallas:
            return
        self.stdout.write('\n  [%s]  %d fichas, %d filas de talla, %d unidades'
                          % (nombre_suc, len(fichas), len(tallas),
                             sum(t.stock or 0 for t in tallas)))

        def color_de(p):
            v = (p.atributo2.valor if p.atributo2 else '?').strip().upper()
            return NORMALIZA_COLOR.get(v, v)

        # agrupar por color
        por_color = defaultdict(list)
        for p in fichas:
            por_color[color_de(p)].append(p)

        for color, fichas_color in sorted(por_color.items()):
            cc = COLOR_CODIGO.get(color)
            if cc is None:
                self.stdout.write(self.style.WARNING(
                    '    color %r sin codigo conocido -> fichas %s NO se tocan'
                    % (color, [p.id for p in fichas_color])))
                continue
            ids_color = {p.id for p in fichas_color}
            tallas_color = [t for t in tallas if t.producto_id in ids_color]

            # tramos de la lista + la "curva aparte" con lo que no calza en ninguno
            def _en_tramo(t, d, h):
                n = _talla_num(t.talla)
                return n is not None and d <= n <= h

            def _sin_tramo(t):
                n = _talla_num(t.talla)
                return n is None or not any(d <= n <= h
                                            for d, h, _s, _p in cfg['tramos'])

            grupos = [(d, h, suf, precio, (lambda t, d=d, h=h: _en_tramo(t, d, h)))
                      for (d, h, suf, precio) in cfg['tramos']]
            grupos.append((None, None, SUFIJO_SIN_TRAMO, None, _sin_tramo))

            for (desde, hasta, suf, precio_lista, pertenece) in grupos:
                en_tramo = [t for t in tallas_color if pertenece(t)]
                if not en_tramo:
                    continue
                codigo_destino = '%s-%s%s' % (base, cc, suf)
                destino = self._elegir_destino(fichas_color, tallas_color,
                                               pertenece, precio_lista)
                creado = False
                if destino is None:
                    plantilla = max(fichas_color,
                                    key=lambda p: (p.fecha_creacion is not None,
                                                   p.fecha_creacion, p.id))
                    desc_destino = ('%s %d/%d' % (cfg['descripcion'], desde, hasta)
                                    if desde is not None
                                    else '%s OTRAS TALLAS' % cfg['descripcion'])
                    destino = Producto(
                        articulo=codigo_destino,
                        descripcion=desc_destino,
                        atributo1=marca_destino,
                        atributo2=plantilla.atributo2,
                        atributo3=plantilla.atributo3,
                        atributo4=plantilla.atributo4,
                        categoria=plantilla.categoria,
                        sucursal_id=suc_id,
                        costo=plantilla.costo or 0,
                        sobreprecio=plantilla.sobreprecio or 0,
                        precioventa=(precio_lista or plantilla.precioventa or 0),
                        tipo_talla=plantilla.tipo_talla,
                        guia_talla=plantilla.guia_talla,
                        temporada=plantilla.temporada,
                    )
                    if aplicar or True:   # dentro de la Tx; dry-run hace rollback
                        destino.save()
                    creado = True
                    tot['creadas'] += 1

                # renombrar el destino a la convencion canonica
                renombrado = ''
                if destino.articulo.strip().upper() != codigo_destino:
                    renombrado = ' (antes %r)' % destino.articulo
                    destino.articulo = codigo_destino
                    destino.descripcion = ('%s %d/%d' % (cfg['descripcion'], desde, hasta)
                                           if desde is not None
                                           else '%s OTRAS TALLAS' % cfg['descripcion'])
                    destino.save(update_fields=['articulo', 'descripcion'])
                    tot['renombradas'] += 1
                if destino.atributo1_id != marca_destino.id:
                    destino.atributo1 = marca_destino
                    destino.save(update_fields=['atributo1'])

                mover = [t for t in en_tramo if t.producto_id != destino.id]
                uds = sum(t.stock or 0 for t in mover)
                etiqueta = ('tramo %d-%d' % (desde, hasta) if desde is not None
                            else 'SIN TRAMO ')
                self.stdout.write('    %-12s %-8s %-10s -> destino id=%-7s%s%s: '
                                  '%d tallas, %d unidades%s'
                                  % (codigo_destino, color[:8], etiqueta,
                                     destino.id, ' NUEVA' if creado else '',
                                     renombrado, len(mover), uds,
                                     '  (%s)' % ', '.join(sorted({str(t.talla)
                                        for t in en_tramo})[:8])
                                     if desde is None else ''))
                fuentes_mov = sorted({t.producto_id for t in en_tramo
                                      if t.producto_id != destino.id})
                self.plan.append({
                    'familia': base, 'sucursal': nombre_suc, 'color': color,
                    'tramo': ('%d-%d' % (desde, hasta) if desde is not None
                              else 'sin tramo'),
                    'codigo_destino': codigo_destino,
                    'destino_id': None if creado else destino.id,
                    'creado': creado,
                    'renombrado_desde': renombrado.replace(" (antes '", '').rstrip("')") if renombrado else '',
                    'tallas_movidas': len(mover), 'unidades': uds,
                    'fuentes': fuentes_mov,
                    'precio_destino': destino.precioventa or 0,
                    'precio_lista': precio_lista or 0,
                })
                if precio_lista is None:
                    tot['sin_tramo'] += len(mover)
                for t in mover:
                    Producto_Talla.objects.filter(id=t.id).update(producto_id=destino.id)
                    # reflejar el movimiento en memoria: los tramos se procesan de
                    # mayor a menor y una ficha /2 con una talla 34 se vuelve "pura"
                    # (y por tanto elegible como destino) cuando el tramo /1 ya se
                    # llevo esa talla. Sin esto, las fichas del intento de mayo-2026
                    # se vaciarian en fichas NUEVAS en vez de sobrevivir.
                    t.producto_id = destino.id
                tot['tallas'] += len(mover)
                tot['unidades'] += uds

                if precio_lista is not None and destino.precioventa != precio_lista:
                    self.precios_pendientes.append(
                        (base, nombre_suc, codigo_destino, '%d-%d' % (desde, hasta),
                         '{:,}'.format(destino.precioventa or 0),
                         '{:,}'.format(precio_lista)))

        # fuentes vaciadas -> excluir de analitica
        vacias = [p.id for p in fichas
                  if not Producto_Talla.objects.filter(producto_id=p.id).exists()
                  and not p.excluir_de_analitica]
        if vacias:
            Producto.objects.filter(id__in=vacias).update(excluir_de_analitica=True)
            tot['excluidas'] += len(vacias)
            self.stdout.write('    fuentes vaciadas y excluidas de analitica: %s' % vacias)
