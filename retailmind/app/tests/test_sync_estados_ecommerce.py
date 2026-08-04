"""
Tests de la sincronización de ESTADOS con AllConnected + guard de facturación
+ impresión masiva de guías por sucursal + guard de empresa en reasignación
(cambios 2026-08-04).

- `sincronizar_estados_pedidos`: pregunta a AC el estado real de los
  PENDIENTES locales; los cancelados/devueltos en el canal pasan a CANCELADO
  acá (con historial, mismo rastro que el push oficial) y los sin pago quedan
  marcados (`estado_canal='PENDIENTE'`).
- `_bloqueo_por_estado_canal`: la facturación rechaza pedidos que el canal
  reporta cancelados o sin pago confirmado; '' (nunca sincronizado) no bloquea.
- `api_imprimir_guias_sucursal`: un clic imprime TODO lo por preparar de la
  sucursal activa (ASIGNADO/EN_PREPARACION sin guía), con tope.
- `api_reasignar_pedido`: solo sucursales de la MISMA empresa (la boleta debe
  salir con el RUT del canal que vendió).

Correr en BD local desechable:
    python manage.py test app.tests.test_sync_estados_ecommerce
"""
import json
from unittest import mock

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from app.models import (
    HistorialPedidoEcommerce, ModuloSistema, OpcionMenu, PedidoEcommerce, PermisoRol,
)
from app.services import allconnected_pedidos_service as ac_service
from app.views_ecommerce import (
    _bloqueo_por_estado_canal,
    api_imprimir_guias_sucursal,
    api_reasignar_pedido,
)

from .factories import crear_empresa, crear_sucursal, crear_usuario


def _pedido(sucursal, numero, canal='RIPLEY', estado='PENDIENTE',
            sub_estado='ASIGNADO', **extra):
    defaults = dict(
        numero_ticket_rm=f'RM-{canal}-{numero}',
        numero_pedido_canal=numero,
        canal_origen=canal,
        sucursal=sucursal,
        cliente_nombre='Cliente Sync',
        estado=estado,
        sub_estado=sub_estado,
        fecha_asignacion=timezone.now(),
        total=10000,
        items=[{'sku': '111', 'nombre': 'Zapatilla', 'cantidad': 1, 'precio_unitario': 10000}],
    )
    defaults.update(extra)
    return PedidoEcommerce.objects.create(**defaults)


def _respuesta_ac(estados, no_encontrados=None):
    """Mock de requests.post → respuesta del endpoint /app/pedidos/estados/ de AC."""
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {'ok': True, 'estados': estados,
                              'no_encontrados': no_encontrados or []}
    return resp


@override_settings(ALLCONNECTED_API_BASE_URL='https://ac.test',
                   ALLCONNECTED_API_KEY='k')
