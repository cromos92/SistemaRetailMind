"""
NC / ajuste / cambio de talla / limbo sobre traspasos (auditoría 29-09-2026,
key "recepcion"): el kardex nunca se borra, los lotes acompañan al stock y el
DTE cierra CANCELADO cuando ya no quedan unidades.

- R-02a: NC por línea PRE-recepción → sin DEVOLUCION_NC (+N fantasma), stock
  y lotes del origen +q, TRASPASO_SALIDA CANCELADO/reducido, DTE CANCELADO a 0 u.
- N-02: NC TOTAL sin líneas pre-recepción devuelve el stock al origen.
- R-08: NC total sobre RECHAZADO cierra el DTE en CANCELADO.
- R-03: NC parcial por línea sobre CANCELADO/RECHAZADO devuelto → 409; ajustar
  a 0 y cambio de talla + ajuste no borran kardex ni pierden lotes.
- R-01: limbo_dte (devolución física confirmada / absorber) mueve lotes.
- N-05: los consumidores de TRASPASO_SALIDA ignoran las filas CANCELADO.
"""
import json

from django.test import RequestFactory
from django.utils import timezone

from app.models import (
    Dte, Movimientos_Producto, LoteProducto, Producto_Talla,
)
from .factories import crear_lote_fifo
from .test_rechazo_recepcion import BaseRechazoTest


class _BaseNc(BaseRechazoTest):

    def _nc(self, dte, productos_afectados=None, motivo='NC test'):
        """NC desde Consulta Documentos: por línea (productos_afectados) o
        total sin líneas (razón 1)."""
        self._sesion(self.origen)
        body = {
            'dte_id': dte.id, 'tipo_anulacion': 'ANULACION',
            'metodo_devolucion': 'NO_AFECTA_CAJA', 'motivo': motivo, 'return_json': True,
        }
        if productos_afectados is not None:
            body['productos_afectados'] = productos_afectados
        return self._post('/app/documentos/anular-factura/', body)

    def _ajustar(self, dte, dp, nueva_cantidad, **extra):
        self._sesion(self.origen)
        payload = {'dte_id': dte.id, 'motivo': 'ajuste test',
                   'ajustes': [{'dte_producto_id': dp.id, 'nueva_cantidad': nueva_cantidad}]}
        payload.update(extra)
        return self._post('/app/dte/ajustar_traspaso/', payload)

    def _en_emitidos_pendientes(self, dte):
        self._sesion(self.origen)
        resp = self._get('/app/dte/emitidos_pendientes/?estado=EMITIDO')
        self.assertEqual(resp.status_code, 200, resp.content[:200])
        return any(i.get('id') == dte.id for i in (resp.json().get('items') or []))

    @staticmethod
    def _devolucion_nc(dte):
        return Movimientos_Producto.objects.filter(
            dte__documento_afectado=dte, concepto='DEVOLUCION_NC').count()


