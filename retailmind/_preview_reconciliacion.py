"""
Preview de lo que hara --apply.

Lee la BD productiva (segun .env) y muestra un resumen DETALLADO de:
- Cuantos movimientos se crearan (total y por sucursal).
- Cuantos SKUs se ajustaran (DISMINUIR) y cuantos se omitiran (AUMENTAR).
- Top 20 ajustes mas grandes que SI se haran.
- Top 20 omitidos por seguridad.

NO modifica nada. Solo lee.

Uso:
    python _preview_reconciliacion.py
    python _preview_reconciliacion.py --desde 2026-04-17
"""

import argparse
import os
import sys
from datetime import date

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db.models import Sum, Count, Exists, OuterRef

from app.models.catalogo import Producto_Talla
from app.models.inventario import Movimientos_Producto, LoteProducto
from app.models.ventas import Ticket, Ticket_Productos


FECHA_INICIO_DEFAULT = date(2026, 4, 17)


def parse_args():
    p = argparse.ArgumentParser(description='Preview de reconciliacion bug FIFO')
    p.add_argument('--desde', type=str, default=FECHA_INICIO_DEFAULT.isoformat())
    return p.parse_args()


def main():
    args = parse_args()
    fecha_desde = date.fromisoformat(args.desde)

    print("=" * 80)
    print(f"PREVIEW RECONCILIACION  (desde {fecha_desde.isoformat()})")
    print("=" * 80)
    print("Este script SOLO LEE la BD. No modifica nada.")
    print()

    # --- 1. Tickets afectados ---
    tiene_egreso = Movimientos_Producto.objects.filter(
        ticket=OuterRef('pk'), tipo_movimiento='EGRESO'
    )
    tickets_qs = (
        Ticket.objects
        .filter(estado='PAGADO',
                modulo_origen__in=['POS', 'VENTA_PUBLICO'],
                fecha__gte=fecha_desde)
        .annotate(tiene_mov=Exists(tiene_egreso))
        .filter(tiene_mov=False)
    )

    total_tickets = tickets_qs.count()
    print(f"[1] TICKETS POS pagados sin movimiento: {total_tickets}")
    print()
    print("    Por sucursal:")
    for r in tickets_qs.values('sucursal__alias').annotate(c=Count('id')).order_by('-c'):
        print(f"      {r['sucursal__alias'] or '?':<20} {r['c']:>6}")
    print()

    # --- 2. Movimientos que se crearan ---
    print(f"[2] MOVIMIENTOS de EGRESO que se crearan:")
    detalle_qs = Ticket_Productos.objects.filter(
        idTicket__in=tickets_qs,
        ProductoTalla__isnull=False,
    )
    total_movs = detalle_qs.count()
    total_unidades = detalle_qs.aggregate(s=Sum('stock'))['s'] or 0
    print(f"    Lineas de detalle (= movimientos):  {total_movs}")
    print(f"    Unidades totales a descontar:       {total_unidades}")
    print()

    print("    Movimientos por sucursal:")
    print(f"      {'sucursal':<20} {'lineas':>8} {'unidades':>10}")
    for r in (detalle_qs
              .values('idTicket__sucursal__alias')
              .annotate(c=Count('id'), u=Sum('stock'))
              .order_by('-c')):
        print(f"      {r['idTicket__sucursal__alias'] or '?':<20} {r['c']:>8} {r['u']:>10}")
    print()

    # --- 3. SKUs afectados ---
    skus_ids = set(detalle_qs.values_list('ProductoTalla_id', flat=True).distinct())
    print(f"[3] SKUs unicos afectados: {len(skus_ids)}")
    print()
    print("    Calculando ajustes de stock (puede tardar)...")

    # Unidades reales vendidas por el bug POS por SKU (suma de Ticket_Productos.stock)
    unidades_por_sku = dict(
        detalle_qs
        .values('ProductoTalla_id')
        .annotate(total=Sum('stock'))
        .values_list('ProductoTalla_id', 'total')
    )

    # Lotes y kardex (solo informativos para mostrar contexto, NO se usan para ajustar)
    lotes_por_sku = dict(
        LoteProducto.objects
        .filter(producto_talla_id__in=skus_ids, activo=True)
        .values('producto_talla_id')
        .annotate(total=Sum('cantidad_disponible'))
        .values_list('producto_talla_id', 'total')
    )

    cnt_ajustar = 0
    cnt_negativos = 0
    unid_a_bajar = 0
    top_ajustar = []
    top_negativos = []
    por_sucursal = {}

    for pt in (Producto_Talla.objects
               .filter(id__in=skus_ids)
               .select_related('producto__sucursal')
               .iterator(chunk_size=500)):
        legacy = pt.stock or 0
        unid_vendidas = unidades_por_sku.get(pt.id, 0) or 0
        if unid_vendidas <= 0:
            continue
        stock_nuevo = legacy - unid_vendidas
        lotes = lotes_por_sku.get(pt.id, 0) or 0
        sucursal = pt.producto.sucursal if pt.producto else None
        suc_alias = (sucursal.alias if sucursal else '?') or '?'

        d = por_sucursal.setdefault(suc_alias, {'skus': 0, 'unid': 0, 'neg': 0})
        d['skus'] += 1
        d['unid'] += unid_vendidas
        if stock_nuevo < 0:
            d['neg'] += 1
            cnt_negativos += 1
            top_negativos.append((pt.sku, suc_alias, legacy, unid_vendidas,
                                  stock_nuevo, lotes,
                                  pt.producto.articulo if pt.producto else ''))

        cnt_ajustar += 1
        unid_a_bajar += unid_vendidas
        top_ajustar.append((pt.sku, suc_alias, legacy, unid_vendidas,
                            stock_nuevo, lotes,
                            pt.producto.articulo if pt.producto else ''))

    print()
    print("[4] AJUSTES DE STOCK (nueva logica: stock -= unidades vendidas por bug POS):")
    print(f"    SKUs a ajustar:                  {cnt_ajustar}")
    print(f"    Unidades totales a bajar:        {unid_a_bajar}")
    print(f"    SKUs que quedarian negativos:    {cnt_negativos}")
    print()

    # --- 5. Detalle por sucursal ---
    print("[5] RESUMEN POR SUCURSAL:")
    print(f"    {'sucursal':<20} {'SKUs':>6} {'unid_bajar':>11} {'queda_neg':>10}")
    for suc, v in sorted(por_sucursal.items(), key=lambda x: -x[1]['unid']):
        print(f"    {suc:<20} {v['skus']:>6} {v['unid']:>11} {v['neg']:>10}")
    print()

    # --- 6. Top ajustes ---
    print("[6] TOP 20 SKUs con mas unidades a bajar:")
    print(f"    {'SKU':<12} {'suc':<6} {'articulo':<25} {'legacy':>7} {'-vendido':>9} {'=nuevo':>7} {'lotes':>7}")
    top_ajustar.sort(key=lambda x: -x[3])  # mas unidades vendidas primero
    for sku, suc, leg, vend, nuevo, lot, art in top_ajustar[:20]:
        print(f"    {str(sku)[:12]:<12} {suc:<6} {str(art)[:25]:<25} {leg:>7} {vend:>9} {nuevo:>7} {lot:>7}")
    print()

    if top_negativos:
        print(f"[7] WARNING: SKUs que quedaran NEGATIVOS (revisar antes de aplicar):")
        print(f"    {'SKU':<12} {'suc':<6} {'articulo':<25} {'legacy':>7} {'-vendido':>9} {'=nuevo':>7} {'lotes':>7}")
        top_negativos.sort(key=lambda x: x[4])  # mas negativo primero
        for sku, suc, leg, vend, nuevo, lot, art in top_negativos[:20]:
            print(f"    {str(sku)[:12]:<12} {suc:<6} {str(art)[:25]:<25} {leg:>7} {vend:>9} {nuevo:>7} {lot:>7}")
        print()

    print("=" * 80)
    print("RESUMEN PARA APLICAR --apply:")
    print("=" * 80)
    print(f"  Tickets a procesar:               {total_tickets}")
    print(f"  Movimientos EGRESO a crear:       {total_movs}")
    print(f"  Unidades en movimientos:          {total_unidades}")
    print(f"  SKUs con stock a ajustar:         {cnt_ajustar}")
    print(f"  Unidades totales a bajar:         {unid_a_bajar}")
    if cnt_negativos:
        print(f"  WARNING SKUs negativos:           {cnt_negativos}")
    print()
    print("Logica: stock_nuevo = stock_actual - unidades_vendidas_bug_POS")
    print("  -> NO toca descuadres historicos preexistentes.")
    print()
    print("Para aplicar:")
    print("  python _reconciliacion_stock_fifo.py --apply")


if __name__ == '__main__':
    main()
