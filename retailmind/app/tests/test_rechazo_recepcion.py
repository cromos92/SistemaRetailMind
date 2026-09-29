"""
Rechazo de recepción de traspasos (auditoría 29-09-2026, key "recepcion"):

- N-03: motivo > 100 caracteres → 400 sin tocar nada (antes DataError → 500).
- Rechazo: stock y lote del origen +N, TRASPASO_SALIDA → CANCELADO con la
  marca JSON del evento, y comprobante PDF (R-04/R-05) descargable por origen
  y destino (403 para terceros), también después de cancelar.
- R-03: cancelar crea el lote de reposición sin FK al movimiento.
- R-07: la guía con faltante auto-devuelto NO dispara "Regularización
  requerida".
"""
import json
import tempfile
from decimal import Decimal
from unittest import mock

from django.db.models import Sum
from django.test import TestCase, Client

from app.models import (
    Dte, Dte_Productos, Producto_Talla, Movimientos_Producto, LoteProducto,
    NotificacionDTE,
)
from app.services.inventario_service import consumir_lotes_fifo
from app.services.pdf_comprobante_rechazo import MARCA_RECHAZO_JSON
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla, crear_correlativo, crear_lote_fifo,
)


def _permisos():
    """`PermisoRol.tiene_permiso` es un classmethod: parchearlo por cualquiera
    de sus rutas de import cubre decoradores, middleware y helpers."""
    return mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True)


class BaseRechazoTest(TestCase):
    """Origen con stock y LOTES cuadrados (stock == SUM(lotes)); el helper
    `_traspaso` replica emitir_dte: líneas, TRASPASO_SALIDA COMPLETADO con la
    fecha del despacho, stock -N y consumo FIFO."""
    SKU = 96001
    STOCK_ORIGEN = 18
    FECHA_DESPACHO = '2026-09-01'

    def setUp(self):
        self.user = crear_usuario(username='rch_admin', rol='administrador')
        self.empresa = crear_empresa()
        self.origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.origen)
        crear_correlativo(self.origen, tipo_dte='AJUSTE TRASPASO')
        crear_correlativo(self.origen, tipo_dte='AJUSTE TRASPASO POST')
        crear_correlativo(self.origen, tipo_dte='NOTA DE CREDITO')

        self.prod_origen, self.t_origen = crear_producto_con_talla(
            self.origen, articulo='Zap Rechazo', talla='M', sku=self.SKU,
            stock=self.STOCK_ORIGEN, costo=100)
        crear_lote_fifo(self.t_origen, cantidad=self.STOCK_ORIGEN, costo_unitario=100)
        _, self.t_destino = crear_producto_con_talla(
            self.destino, articulo='Zap Rechazo', talla='M', sku=self.SKU, stock=0, costo=100)

        self.client = Client()
        self.client.force_login(self.user)
        self._folio = 80000

    # ── helpers ───────────────────────────────────────────────────────────
    def _traspaso(self, lineas=None, tipo_documento='GUIA', estado='EMITIDO'):
        lineas = lineas or [(self.t_origen, 3)]
        self._folio += 1
        total = sum(c for _, c in lineas)
        dte = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=self._folio, tipo_documento=tipo_documento,
            monto_neto=Decimal(total * 1000), monto_con_iva=Decimal(total * 1190),
            estado_pago='PENDIENTE', estado_dte=estado, responsable='tester',
            fecha_emision=self.FECHA_DESPACHO, fecha_vencimiento=self.FECHA_DESPACHO,
            diasCredito=0, bultos=1, unidades_productos=total,
            tipo_transaccion='TRASPASO', sucursal=self.origen,
        )
        dps = []
        for talla, cant in lineas:
            dps.append(Dte_Productos.objects.create(
                dte=dte, productoTalla=talla,
                descripcion=f'{talla.producto.articulo} - Talla {talla.talla}',
                costo=100, sobreprecio=0, precio=1000, stock=cant, activo=True,
            ))
            Movimientos_Producto.objects.create(
                dte=dte, ProductoTalla=talla,
                sucursal_origen=self.origen, sucursal_destino=self.destino,
                cantidad=-cant, costo=100, precio=1000, concepto='TRASPASO_SALIDA',
                tipo_movimiento='EGRESO', estado='COMPLETADO', responsable='tester',
                fecha=self.FECHA_DESPACHO, hora='10:00:00',
            )
            Producto_Talla.objects.filter(id=talla.id).update(
                stock=Producto_Talla.objects.get(id=talla.id).stock - cant)
            consumir_lotes_fifo(talla, cant, usar_lock=False)
        return dte, dps

    def _sesion(self, sucursal, client=None):
        c = client or self.client
        s = c.session
        s['idSucursalActual'] = sucursal.id
        s['idEmpresaActual'] = sucursal.empresa_id
        s['alias'] = sucursal.alias
        s.save()

    def _post(self, url, payload, client=None):
        with _permisos(), self.settings(MEDIA_ROOT=tempfile.mkdtemp()):
            return (client or self.client).post(
                url, data=json.dumps(payload), content_type='application/json')

    def _get(self, url, client=None):
        with _permisos():
            return (client or self.client).get(url)

    def _rechazar(self, dte, motivo='No llegó la mercadería'):
        self._sesion(self.destino)
        return self._post('/app/dte/rechazar_recepcion/',
                          {'dte_id': dte.id, 'motivo_rechazo': motivo})

    def _cancelar(self, dte, motivo='Emitido por error'):
        self._sesion(self.origen)
        return self._post('/app/dte/cancelar_traspaso/', {'dte_id': dte.id, 'motivo': motivo})

    def _confirmar(self, dte, productos):
        self._sesion(self.destino)
        return self._post('/app/dte/confirmar_recepcion/',
                          {'dte_id': dte.id, 'productos': productos})

    def _linea_payload(self, dp, recibida=None, estado='RECEPCIONADO_OK'):
        return {
            'dte_producto_id': dp.id,
            'cantidad_esperada': dp.stock,
            'cantidad_recepcionada': dp.stock if recibida is None else recibida,
            'cantidad_danada': 0, 'cantidad_sobrante': 0,
            'estado': estado, 'observaciones': '',
        }

    @staticmethod
    def _stock(talla):
        return Producto_Talla.objects.get(id=talla.id).stock

    @staticmethod
    def _lotes(talla):
        return LoteProducto.objects.filter(
            producto_talla_id=talla.id, activo=True, agotado=False,
        ).aggregate(t=Sum('cantidad_disponible'))['t'] or 0

    @staticmethod
    def _kardex_completado(talla):
        return Movimientos_Producto.objects.filter(
            ProductoTalla_id=talla.id, estado='COMPLETADO',
        ).aggregate(t=Sum('cantidad'))['t'] or 0

    def assert_sincronizado(self, talla):
        """Regla del proyecto: stock plano == SUM(lotes disponibles)."""
        self.assertEqual(self._stock(talla), self._lotes(talla),
                         f'stock {self._stock(talla)} != lotes {self._lotes(talla)} (talla {talla.id})')

    @staticmethod
    def _salidas(dte):
        return Movimientos_Producto.objects.filter(dte=dte, concepto='TRASPASO_SALIDA').order_by('id')


