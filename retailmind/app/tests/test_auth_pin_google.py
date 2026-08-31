"""
Tests del endurecimiento del PIN 2FA (doble envío, intentos, reenvío) y del
ingreso con Google.
"""
from unittest.mock import patch

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

from .factories import crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user

Usuario = get_user_model()

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'
LOCMEM_EMAIL = 'django.core.mail.backends.locmem.EmailBackend'

AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST, EMAIL_BACKEND=LOCMEM_EMAIL)
class Pin2FADobleEnvioTest(TestCase):
    """El bug reportado: apretar "Verificar código" varias veces daba error."""

    def setUp(self):
        self.client = Client()
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa)
        self.user = crear_usuario(
            username='admin2fa',
            password='SecurePass123!',
            email='admin2fa@test.com',
            rol='administrador',
            requiere_2fa=True,
        )
        crear_empresa_user(self.user, self.empresa, self.sucursal)

    def _pedir_pin(self):
        self.client.post(reverse('login'), {
            'email': 'admin2fa@test.com',
            'password-input': 'SecurePass123!',
        })
        self.user.refresh_from_db()
        return self.user.codigo_2fa

    def test_segundo_post_con_el_mismo_pin_no_da_error(self):
        """El segundo click llega cuando el primero ya autenticó: debe entrar."""
        codigo = self._pedir_pin()

        primera = self.client.post(reverse('login_2fa'), {'pin': codigo})
        self.assertEqual(primera.status_code, 302)
        self.assertNotIn('pending_2fa_user_id', self.client.session)

        segunda = self.client.post(reverse('login_2fa'), {'pin': codigo})
        self.assertEqual(segunda.status_code, 302)
        # Antes redirigía al login con "La verificación de PIN no está activa".
        self.assertNotEqual(segunda['Location'], reverse('login'))

    def test_segundo_post_ajax_responde_ok(self):
        codigo = self._pedir_pin()
        self.client.post(reverse('login_2fa'), {'pin': codigo}, **AJAX)

        segunda = self.client.post(reverse('login_2fa'), {'pin': codigo}, **AJAX)
        self.assertEqual(segunda.status_code, 200)
        datos = segunda.json()
        self.assertTrue(datos['ok'])
        self.assertTrue(datos['redirect'])

    def test_ajax_pin_correcto_responde_redirect(self):
        codigo = self._pedir_pin()
        respuesta = self.client.post(reverse('login_2fa'), {'pin': codigo}, **AJAX)
        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.json()
        self.assertTrue(datos['ok'])
        self.assertIn('_auth_user_id', self.client.session)

    def test_pin_se_consume_tras_usarlo(self):
        """En modo 'session' el PIN es de un solo uso."""
        codigo = self._pedir_pin()
        self.client.post(reverse('login_2fa'), {'pin': codigo})

        self.user.refresh_from_db()
        self.assertIsNone(self.user.codigo_2fa)

    def test_pin_acepta_espacios_y_guiones(self):
        codigo = self._pedir_pin()
        con_ruido = '{} {}-{}'.format(codigo[:2], codigo[2:4], codigo[4:])

        respuesta = self.client.post(reverse('login_2fa'), {'pin': con_ruido}, **AJAX)
        self.assertTrue(respuesta.json()['ok'])


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST, EMAIL_BACKEND=LOCMEM_EMAIL)
class Pin2FAIntentosTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa)
        self.user = crear_usuario(
            username='admin2fa',
            password='SecurePass123!',
            email='admin2fa@test.com',
            rol='administrador',
            requiere_2fa=True,
        )
        crear_empresa_user(self.user, self.empresa, self.sucursal)
        self.client.post(reverse('login'), {
            'email': 'admin2fa@test.com',
            'password-input': 'SecurePass123!',
        })

    @override_settings(PIN_2FA_MAX_INTENTOS=3)
    def test_intentos_se_agotan_e_invalidan_el_pin(self):
        for _ in range(2):
            respuesta = self.client.post(reverse('login_2fa'), {'pin': '000000'}, **AJAX)
            self.assertFalse(respuesta.json()['ok'])
            self.assertFalse(respuesta.json().get('expirado'))

        ultima = self.client.post(reverse('login_2fa'), {'pin': '000000'}, **AJAX)
        datos = ultima.json()
        self.assertFalse(datos['ok'])
        self.assertTrue(datos['expirado'])
        self.assertEqual(datos['redirect'], reverse('login'))

        self.user.refresh_from_db()
        self.assertIsNone(self.user.codigo_2fa)
        self.assertNotIn('pending_2fa_user_id', self.client.session)

    @override_settings(PIN_2FA_MAX_INTENTOS=5)
    def test_informa_intentos_restantes(self):
        respuesta = self.client.post(reverse('login_2fa'), {'pin': '000000'}, **AJAX)
        self.assertEqual(respuesta.json()['intentos_restantes'], 4)

    def test_reenvio_respeta_cooldown(self):
        respuesta = self.client.post(reverse('login_2fa_resend'), **AJAX)
        datos = respuesta.json()
        self.assertFalse(datos['ok'])
        self.assertGreater(datos['espera'], 0)

    @override_settings(PIN_2FA_REENVIO_COOLDOWN=0)
    def test_reenvio_genera_pin_nuevo(self):
        self.user.refresh_from_db()
        anterior = self.user.codigo_2fa

        respuesta = self.client.post(reverse('login_2fa_resend'), **AJAX)
        self.assertTrue(respuesta.json()['ok'])

        self.user.refresh_from_db()
        self.assertNotEqual(self.user.codigo_2fa, anterior)

    def test_reenvio_sin_verificacion_activa(self):
        limpio = Client()
        respuesta = limpio.post(reverse('login_2fa_resend'), **AJAX)
        datos = respuesta.json()
        self.assertFalse(datos['ok'])
        self.assertTrue(datos['expirado'])


