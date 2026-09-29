"""
Regresión de los caminos de stock que viven FUERA de views.py (auditoría
29-09-2026, key "caminos"): H6, H9, H10, H11, H12 y los fallbacks de
`inventario_service` (N4).

Regla que se verifica en todos: cada movimiento deja stock plano, lote FIFO y
kardex sincronizados, el kardex se atribuye a la sucursal dueña del SKU y un
stock insuficiente nunca se recorta a 0 en silencio (queda negativo VISIBLE
con marca en el ticket) ni se rechaza cuando la venta ya ocurrió.

Correr en sqlite en memoria:
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_caminos_stock
"""
import json
import uuid
from unittest import mock

from django.db.models import Sum
from django.test import Client, TestCase
from django.utils import timezone

from app.models import (
    Cotizacion_Empresa_Detalle_SKU, LoteProducto, Movimientos_Producto,
    PedidoEcommerce, Ticket, Traspaso,
)
from app.services import inventario_service

from .factories import (
    crear_empresa, crear_empresa_user, crear_lote_fifo,
    crear_producto_con_talla, crear_sucursal, crear_usuario,
    setup_entorno_completo,
)
from .test_cotizaciones_despacho import DespachoDiferidoBase


def _lotes_disponibles(pt):
    return (
        LoteProducto.objects.filter(
            producto_talla=pt, activo=True, agotado=False, cantidad_disponible__gt=0,
        ).aggregate(s=Sum('cantidad_disponible'))['s'] or 0
    )


def _suma_kardex(pt):
    return (
        Movimientos_Producto.objects.filter(ProductoTalla=pt, estado='COMPLETADO')
        .aggregate(s=Sum('cantidad'))['s'] or 0
    )


def _permiso_total():
    """Las URLs de estos flujos pasan por el middleware de permisos (por rol):
    se parcha `PermisoRol.tiene_permiso` como en test_ajuste_traspaso."""
    return mock.patch('app.models.PermisoRol.tiene_permiso', return_value=True)


# =====================================================================
# N4 — fallbacks y flags retrocompatibles de inventario_service
# =====================================================================

