"""
Tests del flujo de picking en tienda para pedidos ecommerce.

Plan: docs/PLAN_PICKING_TIENDAS_ECOMMERCE_2026-08-03.md
- Imprimir la guía de preparación registra quién/cuándo y transiciona
  ASIGNADO → EN_PREPARACION (idempotente al reimprimir).
- Las transiciones de sub-estado sellan fecha_inicio_preparacion /
  fecha_listo_despacho SOLO la primera vez.
- La API para AllConnected (consultar + estado-batch) expone sub_estado,
  sucursal y la línea de tiempo completa (retrocompatible: las claves
  originales estado / ticket_id / dte_id se mantienen).

Correr en BD local desechable:
    python manage.py test app.tests.test_picking_ecommerce
"""
import json
from datetime import timedelta

from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from app.models import (
    HistorialPedidoEcommerce, ModuloSistema, OpcionMenu, PedidoEcommerce, PermisoRol,
)
from app.views_ecommerce import (
    api_asignar_ticket_rm,
    api_cambiar_sub_estado,
    api_estado_pedidos_batch,
    api_imprimir_guia_preparacion,
)

from .factories import crear_sucursal, crear_usuario


class _BasePickingTest(TestCase):
    """Entorno mínimo: sucursal + usuario con puede_editar sobre el módulo
    ecommerce + un pedido ASIGNADO con 2 líneas."""

    def setUp(self):
        self.sucursal = crear_sucursal()
        self.user = crear_usuario(rol='administrador')
        modulo = ModuloSistema.objects.create(codigo='ecommerce', nombre='Ecommerce')
        self.opcion = OpcionMenu.objects.create(
            modulo=modulo, codigo='ecommerce_pedidos_todos', nombre='Pedidos Ecommerce',
        )
        PermisoRol.objects.create(
            rol=self.user.rol, opcion_menu=self.opcion,
            puede_ver=True, puede_editar=True,
        )
        self.pedido = PedidoEcommerce.objects.create(
            numero_ticket_rm='RM-PICK0001',
            numero_pedido_canal='SHOP-1',
            canal_origen='SHOPIFY',
            sucursal=self.sucursal,
            cliente_nombre='Cliente Picking',
            sub_estado='ASIGNADO',
            fecha_asignacion=timezone.now(),
            correlativo='PA3000198',
            total=30000,
            items=[
                {'sku': '111', 'nombre': 'Zapatilla A', 'cantidad': 2, 'precio_unitario': 10000},
                {'sku': '222', 'nombre': 'Zapatilla B', 'cantidad': 1, 'precio_unitario': 10000},
            ],
        )

    def _post_json(self, view, url, pedido_id=None, data=None, **extra):
        request = RequestFactory().post(
            url, data=json.dumps(data or {}), content_type='application/json', **extra,
        )
        request.user = self.user
        request.session = {}
        if pedido_id is not None:
            return view(request, pedido_id)
        return view(request)


