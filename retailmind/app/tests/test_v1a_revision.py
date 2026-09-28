"""
Unidad V1A — correcciones tras la revisión adversarial.

Cubre:
- B14-02: el control de factura reutilizada (409 needs_confirmation) también
  corre en la rama "cantidad 0 + factura" y cuando el documento no declara
  unidades (unidades_productos = 0).
- B2-04: el tope de «Editar recepciones» se evalúa sobre el total FINAL del
  lote (un intercambio entre dos recepciones de la misma talla ya no depende
  del orden); una compra eliminada solo admite bajar recepciones.
- B14-03 / B2-02 / B14-01: después de compras_recalcular_avance --apply,
  borrar una recepción pendiente no deja un piso fantasma (la compra se puede
  eliminar y Pendientes muestra lo que falta); el comando re-sincroniza y el
  piso legacy se sigue respetando en líneas enlazadas a un SKU.
- B1-06: stock fuera de rango (fila o suma sin talla) da 400 JSON, no 500.

Ejecutar (BD de test aislada):
    python manage.py test app.tests.test_v1a_revision --keepdb
"""
import io

from django.core.management import call_command

from app.models import Compras_Producto, Compras_Producto_Talla, Productos_Recepcionados
from .factories import crear_producto_con_talla
from .test_v1a_compras import _BaseV1A


class ReusoFacturaTest(_BaseV1A):
    URL = '/app/guardar_recepcion/'

    def setUp(self):
        super().setUp()
        self.compra = self._compra()
        self.cp, (self.cpt,) = self._linea(self.compra, tallas=(('40', 5),))
        self.otra_compra = self._compra(nombre='OC duplicada')
        _cp, (self.cpt_otra,) = self._linea(self.otra_compra, tallas=(('40', 5),))

    def _post(self, recs, **extra):
        body = dict(compra_id=self.compra.id, recepciones=recs, sucursal_destino_id=None, **extra)
        return self._json('post', self.URL, body)

    def _uds_factura(self, factura):
        return sum(Productos_Recepcionados.objects.filter(dte=factura)
                   .values_list('stockArribado', flat=True))

    def test_asignar_factura_a_recepcion_sin_dte_tambien_pide_confirmacion(self):
        factura = self._dte(7101, self.proveedor, unidades=5)
        self._recepcion(self.cpt_otra, 5, dte=factura)  # ya completa en otra compra
        # Paso 1: 3 u sin factura (permitido, no hay factura en juego)
        r = self._post([{'compra_producto_talla_id': self.cpt.id, 'recepcionado': 3}])
        self.assertEqual(r.status_code, 200, r.content)
        # Paso 2: cantidad 0 + factura => asigna la factura a esa recepción
        rec = [{'compra_producto_talla_id': self.cpt.id, 'recepcionado': 0, 'factura_id': factura.id}]
        r = self._post(rec)
        self.assertEqual(r.status_code, 409, r.content)
        self.assertTrue(r.json()['needs_confirmation'])
        self.assertEqual(self._uds_factura(factura), 5)
        self.assertTrue(Productos_Recepcionados.objects.filter(
            compra_producto_talla=self.cpt, dte__isnull=True).exists())
        r = self._post(rec, confirmar_exceso_factura=True)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self._uds_factura(factura), 8)

    def test_factura_sin_unidades_declaradas_reusada_pide_confirmacion(self):
        factura = self._dte(7102, self.proveedor, unidades=0)
        self._recepcion(self.cpt_otra, 4, dte=factura)
        rec = [{'compra_producto_talla_id': self.cpt.id, 'recepcionado': 2, 'factura_id': factura.id}]
        r = self._post(rec)
        self.assertEqual(r.status_code, 409, r.content)
        d = r.json()
        self.assertIsNone(d['facturas_reutilizadas'][0]['unidades_documento'])
        self.assertIn('no declara unidades', d['error'])
        self.assertEqual(self._uds_factura(factura), 4)
        r = self._post(rec, confirmar_exceso_factura=True)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self._uds_factura(factura), 6)

    def test_factura_repartida_dentro_de_lo_declarado_no_pregunta(self):
        factura = self._dte(7103, self.proveedor, unidades=10)
        self._recepcion(self.cpt_otra, 4, dte=factura)
        r = self._post([{'compra_producto_talla_id': self.cpt.id, 'recepcionado': 5,
                         'factura_id': factura.id}])
        self.assertEqual(r.status_code, 200, r.content)

    def test_factura_nueva_sin_otras_compras_no_pregunta(self):
        factura = self._dte(7104, self.proveedor, unidades=0)
        r = self._post([{'compra_producto_talla_id': self.cpt.id, 'recepcionado': 5,
                         'factura_id': factura.id}])
        self.assertEqual(r.status_code, 200, r.content)


