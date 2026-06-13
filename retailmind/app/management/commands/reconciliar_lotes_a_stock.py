"""
Reconcilia los lotes FIFO (LoteProducto.cantidad_disponible) para que su saldo
total por talla cuadre con el stock plano (Producto_Talla.stock), tratando el
stock plano como la fuente de verdad (está sincronizado desde MySQL/producción).

Contexto:
    El fallback de descuento manual en registrar_pagos_ticket baja
    Producto_Talla.stock cuando consumir_stock_fifo falla, pero NO baja los
    lotes. Con el tiempo eso desincroniza el saldo de lotes del stock plano.
    `reconciliar_stock_lotes.py` reporta el drift (solo lectura); ESTE comando
    lo corrige.

    Para cada Producto_Talla compara saldo_lotes (suma de cantidad_disponible
    de lotes activos) contra stock plano y resuelve tres casos:

      A. stock>0 y SIN lotes activos   → crea 1 lote con cantidad = stock,
         costo = producto.costo (mismo criterio que generar_lotes_faltantes).
      B. saldo_lotes > stock (inflado) → baja cantidad_disponible empezando por
         el lote MÁS NUEVO (preserva la antigüedad FIFO del stock más viejo).
         Un lote que llega a 0 queda agotado=True (no se borra).
      C. saldo_lotes < stock (corto)   → crea un lote complementario por la
         diferencia, costo = producto.costo.

    NO toca LoteProducto.fecha_ingreso (la antigüedad que consume el ecommerce
    se calcula desde Movimientos_Producto, no desde los lotes).

Uso:
    python manage.py reconciliar_lotes_a_stock                 # dry-run
    python manage.py reconciliar_lotes_a_stock --sucursal 5    # una sucursal
    python manage.py reconciliar_lotes_a_stock --limit 200     # prueba 200 PT
    python manage.py reconciliar_lotes_a_stock --solo-inflados # solo caso B
    python manage.py reconciliar_lotes_a_stock --apply         # aplica
"""

import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum, Q, F

from app.models import Producto_Talla, LoteProducto

logger = logging.getLogger('app')


