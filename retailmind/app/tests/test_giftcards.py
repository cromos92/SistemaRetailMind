"""
Tests de Gift Cards: emisión, código único, consumo con idempotencia,
saldo insuficiente, anulación, reversa, motivo/descripción, bloqueo/desbloqueo,
edición y expiración.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from app.models import GiftCard, MovimientoGiftCard, Ticket, TicketDetallePago
from app.services import giftcard_service
from app.services.giftcard_service import GiftCardError

from .factories import crear_sucursal, crear_vendedor, crear_empresa


class GiftCardServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)
        cls.vendedor = crear_vendedor(empresa=cls.empresa)

    def _crear_ticket(self, total=10000, correlativo=1):
        return Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal,
            correlativo=correlativo, estado='PAGADO',
            subTotal=total, total=total, responsable='tester',
        )

    def test_emision_genera_codigo_y_saldo(self):
        gc = giftcard_service.emitir(20000, sucursal=self.sucursal)
        self.assertTrue(gc.codigo.startswith('GC-'))
        self.assertEqual(gc.saldo_actual, 20000)
        self.assertEqual(gc.estado, 'ACTIVA')
        # Ledger: debe existir el movimiento EMISION
        self.assertEqual(gc.movimientos.filter(tipo='EMISION').count(), 1)
        # saldo denormalizado == saldo del ledger
        self.assertEqual(gc.saldo_actual, gc.saldo_calculado)

    def test_codigos_son_unicos(self):
        codigos = {giftcard_service.emitir(1000).codigo for _ in range(15)}
        self.assertEqual(len(codigos), 15)

    def test_consumo_descuenta_saldo(self):
        gc = giftcard_service.emitir(10000)
        ticket = self._crear_ticket()
        pago = TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='GIFTCARD', voucher=gc.codigo, monto=3000,
        )
        giftcard_service.consumir(gc.codigo, 3000, ticket=ticket, pago_ticket=pago)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 7000)
        self.assertEqual(gc.estado, 'ACTIVA')

    def test_consumo_total_marca_agotada(self):
        gc = giftcard_service.emitir(5000)
        ticket = self._crear_ticket()
        pago = TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='GIFTCARD', voucher=gc.codigo, monto=5000,
        )
        giftcard_service.consumir(gc.codigo, 5000, ticket=ticket, pago_ticket=pago)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 0)
        self.assertEqual(gc.estado, 'AGOTADA')

    def test_consumo_es_idempotente(self):
        """Reintentar el mismo pago no descuenta dos veces."""
        gc = giftcard_service.emitir(10000)
        ticket = self._crear_ticket()
        pago = TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='GIFTCARD', voucher=gc.codigo, monto=4000,
        )
        giftcard_service.consumir(gc.codigo, 4000, ticket=ticket, pago_ticket=pago)
        giftcard_service.consumir(gc.codigo, 4000, ticket=ticket, pago_ticket=pago)  # reintento
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 6000)  # solo se descontó una vez
        self.assertEqual(
            MovimientoGiftCard.objects.filter(giftcard=gc, tipo='CONSUMO').count(), 1
        )

    def test_consumo_saldo_insuficiente_falla(self):
        gc = giftcard_service.emitir(1000)
        with self.assertRaises(GiftCardError):
            giftcard_service.consumir(gc.codigo, 5000)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 1000)  # intacto

    def test_validar_no_descuenta(self):
        gc = giftcard_service.emitir(2000)
        res = giftcard_service.validar(gc.codigo, 1500)
        self.assertTrue(res['valida'])
        self.assertTrue(res['saldo_suficiente'])
        res2 = giftcard_service.validar(gc.codigo, 5000)
        self.assertFalse(res2['valida'])
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 2000)  # validar no toca el saldo

    def test_anular_lleva_saldo_a_cero(self):
        gc = giftcard_service.emitir(8000)
        giftcard_service.anular(gc.codigo)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 0)
        self.assertEqual(gc.estado, 'ANULADA')

    def test_reversa_recarga_giftcard(self):
        gc = giftcard_service.emitir(10000)
        ticket = self._crear_ticket()
        pago = TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='GIFTCARD', voucher=gc.codigo, monto=6000,
        )
        giftcard_service.consumir(gc.codigo, 6000, ticket=ticket, pago_ticket=pago)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 4000)
        # Reversa por anulación de la venta
        giftcard_service.reversar(gc.codigo, 6000, ticket=ticket)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 10000)
        self.assertEqual(gc.estado, 'ACTIVA')
        # Reversa idempotente
        giftcard_service.reversar(gc.codigo, 6000, ticket=ticket)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 10000)

    def test_recargar_suma_saldo(self):
        gc = giftcard_service.emitir(1000)
        giftcard_service.recargar(gc.codigo, 2500)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 3500)

    # ===== Motivo / descripción =====

    def test_emision_con_motivo_y_descripcion(self):
        gc = giftcard_service.emitir(
            5000, motivo='REGALO_CORP', descripcion='Regalo corporativo Empresa X',
        )
        self.assertEqual(gc.motivo, 'REGALO_CORP')
        self.assertEqual(gc.descripcion, 'Regalo corporativo Empresa X')

    def test_emision_motivo_default_es_otro(self):
        gc = giftcard_service.emitir(5000)
        self.assertEqual(gc.motivo, 'OTRO')
        self.assertIsNone(gc.descripcion)

    def test_emision_motivo_invalido_falla(self):
        with self.assertRaises(GiftCardError):
            giftcard_service.emitir(5000, motivo='INEXISTENTE')

    # ===== Bloqueo / desbloqueo =====

    def test_bloquear_impide_consumo(self):
        gc = giftcard_service.emitir(10000)
        giftcard_service.bloquear(gc.codigo, observaciones='Sospecha de fraude')
        gc.refresh_from_db()
        self.assertEqual(gc.estado, 'BLOQUEADA')
        # Deja una fila de auditoría monto=0
        self.assertEqual(gc.movimientos.filter(tipo='BLOQUEO', monto=0).count(), 1)
        # El consumo se rechaza mientras está bloqueada
        with self.assertRaises(GiftCardError):
            giftcard_service.consumir(gc.codigo, 1000)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 10000)  # saldo intacto

    def test_desbloquear_reactiva_y_permite_consumo(self):
        gc = giftcard_service.emitir(10000)
        giftcard_service.bloquear(gc.codigo)
        giftcard_service.desbloquear(gc.codigo)
        gc.refresh_from_db()
        self.assertEqual(gc.estado, 'ACTIVA')
        self.assertEqual(gc.movimientos.filter(tipo='DESBLOQUEO').count(), 1)
        giftcard_service.consumir(gc.codigo, 4000)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 6000)

    def test_desbloquear_saldo_cero_queda_agotada(self):
        gc = giftcard_service.emitir(5000)
        ticket = self._crear_ticket()
        pago = TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='GIFTCARD', voucher=gc.codigo, monto=5000,
        )
        giftcard_service.consumir(gc.codigo, 5000, ticket=ticket, pago_ticket=pago)
        giftcard_service.bloquear(gc.codigo)
        giftcard_service.desbloquear(gc.codigo)
        gc.refresh_from_db()
        self.assertEqual(gc.estado, 'AGOTADA')

    def test_bloquear_desde_anulada_falla(self):
        gc = giftcard_service.emitir(3000)
        giftcard_service.anular(gc.codigo)
        with self.assertRaises(GiftCardError):
            giftcard_service.bloquear(gc.codigo)

    def test_desbloquear_no_bloqueada_falla(self):
        gc = giftcard_service.emitir(3000)
        with self.assertRaises(GiftCardError):
            giftcard_service.desbloquear(gc.codigo)

    # ===== Edición =====

    def test_editar_actualiza_y_deja_auditoria(self):
        gc = giftcard_service.emitir(2000, motivo='OTRO')
        giftcard_service.editar(
            gc.codigo, descripcion='Compensación reclamo #123', motivo='COMPENSACION',
        )
        gc.refresh_from_db()
        self.assertEqual(gc.motivo, 'COMPENSACION')
        self.assertEqual(gc.descripcion, 'Compensación reclamo #123')
        mov = gc.movimientos.filter(tipo='AJUSTE', monto=0).first()
        self.assertIsNotNone(mov)
        self.assertIn('Edición', mov.observaciones)

    def test_editar_sin_cambios_no_crea_movimiento(self):
        gc = giftcard_service.emitir(2000)
        antes = gc.movimientos.count()
        giftcard_service.editar(gc.codigo)  # nada que cambiar
        gc.refresh_from_db()
        self.assertEqual(gc.movimientos.count(), antes)

    def test_editar_motivo_invalido_falla(self):
        gc = giftcard_service.emitir(2000)
        with self.assertRaises(GiftCardError):
            giftcard_service.editar(gc.codigo, motivo='XX')

    # ===== Expiración =====

    def test_marcar_vencidas(self):
        vencida = giftcard_service.emitir(5000)
        vigente = giftcard_service.emitir(5000)
        # Forzar fecha de vencimiento en el pasado sin pasar por save()
        ayer = timezone.localdate() - timedelta(days=1)
        GiftCard.objects.filter(pk=vencida.pk).update(fecha_vencimiento=ayer)

        n = giftcard_service.marcar_vencidas()
        self.assertEqual(n, 1)
        vencida.refresh_from_db()
        vigente.refresh_from_db()
        self.assertEqual(vencida.estado, 'VENCIDA')
        self.assertEqual(vigente.estado, 'ACTIVA')
