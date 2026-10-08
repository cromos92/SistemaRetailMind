"""
Tests de la facturación de pedidos de internet (ecommerce).

Foco: que la facturación replique una venta normal y que los Movimientos_Producto
queden bien etiquetados como EGRESO / VENTA_PUBLICO (regresión del bug donde el
FIFO de ecommerce los dejaba como tipo_movimiento='INGRESO').

Correr en BD local desechable:
    python manage.py test app.tests.test_ecommerce_facturacion
"""
import json

from django.test import RequestFactory, TestCase
from django.utils import timezone

from app.models import Movimientos_Producto, TicketDetallePago, Dte, PedidoEcommerce
from app.views import obtener_siguiente_correlativo
from app.views_ecommerce import _crear_ticket_desde_pedido, facturar_ecommerce_masivo
from app.views_modulo_documentos import construir_datos_txt_desde_dte, generar_txt_dte_acepta
from app.views_modulo_ventas import generar_dte_desde_ticket

from .factories import (
    setup_entorno_completo, crear_empresa, crear_sucursal, crear_vendedor,
    crear_producto_con_talla, crear_lote_fifo,
)


class FacturacionEcommerceTest(TestCase):
    """El flujo de facturación de internet debe rebajar stock y registrar
    movimientos EGRESO igual que una venta del POS."""

    def setUp(self):
        self.entorno = setup_entorno_completo()
        self.sucursal = self.entorno['sucursal']
        self.vendedor = self.entorno['vendedor']
        self.user = self.entorno['user']
        self.producto_talla = self.entorno['producto_talla']  # stock=10
        self.lote = self.entorno['lote']                      # cantidad=10

        self.pedido = PedidoEcommerce.objects.create(
            numero_ticket_rm='RM-TEST0001',
            numero_pedido_canal='SHOP-9001',
            canal_origen='SHOPIFY',
            sucursal=self.sucursal,
            cliente_nombre='Cliente Internet',
            subtotal=40000,
            total=40000,
            items=[{
                'sku': str(self.producto_talla.sku),
                'nombre': 'Zapatilla Test',
                'cantidad': 2,
                'precio_unitario': 20000,
            }],
        )

    def _facturar_ticket(self):
        correlativo = obtener_siguiente_correlativo(self.sucursal, 'TICKET')
        return _crear_ticket_desde_pedido(
            self.pedido, self.vendedor, correlativo,
            responsable=self.user.username, sucursal=self.sucursal,
        )

    def test_fifo_genera_movimiento_egreso(self):
        """Con lote FIFO disponible: el movimiento es EGRESO/VENTA_PUBLICO,
        el stock y el lote bajan en la cantidad vendida."""
        ticket = self._facturar_ticket()

        movs = Movimientos_Producto.objects.filter(
            ticket=ticket, ProductoTalla=self.producto_talla,
        )
        self.assertEqual(movs.count(), 1, 'Debe crearse exactamente un movimiento')
        mov = movs.first()
        self.assertEqual(mov.tipo_movimiento, 'EGRESO')
        self.assertEqual(mov.concepto, 'VENTA_PUBLICO')
        self.assertEqual(mov.cantidad, -2)

        self.producto_talla.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 8)

        self.lote.refresh_from_db(fields=['cantidad_disponible'])
        self.assertEqual(self.lote.cantidad_disponible, 8)

    def test_sin_lote_usa_fallback_egreso(self):
        """Sin lote activo (stock legacy): el fallback manual igual deja
        un movimiento EGRESO/VENTA_PUBLICO y rebaja el stock."""
        self.lote.activo = False
        self.lote.save(update_fields=['activo'])

        ticket = self._facturar_ticket()

        movs = Movimientos_Producto.objects.filter(
            ticket=ticket, ProductoTalla=self.producto_talla,
        )
        self.assertEqual(movs.count(), 1)
        mov = movs.first()
        self.assertEqual(mov.tipo_movimiento, 'EGRESO')
        self.assertEqual(mov.concepto, 'VENTA_PUBLICO')
        self.assertEqual(mov.cantidad, -2)

        self.producto_talla.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 8)

    def test_no_genera_movimientos_ingreso(self):
        """Ninguna venta de internet debe quedar marcada como INGRESO."""
        ticket = self._facturar_ticket()
        self.assertFalse(
            Movimientos_Producto.objects.filter(
                ticket=ticket, tipo_movimiento='INGRESO',
            ).exists(),
            'Las ventas de internet no deben generar movimientos INGRESO',
        )

    def test_facturacion_completa_genera_dte_y_vincula_movimiento(self):
        """End-to-end: tras generar el DTE se crea el documento y el movimiento
        de stock queda vinculado al DTE y sigue siendo EGRESO.

        (El formato del TXT Acepta está cubierto aparte en test_txt_dte.py.)"""
        ticket = self._facturar_ticket()
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='VENTA_INTERNET',
            monto=int(self.pedido.total), notas='Pago SHOPIFY',
        )
        ticket.estado = 'PAGADO'
        ticket.save(update_fields=['estado'])

        dte = generar_dte_desde_ticket(ticket, 'BOLETA_ELECTRONICA', self.user)

        self.assertIsInstance(dte, Dte)
        self.assertTrue(dte.numero_documento)

        mov = Movimientos_Producto.objects.filter(ticket=ticket).first()
        self.assertIsNotNone(mov)
        self.assertEqual(mov.dte_id, dte.id, 'El movimiento debe quedar ligado al DTE')
        self.assertEqual(mov.tipo_movimiento, 'EGRESO')


