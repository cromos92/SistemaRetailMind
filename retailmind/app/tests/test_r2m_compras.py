"""
Unidad R2M (ronda 2 de mejoras de Compras, 2026-09-26): views_modulo_compras.

Cubre:
- R2M-1 (B3-05 / B15-06): KPI de pendientes. Universo sin NC, descartados ni
  RECHAZADO/ANULADO/CANCELADO; estados sin distinguir mayúsculas; saldo que
  resta TODOS los pagos con un Subquery (sin multiplicar por JOIN) y en un
  número constante de consultas.
- R2M-2 (B3-07): asociar_factura_compensacion bloquea objetivo e instrumento
  ANTES de las guardas (todas dentro del atomic).
- R2M-3 (B14-10): verificar_dte_duplicado con el criterio del guardado (RUT,
  tipo, folio, sin fecha) y fallback al criterio anterior sin tipo.
- R2M-4: permisos de empresas_proveedoras y dte_documentos_vinculados_api.
- R2M-5: alcance por empresa en las 6 APIs de compensación.
- R2M-6 (B11-14): importar_proveedores_csv normaliza el RUT.
- R2M-7 (B14-08): KPI 'Compras no inventariables' blindado.

Ejecutar (BD de test aislada):
    DATABASE_URL=postgres://postgres:admin@localhost:5432/retail_r2m \
    python manage.py test app.tests.test_r2m_compras --keepdb --noinput
"""
import json
from datetime import date, timedelta
from unittest import mock

from django.db import connection
from django.test import Client, RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.models import (
    Dte, Dte_Detalle_Pago, Dte_Incidencia, Dte_Productos, Empresa,
    Movimientos_Producto, Productos_Recepcionados,
)
from app.views_modulo_compras import (
    METODO_COMPENSACION, METODO_COMPENSACION_EMITIDA, obtener_resumen_pendientes_anio,
)

from .factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla, crear_sucursal,
    crear_usuario, otorgar_ver_pantalla,
)

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'
XHR = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


def _dv(numero):
    suma, mult = 0, 2
    for d in reversed(str(numero)):
        suma += int(d) * mult
        mult = mult + 1 if mult < 7 else 2
    dv = 11 - suma % 11
    return '0' if dv == 11 else 'K' if dv == 10 else str(dv)


def rut_valido(numero):
    """RUT 'NNNNNNNN-D' con dígito verificador correcto."""
    return f'{numero}-{_dv(numero)}'


def numero_con_dv_k(desde=76000000):
    n = desde
    while _dv(n) != 'K':
        n += 1
    return n


def _dte(emisor, receptor, numero, tipo_transaccion='COMPRA', monto=119000,
         tipo_documento='FACTURA ELECTRONICA', fecha=None, estado_pago='Pendiente', **extra):
    fecha = fecha or timezone.localdate()
    datos = dict(
        emisor=emisor, receptor=receptor, numero_documento=numero,
        tipo_documento=tipo_documento, monto_con_iva=monto,
        monto_neto=int(round(monto / 1.19)), descuento=0, estado_pago=estado_pago,
        estado_dte='EMITIDO', responsable='test', fecha_emision=fecha,
        fecha_vencimiento=fecha + timedelta(days=30), diasCredito=30, bultos=1,
        unidades_productos=5, tipo_transaccion=tipo_transaccion,
        es_nota_credito=(tipo_documento == 'NOTA DE CREDITO'),
    )
    datos.update(extra)
    return Dte.objects.create(**datos)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class _BaseR2M(TestCase):
    rol = 'administrador'

    def setUp(self):
        self.empresa = crear_empresa(nombre='Nosotros R2M', rut=rut_valido(76100001), esProveedor=True)
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='R2M-SUC')
        self.otra_empresa = crear_empresa(nombre='Otra del grupo R2M', rut=rut_valido(76200002))
        crear_sucursal(empresa=self.otra_empresa, alias='R2M-OTRA')
        self.proveedor = crear_empresa(nombre='Proveedor R2M', rut=rut_valido(77300003), esProveedor=True)
        self.otro_proveedor = crear_empresa(nombre='Otro Proveedor R2M', rut=rut_valido(77400004),
                                            esProveedor=True)
        self.user = crear_usuario(username='r2m_admin', rol=self.rol)
        crear_empresa_user(self.user, self.empresa, self.sucursal)
        otorgar_ver_pantalla(self.rol, 'gestion_dte_compras', puede_crear=True, puede_editar=True)
        self.client = self._cliente(self.user)

    def _cliente(self, user, empresa=None):
        c = Client()
        c.force_login(user)
        s = c.session
        s['idSucursalActual'] = self.sucursal.id
        if empresa is not False:
            s['idEmpresaActual'] = (empresa or self.empresa).id
        s.save()
        return c

    def _post_json(self, url, body, client=None):
        r = (client or self.client).post(url, data=json.dumps(body) if not isinstance(body, str) else body,
                                         content_type='application/json', **XHR)
        try:
            return r, json.loads(r.content)
        except ValueError:
            return r, {}

    def _get_json(self, url, params=None, client=None):
        r = (client or self.client).get(url, params or {}, **XHR)
        try:
            return r, json.loads(r.content)
        except ValueError:
            return r, {}


