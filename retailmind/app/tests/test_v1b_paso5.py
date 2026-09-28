"""
Paso 5 de compras (Gestión Producto): crear, revertir, eliminar y vincular
productos de la compra. Tests de la unidad V1B (auditoría 26-sep-2026).

- CC-02  eliminar_producto_todas_sucursales: permiso puede_eliminar, alcance
         por empresa y bloqueo (409) ante cualquier historia (ventas PAGADAS,
         traspasos, movimientos que no sean el ingreso de compra).
- CC-01  revertir_producto_a_pendiente: permiso, la reversión total de una
         talla con historia NO borra nada (egreso de corrección) y la parcial
         ya no revienta (antes create() con kwargs inexistentes → 500).
- CC-03  ingresos de stock: permiso puede_crear y DTE de compra emitido a la
         empresa de la sesión (Crear Manual, suma rápida, selector de DTE).
- B2-01  crear_producto_desde_recepcion no ingresa stock sin recepción ni
         re-enlaza recepciones ya creadas.
- B15-03 guardia por ingreso manual previo con la misma factura.
- B2-05  desvincular deshace exactamente lo que hizo vincular.
- B2-07  limpiar_productos_compra (sin consumidor) se retiró: 404, nada borrado.
- B2-09  actualizar_recepciones_producto: tope de cantidad y talla compartida.

Ejecutar (BD de test aislada, NO producción):
    python manage.py test app.tests.test_v1b_paso5 --keepdb --noinput
"""
import json
from unittest import mock

from django.test import TestCase

from app import views as app_views
from app.models import (
    AtributoOpcion, Categoria, Compras, Compras_Producto, Compras_Producto_Talla,
    Dte, LoteProducto, ModuloSistema, Movimientos_Producto, OpcionMenu,
    PendienteDespacho, PermisoRol, Producto, Producto_Talla, Productos_Atributos,
    Productos_Recepcionados, Ticket, Ticket_Productos,
)

from .factories import (
    crear_empresa, crear_empresa_user, crear_lote_fifo, crear_producto_con_talla,
    crear_sucursal, crear_usuario, crear_vendedor,
)

TODOS = {
    'puede_ver': True, 'puede_crear': True, 'puede_editar': True,
    'puede_eliminar': True, 'puede_exportar': True, 'puede_aprobar': True,
}


CODIGOS_PANTALLA = (
    'gestion_producto', 'gestion_compras', 'gestion_dte_compras', 'recepcion_dte',
    'dashboard_productos', 'trazabilidad_producto',
)


def otorgar(rol, codigos=CODIGOS_PANTALLA, **flags):
    """PermisoRol explícito (el middleware y los chequeos finos lo exigen)."""
    modulo, _ = ModuloSistema.objects.get_or_create(
        codigo='v1b_test', defaults={'nombre': 'V1B'})
    valores = dict(TODOS)
    valores.update(flags)
    for codigo in codigos:
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo=codigo, defaults={'modulo': modulo, 'nombre': codigo, 'activo': True})
        PermisoRol.objects.update_or_create(rol=rol, opcion_menu=opcion, defaults=valores)


def crear_dte(emisor, receptor, numero, tipo_transaccion='COMPRA',
              tipo_documento='FACTURA ELECTRONICA', **kwargs):
    datos = dict(
        emisor=emisor, receptor=receptor, numero_documento=numero,
        tipo_documento=tipo_documento, monto_neto=1000, monto_con_iva=1190,
        estado_pago='PENDIENTE', estado_dte='ACEPTADO', responsable='test',
        fecha_emision='2026-09-01', fecha_vencimiento='2026-09-30',
        diasCredito=30, bultos=1, unidades_productos=0,
        tipo_transaccion=tipo_transaccion,
    )
    datos.update(kwargs)
    return Dte.objects.create(**datos)