class DistribuirAjusteEcommerceTest(TestCase):
    """La diferencia entre el total del canal y la suma de ítems se reparte ENTRE
    las líneas de producto (sin línea 'AJUSTE' sin producto), manteniendo la suma
    EXACTA = total del pedido."""

    def setUp(self):
        self.empresa = crear_empresa(rut='78.503.140-7')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='PAO2')
        self.vendedor = crear_vendedor(empresa=self.empresa)
        # Dos productos con distinto precio RM para verificar la ponderación.
        self.prod1, self.pt1 = crear_producto_con_talla(
            self.sucursal, articulo='A', sku=1001, stock=10, precioventa=30000)
        self.prod2, self.pt2 = crear_producto_con_talla(
            self.sucursal, articulo='B', sku=1002, stock=10, precioventa=20000)
        crear_lote_fifo(self.pt1)
        crear_lote_fifo(self.pt2)

    def _pedido(self, items, total, costo_envio=0, num='1'):
        return PedidoEcommerce.objects.create(
            numero_ticket_rm=f'RM-AJ-{num}',
            numero_pedido_canal=f'PC-AJ-{num}',
            canal_origen='PARIS',
            sucursal=self.sucursal,
            rut_empresa='78503140-7',
            cliente_nombre='Cliente Test',
            total=total,
            costo_envio=costo_envio,
            items=items,
        )

    def _suma_lineas(self, ticket):
        return sum(int(tp.precio) * int(tp.stock) for tp in ticket.ticket_productos.all())

    def _descripciones(self, ticket):
        return [tp.descripcion_linea for tp in ticket.ticket_productos.all()]

    def test_paris_precio_cero_distribuye_sin_ajuste(self):
        """Paris manda total pero ítems con precio 0 → se distribuye, sin AJUSTE."""
        items = [
            {'sku': '1001', 'nombre': 'A', 'cantidad': 1, 'precio_unitario': 0},
            {'sku': '1002', 'nombre': 'B', 'cantidad': 1, 'precio_unitario': 0},
        ]
        ticket = _crear_ticket_desde_pedido(
            self._pedido(items, 50000, costo_envio=3000, num='1'),
            self.vendedor, 1, sucursal=self.sucursal)

        self.assertNotIn('AJUSTE', self._descripciones(ticket))
        self.assertIn('DESPACHO', self._descripciones(ticket))
        self.assertEqual(self._suma_lineas(ticket), 50000)
        montos_prod = sorted(
            int(tp.precio) * int(tp.stock)
            for tp in ticket.ticket_productos.all() if tp.ProductoTalla)
        self.assertEqual(sum(montos_prod), 47000)          # total − envío
        self.assertEqual(montos_prod, [18800, 28200])       # ponderado 30k:20k = 3:2

    def test_qty_mayor_uno_suma_exacta(self):
        """Con cantidades > 1 e indivisibilidad, la suma sigue siendo exacta."""
        items = [
            {'sku': '1001', 'nombre': 'A', 'cantidad': 3, 'precio_unitario': 0},
            {'sku': '1002', 'nombre': 'B', 'cantidad': 1, 'precio_unitario': 0},
        ]
        ticket = _crear_ticket_desde_pedido(
            self._pedido(items, 49999, num='2'), self.vendedor, 2, sucursal=self.sucursal)

        self.assertNotIn('AJUSTE', self._descripciones(ticket))
        self.assertEqual(self._suma_lineas(ticket), 49999)
        for tp in ticket.ticket_productos.all():
            self.assertGreaterEqual(int(tp.precio), 0)

    def test_diff_cero_no_distribuye(self):
        """Si los ítems ya suman el total, no hay AJUSTE ni cambios."""
        items = [
            {'sku': '1001', 'nombre': 'A', 'cantidad': 1, 'precio_unitario': 25000},
            {'sku': '1002', 'nombre': 'B', 'cantidad': 1, 'precio_unitario': 25000},
        ]
        ticket = _crear_ticket_desde_pedido(
            self._pedido(items, 50000, num='3'), self.vendedor, 3, sucursal=self.sucursal)

        self.assertNotIn('AJUSTE', self._descripciones(ticket))
        self.assertEqual(self._suma_lineas(ticket), 50000)

    def test_diff_negativo_recorta_al_total_del_canal(self):
        """Ítems > total (caso Walmart): se recortan las líneas al total del canal,
        sin líneas negativas ni AJUSTE. El total de la tabla es autoritativo."""
        items = [
            {'sku': '1001', 'nombre': 'A', 'cantidad': 1, 'precio_unitario': 40000},
        ]
        ticket = _crear_ticket_desde_pedido(
            self._pedido(items, 30000, num='4'), self.vendedor, 4, sucursal=self.sucursal)

        self.assertNotIn('AJUSTE', self._descripciones(ticket))
        self.assertEqual(self._suma_lineas(ticket), 30000)
        self.assertEqual(int(ticket.total), 30000)
        for tp in ticket.ticket_productos.all():
            self.assertGreaterEqual(int(tp.precio), 0)

    def test_walmart_varios_items_recorte_proporcional_suma_exacta(self):
        """Varios ítems de lista que exceden el total: recorte proporcional y
        suma EXACTA = total del canal (con qty > 1, sin negativos)."""
        items = [
            {'sku': '1001', 'nombre': 'A', 'cantidad': 2, 'precio_unitario': 30000},
            {'sku': '1002', 'nombre': 'B', 'cantidad': 1, 'precio_unitario': 20000},
        ]
        # Líneas suman 80000; total real del canal 37980 (caso del ticket WAL-...).
        ticket = _crear_ticket_desde_pedido(
            self._pedido(items, 37980, num='5'), self.vendedor, 5, sucursal=self.sucursal)

        self.assertNotIn('AJUSTE', self._descripciones(ticket))
        self.assertEqual(self._suma_lineas(ticket), 37980)
        self.assertEqual(int(ticket.total), 37980)
        for tp in ticket.ticket_productos.all():
            self.assertGreaterEqual(int(tp.precio), 0)
            self.assertEqual(int(tp.precio) * int(tp.stock), int(tp.subtotal))