class SincronizarEstadosTest(TestCase):

    def setUp(self):
        self.sucursal = crear_sucursal()

    def test_cancelado_en_canal_marca_cancelado_local_con_historial(self):
        p = _pedido(self.sucursal, 'RIP-1')
        with mock.patch.object(ac_service.requests, 'post') as m:
            m.return_value = _respuesta_ac([{
                'canal_origen': 'RIPLEY', 'numero_pedido_canal': 'RIP-1',
                'estado': 'CANCELADO', 'cancelado': True, 'pagado': True,
            }])
            res = ac_service.sincronizar_estados_pedidos()

        self.assertTrue(res['ok'])
        self.assertEqual(res['cancelados'], 1)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'CANCELADO')
        self.assertEqual(p.sub_estado, 'CANCELADO_CLIENTE')
        self.assertEqual(p.estado_canal, 'CANCELADO')
        self.assertIsNotNone(p.fecha_sync_estado_canal)
        h = HistorialPedidoEcommerce.objects.get(pedido=p)
        self.assertEqual(h.estado_nuevo, 'CANCELADO')
        self.assertIn('CANCELADO', h.motivo)

    def test_devuelto_tambien_cancela(self):
        p = _pedido(self.sucursal, 'RIP-2')
        with mock.patch.object(ac_service.requests, 'post') as m:
            m.return_value = _respuesta_ac([{
                'canal_origen': 'RIPLEY', 'numero_pedido_canal': 'RIP-2',
                'estado': 'DEVUELTO', 'cancelado': True, 'pagado': True,
            }])
            res = ac_service.sincronizar_estados_pedidos()
        self.assertEqual(res['cancelados'], 1)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'CANCELADO')

    def test_sin_pago_solo_marca_estado_canal(self):
        p = _pedido(self.sucursal, 'SHO-1', canal='SHOPIFY')
        with mock.patch.object(ac_service.requests, 'post') as m:
            m.return_value = _respuesta_ac([{
                'canal_origen': 'SHOPIFY', 'numero_pedido_canal': 'SHO-1',
                'estado': 'PENDIENTE', 'cancelado': False, 'pagado': False,
            }])
            res = ac_service.sincronizar_estados_pedidos()
        self.assertEqual(res['sin_pago'], 1)
        self.assertEqual(res['cancelados'], 0)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'PENDIENTE', 'sin pago NO cancela: solo marca')
        self.assertEqual(p.estado_canal, 'PENDIENTE')

    def test_pagado_actualiza_estado_canal_y_desbloquea(self):
        p = _pedido(self.sucursal, 'SHO-2', canal='SHOPIFY', estado_canal='PENDIENTE')
        with mock.patch.object(ac_service.requests, 'post') as m:
            m.return_value = _respuesta_ac([{
                'canal_origen': 'SHOPIFY', 'numero_pedido_canal': 'SHO-2',
                'estado': 'PAGADO', 'cancelado': False, 'pagado': True,
            }])
            ac_service.sincronizar_estados_pedidos()
        p.refresh_from_db()
        self.assertEqual(p.estado_canal, 'PAGADO')
        self.assertIsNone(_bloqueo_por_estado_canal(p))

    def test_despachado_se_cierra_como_facturado_externo(self):
        """Regla de negocio 04-ago: ENVIADO/ENTREGADO en el canal = la venta ya
        se documentó por concepto fuera del módulo → sale de la cola SIN
        cancelarse (es una venta real) y SIN DTE propio."""
        enviado = _pedido(self.sucursal, 'RIP-6')
        entregado = _pedido(self.sucursal, 'RIP-7')
        with mock.patch.object(ac_service.requests, 'post') as m:
            m.return_value = _respuesta_ac([
                {'canal_origen': 'RIPLEY', 'numero_pedido_canal': 'RIP-6',
                 'estado': 'ENVIADO', 'cancelado': False, 'pagado': True},
                {'canal_origen': 'RIPLEY', 'numero_pedido_canal': 'RIP-7',
                 'estado': 'ENTREGADO', 'cancelado': False, 'pagado': True},
            ])
            res = ac_service.sincronizar_estados_pedidos()

        self.assertEqual(res['cerrados_despachados'], 2)
        self.assertEqual(res['cancelados'], 0)
        for p, estado_ac in ((enviado, 'ENVIADO'), (entregado, 'ENTREGADO')):
            p.refresh_from_db()
            self.assertEqual(p.estado, 'FACTURADO')
            self.assertEqual(p.sub_estado, 'FACTURADO_EXTERNO')
            self.assertEqual(p.estado_canal, estado_ac)
            self.assertIsNone(p.dte_id, 'sin DTE propio: se documentó por concepto')
            h = HistorialPedidoEcommerce.objects.get(pedido=p)
            self.assertIn('por concepto', h.motivo)
            self.assertEqual(h.sub_estado_nuevo, 'FACTURADO_EXTERNO')

    def test_facturados_no_se_consultan(self):
        _pedido(self.sucursal, 'RIP-3', estado='FACTURADO', sub_estado='FACTURADO_OK')
        with mock.patch.object(ac_service.requests, 'post') as m:
            res = ac_service.sincronizar_estados_pedidos()
        m.assert_not_called()
        self.assertEqual(res['consultados'], 0)

    def test_endpoint_404_no_es_error(self):
        """AC sin el deploy del endpoint: el sync avisa y no rompe el pull."""
        _pedido(self.sucursal, 'RIP-4')
        resp = mock.Mock()
        resp.status_code = 404
        with mock.patch.object(ac_service.requests, 'post', return_value=resp):
            res = ac_service.sincronizar_estados_pedidos()
        self.assertTrue(res['ok'])
        self.assertEqual(res['cancelados'], 0)
        self.assertIn('deploy pendiente', res.get('detalle', ''))

    def test_sync_cubre_todas_las_empresas(self):
        """Sin filtro de empresa: los zombies de OTRA cadena también se limpian
        (en prod, 42 cancelados de PAOLA quedaban vivos porque el sync corría
        desde una sesión NICK)."""
        _pedido(self.sucursal, 'RIP-5', rut_empresa='76.111.111-1')
        _pedido(self.sucursal, 'PAO-1', canal='PAOLA', rut_empresa='76.222.222-2')
        with mock.patch.object(ac_service.requests, 'post') as m:
            m.return_value = _respuesta_ac([
                {'canal_origen': 'RIPLEY', 'numero_pedido_canal': 'RIP-5',
                 'estado': 'CANCELADO', 'cancelado': True, 'pagado': True},
                {'canal_origen': 'PAOLA', 'numero_pedido_canal': 'PAO-1',
                 'estado': 'CANCELADO', 'cancelado': True, 'pagado': True},
            ])
            res = ac_service.sincronizar_estados_pedidos()
        self.assertEqual(res['cancelados'], 2)
        # El request incluyó AMBOS pedidos (ninguna cadena quedó fuera).
        enviados = m.call_args.kwargs['json']['pedidos']
        self.assertEqual({p['numero_pedido_canal'] for p in enviados}, {'RIP-5', 'PAO-1'})


