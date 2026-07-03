"""
Reconcilia los lotes FIFO (LoteProducto.cantidad_disponible) para que su saldo
total por talla cuadre con el stock plano (Producto_Talla.stock), tratando el
stock plano como la fuente de verdad.

Casos:
  A. stock>0 y SIN lotes activos   → crea 1 lote con cantidad = stock.
  B. saldo_lotes > stock (inflado) → baja cantidad_disponible empezando por el
     lote MÁS NUEVO (preserva la antigüedad FIFO). Lote en 0 queda agotado.
  C. saldo_lotes < stock (corto)   → crea un lote complementario.

v2 (optimizada para BD remota):
  - Una sola query de clasificación (values + annotate), sin N+1.
  - Casos A/C se crean con bulk_create por bloques (1 round-trip por bloque).
  - Caso B procesa BLOQUES de tallas: un select_for_update + un bulk_update
    por bloque dentro de una transacción corta (~200 tallas por commit).
  - Progreso visible por bloque con acumulados y tiempo transcurrido.
  - Sigue siendo idempotente y re-ejecutable: si se corta, lo aplicado queda
    firme y la siguiente corrida procesa solo lo que siga descuadrado.

NO toca LoteProducto.fecha_ingreso (la antigüedad que consume el ecommerce se
calcula desde Movimientos_Producto, no desde los lotes).

Nota de consistencia: el snapshot de descuadres se toma al inicio; ventas que
ocurran durante la corrida pueden dejar un puñado de tallas nuevamente
descuadradas — la verificación final (`reconciliar_stock_lotes`) las muestra y
basta re-ejecutar.

Uso:
    python manage.py reconciliar_lotes_a_stock                 # dry-run
    python manage.py reconciliar_lotes_a_stock --sucursal 5    # una sucursal
    python manage.py reconciliar_lotes_a_stock --limit 200     # prueba 200 PT
    python manage.py reconciliar_lotes_a_stock --solo-inflados # solo caso B
    python manage.py reconciliar_lotes_a_stock --apply         # aplica
"""

import logging
import time
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F, IntegerField, Q, Sum, Value
from django.db.models.functions import Coalesce

from app.models import LoteProducto, Producto_Talla

logger = logging.getLogger('app')

BATCH_B = 200        # tallas del caso B por transacción (locks cortos)
BATCH_CREATE = 1000  # lotes nuevos por bulk_create


