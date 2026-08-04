"""
Tests de los reportes de control despacho vs recepción
(`app/views_modulo_reportes_diferencias.py`).

Cubren lo que hasta ahora nadie miraba:

1. Las diferencias de recepción (`cantidad_faltante`, `cantidad_danada`,
   `cantidad_sobrante`) se agregan y valorizan bien al costo de la línea.
2. Los filtros (período, sucursal destino, proveedor, tipo de diferencia)
   acotan de verdad y la paginación es real.
3. Los documentos descartados / anulados NO entran (perseguir faltantes de un
   documento anulado es ruido).
4. El alcance por empresa: un usuario de otra empresa no ve estas líneas ni
   puede pedir el detalle de un despacho ajeno cambiando el id a mano.
5. Mercadería en tránsito: enviado − recibido − devuelto por NC, clasificación
   EN_TRANSITO / SIN_RECIBIR / RECIBIDO / SOBRE_RECIBIDO, consolidación
   multi-sucursal y drill-down por SKU (que cruza por SKU, no por
   `Producto_Talla`, porque cada sucursal tiene sus propias filas).
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, Movimientos_Producto, Productos_Recepcionados,
)
from .factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla,
    crear_sucursal, crear_usuario,
)


def _patch_permisos():
    """El decorador de permisos pega a la BD de permisos; en tests va a True."""
    return mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True)


def _crear_dte(emisor, receptor, sucursal, numero, unidades,
               tipo_documento='GUIA', estado_dte='EMITIDO', **kwargs):
    defaults = dict(
        emisor=emisor,
        receptor=receptor,
        numero_documento=numero,
        tipo_documento=tipo_documento,
        monto_neto=Decimal(unidades * 1000),
        monto_con_iva=Decimal(unidades * 1190),
        estado_pago='PENDIENTE',
        estado_dte=estado_dte,
        responsable='tester',
        fecha_emision=timezone.localdate(),
        fecha_vencimiento=timezone.localdate(),
        diasCredito=0,
        bultos=1,
        unidades_productos=unidades,
        tipo_transaccion='TRASPASO',
        sucursal=sucursal,
    )
    defaults.update(kwargs)
    return Dte.objects.create(**defaults)


class BaseReporteDiferencias(TestCase):
    """Escenario común: una empresa con bodega + tienda y un usuario de otra."""

    def setUp(self):
        self.user = crear_usuario(username='reportero', rol='administrador')
        self.empresa = crear_empresa(nombre='Mi Empresa')
        self.bodega = crear_sucursal(self.empresa, alias='EDEL')
        self.tienda = crear_sucursal(self.empresa, alias='NICK1')
        crear_empresa_user(self.user, self.empresa, self.bodega)

        # Usuario de otra empresa: no debe ver nada de lo anterior.
        # OJO: NO puede ser 'administrador' — `obtener_empresas_usuario` le
        # devuelve TODAS las empresas a ese rol, así que un admin ajeno sí ve
        # todo por diseño. El alcance real se prueba con un rol acotado.
        self.otro_user = crear_usuario(username='ajeno', rol='jefe_local')
        self.otra_empresa = crear_empresa(nombre='Otra Empresa', rut='77.111.111-1')
        self.otra_sucursal = crear_sucursal(self.otra_empresa, alias='OTRA')
        crear_empresa_user(self.otro_user, self.otra_empresa, self.otra_sucursal)

        self.proveedor = crear_empresa(nombre='Proveedor X', rut='78.222.222-2')

        self.client = Client()
        self.client.force_login(self.user)
        sesion = self.client.session
        sesion['idSucursalActual'] = self.bodega.id
        sesion['idEmpresaActual'] = self.empresa.id
        sesion.save()

    def _get(self, url, **params):
        with _patch_permisos():
            return self.client.get(url, params)

    def _json(self, url, **params):
        resp = self._get(url, **params)
        return resp.status_code, resp.json()


class DiferenciasRecepcionTest(BaseReporteDiferencias):
    URL = '/app/api/reportes/diferencias-recepcion/'

    def _recepcion(self, dte, talla, esperada, recibida, faltante=0, danada=0,
                   sobrante=0, estado='RECEPCIONADO_OK', costo=1000,
                   sucursal=None, fecha_recepcion=None):
        linea = Dte_Productos.objects.create(
            dte=dte, productoTalla=talla, descripcion='Producto Test',
            costo=costo, sobreprecio=0, precio=2000, stock=esperada, activo=True,
        )
        return Productos_Recepcionados.objects.create(
            dte=dte,
            dte_producto=linea,
            producto_talla=talla,
            sucursal_destino=sucursal or self.tienda,
            stockArribado=recibida,
            cantidad_esperada=esperada,
            cantidad_faltante=faltante,
            cantidad_danada=danada,
            cantidad_sobrante=sobrante,
            estado=estado,
            fecha_recepcion=fecha_recepcion or timezone.now(),
            recepcionado_por='bodeguero',
        )

    def setUp(self):
        super().setUp()
        _, self.talla = crear_producto_con_talla(
            self.tienda, articulo='Zapatilla A', sku=5001, stock=0,
        )
        _, self.talla_b = crear_producto_con_talla(
            self.tienda, articulo='Zapatilla B', sku=5002, stock=0, talla='38',
        )

        self.dte_ok = _crear_dte(self.proveedor, self.empresa, self.bodega, 100, 10)
        self._recepcion(self.dte_ok, self.talla, esperada=10, recibida=10)

        # 10 esperadas: llegan 6, faltan 3 y 1 llega dañada. Costo 2.000.
        self.dte_malo = _crear_dte(self.proveedor, self.empresa, self.bodega, 101, 10)
        self._recepcion(
            self.dte_malo, self.talla_b, esperada=10, recibida=7, faltante=3,
            danada=1, estado='RECEPCIONADO_PARCIAL', costo=2000,
        )

        # Sobrante de 2 unidades en otra sucursal.
        self.dte_sobrante = _crear_dte(self.proveedor, self.empresa, self.bodega, 102, 5)
        self._recepcion(
            self.dte_sobrante, self.talla, esperada=5, recibida=7, sobrante=2,
            estado='RECEPCIONADO_SOBRANTE', costo=500, sucursal=self.bodega,
        )

    def test_kpis_suman_unidades_y_valorizan_al_costo_de_la_linea(self):
        status, data = self._json(self.URL, tipo='todas')
        self.assertEqual(status, 200)
        self.assertTrue(data['success'])
        r = data['resumen']

        self.assertEqual(r['esperadas'], 25)          # 10 + 10 + 5
        self.assertEqual(r['recibidas'], 24)          # 10 + 7 + 7
        self.assertEqual(r['faltantes'], 3)
        self.assertEqual(r['danadas'], 1)
        self.assertEqual(r['sobrantes'], 2)
        self.assertEqual(r['documentos'], 3)
        self.assertEqual(r['lineas'], 3)

        # (3 faltantes + 1 dañada) x $2.000 de la línea del DTE 101
        self.assertEqual(r['valor_faltante'], 6000)
        self.assertEqual(r['valor_danado'], 2000)
        self.assertEqual(r['valor_problema'], 8000)
        self.assertEqual(r['valor_sobrante'], 1000)   # 2 x $500

        # (3 + 1) / 25 = 16%
        self.assertEqual(r['pct_diferencia'], 16.0)

    def test_filtro_solo_con_diferencia_excluye_las_lineas_ok(self):
        _, data = self._json(self.URL, tipo='con_diferencia')
        folios = {d['folio'] for d in data['datos']}
        self.assertEqual(folios, {101, 102})
        self.assertNotIn(100, folios)

        _, data_ok = self._json(self.URL, tipo='ok')
        self.assertEqual([d['folio'] for d in data_ok['datos']], [100])

        _, data_dan = self._json(self.URL, tipo='danado')
        self.assertEqual([d['folio'] for d in data_dan['datos']], [101])

        _, data_sob = self._json(self.URL, tipo='sobrante')
        self.assertEqual([d['folio'] for d in data_sob['datos']], [102])

    def test_filtro_por_sucursal_destino_y_proveedor(self):
        _, data = self._json(self.URL, tipo='todas', sucursal_id=self.bodega.id)
        self.assertEqual([d['folio'] for d in data['datos']], [102])
        self.assertEqual(data['resumen']['esperadas'], 5)

        _, data = self._json(self.URL, tipo='todas', proveedor_id=self.proveedor.id)
        self.assertEqual(data['resumen']['lineas'], 3)

        otro_proveedor = crear_empresa(nombre='Proveedor Y', rut='79.333.333-3')
        _, data = self._json(self.URL, tipo='todas', proveedor_id=otro_proveedor.id)
        self.assertEqual(data['resumen']['lineas'], 0)
        self.assertEqual(data['datos'], [])

    def test_filtro_por_periodo_deja_fuera_lo_antiguo(self):
        antigua = _crear_dte(self.proveedor, self.empresa, self.bodega, 103, 4)
        self._recepcion(
            antigua, self.talla, esperada=4, recibida=0, faltante=4,
            estado='FALTANTE', fecha_recepcion=timezone.now() - timedelta(days=120),
        )
        hoy = timezone.localdate()

        _, reciente = self._json(
            self.URL, tipo='todas',
            fecha_desde=(hoy - timedelta(days=7)).strftime('%Y-%m-%d'),
            fecha_hasta=hoy.strftime('%Y-%m-%d'),
        )
        self.assertNotIn(103, {d['folio'] for d in reciente['datos']})

        _, amplio = self._json(
            self.URL, tipo='todas',
            fecha_desde=(hoy - timedelta(days=200)).strftime('%Y-%m-%d'),
            fecha_hasta=hoy.strftime('%Y-%m-%d'),
        )
        self.assertIn(103, {d['folio'] for d in amplio['datos']})
        self.assertEqual(amplio['resumen']['faltantes'], 7)  # 3 + 4

    def test_documentos_descartados_y_anulados_no_entran(self):
        Dte.objects.filter(id=self.dte_malo.id).update(descartado=True)
        _, data = self._json(self.URL, tipo='todas')
        self.assertNotIn(101, {d['folio'] for d in data['datos']})
        self.assertEqual(data['resumen']['faltantes'], 0)

        Dte.objects.filter(id=self.dte_malo.id).update(
            descartado=False, estado_dte='ANULADO',
        )
        _, data = self._json(self.URL, tipo='todas')
        self.assertNotIn(101, {d['folio'] for d in data['datos']})

    def test_agrupaciones_por_sucursal_y_proveedor(self):
        _, data = self._json(self.URL, tipo='todas')

        por_suc = {g['sucursal']: g for g in data['por_sucursal']}
        self.assertEqual(por_suc['NICK1']['faltantes'], 3)
        self.assertEqual(por_suc['NICK1']['danadas'], 1)
        self.assertEqual(por_suc['EDEL']['sobrantes'], 2)

        por_prov = {g['proveedor']: g for g in data['por_proveedor']}
        self.assertEqual(por_prov['Proveedor X']['esperadas'], 25)
        self.assertEqual(por_prov['Proveedor X']['valor_problema'], 8000)

        # El combo de proveedores sale del universo del período.
        self.assertEqual(
            [p['nombre'] for p in data['proveedores']], ['Proveedor X'],
        )

    def test_paginacion_real(self):
        for i in range(8):
            dte = _crear_dte(self.proveedor, self.empresa, self.bodega, 200 + i, 1)
            self._recepcion(dte, self.talla, esperada=1, recibida=1)

        _, p1 = self._json(self.URL, tipo='todas', page=1, page_size=10)
        self.assertEqual(len(p1['datos']), 10)
        self.assertEqual(p1['total_registros'], 11)
        self.assertEqual(p1['total_paginas'], 2)

        _, p2 = self._json(self.URL, tipo='todas', page=2, page_size=10)
        self.assertEqual(len(p2['datos']), 1)
        # Sin solapamiento entre páginas.
        self.assertFalse({d['id'] for d in p1['datos']} & {d['id'] for d in p2['datos']})

        # Los KPI son del universo completo, no de la página.
        self.assertEqual(p2['resumen']['lineas'], 11)

    def test_parametros_basura_no_rompen(self):
        status, data = self._json(
            self.URL, tipo='inventado', page='abc', page_size='-5',
            fecha_desde='ayer', fecha_hasta='',
        )
        self.assertEqual(status, 200)
        self.assertTrue(data['success'])

    def test_usuario_de_otra_empresa_no_ve_nada(self):
        self.client.force_login(self.otro_user)
        _, data = self._json(self.URL, tipo='todas')
        self.assertEqual(data['datos'], [])
        self.assertEqual(data['resumen']['lineas'], 0)

    def test_sucursal_ajena_devuelve_403(self):
        self.client.force_login(self.otro_user)
        resp = self._get(self.URL, sucursal_id=self.tienda.id)
        self.assertEqual(resp.status_code, 403)

    def test_detalle_expone_quien_recepciono_y_estado(self):
        _, data = self._json(self.URL, tipo='faltante')
        fila = data['datos'][0]
        self.assertEqual(fila['folio'], 101)
        self.assertEqual(fila['esperado'], 10)
        self.assertEqual(fila['recibido'], 7)
        self.assertEqual(fila['faltante'], 3)
        self.assertEqual(fila['danado'], 1)
        self.assertEqual(fila['recepcionado_por'], 'bodeguero')
        self.assertEqual(fila['sucursal'], 'NICK1')
        self.assertEqual(fila['sku'], '5002')
        self.assertEqual(fila['estado_label'], 'Recepcionado Parcial')
        self.assertTrue(fila['tiene_diferencia'])
        self.assertEqual(fila['valor_problema'], 8000)


class MercaderiaTransitoTest(BaseReporteDiferencias):
    URL = '/app/api/reportes/mercaderia-transito/'
    URL_DETALLE = '/app/api/reportes/mercaderia-transito/detalle/'

    def setUp(self):
        super().setUp()
        # Mismo SKU en ambas sucursales: el cruce del detalle va por SKU, no por
        # Producto_Talla (cada sucursal tiene su propia fila).
        _, self.talla_origen = crear_producto_con_talla(
            self.bodega, articulo='Zapatilla A', sku=7001, stock=100,
        )
        _, self.talla_destino = crear_producto_con_talla(
            self.tienda, articulo='Zapatilla A', sku=7001, stock=0,
        )

    def _despacho(self, numero, enviadas, recibidas=0, dias_atras=0,
                  origen=None, destino=None):
        origen = origen or self.bodega
        destino = destino or self.tienda
        fecha = timezone.localdate() - timedelta(days=dias_atras)
        dte = _crear_dte(self.empresa, self.empresa, origen, numero, enviadas)
        Dte_Productos.objects.create(
            dte=dte, productoTalla=self.talla_origen, descripcion='Zapatilla A',
            costo=1000, sobreprecio=0, precio=2000, stock=enviadas, activo=True,
        )
        Movimientos_Producto.objects.create(
            dte=dte, ProductoTalla=self.talla_origen,
            sucursal_origen=origen, sucursal_destino=destino,
            cantidad=-enviadas, concepto='TRASPASO_SALIDA',
            tipo_movimiento='EGRESO', estado='COMPLETADO',
            responsable='tester', fecha=fecha,
        )
        if recibidas:
            Movimientos_Producto.objects.create(
                dte=dte, ProductoTalla=self.talla_destino,
                sucursal_origen=origen, sucursal_destino=destino,
                cantidad=recibidas, concepto='TRASPASO_ENTRADA',
                tipo_movimiento='INGRESO', estado='COMPLETADO',
                responsable='tester', fecha=fecha,
            )
        return dte

    def test_clasificacion_por_antiguedad_y_pendientes(self):
        self._despacho(300, enviadas=10, recibidas=10, dias_atras=10)  # RECIBIDO
        self._despacho(301, enviadas=10, recibidas=0, dias_atras=1)    # EN_TRANSITO
        self._despacho(302, enviadas=10, recibidas=0, dias_atras=30)   # SIN_RECIBIR
        self._despacho(303, enviadas=5, recibidas=8, dias_atras=5)     # SOBRE_RECIBIDO

        _, data = self._json(self.URL, situacion='')
        por_folio = {d['folio']: d for d in data['despachos']}

        self.assertEqual(por_folio[300]['situacion'], 'RECIBIDO')
        self.assertEqual(por_folio[301]['situacion'], 'EN_TRANSITO')
        self.assertEqual(por_folio[302]['situacion'], 'SIN_RECIBIR')
        self.assertEqual(por_folio[303]['situacion'], 'SOBRE_RECIBIDO')
        self.assertEqual(por_folio[302]['dias'], 30)

        r = data['resumen']
        self.assertEqual(r['documentos'], 4)
        self.assertEqual(r['unidades_enviadas'], 35)
        self.assertEqual(r['unidades_recibidas'], 18)
        self.assertEqual(r['unidades_pendientes'], 20)
        self.assertEqual(r['docs_sin_recibir'], 1)
        self.assertEqual(r['docs_en_transito'], 1)
        self.assertEqual(r['docs_sobre_recibidos'], 1)
        self.assertEqual(r['unidades_sobre_recibidas'], 3)
        self.assertEqual(r['antiguedad_max_dias'], 30)

    def test_valoriza_lo_pendiente_al_costo_del_documento(self):
        self._despacho(310, enviadas=10, recibidas=4, dias_atras=20)
        _, data = self._json(self.URL, situacion='SIN_RECIBIR')
        fila = data['despachos'][0]
        self.assertEqual(fila['pendientes'], 6)
        self.assertEqual(fila['costo_unitario'], 1000)
        self.assertEqual(fila['valor_pendiente'], 6000)
        self.assertEqual(data['resumen']['valor_sin_recibir'], 6000)

    def test_nota_de_credito_cierra_la_diferencia(self):
        dte = self._despacho(320, enviadas=10, recibidas=6, dias_atras=20)
        nc = _crear_dte(
            self.empresa, self.empresa, self.bodega, 900, 4,
            tipo_documento='NOTA DE CREDITO', es_nota_credito=True,
            documento_afectado=dte,
        )
        Movimientos_Producto.objects.create(
            dte=nc, ProductoTalla=self.talla_origen,
            sucursal_origen=self.tienda, sucursal_destino=self.bodega,
            cantidad=4, concepto='DEVOLUCION_NC',
            tipo_movimiento='INGRESO', estado='COMPLETADO', responsable='tester',
        )

        _, data = self._json(self.URL, situacion='')
        fila = data['despachos'][0]
        self.assertEqual(fila['devueltas_nc'], 4)
        self.assertEqual(fila['pendientes'], 0)
        self.assertEqual(fila['situacion'], 'RECIBIDO')

    def test_consolida_varias_sucursales_de_origen(self):
        self._despacho(330, enviadas=10, dias_atras=20, origen=self.bodega, destino=self.tienda)
        self._despacho(331, enviadas=4, dias_atras=20, origen=self.tienda, destino=self.bodega)

        _, data = self._json(self.URL, situacion='')
        self.assertEqual(data['resumen']['documentos'], 2)
        origenes = {d['origen'] for d in data['despachos']}
        self.assertEqual(origenes, {'EDEL', 'NICK1'})

        # Y el filtro por origen acota de verdad.
        _, solo_bodega = self._json(self.URL, situacion='', origen_id=self.bodega.id)
        self.assertEqual([d['folio'] for d in solo_bodega['despachos']], [330])

        # Consolidado por destino.
        por_destino = {d['destino']: d for d in data['por_destino']}
        self.assertEqual(por_destino['NICK1']['pendientes'], 10)
        self.assertEqual(por_destino['EDEL']['pendientes'], 4)

    def test_filtro_de_situacion_no_altera_los_kpi(self):
        self._despacho(340, enviadas=10, recibidas=10, dias_atras=10)
        self._despacho(341, enviadas=6, recibidas=0, dias_atras=30)

        _, data = self._json(self.URL, situacion='SIN_RECIBIR')
        self.assertEqual(len(data['despachos']), 1)
        # Los KPI siguen midiendo el período completo: si cambiaran con el
        # filtro no habría contra qué comparar lo pendiente.
        self.assertEqual(data['resumen']['documentos'], 2)
        self.assertEqual(data['resumen']['unidades_enviadas'], 16)

    def test_documentos_anulados_quedan_fuera(self):
        dte = self._despacho(350, enviadas=10, dias_atras=20)
        Dte.objects.filter(id=dte.id).update(estado_dte='ANULADO')
        _, data = self._json(self.URL, situacion='')
        self.assertEqual(data['despachos'], [])

    def test_detalle_por_sku_cruza_origen_y_destino(self):
        dte = self._despacho(360, enviadas=10, recibidas=4, dias_atras=20)
        _, data = self._json(self.URL_DETALLE, dte_id=dte.id)

        self.assertTrue(data['success'])
        self.assertEqual(len(data['detalle']), 1)
        fila = data['detalle'][0]
        self.assertEqual(fila['sku'], '7001')
        self.assertEqual(fila['enviadas'], 10)
        # La entrada se registró contra el Producto_Talla del DESTINO (id
        # distinto al del origen): si el cruce fuera por FK, esto daría 0.
        self.assertEqual(fila['recibidas'], 4)
        self.assertEqual(fila['pendientes'], 6)
        self.assertEqual(fila['valor_pendiente'], 6000)
        self.assertEqual(data['totales']['pendientes'], 6)
        self.assertEqual(data['documento']['folio'], 360)

    def test_detalle_de_despacho_ajeno_devuelve_403(self):
        dte = self._despacho(370, enviadas=10, dias_atras=5)
        self.client.force_login(self.otro_user)
        resp = self._get(self.URL_DETALLE, dte_id=dte.id)
        self.assertEqual(resp.status_code, 403)

    def test_detalle_sin_dte_id_es_400(self):
        resp = self._get(self.URL_DETALLE)
        self.assertEqual(resp.status_code, 400)

    def test_usuario_de_otra_empresa_no_ve_despachos(self):
        self._despacho(380, enviadas=10, dias_atras=5)
        self.client.force_login(self.otro_user)
        _, data = self._json(self.URL, situacion='')
        self.assertEqual(data['despachos'], [])
        self.assertEqual(data['resumen']['documentos'], 0)


class PaginasHTMLTest(BaseReporteDiferencias):
    def test_las_dos_pantallas_renderizan(self):
        for url in ('/app/reportes/diferencias-recepcion/',
                    '/app/reportes/mercaderia-transito/'):
            with self.subTest(url=url):
                resp = self._get(url)
                self.assertEqual(resp.status_code, 200)
                self.assertIn('EDEL', resp.content.decode())

    def test_sin_permiso_redirige(self):
        with mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=False):
            resp = self.client.get('/app/reportes/diferencias-recepcion/')
        self.assertEqual(resp.status_code, 302)
