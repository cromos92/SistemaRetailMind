"""
Seguridad transversal (auditoría 2026-09, unidad SEC).

El middleware de permisos solo protegía las PÁGINAS de Compras, Documentos de
Compra, Gestión Producto y Recepción DTE: sus APIs respondían a cualquier
usuario con sesión (un vendedor pagaba facturas de otra empresa, creaba
compras, importaba DTE o reescribía la razón social de un proveedor).

Cubre:
1. `obtener_codigo_opcion` resuelve cada endpoint al código de SU pantalla, y
   deja SIN mapear los que usan varias pantallas con permisos distintos.
2. Vendedor sin permisos → 403 (JSON) en pagos, pago masivo, compensación,
   NC, proveedores, importaciones, compras y recepción; nada se escribe.
3. Administrador con la pantalla → pasa (200 / validación propia de la vista).
4. Maestro pasa sin filas.
5. Widgets del home ('/app/dashboard/api/...') exigen dashboard_general.
6. Lotes FIFO: permiso de alguna de las pantallas que enlazan + alcance por
   empresa; crear/ajustar lote exigen gestion_producto (su alcance por empresa
   sigue pendiente: test expectedFailure que salta al aplicar el parche).
7. /media/ exige sesión.
8. Notificaciones de DTE del menú escapan el texto antes del innerHTML.
9. Timeline de traspaso ('/app/dte/<id>/audit/') exige recepcion_dte.
"""
import json
import os
import unittest
from datetime import timedelta

from django.conf import settings
from django.shortcuts import resolve_url
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from app.middleware_permisos import PermisosMenuMiddleware
from app.models import (
    Compras, Dte, Dte_Detalle_Pago, Empresa, LoteProducto, ModuloSistema, OpcionMenu, PermisoRol,
)
from .factories import (
    crear_empresa, crear_empresa_user, crear_lote_fifo, crear_producto_con_talla,
    crear_sucursal, crear_usuario,
)

TIPOS = ('puede_ver', 'puede_crear', 'puede_editar', 'puede_eliminar', 'puede_exportar', 'puede_aprobar')


def _permiso(rol, codigo, **flags):
    modulo, _ = ModuloSistema.objects.get_or_create(codigo='sec_test', defaults={'nombre': 'SEC test'})
    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo=codigo, defaults={'modulo': modulo, 'nombre': codigo, 'activo': True})
    if not opcion.activo:
        opcion.activo = True
        opcion.save(update_fields=['activo'])
    valores = {t: False for t in TIPOS}
    valores.update(flags)
    PermisoRol.objects.update_or_create(rol=rol, opcion_menu=opcion, defaults=valores)


def _todos(rol, *codigos):
    for codigo in codigos:
        _permiso(rol, codigo, **{t: True for t in TIPOS})


