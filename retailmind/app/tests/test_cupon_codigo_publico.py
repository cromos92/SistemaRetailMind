"""
Tests del CÓDIGO PÚBLICO de campaña (ej. "GISE2438").

Segundo modo del módulo de cupones, decidido el 07-ago-2026: en vez de emitir un
código distinto a cada cliente, se publica UNO solo y cualquier cliente lo
presenta en caja. La protección deja de ser "sólo su dueño" (no hay dueño) y pasa
a ser la CUOTA POR RUT: con `limite_por_cliente='UNICO'`, cada cliente lo usa una
sola vez.

Lo que hay que fijar acá, porque es donde se pierde plata:
  - que el mismo RUT no lo pueda usar dos veces,
  - que exija ficha de cliente (si no, no hay a quién contarle el uso),
  - que cada canje deje su fila de ledger,
  - y que anular la venta le devuelva la cuota al cliente.
"""
import json
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    CampanaCupon, Cliente, CuponCliente, Ticket, Ticket_Productos,
    normalizar_codigo_publico,
)
from app.services import cupon_service
from app.services.cupon_service import CuponError

from .factories import (
    crear_empresa, crear_producto_con_talla, crear_sucursal, crear_usuario,
    crear_vendedor,
)

RUT_JUAN = '12.345.678-5'
RUT_ANA = '11.111.111-1'


class NormalizacionCodigoTest(TestCase):
    """El cajero teclea a mano: el código tiene que tolerar formato."""

    def test_mayusculas_y_espacios(self):
        self.assertEqual(normalizar_codigo_publico('  gise2438 '), 'GISE2438')

    def test_quita_acentos_y_simbolos(self):
        self.assertEqual(normalizar_codigo_publico('gisé 24/38'), 'GISE2438')

    def test_conserva_el_guion(self):
        self.assertEqual(normalizar_codigo_publico('gise-2438'), 'GISE-2438')

    def test_vacio_es_none_no_cadena_vacia(self):
        """
        `codigo_publico` es unique: guardar '' haría chocar a la segunda campaña
        nominativa (en Postgres dos NULL no colisionan, dos '' sí).
        """
        self.assertIsNone(normalizar_codigo_publico(''))
        self.assertIsNone(normalizar_codigo_publico('   '))
        self.assertIsNone(normalizar_codigo_publico('///'))
        self.assertIsNone(normalizar_codigo_publico(None))


class _BaseCodigoPublico(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)
        cls.vendedor = crear_vendedor(empresa=cls.empresa)
        cls.vendedor.sucursales.add(cls.sucursal)
        cls.usuario = crear_usuario(username='cajero_pub', rol='vendedor')
        cls.producto, cls.producto_talla = crear_producto_con_talla(
            cls.sucursal, sku=8800001, stock=50, precioventa=19990)

        hoy = timezone.localdate()
        cls.campana = CampanaCupon.objects.create(
            nombre='Bienvenida GISE', empresa=cls.empresa,
            codigo_publico='GISE2438',
            tipo_valor='PORCENTAJE', valor=5, monto_minimo=0, vigencia_dias=30,
            fecha_inicio=hoy - timedelta(days=1), fecha_fin=hoy + timedelta(days=30),
            activo=True, limite_por_cliente='UNICO',
        )
        cls.juan = Cliente.objects.create(
            nombre='Juan', apellido='Pérez', rut=RUT_JUAN,
            email='juan@example.cl', empresa=cls.empresa)
        cls.ana = Cliente.objects.create(
            nombre='Ana', apellido='Soto', rut=RUT_ANA,
            email='ana@example.cl', empresa=cls.empresa)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.usuario)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

    def _ticket_pendiente(self, correlativo=1, rut=RUT_JUAN, precio=19990,
                          descuento_unitario=0):
        neto = precio - descuento_unitario
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal,
            correlativo=correlativo, estado='PENDIENTE',
            subTotal=precio, descuento=descuento_unitario, total=neto,
            responsable=self.usuario.username, cliente_rut=rut,
            cliente_nombre='Cliente Test',
        )
        Ticket_Productos.objects.create(
            idTicket=ticket, ProductoTalla=self.producto_talla,
            stock=1, precio=precio, descuento_unitario=descuento_unitario,
            subtotal=neto, precio_original=precio,
        )
        return ticket

    def _cobrar(self, ticket, monto, codigo='GISE2438'):
        payload = {
            'cliente': {'rut': ticket.cliente_rut, 'nombre': 'Cliente Test'},
            'estado': 'PAGADO',
            'tipo_documento': 'BOLETA_ELECTRONICA',
            'pagos': [{'metodo_pago': 'EFECTIVO', 'monto': monto}],
            'codigo_cupon_descuento': codigo,
        }
        return self.client.post(
            reverse('registrar_pagos_ticket', args=[ticket.correlativo]),
            data=json.dumps(payload), content_type='application/json',
        )


