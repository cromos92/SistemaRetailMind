# -*- coding: utf-8 -*-
"""
Recalcula el avance de las líneas de compra (Compras_Producto_Talla) desde su
fuente de verdad: las recepciones (Productos_Recepcionados.stockArribado).

Por qué existe: el flujo normal de recepción (guardar_recepcion) nunca escribió
`unidades_recibidas` ni `estado_item`; solo lo hacen Compra Manual y la
vinculación retroactiva. Resultado en la copia local: 851 líneas con
recepciones y `unidades_recibidas = 0`, todas en 'pendiente'. Lo leen:
  - la predicción de compras (tránsito = stock - unidades_recibidas),
  - reportes/dashboard de compras que suman `unidades_recibidas`.

Reglas (idempotente; se puede volver a correr para re-sincronizar):
  - Líneas SIN producto_talla (flujo normal de recepción): espejo exacto,
    unidades_recibidas = min(stock, suma de recepciones) y estado_item
    según ese total (pendiente / recibido_parcial / recibido_completo). PUEDE
    BAJAR: si se borraron recepciones pendientes después de un --apply, una
    nueva corrida lo corrige. eliminar_compra y la pestaña Pendientes NO usan
    el campo en estas líneas (leen las recepciones), así que el valor escrito
    aquí nunca bloquea ni oculta nada.
  - Líneas CON producto_talla (Compra Manual / vinculación retroactiva, que
    escriben el campo a propósito y cuyos lectores lo toman como piso): nunca
    baja, y solo sube hasta las recepciones YA CREADAS como producto
    (las pendientes se pueden borrar desde «Editar recepciones»; las creadas
    solo con «Revertir a pendiente», que ya descuenta el campo).
  - No toca 'cancelado' ni líneas con stock <= 0.
  - Con --vincular-producto: enlaza producto_talla solo si la línea no tiene
    recepciones pendientes de crear y TODAS sus recepciones creadas apuntan
    al mismo Producto_Talla.

Por defecto es DRY-RUN (solo reporta). Para escribir: --apply.
Ojo: guardar_recepcion y la edición de recepciones no mantienen el campo, así
que en las líneas sin SKU el espejo se desactualiza con recepciones nuevas o
borradas hasta la próxima corrida (solo lo leen reportes/dashboard).

Uso:
    python manage.py compras_recalcular_avance                    # reporte
    python manage.py compras_recalcular_avance --compra 14        # una compra
    python manage.py compras_recalcular_avance --estados ACTIVA   # solo activas
    python manage.py compras_recalcular_avance --apply            # escribe
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q, Sum

from app.models import Compras_Producto_Talla, Productos_Recepcionados


_RANGO_ESTADO = {'pendiente': 0, 'recibido_parcial': 1, 'recibido_completo': 2}


def _estado_para(recibido, stock):
    if recibido <= 0:
        return 'pendiente'
    if stock and recibido >= stock:
        return 'recibido_completo'
    return 'recibido_parcial'


class Command(BaseCommand):
    help = (
        'Recalcula unidades_recibidas/estado_item de las líneas de compra desde '
        'las recepciones. DRY-RUN por defecto; --apply para escribir.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--compra', type=int, default=None,
                            help='Solo esta compra (id).')
        parser.add_argument('--estados', default='ACTIVA,ELIMINADA,COMPLETADA',
                            help='Estados de compra a revisar, separados por coma '
                                 '(default: ACTIVA,ELIMINADA,COMPLETADA).')
        parser.add_argument('--vincular-producto', action='store_true',
                            help='Además enlaza producto_talla cuando es único.')
        parser.add_argument('--apply', action='store_true',
                            help='Escribe los cambios (sin esto es dry-run).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Explícito: solo reporta (es el comportamiento por defecto).')
        parser.add_argument('--detalle', type=int, default=20,
                            help='Cuántas líneas de ejemplo mostrar (default 20).')

    def handle(self, *args, **opts):
        aplicar = bool(opts['apply']) and not opts['dry_run']
        estados = [e.strip().upper() for e in (opts['estados'] or '').split(',') if e.strip()]

        cpts = Compras_Producto_Talla.objects.select_related('compra_producto__compras')
        if opts['compra']:
            cpts = cpts.filter(compra_producto__compras_id=opts['compra'])
        elif estados:
            cpts = cpts.filter(compra_producto__compras__estado__in=estados)
        cpts = cpts.exclude(estado_item='cancelado')

        ids = list(cpts.values_list('id', flat=True))
        recibido = {}
        recibido_creado = {}
        for row in (
            Productos_Recepcionados.objects
            .filter(compra_producto_talla_id__in=ids)
            .order_by()
            .values('compra_producto_talla_id')
            .annotate(s=Sum('stockArribado'),
                      c=Sum('stockArribado', filter=Q(producto_talla__isnull=False)))
        ):
            recibido[row['compra_producto_talla_id']] = int(row['s'] or 0)
            recibido_creado[row['compra_producto_talla_id']] = int(row['c'] or 0)
        productos_por_cpt = defaultdict(set)
        con_pendientes = set()
        if opts['vincular_producto']:
            for cpt_id, pt_id in (
                Productos_Recepcionados.objects
                .filter(compra_producto_talla_id__in=ids, producto_talla__isnull=False)
                .values_list('compra_producto_talla_id', 'producto_talla_id')
            ):
                productos_por_cpt[cpt_id].add(pt_id)
            con_pendientes = set(
                Productos_Recepcionados.objects
                .filter(compra_producto_talla_id__in=ids, producto_talla__isnull=True)
                .values_list('compra_producto_talla_id', flat=True)
            )

        cambios = []
        por_compra = defaultdict(lambda: {'lineas': 0, 'uds_antes': 0, 'uds_despues': 0})
        ambiguos = 0
        for cpt in cpts.iterator(chunk_size=2000):
            stock = int(cpt.stock or 0)
            ur_actual = int(cpt.unidades_recibidas or 0)
            rec = int(recibido.get(cpt.id) or 0)
            pt_nuevo = None
            if opts['vincular_producto'] and cpt.producto_talla_id is None:
                pts = productos_por_cpt.get(cpt.id) or set()
                if len(pts) == 1 and cpt.id not in con_pendientes:
                    pt_nuevo = next(iter(pts))
                elif len(pts) > 1:
                    ambiguos += 1
            enlazada = cpt.producto_talla_id is not None or pt_nuevo is not None
            if stock <= 0:
                # Líneas de stock 0 (p. ej. Compra Manual marcadas
                # 'recibido_completo'): no se tocan.
                ur_nuevo, estado_nuevo = ur_actual, cpt.estado_item
            elif enlazada:
                # Piso legacy: nunca baja y solo cuenta lo ya creado.
                ur_nuevo = max(ur_actual, min(stock, int(recibido_creado.get(cpt.id) or 0)))
                estado_calc = _estado_para(ur_nuevo, stock)
                if _RANGO_ESTADO.get(estado_calc, 0) > _RANGO_ESTADO.get(cpt.estado_item, 0):
                    estado_nuevo = estado_calc
                else:
                    estado_nuevo = cpt.estado_item
            else:
                # Espejo exacto de las recepciones (puede bajar).
                ur_nuevo = min(stock, rec)
                estado_nuevo = _estado_para(ur_nuevo, stock)
            if ur_nuevo != ur_actual or estado_nuevo != cpt.estado_item or pt_nuevo:
                cambios.append((cpt, ur_actual, ur_nuevo, estado_nuevo, pt_nuevo, rec))
                c = por_compra[(cpt.compra_producto.compras_id, cpt.compra_producto.compras.estado,
                                cpt.compra_producto.compras.nombre)]
                c['lineas'] += 1
                c['uds_antes'] += ur_actual
                c['uds_despues'] += ur_nuevo

        modo = 'APLICANDO' if aplicar else 'DRY-RUN (no se escribe nada)'
        self.stdout.write(self.style.WARNING(f'== compras_recalcular_avance: {modo}'))
        self.stdout.write(f'Líneas revisadas: {len(ids)} · con cambios: {len(cambios)}')
        if opts['vincular_producto']:
            self.stdout.write(f'Líneas con más de un producto creado (no se enlazan): {ambiguos}')
        self.stdout.write('Por compra (id, estado, nombre): líneas · unidades_recibidas antes -> después')
        for (cid, est, nom), c in sorted(por_compra.items()):
            self.stdout.write(
                f'  #{cid} {est:<10} {str(nom)[:40]:<40} {c["lineas"]:>5} · '
                f'{c["uds_antes"]:>6} -> {c["uds_despues"]:>6}')
        for cpt, ur_a, ur_n, est_n, pt_n, rec in cambios[:opts['detalle']]:
            self.stdout.write(
                f'    cpt {cpt.id} compra #{cpt.compra_producto.compras_id} '
                f'{cpt.compra_producto.nombre} T{cpt.talla}: stock {cpt.stock}, recep {rec}, '
                f'ur {ur_a}->{ur_n}, estado {cpt.estado_item}->{est_n}'
                + (f', producto_talla -> {pt_n}' if pt_n else ''))
        exceso = sum(1 for cpt, _a, _n, _e, _p, rec in cambios if rec > int(cpt.stock or 0))
        if exceso:
            self.stdout.write(self.style.WARNING(
                f'Líneas con MÁS recepcionado que comprado (se topa en el stock): {exceso}'))

        if not aplicar:
            self.stdout.write(self.style.SUCCESS('Dry-run terminado. Usa --apply para escribir.'))
            return

        with transaction.atomic():
            for cpt, _ur_a, ur_n, est_n, pt_n, _rec in cambios:
                campos = {'unidades_recibidas': ur_n, 'estado_item': est_n}
                if pt_n:
                    campos['producto_talla_id'] = pt_n
                # .update(): no dispara el post_save que re-guarda la compra.
                Compras_Producto_Talla.objects.filter(id=cpt.id).update(**campos)
        self.stdout.write(self.style.SUCCESS(f'Actualizadas {len(cambios)} líneas.'))