class InventarioServiceFallbacksTest(TestCase):

    def setUp(self):
        self.user = crear_usuario(username='bodeguero')
        self.sucursal = crear_sucursal()
        self.producto, self.pt = crear_producto_con_talla(
            self.sucursal, stock=10, costo=15000, sobreprecio=5000, precioventa=20000,
        )
        self.lote = crear_lote_fifo(self.pt, cantidad=10, costo_unitario=15000)

    def test_ingresar_sin_kwargs_rellena_sucursal_costo_y_username(self):
        mov = inventario_service.ingresar(self.pt, 2, 'AJUSTE_POSITIVO', self.user)

        self.assertEqual(mov.sucursal_destino_id, self.sucursal.id)
        self.assertEqual((mov.costo, mov.sobreprecio, mov.precio), (15000, 5000, 20000))
        # Usuario.__str__ es 'Nombre (username)': el kardex guarda el username.
        self.assertEqual(mov.responsable, 'bodeguero')
        self.assertEqual(mov.tipo_movimiento, 'INGRESO')

        lote = LoteProducto.objects.get(movimiento=mov)
        self.assertEqual((lote.cantidad_disponible, lote.costo_unitario), (2, 15000))
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 12)
        self.assertEqual(_lotes_disponibles(self.pt), 12)

    def test_ingresar_costo_explicito_se_respeta(self):
        mov = inventario_service.ingresar(
            self.pt, 1, 'AJUSTE_POSITIVO', 'x', costo_unitario=999, precio_unitario=1234,
        )
        self.assertEqual((mov.costo, mov.precio), (999, 1234))
        self.assertEqual(LoteProducto.objects.get(movimiento=mov).costo_unitario, 999)

    def test_ingresar_crear_lote_false_no_crea_lote(self):
        antes = LoteProducto.objects.filter(producto_talla=self.pt).count()
        mov = inventario_service.ingresar(
            self.pt, 3, 'AJUSTE_POSITIVO', 'x', crear_lote=False,
        )
        self.assertEqual(LoteProducto.objects.filter(producto_talla=self.pt).count(), antes)
        self.assertFalse(LoteProducto.objects.filter(movimiento=mov).exists())
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 13)

    def test_ingresar_numero_lote_y_vencimiento_van_al_lote(self):
        mov = inventario_service.ingresar(
            self.pt, 1, 'AJUSTE_POSITIVO', 'x',
            numero_lote='LOTE-ABC', fecha_vencimiento='2030-01-31',
        )
        lote = LoteProducto.objects.get(movimiento=mov)
        self.assertEqual(lote.numero_lote, 'LOTE-ABC')
        self.assertEqual(str(lote.fecha_vencimiento), '2030-01-31')

    def test_egresar_sin_kwargs_rellena_sucursal_origen_y_precio(self):
        mov = inventario_service.egresar(self.pt, 3, 'AJUSTE_NEGATIVO', 'x')

        self.assertEqual(mov.sucursal_origen_id, self.sucursal.id)
        self.assertEqual((mov.cantidad, mov.costo, mov.precio), (-3, 15000, 20000))
        self.assertEqual(mov.tipo_movimiento, 'EGRESO')
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 7)
        self.assertEqual(_lotes_disponibles(self.pt), 7)

    def test_egresar_consumir_lotes_false_no_toca_lotes(self):
        inventario_service.egresar(self.pt, 4, 'AJUSTE_NEGATIVO', 'x', consumir_lotes=False)
        self.lote.refresh_from_db()
        self.assertEqual(self.lote.cantidad_disponible, 10)
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 6)

    def test_egresar_devolver_lotes_entrega_detalle_para_costeo(self):
        resultado = inventario_service.egresar(
            self.pt, 4, 'AJUSTE_NEGATIVO', 'x', devolver_lotes=True,
        )
        self.assertIsInstance(resultado, tuple)
        mov, lotes = resultado
        self.assertEqual(mov.cantidad, -4)
        self.assertEqual(len(lotes), 1)
        self.assertEqual(lotes[0]['lote_id'], self.lote.id)
        self.assertEqual(lotes[0]['cantidad_consumida'], 4)
        self.assertEqual(lotes[0]['costo_total'], 4 * 15000)

    def test_egresar_por_defecto_sigue_devolviendo_solo_el_movimiento(self):
        mov = inventario_service.egresar(self.pt, 1, 'AJUSTE_NEGATIVO', 'x')
        self.assertIsInstance(mov, Movimientos_Producto)

    def test_egresar_stock_insuficiente_rechaza_sin_mover(self):
        with self.assertRaises(ValueError):
            inventario_service.egresar(self.pt, 11, 'AJUSTE_NEGATIVO', 'x')
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 10)
        self.assertEqual(_lotes_disponibles(self.pt), 10)
        self.assertEqual(Movimientos_Producto.objects.filter(ProductoTalla=self.pt).count(), 0)

    def test_egresar_permitido_deja_negativo_visible_y_kardex_exacto(self):
        """Nunca se recorta a 0: stock -3, lotes 0 y kardex -13 (lo que bajó)."""
        mov = inventario_service.egresar(
            self.pt, 13, 'VENTA_PUBLICO', 'x', permitir_stock_insuficiente=True,
        )
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, -3)
        self.assertEqual(_lotes_disponibles(self.pt), 0)
        self.assertEqual(mov.cantidad, -13)
        self.assertEqual(_suma_kardex(self.pt), -13)


# =====================================================================
# H6 — revertir_sku_despachado (cotización): lote + guard de sucursal
# =====================================================================