class BloqueoEstadoCanalTest(TestCase):

    def setUp(self):
        self.sucursal = crear_sucursal()

    def test_sin_sync_no_bloquea(self):
        p = _pedido(self.sucursal, 'B-1')
        self.assertIsNone(_bloqueo_por_estado_canal(p))

    def test_cancelado_bloquea(self):
        p = _pedido(self.sucursal, 'B-2', estado_canal='CANCELADO')
        self.assertIn('CANCELADO', _bloqueo_por_estado_canal(p))

    def test_reembolsado_bloquea(self):
        p = _pedido(self.sucursal, 'B-3', estado_canal='REEMBOLSADO')
        self.assertIn('REEMBOLSADO', _bloqueo_por_estado_canal(p))

    def test_pendiente_en_canal_bloquea(self):
        p = _pedido(self.sucursal, 'B-4', estado_canal='PENDIENTE',
                    fecha_sync_estado_canal=timezone.now())
        msg = _bloqueo_por_estado_canal(p)
        self.assertIn('no confirma el pago', msg)

    def test_pagado_no_bloquea(self):
        p = _pedido(self.sucursal, 'B-5', estado_canal='PAGADO')
        self.assertIsNone(_bloqueo_por_estado_canal(p))

    def test_despachado_bloquea_por_doble_documento(self):
        """ENVIADO/ENTREGADO: ya facturado por concepto → boletear acá sería
        doble documento (cubre la ventana entre syncs con estado_canal viejo)."""
        for ec in ('ENVIADO', 'EN_TRANSITO', 'ENTREGADO'):
            p = _pedido(self.sucursal, f'B-D-{ec}', estado_canal=ec)
            msg = _bloqueo_por_estado_canal(p)
            self.assertIn('doble documento', msg, ec)


class _BaseVistaTest(TestCase):
    """Usuario admin con permisos del módulo + sucursal en sesión (dict)."""

    def setUp(self):
        self.empresa = crear_empresa(nombre='NICK SPA', rut='76.111.111-1')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='NICK2')
        self.user = crear_usuario(rol='administrador')
        modulo = ModuloSistema.objects.create(codigo='ecommerce', nombre='Ecommerce')
        opcion = OpcionMenu.objects.create(
            modulo=modulo, codigo='ecommerce_pedidos_todos', nombre='Pedidos Ecommerce',
        )
        PermisoRol.objects.create(
            rol=self.user.rol, opcion_menu=opcion,
            puede_ver=True, puede_editar=True, puede_crear=True,
        )

    def _post(self, view, url, pedido_id=None, data=None):
        request = RequestFactory().post(
            url, data=json.dumps(data or {}), content_type='application/json',
        )
        request.user = self.user
        request.session = {'idSucursalActual': self.sucursal.id}
        if pedido_id is not None:
            return view(request, pedido_id)
        return view(request)