# ---------------------------------------------------------------------------
# R2M-1: KPI de pendientes
# ---------------------------------------------------------------------------

class ResumenPendientesR2MTest(TestCase):
    def setUp(self):
        self.proveedor = crear_empresa(nombre='Prov KPI R2M', rut=rut_valido(77111112), esProveedor=True)
        self.nuestra = crear_empresa(nombre='Nosotros KPI R2M', rut=rut_valido(76222223))
        self.otra = crear_empresa(nombre='Otra KPI R2M', rut=rut_valido(76333334))
        self.user = crear_usuario(username='kpi-r2m')
        self.hoy = timezone.localdate()

    def _resumen(self):
        req = RequestFactory().get('/app/api/resumen-pendientes-anio/')
        req.user = self.user
        req.session = {'idEmpresaActual': self.nuestra.id}
        return json.loads(obtener_resumen_pendientes_anio(req).content)

    def _d(self, numero, receptor='nuestra', **kw):
        kw.setdefault('fecha', self.hoy)
        receptor = {'nuestra': self.nuestra, 'otra': self.otra, None: None}[receptor]
        return _dte(self.proveedor, receptor, numero, **kw)

    def test_excluye_anulados_cancelados_y_rechazados_sin_distinguir_mayusculas(self):
        self._d(1, monto=100000)                                  # cuenta
        self._d(2, monto=200000, estado_dte='ANULADO')
        self._d(3, monto=300000, estado_dte='cancelado')
        self._d(4, monto=400000, estado_dte='Rechazado')
        self._d(5, monto=500000, tipo_documento='NOTA DE CREDITO')
        self._d(6, monto=600000, es_nota_credito=True)            # marcada NC con otro tipo
        self._d(7, monto=700000, descartado=True)
        r = self._resumen()
        self.assertEqual(r['cantidad_pendientes'], 1, r)
        self.assertEqual(int(r['monto_pendiente']), 100000)
        self.assertEqual(r['total_dtes'], 1)

    def test_estados_sin_distinguir_mayusculas(self):
        for n, estado in enumerate(['PENDIENTE', 'pendiente', 'Parcial', 'PARCIAL', 'Abonado', 'ABONADO'], 10):
            self._d(n, monto=10000, estado_pago=estado)
        self._d(20, monto=10000, estado_pago='Pagado')
        self._d(21, monto=10000, estado_pago='PAGADO')
        r = self._resumen()
        self.assertEqual(r['cantidad_pendientes'], 6, r)
        self.assertEqual(int(r['monto_pendiente']), 60000)
        self.assertEqual(r['pagados'], 2)
        self.assertEqual(r['total_dtes'], 8)

    def test_saldo_resta_todos_los_pagos_sin_multiplicar(self):
        d = self._d(30, monto=100000, estado_pago='Parcial')
        for metodo, monto in (('Transferencia', 10000), ('Nota de Crédito', 20000),
                              (METODO_COMPENSACION, 5000), (METODO_COMPENSACION_EMITIDA, 1000)):
            Dte_Detalle_Pago.objects.create(dte=d, metodo_pago=metodo, voucher='x', monto=monto,
                                            fecha_pago=self.hoy)
        self._d(31, monto=50000)
        casi_pagada = self._d(32, monto=30000)
        Dte_Detalle_Pago.objects.create(dte=casi_pagada, metodo_pago='Cheque', voucher='c', monto=29999,
                                        fecha_pago=self.hoy)                   # saldo $1: no cuenta
        r = self._resumen()
        self.assertEqual(r['cantidad_pendientes'], 2, r)
        self.assertEqual(int(r['monto_pendiente']), 64000 + 50000)

    def test_alcance_empresa_en_sesion_o_sin_receptor(self):
        self._d(40, monto=10000)
        self._d(41, monto=20000, receptor=None)
        self._d(42, monto=40000, receptor='otra')
        r = self._resumen()
        self.assertEqual(r['cantidad_pendientes'], 2, r)
        self.assertEqual(int(r['monto_pendiente']), 30000)

    def test_consultas_constantes_con_varios_pagos(self):
        def medir():
            with CaptureQueriesContext(connection) as ctx:
                r = self._resumen()
            return len(ctx.captured_queries), r

        d = self._d(100)
        Dte_Detalle_Pago.objects.create(dte=d, metodo_pago='Transferencia', voucher='a', monto=1, fecha_pago=self.hoy)
        n1, _ = medir()
        for n in range(101, 115):
            d = self._d(n, estado_pago='Abonado')
            for _ in range(3):
                Dte_Detalle_Pago.objects.create(dte=d, metodo_pago='Transferencia', voucher='a', monto=1000,
                                                fecha_pago=self.hoy)
        n2, r = medir()
        self.assertEqual(n1, n2)
        self.assertLessEqual(n2, 2)
        self.assertEqual(r['cantidad_pendientes'], 15)
        self.assertEqual(int(r['monto_pendiente']), 119000 - 1 + 14 * (119000 - 3000))


# ---------------------------------------------------------------------------
# R2M-2 / R2M-5: compensación con factura (bloqueo + alcance)
# ---------------------------------------------------------------------------

