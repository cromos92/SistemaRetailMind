"""Probe read-only: clasificación de sucursales y ventas públicas mensuales SKECHERS."""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db.models import Count, Sum, F  # noqa
from django.db.models.functions import Abs, ExtractYear, ExtractMonth  # noqa
from app.constants_kardex import CONCEPTOS_VENTA  # noqa
from app.models import AtributoOpcion, Movimientos_Producto, Producto, Sucursal  # noqa

print("=== TODAS LAS SUCURSALES ===")
for s in Sucursal.objects.all().order_by('alias'):
    print(f"  id={s.id:<4} alias={s.alias!r:<20} tipo={s.tipo_sucursal:<20} "
          f"es_cd={s.es_centro_distribucion}")

marca_ids = list(AtributoOpcion.objects.filter(valor__icontains='SKECH').values_list('id', flat=True))
prod_ids = list(Producto.objects.filter(atributo1_id__in=marca_ids, excluir_de_analitica=False).values_list('id', flat=True))
movs_ok = Movimientos_Producto.objects.filter(ProductoTalla__producto_id__in=prod_ids, estado='COMPLETADO')
ventas = movs_ok.filter(concepto__in=CONCEPTOS_VENTA)

print("\n=== VENTA por sucursal + concepto (para ver mayorista vs publico) ===")
for f in (ventas.values('ProductoTalla__producto__sucursal__alias', 'concepto')
          .annotate(u=Sum(Abs('cantidad'))).order_by('ProductoTalla__producto__sucursal__alias', '-u')):
    print(f"  {str(f['ProductoTalla__producto__sucursal__alias']):<18} {f['concepto']:<18} {f['u'] or 0:>10,}")

print("\n=== VENTA mensual TOTAL (todas suc) para estacionalidad (u por mes-calendario) ===")
for f in (ventas.annotate(m=ExtractMonth('fecha')).values('m')
          .annotate(u=Sum(Abs('cantidad'))).order_by('m')):
    print(f"  mes {f['m']:>2}: {f['u'] or 0:>10,}")