class ImprimirGuiasSucursalTest(_BaseVistaTest):

    def test_imprime_solo_por_preparar_sin_guia(self):
        asignado = _pedido(self.sucursal, 'G-1')
        en_prep = _pedido(self.sucursal, 'G-2', sub_estado='EN_PREPARACION')
        # Fuera de alcance: sin stock confirmado, ya listo, ya con guía, otra sucursal
        _pedido(self.sucursal, 'G-3', sub_estado='RECIBIDO')
        _pedido(self.sucursal, 'G-4', sub_estado='LISTO_DESPACHO')
        _pedido(self.sucursal, 'G-5', fecha_impresion_guia=timezone.now())
        otra = crear_sucursal(empresa=self.empresa, alias='NICK1')
        _pedido(otra, 'G-6')

        response = self._post(api_imprimir_guias_sucursal,
                              '/app/ecommerce/pedidos/imprimir-guias-sucursal/')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['ok'])
        self.assertEqual(data['total'], 2)
        self.assertFalse(data['truncado'])
        ids = {g['pedido_id'] for g in data['guias']}
        self.assertEqual(ids, {asignado.id, en_prep.id})
        for g in data['guias']:
            self.assertTrue(g['print_data']['es_guia'])

        # El ASIGNADO transicionó (imprimir la guía ES el inicio del picking).
        asignado.refresh_from_db()
        self.assertEqual(asignado.sub_estado, 'EN_PREPARACION')
        self.assertIsNotNone(asignado.fecha_impresion_guia)

    def test_reejecutar_no_duplica(self):
        _pedido(self.sucursal, 'G-7')
        self._post(api_imprimir_guias_sucursal, '/x/')
        data = json.loads(self._post(api_imprimir_guias_sucursal, '/x/').content)
        self.assertEqual(data['total'], 0, 'ya impresos: la segunda pasada no reimprime')

    def test_incluir_reimpresiones(self):
        _pedido(self.sucursal, 'G-8', fecha_impresion_guia=timezone.now())
        data = json.loads(self._post(
            api_imprimir_guias_sucursal, '/x/', data={'incluir_reimpresiones': True},
        ).content)
        self.assertEqual(data['total'], 1)

    def test_sin_sucursal_en_sesion_da_400(self):
        request = RequestFactory().post('/x/', data='{}', content_type='application/json')
        request.user = self.user
        request.session = {}
        response = api_imprimir_guias_sucursal(request)
        self.assertEqual(response.status_code, 400)


class ReasignarMismaEmpresaTest(_BaseVistaTest):

    def test_reasignar_dentro_de_la_empresa_ok(self):
        destino = crear_sucursal(empresa=self.empresa, alias='NICK1')
        p = _pedido(self.sucursal, 'R-1')
        response = self._post(api_reasignar_pedido, '/x/', pedido_id=p.id,
                              data={'sucursal_id': destino.id, 'motivo': 'sin stock'})
        self.assertEqual(response.status_code, 200)
        p.refresh_from_db()
        self.assertEqual(p.sucursal_id, destino.id)

    def test_reasignar_a_otra_empresa_bloqueado(self):
        otra_empresa = crear_empresa(nombre='PAOLA SPA', rut='76.222.222-2')
        destino = crear_sucursal(empresa=otra_empresa, alias='PAO1')
        p = _pedido(self.sucursal, 'R-2')
        response = self._post(api_reasignar_pedido, '/x/', pedido_id=p.id,
                              data={'sucursal_id': destino.id})
        self.assertEqual(response.status_code, 400)
        self.assertIn('otra empresa', json.loads(response.content)['error'])
        p.refresh_from_db()
        self.assertEqual(p.sucursal_id, self.sucursal.id, 'no debe moverse')
