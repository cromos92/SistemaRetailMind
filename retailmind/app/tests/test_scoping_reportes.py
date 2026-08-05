"""
Tests de ALCANCE POR EMPRESA (scoping) de los reportes.

Contexto: el holding tiene varias empresas (EDEL, GILD, IMP, PA00 + tiendas) y
el middleware de permisos es FAIL-OPEN — una URL que no está en
`URL_PERMISO_MAP` pasa, y las que sí están sólo se contrastan contra
`puede_ver`. Es decir: el middleware nunca mira A QUÉ EMPRESA pertenece el dato
pedido. La frontera entre empresas tiene que estar dentro de la vista, y estos
tests son los que la sostienen.

Lo que se prueba, endpoint por endpoint:

1. Pedir explícitamente una sucursal ajena por querystring devuelve 403
   (nunca se confía en el `sucursal_id` que llega del cliente).
2. NO pasar ningún filtro devuelve sólo los datos propios — el caso más
   peligroso, porque "sin filtro" antes significaba "todo el holding".
3. El rol `administrador` sigue viendo todo (`obtener_empresas_usuario` le
   devuelve todas las empresas por diseño): por eso el alcance se prueba
   siempre con un rol acotado (`jefe_local`).

OJO con el rol: un usuario `administrador` de otra empresa NO sirve como
usuario "ajeno", porque ve todo por diseño.
"""
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
    """
    El middleware de permisos consulta la BD de permisos (que en un test vacío
    no tiene nada configurado). Se fuerza a True para que lo único que pueda
    devolver 403 sea el scoping que estamos probando.
    """
    return mock.patch(
        'app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True
    )


def _crear_dte(emisor, receptor, sucursal, numero, tipo_transaccion='COMPRA',
               tipo_documento='FACTURA', unidades=10, **kwargs):
    defaults = dict(
        emisor=emisor,
        receptor=receptor,
        numero_documento=numero,
        tipo_documento=tipo_documento,
        monto_neto=Decimal(unidades * 1000),
        monto_con_iva=Decimal(unidades * 1190),
        estado_pago='PENDIENTE',
        estado_dte='ACEPTADO',
        responsable='tester',
        fecha_emision=timezone.localdate(),
        fecha_vencimiento=timezone.localdate(),
        diasCredito=0,
        bultos=1,
        unidades_productos=unidades,
        tipo_transaccion=tipo_transaccion,
        sucursal=sucursal,
    )
    defaults.update(kwargs)
    return Dte.objects.create(**defaults)


def _crear_movimiento(producto_talla, sucursal, concepto, cantidad,
                      tipo_movimiento='INGRESO', costo=1000, precio=2000):
    return Movimientos_Producto.objects.create(
        ProductoTalla=producto_talla,
        sucursal_origen=sucursal,
        cantidad=cantidad,
        costo=costo,
        precio=precio,
        concepto=concepto,
        tipo_movimiento=tipo_movimiento,
        estado='COMPLETADO',
        fecha=timezone.localdate(),
    )