class CompensacionFacturaR2MTest(_BaseR2M):
    URL_ASOCIAR = '/app/asociar_factura_compensacion/'
    URL_DISPONIBLES = '/app/obtener_facturas_compensar_disponibles/'

    def _asociar(self, objetivo_id, instrumento_id, monto=None, client=None):
        body = {'dte_id': objetivo_id, 'factura_compensadora_id': instrumento_id}
        if monto is not None:
            body['monto'] = monto
        return self._post_json(self.URL_ASOCIAR, body, client=client)

    def test_bloquea_ambas_facturas_antes_de_las_guardas(self):
        objetivo = _dte(self.proveedor, self.empresa, 1001, monto=100000)
        instrumento = _dte(self.proveedor, self.empresa, 1002, monto=40000)
        with CaptureQueriesContext(connection) as ctx:
            r, js = self._asociar(objetivo.id, instrumento.id)
        self.assertEqual(r.status_code, 200, js)
        sqls = [q['sql'] for q in ctx.captured_queries]
        i_lock = next(i for i, s in enumerate(sqls) if 'FOR UPDATE' in s and '"app_dte"' in s)
        self.assertIn(str(objetivo.id), sqls[i_lock])
        self.assertIn(str(instrumento.id), sqls[i_lock])
        # Guardas (incidencias, instrumento ya usado) y saldos: todas DESPUÉS del bloqueo.
        i_incidencia = next(i for i, s in enumerate(sqls) if 'app_dte_incidencia' in s)
        i_usado = next(i for i, s in enumerate(sqls)
                       if 'app_dte_detalle_pago' in s and 'voucher' in s and 'SELECT' in s)
        self.assertLess(i_lock, i_incidencia)
        self.assertLess(i_lock, i_usado)
        self.assertEqual(js['monto_aplicado'], 40000)

    def test_mismo_folio_de_otro_proveedor_no_bloquea(self):
        objetivo = _dte(self.proveedor, self.empresa, 1101, monto=100000)
        instrumento = _dte(self.proveedor, self.empresa, 555, monto=10000)
        # Otro proveedor ya usó SU factura 555 como compensación.
        otro_obj = _dte(self.otro_proveedor, self.empresa, 1102, monto=50000)
        Dte_Detalle_Pago.objects.create(dte=otro_obj, metodo_pago=METODO_COMPENSACION, voucher='555',
                                        monto=1000, fecha_pago=timezone.localdate())
        r, js = self._asociar(objetivo.id, instrumento.id)
        self.assertEqual(r.status_code, 200, js)
        _r, lst = self._get_json(self.URL_DISPONIBLES, {'dte_id': otro_obj.id})
        self.assertEqual(lst['facturas'], [])

    def test_folio_usado_en_otra_ficha_del_mismo_rut_bloquea(self):
        gemela = crear_empresa(nombre='Proveedor R2M (ficha 2)',
                               rut=self.proveedor.rut.replace('-', ''), esProveedor=True)
        objetivo = _dte(self.proveedor, self.empresa, 1201, monto=100000)
        instrumento = _dte(self.proveedor, self.empresa, 777, monto=10000)
        obj_gemela = _dte(gemela, self.empresa, 1202, monto=50000)
        Dte_Detalle_Pago.objects.create(dte=obj_gemela, metodo_pago=METODO_COMPENSACION, voucher='777',
                                        monto=1000, fecha_pago=timezone.localdate())
        r, js = self._asociar(objetivo.id, instrumento.id)
        self.assertEqual(r.status_code, 400, js)
        self.assertIn('ya fue usada', js['error'])
        _r, lst = self._get_json(self.URL_DISPONIBLES, {'dte_id': objetivo.id})
        self.assertNotIn(instrumento.id, {f['id'] for f in lst['facturas']})

    def test_doble_envio_secuencial_solo_registra_uno(self):
        objetivo = _dte(self.proveedor, self.empresa, 1301, monto=100000)
        otro_objetivo = _dte(self.proveedor, self.empresa, 1303, monto=100000)
        instrumento = _dte(self.proveedor, self.empresa, 1302, monto=40000)
        r1, js1 = self._asociar(objetivo.id, instrumento.id)
        r2, js2 = self._asociar(otro_objetivo.id, instrumento.id)
        self.assertEqual((r1.status_code, r2.status_code), (200, 400), (js1, js2))
        self.assertEqual(Dte_Detalle_Pago.objects.filter(metodo_pago=METODO_COMPENSACION).count(), 1)

    def test_objetivo_de_otra_empresa_404(self):
        objetivo = _dte(self.proveedor, self.otra_empresa, 1401, monto=100000)
        instrumento = _dte(self.proveedor, self.empresa, 1402, monto=40000)
        r, js = self._asociar(objetivo.id, instrumento.id)
        self.assertEqual(r.status_code, 404, js)
        r, js = self._get_json(self.URL_DISPONIBLES, {'dte_id': objetivo.id})
        self.assertEqual(r.status_code, 404, js)
        self.assertFalse(Dte_Detalle_Pago.objects.exists())

    def test_instrumento_de_otra_empresa_404(self):
        objetivo = _dte(self.proveedor, self.empresa, 1501, monto=100000)
        instrumento = _dte(self.proveedor, self.otra_empresa, 1502, monto=40000)
        r, js = self._asociar(objetivo.id, instrumento.id)
        self.assertEqual(r.status_code, 404, js)
        self.assertFalse(Dte_Detalle_Pago.objects.exists())

    def test_listado_alcance_y_documentos_sin_efecto(self):
        objetivo = _dte(self.proveedor, self.empresa, 1601, monto=100000)
        ok = _dte(self.proveedor, self.empresa, 1602, monto=10000)
        sin_receptor = _dte(self.proveedor, None, 1603, monto=10000)
        _dte(self.proveedor, self.otra_empresa, 1604, monto=10000)          # otra empresa
        descartada = _dte(self.proveedor, self.empresa, 1605, monto=10000, descartado=True)
        anulada = _dte(self.proveedor, self.empresa, 1606, monto=10000, estado_dte='ANULADO')
        r, js = self._get_json(self.URL_DISPONIBLES, {'dte_id': objetivo.id})
        self.assertEqual(r.status_code, 200, js)
        self.assertEqual({f['id'] for f in js['facturas']}, {ok.id, sin_receptor.id})
        # Y al asociar: sin receptor sirve; descartada / anulada no.
        r, js = self._asociar(objetivo.id, sin_receptor.id)
        self.assertEqual(r.status_code, 200, js)
        for doc in (descartada, anulada):
            r, js = self._asociar(objetivo.id, doc.id)
            self.assertEqual(r.status_code, 400, js)

    def test_objetivo_nc_o_descartado_rechazado(self):
        nc = _dte(self.proveedor, self.empresa, 1701, monto=100000, tipo_documento='NOTA DE CREDITO')
        descartado = _dte(self.proveedor, self.empresa, 1702, monto=100000, descartado=True)
        instrumento = _dte(self.proveedor, self.empresa, 1703, monto=10000)
        for obj in (nc, descartado):
            r, js = self._asociar(obj.id, instrumento.id)
            self.assertEqual(r.status_code, 400, js)
        self.assertFalse(Dte_Detalle_Pago.objects.exists())

    def test_entradas_invalidas(self):
        objetivo = _dte(self.proveedor, self.empresa, 1801, monto=100000)
        instrumento = _dte(self.proveedor, self.empresa, 1802, monto=10000)
        r, js = self._post_json(self.URL_ASOCIAR, '{no es json')
        self.assertEqual(r.status_code, 400, js)
        r, js = self._post_json(self.URL_ASOCIAR, {'dte_id': 'abc', 'factura_compensadora_id': instrumento.id})
        self.assertEqual(r.status_code, 400, js)
        r, js = self._post_json(self.URL_ASOCIAR, '{"dte_id": %d, "factura_compensadora_id": %d, "monto": NaN}'
                                % (objetivo.id, instrumento.id))
        self.assertEqual(r.status_code, 400, js)
        r, js = self._get_json(self.URL_DISPONIBLES, {'dte_id': 'abc'})
        self.assertEqual(r.status_code, 400, js)
        self.assertFalse(Dte_Detalle_Pago.objects.exists())

    def test_sin_empresa_en_sesion_403(self):
        objetivo = _dte(self.proveedor, self.empresa, 1901, monto=100000)
        instrumento = _dte(self.proveedor, self.empresa, 1902, monto=10000)
        c = self._cliente(self.user, empresa=False)
        r, js = self._asociar(objetivo.id, instrumento.id, client=c)
        self.assertEqual(r.status_code, 403, js)

    def test_desasociar_de_otra_empresa_404_y_no_borra(self):
        objetivo = _dte(self.proveedor, self.otra_empresa, 2001, monto=100000)
        pago = Dte_Detalle_Pago.objects.create(dte=objetivo, metodo_pago=METODO_COMPENSACION, voucher='9',
                                               monto=1000, fecha_pago=timezone.localdate())
        r, js = self._post_json(f'/app/desasociar_factura_compensacion/{pago.id}/', {})
        self.assertEqual(r.status_code, 404, js)
        self.assertTrue(Dte_Detalle_Pago.objects.filter(id=pago.id).exists())

    def test_desasociar_propia_revierte_y_bloquea_la_factura(self):
        objetivo = _dte(self.proveedor, self.empresa, 2101, monto=100000)
        instrumento = _dte(self.proveedor, self.empresa, 2102, monto=100000)
        r, js = self._asociar(objetivo.id, instrumento.id)
        self.assertEqual(r.status_code, 200, js)
        pago = Dte_Detalle_Pago.objects.get(dte=objetivo)
        with CaptureQueriesContext(connection) as ctx:
            r, js = self._post_json(f'/app/desasociar_factura_compensacion/{pago.id}/', {})
        self.assertEqual(r.status_code, 200, js)
        self.assertTrue(any('FOR UPDATE' in q['sql'] for q in ctx.captured_queries))
        self.assertFalse(Dte_Detalle_Pago.objects.filter(id=pago.id).exists())
        objetivo.refresh_from_db()
        self.assertEqual(objetivo.estado_pago.upper(), 'PENDIENTE')

    def test_desasociar_con_incidencia_no_borra(self):
        objetivo = _dte(self.proveedor, self.empresa, 2201, monto=100000)
        pago = Dte_Detalle_Pago.objects.create(dte=objetivo, metodo_pago=METODO_COMPENSACION, voucher='9',
                                               monto=1000, fecha_pago=timezone.localdate())
        Dte_Incidencia.objects.create(dte=objetivo, tipo='FACTURACION', descripcion='x', estado='EN_GESTION')
        r, js = self._post_json(f'/app/desasociar_factura_compensacion/{pago.id}/', {})
        self.assertEqual(r.status_code, 400, js)
        self.assertTrue(Dte_Detalle_Pago.objects.filter(id=pago.id).exists())


