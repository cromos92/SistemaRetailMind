"""
Unidad V2 — pagos y notas de crédito de documentos de COMPRA (views.py).

Cubre los escritores arreglados:
- B13-03 / B3-03 / B5-07 / B14-09: NC↔factura por (proveedor, folio, empresa):
  desasociar NO borra el pago de la factura de otro proveedor con el mismo
  folio; asociar rechaza NC de otro proveedor, de otra empresa, que supera el
  saldo, o ya aplicada; el estado se recalcula con todos los pagos.
- B3-02: estado_pago canónico (PENDIENTE / PARCIAL / PAGADO) y lectura
  insensible a mayúsculas en el filtro de pendientes de cargarDteCompra.
- B3-07: registrarPagoDTE rechaza el doble envío inmediato y documentos NC;
  procesar_pago_masivo valida el voucher de todo el lote antes de escribir.
- B3-03 / B16-09: los endpoints heredados agregar/eliminar NC se retiraron
  (unidad D): la ruta da 404 y no escribe.

Ejecutar (BD de test aislada):
    python manage.py test app.tests.test_v2_pagos_nc --keepdb
"""
import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from app.models import Dte, Dte_Detalle_Pago, Dte_Incidencia
from .factories import crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario, otorgar_ver_pantalla


class _BaseV2(TestCase):
    ROL = 'administracion'

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa(nombre='Nosotros V2', rut='76.200.000-2')
        cls.otra_empresa = crear_empresa(nombre='Otra Empresa V2', rut='76.300.000-3')
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='V2-SUC')
        cls.prov_a = crear_empresa(nombre='Proveedor A', rut='77.100.000-1', esProveedor=True)
        cls.prov_b = crear_empresa(nombre='Proveedor B', rut='77.200.000-2', esProveedor=True)
        cls.user = crear_usuario(username='v2_admin', rol=cls.ROL, first_name='Ana', last_name='Pagos')
        crear_empresa_user(cls.user, cls.empresa, cls.sucursal)
        # Pantalla + permisos finos de pagos (la unidad SEC exige la pantalla
        # también en las APIs).
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
             estado_pago='Pendiente', tipo_transaccion='COMPRA', dias=30):
        return Dte.objects.create(
            emisor=emisor, receptor=receptor or self.empresa, numero_documento=numero,
            tipo_documento=tipo, monto_con_iva=monto, monto_neto=round(monto / 1.19),
            descuento=0, estado_pago=estado_pago, estado_dte='ACEPTADO', responsable='test',
            fecha_emision=self.hoy, fecha_vencimiento=self.hoy + timedelta(days=dias),
            diasCredito=dias, bultos=0, unidades_productos=0,
            tipo_transaccion=tipo_transaccion, sucursal=self.sucursal,
        )

    def _post(self, url, data=None, method='post'):
        return getattr(self.client, method)(
            url, data=json.dumps(data or {}), content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def _rechazado(self, r, texto=None):
        """Rechazo por regla de negocio en asociar/desasociar NC: 200 con
        {success: false, error} (el JS muestra response.error solo en success)."""
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()['success'], r.content)
        if texto:
            self.assertIn(texto, r.json()['error'])


