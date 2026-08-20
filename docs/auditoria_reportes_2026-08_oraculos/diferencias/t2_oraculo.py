# -*- coding: utf-8 -*-
# AUDITORIA read-only - tanda 2: permisos, solicitudes, DTEs con faltante,
# y ORACULO independiente de mercaderia en transito (90 dias, alcance admin).
# SOLO SELECT.
import os
import sys
import time
from datetime import date, timedelta
from collections import defaultdict

import django

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db.models import Q, Sum, Count, F, Value, IntegerField, Max, Min
from django.db.models.functions import Abs, Coalesce
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, Movimientos_Producto, Productos_Recepcionados,
    Sucursal, OpcionMenu, PermisoRol, PermisoUsuario,
)

T0 = time.time()
EXCL = ('ANULADO', 'CANCELADO', 'RECHAZADO')
ENTRADA = ('TRASPASO_ENTRADA', 'REGULARIZACION_TRASPASO')


def p(*a):
    print(*a, flush=True)


p('== A. PERMISOS (BD prod) ==')
for cod in ('reporte_diferencias_recepcion', 'reporte_mercaderia_transito'):
    om = OpcionMenu.objects.filter(codigo=cod).values('id', 'codigo', 'activo', 'url_path').first()
    p(cod, '->', om)
    if om:
        roles = list(PermisoRol.objects.filter(opcion_menu_id=om['id'], puede_ver=True)
                     .values_list('rol', flat=True))
        p('   roles con puede_ver:', sorted(roles))
        p('   overrides PermisoUsuario:', PermisoUsuario.objects.filter(opcion_menu_id=om['id']).count())
        try:
            from app.models import PermisoSucursal
            p('   PermisoSucursal habilitado=False:',
              PermisoSucursal.objects.filter(opcion_menu_id=om['id'], habilitado=False).count())
        except Exception as e:
            p('   PermisoSucursal n/d:', e)

p('== B. Solicitudes de regularizacion (por estado) ==')
try:
    from app.models import Solicitud_Regularizacion as SR
except ImportError:
    from app.models.compras import Solicitud_Regularizacion as SR
for row in SR.objects.values('estado').annotate(n=Count('id')).order_by('-n'):
    p('  ', row)

p('== C. Los DTEs con faltante>0 hoy (universo diferencias) ==')
base = (Productos_Recepcionados.objects.filter(dte__isnull=False)
        .exclude(dte__descartado=True).exclude(dte__estado_dte__in=EXCL))
falt = base.filter(cantidad_faltante__gt=0)
p('rango fecha_recepcion de lineas faltante>0:',
  falt.aggregate(Min('fecha_recepcion'), Max('fecha_recepcion')))
for row in (falt.values('dte_id', 'dte__numero_documento', 'dte__tipo_documento',
                        'dte__estado_dte', 'dte__fecha_emision', 'sucursal_destino__alias')
            .annotate(lineas=Count('id'), esp=Sum('cantidad_esperada'),
                      rec=Sum('stockArribado'), f=Sum('cantidad_faltante'))
            .order_by('-f')):
    p('  ', row)

p('== D. ORACULO transito 90d (alcance total/admin) ==')
hoy = timezone.localdate()
DIAS = 90
desde = hoy - timedelta(days=DIAS)
ms = Movimientos_Producto.objects

p('TRASPASO_SALIDA con sucursal_origen NULL (quedarian fuera del scoping):',
  ms.filter(concepto='TRASPASO_SALIDA', sucursal_origen__isnull=True).count())

dte_ids = list(
    ms.filter(concepto='TRASPASO_SALIDA', dte__isnull=False, fecha__gte=desde)
    .exclude(dte__descartado=True).exclude(dte__estado_dte__in=EXCL)
    .order_by().values_list('dte_id', flat=True).distinct()
)
p(f'dtes seleccionados (90d): {len(dte_ids)}  (tope del reporte: 2000)')
n365 = (ms.filter(concepto='TRASPASO_SALIDA', dte__isnull=False, fecha__gte=hoy - timedelta(days=365))
        .exclude(dte__descartado=True).exclude(dte__estado_dte__in=EXCL)
        .values('dte_id').distinct().count())
p('dtes 365d:', n365)
p('dtes historia completa:',
  ms.filter(concepto='TRASPASO_SALIDA', dte__isnull=False).values('dte_id').distinct().count())