class EditarRecepcionesLoteTest(_BaseV1A):
    URL = '/app/actualizar_recepciones_compra/'

    def setUp(self):
        super().setUp()
        self.compra = self._compra()
        _cp, (self.cpt,) = self._linea(self.compra, tallas=(('40', 4),))
        self.a = self._recepcion(self.cpt, 3)
        self.b = self._recepcion(self.cpt, 1)

    def _post(self, cambios):
        return self._json('post', self.URL, {'compra_id': self.compra.id, 'cambios': cambios})

    def test_intercambio_no_depende_del_orden(self):
        for orden in ([(self.b, 3), (self.a, 1)], [(self.a, 1), (self.b, 3)]):
            # dejar el estado inicial (A=3, B=1) antes de cada variante
            Productos_Recepcionados.objects.filter(id=self.a.id).update(stockArribado=3)
            Productos_Recepcionados.objects.filter(id=self.b.id).update(stockArribado=1)
            r = self._post([{'recepcion_id': rec.id, 'cantidad': c} for rec, c in orden])
            self.assertEqual(r.status_code, 200, r.content)
            self.a.refresh_from_db()
            self.b.refresh_from_db()
            self.assertEqual((self.a.stockArribado, self.b.stockArribado), (1, 3))

    def test_total_final_sobre_lo_comprado_se_rechaza_sin_escribir(self):
        r = self._post([{'recepcion_id': self.a.id, 'cantidad': 1},
                        {'recepcion_id': self.b.id, 'cantidad': 4}])
        self.assertEqual(r.status_code, 400)
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual((self.a.stockArribado, self.b.stockArribado), (3, 1))

    def test_borrar_y_mover_en_el_mismo_lote(self):
        r = self._post([{'recepcion_id': self.b.id, 'cantidad': 4},
                        {'recepcion_id': self.a.id, 'eliminar': True}])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Productos_Recepcionados.objects.filter(id=self.a.id).exists())
        self.b.refresh_from_db()
        self.assertEqual(self.b.stockArribado, 4)

    def test_compra_eliminada_solo_permite_bajar(self):
        self.compra.estado = 'ELIMINADA'
        self.compra.save()
        r = self._post([{'recepcion_id': self.b.id, 'cantidad': 2},
                        {'recepcion_id': self.a.id, 'cantidad': 2}])
        # neto 4 -> 4: no sube, se permite reordenar
        self.assertEqual(r.status_code, 200, r.content)
        r = self._post([{'recepcion_id': self.b.id, 'cantidad': 3}])
        self.assertEqual(r.status_code, 400)
        r = self._post([{'recepcion_id': self.b.id, 'eliminar': True}])
        self.assertEqual(r.status_code, 200, r.content)


