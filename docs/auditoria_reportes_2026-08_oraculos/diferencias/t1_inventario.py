# -*- coding: utf-8 -*-
# AUDITORIA read-only - tanda 1: permisos sembrados + inventario de datos.
# SOLO SELECT. Sin save/update/delete/create.
import time
from datetime import date, timedelta

from django.db.models import Q, Sum, Count, F, Value, IntegerField, Max, Min
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, Movimientos_Producto, Productos_Recepcionados,
    Sucursal, OpcionMenu, PermisoRol, PermisoUsuario,
)

T0 = time.time()
EXCL = ('ANULADO', 'CANCELADO', 'RECHAZADO')


def p(*a):
    print(*a, flush=True)


p('== A. PERMISOS (BD prod, hoy) ==')
for cod in ('reporte_diferencias_recepcion', 'reporte_mercaderia_transito'):
    om = OpcionMenu.objects.filter(codigo=cod).values('id', 'codigo', 'activo', 'url').first()
    p(cod, '->', om)
    if om:
        roles = list(PermisoRol.objects.filter(opcion_menu_id=om['id'], puede_ver=True)
                     .values_list('rol', flat=True))
        p('   roles con puede_ver:', roles)
        p('   overrides PermisoUsuario:', PermisoUsuario.objects.filter(opcion_menu_id=om['id']).count())
try:
    from app.models import PermisoSucursal
    for cod in ('reporte_diferencias_recepcion', 'reporte_mercaderia_transito'):
        n = PermisoSucursal.objects.filter(opcion_menu__codigo=cod, habilitado=False).count()
        p('   PermisoSucursal deshabilitado para', cod, ':', n)
except Exception as e:
    p('   PermisoSucursal no disponible:', e)

p('== B. UNIVERSO Productos_Recepcionados ==')
PR = Productos_Recepcionados.objects
p('total filas:', PR.count())
con_dte = PR.filter(dte__isnull=False)
p('con dte:', con_dte.count(), '| sin dte (compras s/DTE, fuera del reporte):',
  PR.filter(dte__isnull=True).count())
p('con dte descartado (excluidas):', con_dte.filter(dte__descartado=True).count())
p('con dte estado ANULADO/CANCELADO/RECHAZADO (excluidas):',
  con_dte.filter(dte__estado_dte__in=EXCL).count())

base = con_dte.exclude(dte__descartado=True).exclude(dte__estado_dte__in=EXCL)
p('universo del reporte (base):', base.count())
p('fecha_recepcion NULL (caen al fallback fecha auto_now):',
  base.filter(fecha_recepcion__isnull=True).count())
p('fecha_recepcion NULL y CON diferencia:',
  base.filter(fecha_recepcion__isnull=True)
      .filter(Q(cantidad_faltante__gt=0) | Q(cantidad_danada__gt=0) | Q(cantidad_sobrante__gt=0)).count())
p('dte_producto NULL (valorizan a costo 0):', base.filter(dte_producto__isnull=True).count())
p('dte_producto NULL y con diferencia:',
  base.filter(dte_producto__isnull=True)
      .filter(Q(cantidad_faltante__gt=0) | Q(cantidad_danada__gt=0) | Q(cantidad_sobrante__gt=0)).count())
p('linea con costo NULL:', base.filter(dte_producto__isnull=False, dte_producto__costo__isnull=True).count(),
  '| costo=0:', base.filter(dte_producto__costo=0).count())
p('sucursal_destino NULL:', base.filter(sucursal_destino__isnull=True).count())

p('-- base por estado --')
for row in (base.values('estado')
            .annotate(n=Count('id'), falt=Sum('cantidad_faltante'),
                      dan=Sum('cantidad_danada'), sob=Sum('cantidad_sobrante'))
            .order_by('-n')):
    p('  ', row)

p('-- faltantes con NC vigente sobre su dte --')
falt = base.filter(cantidad_faltante__gt=0)
falt_dtes = list(falt.values_list('dte_id', flat=True).distinct())
p('lineas faltante>0:', falt.count(), '| dtes distintos:', len(falt_dtes))
ncs = (Dte.objects.filter(documento_afectado_id__in=falt_dtes)
       .exclude(estado_dte__in=('ANULADO', 'CANCELADO')))
dtes_con_nc = set(ncs.values_list('documento_afectado_id', flat=True))
p('dtes con faltante que YA tienen NC/ajuste vigente:', len(dtes_con_nc))
p('  esas NC por redujo_lineas_documento:',
  list(ncs.values('redujo_lineas_documento').annotate(n=Count('id'))))
falt_con_nc = falt.filter(dte_id__in=list(dtes_con_nc)).aggregate(
    n=Count('id'), u=Sum('cantidad_faltante'))
p('  lineas faltante>0 en esos dtes:', falt_con_nc)

p('== B2. JULIO 2026 con el MISMO filtro de fecha del reporte ==')
desde, hasta = date(2026, 7, 1), date(2026, 7, 31)
filtro = (Q(fecha_recepcion__date__gte=desde, fecha_recepcion__date__lte=hasta)
          | (Q(fecha_recepcion__isnull=True) & Q(fecha__gte=desde, fecha__lte=hasta)))
jul = base.filter(filtro)
p('julio:', jul.aggregate(n=Count('id'), docs=Count('dte_id', distinct=True),
                          esp=Sum('cantidad_esperada'), rec=Sum('stockArribado'),
                          falt=Sum('cantidad_faltante'), dan=Sum('cantidad_danada'),
                          sob=Sum('cantidad_sobrante')))
p('-- top 6 DTEs de julio con faltantes/danadas --')
for row in (jul.filter(Q(cantidad_faltante__gt=0) | Q(cantidad_danada__gt=0))
            .values('dte_id', 'dte__numero_documento', 'dte__tipo_documento',
                    'dte__emisor__nombre', 'sucursal_destino__alias')
            .annotate(esp=Sum('cantidad_esperada'), rec=Sum('stockArribado'),
                      falt=Sum('cantidad_faltante'), dan=Sum('cantidad_danada'),
                      sob=Sum('cantidad_sobrante'))
            .order_by('-falt')[:6]):
    p('  ', row)

p('== C. Solicitudes de regularizacion ==')
try:
    from app.models import Solicitud_Regularizacion as SR
except ImportError:
    from app.models.compras import Solicitud_Regularizacion as SR
for row in SR.objects.values('estado').annotate(n=Count('id')).order_by('-n'):
    p('  ', row)

p('== D. Transito: sizing ==')
hoy = timezone.localdate()
ms = Movimientos_Producto.objects
p('TRASPASO_SALIDA con dte NULL (invisibles al reporte):',
  ms.filter(concepto='TRASPASO_SALIDA', dte__isnull=True).count())
for dias in (90, 365):
    d0 = hoy - timedelta(days=dias)
    n = (ms.filter(concepto='TRASPASO_SALIDA', dte__isnull=False, fecha__gte=d0)
         .exclude(dte__descartado=True).exclude(dte__estado_dte__in=EXCL)
         .values('dte_id').distinct().count())
    p(f'dtes con TRASPASO_SALIDA ultimos {dias}d (sin muertos):', n)
p('dtes TRASPASO_SALIDA historia completa:',
  ms.filter(concepto='TRASPASO_SALIDA', dte__isnull=False).values('dte_id').distinct().count())
p('rango fechas TRASPASO_SALIDA:', ms.filter(concepto='TRASPASO_SALIDA').aggregate(Min('fecha'), Max('fecha')))

p(f'[tiempo total tanda 1: {time.time()-T0:.1f}s]')
