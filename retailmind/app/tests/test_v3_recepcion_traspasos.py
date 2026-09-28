"""
Unidad V3 — Recepción de traspasos, sobrantes, ajustes y reparaciones.

Fija el contrato de los escritores corregidos en esta unidad:

1. decidir_sobrante_api (B6-01/B6-02/B6-05): ya no responde 500 por el
   FOR UPDATE sobre un outer join; ACEPTAR deja UN solo ingreso COMPLETADO
   (+N stock / +N kardex / +N lotes) y DEVOLVER no toca stock ni kardex y su
   notificación al origen sobrevive al cierre del DTE.
2. confirmar_recepcion_api (B6-03/B6-04/B6-05): valida el payload contra las
   líneas activas del documento (repetidas → 400, omitidas / cantidades viejas
   → 409 sin escribir), bloquea líneas sin ficha y la auto-devolución de guía
   repone lote en el origen.
3. corregir_recepcion_emisor_api (B7-01/B7-05/B13-07/B7-07): no corrige líneas
   REGULARIZADO (una fila regularizada sin cambio no bloquea el resto), un 400
   a mitad del lote no deja nada escrito, exige recepcion_dte.puede_editar y
   crea el lote del ingreso.
4. ajustar_dte_emisor_api (B7-03/B7-04/B7-07): legacy sin TRASPASO_SALIDA →
   409; un error en una línea posterior revierte las anteriores; el ajuste
   pre-recepción repone lote; un 500 no expone el detalle de la excepción.
5. rechazar / rehabilitar / cancelar (B6-05/B7-09): la capa de lotes acompaña
   al stock plano y la cancelación no se puede aplicar dos veces.

Corren sobre PostgreSQL (el 500 de B6-01 era exclusivo de ese motor).
"""
import json
from decimal import Decimal
from unittest import mock

from django.db.models import Sum
from django.test import TestCase, Client

from app.models import (
    Dte, Dte_Productos, Producto_Talla, Movimientos_Producto,
    Productos_Recepcionados, LoteProducto, NotificacionDTE,
)
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla, crear_correlativo, crear_lote_fifo,
)


def _permisos(side_effect=None):
    """Concede (o filtra con side_effect) PermisoRol.tiene_permiso: lo usan los
    decoradores, el middleware y los helpers de las vistas."""
    kwargs = {'side_effect': side_effect} if side_effect else {'return_value': True}
    return mock.patch('app.decorators.PermisoRol.tiene_permiso', **kwargs)


