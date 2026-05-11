"""
Script de reconciliación del bug FIFO en POS.

PROBLEMA QUE RESUELVE:
La versión de consumir_stock_fifo en views.py (antes del fix) solo descontaba
LoteProducto.cantidad_disponible, pero NO actualizaba Producto_Talla.stock NI
creaba Movimientos_Producto. Esto generó descuadres en ventas POS desde el
inicio de operaciones.

QUÉ HACE:
1. Detecta tickets POS pagados sin Movimientos_Producto de egreso asociado.
2. Crea los movimientos faltantes con concepto VENTA_DIRECTA.
3. Ajusta Producto_Talla.stock a SUM(LoteProducto.cantidad_disponible) — los
   lotes son la fuente de verdad porque SÍ se descontaron correctamente.

USO:
  Dry-run (solo reporta, no modifica nada):
    python _reconciliacion_stock_fifo.py

  Aplicar cambios:
    python _reconciliacion_stock_fifo.py --apply

  Cambiar fecha de inicio (default 2026-04-17):
    python _reconciliacion_stock_fifo.py --desde 2026-04-17 --apply

SALVAGUARDAS:
- Idempotente: re-ejecutar no duplica movimientos.
- Transacción atómica por ticket: un fallo no contamina los demás.
- Movimientos llevan responsable='reconciliacion_script' y
  referencia_externa='RECONCIL_TICKET_<id>' para poder revertir.
- Antes de --apply HAZ BACKUP DE LA BASE DE DATOS.
"""

import argparse
import csv
import os
import sys
from datetime import date

# Forzar UTF-8 en stdout/stderr para que los emojis no rompan en consolas Windows (cp1252)
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db import transaction
from django.db.models import Sum, Exists, OuterRef
from django.utils import timezone

from app.models.inventario import Movimientos_Producto, LoteProducto
from app.models.ventas import Ticket, Ticket_Productos
from app.models.catalogo import Producto_Talla


FECHA_INICIO_DEFAULT = date(2026, 4, 17)
RESPONSABLE_RECONCIL = 'reconciliacion_script'


def parse_args():
    parser = argparse.ArgumentParser(description='Reconciliación bug FIFO POS')
    parser.add_argument('--apply', action='store_true',
                        help='Aplicar cambios. Sin este flag solo reporta (dry-run).')
    parser.add_argument('--desde', type=str, default=FECHA_INICIO_DEFAULT.isoformat(),
                        help=f'Fecha de inicio YYYY-MM-DD (default {FECHA_INICIO_DEFAULT.isoformat()})')
    return parser.parse_args()


def tickets_pos_sin_movimiento(fecha_desde):
    """
    Retorna tickets POS pagados desde fecha_desde que NO tienen Movimientos_Producto
    de egreso asociado al ticket. Optimizado con select_related/prefetch_related.
    """
    tiene_egreso = Movimientos_Producto.objects.filter(
        ticket=OuterRef('pk'),
        tipo_movimiento='EGRESO',
    )
    return (
        Ticket.objects
        .filter(
            estado='PAGADO',
            modulo_origen__in=['POS', 'VENTA_PUBLICO'],
            fecha__gte=fecha_desde,
        )
        .annotate(tiene_mov=Exists(tiene_egreso))
        .filter(tiene_mov=False)
        .select_related('sucursal')
        .prefetch_related('ticket_productos__ProductoTalla__producto__sucursal')
        .order_by('fecha', 'correlativo')
    )


