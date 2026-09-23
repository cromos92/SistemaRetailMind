"""
Cuadratura y Arqueo: alerta Mercado Pago + edición/eliminación de documentos
solo por permiso.

Cubre:
- `generar_cuadratura_caja` agrega `alertas_mp` (cobros MP sin venta / pagos
  «MP manual» sin transacción) y nunca se cae si ese cálculo falla.
- La página `cuadratura_caja` trae la alerta de hoy y los flags del link a
  Conciliación MP.
- Editar / eliminar / anular documentos ya no tiene bypass por rol: el
  administrador sin el permiso fino recibe 403 y el Maestro pasa.

Correr en BD local desechable:
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_cuadratura_alertas_y_edicion
"""
import json
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import (
    Dte, ModuloSistema, OpcionMenu, PermisoRol, Ticket, TicketDetallePago,
)

from .factories import crear_empresa, crear_sucursal, crear_usuario, crear_vendedor
from .test_mercadopago_pos import _config, _transaccion

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


def _permiso(rol, codigo, **flags):
    """Deja la fila PermisoRol (rol, codigo) con exactamente `flags` encendidos."""
    modulo, _ = ModuloSistema.objects.get_or_create(
        codigo='documentos', defaults={'nombre': 'Módulo Documentos', 'orden': 3})
    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo=codigo, defaults={'modulo': modulo, 'nombre': codigo, 'orden': 99})
    valores = {c: False for c in ('puede_ver', 'puede_crear', 'puede_editar',
                                  'puede_eliminar', 'puede_exportar', 'puede_aprobar')}
    valores.update(flags)
    PermisoRol.objects.update_or_create(rol=rol, opcion_menu=opcion, defaults=valores)


class _Base(TestCase):

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(empresa=self.empresa)
        self.vendedor = crear_vendedor(empresa=self.empresa)
        self.hoy = timezone.localdate()

    def _cliente(self, usuario):
        c = Client()
        c.force_login(usuario)
        s = c.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()
        return c

    def _dte(self, numero=1001, tipo='BOLETA ELECTRONICA', monto=11900):
        return Dte.objects.create(
            emisor=self.empresa, numero_documento=numero, tipo_documento=tipo,
            monto_con_iva=monto, monto_neto=round(monto / 1.19), descuento=0,
            estado_pago='PAGADO', estado_dte='EMITIDO', responsable='test',
            fecha_emision=self.hoy, fecha_vencimiento=self.hoy, diasCredito=0,
            bultos=0, unidades_productos=1, tipo_transaccion='VENTA_PUBLICO',
            sucursal=self.sucursal, es_nota_credito=(tipo == 'NOTA DE CREDITO'),
        )


# ---------------------------------------------------------------------------
# Alerta Mercado Pago en la cuadratura
# ---------------------------------------------------------------------------

