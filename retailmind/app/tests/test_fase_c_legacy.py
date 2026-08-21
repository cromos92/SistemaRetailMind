"""
Tests de la Fase C (limpieza P2) de la auditoría de Reportes 2026-08.

1. `anular_ticket_pendiente` (views_modulo_ventas.py): era el ÚNICO writer
   activo que seguía creando movimientos INGRESO con `sucursal_destino=NULL`
   (medido en prod jul-ago 2026: 412 movimientos / 439 u, todos con concepto
   ANULACION_TICKET). Regla canónica: la sucursal receptora de un INGRESO es
   SIEMPRE la dueña del SKU (`ProductoTalla.producto.sucursal`).

2. `reporte_despachos_por_proveedor` (views.py): las métricas muertas
   "despachado" y "saldo restante" (0 EGRESOS sobre DTE de compra en todo el
   histórico) se eliminaron del payload, y la carga sin filtros aplica un
   rango por defecto de 90 días declarado en `filtros_aplicados`.

Correr:
    $env:DATABASE_URL="sqlite:///C:/temp/tc2.sqlite3"
    python manage.py test app.tests.test_fase_c_legacy
"""
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, Movimientos_Producto, Ticket, Ticket_Productos,
)
from .factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla,
    crear_sucursal, crear_usuario, crear_vendedor,
)


def _patch_permisos():
    """El middleware de permisos se fuerza a True: acá se prueba la vista."""
    return mock.patch(
        'app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True
    )


class AnulacionTicketDestinoTest(TestCase):
    """El INGRESO por anulación de ticket nace con `sucursal_destino` poblado."""

    URL = '/app/api/tickets/anular/'

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa, alias='TIENDA')
        self.user = crear_usuario(rol='administrador')
        crear_empresa_user(self.user, self.empresa, self.sucursal)
        self.vendedor = crear_vendedor(empresa=self.empresa)

        self.producto, self.talla = crear_producto_con_talla(
            self.sucursal, articulo='ZAP-1', sku=1000001, stock=5,
        )

        self.client = Client()
        self.client.force_login(self.user)
        sesion = self.client.session
        sesion['idSucursalActual'] = self.sucursal.id
        sesion.save()

    def _crear_ticket_pendiente(self, correlativo=101, talla=None, cantidad=2):
        ticket = Ticket.objects.create(
            vendedor=self.vendedor,
            sucursal=self.sucursal,
            correlativo=correlativo,
            estado='PENDIENTE',
            subTotal=40000,
            total=40000,
            responsable='tester',
        )
        Ticket_Productos.objects.create(
            idTicket=ticket,
            ProductoTalla=talla or self.talla,
            stock=cantidad,
            precio=20000,
            subtotal=20000 * cantidad,
        )
        return ticket

    def _anular(self, correlativo):
        with _patch_permisos():
            return self.client.post(
                self.URL,
                data=json.dumps({'correlativo': correlativo, 'motivo': 'test'}),
                content_type='application/json',
            )

    def test_movimiento_de_anulacion_nace_con_destino(self):
        ticket = self._crear_ticket_pendiente(correlativo=101)
        resp = self._anular(101)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get('success'), resp.json())

        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, 'ANULADO')

        mov = Movimientos_Producto.objects.get(concepto='ANULACION_TICKET')
        self.assertEqual(mov.tipo_movimiento, 'INGRESO')
        self.assertEqual(mov.cantidad, 2)
        self.assertIsNotNone(mov.sucursal_destino_id)
        self.assertEqual(mov.sucursal_destino_id, self.sucursal.id)

    def test_destino_es_la_suc_duena_del_sku_no_la_de_sesion(self):
        """Regla canónica: manda la dueña del SKU, no la sucursal de sesión."""
        otra_sucursal = crear_sucursal(self.empresa, alias='BODEGA')
        _, talla_ajena = crear_producto_con_talla(
            otra_sucursal, articulo='ZAP-2', sku=2000001, stock=5,
        )
        self._crear_ticket_pendiente(correlativo=202, talla=talla_ajena)

        resp = self._anular(202)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get('success'), resp.json())

        mov = Movimientos_Producto.objects.get(
            concepto='ANULACION_TICKET', ProductoTalla=talla_ajena)
        self.assertEqual(mov.sucursal_destino_id, otra_sucursal.id)


