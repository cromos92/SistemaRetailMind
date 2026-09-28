"""
B2-01 (revisión adversarial): dos POST simultáneos de "Crear producto desde
recepción" sobre la misma línea de compra ingresaban dos veces las mismas
recepciones (kardex y lotes al doble; el stock plano, con actualización
perdida). La vista ahora bloquea la línea (Compras_Producto FOR UPDATE) antes
de leer sus recepciones pendientes: el segundo pedido espera el commit del
primero, las ve enlazadas y responde 409.

Es un TransactionTestCase (dos conexiones reales, en hilos). Usa
serialized_rollback para devolver la BD de test a su estado inicial.

OJO con --keepdb: TransactionTestCase recarga el contenido serializado al
EMPEZAR cada test, pero al TERMINAR hace flush (TRUNCATE de todo). El último
test dejaba la BD de test vacía (sin los OpcionMenu / PermisoRol que siembran
las migraciones) y la corrida siguiente con --keepdb serializaba esa BD vacía:
403 y errores masivos en test_rol_maestro_permisos y compañía. Por eso
_fixture_teardown vuelve a cargar el contenido serializado después del flush.

Ejecutar (BD de test aislada, NO producción):
    python manage.py test app.tests.test_v1b_concurrencia --keepdb --noinput
"""
import threading
import time
from unittest import mock

from django.db import connections
from django.db import connection
from django.test import Client, TransactionTestCase

from app import views as app_views
from app.models import (
    AtributoOpcion, Categoria, Compras, Compras_Producto, Compras_Producto_Talla,
    Dte, LoteProducto, Movimientos_Producto, Producto, Producto_Talla, Productos_Atributos,
    Productos_Recepcionados,
)

from .factories import crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario


class RestaurarBDTrasFlushMixin:
    """Deja la BD de test como la encontró: tras el flush del teardown de
    TransactionTestCase vuelve a cargar el contenido serializado al crear la
    BD de test (datos sembrados por migraciones). Sin esto, --keepdb dejaba
    la BD vacía para las corridas siguientes."""

    def _fixture_teardown(self):
        super()._fixture_teardown()
        if not self.serialized_rollback:
            return
        for db_name in self._databases_names(include_mirrors=False):
            conn = connections[db_name]
            contenido = getattr(conn, '_test_serialized_contents', None)
            if contenido:
                conn.creation.deserialize_db_from_string(contenido)


class CrearDesdeRecepcionConcurrenteTest(RestaurarBDTrasFlushMixin, TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        emp = crear_empresa(nombre='Empresa C', rut='76.500.500-5')
        self.suc = crear_sucursal(empresa=emp, alias='BOD-C')
        proveedor = crear_empresa(nombre='Proveedor C', rut='77.600.600-6', esProveedor=True)
        self.maestro = crear_usuario(username='v1b_conc_maestro', rol='maestro')
        crear_empresa_user(self.maestro, emp, self.suc)
        self.cat = Categoria.objects.create(nombre='Zapatillas C')
        am = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        self.marca = AtributoOpcion.objects.create(atributo=am, valor='NIKE')
        dte = Dte.objects.create(
            emisor=proveedor, receptor=emp, numero_documento=8801,
            tipo_documento='FACTURA ELECTRONICA', monto_neto=1000, monto_con_iva=1190,
            estado_pago='PENDIENTE', estado_dte='ACEPTADO', responsable='test',
            fecha_emision='2026-09-01', fecha_vencimiento='2026-09-30',
            diasCredito=30, bultos=1, unidades_productos=0, tipo_transaccion='COMPRA',
        )
        compra = Compras.objects.create(
            empresa=proveedor, nombre='OC C', correlativo=1, responsable='test', temporada='')
        self.cp = Compras_Producto.objects.create(
            compras=compra, nombre='CONC-1', atributo1='M', atributo2='C', atributo3='G',
            atributo4='', costo=1000, precioSugerido=2000, sucursal_destino=self.suc)
        cpt = Compras_Producto_Talla.objects.create(compra_producto=self.cp, stock=3, talla='40')
        self.rec = Productos_Recepcionados.objects.create(
            compra_producto_talla=cpt, stockArribado=3, dte=dte, sucursal_destino=self.suc)
        # Producto y talla YA existentes (reposición): así ningún pedido genera
        # SKU nuevo, que por su cuenta serializaba y tapaba la carrera.
        producto = Producto.objects.create(
            articulo='CONC-1', descripcion='x', sucursal=self.suc, costo=1000,
            sobreprecio=0, precioventa=2000, categoria=self.cat, atributo1=self.marca)
        self.pt = Producto_Talla.objects.create(producto=producto, sku=555001, stock=0, talla='40')

    def _cliente(self):
        cliente = Client()
        cliente.force_login(self.maestro)
        s = cliente.session
        s['idSucursalActual'] = self.suc.id
        s['idEmpresaActual'] = self.suc.empresa_id
        s['nombreUsuario'] = self.maestro.username
        s.save()
        return cliente

    def _form(self):
        return {
            'producto_compra_id': str(self.cp.id), 'articulo': 'CONC-1', 'descripcion': 'x',
            'atributo1': str(self.marca.id), 'categoria': str(self.cat.id),
            'costo': '1000', 'sobreprecio': '0', 'precioventa': '2000', 'tipo_talla': 'CL',
            'sucursales_destino_ids[]': [str(self.suc.id)], 'sku_40': '', 'stock_40': '3',
        }

    def test_dos_post_simultaneos_ingresan_una_sola_vez(self):
        clientes = [self._cliente(), self._cliente()]
        original = app_views.registrar_movimiento_producto
        dentro = threading.Event()

        def lento(*args, **kwargs):
            # El primer pedido se demora DENTRO de su transacción: el segundo
            # llega mientras tanto.
            if not dentro.is_set():
                dentro.set()
                time.sleep(1.5)
            return original(*args, **kwargs)

        respuestas = {}

        def pedir(nombre, cliente):
            try:
                respuestas[nombre] = cliente.post('/app/crear_producto_desde_recepcion/', self._form())
            finally:
                connection.close()

        with mock.patch.object(app_views, 'registrar_movimiento_producto', side_effect=lento):
            h1 = threading.Thread(target=pedir, args=('a', clientes[0]))
            h1.start()
            self.assertTrue(dentro.wait(20), 'el primer pedido no llegó a ingresar')
            h2 = threading.Thread(target=pedir, args=('b', clientes[1]))
            h2.start()
            h1.join(30)
            h2.join(30)

        codigos = sorted(r.status_code for r in respuestas.values())
        self.assertEqual(codigos, [200, 409], {k: v.content[:300] for k, v in respuestas.items()})
        pt = Producto_Talla.objects.get(id=self.pt.id)
        ingresos = sum(Movimientos_Producto.objects.filter(
            ProductoTalla=pt, concepto='INGRESO_INICIAL').values_list('cantidad', flat=True))
        lotes = sum(LoteProducto.objects.filter(producto_talla=pt).values_list('cantidad_inicial', flat=True))
        self.assertEqual((pt.stock, ingresos, lotes), (3, 3, 3))
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.producto_talla_id, pt.id)