class _BaseFacturacionConDte(TestCase):
    """Helper compartido: entorno completo + facturar un pedido hasta el DTE."""

    def setUp(self):
        self.entorno = setup_entorno_completo()
        self.sucursal = self.entorno['sucursal']
        self.vendedor = self.entorno['vendedor']
        self.user = self.entorno['user']
        self.producto_talla = self.entorno['producto_talla']  # stock=10

    def _pedido(self, num, cantidad=2, precio=20000):
        return PedidoEcommerce.objects.create(
            numero_ticket_rm=f'RM-TXT-{num}',
            numero_pedido_canal=f'SHOP-TXT-{num}',
            canal_origen='SHOPIFY',
            sucursal=self.sucursal,
            cliente_nombre='Cliente Internet',
            subtotal=cantidad * precio,
            total=cantidad * precio,
            items=[{
                'sku': str(self.producto_talla.sku),
                'nombre': 'Zapatilla Test',
                'cantidad': cantidad,
                'precio_unitario': precio,
            }],
        )

    def _facturar_con_dte(self, pedido):
        correlativo = obtener_siguiente_correlativo(self.sucursal, 'TICKET')
        ticket = _crear_ticket_desde_pedido(
            pedido, self.vendedor, correlativo,
            responsable=self.user.username, sucursal=self.sucursal,
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='VENTA_INTERNET',
            monto=int(pedido.total), notas=f'Pago {pedido.canal_origen}',
        )
        ticket.estado = 'PAGADO'
        ticket.save(update_fields=['estado'])
        return generar_dte_desde_ticket(ticket, 'BOLETA_ELECTRONICA', self.user)