class NcPreRecepcionPorLineaTest(_BaseNc):

    def test_nc_por_linea_total_repone_sin_devolucion_nc_y_cierra_el_dte(self):
        dte, (dp,) = self._traspaso(tipo_documento='FACTURA ELECTRONICA')
        kardex0 = self._kardex_completado(self.t_origen)
        stock0, lotes0 = self._stock(self.t_origen), self._lotes(self.t_origen)
        n_movs = Movimientos_Producto.objects.filter(dte=dte).count()

        resp = self._nc(dte, [{'dte_producto_id': dp.id, 'cantidad': 3}])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['lineas_sin_reversa_stock'], [])

        # Stock y lotes vuelven; el kardex COMPLETADO cuadra sin +N fantasma.
        self.assertEqual(self._stock(self.t_origen), stock0 + 3)
        self.assertEqual(self._lotes(self.t_origen), lotes0 + 3)
        self.assert_sincronizado(self.t_origen)
        self.assertEqual(self._kardex_completado(self.t_origen), kardex0 + 3)
        self.assertEqual(self._devolucion_nc(dte), 0)

        # La salida no se borró: CANCELADO con su cantidad original.
        self.assertEqual(Movimientos_Producto.objects.filter(dte=dte).count(), n_movs)
        salida = self._salidas(dte).get()
        self.assertEqual(salida.estado, 'CANCELADO')
        self.assertEqual(salida.cantidad, -3)

        dte.refresh_from_db()
        dp.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'CANCELADO')
        self.assertEqual(dte.unidades_productos, 0)
        self.assertFalse(dp.activo)
        nc = Dte.objects.get(documento_afectado=dte, es_nota_credito=True)
        self.assertTrue(nc.redujo_lineas_documento)
        self.assertFalse(self._en_emitidos_pendientes(dte))

        # Ya no se puede recepcionar ni rechazar; el stock no se mueve.
        resp = self._confirmar(dte, [self._linea_payload(dp)])
        self.assertIn(resp.status_code, (400, 409), resp.content)
        self.assertIn(self._rechazar(dte).status_code, (400, 409))
        self.assertEqual(self._stock(self.t_destino), 0)
        self.assertEqual(self._stock(self.t_origen), stock0 + 3)

    def test_nc_parcial_por_linea_reduce_la_salida_y_el_rechazo_devuelve_el_resto(self):
        dte, (dp,) = self._traspaso(tipo_documento='FACTURA ELECTRONICA')
        stock0 = self._stock(self.t_origen)

        resp = self._nc(dte, [{'dte_producto_id': dp.id, 'cantidad': 1}])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.t_origen), stock0 + 1)
        self.assert_sincronizado(self.t_origen)
        salida = self._salidas(dte).get()
        self.assertEqual(salida.estado, 'COMPLETADO')
        self.assertEqual(salida.cantidad, -2)
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'EMITIDO')
        self.assertEqual(dte.unidades_productos, 2)

        # El rechazo posterior devuelve SOLO las 2 que seguían afuera.
        resp = self._rechazar(dte)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['unidades_devueltas'], 2)
        self.assertEqual(self._stock(self.t_origen), self.STOCK_ORIGEN)
        self.assert_sincronizado(self.t_origen)


class NcTotalSinLineasTest(_BaseNc):

    def test_nc_total_sin_lineas_pre_recepcion_devuelve_al_origen_y_cierra(self):
        dte, (dp,) = self._traspaso(tipo_documento='FACTURA ELECTRONICA')
        stock0, lotes0 = self._stock(self.t_origen), self._lotes(self.t_origen)

        resp = self._nc(dte)  # razón 1, sin productos_afectados
        self.assertEqual(resp.status_code, 200, resp.content)

        self.assertEqual(self._stock(self.t_origen), stock0 + 3)
        self.assertEqual(self._lotes(self.t_origen), lotes0 + 3)
        self.assert_sincronizado(self.t_origen)
        self.assertEqual(self._devolucion_nc(dte), 0)
        salida = self._salidas(dte).get()
        self.assertEqual(salida.estado, 'CANCELADO')
        self.assertEqual(salida.cantidad, -3)

        dte.refresh_from_db()
        dp.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'CANCELADO')
        self.assertFalse(dp.activo)
        nc = Dte.objects.get(documento_afectado=dte, es_nota_credito=True)
        self.assertTrue(nc.redujo_lineas_documento)
        self.assertFalse(self._en_emitidos_pendientes(dte))

        # Rechazar/cancelar después no acreditan dos veces.
        self.assertIn(self._rechazar(dte).status_code, (400, 409))
        resp = self._cancelar(dte)
        self.assertIn(resp.status_code, (400, 409), resp.content)
        self.assertEqual(self._stock(self.t_origen), stock0 + 3)

    def test_nc_total_sobre_rechazado_cierra_en_cancelado_sin_mover_stock(self):
        dte, (dp,) = self._traspaso(tipo_documento='FACTURA ELECTRONICA')
        self.assertEqual(self._rechazar(dte).status_code, 200)
        self.assertEqual(self._stock(self.t_origen), self.STOCK_ORIGEN)

        resp = self._nc(dte)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(len(resp.json()['lineas_sin_reversa_stock']), 1)
        self.assertIn('rechazar', resp.json()['lineas_sin_reversa_stock'][0]['motivo'])

        self.assertEqual(self._stock(self.t_origen), self.STOCK_ORIGEN)
        self.assert_sincronizado(self.t_origen)
        salida = self._salidas(dte).get()
        self.assertEqual(salida.estado, 'CANCELADO')
        self.assertEqual(salida.cantidad, -3)     # la evidencia del rechazo no se reescribe
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'CANCELADO')


