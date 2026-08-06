"""
Tests de cupones de descuento nominativos.

Cubre las 4 reglas de negocio que justifican el módulo:
  1. Nominativo — sólo lo usa el dueño (el RUT de la venta manda).
  2. Por empresa — no cruza cadenas.
  3. Requiere ficha de cliente usable para emitirse.
  4. Un solo beneficio por venta — excluyente con descuento manual y con vale
     de puntos.
Más el snapshot congelado, la idempotencia y la liberación al anular la venta.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from app.models import CampanaCupon, Cliente, CuponCliente, Ticket
from app.services import cupon_service
from app.services.cupon_service import CuponError

from .factories import crear_empresa, crear_sucursal, crear_usuario, crear_vendedor


def _campana(empresa, **kwargs):
    hoy = timezone.localdate()
    defaults = {
        'nombre': 'Bienvenida 5%',
        'empresa': empresa,
        'tipo_valor': 'PORCENTAJE',
        'valor': 5,
        'monto_minimo': 0,
        'vigencia_dias': 30,
        'fecha_inicio': hoy - timedelta(days=1),
        'fecha_fin': hoy + timedelta(days=30),
        'activo': True,
    }
    defaults.update(kwargs)
    return CampanaCupon.objects.create(**defaults)


class CalculoDescuentoTest(TestCase):
    """La aritmética del descuento, sin tocar la BD."""

    def test_porcentaje_redondea_al_peso(self):
        from app.models import calcular_descuento_cupon
        # 5% de 19.990 = 999,5 -> 1.000 (HALF_UP)
        self.assertEqual(calcular_descuento_cupon('PORCENTAJE', 5, None, 19990), 1000)
        self.assertEqual(calcular_descuento_cupon('PORCENTAJE', 5, None, 20000), 1000)

    def test_tope_recorta_el_porcentaje(self):
        from app.models import calcular_descuento_cupon
        # 5% de 500.000 = 25.000, pero el tope son 10.000
        self.assertEqual(
            calcular_descuento_cupon('PORCENTAJE', 5, 10000, 500000), 10000)

    def test_nunca_supera_el_monto_de_la_venta(self):
        """Un cupón de monto fijo mayor que la compra no deja el total negativo."""
        from app.models import calcular_descuento_cupon
        self.assertEqual(calcular_descuento_cupon('MONTO', 50000, None, 12000), 12000)

    def test_monto_cero_o_negativo_no_descuenta(self):
        from app.models import calcular_descuento_cupon
        self.assertEqual(calcular_descuento_cupon('PORCENTAJE', 5, None, 0), 0)
        self.assertEqual(calcular_descuento_cupon('PORCENTAJE', 5, None, -100), 0)


class FichaClienteTest(TestCase):
    """Regla 3: sin ficha usable no se emite."""

    def test_ficha_valida_pasa(self):
        c = Cliente.objects.create(
            nombre='Juan', apellido='Pérez', rut='12.345.678-5',
            email='juan@example.cl',
        )
        ok, faltantes = cupon_service.ficha_completa(c)
        self.assertTrue(ok, faltantes)

    def test_sin_contacto_no_pasa(self):
        c = Cliente.objects.create(
            nombre='Juan', apellido='Pérez', rut='12.345.678-5')
        ok, faltantes = cupon_service.ficha_completa(c)
        self.assertFalse(ok)
        self.assertIn('email o celular de contacto', faltantes)

    def test_contacto_dummy_del_generico_no_cuenta_como_contacto(self):
        c = Cliente.objects.create(
            nombre='Cliente', apellido='Genérico', rut='12.345.678-5',
            email='generico@test.cl', celular='999999999',
        )
        ok, faltantes = cupon_service.ficha_completa(c)
        self.assertFalse(ok)
        self.assertIn('email o celular de contacto', faltantes)

    def test_rut_generico_no_pasa(self):
        c = Cliente.objects.create(
            nombre='Cliente', apellido='Genérico', rut='66666666-6',
            email='real@example.cl',
        )
        ok, faltantes = cupon_service.ficha_completa(c)
        self.assertFalse(ok)
        self.assertIn('RUT genérico (consumidor final)', faltantes)

    def test_rut_de_empresa_no_pasa(self):
        c = Cliente.objects.create(
            nombre='Comercial', apellido='SpA', rut='76.104.936-4',
            email='contacto@empresa.cl',
        )
        ok, faltantes = cupon_service.ficha_completa(c)
        self.assertFalse(ok)
        self.assertIn(
            'RUT de empresa (el cupón es para clientes particulares)', faltantes)

    def test_cliente_inactivo_no_pasa(self):
        c = Cliente.objects.create(
            nombre='Juan', apellido='Pérez', rut='12.345.678-5',
            email='juan@example.cl', activo=False,
        )
        ok, faltantes = cupon_service.ficha_completa(c)
        self.assertFalse(ok)
        self.assertIn('cliente inactivo', faltantes)


class EmisionCuponTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.campana = _campana(cls.empresa)
        cls.cliente = Cliente.objects.create(
            nombre='Juan', apellido='Pérez', rut='12.345.678-5',
            email='juan@example.cl',
        )

    def test_emitir_congela_el_snapshot_y_normaliza_el_rut(self):
        cupon = cupon_service.emitir_cupon(self.campana, self.cliente)
        self.assertEqual(cupon.estado, 'PENDIENTE')
        self.assertEqual(cupon.tipo_valor, 'PORCENTAJE')
        self.assertEqual(int(cupon.valor), 5)
        self.assertEqual(cupon.empresa_id, self.empresa.id)
        # RUT guardado sin puntos ni guion, para comparar en caja sin formato
        self.assertEqual(cupon.rut_cliente, '123456785')
        self.assertTrue(cupon.codigo.startswith('DC-'))

    def test_editar_la_campana_no_muta_los_cupones_ya_emitidos(self):
        """El motivo de existir del snapshot."""
        cupon = cupon_service.emitir_cupon(self.campana, self.cliente)
        self.campana.valor = 40
        self.campana.save(update_fields=['valor'])
        cupon.refresh_from_db()
        self.assertEqual(int(cupon.valor), 5)
        self.assertEqual(cupon.calcular_descuento(10000), 500)

    # --- límite UNICO: un cupón por cliente para siempre (default) ---
    def test_unico_no_emite_un_segundo_cupon_al_mismo_cliente(self):
        cupon_service.emitir_cupon(self.campana, self.cliente)
        with self.assertRaises(CuponError) as ctx:
            cupon_service.emitir_cupon(self.campana, self.cliente)
        self.assertIn('un solo cupón por cliente', str(ctx.exception))

    def test_unico_no_reemite_aunque_el_cliente_ya_lo_haya_usado(self):
        """La promesa es 'una vez en la vida': usarlo no habilita otro."""
        cupon = cupon_service.emitir_cupon(self.campana, self.cliente)
        CuponCliente.objects.filter(pk=cupon.pk).update(
            estado='CANJEADO', canjeado_en=timezone.now())

        with self.assertRaises(CuponError) as ctx:
            cupon_service.emitir_cupon(self.campana, self.cliente)
        self.assertIn('ya recibió el suyo', str(ctx.exception))

    def test_unico_no_reemite_aunque_se_le_haya_vencido(self):
        cupon = cupon_service.emitir_cupon(self.campana, self.cliente)
        CuponCliente.objects.filter(pk=cupon.pk).update(
            expira_en=timezone.now() - timedelta(days=1))

        with self.assertRaises(CuponError) as ctx:
            cupon_service.emitir_cupon(self.campana, self.cliente)
        self.assertIn('ya recibió el suyo', str(ctx.exception))

    def test_unico_permite_reemitir_si_el_anterior_fue_anulado(self):
        """Anular es corregir un error administrativo: no quema la oportunidad."""
        cupon = cupon_service.emitir_cupon(self.campana, self.cliente)
        CuponCliente.objects.filter(pk=cupon.pk).update(estado='ANULADO')

        nuevo = cupon_service.emitir_cupon(self.campana, self.cliente)
        self.assertEqual(nuevo.estado, 'PENDIENTE')
        self.assertNotEqual(nuevo.codigo, cupon.codigo)

    def test_otra_campana_si_puede_emitirle_al_mismo_cliente(self):
        """UNICO es por campaña, no un veto global al cliente."""
        cupon_service.emitir_cupon(self.campana, self.cliente)
        otra = _campana(self.empresa, nombre='Navidad 10%', valor=10)
        nuevo = cupon_service.emitir_cupon(otra, self.cliente)
        self.assertEqual(nuevo.estado, 'PENDIENTE')

    # --- límite VIVO: campañas recurrentes ---
    def test_vivo_no_emite_dos_sin_usar_a_la_vez(self):
        recurrente = _campana(self.empresa, nombre='Mensual',
                              limite_por_cliente='VIVO')
        cupon_service.emitir_cupon(recurrente, self.cliente)
        with self.assertRaises(CuponError) as ctx:
            cupon_service.emitir_cupon(recurrente, self.cliente)
        self.assertIn('ya tiene un cupón sin usar', str(ctx.exception))

    def test_vivo_reemite_cuando_el_anterior_vencio(self):
        recurrente = _campana(self.empresa, nombre='Mensual',
                              limite_por_cliente='VIVO')
        viejo = cupon_service.emitir_cupon(recurrente, self.cliente)
        CuponCliente.objects.filter(pk=viejo.pk).update(
            expira_en=timezone.now() - timedelta(days=1))

        nuevo = cupon_service.emitir_cupon(recurrente, self.cliente)
        viejo.refresh_from_db()
        self.assertEqual(viejo.estado, 'EXPIRADO')
        self.assertEqual(nuevo.estado, 'PENDIENTE')
        self.assertNotEqual(viejo.codigo, nuevo.codigo)

    def test_emision_es_idempotente_por_clave(self):
        a = cupon_service.emitir_cupon(self.campana, self.cliente,
                                       idempotency_key='k-1')
        b = cupon_service.emitir_cupon(self.campana, self.cliente,
                                       idempotency_key='k-1')
        self.assertEqual(a.id, b.id)
        self.assertEqual(CuponCliente.objects.count(), 1)

    def test_campana_no_vigente_no_emite(self):
        hoy = timezone.localdate()
        vencida = _campana(self.empresa, nombre='Vieja',
                           fecha_inicio=hoy - timedelta(days=60),
                           fecha_fin=hoy - timedelta(days=30))
        with self.assertRaises(CuponError) as ctx:
            cupon_service.emitir_cupon(vencida, self.cliente)
        self.assertIn('no está vigente', str(ctx.exception))

    def test_ficha_incompleta_no_emite(self):
        sin_contacto = Cliente.objects.create(
            nombre='Ana', apellido='Soto', rut='11.111.111-1')
        with self.assertRaises(CuponError) as ctx:
            cupon_service.emitir_cupon(self.campana, sin_contacto)
        self.assertIn('ficha del cliente', str(ctx.exception))

    def test_emision_masiva_omite_los_malos_y_sigue_con_los_buenos(self):
        bueno = Cliente.objects.create(
            nombre='Ana', apellido='Soto', rut='11.111.111-1',
            email='ana@example.cl')
        malo = Cliente.objects.create(
            nombre='Sin', apellido='Contacto', rut='13.579.246-8')

        res = cupon_service.emitir_lote(self.campana, [self.cliente, bueno, malo])

        self.assertEqual(len(res['emitidos']), 2)
        self.assertEqual(len(res['omitidos']), 1)
        self.assertEqual(res['omitidos'][0][0].id, malo.id)


class ValidacionEnCajaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)
        cls.campana = _campana(cls.empresa)
        cls.cliente = Cliente.objects.create(
            nombre='Juan', apellido='Pérez', rut='12.345.678-5',
            email='juan@example.cl',
        )

    def setUp(self):
        self.cupon = cupon_service.emitir_cupon(self.campana, self.cliente)

    def _validar(self, **kwargs):
        base = {'monto': 20000, 'rut_cliente': '12.345.678-5',
                'sucursal': self.sucursal}
        base.update(kwargs)
        return cupon_service.validar_cupon(self.cupon.codigo, **base)

    # --- camino feliz ---
    def test_cupon_valido_devuelve_el_descuento(self):
        res = self._validar()
        self.assertTrue(res['valido'])
        self.assertEqual(res['descuento_pesos'], 1000)   # 5% de 20.000

    def test_el_rut_matchea_con_o_sin_formato(self):
        self.assertTrue(self._validar(rut_cliente='123456785')['valido'])
        self.assertTrue(self._validar(rut_cliente='12.345.678-5')['valido'])
        self.assertTrue(self._validar(rut_cliente='12345678-5')['valido'])

    # --- regla 1: nominativo ---
    def test_otro_cliente_no_puede_usarlo(self):
        res = self._validar(rut_cliente='11.111.111-1')
        self.assertFalse(res['valido'])
        self.assertEqual(res['motivo_codigo'], 'OTRO_CLIENTE')

    def test_cliente_generico_no_puede_usarlo(self):
        """El caso que motivó la funcionalidad: sin identificarse, no hay 5%."""
        res = self._validar(rut_cliente='66666666-6')
        self.assertFalse(res['valido'])
        self.assertEqual(res['motivo_codigo'], 'OTRO_CLIENTE')

    def test_venta_sin_rut_no_puede_usarlo(self):
        res = self._validar(rut_cliente='')
        self.assertFalse(res['valido'])
        self.assertEqual(res['motivo_codigo'], 'SIN_CLIENTE')

    # --- regla 2: por empresa ---
    def test_no_cruza_cadenas(self):
        otra = crear_empresa(nombre='Otra Cadena', rut='77.000.000-1')
        suc_otra = crear_sucursal(empresa=otra, alias='SUC-OTRA')
        res = self._validar(sucursal=suc_otra)
        self.assertFalse(res['valido'])
        self.assertEqual(res['motivo_codigo'], 'OTRA_EMPRESA')

    # --- regla 4: un solo beneficio por venta ---
    def test_no_se_combina_con_descuento_manual(self):
        res = self._validar(tiene_dcto_manual=True)
        self.assertFalse(res['valido'])
        self.assertEqual(res['motivo_codigo'], 'DCTO_MANUAL')

    def test_no_se_combina_con_vale_de_puntos(self):
        res = self._validar(tiene_vale_puntos=True)
        self.assertFalse(res['valido'])
        self.assertEqual(res['motivo_codigo'], 'VALE_ACTIVO')

    # --- estado y condiciones ---
    def test_codigo_inexistente(self):
        res = cupon_service.validar_cupon('DC-NOEXISTE', monto=20000,
                                          rut_cliente='12.345.678-5')
        self.assertFalse(res['valido'])
        self.assertEqual(res['motivo_codigo'], 'NO_EXISTE')

    def test_cupon_vencido(self):
        CuponCliente.objects.filter(pk=self.cupon.pk).update(
            expira_en=timezone.now() - timedelta(minutes=1))
        res = self._validar()
        self.assertFalse(res['valido'])
        self.assertEqual(res['motivo_codigo'], 'EXPIRADO')

    def test_cupon_anulado(self):
        CuponCliente.objects.filter(pk=self.cupon.pk).update(estado='ANULADO')
        res = self._validar()
        self.assertFalse(res['valido'])
        self.assertEqual(res['motivo_codigo'], 'ANULADO')

    def test_monto_minimo_no_alcanzado(self):
        CuponCliente.objects.filter(pk=self.cupon.pk).update(monto_minimo=30000)
        res = self._validar(monto=20000)
        self.assertFalse(res['valido'])
        self.assertEqual(res['motivo_codigo'], 'MONTO_MINIMO')


class CanjeCuponTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)
        cls.vendedor = crear_vendedor(empresa=cls.empresa)
        cls.usuario = crear_usuario(username='cajero_cupon')
        cls.campana = _campana(cls.empresa)
        cls.cliente = Cliente.objects.create(
            nombre='Juan', apellido='Pérez', rut='12.345.678-5',
            email='juan@example.cl',
        )

    def setUp(self):
        self.cupon = cupon_service.emitir_cupon(self.campana, self.cliente)

    def _ticket(self, correlativo=1, total=20000):
        return Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal,
            correlativo=correlativo, estado='PAGADO',
            subTotal=total, total=total, responsable='tester',
            cliente_rut='12.345.678-5',
        )

    def test_canje_marca_el_cupon_y_lo_ata_al_ticket(self):
        ticket = self._ticket()
        cupon = cupon_service.canjear_cupon(
            self.cupon.codigo, ticket=ticket, sucursal=self.sucursal,
            usuario=self.usuario, monto_descuento=1000,
        )
        self.assertEqual(cupon.estado, 'CANJEADO')
        self.assertEqual(cupon.ticket_id, ticket.id)
        self.assertEqual(cupon.monto_descuento, 1000)
        self.assertEqual(cupon.usuario_canje_id, self.usuario.id)
        self.assertIsNotNone(cupon.canjeado_en)

    def test_canje_es_idempotente_para_el_mismo_ticket(self):
        """Reintento del POS: no debe fallar ni reescribir."""
        ticket = self._ticket()
        cupon_service.canjear_cupon(self.cupon.codigo, ticket=ticket,
                                    sucursal=self.sucursal, monto_descuento=1000)
        cupon = cupon_service.canjear_cupon(self.cupon.codigo, ticket=ticket,
                                            sucursal=self.sucursal,
                                            monto_descuento=1000)
        self.assertEqual(cupon.monto_descuento, 1000)
        self.assertEqual(
            CuponCliente.objects.filter(estado='CANJEADO').count(), 1)

    def test_no_se_puede_usar_dos_veces_en_ventas_distintas(self):
        ticket1 = self._ticket(correlativo=1)
        ticket2 = self._ticket(correlativo=2)
        cupon_service.canjear_cupon(self.cupon.codigo, ticket=ticket1,
                                    sucursal=self.sucursal, monto_descuento=1000)
        with self.assertRaises(CuponError) as ctx:
            cupon_service.canjear_cupon(self.cupon.codigo, ticket=ticket2,
                                        sucursal=self.sucursal,
                                        monto_descuento=1000)
        self.assertIn('ya fue utilizado en otra venta', str(ctx.exception))

    def test_canjear_vencido_lo_marca_expirado_y_falla(self):
        CuponCliente.objects.filter(pk=self.cupon.pk).update(
            expira_en=timezone.now() - timedelta(minutes=1))
        ticket = self._ticket()
        with self.assertRaises(CuponError):
            cupon_service.canjear_cupon(self.cupon.codigo, ticket=ticket,
                                        sucursal=self.sucursal,
                                        monto_descuento=1000)
        self.cupon.refresh_from_db()
        # El marcado EXPIRADO sobrevive al error (se lanza fuera del atomic).
        self.assertEqual(self.cupon.estado, 'EXPIRADO')

    def test_anular_la_venta_devuelve_el_cupon_al_cliente(self):
        ticket = self._ticket()
        cupon_service.canjear_cupon(self.cupon.codigo, ticket=ticket,
                                    sucursal=self.sucursal, monto_descuento=1000)

        liberados = cupon_service.liberar_cupon(ticket, motivo='anulación')

        self.assertEqual(liberados, 1)
        self.cupon.refresh_from_db()
        self.assertEqual(self.cupon.estado, 'PENDIENTE')
        self.assertIsNone(self.cupon.ticket_id)
        self.assertEqual(self.cupon.monto_descuento, 0)

    def test_anular_una_venta_con_cupon_ya_vencido_no_lo_resucita(self):
        ticket = self._ticket()
        cupon_service.canjear_cupon(self.cupon.codigo, ticket=ticket,
                                    sucursal=self.sucursal, monto_descuento=1000)
        CuponCliente.objects.filter(pk=self.cupon.pk).update(
            expira_en=timezone.now() - timedelta(days=1))

        liberados = cupon_service.liberar_cupon(ticket)

        self.assertEqual(liberados, 0)
        self.cupon.refresh_from_db()
        self.assertEqual(self.cupon.estado, 'EXPIRADO')


class ExclusionConDescuentoManualTest(TestCase):
    """
    Regla 4 vista desde el cobro: el backend NO puede confiar en la UI.

    El chequeo se rehace sobre las líneas del ticket, porque de lo contrario
    bastaba con validar el cupón primero y aplicar el descuento después.
    """

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)
        cls.campana = _campana(cls.empresa)
        cls.cliente = Cliente.objects.create(
            nombre='Juan', apellido='Pérez', rut='12.345.678-5',
            email='juan@example.cl',
        )

    def test_con_descuento_manual_el_cupon_no_aplica(self):
        cupon = cupon_service.emitir_cupon(self.campana, self.cliente)
        res = cupon_service.validar_cupon(
            cupon.codigo, monto=20000, rut_cliente='12.345.678-5',
            sucursal=self.sucursal, tiene_dcto_manual=True,
        )
        self.assertFalse(res['valido'])
        self.assertEqual(res['motivo_codigo'], 'DCTO_MANUAL')

    def test_sin_descuento_manual_el_mismo_cupon_si_aplica(self):
        """Contraprueba: el rechazo viene del descuento, no del cupón."""
        cupon = cupon_service.emitir_cupon(self.campana, self.cliente)
        res = cupon_service.validar_cupon(
            cupon.codigo, monto=20000, rut_cliente='12.345.678-5',
            sucursal=self.sucursal, tiene_dcto_manual=False,
        )
        self.assertTrue(res['valido'])
        self.assertEqual(res['descuento_pesos'], 1000)


class ExpiracionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.campana = _campana(cls.empresa)
        cls.cliente = Cliente.objects.create(
            nombre='Juan', apellido='Pérez', rut='12.345.678-5',
            email='juan@example.cl',
        )

    def test_expirar_vencidos_solo_toca_los_pendientes_vencidos(self):
        vencido = cupon_service.emitir_cupon(self.campana, self.cliente)
        CuponCliente.objects.filter(pk=vencido.pk).update(
            expira_en=timezone.now() - timedelta(days=1))

        otro_cliente = Cliente.objects.create(
            nombre='Ana', apellido='Soto', rut='11.111.111-1',
            email='ana@example.cl')
        vigente = cupon_service.emitir_cupon(self.campana, otro_cliente)

        n = cupon_service.expirar_vencidos()

        self.assertEqual(n, 1)
        vencido.refresh_from_db()
        vigente.refresh_from_db()
        self.assertEqual(vencido.estado, 'EXPIRADO')
        self.assertEqual(vigente.estado, 'PENDIENTE')