class _BaseV3(TestCase):
    SKU_A = 93001
    SKU_B = 93002

    def setUp(self):
        self.user = crear_usuario(username='v3admin', rol='administrador')
        self.empresa = crear_empresa()
        self.origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.origen)
        crear_correlativo(self.origen, tipo_dte='AJUSTE TRASPASO')
        crear_correlativo(self.origen, tipo_dte='AJUSTE TRASPASO POST')

        _, self.a_origen = crear_producto_con_talla(
            self.origen, articulo='Zap A', sku=self.SKU_A, stock=20, costo=100)
        _, self.a_destino = crear_producto_con_talla(
            self.destino, articulo='Zap A', sku=self.SKU_A, stock=0, costo=100)
        _, self.b_origen = crear_producto_con_talla(
            self.origen, articulo='Zap B', sku=self.SKU_B, stock=20, costo=100)
        _, self.b_destino = crear_producto_con_talla(
            self.destino, articulo='Zap B', sku=self.SKU_B, stock=0, costo=100)

        self.client = Client()
        self.client.force_login(self.user)
        self._folio = 70000

    # ── helpers ───────────────────────────────────────────────────────────
    def _traspaso(self, lineas, tipo_documento='GUIA', con_salida=True):
        """DTE de TRASPASO EMITIDO. `lineas` = [(talla_origen, cantidad), ...].
        Replica lo que deja emitir_dte: líneas, TRASPASO_SALIDA y stock
        descontado en el origen."""
        self._folio += 1
        total = sum(c for _, c in lineas)
        dte = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=self._folio, tipo_documento=tipo_documento,
            monto_neto=Decimal(total * 1000), monto_con_iva=Decimal(total * 1190),
            estado_pago='PENDIENTE', estado_dte='EMITIDO', responsable='tester',
            fecha_emision='2026-09-01', fecha_vencimiento='2026-09-01',
            diasCredito=0, bultos=1, unidades_productos=total,
            tipo_transaccion='TRASPASO', sucursal=self.origen,
        )
        dps = []
        for talla, cant in lineas:
            dps.append(Dte_Productos.objects.create(
                dte=dte, productoTalla=talla, descripcion=talla.producto.articulo,
                costo=100, sobreprecio=0, precio=1000, stock=cant, activo=True,
            ))
            if con_salida:
                Movimientos_Producto.objects.create(
                    dte=dte, ProductoTalla=talla,
                    sucursal_origen=self.origen, sucursal_destino=self.destino,
                    cantidad=-cant, costo=100, concepto='TRASPASO_SALIDA',
                    tipo_movimiento='EGRESO', estado='COMPLETADO', responsable='tester',
                )
            Producto_Talla.objects.filter(id=talla.id).update(
                stock=Producto_Talla.objects.get(id=talla.id).stock - cant)
        return dte, dps

    def _sesion(self, sucursal):
        session = self.client.session
        session['idSucursalActual'] = sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session['alias'] = sucursal.alias
        session.save()

    def _post(self, url, payload, side_effect=None):
        with _permisos(side_effect):
            return self.client.post(url, data=json.dumps(payload),
                                    content_type='application/json')

    @staticmethod
    def _stock(talla):
        return Producto_Talla.objects.get(id=talla.id).stock

    @staticmethod
    def _kardex(talla):
        return Movimientos_Producto.objects.filter(
            ProductoTalla_id=talla.id, estado='COMPLETADO',
        ).aggregate(t=Sum('cantidad'))['t'] or 0

    @staticmethod
    def _lotes(talla):
        return LoteProducto.objects.filter(
            producto_talla_id=talla.id, activo=True,
        ).aggregate(t=Sum('cantidad_disponible'))['t'] or 0

    def _linea_payload(self, dp, recibida=None, sobrante=0, danada=0, esperada=None,
                       estado='RECEPCIONADO_OK'):
        return {
            'dte_producto_id': dp.id,
            'cantidad_esperada': dp.stock if esperada is None else esperada,
            'cantidad_recepcionada': dp.stock if recibida is None else recibida,
            'cantidad_danada': danada,
            'cantidad_sobrante': sobrante,
            'estado': estado,
            'observaciones': '',
        }

    def _confirmar(self, dte, productos):
        self._sesion(self.destino)
        return self._post('/app/dte/confirmar_recepcion/',
                          {'dte_id': dte.id, 'productos': productos})