class NcSobreStockYaDevueltoTest(_BaseNc):

    def test_nc_parcial_por_linea_sobre_cancelado_responde_409_y_no_pierde_lotes(self):
        dte, (dp,) = self._traspaso(tipo_documento='FACTURA ELECTRONICA')
        self.assertEqual(self._cancelar(dte).status_code, 200)
        self.assert_sincronizado(self.t_origen)
        lotes0 = self._lotes(self.t_origen)

        resp = self._nc(dte, [{'dte_producto_id': dp.id, 'cantidad': 2}])
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertTrue(resp.json().get('stock_ya_devuelto'))
        self.assertIn('cancelar', resp.json()['error'])
        self.assertFalse(Dte.objects.filter(documento_afectado=dte).exists())
        self.assertEqual(self._lotes(self.t_origen), lotes0)
        self.assertEqual(self._salidas(dte).get().estado, 'CANCELADO')

    def test_nc_por_linea_por_el_total_sobre_cancelado_es_documental(self):
        """La factura cancelada igual necesita su NC ante el SII: por el
        total pasa, sin tocar stock, lotes ni la fila CANCELADO."""
        dte, (dp,) = self._traspaso(tipo_documento='FACTURA ELECTRONICA')
        self.assertEqual(self._cancelar(dte).status_code, 200)
        stock0, lotes0 = self._stock(self.t_origen), self._lotes(self.t_origen)
        n_movs = Movimientos_Producto.objects.filter(dte=dte).count()

        resp = self._nc(dte, [{'dte_producto_id': dp.id, 'cantidad': 3}])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(len(resp.json()['lineas_sin_reversa_stock']), 1)
        self.assertEqual(self._stock(self.t_origen), stock0)
        self.assertEqual(self._lotes(self.t_origen), lotes0)
        self.assertEqual(Movimientos_Producto.objects.filter(dte=dte).count(), n_movs)
        salida = self._salidas(dte).get()
        self.assertEqual((salida.estado, salida.cantidad), ('CANCELADO', -3))
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'CANCELADO')

    def test_nc_parcial_por_linea_sobre_rechazado_devuelto_responde_409(self):
        dte, (dp,) = self._traspaso(tipo_documento='FACTURA ELECTRONICA')
        self.assertEqual(self._rechazar(dte).status_code, 200)
        resp = self._nc(dte, [{'dte_producto_id': dp.id, 'cantidad': 1}])
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertIn('rechazar', resp.json()['error'])
        salida = self._salidas(dte).get()
        self.assertEqual((salida.estado, salida.cantidad), ('CANCELADO', -3))


