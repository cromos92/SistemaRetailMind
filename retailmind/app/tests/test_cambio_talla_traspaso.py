"""
Tests de `cambiar_talla_dte_traspaso_api` (POST /app/dte/cambiar_talla/).

El endpoint mueve unidades de una talla a otra DENTRO de un traspaso que aún
no fue recepcionado, sin emitir documento hijo y sin mover los totales del
folio.

Lo que se cubre, y por qué:

1. Swap básico: la línea origen baja, la destino se crea, el stock se mueve en
   la bodega emisora y los movimientos TRASPASO_SALIDA quedan espejando las
   líneas.
2. **fecha/hora heredadas** — el test que NO se puede borrar. Si el movimiento
   nuevo se fecha HOY en vez de heredar la fecha del despacho original, nada
   falla visiblemente pero las unidades saltan de período en todos los
   reportes fechados por kardex.
3. Invariante de totales: el folio declarado no cambia de valor.
4. Fusión contra una línea existente de la misma talla (no duplicar).
5. Vaciar la línea origen: queda inactiva y su movimiento en CANCELADO.
6. Idempotencia por token: reintentar no duplica.
7. Guards: post-recepción, sucursal no emisora, stock insuficiente, talla de
   otro producto, línea sin movimiento de despacho (fail-closed), y mover más
   de lo que la línea tiene.
8. Rotación neta en una sola pasada (37→38 y 38→39).
"""
import json
from decimal import Decimal
from unittest import mock

from django.test import TestCase, Client
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, Producto_Talla, Movimientos_Producto,
)
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla,
)

URL = '/app/dte/cambiar_talla/'


def _patch_permisos():
    """`requiere_permiso('recepcion_dte','puede_aprobar')` depende de la BD de
    permisos; en tests se parcha a True, igual que en test_ajuste_traspaso."""
    return (
        mock.patch('app.views.PermisoRol.tiene_permiso', return_value=True),
        mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True),
    )


class _BaseCambioTalla(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.origen)

        # Un producto en la bodega origen con tres tallas hermanas.
        self.producto, self.t37 = crear_producto_con_talla(
            self.origen, articulo='Zapatilla Test', talla='37', sku=3701, stock=50,
        )
        self.t38 = Producto_Talla.objects.create(
            producto=self.producto, sku=3801, stock=20, talla='38',
        )
        self.t39 = Producto_Talla.objects.create(
            producto=self.producto, sku=3901, stock=10, talla='39',
        )
        # Producto DISTINTO en la misma bodega (para el guard de producto).
        self.otro_producto, self.otra_talla = crear_producto_con_talla(
            self.origen, articulo='Otro Articulo', talla='40', sku=4001, stock=10,
        )

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.origen.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _crear_traspaso(self, lineas, tipo_documento='FACTURA ELECTRONICA',
                        numero=17117, fecha='2026-08-24', hora='09:30:00'):
        """Crea un traspaso EMITIDO como lo haría `emitir_dte`.

        `lineas` = [(producto_talla, cantidad, precio), ...]
        Descuenta el stock de la bodega origen, igual que la emisión real.
        """
        total_uds = sum(c for _, c, _ in lineas)
        total_neto = sum(c * p for _, c, p in lineas)
        dte = Dte.objects.create(
            emisor=self.empresa,
            receptor=self.empresa,
            numero_documento=numero,
            tipo_documento=tipo_documento,
            monto_neto=Decimal(total_neto),
            monto_con_iva=Decimal(int(round(total_neto * 1.19))),
            estado_pago='PENDIENTE',
            estado_dte='EMITIDO',
            responsable='tester',
            fecha_emision=fecha,
            fecha_vencimiento=fecha,
            diasCredito=0,
            bultos=1,
            unidades_productos=total_uds,
            tipo_transaccion='TRASPASO',
            sucursal=self.origen,
        )
        dps = []
        for talla, cantidad, precio in lineas:
            dps.append(Dte_Productos.objects.create(
                dte=dte,
                productoTalla=talla,
                descripcion=f'{talla.producto.articulo} - Talla {talla.talla}',
                costo=100,
                sobreprecio=0,
                precio=precio,
                stock=cantidad,
                activo=True,
            ))
            Movimientos_Producto.objects.create(
                dte=dte,
                ProductoTalla=talla,
                sucursal_origen=self.origen,
                sucursal_destino=self.destino,
                cantidad=-cantidad,
                costo=100,
                sobreprecio=0,
                precio=precio,
                concepto='TRASPASO_SALIDA',
                tipo_movimiento='EGRESO',
                estado='COMPLETADO',
                responsable='tester',
                fecha=fecha,
                hora=hora,
            )
            Producto_Talla.objects.filter(id=talla.id).update(
                stock=Producto_Talla.objects.get(id=talla.id).stock - cantidad
            )
        return dte, dps

    def _post(self, payload):
        p1, p2 = _patch_permisos()
        with p1, p2:
            return self.client.post(
                URL, data=json.dumps(payload), content_type='application/json',
            )


