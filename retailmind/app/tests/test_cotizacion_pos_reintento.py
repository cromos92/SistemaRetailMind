"""
Cobro de una cotización desde el POS: reintentos, cobros Mercado Pago bajo
`COT-…` y tickets pendientes retomados desde el dashboard.

Regresión del 22-09-2026 (CLUB DEPORTIVO LAS ROCAS, $1.077.280): el cobro en
la Point se creó bajo el número de la cotización, el cierre buscaba el respaldo
solo por el número del ticket nuevo (MP_SIN_RESPALDO) y cada reintento creaba
otro ticket pendiente (8 seguidos). El cajero terminó pagando uno de esos
tickets desde el dashboard, que saltó al paso 3 con el radio en Boleta.

Correr en BD local desechable:
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_cotizacion_pos_reintento
"""
import json

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from app.models import (
    Cotizacion_Empresa, Cotizacion_Empresa_Detalle, Ticket, Ticket_Productos,
    TransaccionMercadoPago,
)
from app.services import mercadopago_service as mp
from app.views_modulo_ventas import construir_ticket_data, _rut_parece_empresa

from .factories import setup_entorno_completo
from .test_mercadopago_pos import _config, _transaccion


NUMERO_COT = 'COT-TEST-0001'


class _BaseCotizacionPOS(TestCase):

    def setUp(self):
        self.entorno = setup_entorno_completo()
        self.sucursal = self.entorno['sucursal']
        self.empresa = self.entorno['empresa']
        self.vendedor = self.entorno['vendedor']
        self.user = self.entorno['user']
        self.producto_talla = self.entorno['producto_talla']  # stock=10
        self.config_mp = _config(self.sucursal)

        hoy = timezone.localdate()
        self.cotizacion = Cotizacion_Empresa.objects.create(
            sucursal=self.sucursal,
            cliente=self.empresa,
            vendedor=self.vendedor,
            usuario_creador=self.user,
            numero_cotizacion=NUMERO_COT,
            fecha_emision=hoy,
            fecha_validez=hoy,
            total=40000,
        )
        self.detalle = Cotizacion_Empresa_Detalle.objects.create(
            cotizacion=self.cotizacion,
            numero_linea=1,
            descripcion='Zapatilla Test',
            cantidad=2,
            precio_unitario=20000,
            subtotal=40000,
        )

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

    # ── helpers ──────────────────────────────────────────────────────────

    def _productos(self):
        return [{
            'sku': self.producto_talla.sku,
            'producto_talla_id': self.producto_talla.id,
            'articulo': 'Zapatilla Test',
            'cantidad': 2,
            'precio_unitario': 20000,
            'descuento_unitario': 0,
            'subtotal': 40000,
            'cotizacion_item_id': self.detalle.id,
        }]

    def _payload(self, pagos=None, cotizacion_id=True):
        payload = {
            'cliente': {'rut': self.empresa.rut, 'nombre': self.empresa.nombre,
                        'giro': self.empresa.giro},
            'estado': 'PAGADO',
            'tipo_documento': '',
            'productos': self._productos(),
            'pagos': pagos or [{'metodo_pago': 'MP_POINT', 'monto': 40000,
                                'origen_pago': 'POS_INTEGRADO', 'voucher': ''}],
        }
        if cotizacion_id:
            payload['cotizacion_id'] = self.cotizacion.id
        return payload

    def _post(self, correlativo, payload):
        return self.client.post(
            reverse('registrar_pagos_ticket', args=[correlativo]),
            data=json.dumps(payload), content_type='application/json',
        )

    def _cobro_aprobado_bajo_cot(self, monto=40000, payment_id='PAYCOT1'):
        return _transaccion(self.config_mp, correlativo=NUMERO_COT, monto=monto,
                            canal='POINT', payment_id=payment_id)