class ImprimirGuiaTest(_BasePickingTest):
    """Imprimir la guía ES el inicio del picking: registra y transiciona."""

    def _imprimir(self):
        return self._post_json(
            api_imprimir_guia_preparacion,
            f'/app/ecommerce/pedidos/{self.pedido.id}/imprimir-guia/',
            pedido_id=self.pedido.id,
        )

    def test_imprimir_marca_en_preparacion(self):
        response = self._imprimir()
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['ok'])
        self.assertTrue(data['transiciono'])
        self.assertEqual(data['sub_estado'], 'EN_PREPARACION')

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.sub_estado, 'EN_PREPARACION')
        self.assertIsNotNone(self.pedido.fecha_impresion_guia)
        self.assertIsNotNone(self.pedido.fecha_inicio_preparacion)
        self.assertEqual(self.pedido.guia_impresa_por, self.user)

        historial = HistorialPedidoEcommerce.objects.filter(pedido=self.pedido)
        self.assertEqual(historial.count(), 1)
        self.assertIn('Guía', historial.first().motivo)

        # print_data listo para imprimirConQZ en modo guía
        pd = data['print_data']
        self.assertTrue(pd['es_guia'])
        self.assertEqual(pd['modulo_origen'], 'ECOMMERCE')
        self.assertEqual(pd['folio_despacho'], 'PA3000198')
        self.assertEqual(len(pd['productos']), 2)
        self.assertEqual(pd['productos'][0]['sku'], '111')
        self.assertEqual(pd['sucursal']['alias'], self.sucursal.nombre or self.sucursal.alias)

    def test_reimprimir_es_idempotente(self):
        self._imprimir()
        self.pedido.refresh_from_db()
        primera_impresion = self.pedido.fecha_impresion_guia
        primer_inicio = self.pedido.fecha_inicio_preparacion

        data = json.loads(self._imprimir().content)
        self.assertTrue(data['ok'])
        self.assertFalse(data['transiciono'], 'reimprimir no debe volver a transicionar')

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.fecha_impresion_guia, primera_impresion)
        self.assertEqual(self.pedido.fecha_inicio_preparacion, primer_inicio)
        self.assertEqual(
            HistorialPedidoEcommerce.objects.filter(pedido=self.pedido).count(), 1,
            'la reimpresión no debe duplicar el historial',
        )

    def test_pedido_no_pendiente_da_404(self):
        self.pedido.estado = 'FACTURADO'
        self.pedido.save(update_fields=['estado'])
        with self.assertRaises(Http404):
            self._imprimir()

    def test_sin_permiso_da_403(self):
        self.user = crear_usuario(username='vendedor1', rol='vendedor')
        response = self._imprimir()
        self.assertEqual(response.status_code, 403)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.sub_estado, 'ASIGNADO')


class SubEstadoTimestampsTest(_BasePickingTest):
    """api_cambiar_sub_estado sella los timestamps de etapa la PRIMERA vez."""

    def _cambiar(self, nuevo):
        return self._post_json(
            api_cambiar_sub_estado,
            f'/app/ecommerce/pedidos/{self.pedido.id}/sub-estado/',
            pedido_id=self.pedido.id,
            data={'sub_estado': nuevo},
        )

    def test_listo_despacho_sella_fecha(self):
        self.assertEqual(self._cambiar('EN_PREPARACION').status_code, 200)
        self.assertEqual(self._cambiar('LISTO_DESPACHO').status_code, 200)
        self.pedido.refresh_from_db()
        self.assertIsNotNone(self.pedido.fecha_inicio_preparacion)
        self.assertIsNotNone(self.pedido.fecha_listo_despacho)

    def test_retroceder_y_avanzar_no_pisa_el_primer_sello(self):
        self._cambiar('EN_PREPARACION')
        self._cambiar('LISTO_DESPACHO')
        self.pedido.refresh_from_db()
        primer_listo = self.pedido.fecha_listo_despacho

        self._cambiar('EN_PREPARACION')   # retroceso permitido
        self._cambiar('LISTO_DESPACHO')   # segundo avance
        self.pedido.refresh_from_db()
        self.assertEqual(
            self.pedido.fecha_listo_despacho, primer_listo,
            'el segundo LISTO_DESPACHO no debe pisar la medición original',
        )

    def test_transicion_invalida_da_400(self):
        # ASIGNADO no puede saltar directo a LISTO_DESPACHO
        response = self._cambiar('LISTO_DESPACHO')
        self.assertEqual(response.status_code, 400)
        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.sub_estado, 'ASIGNADO')