class RechazoStockYComprobanteTest(BaseRechazoTest):

    def test_rechazo_devuelve_stock_y_lote_y_marca_la_salida(self):
        dte, (dp,) = self._traspaso()
        self.assertEqual(self._stock(self.t_origen), self.STOCK_ORIGEN - 3)
        self.assert_sincronizado(self.t_origen)
        kardex0 = self._kardex_completado(self.t_origen)

        resp = self._rechazar(dte, motivo='Caja rota')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data['unidades_devueltas'], 3)
        self.assertEqual(data['comprobante_url'], f'/app/dte/{dte.id}/comprobante-rechazo/')
        self.assertEqual(data['rechazado_por'], 'rch_admin')

        # Origen: stock y lotes vuelven; el kardex COMPLETADO cuadra solo
        # (la salida sale del set, no se escribe ingreso de contrapartida).
        self.assertEqual(self._stock(self.t_origen), self.STOCK_ORIGEN)
        self.assert_sincronizado(self.t_origen)
        self.assertEqual(self._kardex_completado(self.t_origen), kardex0 + 3)
        self.assertEqual(self._stock(self.t_destino), 0)

        salida = self._salidas(dte).get()
        self.assertEqual(salida.estado, 'CANCELADO')
        self.assertEqual(salida.cantidad, -3)   # la cantidad original es evidencia
        self.assertIn(MARCA_RECHAZO_JSON, salida.observaciones)
        marca = json.loads(salida.observaciones.split(MARCA_RECHAZO_JSON)[-1].strip())
        self.assertEqual(marca['usuario'], 'rch_admin')
        self.assertEqual(marca['motivo'], 'Caja rota')
        self.assertEqual(marca['cantidad'], 3)
        lote = LoteProducto.objects.get(id=marca['lote_id'])
        self.assertEqual(lote.cantidad_disponible, 3)
        self.assertIsNone(lote.movimiento_id)
        self.assertEqual(str(lote.fecha_ingreso.date()), self.FECHA_DESPACHO)

    def test_motivo_de_mas_de_100_caracteres_responde_400_sin_cambios(self):
        dte, _ = self._traspaso()
        resp = self._rechazar(dte, motivo='M' * 101)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('100', resp.json()['error'])
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'EMITIDO')
        self.assertEqual(self._stock(self.t_origen), self.STOCK_ORIGEN - 3)
        self.assertEqual(self._salidas(dte).get().estado, 'COMPLETADO')
        # Con 100 exactos sí pasa.
        resp = self._rechazar(dte, motivo='M' * 100)
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_comprobante_pdf_para_destino_y_origen_y_403_para_terceros(self):
        dte, _ = self._traspaso()
        self.assertEqual(self._rechazar(dte).status_code, 200)
        url = f'/app/dte/{dte.id}/comprobante-rechazo/'

        # Destino (quien rechazó).
        self._sesion(self.destino)
        resp = self._get(url)
        self.assertEqual(resp.status_code, 200, resp.content[:200])
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertTrue(resp.content.startswith(b'%PDF'))
        self.assertIn('comprobante_rechazo', resp['Content-Disposition'])

        # Origen (recibe la mercadería de vuelta).
        self._sesion(self.origen)
        self.assertEqual(self._get(url).status_code, 200)

        # Un vendedor de una tercera sucursal, con la pantalla habilitada, no.
        otra = crear_sucursal(self.empresa, alias='OTRA')
        vendedor = crear_usuario(username='rch_vend', rol='vendedor')
        crear_empresa_user(vendedor, self.empresa, otra)
        cv = Client()
        cv.force_login(vendedor)
        self._sesion(otra, cv)
        self.assertEqual(self._get(url, cv).status_code, 403)

        # Un traspaso sin rechazo no tiene comprobante.
        dte2, _ = self._traspaso()
        self._sesion(self.origen)
        self.assertEqual(self._get(f'/app/dte/{dte2.id}/comprobante-rechazo/').status_code, 404)

    def test_comprobante_sobrevive_a_la_cancelacion_posterior(self):
        """Cancelar pone motivo_rechazo=None y la señal borra la
        NotificacionDTE: el comprobante sale igual desde la marca JSON."""
        dte, _ = self._traspaso()
        self.assertEqual(self._rechazar(dte, motivo='Sin espacio').status_code, 200)
        resp = self._cancelar(dte)
        self.assertEqual(resp.status_code, 200, resp.content)
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'CANCELADO')
        self.assertIsNone(dte.motivo_rechazo)
        self.assertEqual(self._stock(self.t_origen), self.STOCK_ORIGEN)  # sin doble crédito
        self.assert_sincronizado(self.t_origen)

        from app.services.pdf_comprobante_rechazo import payload_comprobante_rechazo
        payload = payload_comprobante_rechazo(dte, self.destino, impreso_por='x')
        self.assertIsNotNone(payload)
        self.assertEqual(payload['rechazo']['motivo'], 'Sin espacio')
        self.assertEqual(payload['rechazo']['usuario'], 'rch_admin')
        self.assertEqual(payload['unidades_devueltas'], 3)
        self.assertEqual(payload['lineas'][0]['sku'], self.SKU)
        self.assertEqual(payload['lineas'][0]['talla'], 'M')
        self.assertEqual(payload['destino']['alias'], 'DESTINO')

        self._sesion(self.origen)
        self.assertEqual(self._get(f'/app/dte/{dte.id}/comprobante-rechazo/').status_code, 200)


