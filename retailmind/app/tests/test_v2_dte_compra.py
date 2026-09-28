"""
Unidad V2 — alta/edición de documentos de COMPRA y comprobante (views.py).

Cubre:
- B3-04: fecha_vencimiento = emisión + diasCredito al crear y al editar.
- B3-11 / B14-10: duplicado por (RUT proveedor, tipo, folio) sin fecha; una
  factura y una NC pueden compartir folio.
- B3-12: responsable = usuario (antes siempre 'Sistema').
- B14-08: es_por_concepto solo si se marca.
- B3-08 / B5-11: editar con pagos no baja el monto bajo lo pagado ni cambia el
  proveedor; estado_pago no se copia del formulario; documento_padre solo se
  toca si viene en el payload.
- CC-11: asociar_factura_cotizacion solo documentos de COMPRA del mismo
  proveedor/empresa y sin pisar otro documento base.
- B16-03: restaurar_dte (sin UI) se retiró: la ruta da 404 y no des-descarta.
- B3-14 / B5-09: el comprobante no presenta compensaciones como cheque.

Ejecutar (BD de test aislada):
    python manage.py test app.tests.test_v2_dte_compra --keepdb
"""
import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from app.models import Dte, Dte_Detalle_Pago
from .factories import crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario, otorgar_ver_pantalla


class _BaseDteCompra(TestCase):
    ROL = 'administracion'

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa(nombre='Nosotros V2b', rut='76.210.000-1')
        cls.otra_empresa = crear_empresa(nombre='Otra V2b', rut='76.310.000-1')
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='V2B-SUC')
        cls.prov_a = crear_empresa(nombre='Prov A V2b', rut='77.110.000-1', esProveedor=True)
        cls.prov_a_dup = crear_empresa(nombre='Prov A (ficha 2)', rut='77110000-1', esProveedor=True)
        cls.prov_b = crear_empresa(nombre='Prov B V2b', rut='77.210.000-1', esProveedor=True)
        cls.user = crear_usuario(username='v2b_admin', rol=cls.ROL, first_name='Beatriz', last_name='Compras')
        crear_empresa_user(cls.user, cls.empresa, cls.sucursal)
        otorgar_ver_pantalla(cls.ROL, 'gestion_dte_compras', puede_crear=True, puede_editar=True, puede_eliminar=True)
        otorgar_ver_pantalla(cls.ROL, 'dte_compras_pagos', puede_editar=True, puede_eliminar=True)
        otorgar_ver_pantalla(cls.ROL, 'dte_compras_eliminar', puede_eliminar=True)

    def setUp(self):
        self.client.force_login(self.user)
        s = self.client.session
        s['idEmpresaActual'] = self.empresa.id
        s['idSucursalActual'] = self.sucursal.id
        s.save()
        self.hoy = timezone.localdate()

    def _dte(self, numero, emisor, tipo='FACTURA ELECTRONICA', monto=100000, receptor=None,
             tipo_transaccion='COMPRA', estado_pago='Pendiente', dias=0, **extra):
        return Dte.objects.create(
            emisor=emisor, receptor=receptor or self.empresa, numero_documento=numero,
            tipo_documento=tipo, monto_con_iva=monto, monto_neto=round(monto / 1.19),
            descuento=0, estado_pago=estado_pago, estado_dte='ACEPTADO', responsable='test',
            fecha_emision=self.hoy, fecha_vencimiento=self.hoy + timedelta(days=dias),
            diasCredito=dias, bultos=0, unidades_productos=0,
            tipo_transaccion=tipo_transaccion, sucursal=self.sucursal, **extra,
        )

    def _payload(self, **kw):
        base = {
            'receptor_id': self.prov_a.id, 'numero_documento': 1234, 'monto_con_iva': 119000,
            'tipo_documento': 'FACTURA ELECTRONICA', 'fecha_emision': self.hoy.isoformat(),
            'fecha_recepcion': self.hoy.isoformat(), 'estado_dte': 'ACEPTADO',
            'estado_pago': 'Pendiente', 'diasCredito': 30, 'bultos': 1, 'unidades_productos': 10,
            'descuento': 0, 'empresa_receptora_id': self.empresa.id,
            'sucursal_receptora_id': self.sucursal.id,
        }
        base.update(kw)
        return base

    def _json(self, method, url, data=None):
        return getattr(self.client, method)(
            url, data=json.dumps(data or {}), content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )


class CrearDteCompraTest(_BaseDteCompra):
    def test_crea_con_vencimiento_responsable_y_estado_canonico(self):
        r = self._json('post', '/app/crearDteCompras/', self._payload(numero_documento=1001))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['success'], r.content)
        d = Dte.objects.get(id=r.json()['id'])
        self.assertEqual(d.fecha_vencimiento, self.hoy + timedelta(days=30))       # B3-04
        self.assertEqual(d.responsable, 'Beatriz Compras')                          # B3-12
        self.assertEqual(d.estado_pago, 'PENDIENTE')                                # B3-02
        self.assertFalse(d.es_por_concepto)                                         # B14-08

    def test_por_concepto_solo_si_se_marca(self):
        r = self._json('post', '/app/crearDteCompras/', self._payload(numero_documento=1002, por_concepto=True))
        self.assertTrue(r.json()['success'], r.content)
        self.assertTrue(Dte.objects.get(id=r.json()['id']).es_por_concepto)

    def test_duplicado_por_rut_tipo_folio_sin_fecha(self):
        self._dte(775124, self.prov_a, tipo='NOTA DE CREDITO', monto=958977)
        otra_fecha = (self.hoy - timedelta(days=60)).isoformat()
        # Misma NC con otra fecha y desde la ficha duplicada del proveedor (mismo RUT)
        r = self._json('post', '/app/crearDteCompras/', self._payload(
            receptor_id=self.prov_a_dup.id, numero_documento=775124, tipo_documento='NOTA DE CREDITO',
            fecha_emision=otra_fecha, monto_con_iva=958977,
        ))
        self.assertFalse(r.json()['success'], r.content)
        self.assertIn('Ya existe NOTA DE CREDITO N° 775124', r.json()['error'])
        # Una FACTURA con el mismo folio sí se puede (otro tipo SII)
        r = self._json('post', '/app/crearDteCompras/', self._payload(numero_documento=775124))
        self.assertTrue(r.json()['success'], r.content)
        # El mismo folio de otro proveedor también
        r = self._json('post', '/app/crearDteCompras/', self._payload(
            receptor_id=self.prov_b.id, numero_documento=775124, tipo_documento='NOTA DE CREDITO'))
        self.assertTrue(r.json()['success'], r.content)

    def test_documento_base_de_otro_proveedor_rechazado(self):
        guia_b = self._dte(52001, self.prov_b, tipo='GUIA', monto=10000)
        r = self._json('post', '/app/crearDteCompras/', self._payload(
            numero_documento=1003, documento_padre_id=guia_b.id))
        self.assertFalse(r.json()['success'], r.content)
        self.assertIn('otro proveedor', r.json()['error'])