class CompensacionEmitidaAlcanceR2MTest(_BaseR2M):
    URL_ASOCIAR = '/app/asociar_documento_emitido_compensacion/'
    URL_DISPONIBLES = '/app/obtener_documentos_emitidos_compensar_disponibles/'

    def test_objetivo_de_otra_empresa_404(self):
        objetivo = _dte(self.proveedor, self.otra_empresa, 3001, monto=100000)
        emitida = _dte(self.empresa, self.proveedor, 9001, tipo_transaccion='VENTA', monto=50000)
        r, js = self._get_json(self.URL_DISPONIBLES, {'dte_id': objetivo.id})
        self.assertEqual(r.status_code, 404, js)
        for body in ({'dte_id': objetivo.id, 'modo': 'existente', 'documento_emitido_id': emitida.id},
                     {'dte_id': objetivo.id, 'modo': 'manual', 'numero': '123', 'monto': 1000}):
            r, js = self._post_json(self.URL_ASOCIAR, body)
            self.assertEqual(r.status_code, 404, js)
        self.assertFalse(Dte_Detalle_Pago.objects.exists())

    def test_propia_y_sin_receptor_funcionan(self):
        emitida = _dte(self.empresa, self.proveedor, 9101, tipo_transaccion='VENTA', monto=50000)
        for n, receptor in ((3101, self.empresa), (3102, None)):
            objetivo = _dte(self.proveedor, receptor, n, monto=10000)
            r, js = self._get_json(self.URL_DISPONIBLES, {'dte_id': objetivo.id})
            self.assertEqual(r.status_code, 200, js)
            self.assertIn(emitida.id, {d['id'] for d in js['documentos']})
            r, js = self._post_json(self.URL_ASOCIAR, {'dte_id': objetivo.id, 'modo': 'existente',
                                                       'documento_emitido_id': emitida.id})
            self.assertEqual(r.status_code, 200, js)

    def test_emitida_descartada_no_se_ofrece_ni_asocia(self):
        objetivo = _dte(self.proveedor, self.empresa, 3201, monto=100000)
        emitida = _dte(self.empresa, self.proveedor, 9201, tipo_transaccion='VENTA', monto=50000, descartado=True)
        r, js = self._get_json(self.URL_DISPONIBLES, {'dte_id': objetivo.id})
        self.assertEqual(js['documentos'], [])
        r, js = self._post_json(self.URL_ASOCIAR, {'dte_id': objetivo.id, 'modo': 'existente',
                                                   'documento_emitido_id': emitida.id})
        self.assertEqual(r.status_code, 400, js)

    def test_desasociar_de_otra_empresa_404_y_no_borra(self):
        objetivo = _dte(self.proveedor, self.otra_empresa, 3301, monto=100000)
        pago = Dte_Detalle_Pago.objects.create(dte=objetivo, metodo_pago=METODO_COMPENSACION_EMITIDA,
                                               voucher='9', monto=1000, fecha_pago=timezone.localdate())
        r, js = self._post_json(f'/app/desasociar_documento_emitido_compensacion/{pago.id}/', {})
        self.assertEqual(r.status_code, 404, js)
        self.assertTrue(Dte_Detalle_Pago.objects.filter(id=pago.id).exists())
        # El id de una compensación de OTRO método tampoco sirve por esta ruta.
        propio = _dte(self.proveedor, self.empresa, 3302, monto=100000)
        otro_metodo = Dte_Detalle_Pago.objects.create(dte=propio, metodo_pago=METODO_COMPENSACION,
                                                      voucher='9', monto=1000, fecha_pago=timezone.localdate())
        r, js = self._post_json(f'/app/desasociar_documento_emitido_compensacion/{otro_metodo.id}/', {})
        self.assertEqual(r.status_code, 404, js)
        self.assertTrue(Dte_Detalle_Pago.objects.filter(id=otro_metodo.id).exists())


