from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import PedidoEcommerce, Ticket
from .factories import (
    crear_empresa,
    crear_empresa_user,
    crear_sucursal,
    crear_usuario,
    crear_vendedor,
)


STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class ReporteVentasInternetTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa(nombre='Empresa Web', rut='76.111.222-3')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='WEB-1')
        self.vendedor = crear_vendedor(
            nombre='Vendedor Internet', empresa=self.empresa, codigo_vendedor='1000'
        )
        self.vendedor.sucursales.add(self.sucursal)
        self.usuario = crear_usuario(username='reportes', rol='administrador')
        self.client = Client()
        self.client.login(username='reportes', password='TestPass123!')
        self.url = reverse('obtener_reporte_ventas_internet')

    def _ticket(self, correlativo, total=50000, sucursal=None, vendedor=None):
        return Ticket.objects.create(
            vendedor=vendedor or self.vendedor,
            sucursal=sucursal or self.sucursal,
            correlativo=correlativo,
            estado='PAGADO',
            subTotal=total,
            descuento=0,
            total=total,
            responsable='Ecommerce',
            metodo_pago='VENTA_INTERNET',
            modulo_origen='ECOMMERCE',
        )

    def _pedido(self, numero, estado='FACTURADO', total=50000, items=None,
                sucursal=None, ticket=None, fecha=None):
        fecha = fecha or timezone.now()
        return PedidoEcommerce.objects.create(
            numero_ticket_rm=f'RM-{numero}',
            numero_pedido_canal=f'CANAL-{numero}',
            canal_origen='SHOPIFY',
            sucursal=sucursal or self.sucursal,
            rut_empresa=(sucursal or self.sucursal).empresa.rut,
            cliente_nombre='Cliente Web',
            cliente_documento='11111111-1',
            subtotal=total,
            total=total,
            items=items or [
                {'sku': 'SKU-1', 'nombre': 'Zapatilla Web', 'cantidad': 2, 'precio_unitario': total / 2}
            ],
            estado=estado,
            sub_estado='FACTURADO_OK' if estado == 'FACTURADO' else 'CANCELADO_CLIENTE',
            ticket=ticket,
            fecha_facturacion=fecha if estado == 'FACTURADO' else None,
            facturado_por=self.usuario,
        )

    def _get(self, **params):
        hoy = timezone.localdate()
        params.setdefault('fecha_inicio', (hoy - timedelta(days=5)).isoformat())
        params.setdefault('fecha_fin', hoy.isoformat())
        return self.client.get(self.url, params)

    def test_resume_ventas_productos_empresa_y_vendedor(self):
        ticket = self._ticket(1, total=50000)
        self._pedido('1', total=50000, ticket=ticket)

        response = self._get()
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()

        self.assertTrue(data['success'])
        self.assertEqual(data['resumen']['ventas'], 50000.0)
        self.assertEqual(data['resumen']['pedidos'], 1)
        self.assertEqual(data['resumen']['unidades'], 2)
        self.assertTrue(data['resumen']['vendedor_unico'])
        self.assertEqual(data['empresas'][0]['nombre'], 'Empresa Web')
        self.assertEqual(data['productos'][0]['sku'], 'SKU-1')
        self.assertEqual(data['vendedores'][0]['codigo'], '1000')

    def test_excluye_pedidos_cancelados(self):
        self._pedido('cancelado', estado='CANCELADO', total=90000)
        response = self._get()
        self.assertEqual(response.json()['resumen']['pedidos'], 0)
        self.assertEqual(response.json()['resumen']['ventas'], 0.0)

    def test_restringe_sucursales_segun_usuario(self):
        otra_empresa = crear_empresa(nombre='Otra Empresa', rut='76.222.333-4')
        otra_sucursal = crear_sucursal(empresa=otra_empresa, alias='WEB-2')
        otro_vendedor = crear_vendedor(
            nombre='Otro Vendedor', empresa=otra_empresa, codigo_vendedor='2000'
        )
        otro_vendedor.sucursales.add(otra_sucursal)
        self._pedido('permitido', ticket=self._ticket(2))
        self._pedido(
            'oculto', sucursal=otra_sucursal,
            ticket=self._ticket(1, sucursal=otra_sucursal, vendedor=otro_vendedor),
        )

        limitado = crear_usuario(username='limitado', rol='vendedor')
        crear_empresa_user(limitado, self.empresa, self.sucursal)
        self.client.logout()
        self.client.login(username='limitado', password='TestPass123!')

        data = self._get().json()
        self.assertEqual(data['resumen']['pedidos'], 1)
        self.assertEqual(data['empresas'][0]['nombre'], 'Empresa Web')

    def test_exporta_excel(self):
        self._pedido('excel', ticket=self._ticket(3))
        response = self.client.get(
            reverse('exportar_reporte_ventas_internet'),
            {
                'fecha_inicio': (timezone.localdate() - timedelta(days=1)).isoformat(),
                'fecha_fin': timezone.localdate().isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

