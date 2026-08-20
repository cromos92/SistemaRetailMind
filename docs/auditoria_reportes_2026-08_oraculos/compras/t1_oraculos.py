# -*- coding: utf-8 -*-
# TANDA 1 — Oráculos compras JULIO 2026 + recon. SOLO LECTURA.
import sys
from datetime import date
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from django.db.models import Sum, Count, Q, F
from app.models import (Dte, Dte_Productos, Dte_Detalle_Pago, Compras,
                        Compras_Producto, Compras_Producto_Talla,
                        Productos_Recepcionados, Empresa, Sucursal)

FI, FF = date(2026, 7, 1), date(2026, 7, 31)
ANULADOS = ['ANULADO', 'CANCELADO', 'RECHAZADO']
NC = 'NOTA DE CREDITO'

print('=== A. UNIVERSO Dte COMPRA JULIO 2026 (crudo, sin excluir nada) ===')
crudo = Dte.objects.filter(tipo_transaccion='COMPRA', fecha_emision__range=(FI, FF))
print('total_crudo:', crudo.count())
print('descartados:', crudo.filter(descartado=True).count())
print('por estado_dte:', dict(crudo.values_list('estado_dte').annotate(n=Count('id'))))
print('por tipo_documento:', dict(crudo.values_list('tipo_documento').annotate(n=Count('id'))))
print('estado_pago values:', dict(crudo.values_list('estado_pago').annotate(n=Count('id'))))
print('receptores:', list(crudo.values('receptor_id', 'receptor__nombre').annotate(n=Count('id'), m=Sum('monto_neto')).order_by('-n')[:8]))
print('sucursal NULL:', crudo.filter(sucursal__isnull=True).count())

print()
print('=== B. ORACULO regla-reporte JULIO (vivos = sin descartado ni anulados) ===')
vivos = crudo.exclude(descartado=True).exclude(estado_dte__in=ANULADOS)
es_nc = Q(tipo_documento=NC)
agg = vivos.aggregate(
    docs=Count('id', filter=~es_nc), ncs=Count('id', filter=es_nc),
    neto=Sum('monto_neto', filter=~es_nc), neto_nc=Sum('monto_neto', filter=es_nc),
    iva=Sum('monto_con_iva', filter=~es_nc), iva_nc=Sum('monto_con_iva', filter=es_nc),
    prov=Count('emisor_id', distinct=True, filter=~es_nc),
    uds_header=Sum('unidades_productos', filter=~es_nc),
)
print('docs=%s ncs=%s prov=%s' % (agg['docs'], agg['ncs'], agg['prov']))
print('neto=%s neto_nc=%s -> inversion_neta=%s' % (agg['neto'], agg['neto_nc'], (agg['neto'] or 0) - (agg['neto_nc'] or 0)))
print('con_iva=%s con_iva_nc=%s' % (agg['iva'], agg['iva_nc']))
print('uds_header(unidades_productos)=%s' % agg['uds_header'])

print()
print('=== C. LINEAS vs RECEPCIONES de esos DTE julio (ceguera 74%?) ===')
ids_fact = list(vivos.filter(~es_nc).values_list('id', flat=True))
print('n facturas vivas:', len(ids_fact))
con_lineas = set(Dte_Productos.objects.filter(dte_id__in=ids_fact).values_list('dte_id', flat=True).distinct())
con_recep = set(Productos_Recepcionados.objects.filter(dte_id__in=ids_fact).values_list('dte_id', flat=True).distinct())
print('con Dte_Productos:', len(con_lineas), '| con Productos_Recepcionados:', len(con_recep),
      '| con alguna:', len(con_lineas | con_recep), '| SIN NINGUNA:', len(ids_fact) - len(con_lineas | con_recep))
uds_lineas = Dte_Productos.objects.filter(dte_id__in=ids_fact).aggregate(u=Sum('stock'))['u']
uds_recep = Productos_Recepcionados.objects.filter(dte_id__in=ids_fact).aggregate(u=Sum('stockArribado'), e=Sum('cantidad_esperada'), f=Sum('cantidad_faltante'))
print('uds lineas Dte_Productos:', uds_lineas, '| uds recep arribadas:', uds_recep['u'],
      '| esperadas:', uds_recep['e'], '| faltantes:', uds_recep['f'])
# monto de facturas ciegas (sin lineas ni recepcion)
ciegos = [i for i in ids_fact if i not in con_lineas and i not in con_recep]
m_ciego = Dte.objects.filter(id__in=ciegos).aggregate(m=Sum('monto_neto'))['m']
print('monto_neto facturas SIN lineas ni recepcion: %s (%s docs)' % (m_ciego, len(ciegos)))

print()
print('=== D. DEUDA ORACULO julio (saldo real via Dte_Detalle_Pago) ===')
pagos = dict(Dte_Detalle_Pago.objects.filter(dte_id__in=ids_fact)
             .values_list('dte_id').annotate(t=Sum('monto')))
tot_iva = dict(Dte.objects.filter(id__in=ids_fact).values_list('id', 'monto_con_iva'))
deuda = 0.0
n_deuda = 0
pagadas = 0
for i in ids_fact:
    saldo = float(tot_iva.get(i) or 0) - float(pagos.get(i) or 0)
    if saldo > 0:
        deuda += saldo
        n_deuda += 1
    else:
        pagadas += 1
