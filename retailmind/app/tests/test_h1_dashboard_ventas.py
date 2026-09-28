"""
Tests H1 (26-sep-2026) del dashboard de ventas (`views_modulo_ventas`).

1. A2-01 «Neto de NC»: sólo restan las NC cuya venta afectada es un Ticket
   PAGADO de la base del tablero; no las NC de traspasos ni las de boletas sin
   ticket. Con estado distinto de PAGADO el neto no se calcula (None).
2. A2-02 Alcance: stock (indicadores avanzados e indicador de compra), POS y
   depósitos respetan las tiendas visibles del usuario, igual que las ventas; el
   filtro Empresa acota también los depósitos.
3. A2-03 /api/ventas/por-sucursal/: un jefe de local sólo compara sus tiendas.
4. A2-07 Cuadraturas pendientes = pares (tienda, día) con venta y sin arqueo,
   hasta ayer; los pares se deduplican en SQL (sin el Meta.ordering de Ticket).
5. Indicador de compra: error al cliente genérico, sin el texto de la excepción.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.core.cache import caches
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.models import (
    ArqueoCaja, Categoria, ConfiguracionPOS, DepositoBancario, Dte, Producto,
    Producto_Talla, Ticket, TransaccionPOS,
)

from .factories import (
    crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario,
    crear_vendedor, otorgar_ver_pantalla,
)

# Caché `ventas` aislado: con REDIS_URL definido, el alias real comparte la
# base Redis con `catalogo` y `throttle` y un clear() la vaciaría entera.
CACHES_TEST = {
    **settings.CACHES,
    'ventas': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'test-h1-dashboard-ventas',
    },
}


@override_settings(CACHES=CACHES_TEST)
class _BaseDashboardVentasTest(TestCase):

    def setUp(self):
        caches['ventas'].clear()
        self.hoy = timezone.localdate()
        self.desde = self.hoy - timedelta(days=10)
        self.periodo = {
            'fecha_inicio': self.desde.isoformat(),
            'fecha_fin': self.hoy.isoformat(),
        }

        self.empresa = crear_empresa(nombre='Empresa H1', rut='76.101.101-1')
        self.otra_empresa = crear_empresa(nombre='Otra H1', rut='76.202.202-2')
        self.suc = crear_sucursal(empresa=self.empresa, alias='H1-A')
        self.otra_suc = crear_sucursal(empresa=self.otra_empresa, alias='H1-B')
        self.vendedor = crear_vendedor(nombre='Vend H1', empresa=self.empresa)

        otorgar_ver_pantalla('administrador', 'dashboard_ventas')
        otorgar_ver_pantalla('jefe_local', 'dashboard_ventas')

        self.admin = crear_usuario(username='h1_admin', rol='administrador')
        crear_empresa_user(self.admin, self.empresa, self.suc)
        self.jefe = crear_usuario(username='h1_jefe', rol='jefe_local')
        crear_empresa_user(self.jefe, self.empresa, self.suc)

        self._correlativo = 0
        self._folio = 700

    # ---------- helpers ----------

    def _cliente(self, usuario):
        c = Client()
        c.force_login(usuario)
        s = c.session
        s['idSucursalActual'] = self.suc.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()
        return c

    def _get(self, usuario, ruta, **extra):
        params = dict(self.periodo, **extra)
        r = self._cliente(usuario).get(ruta, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        data = r.json()
        self.assertTrue(data.get('success'), data)
        return data

    def _ticket(self, total, dia, *, sucursal=None, folio_dte=None, estado='PAGADO',
                modulo_origen='VENTA_PUBLICO'):
        self._correlativo += 1
        t = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=sucursal or self.suc,
            correlativo=self._correlativo, subTotal=total, total=total,
            responsable='test', estado=estado, modulo_origen=modulo_origen,
            folio_dte=folio_dte,
        )
        momento = timezone.make_aware(datetime.combine(dia, time(12, 0)))
        Ticket.objects.filter(pk=t.pk).update(created_at=momento)
        return t

    def _dte(self, monto, *, tipo_documento='BOLETA ELECTRONICA',
             tipo_transaccion='VENTA_PUBLICO', numero=None, documento_afectado=None,
             sucursal=None, fecha=None, receptor=None):
        self._folio += 1
        monto = Decimal(monto)
        return Dte.objects.create(
            emisor=self.empresa, receptor=receptor,
            numero_documento=numero if numero is not None else self._folio,
            tipo_documento=tipo_documento,
            monto_con_iva=monto, monto_neto=(monto / Decimal('1.19')).quantize(Decimal('1')),
            descuento=Decimal(0), estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable='test', fecha_emision=fecha or self.hoy - timedelta(days=1),
            fecha_vencimiento=fecha or self.hoy, diasCredito=0, bultos=0,
            unidades_productos=1, vendedor=None, sucursal=sucursal or self.suc,
            tipo_transaccion=tipo_transaccion, descartado=False,
            es_nota_credito=(tipo_documento == 'NOTA DE CREDITO'),
            documento_afectado=documento_afectado,
            hora=timezone.localtime().time(),
        )


class NetoDeNcTest(_BaseDashboardVentasTest):
    """A2-01: el neto sólo resta NC de ventas que están en la base del KPI."""

    URL = '/app/api/ventas/indicadores-globales/'

    def setUp(self):
        super().setUp()
        dia = self.hoy - timedelta(days=3)
        self._ticket(100000, dia, folio_dte=501)
        boleta_con_ticket = self._dte(100000, numero=501, fecha=dia)
        boleta_sin_ticket = self._dte(50000, numero=502, fecha=dia)
        factura_traspaso = self._dte(
            300000, tipo_documento='FACTURA ELECTRONICA', tipo_transaccion='TRASPASO',
            numero=903, fecha=dia, receptor=self.otra_empresa)
        # Sólo la primera debe restar.
        self._dte(10000, tipo_documento='NOTA DE CREDITO', tipo_transaccion='DEVOLUCION',
                  documento_afectado=boleta_con_ticket)
        self._dte(20000, tipo_documento='NOTA DE CREDITO', tipo_transaccion='DEVOLUCION',
                  documento_afectado=boleta_sin_ticket)
        self._dte(30000, tipo_documento='NOTA DE CREDITO', tipo_transaccion='ANULACION',
                  documento_afectado=factura_traspaso)

    def test_solo_resta_nc_de_tickets_de_la_base(self):
        data = self._get(self.admin, self.URL)

        self.assertEqual(data['ventas_totales'], 100000)
        self.assertEqual(data['nc_cantidad'], 1)
        self.assertEqual(data['nc_monto'], 10000)
        self.assertEqual(data['ventas_netas'], 90000)

    def test_estado_distinto_de_pagado_no_netea(self):
        data = self._get(self.admin, self.URL, estado='ANULADO')

        self.assertIsNone(data['nc_monto'])
        self.assertIsNone(data['nc_cantidad'])
        self.assertIsNone(data['ventas_netas'])

    def test_nc_sobre_ticket_anulado_no_resta(self):
        """Si la venta original ya no es PAGADA no está en la base: su NC no resta."""
        Ticket.objects.filter(folio_dte=501).update(estado='ANULADO')

        data = self._get(self.admin, self.URL)

        self.assertEqual(data['nc_cantidad'], 0)
        self.assertEqual(data['ventas_netas'], 0)


class AlcanceStockPosDepositosTest(_BaseDashboardVentasTest):
    """A2-02: stock, POS y depósitos con el mismo alcance que las ventas."""

    def setUp(self):
        super().setUp()
        padre = Categoria.objects.create(nombre='Calzado H1')
        self.cat = Categoria.objects.create(nombre='Running H1', padre=padre)
        self._producto(self.suc, sku=9100001, stock=10)
        self._producto(self.otra_suc, sku=9100002, stock=50)

        self._ticket(40000, self.hoy - timedelta(days=2))
        self._ticket(80000, self.hoy - timedelta(days=2), sucursal=self.otra_suc)

        for n, suc in enumerate((self.suc, self.otra_suc), 1):
            conf = ConfiguracionPOS.objects.create(
                sucursal=suc, nombre=f'POS {n}', tipo_pos='OTRO',
                puerto_conexion='COM1', es_principal=False)
            TransaccionPOS.objects.create(
                configuracion_pos=conf, ticket_pos=f'H1-POS-{n}',
                monto=Decimal('1000'), estado='APROBADA')
            arqueo = ArqueoCaja.objects.create(
                fecha_arqueo=self.hoy - timedelta(days=1), sucursal=suc,
                usuario_responsable=self.admin, estado='CERRADO')
            DepositoBancario.objects.create(
                arqueo=arqueo, fecha_deposito=self.hoy, monto=5000,
                monto_declarado=5000, monto_confirmado=5000, banco='ESTADO',
                numero_comprobante=f'H1-{n}', declarado_por=self.admin,
                fecha_declaracion=timezone.now(), registrado_por=self.admin,
                verificado=True, verificado_por=self.admin,
                fecha_verificacion=timezone.now(),
            )

    def _producto(self, sucursal, sku, stock):
        producto = Producto.objects.create(
            articulo=f'ART-{sku}', descripcion='Prod H1', sucursal=sucursal,
            costo=1000, sobreprecio=0, precioventa=2000, categoria=self.cat)
        return Producto_Talla.objects.create(producto=producto, sku=sku, stock=stock, talla='40')

    def test_jefe_ve_stock_de_su_tienda(self):
        data = self._get(self.jefe, '/app/api/ventas/indicadores-avanzados/')
        self.assertEqual(data['stock_actual'], 10)

    def test_jefe_con_tienda_ajena_recibe_cero(self):
        data = self._get(self.jefe, '/app/api/ventas/indicadores-avanzados/',
                         sucursal_id=str(self.otra_suc.id))
        self.assertEqual(data['stock_actual'], 0)

    def test_admin_sin_filtro_sigue_viendo_toda_la_cadena(self):
        data = self._get(self.admin, '/app/api/ventas/indicadores-avanzados/')
        self.assertEqual(data['stock_actual'], 60)

    def test_indicador_compra_con_stock_del_alcance(self):
        data = self._get(self.jefe, '/app/api/ventas/indicador-compra/')
        stock = sum(i['stock'] for i in data['indicadores'])
        self.assertEqual(stock, 10)

    def test_pos_y_depositos_del_alcance(self):
        data = self._get(self.jefe, '/app/api/ventas/estado-operacional/')
        self.assertEqual(data['pos']['total'], 1)
        self.assertEqual(data['depositos']['verificados'] + data['depositos']['pendientes'], 1)

    def test_filtro_empresa_acota_depositos(self):
        todos = self._get(self.admin, '/app/api/ventas/estado-operacional/')
        acotado = self._get(self.admin, '/app/api/ventas/estado-operacional/',
                            empresa_id=str(self.empresa.id))
        self.assertEqual(todos['depositos']['verificados'], 2)
        self.assertEqual(acotado['depositos']['verificados'], 1)
        self.assertEqual(acotado['pos']['total'], 1)


class VentasPorSucursalAlcanceTest(_BaseDashboardVentasTest):
    """A2-03: el comparativo por tienda no filtra ventas de tiendas ajenas."""

    def test_jefe_solo_compara_sus_tiendas(self):
        self._ticket(40000, self.hoy - timedelta(days=2))
        self._ticket(80000, self.hoy - timedelta(days=2), sucursal=self.otra_suc)

        jefe = self._get(self.jefe, '/app/api/ventas/por-sucursal/')
        admin = self._get(self.admin, '/app/api/ventas/por-sucursal/')

        self.assertEqual([s['id'] for s in jefe['sucursales']], [self.suc.id])
        self.assertEqual({s['id'] for s in admin['sucursales']}, {self.suc.id, self.otra_suc.id})


class CuadraturasPendientesTest(_BaseDashboardVentasTest):
    """A2-07: pendientes por pares (tienda, día) con venta y sin arqueo."""

    def test_pares_con_venta_sin_arqueo_hasta_ayer(self):
        d1 = self.hoy - timedelta(days=3)
        d2 = self.hoy - timedelta(days=2)
        self._ticket(10000, d1)
        self._ticket(10000, d2)                         # sin arqueo -> pendiente
        self._ticket(10000, d1, sucursal=self.otra_suc)
        self._ticket(10000, d2, sucursal=self.otra_suc, modulo_origen='ECOMMERCE')  # no cuenta
        self._ticket(10000, d2, sucursal=self.otra_suc, estado='ANULADO')           # no cuenta
        self._ticket(10000, self.hoy)                   # hoy: el arqueo es al cierre
        for suc in (self.suc, self.otra_suc):
            ArqueoCaja.objects.create(
                fecha_arqueo=d1, sucursal=suc, usuario_responsable=self.admin, estado='CERRADO')

        data = self._get(self.admin, '/app/api/ventas/estado-cuadraturas/')

        self.assertEqual(data['total'], 2)
        self.assertEqual(data['pendientes'], 1)
        self.assertEqual(data['pendientes_detalle'][0]['sucursal_id'], self.suc.id)
        self.assertEqual(data['pendientes_detalle'][0]['fecha'], d2.strftime('%d/%m/%Y'))

        # El jefe (sólo H1-A) ve lo mismo para su tienda; nada de la ajena.
        jefe = self._get(self.jefe, '/app/api/ventas/estado-cuadraturas/')
        self.assertEqual(jefe['pendientes'], 1)
        self.assertEqual(jefe['total'], 1)

    def test_pares_se_deduplican_en_sql(self):
        """El Meta.ordering de Ticket (fecha, hora) no debe entrar al SELECT
        DISTINCT: si entra, la consulta trae una fila por ticket."""
        d1 = self.hoy - timedelta(days=3)
        for _ in range(3):
            self._ticket(10000, d1)

        with CaptureQueriesContext(connection) as ctx:
            data = self._get(self.admin, '/app/api/ventas/estado-cuadraturas/')

        self.assertEqual(data['pendientes'], 1)
        distinct = [q['sql'] for q in ctx.captured_queries
                    if q['sql'].startswith('SELECT DISTINCT') and '"app_ticket"' in q['sql']]
        self.assertEqual(len(distinct), 1, distinct)
        self.assertNotIn('ORDER BY', distinct[0])
        self.assertNotIn('"app_ticket"."hora"', distinct[0])


class ErrorGenericoTest(_BaseDashboardVentasTest):
    """Los errores del indicador de compra no exponen el texto de la excepción."""

    def test_indicador_compra_error_generico(self):
        with mock.patch('app.views_modulo_ventas._rango_periodo',
                        side_effect=Exception('detalle interno secreto')), \
                self.assertLogs('app', level='ERROR'):
            r = self._cliente(self.admin).get(
                '/app/api/ventas/indicador-compra/', self.periodo,
                HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json(), {'success': False,
                                    'error': 'Error al obtener indicador de compra'})
        self.assertNotIn('secreto', r.content.decode())