class MapaUrlsTest(TestCase):
    """El mapa resuelve cada API al código de su pantalla (sin colisiones)."""

    ESPERADO = {
        # Gestión Compras
        '/app/crear_compra/': 'gestion_compras',
        '/app/obtener_compras/': 'gestion_compras',
        '/app/obtener_compra/14/': 'gestion_compras',
        '/app/importar_csv_compra/': 'gestion_compras',
        '/app/compra/recepcionar/': 'gestion_compras',
        '/app/guardar_recepcion/': 'gestion_compras',
        '/app/api/compra/vincular-retroactivo/': 'gestion_compras',
        '/app/api/curvas-distribucion/guardar/': 'gestion_compras',
        '/app/api/exportar-compras-csv/': 'gestion_compras',
        '/app/obtener_recepciones_compra/47/': 'gestion_compras',
        '/app/eliminar_compra/': 'gestion_compras',
        # Gestión Documentos Compras (pagos, NC, compensación, proveedores)
        '/app/registrarPagoDTE/': 'gestion_dte_compras',
        '/app/procesar_pago_masivo/': 'gestion_dte_compras',
        '/app/obtenerDetallePago/9/': 'gestion_dte_compras',
        '/app/detallePago/9/': 'gestion_dte_compras',
        '/app/crearDteCompras/': 'gestion_dte_compras',
        '/app/actualizarDteCompras/9/': 'gestion_dte_compras',
        '/app/incidencias/eliminar/51/': 'gestion_dte_compras',
        '/app/asociar_factura_cotizacion/': 'gestion_dte_compras',
        '/app/desasociar_factura_cotizacion/9/': 'gestion_dte_compras',
        '/app/asociar_nc_existente/': 'gestion_dte_compras',
        '/app/desasociar_nc/9/': 'gestion_dte_compras',
        '/app/asociar_factura_compensacion/': 'gestion_dte_compras',
        '/app/desasociar_documento_emitido_compensacion/9/': 'gestion_dte_compras',
        '/app/gestionar_proveedor/1802/': 'gestion_dte_compras',
        '/app/api/importar-proveedores/': 'gestion_dte_compras',
        '/app/api/exportar-dtes-excel/': 'gestion_dte_compras',
        # Gestión Producto (paso 5 / ingreso manual / lotes)
        '/app/crear_producto_desde_recepcion/': 'gestion_producto',
        '/app/crear_producto_manual/': 'gestion_producto',
        '/app/api/ingreso-manual/sumar-stock/': 'gestion_producto',
        '/app/eliminar_producto_todas_sucursales/': 'gestion_producto',
        '/app/crear_lote_manual/': 'gestion_producto',
        '/app/ajustar_lote/1/': 'gestion_producto',
        # Recepción / regularización de traspasos
        '/app/dte/recepciones_pendientes/': 'recepcion_dte',
        '/app/dte/rehabilitar_rechazado/': 'recepcion_dte',
        '/app/dte/documento-regularizacion/4018/': 'recepcion_dte',
        '/app/dte/obtener_detalle_dte_recepcionado/': 'recepcion_dte',
        '/app/dte/regularizar_producto/': 'recepcion_dte',
        '/app/dtes-en-limbo/': 'recepcion_dte',
        # Timeline del traspaso (dte_audit_api): única ruta con '/audit/'.
        '/app/dte/2183045/audit/': 'recepcion_dte',
        # Home
        '/app/dashboard/api/stock-alertas/': 'dashboard_general',
    }

    # Endpoints que llaman pantallas con permisos distintos (o el menú, que
    # está en todas): el middleware no los gatea; la vista decide.
    SIN_MAPEAR = (
        '/app/cargarDteCompra/',
        '/app/api/producto/revertir-a-pendiente/',
        '/app/empresas_proveedoras/',
        '/app/proveedores/',
        '/app/dtes-pendientes-recibir/',
        '/app/dtes-pendientes-recibir/descartar/',
        '/app/dtes-pendientes-regularizar/',
        '/app/notificaciones-dte/',
        '/app/dte/15/txt-acepta/',
        '/app/dte/15/documentos-vinculados/',
        '/app/lotes_producto/1/',
        '/app/obtener_lotes_producto/1/',
        '/app/api/compras/xml-dte/analizar/',
    )

    def setUp(self):
        self.mw = PermisosMenuMiddleware(lambda r: None)

    def test_endpoints_mapeados_a_su_pantalla(self):
        for path, codigo in self.ESPERADO.items():
            with self.subTest(path=path):
                self.assertEqual(self.mw.obtener_codigo_opcion(path), codigo)

    def test_endpoints_compartidos_quedan_sin_mapear(self):
        for path in self.SIN_MAPEAR:
            with self.subTest(path=path):
                self.assertIsNone(self.mw.obtener_codigo_opcion(path))

    def test_paginas_sin_verificacion_son_coincidencia_exacta(self):
        rf = RequestFactory()
        for path in ('/app/home/', '/app/dashboard/', '/app/bienvenida/'):
            with self.subTest(path=path):
                request = rf.get(path)
                request.user = crear_usuario(username=f'u{len(path)}', rol='vendedor')
                request.session = {}
                self.assertIsNone(self.mw.verificar_permiso(request))


