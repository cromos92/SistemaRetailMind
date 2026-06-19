"""
Tests de Fidelización: cálculo/redondeo de puntos, acumulación idempotente,
FIFO de vencimiento, canje, reversa de venta anulada y expiración de lotes.
"""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from app.models import (
    Cliente, CuentaPuntos, MovimientoPuntos, ProgramaFidelizacion, Ticket,
    CanjeVale,
)
from app.services import fidelizacion_service
from app.services.fidelizacion_service import FidelizacionError
from app.views_modulo_fidelizacion import construir_reporte_fidelizacion

from .factories import crear_sucursal, crear_usuario, crear_vendedor, crear_empresa


class ProgramaCalculoTest(TestCase):
    def test_valores_default_estrategia_punto_peso(self):
        p = ProgramaFidelizacion()
        self.assertEqual(p.puntos_por_monto, 10)
        self.assertEqual(p.monto_base_acumulacion, 1000)
        self.assertEqual(p.valor_punto_en_pesos, 1)
        self.assertEqual(p.minimo_canje_puntos, 500)
        self.assertEqual(p.puntos_bienvenida, 200)
        self.assertEqual(p.calcular_puntos(10000), 100)
        self.assertEqual(p.tasa_descuento_efectiva, 1.0)

    def test_calcular_puntos_floor(self):
        p = ProgramaFidelizacion(puntos_por_monto=10, monto_base_acumulacion=1000,
                                 redondeo='FLOOR')
        self.assertEqual(p.calcular_puntos(10000), 100)
        self.assertEqual(p.calcular_puntos(10999), 109)  # trunca
        self.assertEqual(p.calcular_puntos(99), 0)

    def test_calcular_puntos_round_y_ceil(self):
        p = ProgramaFidelizacion(puntos_por_monto=10, monto_base_acumulacion=1000)
        p.redondeo = 'ROUND'
        self.assertEqual(p.calcular_puntos(10550), 106)
        p.redondeo = 'CEIL'
        self.assertEqual(p.calcular_puntos(10001), 101)

    def test_tasa_descuento_efectiva(self):
        # 10 pts por $1.000, 1 pto = $1 -> 1% de retorno
        p = ProgramaFidelizacion(puntos_por_monto=10, monto_base_acumulacion=1000,
                                 valor_punto_en_pesos=1)
        self.assertEqual(p.tasa_descuento_efectiva, 1.0)


class FidelizacionServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)
        cls.vendedor = crear_vendedor(empresa=cls.empresa)
        cls.programa = ProgramaFidelizacion.objects.create(
            nombre='Test', puntos_por_monto=10, monto_base_acumulacion=1000,
            valor_punto_en_pesos=1, vigencia_dias=365, minimo_canje_puntos=500,
            puntos_bienvenida=200, activo=True,
        )
        cls.cliente = Cliente.objects.create(
            nombre='Juan', apellido='Pérez', rut='12.345.678-9',
        )

    def _ticket(self, total=10000, correlativo=1, rut='12.345.678-9'):
        return Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal,
            correlativo=correlativo, estado='PAGADO',
            subTotal=total, total=total, responsable='tester',
            cliente_rut=rut,
        )

    def test_resolver_cliente_por_rut_con_y_sin_formato(self):
        self.assertEqual(
            fidelizacion_service.resolver_cliente_por_rut('123456789'), self.cliente
        )
        self.assertEqual(
            fidelizacion_service.resolver_cliente_por_rut('12.345.678-9'), self.cliente
        )
        self.assertIsNone(fidelizacion_service.resolver_cliente_por_rut('99.999.999-9'))

    def test_acumulacion_otorga_puntos_y_bienvenida(self):
        ticket = self._ticket(total=10000)
        res = fidelizacion_service.acumular_puntos_por_venta(ticket)
        self.assertIsNotNone(res)
        # 200 de bienvenida (cuenta nueva) + 100 por la compra
        self.assertEqual(res['puntos_ganados'], 100)
        self.assertEqual(res['valor_ganado_pesos'], 100)
        self.assertEqual(res['saldo_total'], 300)
        # ticket queda enlazado al cliente
        ticket.refresh_from_db()
        self.assertEqual(ticket.cliente_id, self.cliente.id)

    def test_venta_anonima_no_acumula(self):
        ticket = self._ticket(total=10000, rut='', correlativo=2)
        res = fidelizacion_service.acumular_puntos_por_venta(ticket)
        self.assertIsNone(res)

    def test_acumulacion_es_idempotente(self):
        ticket = self._ticket(total=20000)
        fidelizacion_service.acumular_puntos_por_venta(ticket)
        fidelizacion_service.acumular_puntos_por_venta(ticket)  # reintento
        cuenta = CuentaPuntos.objects.get(cliente=self.cliente)
        # bienvenida 200 + 200 de la compra (una sola vez)
        self.assertEqual(cuenta.saldo_puntos, 400)
        self.assertEqual(
            MovimientoPuntos.objects.filter(ticket=ticket, tipo='ACUMULACION').count(), 1
        )

    def test_canje_consume_fifo_y_respeta_minimo(self):
        cuenta, _ = fidelizacion_service.get_or_create_cuenta(self.cliente)
        # otorgar 1.000 puntos vía ajuste
        fidelizacion_service.ajuste_manual(self.cliente, 1000)
        cuenta.refresh_from_db()
        saldo_inicial = cuenta.saldo_puntos
        # canje bajo el mínimo falla
        with self.assertRaises(FidelizacionError):
            fidelizacion_service.canjear_puntos(self.cliente, 100)
        # canje válido
        res = fidelizacion_service.canjear_puntos(self.cliente, 600)
        self.assertEqual(res['puntos_canjeados'], 600)
        self.assertEqual(res['valor_pesos'], 600)  # 600 * $1
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.saldo_puntos, saldo_inicial - 600)

    def test_reversa_venta_descuenta_puntos(self):
        ticket = self._ticket(total=50000, correlativo=5)
        fidelizacion_service.acumular_puntos_por_venta(ticket)
        cuenta = CuentaPuntos.objects.get(cliente=self.cliente)
        saldo_con_compra = cuenta.saldo_puntos  # 200 bienvenida + 500 compra
        self.assertEqual(saldo_con_compra, 700)
        revertidos = fidelizacion_service.reversar_venta(ticket)
        self.assertEqual(revertidos, 500)
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.saldo_puntos, 200)  # solo queda la bienvenida
        # idempotente
        self.assertEqual(fidelizacion_service.reversar_venta(ticket), 0)

    def test_expiracion_de_lotes_vencidos(self):
        cuenta, _ = fidelizacion_service.get_or_create_cuenta(
            self.cliente, otorgar_bienvenida=False
        )
        # Crear un lote ya vencido manualmente
        MovimientoPuntos.objects.create(
            cuenta=cuenta, tipo='ACUMULACION', puntos=40, saldo_resultante=40,
            fecha_expiracion=timezone.localdate() - timedelta(days=1),
        )
        cuenta.saldo_puntos = 40
        cuenta.save(update_fields=['saldo_puntos'])

        total = fidelizacion_service.expirar_lotes_vencidos()
        self.assertEqual(total, 40)
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.saldo_puntos, 0)
        self.assertTrue(
            MovimientoPuntos.objects.filter(cuenta=cuenta, tipo='EXPIRACION').exists()
        )

    def test_consultar_saldo_por_rut(self):
        fidelizacion_service.acumular_puntos_por_venta(self._ticket(correlativo=9))
        info = fidelizacion_service.consultar_saldo(rut='12.345.678-9')
        self.assertEqual(info['saldo_puntos'], 300)  # 200 + 100
        self.assertEqual(info['valor_pesos'], 300)

    def test_normalizar_celular_chileno(self):
        ns = fidelizacion_service.normalizar_celular
        self.assertEqual(ns('+56 9 1234 5678'), '912345678')
        self.assertEqual(ns('912345678'), '912345678')
        self.assertEqual(ns('56912345678'), '912345678')
        self.assertEqual(ns('221234567'), '')   # fijo, no móvil
        self.assertEqual(ns('123'), '')
        self.assertEqual(ns(''), '')

    def test_validar_email(self):
        self.assertTrue(fidelizacion_service.validar_email('a@b.cl'))
        self.assertFalse(fidelizacion_service.validar_email('a@b'))
        self.assertFalse(fidelizacion_service.validar_email('sin-arroba.cl'))

    def test_registrar_cliente_manual_crea_cuenta_con_bienvenida(self):
        cliente, cuenta, creado = fidelizacion_service.registrar_cliente_manual(
            nombre='Ana', apellido='Soto', rut='5.126.663-3',
            email='ana@correo.cl', celular='+56 9 8765 4321',
        )
        self.assertTrue(creado)
        self.assertEqual(cuenta.saldo_puntos, 200)  # bienvenida
        self.assertEqual(cliente.celular, '987654321')

    def test_registrar_cliente_manual_valida_datos(self):
        with self.assertRaises(FidelizacionError):
            fidelizacion_service.registrar_cliente_manual(
                nombre='X', rut='12345678-0', celular='+56 9 1111 1111',  # RUT DV inválido (sería -5)
            )
        with self.assertRaises(FidelizacionError):
            fidelizacion_service.registrar_cliente_manual(
                nombre='X', rut='5.126.663-3', celular='221234567',  # no móvil
            )

    # ===== CANJE CON CÓDIGO (vales) =====

    def _con_puntos(self, puntos):
        """Deja al cliente con `puntos` disponibles (cuenta sin bienvenida)."""
        fidelizacion_service.get_or_create_cuenta(self.cliente, otorgar_bienvenida=False)
        fidelizacion_service.ajuste_manual(self.cliente, puntos)

    def test_generar_vale_compromete_sin_debitar(self):
        self._con_puntos(2000)
        vale = fidelizacion_service.generar_vale_canje(self.cliente, 1200)
        self.assertEqual(vale.estado, 'PENDIENTE')
        self.assertTrue(vale.codigo.startswith('RM-'))
        self.assertEqual(vale.valor_pesos, 1200)  # 1200 * $1
        cuenta = CuentaPuntos.objects.get(cliente=self.cliente)
        # El saldo NO baja (no se debitó), pero el disponible sí.
        self.assertEqual(cuenta.saldo_puntos, 2000)
        self.assertEqual(fidelizacion_service.saldo_disponible_para_reserva(cuenta), 800)
        # No hay movimiento de canje todavía.
        self.assertFalse(MovimientoPuntos.objects.filter(cuenta=cuenta, tipo='CANJE').exists())

    def test_generar_vale_respeta_minimo_y_disponible(self):
        self._con_puntos(600)
        with self.assertRaises(FidelizacionError):
            fidelizacion_service.generar_vale_canje(self.cliente, 100)  # bajo mínimo (500)
        fidelizacion_service.generar_vale_canje(self.cliente, 600)      # toma todo el disponible
        with self.assertRaises(FidelizacionError):
            fidelizacion_service.generar_vale_canje(self.cliente, 500)  # ya no hay disponible

    def test_canjear_vale_debita_fifo_y_es_idempotente(self):
        self._con_puntos(2000)
        vale = fidelizacion_service.generar_vale_canje(self.cliente, 1200)
        res = fidelizacion_service.canjear_vale(vale.codigo, sucursal=self.sucursal)
        self.assertEqual(res['valor_pesos'], 1200)
        self.assertFalse(res['ya_canjeado'])
        cuenta = CuentaPuntos.objects.get(cliente=self.cliente)
        self.assertEqual(cuenta.saldo_puntos, 800)  # 2000 - 1200
        vale.refresh_from_db()
        self.assertEqual(vale.estado, 'CANJEADO')
        self.assertIsNotNone(vale.canjeado_en)
        self.assertEqual(vale.sucursal_canje_id, self.sucursal.id)
        # Reintento: no vuelve a debitar.
        res2 = fidelizacion_service.canjear_vale(vale.codigo)
        self.assertTrue(res2['ya_canjeado'])
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.saldo_puntos, 800)
        self.assertEqual(MovimientoPuntos.objects.filter(cuenta=cuenta, tipo='CANJE').count(), 1)

    def test_canjear_vale_inexistente_o_expirado(self):
        with self.assertRaises(FidelizacionError):
            fidelizacion_service.canjear_vale('RM-NOEXISTE')
        self._con_puntos(2000)
        vale = fidelizacion_service.generar_vale_canje(self.cliente, 1200)
        # Forzar expiración
        vale.expira_en = timezone.now() - timedelta(minutes=1)
        vale.save(update_fields=['expira_en'])
        with self.assertRaises(FidelizacionError):
            fidelizacion_service.canjear_vale(vale.codigo)
        vale.refresh_from_db()
        self.assertEqual(vale.estado, 'EXPIRADO')

    def test_expirar_vales_libera_disponible_sin_tocar_ledger(self):
        self._con_puntos(2000)
        vale = fidelizacion_service.generar_vale_canje(self.cliente, 1200)
        vale.expira_en = timezone.now() - timedelta(minutes=1)
        vale.save(update_fields=['expira_en'])
        total = fidelizacion_service.expirar_vales_vencidos()
        self.assertEqual(total, 1)
        cuenta = CuentaPuntos.objects.get(cliente=self.cliente)
        self.assertEqual(cuenta.saldo_puntos, 2000)  # nada debitado
        self.assertEqual(fidelizacion_service.saldo_disponible_para_reserva(cuenta), 2000)
        self.assertFalse(MovimientoPuntos.objects.filter(cuenta=cuenta, tipo='CANJE').exists())

    def test_anular_vale_recupera_disponible(self):
        self._con_puntos(2000)
        vale = fidelizacion_service.generar_vale_canje(self.cliente, 1200)
        fidelizacion_service.anular_vale(vale, motivo='test')
        vale.refresh_from_db()
        self.assertEqual(vale.estado, 'ANULADO')
        cuenta = CuentaPuntos.objects.get(cliente=self.cliente)
        self.assertEqual(fidelizacion_service.saldo_disponible_para_reserva(cuenta), 2000)

    def test_generar_vale_idempotente_por_key(self):
        self._con_puntos(2000)
        v1 = fidelizacion_service.generar_vale_canje(self.cliente, 1200, idempotency_key='k1')
        v2 = fidelizacion_service.generar_vale_canje(self.cliente, 1200, idempotency_key='k1')
        self.assertEqual(v1.id, v2.id)
        self.assertEqual(CanjeVale.objects.filter(cliente=self.cliente).count(), 1)

    def test_registrar_cliente_existente_actualiza_sin_pisar_con_vacio(self):
        # Alta inicial con email
        c1, _, creado1 = fidelizacion_service.registrar_cliente_manual(
            nombre='Ana', apellido='Soto', rut='5.126.663-3',
            email='viejo@correo.cl', celular='+56 9 8765 4321',
        )
        self.assertTrue(creado1)
        # Segundo registro: corrige email (valor nuevo) y NO manda fecha (vacío)
        c2, _, creado2 = fidelizacion_service.registrar_cliente_manual(
            nombre='Ana', apellido='Soto', rut='5.126.663-3',
            email='nuevo@correo.cl', celular='+56 9 8765 4321',
        )
        self.assertFalse(creado2)
        self.assertEqual(c1.id, c2.id)  # mismo cliente, no duplica
        c2.refresh_from_db()
        self.assertEqual(c2.email, 'nuevo@correo.cl')   # se actualizó
        self.assertEqual(c2.celular, '987654321')        # se conserva


class FidelizacionReporteTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(empresa=self.empresa)
        self.vendedor = crear_vendedor(empresa=self.empresa)
        self.cajera = crear_usuario(
            username='cajera_reporte',
            rol='cajero',
            first_name='Carla',
            last_name='Caja',
        )
        self.programa = ProgramaFidelizacion.objects.create(
            nombre='Test', puntos_por_monto=10, monto_base_acumulacion=1000,
            valor_punto_en_pesos=1, vigencia_dias=365, minimo_canje_puntos=500,
            puntos_bienvenida=200, activo=True,
        )
        self.cliente = Cliente.objects.create(
            nombre='Ana', apellido='Soto', rut='5.126.663-3',
            empresa=self.empresa,
        )

    def _ticket(self, correlativo, total=10000):
        return Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal,
            correlativo=correlativo, estado='PAGADO',
            subTotal=total, total=total, responsable='cajera',
            cliente_rut=self.cliente.rut,
        )

    def _reporte(self):
        ahora = timezone.now()
        return construir_reporte_fidelizacion(
            fecha_inicio_dt=ahora - timedelta(days=1),
            fecha_fin_dt=ahora + timedelta(days=1),
            dias_vencimiento=30,
        )

    def test_reporte_resume_vencimientos_y_canjes_en_pesos(self):
        ticket = self._ticket(correlativo=100, total=50000)
        fidelizacion_service.acumular_puntos_por_venta(ticket, usuario=self.cajera)
        cuenta = CuentaPuntos.objects.get(cliente=self.cliente)

        lote_vencimiento = MovimientoPuntos.objects.create(
            cuenta=cuenta, tipo='ACUMULACION', puntos=300,
            saldo_resultante=cuenta.saldo_puntos + 300,
            fecha_expiracion=timezone.localdate() + timedelta(days=10),
            sucursal=self.sucursal, usuario=self.cajera,
            observaciones='Lote proximo a vencer',
        )
        cuenta.saldo_puntos += lote_vencimiento.puntos
        cuenta.save(update_fields=['saldo_puntos'])

        MovimientoPuntos.objects.create(
            cuenta=cuenta, tipo='CANJE', puntos=-500,
            saldo_resultante=cuenta.saldo_puntos - 500,
            sucursal=self.sucursal, usuario=self.cajera,
            observaciones='Canje de prueba',
        )
        cuenta.saldo_puntos -= 500
        cuenta.save(update_fields=['saldo_puntos'])

        reporte = self._reporte()

        self.assertEqual(reporte['resumen']['puntos_por_vencer'], 300)
        self.assertEqual(reporte['resumen']['valor_por_vencer'], 300)
        self.assertEqual(reporte['resumen']['puntos_canjeados_periodo'], 500)
        self.assertEqual(reporte['resumen']['valor_canjeado_periodo'], 500)
        self.assertEqual(reporte['puntos_por_vencer'][0]['cliente'], 'Ana Soto')
        self.assertEqual(reporte['canjes_recientes'][0]['puntos'], 500)

    def test_reporte_alerta_si_cajera_acumula_muchos_tickets_en_una_cuenta(self):
        for correlativo in range(200, 206):
            ticket = self._ticket(correlativo=correlativo, total=10000)
            fidelizacion_service.acumular_puntos_por_venta(ticket, usuario=self.cajera)

        reporte = self._reporte()

        self.assertEqual(reporte['resumen']['alertas_concentracion'], 1)
        alerta = reporte['alertas_concentracion'][0]
        self.assertEqual(alerta['usuario'], 'Carla Caja')
        self.assertEqual(alerta['cliente'], 'Ana Soto')
        self.assertEqual(alerta['tickets'], 6)
        self.assertEqual(alerta['tickets_usuario'], 6)
        self.assertEqual(alerta['participacion'], 100.0)
        self.assertEqual(alerta['severidad'], 'ALTA')
        self.assertEqual(alerta['puntos'], 600)
        self.assertEqual(alerta['valor_pesos'], 600)