class BaseV1B(TestCase):
    """Dos empresas del holding (A y B), un proveedor y un maestro en A."""

    _correlativo = 0

    @classmethod
    def setUpTestData(cls):
        cls.emp_a = crear_empresa(nombre='Empresa A', rut='76.100.100-1')
        cls.emp_b = crear_empresa(nombre='Empresa B', rut='76.200.200-2')
        cls.suc_a = crear_sucursal(empresa=cls.emp_a, alias='BOD-A')
        cls.suc_a2 = crear_sucursal(empresa=cls.emp_a, alias='TIENDA-A')
        cls.suc_b = crear_sucursal(empresa=cls.emp_b, alias='BOD-B')
        cls.proveedor = crear_empresa(nombre='Proveedor', rut='77.300.300-3', esProveedor=True)
        cls.vendedor = crear_vendedor(empresa=cls.emp_a)

        cls.maestro = crear_usuario(username='v1b_maestro', rol='maestro')
        crear_empresa_user(cls.maestro, cls.emp_a, cls.suc_a)
        cls.vend = crear_usuario(username='v1b_vendedor', rol='vendedor')
        crear_empresa_user(cls.vend, cls.emp_a, cls.suc_a)
        # jefe_local con TODOS los permisos de pantalla pero alcance = empresa A.
        cls.jefe = crear_usuario(username='v1b_jefe_local', rol='jefe_local')
        crear_empresa_user(cls.jefe, cls.emp_a, cls.suc_a)
        otorgar('jefe_local')
        # vendedor: ve la pantalla pero sin crear/editar/eliminar.
        otorgar('vendedor', puede_crear=False, puede_editar=False, puede_eliminar=False)

    def entrar(self, user, sucursal=None):
        sucursal = sucursal or self.suc_a
        self.client.force_login(user)
        s = self.client.session
        s['idSucursalActual'] = sucursal.id
        s['idEmpresaActual'] = sucursal.empresa_id
        s['nombreUsuario'] = user.username
        s.save()

    def post_json(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type='application/json')

    def assertBloqueado(self, r):
        # 403 del chequeo fino de la vista (o del middleware de permisos, que
        # a un POST de formulario puede responder con redirect).
        self.assertIn(r.status_code, (302, 403), r.content)

    @classmethod
    def vender(cls, pt, estado='PAGADO'):
        cls._correlativo += 1
        ticket = Ticket.objects.create(
            vendedor=cls.vendedor, sucursal=pt.producto.sucursal,
            correlativo=cls._correlativo, estado=estado, subTotal=1000,
            total=1000, responsable='test',
        )
        return Ticket_Productos.objects.create(
            idTicket=ticket, ProductoTalla=pt, stock=1, precio=1000,
            precio_original=1000, descuento_unitario=0, subtotal=1000,
        )

    def mov(self, pt, concepto, cantidad, dte=None):
        return Movimientos_Producto.objects.create(
            ProductoTalla=pt, concepto=concepto, cantidad=cantidad, dte=dte,
            sucursal_origen=pt.producto.sucursal, sucursal_destino=pt.producto.sucursal,
        )

    def compra_con_recepcion(self, pt=None, talla='40', comprado=5, recibido=5,
                             dte=None, sucursal_destino=None, nombre='ART-1'):
        self._correlativo += 1
        compra = Compras.objects.create(
            empresa=self.proveedor, nombre=f'OC {self._correlativo}',
            correlativo=self._correlativo, responsable='test', temporada='',
        )
        cp = Compras_Producto.objects.create(
            compras=compra, nombre=nombre, atributo1='M', atributo2='C',
            atributo3='G', atributo4='', costo=1000, precioSugerido=2000,
            sucursal_destino=sucursal_destino,
        )
        cpt = Compras_Producto_Talla.objects.create(
            compra_producto=cp, stock=comprado, talla=talla, producto_talla=pt)
        rec = Productos_Recepcionados.objects.create(
            compra_producto_talla=cpt, producto_talla=pt, stockArribado=recibido,
            dte=dte, sucursal_destino=sucursal_destino,
        )
        return compra, cp, cpt, rec


# =========================================================================
# CC-02 — eliminar producto en todas las sucursales
# =========================================================================
class EliminarProductoTest(BaseV1B):
    URL = '/app/eliminar_producto_todas_sucursales/'

    def setUp(self):
        self.prod, self.pt = crear_producto_con_talla(self.suc_a, articulo='DEL-1', sku=91001, stock=0)

    def test_sin_permiso_eliminar_responde_403_y_no_borra(self):
        self.entrar(self.vend)
        r = self.post_json(self.URL, {'producto_id': self.prod.id})
        self.assertBloqueado(r)
        self.assertTrue(Producto.objects.filter(id=self.prod.id).exists())

    def test_venta_pagada_bloquea_con_409(self):
        linea = self.vender(self.pt, estado='PAGADO')
        self.entrar(self.maestro)
        r = self.post_json(self.URL, {'producto_id': self.prod.id})
        self.assertEqual(r.status_code, 409, r.content)
        tipos = [b['tipo'] for b in r.json()['bloqueos']]
        self.assertIn('VENTAS_REGISTRADAS', tipos)
        self.assertTrue(Ticket_Productos.objects.filter(id=linea.id).exists())
        self.assertTrue(Producto_Talla.objects.filter(id=self.pt.id).exists())

    def test_movimiento_que_no_es_ingreso_bloquea(self):
        self.mov(self.pt, 'INGRESO_INICIAL', 3)
        self.mov(self.pt, 'VENTA_PUBLICO', -3)
        self.entrar(self.maestro)
        r = self.post_json(self.URL, {'producto_id': self.prod.id})
        self.assertEqual(r.status_code, 409, r.content)
        self.assertIn('MOVIMIENTOS_HISTORIAL', [b['tipo'] for b in r.json()['bloqueos']])
        self.assertEqual(Movimientos_Producto.objects.filter(ProductoTalla=self.pt).count(), 2)

    def test_solo_ingreso_de_compra_y_stock_cero_se_borra(self):
        self.mov(self.pt, 'INGRESO_INICIAL', 0)
        self.entrar(self.maestro)
        r = self.post_json(self.URL, {'producto_id': self.prod.id})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Producto.objects.filter(id=self.prod.id).exists())

    def test_producto_de_otra_empresa_403(self):
        prod_b, _pt_b = crear_producto_con_talla(self.suc_b, articulo='DEL-B', sku=91002, stock=0)
        self.entrar(self.jefe)
        r = self.post_json(self.URL, {'producto_id': prod_b.id})
        self.assertEqual(r.status_code, 403, r.content)
        self.assertTrue(Producto.objects.filter(id=prod_b.id).exists())

    def test_gemelo_de_otra_empresa_no_se_borra(self):
        # Misma clave lógica en la empresa B: un jefe_local de A solo borra lo suyo.
        prod_b, _ = crear_producto_con_talla(self.suc_b, articulo='DEL-1', sku=91003, stock=0)
        Producto.objects.filter(id=prod_b.id).update(categoria=self.prod.categoria)
        self.entrar(self.jefe)
        r = self.post_json(self.URL, {'producto_id': self.prod.id})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Producto.objects.filter(id=self.prod.id).exists())
        self.assertTrue(Producto.objects.filter(id=prod_b.id).exists())