class CancelarLoteSinFkTest(BaseRechazoTest):

    def test_cancelar_crea_lote_sin_movimiento_y_borrar_la_salida_no_lo_arrastra(self):
        dte, _ = self._traspaso()
        resp = self._cancelar(dte)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.t_origen), self.STOCK_ORIGEN)
        self.assert_sincronizado(self.t_origen)
        lote = LoteProducto.objects.get(
            producto_talla=self.t_origen, observaciones__icontains='cancelación')
        self.assertIsNone(lote.movimiento_id)
        # Aunque alguien borrara la fila de kardex, el lote sobrevive.
        self._salidas(dte).delete()
        self.assertTrue(LoteProducto.objects.filter(id=lote.id).exists())
        self.assert_sincronizado(self.t_origen)


class NotificacionRegularizacionTest(BaseRechazoTest):

    def test_guia_parcial_auto_devuelta_no_pide_regularizacion(self):
        dte, (dp,) = self._traspaso([(self.t_origen, 5)], tipo_documento='GUIA')
        resp = self._confirmar(dte, [self._linea_payload(dp, recibida=3, estado='FALTANTE')])
        self.assertEqual(resp.status_code, 200, resp.content)
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'RECEPCIONADO_COMPLETO')
        # El faltante volvió solo al origen: 18 - 5 + 2, con su lote.
        self.assertEqual(self._stock(self.t_origen), self.STOCK_ORIGEN - 3)
        self.assert_sincronizado(self.t_origen)
        self.assertFalse(NotificacionDTE.objects.filter(
            dte=dte, tipo='REGULARIZACION_REQUERIDA').exists())

    def test_factura_parcial_si_pide_regularizacion(self):
        dte, (dp,) = self._traspaso([(self.t_origen, 5)], tipo_documento='FACTURA ELECTRONICA')
        resp = self._confirmar(dte, [self._linea_payload(dp, recibida=3, estado='FALTANTE')])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(NotificacionDTE.objects.filter(
            dte=dte, tipo='REGULARIZACION_REQUERIDA').exists())