class _BaseSec(TestCase):
    def setUp(self):
        self.empresa = crear_empresa(nombre='Empresa SEC')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='EDEL')
        self.proveedor = crear_empresa(nombre='Proveedor SEC', rut='76.222.222-2', esProveedor=True)

        self.vendedor = crear_usuario(username='vendedor_sec', rol='vendedor')
        self.admin = crear_usuario(username='admin_sec', rol='administrador')
        self.maestro = crear_usuario(username='maestro_sec', rol='maestro')
        for u in (self.vendedor, self.admin, self.maestro):
            crear_empresa_user(u, self.empresa, self.sucursal)
        # Vendedor: sin ninguna pantalla de compras/producto/recepción.
        for codigo in ('gestion_compras', 'gestion_dte_compras', 'gestion_producto',
                       'recepcion_dte', 'dashboard_general', 'dashboard_fifo'):
            _permiso('vendedor', codigo)
        _todos('administrador', 'gestion_compras', 'gestion_dte_compras', 'gestion_producto',
               'recepcion_dte', 'dashboard_general', 'dashboard_fifo',
               'dte_compras_pagos', 'dte_compras_eliminar')

        hoy = timezone.localdate()
        self.factura = Dte.objects.create(
            emisor=self.proveedor, receptor=self.empresa, numero_documento=9101,
            tipo_documento='FACTURA ELECTRONICA', monto_con_iva=119000, monto_neto=100000,
            descuento=0, estado_pago='Pendiente', estado_dte='RECEPCIONADO_COMPLETO',
            responsable='test', fecha_emision=hoy, fecha_vencimiento=hoy + timedelta(days=30),
            diasCredito=30, bultos=0, unidades_productos=1,
            tipo_transaccion='COMPRA', sucursal=self.sucursal,
        )

    def _cliente(self, usuario):
        c = Client()
        c.force_login(usuario)
        s = c.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s['alias'] = self.sucursal.alias
        s.save()
        return c

    @staticmethod
    def _json(c, method, url, data=None):
        if method == 'get':
            return c.get(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        return getattr(c, method)(
            url, data=json.dumps(data or {}), content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def _pago(self, voucher='SEC-1', monto=1000):
        return {
            'dte_id': self.factura.id, 'metodo_pago': 'Transferencia', 'voucher': voucher,
            'monto': monto, 'fecha_pago': timezone.localdate().isoformat(),
        }


class VendedorBloqueadoTest(_BaseSec):
    """Un vendedor sin las pantallas recibe 403 y no escribe nada."""

    def assert403(self, r, codigo):
        self.assertEqual(r.status_code, 403, r.content[:200])
        self.assertEqual(r.json().get('codigo_requerido'), codigo)

    def test_pagos_y_pago_masivo(self):
        c = self._cliente(self.vendedor)
        self.assert403(self._json(c, 'post', '/app/registrarPagoDTE/', self._pago()), 'gestion_dte_compras')
        self.assert403(self._json(c, 'post', '/app/procesar_pago_masivo/', {
            'facturas': [self.factura.id], 'metodo_pago': 'Transferencia',
            'fecha_pago': timezone.localdate().isoformat(),
        }), 'gestion_dte_compras')
        self.assert403(self._json(c, 'get', f'/app/pagosDTE/{self.factura.id}/'), 'gestion_dte_compras')
        self.assertFalse(Dte_Detalle_Pago.objects.filter(dte=self.factura).exists())
        self.factura.refresh_from_db()
        self.assertEqual(self.factura.estado_pago, 'Pendiente')

    def test_compensacion_nc_y_restaurar(self):
        c = self._cliente(self.vendedor)
        self.assert403(self._json(c, 'post', '/app/asociar_factura_compensacion/', {
            'dte_id': self.factura.id}), 'gestion_dte_compras')
        self.assert403(self._json(c, 'post', '/app/asociar_nc_existente/', {
            'dte_id': self.factura.id}), 'gestion_dte_compras')
        self.assert403(self._json(c, 'post', '/app/desasociar_nc/1/'), 'gestion_dte_compras')
        # '/app/restaurarDTE/<id>/' ya no existe (B16-03, unidad D).
        self.assertFalse(Dte_Detalle_Pago.objects.filter(dte=self.factura).exists())

    def test_proveedores_e_importaciones(self):
        c = self._cliente(self.vendedor)
        razon = self.proveedor.razon_social
        self.assert403(self._json(c, 'put', f'/app/gestionar_proveedor/{self.proveedor.id}/', {
            'razon_social': 'RAZON CAMBIADA'}), 'gestion_dte_compras')
        self.assert403(self._json(c, 'delete', f'/app/gestionar_proveedor/{self.proveedor.id}/'),
                       'gestion_dte_compras')
        self.assert403(self._json(c, 'post', '/app/crear_proveedor/', {'nombre': 'X'}), 'gestion_dte_compras')
        self.assert403(self._json(c, 'post', '/app/api/importar-dtes/'), 'gestion_dte_compras')
        self.assert403(self._json(c, 'post', '/app/api/importar-proveedores/'), 'gestion_dte_compras')
        self.assert403(self._json(c, 'get', '/app/api/exportar-dtes-excel/'), 'gestion_dte_compras')
        self.proveedor.refresh_from_db()
        self.assertEqual(self.proveedor.razon_social, razon)
        self.assertTrue(Empresa.objects.filter(id=self.proveedor.id).exists())

    def test_navegacion_directa_redirige_a_bienvenida(self):
        c = self._cliente(self.vendedor)
        r = c.get('/app/importacion-dtes/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('bienvenida', r['Location'])

    def test_compras(self):
        c = self._cliente(self.vendedor)
        antes = Compras.objects.count()
        self.assert403(self._json(c, 'post', '/app/crear_compra/', {'nombre': 'X'}), 'gestion_compras')
        self.assert403(self._json(c, 'post', '/app/importar_csv_compra/'), 'gestion_compras')
        self.assert403(self._json(c, 'post', '/app/eliminar_compra/', {'id': 1}), 'gestion_compras')
        self.assert403(self._json(c, 'get', '/app/obtener_compras/?anio=2026'), 'gestion_compras')
        self.assert403(self._json(c, 'get', '/app/api/exportar-compras-csv/?anio=2026'), 'gestion_compras')
        self.assertEqual(Compras.objects.count(), antes)

    def test_paso5_y_recepcion(self):
        c = self._cliente(self.vendedor)
        self.assert403(self._json(c, 'post', '/app/crear_producto_desde_recepcion/'), 'gestion_producto')
        self.assert403(self._json(c, 'post', '/app/api/ingreso-manual/sumar-stock/'), 'gestion_producto')
        self.assert403(self._json(c, 'get', '/app/dte/recepciones_pendientes/'), 'recepcion_dte')
        self.assert403(self._json(c, 'post', '/app/dte/rehabilitar_rechazado/', {
            'dte_id': self.factura.id}), 'recepcion_dte')
        r = c.get('/app/dte/documento-regularizacion/1/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('bienvenida', r['Location'])

    def test_timeline_de_traspaso_exige_recepcion_dte(self):
        # B8-12: antes cualquier logueado recorría ids y leía la timeline.
        c = self._cliente(self.vendedor)
        r = self._json(c, 'get', f'/app/dte/{self.factura.id}/audit/')
        self.assert403(r, 'recepcion_dte')
        self.assertNotIn(b'timeline', r.content)

    def test_widgets_del_home_exigen_dashboard_general(self):
        c = self._cliente(self.vendedor)
        self.assert403(self._json(c, 'get', '/app/dashboard/api/ventas-tiempo-real/'), 'dashboard_general')
        self.assert403(self._json(c, 'get', '/app/dashboard/api/stock-alertas/'), 'dashboard_general')


class AdministradorConPermisoTest(_BaseSec):
    """Quien tiene la pantalla sigue operando igual que antes."""

    def test_registra_pago(self):
        c = self._cliente(self.admin)
        r = self._json(c, 'post', '/app/registrarPagoDTE/', self._pago(voucher='SEC-OK'))
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertTrue(Dte_Detalle_Pago.objects.filter(dte=self.factura, voucher='SEC-OK').exists())

    def test_lecturas_de_compras_y_documentos(self):
        c = self._cliente(self.admin)
        for url in (f'/app/pagosDTE/{self.factura.id}/', '/app/listar_proveedores/',
                    '/app/obtener_compras/?anio=2026'):
            with self.subTest(url=url):
                r = self._json(c, 'get', url)
                self.assertEqual(r.status_code, 200, r.content[:300])

    def test_timeline_de_traspaso_con_recepcion_dte(self):
        c = self._cliente(self.admin)
        r = self._json(c, 'get', f'/app/dte/{self.factura.id}/audit/')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.json()['dte']['id'], self.factura.id)

    def test_widget_home_no_lo_corta_el_middleware(self):
        c = self._cliente(self.admin)
        r = self._json(c, 'get', '/app/dashboard/api/ventas-tiempo-real/')
        self.assertNotEqual(r.status_code, 403, r.content[:300])

    def test_maestro_pasa_sin_filas(self):
        c = self._cliente(self.maestro)
        r = self._json(c, 'post', '/app/registrarPagoDTE/', self._pago(voucher='SEC-M'))
        self.assertEqual(r.status_code, 200, r.content[:300])
        r = self._json(c, 'get', '/app/listar_proveedores/')
        self.assertEqual(r.status_code, 200, r.content[:300])


class LotesFifoAlcanceTest(TestCase):
    """Lotes FIFO: permiso de pantalla + alcance por empresa."""

    def setUp(self):
        self.empresa_a = crear_empresa(nombre='Empresa A', rut='76.333.333-3')
        self.suc_a = crear_sucursal(empresa=self.empresa_a, alias='SUCA')
        self.empresa_b = crear_empresa(nombre='Empresa B', rut='76.444.444-4')
        self.suc_b = crear_sucursal(empresa=self.empresa_b, alias='SUCB')
        _, self.pt_a = crear_producto_con_talla(self.suc_a, articulo='LOTE-A', sku=7701)
        _, self.pt_b = crear_producto_con_talla(self.suc_b, articulo='LOTE-B', sku=7702)
        crear_lote_fifo(self.pt_a, cantidad=3, costo_unitario=11111)
        crear_lote_fifo(self.pt_b, cantidad=3, costo_unitario=22222)

        self.jefe_b = crear_usuario(username='jefe_b', rol='jefe_local')
        crear_empresa_user(self.jefe_b, self.empresa_b, self.suc_b)
        _permiso('jefe_local', 'dashboard_fifo', puede_ver=True)
        self.vendedor = crear_usuario(username='vend_lotes', rol='vendedor')
        crear_empresa_user(self.vendedor, self.empresa_b, self.suc_b)
        self.admin = crear_usuario(username='admin_lotes', rol='administrador')
        crear_empresa_user(self.admin, self.empresa_b, self.suc_b)
        _permiso('administrador', 'gestion_producto', puede_ver=True, puede_crear=True, puede_editar=True)

    def _cliente(self, u, suc):
        c = Client()
        c.force_login(u)
        s = c.session
        s['idSucursalActual'] = suc.id
        s['idEmpresaActual'] = suc.empresa_id
        s['alias'] = suc.alias
        s.save()
        return c

    def test_sin_pantalla_no_ve_lotes(self):
        c = self._cliente(self.vendedor, self.suc_b)
        r = c.get(f'/app/obtener_lotes_producto/{self.pt_b.id}/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(b'22222', r.content)
        r = c.get(f'/app/lotes_producto/{self.pt_b.id}/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('bienvenida', r['Location'])

    def test_otra_empresa_no_ve_costos(self):
        c = self._cliente(self.jefe_b, self.suc_b)
        r = c.get(f'/app/obtener_lotes_producto/{self.pt_a.id}/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(b'11111', r.content)
        r = c.get(f'/app/lotes_producto/{self.pt_a.id}/')
        self.assertEqual(r.status_code, 403)

    def test_propia_empresa_ve_lotes(self):
        c = self._cliente(self.jefe_b, self.suc_b)
        r = c.get(f'/app/obtener_lotes_producto/{self.pt_b.id}/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(len(r.json()['lotes']), 1)

    def test_administrador_ve_todas_y_producto_inexistente_es_404(self):
        c = self._cliente(self.admin, self.suc_b)
        r = c.get(f'/app/obtener_lotes_producto/{self.pt_a.id}/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        r = c.get('/app/obtener_lotes_producto/999999/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 404)

    def test_crear_y_ajustar_lote_exigen_gestion_producto(self):
        c = self._cliente(self.jefe_b, self.suc_b)  # dashboard_fifo sí, gestion_producto no
        stock_antes = self.pt_b.stock
        r = c.post('/app/crear_lote_manual/', {
            'producto_talla_id': self.pt_b.id, 'cantidad': 7, 'costo_unitario': 1,
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json().get('codigo_requerido'), 'gestion_producto')
        r = c.post('/app/ajustar_lote/1/', {'cantidad': 0}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403)
        self.pt_b.refresh_from_db()
        self.assertEqual(self.pt_b.stock, stock_antes)

    def test_con_gestion_producto_crea_lote_de_su_empresa(self):
        # Control del parche pendiente: el dueño del SKU sigue pudiendo operar.
        _permiso('jefe_local', 'gestion_producto', puede_ver=True, puede_crear=True, puede_editar=True)
        c = self._cliente(self.jefe_b, self.suc_b)
        stock_antes = self.pt_b.stock
        r = c.post('/app/crear_lote_manual/', {
            'producto_talla_id': self.pt_b.id, 'cantidad': 5,
            'costo_unitario': 1000, 'precio_venta_unitario': 2000,
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.pt_b.refresh_from_db()
        self.assertEqual(self.pt_b.stock, stock_antes + 5)

    def test_crear_y_ajustar_lote_de_otra_empresa_es_403(self):
        """A3-01, parte 2 (cerrado en la ronda 2, R2V1): crear_lote_manual y
        ajustar_lote validan el alcance por empresa
        (_lotes_producto_fuera_de_alcance) ANTES de escribir: un jefe_local
        con gestion_producto ya no mueve stock de un SKU de otra empresa."""
        _permiso('jefe_local', 'gestion_producto', puede_ver=True, puede_crear=True, puede_editar=True)
        c = self._cliente(self.jefe_b, self.suc_b)
        lote_a = LoteProducto.objects.filter(producto_talla=self.pt_a).first()
        stock_antes, disp_antes = self.pt_a.stock, lote_a.cantidad_disponible
        lotes_antes = LoteProducto.objects.filter(producto_talla=self.pt_a).count()
        r = c.post('/app/crear_lote_manual/', {
            'producto_talla_id': self.pt_a.id, 'cantidad': 5,
            'costo_unitario': 1000, 'precio_venta_unitario': 2000,
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403, r.content[:300])
        r = c.post(f'/app/ajustar_lote/{lote_a.id}/', {'cantidad_disponible': 0},
                   HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403, r.content[:300])
        self.pt_a.refresh_from_db()
        lote_a.refresh_from_db()
        self.assertEqual(self.pt_a.stock, stock_antes)
        self.assertEqual(lote_a.cantidad_disponible, disp_antes)
        self.assertEqual(LoteProducto.objects.filter(producto_talla=self.pt_a).count(), lotes_antes)


class CachePermisosPorRequestTest(TestCase):
    """B12-12: la caché por request de PermisoRol.tiene_permiso da EXACTAMENTE
    el mismo resultado que la consulta directa, ahorra las consultas repetidas
    y se invalida si se escriben permisos en la misma request."""

    def setUp(self):
        from app.models import PermisoSucursal, PermisoUsuario
        self.PermisoSucursal, self.PermisoUsuario = PermisoSucursal, PermisoUsuario
        self.empresa = crear_empresa(nombre='Empresa Cache')
        self.suc = crear_sucursal(empresa=self.empresa, alias='CACHE1')
        self.user = crear_usuario(username='cache_user', rol='jefe_local')

    def _cacheado(self):
        from users.models import Usuario
        u = Usuario.objects.get(pk=self.user.pk)
        setattr(u, PermisoRol.ATRIBUTO_CACHE_REQUEST, {})
        return u

    def _directo(self):
        from users.models import Usuario
        return Usuario.objects.get(pk=self.user.pk)

    def test_misma_respuesta_en_toda_la_matriz(self):
        opcion_mod, _ = ModuloSistema.objects.get_or_create(codigo='sec_test', defaults={'nombre': 'x'})
        opcion = OpcionMenu.objects.create(codigo='sec_cache_op', modulo=opcion_mod, nombre='x', activo=True)
        estados_rol = (None, {'puede_ver': True, 'puede_crear': False},
                       {'puede_ver': True, 'puede_crear': True}, {'puede_ver': False, 'puede_crear': True})
        estados_usuario = (None, {'puede_ver': None, 'puede_crear': None},
                           {'puede_ver': True, 'puede_crear': False}, {'puede_ver': False, 'puede_crear': True})
        estados_sucursal = (None, {'habilitado': True, 'puede_crear': True},
                            {'habilitado': False, 'puede_crear': True}, {'habilitado': True, 'puede_crear': False})
        casos = 0
        for er in estados_rol:
            PermisoRol.objects.filter(opcion_menu=opcion).delete()
            if er is not None:
                PermisoRol.objects.create(rol='jefe_local', opcion_menu=opcion, **er)
            for eu in estados_usuario:
                self.PermisoUsuario.objects.filter(opcion_menu=opcion).delete()
                if eu is not None:
                    self.PermisoUsuario.objects.create(usuario=self.user, opcion_menu=opcion, **eu)
                for es in estados_sucursal:
                    self.PermisoSucursal.objects.filter(opcion_menu=opcion).delete()
                    if es is not None:
                        self.PermisoSucursal.objects.create(sucursal=self.suc, opcion_menu=opcion, **es)
                    for suc_id in (None, self.suc.id):
                        cacheado = self._cacheado()
                        for tipo in ('puede_ver', 'puede_crear'):
                            directo = PermisoRol.tiene_permiso(self._directo(), 'sec_cache_op', tipo, suc_id)
                            # dos veces: la segunda sale de la caché
                            r1 = PermisoRol.tiene_permiso(cacheado, 'sec_cache_op', tipo, suc_id)
                            r2 = PermisoRol.tiene_permiso(cacheado, 'sec_cache_op', tipo, suc_id)
                            self.assertEqual((r1, r2), (directo, directo),
                                             (er, eu, es, suc_id, tipo))
                            casos += 1
        self.assertEqual(casos, 4 * 4 * 4 * 2 * 2)
        # Opción inexistente: fail-closed igual que antes.
        self.assertFalse(PermisoRol.tiene_permiso(self._cacheado(), 'no_existe_sec', 'puede_ver'))

    def test_repetir_chequeos_no_repite_consultas(self):
        _permiso('jefe_local', 'sec_cache_q', puede_ver=True, puede_crear=True)
        u = self._cacheado()
        with self.assertNumQueries(4):  # opción, override, rol, sucursal
            for tipo in TIPOS:
                PermisoRol.tiene_permiso(u, 'sec_cache_q', tipo, self.suc.id)

    def test_escribir_permisos_invalida_la_cache(self):
        _permiso('jefe_local', 'sec_cache_inv', puede_ver=True)
        u = self._cacheado()
        self.assertTrue(PermisoRol.tiene_permiso(u, 'sec_cache_inv', 'puede_ver'))
        _permiso('jefe_local', 'sec_cache_inv', puede_ver=False)
        self.assertFalse(PermisoRol.tiene_permiso(u, 'sec_cache_inv', 'puede_ver'))

    def test_el_middleware_crea_una_cache_nueva_por_request(self):
        _permiso('jefe_local', 'gestion_compras', puede_ver=True)
        c = Client()
        c.force_login(self.user)
        r = c.get('/app/obtener_compras/?anio=2026', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertNotEqual(r.status_code, 403)
        _permiso('jefe_local', 'gestion_compras', puede_ver=False)
        r = c.get('/app/obtener_compras/?anio=2026', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403)


class MediaExigeSesionTest(TestCase):
    def test_anonimo_redirige_a_login(self):
        r = Client().get('/media/documentos_electronicos/nc/NC_1_20260730_150343.txt')
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r['Location'].startswith(resolve_url(settings.LOGIN_URL)), r['Location'])
        self.assertIn('next=/media/', r['Location'])

    def test_con_sesion_llega_a_la_vista(self):
        c = Client()
        c.force_login(crear_usuario(username='media_ok', rol='vendedor'))
        r = c.get('/media/no-existe-sec-test.txt')
        self.assertEqual(r.status_code, 404)


class MenuNotificacionesEscapaHtmlTest(TestCase):
    """Los campos de texto de las notificaciones de DTE del menú pasan por el
    escape antes del innerHTML (Empresa.nombre y alias son editables)."""

    def test_campos_escapados(self):
        ruta = os.path.join(settings.BASE_DIR, 'app', 'templates', 'layout', 'menu.html')
        with open(ruta, encoding='utf-8') as f:
            html = f.read()
        self.assertIn('function _escNotifDte(', html)
        for crudo in ('${dte.emisor_nombre}', '${dte.destino_nombre}',
                      "${dte.sucursal_origen_alias || '-'}", '${dte.tipo_documento}',
                      '#${dte.numero_documento}'):
            with self.subTest(campo=crudo):
                self.assertNotIn(crudo, html)
        for escapado in ('_escNotifDte(dte.emisor_nombre)', '_escNotifDte(dte.destino_nombre)',
                         '_escNotifDte(dte.numero_documento)'):
            with self.subTest(campo=escapado):
                self.assertIn(escapado, html)
