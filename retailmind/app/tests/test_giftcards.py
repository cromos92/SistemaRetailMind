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


class GiftCardAmbitoEmpresaTest(TestCase):
    """Ámbito de canje por empresa: gc.empresa acota; null = global."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa_a = crear_empresa('Empresa A', rut='76.104.936-4')
        cls.empresa_b = crear_empresa('Empresa B', rut='77.000.111-2')
        cls.suc_a = crear_sucursal(empresa=cls.empresa_a, alias='NICKA')
        cls.suc_b = crear_sucursal(empresa=cls.empresa_b, alias='PAOLB')

    def test_consumir_en_sucursal_de_la_empresa_ok(self):
        gc = giftcard_service.emitir(10000, empresa=self.empresa_a)
        giftcard_service.consumir(gc.codigo, 3000, sucursal=self.suc_a)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 7000)

    def test_consumir_en_otra_empresa_rechazado(self):
        gc = giftcard_service.emitir(10000, empresa=self.empresa_a)
        with self.assertRaises(GiftCardError):
            giftcard_service.consumir(gc.codigo, 3000, sucursal=self.suc_b)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 10000)  # intacto

    def test_consumir_acotada_sin_sucursal_rechazado(self):
        """Fail-closed: tarjeta acotada + sucursal desconocida = no se canjea."""
        gc = giftcard_service.emitir(10000, empresa=self.empresa_a)
        with self.assertRaises(GiftCardError):
            giftcard_service.consumir(gc.codigo, 3000)

    def test_global_se_canjea_en_cualquier_empresa(self):
        gc = giftcard_service.emitir(10000)  # sin empresa = global
        giftcard_service.consumir(gc.codigo, 2000, sucursal=self.suc_b)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 8000)

    def test_validar_detecta_ambito(self):
        gc = giftcard_service.emitir(10000, empresa=self.empresa_a)
        ok = giftcard_service.validar(gc.codigo, 1000, sucursal=self.suc_a)
        self.assertTrue(ok['valida'])
        mal = giftcard_service.validar(gc.codigo, 1000, sucursal=self.suc_b)
        self.assertFalse(mal['valida'])
        self.assertIn('Empresa A', mal['motivo'])

    def test_cambiar_ambito_deja_rastro_en_ledger(self):
        gc = giftcard_service.emitir(10000, empresa=self.empresa_a)
        gc = giftcard_service.cambiar_ambito(gc.codigo, None)   # abrir a todas
        self.assertIsNone(gc.empresa_id)
        gc = giftcard_service.cambiar_ambito(gc.codigo, self.empresa_b)
        self.assertEqual(gc.empresa_id, self.empresa_b.id)
        ajustes = gc.movimientos.filter(tipo='AJUSTE', observaciones__icontains='Ámbito')
        self.assertEqual(ajustes.count(), 2)
        # Sin cambio real: no agrega fila
        giftcard_service.cambiar_ambito(gc.codigo, self.empresa_b)
        self.assertEqual(
            gc.movimientos.filter(tipo='AJUSTE', observaciones__icontains='Ámbito').count(), 2
        )

    def test_cambiar_ambito_anulada_falla(self):
        gc = giftcard_service.emitir(10000)
        giftcard_service.anular(gc.codigo)
        with self.assertRaises(GiftCardError):
            giftcard_service.cambiar_ambito(gc.codigo, self.empresa_a)


class GiftCardCuadraturaTest(TestCase):
    """El pago GIFTCARD debe caer en su bucket propio y NO en efectivo."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)
        cls.vendedor = crear_vendedor(empresa=cls.empresa)

    def test_pago_giftcard_va_a_total_giftcard(self):
        from app.views_modulo_ventas import _calcular_cuadratura_data
        gc = giftcard_service.emitir(50000, empresa=self.empresa)
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal,
            correlativo=99, estado='PAGADO',
            subTotal=60000, total=60000, responsable='tester',
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='GIFTCARD', voucher=gc.codigo, monto=50000,
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='EFECTIVO', monto=10000,
        )
        fecha = timezone.localdate().isoformat()
        data = _calcular_cuadratura_data(self.sucursal, fecha)
        self.assertEqual(data['total_giftcard'], 50000)
        self.assertEqual(data['total_efectivo'], 10000)   # la GC no infla el efectivo
        self.assertEqual(data['total_tickets'], 60000)
        # El desglose por medios ahora alcanza la venta total (sin gap)
        suma_medios = (
            data['total_efectivo'] + data['total_giftcard'] + data['total_transbank']
            + data['total_transferencia'] + data['total_cheque'] + data['total_convenio']
            + data['total_credito_trabajador'] + data['total_credito_externo']
            + data['total_orden_compra'] + data['total_tarjetas_comerciales']
            + data['total_venta_internet']
        )
        self.assertEqual(suma_medios, data['venta_total'])