class CambioTallaFelizTest(_BaseCambioTalla):
    def test_swap_mueve_linea_stock_y_kardex(self):
        dte, dps = self._crear_traspaso([(self.t37, 45, 1000)])
        dp37 = dps[0]
        self.t37.refresh_from_db(); self.t38.refresh_from_db()
        stock37_antes, stock38_antes = self.t37.stock, self.t38.stock  # 5 y 20

        resp = self._post({
            'dte_id': dte.id,
            'token_operacion': 'tok-1',
            'motivo': 'Se picaron 10 pares en la talla equivocada',
            'cambios': [{'dte_producto_id': dp37.id, 'talla_destino_id': self.t38.id, 'cantidad': 10}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertFalse(data['ya_aplicado'])

        # Línea origen bajó a 35 y sigue activa.
        dp37.refresh_from_db()
        self.assertEqual(dp37.stock, 35)
        self.assertTrue(dp37.activo)

        # Se creó la línea de la talla 38 heredando precio/costo del origen.
        dp38 = Dte_Productos.objects.get(dte=dte, productoTalla=self.t38)
        self.assertEqual(dp38.stock, 10)
        self.assertTrue(dp38.activo)
        self.assertEqual(dp38.precio, dp37.precio)
        self.assertEqual(dp38.costo, dp37.costo)

        # Stock: la 37 recupera 10, la 38 entrega 10.
        self.t37.refresh_from_db(); self.t38.refresh_from_db()
        self.assertEqual(self.t37.stock, stock37_antes + 10)
        self.assertEqual(self.t38.stock, stock38_antes - 10)

        # Kardex: dos egresos que suman las mismas 45 unidades.
        m37 = Movimientos_Producto.objects.get(dte=dte, ProductoTalla=self.t37)
        m38 = Movimientos_Producto.objects.get(dte=dte, ProductoTalla=self.t38)
        self.assertEqual(m37.cantidad, -35)
        self.assertEqual(m38.cantidad, -10)
        self.assertEqual(m38.concepto, 'TRASPASO_SALIDA')
        self.assertEqual(m38.tipo_movimiento, 'EGRESO')
        self.assertEqual(m38.sucursal_destino_id, self.destino.id)
        self.assertEqual(
            abs(m37.cantidad) + abs(m38.cantidad), 45,
            'El total despachado del documento cambió',
        )

        # Queda traza en las referencias.
        dte.refresh_from_db()
        self.assertIn('[CAMBIO TALLA]', dte.referencias)
        self.assertIn('37->38 x10', dte.referencias)

    def test_movimiento_nuevo_hereda_fecha_y_hora_del_despacho(self):
        """EL TEST QUE NO SE PUEDE BORRAR.

        Si el movimiento de la talla nueva se fecha HOY en vez de heredar la
        fecha del despacho original, no falla nada visible: simplemente las
        unidades saltan de período en los reportes fechados por kardex.
        """
        dte, dps = self._crear_traspaso(
            [(self.t37, 45, 1000)], fecha='2026-08-24', hora='09:30:00',
        )
        m_original = Movimientos_Producto.objects.get(dte=dte, ProductoTalla=self.t37)
        fecha_esperada, hora_esperada = m_original.fecha, m_original.hora

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-fecha', 'motivo': 'test fecha',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t38.id, 'cantidad': 10}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        m38 = Movimientos_Producto.objects.get(dte=dte, ProductoTalla=self.t38)
        self.assertEqual(m38.fecha, fecha_esperada,
                         'El movimiento nuevo se re-fechó a hoy: salta de período en los reportes')
        self.assertEqual(m38.hora, hora_esperada)

    def test_totales_del_folio_quedan_invariantes(self):
        dte, dps = self._crear_traspaso([(self.t37, 45, 1000)])
        neto_antes = dte.monto_neto
        iva_antes = dte.monto_con_iva
        uds_antes = dte.unidades_productos

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-inv', 'motivo': 'test invariante',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t38.id, 'cantidad': 10}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        dte.refresh_from_db()
        self.assertEqual(dte.monto_neto, neto_antes)
        self.assertEqual(dte.monto_con_iva, iva_antes)
        self.assertEqual(dte.unidades_productos, uds_antes)

        # Y la suma de las líneas activas tampoco se movió.
        suma = sum(
            dp.stock * dp.precio
            for dp in Dte_Productos.objects.filter(dte=dte, activo=True)
        )
        self.assertEqual(Decimal(suma), neto_antes)

    def test_fusiona_con_linea_existente_de_la_misma_talla(self):
        """La talla 38 ya viene en el DTE: se suma a esa línea, no se duplica."""
        dte, dps = self._crear_traspaso([(self.t37, 45, 1000), (self.t38, 5, 1000)])
        dp37, dp38 = dps

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-fusion', 'motivo': 'fusion',
            'cambios': [{'dte_producto_id': dp37.id, 'talla_destino_id': self.t38.id, 'cantidad': 10}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        self.assertEqual(
            Dte_Productos.objects.filter(dte=dte, productoTalla=self.t38).count(), 1,
            'Se duplicó la línea de la talla 38 en vez de fusionar',
        )
        dp38.refresh_from_db()
        self.assertEqual(dp38.stock, 15)
        m38 = Movimientos_Producto.objects.get(dte=dte, ProductoTalla=self.t38)
        self.assertEqual(m38.cantidad, -15)

    def test_vaciar_linea_la_desactiva_y_cancela_su_movimiento(self):
        dte, dps = self._crear_traspaso([(self.t37, 10, 1000)])
        dp37 = dps[0]

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-vaciar', 'motivo': 'toda la linea',
            'cambios': [{'dte_producto_id': dp37.id, 'talla_destino_id': self.t38.id, 'cantidad': 10}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        dp37.refresh_from_db()
        self.assertEqual(dp37.stock, 0)
        self.assertFalse(dp37.activo)

        m37 = Movimientos_Producto.objects.get(dte=dte, ProductoTalla=self.t37)
        self.assertEqual(m37.cantidad, 0)
        self.assertEqual(m37.estado, 'CANCELADO',
                         'El movimiento vaciado debe quedar CANCELADO, no COMPLETADO en 0')

    def test_rotacion_neta_en_una_sola_pasada(self):
        """37→38 y 38→39 juntos: la 38 no necesita stock propio porque el mismo
        request se lo está reponiendo. Se valida por NETO, no por bruto."""
        dte, dps = self._crear_traspaso([(self.t37, 20, 1000), (self.t38, 20, 1000)])
        dp37, dp38 = dps
        self.t38.refresh_from_db()
        self.assertEqual(
            self.t38.stock, 0,
            'precondición: la 38 queda SIN stock libre — un chequeo por bruto la rechazaría',
        )

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-rot', 'motivo': 'rotacion',
            'cambios': [
                {'dte_producto_id': dp37.id, 'talla_destino_id': self.t38.id, 'cantidad': 5},
                {'dte_producto_id': dp38.id, 'talla_destino_id': self.t39.id, 'cantidad': 5},
            ],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        dp37.refresh_from_db(); dp38.refresh_from_db()
        self.assertEqual(dp37.stock, 15)
        self.assertEqual(dp38.stock, 20)  # -5 que salen, +5 que entran
        dp39 = Dte_Productos.objects.get(dte=dte, productoTalla=self.t39)
        self.assertEqual(dp39.stock, 5)

        dte.refresh_from_db()
        self.assertEqual(dte.unidades_productos, 40)

    def test_idempotencia_por_token(self):
        dte, dps = self._crear_traspaso([(self.t37, 45, 1000)])
        payload = {
            'dte_id': dte.id, 'token_operacion': 'tok-idem', 'motivo': 'idempotente',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t38.id, 'cantidad': 10}],
        }
        r1 = self._post(payload)
        self.assertEqual(r1.status_code, 200, r1.content)
        self.assertFalse(r1.json()['ya_aplicado'])

        r2 = self._post(payload)
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertTrue(r2.json()['ya_aplicado'])

        dps[0].refresh_from_db()
        self.assertEqual(dps[0].stock, 35, 'El reintento volvió a aplicar el cambio')
        dp38 = Dte_Productos.objects.get(dte=dte, productoTalla=self.t38)
        self.assertEqual(dp38.stock, 10)


class CambioTallaGuardsTest(_BaseCambioTalla):
    def test_bloquea_si_ya_fue_recepcionado(self):
        dte, dps = self._crear_traspaso([(self.t37, 45, 1000)])
        dte.fecha_recepcion = '2026-08-25'
        dte.save(update_fields=['fecha_recepcion'])

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-post', 'motivo': 'tarde',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t38.id, 'cantidad': 5}],
        })
        self.assertEqual(resp.status_code, 409)
        self.assertIn('ya fue recepcionado', resp.json()['error'])

    def test_solo_la_sucursal_emisora(self):
        dte, dps = self._crear_traspaso([(self.t37, 45, 1000)])
        session = self.client.session
        session['idSucursalActual'] = self.destino.id
        session.save()

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-403', 'motivo': 'desde destino',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t38.id, 'cantidad': 5}],
        })
        self.assertEqual(resp.status_code, 403)

    def test_stock_insuficiente_en_talla_destino(self):
        dte, dps = self._crear_traspaso([(self.t37, 45, 1000)])
        self.t38.refresh_from_db()  # 20 disponibles

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-stock', 'motivo': 'sin stock',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t38.id, 'cantidad': 30}],
        })
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertTrue(data.get('stock_insuficiente'))
        self.assertEqual(data['disponible_origen'], 20)
        self.assertEqual(data['solicitado'], 30)

    def test_rechaza_talla_de_otro_producto(self):
        dte, dps = self._crear_traspaso([(self.t37, 45, 1000)])

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-otro', 'motivo': 'otro producto',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.otra_talla.id, 'cantidad': 5}],
        })
        self.assertEqual(resp.status_code, 409)
        self.assertIn('MISMO', resp.json()['error'])

    def test_no_se_puede_mover_mas_de_lo_que_tiene_la_linea(self):
        dte, dps = self._crear_traspaso([(self.t37, 10, 1000)])

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-exceso', 'motivo': 'exceso',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t38.id, 'cantidad': 15}],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('solo tiene', resp.json()['error'])

    def test_fail_closed_si_la_linea_no_tiene_movimiento_de_despacho(self):
        """El botón Ajustar acredita stock a ciegas en este caso (doble crédito
        silencioso). Acá se rechaza."""
        dte, dps = self._crear_traspaso([(self.t37, 45, 1000)])
        Movimientos_Producto.objects.filter(dte=dte, ProductoTalla=self.t37).delete()

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-legacy', 'motivo': 'legacy',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t38.id, 'cantidad': 5}],
        })
        # Sin movimiento tampoco hay sucursal destino derivable: el fail-closed
        # puede saltar por cualquiera de los dos guards, ambos son correctos.
        self.assertIn(resp.status_code, (400, 409))
        self.t37.refresh_from_db()
        self.assertEqual(self.t37.stock, 5, 'Se acreditó stock pese a rechazar la operación')

    def test_rechaza_misma_talla_origen_y_destino(self):
        dte, dps = self._crear_traspaso([(self.t37, 45, 1000)])

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-misma', 'motivo': 'misma talla',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t37.id, 'cantidad': 5}],
        })
        self.assertEqual(resp.status_code, 409)
        self.assertIn('misma que la de origen', resp.json()['error'])

    def test_exige_motivo(self):
        dte, dps = self._crear_traspaso([(self.t37, 45, 1000)])

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-sinmotivo', 'motivo': '   ',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t38.id, 'cantidad': 5}],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('motivo', resp.json()['error'].lower())

    def test_no_emite_ningun_documento_hijo(self):
        """La diferencia central con Ajustar: acá no nace NC ni AJUSTE."""
        dte, dps = self._crear_traspaso([(self.t37, 45, 1000)])
        dtes_antes = Dte.objects.count()

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-nohijo', 'motivo': 'sin hijos',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t38.id, 'cantidad': 10}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Dte.objects.count(), dtes_antes, 'Se emitió un documento hijo')
        self.assertFalse(Dte.objects.filter(documento_afectado=dte).exists())