# ═════════════════════════════════════════════════════════════════════════
# 1. Sobrantes
# ═════════════════════════════════════════════════════════════════════════
class DecidirSobranteTest(_BaseV3):

    def _dte_con_sobrante(self, sobrante=2):
        dte, (dp,) = self._traspaso([(self.a_origen, 5)])
        resp = self._confirmar(dte, [self._linea_payload(
            dp, sobrante=sobrante, estado='RECEPCIONADO_SOBRANTE')])
        self.assertEqual(resp.status_code, 200, resp.content)
        rec = Productos_Recepcionados.objects.get(dte=dte)
        self.assertEqual(rec.estado, 'RECEPCIONADO_SOBRANTE')
        self.assertTrue(Movimientos_Producto.objects.filter(
            dte=dte, concepto='RECEPCION_SOBRANTE', estado='PENDIENTE').exists())
        return dte, rec

    def _decidir(self, rec, decision):
        self._sesion(self.destino)
        return self._post('/app/dte/decidir_sobrante/', {
            'recepcion_id': rec.id, 'decision': decision, 'observaciones': 'test',
        })

    def test_aceptar_ingresa_una_sola_vez_con_lote(self):
        dte, rec = self._dte_con_sobrante(2)
        stock0, kardex0, lotes0 = (self._stock(self.a_destino),
                                   self._kardex(self.a_destino),
                                   self._lotes(self.a_destino))

        resp = self._decidir(rec, 'ACEPTAR')
        self.assertEqual(resp.status_code, 200, resp.content)

        rec.refresh_from_db()
        self.assertEqual(rec.estado, 'REGULARIZADO')
        # +N / +N / +N: stock, kardex COMPLETADO y lotes suben lo mismo.
        self.assertEqual(self._stock(self.a_destino) - stock0, 2)
        self.assertEqual(self._kardex(self.a_destino) - kardex0, 2)
        self.assertEqual(self._lotes(self.a_destino) - lotes0, 2)
        # El PENDIENTE se cancela, no se completa (antes quedaban 2 ingresos).
        self.assertFalse(Movimientos_Producto.objects.filter(
            dte=dte, concepto='RECEPCION_SOBRANTE', estado__in=['PENDIENTE', 'COMPLETADO']).exists())
        self.assertEqual(Movimientos_Producto.objects.filter(
            dte=dte, concepto='SOBRANTE_INGRESO', estado='COMPLETADO').count(), 1)
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'RECEPCIONADO_COMPLETO')

    def test_devolver_no_mueve_stock_ni_kardex(self):
        dte, rec = self._dte_con_sobrante(3)
        stock0, kardex0, lotes0 = (self._stock(self.a_destino),
                                   self._kardex(self.a_destino),
                                   self._lotes(self.a_destino))

        resp = self._decidir(rec, 'DEVOLVER')
        self.assertEqual(resp.status_code, 200, resp.content)

        rec.refresh_from_db()
        self.assertEqual(rec.estado, 'REGULARIZADO')
        self.assertEqual(self._stock(self.a_destino) - stock0, 0)
        self.assertEqual(self._kardex(self.a_destino) - kardex0, 0)
        self.assertEqual(self._lotes(self.a_destino) - lotes0, 0)
        devuelto = Movimientos_Producto.objects.get(dte=dte, concepto='SOBRANTE_DEVUELTO')
        self.assertEqual(devuelto.cantidad, 0)
        self.assertEqual(devuelto.tipo_movimiento, 'EGRESO')
        self.assertEqual(Movimientos_Producto.objects.get(
            dte=dte, concepto='RECEPCION_SOBRANTE').estado, 'CANCELADO')

    def test_devolver_notifica_al_origen_aunque_cierre_el_dte(self):
        # La decisión del ÚLTIMO sobrante cierra el documento: el dte.save()
        # del recálculo dispara la señal que borra las NotificacionDTE de un
        # DTE no EMITIDO. La notificación debe sobrevivir (se crea después).
        dte, rec = self._dte_con_sobrante(3)
        resp = self._decidir(rec, 'DEVOLVER')
        self.assertEqual(resp.status_code, 200, resp.content)
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'RECEPCIONADO_COMPLETO')
        self.assertTrue(NotificacionDTE.objects.filter(
            dte=dte, sucursal=self.origen, empresa_receptora=self.empresa,
            titulo__startswith='Sobrante devuelto',
        ).exists())

    def test_segunda_decision_responde_409_sin_duplicar(self):
        _dte, rec = self._dte_con_sobrante(2)
        self.assertEqual(self._decidir(rec, 'ACEPTAR').status_code, 200)
        stock1 = self._stock(self.a_destino)
        resp = self._decidir(rec, 'ACEPTAR')
        self.assertIn(resp.status_code, (400, 409), resp.content)
        self.assertEqual(self._stock(self.a_destino), stock1)