class GiftCardCorreoTest(TestCase):
    """Template del correo + registro ENVIO_CORREO en el ledger."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa('Empresa Correo', rut='76.104.936-4')
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='NICKC')

    def test_template_correo_renderiza(self):
        from django.template.loader import render_to_string
        html = render_to_string('emails/giftcard_codigo.html', {
            'marca': 'REALSPORT',
            'nombre_destinatario': 'Javier',
            'tarjetas': [{'codigo': 'GC-TEST-TEST-TEST', 'monto': 50000,
                          'beneficiario': 'ISIDORA DIAZ'}],
            'fecha_vencimiento_humana': '20 de octubre de 2026',
            'vigencia_dias': 60,
            'empresa_nombre': self.empresa.nombre,
            'empresa_rut': self.empresa.rut,
            'tiendas': ['NICK1', 'NICK2', 'NICK3'],
        })
        self.assertIn('GC-TEST-TEST-TEST', html)
        self.assertIn('REALSPORT', html)
        self.assertIn('ISIDORA DIAZ', html)
        self.assertIn('responsabilidad exclusiva del titular', html)
        self.assertIn('NICK2', html)

    def test_template_correo_con_varias_tarjetas(self):
        """Un trabajador con varios hijos recibe UN correo con N códigos."""
        from django.template.loader import render_to_string
        html = render_to_string('emails/giftcard_codigo.html', {
            'marca': 'REALSPORT',
            'nombre_destinatario': 'PATRICK RIVERA',
            'tarjetas': [
                {'codigo': 'GC-AAAA-AAAA-AAAA', 'monto': 50000, 'beneficiario': 'JAVIERA RIVERA'},
                {'codigo': 'GC-BBBB-BBBB-BBBB', 'monto': 50000, 'beneficiario': 'MAXIMILIANO RIVERA'},
            ],
            'fecha_vencimiento_humana': '20 de octubre de 2026',
            'vigencia_dias': 60,
            'empresa_nombre': self.empresa.nombre,
            'empresa_rut': self.empresa.rut,
            'tiendas': ['NICK1', 'NICK2'],
        })
        self.assertIn('GC-AAAA-AAAA-AAAA', html)
        self.assertIn('GC-BBBB-BBBB-BBBB', html)
        self.assertIn('JAVIERA RIVERA', html)
        self.assertIn('MAXIMILIANO RIVERA', html)
        self.assertIn('2 Gift Cards', html)

    def test_envio_correo_queda_en_ledger(self):
        gc = giftcard_service.emitir(50000, empresa=self.empresa)
        MovimientoGiftCard.objects.create(
            giftcard=gc, tipo='ENVIO_CORREO', monto=0,
            saldo_resultante=gc.saldo_actual,
            observaciones='Código enviado a test@test.cl',
        )
        fila = gc.movimientos.filter(tipo='ENVIO_CORREO').first()
        self.assertIsNotNone(fila)
        self.assertEqual(fila.monto, 0)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 50000)  # el envío no toca el saldo
        self.assertEqual(gc.estado, 'ACTIVA')


class GiftCardFixesRevisionTest(TestCase):
    """Regresiones de la revisión adversarial del 21-ago."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa('Empresa Fix', rut='76.104.936-4')
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='NICKF')
        cls.vendedor = crear_vendedor(empresa=cls.empresa)

    def test_emitir_con_fecha_string_deja_date_usable(self):
        """El modal manda 'YYYY-MM-DD': la instancia debe quedar con date real.

        Antes, `gc.fecha_vencimiento.isoformat()` en la vista reventaba con
        AttributeError DESPUÉS de crear la tarjeta -> 500 y el usuario
        reintentaba, duplicando el pasivo.
        """
        from datetime import date
        gc = giftcard_service.emitir(50000, vencimiento='2026-10-20')
        self.assertIsInstance(gc.fecha_vencimiento, date)
        self.assertEqual(gc.fecha_vencimiento.isoformat(), '2026-10-20')

    def test_emitir_con_fecha_invalida_falla_sin_crear(self):
        antes = GiftCard.objects.count()
        with self.assertRaises(GiftCardError):
            giftcard_service.emitir(50000, vencimiento='20-10-2026')
        self.assertEqual(GiftCard.objects.count(), antes)

    def test_reversa_por_pago_devuelve_ambos_pagos_de_la_misma_gc(self):
        """Dos pagos con la MISMA tarjeta: la anulación debe devolver los dos.

        Con la clave de idempotencia por ticket+código, la segunda reversa se
        auto-anulaba y el cliente perdía ese saldo.
        """
        gc = giftcard_service.emitir(10000)
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal,
            correlativo=555, estado='PAGADO',
            subTotal=10000, total=10000, responsable='tester',
        )
        pagos = [
            TicketDetallePago.objects.create(
                ticket=ticket, metodo_pago='GIFTCARD', voucher=gc.codigo, monto=5000,
            ) for _ in range(2)
        ]
        for p in pagos:
            giftcard_service.consumir(gc.codigo, 5000, ticket=ticket, pago_ticket=p)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 0)

        for p in pagos:
            giftcard_service.reversar(gc.codigo, 5000, ticket=ticket, pago_ticket=p)
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 10000)   # antes quedaba en 5000

    def test_reversa_sigue_siendo_idempotente_por_pago(self):
        gc = giftcard_service.emitir(10000)
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal,
            correlativo=556, estado='PAGADO',
            subTotal=5000, total=5000, responsable='tester',
        )
        pago = TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='GIFTCARD', voucher=gc.codigo, monto=5000,
        )
        giftcard_service.consumir(gc.codigo, 5000, ticket=ticket, pago_ticket=pago)
        giftcard_service.reversar(gc.codigo, 5000, ticket=ticket, pago_ticket=pago)
        giftcard_service.reversar(gc.codigo, 5000, ticket=ticket, pago_ticket=pago)  # reintento
        gc.refresh_from_db()
        self.assertEqual(gc.saldo_actual, 10000)   # no recarga dos veces