class CambioTallaSinStockTest(_BaseCambioTalla):
    """La talla destino marca 0 en el sistema.

    Caso real (DTE #17110): el documento dice 3 pares de talla 10, pero en el
    bulto iban 2 del 10 y 1 del 10.5. Esa unidad de 10.5 YA salió de la bodega
    — al emitir se descontó del 10, no del 10.5. Si el sistema marca 0 en el
    10.5 es que su stock ya venía mal contado, no que el cambio sea imposible.

    Por eso el bloqueo no puede ser duro: sería dejar el documento mal para
    siempre. Se avisa, se exige confirmación explícita y queda escrito.
    """

    def setUp(self):
        super().setUp()
        # Talla hermana SIN stock en el sistema.
        self.t105 = Producto_Talla.objects.create(
            producto=self.producto, sku=10501, stock=0, talla='10.5',
        )

    def test_por_defecto_bloquea_pero_ofrece_forzar(self):
        dte, dps = self._crear_traspaso([(self.t37, 3, 1000)])

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-ss1', 'motivo': 'llego un 10.5',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t105.id, 'cantidad': 1}],
        })
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertTrue(data['stock_insuficiente'])
        self.assertTrue(data['puede_forzar'], 'La UI necesita saber que se puede confirmar y reintentar')
        self.assertEqual(data['talla'], '10.5')
        self.assertEqual(data['disponible_origen'], 0)
        self.assertEqual(data['solicitado'], 1)

        # No se escribió nada.
        dps[0].refresh_from_db()
        self.assertEqual(dps[0].stock, 3)
        self.t105.refresh_from_db()
        self.assertEqual(self.t105.stock, 0)

    def test_con_permitir_sin_stock_aplica_y_deja_constancia(self):
        dte, dps = self._crear_traspaso([(self.t37, 3, 1000)])
        self.t37.refresh_from_db()
        stock37_antes = self.t37.stock

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-ss2', 'motivo': 'del 10 llegaron 2, el otro era 10.5',
            'permitir_sin_stock': True,
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t105.id, 'cantidad': 1}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(len(data['tallas_forzadas']), 1)
        self.assertIn('queda en -1', data['tallas_forzadas'][0])

        # La línea original baja y nace la del 10.5.
        dps[0].refresh_from_db()
        self.assertEqual(dps[0].stock, 2)
        dp105 = Dte_Productos.objects.get(dte=dte, productoTalla=self.t105)
        self.assertEqual(dp105.stock, 1)

        # El stock del 10.5 queda en negativo: refleja que ya venía mal contado.
        self.t105.refresh_from_db()
        self.assertEqual(self.t105.stock, -1)
        # Y el 37 recupera la unidad que nunca salió.
        self.t37.refresh_from_db()
        self.assertEqual(self.t37.stock, stock37_antes + 1)

        # Queda escrito en el documento.
        dte.refresh_from_db()
        self.assertIn('[STOCK FORZADO]', dte.referencias)
        self.assertIn('10.5', dte.referencias)

        # El total del folio sigue sin moverse.
        self.assertEqual(dte.unidades_productos, 3)
        suma = sum(
            dp.stock * dp.precio
            for dp in Dte_Productos.objects.filter(dte=dte, activo=True)
        )
        self.assertEqual(Decimal(suma), dte.monto_neto)

    def test_el_flag_no_afecta_cuando_si_hay_stock(self):
        """Mandar el flag no debe relajar nada más: con stock suficiente el
        camino es el normal y no se marca nada como forzado."""
        dte, dps = self._crear_traspaso([(self.t37, 10, 1000)])

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-ss3', 'motivo': 'con stock',
            'permitir_sin_stock': True,
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t38.id, 'cantidad': 5}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['tallas_forzadas'], [])
        dte.refresh_from_db()
        self.assertNotIn('[STOCK FORZADO]', dte.referencias)