# =========================================================================
# CC-01 — revertir producto a pendiente
# =========================================================================
class RevertirAPendienteTest(BaseV1B):
    URL = '/app/api/producto/revertir-a-pendiente/'

    def setUp(self):
        self.prod, self.pt = crear_producto_con_talla(self.suc_a, articulo='REV-1', sku=92001, stock=0)
        self.dte = crear_dte(self.proveedor, self.emp_a, 5001)
        # Ingreso por recepción: 5 u (movimiento INGRESO_INICIAL + lote).
        self.mov(self.pt, 'INGRESO_INICIAL', 5, dte=self.dte)
        Producto_Talla.objects.filter(id=self.pt.id).update(stock=5)
        crear_lote_fifo(self.pt, cantidad=5, costo_unitario=1000)
        _, _, self.cpt, self.rec = self.compra_con_recepcion(pt=self.pt, dte=self.dte)
        Compras_Producto_Talla.objects.filter(id=self.cpt.id).update(unidades_recibidas=5)

    def test_sin_permiso_403(self):
        self.entrar(self.vend)
        r = self.post_json(self.URL, {'producto_talla_id': self.pt.id, 'motivo': 'x'})
        self.assertBloqueado(r)
        self.assertTrue(Producto_Talla.objects.filter(id=self.pt.id).exists())

    def test_total_sin_historia_borra_la_talla(self):
        self.entrar(self.maestro)
        r = self.post_json(self.URL, {'producto_talla_id': self.pt.id, 'motivo': 'código mal'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['resumen']['tipo'], 'total')
        self.assertFalse(Producto_Talla.objects.filter(id=self.pt.id).exists())
        self.rec.refresh_from_db()
        self.assertIsNone(self.rec.producto_talla_id)

    def test_total_con_venta_pagada_no_borra_y_registra_egreso(self):
        linea = self.vender(self.pt, estado='PAGADO')
        self.mov(self.pt, 'VENTA_PUBLICO', -1)
        Producto_Talla.objects.filter(id=self.pt.id).update(stock=4)
        self.entrar(self.maestro)
        r = self.post_json(self.URL, {'producto_talla_id': self.pt.id, 'motivo': 'código mal'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['resumen']['tipo'], 'total_sin_borrar')
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 0)
        self.assertTrue(Ticket_Productos.objects.filter(id=linea.id).exists())
        egreso = Movimientos_Producto.objects.get(ProductoTalla=self.pt, concepto='CORRECCION_STOCK')
        self.assertEqual(egreso.cantidad, -4)
        self.assertEqual(egreso.tipo_movimiento, 'EGRESO')
        # 4 u volvieron a pendiente (split de la recepción de 5); 1 sigue creada.
        pendientes = Productos_Recepcionados.objects.filter(
            compra_producto_talla=self.cpt, producto_talla__isnull=True)
        self.assertEqual(sum(p.stockArribado for p in pendientes), 4)
        self.rec.refresh_from_db()
        self.assertEqual((self.rec.producto_talla_id, self.rec.stockArribado), (self.pt.id, 1))
        self.cpt.refresh_from_db()
        self.assertEqual(self.cpt.unidades_recibidas, 1)
        # Kardex intacto: ingreso + venta + egreso de corrección.
        self.assertEqual(Movimientos_Producto.objects.filter(ProductoTalla=self.pt).count(), 3)

    def test_parcial_ya_no_revienta(self):
        self.entrar(self.maestro)
        r = self.post_json(self.URL, {'producto_talla_id': self.pt.id, 'cantidad': 2, 'motivo': 'sobraban'})
        self.assertEqual(r.status_code, 200, r.content)
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 3)
        lote = LoteProducto.objects.get(producto_talla=self.pt)
        self.assertEqual(lote.cantidad_disponible, 3)

    def test_con_historia_y_sin_stock_409(self):
        self.vender(self.pt, estado='PAGADO')
        Producto_Talla.objects.filter(id=self.pt.id).update(stock=0)
        self.entrar(self.maestro)
        r = self.post_json(self.URL, {'producto_talla_id': self.pt.id, 'motivo': 'x'})
        self.assertEqual(r.status_code, 409, r.content)
        self.assertTrue(Producto_Talla.objects.filter(id=self.pt.id).exists())

    def test_talla_de_otra_empresa_403(self):
        _, pt_b = crear_producto_con_talla(self.suc_b, articulo='REV-B', sku=92002, stock=0)
        self.entrar(self.jefe)
        r = self.post_json(self.URL, {'producto_talla_id': pt_b.id, 'motivo': 'x'})
        self.assertEqual(r.status_code, 403, r.content)

    # ----- revisión adversarial -----------------------------------------

    def test_permiso_de_gestion_compras_basta(self):
        # El botón Revertir de Editar Recepciones (Gestión de Compras) usa el
        # mismo endpoint: gestion_compras/puede_eliminar alcanza.
        adm = crear_usuario(username='v1b_administracion', rol='administracion')
        crear_empresa_user(adm, self.emp_a, self.suc_a)
        otorgar('administracion', codigos=('gestion_compras',))
        otorgar('administracion', codigos=('gestion_producto',), puede_eliminar=False)
        self.entrar(adm)
        r = self.post_json(self.URL, {'producto_talla_id': self.pt.id, 'motivo': 'desde compras'})
        self.assertEqual(r.status_code, 200, r.content)

    def test_total_con_stock_sin_factura_no_borra_ese_saldo(self):
        # 5 u de la recepción + 7 u de un ingreso manual SIN factura: el
        # borrado total hacía desaparecer las 7 u.
        self.mov(self.pt, 'INGRESO_MANUAL', 7)
        Producto_Talla.objects.filter(id=self.pt.id).update(stock=12)
        crear_lote_fifo(self.pt, cantidad=7, costo_unitario=1000)
        self.entrar(self.maestro)
        r = self.post_json(self.URL, {'producto_talla_id': self.pt.id, 'motivo': 'x'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['resumen']['tipo'], 'total_sin_borrar')
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 7)
        self.rec.refresh_from_db()
        self.assertIsNone(self.rec.producto_talla_id)
        egreso = Movimientos_Producto.objects.get(ProductoTalla=self.pt, concepto='CORRECCION_STOCK')
        self.assertEqual(egreso.cantidad, -5)

    def test_total_de_saldo_sin_recepciones_409(self):
        # Saldo de migración: INGRESO_INICIAL sin DTE y sin recepciones.
        prod, pt = crear_producto_con_talla(self.suc_a, articulo='SALDO-1', sku=92003, stock=20)
        self.mov(pt, 'INGRESO_INICIAL', 20)
        self.entrar(self.maestro)
        r = self.post_json(self.URL, {'producto_talla_id': pt.id, 'motivo': 'x'})
        self.assertEqual(r.status_code, 409, r.content)
        pt.refresh_from_db()
        self.assertEqual(pt.stock, 20)
        self.assertTrue(Producto.objects.filter(id=prod.id).exists())
        self.assertEqual(Movimientos_Producto.objects.filter(ProductoTalla=pt).count(), 1)

    def test_parcial_y_luego_total_si_borra(self):
        # El egreso de la reversión parcial no es "historia de terceros".
        self.entrar(self.maestro)
        r1 = self.post_json(self.URL, {'producto_talla_id': self.pt.id, 'cantidad': 2, 'motivo': 'x'})
        self.assertEqual(r1.status_code, 200, r1.content)
        r2 = self.post_json(self.URL, {'producto_talla_id': self.pt.id, 'motivo': 'x'})
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertEqual(r2.json()['resumen']['tipo'], 'total')
        self.assertFalse(Producto_Talla.objects.filter(id=self.pt.id).exists())
        pendientes = Productos_Recepcionados.objects.filter(
            compra_producto_talla=self.cpt, producto_talla__isnull=True)
        self.assertEqual(sum(p.stockArribado for p in pendientes), 5)

    def test_desenlace_completo_suelta_el_movimiento_ingreso(self):
        ingreso = Movimientos_Producto.objects.get(ProductoTalla=self.pt, concepto='INGRESO_INICIAL')
        Productos_Recepcionados.objects.filter(id=self.rec.id).update(movimiento_ingreso=ingreso)
        self.vender(self.pt, estado='PAGADO')   # historia → total_sin_borrar
        self.entrar(self.maestro)
        r = self.post_json(self.URL, {'producto_talla_id': self.pt.id, 'motivo': 'x'})
        self.assertEqual(r.status_code, 200, r.content)
        self.rec.refresh_from_db()
        self.assertIsNone(self.rec.producto_talla_id)
        self.assertIsNone(self.rec.movimiento_ingreso_id)

    def _cola_despacho(self, despachada=0):
        Productos_Recepcionados.objects.filter(id=self.rec.id).update(sucursal_destino=self.suc_a2)
        return PendienteDespacho.objects.create(
            producto_talla=self.pt, sucursal_origen=self.suc_a, sucursal_destino=self.suc_a2,
            cantidad=5, cantidad_despachada=despachada,
            estado='DESPACHADO' if despachada >= 5 else 'PENDIENTE',
        )

    def test_revertir_descuenta_la_cola_de_despacho(self):
        pd = self._cola_despacho()
        self.vender(self.pt, estado='PAGADO')
        Producto_Talla.objects.filter(id=self.pt.id).update(stock=4)
        self.entrar(self.maestro)
        r = self.post_json(self.URL, {'producto_talla_id': self.pt.id, 'motivo': 'x'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['resumen']['tipo'], 'total_sin_borrar')
        pd.refresh_from_db()
        # 4 u volvieron a pendiente de crear: la cola ya no las pide.
        self.assertEqual((pd.cantidad, pd.estado), (1, 'PENDIENTE'))

    def test_revertir_unidades_ya_despachadas_409(self):
        self._cola_despacho(despachada=5)
        self.vender(self.pt, estado='PAGADO')
        Producto_Talla.objects.filter(id=self.pt.id).update(stock=4)
        self.entrar(self.maestro)
        r = self.post_json(self.URL, {'producto_talla_id': self.pt.id, 'motivo': 'x'})
        self.assertEqual(r.status_code, 409, r.content)
        self.rec.refresh_from_db()
        self.assertEqual((self.rec.producto_talla_id, self.rec.stockArribado), (self.pt.id, 5))
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 4)