class ReintentoCotizacionTest(_BaseCotizacionPOS):

    def test_reintentos_reutilizan_el_mismo_ticket_pendiente(self):
        # 1er intento: el pago MP no tiene respaldo → 400, pero el ticket ya nació.
        r1 = self._post(NUMERO_COT, self._payload())
        self.assertEqual(r1.status_code, 400, r1.content)
        self.assertEqual(r1.json().get('error_tipo'), 'MP_SIN_RESPALDO')
        pendientes = Ticket.objects.filter(sucursal=self.sucursal, estado='PENDIENTE')
        self.assertEqual(pendientes.count(), 1)
        ticket = pendientes.get()
        self.assertEqual(ticket.ticket_productos.count(), 1)
        self.assertEqual(ticket.numero_cotizacion_origen, NUMERO_COT)

        # 2º intento (mismo error): NO nace otro ticket ni se duplican líneas.
        r2 = self._post(NUMERO_COT, self._payload())
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(Ticket.objects.filter(sucursal=self.sucursal).count(), 1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, 'PENDIENTE')
        self.assertEqual(ticket.ticket_productos.count(), 1)

        # 3er intento con el cobro aprobado en la Point bajo COT-…: cierra
        # sobre el MISMO ticket y consume ese cobro.
        trx = self._cobro_aprobado_bajo_cot()
        r3 = self._post(NUMERO_COT, self._payload())
        self.assertEqual(r3.status_code, 200, r3.content)
        self.assertTrue(r3.json().get('success'))
        self.assertEqual(Ticket.objects.filter(sucursal=self.sucursal).count(), 1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, 'PAGADO')
        self.assertEqual(ticket.pagos.count(), 1)
        trx.refresh_from_db()
        self.assertTrue(trx.consumida)
        self.assertEqual(trx.ticket_id, ticket.id)

    def test_cobro_bajo_cot_respalda_el_cierre_al_primer_intento(self):
        trx = self._cobro_aprobado_bajo_cot()
        r = self._post(NUMERO_COT, self._payload())
        self.assertEqual(r.status_code, 200, r.content)
        ticket = Ticket.objects.get(sucursal=self.sucursal)
        self.assertEqual(ticket.estado, 'PAGADO')
        trx.refresh_from_db()
        self.assertTrue(trx.consumida)
        self.assertEqual(trx.ticket_id, ticket.id)

    def test_cobro_aprobado_bajo_cot_sin_cargar_bloquea_el_cierre(self):
        """Guard inverso: la Point ya cobró bajo COT-… y la venta se quiere
        cerrar con otro medio → se rechaza (plata sin respaldo)."""
        self._cobro_aprobado_bajo_cot()
        pagos = [{'metodo_pago': 'EFECTIVO', 'monto': 40000}]
        r = self._post(NUMERO_COT, self._payload(pagos=pagos))
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json().get('error_tipo'), 'MP_COBRO_SIN_USAR')


class TicketRetomadoTest(_BaseCotizacionPOS):
    """El cajero toma el ticket pendiente desde el dashboard / por número."""

    def _ticket_pendiente_de_intento_fallido(self):
        r = self._post(NUMERO_COT, self._payload())
        self.assertEqual(r.status_code, 400)
        return Ticket.objects.get(sucursal=self.sucursal, estado='PENDIENTE')

    def test_ticket_expone_su_cotizacion_y_cliente_empresa(self):
        ticket = self._ticket_pendiente_de_intento_fallido()
        data = construir_ticket_data(ticket)
        self.assertEqual(data['cotizacion_id'], self.cotizacion.id)
        self.assertEqual(data['numero_cotizacion'], NUMERO_COT)
        self.assertEqual(data['cotizacion']['numero_cotizacion'], NUMERO_COT)
        self.assertTrue(data['cliente_es_empresa'])

        # Un ticket normal no trae cotización.
        normal = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=999,
            estado='PENDIENTE', subTotal=0, descuento=0, total=0,
            responsable=self.user.username, cliente_rut='12.345.678-5',
        )
        data_normal = construir_ticket_data(normal)
        self.assertIsNone(data_normal['cotizacion_id'])
        self.assertEqual(data_normal['numero_cotizacion'], '')
        self.assertFalse(data_normal['cliente_es_empresa'])

    def test_en_curso_del_ticket_numerico_ve_el_cobro_bajo_cot(self):
        ticket = self._ticket_pendiente_de_intento_fallido()
        self._cobro_aprobado_bajo_cot()
        self.assertEqual(
            mp.correlativos_equivalentes_de_ticket(self.sucursal.id, str(ticket.correlativo)),
            [str(ticket.correlativo), NUMERO_COT],
        )
        r = self.client.get(reverse('mp_cobros_vivos_ticket', args=[ticket.correlativo]))
        self.assertEqual(r.status_code, 200, r.content)
        cobros = r.json()['cobros']
        self.assertEqual(len(cobros), 1)
        self.assertEqual(cobros[0]['external_reference'],
                         TransaccionMercadoPago.objects.get().external_reference)
        self.assertTrue(r.json()['hay_aprobado_sin_usar'])

    def test_pagar_ticket_numerico_con_cotizacion_id_consume_el_cobro_cot(self):
        ticket = self._ticket_pendiente_de_intento_fallido()
        trx = self._cobro_aprobado_bajo_cot()
        r = self._post(ticket.correlativo, self._payload())
        self.assertEqual(r.status_code, 200, r.content)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, 'PAGADO')
        self.assertEqual(Ticket.objects.filter(sucursal=self.sucursal).count(), 1)
        trx.refresh_from_db()
        self.assertTrue(trx.consumida)
        self.assertEqual(trx.ticket_id, ticket.id)
        # La línea se actualizó en su lugar (no se duplicó ni se recreó). La marca
        # textual no sobrevive al cierre (el POS no reenvía observaciones_adicionales).
        self.assertEqual(ticket.ticket_productos.count(), 1)

    def test_cotizacion_id_ajeno_se_ignora(self):
        """Un cotizacion_id cuyas líneas no son las del ticket no enlaza nada
        (ni consume cobros hechos bajo su número)."""
        ticket = self._ticket_pendiente_de_intento_fallido()
        otra = Cotizacion_Empresa.objects.create(
            sucursal=self.sucursal, cliente=self.empresa, vendedor=self.vendedor,
            usuario_creador=self.user, numero_cotizacion='COT-TEST-0002',
            fecha_emision=timezone.localdate(), fecha_validez=timezone.localdate(),
            total=40000,
        )
        Cotizacion_Empresa_Detalle.objects.create(
            cotizacion=otra, numero_linea=1, descripcion='Otra', cantidad=2,
            precio_unitario=20000, subtotal=40000,
        )
        _transaccion(self.config_mp, correlativo='COT-TEST-0002', monto=40000,
                     canal='POINT', payment_id='PAYOTRA')
        payload = self._payload()
        payload['cotizacion_id'] = otra.id
        r = self._post(ticket.correlativo, payload)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json().get('error_tipo'), 'MP_SIN_RESPALDO')
        self.assertFalse(TransaccionMercadoPago.objects.get(
            correlativo_ticket='COT-TEST-0002').consumida)