class CambioTallaHallazgosRevisionTest(_BaseCambioTalla):
    """Casos que salieron de la revisión adversarial del endpoint."""

    def test_kardex_desparejado_se_rechaza_sin_dejar_egreso_positivo(self):
        """Si la línea declara más unidades de las que registra su movimiento de
        despacho, restarle al movimiento lo cruzaría a POSITIVO y quedaría un
        EGRESO con cantidad positiva. Nadie espera esa fila: `rechazar` y
        `cancelar` la leen con abs() y acreditarían unidades inventadas."""
        dte, dps = self._crear_traspaso([(self.t37, 45, 1000)])
        mov = Movimientos_Producto.objects.get(dte=dte, ProductoTalla=self.t37)
        # Folio desparejado: la línea dice 45, el kardex solo registra 5.
        mov.cantidad = -5
        mov.save(update_fields=['cantidad'])

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-kardex', 'motivo': 'folio desparejado',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t38.id, 'cantidad': 10}],
        })
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertTrue(resp.json().get('kardex_desparejado'))

        # Nada se movió, y el movimiento sigue siendo un egreso.
        mov.refresh_from_db()
        self.assertEqual(mov.cantidad, -5)
        self.assertLessEqual(mov.cantidad, 0, 'Quedó un EGRESO con cantidad positiva')
        dps[0].refresh_from_db()
        self.assertEqual(dps[0].stock, 45)

    def test_el_lote_de_reingreso_conserva_la_fecha_del_despacho(self):
        """`crear_lote(fecha_ingreso=...)` es un no-op porque el campo es
        auto_now_add. Sin corregirlo a mano, las unidades devueltas nacen con
        fecha de HOY y, como el FIFO ordena por fecha_ingreso, se van al final
        de la cola y sobreviven en aging/liquidación como stock fresco."""
        from app.models import LoteProducto

        dte, dps = self._crear_traspaso(
            [(self.t37, 45, 1000)], fecha='2026-08-24', hora='09:30:00',
        )
        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-lote', 'motivo': 'antiguedad fifo',
            'cambios': [{'dte_producto_id': dps[0].id, 'talla_destino_id': self.t38.id, 'cantidad': 10}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)

        lote = LoteProducto.objects.filter(producto_talla=self.t37).order_by('-id').first()
        self.assertIsNotNone(lote, 'No se creó el lote de reingreso')
        self.assertEqual(lote.cantidad_disponible, 10)
        self.assertEqual(
            timezone.localtime(lote.fecha_ingreso).date().isoformat(), '2026-08-24',
            'El lote nació con la fecha de hoy: pierde su antigüedad FIFO',
        )

    def test_dos_lineas_de_distinto_precio_a_la_misma_talla_nueva(self):
        """Ambas ven `linea_destino=None`, así que el guard de precio por línea
        no las frena; sin este chequeo solo saltaba el assert final, con un
        rollback y un mensaje que no dice qué hacer."""
        dte, dps = self._crear_traspaso([(self.t37, 10, 1000), (self.t38, 10, 2000)])

        resp = self._post({
            'dte_id': dte.id, 'token_operacion': 'tok-precios', 'motivo': 'dos precios',
            'cambios': [
                {'dte_producto_id': dps[0].id, 'talla_destino_id': self.t39.id, 'cantidad': 2},
                {'dte_producto_id': dps[1].id, 'talla_destino_id': self.t39.id, 'cantidad': 2},
            ],
        })
        self.assertEqual(resp.status_code, 409, resp.content)
        data = resp.json()
        self.assertTrue(data.get('precio_distinto'))
        self.assertIn('dos pasadas', data['error'])

        # Nada se escribió.
        self.assertFalse(Dte_Productos.objects.filter(dte=dte, productoTalla=self.t39).exists())
