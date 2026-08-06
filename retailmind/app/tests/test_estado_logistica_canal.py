"""
Tests del fix de zombies del 2026-08-05 (lado RetailMind).

Diagnóstico: AllConnected tiene `estado` y `estado_logistica` en PARALELO. Un
pedido despachado queda `estado='PREPARANDO'` + `estado_logistica='ENVIADO'`, y
la sincronización miraba SOLO el primero: pedidos ya enviados seguían en la cola
de picking de la tienda (había uno de 44 días con tracking).

Cubre:
- el sync cierra como FACTURADO_EXTERNO lo que viene `despachado` (por
  cualquiera de los dos campos de AC);
- LISTO_ENVIO/LISTO_RETIRO NO se cierra y es solo INFORMATIVO: la tienda igual
  imprime la guía (individual y masiva). Se probó bloquearlo y fue un error —
  ver `test_guia_SI_se_imprime_con_listo_envio`;
- la facturación se bloquea por el estado logístico despachado;
- `confirmar_tickets_en_allconnected` (contrato defensivo del cliente HTTP).

Correr en BD local desechable:
    python manage.py test app.tests.test_estado_logistica_canal
"""
import json
from unittest import mock

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from app.models import ModuloSistema, OpcionMenu, PedidoEcommerce, PermisoRol
from app.services import allconnected_pedidos_service as ac_service
from app.views_ecommerce import (
    _bloqueo_por_estado_canal,
    _listo_envio_en_canal,
    api_imprimir_guia_preparacion,
    api_imprimir_guias_sucursal,
)

from .factories import crear_sucursal, crear_usuario


def _resp_estados(estados):
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {'ok': True, 'estados': estados, 'no_encontrados': []}
    return resp


@override_settings(ALLCONNECTED_API_BASE_URL='https://ac.test', ALLCONNECTED_API_KEY='k')
class SyncEstadoLogisticaTest(TestCase):

    def setUp(self):
        self.sucursal = crear_sucursal()

    def _pedido(self, numero, sub_estado='EN_PREPARACION'):
        return PedidoEcommerce.objects.create(
            numero_ticket_rm=f'RM-{numero}',
            numero_pedido_canal=numero,
            canal_origen='PARIS',
            sucursal=self.sucursal,
            cliente_nombre='Cliente Log',
            sub_estado=sub_estado,
            fecha_asignacion=timezone.now(),
            total=10000,
            items=[{'sku': '1', 'nombre': 'X', 'cantidad': 1, 'precio_unitario': 10000}],
        )

    def _sync(self, estado, estado_logistica, despachado=False, listo_envio=False):
        with mock.patch.object(ac_service.requests, 'post') as m:
            m.return_value = _resp_estados([{
                'canal_origen': 'PARIS', 'numero_pedido_canal': 'P-1',
                'estado': estado, 'estado_logistica': estado_logistica,
                'cancelado': False, 'pagado': True,
                'despachado': despachado, 'listo_envio': listo_envio,
            }])
            return ac_service.sincronizar_estados_pedidos()

    def test_enviado_por_logistica_cierra_el_pedido(self):
        """El caso real: estado=PREPARANDO pero ya salió (logística=ENVIADO)."""
        p = self._pedido('P-1')
        res = self._sync('PREPARANDO', 'ENVIADO', despachado=True)

        self.assertEqual(res['cerrados_despachados'], 1)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'FACTURADO')
        self.assertEqual(p.sub_estado, 'FACTURADO_EXTERNO')
        self.assertEqual(p.estado_logistica_canal, 'ENVIADO')

    def test_fallback_sin_flag_despachado(self):
        """AllConnected viejo (sin `despachado`): RM igual lo deduce del campo."""
        p = self._pedido('P-1')
        res = self._sync('PREPARANDO', 'ENTREGADO', despachado=False)
        self.assertEqual(res['cerrados_despachados'], 1)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'FACTURADO')

    def test_listo_envio_no_cierra_pero_se_registra(self):
        p = self._pedido('P-1')
        res = self._sync('PREPARANDO', 'LISTO_ENVIO', listo_envio=True)

        self.assertEqual(res['listos_central'], 1)
        self.assertEqual(res['cerrados_despachados'], 0)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'PENDIENTE', 'aún no salió: no se cierra')
        self.assertEqual(p.estado_logistica_canal, 'LISTO_ENVIO')
        self.assertTrue(_listo_envio_en_canal(p))

    def test_preparando_sigue_siendo_trabajo(self):
        p = self._pedido('P-1')
        res = self._sync('PREPARANDO', 'PREPARANDO')
        self.assertEqual(res['cerrados_despachados'], 0)
        self.assertEqual(res['listos_central'], 0)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'PENDIENTE')
        self.assertFalse(_listo_envio_en_canal(p))


