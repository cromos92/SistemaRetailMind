"""
Tests del descuento a la diferencia de un ticket de CAMBIO en el POS,
autorizado con el PIN de un administrador.

Contexto: el POS bloqueaba TODO descuento sobre un ticket de cambio (botón
"Dcto. Global" deshabilitado y `mostrarModalDescuento` cortado), así que cuando
el cambio dejaba una diferencia a cobrar no había forma de rebajarla en la caja
— sólo un administrador logueado, desde el módulo de Cambios y Devoluciones.

Lo que se prueba acá:

1. **El descuento llega a la BD y al DTE.** No basta con bajar `ticket.total`:
   `generar_dte_desde_ticket` recalcula el total AUTORITATIVO desde la suma de
   las líneas y reescribe `ticket.total` si no cuadran. Por eso la rebaja se
   materializa como una línea manual negativa; sin ella, la boleta saldría por
   el monto original y el cliente pagaría uno distinto al del documento.

2. **El PIN es la autorización real.** Sin PIN válido no se toca el ticket, y
   cinco intentos fallidos bloquean al cajero por 15 minutos.

3. **El cambio asociado queda sincronizado.** Si `CambioDevolucion.diferencia_monto`
   no baja junto al ticket, el módulo de cambios muestra un monto por cobrar que
   ya no existe.

Nota: el endpoint vive bajo `/app/ventas/api/`, que `PermisosMenuMiddleware` no
intercepta (su mapa no tiene esa clave), así que basta estar autenticado y con
sucursal en sesión.
"""
import json
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    CambioDevolucion, HistorialCambioDevolucion, RegistroAutorizacion,
    Ticket, Ticket_Productos,
)
from app.views_modulo_ventas import generar_dte_desde_ticket

from .factories import (
    crear_correlativo, crear_empresa, crear_producto_con_talla, crear_sucursal,
    crear_usuario, crear_vendedor,
)

PIN_ADMIN = '482913'