class ActualizarDteCompraTest(_BaseDteCompra):
    def test_no_baja_monto_bajo_lo_pagado_ni_cambia_proveedor(self):
        factura = self._dte(2001, self.prov_a, monto=284743, estado_pago='PAGADO', dias=30)
        Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='Cheque', voucher='C9',
                                        monto=284743, fecha_pago=self.hoy)
        r = self._json('put', f'/app/actualizarDteCompras/{factura.id}/', self._payload(
            numero_documento=2001, monto_con_iva=1000))
        self.assertFalse(r.json()['success'], r.content)
        self.assertIn('bajo lo ya pagado', r.json()['error'])
        r = self._json('put', f'/app/actualizarDteCompras/{factura.id}/', self._payload(
            receptor_id=self.prov_b.id, numero_documento=2001, monto_con_iva=284743))
        self.assertFalse(r.json()['success'], r.content)
        factura.refresh_from_db()
        self.assertEqual((factura.emisor_id, int(factura.monto_con_iva)), (self.prov_a.id, 284743))

    def test_subir_monto_con_pagos_recalcula_estado(self):
        factura = self._dte(2002, self.prov_a, monto=284743, estado_pago='PAGADO', dias=30)
        Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='Cheque', voucher='C8',
                                        monto=284743, fecha_pago=self.hoy)
        # El formulario reenvía el estado viejo; no debe mandar.
        r = self._json('put', f'/app/actualizarDteCompras/{factura.id}/', self._payload(
            numero_documento=2002, monto_con_iva=334743, estado_pago='Pagado', diasCredito=45))
        self.assertTrue(r.json()['success'], r.content)
        factura.refresh_from_db()
        self.assertEqual(factura.estado_pago, 'PARCIAL')
        self.assertEqual(factura.fecha_vencimiento, self.hoy + timedelta(days=45))   # B3-04

    def test_estado_pago_del_formulario_no_pisa_la_bd(self):
        factura = self._dte(2003, self.prov_a, monto=10000, estado_pago='PENDIENTE')
        r = self._json('put', f'/app/actualizarDteCompras/{factura.id}/', self._payload(
            numero_documento=2003, monto_con_iva=10000, estado_pago='PAGADO'))
        self.assertTrue(r.json()['success'], r.content)
        factura.refresh_from_db()
        self.assertEqual(factura.estado_pago, 'PENDIENTE')

    def test_documento_padre_se_conserva_si_no_viene(self):
        guia = self._dte(52002, self.prov_a, tipo='GUIA', monto=10000)
        factura = self._dte(2004, self.prov_a, monto=10000, documento_padre=guia)
        payload = self._payload(numero_documento=2004, monto_con_iva=10000)
        r = self._json('put', f'/app/actualizarDteCompras/{factura.id}/', payload)
        self.assertTrue(r.json()['success'], r.content)
        factura.refresh_from_db()
        self.assertEqual(factura.documento_padre_id, guia.id)
        # Si el formulario lo manda vacío explícitamente, se quita.
        r = self._json('put', f'/app/actualizarDteCompras/{factura.id}/', dict(payload, documento_padre_id=None))
        self.assertTrue(r.json()['success'], r.content)
        factura.refresh_from_db()
        self.assertIsNone(factura.documento_padre_id)

    def test_editar_duplicado_historico_sin_cambiar_identidad(self):
        self._dte(3001, self.prov_a, tipo='NOTA DE CREDITO', monto=500)
        copia = self._dte(3001, self.prov_a, tipo='NOTA DE CREDITO', monto=500)
        r = self._json('put', f'/app/actualizarDteCompras/{copia.id}/', self._payload(
            numero_documento=3001, tipo_documento='NOTA DE CREDITO', monto_con_iva=500, bultos=3))
        self.assertTrue(r.json()['success'], r.content)
        # Pero convertir otra factura en esa NC sí se bloquea
        otra = self._dte(3002, self.prov_a, tipo='NOTA DE CREDITO', monto=500)
        r = self._json('put', f'/app/actualizarDteCompras/{otra.id}/', self._payload(
            numero_documento=3001, tipo_documento='NOTA DE CREDITO', monto_con_iva=500))
        self.assertFalse(r.json()['success'], r.content)

    def test_no_edita_documento_de_otra_empresa(self):
        ajeno = self._dte(2005, self.prov_a, monto=10000, receptor=self.otra_empresa)
        r = self._json('put', f'/app/actualizarDteCompras/{ajeno.id}/', self._payload(numero_documento=2005))
        self.assertFalse(r.json()['success'])
        ajeno.refresh_from_db()
        self.assertEqual(ajeno.bultos, 0)