class RevertirSkuDespachadoTest(DespachoDiferidoBase):

    def _como_admin(self):
        admin = crear_usuario(username='admin_rev', rol='administrador')
        crear_empresa_user(admin, self.empresa, self.sucursal)
        self._dar_permiso_cotizaciones(rol='administrador')
        self.client.force_login(admin)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()
        return admin

    def _revertir(self, sku_id=None):
        body = {'detalle_id': self.detalle.id, 'motivo': 'SKU equivocado'}
        if sku_id:
            body['sku_id'] = sku_id
        return self.client.post(
            '/app/api/cotizaciones/revertir-sku-despachado/',
            data=json.dumps(body), content_type='application/json',
        )

    def test_reversa_deja_stock_lote_y_kardex_iguales_con_sucursal(self):
        """Despacho 5 + reversa 5: stock vuelve a 10 y la capa FIFO también
        (antes el lote no se creaba: lotes 5 con stock 10 para siempre)."""
        self._asignar(self.producto_talla, 5)
        self.producto_talla.refresh_from_db()
        self.assertEqual(self.producto_talla.stock, 5)
        self.assertEqual(_lotes_disponibles(self.producto_talla), 5)

        self._como_admin()
        resp = self._revertir()
        self.assertTrue(resp.json()['success'], resp.json())

        self.producto_talla.refresh_from_db()
        self.assertEqual(self.producto_talla.stock, 10)
        self.assertEqual(_lotes_disponibles(self.producto_talla), 10)
        self.assertEqual(_suma_kardex(self.producto_talla), 0)

        reversa = Movimientos_Producto.objects.get(
            ProductoTalla=self.producto_talla, concepto='DESPACHO_COTIZACION', cantidad__gt=0,
        )
        self.assertEqual(reversa.cantidad, 5)
        self.assertEqual(reversa.tipo_movimiento, 'INGRESO')
        self.assertEqual(reversa.sucursal_destino_id, self.sucursal.id)
        # Origen también = sucursal de la cotización: el resumen de existencias
        # corta 'ventas' por sucursal_origen y así netea contra el despacho.
        self.assertEqual(reversa.sucursal_origen_id, self.sucursal.id)
        self.assertEqual(reversa.dte_id, self.dte.id)
        self.assertEqual(reversa.referencia_externa, self.cotizacion.numero_cotizacion)
        self.assertEqual(reversa.costo, 15000)

        lote = LoteProducto.objects.get(movimiento=reversa)
        self.assertEqual((lote.cantidad_inicial, lote.costo_unitario), (5, 15000))

    def test_reversa_rechaza_sku_de_otra_sucursal_sin_mover_nada(self):
        otra = crear_sucursal(empresa=self.empresa, alias='OTRA')
        _, pt_ajeno = crear_producto_con_talla(otra, sku=1000009, stock=3)
        fila = Cotizacion_Empresa_Detalle_SKU.objects.create(
            detalle=self.detalle, producto_talla=pt_ajeno, cantidad=1,
            costo_unitario=15000, precio_unitario=20000, asignado_post_factura=True,
        )
        self._como_admin()
        resp = self._revertir(sku_id=fila.id)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('otra sucursal', resp.json()['error'])

        pt_ajeno.refresh_from_db()
        self.assertEqual(pt_ajeno.stock, 3)
        self.assertFalse(Movimientos_Producto.objects.filter(ProductoTalla=pt_ajeno).exists())
        self.assertTrue(Cotizacion_Empresa_Detalle_SKU.objects.filter(pk=fila.pk).exists())


# =====================================================================
# H12 — ajustar_stock (edición de producto): sucursal dueña + lote enlazado
# =====================================================================