@override_settings(RETAILMIND_API_KEY='test-key')
class ApiAllConnectedTest(_BasePickingTest):
    """Los endpoints que consume AllConnected exponen el tracking completo."""

    def _batch(self, tickets, key='test-key'):
        extra = {'HTTP_X_RETAILMIND_KEY': key} if key else {}
        request = RequestFactory().post(
            '/app/api/ecommerce/pedidos/estado-batch/',
            data=json.dumps({'tickets': tickets}),
            content_type='application/json',
            **extra,
        )
        return api_estado_pedidos_batch(request)

    def test_batch_sin_key_da_401(self):
        self.assertEqual(self._batch(['RM-PICK0001'], key=None).status_code, 401)

    def test_batch_devuelve_tracking(self):
        response = self._batch(['RM-PICK0001', 'RM-NOEXISTE'])
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['ok'])
        self.assertEqual(data['no_encontrados'], ['RM-NOEXISTE'])

        t = data['pedidos']['RM-PICK0001']
        self.assertEqual(t['estado'], 'PENDIENTE')
        self.assertEqual(t['sub_estado'], 'ASIGNADO')
        self.assertEqual(t['sucursal']['id'], self.sucursal.id)
        self.assertIsNotNone(t['fechas']['asignacion'])
        self.assertIsNone(t['fechas']['listo_despacho'])

    def test_batch_tope_300_da_400(self):
        response = self._batch([f'RM-X{i}' for i in range(301)])
        self.assertEqual(response.status_code, 400)

    def test_consultar_mantiene_claves_viejas_y_suma_tracking(self):
        request = RequestFactory().get(
            '/app/api/ecommerce/pedidos/consultar/',
            {'numero_ticket_rm': 'RM-PICK0001'},
            HTTP_X_RETAILMIND_KEY='test-key',
        )
        response = api_asignar_ticket_rm(request)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        # Claves originales (el cliente viejo de AC sigue funcionando)
        for clave in ('estado', 'canal_origen', 'cliente_nombre', 'total', 'ticket_id', 'dte_id'):
            self.assertIn(clave, data)
        # Claves nuevas del tracking
        self.assertEqual(data['sub_estado'], 'ASIGNADO')
        self.assertIn('fechas', data)
        self.assertIn('impresion_guia', data['fechas'])


class DuracionesEtapaTest(TestCase):
    """Propiedades minutos_reaccion / minutos_picking / minutos_espera_factura."""

    def _pedido(self, **kwargs):
        sucursal = crear_sucursal(alias=f"S-{kwargs.pop('alias', 'DUR')}")
        base = timezone.now()
        defaults = dict(
            numero_ticket_rm=f"RM-DUR{PedidoEcommerce.objects.count()}",
            numero_pedido_canal=f"C-{PedidoEcommerce.objects.count()}",
            canal_origen='SHOPIFY',
            sucursal=sucursal,
            cliente_nombre='Cliente',
        )
        defaults.update(kwargs)
        return PedidoEcommerce.objects.create(**defaults), base

    def test_duraciones_completas(self):
        base = timezone.now()
        pedido, _ = self._pedido(
            fecha_asignacion=base,
            fecha_impresion_guia=base + timedelta(minutes=30),
            fecha_inicio_preparacion=base + timedelta(minutes=30),
            fecha_listo_despacho=base + timedelta(minutes=75),
            fecha_facturacion=base + timedelta(minutes=90),
        )
        self.assertEqual(pedido.minutos_reaccion, 30)
        self.assertEqual(pedido.minutos_picking, 45)
        self.assertEqual(pedido.minutos_espera_factura, 15)

    def test_etapas_faltantes_dan_none(self):
        pedido, _ = self._pedido(alias='N')
        self.assertIsNone(pedido.minutos_reaccion)
        self.assertIsNone(pedido.minutos_picking)
        self.assertIsNone(pedido.minutos_espera_factura)

    def test_orden_invertido_da_none(self):
        base = timezone.now()
        pedido, _ = self._pedido(
            alias='I',
            fecha_asignacion=base,
            fecha_impresion_guia=base - timedelta(minutes=5),
        )
        self.assertIsNone(pedido.minutos_reaccion)