class AsociarFacturaCotizacionTest(_BaseDteCompra):
    def test_solo_compra_mismo_proveedor_y_sin_pisar(self):
        guia = self._dte(52010, self.prov_a, tipo='GUIA', monto=10000)
        guia_b = self._dte(52011, self.prov_b, tipo='GUIA', monto=10000)
        factura = self._dte(4001, self.prov_a, monto=10000)
        traspaso = self._dte(4002, self.prov_a, monto=10000, tipo_transaccion='TRASPASO')

        r = self._json('post', '/app/asociar_factura_cotizacion/', {'factura_id': traspaso.id, 'documento_base_id': guia.id})
        self.assertEqual(r.status_code, 404, r.content)
        # Rechazo por regla de negocio: 200 {success: false, error} (el JS
        # muestra resp.error solo en la rama success).
        r = self._json('post', '/app/asociar_factura_cotizacion/', {'factura_id': factura.id, 'documento_base_id': guia_b.id})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()['success'])
        self.assertIn('otro proveedor', r.json()['error'])
        r = self._json('post', '/app/asociar_factura_cotizacion/', {'factura_id': factura.id, 'documento_base_id': guia.id})
        self.assertEqual(r.status_code, 200, r.content)
        factura.refresh_from_db()
        self.assertEqual(factura.documento_padre_id, guia.id)

        otra_guia = self._dte(52012, self.prov_a, tipo='GUIA', monto=10000)
        r = self._json('post', '/app/asociar_factura_cotizacion/', {'factura_id': factura.id, 'documento_base_id': otra_guia.id})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()['success'])
        self.assertIn('otro documento base', r.json()['error'])
        factura.refresh_from_db()
        self.assertEqual(factura.documento_padre_id, guia.id)

        r = self._json('post', f'/app/desasociar_factura_cotizacion/{factura.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        factura.refresh_from_db()
        self.assertIsNone(factura.documento_padre_id)


class RestaurarDteTest(_BaseDteCompra):
    """B16-03: el endpoint sin UI se retiró (unidad D, 2026-09-26)."""

    def test_la_ruta_de_restaurar_ya_no_existe_y_no_des_descarta(self):
        compra = self._dte(5001, self.prov_a, descartado=True, descartado_por='Patricia', motivo_descarte='error')
        venta = self._dte(5002, self.empresa, tipo_transaccion='VENTA_PUBLICO', descartado=True,
                          descartado_por='Javier', motivo_descarte='anulada')
        for dte in (compra, venta):
            r = self._json('post', f'/app/restaurarDTE/{dte.id}/')
            self.assertEqual(r.status_code, 404, r.content)
            dte.refresh_from_db()
            self.assertTrue(dte.descartado)
        self.assertEqual((compra.descartado_por, compra.motivo_descarte), ('Patricia', 'error'))


class ComprobanteCompensacionTest(_BaseDteCompra):
    def test_compensacion_no_sale_como_cheque(self):
        factura = self._dte(280, self.prov_a, monto=415072)
        Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='Compensación con Factura', voucher='256',
                                        monto=100000, fecha_pago=self.hoy)
        r = self.client.get(f'/app/datos_envio_comprobante/{factura.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        prev = r.json()['preview']
        fila = prev['filas'][0]
        self.assertEqual(fila['cheque'], '-')
        self.assertIn('Comp. Fact. N°256', fila['nc_numeros'])
        self.assertEqual(prev['totales']['pago'], '$315.072')   # saldo real, no la compensación
        self.assertTrue(prev['hay_compensaciones'])

        r = self.client.get(f'/app/comprobantePagoDTE/?dte_ids={factura.id}&inline=1')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')

    def test_voucher_con_html_se_escapa_en_la_vista_previa(self):
        factura = self._dte(281, self.prov_a, monto=1000)
        Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='Transferencia', voucher='<img src=x>',
                                        monto=500, fecha_pago=self.hoy)
        r = self.client.get(f'/app/datos_envio_comprobante/{factura.id}/')
        self.assertNotIn('<img', r.json()['preview']['filas'][0]['cheque'])