# ---------------------------------------------------------------------------
# R2M-3: verificar_dte_duplicado
# ---------------------------------------------------------------------------

class VerificarDteDuplicadoR2MTest(_BaseR2M):
    URL = '/app/verificar_dte_duplicado/'

    def _verificar(self, **params):
        params.setdefault('emisor_id', self.proveedor.id)
        r, js = self._get_json(self.URL, params)
        return r, js

    def test_con_tipo_bloquea_sin_mirar_la_fecha(self):
        _dte(self.proveedor, self.empresa, 775124, tipo_documento='NOTA DE CREDITO', fecha=date(2026, 3, 13))
        r, js = self._verificar(numero_documento=775124, fecha_emision='2026-05-13',
                                tipo_documento='NOTA DE CREDITO')
        self.assertEqual(r.status_code, 200, js)
        self.assertTrue(js['bloqueante'], js)
        self.assertEqual(js['criterio'], 'rut_tipo_folio')
        self.assertTrue(js['coincidencias'][0]['mismo_tipo'])

    def test_con_otro_tipo_avisa_pero_no_bloquea(self):
        _dte(self.proveedor, self.empresa, 500, tipo_documento='GUIA', fecha=date(2026, 3, 13))
        r, js = self._verificar(numero_documento=500, fecha_emision='2026-03-13',
                                tipo_documento='FACTURA ELECTRONICA')
        self.assertTrue(js['existe'])
        self.assertFalse(js['bloqueante'], js)
        self.assertFalse(js['coincidencias'][0]['mismo_tipo'])

    def test_ficha_gemela_del_mismo_rut_bloquea(self):
        gemela = crear_empresa(nombre='Prov R2M ficha 2', rut='77.300.003-' + self.proveedor.rut[-1],
                               esProveedor=True)
        _dte(gemela, self.empresa, 600, fecha=date(2026, 1, 5))
        r, js = self._verificar(numero_documento=600, fecha_emision='2026-02-01',
                                tipo_documento='FACTURA ELECTRONICA')
        self.assertTrue(js['bloqueante'], js)
        self.assertEqual(len(js['coincidencias']), 1)

    def test_descartado_no_bloquea_pero_se_lista(self):
        _dte(self.proveedor, self.empresa, 700, fecha=date(2026, 1, 5), descartado=True)
        for extra in ({'tipo_documento': 'FACTURA ELECTRONICA'}, {}):
            r, js = self._verificar(numero_documento=700, fecha_emision='2026-01-05', **extra)
            self.assertFalse(js['bloqueante'], (extra, js))
            self.assertTrue(js['coincidencias'][0]['descartado'])

    def test_cotizacion_conserva_folio_y_fecha(self):
        _dte(self.proveedor, self.empresa, 800, tipo_documento='COTIZACION', fecha=date(2026, 1, 5))
        r, js = self._verificar(numero_documento=800, fecha_emision='2026-01-06', tipo_documento='COTIZACION')
        self.assertFalse(js['bloqueante'], js)
        r, js = self._verificar(numero_documento=800, fecha_emision='2026-01-05', tipo_documento='COTIZACION')
        self.assertTrue(js['bloqueante'], js)

    def test_sin_tipo_mantiene_el_criterio_anterior(self):
        """gestionDteCompras.html todavía no envía tipo_documento: folio + fecha."""
        _dte(self.proveedor, self.empresa, 900, fecha=date(2026, 1, 5))
        r, js = self._verificar(numero_documento=900, fecha_emision='2026-01-05')
        self.assertTrue(js['bloqueante'], js)
        self.assertEqual(js['criterio'], 'folio_fecha')
        r, js = self._verificar(numero_documento=900, fecha_emision='2026-01-06')
        self.assertFalse(js['bloqueante'], js)
        self.assertTrue(js['existe'])
        # Tipo desconocido = sin tipo.
        r, js = self._verificar(numero_documento=900, fecha_emision='2026-01-06', tipo_documento='XYZ')
        self.assertEqual(js['criterio'], 'folio_fecha')
        self.assertFalse(js['bloqueante'], js)

    def test_edicion_se_excluye_a_si_mismo(self):
        d = _dte(self.proveedor, self.empresa, 1000, fecha=date(2026, 1, 5))
        r, js = self._verificar(numero_documento=1000, fecha_emision='2026-01-05',
                                tipo_documento='FACTURA ELECTRONICA', dte_id=d.id)
        self.assertFalse(js['existe'], js)
        self.assertFalse(js['bloqueante'], js)

    def test_coincide_con_el_control_del_guardado(self):
        from app.views import _dte_compra_duplicado
        gemela = crear_empresa(nombre='Gemela R2M', rut=self.proveedor.rut.replace('-', ''), esProveedor=True)
        docs = [
            _dte(self.proveedor, self.empresa, 1100, fecha=date(2026, 1, 5)),
            _dte(gemela, self.empresa, 1101, tipo_documento='NOTA DE CREDITO', fecha=date(2026, 2, 5)),
            _dte(self.proveedor, self.empresa, 1102, tipo_documento='COTIZACION', fecha=date(2026, 3, 5)),
            _dte(self.proveedor, self.empresa, 1103, descartado=True, fecha=date(2026, 3, 5)),
        ]
        for numero in (1100, 1101, 1102, 1103, 1104):
            for tipo in ('FACTURA ELECTRONICA', 'NOTA DE CREDITO', 'COTIZACION'):
                for fecha in (date(2026, 1, 5), date(2026, 3, 5)):
                    r, js = self._verificar(numero_documento=numero, fecha_emision=fecha.isoformat(),
                                            tipo_documento=tipo)
                    esperado = _dte_compra_duplicado(self.proveedor, tipo, numero, fecha) is not None
                    self.assertEqual(js['bloqueante'], esperado, (numero, tipo, fecha, js))
        self.assertEqual(len(docs), 4)

    def test_parametros_invalidos_400(self):
        for numero in ('abc', '99999999999', '0', '-5'):
            r, js = self._verificar(numero_documento=numero)
            self.assertEqual(r.status_code, 400, (numero, js))

    def test_emisor_inexistente_sin_coincidencias(self):
        r, js = self._verificar(emisor_id=987654321, numero_documento=5, tipo_documento='FACTURA ELECTRONICA')
        self.assertEqual(r.status_code, 200, js)
        self.assertFalse(js['existe'])