# ═════════════════════════════════════════════════════════════════════════
# 2. Validación del payload de confirmar_recepcion_api
# ═════════════════════════════════════════════════════════════════════════
class ConfirmarPayloadTest(_BaseV3):

    def setUp(self):
        super().setUp()
        self.dte, (self.dp_a, self.dp_b) = self._traspaso(
            [(self.a_origen, 5), (self.b_origen, 3)], tipo_documento='FACTURA ELECTRONICA')

    def _nada_escrito(self):
        self.dte.refresh_from_db()
        self.assertEqual(self.dte.estado_dte, 'EMITIDO')
        self.assertIsNone(self.dte.fecha_recepcion)
        self.assertFalse(Productos_Recepcionados.objects.filter(dte=self.dte).exists())
        self.assertEqual(self._stock(self.a_destino), 0)
        self.assertEqual(self._stock(self.b_destino), 0)
        self.assertEqual(self._lotes(self.a_destino), 0)

    def test_linea_repetida_400(self):
        la = self._linea_payload(self.dp_a)
        resp = self._confirmar(self.dte, [la, dict(la), self._linea_payload(self.dp_b)])
        self.assertEqual(resp.status_code, 400, resp.content)
        self._nada_escrito()

    def test_linea_omitida_409(self):
        resp = self._confirmar(self.dte, [self._linea_payload(self.dp_a)])
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertTrue(resp.json().get('documento_cambio'))
        self._nada_escrito()

    def test_cantidad_vieja_tras_cambio_del_emisor_409(self):
        # El modal del destino se abrió con 5; el emisor bajó la línea a 4.
        payload = [self._linea_payload(self.dp_a), self._linea_payload(self.dp_b)]
        Dte_Productos.objects.filter(id=self.dp_a.id).update(stock=4)
        resp = self._confirmar(self.dte, payload)
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertTrue(resp.json().get('documento_cambio'))
        self._nada_escrito()

    def test_linea_nueva_del_emisor_no_incluida_409(self):
        # Línea activa agregada después de abrir el modal (p.ej. cambiar talla).
        payload = [self._linea_payload(self.dp_a), self._linea_payload(self.dp_b)]
        Dte_Productos.objects.create(
            dte=self.dte, productoTalla=self.a_origen, descripcion='Nueva',
            costo=100, sobreprecio=0, precio=1000, stock=1, activo=True,
        )
        resp = self._confirmar(self.dte, payload)
        self.assertEqual(resp.status_code, 409, resp.content)
        self._nada_escrito()

    def test_payload_completo_recepciona(self):
        resp = self._confirmar(self.dte, [self._linea_payload(self.dp_a),
                                          self._linea_payload(self.dp_b)])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.dte.refresh_from_db()
        self.assertEqual(self.dte.estado_dte, 'RECEPCIONADO_COMPLETO')
        self.assertEqual(self._stock(self.a_destino), 5)
        self.assertEqual(self._stock(self.b_destino), 3)
        self.assertEqual(self._lotes(self.a_destino), 5)

    def test_linea_inactiva_no_se_exige(self):
        # Una línea anulada por ajuste (activo=False, stock 0) no viaja en el
        # payload de la UI y no debe bloquear.
        Dte_Productos.objects.filter(id=self.dp_b.id).update(activo=False, stock=0)
        resp = self._confirmar(self.dte, [self._linea_payload(self.dp_a)])
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_linea_sin_ficha_bloquea_409(self):
        Dte_Productos.objects.filter(id=self.dp_b.id).update(productoTalla=None)
        self.dp_b.refresh_from_db()
        resp = self._confirmar(self.dte, [self._linea_payload(self.dp_a),
                                          self._linea_payload(self.dp_b)])
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertTrue(resp.json().get('lineas_sin_ficha'))
        self._nada_escrito()
        # También por la rama legacy (sin 'productos').
        resp = self._confirmar(self.dte, [])
        self.assertEqual(resp.status_code, 409, resp.content)
        self._nada_escrito()

    def test_fecha_y_bitacora_en_hora_local(self):
        from django.utils import timezone
        resp = self._confirmar(self.dte, [self._linea_payload(self.dp_a),
                                          self._linea_payload(self.dp_b)])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.dte.refresh_from_db()
        self.assertEqual(self.dte.fecha_recepcion, timezone.localdate())
        self.assertIn(timezone.localtime().strftime('%Y-%m-%d'), self.dte.referencias)