class ResolucionCodigoTest(_BaseCodigoPublico):

    def test_resuelve_el_codigo_publico_a_su_campana(self):
        cupon, campana = cupon_service.resolver_codigo('GISE2438')
        self.assertIsNone(cupon)
        self.assertEqual(campana.id, self.campana.id)

    def test_resuelve_sin_importar_el_formato_tecleado(self):
        _, campana = cupon_service.resolver_codigo('  gise2438  ')
        self.assertEqual(campana.id, self.campana.id)

    def test_codigo_inexistente_no_resuelve_nada(self):
        cupon, campana = cupon_service.resolver_codigo('NOEXISTE99')
        self.assertIsNone(cupon)
        self.assertIsNone(campana)

    def test_un_cupon_nominativo_gana_sobre_un_codigo_publico_homonimo(self):
        """El individual tiene dueño; romperlo sería peor."""
        nominativa = CampanaCupon.objects.create(
            nombre='Nominativa', empresa=self.empresa,
            tipo_valor='PORCENTAJE', valor=10, vigencia_dias=30,
            fecha_inicio=self.campana.fecha_inicio,
            fecha_fin=self.campana.fecha_fin, activo=True,
        )
        cupon = cupon_service.emitir_cupon(nominativa, self.juan)
        CuponCliente.objects.filter(pk=cupon.pk).update(codigo='GISE2438')

        encontrado, campana = cupon_service.resolver_codigo('GISE2438')
        self.assertIsNotNone(encontrado)
        self.assertIsNone(campana)