def diagnostico(fecha_desde):
    """Fase A: solo reporta, no modifica."""
    print(f"\n{'='*70}")
    print(f"FASE A — DIAGNÓSTICO  (desde {fecha_desde.isoformat()})")
    print(f"{'='*70}")

    tickets_afectados = tickets_pos_sin_movimiento(fecha_desde)
    total = tickets_afectados.count()
    print(f"\nTickets POS pagados sin Movimientos_Producto de egreso: {total}")

    if total == 0:
        print("[OK] No hay descuadres detectados. Nada que reconciliar.")
        return [], {}

    skus_cache = {}
    tickets_data = []

    for ticket in tickets_afectados.iterator(chunk_size=500):
        productos_data = []
        for tp in ticket.ticket_productos.all():
            if tp.ProductoTalla is None:
                continue
            pt = tp.ProductoTalla
            if pt.id not in skus_cache:
                skus_cache[pt.id] = pt
            productos_data.append({
                'producto_talla_id': pt.id,
                'sku': pt.sku,
                'cantidad': tp.stock,
                'precio': tp.precio,
            })
        if productos_data:
            tickets_data.append({
                'ticket_id': ticket.id,
                'correlativo': ticket.correlativo,
                'sucursal_id': ticket.sucursal_id,
                'fecha': ticket.fecha,
                'hora': ticket.hora,
                'productos': productos_data,
            })

    skus_afectados = skus_cache
    print(f"SKUs únicos afectados: {len(skus_afectados)}")

    print("Calculando stock_lotes y stock_kardex en bulk...")
    lotes_por_sku = dict(
        LoteProducto.objects
        .filter(producto_talla_id__in=skus_afectados.keys(), activo=True)
        .values('producto_talla_id')
        .annotate(total=Sum('cantidad_disponible'))
        .values_list('producto_talla_id', 'total')
    )
    kardex_por_sku = dict(
        Movimientos_Producto.objects
        .filter(ProductoTalla_id__in=skus_afectados.keys(), estado='COMPLETADO')
        .values('ProductoTalla_id')
        .annotate(total=Sum('cantidad'))
        .values_list('ProductoTalla_id', 'total')
    )

    print("\n--- Descuadre por SKU (top 30) ---")
    print(f"{'SKU':<20} {'stock_legacy':>12} {'stock_lotes':>12} {'stock_kardex':>12} {'diff':>8}")

    csv_path = f"reconciliacion_diagnostico_{date.today().isoformat()}.csv"
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['sku', 'producto_talla_id', 'sucursal_id', 'sucursal_alias',
                    'sucursal_nombre', 'articulo', 'talla',
                    'stock_legacy', 'stock_lotes', 'stock_kardex',
                    'ajuste_necesario', 'direccion'])

        rows = []
        for pt_id, pt in skus_afectados.items():
            sucursal = pt.producto.sucursal if pt.producto else None
            stock_legacy = pt.stock or 0
            stock_lotes = lotes_por_sku.get(pt_id, 0) or 0
            stock_kardex = kardex_por_sku.get(pt_id, 0) or 0
            ajuste = stock_lotes - stock_legacy
            if ajuste > 0:
                direccion = 'AUMENTAR'
            elif ajuste < 0:
                direccion = 'DISMINUIR'
            else:
                direccion = 'SIN_CAMBIO'

            suc_id = sucursal.id if sucursal else ''
            suc_alias = (sucursal.alias if sucursal else '') or ''
            suc_nombre = (sucursal.nombre if sucursal else '') or ''
            articulo = (pt.producto.articulo if pt.producto else '') or ''
            talla = pt.talla or ''

            rows.append((pt.sku, pt_id, suc_id, suc_alias, suc_nombre, articulo,
                         talla, stock_legacy, stock_lotes, stock_kardex, ajuste, direccion))
            w.writerow([pt.sku, pt_id, suc_id, suc_alias, suc_nombre, articulo,
                        talla, stock_legacy, stock_lotes, stock_kardex, ajuste, direccion])

        rows.sort(key=lambda r: abs(r[10]), reverse=True)
        print(f"\n{'SKU':<20} {'sucursal':<18} {'legacy':>8} {'lotes':>8} {'kardex':>8} {'ajuste':>8} {'dir':<10}")
        for sku, _, _, sa, _, _, _, sl, slt, sk, aj, di in rows[:30]:
            print(f"{str(sku):<20} {sa[:18]:<18} {sl:>8} {slt:>8} {sk:>8} {aj:>+8d} {di:<10}")

        print("\n--- Resumen por sucursal ---")
        by_suc = {}
        for r in rows:
            key = (r[2], r[3], r[4])
            d = by_suc.setdefault(key, {'aumentar': 0, 'disminuir': 0, 'skus': 0})
            d['skus'] += 1
            if r[10] > 0:
                d['aumentar'] += r[10]
            elif r[10] < 0:
                d['disminuir'] += abs(r[10])
        print(f"{'sucursal':<25} {'skus':>6} {'unid_aumentar':>14} {'unid_disminuir':>14}")
        for (sid, sal, snom), v in sorted(by_suc.items(), key=lambda x: -x[1]['disminuir']):
            etiqueta = f"{sal or snom or sid}"
            print(f"{str(etiqueta)[:25]:<25} {v['skus']:>6} {v['aumentar']:>14} {v['disminuir']:>14}")

    print(f"\nCSV exportado: {csv_path}")
    return tickets_data, skus_afectados


