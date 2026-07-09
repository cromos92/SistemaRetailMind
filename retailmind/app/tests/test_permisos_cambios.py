import json

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import (
    CambioDevolucion,
    CodigoAutorizacionDinamico,
    ModuloSistema,
    OpcionMenu,
    PermisoRol,
    PermisoTemporalCambio,
    RegistroAutorizacion,
    Ticket,
)
from .factories import (
    crear_empresa,
    crear_empresa_user,
    crear_sucursal,
    crear_usuario,
    crear_vendedor,
)


STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class PermisosCambiosTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa(nombre='Empresa Permisos')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='SUC-PERM')
        self.vendedor = crear_vendedor(empresa=self.empresa)
        self.vendedor.sucursales.add(self.sucursal)

        self.admin = crear_usuario(username='admin-cambios', rol='administrador')
        self.jefe = crear_usuario(username='jefe-cambios', rol='jefe_local')
        self.operador = crear_usuario(username='vendedor-cambios', rol='vendedor')
        crear_empresa_user(self.admin, self.empresa, self.sucursal)
        crear_empresa_user(self.jefe, self.empresa, self.sucursal)
        crear_empresa_user(self.operador, self.empresa, self.sucursal)

        modulo, _ = ModuloSistema.objects.get_or_create(
            codigo='ventas-test', defaults={'nombre': 'Ventas Test'}
        )
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='cambios_devoluciones',
            defaults={'modulo': modulo, 'nombre': 'Cambios y devoluciones'},
        )
        PermisoRol.objects.update_or_create(
            rol='vendedor',
            opcion_menu=opcion,
            defaults={'puede_ver': True, 'puede_eliminar': True},
        )

        self.ticket_original = Ticket.objects.create(
            correlativo=901,
            vendedor=self.vendedor,
            sucursal=self.sucursal,
            subTotal=20000,
            total=20000,
            estado='PAGADO',
            responsable='test',
            cliente_nombre='Cliente Prueba',
        )
        self.cambio = CambioDevolucion.objects.create(
            ticket_original=self.ticket_original,
            sucursal=self.sucursal,
            numero_operacion='CD-PERM-001',
            tipo_operacion='CAMBIO_SIMPLE',
            estado='SOLICITADO',
            fecha_limite_cambio=timezone.localdate() + timezone.timedelta(days=10),
            monto_original=20000,
            monto_nuevo=20000,
            diferencia_monto=0,
            solicitado_por=self.jefe,
            motivo_principal='TALLA_INCORRECTA',
        )

        self.client = Client()
        self.client.force_login(self.jefe)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _codigo_admin(self, admin=None, codigo='123456'):
        return CodigoAutorizacionDinamico.objects.create(
            codigo=codigo,
            fecha_hora_inicio=timezone.now() - timezone.timedelta(minutes=1),
            fecha_hora_fin=timezone.now() + timezone.timedelta(minutes=30),
            generado_por=admin or self.admin,
        )

    def _post_cancelar(self, **extra):
        payload = {
            'cambio_id': self.cambio.id,
            'motivo': 'Cliente desiste de la solicitud',
        }
        payload.update(extra)
        return self.client.post(
            reverse('cancelar_cambio_devolucion'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_jefe_sin_autorizacion_temporal_no_puede_cancelar(self):
        response = self._post_cancelar()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'TEMP_AUTH_REQUIRED')
        self.cambio.refresh_from_db()
        self.assertEqual(self.cambio.estado, 'SOLICITADO')

    def test_codigo_navbar_admin_otorga_permiso_y_cancela(self):
        codigo = self._codigo_admin()
        response = self._post_cancelar(
            codigo_autorizacion=codigo.codigo,
            minutos_autorizacion=30,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])

        self.cambio.refresh_from_db()
        codigo.refresh_from_db()
        self.assertEqual(self.cambio.estado, 'CANCELADO')
        self.assertTrue(codigo.usado)
        self.assertTrue(PermisoTemporalCambio.objects.filter(
            usuario=self.jefe,
            accion=PermisoTemporalCambio.ACCION_CANCELAR,
            sucursal=self.sucursal,
        ).exists())
        self.assertTrue(RegistroAutorizacion.objects.filter(
            cambio_devolucion=self.cambio,
            usuario_autorizador=self.admin,
            exitoso=True,
        ).exists())

    def test_otro_perfil_con_permiso_requiere_autorizacion_temporal(self):
        self.client.force_login(self.operador)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

        response_sin_codigo = self._post_cancelar()
        self.assertEqual(response_sin_codigo.status_code, 403)
        self.assertEqual(response_sin_codigo.json()['code'], 'TEMP_AUTH_REQUIRED')

        codigo = self._codigo_admin(codigo='444555')
        response_autorizado = self._post_cancelar(codigo_autorizacion=codigo.codigo)
        self.assertEqual(response_autorizado.status_code, 200)
        self.cambio.refresh_from_db()
        self.assertEqual(self.cambio.estado, 'CANCELADO')

    def test_administrador_cancela_sin_codigo(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

        response = self._post_cancelar()
        self.assertEqual(response.status_code, 200)
        self.cambio.refresh_from_db()
        self.assertEqual(self.cambio.estado, 'CANCELADO')

    def test_codigo_administrador_otra_empresa_es_rechazado(self):
        otra_empresa = crear_empresa(nombre='Empresa Ajena', rut='77.000.000-1')
        otra_sucursal = crear_sucursal(empresa=otra_empresa, alias='SUC-AJENA')
        otro_admin = crear_usuario(username='admin-ajeno', rol='administrador')
        crear_empresa_user(otro_admin, otra_empresa, otra_sucursal)
        codigo = self._codigo_admin(admin=otro_admin, codigo='654321')

        response = self._post_cancelar(codigo_autorizacion=codigo.codigo)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'CROSS_COMPANY_AUTH')
        self.cambio.refresh_from_db()
        self.assertEqual(self.cambio.estado, 'SOLICITADO')

    def test_codigo_navbar_de_jefe_local_no_autoriza(self):
        codigo = self._codigo_admin(admin=self.jefe, codigo='222333')

        response = self._post_cancelar(codigo_autorizacion=codigo.codigo)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'INVALID_AUTHORIZER')
        codigo.refresh_from_db()
        self.cambio.refresh_from_db()
        self.assertFalse(codigo.usado)
        self.assertEqual(self.cambio.estado, 'SOLICITADO')

    def test_no_revertir_cambio_completado_y_pagado(self):
        ticket_nuevo = Ticket.objects.create(
            correlativo=902,
            vendedor=self.vendedor,
            sucursal=self.sucursal,
            subTotal=0,
            total=0,
            estado='PAGADO',
            responsable='test',
        )
        self.cambio.estado = 'COMPLETADO'
        self.cambio.ticket_nuevo = ticket_nuevo
        self.cambio.save(update_fields=['estado', 'ticket_nuevo'])

        self.client.force_login(self.admin)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()
        response = self.client.post(
            reverse('revertir_cambio_devolucion'),
            data=json.dumps({
                'cambio_id': self.cambio.id,
                'motivo': 'Error detectado después del pago',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'INVALID_STATE')
        self.cambio.refresh_from_db()
        self.assertEqual(self.cambio.estado, 'COMPLETADO')

    def test_aprobacion_exige_codigo_navbar(self):
        response = self.client.post(
            reverse('aprobar_cambio_generar_ticket'),
            data=json.dumps({
                'cambio_id': self.cambio.id,
                'vendedor_id': self.vendedor.id,
                'observaciones': '',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'AUTH_CODE_REQUIRED')

    def test_flujos_legacy_no_permiten_saltar_autorizacion(self):
        for url_name, payload in (
            ('aprobar_cambio_devolucion', {
                'cambio_id': self.cambio.id,
                'accion': 'aprobar',
            }),
            ('ejecutar_cambio_devolucion', {
                'cambio_id': self.cambio.id,
            }),
        ):
            with self.subTest(url_name=url_name):
                response = self.client.post(
                    reverse(url_name),
                    data=json.dumps(payload),
                    content_type='application/json',
                )
                self.assertEqual(response.status_code, 410)
                self.assertEqual(response.json()['code'], 'LEGACY_FLOW_DISABLED')
