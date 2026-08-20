# -*- coding: utf-8 -*-
"""Tanda 4 (SOLO LECTURA): default-load despachos_por_proveedor + modo historico."""
import json
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.getcwd())

import django

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.conf import settings
settings.DEBUG = True

from django.db import connection, reset_queries, transaction
from django.db.models import F, Q, Sum
from django.test import RequestFactory
from django.contrib.auth import get_user_model

from app.models import Dte, Movimientos_Producto, Producto, Producto_Talla
from app.constants_kardex import REF_SALDO_INICIAL_SINTETICO
from app.views import reporte_despachos_por_proveedor
from app.views_modulo_reportes import obtener_reporte_movimientos_sucursal

User = get_user_model()
admin = User.objects.filter(username='javier').first()


def call(view, params, user):
    rf = RequestFactory()
    req = rf.get('/x', data=params)
    req.user = user
    req.session = {'idSucursalActual': None, 'idEmpresaActual': None}
    reset_queries()
    t0 = time.perf_counter()
    with transaction.atomic():
        resp = view(req)
        transaction.set_rollback(True)
    return resp, round(time.perf_counter() - t0, 2), len(connection.queries)


# E1: volumen DTE compra historico (lo que carga la pagina por defecto)
excl = ['BOLETA ELECTRONICA', 'BOLETA PAPEL', 'TICKET']
n_dtes = (Dte.objects.filter(tipo_transaccion='COMPRA')
          .exclude(tipo_documento__in=excl)
          .exclude(emisor_id=F('receptor_id')).count())
print('E1 DTEs compra historicos (universo default): %s' % n_dtes)

# E2: carga por defecto (sin fechas) como hace el template al abrir
r, t, q = call(reporte_despachos_por_proveedor,
               {'page': '1', 'page_size': '25'}, admin)
j = json.loads(r.content)
print('E2 default-load despachos_por_proveedor: status=%s t=%ss q=%d' %
      (r.status_code, t, q))
if j.get('success'):
    res = j['resumen']
    print('  resumen: dtes=%s ingresadas=%s despachadas=%s monto=%s' %
          (res.get('total_dtes'), res.get('total_unidades_ingresadas'),
           res.get('total_unidades_despachadas'), res.get('total_monto_compras')))
    fila0 = j['data'][0] if j.get('data') else None
    if fila0:
        print('  fila0: ingresado=%s despachado=%s saldo=%s (saldo==ingresado? %s)' %
              (fila0['total_ingresado'], fila0['total_despachado'],
               fila0['saldo_restante'],
               fila0['saldo_restante'] == fila0['total_ingresado']))

# E3: modo historico de movimientos-sucursal (sin fechas) para BUFANDAS
r3, t3, q3 = call(obtener_reporte_movimientos_sucursal,
                  {'marca_id': '299', 'mostrar': 'todo', 'solo_tiendas': 'true'},
                  admin)
j3 = json.loads(r3.content)
fila = next((f for f in j3.get('datos', []) if f['articulo'] == 'BUFANDAS'), None)
if fila:
    d = fila['sucursales'].get('PAO4')
    if d:
        prods = list(Producto.objects.filter(
            articulo='BUFANDAS', sucursal_id=d['sucursal_id'], atributo1_id=299,
            excluir_de_analitica=False).values_list('id', flat=True))
        base_m = Movimientos_Producto.objects.filter(
            ProductoTalla__producto_id__in=prods, estado='COMPLETADO'
        ).exclude(concepto='INGRESO_INICIAL',
                  referencia_externa=REF_SALDO_INICIAL_SINTETICO)
        ent = base_m.filter(cantidad__gt=0).aggregate(s=Sum('cantidad'))['s'] or 0
        sal = abs(base_m.filter(cantidad__lt=0).aggregate(s=Sum('cantidad'))['s'] or 0)
        stock_hoy = Producto_Talla.objects.filter(producto_id__in=prods).aggregate(
            s=Sum('stock'))['s'] or 0
        print('E3 historico BUFANDAS @ PAO4: reporte original=%s actual=%s | '
              'oraculo ent_total=%s sal_total=%s stock_hoy=%s saldo_ini_implicito=%s' %
              (d['stock_original'], d['stock_actual'], ent, sal, stock_hoy,
               stock_hoy - (ent - sal)))
        print('  t=%ss q=%d' % (t3, q3))
else:
    print('E3: BUFANDAS no aparece en modo historico')

print('FIN T4')