class BaseScopingReportes(TestCase):
    """
    Dos empresas simétricas del mismo holding, cada una con su usuario acotado.

    Todo lo que se crea para la empresa B lleva cantidades distintas a las de A,
    así un test que "se cuele" no puede pasar por casualidad: los números no
    coinciden.
    """

    UNIDADES_A = 10
    UNIDADES_B = 77

    def setUp(self):
        self.anio = timezone.localdate().year

        # --- Empresa A (la del usuario que hace las llamadas) ---
        self.empresa_a = crear_empresa(nombre='Empresa A', rut='76.111.111-1')
        self.sucursal_a = crear_sucursal(self.empresa_a, alias='TIENDA-A')
        self.user_a = crear_usuario(username='jefe_a', rol='jefe_local')
        crear_empresa_user(self.user_a, self.empresa_a, self.sucursal_a)

        # --- Empresa B (la ajena) ---
        self.empresa_b = crear_empresa(nombre='Empresa B', rut='77.222.222-2')
        self.sucursal_b = crear_sucursal(self.empresa_b, alias='TIENDA-B')
        self.user_b = crear_usuario(username='jefe_b', rol='jefe_local')
        crear_empresa_user(self.user_b, self.empresa_b, self.sucursal_b)

        # --- Administrador: ve todo por diseño, sirve de control ---
        self.admin = crear_usuario(username='admin_holding', rol='administrador')
        crear_empresa_user(self.admin, self.empresa_a, self.sucursal_a)

        self.proveedor = crear_empresa(nombre='Proveedor X', rut='78.333.333-3',
                                       esProveedor=True)

        self.prod_a, self.talla_a = crear_producto_con_talla(
            self.sucursal_a, articulo='ART-A', sku=1000001, stock=50,
        )
        self.prod_b, self.talla_b = crear_producto_con_talla(
            self.sucursal_b, articulo='ART-B', sku=2000001, stock=50,
        )

        # DTE de compra por empresa (fuente del embudo cuando el año todavía no
        # tiene movimientos del sistema nuevo).
        self.dte_a = _crear_dte(self.proveedor, self.empresa_a, self.sucursal_a,
                                numero=1001, unidades=self.UNIDADES_A)
        Dte_Productos.objects.create(
            dte=self.dte_a, productoTalla=self.talla_a, descripcion='ART-A',
            costo=1000, sobreprecio=0, precio=2000, stock=self.UNIDADES_A, activo=True,
        )
        self.dte_b = _crear_dte(self.proveedor, self.empresa_b, self.sucursal_b,
                                numero=2002, unidades=self.UNIDADES_B)
        Dte_Productos.objects.create(
            dte=self.dte_b, productoTalla=self.talla_b, descripcion='ART-B',
            costo=1000, sobreprecio=0, precio=2000, stock=self.UNIDADES_B, activo=True,
        )

        self.client = Client()
        self._login(self.user_a, self.sucursal_a, self.empresa_a)

    # -- utilidades ---------------------------------------------------------

    def _login(self, usuario, sucursal, empresa):
        self.client.force_login(usuario)
        sesion = self.client.session
        sesion['idSucursalActual'] = sucursal.id
        sesion['idEmpresaActual'] = empresa.id
        sesion['alias'] = sucursal.alias
        sesion.save()

    def _get(self, url, **params):
        with _patch_permisos():
            return self.client.get(url, params)

    def _json(self, url, **params):
        resp = self._get(url, **params)
        return resp.status_code, resp.json()


class RendimientoComprasScopingTest(BaseScopingReportes):
    """`api_rendimiento_compras` — inversión, ventas, margen y ROI del embudo."""

    URL = '/app/api/rendimiento-compras/'

    def test_sucursal_ajena_devuelve_403(self):
        status, data = self._json(self.URL, anio=self.anio, sucursal=self.sucursal_b.id)
        self.assertEqual(status, 403)
        self.assertFalse(data['success'])

    def test_sin_filtro_solo_ve_lo_propio(self):
        """Sin `sucursal` no puede aparecer la inversión de la empresa ajena."""
        status, data = self._json(self.URL, anio=self.anio)
        self.assertEqual(status, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['resumen']['total_comprado'], self.UNIDADES_A)

    def test_usuario_ajeno_no_ve_la_empresa_a(self):
        self._login(self.user_b, self.sucursal_b, self.empresa_b)
        status, data = self._json(self.URL, anio=self.anio)
        self.assertEqual(status, 200)
        self.assertEqual(data['resumen']['total_comprado'], self.UNIDADES_B)

    def test_administrador_sigue_viendo_todo(self):
        """Control de no-regresión: el rol administrador no debe recibir 403."""
        self._login(self.admin, self.sucursal_a, self.empresa_a)
        status, data = self._json(self.URL, anio=self.anio, sucursal=self.sucursal_b.id)
        self.assertEqual(status, 200)

        status, data = self._json(self.URL, anio=self.anio)
        self.assertEqual(status, 200)
        self.assertEqual(data['resumen']['total_comprado'],
                         self.UNIDADES_A + self.UNIDADES_B)

    def test_scoping_por_movimientos_cuando_el_anio_usa_el_sistema_nuevo(self):
        """
        La vista tiene DOS fuentes: DTE migrados y `Movimientos_Producto`, y
        elige según cuántos movimientos tenga el año (umbral 500). Los otros
        tests recorren la rama DTE; ésta fuerza la rama de movimientos, que
        acota por `sucursal_origen`.
        """
        # Relleno neutro: CORRECCION_STOCK no entra en ninguna de las listas de
        # conceptos del reporte, así que sólo empuja el año por sobre el umbral.
        Movimientos_Producto.objects.bulk_create([
            Movimientos_Producto(
                ProductoTalla=self.talla_a, sucursal_origen=self.sucursal_a,
                cantidad=0, costo=0, precio=0, concepto='CORRECCION_STOCK',
                tipo_movimiento='INGRESO', estado='COMPLETADO',
                fecha=timezone.localdate(),
            )
            for _ in range(505)
        ])
        _crear_movimiento(self.talla_a, self.sucursal_a, 'RECEPCION_COMPRA',
                          self.UNIDADES_A)
        _crear_movimiento(self.talla_b, self.sucursal_b, 'RECEPCION_COMPRA',
                          self.UNIDADES_B)

        status, data = self._json(self.URL, anio=self.anio)
        self.assertEqual(status, 200)
        self.assertEqual(data['fuente_datos'], 'movimientos')
        self.assertEqual(data['resumen']['total_comprado'], self.UNIDADES_A)

        # Y el desglose por sucursal no puede nombrar a la tienda ajena.
        aliases = {fila['sucursal'] for fila in data['por_sucursal']}
        self.assertNotIn(self.sucursal_b.alias, aliases)


