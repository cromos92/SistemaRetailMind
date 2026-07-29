"""
Tests del endpoint `reasignar_destino_traspaso_api` (gestion-dte):
cambiar la sucursal DESTINO de un traspaso EMITIDO antes de la recepción.

Cubre:
1. Reasignación OK: movimientos TRASPASO_SALIDA apuntan al nuevo destino,
   queda movimiento documental REASIGNACION_DESTINO (cantidad 0) y nota
   en Dte.referencias.
2. Recepción post-reasignación: el nuevo destino puede confirmar (stock
   entra a su sucursal activa) y el destino anterior recibe 403.
3. La lista de recepciones pendientes del nuevo destino incluye el DTE
   con el aviso `reasignado_desde`.
4. Bloqueos: ya recepcionado, cross-empresa (otro RUT receptor), sin
   permiso, sesión no emisora, sin motivo, mismo destino.
"""
import json
from decimal import Decimal
from unittest import mock

from django.test import TestCase, Client

from app.models import (
    Dte, Dte_Productos, Producto_Talla, Movimientos_Producto,
)
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla,
)

URL_REASIGNAR = '/app/dte/reasignar_destino_traspaso/'
URL_CONFIRMAR = '/app/dte/confirmar_recepcion/'
URL_PENDIENTES = '/app/dte/recepciones_pendientes/'


def _patch_permiso(valor=True):
    return mock.patch('app.views.PermisoRol.tiene_permiso', return_value=valor)


def _patch_permiso_decorators(valor=True):
    return mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=valor)


def _crear_traspaso(sucursal_origen, sucursal_destino, talla_origen,
                    cantidad=5, numero=2000):
    """Simula lo que hace emitir_dte para un traspaso interno."""
    dte = Dte.objects.create(
        emisor=sucursal_origen.empresa,
        receptor=sucursal_destino.empresa,
        numero_documento=numero,
        tipo_documento='GUIA',
        monto_neto=Decimal(cantidad * 1000),
        monto_con_iva=Decimal(cantidad * 1190),
        estado_pago='PENDIENTE',
        estado_dte='EMITIDO',
        responsable='tester',
        fecha_emision='2026-07-01',
        fecha_vencimiento='2026-07-01',
        diasCredito=0,
        bultos=1,
        unidades_productos=cantidad,
        tipo_transaccion='TRASPASO',
        sucursal=sucursal_origen,
    )
    dp = Dte_Productos.objects.create(
        dte=dte,
        productoTalla=talla_origen,
        descripcion='Producto Test',
        costo=100,
        sobreprecio=0,
        precio=1000,
        stock=cantidad,
        activo=True,
    )
    Movimientos_Producto.objects.create(
        dte=dte,
        ProductoTalla=talla_origen,
        sucursal_origen=sucursal_origen,
        sucursal_destino=sucursal_destino,
        cantidad=-cantidad,
        concepto='TRASPASO_SALIDA',
        tipo_movimiento='EGRESO',
        estado='COMPLETADO',
        responsable='tester',
    )
    Producto_Talla.objects.filter(id=talla_origen.id).update(
        stock=talla_origen.stock - cantidad
    )
    return dte, dp