class Command(BaseCommand):
    help = (
        'Reconcilia el saldo de lotes FIFO al stock plano (Producto_Talla.stock '
        'como fuente de verdad): crea lotes faltantes, baja lotes inflados y '
        'completa lotes cortos. Por defecto dry-run; usa --apply para escribir.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Aplicar cambios. Sin este flag corre en dry-run.')
        parser.add_argument('--sucursal', type=int, default=None,
                            help='Procesar solo tallas de esta sucursal.')
        parser.add_argument('--limit', type=int, default=None,
                            help='Procesar solo las primeras N tallas con descuadre (para pruebas).')
        parser.add_argument('--solo-inflados', action='store_true',
                            help='Procesar solo el caso B (saldo_lotes > stock).')
        parser.add_argument('--solo-faltantes', action='store_true',
                            help='Procesar solo el caso A (stock>0 sin lotes).')
        parser.add_argument('--mostrar-muestras', type=int, default=20,
                            help='Cantidad de muestras en el reporte. Default: 20.')

    def handle(self, *args, **opts):
        apply_changes = opts['apply']
        sucursal_id = opts['sucursal']
        limit = opts['limit']
        solo_inflados = opts['solo_inflados']
        solo_faltantes = opts['solo_faltantes']
        n_muestras = opts['mostrar_muestras']

        if solo_inflados and solo_faltantes:
            self.stderr.write(self.style.ERROR(
                'No uses --solo-inflados y --solo-faltantes a la vez.'
            ))
            return

        modo = 'APPLY' if apply_changes else 'DRY-RUN'
        self.stdout.write(self.style.WARNING(
            f'\n=== Reconciliación lotes FIFO → stock plano (modo {modo}) ==='
        ))
        if sucursal_id:
            self.stdout.write(f'  Filtrado a sucursal ID : {sucursal_id}')
        if limit:
            self.stdout.write(f'  Límite de tallas       : {limit}')
        if solo_inflados:
            self.stdout.write('  Solo caso B (inflados)')
        if solo_faltantes:
            self.stdout.write('  Solo caso A (faltantes)')
        self.stdout.write('')

        # Tallas anotadas con el saldo de lotes activos. saldo_lotes es None
        # cuando no hay lote activo alguno.
        qs = (
            Producto_Talla.objects
            .select_related('producto')
            .annotate(saldo_lotes=Sum(
                'lotes__cantidad_disponible', filter=Q(lotes__activo=True),
            ))
        )
        if sucursal_id:
            qs = qs.filter(producto__sucursal_id=sucursal_id)

        # Solo las descuadradas: saldo != stock (tratando None como 0). El None
        # no compara con != en SQL, así que lo cubrimos con el OR explícito.
        qs = qs.filter(
            ~Q(saldo_lotes=F('stock'))
            | Q(saldo_lotes__isnull=True, stock__gt=0)
        )

        if solo_inflados:
            qs = qs.filter(saldo_lotes__gt=F('stock'))
        elif solo_faltantes:
            qs = qs.filter(saldo_lotes__isnull=True, stock__gt=0)

        if limit:
            qs = qs[:limit]

        # Contadores y muestras por caso.
        n_creados = n_bajados = n_completados = 0     # tallas afectadas por caso
        u_creadas = u_bajadas = u_completadas = 0     # unidades movidas por caso
        lotes_nuevos = lotes_tocados = lotes_agotados = 0
        sin_costo = 0
        muestras = []

        # Cada talla se reconcilia en su propia transacción (caso B puede tocar
        # varios lotes; deben aplicarse atómicamente). NO usamos una transacción
        # global: contra la DB remota un atomic() sobre ~24k tallas mantendría
        # locks demasiado tiempo. En dry-run no se abre transacción alguna.
        for pt in qs.iterator(chunk_size=1000):
            stock = pt.stock or 0
            saldo = pt.saldo_lotes  # None si no hay lotes activos

            # CASO A — stock>0 sin lotes → crear lote por todo el stock.
            if saldo is None:
                if stock <= 0:
                    continue  # nada que hacer (no debería entrar por el filtro)
                costo = pt.producto.costo or 0
                if costo == 0:
                    sin_costo += 1
                if apply_changes:
                    self._crear_lote(pt, stock,
                                     f'(caso A: stock={stock} sin lotes)')
                    lotes_nuevos += 1
                n_creados += 1
                u_creadas += stock
                self._muestra(muestras, n_muestras, pt, 'A crear', 0, stock, stock)
                continue

            diff = saldo - stock

            # CASO B — saldo > stock → bajar empezando por el lote más nuevo.
            if diff > 0:
                restante = diff
                if apply_changes:
                    with transaction.atomic():
                        lotes_act = list(
                            LoteProducto.objects
                            .select_for_update()
                            .filter(producto_talla=pt, activo=True, cantidad_disponible__gt=0)
                            .order_by('-fecha_ingreso')  # más nuevo primero
                        )
                        for lote in lotes_act:
                            if restante <= 0:
                                break
                            quita = min(restante, lote.cantidad_disponible)
                            lote.cantidad_disponible -= quita
                            lote.save()  # save() marca agotado si llega a 0
                            lotes_tocados += 1
                            if lote.cantidad_disponible <= 0:
                                lotes_agotados += 1
                            restante -= quita
                else:
                    # Dry-run: simular sin tocar nada para reportar bien las unidades.
                    saldos = (
                        LoteProducto.objects
                        .filter(producto_talla=pt, activo=True, cantidad_disponible__gt=0)
                        .order_by('-fecha_ingreso')
                        .values_list('cantidad_disponible', flat=True)
                    )
                    for c in saldos:
                        if restante <= 0:
                            break
                        restante -= min(restante, c)
                n_bajados += 1
                u_bajadas += (diff - restante)
                self._muestra(muestras, n_muestras, pt, 'B bajar', saldo, stock, -(diff - restante))
                continue

            # CASO C — saldo < stock (y saldo>0) → crear lote complementario.
            if diff < 0:
                faltan = -diff
                costo = pt.producto.costo or 0
                if costo == 0:
                    sin_costo += 1
                if apply_changes:
                    self._crear_lote(pt, faltan,
                                     f'(caso C: saldo={saldo} < stock={stock})')
                    lotes_nuevos += 1
                n_completados += 1
                u_completadas += faltan
                self._muestra(muestras, n_muestras, pt, 'C completar', saldo, stock, faltan)

        # ── Reporte ──
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Tallas a reconciliar por caso:'))
        self.stdout.write(f'  A) Crear lote (stock sin lotes) : {n_creados:>7}  ({u_creadas:,} u)')
        self.stdout.write(f'  B) Bajar lotes inflados         : {n_bajados:>7}  ({u_bajadas:,} u)')
        self.stdout.write(f'  C) Completar lotes cortos       : {n_completados:>7}  ({u_completadas:,} u)')
        total_tallas = n_creados + n_bajados + n_completados
        self.stdout.write(self.style.WARNING(f'  TOTAL tallas afectadas          : {total_tallas:>7}'))
        if sin_costo:
            self.stdout.write(self.style.WARNING(
                f'  (!) {sin_costo} tallas con producto.costo=0 → lote creado con costo 0'
            ))

        if muestras:
            self.stdout.write('')
            self.stdout.write(f"  {'SKU':>12} {'CASO':>11} {'SUC':>6} {'TALLA':>7} {'SALDO':>7} {'STOCK':>7} {'Δ APLIC':>8}  ARTÍCULO")
            self.stdout.write('  ' + '-' * 78)
            for m in muestras:
                self.stdout.write(
                    f"  {m['sku']:>12} {m['caso']:>11} {m['suc']:>6} {m['talla']:>7} "
                    f"{m['saldo']:>7} {m['stock']:>7} {m['delta']:>8}  {m['articulo'][:28]}"
                )

        if apply_changes:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS(
                f'[OK] Aplicado. Lotes creados: {lotes_nuevos} | '
                f'lotes ajustados: {lotes_tocados} (agotados: {lotes_agotados}).'
            ))
            logger.info(
                'reconciliar_lotes_a_stock APPLY: tallas=%s creadas=%s bajadas=%s '
                'completadas=%s lotes_nuevos=%s lotes_tocados=%s agotados=%s sucursal=%s',
                total_tallas, n_creados, n_bajados, n_completados,
                lotes_nuevos, lotes_tocados, lotes_agotados, sucursal_id or 'todas',
            )
        else:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                '[DRY-RUN] No se escribió nada. Usa --apply para aplicar.'
            ))

    def _crear_lote(self, pt, cantidad, nota):
        LoteProducto.objects.create(
            producto_talla=pt,
            cantidad_inicial=cantidad,
            cantidad_disponible=cantidad,
            costo_unitario=pt.producto.costo or 0,
            sobreprecio_unitario=pt.producto.sobreprecio or 0,
            precio_venta_unitario=pt.producto.precioventa or 0,
            activo=True,
            agotado=False,
            observaciones=f'Lote creado por reconciliar_lotes_a_stock {nota}',
        )

    def _muestra(self, muestras, n_max, pt, caso, saldo, stock, delta):
        if len(muestras) >= n_max:
            return
        prod = pt.producto
        muestras.append({
            'sku': str(pt.sku),
            'caso': caso,
            'suc': (prod.sucursal.alias if prod and prod.sucursal else '?'),
            'talla': str(pt.talla),
            'saldo': saldo,
            'stock': stock,
            'delta': delta,
            'articulo': (prod.articulo if prod else '?') or '?',
        })
