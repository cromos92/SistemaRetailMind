"""
Regresión de la edición de productos de /app/verGestionProducto/.

Cubre tres hoyos detectados el 2026-08-19:

1. ALCANCE DE LA PROPAGACIÓN. `actualizar_producto` y
   `actualizar_productos_masivo` propagaban con `filter(articulo=...)`: solo el
   código, sin marca/color/género/categoría y sin límite de empresa. Editar la
   variante NEGRA de un código reescribía el color, la categoría y los precios
   de la variante BLANCA —y de las fichas de las otras empresas—. Es la misma
   clave floja que ya había dejado unas zapatillas a precio de guante (ver
   `qs_fichas_identidad_otras_sucursales`).

2. PERMISOS. Los dos endpoints solo pedían `@login_required`: cualquier usuario
   logueado, de cualquier empresa, podía renombrar y re-precear el catálogo
   completo. Ahora exigen `gestion_producto.puede_editar`, el mismo permiso que
   ya pedía el ajuste de stock de este módulo.

3. EDICIÓN CON MERCADERÍA EN TRÁNSITO. La pantalla de recepción arma cada línea
   leyendo el producto VIVO por FK, no el texto congelado en
   `Dte_Productos.descripcion`. Si se renombra el artículo mientras la guía
   viaja, el receptor ve el nombre nuevo y su papel dice el anterior. No se
   bloquea la edición (el vínculo es por SKU y la guía se recepciona igual),
   pero se avisa a quien edita y se marca la línea en la recepción.
"""
import json
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from app.models import (
    AtributoOpcion, Dte, Dte_Productos, ModuloSistema, Movimientos_Producto,
    OpcionMenu, PermisoRol, Producto, Productos_Atributos,
)
from .factories import (
    crear_empresa,
    crear_empresa_user,
    crear_producto_con_talla,
    crear_sucursal,
    crear_usuario,
)

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


def habilitar_permiso_edicion(rol='administrador', puede_editar=True):
    modulo, _ = ModuloSistema.objects.get_or_create(
        codigo='existencias', defaults={'nombre': 'Existencias', 'orden': 5})
    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo='gestion_producto',
        defaults={'modulo': modulo, 'nombre': 'Gestion Producto', 'orden': 1})
    PermisoRol.objects.update_or_create(
        rol=rol, opcion_menu=opcion,
        defaults={'puede_ver': True, 'puede_crear': True, 'puede_editar': puede_editar})
    return opcion


