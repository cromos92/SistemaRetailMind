"""
Tests del módulo "Retiro pedido local" (/app/ecommerce/retiro-local/).

Cubre los 4 endpoints: pantalla del mesón, validar (preview), confirmar
(registra el retiro en AllConnected) y el comprobante PDF de respaldo.
AllConnected se mockea siempre: acá se prueba el lado RetailMind del
contrato (guards, validaciones, rastro local y print_data enriquecido).

Correr en BD local desechable:
    python manage.py test app.tests.test_retiro_local --keepdb
"""
import json
from unittest import mock

from django.test import TestCase

from app.models import HistorialPedidoEcommerce, PedidoEcommerce
from app.services.pdf_comprobante_retiro import generar_comprobante_retiro_pdf
from app.services.pdf_guia_preparacion import _paginas
from app.views_ecommerce import _enmascarar_documento

from .factories import setup_entorno_completo

URL_PANTALLA = '/app/ecommerce/retiro-local/'
URL_VALIDAR = '/app/ecommerce/retiro-local/validar/'
URL_CONFIRMAR = '/app/ecommerce/retiro-local/confirmar/'
URL_PDF = '/app/ecommerce/retiro-local/comprobante-pdf/'

# RUT con dígito verificador correcto (módulo 11): 12.345.678 → DV 5
RUT_VALIDO = '12.345.678-5'
RUT_INVALIDO = '12.345.678-9'

# Shape que devuelve AllConnected en la confirmación (contrato del service)
PRINT_DATA = {
    'numero_pedido': 'SHOP-9001',
    'ticket_rm': 'RM-TEST0001',
    'cliente': 'Cliente Internet',
    'retirador_nombre': 'Juan Retirador',
    'retirador_documento': RUT_VALIDO,
    'items': [
        {'sku': '4800769', 'nombre': 'Zapatilla Test', 'talla': '39', 'cantidad': 1},
        {'sku': '4800770', 'nombre': 'Polera Test', 'talla': 'M', 'cantidad': 2},
    ],
    'codigo_enmascarado': '****20',
    'fecha': '31/08/2026 12:00',
    'sucursal': 'PAO1',
    'usuario_pos': 'testuser',
}


def _permiso_ok(valor=True):
    return mock.patch('app.views_ecommerce.PermisoRol.tiene_permiso',
                      return_value=valor)


class RetiroLocalBase(TestCase):
    def setUp(self):
        self.entorno = setup_entorno_completo()
        self.user = self.entorno['user']
        self.sucursal = self.entorno['sucursal']
        self.sucursal.alias = 'PAO1'
        self.sucursal.save(update_fields=['alias'])

    def _sesion(self, alias='PAO1'):
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session['alias'] = alias
        session.save()

    def _pedido(self, **kwargs):
        defaults = dict(
            numero_ticket_rm='RM-TEST0001',
            numero_pedido_canal='SHOP-9001',
            canal_origen='SHOPIFY',
            sucursal=self.sucursal,
            cliente_nombre='Cliente Internet',
            cliente_documento='11.111.111-1',
            total=40000,
            es_retiro_local=True,
            estado_logistica_canal='LISTO_RETIRO',
            items=[{'sku': '123', 'nombre': 'Zapatilla', 'cantidad': 1}],
        )
        defaults.update(kwargs)
        return PedidoEcommerce.objects.create(**defaults)

    def _post(self, url, body):
        return self.client.post(url, data=json.dumps(body),
                                content_type='application/json')