class DescuentoDiferenciaCambioTests(TestCase):
    """El cajero rebaja la diferencia de un cambio con el PIN de un admin."""

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa)
        self.vendedor = crear_vendedor(empresa=self.empresa)

        self.cajero = crear_usuario(username='cajera', rol='cajero')
        self.admin = crear_usuario(username='jefa', rol='administrador')
        self.admin.set_pin_autorizacion(PIN_ADMIN)

        self.producto, self.talla = crear_producto_con_talla(self.sucursal)

        self.client = Client()
        self.client.force_login(self.cajero)
        sesion = self.client.session
        sesion['idSucursalActual'] = self.sucursal.id
        sesion.save()

        self.url = reverse('aplicar_descuento_diferencia_cambio')

    # ------------------------------------------------------------------ utils

    def _crear_ticket_cambio(self, diferencia=20000, modulo='CAMBIO_DEVOLUCION',
                             estado='PENDIENTE'):
        """
        Ticket de cambio tal como lo deja el módulo: una línea positiva (producto
        que se lleva) y una negativa (el que devuelve). `total` = la diferencia.
        """
        ticket = Ticket.objects.create(
            correlativo=5001,
            sucursal=self.sucursal,
            vendedor=self.vendedor,
            subTotal=diferencia,
            descuento=0,
            total=diferencia,
            estado=estado,
            responsable=self.cajero.username,
            modulo_origen=modulo,
            cliente_nombre='Cliente Test',
            cliente_rut='11.111.111-1',
        )
        Ticket_Productos.objects.create(
            idTicket=ticket, ProductoTalla=self.talla, stock=1,
            precio=50000, precio_original=50000, descuento_unitario=0,
            subtotal=50000,
        )
        Ticket_Productos.objects.create(
            idTicket=ticket, ProductoTalla=self.talla, stock=1,
            precio=-(50000 - diferencia), precio_original=-(50000 - diferencia),
            descuento_unitario=0, subtotal=-(50000 - diferencia),
        )
        return ticket

    def _crear_cambio(self, ticket_diferencia, diferencia=20000):
        original = Ticket.objects.create(
            correlativo=4001, sucursal=self.sucursal, vendedor=self.vendedor,
            subTotal=30000, descuento=0, total=30000, estado='PAGADO',
            responsable=self.cajero.username, modulo_origen='VENTA_PUBLICO',
        )
        return CambioDevolucion.objects.create(
            ticket_original=original,
            ticket_diferencia=ticket_diferencia,
            sucursal=self.sucursal,
            numero_operacion='CD-TEST-0001',
            tipo_operacion='CAMBIO_PRODUCTO',
            estado='EJECUTADO_COBRO_PENDIENTE',
            fecha_limite_cambio=timezone.localdate() + timedelta(days=30),
            monto_original=30000,
            monto_nuevo=50000,
            diferencia_monto=diferencia,
            solicitado_por=self.cajero,
            motivo_principal='CAMBIO_TALLA',
        )

    def _post(self, **payload):
        body = {
            'correlativo': 5001,
            'tipo': 'PORCENTAJE',
            'valor': 10,
            'motivo': 'Acuerdo con el cliente por la demora',
            'pin': PIN_ADMIN,
        }
        body.update(payload)
        return self.client.post(
            self.url, data=json.dumps(body), content_type='application/json'
        )

    # ------------------------------------------------------------- happy path

    def test_descuento_porcentaje_rebaja_el_ticket(self):
        ticket = self._crear_ticket_cambio(diferencia=20000)

        resp = self._post(tipo='PORCENTAJE', valor=10)
        data = resp.json()

        self.assertEqual(resp.status_code, 200, data)
        self.assertTrue(data['success'])
        self.assertEqual(data['descuento_aplicado'], 2000)
        self.assertEqual(data['nuevo_total'], 18000)

        ticket.refresh_from_db()
        self.assertEqual(ticket.total, 18000)

        # La suma de las líneas tiene que seguir cuadrando con el total: es lo
        # que mira la emisión del DTE.
        suma_lineas = sum(tp.precio * tp.stock for tp in ticket.ticket_productos.all())
        self.assertEqual(suma_lineas, 18000)

    def test_descuento_por_monto(self):
        ticket = self._crear_ticket_cambio(diferencia=20000)
        resp = self._post(tipo='MONTO', valor=3500)
        self.assertTrue(resp.json()['success'], resp.json())
        ticket.refresh_from_db()
        self.assertEqual(ticket.total, 16500)

    def test_descuento_por_precio_final(self):
        ticket = self._crear_ticket_cambio(diferencia=20000)
        resp = self._post(tipo='PRECIO_FINAL', valor=15000)
        data = resp.json()
        self.assertTrue(data['success'], data)
        self.assertEqual(data['descuento_aplicado'], 5000)
        ticket.refresh_from_db()
        self.assertEqual(ticket.total, 15000)

    def test_crea_linea_negativa_de_respaldo(self):
        ticket = self._crear_ticket_cambio(diferencia=20000)
        self._post(tipo='MONTO', valor=5000)

        linea = ticket.ticket_productos.filter(ProductoTalla__isnull=True).get()
        self.assertEqual(linea.precio, -5000)
        self.assertEqual(linea.subtotal, -5000)
        self.assertEqual(linea.stock, 1)
        self.assertIn('DESCUENTO DIFERENCIA DE CAMBIO', linea.descripcion_linea)
        self.assertIn(self.admin.username, linea.descripcion_linea)

    def test_dte_se_emite_por_el_total_ya_descontado(self):
        """
        El corazón del asunto: `generar_dte_desde_ticket` recalcula el total
        desde las líneas. Si la rebaja no estuviera materializada como línea,
        acá el total volvería a $20.000 y la boleta saldría por más de lo cobrado.
        """
        ticket = self._crear_ticket_cambio(diferencia=20000)
        crear_correlativo(self.sucursal, tipo_dte='BOLETA ELECTRONICA')

        self._post(tipo='MONTO', valor=4000)
        ticket.refresh_from_db()

        dte = generar_dte_desde_ticket(ticket, 'BOLETA_ELECTRONICA', self.cajero)

        self.assertEqual(int(dte.monto_con_iva), 16000)
        ticket.refresh_from_db()
        self.assertEqual(ticket.total, 16000)

    def test_sincroniza_el_cambio_asociado(self):
        ticket = self._crear_ticket_cambio(diferencia=20000)
        cambio = self._crear_cambio(ticket, diferencia=20000)

        self._post(tipo='MONTO', valor=6000)

        cambio.refresh_from_db()
        self.assertEqual(int(cambio.diferencia_monto), 14000)
        self.assertEqual(int(cambio.monto_diferencia_original), 20000)
        self.assertTrue(cambio.diferencia_ajustada)
        self.assertEqual(cambio.ajustada_por_id, self.admin.id)
        # El cobro NO se cierra: sigue pendiente en el POS por el monto nuevo.
        self.assertEqual(cambio.estado, 'EJECUTADO_COBRO_PENDIENTE')

        historial = HistorialCambioDevolucion.objects.filter(
            cambio_devolucion=cambio, accion='AJUSTE_DIFERENCIA'
        ).get()
        self.assertEqual(historial.usuario_id, self.cajero.id)
        self.assertEqual(historial.datos_adicionales['origen'], 'POS_DESCUENTO_PIN')

    def test_registra_la_autorizacion(self):
        self._crear_ticket_cambio(diferencia=20000)
        self._post(tipo='PORCENTAJE', valor=10)

        registro = RegistroAutorizacion.objects.get(tipo_operacion='DESCUENTO_ESPECIAL')
        self.assertTrue(registro.exitoso)
        self.assertEqual(registro.usuario_solicitante_id, self.cajero.id)
        self.assertEqual(registro.usuario_autorizador_id, self.admin.id)
        self.assertNotIn(PIN_ADMIN, json.dumps(registro.datos_adicionales))

    def test_descuentos_sucesivos_se_acumulan(self):
        ticket = self._crear_ticket_cambio(diferencia=20000)
        self._post(tipo='MONTO', valor=5000)
        self._post(tipo='MONTO', valor=3000)

        ticket.refresh_from_db()
        self.assertEqual(ticket.total, 12000)
        self.assertEqual(ticket.ticket_productos.filter(ProductoTalla__isnull=True).count(), 2)

    # ------------------------------------------------------------------ guards

    def test_pin_incorrecto_no_toca_el_ticket(self):
        ticket = self._crear_ticket_cambio(diferencia=20000)

        resp = self._post(pin='000000')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['error_tipo'], 'PIN_INVALIDO')

        ticket.refresh_from_db()
        self.assertEqual(ticket.total, 20000)
        self.assertEqual(ticket.ticket_productos.filter(ProductoTalla__isnull=True).count(), 0)

        registro = RegistroAutorizacion.objects.get(tipo_operacion='DESCUENTO_ESPECIAL')
        self.assertFalse(registro.exitoso)

    def test_pin_de_usuario_sin_rol_admin_no_sirve(self):
        """Un PIN sólo autoriza si su dueño es administrador/jefe de local."""
        ticket = self._crear_ticket_cambio(diferencia=20000)
        # `set_pin_autorizacion` rechaza roles sin permiso; se fuerza el hash a
        # mano para probar que el lookup igual descarta al vendedor.
        from django.contrib.auth.hashers import make_password
        vendedor_user = crear_usuario(username='vendedorx', rol='vendedor')
        vendedor_user.pin_autorizacion = make_password('654321')
        vendedor_user.save(update_fields=['pin_autorizacion'])

        resp = self._post(pin='654321')
        self.assertEqual(resp.status_code, 403)
        ticket.refresh_from_db()
        self.assertEqual(ticket.total, 20000)

    def test_cinco_intentos_fallidos_bloquean(self):
        self._crear_ticket_cambio(diferencia=20000)
        for _ in range(5):
            self.assertEqual(self._post(pin='000000').status_code, 403)

        resp = self._post(pin=PIN_ADMIN)  # PIN bueno, pero ya bloqueado
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()['error_tipo'], 'PIN_BLOQUEADO')

    def test_rechaza_ticket_de_venta_normal(self):
        ticket = self._crear_ticket_cambio(diferencia=20000, modulo='VENTA_PUBLICO')
        resp = self._post()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error_tipo'], 'NO_ES_CAMBIO')
        ticket.refresh_from_db()
        self.assertEqual(ticket.total, 20000)

    def test_rechaza_ticket_ya_pagado(self):
        self._crear_ticket_cambio(diferencia=20000, estado='PAGADO')
        resp = self._post()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error_tipo'], 'TICKET_NO_PENDIENTE')

    def test_rechaza_descuento_que_deja_la_diferencia_en_cero(self):
        """Perdonar el 100% es "Condonar", que además cierra el cambio."""
        ticket = self._crear_ticket_cambio(diferencia=20000)
        resp = self._post(tipo='PORCENTAJE', valor=100)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error_tipo'], 'DESCUENTO_TOTAL')
        ticket.refresh_from_db()
        self.assertEqual(ticket.total, 20000)

    def test_rechaza_sin_justificacion(self):
        self._crear_ticket_cambio(diferencia=20000)
        resp = self._post(motivo='ok')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('justificación', resp.json()['error'])

    def test_rechaza_pin_de_formato_invalido(self):
        self._crear_ticket_cambio(diferencia=20000)
        resp = self._post(pin='123')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error_tipo'], 'PIN_FORMATO')

    def test_rechaza_ticket_de_otra_sucursal(self):
        self._crear_ticket_cambio(diferencia=20000)
        otra = crear_sucursal(self.empresa, alias='SUC-OTRA')
        sesion = self.client.session
        sesion['idSucursalActual'] = otra.id
        sesion.save()

        resp = self._post()
        self.assertEqual(resp.status_code, 404)

    def test_exige_sesion_con_sucursal(self):
        self._crear_ticket_cambio(diferencia=20000)
        sesion = self.client.session
        del sesion['idSucursalActual']
        sesion.save()

        resp = self._post()
        self.assertEqual(resp.status_code, 400)
        self.assertIn('sucursal', resp.json()['error'])

    def test_exige_autenticacion(self):
        self._crear_ticket_cambio(diferencia=20000)
        anonimo = Client()
        resp = anonimo.post(
            self.url,
            data=json.dumps({'correlativo': 5001, 'tipo': 'MONTO', 'valor': 100,
                             'motivo': 'prueba anonima', 'pin': PIN_ADMIN}),
            content_type='application/json',
        )
        self.assertIn(resp.status_code, (302, 403))
        self.assertEqual(Ticket.objects.get(correlativo=5001).total, 20000)
