"""
Tests R2H (27-sep-2026) de dashboards.

1. /app/api/ventas/estado-operacional/ (A2-02, resto): `dtes_pendientes`
   (traspasos EMITIDO) y `regularizaciones_pendientes` usan el mismo alcance y
   filtros que el resto del panel. Cada caso cuenta si CUALQUIERA de sus dos
   tiendas cae en el alcance (traspaso: origen `Dte.sucursal` o destino del
   movimiento TRASPASO_SALIDA; regularización: solicitante o emisora). El
   administrador en «Todas» sigue viendo el conteo global. La caché sigue
   variando por usuario.
2. /app/api/dashboard-documentos/datos/: los estados de pago con saldo son
   PENDIENTE, PARCIAL y ABONADO sin distinguir mayúsculas
   (`utils_estado_pago.q_estado_pago_pendiente`); los abonados cuentan por su
   SALDO (monto − pagos, topado en [0, monto]); los PENDIENTE no cambian.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import caches
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.models import (
    Dte, Dte_Detalle_Pago, Movimientos_Producto, Productos_Recepcionados,
    Solicitud_Regularizacion,
)

from .factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla, crear_sucursal,
    crear_usuario, otorgar_ver_pantalla,
)

# Caché `ventas` aislado (con REDIS_URL el alias real comparte base con otros).
CACHES_TEST = {
    **settings.CACHES,
    'ventas': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'test-r2h-dashboards',
    },
}

URL_OPERACIONAL = '/app/api/ventas/estado-operacional/'
URL_DOCUMENTOS = '/app/api/dashboard-documentos/datos/'


def _dte(emisor, sucursal, numero, *, tipo_documento='GUIA', tipo_transaccion='TRASPASO',
         monto=10000, estado_dte='EMITIDO', estado_pago='PENDIENTE', fecha=None,
         vencimiento=None, dias_credito=0, es_nc=False):
    hoy = timezone.localdate()
    monto = Decimal(monto)
    return Dte.objects.create(
        emisor=emisor, receptor=None, numero_documento=numero,
        tipo_documento=tipo_documento, monto_con_iva=monto,
        monto_neto=(monto / Decimal('1.19')).quantize(Decimal('1')), descuento=0,
        estado_pago=estado_pago, estado_dte=estado_dte, responsable='test',
        fecha_emision=fecha or hoy, fecha_vencimiento=vencimiento or fecha or hoy,
        diasCredito=dias_credito, bultos=0, unidades_productos=1,
        tipo_transaccion=tipo_transaccion, sucursal=sucursal, es_nota_credito=es_nc,
        hora=timezone.localtime().time(),
    )


@override_settings(CACHES=CACHES_TEST)
class EstadoOperacionalAlcancePendientesTest(TestCase):
    """dtes_pendientes y regularizaciones_pendientes con el alcance del usuario."""

    def setUp(self):
        caches['ventas'].clear()
        self.e1 = crear_empresa(nombre='R2H Uno', rut='76.301.301-1')
        self.e2 = crear_empresa(nombre='R2H Dos', rut='76.302.302-2')
        self.suc = crear_sucursal(empresa=self.e1, alias='R2H-A')        # tienda del jefe
        self.otra = crear_sucursal(empresa=self.e2, alias='R2H-B')
        self.tercera = crear_sucursal(empresa=self.e2, alias='R2H-C')

        otorgar_ver_pantalla('administrador', 'dashboard_ventas')
        otorgar_ver_pantalla('jefe_local', 'dashboard_ventas')
        self.admin = crear_usuario(username='r2h_admin', rol='administrador')
        crear_empresa_user(self.admin, self.e1, self.suc)
        self.jefe = crear_usuario(username='r2h_jefe', rol='jefe_local')
        crear_empresa_user(self.jefe, self.e1, self.suc)

        _, self.pt = crear_producto_con_talla(self.suc, sku=9300001, stock=5)
        # Traspasos EMITIDO (pendientes de recepción):
        #   1. suc → otra        (el jefe lo ve: su tienda es el ORIGEN)
        #   2. otra → suc        (el jefe lo ve: su tienda es el DESTINO)
        #   3. otra → tercera    (ajeno)
        #   4. tercera, legado sin movimientos (ajeno; sólo origen)
        #   5. suc → otra, ya RECEPCIONADO_COMPLETO (no es pendiente)
        self.t1 = self._traspaso(1, self.suc, self.otra)
        self.t2 = self._traspaso(2, self.otra, self.suc)
        self.t3 = self._traspaso(3, self.otra, self.tercera)
        self.t4 = _dte(self.e2, self.tercera, 4)
        self._traspaso(5, self.suc, self.otra, estado_dte='RECEPCIONADO_COMPLETO')

        # Regularizaciones: (solicitante, emisora, estado)
        self._regularizacion('R2H-1', self.t2, self.suc, self.otra, 'PENDIENTE')     # jefe: solicita
        self._regularizacion('R2H-2', self.t1, self.otra, self.suc, 'EN_REVISION')   # jefe: aprueba
        self._regularizacion('R2H-3', self.t3, self.tercera, self.otra, 'PENDIENTE')  # ajena
        self._regularizacion('R2H-4', self.t2, self.suc, self.otra, 'EJECUTADA')     # cerrada

    # ---------- helpers ----------

    def _traspaso(self, numero, origen, destino, estado_dte='EMITIDO'):
        dte = _dte(origen.empresa, origen, numero, estado_dte=estado_dte)
        Movimientos_Producto.objects.create(
            dte=dte, ProductoTalla=self.pt, sucursal_origen=origen, sucursal_destino=destino,
            cantidad=-1, costo=1000, fecha=timezone.localdate(),
            concepto='TRASPASO_SALIDA', estado='COMPLETADO',
        )
        return dte

    def _regularizacion(self, numero, dte, solicitante, emisora, estado):
        rec = Productos_Recepcionados.objects.create(
            dte=dte, producto_talla=self.pt, sucursal_destino=solicitante,
            cantidad_esperada=1, stockArribado=0, cantidad_faltante=1,
            estado='FALTANTE', recepcionado_por='test',
        )
        return Solicitud_Regularizacion.objects.create(
            numero_solicitud=numero, dte_original=dte, producto_recepcionado=rec,
            sucursal_solicitante=solicitante, sucursal_emisora=emisora,
            usuario_solicita='test', tipo_problema='FALTANTE', cantidad_problema=1,
            descripcion_problema='test', tipo_solucion_solicitada='NOTA_CREDITO',
            estado=estado,
        )

    def _get(self, usuario, **params):
        c = Client()
        c.force_login(usuario)
        s = c.session
        s['idSucursalActual'] = self.suc.id
        s['idEmpresaActual'] = self.e1.id
        s.save()
        r = c.get(URL_OPERACIONAL, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        data = r.json()
        self.assertTrue(data.get('success'), data)
        return data

    def _pendientes(self, usuario, **params):
        d = self._get(usuario, **params)
        return d['dtes_pendientes'], d['regularizaciones_pendientes']

    # ---------- tests ----------

    def test_admin_en_todas_ve_el_conteo_global(self):
        self.assertEqual(self._pendientes(self.admin), (4, 3))

    def test_jefe_ve_solo_los_de_su_tienda_como_origen_o_destino(self):
        self.assertEqual(self._pendientes(self.jefe), (2, 2))

    def test_jefe_con_tienda_ajena_recibe_cero(self):
        self.assertEqual(self._pendientes(self.jefe, sucursal_id=str(self.otra.id)), (0, 0))

    def test_admin_con_filtro_de_sucursal(self):
        self.assertEqual(self._pendientes(self.admin, sucursal_id=str(self.suc.id)), (2, 2))
        # R2H-C: origen del legado (t4) y destino de t3; emisora de ninguna,
        # solicitante de la R2H-3.
        self.assertEqual(self._pendientes(self.admin, sucursal_id=str(self.tercera.id)), (2, 1))

    def test_admin_con_filtro_de_empresa(self):
        self.assertEqual(self._pendientes(self.admin, empresa_id=str(self.e1.id)), (2, 2))
        self.assertEqual(self._pendientes(self.admin, empresa_id=str(self.e2.id)), (4, 3))

    def test_movimiento_de_devolucion_no_cuenta_como_destino(self):
        """Sólo la pierna TRASPASO_SALIDA define el destino: una DEVOLUCION_NC
        hacia la tienda del jefe sobre un traspaso ajeno no se lo asigna."""
        Movimientos_Producto.objects.create(
            dte=self.t3, ProductoTalla=self.pt, sucursal_origen=self.tercera,
            sucursal_destino=self.suc, cantidad=1, costo=1000,
            fecha=timezone.localdate(), concepto='DEVOLUCION_NC', estado='COMPLETADO',
        )
        self.assertEqual(self._pendientes(self.jefe)[0], 2)

    def test_la_cache_no_mezcla_usuarios(self):
        """Mismos parámetros, distinto usuario: la respuesta cacheada del
        administrador no se le sirve al jefe (vary_on_session)."""
        self.assertEqual(self._pendientes(self.admin), (4, 3))
        self.assertEqual(self._pendientes(self.jefe), (2, 2))
        self.assertEqual(self._pendientes(self.admin), (4, 3))


class _DocumentosBase(TestCase):

    def setUp(self):
        otorgar_ver_pantalla('administrador', 'dashboard_documentos')
        self.user = crear_usuario(username='r2h_docs', rol='administrador')
        self.empresa = crear_empresa(nombre='R2H Docs', rut='76.303.303-3')
        self.suc = crear_sucursal(empresa=self.empresa, alias='R2H-D')
        self.client = Client()
        self.client.force_login(self.user)
        s = self.client.session
        s['idSucursalActual'] = self.suc.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()
        self.hoy = timezone.localdate()
        self.ayer = self.hoy - timedelta(days=1)
        self.url = f'{URL_DOCUMENTOS}?fecha_inicio={self.ayer}&fecha_fin={self.hoy}'
        self._n = 0

    def _compra(self, monto, estado_pago, *, vencida=False, pagos=(), es_nc=False,
                tipo_documento='FACTURA ELECTRONICA'):
        self._n += 1
        dte = _dte(self.empresa, None, 800 + self._n, tipo_documento=tipo_documento,
                   tipo_transaccion='COMPRA', monto=monto, estado_pago=estado_pago,
                   fecha=self.ayer, vencimiento=self.ayer if vencida else self.hoy + timedelta(days=30),
                   es_nc=es_nc)
        for p in pagos:
            Dte_Detalle_Pago.objects.create(dte=dte, metodo_pago='TRANSFERENCIA', monto=p)
        return dte

    def _get(self):
        r = self.client.get(self.url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        return json.loads(r.content)


class DocumentosEstadoPagoConSaldoTest(_DocumentosBase):
    """views_dashboards_kpi: PARCIAL/ABONADO entran a la deuda por su saldo."""

    def test_abonada_suma_su_saldo_en_por_pagar_y_vencido(self):
        self._compra(100000, 'PARCIAL', vencida=True, pagos=(20000, 10000))
        k = self._get()['kpis']
        self.assertEqual(k['monto_por_pagar'], 70000.0)
        self.assertEqual(k['monto_pendiente_pago'], 70000.0)
        self.assertEqual(k['monto_vencido'], 70000.0)
        self.assertEqual(k['dtes_vencidos'], 1)

    def test_grafias_sin_distinguir_mayusculas(self):
        self._compra(1000, 'Pendiente')
        self._compra(2000, 'pendiente')
        self._compra(3000, 'Parcial', pagos=(500,))
        self._compra(4000, 'Abonado', pagos=(1000,))
        self._compra(5000, 'abonado', pagos=(0,))
        self._compra(9999, 'Pagado', pagos=(9999,))
        k = self._get()['kpis']
        # 1000 + 2000 + (3000-500) + (4000-1000) + 5000
        self.assertEqual(k['monto_por_pagar'], 13500.0)

    def test_pendiente_con_pagos_no_cambia(self):
        """Las PENDIENTE siguen sumando su monto completo (cifra de antes)."""
        self._compra(50000, 'PENDIENTE', pagos=(10000,))
        self.assertEqual(self._get()['kpis']['monto_por_pagar'], 50000.0)

    def test_abonada_sobrepagada_no_resta_de_mas(self):
        self._compra(10000, 'PARCIAL', pagos=(15000,))
        self._compra(20000, 'PENDIENTE')
        self.assertEqual(self._get()['kpis']['monto_por_pagar'], 20000.0)

    def test_nc_de_proveedor_abonada_sigue_fuera(self):
        self._compra(30000, 'PARCIAL', pagos=(1000,), es_nc=True, tipo_documento='NOTA DE CREDITO')
        k = self._get()['kpis']
        self.assertEqual((k['monto_por_pagar'], k['dtes_vencidos']), (0.0, 0))

    def test_venta_a_credito_abonada_suma_su_saldo_por_cobrar(self):
        dte = _dte(self.empresa, self.suc, 900, tipo_documento='FACTURA ELECTRONICA',
                   tipo_transaccion='VENTA', monto=60000, estado_pago='PARCIAL',
                   fecha=self.hoy, dias_credito=30)
        Dte_Detalle_Pago.objects.create(dte=dte, metodo_pago='TRANSFERENCIA', monto=25000)
        _dte(self.empresa, self.suc, 901, tipo_documento='FACTURA ELECTRONICA',
             tipo_transaccion='VENTA', monto=10000, estado_pago='PENDIENTE',
             fecha=self.hoy, dias_credito=30)
        self.assertEqual(self._get()['kpis']['monto_por_cobrar'], 45000.0)

    def test_top_proveedores_cuenta_las_abonadas_como_pendientes(self):
        self._compra(1000, 'PARCIAL', pagos=(100,))
        self._compra(1000, 'Pendiente')
        self._compra(1000, 'PAGADO')
        prov = self._get()['top_proveedores'][0]
        self.assertEqual((prov['cantidad'], prov['pendientes']), (3, 2))

    def test_dona_junta_abonado_con_parcial(self):
        self._compra(1000, 'Abonado', pagos=(100,))
        self._compra(2000, 'PARCIAL', pagos=(100,))
        self._compra(4000, 'Pendiente')
        estados = {e['estado_pago']: (e['cantidad'], e['monto'])
                   for e in self._get()['por_estado_pago']}
        self.assertEqual(estados, {'PARCIAL': (2, 3000.0), 'PENDIENTE': (1, 4000.0)})

    def test_sin_abonados_no_consulta_pagos(self):
        """El descuento de abonos sólo corre si el período tiene alguno."""
        self._compra(1000, 'PENDIENTE')
        with CaptureQueriesContext(connection) as ctx:
            self._get()
        tabla = Dte_Detalle_Pago._meta.db_table
        self.assertFalse(any(tabla in q['sql'] for q in ctx.captured_queries))

        self._compra(3000, 'PARCIAL', pagos=(1000,))
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self._get()['kpis']['monto_por_pagar'], 3000.0)
        self.assertEqual(sum(tabla in q['sql'] for q in ctx.captured_queries), 1)