class PantallaRetiroTest(RetiroLocalBase):
    def test_lista_solo_listo_retiro_de_la_sucursal(self):
        """La pantalla lista únicamente los retiros liberados (LISTO_RETIRO)
        de la sucursal en sesión; fuera quedan los no liberados, los
        cancelados y los de otras sucursales."""
        from app.tests.factories import crear_sucursal
        visible = self._pedido()
        self._pedido(numero_ticket_rm='RM-NOLIB01', numero_pedido_canal='SHOP-9002',
                     estado_logistica_canal='')
        self._pedido(numero_ticket_rm='RM-CANCEL1', numero_pedido_canal='SHOP-9003',
                     estado='CANCELADO')
        otra = crear_sucursal(empresa=self.entorno['empresa'], alias='PAO3')
        self._pedido(numero_ticket_rm='RM-OTRASUC', numero_pedido_canal='SHOP-9004',
                     sucursal=otra)

        self._sesion()
        with _permiso_ok():
            resp = self.client.get(URL_PANTALLA)
        self.assertEqual(resp.status_code, 200)
        tickets = [p.numero_ticket_rm for p in resp.context['pedidos']]
        self.assertEqual(tickets, [visible.numero_ticket_rm])

    def test_middleware_bloquea_fuera_de_pao1(self):
        """Con otra sucursal activa el middleware corta antes de la vista."""
        self._sesion(alias='PAO3')
        with _permiso_ok():
            resp = self.client.get(URL_PANTALLA)
        self.assertEqual(resp.status_code, 302)

    def test_sin_permiso_redirige(self):
        self._sesion()
        with _permiso_ok(False):
            resp = self.client.get(URL_PANTALLA)
        self.assertEqual(resp.status_code, 302)


class ValidarRetiroTest(RetiroLocalBase):
    def test_reenvia_a_allconnected_y_pasa_la_respuesta(self):
        contrato = {'ok': True, 'pedido': {'numero_pedido_canal': 'SHOP-9001'},
                    'items': [], 'advertencias': []}
        self._sesion()
        with _permiso_ok(), mock.patch(
                'app.services.allconnected_pedidos_service.validar_retiro',
                return_value=contrato) as m:
            resp = self._post(URL_VALIDAR, {
                'numero_ticket_rm': 'RM-TEST0001', 'codigo': '483920'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), contrato)
        m.assert_called_once_with(numero_ticket_rm='RM-TEST0001',
                                  numero_pedido_canal='', codigo='483920')

    def test_codigo_debe_ser_6_digitos(self):
        self._sesion()
        with _permiso_ok():
            for codigo in ('12345', '1234567', 'ABC920', ''):
                resp = self._post(URL_VALIDAR, {
                    'numero_ticket_rm': 'RM-TEST0001', 'codigo': codigo})
                self.assertEqual(resp.status_code, 400, codigo)

    def test_falta_el_pedido(self):
        self._sesion()
        with _permiso_ok():
            resp = self._post(URL_VALIDAR, {'codigo': '483920'})
        self.assertEqual(resp.status_code, 400)

    def test_sin_permiso_403(self):
        self._sesion()
        with _permiso_ok(False):
            resp = self._post(URL_VALIDAR, {
                'numero_ticket_rm': 'RM-TEST0001', 'codigo': '483920'})
        self.assertEqual(resp.status_code, 403)

    def test_fuera_de_pao1_403_ajax(self):
        self._sesion(alias='EDEL')
        with _permiso_ok():
            resp = self._post(URL_VALIDAR, {
                'numero_ticket_rm': 'RM-TEST0001', 'codigo': '483920'})
        self.assertEqual(resp.status_code, 403)


