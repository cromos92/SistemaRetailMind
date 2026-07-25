"""
Tests del envío de requerimientos al proveedor.

Cubre el flujo de correo dual:
- correo al proveedor (con CC al administrador del proveedor y fotos adjuntas)
- copia-resumen de control SIN fotos (env REQUERIMIENTOS_CORREO_COPIA,
  campo correo_copia del POST, o el correo del usuario que envía)
y el fix de la transición ESPERANDO_RESPUESTA para jefe_local.
"""
import json

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from app.models import Requerimiento
from app.tests.factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='sistema@test.cl',
)
class EnviarAProveedorTest(TestCase):

    def setUp(self):
        self.admin = crear_usuario(
            username='admin_req', rol='administrador', email='admin_req@test.com'
        )
        self.empresa = crear_empresa(nombre='Holding Test')
        self.sucursal = crear_sucursal(empresa=self.empresa)
        crear_empresa_user(self.admin, self.empresa, self.sucursal)

        self.proveedor = crear_empresa(
            nombre='Proveedor Test',
            rut='77.111.111-1',
            esProveedor=True,
            correoVendedor='ventas@proveedor.cl',
            correoIntercambio='intercambio@proveedor.cl',
            correoAdministrador='admin@proveedor.cl',
        )

        self.req = Requerimiento.objects.create(
            tipo='PRODUCTO_FALLADO',
            sucursal=self.sucursal,
            usuario_creador=self.admin,
            sku='12345',
            nombre_producto='Zapatilla Test',
            cliente_nombre='Cliente Test',
            motivo='Suela despegada',
            proveedor=self.proveedor,
        )
        self.url = reverse('api_enviar_a_proveedor', args=[self.req.id])
        self.client.force_login(self.admin)

    def _post(self, payload=None):
        return self.client.post(
            self.url,
            data=json.dumps(payload or {}),
            content_type='application/json',
        )

    def test_envia_correo_proveedor_y_copia_resumen(self):
        resp = self._post({'correo_copia': 'control@miempresa.cl'})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['copia_enviada'])
        self.assertEqual(data['correo_destino'], 'ventas@proveedor.cl')

        self.assertEqual(len(mail.outbox), 2)

        correo_prov = mail.outbox[0]
        self.assertIn('ventas@proveedor.cl', correo_prov.to)
        self.assertIn('admin@proveedor.cl', correo_prov.cc)
        self.assertIn(self.req.numero_requerimiento, correo_prov.subject)

        copia = mail.outbox[1]
        self.assertEqual(copia.to, ['control@miempresa.cl'])
        self.assertEqual(copia.cc, [])
        # La copia de control NUNCA lleva las fotos adjuntas
        self.assertEqual(copia.attachments, [])
        self.assertIn('[COPIA]', copia.subject)

        self.req.refresh_from_db()
        self.assertEqual(self.req.estado, 'ESPERANDO_RESPUESTA')
        self.assertTrue(self.req.correo_enviado_proveedor)
        self.assertEqual(self.req.correo_proveedor_destino, 'ventas@proveedor.cl')
        self.assertEqual(self.req.intentos_envio, 1)

        acciones = list(self.req.historial.values_list('accion', flat=True))
        self.assertIn('ENVIADO_A_PROVEEDOR', acciones)
        self.assertIn('COPIA_RESUMEN_ENVIADA', acciones)

    def test_copia_por_defecto_va_al_correo_del_usuario(self):
        resp = self._post()

        self.assertTrue(resp.json()['success'])
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[1].to, ['admin_req@test.com'])

    def test_fallback_a_campo_email_del_proveedor(self):
        self.proveedor.correoVendedor = ''
        self.proveedor.email = 'contacto@proveedor.cl'
        self.proveedor.save()

        resp = self._post()

        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['correo_destino'], 'contacto@proveedor.cl')

    def test_error_si_proveedor_sin_ningun_correo(self):
        self.proveedor.correoVendedor = ''
        self.proveedor.email = ''
        self.proveedor.correoIntercambio = ''
        self.proveedor.save()

        resp = self._post()

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)
        self.req.refresh_from_db()
        self.assertEqual(self.req.estado, 'PENDIENTE')

    def test_correo_destino_manual_y_reenvio(self):
        resp = self._post({'correo_destino': 'otro@proveedor.cl'})
        self.assertEqual(resp.json()['correo_destino'], 'otro@proveedor.cl')

        mail.outbox.clear()

        resp2 = self._post({'es_reenvio': True})
        self.assertTrue(resp2.json()['success'])
        self.assertIn('RECORDATORIO', mail.outbox[0].subject)

        self.req.refresh_from_db()
        self.assertEqual(self.req.intentos_envio, 2)
        self.assertIsNotNone(self.req.ultimo_recordatorio)
        acciones = list(self.req.historial.values_list('accion', flat=True))
        self.assertIn('RECORDATORIO_ENVIADO', acciones)

    def test_correo_destino_invalido(self):
        resp = self._post({'correo_destino': 'no-es-un-correo'})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_vendedor_no_puede_enviar(self):
        vendedor = crear_usuario(username='vend_req', rol='vendedor')
        crear_empresa_user(vendedor, self.empresa, self.sucursal)
        self.client.force_login(vendedor)

        resp = self._post()

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class ActualizarEstadoJefeLocalTest(TestCase):
    """El chequeo histórico comparaba contra 'ESPERANDO_PROVEEDOR' (estado
    inexistente) y nunca se disparaba; ahora bloquea ESPERANDO_RESPUESTA."""

    def setUp(self):
        self.empresa = crear_empresa(nombre='Holding Estado Test')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='SUC-EST')
        self.jefe = crear_usuario(username='jefe_req', rol='jefe_local')
        crear_empresa_user(self.jefe, self.empresa, self.sucursal)

        self.req = Requerimiento.objects.create(
            tipo='GARANTIA',
            sucursal=self.sucursal,
            usuario_creador=self.jefe,
            sku='9999',
            nombre_producto='Bototo Test',
            cliente_nombre='Cliente Estado',
            motivo='Garantía por costura',
        )
        self.url = reverse('api_actualizar_estado_requerimiento', args=[self.req.id])
        self.client.force_login(self.jefe)

    def test_jefe_local_no_puede_marcar_esperando_respuesta(self):
        resp = self.client.post(
            self.url,
            data=json.dumps({'estado': 'ESPERANDO_RESPUESTA'}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 403)
        self.req.refresh_from_db()
        self.assertEqual(self.req.estado, 'PENDIENTE')

    def test_jefe_local_si_puede_pasar_a_en_revision(self):
        resp = self.client.post(
            self.url,
            data=json.dumps({'estado': 'EN_REVISION'}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 200)
        self.req.refresh_from_db()
        self.assertEqual(self.req.estado, 'EN_REVISION')

    def test_transicion_invalida_rechazada(self):
        resp = self.client.post(
            self.url,
            data=json.dumps({'estado': 'COMPLETADO'}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 400)
        self.req.refresh_from_db()
        self.assertEqual(self.req.estado, 'PENDIENTE')