# enviadas + fecha salida por dte
env = {}
for r in (ms.filter(concepto='TRASPASO_SALIDA', dte_id__in=dte_ids)
          .values('dte_id')
          .annotate(u=Sum(Abs('cantidad')), f0=Min('fecha'))
          .order_by()):
    env[r['dte_id']] = (r['u'] or 0, r['f0'])

# recibidas (conceptos oficiales de entrada) por dte
rec = dict(
    ms.filter(concepto__in=ENTRADA, dte_id__in=dte_ids)
    .values_list('dte_id').annotate(u=Sum(Abs('cantidad'))).order_by()
)

# otros INGRESOS colgados del MISMO dte con conceptos distintos (lo que el reporte NO cuenta)
p('-- ingresos al mismo dte con conceptos NO contados por el reporte --')
otros = defaultdict(dict)
for r in (ms.filter(dte_id__in=dte_ids, tipo_movimiento='INGRESO')
          .exclude(concepto__in=ENTRADA).exclude(concepto='TRASPASO_SALIDA')
          .values('concepto')
          .annotate(u=Sum(Abs('cantidad')), n=Count('id'), docs=Count('dte_id', distinct=True))
          .order_by('-u')):
    p('  ', r)
otros_por_dte = dict(
    ms.filter(dte_id__in=dte_ids, tipo_movimiento='INGRESO')
    .exclude(concepto__in=ENTRADA).exclude(concepto='TRASPASO_SALIDA')
    .values_list('dte_id').annotate(u=Sum(Abs('cantidad'))).order_by()
)

# NCs hijas: vivas y muertas, con sus ingresos
nc_all = list(Dte.objects.filter(documento_afectado_id__in=dte_ids)
              .values('id', 'documento_afectado_id', 'estado_dte', 'redujo_lineas_documento'))
nc_ids = [n['id'] for n in nc_all]
nc_map = {n['id']: n for n in nc_all}
nc_ing = dict(
    ms.filter(dte_id__in=nc_ids, tipo_movimiento__in=('INGRESO', 'DEVOLUCION'))
    .values_list('dte_id').annotate(u=Sum(Abs('cantidad'))).order_by()
) if nc_ids else {}
dev_por_dte = defaultdict(int)       # lo que el reporte descuenta (todas las NC)
dev_muertas_por_dte = defaultdict(int)  # solo NC anuladas/canceladas (no debieran descontar)
for nc_id, u in nc_ing.items():
    n = nc_map[nc_id]
    dev_por_dte[n['documento_afectado_id']] += (u or 0)
    if n['estado_dte'] in ('ANULADO', 'CANCELADO'):
        dev_muertas_por_dte[n['documento_afectado_id']] += (u or 0)

# costo unitario promedio por dte (misma formula del reporte)
costos = {}
for r in (Dte_Productos.objects.filter(dte_id__in=dte_ids, activo=True)
          .values('dte_id')
          .annotate(v=Coalesce(Sum(F('costo') * F('stock'), output_field=IntegerField()),
                               Value(0), output_field=IntegerField()),
                    u=Coalesce(Sum('stock'), Value(0), output_field=IntegerField()))
          .order_by()):
    u = r['u'] or 0
    costos[r['dte_id']] = int((r['v'] or 0) / u) if u else 0

# lo documentado en las lineas del dte (camino independiente del kardex)
docu = dict(
    Dte_Productos.objects.filter(dte_id__in=dte_ids, activo=True)
    .values_list('dte_id').annotate(u=Sum('stock')).order_by()
)

# recepciones registradas (Productos_Recepcionados) por dte
pr = {}
for r in (Productos_Recepcionados.objects.filter(dte_id__in=dte_ids)
          .values('dte_id')
          .annotate(esp=Sum('cantidad_esperada'), arr=Sum('stockArribado'),
                    f=Sum('cantidad_faltante'), n=Count('id'))
          .order_by()):
    pr[r['dte_id']] = r