def crear_color(valor):
    atributo, _ = Productos_Atributos.objects.get_or_create(
        nombre='Color', defaults={'descripcion': 'Color del producto'})
    return AtributoOpcion.objects.create(atributo=atributo, valor=valor)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class EdicionProductoIdentidadTest(TestCase):
    """La edición alcanza a la MISMA variante, no a todo el código."""

    def setUp(self):
        self.empresa = crear_empresa(nombre='Empresa Ident')
        self.bodega = crear_sucursal(empresa=self.empresa, alias='CD')
        self.tienda = crear_sucursal(empresa=self.empresa, alias='TIENDA')
        self.admin = crear_usuario(username='admin-ident', rol='administrador')
        crear_empresa_user(self.admin, self.empresa, self.bodega)
        habilitar_permiso_edicion()

        self.negro = crear_color('NEGRO')
        self.blanco = crear_color('BLANCO')

        # Mismo código en dos colores: son productos DISTINTOS.
        self.prod_negro, _ = crear_producto_con_talla(
            self.bodega, articulo='ZAP-777', sku=7770001,
            costo=10000, sobreprecio=10000, precioventa=20000,
            atributo2=self.negro,
        )
        self.prod_blanco, _ = crear_producto_con_talla(
            self.bodega, articulo='ZAP-777', sku=7770002,
            costo=10000, sobreprecio=10000, precioventa=20000,
            atributo2=self.blanco,
        )
        # La gemela del NEGRO en otra bodega: misma identidad completa.
        self.prod_negro_tienda, _ = crear_producto_con_talla(
            self.tienda, articulo='ZAP-777', sku=7770003,
            costo=10000, sobreprecio=10000, precioventa=20000,
            atributo2=self.negro,
        )

        self.client = Client()
        self.client.force_login(self.admin)
        session = self.client.session
        session['idSucursalActual'] = self.bodega.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _payload(self, producto, **overrides):
        datos = {
            'articulo': producto.articulo,
            'descripcion': producto.descripcion,
            'categoria_id': producto.categoria_id,
            'atributo1_id': producto.atributo1_id,
            'atributo2_id': producto.atributo2_id,
            'atributo3_id': producto.atributo3_id,
            'atributo4_id': producto.atributo4_id,
            'costo': producto.costo,
            'sobreprecio': producto.sobreprecio,
            'precioventa': producto.precioventa,
            'precioSugerido': 0,
            'propagar_sucursales': True,
        }
        datos.update(overrides)
        return datos

    def _editar(self, producto, **overrides):
        return self.client.post(
            reverse('actualizar_producto', args=[producto.id]),
            data=json.dumps(self._payload(producto, **overrides)),
            content_type='application/json',
        )

    def test_no_toca_otra_variante_del_mismo_codigo(self):
        resp = self._editar(
            self.prod_negro, articulo='ZAP-777-A', precioventa=44990)
        self.assertEqual(resp.status_code, 200, resp.content)

        self.prod_blanco.refresh_from_db()
        self.assertEqual(self.prod_blanco.articulo, 'ZAP-777')
        self.assertEqual(self.prod_blanco.precioventa, 20000)
        self.assertEqual(self.prod_blanco.atributo2_id, self.blanco.id)

    def test_alcanza_la_gemela_de_otra_bodega(self):
        resp = self._editar(
            self.prod_negro, articulo='ZAP-777-A', precioventa=44990)
        self.assertEqual(resp.status_code, 200, resp.content)

        self.prod_negro_tienda.refresh_from_db()
        self.assertEqual(self.prod_negro_tienda.articulo, 'ZAP-777-A')
        self.assertEqual(self.prod_negro_tienda.precioventa, 44990)

        sinc = resp.json()['sincronizacion']
        self.assertEqual(sinc['productos_actualizados'], 2)
        self.assertEqual(sorted(sinc['sucursales_afectadas']), ['CD', 'TIENDA'])

    def test_codigo_con_distinta_caja_tambien_se_sincroniza(self):
        """Las fichas legacy con el código en otra caja no pueden quedar fuera."""
        Producto.objects.filter(id=self.prod_negro_tienda.id).update(articulo='zap-777')

        resp = self._editar(self.prod_negro, precioventa=31990)
        self.assertEqual(resp.status_code, 200, resp.content)

        self.prod_negro_tienda.refresh_from_db()
        self.assertEqual(self.prod_negro_tienda.precioventa, 31990)

    def test_sin_propagar_solo_toca_la_ficha_seleccionada(self):
        resp = self._editar(
            self.prod_negro, precioventa=55990, propagar_sucursales=False)
        self.assertEqual(resp.status_code, 200, resp.content)

        self.prod_negro_tienda.refresh_from_db()
        self.assertEqual(self.prod_negro_tienda.precioventa, 20000)

    def test_edicion_masiva_respeta_la_identidad(self):
        resp = self.client.post(
            reverse('actualizar_productos_masivo'),
            data=json.dumps({
                'producto_ids': [self.prod_negro.id],
                'campos': {
                    'aplicar_precios': True,
                    'costo': 12000,
                    'sobreprecio': 12000,
                    'precioventa': 34990,
                },
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        self.prod_negro_tienda.refresh_from_db()
        self.prod_blanco.refresh_from_db()
        self.assertEqual(self.prod_negro_tienda.precioventa, 34990)
        self.assertEqual(self.prod_blanco.precioventa, 20000)

    def test_sin_permiso_de_edicion_responde_403(self):
        vendedor = crear_usuario(username='vendedor-ident', rol='vendedor')
        crear_empresa_user(vendedor, self.empresa, self.bodega)
        cliente = Client()
        cliente.force_login(vendedor)
        session = cliente.session
        session['idSucursalActual'] = self.bodega.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

        resp = cliente.post(
            reverse('actualizar_producto', args=[self.prod_negro.id]),
            data=json.dumps(self._payload(self.prod_negro, articulo='HACKEADO')),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)
        self.prod_negro.refresh_from_db()
        self.assertEqual(self.prod_negro.articulo, 'ZAP-777')

        resp_masivo = cliente.post(
            reverse('actualizar_productos_masivo'),
            data=json.dumps({
                'producto_ids': [self.prod_negro.id],
                'campos': {'aplicar_precios': True, 'costo': 1,
                           'sobreprecio': 1, 'precioventa': 1},
            }),
            content_type='application/json',
        )
        self.assertEqual(resp_masivo.status_code, 403)


def crear_traspaso_emitido(origen, destino, talla, cantidad=5, numero=8800,
                           descripcion=None):
    """Guía emitida y SIN recepcionar, con el snapshot del nombre al emitir."""
    dte = Dte.objects.create(
        emisor=origen.empresa,
        receptor=destino.empresa,
        numero_documento=numero,
        tipo_documento='GUIA',
        monto_neto=Decimal(1000 * cantidad),
        monto_con_iva=Decimal(1190 * cantidad),
        estado_pago='PENDIENTE',
        estado_dte='EMITIDO',
        responsable='tester',
        fecha_emision='2026-08-01',
        fecha_vencimiento='2026-08-01',
        diasCredito=0,
        bultos=1,
        unidades_productos=cantidad,
        tipo_transaccion='TRASPASO',
        sucursal=origen,
    )
    linea = Dte_Productos.objects.create(
        dte=dte, productoTalla=talla,
        # Mismo formato que usa la emisión real de traspasos.
        descripcion=descripcion or f'{talla.producto.articulo} - Talla {talla.talla}',
        costo=1000, sobreprecio=0, precio=1000, stock=cantidad, activo=True,
    )
    Movimientos_Producto.objects.create(
        dte=dte, ProductoTalla=talla,
        sucursal_origen=origen, sucursal_destino=destino,
        cantidad=-cantidad, concepto='TRASPASO_SALIDA',
        tipo_movimiento='EGRESO', estado='COMPLETADO', responsable='tester',
    )
    return dte, linea


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class EdicionConMercaderiaEnTransitoTest(TestCase):
    """Editar no bloquea el despacho, pero avisa en los dos extremos."""

    def setUp(self):
        self.empresa = crear_empresa(nombre='Empresa Transito')
        self.origen = crear_sucursal(empresa=self.empresa, alias='CD-T')
        self.destino = crear_sucursal(empresa=self.empresa, alias='TIENDA-T')
        self.admin = crear_usuario(username='admin-transito', rol='administrador')
        crear_empresa_user(self.admin, self.empresa, self.origen)
        habilitar_permiso_edicion()

        self.producto, self.talla = crear_producto_con_talla(
            self.origen, articulo='ZAP-901', talla='42', sku=9010001, stock=20,
            costo=10000, sobreprecio=10000, precioventa=20000,
        )
        self.dte, self.linea = crear_traspaso_emitido(
            self.origen, self.destino, self.talla, cantidad=5, numero=8801,
        )

        self.client = Client()
        self.client.force_login(self.admin)
        session = self.client.session
        session['idSucursalActual'] = self.origen.id
        session['idEmpresaActual'] = self.empresa.id
        session['alias'] = self.origen.alias
        session.save()

    def _editar_articulo(self, nuevo):
        return self.client.post(
            reverse('actualizar_producto', args=[self.producto.id]),
            data=json.dumps({
                'articulo': nuevo,
                'descripcion': self.producto.descripcion,
                'categoria_id': self.producto.categoria_id,
                'costo': self.producto.costo,
                'sobreprecio': self.producto.sobreprecio,
                'precioventa': self.producto.precioventa,
                'precioSugerido': 0,
                'propagar_sucursales': True,
            }),
            content_type='application/json',
        )

    def test_la_edicion_avisa_del_traspaso_en_transito(self):
        resp = self._editar_articulo('ZAP-901-NUEVO')
        self.assertEqual(resp.status_code, 200, resp.content)

        transito = resp.json()['traspasos_en_transito']
        self.assertIsNotNone(transito, 'no avisó del despacho sin recepcionar')
        self.assertEqual(transito['total_documentos'], 1)
        self.assertEqual(transito['unidades'], 5)
        self.assertEqual(transito['sucursales_destino'], ['TIENDA-T'])
        self.assertEqual(transito['documentos'][0]['numero_documento'], 8801)

    def test_dte_recepcionado_ya_no_avisa(self):
        Dte.objects.filter(id=self.dte.id).update(
            fecha_recepcion='2026-08-05', estado_dte='RECEPCIONADO_COMPLETO')
        resp = self._editar_articulo('ZAP-901-OTRO')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIsNone(resp.json()['traspasos_en_transito'])

    def test_recepcion_marca_la_linea_como_ficha_editada(self):
        self._editar_articulo('ZAP-901-NUEVO')

        session = self.client.session
        session['idSucursalActual'] = self.destino.id
        session['alias'] = self.destino.alias
        session.save()

        with mock.patch('app.views.PermisoRol.tiene_permiso', return_value=True), \
             mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True):
            resp = self.client.get('/app/dte/recepciones_pendientes/')

        self.assertEqual(resp.status_code, 200, resp.content)
        item = next(i for i in resp.json()['items'] if i['id'] == self.dte.id)
        self.assertEqual(item['lineas_editadas'], 1)

        linea = item['detalle'][0]
        self.assertTrue(linea['ficha_editada'])
        self.assertEqual(linea['descripcion_documento'], 'ZAP-901 - Talla 42')
        self.assertEqual(linea['articulo'], 'ZAP-901-NUEVO')

    def test_sin_edicion_la_linea_no_se_marca(self):
        session = self.client.session
        session['idSucursalActual'] = self.destino.id
        session['alias'] = self.destino.alias
        session.save()

        with mock.patch('app.views.PermisoRol.tiene_permiso', return_value=True), \
             mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True):
            resp = self.client.get('/app/dte/recepciones_pendientes/')

        item = next(i for i in resp.json()['items'] if i['id'] == self.dte.id)
        self.assertEqual(item['lineas_editadas'], 0)
        self.assertFalse(item['detalle'][0]['ficha_editada'])

    def test_recepcion_reusa_la_ficha_legacy_con_otra_caja(self):
        """El destino ya tiene el código en minúscula: no debe duplicarse.

        La recepción crea la ficha de la tienda buscándola por código +
        atributos. Con igualdad exacta, 'zap-901' (herencia de la migración
        Laravel) no calzaba con 'ZAP-901' y nacía una SEGUNDA ficha del mismo
        producto en la misma bodega, partiendo histórico y SKU.
        """
        legacy, _ = crear_producto_con_talla(
            self.destino, articulo='zap-901', talla='38', sku=9010099, stock=3,
            costo=10000, sobreprecio=10000, precioventa=20000,
        )
        fichas_antes = Producto.objects.filter(sucursal=self.destino).count()

        session = self.client.session
        session['idSucursalActual'] = self.destino.id
        session['alias'] = self.destino.alias
        session.save()

        with mock.patch('app.views.PermisoRol.tiene_permiso', return_value=True), \
             mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True):
            resp = self.client.post(
                reverse('confirmar_recepcion_api'),
                data=json.dumps({'dte_id': self.dte.id}),
                content_type='application/json',
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'], resp.content)
        self.assertEqual(
            Producto.objects.filter(sucursal=self.destino).count(), fichas_antes,
            'la recepción creó una ficha duplicada en el destino',
        )
        legacy.refresh_from_db()
        self.assertTrue(
            legacy.producto_talla.filter(talla='42').exists(),
            'la talla recepcionada no colgó de la ficha existente',
        )
