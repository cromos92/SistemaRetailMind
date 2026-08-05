"""
Tests del quiebre de stock en tienda (pedidos ecommerce, 2026-08-05).

La tienda va a buscar el producto con la guía, no está, y aprieta "Sin stock":
el pedido queda en sub-estado SIN_STOCK (sigue PENDIENTE pero fuera del flujo
de picking) y se le reporta la incidencia a AllConnected, que la abre como
`PedidoIncidenciaOperativa` tipo SIN_STOCK y avisa a central.

Cubre:
- marcado + aviso OK / aviso caído (el pedido SIEMPRE queda marcado acá);
- reintento del aviso al volver a marcar un pedido ya marcado;
- bloqueos: no se imprime guía ni se factura (individual) mientras esté abierto;
- reactivar (el producto apareció) y reasignar (limpia el estado);
- permisos y transiciones inválidas.

Correr en BD local desechable:
    python manage.py test app.tests.test_sin_stock_ecommerce
"""
import json
from unittest import mock

from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from app.models import (
    HistorialPedidoEcommerce, ModuloSistema, OpcionMenu, PedidoEcommerce, PermisoRol,
)
from app.services import allconnected_pedidos_service as ac_service
from app.views_ecommerce import (
    api_facturar_pedido_individual,
    api_imprimir_guia_preparacion,
    api_marcar_sin_stock,
    api_reactivar_sin_stock,
    api_reasignar_pedido,
)

from .factories import crear_sucursal, crear_usuario, crear_vendedor


def _resp_ac(ok=True, ya_existia=False, status_code=200, incidencia_id=7):
    """Mock de requests.post → /app/pedidos/incidencia-sin-stock/ de AC."""
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = {
        'ok': ok, 'ya_existia': ya_existia, 'incidencia_id': incidencia_id,
        'numero_pedido': 'MP-000123', 'estado_operativo': 'RETENIDO',
    }
    return resp


@override_settings(ALLCONNECTED_API_BASE_URL='https://ac.test', ALLCONNECTED_API_KEY='k')
class _BaseSinStockTest(TestCase):

    def setUp(self):
        self.sucursal = crear_sucursal()
        self.user = crear_usuario(rol='administrador')
        modulo = ModuloSistema.objects.create(codigo='ecommerce', nombre='Ecommerce')
        self.opcion = OpcionMenu.objects.create(
            modulo=modulo, codigo='ecommerce_pedidos_todos', nombre='Pedidos Ecommerce',
        )
        PermisoRol.objects.create(
            rol=self.user.rol, opcion_menu=self.opcion,
            puede_ver=True, puede_editar=True, puede_crear=True,
        )
        self.pedido = PedidoEcommerce.objects.create(
            numero_ticket_rm='RM-SINSTOCK1',
            numero_pedido_canal='ORD-1',
            canal_origen='PAOLA',
            sucursal=self.sucursal,
            cliente_nombre='Cliente Quiebre',
            sub_estado='EN_PREPARACION',
            fecha_asignacion=timezone.now(),
            total=19990,
            items=[{'sku': '999999', 'nombre': 'Zapatilla fantasma',
                    'cantidad': 1, 'precio_unitario': 19990}],
        )

    def _post(self, view, pedido_id, data=None, user=None):
        request = RequestFactory().post(
            f'/app/ecommerce/pedidos/{pedido_id}/x/',
            data=json.dumps(data or {}), content_type='application/json',
        )
        request.user = user or self.user
        request.session = {}
        return view(request, pedido_id)


