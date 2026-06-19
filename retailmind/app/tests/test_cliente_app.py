"""
Tests de la API de clientes finales (app móvil de fidelización).

Cubre: vinculación (anti-enumeración), ciclo OTP completo, expiración/reúso de
OTP, emisión y aislamiento de tokens staff↔cliente, y los endpoints de datos.
Usa el test runner de Django (no pytest).
"""
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient

from app.models import (
    Cliente,
    CuentaClienteApp,
    CodigoOTPCliente,
    ProgramaFidelizacion,
    GiftCard,
)
from app.services import cliente_app_service, fidelizacion_service
from .factories import crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'

# RUTs chilenos válidos (cuerpo + DV correcto).
RUT_CLIENTE = '11.111.111-1'
RUT_INEXISTENTE = '22.222.222-2'

# Backend de email en memoria para inspeccionar el OTP enviado.
EMAIL_BACKEND_TEST = 'django.core.mail.backends.locmem.EmailBackend'


@override_settings(
    STATICFILES_STORAGE=STATICFILES_STORAGE_TEST,
    EMAIL_BACKEND=EMAIL_BACKEND_TEST,
    # Desactiva throttling en los tests (rates altísimos).
    REST_FRAMEWORK={
        'DEFAULT_THROTTLE_RATES': {
            'otp_solicitar': '1000/hour',
            'otp_verificar': '1000/hour',
            'vincular_cliente': '1000/hour',
        }
    },
)
class ClienteAppAuthTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.cliente = Cliente.objects.create(
            nombre='Juan', apellido='Pérez', rut=RUT_CLIENTE,
            email='juan@test.com', celular='912345678', activo=True,
        )

    # ---- solicitar OTP / vincular (anti-enumeración) ----

    def test_solicitar_otp_cliente_existente_envia_email(self):
        from django.core import mail
        resp = self.client.post('/api/v1/cliente/auth/solicitar-otp/',
                                {'rut': RUT_CLIENTE}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['success'])
        self.assertEqual(len(mail.outbox), 1)
        # Se creó la cuenta y un OTP.
        self.assertTrue(CuentaClienteApp.objects.filter(cliente=self.cliente).exists())
        self.assertTrue(CodigoOTPCliente.objects.filter(cliente=self.cliente, usado=False).exists())

    def test_email_otp_es_html_con_marca_y_logo(self):
        """El correo del código va con plantilla HTML de marca + logo incrustado."""
        from django.core import mail
        self.client.post('/api/v1/cliente/auth/solicitar-otp/',
                         {'rut': RUT_CLIENTE}, format='json')
        msg = mail.outbox[0]
        # Lleva alternativa HTML
        tipos = [c for _, c in msg.alternatives]
        self.assertIn('text/html', tipos)
        html = msg.alternatives[0][0]
        self.assertIn('cid:logo_realsport', html)     # logo corporativo inline
        self.assertIn('cid:logo_paola', html)         # logo del programa inline
        self.assertIn('Paola', html)
        self.assertIn('Realsport', html)
        # Adjunta las dos imágenes (un logo por marca)
        imagenes = [
            part for part in msg.message().walk()
            if part.get_content_type() == 'image/png'
        ]
        self.assertEqual(len(imagenes), 2)

    def test_solicitar_otp_rut_inexistente_respuesta_generica_sin_email(self):
        from django.core import mail
        resp = self.client.post('/api/v1/cliente/auth/solicitar-otp/',
                                {'rut': RUT_INEXISTENTE}, format='json')
        # Misma respuesta que un RUT válido (no se filtra existencia) y sin email.
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['success'])
        self.assertEqual(len(mail.outbox), 0)

    def test_solicitar_otp_rut_invalido_400(self):
        resp = self.client.post('/api/v1/cliente/auth/solicitar-otp/',
                                {'rut': 'no-es-rut'}, format='json')
        self.assertEqual(resp.status_code, 400)

    # ---- verificar OTP / emisión de tokens ----

    def test_ciclo_otp_completo_devuelve_tokens(self):
        # Generamos un OTP conocido directamente (el claro no se persiste).
        otp, codigo = CodigoOTPCliente.crear_para_cliente(
            self.cliente, canal='EMAIL', destino=self.cliente.email,
        )
        resp = self.client.post('/api/v1/cliente/auth/verificar-otp/',
                                {'rut': RUT_CLIENTE, 'codigo': codigo}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('access', resp.data)
        self.assertIn('refresh', resp.data)
        self.assertIn('expires_at', resp.data)

    def test_verificar_otp_codigo_incorrecto_400(self):
        CodigoOTPCliente.crear_para_cliente(self.cliente, canal='EMAIL', destino=self.cliente.email)
        resp = self.client.post('/api/v1/cliente/auth/verificar-otp/',
                                {'rut': RUT_CLIENTE, 'codigo': '000000'}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_verificar_otp_expirado_falla(self):
        otp, codigo = CodigoOTPCliente.crear_para_cliente(
            self.cliente, canal='EMAIL', destino=self.cliente.email,
        )
        otp.expires_at = timezone.now() - timezone.timedelta(minutes=1)
        otp.save(update_fields=['expires_at'])
        resp = self.client.post('/api/v1/cliente/auth/verificar-otp/',
                                {'rut': RUT_CLIENTE, 'codigo': codigo}, format='json')
        self.assertEqual(resp.status_code, 400)

    def test_otp_un_solo_uso(self):
        otp, codigo = CodigoOTPCliente.crear_para_cliente(
            self.cliente, canal='EMAIL', destino=self.cliente.email,
        )
        # Primer uso OK.
        ok1, _ = CodigoOTPCliente.validar(self.cliente, codigo)
        # Segundo uso del mismo código falla.
        ok2, _ = CodigoOTPCliente.validar(self.cliente, codigo)
        self.assertTrue(ok1)
        self.assertFalse(ok2)

    # ---- refresh y reúso ----

    def test_refresh_rota_token(self):
        tokens = cliente_app_service.emitir_tokens(
            self.cliente, CuentaClienteApp.objects.create(cliente=self.cliente),
        )
        resp = self.client.post('/api/v1/cliente/auth/refresh/',
                                {'refresh': tokens['refresh']}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertNotEqual(resp.data['refresh'], tokens['refresh'])

    def test_reuso_refresh_invalida_familia(self):
        tokens = cliente_app_service.emitir_tokens(
            self.cliente, CuentaClienteApp.objects.create(cliente=self.cliente),
        )
        # Primer refresh OK.
        self.client.post('/api/v1/cliente/auth/refresh/',
                         {'refresh': tokens['refresh']}, format='json')
        # Reúso del refresh ya usado → 401.
        resp = self.client.post('/api/v1/cliente/auth/refresh/',
                                {'refresh': tokens['refresh']}, format='json')
        self.assertEqual(resp.status_code, 401)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class ClienteAppDatosTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.programa = ProgramaFidelizacion.objects.create(activo=True)
        self.cliente = Cliente.objects.create(
            nombre='Ana', apellido='Soto', rut=RUT_CLIENTE,
            email='ana@test.com', activo=True,
        )
        self.cuenta_app, _ = fidelizacion_service.get_or_create_cuenta(self.cliente)
        # Autenticar con un access token de cliente.
        cuenta_app = CuentaClienteApp.objects.create(cliente=self.cliente)
        tokens = cliente_app_service.emitir_tokens(self.cliente, cuenta_app)
        self.access = tokens['access']

    def _auth(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access}')

    def test_saldo_requiere_token(self):
        resp = self.client.get('/api/v1/cliente/puntos/saldo/')
        self.assertEqual(resp.status_code, 401)

    def test_saldo_con_token_ok(self):
        self._auth()
        resp = self.client.get('/api/v1/cliente/puntos/saldo/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['success'])
        self.assertIn('saldo_puntos', resp.data)

    def test_movimientos_paginado(self):
        self._auth()
        resp = self.client.get('/api/v1/cliente/puntos/movimientos/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['success'])
        self.assertIn('results', resp.data)

    def test_giftcards_lista(self):
        GiftCard.objects.create(saldo_inicial=10000, saldo_actual=10000, cliente=self.cliente)
        self._auth()
        resp = self.client.get('/api/v1/cliente/giftcards/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 1)
        # No debe exponer el PIN ni saldo_inicial.
        self.assertNotIn('pin', resp.data['results'][0])
        self.assertNotIn('saldo_inicial', resp.data['results'][0])

    def test_carnet_devuelve_rut(self):
        self._auth()
        resp = self.client.get('/api/v1/cliente/carnet/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['qr_payload'], '111111111')
        self.assertEqual(resp.data['rut_formateado'], '11.111.111-1')

    def test_perfil_patch_email_es_presencial_ignorado(self):
        """El email NO se edita desde la app (gestión presencial): el PATCH lo ignora
        y no toca su verificación."""
        cuenta = CuentaClienteApp.objects.get(cliente=self.cliente)
        cuenta.email_verificado = True
        cuenta.save(update_fields=['email_verificado'])
        email_original = self.cliente.email
        self._auth()
        resp = self.client.patch('/api/v1/cliente/perfil/',
                                 {'email': 'nuevo@test.com'}, format='json')
        self.assertEqual(resp.status_code, 200)
        cuenta.refresh_from_db()
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.email, email_original)
        self.assertTrue(cuenta.email_verificado)

    def test_perfil_patch_celular_resetea_verificacion(self):
        """El celular SÍ se edita desde la app y resetea su verificación."""
        cuenta = CuentaClienteApp.objects.get(cliente=self.cliente)
        cuenta.celular_verificado = True
        cuenta.save(update_fields=['celular_verificado'])
        self._auth()
        resp = self.client.patch('/api/v1/cliente/perfil/',
                                 {'celular': '+56 9 8765 4321'}, format='json')
        self.assertEqual(resp.status_code, 200)
        cuenta.refresh_from_db()
        self.cliente.refresh_from_db()
        self.assertTrue(self.cliente.celular)
        self.assertFalse(cuenta.celular_verificado)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class AislamientoTokensTest(TestCase):
    """Un token de staff no debe poder usar endpoints de cliente."""

    def setUp(self):
        self.client = APIClient()
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa)
        self.user = crear_usuario(username='staff1', password='Pass123!',
                                  email='staff1@test.com', rol='cajero')
        crear_empresa_user(self.user, self.empresa, self.sucursal)

    def test_token_staff_no_accede_a_endpoints_cliente(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        access = str(RefreshToken.for_user(self.user).access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        resp = self.client.get('/api/v1/cliente/puntos/saldo/')
        # ClienteJWTAuthentication rechaza por falta de tipo='cliente'.
        self.assertEqual(resp.status_code, 401)