class EditarConNCAplicadaTest(_BaseDteCompra):
    """Revisión adversarial B3-08 / B13-03: editar no puede 'soltar' una NC aplicada."""

    def _aplicar_nc(self, factura, nc):
        r = self._json('post', '/app/asociar_nc_existente/', {'nc_id': nc.id, 'dte_id': factura.id})
        self.assertTrue(r.json()['success'], r.content)

    def test_no_cambia_empresa_receptora_de_factura_con_nc(self):
        crear_empresa_user(self.user, self.otra_empresa, crear_sucursal(empresa=self.otra_empresa, alias='V2B-OTRA'))
        factura = self._dte(6101, self.prov_a, monto=100000)
        nc = self._dte(9601, self.prov_a, tipo='NOTA DE CREDITO', monto=10000)
        self._aplicar_nc(factura, nc)
        r = self._json('put', f'/app/actualizarDteCompras/{factura.id}/', self._payload(
            numero_documento=6101, monto_con_iva=100000, empresa_receptora_id=self.otra_empresa.id))
        self.assertFalse(r.json()['success'], r.content)
        self.assertIn('empresa receptora', r.json()['error'])
        factura.refresh_from_db()
        self.assertEqual(factura.receptor_id, self.empresa.id)
        # La NC sigue viéndose aplicada: no vuelve a ofrecerse
        r = self.client.get('/app/obtener_ncs_disponibles/', {'proveedor': self.prov_a.id})
        self.assertNotIn(nc.id, {n['id'] for n in r.json()['ncs']})

    def test_nc_aplicada_no_cambia_identidad_ni_monto(self):
        factura = self._dte(6102, self.prov_a, monto=100000)
        nc = self._dte(9602, self.prov_a, tipo='NOTA DE CREDITO', monto=10000)
        self._aplicar_nc(factura, nc)
        base = dict(numero_documento=9602, tipo_documento='NOTA DE CREDITO', monto_con_iva=10000)
        for cambio in ({'receptor_id': self.prov_b.id}, {'numero_documento': 9699},
                       {'monto_con_iva': 12000}, {'estado_dte': 'ANULADO'}):
            r = self._json('put', f'/app/actualizarDteCompras/{nc.id}/', self._payload(**dict(base, **cambio)))
            self.assertFalse(r.json()['success'], (cambio, r.content))
            self.assertIn('#6102', r.json()['error'])
        nc.refresh_from_db()
        self.assertEqual((nc.emisor_id, nc.numero_documento, int(nc.monto_con_iva)), (self.prov_a.id, 9602, 10000))
        r = self.client.get(f'/app/obtener_info_asociacion_nc/{nc.id}/')
        self.assertTrue(r.json()['info']['esta_asociada'])
        # Editar datos que no sostienen la asociación sí se puede
        r = self._json('put', f'/app/actualizarDteCompras/{nc.id}/', self._payload(**dict(base, bultos=4)))
        self.assertTrue(r.json()['success'], r.content)