def aplicar_correccion(tickets_data, skus_afectados):
    """Fase B: crea movimientos faltantes + ajusta Producto_Talla.stock."""
    print(f"\n{'='*70}")
    print(f"FASE B — APLICANDO CORRECCION")
    print(f"{'='*70}")

    csv_path = f"reconciliacion_aplicada_{date.today().isoformat()}.csv"
    movimientos_creados = 0
    movimientos_existentes = 0
    tickets_procesados = 0
    tickets_fallidos = 0

    ticket_ids = [td['ticket_id'] for td in tickets_data]
    print(f"Cargando movimientos existentes en bulk ({len(ticket_ids)} tickets)...")
    existentes = set(
        Movimientos_Producto.objects
        .filter(ticket_id__in=ticket_ids, tipo_movimiento='EGRESO')
        .values_list('ticket_id', 'ProductoTalla_id')
    )

    sucursales = {
        td['sucursal_id']: None for td in tickets_data if td.get('sucursal_id')
    }
    from app.models.organizacion import Sucursal
    for s in Sucursal.objects.filter(id__in=sucursales.keys()):
        sucursales[s.id] = s

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['ticket_id', 'correlativo', 'sku', 'producto_talla_id',
                    'sucursal_id', 'sucursal_alias', 'cantidad',
                    'accion', 'observacion'])

        BATCH_SIZE = 500
        nuevos_movimientos = []
        nuevos_rows = []

        for td in tickets_data:
            sucursal = sucursales.get(td['sucursal_id'])
            suc_id = td.get('sucursal_id') or ''
            suc_alias = (sucursal.alias if sucursal else '') or ''
            try:
                for p in td['productos']:
                    pt = skus_afectados.get(p['producto_talla_id'])
                    if pt is None:
                        continue

                    if (td['ticket_id'], pt.id) in existentes:
                        movimientos_existentes += 1
                        w.writerow([td['ticket_id'], td['correlativo'], p['sku'],
                                    pt.id, suc_id, suc_alias, p['cantidad'],
                                    'SKIP', 'Movimiento ya existe (idempotente)'])
                        continue

                    nuevos_movimientos.append(Movimientos_Producto(
                        ticket_id=td['ticket_id'],
                        ProductoTalla_id=pt.id,
                        sucursal_origen=sucursal,
                        cantidad=-p['cantidad'],
                        costo=(pt.producto.costo if pt.producto else 0) or 0,
                        precio=p['precio'] or 0,
                        sobreprecio=(getattr(pt.producto, 'sobreprecio', 0) if pt.producto else 0) or 0,
                        concepto='VENTA_DIRECTA',
                        tipo_movimiento='EGRESO',
                        estado='COMPLETADO',
                        responsable=RESPONSABLE_RECONCIL,
                        observaciones=f'Reconciliacion bug FIFO - Ticket #{td["correlativo"]}',
                        referencia_externa=f'RECONCIL_TICKET_{td["correlativo"]}',
                        fecha=td['fecha'],
                        hora=td['hora'],
                    ))
                    nuevos_rows.append([
                        td['ticket_id'], td['correlativo'], p['sku'], pt.id,
                        suc_id, suc_alias, p['cantidad'], 'CREADO',
                        'Movimiento de EGRESO creado'
                    ])

                    if len(nuevos_movimientos) >= BATCH_SIZE:
                        with transaction.atomic():
                            Movimientos_Producto.objects.bulk_create(nuevos_movimientos)
                        movimientos_creados += len(nuevos_movimientos)
                        for row in nuevos_rows:
                            w.writerow(row)
                        nuevos_movimientos = []
                        nuevos_rows = []

                tickets_procesados += 1
            except Exception as e:
                tickets_fallidos += 1
                print(f"[ERROR] Ticket {td['ticket_id']} (correlativo {td['correlativo']}): {e}")
                w.writerow([td['ticket_id'], td['correlativo'], '', '', '', '', '',
                            'ERROR', str(e)])

        if nuevos_movimientos:
            with transaction.atomic():
                Movimientos_Producto.objects.bulk_create(nuevos_movimientos)
            movimientos_creados += len(nuevos_movimientos)
            for row in nuevos_rows:
                w.writerow(row)

        print(f"\nMovimientos creados:    {movimientos_creados}")
        print(f"Movimientos existentes: {movimientos_existentes} (skipped)")
        print(f"Tickets procesados OK:  {tickets_procesados}")
        print(f"Tickets fallidos:       {tickets_fallidos}")

        print(f"\n--- Ajustando Producto_Talla.stock = SUM(lotes) en bulk ---")
        lotes_actuales = dict(
            LoteProducto.objects
            .filter(producto_talla_id__in=skus_afectados.keys(), activo=True)
            .values('producto_talla_id')
            .annotate(total=Sum('cantidad_disponible'))
            .values_list('producto_talla_id', 'total')
        )

        ajustes_stock = 0
        omitidos_aumentar = 0
        ajustes_batch = []
        for pt_id, pt in skus_afectados.items():
            stock_real = lotes_actuales.get(pt_id, 0) or 0
            stock_antes = pt.stock or 0
            sucursal = pt.producto.sucursal if pt.producto else None
            suc_id = sucursal.id if sucursal else ''
            suc_alias = (sucursal.alias if sucursal else '') or ''

            if stock_antes == stock_real:
                continue

            if stock_real < stock_antes:
                pt.stock = stock_real
                ajustes_batch.append(pt)
                w.writerow(['', '', pt.sku, pt_id, suc_id, suc_alias, '',
                            'AJUSTE_STOCK',
                            f'stock {stock_antes} -> {stock_real} (DISMINUIR - bug POS)'])
                ajustes_stock += 1
            else:
                omitidos_aumentar += 1
                w.writerow(['', '', pt.sku, pt_id, suc_id, suc_alias, '',
                            'OMITIDO_AUMENTAR',
                            f'stock {stock_antes} < lotes {stock_real}. '
                            f'NO se ajusta automaticamente (revisar manualmente).'])

        if ajustes_batch:
            with transaction.atomic():
                Producto_Talla.objects.bulk_update(ajustes_batch, ['stock'], batch_size=500)

        print(f"SKUs con stock ajustado (DISMINUIR): {ajustes_stock}")
        print(f"SKUs OMITIDOS (caso AUMENTAR, revisar manual): {omitidos_aumentar}")

    print(f"\nCSV exportado: {csv_path}")


