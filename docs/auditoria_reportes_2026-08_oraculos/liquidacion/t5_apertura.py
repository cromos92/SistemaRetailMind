# -*- coding: utf-8 -*-
# TANDA 5 — apertura migracion dentro de "ingresado" del sell-through
import sys
from datetime import date
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
from django.db.models import Sum, Count, BigIntegerField, Q
from django.db.models.functions import Abs, ExtractYear
from django.contrib.auth import get_user_model
from app.models import AtributoOpcion, EmpresaUser, Movimientos_Producto, Sucursal
from app.constants_kardex import CONCEPTOS_ABASTECIMIENTO, REF_SALDO_INICIAL_SINTETICO
BI = BigIntegerField()
P = print
User = get_user_model()
admin = (User.objects.filter(rol='administrador', is_active=True).first()
         or User.objects.filter(is_superuser=True, is_active=True).first())
emp_ids = list(EmpresaUser.objects.filter(user=admin, status=True)
               .values_list('empresa_id', flat=True).distinct())
all_ids = list(Sucursal.objects.filter(empresa_id__in=emp_ids)
               .values_list('id', flat=True))
sk = AtributoOpcion.objects.filter(atributo__nombre__icontains='marca',
                                   valor__icontains='SKECHERS').first()
ab = Movimientos_Producto.objects.filter(
    ProductoTalla__producto__atributo1_id=sk.id,
    ProductoTalla__producto__excluir_de_analitica=False,
    ProductoTalla__producto__sucursal_id__in=all_ids,
    estado='COMPLETADO', concepto__in=CONCEPTOS_ABASTECIMIENTO)
por_anio = list(ab.annotate(a=ExtractYear('fecha')).values('a')
                .annotate(u=Sum(Abs('cantidad'), output_field=BI),
                          u_mig=Sum(Abs('cantidad'),
                                    filter=Q(referencia_externa__icontains=REF_SALDO_INICIAL_SINTETICO),
                                    output_field=BI),
                          u_ini=Sum(Abs('cantidad'), filter=Q(concepto='INGRESO_INICIAL'),
                                    output_field=BI))
                .order_by('a'))
P('SKECHERS abastecimiento por anio (u, de-eso ref-MIGRACION, de-eso INGRESO_INICIAL):')
for r in por_anio:
    P('  %s: u=%s mig=%s ingreso_inicial=%s' % (r['a'], r['u'], r['u_mig'], r['u_ini']))
P('FIN T5')