class AsociarDesasociarNCTest(_BaseV2):
    def test_desasociar_no_borra_pago_de_otro_proveedor_con_mismo_folio(self):
        """B13-03: la NC 718 del proveedor A no es la NC 718 del proveedor B."""
        factura_b = self._dte(5001, self.prov_b, monto=50000)
        pago_b = Dte_Detalle_Pago.objects.create(
            dte=factura_b, metodo_pago='Nota de Crédito', voucher='718', monto=10000,
            fecha_pago=self.hoy,
        )
        factura_b.estado_pago = 'PARCIAL'
        factura_b.save(update_fields=['estado_pago'])
        nc_a = self._dte(718, self.prov_a, tipo='NOTA DE CREDITO', monto=10000)

        r = self._post(f'/app/desasociar_nc/{nc_a.id}/')
        self._rechazado(r)
        self.assertTrue(Dte_Detalle_Pago.objects.filter(id=pago_b.id).exists())
        factura_b.refresh_from_db()
        self.assertEqual(factura_b.estado_pago, 'PARCIAL')

        # info de asociación: la NC de A no aparece enlazada a la factura de B
        r = self.client.get(f'/app/obtener_info_asociacion_nc/{nc_a.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()['info']['esta_asociada'])

    def test_asociar_rechaza_nc_de_otro_proveedor(self):
        factura = self._dte(6001, self.prov_a, monto=415072)
        nc_b = self._dte(9381, self.prov_b, tipo='NOTA DE CREDITO', monto=100000)
        r = self._post('/app/asociar_nc_existente/', {'nc_id': nc_b.id, 'dte_id': factura.id})
        self._rechazado(r, 'otro proveedor')
        self.assertFalse(Dte_Detalle_Pago.objects.filter(dte=factura).exists())

    def test_asociar_rechaza_nc_que_supera_el_saldo(self):
        factura = self._dte(6002, self.prov_a, monto=100000)
        Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='Transferencia', monto=95000, fecha_pago=self.hoy)
        nc = self._dte(9001, self.prov_a, tipo='NOTA DE CREDITO', monto=10000)
        r = self._post('/app/asociar_nc_existente/', {'nc_id': nc.id, 'dte_id': factura.id})
        self._rechazado(r, 'supera el saldo')
        self.assertEqual(Dte_Detalle_Pago.objects.filter(dte=factura).count(), 1)

    def test_asociar_rechaza_nc_de_otra_empresa(self):
        factura = self._dte(6003, self.prov_a, monto=100000)
        nc_otra = self._dte(9002, self.prov_a, tipo='NOTA DE CREDITO', monto=1000, receptor=self.otra_empresa)
        r = self._post('/app/asociar_nc_existente/', {'nc_id': nc_otra.id, 'dte_id': factura.id})
        self.assertEqual(r.status_code, 404, r.content)

    def test_asociar_rechaza_si_la_factura_tiene_incidencias(self):
        factura = self._dte(6004, self.prov_a, monto=100000)
        Dte_Incidencia.objects.create(dte=factura, tipo='OTRO', descripcion='Falta revisar', estado='PENDIENTE')
        nc = self._dte(9003, self.prov_a, tipo='NOTA DE CREDITO', monto=1000)
        r = self._post('/app/asociar_nc_existente/', {'nc_id': nc.id, 'dte_id': factura.id})
        self._rechazado(r, 'incidencias')

    def test_asociar_valida_deja_parcial_y_desasociar_recalcula(self):
        factura = self._dte(6005, self.prov_a, monto=100000)
        Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='Transferencia', monto=20000, fecha_pago=self.hoy)
        nc = self._dte(9004, self.prov_a, tipo='NOTA DE CREDITO', monto=30000)

        r = self._post('/app/asociar_nc_existente/', {'nc_id': nc.id, 'dte_id': factura.id})
        self.assertEqual(r.status_code, 200, r.content)
        pago_nc = Dte_Detalle_Pago.objects.get(dte=factura, metodo_pago='Nota de Crédito')
        self.assertEqual((pago_nc.voucher, pago_nc.monto, pago_nc.fecha_pago), ('9004', 30000, self.hoy))
        factura.refresh_from_db()
        self.assertEqual(factura.estado_pago, 'PARCIAL')

        # Segunda vez: ya aplicada
        otra = self._dte(6006, self.prov_a, monto=100000)
        r = self._post('/app/asociar_nc_existente/', {'nc_id': nc.id, 'dte_id': otra.id})
        self._rechazado(r, 'ya está asociada')

        # Desasociar: queda PARCIAL por la transferencia (antes: 'PENDIENTE')
        r = self._post(f'/app/desasociar_nc/{nc.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Dte_Detalle_Pago.objects.filter(id=pago_nc.id).exists())
        factura.refresh_from_db()
        self.assertEqual(factura.estado_pago, 'PARCIAL')

    def test_fichas_distintas_con_mismo_rut_no_se_cruzan(self):
        """Revisión adversarial B13-03: NC en la ficha 2 del proveedor (mismo RUT)
        no se aplica a la factura de la ficha 1. Antes se aceptaba y los lectores
        (por ficha exacta) no la veían: se aplicaba dos veces y no se podía desasociar."""
        from .factories import crear_empresa as _ce
        prov_a_ficha2 = _ce(nombre='Proveedor A (ficha 2)', rut='77100000-1', esProveedor=True)
        f1 = self._dte(6101, self.prov_a, monto=206252)
        f2 = self._dte(6102, self.prov_a, monto=85394)
        nc = self._dte(9210, prov_a_ficha2, tipo='NOTA DE CREDITO', monto=3558)
        for f in (f1, f2):
            r = self._post('/app/asociar_nc_existente/', {'nc_id': nc.id, 'dte_id': f.id})
            self._rechazado(r, 'otra ficha del mismo proveedor')
        self.assertFalse(Dte_Detalle_Pago.objects.filter(metodo_pago='Nota de Crédito', voucher='9210').exists())
        # Sigue disponible para una factura de SU ficha, y ahí se ve y se desasocia
        f3 = self._dte(6103, prov_a_ficha2, monto=50000)
        r = self._post('/app/asociar_nc_existente/', {'nc_id': nc.id, 'dte_id': f3.id})
        self.assertTrue(r.json()['success'], r.content)
        r = self._post('/app/asociar_nc_existente/', {'nc_id': nc.id, 'dte_id': f1.id})
        self._rechazado(r)
        self.assertEqual(Dte_Detalle_Pago.objects.filter(metodo_pago='Nota de Crédito', voucher='9210').count(), 1)
        r = self.client.get(f'/app/obtener_info_asociacion_nc/{nc.id}/')
        self.assertTrue(r.json()['info']['esta_asociada'])
        r = self.client.get(f'/app/obtener_asociaciones_dte/{f3.id}/')
        self.assertEqual(r.json()['notas_credito'][0]['nc_id'], nc.id)
        r = self._post(f'/app/desasociar_nc/{nc.id}/')
        self.assertTrue(r.json()['success'], r.content)

    def test_ncs_disponibles_excluye_nc_aplicada_a_factura_sin_receptor(self):
        factura_sin_receptor = Dte.objects.create(
            emisor=self.prov_a, receptor=None, numero_documento=6201, tipo_documento='FACTURA ELECTRONICA',
            monto_con_iva=10000, monto_neto=8403, descuento=0, estado_pago='PARCIAL', estado_dte='ACEPTADO',
            responsable='test', fecha_emision=self.hoy, fecha_vencimiento=self.hoy, diasCredito=0,
            bultos=0, unidades_productos=0, tipo_transaccion='COMPRA', sucursal=self.sucursal,
        )
        Dte_Detalle_Pago.objects.create(dte=factura_sin_receptor, metodo_pago='Nota de Crédito', voucher='9301',
                                        monto=1000, fecha_pago=self.hoy)
        nc = self._dte(9301, self.prov_a, tipo='NOTA DE CREDITO', monto=1000)
        r = self.client.get('/app/obtener_ncs_disponibles/')
        self.assertNotIn(nc.id, {n['id'] for n in r.json()['ncs']})

    def test_asociar_por_el_total_deja_pagado(self):
        factura = self._dte(6007, self.prov_a, monto=30000)
        nc = self._dte(9005, self.prov_a, tipo='NOTA DE CREDITO', monto=30000)
        r = self._post('/app/asociar_nc_existente/', {'nc_id': nc.id, 'dte_id': factura.id})
        self.assertEqual(r.status_code, 200, r.content)
        factura.refresh_from_db()
        self.assertEqual(factura.estado_pago, 'PAGADO')

    def test_ncs_disponibles_excluye_por_par_proveedor_folio(self):
        factura_b = self._dte(5002, self.prov_b, monto=50000)
        Dte_Detalle_Pago.objects.create(dte=factura_b, metodo_pago='Nota de Crédito', voucher='333',
                                        monto=1000, fecha_pago=self.hoy)
        nc_a = self._dte(333, self.prov_a, tipo='NOTA DE CREDITO', monto=1000)
        nc_b = self._dte(333, self.prov_b, tipo='NOTA DE CREDITO', monto=1000)
        r = self.client.get('/app/obtener_ncs_disponibles/')
        self.assertEqual(r.status_code, 200, r.content)
        ids = {n['id'] for n in r.json()['ncs']}
        self.assertIn(nc_a.id, ids)        # antes quedaba oculta por el folio de B
        self.assertNotIn(nc_b.id, ids)     # la de B sí está usada

    def test_listado_marca_nc_asociada_solo_con_su_proveedor(self):
        factura_b = self._dte(5003, self.prov_b, monto=50000)
        Dte_Detalle_Pago.objects.create(dte=factura_b, metodo_pago='Nota de Crédito', voucher='444',
                                        monto=1000, fecha_pago=self.hoy)
        nc_a = self._dte(444, self.prov_a, tipo='NOTA DE CREDITO', monto=1000)
        r = self._post('/app/cargarDteCompra/', {
            'fecha_inicio': (self.hoy - timedelta(days=5)).isoformat(),
            'fecha_fin': (self.hoy + timedelta(days=5)).isoformat(),
            'tipo_documento': 'NOTA DE CREDITO', 'page': 1, 'page_size': 20,
        })
        self.assertEqual(r.status_code, 200, r.content)
        fila = next(i for i in r.json()['items'] if i['id'] == nc_a.id)
        self.assertFalse(fila['nc_esta_asociada'])


class EstadoPagoYRegistroTest(_BaseV2):
    def test_abono_parcial_queda_parcial_y_sigue_en_pendientes(self):
        """B3-02: antes quedaba 'Abonado' y salía del filtro de pendientes."""
        factura = self._dte(7001, self.prov_a, monto=100000, estado_pago='PENDIENTE')
        r = self._post('/app/registrarPagoDTE/', {
            'dte_id': factura.id, 'metodo_pago': 'Transferencia', 'voucher': '',
            'monto': 1000, 'fecha_pago': self.hoy.isoformat(),
        })
        self.assertEqual(r.status_code, 200, r.content)
        factura.refresh_from_db()
        self.assertEqual(factura.estado_pago, 'PARCIAL')

        legado = self._dte(7002, self.prov_a, monto=5000, estado_pago='Abonado')
        mayus = self._dte(7003, self.prov_a, monto=5000, estado_pago='PENDIENTE')
        pagada = self._dte(7004, self.prov_a, monto=5000, estado_pago='Pagado')
        r = self._post('/app/cargarDteCompra/', {
            'fecha_inicio': (self.hoy - timedelta(days=5)).isoformat(),
            'fecha_fin': (self.hoy + timedelta(days=5)).isoformat(),
            'filtro_vencimiento': 'pendientes', 'page': 1, 'page_size': 100,
        })
        self.assertEqual(r.status_code, 200, r.content)
        ids = {i['id'] for i in r.json()['items']}
        self.assertTrue({factura.id, legado.id, mayus.id} <= ids)
        self.assertNotIn(pagada.id, ids)

    def test_pago_total_queda_pagado(self):
        factura = self._dte(7005, self.prov_a, monto=100000)
        r = self._post('/app/registrarPagoDTE/', {
            'dte_id': factura.id, 'metodo_pago': 'Cheque', 'voucher': 'CH-1',
            'monto': 100000, 'fecha_pago': self.hoy.isoformat(),
        })
        self.assertEqual(r.status_code, 200, r.content)
        factura.refresh_from_db()
        self.assertEqual(factura.estado_pago, 'PAGADO')

    def test_cuotas_identicas_se_registran_sin_confirmacion_del_cliente(self):
        """Revisión adversarial B3-07: el JS actual no sabe confirmar un 409, así
        que dos cuotas idénticas legítimas (sin clave confirmar_duplicado) se
        registran como antes; el tope bajo bloqueo impide el sobrepago."""
        factura = self._dte(7010, self.prov_a, monto=2000)
        payload = {'dte_id': factura.id, 'metodo_pago': 'Efectivo', 'voucher': '',
                   'monto': 1000, 'fecha_pago': self.hoy.isoformat()}
        self.assertEqual(self._post('/app/registrarPagoDTE/', payload).status_code, 200)
        self.assertEqual(self._post('/app/registrarPagoDTE/', payload).status_code, 200)
        r = self._post('/app/registrarPagoDTE/', payload)
        self.assertEqual(r.status_code, 400, r.content)   # excede el total
        self.assertEqual(Dte_Detalle_Pago.objects.filter(dte=factura).count(), 2)

    def test_doble_envio_se_avisa_si_el_cliente_lo_confirma(self):
        """B3-07: con confirmar_duplicado=false el mismo POST sin voucher da 409
        {duplicado: true}; reenviado con true, se registra."""
        factura = self._dte(7006, self.prov_a, monto=100000)
        payload = {'dte_id': factura.id, 'metodo_pago': 'Transferencia', 'voucher': '',
                   'monto': 1000, 'fecha_pago': self.hoy.isoformat(), 'confirmar_duplicado': False}
        self.assertEqual(self._post('/app/registrarPagoDTE/', payload).status_code, 200)
        r = self._post('/app/registrarPagoDTE/', payload)
        self.assertEqual(r.status_code, 409, r.content)
        self.assertTrue(r.json().get('duplicado'))
        self.assertEqual(Dte_Detalle_Pago.objects.filter(dte=factura).count(), 1)

        # Un segundo pago real: con su N° de comprobante, o confirmado.
        payload2 = dict(payload, voucher='TR-2')
        self.assertEqual(self._post('/app/registrarPagoDTE/', payload2).status_code, 200)
        payload3 = dict(payload, confirmar_duplicado=True)
        self.assertEqual(self._post('/app/registrarPagoDTE/', payload3).status_code, 200)
        self.assertEqual(Dte_Detalle_Pago.objects.filter(dte=factura).count(), 3)

    def test_metodos_reservados_de_nc_y_compensacion(self):
        """Revisión adversarial B3-03/B3-07: 'Nota de Crédito' y las compensaciones
        solo se aplican por sus vistas, no como pago (registrar, masivo, editar)."""
        factura = self._dte(7011, self.prov_a, monto=100000)
        for metodo in ('Nota de Crédito', 'nota de crédito', 'Compensación con Factura',
                       'Compensación con Factura Emitida'):
            r = self._post('/app/registrarPagoDTE/', {
                'dte_id': factura.id, 'metodo_pago': metodo, 'voucher': '9999',
                'monto': 1000, 'fecha_pago': self.hoy.isoformat(),
            })
            self.assertEqual(r.status_code, 400, (metodo, r.content))
        r = self._post('/app/procesar_pago_masivo/', {
            'facturas': [{'id': factura.id}], 'metodo_pago': 'Nota de Crédito',
            'voucher': '', 'fecha_pago': self.hoy.isoformat(),
        })
        self.assertEqual(r.status_code, 400, r.content)
        self.assertFalse(Dte_Detalle_Pago.objects.filter(dte=factura).exists())

        pago_nc = Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='Nota de Crédito', voucher='9998',
                                                  monto=5000, fecha_pago=self.hoy)
        pago = Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='Cheque', voucher='C7',
                                               monto=5000, fecha_pago=self.hoy)
        r = self._post(f'/app/editarPago/{pago_nc.id}/', {
            'metodo_pago': 'Nota de Crédito', 'voucher': '9998', 'monto': 90000,
            'fecha_pago': self.hoy.isoformat()}, method='put')
        self.assertEqual(r.status_code, 400, r.content)
        r = self._post(f'/app/editarPago/{pago.id}/', {
            'metodo_pago': 'Nota de Crédito', 'voucher': 'C7', 'monto': 5000,
            'fecha_pago': self.hoy.isoformat()}, method='put')
        self.assertEqual(r.status_code, 400, r.content)
        pago_nc.refresh_from_db()
        pago.refresh_from_db()
        self.assertEqual((pago_nc.monto, pago.metodo_pago), (5000, 'Cheque'))

    def test_no_registra_pago_a_nc_ni_voucher_largo(self):
        nc = self._dte(9100, self.prov_a, tipo='NOTA DE CREDITO', monto=5000)
        r = self._post('/app/registrarPagoDTE/', {
            'dte_id': nc.id, 'metodo_pago': 'Transferencia', 'voucher': 'X',
            'monto': 1000, 'fecha_pago': self.hoy.isoformat(),
        })
        self.assertEqual(r.status_code, 400, r.content)
        factura = self._dte(7007, self.prov_a, monto=100000)
        r = self._post('/app/registrarPagoDTE/', {
            'dte_id': factura.id, 'metodo_pago': 'Transferencia', 'voucher': 'V' * 51,
            'monto': 1000, 'fecha_pago': self.hoy.isoformat(),
        })
        self.assertEqual(r.status_code, 400, r.content)
        self.assertFalse(Dte_Detalle_Pago.objects.filter(dte__in=[nc, factura]).exists())

    def test_no_registra_pago_en_documento_de_otra_empresa(self):
        ajena = self._dte(7008, self.prov_a, monto=100000, receptor=self.otra_empresa)
        r = self._post('/app/registrarPagoDTE/', {
            'dte_id': ajena.id, 'metodo_pago': 'Transferencia', 'voucher': 'Z',
            'monto': 1000, 'fecha_pago': self.hoy.isoformat(),
        })
        self.assertEqual(r.status_code, 404, r.content)

    def test_editar_y_eliminar_pago_recalculan_canonico(self):
        factura = self._dte(7009, self.prov_a, monto=100000, estado_pago='Pagado')
        pago = Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='Cheque', voucher='C1',
                                               monto=100000, fecha_pago=self.hoy)
        r = self._post(f'/app/editarPago/{pago.id}/', {
            'metodo_pago': 'Cheque', 'voucher': 'C1', 'monto': 40000,
            'fecha_pago': self.hoy.isoformat(),
        }, method='put')
        self.assertEqual(r.status_code, 200, r.content)
        factura.refresh_from_db()
        self.assertEqual(factura.estado_pago, 'PARCIAL')
        r = self._post(f'/app/eliminarPago/{pago.id}/', method='delete')
        self.assertEqual(r.status_code, 200, r.content)
        factura.refresh_from_db()
        self.assertEqual(factura.estado_pago, 'PENDIENTE')


