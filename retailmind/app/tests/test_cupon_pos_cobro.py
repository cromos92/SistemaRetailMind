"""
Tests del cupón nominativo EN EL COBRO REAL DEL POS (`/app/pos-dashboard/`).

`test_cupones.py` prueba el servicio en memoria y ninguno de sus 44 tests usa
`self.client`: todo el código que mueve plata quedaba sin cubrir. Este archivo
ataca exactamente eso, con tres focos:

1. **El DTE refleja lo cobrado.** `generar_dte_desde_ticket` (la del POS)
   contemplaba `descuento_fidelizacion` pero no `descuento_cupon`: emitía la
   boleta por el monto BRUTO y, de paso, reescribía `ticket.total` al bruto en
   la BD. El cliente pagaba $18.990 y la boleta salía por $19.990 — IVA
   declarado sobre plata que nunca entró y descuadre de caja por venta.

2. **El checkout deja cobrar el monto correcto.** El guard de cobertura de
   pagos corre contra el total ya descontado.

3. **La reversa devuelve el cupón.** `liberar_cupon` era código inalcanzable:
   su único llamador exigía ticket PENDIENTE y un cupón canjeado siempre vive
   en un ticket PAGADO.

Nota: los endpoints viven bajo `/app/api/`, que `PermisosMenuMiddleware` NO
intercepta (el mapa no matchea rutas con `/api/`), así que basta con estar
autenticado y tener sucursal en sesión.
"""
import json
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    CampanaCupon, Cliente, CuponCliente, Dte, Dte_Detalle_Pago,
    Ticket, Ticket_Productos,
)
from app.services import cupon_service
from app.views_modulo_ventas import construir_ticket_data, generar_dte_desde_ticket

from .factories import (
    crear_empresa, crear_producto_con_talla, crear_sucursal, crear_usuario,
    crear_vendedor,
)

RUT_CLIENTE = '12.345.678-5'