class AutoDevolucionGuiaLoteTest(_BaseV3):

    def test_faltante_de_guia_repone_lote_en_origen(self):
        dte, (dp,) = self._traspaso([(self.a_origen, 5)], tipo_documento='GUIA')
        stock0, lotes0 = self._stock(self.a_origen), self._lotes(self.a_origen)
        resp = self._confirmar(dte, [self._linea_payload(
            dp, recibida=3, estado='FALTANTE')])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.a_origen) - stock0, 2)
        self.assertEqual(self._lotes(self.a_origen) - lotes0, 2)
        # El destino recibe lo que llegó, con su lote.
        self.assertEqual(self._stock(self.a_destino), 3)
        self.assertEqual(self._lotes(self.a_destino), 3)


# ═════════════════════════════════════════════════════════════════════════
# 3. Corrección del emisor
# ═════════════════════════════════════════════════════════════════════════
class CorregirEmisorTest(_BaseV3):

    def setUp(self):
        super().setUp()
        self.dte, (self.dp_a, self.dp_b) = self._traspaso(
            [(self.a_origen, 5), (self.b_origen, 4)], tipo_documento='FACTURA ELECTRONICA')
        resp = self._confirmar(self.dte, [
            self._linea_payload(self.dp_a, recibida=3, estado='RECEPCIONADO_PARCIAL'),
            self._linea_payload(self.dp_b, recibida=2, estado='RECEPCIONADO_PARCIAL'),
        ])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.rec_a = Productos_Recepcionados.objects.get(dte=self.dte, dte_producto=self.dp_a)
        self.rec_b = Productos_Recepcionados.objects.get(dte=self.dte, dte_producto=self.dp_b)

    def _corregir(self, productos, side_effect=None):
        self._sesion(self.origen)
        return self._post('/app/dte/corregir_recepcion_emisor/',
                          {'dte_id': self.dte.id, 'productos': productos},
                          side_effect=side_effect)

    def test_corrige_linea_abierta_y_crea_lote(self):
        stock0, lotes0 = self._stock(self.a_destino), self._lotes(self.a_destino)
        resp = self._corregir([{'recepcion_id': self.rec_a.id, 'cantidad_corregida': 5}])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.a_destino) - stock0, 2)
        self.assertEqual(self._lotes(self.a_destino) - lotes0, 2)
        self.rec_a.refresh_from_db()
        self.assertEqual(self.rec_a.estado, 'RECEPCIONADO_OK')

    def test_linea_regularizada_no_se_vuelve_a_acreditar(self):
        Productos_Recepcionados.objects.filter(id=self.rec_a.id).update(estado='REGULARIZADO')
        stock0 = self._stock(self.a_destino)
        resp = self._corregir([{'recepcion_id': self.rec_a.id, 'cantidad_corregida': 5}])
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(self._stock(self.a_destino), stock0)
        self.rec_a.refresh_from_db()
        self.assertEqual(self.rec_a.estado, 'REGULARIZADO')

    def test_dte_mixto_corrige_solo_la_linea_abierta(self):
        # El Limbo manda también las filas ya REGULARIZADO. Si esa fila queda
        # en lo recibido (sin cambio), no debe bloquear la corrección de la
        # línea abierta.
        Productos_Recepcionados.objects.filter(id=self.rec_a.id).update(estado='REGULARIZADO')
        stock_a0, stock_b0 = self._stock(self.a_destino), self._stock(self.b_destino)
        resp = self._corregir([
            {'recepcion_id': self.rec_a.id, 'cantidad_corregida': 3},   # sin cambio
            {'recepcion_id': self.rec_b.id, 'cantidad_corregida': 4},   # +2
        ])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.a_destino), stock_a0)
        self.assertEqual(self._stock(self.b_destino) - stock_b0, 2)
        self.rec_a.refresh_from_db()
        self.rec_b.refresh_from_db()
        self.assertEqual(self.rec_a.estado, 'REGULARIZADO')
        self.assertEqual(self.rec_a.stockArribado, 3)
        self.assertEqual(self.rec_b.estado, 'RECEPCIONADO_OK')

    def test_dte_mixto_con_linea_regularizada_que_suma_sigue_en_400(self):
        Productos_Recepcionados.objects.filter(id=self.rec_a.id).update(estado='REGULARIZADO')
        stock_b0 = self._stock(self.b_destino)
        resp = self._corregir([
            {'recepcion_id': self.rec_a.id, 'cantidad_corregida': 5},   # regularizada, +2
            {'recepcion_id': self.rec_b.id, 'cantidad_corregida': 4},
        ])
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(self._stock(self.b_destino), stock_b0)

    def test_error_en_segunda_linea_no_deja_la_primera_escrita(self):
        stock_a0 = self._stock(self.a_destino)
        entradas0 = Movimientos_Producto.objects.filter(
            dte=self.dte, concepto='TRASPASO_ENTRADA').count()
        resp = self._corregir([
            {'recepcion_id': self.rec_a.id, 'cantidad_corregida': 5},
            {'recepcion_id': self.rec_b.id, 'cantidad_corregida': 99},
        ])
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(self._stock(self.a_destino), stock_a0)
        self.rec_a.refresh_from_db()
        self.assertEqual(self.rec_a.stockArribado, 3)
        self.assertEqual(Movimientos_Producto.objects.filter(
            dte=self.dte, concepto='TRASPASO_ENTRADA').count(), entradas0)

    def test_sin_permiso_de_edicion_403(self):
        def _deniega_editar(*args, **kwargs):
            return not (kwargs.get('codigo_opcion') == 'recepcion_dte'
                        and kwargs.get('tipo_permiso') == 'puede_editar')
        stock0 = self._stock(self.a_destino)
        resp = self._corregir([{'recepcion_id': self.rec_a.id, 'cantidad_corregida': 5}],
                              side_effect=_deniega_editar)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertEqual(self._stock(self.a_destino), stock0)