class BloqueosPorEstadoLogisticoTest(TestCase):

    def setUp(self):
        self.sucursal = crear_sucursal()
        self.user = crear_usuario(rol='administrador')
        modulo = ModuloSistema.objects.create(codigo='ecommerce', nombre='Ecommerce')
        opcion = OpcionMenu.objects.create(
            modulo=modulo, codigo='ecommerce_pedidos_todos', nombre='Pedidos Ecommerce')
        PermisoRol.objects.create(rol=self.user.rol, opcion_menu=opcion,
                                  puede_ver=True, puede_editar=True)
        self.pedido = PedidoEcommerce.objects.create(
            numero_ticket_rm='RM-LOG1', numero_pedido_canal='ORD-LOG1',
            canal_origen='PARIS', sucursal=self.sucursal, cliente_nombre='Cliente',
            sub_estado='ASIGNADO', fecha_asignacion=timezone.now(), total=10000,
            items=[{'sku': '1', 'nombre': 'X', 'cantidad': 1, 'precio_unitario': 10000}],
        )

    def _guia(self):
        request = RequestFactory().post(
            f'/app/ecommerce/pedidos/{self.pedido.id}/imprimir-guia/',
            data='{}', content_type='application/json')
        request.user = self.user
        request.session = {}
        return api_imprimir_guia_preparacion(request, self.pedido.id)

    def test_facturar_bloqueado_si_logistica_dice_enviado(self):
        self.pedido.estado_logistica_canal = 'ENVIADO'
        self.pedido.save(update_fields=['estado_logistica_canal'])
        msg = _bloqueo_por_estado_canal(self.pedido)
        self.assertIsNotNone(msg)
        self.assertIn('ENVIADO', msg)

    def test_facturar_no_se_bloquea_por_listo_envio(self):
        """LISTO_ENVIO nunca bloqueó la facturación (y sigue sin hacerlo)."""
        self.pedido.estado_logistica_canal = 'LISTO_ENVIO'
        self.pedido.save(update_fields=['estado_logistica_canal'])
        self.assertIsNone(_bloqueo_por_estado_canal(self.pedido))

    def test_guia_SI_se_imprime_con_listo_envio(self):
        """REGRESIÓN (06-ago): LISTO_ENVIO NO puede bloquear la guía.

        Se creyó que ese estado significaba "lo preparó la central", pero en prod
        los pedidos de Paris llegan con LISTO_ENVIO sin tracking y con la bitácora
        solo en 'En Preparacion': el producto sigue sin sacarse. Bloquear la guía
        dejó pedidos reales sin poder prepararse en tienda.
        """
        self.pedido.estado_logistica_canal = 'LISTO_ENVIO'
        self.pedido.save(update_fields=['estado_logistica_canal'])
        response = self._guia()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)['ok'])

    def test_guia_ok_sin_estado_logistico(self):
        self.assertEqual(self._guia().status_code, 200)

    def test_masiva_INCLUYE_los_listo_envio(self):
        """Misma regresión, del lado de la impresión masiva por sucursal."""
        self.pedido.estado_logistica_canal = 'LISTO_RETIRO'
        self.pedido.save(update_fields=['estado_logistica_canal'])
        request = RequestFactory().post('/app/ecommerce/pedidos/imprimir-guias-sucursal/',
                                        data='{}', content_type='application/json')
        request.user = self.user
        request.session = {'idSucursalActual': self.sucursal.id}
        response = api_imprimir_guias_sucursal(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['total'], 1)

    def test_masiva_excluye_los_ya_despachados(self):
        """Lo que SÍ debe excluirse: los que el canal reporta ya enviados."""
        self.pedido.estado_logistica_canal = 'ENVIADO'
        self.pedido.save(update_fields=['estado_logistica_canal'])
        request = RequestFactory().post('/app/ecommerce/pedidos/imprimir-guias-sucursal/',
                                        data='{}', content_type='application/json')
        request.user = self.user
        request.session = {'idSucursalActual': self.sucursal.id}
        response = api_imprimir_guias_sucursal(request)
        self.assertEqual(json.loads(response.content)['total'], 0)