# =========================================================================
# CC-03 — ingresos de stock: permiso y receptor del DTE
# =========================================================================
class IngresoStockPermisoReceptorTest(BaseV1B):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cat = Categoria.objects.create(nombre='Zapatillas')
        am = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        ac = Productos_Atributos.objects.create(nombre='Color', descripcion='Color')
        ag = Productos_Atributos.objects.create(nombre='Género', descripcion='Género')
        cls.marca = AtributoOpcion.objects.create(atributo=am, valor='NIKE')
        cls.color = AtributoOpcion.objects.create(atributo=ac, valor='NEGRO')
        cls.genero = AtributoOpcion.objects.create(atributo=ag, valor='HOMBRE')
        cls.dte_a = crear_dte(cls.proveedor, cls.emp_a, 7001)
        cls.dte_b = crear_dte(cls.proveedor, cls.emp_b, 7002)
        cls.nc_a = crear_dte(cls.proveedor, cls.emp_a, 7003, tipo_documento='NOTA DE CREDITO',
                             es_nota_credito=True)

    def payload_manual(self, dte):
        return {
            'es_manual': 'true', 'proveedor': str(self.proveedor.id), 'dte_manual': str(dte.id),
            'articulo': 'MAN-1', 'descripcion': 'x', 'atributo1': str(self.marca.id),
            'atributo2': str(self.color.id), 'atributo3': str(self.genero.id),
            'categoria': str(self.cat.id), 'tipo_talla': 'CL', 'costo': '1000',
            'sobreprecio': '0', 'precioventa': '2000',
            'talla[]': ['40'], 'stock[]': ['3'], 'sku[]': [''],
        }

    def test_crear_manual_sin_permiso_403(self):
        self.entrar(self.vend)
        r = self.client.post('/app/crear_producto_manual/', self.payload_manual(self.dte_a))
        self.assertBloqueado(r)
        self.assertFalse(Producto.objects.filter(articulo='MAN-1').exists())

    def test_crear_manual_con_factura_de_otra_empresa_400(self):
        self.entrar(self.maestro)
        r = self.client.post('/app/crear_producto_manual/', self.payload_manual(self.dte_b))
        self.assertEqual(r.status_code, 400, r.content)
        self.assertFalse(Producto.objects.filter(articulo='MAN-1').exists())
        self.assertFalse(Movimientos_Producto.objects.filter(dte=self.dte_b).exists())

    def test_crear_manual_con_nota_de_credito_400(self):
        self.entrar(self.maestro)
        r = self.client.post('/app/crear_producto_manual/', self.payload_manual(self.nc_a))
        self.assertEqual(r.status_code, 400, r.content)

    def test_crear_manual_valido_ingresa(self):
        self.entrar(self.maestro)
        r = self.client.post('/app/crear_producto_manual/', self.payload_manual(self.dte_a))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['success'], r.content)
        pt = Producto_Talla.objects.get(producto__articulo='MAN-1', producto__sucursal=self.suc_a)
        self.assertEqual(pt.stock, 3)

    def test_crear_manual_error_a_mitad_no_deja_stock(self):
        # Antes: producto, talla e INGRESO_MANUAL en autocommit; un error
        # posterior respondía 500 "reintenta" con el stock ya dentro.
        payload = self.payload_manual(self.dte_a)
        payload.update({'talla[]': ['40', '41'], 'stock[]': ['3', '2'], 'sku[]': ['', '']})
        original = app_views.registrar_movimiento_producto
        llamadas = []

        def falla_en_la_segunda(*args, **kwargs):
            llamadas.append(1)
            if len(llamadas) == 2:
                raise RuntimeError('falla simulada')
            return original(*args, **kwargs)

        self.entrar(self.maestro)
        with mock.patch.object(app_views, 'registrar_movimiento_producto',
                               side_effect=falla_en_la_segunda):
            r = self.client.post('/app/crear_producto_manual/', payload)
        self.assertEqual(r.status_code, 500, r.content)
        self.assertEqual(len(llamadas), 2)
        self.assertFalse(Producto.objects.filter(articulo='MAN-1').exists())
        self.assertFalse(Movimientos_Producto.objects.filter(dte=self.dte_a).exists())
        self.assertFalse(LoteProducto.objects.filter(producto_talla__producto__articulo='MAN-1').exists())

    def test_suma_rapida_sin_permiso_y_con_factura_ajena(self):
        prod, pt = crear_producto_con_talla(self.suc_a, articulo='SUM-1', sku=93001, stock=0)
        payload = {'producto_id': prod.id, 'dte_id': self.dte_b.id, 'vincular_dte': True,
                   'lineas': [{'producto_talla_id': pt.id, 'cantidad': 500}]}
        self.entrar(self.vend)
        self.assertBloqueado(self.post_json('/app/api/ingreso-manual/sumar-stock/', payload))
        self.entrar(self.maestro)
        r = self.post_json('/app/api/ingreso-manual/sumar-stock/', payload)
        self.assertEqual(r.status_code, 400, r.content)
        pt.refresh_from_db()
        self.assertEqual(pt.stock, 0)

    def test_selector_dtes_solo_compras_de_la_empresa_sin_nc(self):
        self.entrar(self.maestro)
        r = self.client.get(f'/app/dtes_por_proveedor/{self.proveedor.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        ids = {d['id'] for d in r.json()}
        self.assertEqual(ids, {self.dte_a.id})

    def test_selector_sin_compras_no_cae_a_cualquier_dte(self):
        otro = crear_empresa(nombre='Prov sin compras', rut='77.999.999-9', esProveedor=True)
        crear_dte(self.emp_a, otro, 7100, tipo_transaccion='VENTA', tipo_documento='FACTURA ELECTRONICA')
        self.entrar(self.maestro)
        r = self.client.get(f'/app/dtes_por_proveedor/{otro.id}/')
        self.assertEqual(r.json(), [])

    def test_editar_talla_creado_sin_permiso_403(self):
        _, pt = crear_producto_con_talla(self.suc_a, articulo='EDT-1', sku=93002, stock=0)
        self.entrar(self.vend)
        r = self.post_json('/app/api/producto/editar-talla-creado/',
                           {'producto_talla_id': pt.id, 'nueva_talla': '41'})
        self.assertBloqueado(r)

    def test_editar_talla_sku_repetido_no_deja_la_compra_renombrada(self):
        _, pt = crear_producto_con_talla(self.suc_a, articulo='EDT-2', sku=93003, stock=0, talla='40')
        crear_producto_con_talla(self.suc_a, articulo='EDT-3', sku=93004, stock=0)
        _, _, cpt, _ = self.compra_con_recepcion(pt=pt, talla='40')
        self.entrar(self.maestro)
        r = self.post_json('/app/api/producto/editar-talla-creado/',
                           {'producto_talla_id': pt.id, 'nueva_talla': '41', 'nuevo_sku': '93004'})
        self.assertEqual(r.status_code, 409, r.content)
        cpt.refresh_from_db()
        self.assertEqual(cpt.talla, '40')


# =========================================================================
# B2-01 / B15-03 — crear producto desde recepción
# =========================================================================
class CrearDesdeRecepcionTest(BaseV1B):
    URL = '/app/crear_producto_desde_recepcion/'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cat = Categoria.objects.create(nombre='Zapatillas')
        am = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        cls.marca = AtributoOpcion.objects.create(atributo=am, valor='NIKE')
        cls.dte = crear_dte(cls.proveedor, cls.emp_a, 8001)

    def form(self, cp, stocks, **extra):
        data = {
            'producto_compra_id': str(cp.id), 'articulo': 'REC-1', 'descripcion': 'x',
            'atributo1': str(self.marca.id), 'categoria': str(self.cat.id),
            'costo': '1000', 'sobreprecio': '0', 'precioventa': '2000',
            'tipo_talla': 'CL', 'sucursales_destino_ids[]': [str(self.suc_a.id)],
        }
        for talla, stock in stocks.items():
            data[f'sku_{talla}'] = ''
            data[f'stock_{talla}'] = str(stock)
        data.update(extra)
        return data

    def test_talla_sin_recepcion_pendiente_409_y_no_ingresa_nada(self):
        _, cp, _, rec = self.compra_con_recepcion(talla='36', recibido=2, dte=self.dte,
                                                  sucursal_destino=self.suc_a, nombre='REC-1')
        self.entrar(self.maestro)
        # El usuario "corrigió" 36 -> 35 en el modal: no hay recepción de 35.
        r = self.client.post(self.URL, self.form(cp, {'35': 2}))
        self.assertEqual(r.status_code, 409, r.content)
        self.assertFalse(Producto.objects.filter(articulo='REC-1').exists())
        rec.refresh_from_db()
        self.assertIsNone(rec.producto_talla_id)

    def test_talla_origen_permite_corregir_la_talla(self):
        _, cp, _, rec = self.compra_con_recepcion(talla='36', recibido=2, dte=self.dte,
                                                  sucursal_destino=self.suc_a, nombre='REC-1')
        self.entrar(self.maestro)
        r = self.client.post(self.URL, self.form(cp, {'35': 2}, talla_origen_35='36'))
        self.assertEqual(r.status_code, 200, r.content)
        pt = Producto_Talla.objects.get(producto__articulo='REC-1', talla='35.0')
        self.assertEqual(pt.stock, 2)
        rec.refresh_from_db()
        self.assertEqual(rec.producto_talla_id, pt.id)

    def test_segunda_creacion_no_duplica_stock(self):
        _, cp, _, rec = self.compra_con_recepcion(talla='40', recibido=3, dte=self.dte,
                                                  sucursal_destino=self.suc_a, nombre='REC-1')
        self.entrar(self.maestro)
        r1 = self.client.post(self.URL, self.form(cp, {'40': 3}))
        self.assertEqual(r1.status_code, 200, r1.content)
        r2 = self.client.post(self.URL, self.form(cp, {'40': 3}))
        self.assertEqual(r2.status_code, 409, r2.content)
        pt = Producto_Talla.objects.get(producto__articulo='REC-1')
        self.assertEqual(pt.stock, 3)

    def test_guardia_ingreso_manual_previo_con_misma_factura(self):
        _, cp, _, _rec = self.compra_con_recepcion(talla='40', recibido=3, dte=self.dte,
                                                   sucursal_destino=self.suc_a, nombre='REC-1')
        # La misma factura ya ingresó el artículo por Crear Manual.
        _, pt_prev = crear_producto_con_talla(self.suc_a, articulo='REC-1', sku=94001, stock=3)
        self.mov(pt_prev, 'INGRESO_MANUAL', 3, dte=self.dte)
        self.entrar(self.maestro)
        r = self.client.post(self.URL, self.form(cp, {'40': 3}))
        self.assertEqual(r.status_code, 409, r.content)
        self.assertTrue(r.json()['requiere_confirmacion_ingreso_previo'])
        self.assertEqual(Movimientos_Producto.objects.filter(concepto='INGRESO_INICIAL').count(), 0)
        # Confirmado explícitamente (otra entrega): sigue.
        r2 = self.client.post(self.URL, self.form(cp, {'40': 3}, confirmar_ingreso_previo='1'))
        self.assertEqual(r2.status_code, 200, r2.content)

    def test_sin_permiso_crear_403(self):
        _, cp, _, _ = self.compra_con_recepcion(talla='40', recibido=3, dte=self.dte,
                                                sucursal_destino=self.suc_a, nombre='REC-1')
        self.entrar(self.vend)
        r = self.client.post(self.URL, self.form(cp, {'40': 3}))
        self.assertBloqueado(r)
        self.assertFalse(Producto.objects.filter(articulo='REC-1').exists())

    # ----- revisión adversarial -----------------------------------------

    def test_talla_origen_no_consume_dos_veces_la_misma_recepcion(self):
        # stock_40 y stock_41 con talla_origen_41=40 sobre UNA recepción de
        # 3 u: antes entraban 6 u con DTE (la 41 sin recepción enlazada).
        _, cp, _, rec = self.compra_con_recepcion(talla='40', recibido=3, dte=self.dte,
                                                  sucursal_destino=self.suc_a, nombre='REC-1')
        self.entrar(self.maestro)
        r = self.client.post(self.URL, self.form(cp, {'40': 3, '41': 3}, talla_origen_41='40'))
        self.assertEqual(r.status_code, 409, r.content)
        self.assertFalse(Movimientos_Producto.objects.filter(concepto='INGRESO_INICIAL').exists())
        self.assertFalse(Producto.objects.filter(articulo='REC-1').exists())
        rec.refresh_from_db()
        self.assertIsNone(rec.producto_talla_id)

    def test_crear_enlaza_exactamente_lo_ingresado(self):
        _, cp, _, rec = self.compra_con_recepcion(talla='40', recibido=3, dte=self.dte,
                                                  sucursal_destino=self.suc_a, nombre='REC-1')
        self.entrar(self.maestro)
        r = self.client.post(self.URL, self.form(cp, {'40': 3}))
        self.assertEqual(r.status_code, 200, r.content)
        pt = Producto_Talla.objects.get(producto__articulo='REC-1')
        rec.refresh_from_db()
        ingreso = Movimientos_Producto.objects.get(ProductoTalla=pt, concepto='INGRESO_INICIAL')
        self.assertEqual((rec.producto_talla_id, rec.movimiento_ingreso_id), (pt.id, ingreso.id))
        self.assertEqual((pt.stock, ingreso.cantidad), (3, 3))

    def test_linea_de_compra_inexistente_404(self):
        self.entrar(self.maestro)
        data = self.form(Compras_Producto(id=987654), {'40': 3})
        r = self.client.post(self.URL, data)
        self.assertEqual(r.status_code, 404, r.content)
        self.assertFalse(Producto.objects.filter(articulo='REC-1').exists())

    def test_sin_destino_indica_la_sucursal_de_las_pendientes(self):
        # "Editar recepción → Crear producto" no manda sucursal destino: la
        # talla solo tiene pendientes para TIENDA-A → 409 que dice dónde.
        _, cp, _, _ = self.compra_con_recepcion(talla='40', recibido=3, dte=self.dte,
                                                sucursal_destino=self.suc_a2, nombre='REC-1')
        self.entrar(self.maestro)
        data = self.form(cp, {'40': 3})
        data.pop('sucursales_destino_ids[]')
        r = self.client.post(self.URL, data)
        self.assertEqual(r.status_code, 409, r.content)
        self.assertIn('TIENDA-A', r.json()['error'])


# =========================================================================
# B2-05 — vincular / desvincular retroactivo
# =========================================================================
class VincularDesvincularTest(BaseV1B):

    def setUp(self):
        _, self.pt1 = crear_producto_con_talla(self.suc_a, articulo='VIN-1', sku=95001, stock=0)
        _, self.pt2 = crear_producto_con_talla(self.suc_a, articulo='VIN-2', sku=95002, stock=0)
        self.dte = crear_dte(self.proveedor, self.emp_a, 9001)
        self.entrar(self.maestro)

    def vincular(self, compra, cpt, pt, es_historica=False):
        return self.post_json('/app/api/compra/vincular-retroactivo/', {
            'compra_id': compra.id, 'es_historica': es_historica,
            'vinculaciones': [{'cpt_id': cpt.id, 'producto_talla_id': pt.id}],
        })

    def desvincular(self, cpt):
        return self.post_json('/app/api/compra/desvincular-retroactivo/', {'cpt_id': cpt.id})

    def test_no_historica_desvincular_y_revincular_no_duplica(self):
        compra, _, cpt, rec = self.compra_con_recepcion(talla='40', comprado=2, recibido=2, dte=self.dte)
        self.assertEqual(self.vincular(compra, cpt, self.pt1).status_code, 200)
        r = self.desvincular(cpt)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['recepciones_restauradas'], 1)
        rec.refresh_from_db()
        self.assertIsNone(rec.producto_talla_id)
        self.assertEqual(rec.dte_id, self.dte.id)
        self.assertEqual(self.vincular(compra, cpt, self.pt2).status_code, 200)
        recs = Productos_Recepcionados.objects.filter(compra_producto_talla=cpt)
        self.assertEqual(recs.count(), 1)
        self.assertEqual(sum(x.stockArribado for x in recs), 2)
        self.assertEqual(recs.get().producto_talla_id, self.pt2.id)

    def test_historica_con_recepcion_real_no_se_borra(self):
        compra, _, cpt, rec = self.compra_con_recepcion(talla='40', comprado=1, recibido=1, dte=self.dte)
        self.assertEqual(self.vincular(compra, cpt, self.pt1, es_historica=True).status_code, 200)
        r = self.desvincular(cpt)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['recepciones_borradas'], 0)
        rec.refresh_from_db()
        self.assertIsNone(rec.producto_talla_id)
        self.assertFalse(rec.es_historica)

    def test_fila_creada_por_vincular_se_borra_al_desvincular(self):
        compra, _, cpt, rec = self.compra_con_recepcion(talla='40', comprado=2, recibido=2)
        rec.delete()  # compra histórica sin recepción previa
        self.assertEqual(self.vincular(compra, cpt, self.pt1, es_historica=True).status_code, 200)
        self.assertEqual(Productos_Recepcionados.objects.filter(compra_producto_talla=cpt).count(), 1)
        r = self.desvincular(cpt)
        self.assertEqual(r.json()['recepciones_borradas'], 1)
        self.assertFalse(Productos_Recepcionados.objects.filter(compra_producto_talla=cpt).exists())

    def test_vincular_no_pisa_la_factura_real(self):
        compra, _, cpt, rec = self.compra_con_recepcion(talla='40', comprado=1, recibido=1, dte=self.dte)
        otra = crear_dte(self.proveedor, self.emp_a, 9002)
        r = self.post_json('/app/api/compra/vincular-retroactivo/', {
            'compra_id': compra.id, 'dte_id': otra.id,
            'vinculaciones': [{'cpt_id': cpt.id, 'producto_talla_id': self.pt1.id}],
        })
        self.assertEqual(r.status_code, 200, r.content)
        rec.refresh_from_db()
        self.assertEqual(rec.dte_id, self.dte.id)

    def test_desvincular_quita_la_factura_que_puso_vincular(self):
        compra, _, cpt, rec = self.compra_con_recepcion(talla='40', comprado=1, recibido=1, dte=None)
        otra = crear_dte(self.proveedor, self.emp_a, 9003)
        r = self.post_json('/app/api/compra/vincular-retroactivo/', {
            'compra_id': compra.id, 'dte_id': otra.id,
            'vinculaciones': [{'cpt_id': cpt.id, 'producto_talla_id': self.pt1.id}],
        })
        self.assertEqual(r.status_code, 200, r.content)
        rec.refresh_from_db()
        self.assertEqual((rec.producto_talla_id, rec.dte_id), (self.pt1.id, otra.id))
        r = self.desvincular(cpt)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['recepciones_restauradas'], 1)
        rec.refresh_from_db()
        self.assertIsNone(rec.producto_talla_id)
        self.assertIsNone(rec.dte_id)