class ProductosPorOrigenScopingTest(BaseScopingReportes):
    """`api_productos_por_origen` — altas de catálogo por origen del SKU."""

    URL = '/app/api/reportes/productos-origen/'

    def setUp(self):
        super().setUp()
        # Un alta de catálogo por empresa (el "origen" es el concepto del PRIMER
        # movimiento de ingreso del SKU).
        _crear_movimiento(self.talla_a, self.sucursal_a, 'RECEPCION_COMPRA',
                          self.UNIDADES_A)
        _crear_movimiento(self.talla_b, self.sucursal_b, 'INGRESO_MANUAL',
                          self.UNIDADES_B)

    def test_sucursal_ajena_devuelve_403(self):
        status, data = self._json(self.URL, anio=self.anio, sucursal=self.sucursal_b.id)
        self.assertEqual(status, 403)
        self.assertFalse(data['success'])

    def test_sin_filtro_solo_cuenta_los_sku_propios(self):
        status, data = self._json(self.URL, anio=self.anio)
        self.assertEqual(status, 200)
        self.assertEqual(data['resumen']['total_skus'], 1)
        self.assertEqual(data['resumen']['total_unidades'], self.UNIDADES_A)
        conceptos = {fila['concepto'] for fila in data['por_origen']}
        self.assertEqual(conceptos, {'RECEPCION_COMPRA'})

    def test_administrador_ve_los_dos_sku(self):
        self._login(self.admin, self.sucursal_a, self.empresa_a)
        status, data = self._json(self.URL, anio=self.anio)
        self.assertEqual(status, 200)
        self.assertEqual(data['resumen']['total_skus'], 2)


class RecepcionesDetalladoScopingTest(BaseScopingReportes):
    """
    `api_reporte_recepciones_detallado`.

    HUÉRFANO: ningún template ni JS llama esta URL, pero la ruta sigue
    publicada, así que se le exige el mismo alcance que al resto.
    """

    URL = '/app/api/reporte-recepciones-detallado/'

    def setUp(self):
        super().setUp()
        self._recepcion(self.dte_a, self.talla_a, self.sucursal_a, self.UNIDADES_A)
        self._recepcion(self.dte_b, self.talla_b, self.sucursal_b, self.UNIDADES_B)

    def _recepcion(self, dte, talla, sucursal, unidades):
        linea = Dte_Productos.objects.filter(dte=dte).first()
        return Productos_Recepcionados.objects.create(
            dte=dte,
            dte_producto=linea,
            producto_talla=talla,
            sucursal_destino=sucursal,
            stockArribado=unidades,
            cantidad_esperada=unidades,
            fecha_recepcion=timezone.now(),
        )

    def test_sucursal_ajena_devuelve_403(self):
        status, data = self._json(self.URL, sucursal_id=self.sucursal_b.id)
        self.assertEqual(status, 403)
        self.assertFalse(data['success'])

    def test_sin_filtro_solo_ve_sus_recepciones(self):
        status, data = self._json(self.URL)
        self.assertEqual(status, 200)
        self.assertEqual(data['resumen']['total_unidades'], self.UNIDADES_A)
        sucursales = {fila['sucursal'] for fila in data['por_sucursal']}
        self.assertEqual(sucursales, {self.sucursal_a.alias})

    def test_administrador_ve_las_dos_empresas(self):
        self._login(self.admin, self.sucursal_a, self.empresa_a)
        status, data = self._json(self.URL)
        self.assertEqual(status, 200)
        self.assertEqual(data['resumen']['total_unidades'],
                         self.UNIDADES_A + self.UNIDADES_B)