class ReescalarSaldosFidelizacionCommandTest(TestCase):
    """
    El command `reescalar_saldos_fidelizacion --apply` multiplica los saldos por
    un factor para preservar el valor en pesos al bajar valor_punto_en_pesos.
    Debe mantener la invariante de lote (saldo_lote = puntos - puntos_consumidos)
    y ser idempotente (no multiplicar dos veces).
    """

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(empresa=self.empresa)
        self.vendedor = crear_vendedor(empresa=self.empresa)
        self.programa = ProgramaFidelizacion.objects.create(
            nombre='Test', puntos_por_monto=1, monto_base_acumulacion=1000,
            valor_punto_en_pesos=10, vigencia_dias=365, minimo_canje_puntos=50,
            puntos_bienvenida=20, activo=True,
        )
        self.cliente = Cliente.objects.create(
            nombre='Juan', apellido='Pérez', rut='12.345.678-9',
        )
        # Escenario: un lote ACUMULACION de 100 puntos parcialmente consumido
        # (60 canjeados) → saldo 40, lote 100/60.
        fidelizacion_service.get_or_create_cuenta(self.cliente, otorgar_bienvenida=False)
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=1,
            estado='PAGADO', subTotal=100000, total=100000, responsable='t',
            cliente_rut='12.345.678-9',
        )
        fidelizacion_service.acumular_puntos_por_venta(ticket)   # +100 ACUMULACION
        fidelizacion_service.canjear_puntos(self.cliente, 60)    # -60 CANJE (FIFO)
        self.cuenta = CuentaPuntos.objects.get(cliente=self.cliente)

    def _run(self, **kw):
        call_command('reescalar_saldos_fidelizacion', stdout=StringIO(), **kw)

    def test_estado_inicial(self):
        self.assertEqual(self.cuenta.saldo_puntos, 40)
        lote = MovimientoPuntos.objects.get(cuenta=self.cuenta, tipo='ACUMULACION')
        self.assertEqual(lote.puntos, 100)
        self.assertEqual(lote.puntos_consumidos_del_lote, 60)

    def test_apply_preserva_invariante_de_lote(self):
        self._run(apply=True, factor=10)
        self.cuenta.refresh_from_db()
        # saldo ×10 (preservará el valor en pesos cuando el punto pase de $10 a $1).
        self.assertEqual(self.cuenta.saldo_puntos, 400)
        lote = MovimientoPuntos.objects.get(cuenta=self.cuenta, tipo='ACUMULACION')
        self.assertEqual(lote.puntos, 1000)
        self.assertEqual(lote.puntos_consumidos_del_lote, 600)
        self.assertEqual(lote.saldo_lote, 400)          # invariante: 1000 - 600
        self.assertEqual(self.cuenta.saldo_calculado, 400)  # cache == ledger
        canje = MovimientoPuntos.objects.get(cuenta=self.cuenta, tipo='CANJE')
        self.assertEqual(canje.puntos, -600)            # el consumo también escala

    def test_apply_es_idempotente(self):
        self._run(apply=True, factor=10)
        self._run(apply=True, factor=10)   # segundo intento: aborta por centinela
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.saldo_puntos, 400)     # no ×100
        lote = MovimientoPuntos.objects.get(cuenta=self.cuenta, tipo='ACUMULACION')
        self.assertEqual(lote.puntos, 1000)
        self.assertEqual(
            MovimientoPuntos.objects.filter(idempotency_key='reescala_saldos:x10').count(), 1
        )

    def test_dry_run_no_escribe(self):
        self._run(apply=False, factor=10)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.saldo_puntos, 40)      # intacto
        self.assertFalse(
            MovimientoPuntos.objects.filter(idempotency_key='reescala_saldos:x10').exists()
        )