class ReasignarDestinoTraspasoTest(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.otra_empresa = crear_empresa(nombre='Otra Empresa', rut='77.000.000-0')

        self.origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.destino_a = crear_sucursal(self.empresa, alias='PAO2')
        self.destino_b = crear_sucursal(self.empresa, alias='PAO4')
        self.destino_otra = crear_sucursal(self.otra_empresa, alias='AJENA')
        crear_empresa_user(self.user, self.empresa, self.origen)

        _, self.talla_origen = crear_producto_con_talla(
            self.origen, articulo='Zap Test', sku=1001, stock=10,
        )

        self.client = Client()
        self.client.force_login(self.user)
        self._set_sucursal(self.origen)

    def _set_sucursal(self, sucursal):
        session = self.client.session
        session['idSucursalActual'] = sucursal.id
        session['idEmpresaActual'] = sucursal.empresa_id
        session.save()

    def _reasignar(self, dte, nueva_sucursal, motivo='Era para PAO4'):
        return self.client.post(
            URL_REASIGNAR,
            data=json.dumps({
                'dte_id': dte.id,
                'nueva_sucursal_id': nueva_sucursal.id,
                'motivo': motivo,
            }),
            content_type='application/json',
        )

    # ------------------------------------------------------------------
    # Caso feliz
    # ------------------------------------------------------------------
    def test_reasignar_ok_actualiza_movimientos_y_deja_rastro(self):
        dte, _ = _crear_traspaso(self.origen, self.destino_a, self.talla_origen)

        with _patch_permiso():
            resp = self._reasignar(dte, self.destino_b)

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['destino_anterior']['alias'], 'PAO2')
        self.assertEqual(data['destino_nuevo']['alias'], 'PAO4')

        # Todos los TRASPASO_SALIDA apuntan al nuevo destino
        salidas = Movimientos_Producto.objects.filter(dte=dte, concepto='TRASPASO_SALIDA')
        self.assertTrue(salidas.exists())
        for mov in salidas:
            self.assertEqual(mov.sucursal_destino_id, self.destino_b.id)

        # Movimiento documental de trazabilidad (cantidad 0, viejo → nuevo)
        rastro = Movimientos_Producto.objects.get(dte=dte, concepto='REASIGNACION_DESTINO')
        self.assertEqual(rastro.cantidad, 0)
        self.assertEqual(rastro.sucursal_origen_id, self.destino_a.id)
        self.assertEqual(rastro.sucursal_destino_id, self.destino_b.id)
        self.assertIn('Era para PAO4', rastro.observaciones)

        # Nota en el DTE
        dte.refresh_from_db()
        self.assertIn('DESTINO REASIGNADO PAO2 → PAO4', dte.referencias)

    # ------------------------------------------------------------------
    # Recepción después de reasignar
    # ------------------------------------------------------------------
    def test_nuevo_destino_recepciona_y_stock_entra_a_su_sucursal(self):
        dte, dp = _crear_traspaso(self.origen, self.destino_a, self.talla_origen)
        with _patch_permiso():
            self.assertEqual(self._reasignar(dte, self.destino_b).status_code, 200)

        # Ficha homóloga en el nuevo destino (mismo SKU, stock 0)
        _, talla_b = crear_producto_con_talla(
            self.destino_b, articulo='Zap Test B', sku=1001, stock=0,
        )

        self._set_sucursal(self.destino_b)
        with _patch_permiso(), _patch_permiso_decorators():
            resp = self.client.post(
                URL_CONFIRMAR,
                data=json.dumps({'dte_id': dte.id, 'productos': []}),
                content_type='application/json',
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'RECEPCIONADO_COMPLETO')
        talla_b.refresh_from_db()
        self.assertEqual(talla_b.stock, 5)  # las 5 unidades entraron a PAO4

    def test_destino_anterior_no_puede_recepcionar(self):
        dte, _ = _crear_traspaso(self.origen, self.destino_a, self.talla_origen)
        with _patch_permiso():
            self.assertEqual(self._reasignar(dte, self.destino_b).status_code, 200)

        self._set_sucursal(self.destino_a)
        with _patch_permiso(), _patch_permiso_decorators():
            resp = self.client.post(
                URL_CONFIRMAR,
                data=json.dumps({'dte_id': dte.id, 'productos': []}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_lista_pendientes_nuevo_destino_incluye_aviso(self):
        dte, _ = _crear_traspaso(self.origen, self.destino_a, self.talla_origen)
        with _patch_permiso():
            self.assertEqual(self._reasignar(dte, self.destino_b).status_code, 200)

        self._set_sucursal(self.destino_b)
        with _patch_permiso(), _patch_permiso_decorators():
            resp = self.client.get(URL_PENDIENTES, {'estado': 'EMITIDO'})

        self.assertEqual(resp.status_code, 200, resp.content)
        items = resp.json().get('items') or resp.json().get('resultados') or []
        ids = [i['id'] for i in items]
        self.assertIn(dte.id, ids)
        item = next(i for i in items if i['id'] == dte.id)
        self.assertIsNotNone(item.get('reasignado_desde'))
        self.assertEqual(item['reasignado_desde']['sucursal'], 'PAO2')

    # ------------------------------------------------------------------
    # Bloqueos
    # ------------------------------------------------------------------
    def test_bloqueado_si_ya_recepcionado(self):
        dte, _ = _crear_traspaso(self.origen, self.destino_a, self.talla_origen)
        Dte.objects.filter(id=dte.id).update(estado_dte='RECEPCIONADO_COMPLETO')

        with _patch_permiso():
            resp = self._reasignar(dte, self.destino_b)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('no se puede reasignar', resp.json()['error'])

    def test_bloqueado_cross_empresa(self):
        dte, _ = _crear_traspaso(self.origen, self.destino_a, self.talla_origen)
        with _patch_permiso():
            resp = self._reasignar(dte, self.destino_otra)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('anular y re-emitir', resp.json()['error'])

    def test_bloqueado_sin_permiso(self):
        dte, _ = _crear_traspaso(self.origen, self.destino_a, self.talla_origen)
        with _patch_permiso(valor=False):
            resp = self._reasignar(dte, self.destino_b)
        self.assertEqual(resp.status_code, 403)

    def test_bloqueado_si_no_soy_emisor(self):
        dte, _ = _crear_traspaso(self.origen, self.destino_a, self.talla_origen)
        self._set_sucursal(self.destino_a)
        with _patch_permiso():
            resp = self._reasignar(dte, self.destino_b)
        self.assertEqual(resp.status_code, 403)

    def test_bloqueado_sin_motivo(self):
        dte, _ = _crear_traspaso(self.origen, self.destino_a, self.talla_origen)
        with _patch_permiso():
            resp = self._reasignar(dte, self.destino_b, motivo='  ')
        self.assertEqual(resp.status_code, 400)

    def test_bloqueado_mismo_destino_actual(self):
        dte, _ = _crear_traspaso(self.origen, self.destino_a, self.talla_origen)
        with _patch_permiso():
            resp = self._reasignar(dte, self.destino_a)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('ya es el destino actual', resp.json()['error'])