class DespachosDetalladoScopingTest(BaseScopingReportes):
    """
    `api_reporte_despachos_detallado` (también HUÉRFANO).

    No recibe `sucursal_id`, así que el único control posible es que el
    universo de DTE ya venga acotado.
    """

    URL = '/app/api/reporte-despachos-detallado/'

    def test_sin_filtro_solo_ve_sus_dtes(self):
        status, data = self._json(self.URL)
        self.assertEqual(status, 200)
        numeros = {d['numero_dte'] for d in data['despachos']}
        self.assertEqual(numeros, {self.dte_a.numero_documento})
        self.assertEqual(data['resumen']['total_documentos'], 1)

    def test_usuario_ajeno_ve_solo_el_suyo(self):
        self._login(self.user_b, self.sucursal_b, self.empresa_b)
        status, data = self._json(self.URL)
        self.assertEqual(status, 200)
        numeros = {d['numero_dte'] for d in data['despachos']}
        self.assertEqual(numeros, {self.dte_b.numero_documento})

    def test_administrador_ve_los_dos(self):
        self._login(self.admin, self.sucursal_a, self.empresa_a)
        status, data = self._json(self.URL)
        self.assertEqual(status, 200)
        self.assertEqual(data['resumen']['total_documentos'], 2)


class DespachosPorProveedorScopingTest(BaseScopingReportes):
    """`reporte_despachos_por_proveedor` (views.py) — compras, montos y proveedores."""

    URL = '/app/reporte_despachos_por_proveedor/'

    def test_sin_filtro_solo_ve_sus_compras(self):
        status, data = self._json(self.URL)
        self.assertEqual(status, 200)
        numeros = {fila['dte_numero'] for fila in data['data']}
        self.assertEqual(numeros, {self.dte_a.numero_documento})
        self.assertEqual(data['resumen']['total_dtes'], 1)

    def test_usuario_ajeno_ve_solo_el_suyo(self):
        self._login(self.user_b, self.sucursal_b, self.empresa_b)
        status, data = self._json(self.URL)
        self.assertEqual(status, 200)
        numeros = {fila['dte_numero'] for fila in data['data']}
        self.assertEqual(numeros, {self.dte_b.numero_documento})

    def test_administrador_ve_las_dos_empresas(self):
        self._login(self.admin, self.sucursal_a, self.empresa_a)
        status, data = self._json(self.URL)
        self.assertEqual(status, 200)
        self.assertEqual(data['resumen']['total_dtes'], 2)


class MovimientosSucursalAuthTest(BaseScopingReportes):
    """
    `ver_reporte_movimientos_sucursal` no tenía `@login_required`.

    El middleware de permisos devuelve None para los anónimos (no los bloquea),
    así que la vista corría con `AnonymousUser`, el filtro sobre `EmpresaUser`
    reventaba y la página respondía 500 en vez de mandar al login.
    """

    URL = '/app/reportes/movimientos-sucursal/'

    def test_anonimo_va_al_login_y_no_revienta(self):
        self.client.logout()
        with _patch_permisos():
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 302)
        # `LOGIN_URL` resuelve a '/', así que lo que identifica el redirect de
        # autenticación es el `?next=` con la URL pedida, no la palabra "login".
        self.assertIn('next=/app/reportes/movimientos-sucursal/', resp['Location'])

    def test_autenticado_entra(self):
        with _patch_permisos():
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)


class DashboardFifoScopingTest(BaseScopingReportes):
    """
    `dashboard_fifo` y su API validan la sucursal de SESIÓN con el mismo
    criterio que su hermano `reporte_fifo_general` (`puede_ver_sucursal`).
    """

    URL_PAGINA = '/app/dashboard_fifo/'
    URL_DATOS = '/app/obtener_datos_dashboard_fifo/'
    URL_EXPORT = '/app/exportar_dashboard_fifo/'

    def test_sucursal_de_sesion_ajena_devuelve_403_en_la_pagina(self):
        self._login(self.user_a, self.sucursal_b, self.empresa_b)
        with _patch_permisos():
            resp = self.client.get(self.URL_PAGINA)
        self.assertEqual(resp.status_code, 403)

    def test_sucursal_de_sesion_ajena_devuelve_403_en_la_api(self):
        self._login(self.user_a, self.sucursal_b, self.empresa_b)
        status, data = self._json(self.URL_DATOS)
        self.assertEqual(status, 403)
        self.assertFalse(data['success'])

    def test_sucursal_de_sesion_ajena_devuelve_403_en_el_csv(self):
        """El export lleva costo y valorización: mismo candado que la pantalla."""
        self._login(self.user_a, self.sucursal_b, self.empresa_b)
        with _patch_permisos():
            resp = self.client.get(self.URL_EXPORT)
        self.assertEqual(resp.status_code, 403)

    def test_sucursal_propia_no_es_bloqueada(self):
        status, _data = self._json(self.URL_DATOS)
        self.assertNotEqual(status, 403)

    def test_administrador_no_es_bloqueado(self):
        self._login(self.admin, self.sucursal_b, self.empresa_b)
        status, _data = self._json(self.URL_DATOS)
        self.assertNotEqual(status, 403)