# ═════════════════════════════════════════════════════════════════════════
# 4. Ajuste del emisor (pre-recepción)
# ═════════════════════════════════════════════════════════════════════════
class AjusteEmisorPreRecepcionTest(_BaseV3):

    def _ajustar(self, dte, ajustes):
        self._sesion(self.origen)
        return self._post('/app/dte/ajustar_traspaso/', {
            'dte_id': dte.id, 'ajustes': ajustes, 'motivo': 'test v3',
        })

    def test_legacy_sin_traspaso_salida_409_sin_tocar_stock(self):
        dte, (dp,) = self._traspaso([(self.a_origen, 5)], con_salida=False)
        stock0 = self._stock(self.a_origen)
        resp = self._ajustar(dte, [{'dte_producto_id': dp.id, 'nueva_cantidad': 0}])
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertTrue(resp.json().get('legacy_sin_despacho'))
        self.assertEqual(self._stock(self.a_origen), stock0)
        self.assertFalse(Dte.objects.filter(documento_afectado=dte).exists())
        dp.refresh_from_db()
        self.assertEqual(dp.stock, 5)

    def test_error_en_una_linea_revierte_las_demas(self):
        dte, (dp_a, dp_b) = self._traspaso([(self.a_origen, 1), (self.b_origen, 4)])
        stock_a0 = self._stock(self.a_origen)
        resp = self._ajustar(dte, [
            {'dte_producto_id': dp_a.id, 'nueva_cantidad': 0},
            {'dte_producto_id': dp_b.id, 'nueva_cantidad': 9},  # aumento → 400
        ])
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(self._stock(self.a_origen), stock_a0)
        dp_a.refresh_from_db()
        self.assertEqual(dp_a.stock, 1)
        self.assertTrue(dp_a.activo)
        self.assertTrue(Movimientos_Producto.objects.filter(
            dte=dte, ProductoTalla=self.a_origen, concepto='TRASPASO_SALIDA').exists())
        self.assertFalse(Dte.objects.filter(documento_afectado=dte).exists())

    def test_ajuste_pre_recepcion_repone_lote_en_origen(self):
        dte, (dp,) = self._traspaso([(self.a_origen, 5)])
        stock0, lotes0 = self._stock(self.a_origen), self._lotes(self.a_origen)
        resp = self._ajustar(dte, [{'dte_producto_id': dp.id, 'nueva_cantidad': 2}])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.a_origen) - stock0, 3)
        self.assertEqual(self._lotes(self.a_origen) - lotes0, 3)

    def test_error_inesperado_responde_generico_sin_detalle(self):
        dte, (dp,) = self._traspaso([(self.a_origen, 5)], tipo_documento='FACTURA ELECTRONICA')
        stock0 = self._stock(self.a_origen)
        with mock.patch('app.views.puede_emitir_nota_credito',
                        side_effect=RuntimeError('detalle-interno-xyz')):
            resp = self._ajustar(dte, [{'dte_producto_id': dp.id, 'nueva_cantidad': 2}])
        self.assertEqual(resp.status_code, 500, resp.content)
        self.assertNotIn('detalle-interno-xyz', resp.content.decode())
        self.assertEqual(resp.json()['error'],
                         'Error al ajustar el DTE. No se registró ningún cambio.')
        self.assertEqual(self._stock(self.a_origen), stock0)