class ComprobanteEscapeTest(_BaseDteCompra):
    """Revisión adversarial B3-14: datos de usuario escapados en PDF y vista previa."""

    def test_voucher_con_menor_que_no_rompe_el_pdf(self):
        factura = self._dte(282, self.prov_a, monto=1000)
        for v in ('x<y', 'TRF<b>1', 'A&B'):
            Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='Transferencia', voucher=v,
                                            monto=100, fecha_pago=self.hoy)
        r = self.client.get(f'/app/comprobantePagoDTE/?dte_ids={factura.id}&inline=1')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r['Content-Type'], 'application/pdf')

    def test_proveedor_con_html_pdf_y_preview(self):
        prov = crear_empresa(nombre='<script>alert(1)</script> & Cia', rut='77.999.000-1', esProveedor=True)
        factura = self._dte(283, prov, monto=1000)
        r = self.client.get(f'/app/comprobantePagoDTE/?dte_ids={factura.id}&inline=1')
        self.assertEqual(r.status_code, 200, r.content[:300])
        r = self.client.get(f'/app/datos_envio_comprobante/{factura.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        prev = r.json()['preview']
        self.assertNotIn('<script', prev['proveedor'])
        self.assertIn('&lt;script&gt;', prev['proveedor'])
        # El campo de nivel superior (va a inputs) queda sin escapar
        self.assertEqual(r.json()['proveedor'], prov.nombre)


class AlcanceOtraEmpresaTest(_BaseDteCompra):
    """Revisión adversarial: DTE, pagos e incidencias acotados a la empresa en sesión."""

    def setUp(self):
        super().setUp()
        self.ajeno = crear_usuario(username='v2b_ajeno', rol=self.ROL)
        crear_empresa_user(self.ajeno, self.otra_empresa, crear_sucursal(empresa=self.otra_empresa, alias='V2B-AJ'))
        self.factura = self._dte(7101, self.prov_a, monto=10000)
        self.pago = Dte_Detalle_Pago.objects.create(dte=self.factura, metodo_pago='Transferencia', voucher='T1',
                                                    monto=1000, fecha_pago=self.hoy)

    def _como_ajeno(self):
        from django.test import Client
        c = Client()
        c.force_login(self.ajeno)
        s = c.session
        s['idEmpresaActual'] = self.otra_empresa.id
        s.save()
        return c

    def test_otra_empresa_no_lee_ni_crea_incidencias(self):
        from app.models import Dte_Incidencia
        c = self._como_ajeno()
        f = self.factura.id
        for url in (f'/app/obtenerDTE/{f}/', f'/app/pagosDTE/{f}/', f'/app/obtenerDetallePago/{f}/',
                    f'/app/detallePago/{self.pago.id}/', f'/app/incidencias/{f}/'):
            self.assertEqual(c.get(url).status_code, 404, url)
        r = c.post('/app/incidencias/crear/', data=json.dumps({
            'dte_id': f, 'tipo': 'OTRO', 'descripcion': 'Incidencia desde otra empresa'}),
            content_type='application/json')
        self.assertEqual(r.status_code, 404, r.content)
        self.assertFalse(Dte_Incidencia.objects.filter(dte=self.factura).exists())

        inc = Dte_Incidencia.objects.create(dte=self.factura, tipo='OTRO', descripcion='Propia de la empresa',
                                            estado='PENDIENTE')
        r = c.put(f'/app/incidencias/actualizar/{inc.id}/', data=json.dumps({'estado': 'RESUELTO'}),
                  content_type='application/json')
        self.assertEqual(r.status_code, 404, r.content)
        self.assertEqual(c.delete(f'/app/incidencias/eliminar/{inc.id}/').status_code, 404)
        inc.refresh_from_db()
        self.assertEqual(inc.estado, 'PENDIENTE')

    def test_la_empresa_duena_sigue_operando(self):
        from app.models import Dte_Incidencia
        f = self.factura.id
        for url in (f'/app/obtenerDTE/{f}/', f'/app/pagosDTE/{f}/', f'/app/obtenerDetallePago/{f}/',
                    f'/app/detallePago/{self.pago.id}/', f'/app/incidencias/{f}/'):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self._json('post', '/app/incidencias/crear/', {
            'dte_id': f, 'tipo': 'OTRO', 'descripcion': 'Falta una caja del pedido'})
        self.assertTrue(r.json()['success'], r.content)
        inc_id = r.json()['id']
        r = self._json('put', f'/app/incidencias/actualizar/{inc_id}/', {'estado': 'RESUELTO', 'notas_resolucion': 'ok'})
        self.assertTrue(r.json()['success'], r.content)
        self.assertEqual(Dte_Incidencia.objects.get(id=inc_id).estado, 'RESUELTO')
        r = self._json('put', f'/app/incidencias/actualizar/{inc_id}/', {'estado': 'INVENTADO'})
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(self._json('delete', f'/app/incidencias/eliminar/{inc_id}/').status_code, 200)
