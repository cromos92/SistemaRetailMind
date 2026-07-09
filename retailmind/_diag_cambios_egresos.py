"""
DIAGNÓSTICO SOLO LECTURA — Egresos faltantes en cambios (entrega del producto nuevo).

No modifica NADA: solo hace consultas SELECT e imprime.

Confirma si el flujo de CAMBIO dejó (o no) el movimiento de EGRESO del producto
entregado, y mide cuántos cambios en NICK1 quedaron con la entrega sin registrar
(stock inflado). Caso de referencia: SKU 4813255 / cambio CD-6-202605-0002.

USO (desde la carpeta donde está manage.py):
    py _diag_cambios_egresos.py
    py _diag_cambios_egresos.py --sucursal NICK1
"""
import argparse
import os
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db.models import Q
from app.models import Movimientos_Producto, Producto_Talla, CambioDevolucion


def parse_args():
    p = argparse.ArgumentParser(description='Diagnóstico egresos faltantes en cambios (solo lectura)')
    p.add_argument('--sucursal', type=str, default='NICK1', help='Alias de sucursal (default NICK1)')
    return p.parse_args()


def egreso_de_entrega_existe(cambio, producto_talla):
    """¿Hay un EGRESO ligado a este cambio para el producto entregado?
    El flujo crea el egreso con referencia_externa = numero_operacion (o el
    número del cambio en observaciones, en el fallback)."""
    return Movimientos_Producto.objects.filter(
        tipo_movimiento='EGRESO',
    ).filter(
        Q(referencia_externa=cambio.numero_operacion)
        | Q(observaciones__contains=cambio.numero_operacion),
        ProductoTalla=producto_talla,
    ).exists()


def main():
    args = parse_args()
    suc = args.sucursal

    print('=' * 72)
    print(f'DIAGNÓSTICO EGRESOS DE CAMBIO (solo lectura) — sucursal {suc}')
    print('=' * 72)

    # ---- Caso de referencia: SKU 4813255 ----
    print('\n--- SKU 4813255 (producto entregado) en', suc, '---')
    pt = (Producto_Talla.objects
          .filter(sku=4813255, producto__sucursal__alias=suc)
          .select_related('producto__sucursal').first())
    if pt:
        print(f'  talla {pt.talla} | stock actual {pt.stock}')
        movs = Movimientos_Producto.objects.filter(ProductoTalla=pt).order_by('fecha', 'hora', 'id')
        if not movs:
            print('  (sin movimientos)')
        for m in movs:
            print(f"  {str(m.fecha)[:10]} | {m.tipo_movimiento:7} | {m.concepto:20} | "
                  f"cant {m.cantidad:>3} | tk {m.ticket_id} | ref {m.referencia_externa} | "
                  f"{(m.observaciones or '')[:35]}")
    else:
        print('  SKU 4813255 no encontrado en', suc)

    print('\n--- Movimientos ligados a CD-6-202605-0002 (cualquier SKU) ---')
    ligados = (Movimientos_Producto.objects
               .filter(Q(referencia_externa='CD-6-202605-0002')
                       | Q(observaciones__contains='CD-6-202605-0002'))
               .select_related('ProductoTalla'))
    if not ligados:
        print('  (ninguno)')
    for m in ligados:
        print(f"  sku {m.ProductoTalla.sku} | {m.tipo_movimiento:7} | {m.concepto:20} | "
              f"cant {m.cantidad:>3} | tk {m.ticket_id}")

    # ---- Universo: cambios de NICK1 que entregaron producto y su egreso ----
    print('\n--- Cambios en', suc, 'con producto entregado: ¿tienen egreso? ---')
    cambios = (CambioDevolucion.objects
               .filter(sucursal__alias=suc, fecha_ejecucion__isnull=False)
               .prefetch_related('detalles__producto_nuevo__producto')
               .order_by('fecha_ejecucion'))

    total_entregas = 0
    sin_egreso = 0
    unidades_infladas = 0
    faltantes = []
    for c in cambios:
        for d in c.detalles.all():
            if not d.producto_nuevo or not d.cantidad_nueva:
                continue
            total_entregas += 1
            pn = d.producto_nuevo
            if not egreso_de_entrega_existe(c, pn):
                sin_egreso += 1
                unidades_infladas += d.cantidad_nueva
                faltantes.append((c.numero_operacion, str(c.fecha_ejecucion)[:10],
                                  pn.sku, pn.talla, d.cantidad_nueva, pn.stock))

    print(f'  Entregas (producto nuevo) totales:     {total_entregas}')
    print(f'  Entregas SIN egreso registrado:        {sin_egreso}')
    print(f'  Unidades potencialmente infladas:      {unidades_infladas}')
    if faltantes:
        print(f"\n  {'CAMBIO':<20} {'FECHA':<11} {'SKU':>10} {'TALLA':>7} {'ENTREG':>7} {'STOCK_HOY':>9}")
        print('  ' + '-' * 68)
        for op, fe, sku, talla, ent, st in faltantes:
            print(f"  {op:<20} {fe:<11} {str(sku):>10} {str(talla):>7} {ent:>7} {st:>9}")

    print('\n' + '=' * 72)
    print('LECTURA:')
    print('  - Si 4813255 NO tiene EGRESO y stock=1  -> Escenario Y (entrega no restó).')
    print('  - "Entregas SIN egreso" = universo del bug del flujo de cambio.')
    print('  Este script NO modificó nada.')
    print('=' * 72)


if __name__ == '__main__':
    main()