# ═════════════════════════════════════════════════════════════════════════
# 5. Rechazo / rehabilitación / cancelación: lotes y doble envío
# ═════════════════════════════════════════════════════════════════════════
class RechazoCancelacionLotesTest(_BaseV3):

    def setUp(self):
        super().setUp()
        # Capa FIFO coherente con el stock del origen DESPUÉS del despacho.
        self.dte, (self.dp,) = self._traspaso([(self.a_origen, 5)])
        crear_lote_fifo(self.a_origen, cantidad=self._stock(self.a_origen), costo_unitario=100)

    def _rechazar(self):
        self._sesion(self.destino)
        return self._post('/app/dte/rechazar_recepcion/', {
            'dte_id': self.dte.id, 'motivo_rechazo': 'No llegó',
        })

    def test_rechazo_repone_lote_y_rehabilitar_lo_consume(self):
        stock0, lotes0 = self._stock(self.a_origen), self._lotes(self.a_origen)
        self.assertEqual(self._rechazar().status_code, 200)
        self.assertEqual(self._stock(self.a_origen) - stock0, 5)
        self.assertEqual(self._lotes(self.a_origen) - lotes0, 5)

        self._sesion(self.origen)
        resp = self._post('/app/dte/rehabilitar_rechazado/', {'dte_id': self.dte.id})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.a_origen), stock0)
        self.assertEqual(self._lotes(self.a_origen), lotes0)

    def test_cancelar_repone_lote_y_no_se_aplica_dos_veces(self):
        stock0, lotes0 = self._stock(self.a_origen), self._lotes(self.a_origen)
        self._sesion(self.origen)
        resp = self._post('/app/dte/cancelar_traspaso/', {
            'dte_id': self.dte.id, 'motivo': 'Error de emisión'})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.a_origen) - stock0, 5)
        self.assertEqual(self._lotes(self.a_origen) - lotes0, 5)

        resp = self._post('/app/dte/cancelar_traspaso/', {
            'dte_id': self.dte.id, 'motivo': 'Otra vez'})
        self.assertIn(resp.status_code, (400, 409), resp.content)
        self.assertEqual(self._stock(self.a_origen) - stock0, 5)
        self.assertEqual(self._lotes(self.a_origen) - lotes0, 5)

    def test_cancelar_despues_de_rechazo_no_duplica_lote(self):
        stock0, lotes0 = self._stock(self.a_origen), self._lotes(self.a_origen)
        self.assertEqual(self._rechazar().status_code, 200)
        self._sesion(self.origen)
        resp = self._post('/app/dte/cancelar_traspaso/', {
            'dte_id': self.dte.id, 'motivo': 'Se anula'})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.a_origen) - stock0, 5)
        self.assertEqual(self._lotes(self.a_origen) - lotes0, 5)