def _campana(empresa, **kwargs):
    hoy = timezone.localdate()
    defaults = {
        'nombre': 'GISE 5%',
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


class _BaseCuponPOS(TestCase):
    """Entorno mínimo: sucursal + producto con stock + cliente con cupón vivo."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)
        cls.vendedor = crear_vendedor(empresa=cls.empresa)
        cls.vendedor.sucursales.add(cls.sucursal)
        cls.usuario = crear_usuario(username='cajero_pos', rol='vendedor')
        cls.producto, cls.producto_talla = crear_producto_con_talla(
            cls.sucursal, sku=7700001, stock=50, precioventa=19990,
        )
        cls.campana = _campana(cls.empresa)
        cls.cliente = Cliente.objects.create(
            nombre='Juan', apellido='Pérez', rut=RUT_CLIENTE,
            email='juan@example.cl', empresa=cls.empresa,
        )

    def setUp(self):
        self.cupon = cupon_service.emitir_cupon(self.campana, self.cliente)
        self.client = Client()
        self.client.force_login(self.usuario)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _ticket_pendiente(self, correlativo=1, precio=19990, cantidad=1,
                          descuento_unitario=0):
        """Ticket PENDIENTE con una línea real, listo para cobrar."""
        bruto = precio * cantidad
        neto = (precio - descuento_unitario) * cantidad
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal,
            correlativo=correlativo, estado='PENDIENTE',
            subTotal=bruto, descuento=descuento_unitario * cantidad, total=neto,
            responsable=self.usuario.username, cliente_rut=RUT_CLIENTE,
            cliente_nombre='Juan Pérez',
        )
        Ticket_Productos.objects.create(
            idTicket=ticket, ProductoTalla=self.producto_talla,
            stock=cantidad, precio=precio,
            descuento_unitario=descuento_unitario, subtotal=neto,
            precio_original=precio,
        )
        return ticket

    def _cobrar(self, ticket, monto_pagado, codigo_cupon=None,
                tipo_documento='BOLETA_ELECTRONICA'):
        """POST real al endpoint de cobro del POS."""
        payload = {
            'cliente': {'rut': RUT_CLIENTE, 'nombre': 'Juan Pérez'},
            'estado': 'PAGADO',
            'tipo_documento': tipo_documento,
            'pagos': [{'metodo_pago': 'EFECTIVO', 'monto': monto_pagado}],
        }
        if codigo_cupon:
            payload['codigo_cupon_descuento'] = codigo_cupon
        return self.client.post(
            reverse('registrar_pagos_ticket', args=[ticket.correlativo]),
            data=json.dumps(payload), content_type='application/json',
        )


class DteConCuponTest(_BaseCuponPOS):
    """
    El defecto que más costaba: el documento tributario emitido por más plata de
    la que el cliente pagó.
    """

    def test_boleta_se_emite_por_el_monto_cobrado_no_por_el_bruto(self):
        # $19.990 con 5% -> descuento $1.000 (HALF_UP sobre 999,5) -> paga $18.990
        ticket = self._ticket_pendiente()
        resp = self._cobrar(ticket, 18990, codigo_cupon=self.cupon.codigo)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json().get('success'), resp.content)

        dte = Dte.objects.get(referencias=f'TICKET-{ticket.correlativo}')
        self.assertEqual(int(dte.monto_con_iva), 18990)

    def test_ticket_total_no_se_revierte_al_monto_bruto(self):
        """
        La rama de 'Desfase' de generar_dte_desde_ticket reescribía ticket.total
        con la suma de líneas (sin el cupón). Eso arrastraba el error a la
        cuadratura: VENTA TOTAL sumaba $19.990 contra pagos por $18.990.
        """
        ticket = self._ticket_pendiente()
        self._cobrar(ticket, 18990, codigo_cupon=self.cupon.codigo)

        ticket.refresh_from_db()
        self.assertEqual(ticket.total, 18990)
        self.assertEqual(ticket.descuento_cupon, 1000)

    def test_el_dte_cuadra_contra_sus_propios_pagos(self):
        """Un DTE cuyo MntTotal no coincide con sus Dte_Detalle_Pago está roto."""
        ticket = self._ticket_pendiente()
        self._cobrar(ticket, 18990, codigo_cupon=self.cupon.codigo)

        dte = Dte.objects.get(referencias=f'TICKET-{ticket.correlativo}')
        total_pagos = sum(
            p.monto for p in Dte_Detalle_Pago.objects.filter(dte=dte))
        self.assertEqual(total_pagos, int(dte.monto_con_iva))

    def test_neto_mas_iva_reconstruyen_el_total_descontado(self):
        ticket = self._ticket_pendiente()
        self._cobrar(ticket, 18990, codigo_cupon=self.cupon.codigo)

        dte = Dte.objects.get(referencias=f'TICKET-{ticket.correlativo}')
        iva = int(dte.monto_con_iva) - int(dte.monto_neto)
        self.assertEqual(int(dte.monto_neto) + iva, 18990)

    def test_venta_sin_cupon_no_cambia(self):
        """Control de no-regresión: el camino normal sigue igual."""
        ticket = self._ticket_pendiente(correlativo=2)
        resp = self._cobrar(ticket, 19990)
        self.assertEqual(resp.status_code, 200, resp.content)

        ticket.refresh_from_db()
        self.assertEqual(ticket.total, 19990)
        self.assertIsNone(ticket.descuento_cupon)
        dte = Dte.objects.get(referencias=f'TICKET-{ticket.correlativo}')
        self.assertEqual(int(dte.monto_con_iva), 19990)


class TxtAceptaConCuponTest(_BaseCuponPOS):
    """
    El TXT es lo que llega al SII vía Acepta. Sin la línea DscRcgGlobal el
    documento se timbra por el monto bruto y la copia del cliente no tiene
    ninguna glosa que explique el descuento.
    """

    def _generar_boleta(self, ticket):
        return generar_dte_desde_ticket(
            ticket, 'BOLETA_ELECTRONICA', self.usuario)

    def test_boleta_declara_el_descuento_del_cupon_como_dscrcgglobal(self):
        ticket = self._ticket_pendiente()
        ticket.descuento_cupon = 1000
        ticket.total = 18990
        ticket.estado = 'PAGADO'
        ticket.save()

        dte = self._generar_boleta(ticket)
        contenido = (dte.archivo_txt_data or {}).get('contenido', '')
        self.assertIn('Descuento Cupon', contenido)
        self.assertEqual(int(dte.monto_con_iva), 18990)

    def test_boleta_declara_tambien_el_vale_de_puntos(self):
        """
        Mismo mecanismo de cabecera que el cupón: el header ya venía neto del
        vale pero el detalle iba a precio completo y no se declaraba ningún
        descuento, así que sum(detalle) != MntTotal.
        """
        ticket = self._ticket_pendiente(correlativo=3)
        ticket.descuento_fidelizacion = 2000
        ticket.total = 17990
        ticket.estado = 'PAGADO'
        ticket.save()

        dte = self._generar_boleta(ticket)
        contenido = (dte.archivo_txt_data or {}).get('contenido', '')
        self.assertIn('Descuento Puntos Fidelizacion', contenido)
        self.assertEqual(int(dte.monto_con_iva), 17990)

    def test_sin_beneficio_no_se_agrega_linea_de_descuento(self):
        """No inventar un DscRcgGlobal fantasma en la venta normal."""
        ticket = self._ticket_pendiente(correlativo=4)
        ticket.estado = 'PAGADO'
        ticket.save()

        dte = self._generar_boleta(ticket)
        contenido = (dte.archivo_txt_data or {}).get('contenido', '')
        self.assertNotIn('Descuento Cupon', contenido)
        self.assertNotIn('Descuento Puntos Fidelizacion', contenido)


class CoberturaDePagosConCuponTest(_BaseCuponPOS):
    """El cajero tiene que poder cobrar el precio con descuento."""

    def test_pagar_el_total_ya_descontado_es_suficiente(self):
        ticket = self._ticket_pendiente()
        resp = self._cobrar(ticket, 18990, codigo_cupon=self.cupon.codigo)
        self.assertEqual(resp.status_code, 200, resp.content)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, 'PAGADO')

    def test_pagar_menos_que_el_total_descontado_se_rechaza(self):
        ticket = self._ticket_pendiente()
        resp = self._cobrar(ticket, 15000, codigo_cupon=self.cupon.codigo)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get('error_tipo'), 'PAGOS_INSUFICIENTES')
        ticket.refresh_from_db()
        self.assertNotEqual(ticket.estado, 'PAGADO')


class CanjeEnElCobroTest(_BaseCuponPOS):
    """Las reglas duras, ejercidas por HTTP y no por el servicio."""

    def test_el_cobro_marca_el_cupon_canjeado_y_lo_ata_al_ticket(self):
        ticket = self._ticket_pendiente()
        self._cobrar(ticket, 18990, codigo_cupon=self.cupon.codigo)

        self.cupon.refresh_from_db()
        self.assertEqual(self.cupon.estado, 'CANJEADO')
        self.assertEqual(self.cupon.ticket_id, ticket.id)
        self.assertEqual(self.cupon.monto_descuento, 1000)

    def test_cupon_de_otro_cliente_no_deja_cobrar(self):
        otro = Cliente.objects.create(
            nombre='Ana', apellido='Soto', rut='11.111.111-1',
            email='ana@example.cl', empresa=self.empresa,
        )
        cupon_ajeno = cupon_service.emitir_cupon(_campana(
            self.empresa, nombre='Otra campaña'), otro)

        ticket = self._ticket_pendiente()
        resp = self._cobrar(ticket, 18990, codigo_cupon=cupon_ajeno.codigo)

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get('error_tipo'), 'CUPON_OTRO_CLIENTE')
        cupon_ajeno.refresh_from_db()
        self.assertEqual(cupon_ajeno.estado, 'PENDIENTE')

    def test_descuento_manual_en_las_lineas_bloquea_el_cupon(self):
        """
        El re-chequeo server-side es la única defensa real: la UI podía validar
        el cupón primero y aplicar el descuento después.
        """
        ticket = self._ticket_pendiente(descuento_unitario=2000)
        resp = self._cobrar(ticket, 16990, codigo_cupon=self.cupon.codigo)

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get('error_tipo'), 'CUPON_DCTO_MANUAL')

    def test_cupon_y_vale_juntos_se_rechazan(self):
        ticket = self._ticket_pendiente()
        payload = {
            'cliente': {'rut': RUT_CLIENTE, 'nombre': 'Juan Pérez'},
            'estado': 'PAGADO',
            'tipo_documento': 'BOLETA_ELECTRONICA',
            'pagos': [{'metodo_pago': 'EFECTIVO', 'monto': 18990}],
            'codigo_cupon_descuento': self.cupon.codigo,
            'codigo_vale_canje': 'VP-XXXXXXXX',
        }
        resp = self.client.post(
            reverse('registrar_pagos_ticket', args=[ticket.correlativo]),
            data=json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get('error_tipo'), 'BENEFICIOS_EXCLUYENTES')

    def test_factura_no_acepta_cupon(self):
        """Regla 3: es beneficio de cliente particular."""
        ticket = self._ticket_pendiente()
        resp = self._cobrar(ticket, 18990, codigo_cupon=self.cupon.codigo,
                            tipo_documento='FACTURA_ELECTRONICA')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get('error_tipo'), 'CUPON_NO_APLICA')


class ReversaDelCuponTest(_BaseCuponPOS):
    """
    `liberar_cupon` existía y estaba probado, pero era inalcanzable: su único
    llamador exigía ticket PENDIENTE y un cupón canjeado siempre vive en un
    ticket PAGADO. Estos tests cubren las rutas que anulan ventas COBRADAS.
    """

    def _cobrar_con_cupon(self, correlativo=1):
        ticket = self._ticket_pendiente(correlativo=correlativo)
        resp = self._cobrar(ticket, 18990, codigo_cupon=self.cupon.codigo)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.cupon.refresh_from_db()
        self.assertEqual(self.cupon.estado, 'CANJEADO')
        ticket.refresh_from_db()
        return ticket

    def test_anular_documento_venta_devuelve_el_cupon(self):
        ticket = self._cobrar_con_cupon()
        resp = self.client.post(
            reverse('anular_documento_venta'),
            data=json.dumps({'tipo_documento': 'TICKET',
                             'documento_id': ticket.id,
                             'motivo': 'error de caja'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        self.cupon.refresh_from_db()
        self.assertEqual(self.cupon.estado, 'PENDIENTE')
        self.assertIsNone(self.cupon.ticket_id)
        self.assertEqual(self.cupon.monto_descuento, 0)

    def test_eliminar_documento_desde_cuadratura_devuelve_el_cupon(self):
        ticket = self._cobrar_con_cupon(correlativo=5)
        dte = Dte.objects.get(referencias=f'TICKET-{ticket.correlativo}')
        # El vínculo DTE→Ticket es por folio, no por FK.
        Ticket.objects.filter(pk=ticket.pk).update(
            folio_dte=dte.numero_documento)

        # Este endpoint es solo para administradores.
        admin = crear_usuario(username='admin_cuadratura', rol='administrador')
        admin_client = Client()
        admin_client.force_login(admin)
        session = admin_client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

        resp = admin_client.post(
            reverse('eliminar_documento_venta'),
            data=json.dumps({'documento_id': dte.id,
                             'motivo': 'boleta mal emitida'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json().get('success'), resp.content)

        self.cupon.refresh_from_db()
        self.assertEqual(self.cupon.estado, 'PENDIENTE')

    def test_liberar_dos_veces_no_rompe_ni_duplica(self):
        ticket = self._cobrar_con_cupon(correlativo=6)
        from app.views_modulo_ventas import _liberar_cupon_de_venta

        self.assertEqual(_liberar_cupon_de_venta(ticket, 'primera'), 1)
        # La segunda no encuentra nada CANJEADO: no libera de nuevo.
        self.assertEqual(_liberar_cupon_de_venta(ticket, 'segunda'), 0)
        self.assertEqual(
            CuponCliente.objects.filter(estado='PENDIENTE').count(), 1)

    def test_cupon_vencido_mientras_tanto_queda_expirado_no_pendiente(self):
        """No se le regala vigencia extra al cliente."""
        ticket = self._cobrar_con_cupon(correlativo=7)
        CuponCliente.objects.filter(pk=self.cupon.pk).update(
            expira_en=timezone.now() - timedelta(minutes=1))

        from app.views_modulo_ventas import _liberar_cupon_de_venta
        _liberar_cupon_de_venta(ticket, 'anulación tardía')

        self.cupon.refresh_from_db()
        self.assertEqual(self.cupon.estado, 'EXPIRADO')


class NotaCreditoSobreVentaConCuponTest(_BaseCuponPOS):
    """
    Al hacer el DTE consistente, la NC hereda un cabezal YA neto del cupón
    mientras su detalle se copia de `Dte_Productos`, que guarda el precio de
    lista. Si el detalle no se escala, sum(detalle) != MntNeto y Acepta rechaza
    el TXT: la venta anulada no se puede acreditar ante el SII.
    """

    def test_normalizar_detalle_reconoce_la_base_bruta_con_descuento_global(self):
        from app.views_modulo_documentos import normalizar_detalle_para_tipo

        # Boleta de $19.990 con cupón de $1.000 -> DTE por $18.990.
        # La NC hereda neto 15.958 y descuento global 1.000, pero su detalle
        # viene del precio de lista ($19.990).
        detalle = [{'cantidad': 1, 'precio_unitario': 19990, 'monto_item': 19990}]
        totales = {'monto_neto': 15958, 'monto_total': 18990,
                   'descuento_global': 1000}

        normalizar_detalle_para_tipo(detalle, totales, 61)

        self.assertEqual(sum(d['monto_item'] for d in detalle), 15958)

    def test_normalizar_no_toca_un_detalle_ya_correcto(self):
        from app.views_modulo_documentos import normalizar_detalle_para_tipo

        detalle = [{'cantidad': 1, 'precio_unitario': 15958, 'monto_item': 15958}]
        totales = {'monto_neto': 15958, 'monto_total': 18990,
                   'descuento_global': 1000}

        normalizar_detalle_para_tipo(detalle, totales, 61)
        self.assertEqual(detalle[0]['monto_item'], 15958)

    def test_normalizar_sigue_sin_tocar_una_base_desconocida(self):
        """El guard conservador no se debilitó: base rara -> no se toca."""
        from app.views_modulo_documentos import normalizar_detalle_para_tipo

        detalle = [{'cantidad': 1, 'precio_unitario': 7777, 'monto_item': 7777}]
        totales = {'monto_neto': 15958, 'monto_total': 18990,
                   'descuento_global': 1000}

        normalizar_detalle_para_tipo(detalle, totales, 61)
        self.assertEqual(detalle[0]['monto_item'], 7777)


class FacturaConCuponTest(_BaseCuponPOS):
    """
    La rama FACTURA del TXT lleva los montos en NETO (/1.19) y la cuadratura la
    hace `cuadrar_detalle_neto`, que ahora puede recibir varias líneas de
    descuento. En la práctica el cupón no llega a facturas (lo bloquea
    `venta_fideliza`), pero la conversión existe y tiene que cuadrar.
    """

    def test_factura_con_descuento_de_cabecera_cuadra_detalle_contra_neto(self):
        ticket = self._ticket_pendiente(correlativo=20, precio=10000, cantidad=2)
        ticket.descuento_cupon = 1000
        ticket.total = 19000
        ticket.estado = 'PAGADO'
        ticket.cliente_nombre = 'Comercial Test'
        ticket.save()

        dte = generar_dte_desde_ticket(
            ticket, 'FACTURA_ELECTRONICA', self.usuario)

        self.assertEqual(int(dte.monto_con_iva), 19000)
        # neto + iva reconstruyen el total del documento
        iva = int(dte.monto_con_iva) - int(dte.monto_neto)
        self.assertEqual(int(dte.monto_neto) + iva, 19000)
        contenido = (dte.archivo_txt_data or {}).get('contenido', '')
        self.assertIn('Descuento Cupon', contenido)


class TicketDeCambioTest(_BaseCuponPOS):
    """
    En un ticket de cambio todo el monto viaja en una línea sintética
    "DIFERENCIA DE CAMBIO" que YA vale `ticket.total`. Declarar además el cupón
    como descuento global lo restaría dos veces.
    """

    def test_el_cobro_rechaza_el_cupon_en_un_ticket_de_cambio(self):
        ticket = self._ticket_pendiente(correlativo=30)
        Ticket.objects.filter(pk=ticket.pk).update(
            modulo_origen='CAMBIO_DEVOLUCION')

        resp = self._cobrar(ticket, 18990, codigo_cupon=self.cupon.codigo)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json().get('error_tipo'), 'CUPON_NO_APLICA')
        self.cupon.refresh_from_db()
        self.assertEqual(self.cupon.estado, 'PENDIENTE')

    def test_el_txt_de_un_cambio_no_declara_descuento_de_cabecera(self):
        """Segunda barrera: aunque el campo venga poblado, el TXT lo ignora."""
        ticket = self._ticket_pendiente(correlativo=31)
        Ticket.objects.filter(pk=ticket.pk).update(
            modulo_origen='CAMBIO_DEVOLUCION')
        ticket.refresh_from_db()
        ticket.descuento_cupon = 1000
        ticket.total = 18990
        ticket.estado = 'PAGADO'
        ticket.save()

        dte = generar_dte_desde_ticket(
            ticket, 'BOLETA_ELECTRONICA', self.usuario)
        contenido = (dte.archivo_txt_data or {}).get('contenido', '')
        self.assertNotIn('Descuento Cupon', contenido)


class ComprobanteTermicoTest(_BaseCuponPOS):
    """
    El comprobante 80mm imprimía el TOTAL ya descontado sin ninguna línea que
    lo explicara: el cliente veía un total que no cuadraba con las líneas.
    """

    def test_construir_ticket_data_expone_los_descuentos_de_cabecera(self):
        ticket = self._ticket_pendiente()
        ticket.descuento_cupon = 1000
        ticket.descuento_fidelizacion = 500
        ticket.total = 18490
        ticket.save()

        data = construir_ticket_data(ticket)
        self.assertEqual(data['totales']['descuento_cupon'], 1000)
        self.assertEqual(data['totales']['descuento_fidelizacion'], 500)
        self.assertEqual(data['totales']['total'], 18490)

    def test_sin_beneficio_los_campos_van_en_cero(self):
        ticket = self._ticket_pendiente(correlativo=8)
        data = construir_ticket_data(ticket)
        self.assertEqual(data['totales']['descuento_cupon'], 0)
        self.assertEqual(data['totales']['descuento_fidelizacion'], 0)