class AlertaMercadoPagoCuadraturaTest(_Base):

    def setUp(self):
        super().setUp()
        self.config = _config(self.sucursal, nombre='Caja 1')
        self.cajero = crear_usuario(username='cajero_alerta_mp', rol='cajero')
        self.client = self._cliente(self.cajero)

    def _generar(self):
        resp = self.client.post(reverse('generar_cuadratura_caja'),
                                {'fecha': self.hoy.strftime('%Y-%m-%d')})
        self.assertEqual(resp.status_code, 200, resp.content)
        datos = resp.json()
        self.assertTrue(datos.get('success'), datos)
        return datos['cuadratura']

    def test_cobro_mp_sin_venta_aparece_en_la_cuadratura(self):
        # Cobro aprobado que ningún pago respalda (se cerró la espera y la
        # venta se terminó como tarjeta manual).
        _transaccion(self.config, correlativo='501', monto=15990,
                     consumida=False, canal='POINT', payment_id_mp='900001')
        # Cobro ya consumido por su venta: no alerta.
        _transaccion(self.config, correlativo='502', monto=7000, consumida=True,
                     canal='POINT', payment_id_mp='900002')

        alertas = self._generar()['alertas_mp']
        self.assertTrue(alertas['hay_alerta'])
        self.assertEqual(alertas['cobros_sin_venta'], 1)
        self.assertEqual(int(alertas['cobros_sin_venta_monto']), 15990)
        self.assertEqual(alertas['manuales_sin_respaldo'], 0)

    def test_pago_mp_manual_sin_transaccion_cuenta_aparte(self):
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=77,
            estado='PAGADO', subTotal=8000, descuento=0, total=8000, responsable='t',
        )
        TicketDetallePago.objects.create(ticket=ticket, metodo_pago='MP_POINT', monto=8000,
                                         origen_pago='MANUAL', voucher='123456789')

        alertas = self._generar()['alertas_mp']
        self.assertTrue(alertas['hay_alerta'])
        self.assertEqual(alertas['cobros_sin_venta'], 0)
        self.assertEqual(alertas['manuales_sin_respaldo'], 1)
        self.assertEqual(int(alertas['manuales_sin_respaldo_monto']), 8000)

    def test_dia_sin_descalces_no_alerta(self):
        alertas = self._generar()['alertas_mp']
        self.assertFalse(alertas['hay_alerta'])

    def test_si_la_alerta_falla_la_cuadratura_igual_responde(self):
        with mock.patch('app.services.asociacion_mp_service.resumen_alerta_caja',
                        side_effect=RuntimeError('boom')):
            cuadratura = self._generar()
        self.assertNotIn('alertas_mp', cuadratura)
        self.assertIn('venta_total', cuadratura)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class PaginaCuadraturaAlertaMpTest(_Base):

    def test_pagina_trae_alerta_de_hoy_y_link_para_el_maestro(self):
        config = _config(self.sucursal, nombre='Caja 1')
        _transaccion(config, correlativo='DIRECTO-2309-101010', monto=12000,
                     consumida=False, canal='POINT', payment_id_mp='900010')
        maestro = crear_usuario(username='maestro_cuadratura', rol='maestro')

        resp = self._cliente(maestro).get(reverse('cuadratura_caja'))

        self.assertEqual(resp.status_code, 200)
        alertas = resp.context['alertas_mp_hoy']
        self.assertEqual(alertas['cobros_sin_venta'], 1)
        self.assertEqual(alertas['cobros_directos'], 1)
        self.assertTrue(resp.context['puede_ver_conciliacion_mp'])
        self.assertTrue(resp.context['puede_asociar_mp'])
        self.assertTrue(resp.context['puede_eliminar_documento'])
        self.assertContains(resp, 'id="alertasMpHoyData"')
        self.assertContains(resp, 'id="alertaMpCaja"')

    def test_cajero_sin_permiso_de_conciliacion_no_recibe_el_link(self):
        _permiso('cajero', 'cuadratura_caja', puede_ver=True)
        _permiso('cajero', 'dineros_mercadopago')
        _permiso('cajero', 'asociar_pagos_mercadopago')
        cajero = crear_usuario(username='cajero_pagina', rol='cajero')

        resp = self._cliente(cajero).get(reverse('cuadratura_caja'))

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['puede_ver_conciliacion_mp'])
        self.assertFalse(resp.context['puede_asociar_mp'])
        self.assertFalse(resp.context['puede_eliminar_documento'])
        self.assertFalse(resp.context['alertas_mp_hoy']['hay_alerta'])


# ---------------------------------------------------------------------------
# Editar / eliminar documentos: solo por permiso (sin bypass por rol)
# ---------------------------------------------------------------------------