class ValidacionCodigoPublicoTest(_BaseCodigoPublico):

    def _validar(self, **kwargs):
        base = {'monto': 19990, 'rut_cliente': RUT_JUAN,
                'sucursal': self.sucursal}
        base.update(kwargs)
        return cupon_service.validar_cupon('GISE2438', **base)

    def test_cliente_con_ficha_lo_puede_usar(self):
        info = self._validar()
        self.assertTrue(info['valido'], info.get('motivo'))
        self.assertEqual(info['descuento_pesos'], 1000)   # 5% de 19.990
        self.assertTrue(info['es_codigo_publico'])
        self.assertEqual(info['cliente_nombre'], self.juan.nombre_completo)

    def test_sin_rut_no_se_puede_controlar_el_uso(self):
        info = self._validar(rut_cliente='')
        self.assertFalse(info['valido'])
        self.assertEqual(info['motivo_codigo'], 'SIN_CLIENTE')

    def test_rut_generico_de_consumidor_final_no_sirve(self):
        info = self._validar(rut_cliente='66666666-6')
        self.assertFalse(info['valido'])
        self.assertEqual(info['motivo_codigo'], 'SIN_CLIENTE')

    def test_rut_sin_ficha_en_el_crm_se_rechaza(self):
        # RUT válido (DV correcto) pero sin ficha en el CRM: tiene que caer por
        # SIN_FICHA, no por RUT_INVALIDO — son mensajes distintos para el cajero.
        info = self._validar(rut_cliente='9.876.543-3')
        self.assertFalse(info['valido'])
        self.assertEqual(info['motivo_codigo'], 'SIN_FICHA')

    def test_rut_de_empresa_se_rechaza(self):
        Cliente.objects.create(nombre='Comercial', apellido='SpA',
                               rut='76.104.936-4', email='c@e.cl')
        info = self._validar(rut_cliente='76.104.936-4')
        self.assertFalse(info['valido'])
        self.assertEqual(info['motivo_codigo'], 'RUT_EMPRESA')

    def test_cliente_inactivo_no_tiene_ficha_util(self):
        Cliente.objects.filter(pk=self.juan.pk).update(activo=False)
        info = self._validar()
        self.assertFalse(info['valido'])
        self.assertEqual(info['motivo_codigo'], 'SIN_FICHA')

    def test_campana_desactivada_se_rechaza(self):
        CampanaCupon.objects.filter(pk=self.campana.pk).update(activo=False)
        info = self._validar()
        self.assertFalse(info['valido'])
        self.assertEqual(info['motivo_codigo'], 'INACTIVA')

    def test_campana_terminada_se_rechaza(self):
        ayer = timezone.localdate() - timedelta(days=1)
        CampanaCupon.objects.filter(pk=self.campana.pk).update(
            fecha_inicio=ayer - timedelta(days=10), fecha_fin=ayer)
        info = self._validar()
        self.assertFalse(info['valido'])
        self.assertEqual(info['motivo_codigo'], 'EXPIRADO')

    def test_no_se_combina_con_descuento_manual(self):
        info = self._validar(tiene_dcto_manual=True)
        self.assertFalse(info['valido'])
        self.assertEqual(info['motivo_codigo'], 'DCTO_MANUAL')

    def test_no_se_combina_con_vale_de_puntos(self):
        info = self._validar(tiene_vale_puntos=True)
        self.assertFalse(info['valido'])
        self.assertEqual(info['motivo_codigo'], 'VALE_ACTIVO')

    def test_no_cruza_de_cadena(self):
        otra = crear_sucursal(empresa=crear_empresa(nombre='Otra', rut='77.000.000-1'),
                              alias='OTRA-1')
        info = self._validar(sucursal=otra)
        self.assertFalse(info['valido'])
        self.assertEqual(info['motivo_codigo'], 'OTRA_EMPRESA')

    def test_respeta_la_compra_minima(self):
        CampanaCupon.objects.filter(pk=self.campana.pk).update(monto_minimo=50000)
        info = self._validar(monto=19990)
        self.assertFalse(info['valido'])
        self.assertEqual(info['motivo_codigo'], 'MONTO_MINIMO')

    def test_validar_no_escribe_nada(self):
        """Se llama en cada tecla del POS: no puede dejar rastro."""
        self._validar()
        self._validar()
        self.assertEqual(CuponCliente.objects.count(), 0)


class CuotaPorClienteTest(_BaseCodigoPublico):
    """La única protección de un código compartido."""

    def test_el_mismo_cliente_no_lo_usa_dos_veces(self):
        t1 = self._ticket_pendiente(correlativo=1)
        self.assertEqual(self._cobrar(t1, 18990).status_code, 200)

        t2 = self._ticket_pendiente(correlativo=2)
        resp = self._cobrar(t2, 18990)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('ya usó esta promoción', resp.json().get('error', ''))

    def test_otro_cliente_si_lo_puede_usar(self):
        t1 = self._ticket_pendiente(correlativo=1, rut=RUT_JUAN)
        self.assertEqual(self._cobrar(t1, 18990).status_code, 200)

        t2 = self._ticket_pendiente(correlativo=2, rut=RUT_ANA)
        resp = self._cobrar(t2, 18990)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(CuponCliente.objects.filter(estado='CANJEADO').count(), 2)

    def test_sin_limite_deja_repetir(self):
        CampanaCupon.objects.filter(pk=self.campana.pk).update(
            limite_por_cliente='SIN_LIMITE')
        t1 = self._ticket_pendiente(correlativo=1)
        self.assertEqual(self._cobrar(t1, 18990).status_code, 200)
        t2 = self._ticket_pendiente(correlativo=2)
        self.assertEqual(self._cobrar(t2, 18990).status_code, 200)