@override_settings(
    STATICFILES_STORAGE=STATICFILES_STORAGE_TEST,
    GOOGLE_OAUTH_CLIENT_ID='clientdeprueba.apps.googleusercontent.com',
)
class LoginGoogleTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa)
        self.user = crear_usuario(
            username='cajero1',
            password='SecurePass123!',
            email='cajero1@test.com',
            rol='cajero',
        )
        crear_empresa_user(self.user, self.empresa, self.sucursal)

    def _payload(self, **extra):
        datos = {
            'aud': 'clientdeprueba.apps.googleusercontent.com',
            'iss': 'https://accounts.google.com',
            'exp': int(timezone.now().timestamp()) + 3600,
            'email': 'cajero1@test.com',
            'email_verified': True,
        }
        datos.update(extra)
        return datos

    def _post(self, payload):
        objetivo = 'retailmind.views._verificar_id_token_google'
        with patch(objetivo, return_value=payload):
            return self.client.post(
                reverse('login_google'), {'credential': 'token-falso'}, **AJAX
            )

    def test_boton_google_aparece_en_el_login(self):
        respuesta = self.client.get(reverse('login'))
        self.assertContains(respuesta, 'clientdeprueba.apps.googleusercontent.com')
        self.assertContains(respuesta, 'handleGoogleCredential')

    @override_settings(GOOGLE_OAUTH_CLIENT_ID='')
    def test_boton_google_oculto_sin_client_id(self):
        respuesta = self.client.get(reverse('login'))
        self.assertNotContains(respuesta, 'handleGoogleCredential')

    def test_login_google_exitoso(self):
        respuesta = self._post(self._payload())
        self.assertEqual(respuesta.status_code, 200)
        datos = respuesta.json()
        self.assertTrue(datos['ok'])
        self.assertIn('_auth_user_id', self.client.session)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.id)

    def test_login_google_correo_desconocido(self):
        respuesta = self._post(self._payload(email='nadie@test.com'))
        datos = respuesta.json()
        self.assertFalse(datos['ok'])
        self.assertIn('nadie@test.com', datos['error'])
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_google_usuario_desactivado(self):
        self.user.es_activo = False
        self.user.save(update_fields=['es_activo'])

        respuesta = self._post(self._payload())
        datos = respuesta.json()
        self.assertFalse(datos['ok'])
        self.assertIn('desactivada', datos['error'])
        self.assertNotIn('_auth_user_id', self.client.session)

    @override_settings(GOOGLE_OAUTH_ALLOWED_DOMAINS=['miempresa.cl'])
    def test_login_google_dominio_no_autorizado(self):
        respuesta = self._post(self._payload())
        datos = respuesta.json()
        self.assertFalse(datos['ok'])
        self.assertIn('dominio', datos['error'])

    def test_login_google_sin_credencial(self):
        respuesta = self.client.post(reverse('login_google'), {}, **AJAX)
        self.assertFalse(respuesta.json()['ok'])

    def test_login_google_solo_post(self):
        respuesta = self.client.get(reverse('login_google'))
        self.assertEqual(respuesta.status_code, 405)

    @override_settings(GOOGLE_OAUTH_BYPASS_2FA=False, EMAIL_BACKEND=LOCMEM_EMAIL)
    def test_google_puede_exigir_pin_si_se_configura(self):
        self.user.requiere_2fa = True
        self.user.save(update_fields=['requiere_2fa'])

        respuesta = self._post(self._payload())
        datos = respuesta.json()
        self.assertTrue(datos['ok'])
        self.assertEqual(datos['redirect'], reverse('login_2fa'))
        self.assertIn('pending_2fa_user_id', self.client.session)
        self.assertNotIn('_auth_user_id', self.client.session)

    @override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
    def test_google_omite_el_pin_por_defecto(self):
        self.user.requiere_2fa = True
        self.user.save(update_fields=['requiere_2fa'])

        respuesta = self._post(self._payload())
        self.assertTrue(respuesta.json()['ok'])
        self.assertIn('_auth_user_id', self.client.session)


