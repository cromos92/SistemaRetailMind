"""
«Ver como…» en Gestión de Permisos: /app/permisos/vista-previa-menu/ renderiza
el menú lateral REAL (layout/menu.html) para un rol o un usuario simulado.

1. Por rol: un usuario ficticio de ese rol; la entrada aparece solo si el rol
   tiene puede_ver (lo mismo que decide la barra).
2. Por usuario: pesan sus permisos individuales y la sucursal simulada
   (PermisoSucursal.habilitado=False la esconde).
3. Maestro ve todo; vendedor no puede consultar la vista previa.
"""
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from app.models import ModuloSistema, OpcionMenu, PermisoRol, PermisoSucursal, PermisoUsuario
from .factories import crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'
URL = '/app/permisos/vista-previa-menu/'


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class VistaPreviaMenuTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='SUC-VP')
        cls.maestro = crear_usuario(username='maestro', rol='maestro')
        cls.vendedor = crear_usuario(username='vende', rol='vendedor')
        cls.cajero = crear_usuario(username='caja', rol='cajero')
        for u in (cls.maestro, cls.vendedor, cls.cajero):
            crear_empresa_user(u, cls.empresa, cls.sucursal)
        mod, _ = ModuloSistema.objects.get_or_create(codigo='existencias', defaults={'nombre': 'Existencias'})
        cls.opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='gestion_producto', defaults={'modulo': mod, 'nombre': 'Gestión Producto'})
        PermisoRol.objects.update_or_create(rol='cajero', opcion_menu=cls.opcion, defaults={'puede_ver': True})
        PermisoRol.objects.update_or_create(rol='vendedor', opcion_menu=cls.opcion, defaults={'puede_ver': False})
        cls.enlace = reverse('verGestionProducto')

    def _get(self, usuario, **params):
        c = Client()
        c.force_login(usuario)
        return c.get(URL, params)

    def test_por_rol_refleja_puede_ver(self):
        html_cajero = self._get(self.maestro, rol='cajero').json()
        self.assertTrue(html_cajero['success'])
        self.assertIn('id="navbar-nav"', html_cajero['html'])
        self.assertIn(self.enlace, html_cajero['html'])
        self.assertEqual(html_cajero['etiqueta'], 'rol Cajero')

        html_vendedor = self._get(self.maestro, rol='vendedor').json()
        self.assertNotIn(self.enlace, html_vendedor['html'])

        self.assertIn(self.enlace, self._get(self.maestro, rol='maestro').json()['html'])
        self.assertFalse(self._get(self.maestro, rol='inventado').json()['success'])

    def test_por_usuario_pesan_overrides_y_sucursal(self):
        # El vendedor no lo ve por rol, pero un permiso individual se lo da…
        PermisoUsuario.objects.create(usuario=self.vendedor, opcion_menu=self.opcion, puede_ver=True)
        data = self._get(self.maestro, usuario_id=self.vendedor.id).json()
        self.assertIn(self.enlace, data['html'])
        self.assertEqual(data['etiqueta'], self.vendedor.get_full_name())
        # …y la sucursal simulada se lo quita.
        PermisoSucursal.objects.create(sucursal=self.sucursal, opcion_menu=self.opcion, habilitado=False)
        data = self._get(self.maestro, usuario_id=self.vendedor.id, sucursal_id=self.sucursal.id).json()
        self.assertNotIn(self.enlace, data['html'])
        self.assertEqual(data['sucursal'], 'SUC-VP')
        # Sin sucursal (0) vuelve a verlo.
        self.assertIn(self.enlace, self._get(self.maestro, usuario_id=self.vendedor.id, sucursal_id=0).json()['html'])
        self.assertEqual(self._get(self.maestro, usuario_id=999999).status_code, 404)

    def test_solo_administradores(self):
        resp = self._get(self.vendedor, rol='cajero')
        self.assertNotEqual(resp.status_code, 200)