class CanjeCodigoPublicoTest(_BaseCodigoPublico):

    def test_el_canje_deja_su_fila_de_ledger(self):
        ticket = self._ticket_pendiente()
        resp = self._cobrar(ticket, 18990)
        self.assertEqual(resp.status_code, 200, resp.content)

        fila = CuponCliente.objects.get()
        self.assertEqual(fila.origen, 'PUBLICO')
        self.assertEqual(fila.estado, 'CANJEADO')
        self.assertEqual(fila.cliente_id, self.juan.id)
        self.assertEqual(fila.ticket_id, ticket.id)
        self.assertEqual(fila.monto_descuento, 1000)
        self.assertEqual(fila.campana_id, self.campana.id)
        self.assertTrue(fila.codigo.startswith('GISE2438-'),
                        f'código poco trazable: {fila.codigo}')

    def test_el_descuento_llega_al_ticket_y_al_dte(self):
        from app.models import Dte
        ticket = self._ticket_pendiente()
        self._cobrar(ticket, 18990)

        ticket.refresh_from_db()
        self.assertEqual(ticket.descuento_cupon, 1000)
        self.assertEqual(ticket.total, 18990)
        dte = Dte.objects.get(referencias=f'TICKET-{ticket.correlativo}')
        self.assertEqual(int(dte.monto_con_iva), 18990)

    def test_canjear_sin_ficha_de_cliente_falla(self):
        ticket = self._ticket_pendiente(rut='13.870.216-4')
        with self.assertRaises(CuponError):
            cupon_service.canjear_cupon(
                'GISE2438', ticket=ticket, sucursal=self.sucursal,
                usuario=self.usuario, monto_descuento=1000)

    def test_reintento_sobre_el_mismo_ticket_no_duplica(self):
        ticket = self._ticket_pendiente()
        ticket.estado = 'PAGADO'
        ticket.save()
        a = cupon_service.canjear_cupon(
            'GISE2438', ticket=ticket, sucursal=self.sucursal,
            usuario=self.usuario, monto_descuento=1000)
        b = cupon_service.canjear_cupon(
            'GISE2438', ticket=ticket, sucursal=self.sucursal,
            usuario=self.usuario, monto_descuento=1000)
        self.assertEqual(a.id, b.id)
        self.assertEqual(CuponCliente.objects.count(), 1)


class ReversaCodigoPublicoTest(_BaseCodigoPublico):

    def test_anular_la_venta_le_devuelve_la_cuota_al_cliente(self):
        ticket = self._ticket_pendiente()
        self._cobrar(ticket, 18990)
        ticket.refresh_from_db()

        from app.views_modulo_ventas import _liberar_cupon_de_venta
        _liberar_cupon_de_venta(ticket, 'anulación de prueba')

        fila = CuponCliente.objects.get()
        # ANULADO (no PENDIENTE): el `codigo` interno GISE2438-XK7M el cliente
        # nunca lo vio. Lo que se le devuelve es el derecho a usar GISE2438.
        self.assertEqual(fila.estado, 'ANULADO')

        # Y efectivamente lo puede volver a usar.
        t2 = self._ticket_pendiente(correlativo=2)
        self.assertEqual(self._cobrar(t2, 18990).status_code, 200)