@override_settings(
    STATICFILES_STORAGE=STATICFILES_STORAGE_TEST,
    GOOGLE_OAUTH_CLIENT_ID='clientdeprueba.apps.googleusercontent.com',
)
class VerificacionTokenGoogleTest(TestCase):
    """Validaciones sobre el ID token, sin salir a la red.

    Se ejercita la rama de respaldo (endpoint `tokeninfo` vía `requests`), que
    es la que corre cuando `google-auth` no está instalado en el servidor.
    """

    def _verificar(self, payload):
        from retailmind.views import _verificar_id_token_google

        respuesta = type('RespuestaFalsa', (), {
            'status_code': 200,
            'json': lambda self: payload,
        })()

        with patch('requests.get', return_value=respuesta):
            return _verificar_id_token_google('token-falso')

    def test_usa_tokeninfo_si_falta_google_auth(self):
        datos = self._verificar(self._base())
        self.assertEqual(datos['email'], 'cajero1@test.com')

    def test_rechaza_respuesta_no_200_de_google(self):
        from retailmind.views import _verificar_id_token_google

        respuesta = type('RespuestaFalsa', (), {
            'status_code': 400,
            'json': lambda self: {},
        })()

        with patch('requests.get', return_value=respuesta):
            with self.assertRaises(ValueError):
                _verificar_id_token_google('token-falso')

    def test_acepta_email_verified_como_string(self):
        """`tokeninfo` devuelve los booleanos como texto."""
        datos = self._verificar(self._base(email_verified='true'))
        self.assertEqual(datos['email'], 'cajero1@test.com')

    def _base(self, **extra):
        datos = {
            'aud': 'clientdeprueba.apps.googleusercontent.com',
            'iss': 'https://accounts.google.com',
            'exp': int(timezone.now().timestamp()) + 3600,
            'email': 'Cajero1@Test.com',
            'email_verified': True,
        }
        datos.update(extra)
        return datos

    def test_normaliza_el_correo_a_minusculas(self):
        datos = self._verificar(self._base())
        self.assertEqual(datos['email'], 'cajero1@test.com')

    def test_rechaza_aud_de_otra_app(self):
        with self.assertRaises(ValueError):
            self._verificar(self._base(aud='otra-app.apps.googleusercontent.com'))

    def test_rechaza_emisor_falso(self):
        with self.assertRaises(ValueError):
            self._verificar(self._base(iss='https://evil.example.com'))

    def test_rechaza_correo_no_verificado(self):
        with self.assertRaises(ValueError):
            self._verificar(self._base(email_verified=False))

    def test_rechaza_token_expirado(self):
        vencido = int(timezone.now().timestamp()) - 60
        with self.assertRaises(ValueError):
            self._verificar(self._base(exp=vencido))

    @override_settings(GOOGLE_OAUTH_CLIENT_ID='')
    def test_sin_client_id_configurado_falla(self):
        from retailmind.views import _verificar_id_token_google

        with self.assertRaises(ValueError):
            _verificar_id_token_google('token-falso')