class PagoMasivoTest(_BaseV2):
    def test_voucher_largo_no_deja_el_lote_a_medias(self):
        f1 = self._dte(8001, self.prov_a, monto=10000)
        f2 = self._dte(8002123, self.prov_a, monto=20000)
        r = self._post('/app/procesar_pago_masivo/', {
            'facturas': [{'id': f1.id}, {'id': f2.id}], 'metodo_pago': 'Transferencia',
            'voucher': 'B' * 43, 'fecha_pago': self.hoy.isoformat(),
        })
        self.assertEqual(r.status_code, 400, r.content)
        self.assertFalse(Dte_Detalle_Pago.objects.filter(dte__in=[f1, f2]).exists())

    def test_lote_paga_saldos_y_no_repite(self):
        f1 = self._dte(8003, self.prov_a, monto=10000)
        f2 = self._dte(8004, self.prov_a, monto=20000)
        Dte_Detalle_Pago.objects.create(dte=f2, metodo_pago='Transferencia', monto=5000, fecha_pago=self.hoy)
        payload = {'facturas': [{'id': f1.id}, {'id': f2.id}], 'metodo_pago': 'Transferencia',
                   'voucher': 'LOTE', 'fecha_pago': self.hoy.isoformat()}
        r = self._post('/app/procesar_pago_masivo/', payload)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['total_procesado'], 25000)
        for f in (f1, f2):
            f.refresh_from_db()
            self.assertEqual(f.estado_pago, 'PAGADO')
        # Reenvío del mismo lote: ya no hay saldo
        r = self._post('/app/procesar_pago_masivo/', payload)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(Dte_Detalle_Pago.objects.filter(dte__in=[f1, f2]).count(), 3)

    def test_rechaza_proveedores_mezclados_y_otra_empresa(self):
        f1 = self._dte(8005, self.prov_a, monto=10000)
        f2 = self._dte(8006, self.prov_b, monto=10000)
        r = self._post('/app/procesar_pago_masivo/', {
            'facturas': [{'id': f1.id}, {'id': f2.id}], 'metodo_pago': 'Transferencia',
            'voucher': '', 'fecha_pago': self.hoy.isoformat(),
        })
        self.assertEqual(r.status_code, 400, r.content)
        ajena = self._dte(8007, self.prov_a, monto=10000, receptor=self.otra_empresa)
        r = self._post('/app/procesar_pago_masivo/', {
            'facturas': [{'id': ajena.id}], 'metodo_pago': 'Transferencia',
            'voucher': '', 'fecha_pago': self.hoy.isoformat(),
        })
        self.assertEqual(r.status_code, 400, r.content)
        self.assertFalse(Dte_Detalle_Pago.objects.filter(dte__in=[f1, f2, ajena]).exists())


class NotaCreditoHeredadaTest(_BaseV2):
    """Los endpoints heredados agregarNC / eliminarNC se retiraron (B16-09,
    unidad D, 2026-09-26): la UI no los alcanzaba. Queda la guarda de que no
    vuelven a escribir pagos."""

    def test_endpoints_heredados_de_nc_ya_no_existen(self):
        factura = self._dte(8101, self.prov_a, monto=10000)
        r = self._post('/app/agregarNC/', {'dte_id': factura.id, 'voucher': 'N1', 'monto': 10000, 'notas': 'total'})
        self.assertEqual(r.status_code, 404, r.content)
        self.assertFalse(Dte_Detalle_Pago.objects.filter(dte=factura).exists())
        factura.refresh_from_db()
        self.assertEqual(factura.estado_pago, 'Pendiente')  # intacto