class Command(BaseCommand):
    help = (
        'Reconcilia el saldo de lotes FIFO al stock plano (Producto_Talla.stock '
        'como fuente de verdad). Por defecto dry-run; usa --apply para escribir.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Aplicar cambios. Sin este flag corre en dry-run.')
        parser.add_argument('--sucursal', type=int, default=None,
                            help='Procesar solo tallas de esta sucursal.')
        parser.add_argument('--limit', type=int, default=None,
                            help='Procesar solo las primeras N tallas con descuadre.')
        parser.add_argument('--solo-inflados', action='store_true',
                            help='Procesar solo el caso B (saldo_lotes > stock).')
        parser.add_argument('--solo-faltantes', action='store_true',
                            help='Procesar solo el caso A (stock>0 sin lotes).')
        parser.add_argument('--mostrar-muestras', type=int, default=20,
                            help='Cantidad de muestras en el reporte. Default: 20.')

    def handle(self, *args, **opts):
        apply_changes = opts['apply']
        t0 = time.monotonic()

        if opts['solo_inflados'] and opts['solo_faltantes']:
            self.stderr.write(self.style.ERROR(
                'No uses --solo-inflados y --solo-faltantes a la vez.'))
            return

        modo = 'APPLY' if apply_changes else 'DRY-RUN'
        self.stdout.write(self.style.WARNING(
            f'\n=== Reconciliación lotes FIFO → stock plano (modo {modo}, v2 batch) ==='))
        self.stdout.flush()

        # ── [1] Clasificación en UNA query (sin tocar nada) ──
        qs = Producto_Talla.objects.annotate(
            saldo_lotes=Coalesce(
                Sum('lotes__cantidad_disponible', filter=Q(lotes__activo=True)),
                Value(0), output_field=IntegerField(),
            )
        )
        if opts['sucursal']:
            qs = qs.filter(producto__sucursal_id=opts['sucursal'])
        qs = qs.exclude(stock=F('saldo_lotes')).values(
            'id', 'sku', 'talla', 'stock', 'saldo_lotes',
            'producto__costo', 'producto__sobreprecio', 'producto__precioventa',
            'producto__articulo', 'producto__sucursal__alias',
        )
        if opts['limit']:
            qs = qs[:opts['limit']]

        caso_a, caso_b, caso_c = [], [], []
        for fila in qs.iterator(chunk_size=5000):
            stock = fila['stock'] or 0
            saldo = fila['saldo_lotes'] or 0
            if saldo == stock:
                continue
            if saldo == 0 and stock > 0:
                caso_a.append(fila)
            elif saldo > stock:
                caso_b.append(fila)      # incluye stock negativo: baja lotes a 0
            else:
                caso_c.append(fila)      # 0 <= saldo < stock

        if opts['solo_inflados']:
            caso_a, caso_c = [], []
        if opts['solo_faltantes']:
            caso_b, caso_c = [], []

        self.stdout.write(
            f'  Clasificadas en {time.monotonic() - t0:.0f}s → '
            f'A(crear): {len(caso_a):,} · B(bajar): {len(caso_b):,} · '
            f'C(completar): {len(caso_c):,}')
        self.stdout.flush()

        n_muestras = opts['mostrar_muestras']
        muestras = []
        sin_costo = 0
        u_creadas = u_bajadas = u_completadas = 0
        lotes_nuevos = lotes_tocados = lotes_agotados = 0

        def _elapsed():
            s = int(time.monotonic() - t0)
            return f'{s // 60:02d}:{s % 60:02d}'

        # ── [2] Casos A y C: crear lotes con bulk_create por bloques ──
        pendientes_bulk = []

        def _flush_bulk():
            nonlocal lotes_nuevos
            if pendientes_bulk and apply_changes:
                LoteProducto.objects.bulk_create(pendientes_bulk, batch_size=BATCH_CREATE)
                lotes_nuevos += len(pendientes_bulk)
            pendientes_bulk.clear()

        for etiqueta, filas in (('A crear', caso_a), ('C completar', caso_c)):
            for i, fila in enumerate(filas, 1):
                stock = fila['stock'] or 0
                saldo = fila['saldo_lotes'] or 0
                cantidad = stock if etiqueta == 'A crear' else stock - saldo
                if (fila['producto__costo'] or 0) == 0:
                    sin_costo += 1
                if apply_changes:
                    pendientes_bulk.append(LoteProducto(
                        producto_talla_id=fila['id'],
                        cantidad_inicial=cantidad,
                        cantidad_disponible=cantidad,
                        costo_unitario=fila['producto__costo'] or 0,
                        sobreprecio_unitario=fila['producto__sobreprecio'] or 0,
                        precio_venta_unitario=fila['producto__precioventa'] or 0,
                        activo=True,
                        agotado=False,
                        observaciones=f'Lote creado por reconciliar_lotes_a_stock '
                                      f'(caso {etiqueta[0]}: saldo={saldo} stock={stock})',
                    ))
                    if len(pendientes_bulk) >= BATCH_CREATE:
                        _flush_bulk()
                if etiqueta == 'A crear':
                    u_creadas += cantidad
                else:
                    u_completadas += cantidad
                self._muestra(muestras, n_muestras, fila, etiqueta, saldo, stock, cantidad)
                if i % 2000 == 0:
                    self.stdout.write(f'  ... caso {etiqueta[0]}: {i:,}/{len(filas):,} [{_elapsed()}]')
                    self.stdout.flush()
            _flush_bulk()
            if filas:
                self.stdout.write(f'  Caso {etiqueta[0]} listo: {len(filas):,} tallas [{_elapsed()}]')
                self.stdout.flush()

        # ── [3] Caso B por bloques: 1 lock-select + 1 bulk_update por bloque ──
        objetivo = {f['id']: max(f['stock'] or 0, 0) for f in caso_b}  # stock<0 → lotes a 0
        ids_b = [f['id'] for f in caso_b]
        for f in caso_b:
            self._muestra(muestras, n_muestras, f, 'B bajar',
                          f['saldo_lotes'] or 0, f['stock'] or 0,
                          -((f['saldo_lotes'] or 0) - max(f['stock'] or 0, 0)))

        for i in range(0, len(ids_b), BATCH_B):
            bloque = ids_b[i:i + BATCH_B]

            def _procesar_bloque(lotes):
                nonlocal lotes_tocados, lotes_agotados, u_bajadas
                por_talla = defaultdict(list)
                for lote in lotes:
                    por_talla[lote.producto_talla_id].append(lote)
                modificados = []
                for pt_id, lotes_pt in por_talla.items():
                    saldo_actual = sum(l.cantidad_disponible for l in lotes_pt)
                    restante = saldo_actual - objetivo.get(pt_id, 0)
                    if restante <= 0:
                        continue
                    # más nuevo primero para preservar la antigüedad FIFO
                    # (fecha None se trata como el más antiguo: se baja al final)
                    for lote in sorted(
                            lotes_pt,
                            key=lambda l: (l.fecha_ingreso.isoformat()
                                           if l.fecha_ingreso else '', l.id),
                            reverse=True):
                        if restante <= 0:
                            break
                        quita = min(restante, lote.cantidad_disponible)
                        lote.cantidad_disponible -= quita
                        lote.agotado = lote.cantidad_disponible <= 0
                        modificados.append(lote)
                        u_bajadas += quita
                        restante -= quita
                        if lote.agotado:
                            lotes_agotados += 1
                lotes_tocados += len(modificados)
                return modificados

            base = LoteProducto.objects.filter(
                producto_talla_id__in=bloque, activo=True, cantidad_disponible__gt=0)
            if apply_changes:
                with transaction.atomic():
                    modificados = _procesar_bloque(list(base.select_for_update()))
                    if modificados:
                        LoteProducto.objects.bulk_update(
                            modificados, ['cantidad_disponible', 'agotado'],
                            batch_size=1000)
            else:
                _procesar_bloque(list(base))

            hechos = min(i + BATCH_B, len(ids_b))
            self.stdout.write(f'  ... caso B: {hechos:,}/{len(ids_b):,} tallas [{_elapsed()}]')
            self.stdout.flush()

        # ── Reporte ──
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Tallas reconciliadas por caso:'))
        self.stdout.write(f'  A) Crear lote (stock sin lotes) : {len(caso_a):>7,}  ({u_creadas:,} u)')
        self.stdout.write(f'  B) Bajar lotes inflados         : {len(caso_b):>7,}  ({u_bajadas:,} u)')
        self.stdout.write(f'  C) Completar lotes cortos       : {len(caso_c):>7,}  ({u_completadas:,} u)')
        self.stdout.write(self.style.WARNING(
            f'  TOTAL tallas                    : {len(caso_a) + len(caso_b) + len(caso_c):>7,}'
            f'   [{_elapsed()}]'))
        if sin_costo:
            self.stdout.write(self.style.WARNING(
                f'  (!) {sin_costo} tallas con producto.costo=0 → lote con costo 0'))

        if muestras:
            self.stdout.write('')
            self.stdout.write(f"  {'SKU':>12} {'CASO':>11} {'SUC':>6} {'TALLA':>7} "
                              f"{'SALDO':>7} {'STOCK':>7} {'Δ APLIC':>8}  ARTÍCULO")
            self.stdout.write('  ' + '-' * 78)
            for m in muestras:
                self.stdout.write(
                    f"  {m['sku']:>12} {m['caso']:>11} {m['suc']:>6} {m['talla']:>7} "
                    f"{m['saldo']:>7} {m['stock']:>7} {m['delta']:>8}  {m['articulo'][:28]}")

        if apply_changes:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(
                f'[OK] Aplicado en {_elapsed()}. Lotes creados: {lotes_nuevos:,} | '
                f'ajustados: {lotes_tocados:,} (agotados: {lotes_agotados:,}).'))
            logger.info(
                'reconciliar_lotes_a_stock APPLY v2: A=%s B=%s C=%s lotes_nuevos=%s '
                'lotes_tocados=%s agotados=%s sucursal=%s',
                len(caso_a), len(caso_b), len(caso_c), lotes_nuevos,
                lotes_tocados, lotes_agotados, opts['sucursal'] or 'todas',
            )
        else:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                '[DRY-RUN] No se escribió nada. Usa --apply para aplicar.'))

    def _muestra(self, muestras, n_max, fila, caso, saldo, stock, delta):
        if len(muestras) >= n_max:
            return
        muestras.append({
            'sku': str(fila['sku']),
            'caso': caso,
            'suc': fila['producto__sucursal__alias'] or '?',
            'talla': str(fila['talla']),
            'saldo': saldo,
            'stock': stock,
            'delta': delta,
            'articulo': fila['producto__articulo'] or '?',
        })