class DespachosProveedorLegacyTest(TestCase):
    """El reporte legacy queda como INGRESOS por proveedor, en bulk y acotado."""

    URL = '/app/reporte_despachos_por_proveedor/'

    CAMPOS_MUERTOS_FILA = (
        'total_despachado', 'saldo_restante', 'monto_despachado', 'monto_restante',
    )

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa, alias='TIENDA')
        self.proveedor = crear_empresa(
            nombre='Proveedor X', rut='78.333.333-3', esProveedor=True)

        self.user = crear_usuario(rol='administrador')
        crear_empresa_user(self.user, self.empresa, self.sucursal)

        self.producto, self.talla = crear_producto_con_talla(
            self.sucursal, articulo='ZAP-1', sku=1000001, stock=50,
        )

        hoy = timezone.localdate()
        self.dte_reciente = self._crear_dte(numero=1001, fecha=hoy, unidades=10)
        Dte_Productos.objects.create(
            dte=self.dte_reciente, productoTalla=self.talla, descripcion='ZAP-1',
            costo=1000, sobreprecio=0, precio=2000, stock=10, activo=True,
        )
        # 2 ingresos (4 + 3 = 7 u / $7.000) + 1 egreso colgado del DTE que NO
        # debe aparecer en ninguna métrica (la métrica "despachado" murió).
        self._mov(self.dte_reciente, 4, fecha=hoy)
        self._mov(self.dte_reciente, 3, fecha=hoy)
        self._mov(self.dte_reciente, -5, fecha=hoy)

        # DTE fuera de la ventana default de 90 días.
        self.dte_viejo = self._crear_dte(
            numero=7777, fecha=hoy - timedelta(days=200), unidades=6)

        self.client = Client()
        self.client.force_login(self.user)
        sesion = self.client.session
        sesion['idSucursalActual'] = self.sucursal.id
        sesion.save()

    def _crear_dte(self, numero, fecha, unidades):
        return Dte.objects.create(
            emisor=self.proveedor,
            receptor=self.empresa,
            numero_documento=numero,
            tipo_documento='FACTURA',
            monto_neto=Decimal(unidades * 1000),
            monto_con_iva=Decimal(unidades * 1190),
            estado_pago='PENDIENTE',
            estado_dte='ACEPTADO',
            responsable='tester',
            fecha_emision=fecha,
            fecha_vencimiento=fecha,
            diasCredito=0,
            bultos=1,
            unidades_productos=unidades,
            tipo_transaccion='COMPRA',
            sucursal=self.sucursal,
        )

    def _mov(self, dte, cantidad, fecha):
        return Movimientos_Producto.objects.create(
            dte=dte,
            ProductoTalla=self.talla,
            sucursal_origen=self.sucursal,
            sucursal_destino=self.sucursal,
            cantidad=cantidad,
            costo=1000,
            precio=2000,
            concepto='INGRESO_MANUAL' if cantidad > 0 else 'VENTA_PUBLICO',
            estado='COMPLETADO',
            fecha=fecha,
        )

    def _json(self, **params):
        with _patch_permisos():
            resp = self.client.get(self.URL, params)
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_sin_filtros_aplica_default_90_dias_y_lo_declara(self):
        data = self._json()
        self.assertTrue(data['success'])
        self.assertTrue(data['filtros_aplicados']['fecha_defecto_aplicada'])
        numeros = {f['dte_numero'] for f in data['data']}
        self.assertEqual(numeros, {1001})
        self.assertEqual(data['resumen']['total_dtes'], 1)

    def test_metricas_muertas_fuera_del_payload(self):
        data = self._json()
        fila = data['data'][0]
        for campo in self.CAMPOS_MUERTOS_FILA:
            self.assertNotIn(campo, fila)
        self.assertNotIn('total_unidades_despachadas', data['resumen'])

    def test_totales_de_ingreso_cuadran_y_el_egreso_no_contamina(self):
        data = self._json()
        fila = data['data'][0]
        self.assertEqual(fila['total_ingresado'], 7)
        self.assertEqual(fila['unidades_en_dte'], 10)
        self.assertEqual(fila['unidades_pendientes_ingreso'], 3)
        self.assertEqual(fila['monto_ingresado'], 7000.0)
        self.assertEqual(data['resumen']['total_unidades_ingresadas'], 7)
        self.assertEqual(data['resumen']['total_monto_compras'], 7000.0)
        self.assertEqual(data['resumen']['total_unidades_pendientes'], 3)

    def test_con_fechas_explicitas_no_hay_default(self):
        hoy = timezone.localdate()
        data = self._json(
            fecha_inicio=(hoy - timedelta(days=365)).isoformat(),
            fecha_fin=hoy.isoformat(),
        )
        self.assertFalse(data['filtros_aplicados']['fecha_defecto_aplicada'])
        numeros = {f['dte_numero'] for f in data['data']}
        self.assertEqual(numeros, {1001, 7777})
        self.assertEqual(data['resumen']['total_dtes'], 2)

    def test_buscar_por_numero_dte_recorre_todo_el_historico(self):
        """Un número puntual no puede quedar escondido por la ventana default."""
        data = self._json(dte_numero='7777')
        self.assertFalse(data['filtros_aplicados']['fecha_defecto_aplicada'])
        numeros = {f['dte_numero'] for f in data['data']}
        self.assertEqual(numeros, {7777})