class TxtEcommerceLlevaSkuTest(_BaseFacturacionConDte):
    """Regresión: el TXT canónico de una boleta ecommerce debe llevar el SKU
    del producto (antes agrupaba por variante y el SKU se perdía)."""

    def test_txt_canonico_incluye_sku_como_codigo(self):
        dte = self._facturar_con_dte(self._pedido('1'))
        datos = construir_datos_txt_desde_dte(dte)

        sku = str(self.producto_talla.sku)
        codigos = [str(i.get('codigo', '')) for i in datos['detalle']]
        self.assertIn(sku, codigos, 'el detalle del TXT debe llevar el SKU como código de ítem')

        txt = generar_txt_dte_acepta(datos)
        linea_sku = [l for l in txt.split('\n') if l.startswith(f'INT1|{sku}|')]
        self.assertEqual(len(linea_sku), 1, 'la línea de detalle de la boleta debe partir con el SKU')


class FacturacionMasivaEntregaTxtTest(_BaseFacturacionConDte):
    """La facturación masiva entrega, por cada boleta, su TXT Acepta listo
    para bajar como archivo .txt suelto: la carpeta que lee Acepta solo
    procesa .txt (el ZIP que se bajaba antes quedaba ahí sin subir)."""

    def setUp(self):
        super().setUp()
        from app.models import ModuloSistema, OpcionMenu, PermisoRol
        # get_or_create: la migración 0218 ya siembra el módulo 'ecommerce'.
        modulo, _ = ModuloSistema.objects.get_or_create(codigo='ecommerce', defaults={'nombre': 'Ecommerce'})
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='ecommerce_pedidos_todos', defaults={'modulo': modulo, 'nombre': 'Pedidos Ecommerce'},
        )
        PermisoRol.objects.create(rol=self.user.rol, opcion_menu=opcion, puede_ver=True, puede_crear=True)
        crear_vendedor(nombre='Venta Internet', empresa=self.sucursal.empresa, codigo_vendedor=1000)

    def _pedido_con_guia(self, num):
        pedido = self._pedido(num, cantidad=1)
        pedido.fecha_impresion_guia = timezone.now()
        pedido.save(update_fields=['fecha_impresion_guia'])
        return pedido

    def test_cada_boleta_trae_su_txt(self):
        pedidos = [self._pedido_con_guia('M1'), self._pedido_con_guia('M2')]
        request = RequestFactory().post(
            '/app/api/ecommerce/facturar-masivo/',
            data=json.dumps({'pedido_ids': [p.id for p in pedidos],
                             'tipo_documento': 'BOLETA_ELECTRONICA'}),
            content_type='application/json',
        )
        request.user = self.user
        request.session = {'idSucursalActual': self.sucursal.id}
        data = json.loads(facturar_ecommerce_masivo(request).content)

        self.assertEqual(data['exitosos'], 2, data)
        nombres = set()
        for r in data['resultados']:
            self.assertTrue(r['ok'], r)
            archivo = r['archivo_txt']
            self.assertIsNotNone(archivo, f'pedido {r["pedido_id"]} sin TXT')
            self.assertTrue(archivo['nombre_archivo'].endswith('.txt'), archivo['nombre_archivo'])
            # Mismo contenido que el generador canónico (el de /app/ventas/documentos/).
            dte = Dte.objects.get(id=r['dte_id'])
            self.assertEqual(archivo['contenido'], generar_txt_dte_acepta(construir_datos_txt_desde_dte(dte)))
            nombres.add(archivo['nombre_archivo'])
        self.assertEqual(len(nombres), 2, 'cada boleta debe bajar con su propio nombre de archivo')