class GiftCardEnvioCorreoEndpointTest(TestCase):
    """Envío real por el endpoint: seguimiento en BD + guards de estado."""

    @classmethod
    def setUpTestData(cls):
        from app.models import ModuloSistema, OpcionMenu, PermisoRol
        from users.models import Usuario
        cls.empresa = crear_empresa('Empresa Envio', rut='76.104.936-4')
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='NICKE')
        cls.user = Usuario.objects.create_user(
            username='gcadmin', password='x', rol='administrador',
        )
        modulo = ModuloSistema.objects.create(nombre='Fidelizacion', orden=8)
        for codigo, nombre in [('giftcards_listado', 'Gift Cards'),
                               ('giftcards_emitir', 'Emitir Gift Card')]:
            opcion = OpcionMenu.objects.create(
                modulo=modulo, codigo=codigo, nombre=nombre, activo=True,
            )
            PermisoRol.objects.create(
                rol='administrador', opcion_menu=opcion,
                puede_ver=True, puede_crear=True, puede_editar=True,
            )

    def setUp(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _enviar(self, codigo, email='cliente@test.cl', nombre='Javier'):
        import json as _json
        return self.client.post(
            '/app/api/giftcards/enviar-correo/',
            data=_json.dumps({'codigo': codigo, 'email': email, 'nombre': nombre}),
            content_type='application/json',
        )

    def test_envio_registra_seguimiento_y_manda_correo(self):
        from django.core import mail
        gc = giftcard_service.emitir(50000, empresa=self.empresa)
        resp = self._enviar(gc.codigo)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])

        # El correo salió de verdad (backend de test) con el código adentro.
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(gc.codigo, mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ['cliente@test.cl'])

        # Y el sistema lo sabe: campos de seguimiento + fila en el ledger.
        gc.refresh_from_db()
        self.assertEqual(gc.correo_enviado_a, 'cliente@test.cl')
        self.assertIsNotNone(gc.correo_enviado_en)
        self.assertEqual(gc.correo_envios, 1)
        self.assertEqual(gc.movimientos.filter(tipo='ENVIO_CORREO').count(), 1)
        self.assertEqual(gc.saldo_actual, 50000)   # enviar no toca el saldo

    def test_reenvio_incrementa_contador(self):
        gc = giftcard_service.emitir(50000, empresa=self.empresa)
        self._enviar(gc.codigo)
        self._enviar(gc.codigo, email='otro@test.cl')
        gc.refresh_from_db()
        self.assertEqual(gc.correo_envios, 2)
        self.assertEqual(gc.correo_enviado_a, 'otro@test.cl')
        self.assertEqual(gc.movimientos.filter(tipo='ENVIO_CORREO').count(), 2)

    def test_no_envia_vencida_ni_sin_saldo(self):
        from django.core import mail
        vencida = giftcard_service.emitir(50000, empresa=self.empresa)
        GiftCard.objects.filter(pk=vencida.pk).update(
            fecha_vencimiento=timezone.localdate() - timedelta(days=1))
        resp = self._enviar(vencida.codigo)
        self.assertEqual(resp.status_code, 400)

        agotada = giftcard_service.emitir(1000, empresa=self.empresa)
        giftcard_service.consumir(agotada.codigo, 1000, sucursal=self.sucursal)
        resp2 = self._enviar(agotada.codigo)
        self.assertEqual(resp2.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)   # ningún correo salió

    def test_email_invalido_rechazado(self):
        gc = giftcard_service.emitir(50000, empresa=self.empresa)
        resp = self._enviar(gc.codigo, email='no-es-un-correo')
        self.assertEqual(resp.status_code, 400)
        gc.refresh_from_db()
        self.assertEqual(gc.correo_envios, 0)

    def test_listado_expone_estado_de_envio_y_pendiente(self):
        gc = giftcard_service.emitir(50000, empresa=self.empresa)
        self._enviar(gc.codigo)
        resp = self.client.get('/app/api/giftcards/listar/')
        item = resp.json()['items'][0]
        self.assertEqual(item['correo_enviado_a'], 'cliente@test.cl')
        self.assertEqual(item['correo_envios'], 1)
        self.assertEqual(item['pendiente_por_usar'], 50000)

        # Filtros de entrega
        enviadas = self.client.get('/app/api/giftcards/listar/?envio=enviadas').json()
        sin_enviar = self.client.get('/app/api/giftcards/listar/?envio=sin_enviar').json()
        self.assertEqual(enviadas['total'], 1)
        self.assertEqual(sin_enviar['total'], 0)


