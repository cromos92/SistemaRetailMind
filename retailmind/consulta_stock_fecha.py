# Verifica que todo lo que tiene stock quede con fecha tras el fallback de
# ultima_fecha_ingreso -> fecha_creacion. Descartable: podés borrarlo.
# Correr desde retailmind/:  Get-Content consulta_stock_fecha.py | python manage.py shell
from django.db.models import Sum, Min
from app.models import Producto_Talla, Movimientos_Producto

CONCEPTOS_RECEPCION_STOCK = (
    'RECEPCION_COMPRA', 'INGRESO_INICIAL', 'INGRESO_MANUAL',
    'REPOSICION_STOCK', 'SOBRANTE_INGRESO',
)
RUT = '76104936-4'

# 1) SKUs con stock > 0 (consolidado entre sucursales), con su fecha_creacion
con_stock = {
    str(r['sku']): r
    for r in (
        Producto_Talla.objects
        .filter(producto__sucursal__empresa__rut=RUT)
        .values('sku')
        .annotate(stock_total=Sum('stock'), fcrea=Min('producto__fecha_creacion'))
        .filter(stock_total__gt=0)
    )
}
total = len(con_stock)

# 2) SKUs que tienen recepción real (la fecha "estricta")
recep_all = set(
    str(s)
    for s in (
        Movimientos_Producto.objects
        .filter(
            concepto__in=CONCEPTOS_RECEPCION_STOCK,
            ProductoTalla__producto__sucursal__empresa__rut=RUT,
        )
        .values_list('ProductoTalla__sku', flat=True)
        .distinct()
    )
)
recep = recep_all & set(con_stock.keys())

con_recep = len(recep)
sin_recep_con_fc = sum(1 for s, r in con_stock.items() if s not in recep and r['fcrea'])
sin_recep_sin_fc = sum(1 for s, r in con_stock.items() if s not in recep and not r['fcrea'])

print(f"SKUs con stock > 0            : {total}")
print(f"  - con recepcion real       : {con_recep}  (ya tenian fecha)")
print(f"  - sin recepcion, con fcrea  : {sin_recep_con_fc}  (AHORA tienen fecha por el fallback)")
print(f"  - sin recepcion, SIN fcrea  : {sin_recep_sin_fc}  (seguirian NULL)")
print(f"  => con fecha tras fallback  : {con_recep + sin_recep_con_fc} / {total}")

nulos = [s for s, r in con_stock.items() if s not in recep and not r['fcrea']]
if nulos:
    print("SKUs que SEGUIRIAN null:", nulos[:50], "..." if len(nulos) > 50 else "")
else:
    print("OK: todo lo que tiene stock queda con fecha.")