class AjustarNoBorraKardexTest(_BaseNc):

    def test_ajustar_a_cero_deja_la_salida_cancelada_y_cierra_el_dte(self):
        dte, (dp,) = self._traspaso()
        n_movs = Movimientos_Producto.objects.filter(dte=dte).count()
        resp = self._ajustar(dte, dp, 0)
        self.assertEqual(resp.status_code, 200, resp.content)

        self.assertEqual(self._stock(self.t_origen), self.STOCK_ORIGEN)
        self.assert_sincronizado(self.t_origen)
        self.assertEqual(Movimientos_Producto.objects.filter(dte=dte).count(), n_movs)
        salida = self._salidas(dte).get()
        self.assertEqual((salida.estado, salida.cantidad), ('CANCELADO', -3))
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'CANCELADO')
        self.assertEqual(dte.unidades_productos, 0)
        self.assertFalse(self._en_emitidos_pendientes(dte))
        # Nada que rechazar ni cancelar (sin doble crédito).
        self.assertIn(self._rechazar(dte).status_code, (400, 409))
        self.assertEqual(self._stock(self.t_origen), self.STOCK_ORIGEN)

    def test_ajuste_parcial_mantiene_el_dte_emitido(self):
        dte, (dp,) = self._traspaso()
        resp = self._ajustar(dte, dp, 1)
        self.assertEqual(resp.status_code, 200, resp.content)
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'EMITIDO')
        self.assertEqual(self._salidas(dte).get().cantidad, -1)
        self.assert_sincronizado(self.t_origen)

    def test_cambio_de_talla_y_ajuste_a_cero_no_pierden_el_lote_del_swap(self):
        """ESC-K de la auditoría: M 3u → 1u a L → ajustar M a 0.
        M y L deben quedar con stock == lotes (antes M: 18 vs 17)."""
        talla_l = Producto_Talla.objects.create(
            producto=self.prod_origen, sku=self.SKU + 1, stock=17, talla='L')
        crear_lote_fifo(talla_l, cantidad=17, costo_unitario=100)
        dte, (dp,) = self._traspaso()

        self._sesion(self.origen)
        resp = self._post('/app/dte/cambiar_talla/', {
            'dte_id': dte.id, 'motivo': 'talla mal', 'token_operacion': 'tok-test-k',
            'cambios': [{'dte_producto_id': dp.id, 'talla_destino_id': talla_l.id, 'cantidad': 1}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.t_origen), self.STOCK_ORIGEN - 2)
        self.assertEqual(self._stock(talla_l), 16)
        self.assert_sincronizado(self.t_origen)
        self.assert_sincronizado(talla_l)
        lote_swap = LoteProducto.objects.get(
            producto_talla=self.t_origen, observaciones__icontains='CAMBIO TALLA')
        self.assertIsNone(lote_swap.movimiento_id)

        resp = self._ajustar(dte, dp, 0)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.t_origen), self.STOCK_ORIGEN)
        self.assert_sincronizado(self.t_origen)
        self.assertEqual(self._stock(talla_l), 16)
        self.assert_sincronizado(talla_l)
        self.assertTrue(LoteProducto.objects.filter(id=lote_swap.id).exists())
        # La línea L sigue viva: el DTE no se cierra.
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'EMITIDO')
        self.assertEqual(dte.unidades_productos, 1)