class GiftCardEstadoCorreoTest(TestCase):
    """Estado de ENTREGA: webhook del proveedor + confirmación manual."""

    @classmethod
    def setUpTestData(cls):
        from app.models import ModuloSistema, OpcionMenu, PermisoRol
        from users.models import Usuario
        cls.empresa = crear_empresa('Empresa Estado', rut='76.104.936-4')
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='NICKW')
        cls.user = Usuario.objects.create_user(
            username='gcweb', password='x', rol='administrador')
        modulo = ModuloSistema.objects.create(nombre='Fidelizacion2', orden=9)
        for codigo in ('giftcards_listado', 'giftcards_emitir'):
            opcion = OpcionMenu.objects.create(
                modulo=modulo, codigo=codigo, nombre=codigo, activo=True)
            PermisoRol.objects.create(
                rol='administrador', opcion_menu=opcion,
                puede_ver=True, puede_crear=True, puede_editar=True)

    SECRET = 'secreto-de-prueba'

    def _post_webhook(self, payload, secret=None, firma=None):
        import hashlib
        import hmac
        import json as _json
        cuerpo = _json.dumps(payload).encode('utf-8')
        if firma is None:
            firma = hmac.new((secret or self.SECRET).encode('utf-8'),
                             cuerpo, hashlib.sha256).hexdigest()
        return self.client.post(
            '/app/api/giftcards/webhook-correo/', data=cuerpo,
            content_type='application/json', HTTP_SIGNATURE=firma,
        )

    def _gc_enviada(self, message_id='<abc123@mailersend>', email='papa@empresa.com'):
        gc = giftcard_service.emitir(50000, empresa=self.empresa)
        GiftCard.objects.filter(pk=gc.pk).update(
            correo_enviado_a=email, correo_enviado_en=timezone.now(),
            correo_envios=1, correo_message_id=message_id, correo_estado='ENVIADO',
        )
        gc.refresh_from_db()
        return gc

    def _payload(self, tipo, message_id='<abc123@mailersend>', email='papa@empresa.com',
                 reason=''):
        return {'type': tipo, 'data': {'email': {
            'message_id': message_id,
            'recipient': {'email': email},
            'reason': reason,
        }}}

    def test_webhook_sin_secret_permite_el_alta_pero_no_procesa(self):
        """MailerSend valida la URL ANTES de darte el secret: hay que responder
        200 o el webhook no se puede crear. Pero sin secret no se escribe nada."""
        import os
        os.environ.pop('GIFTCARD_WEBHOOK_SECRET', None)
        gc = self._gc_enviada()
        resp = self._post_webhook(self._payload('activity.delivered'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get('pendiente_configuracion'))
        gc.refresh_from_db()
        self.assertEqual(gc.correo_estado, 'ENVIADO')   # nada cambió

    def test_webhook_get_responde_ok_para_verificar_url(self):
        resp = self.client.get('/app/api/giftcards/webhook-correo/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get('success'))

    def test_webhook_firma_invalida_rechazada(self):
        import os
        os.environ['GIFTCARD_WEBHOOK_SECRET'] = self.SECRET
        try:
            gc = self._gc_enviada()
            resp = self._post_webhook(self._payload('activity.delivered'),
                                      firma='firma-falsa')
            self.assertEqual(resp.status_code, 401)
            gc.refresh_from_db()
            self.assertEqual(gc.correo_estado, 'ENVIADO')
        finally:
            os.environ.pop('GIFTCARD_WEBHOOK_SECRET', None)

    def _payload_v2(self, tipo, message_id='ms-id-123', email='papa@empresa.com',
                    reason=''):
        """Formato de los webhooks 2.0: payload plano (data.email es TEXTO)."""
        return {
            'type': tipo,
            'created_at': '2026-08-22T15:54:26.000000Z',
            'data': {
                'id': 'evt-1', 'domain_id': 'dom-1',
                'message_id': message_id, 'email_id': 'eml-1',
                'type': tipo.split('.')[-1],
                'subject': 'Tu Gift Card',
                'email': email,          # <-- string, no objeto (v2)
                'reason': reason,
                'tags': [], 'meta': [],
            },
        }

    def test_webhook_v2_delivered_actualiza_estado(self):
        """Webhooks 2.0: el destinatario viene como texto en data.email.

        Con el parser antiguo (solo v1) el endpoint respondía 200 pero no
        encontraba la tarjeta y el estado se quedaba en ENVIADO.
        """
        import os
        os.environ['GIFTCARD_WEBHOOK_SECRET'] = self.SECRET
        try:
            gc = self._gc_enviada(email='papa@empresa.com')
            resp = self._post_webhook(self._payload_v2('activity.delivered'))
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json().get('tarjetas'), 1)
            gc.refresh_from_db()
            self.assertEqual(gc.correo_estado, 'ENTREGADO')
        finally:
            os.environ.pop('GIFTCARD_WEBHOOK_SECRET', None)

    def test_webhook_v2_bounce_con_motivo(self):
        import os
        os.environ['GIFTCARD_WEBHOOK_SECRET'] = self.SECRET
        try:
            gc = self._gc_enviada(email='malo@empresa.com')
            self._post_webhook(self._payload_v2(
                'activity.hard_bounced', email='malo@empresa.com',
                reason='Recipient address rejected'))
            gc.refresh_from_db()
            self.assertEqual(gc.correo_estado, 'REBOTADO')
            self.assertIn('rejected', gc.correo_estado_detalle)
        finally:
            os.environ.pop('GIFTCARD_WEBHOOK_SECRET', None)

    def test_webhook_v2_actualiza_todas_las_del_mismo_correo(self):
        """Un papá con 3 hijos recibe UN correo: el evento aplica a las 3."""
        import os
        os.environ['GIFTCARD_WEBHOOK_SECRET'] = self.SECRET
        try:
            from app.views_modulo_giftcards import enviar_codigos_por_correo
            gcs = [giftcard_service.emitir(50000, empresa=self.empresa) for _ in range(3)]
            enviar_codigos_por_correo(gcs, 'papa3@empresa.com', sucursal=self.sucursal)
            self._post_webhook(self._payload_v2('activity.delivered',
                                                email='papa3@empresa.com'))
            for gc in gcs:
                gc.refresh_from_db()
                self.assertEqual(gc.correo_estado, 'ENTREGADO')
        finally:
            os.environ.pop('GIFTCARD_WEBHOOK_SECRET', None)

    def test_webhook_delivered_y_bounce_actualizan_estado(self):
        import os
        os.environ['GIFTCARD_WEBHOOK_SECRET'] = self.SECRET
        try:
            gc = self._gc_enviada()
            self.assertEqual(self._post_webhook(
                self._payload('activity.delivered')).status_code, 200)
            gc.refresh_from_db()
            self.assertEqual(gc.correo_estado, 'ENTREGADO')

            # Un rebote posterior SÍ pisa el estado (prioridad mayor)
            otra = self._gc_enviada(message_id='<zzz@ms>', email='malo@empresa.com')
            self._post_webhook(self._payload(
                'activity.hard_bounced', message_id='<zzz@ms>',
                email='malo@empresa.com', reason='Mailbox does not exist'))
            otra.refresh_from_db()
            self.assertEqual(otra.correo_estado, 'REBOTADO')
            self.assertIn('Mailbox', otra.correo_estado_detalle)
            # El rebote queda además en la trazabilidad
            self.assertTrue(otra.movimientos.filter(
                tipo='ENVIO_CORREO', observaciones__icontains='PROBLEMA').exists())
        finally:
            os.environ.pop('GIFTCARD_WEBHOOK_SECRET', None)

    def test_webhook_no_retrocede_el_estado(self):
        """Un `delivered` que llega tarde no debe borrar un rebote."""
        import os
        os.environ['GIFTCARD_WEBHOOK_SECRET'] = self.SECRET
        try:
            gc = self._gc_enviada()
            self._post_webhook(self._payload('activity.hard_bounced'))
            gc.refresh_from_db()
            self.assertEqual(gc.correo_estado, 'REBOTADO')
            self._post_webhook(self._payload('activity.delivered'))
            gc.refresh_from_db()
            self.assertEqual(gc.correo_estado, 'REBOTADO')   # sigue rebotado
        finally:
            os.environ.pop('GIFTCARD_WEBHOOK_SECRET', None)

    def test_webhook_evento_irrelevante_no_falla(self):
        import os
        os.environ['GIFTCARD_WEBHOOK_SECRET'] = self.SECRET
        try:
            gc = self._gc_enviada()
            resp = self._post_webhook(self._payload('activity.queued'))
            self.assertEqual(resp.status_code, 200)
            gc.refresh_from_db()
            self.assertEqual(gc.correo_estado, 'ENVIADO')
        finally:
            os.environ.pop('GIFTCARD_WEBHOOK_SECRET', None)

    def test_peticion_de_prueba_de_mailersend_responde_ok_sin_tocar_datos(self):
        """El botón "Send test" del panel firma con un secret público fijo."""
        import os
        from app.views_modulo_giftcards import _MAILERSEND_TEST_SECRET
        os.environ['GIFTCARD_WEBHOOK_SECRET'] = self.SECRET
        try:
            gc = self._gc_enviada()
            resp = self._post_webhook(self._payload('activity.hard_bounced'),
                                      secret=_MAILERSEND_TEST_SECRET)
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json().get('prueba'))
            gc.refresh_from_db()
            # El secret de prueba es público: NO puede alterar estados reales.
            self.assertEqual(gc.correo_estado, 'ENVIADO')
        finally:
            os.environ.pop('GIFTCARD_WEBHOOK_SECRET', None)

    def test_confirmar_entrega_manual(self):
        gc = self._gc_enviada()
        self.client.force_login(self.user)
        import json as _json
        resp = self.client.post(
            '/app/api/giftcards/confirmar-entrega/',
            data=_json.dumps({'codigo': gc.codigo, 'nota': 'confirmó por WhatsApp'}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        gc.refresh_from_db()
        self.assertEqual(gc.correo_estado, 'CONFIRMADO_MANUAL')
        self.assertIn('WhatsApp', gc.correo_estado_detalle)

    def test_correo_muestra_direcciones_no_alias_internos(self):
        """Al cliente se le dan DIRECCIONES ("Matta 2479"), no códigos internos
        ("NICK1"), y se omiten los locales cerrados o que no operan."""
        import os
        from django.core import mail
        from app.views_modulo_giftcards import enviar_codigos_por_correo
        crear_sucursal(empresa=self.empresa, alias='NICKZ1',
                       direccion='Matta 2479', comuna='Santiago')
        crear_sucursal(empresa=self.empresa, alias='NICKZ2', direccion='Matta 2438')
        crear_sucursal(empresa=self.empresa, alias='NICKZ3',
                       direccion='Matta 2418')                        # excluida por env
        crear_sucursal(empresa=self.empresa, alias='NICKOFF',
                       direccion='Bodega 999', activa=False)          # inactiva

        gc = giftcard_service.emitir(50000, empresa=self.empresa)
        os.environ['GIFTCARD_CORREO_TIENDAS_EXCLUIR'] = 'NICKZ3'
        try:
            enviar_codigos_por_correo([gc], 'cliente@test.cl', sucursal=self.sucursal)
        finally:
            os.environ.pop('GIFTCARD_CORREO_TIENDAS_EXCLUIR', None)

        cuerpo = mail.outbox[-1].body
        html = mail.outbox[-1].alternatives[0][0]
        # Direcciones sí (con comuna cuando existe); alias internos NO
        self.assertIn('Matta 2479, Santiago', html)
        self.assertIn('Matta 2438', html)
        self.assertNotIn('NICKZ1', html)
        self.assertNotIn('NICKZ1', cuerpo)
        # Excluida por configuración e inactiva: ni por alias ni por dirección
        self.assertNotIn('Matta 2418', html)
        self.assertNotIn('Matta 2418', cuerpo)
        self.assertNotIn('Bodega 999', html)
        self.assertNotIn('Bodega 999', cuerpo)

    def test_envio_deja_estado_enviado_y_filtros(self):
        from app.views_modulo_giftcards import enviar_codigos_por_correo
        gc = giftcard_service.emitir(50000, empresa=self.empresa)
        enviar_codigos_por_correo([gc], 'destino@empresa.com',
                                  nombre_destino='Papá', sucursal=self.sucursal)
        gc.refresh_from_db()
        self.assertEqual(gc.correo_estado, 'ENVIADO')

        self.client.force_login(self.user)
        problema = self.client.get('/app/api/giftcards/listar/?envio=problema').json()
        self.assertEqual(problema['total'], 0)
        # Tras un rebote aparece en el filtro "no llegaron"
        GiftCard.objects.filter(pk=gc.pk).update(correo_estado='REBOTADO')
        problema2 = self.client.get('/app/api/giftcards/listar/?envio=problema').json()
        self.assertEqual(problema2['total'], 1)
        self.assertTrue(problema2['items'][0]['correo_problema'])


class EmitirGiftcardsDesdeListaCommandTest(TestCase):
    """Lote corporativo desde CSV: 1 tarjeta por beneficiario, correo agrupado."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa('Empresa Lista', rut='76.104.936-4')
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='NICKX')

    CSV = (
        'n;beneficiario;trabajador;correo\n'
        '1;JAVIERA RIVERA;PATRICK RIVERA;patrick.rivera@empresa.com\n'
        '2;MAXIMILIANO RIVERA;PATRICK RIVERA;patrick.rivera@empresa.com\n'
        '3;AGUSTIN RIVERA;FELIPE RIVERA;felipe.rivera@empresa.com\n'
        '4;;ABEL VICENCIO;abel.vicencio@empresa.com\n'
    )

    def _correr(self, *extra, csv_texto=None):
        import os
        import tempfile
        from django.core.management import call_command
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, 'lista.csv')
            with open(ruta, 'w', encoding='utf-8') as f:
                f.write(csv_texto if csv_texto is not None else self.CSV)
            os.chdir(tmp)
            try:
                call_command(
                    'emitir_giftcards_desde_lista', '--csv', ruta,
                    '--monto', '50000', '--sucursal', 'NICKX',
                    '--empresa', '76.104.936-4', '--vigencia-dias', '60',
                    '--descripcion', 'NAVIDAD 2026', *extra,
                )
                return os.listdir(tmp)
            finally:
                os.chdir(cwd)

    def test_dry_run_no_emite_ni_envia(self):
        from django.core import mail
        self._correr()
        self.assertEqual(GiftCard.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_aplicar_emite_una_por_fila_con_beneficiario(self):
        from django.core import mail
        self._correr('--aplicar')
        self.assertEqual(GiftCard.objects.count(), 4)
        self.assertEqual(len(mail.outbox), 0)   # sin --enviar no manda nada
        gc = GiftCard.objects.filter(descripcion__contains='JAVIERA RIVERA').first()
        self.assertIsNotNone(gc)
        self.assertEqual(gc.saldo_actual, 50000)
        self.assertEqual(gc.empresa_id, self.empresa.id)
        self.assertEqual(gc.fecha_vencimiento,
                         timezone.localdate() + timedelta(days=60))
        self.assertIn('PATRICK RIVERA', gc.observaciones)

    def test_enviar_agrupa_por_destinatario(self):
        from django.core import mail
        self._correr('--aplicar', '--enviar')
        # 4 tarjetas, 3 correos distintos -> 3 mensajes (Patrick recibe 1 con 2)
        self.assertEqual(GiftCard.objects.count(), 4)
        self.assertEqual(len(mail.outbox), 3)
        patrick = [m for m in mail.outbox if m.to == ['patrick.rivera@empresa.com']][0]
        self.assertIn('JAVIERA RIVERA', patrick.body)
        self.assertIn('MAXIMILIANO RIVERA', patrick.body)
        codigos = list(GiftCard.objects.filter(
            descripcion__contains='RIVERA').values_list('codigo', flat=True))
        for c in codigos:
            if 'AGUSTIN' not in c:   # los de Patrick van en su correo
                pass
        # Todas quedaron marcadas como enviadas
        self.assertEqual(GiftCard.objects.filter(correo_enviado_en__isnull=True).count(), 0)
        self.assertEqual(
            GiftCard.objects.filter(movimientos__tipo='ENVIO_CORREO').distinct().count(), 4)

    def test_enviar_uno_por_tarjeta(self):
        from django.core import mail
        self._correr('--aplicar', '--enviar', '--correo-por-tarjeta')
        self.assertEqual(len(mail.outbox), 4)   # un correo por gift card

    def test_csv_con_correo_invalido_falla_sin_emitir(self):
        from django.core.management.base import CommandError
        malo = ('n;beneficiario;trabajador;correo\n'
                '1;X;Y;no-es-correo\n')
        with self.assertRaises(CommandError):
            self._correr('--aplicar', csv_texto=malo)
        self.assertEqual(GiftCard.objects.count(), 0)


class EmitirGiftcardsLoteCommandTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa('Empresa Lote', rut='76.104.936-4')
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='NICKL')

    def test_dry_run_no_emite(self):
        from django.core.management import call_command
        call_command(
            'emitir_giftcards_lote', '--cantidad', '3', '--monto', '50000',
            '--sucursal', 'NICKL', '--empresa', '76.104.936-4',
        )
        self.assertEqual(GiftCard.objects.count(), 0)

    def test_aplicar_emite_lote_con_ambito_y_vigencia(self):
        import os
        import tempfile
        from django.core.management import call_command
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                call_command(
                    'emitir_giftcards_lote', '--cantidad', '3', '--monto', '50000',
                    '--sucursal', 'NICKL', '--empresa', '76.104.936-4',
                    '--vigencia-dias', '60', '--motivo', 'PROMOCION',
                    '--descripcion', 'LOTE TEST', '--aplicar',
                )
                archivos = os.listdir(tmp)
            finally:
                os.chdir(cwd)
        self.assertEqual(GiftCard.objects.count(), 3)
        esperado = timezone.localdate() + timedelta(days=60)
        for gc in GiftCard.objects.all():
            self.assertEqual(gc.saldo_actual, 50000)
            self.assertEqual(gc.empresa_id, self.empresa.id)
            self.assertEqual(gc.fecha_vencimiento, esperado)
            self.assertEqual(gc.motivo, 'PROMOCION')
            self.assertEqual(gc.descripcion, 'LOTE TEST')
            self.assertEqual(gc.tipo_tarjeta, 'DIGITAL')
            self.assertIsNone(gc.cliente_id)
        self.assertTrue(any(a.startswith('_giftcards_lote_') for a in archivos))