def main():
    args = parse_args()
    try:
        fecha_desde = date.fromisoformat(args.desde)
    except ValueError:
        print(f"[ERROR] Fecha invalida: {args.desde}. Use formato YYYY-MM-DD.")
        sys.exit(1)

    modo = "APPLY (escribira en BD)" if args.apply else "DRY-RUN (solo reporte)"
    print(f"\nModo: {modo}")

    if args.apply:
        print("\n[!] HAS USADO --apply. Esto modificara la base de datos.")
        print("[!] Asegurate de tener un BACKUP RECIENTE de las tablas:")
        print("     - app_producto_talla")
        print("     - app_movimientos_producto")
        print("     - app_loteproducto")
        resp = input("\n¿Continuar? (escribe 'SI' para confirmar): ")
        if resp.strip() != 'SI':
            print("Cancelado por el usuario.")
            sys.exit(0)

    tickets_data, skus_afectados = diagnostico(fecha_desde)

    if not tickets_data:
        return

    if args.apply:
        aplicar_correccion(tickets_data, skus_afectados)
        print("\n[OK] Reconciliacion completada.")
    else:
        print(f"\n[i] Modo dry-run. Para aplicar los cambios ejecuta:")
        print(f"     python _reconciliacion_stock_fifo.py --desde {args.desde} --apply")


if __name__ == '__main__':
    main()