class MarcarSinStockTest(_BaseSinStockTest):

    def test_marca_y_avisa_a_allconnected(self):
        with mock.patch.object(ac_service.requests, 'post', return_value=_resp_ac()) as m:
            response = self._post(api_marcar_sin_stock, self.pedido.id,
                                  {'motivo': 'No está en bodega ni en sala'})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['ok'])
        self.assertFalse(data['ya_estaba'])
        self.assertEqual(data['sub_estado'], 'SIN_STOCK')
        self.assertTrue(data['avisado_allconnected'])

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.estado, 'PENDIENTE', 'sigue pendiente: no es una cancelación')
        self.assertEqual(self.pedido.sub_estado, 'SIN_STOCK')
        self.assertEqual(self.pedido.sin_stock_motivo, 'No está en bodega ni en sala')
        self.assertTrue(self.pedido.sin_stock_avisado_ac)

        # El payload que viaja a AC lleva ticket, canal, motivo y sucursal.
        payload = m.call_args.kwargs['json']
        self.assertEqual(payload['numero_ticket_rm'], 'RM-SINSTOCK1')
        self.assertEqual(payload['canal_origen'], 'PAOLA')
        self.assertEqual(payload['motivo'], 'No está en bodega ni en sala')
        self.assertTrue(payload['sucursal'])

        h = HistorialPedidoEcommerce.objects.get(pedido=self.pedido)
        self.assertEqual(h.sub_estado_nuevo, 'SIN_STOCK')
        self.assertEqual(h.tipo_evento, 'ERROR')

    def test_aviso_caido_igual_marca_el_pedido(self):
        """AllConnected no responde: el pedido se marca igual y queda flagueado
        para reintentar (la tienda no puede quedar bloqueada por la red)."""
        with mock.patch.object(ac_service.requests, 'post',
                               side_effect=ac_service.requests.RequestException('timeout')):
            response = self._post(api_marcar_sin_stock, self.pedido.id, {'motivo': 'no está'})
        data = json.loads(response.content)
        self.assertTrue(data['ok'])
        self.assertFalse(data['avisado_allconnected'])
        self.assertIn('No se pudo avisar', data['aviso_detalle'])

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.sub_estado, 'SIN_STOCK')
        self.assertFalse(self.pedido.sin_stock_avisado_ac)

    def test_remarcar_reintenta_el_aviso_sin_duplicar_historial(self):
        with mock.patch.object(ac_service.requests, 'post',
                               side_effect=ac_service.requests.RequestException('timeout')):
            self._post(api_marcar_sin_stock, self.pedido.id, {'motivo': 'no está'})
        self.pedido.refresh_from_db()
        self.assertFalse(self.pedido.sin_stock_avisado_ac)

        # Segundo intento con AC arriba: se avisa y el flag queda en True.
        with mock.patch.object(ac_service.requests, 'post',
                               return_value=_resp_ac(ya_existia=True)):
            response = self._post(api_marcar_sin_stock, self.pedido.id)
        data = json.loads(response.content)
        self.assertTrue(data['ya_estaba'])
        self.assertTrue(data['avisado_allconnected'])

        self.pedido.refresh_from_db()
        self.assertTrue(self.pedido.sin_stock_avisado_ac)
        self.assertEqual(
            HistorialPedidoEcommerce.objects.filter(pedido=self.pedido).count(), 1,
            'reintentar el aviso no debe duplicar el historial',
        )

    def test_sin_allconnected_configurado_marca_pero_avisa_del_gap(self):
        with override_settings(ALLCONNECTED_API_BASE_URL=''):
            response = self._post(api_marcar_sin_stock, self.pedido.id, {'motivo': 'x'})
        data = json.loads(response.content)
        self.assertTrue(data['ok'])
        self.assertFalse(data['avisado_allconnected'])
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.sub_estado, 'SIN_STOCK')

    def test_no_se_puede_marcar_un_facturado(self):
        self.pedido.estado = 'FACTURADO'
        self.pedido.save(update_fields=['estado'])
        with self.assertRaises(Http404):
            self._post(api_marcar_sin_stock, self.pedido.id)

    def test_sin_permiso_da_403(self):
        vendedor = crear_usuario(username='vend_sinstock', rol='vendedor')
        response = self._post(api_marcar_sin_stock, self.pedido.id, user=vendedor)
        self.assertEqual(response.status_code, 403)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.sub_estado, 'EN_PREPARACION')

    def test_desde_listo_despacho_no_se_marca(self):
        """Ya está empaquetado: marcar sin stock ahí es un error de operación."""
        self.pedido.sub_estado = 'LISTO_DESPACHO'
        self.pedido.save(update_fields=['sub_estado'])
        with mock.patch.object(ac_service.requests, 'post', return_value=_resp_ac()) as m:
            response = self._post(api_marcar_sin_stock, self.pedido.id)
        self.assertEqual(response.status_code, 400)
        m.assert_not_called()


class BloqueosSinStockTest(_BaseSinStockTest):

    def setUp(self):
        super().setUp()
        with mock.patch.object(ac_service.requests, 'post', return_value=_resp_ac()):
            self._post(api_marcar_sin_stock, self.pedido.id, {'motivo': 'no está'})
        self.pedido.refresh_from_db()

    def test_no_imprime_guia(self):
        response = self._post(api_imprimir_guia_preparacion, self.pedido.id)
        self.assertEqual(response.status_code, 409)
        self.assertIn('SIN STOCK', json.loads(response.content)['error'])

    def test_no_factura(self):
        # La vista resuelve el vendedor de internet (cód. 1000) ANTES de los
        # guards; sin él daría 400 por otra razón y el test no probaría nada.
        crear_vendedor(nombre='Venta Internet', empresa=self.sucursal.empresa,
                       codigo_vendedor=1000)
        request = RequestFactory().post(
            f'/app/ecommerce/pedidos/{self.pedido.id}/facturar/',
            data=json.dumps({'tipo_documento': 'BOLETA_ELECTRONICA'}),
            content_type='application/json',
        )
        request.user = self.user
        request.session = {'idSucursalActual': self.sucursal.id}
        response = api_facturar_pedido_individual(request, self.pedido.id)
        self.assertEqual(response.status_code, 409)
        self.assertIn('SIN STOCK', json.loads(response.content)['error'])