# ---------------------------------------------------------------------------
# R2M-4: permisos de empresas_proveedoras y documentos vinculados
# ---------------------------------------------------------------------------

@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class PermisosEndpointsR2MTest(_BaseR2M):
    URL_PROV = '/app/empresas_proveedoras/'
    CODIGOS = ('gestion_dte_compras', 'gestion_compras', 'dashboard_compras_estrategico',
               'reporte_compras', 'reporte_rendimiento_proveedor', 'recepcion_dte')
    ROL_PRUEBA = 'cajero'

    def setUp(self):
        super().setUp()
        self.usuario = crear_usuario(username='r2m_cajero', rol=self.ROL_PRUEBA)
        crear_empresa_user(self.usuario, self.empresa, self.sucursal)
        self.c = self._cliente(self.usuario)
        self._solo_con(None)

    def _solo_con(self, codigo):
        """Deja al rol de prueba sin ninguna de las pantallas de CODIGOS (las
        migraciones de datos pueden sembrar permisos) salvo `codigo`."""
        from app.models import PermisoRol
        PermisoRol.objects.filter(rol=self.ROL_PRUEBA, opcion_menu__codigo__in=self.CODIGOS).delete()
        if codigo:
            otorgar_ver_pantalla(self.ROL_PRUEBA, codigo)

    def test_empresas_proveedoras_exige_alguna_pantalla(self):
        r = self.c.get(self.URL_PROV, **XHR)
        self.assertEqual(r.status_code, 403)
        r = self.c.post(self.URL_PROV, data='{}', content_type='application/json')
        self.assertEqual(r.status_code, 403)
        # Sin XHR (fetch del Reporte de Compras): redirección, nunca los datos.
        r = self.c.get(self.URL_PROV)
        self.assertEqual(r.status_code, 302)

    def test_empresas_proveedoras_con_cada_pantalla(self):
        for codigo in self.CODIGOS[:5]:
            self._solo_con(codigo)
            r = self.c.get(self.URL_PROV, **XHR)
            self.assertEqual(r.status_code, 200, (codigo, r.content[:200]))
            nombres = {e['nombre'] for e in json.loads(r.content)}
            self.assertIn('Proveedor R2M', nombres)
            # gestionDteCompras.html la llama por POST con JSON.
            r = self.c.post(self.URL_PROV, data='{}', content_type='application/json')
            self.assertEqual(r.status_code, 200, codigo)
        # recepcion_dte sola no alcanza.
        self._solo_con('recepcion_dte')
        self.assertEqual(self.c.get(self.URL_PROV, **XHR).status_code, 403)

    def test_maestro_pasa(self):
        u = crear_usuario(username='r2m_maestro', rol='maestro')
        crear_empresa_user(u, self.empresa, self.sucursal)
        r = self._cliente(u).get(self.URL_PROV, **XHR)
        self.assertEqual(r.status_code, 200)

    def test_documentos_vinculados_exige_recepcion_o_gestion_dte_compras(self):
        d = _dte(self.empresa, self.otra_empresa, 4001, tipo_transaccion='TRASPASO', tipo_documento='GUIA')
        url = f'/app/dte/{d.id}/documentos-vinculados/'
        r = self.c.get(url, **XHR)
        self.assertEqual(r.status_code, 403)
        self._solo_con('reporte_compras')
        self.assertEqual(self.c.get(url, **XHR).status_code, 403)
        for codigo in ('recepcion_dte', 'gestion_dte_compras'):
            self._solo_con(codigo)
            r = self.c.get(url, **XHR)
            self.assertEqual(r.status_code, 200, (codigo, r.content[:200]))
            self.assertTrue(json.loads(r.content)['success'])