class ConfirmarRetiroTest(RetiroLocalBase):
    def _body(self, **kwargs):
        body = {
            'numero_ticket_rm': 'RM-TEST0001',
            'codigo': '483920',
            'retirador_nombre': 'Juan Retirador',
            'retirador_documento': RUT_VALIDO,
            'tipo_documento': 'RUT',
            'es_titular': True,
        }
        body.update(kwargs)
        return body

    def test_ok_deja_rastro_y_enriquece_print_data(self):
        pedido = self._pedido()
        contrato = {'ok': True, 'acta_id': 77, 'print_data': dict(PRINT_DATA)}
        self._sesion()
        with _permiso_ok(), mock.patch(
                'app.services.allconnected_pedidos_service.confirmar_retiro',
                return_value=contrato) as m:
            resp = self._post(URL_CONFIRMAR, self._body())

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['acta_id'], 77)

        # La confirmación viaja con el operador y la sucursal de la sesión
        kwargs = m.call_args.kwargs
        self.assertEqual(kwargs['codigo'], '483920')
        self.assertEqual(kwargs['sucursal'], 'PAO1')

        # print_data se enriquece con la sucursal REAL de la sesión
        suc_info = data['print_data']['sucursal_info']
        self.assertEqual(suc_info['alias'], 'PAO1')
        self.assertTrue(suc_info['empresa'])

        # Rastro local: historial con acta + documento ENMASCARADO
        hist = HistorialPedidoEcommerce.objects.filter(pedido=pedido).latest('fecha')
        self.assertIn('acta AllConnected #77', hist.motivo)
        self.assertNotIn(RUT_VALIDO, hist.motivo)
        self.assertIn('78-5', hist.motivo)  # solo los últimos 4 visibles

        # Espejo local: el pedido sale de la lista del mesón
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado_logistica_canal, 'ENTREGADO')

    def test_rut_invalido_no_llega_a_allconnected(self):
        self._sesion()
        with _permiso_ok(), mock.patch(
                'app.services.allconnected_pedidos_service.confirmar_retiro') as m:
            resp = self._post(URL_CONFIRMAR,
                              self._body(retirador_documento=RUT_INVALIDO))
        self.assertEqual(resp.status_code, 400)
        m.assert_not_called()

    def test_pasaporte_no_exige_modulo_11(self):
        self._pedido()
        contrato = {'ok': True, 'acta_id': 78, 'print_data': dict(PRINT_DATA)}
        self._sesion()
        with _permiso_ok(), mock.patch(
                'app.services.allconnected_pedidos_service.confirmar_retiro',
                return_value=contrato):
            resp = self._post(URL_CONFIRMAR, self._body(
                retirador_documento='AB123456', tipo_documento='PASAPORTE',
                es_titular=False, retirador_nombre='Turista Extranjero'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])

    def test_faltan_datos_del_retirador(self):
        self._sesion()
        with _permiso_ok():
            resp = self._post(URL_CONFIRMAR, self._body(retirador_documento=''))
            self.assertEqual(resp.status_code, 400)
            resp = self._post(URL_CONFIRMAR, self._body(retirador_nombre=''))
            self.assertEqual(resp.status_code, 400)
            resp = self._post(URL_CONFIRMAR, self._body(tipo_documento='CARNET'))
            self.assertEqual(resp.status_code, 400)

    def test_ya_retirado_pasa_el_error_sin_rastro(self):
        pedido = self._pedido()
        contrato = {'ok': False, 'code': 'YA_RETIRADO',
                    'message': 'Ya retirado el 30/08 por Otro Cliente.'}
        self._sesion()
        with _permiso_ok(), mock.patch(
                'app.services.allconnected_pedidos_service.confirmar_retiro',
                return_value=contrato):
            resp = self._post(URL_CONFIRMAR, self._body())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data['ok'])
        self.assertEqual(data['code'], 'YA_RETIRADO')
        self.assertFalse(
            HistorialPedidoEcommerce.objects.filter(pedido=pedido).exists())
        pedido.refresh_from_db()
        self.assertEqual(pedido.estado_logistica_canal, 'LISTO_RETIRO')


class ComprobantePdfTest(RetiroLocalBase):
    def test_endpoint_responde_pdf_con_dos_copias(self):
        self._sesion()
        with _permiso_ok():
            resp = self._post(URL_PDF, {'print_data': dict(PRINT_DATA)})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertTrue(resp.content.startswith(b'%PDF'))
        self.assertEqual(_paginas(resp.content), 2,
                         'Debe traer ORIGINAL — TIENDA y COPIA — CLIENTE')

    def test_print_data_incompleto_400(self):
        self._sesion()
        with _permiso_ok():
            resp = self._post(URL_PDF, {'print_data': {}})
        self.assertEqual(resp.status_code, 400)

    def test_generador_tolera_campos_faltantes(self):
        """El PDF sale aunque AllConnected mande lo mínimo (solo el ticket)."""
        pdf = generar_comprobante_retiro_pdf({'ticket_rm': 'RM-MINIMO1'})
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(_paginas(pdf), 2)


class EnmascararDocumentoTest(TestCase):
    def test_solo_ultimos_4_visibles(self):
        self.assertEqual(_enmascarar_documento('12.345.678-5'), '********78-5')

    def test_documento_corto_todo_oculto(self):
        self.assertEqual(_enmascarar_documento('123'), '***')
        self.assertEqual(_enmascarar_documento(''), '')
        self.assertEqual(_enmascarar_documento(None), '')
