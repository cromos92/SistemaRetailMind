# -*- coding: utf-8 -*-
# Script E: checks finales
import sys
from datetime import date

sys.path.insert(0, r'C:\Users\cromo\AppData\Local\Temp\claude\c--Users-cromo-Documents-DjangoProyects-SistemaRetailMind\15dd0fbe-a29a-4c80-8ad2-c70c318e71b7\scratchpad\ventas_deriv')
from _boot import invocar, ADMIN  # noqa: E402

from django.db.models import Count, Q, Sum  # noqa: E402
from app.models import Dte, Dte_Detalle_Pago, Ticket  # noqa: E402
from app.views_modulo_reportes import TIPOS_TRANSACCION_NC_VENTA  # noqa: E402

FI, FF = date(2026, 7, 1), date(2026, 7, 31)

# 1. resumen completo buckets vs brutas
r = invocar('app.views_modulo_reportes.obtener_documentos_emitidos',
            {'fecha_desde': '2026-07-01', 'fecha_hasta': '2026-07-31',
             'sucursal_id': 'all', 'page': 1, 'per_page': 10})
res = ((r.get('json') or {}).get('resumen') or {})
print('resumen completo:', res)
buckets = ['efectivo', 'tbk_credito', 'tbk_debito', 'tarjeta_comercial', 'convenio',
           'venta_internet', 'transferencia', 'credito_trabajador', 'otros']
sb = sum(int(res.get(b) or 0) for b in buckets)
print('suma buckets metodo pago =', sb, 'vs ventas_brutas =', res.get('ventas_brutas'),
      'delta =', sb - int(res.get('ventas_brutas') or 0))

# 2. DTEs $0-header julio: cuanta plata pagada llevan
cero = Dte.objects.filter(
    fecha_emision__gte=FI, fecha_emision__lte=FF,
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'], monto_con_iva=0,
    descartado=False).exclude(estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO'])
ids0 = list(cero.values_list('id', flat=True))
pag0 = Dte_Detalle_Pago.objects.filter(dte_id__in=ids0).aggregate(m=Sum('monto'), n=Count('id'))
met0 = Dte_Detalle_Pago.objects.filter(dte_id__in=ids0).values('metodo_pago') \
    .annotate(n=Count('id'), m=Sum('monto'))
print('DTEs header $0:', len(ids0), '| pagos asociados $', int(pag0['m'] or 0),
      '| por metodo:', [(x['metodo_pago'], x['n'], int(x['m'] or 0)) for x in met0])
tipos0 = cero.values('tipo_documento').annotate(n=Count('id'))
print('tipos $0:', [(t['tipo_documento'], t['n']) for t in tipos0])
# rango historico del problema
hist = Dte.objects.filter(
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'], monto_con_iva=0, descartado=False,
).exclude(estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO']) \
    .filter(id__in=Dte_Detalle_Pago.objects.values('dte_id'))
print('historico DTEs $0 con pagos:', hist.count(), 'primero:',
      hist.order_by('fecha_emision').values_list('fecha_emision', flat=True).first(),
      'ultimo:', hist.order_by('-fecha_emision').values_list('fecha_emision', flat=True).first())

# 3. listado solo NC (signo en JSON)
rnc = invocar('app.views_modulo_reportes.obtener_documentos_emitidos',
              {'fecha_desde': '2026-07-01', 'fecha_hasta': '2026-07-31',
               'sucursal_id': 'all', 'tipo_documento': 'NOTA_DE_CREDITO',
               'page': 1, 'per_page': 100})
jn = rnc.get('json') or {}
docs = jn.get('documentos') or []
resn = jn.get('resumen') or {}
print('\nsolo-NC: filas', len(docs), 'total_real', jn.get('total_real'),
      '| totales>0 en lista:', sum(1 for d in docs if d['total'] > 0),
      '| resumen brutas', resn.get('ventas_brutas'), 'nc', resn.get('notas_credito'),
      'total_global', resn.get('total_global'))
if docs:
    print('ejemplo NC:', {k: docs[0][k] for k in ('tipo_documento', 'total',
          'es_nota_credito', 'metodo_pago')})

# 4. vendedor con NC en julio -> drilldown con signo
vnc = (Dte.objects.filter(fecha_emision__gte=FI, fecha_emision__lte=FF,
                          tipo_documento='NOTA DE CREDITO',
                          tipo_transaccion__in=TIPOS_TRANSACCION_NC_VENTA,
                          descartado=False, vendedor__isnull=False)
       .values('vendedor_id').annotate(n=Count('id')).order_by('-n').first())
print('\nvendedor con NC julio:', vnc)
if vnc:
    VID = vnc['vendedor_id']
    ragg = invocar('app.views_modulo_reportes.obtener_ventas_por_vendedor_reporte',
                   {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'})
    fila = next((x for x in ((ragg.get('json') or {}).get('vendedores') or [])
                 if x.get('id') == VID), None)
    rdet = invocar('app.views_modulo_reportes.obtener_documentos_vendedor_reporte',
                   {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31',
                    'vendedor_id': VID})
    jd = rdet.get('json') or {}
    ncs = [d for d in (jd.get('documentos') or []) if d.get('es_nota_credito')]
    print('fila:', {x: fila.get(x) for x in ('ventas', 'ventas_brutas', 'devoluciones')}
          if fila else None)
    print('detalle total:', jd.get('total'), '| NC en detalle:', len(ncs),
          'negativas:', sum(1 for d in ncs if d['monto'] < 0))
    print('CHECK fila.ventas == detalle.total:',
          fila.get('ventas') == jd.get('total') if fila else None)

# 5. ventas-internet POS: boletas de los 16 POS
pos_tk = Ticket.objects.filter(
    estado='PAGADO', created_at__date__gte=FI, created_at__date__lte=FF,
    pagos__metodo_pago='VENTA_INTERNET').exclude(modulo_origen='ECOMMERCE').distinct()
print('\nPOS internet julio:', pos_tk.count(),
      'con folio_dte:', pos_tk.filter(folio_dte__isnull=False).count(),
      'tipos:', list(pos_tk.values_list('tipo_dte', flat=True).distinct()))

# 6. atributo-opciones smoke
for tipo in ('categoria', 'genero', 'especialidad', 'marca'):
    ra = invocar('app.views_modulo_reportes.obtener_atributo_opciones', {'tipo': tipo})
    ja = ra.get('json') or {}
    ops = ja.get('opciones') or []
    print(f'atributo-opciones {tipo}: status {ra["status"]} n={len(ops)} '
          f'ms={ra["ms"]} nq={ra["nq"]}')
print('FIN E')