# ---------------------------------------------------------------------------
# R2M-6: importar_proveedores_csv normaliza el RUT
# ---------------------------------------------------------------------------

@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class ImportarProveedoresRutR2MTest(_BaseR2M):

    def _importar(self, contenido, modo='crear_y_actualizar'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        r = self.client.post('/app/api/importar-proveedores/', {
            'archivo_proveedores': SimpleUploadedFile('p.csv', contenido.encode('utf-8')),
            'modo_actualizacion': modo,
        }, **XHR)
        return r, json.loads(r.content)

    def test_rut_se_guarda_canonico(self):
        n = numero_con_dv_k(76400000)
        con_puntos = f'{n // 1000000}.{n // 1000 % 1000:03d}.{n % 1000:03d}-k'
        r, js = self._importar(f'rut,nombre\n{con_puntos},Proveedor K SPA\n')
        self.assertEqual(js['proveedores_creados'], 1, js)
        self.assertEqual(Empresa.objects.get(nombre='Proveedor K SPA').rut, f'{n}-K')

    def test_ficha_existente_con_otro_formato_se_reconoce(self):
        n = 76512345
        existente = crear_empresa(nombre='Formato Viejo', esProveedor=True,
                                  rut=f'{n // 1000000}.{n // 1000 % 1000:03d}.{n % 1000:03d}-{_dv(n)}')
        antes = Empresa.objects.count()
        for variante in (f'{n}{_dv(n)}', f'{n}-{_dv(n).lower()}', f' {n}-{_dv(n)} '):
            r, js = self._importar(f'rut,nombre,giro\n{variante},Formato Viejo,Giro {variante.strip()}\n')
            self.assertEqual(js['proveedores_creados'], 0, (variante, js))
            self.assertEqual(js['errores'], [], (variante, js))
        self.assertEqual(Empresa.objects.count(), antes)
        existente.refresh_from_db()
        self.assertTrue(existente.giro.startswith('Giro '))

    def test_dv_invalido_no_crea(self):
        n = 76612345
        malo = '0' if _dv(n) != '0' else '1'
        r, js = self._importar(f'rut,nombre\n{n}-{malo},RUT Malo SPA\n')
        self.assertEqual(js['proveedores_creados'], 0, js)
        self.assertIn('no válido', js['errores'][0])
        self.assertFalse(Empresa.objects.filter(nombre='RUT Malo SPA').exists())


# ---------------------------------------------------------------------------
# R2M-7: KPI 'Compras no inventariables' del dashboard
# ---------------------------------------------------------------------------

HOY_DASH = date(2026, 9, 26)
_localdate_original = timezone.localdate


def _localdate_fijo(value=None, timezone=None):
    if value is None:
        return HOY_DASH
    return _localdate_original(value, timezone)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class KpiNoInventariablesR2MTest(TestCase):

    def setUp(self):
        self.empresa = crear_empresa(nombre='Nosotros Dash R2M', rut=rut_valido(76700007))
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='R2M-DASH')
        self.otra = crear_empresa(nombre='Otra Dash R2M', rut=rut_valido(76800008))
        self.proveedor = crear_empresa(nombre='Prov Dash R2M', rut=rut_valido(77900009), esProveedor=True)
        _p, self.pt = crear_producto_con_talla(self.sucursal, sku=8800001)
        self.user = crear_usuario(username='r2m_dash', rol='maestro')
        self.client.force_login(self.user)
        s = self.client.session
        s['idEmpresaActual'] = self.empresa.id
        s['idSucursalActual'] = self.sucursal.id
        s.save()
        p = mock.patch('django.utils.timezone.localdate', side_effect=_localdate_fijo)
        p.start()
        self.addCleanup(p.stop)
        self._n = 0

    def _c(self, monto, receptor=None, **kw):
        self._n += 1
        kw.setdefault('fecha', date(2026, 3, 1))
        kw.setdefault('es_por_concepto', True)
        return _dte(self.proveedor, receptor or self.empresa, 5000 + self._n, monto=monto, **kw)

    def _kpi(self):
        r = self.client.get('/app/dashboard_compras_mejorado_api/', {'anio': 2026}, **XHR)
        self.assertEqual(r.status_code, 200, r.content[:300])
        return json.loads(r.content)['compras_no_inventariables']

    def test_solo_facturas_nd_sin_recepcion_lineas_ni_stock(self):
        self._c(100)                                                     # cuenta
        self._c(200, tipo_documento='FACTURA EXENTA')                    # cuenta
        self._c(400, tipo_documento='NOTA DE DEBITO')                    # cuenta
        egreso = self._c(800)                                            # cuenta (solo egreso)
        Movimientos_Producto.objects.create(dte=egreso, ProductoTalla=self.pt, cantidad=-1)

        con_recepcion = self._c(1_000)
        Productos_Recepcionados.objects.create(dte=con_recepcion, producto_talla=self.pt, stockArribado=1)
        con_lineas = self._c(2_000)
        Dte_Productos.objects.create(dte=con_lineas, productoTalla=self.pt, descripcion='x', precio=1, stock=1)
        con_ingreso = self._c(4_000)
        Movimientos_Producto.objects.create(dte=con_ingreso, ProductoTalla=self.pt, cantidad=3)
        self._c(8_000, tipo_documento='NOTA DE CREDITO')
        self._c(16_000, tipo_documento='GUIA')
        self._c(32_000, tipo_documento='COTIZACION')
        self._c(64_000, estado_dte='RECHAZADO')
        self._c(128_000, descartado=True)
        self._c(256_000, es_por_concepto=False)
        self._c(512_000, receptor=self.otra)
        self._c(1_024_000, fecha=date(2025, 3, 1))                       # otro año

        kpi = self._kpi()
        self.assertEqual(kpi['cantidad'], 4, kpi)
        self.assertEqual(kpi['monto'], 1500.0)