class AjustarStockVariacionTest(TestCase):

    def setUp(self):
        self.user = crear_usuario(username='editor', rol='administrador')
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='DUENA')
        self.otra = crear_sucursal(empresa=self.empresa, alias='OTRA')
        crear_empresa_user(self.user, self.empresa, self.sucursal)
        self.producto, self.pt = crear_producto_con_talla(self.sucursal, stock=10)
        self.lote = crear_lote_fifo(self.pt, cantidad=10)
        self.client = Client()
        self.client.force_login(self.user)
        self._patch = _permiso_total()
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _sesion(self, sucursal_id):
        session = self.client.session
        if sucursal_id is None:
            session.pop('idSucursalActual', None)
        else:
            session['idSucursalActual'] = sucursal_id
        session.save()

    def _ajustar(self, tipo, cantidad, **extra):
        body = {
            'tipo_ajuste': tipo, 'cantidad': cantidad,
            'motivo': 'Prueba de regresión H12 ajuste',
        }
        body.update(extra)
        return self.client.post(
            f'/app/productos/variacion/ajustar-stock/{self.pt.id}/',
            data=json.dumps(body), content_type='application/json',
        )

    def _entrada(self, cantidad=3):
        return self._ajustar(
            'ENTRADA', cantidad, costo_unitario=1000, sobreprecio_unitario=200,
            precio_venta_unitario=2000, numero_lote='LOTE-H12',
        )

    def test_entrada_desde_sesion_en_otra_sucursal_se_rechaza(self):
        self._sesion(self.otra.id)
        resp = self._entrada()
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('DUENA', resp.json()['error'])
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 10)
        self.assertEqual(_lotes_disponibles(self.pt), 10)
        self.assertFalse(Movimientos_Producto.objects.filter(ProductoTalla=self.pt).exists())

    def test_entrada_misma_sucursal_lote_enlazado_y_kardex_de_la_duena(self):
        self._sesion(self.sucursal.id)
        resp = self._entrada(3)
        data = resp.json()
        self.assertTrue(data['success'], data)

        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 13)
        self.assertEqual(data['nuevo_stock'], 13)
        self.assertEqual(_lotes_disponibles(self.pt), 13)

        mov = Movimientos_Producto.objects.get(id=data['movimiento_id'])
        self.assertEqual(mov.concepto, 'AJUSTE_POSITIVO')
        self.assertEqual(mov.cantidad, 3)
        self.assertEqual(mov.sucursal_origen_id, self.sucursal.id)
        self.assertEqual(mov.sucursal_destino_id, self.sucursal.id)
        self.assertEqual((mov.costo, mov.sobreprecio, mov.precio), (1000, 200, 2000))

        lote = LoteProducto.objects.get(id=data['lote_id'])
        self.assertEqual(lote.movimiento_id, mov.id)          # antes: movimiento=None
        self.assertEqual(lote.numero_lote, 'LOTE-H12')
        self.assertEqual(data['numero_lote'], 'LOTE-H12')
        self.assertEqual((lote.cantidad_disponible, lote.costo_unitario), (3, 1000))

    def test_entrada_sin_sucursal_en_sesion_usa_la_del_producto(self):
        self._sesion(None)
        resp = self._entrada(1)
        self.assertTrue(resp.json()['success'], resp.content)
        mov = Movimientos_Producto.objects.get(ProductoTalla=self.pt)
        self.assertEqual(mov.sucursal_destino_id, self.sucursal.id)

    def test_salida_desde_sesion_en_otra_sucursal_se_rechaza(self):
        self._sesion(self.otra.id)
        resp = self._ajustar('SALIDA', 2)
        self.assertEqual(resp.status_code, 400)
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 10)
        self.assertEqual(_lotes_disponibles(self.pt), 10)

    def test_salida_consume_lotes_y_devuelve_el_movimiento_real(self):
        self._sesion(self.sucursal.id)
        resp = self._ajustar('SALIDA', 4)
        data = resp.json()
        self.assertTrue(data['success'], data)

        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 6)
        self.assertEqual(data['nuevo_stock'], 6)
        self.assertEqual(_lotes_disponibles(self.pt), 6)
        self.assertEqual(data['lotes_consumidos'], 1)

        mov = Movimientos_Producto.objects.get(id=data['movimiento_id'])
        self.assertEqual((mov.concepto, mov.cantidad, mov.tipo_movimiento),
                         ('AJUSTE_NEGATIVO', -4, 'EGRESO'))
        self.assertEqual(mov.sucursal_origen_id, self.sucursal.id)
        self.assertEqual(mov.ProductoTalla_id, self.pt.id)

    def test_salida_mayor_que_stock_se_rechaza_sin_mover(self):
        self._sesion(self.sucursal.id)
        resp = self._ajustar('SALIDA', 11)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('insuficiente', resp.json()['error'].lower())
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 10)
        self.assertEqual(_lotes_disponibles(self.pt), 10)


# =====================================================================
# H10 — api/sync (POS offline): kardex ligado al ticket, negativo visible
# =====================================================================