class AvanceSinPisoFantasmaTest(_BaseV1A):

    def test_apply_y_luego_borrar_recepcion_no_bloquea(self):
        compra = self._compra()
        _cp, (cpt,) = self._linea(compra, tallas=(('40', 4),))
        r = self._json('post', '/app/guardar_recepcion/', {
            'compra_id': compra.id, 'sucursal_destino_id': None,
            'recepciones': [{'compra_producto_talla_id': cpt.id, 'recepcionado': 2}]})
        self.assertEqual(r.status_code, 200, r.content)
        call_command('compras_recalcular_avance', '--compra', str(compra.id), '--apply',
                     stdout=io.StringIO())
        cpt.refresh_from_db()
        self.assertEqual(cpt.unidades_recibidas, 2)
        rec = Productos_Recepcionados.objects.get(compra_producto_talla=cpt)
        r = self._json('post', '/app/actualizar_recepciones_compra/', {
            'compra_id': compra.id, 'cambios': [{'recepcion_id': rec.id, 'eliminar': True}]})
        self.assertEqual(r.status_code, 200, r.content)

        d = self._json('get', f'/app/obtener_pendientes_compra/{compra.id}/').json()
        self.assertEqual(d['pendientes'][0]['pendiente'], 4)
        info = self._json('post', '/app/eliminar_compra/', {'compra_id': compra.id, 'mode': 'check'}).json()['info']
        self.assertEqual(info['total_recepcionado'], 0)
        r = self._json('post', '/app/eliminar_compra/', {'compra_id': compra.id, 'mode': 'delete', 'force': True})
        self.assertEqual(r.status_code, 200, r.content)

        # re-sincronizar: el comando baja el espejo en líneas sin SKU
        call_command('compras_recalcular_avance', '--compra', str(compra.id), '--apply',
                     stdout=io.StringIO())
        cpt.refresh_from_db()
        self.assertEqual(cpt.unidades_recibidas, 0)
        self.assertEqual(cpt.estado_item, 'pendiente')

    def test_piso_legacy_en_linea_enlazada_se_respeta(self):
        compra = self._compra()
        _cp, (cpt,) = self._linea(compra, tallas=(('40', 5),))
        _prod, pt = crear_producto_con_talla(self.sucursal, articulo='ART-1', talla='40', sku=9900101)
        # vinculación retroactiva: línea enlazada, recibida completa sin recepciones
        Compras_Producto_Talla.objects.filter(id=cpt.id).update(
            producto_talla=pt, unidades_recibidas=5, estado_item='recibido_completo')
        d = self._json('get', f'/app/obtener_pendientes_compra/{compra.id}/').json()
        self.assertEqual(d['pendientes'], [])
        r = self._json('post', '/app/eliminar_compra/', {'compra_id': compra.id, 'mode': 'delete', 'force': True})
        self.assertEqual(r.status_code, 400)
        # el comando nunca baja el piso de una línea enlazada
        call_command('compras_recalcular_avance', '--compra', str(compra.id), '--apply',
                     stdout=io.StringIO())
        cpt.refresh_from_db()
        self.assertEqual(cpt.unidades_recibidas, 5)

    def test_vincular_producto_no_enlaza_si_hay_pendientes(self):
        compra = self._compra()
        _cp, (cpt,) = self._linea(compra, tallas=(('40', 5),))
        _prod, pt = crear_producto_con_talla(self.sucursal, articulo='ART-1', talla='40', sku=9900102)
        self._recepcion(cpt, 2, producto_talla=pt)
        self._recepcion(cpt, 2)
        call_command('compras_recalcular_avance', '--compra', str(compra.id), '--apply',
                     '--vincular-producto', stdout=io.StringIO())
        cpt.refresh_from_db()
        self.assertIsNone(cpt.producto_talla_id)
        self.assertEqual(cpt.unidades_recibidas, 4)


class ImportarCsvRangoTest(_BaseV1A):
    URL = '/app/importar_csv_compra/'

    def _fila(self, **kw):
        base = dict(nombre='ART-CSV', descripcion='d', atributo1='MARCA', atributo2='NEGRO',
                    atributo3='HOMBRE', atributo4='', costo=1000, precioSugerido=2000, stock=5,
                    talla='40', sucursal='')
        base.update(kw)
        return base

    def test_stock_fuera_de_rango_es_400_con_fila(self):
        compra = self._compra()
        r = self._json('post', self.URL, {'compra_id': compra.id, 'filas': [
            self._fila(), self._fila(talla='41', stock=3000000000)]})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Fila 2', r.json()['error'])
        self.assertFalse(Compras_Producto.objects.filter(compras=compra).exists())

    def test_suma_sin_talla_fuera_de_rango_es_400(self):
        compra = self._compra()
        r = self._json('post', self.URL, {'compra_id': compra.id, 'filas': [
            self._fila(talla='', stock=2000000000), self._fila(talla='', stock=2000000000)]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r['Content-Type'], 'application/json')
        self.assertFalse(Compras_Producto.objects.filter(compras=compra).exists())
