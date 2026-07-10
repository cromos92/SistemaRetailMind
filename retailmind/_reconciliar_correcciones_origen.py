"""
Reconcilia las correcciones de recepción que, por el bug de
corregir_recepcion_emisor_api, sumaron stock a la talla del ORIGEN en vez del
DESTINO.

Detección (inequívoca): un movimiento TRASPASO_ENTRADA (INGRESO, COMPLETADO)
cuya talla pertenece a la MISMA sucursal que emitió el DTE (origen). Una entrada
de recepción SIEMPRE debe caer en el destino, nunca en el origen.

Uso (desde retailmind/):
    python _reconciliar_correcciones_origen.py            # DRY-RUN (solo lista)
    python _reconciliar_correcciones_origen.py --apply    # aplica la reconciliación

Qué hace con --apply, por cada movimiento mal dirigido de `cant` unidades:
    - resta `cant` de la talla ORIGEN (donde cayó por error)
    - suma  `cant` a la talla DESTINO (creándola si no existe)
    - deja 2 movimientos CORRECCION_STOCK de auditoría (EGRESO origen / INGRESO destino)
NO toca el movimiento original ni el estado de la recepción (ya quedó OK).
"""
import os
import sys

for _s in (sys.stdout, sys.stderr):
    if _s is not None and hasattr(_s, 'reconfigure'):
        try:
            _s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django
django.setup()

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from app.models import Movimientos_Producto, Producto_Talla, Producto

APPLY = '--apply' in sys.argv


def talla_destino_de(dte, talla_origen):
    """Talla del SKU en la sucursal destino real del traspaso (crea si no existe)."""
    mov_salida = dte.dte_movimientos.filter(concepto='TRASPASO_SALIDA').first()
    suc_destino = mov_salida.sucursal_destino if mov_salida else None
    if not suc_destino:
        return None, None
    td = Producto_Talla.objects.filter(
        sku=talla_origen.sku, producto__sucursal=suc_destino
    ).select_related('producto').first()
    if td:
        return td, suc_destino
    po = talla_origen.producto
    pd, _ = Producto.objects.get_or_create(
        articulo=po.articulo, sucursal=suc_destino,
        atributo1=po.atributo1, atributo2=po.atributo2,
        atributo3=po.atributo3, atributo4=po.atributo4,
        defaults={
            'descripcion': po.descripcion, 'categoria': po.categoria,
            'costo': po.costo, 'sobreprecio': po.sobreprecio,
            'precioventa': po.precioventa, 'precioSugerido': po.precioSugerido,
            'tipo_talla': po.tipo_talla, 'guia_talla': po.guia_talla,
        },
    )
    td = Producto_Talla.objects.create(
        producto=pd, talla=talla_origen.talla, sku=talla_origen.sku, stock=0
    )
    return td, suc_destino


def main():
    # TRASPASO_ENTRADA cuya talla está en la MISMA sucursal que emitió el DTE = mal dirigida.
    malas = list(
        Movimientos_Producto.objects.filter(
            concepto='TRASPASO_ENTRADA',
            tipo_movimiento='INGRESO',
            estado='COMPLETADO',
            dte__isnull=False,
        )
        .filter(ProductoTalla__producto__sucursal_id=F('dte__sucursal_id'))
        .select_related('dte__sucursal', 'ProductoTalla__producto__sucursal')
        .order_by('id')
    )

    print("=" * 78)
    print(f"CORRECCIONES MAL DIRIGIDAS (TRASPASO_ENTRADA en la talla del ORIGEN): {len(malas)}")
    print("Modo:", "APLICAR" if APPLY else "DRY-RUN (solo lista)")
    print("=" * 78)

    if not malas:
        print("No hay nada que reconciliar.")
        return

    hoy = timezone.now()
    aplicados = 0
    for m in malas:
        dte = m.dte
        talla_ori = m.ProductoTalla
        cant = m.cantidad or 0
        td, suc_dest = talla_destino_de(dte, talla_ori)
        origen_alias = talla_ori.producto.sucursal.alias if talla_ori.producto and talla_ori.producto.sucursal else '?'
        destino_alias = suc_dest.alias if suc_dest else '?'
        print(
            f"mov {m.id} | DTE #{dte.numero_documento} | sku {talla_ori.sku} | "
            f"+{cant} cayó en {origen_alias} (talla {talla_ori.id}) → debe ir a {destino_alias} "
            f"(talla {td.id if td else '—'})"
        )
        if not APPLY:
            continue
        if not td:
            print("   ⚠ sin destino identificable, se omite")
            continue
        if talla_ori.stock < cant:
            print(f"   ⚠ {origen_alias} tiene {talla_ori.stock} < {cant}, se omite (revisar manual)")
            continue
        with transaction.atomic():
            Producto_Talla.objects.filter(id=talla_ori.id).update(stock=F('stock') - cant)
            Producto_Talla.objects.filter(id=td.id).update(stock=F('stock') + cant)
            base = dict(dte=dte, responsable='reconciliacion', estado='COMPLETADO',
                        fecha=hoy.date(), hora=hoy.time())
            Movimientos_Producto.objects.create(
                ProductoTalla=talla_ori, cantidad=-cant, tipo_movimiento='EGRESO',
                concepto='CORRECCION_STOCK', sucursal_origen=talla_ori.producto.sucursal,
                observaciones=f'Reconciliación corrección mal dirigida mov {m.id}: sale de {origen_alias}',
                **base,
            )
            Movimientos_Producto.objects.create(
                ProductoTalla=td, cantidad=cant, tipo_movimiento='INGRESO',
                concepto='CORRECCION_STOCK', sucursal_destino=suc_dest,
                observaciones=f'Reconciliación corrección mal dirigida mov {m.id}: entra a {destino_alias}',
                **base,
            )
        aplicados += 1
        print(f"   ✓ reconciliado: {origen_alias} -{cant}, {destino_alias} +{cant}")

    print("=" * 78)
    if APPLY:
        print(f"APLICADO: {aplicados} correcciones reconciliadas.")
    else:
        print("DRY-RUN. Para aplicar: python _reconciliar_correcciones_origen.py --apply")


if __name__ == '__main__':
    main()