class EliminarDocumentoPorPermisoTest(_Base):

    def _eliminar(self, usuario, dte):
        return self._cliente(usuario).post(
            reverse('eliminar_documento_venta'),
            data=json.dumps({'documento_id': dte.id, 'motivo': 'test permiso'}),
            content_type='application/json',
        )

    def test_administrador_sin_permiso_recibe_403(self):
        _permiso('administrador', 'dte_eliminar_documento', puede_ver=True, puede_eliminar=False)
        admin = crear_usuario(username='admin_sin_eliminar', rol='administrador')
        dte = self._dte()

        resp = self._eliminar(admin, dte)

        self.assertEqual(resp.status_code, 403, resp.content)
        dte.refresh_from_db()
        self.assertFalse(dte.descartado)

    def test_administrador_con_permiso_elimina(self):
        _permiso('administrador', 'dte_eliminar_documento', puede_ver=True, puede_eliminar=True)
        admin = crear_usuario(username='admin_con_eliminar', rol='administrador')
        dte = self._dte(numero=1002)

        resp = self._eliminar(admin, dte)

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json().get('success'), resp.content)

    def test_maestro_elimina_aunque_el_rol_administrador_no_pueda(self):
        _permiso('administrador', 'dte_eliminar_documento', puede_ver=True, puede_eliminar=False)
        maestro = crear_usuario(username='maestro_eliminar', rol='maestro')
        dte = self._dte(numero=1003)

        resp = self._eliminar(maestro, dte)

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json().get('success'), resp.content)
        dte.refresh_from_db()
        self.assertTrue(dte.descartado)

    def test_anular_documento_exige_el_mismo_permiso(self):
        # Antes bastaba estar logueado.
        vendedor = crear_usuario(username='vend_anular', rol='vendedor')
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=88,
            estado='PAGADO', subTotal=5000, descuento=0, total=5000, responsable='t',
        )
        resp = self._cliente(vendedor).post(
            reverse('anular_documento_venta'),
            data=json.dumps({'tipo': 'TICKET', 'documento_id': ticket.id}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403, resp.content)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, 'PAGADO')

    def test_convertir_a_factura_exige_permiso_de_factura(self):
        vendedor = crear_usuario(username='vend_convertir', rol='vendedor')
        resp = self._cliente(vendedor).post(
            reverse('convertir_ticket_a_factura'),
            data=json.dumps({'documento_id': 1}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403, resp.content)


class EditarDocumentoPorPermisoTest(_Base):

    def setUp(self):
        super().setUp()
        # Tipo habilitado: el 403 sale solo por el permiso del CAMPO.
        _permiso('administrador', 'dte_editar_tipo_boleta_electronica', puede_ver=True, puede_editar=True)
        _permiso('administrador', 'dte_editar_pago', puede_ver=True, puede_editar=False)
        _permiso('administrador', 'dte_editar_vendedor', puede_ver=True, puede_editar=False)
        self.admin = crear_usuario(username='admin_sin_editar', rol='administrador')
        self.dte = self._dte(numero=2001)

    def _editar(self, usuario, **campos):
        body = {'documento_id': self.dte.id}
        body.update(campos)
        return self._cliente(usuario).post(
            reverse('editar_dte_boleta_papel'),
            data=json.dumps(body),
            content_type='application/json',
        )

    def test_pago_sin_dte_editar_pago_da_403_aunque_sea_administrador(self):
        # El id del pago no importa: el permiso se valida antes de tocar pagos.
        resp = self._editar(self.admin, pagos=[{'id': 999999, 'metodo_pago': 'EFECTIVO', 'monto': 11900}])
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertIn('pagos', resp.json()['error'])

    def test_vendedor_sin_dte_editar_vendedor_da_403_aunque_sea_administrador(self):
        resp = self._editar(self.admin, vendedor_id=self.vendedor.id)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertIn('vendedor', resp.json()['error'])

    def test_editar_fecha_pago_nc_exige_dte_editar_pago(self):
        nc = self._dte(numero=3001, tipo='NOTA DE CREDITO')
        resp = self._cliente(self.admin).post(
            reverse('editar_fecha_pago_nc'),
            data=json.dumps({'dte_id': nc.id, 'fecha_pago': self.hoy.strftime('%Y-%m-%d')}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_detalle_cuadratura_expone_flags_por_permiso(self):
        _permiso('administrador', 'dte_eliminar_documento', puede_ver=True, puede_eliminar=False)
        resp = self._cliente(self.admin).get(
            reverse('obtener_detalle_cuadratura_metodos_pago'),
            {'fecha': self.hoy.strftime('%Y-%m-%d')})
        self.assertEqual(resp.status_code, 200, resp.content)
        permisos = resp.json()['permisos_usuario']
        self.assertFalse(permisos['puede_eliminar'])
        self.assertFalse(permisos['puede_editar_fecha_pago_nc'])

        maestro = crear_usuario(username='maestro_detalle', rol='maestro')
        permisos = self._cliente(maestro).get(
            reverse('obtener_detalle_cuadratura_metodos_pago'),
            {'fecha': self.hoy.strftime('%Y-%m-%d')}).json()['permisos_usuario']
        self.assertTrue(permisos['puede_eliminar'])
        self.assertTrue(permisos['puede_editar_fecha_pago_nc'])


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class GestionDocumentosPermisosContextoTest(_Base):

    def test_administrador_sin_permisos_no_ve_editar_ni_eliminar(self):
        _permiso('administrador', 'gestion_documentos_ventas', puede_ver=True)
        for codigo in ('dte_editar_fecha', 'dte_editar_numero', 'dte_editar_pago',
                       'dte_editar_vendedor', 'dte_editar_tipo_boleta_electronica',
                       'dte_editar_tipo_boleta_papel', 'dte_editar_tipo_factura_electronica',
                       'dte_editar_tipo_factura_exenta'):
            _permiso('administrador', codigo, puede_ver=True, puede_editar=False)
        _permiso('administrador', 'dte_eliminar_documento', puede_ver=True, puede_eliminar=False)
        admin = crear_usuario(username='admin_docs', rol='administrador')

        resp = self._cliente(admin).get(reverse('gestion_ventas_documentos'))

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['puede_editar_algun_dte'])
        self.assertFalse(resp.context['puede_editar_vendedor_dte'])
        self.assertFalse(resp.context['puede_eliminar_documento'])
        self.assertFalse(any(resp.context['permisos_edicion_dte']['campo'].values()))
        self.assertContains(resp, 'id="permisosEdicionDteData"')

    def test_maestro_tiene_todo_el_mapa_en_true(self):
        maestro = crear_usuario(username='maestro_docs', rol='maestro')

        resp = self._cliente(maestro).get(reverse('gestion_ventas_documentos'))

        self.assertEqual(resp.status_code, 200)
        mapa = resp.context['permisos_edicion_dte']
        self.assertTrue(all(mapa['campo'].values()))
        self.assertTrue(all(mapa['tipo'].values()))
        self.assertTrue(resp.context['puede_eliminar_documento'])
        self.assertTrue(resp.context['puede_editar_algun_dte'])
