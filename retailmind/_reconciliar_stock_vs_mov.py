"""
Script de reconciliación: compara `Producto_Talla.stock` con la suma de
movimientos (`Movimientos_Producto`) y reporta divergencias.

Útil para detectar inconsistencias remanentes de NC parciales legacy
(antes del fix que reversa stock por línea), ajustes manuales sin
movimiento, o bugs de sincronización.

Uso (desde la carpeta retailmind/):
    python manage.py shell < _reconciliar_stock_vs_mov.py

o directamente:
    python _reconciliar_stock_vs_mov.py

La segunda forma inicializa Django inline.

Salida: tabla con SKU, sucursal, stock actual, stock calculado, diff.
Exit code 1 si hay divergencias, 0 si todo cuadra.
"""
from __future__ import annotations

import os
import sys


def _bootstrap_django():
    """Permite ejecutar el script directamente."""
    try:
        import django  # noqa
    except ImportError:
        print("Django no está instalado en este entorno.")
        sys.exit(2)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
    import django
    django.setup()


if __name__ == '__main__' or 'django' not in sys.modules:
    _bootstrap_django()


from django.db.models import Sum, F, Q  # noqa: E402
from app.models import Producto_Talla, Movimientos_Producto  # noqa: E402


INGRESO_CONCEPTOS = {
    'INGRESO_INICIAL', 'INGRESO_MANUAL', 'RECEPCION_COMPRA',
    'REPOSICION_STOCK', 'DEVOLUCION_CLIENTE', 'TRASPASO_ENTRADA',
    'REGULARIZACION_TRASPASO', 'AJUSTE_POSITIVO', 'DONACION_RECIBIDA',
    'AJUSTE_INVENTARIO_ENTRADA', 'SOBRANTE_INGRESO',
    'DEVOLUCION_NC', 'DEVOLUCION_NC_POST_RECEPCION', 'ANULACION',
    'CAMBIO_PRODUCTO_ENTRADA', 'CORRECCION_STOCK',
}
EGRESO_CONCEPTOS = {
    'VENTA_PUBLICO', 'VENTA_MAYORISTA', 'TRASPASO_SALIDA',
    'AJUSTE_NEGATIVO', 'PERDIDA_ROBO', 'PERDIDA_DETERIORO',
    'DONACION_ENTREGADA', 'DEVOLUCION_PROVEEDOR',
    'AJUSTE_INVENTARIO_SALIDA', 'DESPACHO_COTIZACION',
    'SOBRANTE_DEVUELTO', 'CAMBIO_PRODUCTO_SALIDA', 'CANCELACION',
}


def reconciliar():
    tallas = Producto_Talla.objects.select_related('producto', 'producto__sucursal').all()
    total = tallas.count()
    print(f"Analizando {total} Producto_Talla...")

    divergencias = []
    i = 0
    for t in tallas.iterator(chunk_size=500):
        i += 1
        if i % 500 == 0:
            print(f"  ... {i}/{total}")

        # Cantidad neta según movimientos:
        # - INGRESO suma positivo (tomamos valor absoluto de cantidad)
        # - EGRESO resta (tomamos valor absoluto)
        # En este sistema, los EGRESO se guardan con cantidad negativa y
        # los INGRESO con cantidad positiva. Usamos la suma directa.
        calc = Movimientos_Producto.objects.filter(
            ProductoTalla=t,
            estado='COMPLETADO',
        ).aggregate(total=Sum('cantidad'))['total'] or 0

        stock_real = int(t.stock or 0)
        stock_calc = int(calc)
        diff = stock_real - stock_calc

        if diff != 0:
            divergencias.append({
                'sku': t.sku,
                'talla': t.talla,
                'sucursal': t.producto.sucursal.alias if t.producto and t.producto.sucursal else '-',
                'stock_real': stock_real,
                'stock_calc': stock_calc,
                'diff': diff,
            })

    if not divergencias:
        print("\n✓ Todo cuadra. 0 divergencias detectadas.")
        return 0

    print(f"\n⚠ {len(divergencias)} divergencias detectadas:\n")
    print(f"{'SKU':<12} {'Talla':<8} {'Sucursal':<20} {'Real':>8} {'Calc':>8} {'Diff':>8}")
    print('-' * 70)
    for d in sorted(divergencias, key=lambda x: abs(x['diff']), reverse=True)[:100]:
        print(f"{str(d['sku']):<12} {str(d['talla']):<8} {d['sucursal'][:20]:<20} "
              f"{d['stock_real']:>8} {d['stock_calc']:>8} {d['diff']:>8}")
    if len(divergencias) > 100:
        print(f"... y {len(divergencias) - 100} más.")
    return 1


if __name__ == '__main__':
    sys.exit(reconciliar())