class SyncTicketsStockTest(TestCase):

    def setUp(self):
        self.entorno = setup_entorno_completo()
        self.sucursal = self.entorno['sucursal']
        self.vendedor = self.entorno['vendedor']
        self.user = self.entorno['user']
        self.pt = self.entorno['producto_talla']          # stock 10
        self.lote = self.entorno['lote']                  # 10 u
        # Dejar el SKU con 3 unidades reales en las tres capas.
        inventario_service.egresar(self.pt, 7, 'AJUSTE_NEGATIVO', 'setup')
        self.pt.refresh_from_db()
        self.assertEqual((self.pt.stock, _lotes_disponibles(self.pt)), (3, 3))

    def _ticket_data(self, cantidad, local_id=None):
        return {
            'local_id': local_id or uuid.uuid4(),
            'vendedor': self.vendedor,
            'created_at': timezone.now(),
            'subtotal': 20000 * cantidad,
            'total': 20000 * cantidad,
            'descuento_total': 0,
            'items': [{
                'producto_talla': self.pt,
                'cantidad': cantidad,
                'precio_unitario': 20000,
                'subtotal': 20000 * cantidad,
            }],
            'pagos': [{'tipo': 'EFECTIVO', 'monto': 20000 * cantidad}],
        }

    def _servicio(self):
        from app.api.sync.services import TicketSyncService
        return TicketSyncService(self.sucursal, usuario=self.user)

    def test_kardex_nace_ligado_al_ticket_con_referencia(self):
        resultado = self._servicio().procesar_tickets([self._ticket_data(2)])
        self.assertTrue(resultado['success'], resultado)
        ticket = Ticket.objects.get(id=resultado['results'][0]['server_id'])

        movs = Movimientos_Producto.objects.filter(ProductoTalla=self.pt, ticket=ticket)
        self.assertEqual(movs.count(), 1)
        mov = movs.first()
        self.assertEqual(mov.cantidad, -2)
        self.assertEqual(mov.concepto, 'VENTA_PUBLICO')
        self.assertEqual(mov.referencia_externa, f'TICKET_{ticket.correlativo}')
        self.assertEqual(mov.sucursal_origen_id, self.sucursal.id)
        self.assertEqual(mov.precio, 20000)
        self.assertEqual(mov.responsable, self.user.username)

        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 1)
        self.assertEqual(_lotes_disponibles(self.pt), 1)
        self.assertFalse(ticket.requiere_revision)

    def test_stock_insuficiente_no_rechaza_pero_queda_negativo_visible(self):
        """La venta ya ocurrió offline: se registra igual, el stock queda en
        -2 (no se recorta a 0), el kardex baja 5 y el ticket queda marcado."""
        resultado = self._servicio().procesar_tickets([self._ticket_data(5)])
        self.assertTrue(resultado['success'], resultado)
        r = resultado['results'][0]
        self.assertTrue(any('Stock' in w for w in r['warnings']), r)

        ticket = Ticket.objects.get(id=r['server_id'])
        self.assertTrue(ticket.requiere_revision)
        self.assertIn('Stock', ticket.notas_sync)

        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, -2)
        self.assertEqual(_lotes_disponibles(self.pt), 0)
        mov = Movimientos_Producto.objects.get(ProductoTalla=self.pt, ticket=ticket)
        self.assertEqual(mov.cantidad, -5)

    def test_reenvio_mismo_local_id_no_descuenta_dos_veces(self):
        local_id = uuid.uuid4()
        servicio = self._servicio()
        servicio.procesar_tickets([self._ticket_data(1, local_id=local_id)])
        servicio.procesar_tickets([self._ticket_data(1, local_id=local_id)])

        self.assertEqual(Ticket.objects.filter(local_id=local_id).count(), 1)
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 2)
        self.assertEqual(_lotes_disponibles(self.pt), 2)
        self.assertEqual(
            Movimientos_Producto.objects.filter(ProductoTalla=self.pt, ticket__local_id=local_id).count(), 1,
        )


# =====================================================================
# H11 — ecommerce: kardex = lo que bajó el stock, negativo visible
# =====================================================================

