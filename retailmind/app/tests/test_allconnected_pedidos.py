from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings

from app.models import PedidoEcommerce
from app.services import allconnected_pedidos_service
from app.views_ecommerce import _crear_ticket_desde_pedido, _ingestar_pedido_dict

from .factories import crear_empresa, crear_sucursal, crear_vendedor


class AllConnectedPedidosIngestaTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa(rut='78.503.140-7')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='WEB')
        self.vendedor = crear_vendedor(empresa=self.empresa)

    def payload(self, **overrides):
        data = {
            'numero_pedido_canal': 'AC-1001',
            'canal_origen': 'SHOPIFY',
            'sucursal_id': self.sucursal.id,
            'rut_empresa': '78503140-7',
            'cliente_nombre': 'Cliente Web',
            'cliente_email': 'cliente@example.com',
            'cliente_documento': '11111111-1',
            'subtotal': 10000,
            'descuento': 0,
            'costo_envio': 0,
            'total': 10000,
            'items': [
                {'sku': 'SKU-EXT-1', 'nombre': 'Producto externo', 'cantidad': 1, 'precio_unitario': 10000},
            ],
            'direccion_envio': 'Calle Web 123',
        }
        data.update(overrides)
        return data

    def test_ingesta_payload_antiguo_sin_impuestos_deja_cero(self):
        resultado = _ingestar_pedido_dict(self.payload())

        self.assertTrue(resultado['ok'])
        pedido = PedidoEcommerce.objects.get(id=resultado['pedido_ecommerce_id'])
        self.assertEqual(pedido.impuestos, Decimal('0.00'))
        self.assertEqual(pedido.total, Decimal('10000.00'))

    def test_ingesta_payload_con_impuestos_persiste_trazabilidad(self):
        resultado = _ingestar_pedido_dict(self.payload(impuestos=1900, total=11900))

        self.assertTrue(resultado['ok'])
        pedido = PedidoEcommerce.objects.get(id=resultado['pedido_ecommerce_id'])
        self.assertEqual(pedido.impuestos, Decimal('1900.00'))
        self.assertEqual(pedido.total, Decimal('11900.00'))

    def test_reintento_mismo_canal_y_numero_no_duplica(self):
        primero = _ingestar_pedido_dict(self.payload(correlativo='RE30000001', correlativo_numero=30000001))
        segundo = _ingestar_pedido_dict(self.payload(correlativo='RE30000002', correlativo_numero=30000002))

        self.assertTrue(primero['ok'])
        self.assertTrue(segundo['ok'])
        self.assertTrue(segundo['ya_existia'])
        self.assertEqual(PedidoEcommerce.objects.count(), 1)
        pedido = PedidoEcommerce.objects.get()
        self.assertEqual(pedido.correlativo, 'RE30000002')
        self.assertEqual(segundo['pedido_ecommerce_id'], primero['pedido_ecommerce_id'])

    def test_paris_subtotal_cero_crea_ticket_con_total_autoritativo(self):
        resultado = _ingestar_pedido_dict(self.payload(
            numero_pedido_canal='PAR-1001',
            canal_origen='PARIS',
            subtotal=0,
            descuento=0,
            impuestos=0,
            costo_envio=0,
            total=49990,
            items=[
                {'sku': 'PARIS-EXT', 'nombre': 'Producto Paris', 'cantidad': 1, 'precio_unitario': 0},
            ],
        ))
        pedido = PedidoEcommerce.objects.get(id=resultado['pedido_ecommerce_id'])

        ticket = _crear_ticket_desde_pedido(pedido, self.vendedor, correlativo=1, sucursal=self.sucursal)

        self.assertEqual(int(ticket.total), 49990)
        self.assertEqual(sum(int(tp.subtotal) for tp in ticket.ticket_productos.all()), 49990)

    def test_walmart_no_duplica_iva_desde_impuestos(self):
        resultado = _ingestar_pedido_dict(self.payload(
            numero_pedido_canal='WMT-1001',
            canal_origen='WALMART',
            subtotal=10000,
            descuento=0,
            impuestos=1900,
            costo_envio=0,
            total=11900,
            items=[
                {'sku': 'WMT-EXT', 'nombre': 'Producto Walmart', 'cantidad': 1, 'precio_unitario': 11900},
            ],
        ))
        pedido = PedidoEcommerce.objects.get(id=resultado['pedido_ecommerce_id'])

        ticket = _crear_ticket_desde_pedido(pedido, self.vendedor, correlativo=2, sucursal=self.sucursal)

        self.assertEqual(int(ticket.total), 11900)
        self.assertNotEqual(int(ticket.total), 13800)


class AllConnectedPedidosPullTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa(rut='78.503.140-7')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='WEB')

    def remote_payload(self):
        return {
            'ok': True,
            'desde': '2026-06-01',
            'hasta': '2026-06-02',
            'pedidos': [{
                'numero_pedido_canal': 'PULL-1001',
                'canal_origen': 'SHOPIFY',
                'sucursal_id': self.sucursal.id,
                'rut_empresa': '78503140-7',
                'cliente_nombre': 'Cliente Pull',
                'subtotal': 10000,
                'descuento': 0,
                'impuestos': 1900,
                'costo_envio': 0,
                'total': 11900,
                'items': [
                    {'sku': 'PULL-EXT', 'nombre': 'Producto Pull', 'cantidad': 1, 'precio_unitario': 11900},
                ],
            }],
            'total': 1,
            'omitidos': 0,
        }

    @override_settings(
        ALLCONNECTED_API_BASE_URL='https://ecommerce.webappsolutions.cl',
        ALLCONNECTED_API_KEY='secret',
        ALLCONNECTED_API_HEADER_NAME='X-AllConnected-Key',
        ALLCONNECTED_PEDIDOS_PATH='/app/pedidos/pendientes/',
    )
    @mock.patch('app.services.allconnected_pedidos_service.requests.get')
    def test_pull_usa_contrato_actual_e_ingesta_impuestos(self, requests_get):
        response = mock.Mock()
        response.status_code = 200
        response.json.return_value = self.remote_payload()
        requests_get.return_value = response

        resultado = allconnected_pedidos_service.traer_pedidos_pendientes(
            rut_empresa='78503140-7',
            desde='2026-06-01',
            hasta='2026-06-02',
        )

        self.assertTrue(resultado['ok'])
        self.assertEqual(resultado['nuevos'], 1)
        pedido = PedidoEcommerce.objects.get(numero_pedido_canal='PULL-1001')
        self.assertEqual(pedido.impuestos, Decimal('1900.00'))

        _, kwargs = requests_get.call_args
        self.assertEqual(kwargs['headers']['X-AllConnected-Key'], 'secret')
        self.assertEqual(kwargs['params'], {
            'rut_empresa': '78503140-7',
            'desde': '2026-06-01',
            'hasta': '2026-06-02',
        })
        self.assertNotIn('estado', kwargs['params'])