class ResolverSinStockTest(_BaseSinStockTest):

    def setUp(self):
        super().setUp()
        with mock.patch.object(ac_service.requests, 'post', return_value=_resp_ac()):
            self._post(api_marcar_sin_stock, self.pedido.id, {'motivo': 'no está'})
        self.pedido.refresh_from_db()

    def test_reactivar_vuelve_a_asignado_y_limpia(self):
        response = self._post(api_reactivar_sin_stock, self.pedido.id,
                              {'motivo': 'apareció en bodega'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['sub_estado'], 'ASIGNADO')

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.sub_estado, 'ASIGNADO')
        self.assertEqual(self.pedido.sin_stock_motivo, '')
        self.assertFalse(self.pedido.sin_stock_avisado_ac)
        self.assertEqual(
            HistorialPedidoEcommerce.objects.filter(
                pedido=self.pedido, sub_estado_anterior='SIN_STOCK').count(), 1)

        # Y ya se le puede volver a imprimir la guía.
        response = self._post(api_imprimir_guia_preparacion, self.pedido.id)
        self.assertEqual(response.status_code, 200)

    def test_reactivar_solo_aplica_a_sin_stock(self):
        self._post(api_reactivar_sin_stock, self.pedido.id)   # queda ASIGNADO
        with self.assertRaises(Http404):
            self._post(api_reactivar_sin_stock, self.pedido.id)

    def test_reasignar_limpia_el_quiebre(self):
        otra = crear_sucursal(empresa=self.sucursal.empresa, alias='SUC-2')
        request = RequestFactory().post(
            f'/app/ecommerce/pedidos/{self.pedido.id}/reasignar/',
            data=json.dumps({'sucursal_id': otra.id, 'motivo': 'sin stock en la original'}),
            content_type='application/json',
        )
        request.user = self.user
        request.session = {}
        response = api_reasignar_pedido(request, self.pedido.id)
        self.assertEqual(response.status_code, 200)

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.sucursal_id, otra.id)
        self.assertNotEqual(self.pedido.sub_estado, 'SIN_STOCK')
        self.assertEqual(self.pedido.sin_stock_motivo, '')
        self.assertFalse(self.pedido.sin_stock_avisado_ac)


@override_settings(ALLCONNECTED_API_BASE_URL='https://ac.test', ALLCONNECTED_API_KEY='k')
class ReportarSinStockServicioTest(TestCase):
    """Contrato defensivo del cliente HTTP: nunca lanza, siempre devuelve dict."""

    def setUp(self):
        self.sucursal = crear_sucursal()
        self.pedido = PedidoEcommerce.objects.create(
            numero_ticket_rm='RM-SVC1', numero_pedido_canal='ORD-SVC',
            canal_origen='PAOLA', sucursal=self.sucursal,
            cliente_nombre='Cliente', total=1000, items=[],
        )

    def test_deploy_pendiente_en_ac_da_mensaje_claro(self):
        resp = mock.Mock(status_code=501)
        with mock.patch.object(ac_service.requests, 'post', return_value=resp):
            res = ac_service.reportar_sin_stock(self.pedido, 'x')
        self.assertFalse(res['ok'])
        self.assertIn('deploy pendiente', res['detalle'])

    def test_pedido_inexistente_en_ac(self):
        resp = mock.Mock(status_code=404)
        with mock.patch.object(ac_service.requests, 'post', return_value=resp):
            res = ac_service.reportar_sin_stock(self.pedido, 'x')
        self.assertFalse(res['ok'])
        self.assertIn('no encontró', res['detalle'])

    def test_respuesta_ilegible_no_lanza(self):
        resp = mock.Mock(status_code=200)
        resp.json.side_effect = ValueError('boom')
        with mock.patch.object(ac_service.requests, 'post', return_value=resp):
            res = ac_service.reportar_sin_stock(self.pedido, 'x')
        self.assertFalse(res['ok'])
        self.assertIn('ilegible', res['detalle'])

    def test_ya_existia_se_propaga(self):
        with mock.patch.object(ac_service.requests, 'post',
                               return_value=_resp_ac(ya_existia=True)):
            res = ac_service.reportar_sin_stock(self.pedido, 'x')
        self.assertTrue(res['ok'])
        self.assertTrue(res['ya_existia'])
