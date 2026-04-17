"""
Tests para autenticación: login, 2FA, sesiones, permisos.
"""
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model

from app.models import EmpresaUser
from .factories import crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user

Usuario = get_user_model()

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class LoginViewTest(TestCase):
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

    def test_login_page_loads(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)

    def test_login_exitoso(self):
        response = self.client.post(reverse('login'), {
            'email': 'cajero1@test.com',
            'password-input': 'SecurePass123!',
        })
        self.assertEqual(response.status_code, 302)

    def test_login_password_incorrecta(self):
        response = self.client.post(reverse('login'), {
            'email': 'cajero1@test.com',
            'password-input': 'WrongPassword',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'incorrecta')

    def test_login_email_inexistente(self):
        response = self.client.post(reverse('login'), {
            'email': 'noexiste@test.com',
            'password-input': 'Whatever123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No existe')

    def test_login_establece_sesion(self):
        self.client.post(reverse('login'), {
            'email': 'cajero1@test.com',
            'password-input': 'SecurePass123!',
        })
        session = self.client.session
        self.assertEqual(session.get('idEmpresaActual'), self.empresa.id)
        self.assertEqual(session.get('idSucursalActual'), self.sucursal.id)
        self.assertEqual(session.get('alias'), self.sucursal.alias)

    def test_login_sin_empresa_asignada(self):
        user_sin_empresa = crear_usuario(
            username='sinempresa', password='Pass123!', email='sin@test.com',
        )
        response = self.client.post(reverse('login'), {
            'email': 'sin@test.com',
            'password-input': 'Pass123!',
        })
        self.assertEqual(response.status_code, 302)

    def test_usuario_autenticado_ve_sesion_activa(self):
        self.client.login(username='cajero1', password='SecurePass123!')
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get('session_active', False))


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class Login2FATest(TestCase):
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

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_login_con_2fa_redirige_a_pin(self):
        response = self.client.post(reverse('login'), {
            'email': 'admin2fa@test.com',
            'password-input': 'SecurePass123!',
        })
        self.assertRedirects(response, reverse('login_2fa'))
        self.assertIn('pending_2fa_user_id', self.client.session)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_2fa_codigo_correcto(self):
        self.client.post(reverse('login'), {
            'email': 'admin2fa@test.com',
            'password-input': 'SecurePass123!',
        })
        self.user.refresh_from_db()
        codigo = self.user.codigo_2fa

        response = self.client.post(reverse('login_2fa'), {'pin': codigo})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('pending_2fa_user_id', self.client.session)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_2fa_codigo_incorrecto(self):
        self.client.post(reverse('login'), {
            'email': 'admin2fa@test.com',
            'password-input': 'SecurePass123!',
        })
        response = self.client.post(reverse('login_2fa'), {'pin': '000000'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'incorrecto')

    def test_2fa_sin_sesion_redirige_a_login(self):
        response = self.client.get(reverse('login_2fa'))
        self.assertRedirects(response, reverse('login'))


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class LogoutViewTest(TestCase):
    def test_logout(self):
        user = crear_usuario()
        self.client.login(username='testuser', password='TestPass123!')
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 200)


class CheckSessionAPITest(TestCase):
    def test_session_autenticada(self):
        user = crear_usuario()
        self.client.login(username='testuser', password='TestPass123!')
        response = self.client.get(reverse('check_session_status'))
        data = response.json()
        self.assertTrue(data['authenticated'])
        self.assertEqual(data['username'], 'testuser')

    def test_session_no_autenticada(self):
        response = self.client.get(reverse('check_session_status'))
        data = response.json()
        self.assertFalse(data['authenticated'])


class CheckLoginMethodAPITest(TestCase):
    """Tests del endpoint que decide si el usuario ingresa con PIN o clave."""

    def setUp(self):
        self.client = Client()
        self.user_clave = crear_usuario(
            username='soloclave',
            password='Pass123!',
            email='soloclave@test.com',
        )
        self.user_pin = crear_usuario(
            username='conpin',
            password='Pass123!',
            email='conpin@test.com',
            requiere_2fa=True,
        )

    def test_email_inexistente_retorna_error(self):
        response = self.client.post(reverse('check_login_method'), {
            'email': 'noexiste@test.com',
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['ok'])
        self.assertFalse(data.get('exists', True))
        self.assertIn('No existe', data['error'])

    def test_usuario_con_clave(self):
        response = self.client.post(reverse('check_login_method'), {
            'email': 'soloclave@test.com',
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertFalse(data['requiere_pin'])

    def test_usuario_con_pin_activado(self):
        response = self.client.post(reverse('check_login_method'), {
            'email': 'conpin@test.com',
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['requiere_pin'])

    def test_email_vacio_retorna_400(self):
        response = self.client.post(reverse('check_login_method'), {'email': ''})
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['ok'])

    def test_solo_post_permitido(self):
        response = self.client.get(reverse('check_login_method'))
        self.assertEqual(response.status_code, 405)

    def test_email_normaliza_a_minusculas(self):
        response = self.client.post(reverse('check_login_method'), {
            'email': 'SoloClave@Test.Com',
        })
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertFalse(data['requiere_pin'])


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class CambioPasswordObligatorioTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa)
        self.user = crear_usuario(
            username='temporal',
            password='123456',
            email='temporal@test.com',
            requiere_cambio_password=True,
        )
        crear_empresa_user(self.user, self.empresa, self.sucursal)

    def test_login_redirige_a_cambio_password(self):
        response = self.client.post(reverse('login'), {
            'email': 'temporal@test.com',
            'password-input': '123456',
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('cambiar', response.url)
