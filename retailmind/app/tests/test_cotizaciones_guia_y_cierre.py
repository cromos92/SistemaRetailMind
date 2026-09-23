"""
Cotizaciones: guía de despacho (DTE 52) previa a la factura y cierre de
despachos pendientes con motivo.

Correr en BD local desechable:
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_cotizaciones_guia_y_cierre
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from app.models import (
    Cotizacion_Empresa, Cotizacion_Empresa_Detalle, Cotizacion_Empresa_Detalle_SKU,
    Dte, Historial_Cotizacion, Movimientos_Producto, Producto_Talla, Ticket,
    TicketReferencia,
)

from .factories import setup_entorno_completo, crear_usuario, crear_empresa


def _permisos():
    return mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True)


class _Base(TestCase):

    def setUp(self):
        self.entorno = setup_entorno_completo()
        self.sucursal = self.entorno['sucursal']
        self.empresa = self.entorno['empresa']
        self.vendedor = self.entorno['vendedor']
        self.pt = self.entorno['producto_talla']  # stock 10
        self.admin = crear_usuario(username='admin_cot', rol='administrador')
        self.client = Client()
        self.client.force_login(self.admin)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

    def _post(self, nombre, data, client=None):
        with _permisos():
            return (client or self.client).post(
                reverse(nombre), data=json.dumps(data), content_type='application/json')

    def _dte(self, numero, tipo='FACTURA ELECTRONICA', **kw):
        hoy = timezone.localdate()
        base = dict(
            emisor=self.empresa, receptor=self.empresa, numero_documento=numero,
            tipo_documento=tipo, monto_con_iva=40000, monto_neto=33613, descuento=0,
            estado_pago='PAGADO', estado_dte='EMITIDO', responsable='t',
            fecha_emision=hoy, fecha_vencimiento=hoy, diasCredito=0, bultos=1,
            unidades_productos=2, tipo_transaccion='VENTA_PUBLICO', sucursal=self.sucursal,
            hora=timezone.localtime().time(),
        )
        base.update(kw)
        return Dte.objects.create(**base)


class CierrePendienteTest(_Base):

    def setUp(self):
        super().setUp()
        hoy = timezone.localdate()
        self.cot = Cotizacion_Empresa.objects.create(
            sucursal=self.sucursal, cliente=self.empresa, vendedor=self.vendedor,
            usuario_creador=self.admin, numero_cotizacion='COT-C-0001',
            fecha_emision=hoy, fecha_validez=hoy, total=30000,
        )
        self.item = Cotizacion_Empresa_Detalle.objects.create(
            cotizacion=self.cot, numero_linea=1, descripcion='Producto por llegar',
            cantidad=3, precio_unitario=10000, subtotal=30000,
            es_producto_pendiente=True, nombre_producto_pendiente='Producto por llegar',
        )
        self.factura = self._dte(501)
        self.cot.marcar_como_facturada('501', tiene_pendientes=True, dte=self.factura)

    def _cerrar(self, motivo='NOTA_CREDITO', cantidad=None, client=None):
        data = {'detalle_id': self.item.id, 'motivo': motivo,
                'detalle': 'El cliente pidió la devolución del dinero'}
        if cantidad:
            data['cantidad'] = cantidad
        return self._post('cerrar_pendiente_despacho', data, client)

    def test_nota_credito_exige_nc_emitida(self):
        r = self._cerrar('NOTA_CREDITO')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()['error_tipo'], 'FALTA_NC')
        self._dte(77, tipo='NOTA DE CREDITO', es_nota_credito=True, documento_afectado=self.factura)
        r = self._cerrar('NOTA_CREDITO')
        self.assertEqual(r.status_code, 200, r.content)
        self.cot.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.item.unidades_cerradas_sin_despacho, 3)
        self.assertEqual(self.cot.unidades_pendientes_despacho, 0)
        self.assertEqual(self.cot.estado_despacho, Cotizacion_Empresa.DESPACHO_COMPLETADO)
        self.assertTrue(Historial_Cotizacion.objects.filter(
            cotizacion=self.cot, accion='DESPACHO_CERRADO').exists())
        # No movió stock.
        self.assertFalse(Movimientos_Producto.objects.exists())

    def test_cierre_parcial_con_otro_motivo(self):
        r = self._cerrar('ENTREGADO_FUERA_SISTEMA', cantidad=1)
        self.assertEqual(r.status_code, 200, r.content)
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.unidades_pendientes_despacho, 2)
        self.assertEqual(self.cot.estado_despacho, Cotizacion_Empresa.DESPACHO_PARCIAL)
        r = self._cerrar('OTRO', cantidad=5)
        self.assertEqual(r.status_code, 400)

    def test_solo_administrador(self):
        vendedor = crear_usuario(username='vend_cot', rol='vendedor')
        c = Client(); c.force_login(vendedor)
        s = c.session; s['idSucursalActual'] = self.sucursal.id; s.save()
        r = self._cerrar('OTRO', client=c)
        self.assertEqual(r.status_code, 403)

    def test_listado_marca_despacho_atrasado(self):
        Cotizacion_Empresa.objects.filter(pk=self.cot.pk).update(
            fecha_facturacion=timezone.now() - timedelta(days=10))
        with _permisos():
            r = self.client.get(reverse('listar_cotizaciones'), {'estado': 'DESPACHO_ATRASADO'})
        data = r.json()
        self.assertEqual([c['numero_cotizacion'] for c in data['cotizaciones']], ['COT-C-0001'])
        fila = data['cotizaciones'][0]
        self.assertTrue(fila['despacho_atrasado'])
        self.assertEqual(fila['dias_desde_factura'], 10)
        self.assertTrue(fila['puede_cerrar_pendiente'])
        self.assertEqual(data['estadisticas']['despacho_atrasado'], 1)


class GuiaDespachoTest(_Base):

    def setUp(self):
        super().setUp()
        hoy = timezone.localdate()
        # Cliente distinto del emisor: si fueran la misma empresa la guía sería
        # un traslado interno (IndTraslado 5), no una venta.
        self.cliente = crear_empresa(nombre='Club Deportivo Test', rut='65.043.946-5')
        self.cot = Cotizacion_Empresa.objects.create(
            sucursal=self.sucursal, cliente=self.cliente, vendedor=self.vendedor,
            usuario_creador=self.admin, numero_cotizacion='COT-G-0001',
            fecha_emision=hoy, fecha_validez=hoy + timedelta(days=10), total=40000,
        )
        self.item = Cotizacion_Empresa_Detalle.objects.create(
            cotizacion=self.cot, numero_linea=1, descripcion='Zapatilla Test',
            cantidad=2, precio_unitario=20000, subtotal=40000,
        )
        Cotizacion_Empresa_Detalle_SKU.objects.create(
            detalle=self.item, producto_talla=self.pt, cantidad=2,
            costo_unitario=15000, precio_unitario=20000,
        )

    def _emitir(self):
        return self._post('emitir_guia_cotizacion', {'cotizacion_id': self.cot.id})

    def _stock(self):
        return Producto_Talla.objects.get(pk=self.pt.pk).stock

    def test_emitir_guia_saca_stock_y_la_factura_no_descuenta_de_nuevo(self):
        r = self._emitir()
        self.assertEqual(r.status_code, 200, r.content)
        guia = Dte.objects.get(pk=r.json()['dte_id'])
        self.assertEqual(guia.tipo_documento, 'GUIA')
        self.assertEqual(guia.tipo_transaccion, 'VENTA')
        self.assertEqual(guia.unidades_productos, 2)
        self.assertEqual(self._stock(), 8)
        mov = Movimientos_Producto.objects.get(dte=guia)
        self.assertEqual(mov.cantidad, -2)
        self.assertEqual(mov.concepto, 'DESPACHO_COTIZACION')
        self.assertEqual(mov.tipo_movimiento, 'EGRESO')
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.guia_despacho_id, guia.id)

        # Segunda emisión: rechazada.
        self.assertEqual(self._emitir().status_code, 400)
        # Editar / anular la cotización: bloqueado.
        r = self._post('anular_cotizacion', {'cotizacion_id': self.cot.id, 'motivo': 'x'})
        self.assertFalse(r.json()['success'])

        # Facturar en el POS: sin nuevo descuento de stock y con referencia 52.
        payload = {
            'cotizacion_id': self.cot.id,
            'cliente': {'rut': self.cliente.rut, 'nombre': self.cliente.nombre, 'giro': self.cliente.giro},
            'estado': 'PAGADO',
            'tipo_documento': '',
            'productos': [{
                'sku': self.pt.sku, 'producto_talla_id': self.pt.id, 'cantidad': 2,
                'precio_unitario': 20000, 'descuento_unitario': 0, 'subtotal': 40000,
                'cotizacion_item_id': self.item.id,
            }],
            'pagos': [{'metodo_pago': 'EFECTIVO', 'monto': 40000}],
        }
        with _permisos():
            r = self.client.post(reverse('registrar_pagos_ticket', args=['COT-G-0001']),
                                 data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        ticket = Ticket.objects.get(sucursal=self.sucursal)
        self.assertEqual(ticket.estado, 'PAGADO')
        self.assertTrue(ticket.ticket_productos.get().despachado_por_guia)
        self.assertEqual(self._stock(), 8)  # la guía ya lo sacó
        self.assertEqual(Movimientos_Producto.objects.filter(cantidad__lt=0).count(), 1)
        ref = TicketReferencia.objects.get(ticket=ticket)
        self.assertEqual((ref.tipo_documento, ref.folio), ('52', str(guia.numero_documento)))

    def test_factura_electronica_referencia_la_guia(self):
        guia = Dte.objects.get(pk=self._emitir().json()['dte_id'])
        payload = {
            'cotizacion_id': self.cot.id,
            'cliente': {'rut': self.cliente.rut, 'nombre': self.cliente.nombre, 'giro': self.cliente.giro,
                        'direccion': self.cliente.direccion, 'comuna': self.cliente.comuna,
                        'ciudad': self.cliente.ciudad},
            'estado': 'PAGADO',
            'tipo_documento': 'FACTURA_ELECTRONICA',
            'productos': [{
                'sku': self.pt.sku, 'producto_talla_id': self.pt.id, 'cantidad': 2,
                'precio_unitario': 20000, 'descuento_unitario': 0, 'subtotal': 40000,
                'cotizacion_item_id': self.item.id,
            }],
            'pagos': [{'metodo_pago': 'EFECTIVO', 'monto': 40000}],
        }
        with _permisos():
            r = self.client.post(reverse('registrar_pagos_ticket', args=['COT-G-0001']),
                                 data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.cot.refresh_from_db()
        self.assertTrue(self.cot.facturada, r.json())
        factura = self.cot.dte
        self.assertEqual(factura.tipo_documento, 'FACTURA ELECTRONICA')
        self.assertEqual((factura.referencia_tipo, factura.referencia_folio), ('52', str(guia.numero_documento)))
        self.assertEqual(self._stock(), 8)
        # Facturada: la guía ya no se puede anular por esta vía.
        r = self._post('anular_guia_cotizacion', {'cotizacion_id': self.cot.id, 'motivo': 'probando'})
        self.assertEqual(r.status_code, 400)

    def test_txt_acepta_de_la_guia(self):
        from app.views_modulo_documentos import construir_datos_txt_desde_dte, generar_txt_dte_acepta
        guia = Dte.objects.get(pk=self._emitir().json()['dte_id'])
        datos = construir_datos_txt_desde_dte(guia)
        self.assertEqual(int(datos['documento']['tipo_documento']), 52)
        self.assertEqual(int(datos['documento']['ind_traslado']), 1)
        txt = generar_txt_dte_acepta(datos)
        primera = txt.splitlines()[0].split('|')
        self.assertEqual(primera[0], '52')
        self.assertEqual(primera[1], str(guia.numero_documento))

    def test_anular_guia_reingresa_stock(self):
        r = self._emitir()
        guia_id = r.json()['dte_id']
        self.assertEqual(self._stock(), 8)
        r = self._post('anular_guia_cotizacion', {'cotizacion_id': self.cot.id,
                                                  'motivo': 'Cliente cambió el pedido'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self._stock(), 10)
        self.assertEqual(Dte.objects.get(pk=guia_id).estado_dte, 'ANULADO')
        self.cot.refresh_from_db()
        self.assertIsNone(self.cot.guia_despacho_id)
        self.assertTrue(Historial_Cotizacion.objects.filter(cotizacion=self.cot, accion='GUIA_ANULADA').exists())
        # Se puede volver a emitir.
        self.assertEqual(self._emitir().status_code, 200)

    def test_guia_sin_stock_no_emite_nada(self):
        Producto_Talla.objects.filter(pk=self.pt.pk).update(stock=1)
        r = self._emitir()
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()['error_tipo'], 'STOCK_INSUFICIENTE')
        self.assertFalse(Dte.objects.filter(tipo_documento='GUIA').exists())
        self.assertEqual(self._stock(), 1)

    def test_guia_exige_datos_del_cliente(self):
        self.cliente.giro = ''
        self.cliente.save(update_fields=['giro'])
        r = self._emitir()
        self.assertEqual(r.status_code, 400)
        self.assertIn('giro del cliente', r.json()['error'])