class EmisionBloqueadaTest(_BaseCodigoPublico):
    """
    Una campaña de código público no emite cupones uno por uno: ofrecerlo sería
    entregar un código que compite con el publicado.
    """

    def setUp(self):
        super().setUp()
        # La API de emisión exige `fidelizacion_cupones.puede_crear`; en una BD
        # de test recién creada no hay ningún PermisoRol, así que se usa el rol
        # administrador (que el decorador deja pasar).
        self.usuario.rol = 'administrador'
        self.usuario.save()
        self.client.force_login(self.usuario)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

    def test_emitir_sobre_campana_publica_falla(self):
        with self.assertRaises(CuponError) as ctx:
            cupon_service.emitir_cupon(self.campana, self.juan)
        self.assertIn('GISE2438', str(ctx.exception))

    def test_la_api_de_emision_lo_rechaza(self):
        resp = self.client.post(
            reverse('api_emitir_cupon'),
            data=json.dumps({'campana_id': self.campana.id,
                             'cliente_id': self.juan.id}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('código público', resp.json().get('error', ''))


class GuardarCampanaTest(_BaseCodigoPublico):
    """El modal: qué acepta y qué rechaza al guardar."""

    def _guardar(self, **extra):
        payload = {
            'nombre': 'Nueva promo', 'empresa_id': self.empresa.id,
            'tipo_valor': 'PORCENTAJE', 'valor': 5, 'monto_minimo': 0,
            'vigencia_dias': 30,
            'fecha_inicio': timezone.localdate().isoformat(),
            'fecha_fin': (timezone.localdate() + timedelta(days=30)).isoformat(),
            'limite_por_cliente': 'UNICO', 'activo': True,
        }
        payload.update(extra)
        return self.client.post(
            reverse('api_guardar_campana'), data=json.dumps(payload),
            content_type='application/json')

    def setUp(self):
        super().setUp()
        self.usuario.rol = 'administrador'
        self.usuario.save()
        self.client.force_login(self.usuario)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def test_guarda_el_codigo_normalizado(self):
        resp = self._guardar(codigo_publico='  promo-verano ')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['campana']['codigo_publico'], 'PROMO-VERANO')
        self.assertTrue(resp.json()['campana']['es_codigo_publico'])

    def test_sin_codigo_la_campana_queda_nominativa(self):
        resp = self._guardar(codigo_publico='')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(resp.json()['campana']['es_codigo_publico'])
        # NULL, no '': si no, la segunda campaña nominativa choca con el unique.
        self.assertIsNone(
            CampanaCupon.objects.get(id=resp.json()['campana']['id']).codigo_publico)

    def test_dos_campanas_nominativas_no_chocan_entre_si(self):
        self.assertEqual(self._guardar(codigo_publico='').status_code, 200)
        self.assertEqual(
            self._guardar(nombre='Otra más', codigo_publico='').status_code, 200)

    def test_codigo_repetido_se_rechaza(self):
        resp = self._guardar(codigo_publico='GISE2438')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Ya existe otra campaña', resp.json()['error'])

    def test_codigo_muy_corto_se_rechaza(self):
        resp = self._guardar(codigo_publico='AB')
        self.assertEqual(resp.status_code, 400)

    def test_no_se_puede_cambiar_de_modo_con_cupones_ya_usados(self):
        ticket = self._ticket_pendiente()
        self._cobrar(ticket, 18990)

        resp = self.client.post(
            reverse('api_guardar_campana'),
            data=json.dumps({
                'id': self.campana.id, 'nombre': self.campana.nombre,
                'tipo_valor': 'PORCENTAJE', 'valor': 5, 'monto_minimo': 0,
                'vigencia_dias': 30,
                'fecha_inicio': self.campana.fecha_inicio.isoformat(),
                'fecha_fin': self.campana.fecha_fin.isoformat(),
                'limite_por_cliente': 'UNICO', 'activo': True,
                'codigo_publico': '',
            }),
            content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('no se puede cambiar', resp.json()['error'].lower())