# clasificacion replicada
DIAS_NORMAL = 3
resumen = defaultdict(int)
sin_recibir = []
for dte_id in dte_ids:
    enviadas, f0 = env.get(dte_id, (0, None))
    recibidas = rec.get(dte_id, 0) or 0
    dev = dev_por_dte.get(dte_id, 0)
    pend = max(0, enviadas - recibidas - dev)
    dias_tr = (hoy - f0).days if f0 else 0
    costo = costos.get(dte_id, 0)
    if recibidas > enviadas:
        sit = 'SOBRE_RECIBIDO'
    elif pend <= 0:
        sit = 'RECIBIDO'
    elif dias_tr <= DIAS_NORMAL:
        sit = 'EN_TRANSITO'
    else:
        sit = 'SIN_RECIBIR'
    resumen['documentos'] += 1
    resumen['unidades_enviadas'] += enviadas
    resumen['unidades_recibidas'] += recibidas
    resumen['unidades_pendientes'] += pend
    resumen['valor_pendiente'] += pend * costo
    if sit == 'SIN_RECIBIR':
        resumen['docs_sin_recibir'] += 1
        resumen['unidades_sin_recibir'] += pend
        resumen['valor_sin_recibir'] += pend * costo
        sin_recibir.append((dte_id, enviadas, recibidas, dev, pend, dias_tr, costo))
    elif sit == 'EN_TRANSITO':
        resumen['docs_en_transito'] += 1
    elif sit == 'SOBRE_RECIBIDO':
        resumen['docs_sobre_recibidos'] += 1
        resumen['unidades_sobre_recibidas'] += (recibidas - enviadas)
    else:
        resumen['docs_recibidos'] += 1

p('-- ORACULO resumen 90d --')
for k in sorted(resumen):
    p(f'   {k}: {resumen[k]}')

p(f'-- docs SIN_RECIBIR: {len(sin_recibir)} --')
folios = dict(Dte.objects.filter(id__in=[x[0] for x in sin_recibir])
              .values_list('id', 'numero_documento'))
fp_pr_completa = []
fp_llego_todo = []
for (dte_id, enviadas, recibidas, dev, pend, dias_tr, costo) in sorted(sin_recibir, key=lambda x: -x[4]):
    r = pr.get(dte_id)
    otros_u = otros_por_dte.get(dte_id, 0)
    marca = []
    if r and (r['arr'] or 0) >= (r['esp'] or 0) and (r['esp'] or 0) > 0 and (r['f'] or 0) == 0:
        marca.append('PR_COMPLETA')
        fp_pr_completa.append(dte_id)
    if otros_u:
        marca.append(f'OTROS_INGRESOS={otros_u}')
        fp_llego_todo.append(dte_id)
    if dev_muertas_por_dte.get(dte_id):
        marca.append(f'NC_MUERTA_DESCONTADA={dev_muertas_por_dte[dte_id]}')
    p(f'   dte={dte_id} folio={folios.get(dte_id)} env={enviadas} rec={recibidas} '
      f'devNC={dev} pend={pend} dias={dias_tr} valor={pend*costo} '
      f'docu={docu.get(dte_id)} PR={r and dict(r)} {" ".join(marca)}')

p('-- resumen falsos positivos --')
p('   SIN_RECIBIR con recepcion COMPLETA en Productos_Recepcionados:', len(fp_pr_completa), fp_pr_completa)
p('   SIN_RECIBIR con ingresos no contados (p.ej. ANULACION_REGULARIZACION):', len(fp_llego_todo), fp_llego_todo)
p('   dtes (todo el universo 90d) con NC muerta descontada:',
  {k: v for k, v in dev_muertas_por_dte.items() if v})

p('== E. Cruce diferencias vs transito (misma guia en ambos) ==')
falt_dtes = set(falt.values_list('dte_id', flat=True))
en_ambos = [d for d in dte_ids if d in falt_dtes]
p('dtes con faltante>0 (diferencias) que ademas estan en la ventana transito 90d:', en_ambos)
for d in en_ambos:
    enviadas, f0 = env.get(d, (0, None))
    recibidas = rec.get(d, 0) or 0
    dev = dev_por_dte.get(d, 0)
    pend = max(0, enviadas - recibidas - dev)
    p(f'   dte={d} folio={folios.get(d) or Dte.objects.filter(id=d).values_list("numero_documento", flat=True).first()} '
      f'env={enviadas} rec={recibidas} devNC={dev} pend_transito={pend} '
      f'faltante_diferencias={falt.filter(dte_id=d).aggregate(u=Sum("cantidad_faltante"))["u"]}')

p(f'[tiempo total tanda 2: {time.time()-T0:.1f}s]')