class DashboardPendientesTest(_BaseCotizacionPOS):

    def _filas_dashboard(self):
        r = self.client.get(reverse('dashboard_stats'))
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertTrue(data.get('success'), data)
        return data['tickets'], data['tickets_pendientes']

    def test_marca_origen_documento_sugerido_y_repetidos(self):
        r = self._post(NUMERO_COT, self._payload())
        self.assertEqual(r.status_code, 400)
        ticket_cot = Ticket.objects.get(sucursal=self.sucursal, estado='PENDIENTE')

        # Dos pendientes "iguales" de una persona natural (mismo RUT y total).
        for correlativo in (501, 502):
            t = Ticket.objects.create(
                vendedor=self.vendedor, sucursal=self.sucursal, correlativo=correlativo,
                estado='PENDIENTE', subTotal=19990, descuento=0, total=19990,
                responsable=self.user.username, cliente_rut='12.345.678-5',
                cliente_nombre='Juan Pérez',
            )
            Ticket_Productos.objects.create(
                idTicket=t, ProductoTalla=self.producto_talla, stock=1,
                precio=19990, subtotal=19990, precio_original=19990,
            )

        tabla, wizard = self._filas_dashboard()
        por_correlativo = {f['correlativo']: f for f in tabla}
        fila_cot = por_correlativo[ticket_cot.correlativo]
        self.assertEqual(fila_cot['numero_cotizacion'], NUMERO_COT)
        self.assertEqual(fila_cot['cotizacion_id'], self.cotizacion.id)
        self.assertEqual(fila_cot['doc_sugerido'], 'FACTURA')
        self.assertEqual(fila_cot['repetido'], 0)
        for correlativo in (501, 502):
            fila = por_correlativo[correlativo]
            self.assertEqual(fila['numero_cotizacion'], '')
            self.assertEqual(fila['doc_sugerido'], 'BOLETA')
            self.assertEqual(fila['repetido'], 2)

        # La lista del wizard trae los mismos campos.
        fila_w = {f['correlativo']: f for f in wizard}[ticket_cot.correlativo]
        self.assertEqual(fila_w['numero_cotizacion'], NUMERO_COT)
        self.assertEqual(fila_w['doc_sugerido'], 'FACTURA')


class RutEmpresaTest(TestCase):

    def test_rut_parece_empresa(self):
        self.assertTrue(_rut_parece_empresa('65043946-5'))     # club deportivo
        self.assertTrue(_rut_parece_empresa('76.000.000-0'))
        self.assertFalse(_rut_parece_empresa('12.345.678-5'))
        self.assertFalse(_rut_parece_empresa(''))
        self.assertFalse(_rut_parece_empresa(None))