print('facturas julio: %s | con saldo>0: %s | saldo total: %.0f | pagadas(saldo<=0): %s' % (len(ids_fact), n_deuda, deuda, pagadas))
print('con algun pago registrado:', len(pagos))

print()
print('=== E. TOP PROVEEDORES JULIO (oraculo ranking por inversion neta) ===')
top = (vivos.values('emisor_id', 'emisor__nombre')
       .annotate(neto=Sum('monto_neto', filter=~es_nc), nc=Sum('monto_neto', filter=es_nc), n=Count('id'))
       .order_by('-neto')[:6])
for t in top:
    print('  prov=%s %s docs=%s neto=%s nc=%s' % (t['emisor_id'], (t['emisor__nombre'] or '')[:30], t['n'], t['neto'], t['nc']))

print()
print('=== F. ORDENES Compras JULIO + AÑO 2026 ===')
oc_jul = Compras.objects.filter(estado__in=['ACTIVA', 'COMPLETADA'], fecha__range=(FI, FF))
print('OC julio vigentes:', oc_jul.count(), '| estados julio (todas):',
      dict(Compras.objects.filter(fecha__range=(FI, FF)).values_list('estado').annotate(n=Count('id'))))
lin_jul = Compras_Producto_Talla.objects.filter(compra_producto__compras__in=oc_jul).aggregate(
    u=Sum('stock'), costo=Sum(F('compra_producto__costo') * F('stock')),
    venta=Sum(F('compra_producto__precioSugerido') * F('stock')))
print('OC julio: uds_pedidas=%s costo=%s venta_lista=%s' % (lin_jul['u'], lin_jul['costo'], lin_jul['venta']))
rec_jul = Productos_Recepcionados.objects.filter(
    compra_producto_talla__compra_producto__compras__in=oc_jul).aggregate(u=Sum('stockArribado'))
print('OC julio: uds_recepcionadas(all-time)=%s' % rec_jul['u'])

anio = Compras.objects.filter(estado__in=['ACTIVA', 'COMPLETADA'], fecha__year=2026)
lin_a = Compras_Producto_Talla.objects.filter(compra_producto__compras__in=anio).aggregate(
    u=Sum('stock'), costo=Sum(F('compra_producto__costo') * F('stock')),
    venta=Sum(F('compra_producto__precioSugerido') * F('stock')))
c, v = float(lin_a['costo'] or 0), float(lin_a['venta'] or 0)
print('OC 2026: n=%s uds=%s costo=%.0f venta_lista=%.0f markup_teorico=%.1f%%' % (
    anio.count(), lin_a['u'], c, v, (v - c) / c * 100 if c else 0))

print()
print('=== G. AÑO 2026 completo (para comparar con el reporte anual) ===')
crudo_a = Dte.objects.filter(tipo_transaccion='COMPRA', fecha_emision__year=2026)
vivos_a = crudo_a.exclude(descartado=True).exclude(estado_dte__in=ANULADOS)
agg_a = vivos_a.aggregate(
    docs=Count('id', filter=~es_nc), ncs=Count('id', filter=es_nc),
    neto=Sum('monto_neto', filter=~es_nc), neto_nc=Sum('monto_neto', filter=es_nc),
    prov=Count('emisor_id', distinct=True, filter=~es_nc))
print('2026 vivos: docs=%s ncs=%s prov=%s neto=%s neto_nc=%s inversion_neta=%s' % (
    agg_a['docs'], agg_a['ncs'], agg_a['prov'], agg_a['neto'], agg_a['neto_nc'],
    (agg_a['neto'] or 0) - (agg_a['neto_nc'] or 0)))
print('2026 anulados/descartados fuera:', crudo_a.count() - vivos_a.count())
ids_a = list(vivos_a.filter(~es_nc).values_list('id', flat=True))
con_l_a = Dte_Productos.objects.filter(dte_id__in=ids_a).values('dte_id').distinct().count()
con_r_a = Productos_Recepcionados.objects.filter(dte_id__in=ids_a).values('dte_id').distinct().count()
print('2026 facturas=%s con_lineas=%s con_recepcion=%s' % (len(ids_a), con_l_a, con_r_a))

print()
print('=== H. Fixtures para tandas siguientes ===')
from django.contrib.auth import get_user_model
U = get_user_model()
admin = (U.objects.filter(rol='administrador', is_active=True).values('id', 'username').first()
         or U.objects.filter(is_superuser=True, is_active=True).values('id', 'username').first())
print('admin:', admin)
cds = list(Sucursal.objects.filter(es_centro_distribucion=True).values('id', 'alias', 'empresa_id'))
print('CDs:', cds)
# proveedor top julio para invocaciones acotadas
print('proveedor_top_julio:', top[0]['emisor_id'] if top else None)
# recepciones julio (para recepciones-detallado)
rec_prd = Productos_Recepcionados.objects.filter(
    Q(fecha_recepcion__date__range=(FI, FF)) | (Q(fecha_recepcion__isnull=True) & Q(fecha__range=(FI, FF))))
print('Productos_Recepcionados julio (regla reporte):', rec_prd.count(),
      'uds:', rec_prd.aggregate(u=Sum('stockArribado'))['u'])
print('FIN T1')