# =========================================================================
# B2-07 / B2-09 — limpiar compra y editar recepciones pendientes
# =========================================================================
class LimpiarYEditarRecepcionesTest(BaseV1B):

    def setUp(self):
        self.entrar(self.maestro)

    def test_limpiar_productos_compra_ya_no_existe(self):
        """B2-07: la vista (sin consumidor en la UI; su rama force borraba
        recepciones en cascada) se retiró en la unidad D (2026-09-26)."""
        _, pt = crear_producto_con_talla(self.suc_a, articulo='LIM-1', sku=96001, stock=0)
        compra, _, _, rec = self.compra_con_recepcion(pt=pt)
        r = self.post_json('/app/limpiar_productos_compra/',
                           {'compra_id': compra.id, 'mode': 'delete', 'force': True})
        self.assertEqual(r.status_code, 404, r.content)
        self.assertTrue(Productos_Recepcionados.objects.filter(id=rec.id).exists())

    def _pendiente_en_cpt_compartida(self):
        _, pt = crear_producto_con_talla(self.suc_a, articulo='EDR-1', sku=96002, stock=0, talla='35')
        _, cp, cpt, creada = self.compra_con_recepcion(pt=pt, talla='35', comprado=2, recibido=1)
        pendiente = Productos_Recepcionados.objects.create(
            compra_producto_talla=cpt, stockArribado=1)
        return cp, cpt, creada, pendiente

    def test_cantidad_mayor_a_lo_pendiente_400(self):
        cp, _, _, pendiente = self._pendiente_en_cpt_compartida()
        r = self.post_json('/app/actualizar_recepciones_producto/', {
            'producto_id': cp.id, 'cambios': [{'recepcion_id': pendiente.id, 'cantidad': 999}]})
        self.assertEqual(r.status_code, 400, r.content)
        pendiente.refresh_from_db()
        self.assertEqual(pendiente.stockArribado, 1)

    def test_cantidad_negativa_400(self):
        cp, _, _, pendiente = self._pendiente_en_cpt_compartida()
        r = self.post_json('/app/actualizar_recepciones_producto/', {
            'producto_id': cp.id, 'cambios': [{'recepcion_id': pendiente.id, 'cantidad': -7}]})
        self.assertEqual(r.status_code, 400, r.content)

    def test_talla_de_cpt_compartida_no_se_renombra(self):
        cp, cpt, _, pendiente = self._pendiente_en_cpt_compartida()
        r = self.post_json('/app/actualizar_recepciones_producto/', {
            'producto_id': cp.id,
            'cambios': [{'recepcion_id': pendiente.id, 'cantidad': 1, 'talla': 'XXL'}]})
        self.assertEqual(r.status_code, 400, r.content)
        cpt.refresh_from_db()
        self.assertEqual(cpt.talla, '35')

    def test_talla_de_unica_recepcion_si_se_corrige(self):
        compra, cp, cpt, rec = self.compra_con_recepcion(talla='36', comprado=2, recibido=2)
        r = self.post_json('/app/actualizar_recepciones_producto/', {
            'producto_id': cp.id, 'cambios': [{'recepcion_id': rec.id, 'cantidad': 2, 'talla': '37'}]})
        self.assertEqual(r.status_code, 200, r.content)
        cpt.refresh_from_db()
        self.assertEqual(cpt.talla, '37')