@override_settings(ALLCONNECTED_API_BASE_URL='https://ac.test', ALLCONNECTED_API_KEY='k')
class ConfirmarTicketsServicioTest(TestCase):
    """RM le devuelve a AC el ticket que asignó por pull (contrato defensivo)."""

    def setUp(self):
        self.sucursal = crear_sucursal()
        self.pedido = PedidoEcommerce.objects.create(
            numero_ticket_rm='RM-TK1', numero_pedido_canal='ORD-TK1',
            canal_origen='PARIS', sucursal=self.sucursal,
            cliente_nombre='Cliente', total=1000, items=[],
        )

    def _resp(self, actualizados=1, status_code=200):
        r = mock.Mock(status_code=status_code)
        r.json.return_value = {'ok': True, 'actualizados': actualizados, 'ya_tenian': 0,
                               'conflictos': [], 'no_encontrados': []}
        return r

    def test_envia_los_tickets_del_pull(self):
        payload = [{'numero_pedido_canal': 'ORD-TK1', 'canal_origen': 'PARIS'}]
        with mock.patch.object(ac_service.requests, 'post', return_value=self._resp()) as m:
            res = ac_service.confirmar_tickets_en_allconnected(payload)
        self.assertTrue(res['ok'])
        self.assertEqual(res['actualizados'], 1)
        enviado = m.call_args.kwargs['json']['tickets'][0]
        self.assertEqual(enviado['numero_ticket_rm'], 'RM-TK1')
        self.assertEqual(enviado['numero_pedido_canal'], 'ORD-TK1')

    def test_deploy_pendiente_no_lanza(self):
        with mock.patch.object(ac_service.requests, 'post', return_value=mock.Mock(status_code=404)):
            res = ac_service.confirmar_tickets_en_allconnected(
                [{'numero_pedido_canal': 'ORD-TK1', 'canal_origen': 'PARIS'}])
        self.assertFalse(res['ok'])
        self.assertIn('deploy pendiente', res['detalle'])

    def test_red_caida_no_lanza(self):
        with mock.patch.object(ac_service.requests, 'post',
                               side_effect=ac_service.requests.RequestException('timeout')):
            res = ac_service.confirmar_tickets_en_allconnected(
                [{'numero_pedido_canal': 'ORD-TK1', 'canal_origen': 'PARIS'}])
        self.assertFalse(res['ok'])
        self.assertEqual(res['actualizados'], 0)

    def test_sin_pedidos_no_llama(self):
        with mock.patch.object(ac_service.requests, 'post') as m:
            res = ac_service.confirmar_tickets_en_allconnected([])
        m.assert_not_called()
        self.assertTrue(res['ok'])

    def test_backfill_por_queryset(self):
        with mock.patch.object(ac_service.requests, 'post', return_value=self._resp()) as m:
            res = ac_service.confirmar_tickets_en_allconnected(
                pedidos_qs=[self.pedido])
        self.assertEqual(res['enviados'], 1)
        self.assertTrue(m.called)
