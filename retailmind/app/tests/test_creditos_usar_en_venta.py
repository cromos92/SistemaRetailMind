"""
Tests del débito de crédito post-venta (`usar_credito_en_venta`).

Contexto
--------
El POS cierra la venta en `registrar_pagos_ticket` y recién DESPUÉS el
navegador llama a `/app/api/creditos/usar-en-venta/` para debitar el crédito.
El 04-08-2026 ese endpoint empezó a exigir que el crédito fuera de la misma
empresa y sucursal de la sesión (guard contra `credito_id` arbitrario), pero
`validar_codigo_credito` no exige lo mismo: el cajero validaba el código,
cobraba, y el débito rebotaba con 403 sin log — boleta emitida, crédito con el
cupo íntegro (consumos huérfanos del 06/07/25-08-2026, $256.960).

Comportamiento esperado tras el fix: fuera del alcance de la sesión el débito
se acepta SOLO si una venta real ya cobrada lo respalda (pago de ticket
PAGADO con método de crédito, mismo monto y notas que mencionan el
`numero_credito`) y esa venta no fue debitada antes. Sin venta que lo
respalde, el 403 original se mantiene.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    Cliente, CreditoTrabajador, PagoCreditoTrabajador, Ticket, TicketDetallePago,
)

from .factories import crear_empresa, crear_sucursal, setup_entorno_completo


class UsarCreditoEnVentaTest(TestCase):

    def setUp(self):
        # Sesión del cajero: empresa/sucursal "de la venta" (rol vendedor,
        # sin override de sucursales -> alcance 'actual').
        self.env = setup_entorno_completo()
        self.user = self.env['user']
        self.empresa_venta = self.env['empresa']
        self.sucursal_venta = self.env['sucursal']

        # El crédito se emitió en OTRA empresa y OTRA tienda (el caso real:
        # CR-2026-0027 emitido en NICK1/Importadora, gastado en PAO4/Paola).
        self.empresa_emisora = crear_empresa(
            nombre='Importadora Test', rut='76.111.111-1')
        self.sucursal_emisora = crear_sucursal(
            empresa=self.empresa_emisora, alias='NICK1')

        self.beneficiaria = Cliente.objects.create(
            nombre='Najara', apellido='Vidal', rut='12.345.678-5')
        self.credito = self._crear_credito('CR-2026-9027', self.empresa_emisora,
                                           self.sucursal_emisora)

        self.client.force_login(self.user)
        session = self.client.session
        session['idEmpresaActual'] = self.empresa_venta.id
        session['idSucursalActual'] = self.sucursal_venta.id
        session.save()

        self.url = reverse('usar_credito_en_venta')

    def _crear_credito(self, numero, empresa, sucursal, monto=50000):
        return CreditoTrabajador.objects.create(
            numero_credito=numero,
            beneficiario=self.beneficiaria,
            empresa_origen=empresa,
            sucursal=sucursal,
            monto_solicitado=Decimal(monto),
            monto_aprobado=Decimal(monto),
            estado='ACTIVO',
            solicitado_por=self.user,
            fecha_vencimiento=timezone.localdate() + timedelta(days=30),
        )

    def _crear_venta_respaldada(self, credito, correlativo=122980, monto=49990):
        ticket = Ticket.objects.create(
            vendedor=self.env['vendedor'],
            sucursal=self.sucursal_venta,
            correlativo=correlativo,
            estado='PAGADO',
            subTotal=monto,
            total=monto,
            responsable='cajera',
        )
        TicketDetallePago.objects.create(
            ticket=ticket,
            metodo_pago='CREDITO_TRABAJADOR',
            monto=monto,
            notas=f'Crédito {credito.numero_credito}',
        )
        return ticket

    def _debitar(self, credito, monto=49990, ticket_id=122980,
                 boleta='BOLETA ELECTRONICA-551194'):
        payload = {
            'credito_id': credito.id,
            'monto_usado': monto,
            'ticket_id': ticket_id,
            'numero_boleta': boleta,
            'folio_documento': boleta.rsplit('-', 1)[-1],
        }
        return self.client.post(
            self.url, data=json.dumps(payload), content_type='application/json')

    # ---------- el caso que dejó los huérfanos ----------

    def test_debito_cross_tienda_con_venta_respaldada_se_acepta(self):
        self._crear_venta_respaldada(self.credito)

        resp = self._debitar(self.credito)

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'], data)

        pago = PagoCreditoTrabajador.objects.get(credito=self.credito)
        self.assertEqual(pago.referencia_pago, 'BOLETA ELECTRONICA-551194')
        self.assertEqual(pago.monto_pago, Decimal('49990'))
        self.assertEqual(pago.metodo_pago, 'CREDITO_TRABAJADOR')
        self.assertIn('Ticket #122980', pago.observaciones)
        # La sucursal de cobro es la de la SESIÓN (donde se vendió).
        self.assertEqual(pago.sucursal_cobro_id, self.sucursal_venta.id)

        self.credito.refresh_from_db()
        self.assertEqual(self.credito.monto_pagado, Decimal('49990'))
        self.assertEqual(self.credito.estado, 'ACTIVO')  # saldo $10

    def test_debito_sin_venta_que_lo_respalde_mantiene_403(self):
        """El guard original sigue vivo: un credito_id arbitrario no se debita."""
        resp = self._debitar(self.credito)

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(PagoCreditoTrabajador.objects.count(), 0)
        self.credito.refresh_from_db()
        self.assertEqual(self.credito.monto_pagado, Decimal('0'))

    def test_debito_con_monto_distinto_al_de_la_venta_mantiene_403(self):
        """El respaldo exige el monto EXACTO del pago del ticket."""
        self._crear_venta_respaldada(self.credito, monto=49990)

        resp = self._debitar(self.credito, monto=30000)

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(PagoCreditoTrabajador.objects.count(), 0)

    # ---------- idempotencia ----------

    def test_reintento_exacto_no_duplica_el_debito(self):
        self._crear_venta_respaldada(self.credito)
        self.assertEqual(self._debitar(self.credito).status_code, 200)

        resp = self._debitar(self.credito)

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertTrue(data.get('duplicado'), data)
        self.assertEqual(PagoCreditoTrabajador.objects.count(), 1)
        self.credito.refresh_from_db()
        self.assertEqual(self.credito.monto_pagado, Decimal('49990'))

    def test_segundo_debito_de_la_misma_venta_con_otra_referencia_403(self):
        """Cambiar la boleta del payload no permite debitar la venta 2 veces."""
        self._crear_venta_respaldada(self.credito)
        self.assertEqual(self._debitar(self.credito).status_code, 200)

        resp = self._debitar(self.credito, boleta='BOLETA ELECTRONICA-999999')

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(PagoCreditoTrabajador.objects.count(), 1)

    def test_variante_textual_del_ticket_no_permite_segundo_debito(self):
        """'0122980', '1_22980', etc. son la MISMA venta: el ticket_id se
        canoniza a int antes de registrar y de chequear, así que variar el
        texto del número no evade el bloqueo de venta-ya-debitada."""
        self._crear_venta_respaldada(self.credito)
        self.assertEqual(self._debitar(self.credito).status_code, 200)

        for variante in ('0122980', '1_22980', ' 122980'):
            resp = self._debitar(self.credito, ticket_id=variante,
                                 boleta=f'BOLETA ELECTRONICA-9{variante.strip()}')
            self.assertEqual(resp.status_code, 403, (variante, resp.content))
        self.assertEqual(PagoCreditoTrabajador.objects.count(), 1)

    def test_ticket_id_string_se_registra_canonizado(self):
        """Si el primer débito llega con el correlativo como texto raro, las
        observaciones guardan la forma canónica y el bloqueo aplica igual
        para un segundo intento con el int limpio."""
        self._crear_venta_respaldada(self.credito)

        resp = self._debitar(self.credito, ticket_id='0122980')
        self.assertEqual(resp.status_code, 200, resp.content)
        pago = PagoCreditoTrabajador.objects.get(credito=self.credito)
        self.assertIn('Ticket #122980 ', pago.observaciones + ' ')
        self.assertNotIn('#0122980', pago.observaciones)

        resp = self._debitar(self.credito, ticket_id=122980,
                             boleta='BOLETA ELECTRONICA-999999')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(PagoCreditoTrabajador.objects.count(), 1)

    def test_abono_manual_que_menciona_el_ticket_no_bloquea_el_debito(self):
        """Solo los pagos de CONSUMO cuentan como venta-ya-debitada: un abono
        EFECTIVO con observaciones libres no debe rebotar el débito real."""
        self._crear_venta_respaldada(self.credito)
        PagoCreditoTrabajador.objects.create(
            credito=self.credito,
            numero_pago='AB-1',
            monto_pago=Decimal('5000'),
            fecha_pago=timezone.localdate(),
            metodo_pago='EFECTIVO',
            observaciones='Ajuste manual Ticket #122980',
            registrado_por=self.user,
        )

        resp = self._debitar(self.credito, monto=44990)
        # El abono EFECTIVO subió monto_pagado a 5000 (saldo 45000): el
        # respaldo exige el monto del pago del ticket, así que se ajusta la
        # venta respaldada a 44990 para este caso.
        self.assertEqual(resp.status_code, 403)  # monto 44990 != pago 49990

        TicketDetallePago.objects.filter(
            ticket__correlativo=122980).update(monto=44990)
        resp = self._debitar(self.credito, monto=44990)
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_correlativo_prefijo_de_otro_no_bloquea_el_debito(self):
        """'Ticket #12298' registrado no debe matchear con el ticket 122980."""
        otro = self._crear_credito('CR-2026-9028', self.empresa_emisora,
                                   self.sucursal_emisora, monto=200000)
        self._crear_venta_respaldada(otro, correlativo=12298, monto=10000)
        self._crear_venta_respaldada(otro, correlativo=122980, monto=49990)
        self.assertEqual(
            self._debitar(otro, monto=10000, ticket_id=12298,
                          boleta='BOLETA ELECTRONICA-100').status_code, 200)

        resp = self._debitar(otro, monto=49990, ticket_id=122980,
                             boleta='BOLETA ELECTRONICA-101')

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(PagoCreditoTrabajador.objects.filter(credito=otro).count(), 2)

    # ---------- el alcance normal no cambia ----------

    def test_usuario_con_alcance_debita_sin_necesidad_de_respaldo(self):
        """Mismo comportamiento de siempre para el crédito de la propia tienda."""
        propio = self._crear_credito('CR-2026-9029', self.empresa_venta,
                                     self.sucursal_venta)

        resp = self._debitar(propio, boleta='BOLETA ELECTRONICA-777')

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'])
        self.assertEqual(
            PagoCreditoTrabajador.objects.filter(credito=propio).count(), 1)