class LimboLotesTest(_BaseNc):

    def _recepcionar_total(self, dte, dp):
        resp = self._confirmar(dte, [self._linea_payload(dp)])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.t_destino), 3)
        self.assert_sincronizado(self.t_destino)
        self.assert_sincronizado(self.t_origen)

    def test_devolucion_fisica_confirmada_mueve_lotes_en_destino_y_origen(self):
        dte, (dp,) = self._traspaso()
        self._recepcionar_total(dte, dp)
        stock_o, lotes_o = self._stock(self.t_origen), self._lotes(self.t_origen)

        resp = self._ajustar(dte, dp, 1, devolver_stock=True)
        self.assertEqual(resp.status_code, 200, resp.content)
        hijo_id = resp.json()['doc_trazador']['id']
        # Nada se mueve hasta que el destino confirme el despacho.
        self.assertEqual(self._stock(self.t_destino), 3)

        self._sesion(self.destino)
        resp = self._post('/app/dte/confirmar_devolucion_fisica/', {'dte_hijo_id': hijo_id})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['cerrado'])

        self.assertEqual(self._stock(self.t_destino), 1)
        self.assert_sincronizado(self.t_destino)
        self.assertEqual(self._stock(self.t_origen), stock_o + 2)
        self.assertEqual(self._lotes(self.t_origen), lotes_o + 2)
        self.assert_sincronizado(self.t_origen)
        # Las filas del par siguen siendo las mismas (COMPLETADO), sin duplicar.
        hijo = Dte.objects.get(id=hijo_id)
        movs = Movimientos_Producto.objects.filter(dte=hijo, concepto='DEVOLUCION_NC_POST_RECEPCION')
        self.assertEqual(movs.count(), 2)
        lote = LoteProducto.objects.get(dte=hijo, producto_talla=self.t_origen)
        self.assertEqual(lote.cantidad_disponible, 2)
        self.assertEqual(lote.movimiento_id, movs.get(tipo_movimiento='INGRESO').id)
        self.assertEqual(str(lote.fecha_ingreso.date()), self.FECHA_DESPACHO)

    def test_absorber_sin_retorno_consume_lotes_del_destino(self):
        dte, (dp,) = self._traspaso()
        self._recepcionar_total(dte, dp)
        stock_o = self._stock(self.t_origen)

        resp = self._ajustar(dte, dp, 1, devolver_stock=False)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.t_destino), 1)
        self.assert_sincronizado(self.t_destino)
        self.assertEqual(self._stock(self.t_origen), stock_o)
        self.assert_sincronizado(self.t_origen)


class ConsumidoresIgnoranSalidasCanceladasTest(_BaseNc):
    """N-05: historial de existencias y tránsito de diferencias sólo suman
    TRASPASO_SALIDA en COMPLETADO."""

    def _request(self, params=None):
        rf = RequestFactory()
        req = rf.get('/x/', params or {})
        req.user = self.user
        req.session = {'idSucursalActual': self.origen.id, 'idEmpresaActual': self.empresa.id}
        return req

    def test_historial_existencias_no_cuenta_la_salida_cancelada(self):
        from app.views_modulo_existencias_nuevo import _historial_despachos_reales
        dte_rechazado, _ = self._traspaso()
        self.assertEqual(self._rechazar(dte_rechazado).status_code, 200)
        dte_vivo, _ = self._traspaso([(self.t_origen, 2)])

        Movimientos_Producto.objects.filter(dte__in=[dte_rechazado, dte_vivo]).update(
            fecha=timezone.localdate())
        resp = _historial_despachos_reales(self._request({'dias': 30}), self.origen.id)
        data = json.loads(resp.content)
        ids = {d.get('dte_id') for d in data['despachos']}
        self.assertIn(dte_vivo.id, ids)
        self.assertNotIn(dte_rechazado.id, ids)

    def test_transito_diferencias_no_cuenta_la_salida_cancelada(self):
        from app.views_modulo_reportes_diferencias import _documentos_en_transito
        dte_nc, (dp,) = self._traspaso(tipo_documento='FACTURA ELECTRONICA')
        # NC por línea total pre-recepción: la salida queda CANCELADO y el
        # DTE CANCELADO; con una salida CANCELADO en un DTE EMITIDO tampoco
        # debe listarse, así que se fuerza ese caso.
        self.assertEqual(self._nc(dte_nc, [{'dte_producto_id': dp.id, 'cantidad': 3}]).status_code, 200)
        Dte.objects.filter(id=dte_nc.id).update(estado_dte='EMITIDO')
        dte_vivo, _ = self._traspaso([(self.t_origen, 2)])
        Movimientos_Producto.objects.filter(dte__in=[dte_nc, dte_vivo]).update(
            fecha=timezone.localdate())

        documentos, _hoy, _trunc = _documentos_en_transito(self._request(), dias=30)
        ids = {d.get('dte_id') for d in documentos}
        self.assertIn(dte_vivo.id, ids)
        self.assertNotIn(dte_nc.id, ids)