class EcommerceStockNegativoVisibleTest(TestCase):

    def setUp(self):
        self.entorno = setup_entorno_completo()
        self.sucursal = self.entorno['sucursal']
        self.vendedor = self.entorno['vendedor']
        self.user = self.entorno['user']
        self.pt = self.entorno['producto_talla']          # stock 10, costo 15000
        self.lote = self.entorno['lote']
        inventario_service.egresar(self.pt, 7, 'AJUSTE_NEGATIVO', 'setup')
        self.pt.refresh_from_db()
        self.assertEqual((self.pt.stock, _lotes_disponibles(self.pt)), (3, 3))

    def _pedido(self, cantidad):
        return PedidoEcommerce.objects.create(
            numero_ticket_rm='RM-H11',
            numero_pedido_canal='SHOP-H11',
            canal_origen='SHOPIFY',
            sucursal=self.sucursal,
            cliente_nombre='Cliente Internet',
            subtotal=20000 * cantidad,
            total=20000 * cantidad,
            items=[{
                'sku': str(self.pt.sku), 'nombre': 'Zapatilla Test',
                'cantidad': cantidad, 'precio_unitario': 20000,
            }],
        )

    def _facturar(self, pedido):
        from app.views import obtener_siguiente_correlativo
        from app.views_ecommerce import _crear_ticket_desde_pedido
        return _crear_ticket_desde_pedido(
            pedido, self.vendedor, obtener_siguiente_correlativo(self.sucursal, 'TICKET'),
            responsable=self.user.username, sucursal=self.sucursal,
        )

    def test_pedido_mayor_que_stock_deja_negativo_visible_y_kardex_exacto(self):
        """Stock 3, pedido 4: antes stock=0 y kardex -4 (SUM = -1 para siempre).
        Ahora stock -1, lotes 0, kardex -4 y el ticket marcado para revisión."""
        ticket = self._facturar(self._pedido(4))

        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, -1)
        self.assertEqual(_lotes_disponibles(self.pt), 0)

        movs = Movimientos_Producto.objects.filter(ProductoTalla=self.pt, ticket=ticket)
        self.assertEqual(movs.count(), 1)
        mov = movs.first()
        self.assertEqual((mov.cantidad, mov.concepto, mov.tipo_movimiento),
                         (-4, 'VENTA_PUBLICO', 'EGRESO'))
        self.assertEqual(mov.sucursal_origen_id, self.sucursal.id)
        self.assertEqual(mov.referencia_externa, f'TICKET_{ticket.correlativo}')
        # SUM(kardex) sigue explicando el stock: 10 - 7 - 4 = -1
        self.assertEqual(_suma_kardex(self.pt), self.pt.stock - 10)

        ticket.refresh_from_db()
        self.assertTrue(ticket.requiere_revision)
        self.assertIn(str(self.pt.sku), ticket.notas_sync)

        # Costeo de la línea: 3 u del lote (15000) + 1 u al costo de ficha (15000).
        linea = ticket.ticket_productos.get(ProductoTalla=self.pt)
        self.assertEqual(linea.costo_fifo, 15000)
        lotes = json.loads(linea.lotes_utilizados)
        self.assertEqual(lotes[0]['lote_id'], self.lote.id)
        self.assertEqual(lotes[0]['cantidad_consumida'], 3)

    def test_pedido_con_stock_no_marca_revision(self):
        ticket = self._facturar(self._pedido(2))
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 1)
        self.assertEqual(_lotes_disponibles(self.pt), 1)
        ticket.refresh_from_db()
        self.assertFalse(ticket.requiere_revision)
        linea = ticket.ticket_productos.get(ProductoTalla=self.pt)
        self.assertEqual(linea.costo_fifo, 15000)

    def test_sin_lote_activo_kardex_igual_a_lo_que_bajo_el_stock(self):
        """Caso 'FIFO no disponible' (stock legacy sin lotes): no hay fallback
        aparte, el mismo escritor baja stock y kardex por igual."""
        self.lote.activo = False
        self.lote.save(update_fields=['activo'])
        ticket = self._facturar(self._pedido(2))

        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 1)
        mov = Movimientos_Producto.objects.get(ProductoTalla=self.pt, ticket=ticket)
        self.assertEqual(mov.cantidad, -2)
        self.assertNotIn('FIFO no disponible', mov.observaciones or '')
        linea = ticket.ticket_productos.get(ProductoTalla=self.pt)
        self.assertEqual(linea.costo_fifo, 15000)     # costo de ficha
        ticket.refresh_from_db()
        self.assertFalse(ticket.requiere_revision)


# =====================================================================
# H9 — despacho masivo por modelo Traspaso: bloqueado (sin recepción)
# =====================================================================

class DespachoMasivoBloqueadoTest(TestCase):

    def setUp(self):
        self.user = crear_usuario(username='cd', rol='administrador')
        self.empresa = crear_empresa()
        self.origen = crear_sucursal(empresa=self.empresa, alias='CD')
        self.destino = crear_sucursal(empresa=self.empresa, alias='TIENDA')
        crear_empresa_user(self.user, self.empresa, self.origen)
        self.producto, self.pt = crear_producto_con_talla(self.origen, stock=5)
        self.lote = crear_lote_fifo(self.pt, cantidad=5)
        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.origen.id
        session.save()
        self._patch = _permiso_total()
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_bloqueado_remite_a_emision_dte_y_no_mueve_stock(self):
        resp = self.client.post(
            '/app/api/despacho/crear-masivo/',
            data=json.dumps({'despachos': [{
                'sucursal_destino_id': self.destino.id,
                'items': [{'producto_talla_id': self.pt.id, 'cantidad': 2}],
            }], 'observaciones': 'prueba'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 409, resp.content)
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertTrue(data.get('bloqueado'))
        self.assertIn('Emisión DTE', data['error'])

        self.assertEqual(Traspaso.objects.count(), 0)
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, 5)
        self.assertEqual(_lotes_disponibles(self.pt), 5)
        self.assertFalse(Movimientos_Producto.objects.filter(ProductoTalla=self.pt).exists())

    def test_sin_despachos_sigue_siendo_400(self):
        resp = self.client.post(
            '/app/api/despacho/crear-masivo/',
            data=json.dumps({'despachos': []}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
