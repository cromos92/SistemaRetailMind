"""
Fase B de la auditoría de Reportes 2026-08 (hallazgo P1-10,
docs/AUDITORIA_REPORTES_2026-08.md sección 3): tres reportes existían en
urls.py sin código de permiso en OpcionMenu, así que el middleware
(fail-open) dejaba pasar a cualquier autenticado:

    reporte_ventas_global    -> /app/reportes/ventas-global/
    reporte_productos_origen -> /app/reportes/productos-origen/
    inteligencia_compra      -> /app/reportes/inteligencia-compra/

Las vistas se decoran con @requiere_permiso (fail-closed), por lo que el
command `inicializar_permisos` DEBE crear los códigos en prod junto con el
deploy o los reportes darán 403 a todos (incidente del 05-ago con
diferencias/tránsito).

Cubre:
1. `call_command('inicializar_permisos')` crea los 3 OpcionMenu en el módulo
   reportes con el url_path correcto, y PermisoRol con puede_ver +
   puede_exportar para administrador, administracion y jefe_local; cajero y
   vendedor quedan SIN fila (fail-closed en PermisoRol.tiene_permiso).
2. Idempotencia: correrlo dos veces no duplica filas y NO pisa un override
   manual hecho entre corridas (get_or_create solo aplica defaults al crear).
3. El mapa del middleware resuelve los 6 paths nuevos (página + API de cada
   reporte) al código correcto, sin tapar rutas vecinas no mapeadas
   (plan-liquidación comparte prefijos y tiene otro permiso).
"""
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from app.middleware_permisos import PermisosMenuMiddleware
from app.models import OpcionMenu, PermisoRol

CODIGOS_NUEVOS = {
    'reporte_ventas_global': '/app/reportes/ventas-global/',
    'reporte_productos_origen': '/app/reportes/productos-origen/',
    'inteligencia_compra': '/app/reportes/inteligencia-compra/',
}

ROLES_CON_ACCESO = ('administrador', 'administracion', 'jefe_local')
ROLES_SIN_ACCESO = ('cajero', 'vendedor')


def _correr_command():
    call_command('inicializar_permisos', stdout=StringIO())


class InicializarPermisosFaseBTests(TestCase):
    """El command crea los 3 códigos nuevos con los roles correctos."""

    def test_crea_opciones_menu_en_modulo_reportes(self):
        _correr_command()
        for codigo, url_path in CODIGOS_NUEVOS.items():
            opcion = OpcionMenu.objects.get(codigo=codigo)
            self.assertEqual(opcion.modulo.codigo, 'reportes',
                             f'{codigo} debe colgar del módulo reportes')
            self.assertEqual(opcion.url_path, url_path)
            self.assertIsNone(opcion.url_name)
            self.assertTrue(opcion.activo)

    def test_roles_con_acceso_tienen_ver_y_exportar(self):
        _correr_command()
        for codigo in CODIGOS_NUEVOS:
            for rol in ROLES_CON_ACCESO:
                permiso = PermisoRol.objects.get(
                    rol=rol, opcion_menu__codigo=codigo)
                self.assertTrue(permiso.puede_ver,
                                f'{rol} debe poder ver {codigo}')
                self.assertTrue(permiso.puede_exportar,
                                f'{rol} debe poder exportar {codigo}')
            # Ningún rol con acceso puede eliminar salvo administrador
            # (default global del command para TODO el menú).
            self.assertFalse(PermisoRol.objects.get(
                rol='jefe_local', opcion_menu__codigo=codigo).puede_eliminar)

    def test_cajero_y_vendedor_sin_fila_fail_closed(self):
        _correr_command()
        for codigo in CODIGOS_NUEVOS:
            for rol in ROLES_SIN_ACCESO:
                self.assertFalse(
                    PermisoRol.objects.filter(
                        rol=rol, opcion_menu__codigo=codigo).exists(),
                    f'{rol} NO debe tener fila para {codigo} (fail-closed)')

    def test_tiene_permiso_fail_closed_para_vendedor(self):
        """Sin fila PermisoRol, tiene_permiso devuelve False (no fail-open)."""
        from app.tests.factories import crear_usuario
        _correr_command()
        vendedor = crear_usuario(username='vend_faseb', rol='vendedor')
        jefe = crear_usuario(username='jefe_faseb', rol='jefe_local')
        for codigo in CODIGOS_NUEVOS:
            self.assertFalse(PermisoRol.tiene_permiso(
                usuario=vendedor, codigo_opcion=codigo,
                tipo_permiso='puede_ver'))
            self.assertTrue(PermisoRol.tiene_permiso(
                usuario=jefe, codigo_opcion=codigo,
                tipo_permiso='puede_ver'))


class InicializarPermisosIdempotenciaTests(TestCase):
    """Correr el command dos veces no duplica ni pisa overrides manuales."""

    def test_segunda_corrida_no_duplica(self):
        _correr_command()
        opciones_antes = OpcionMenu.objects.count()
        permisos_antes = PermisoRol.objects.count()
        _correr_command()
        self.assertEqual(OpcionMenu.objects.count(), opciones_antes)
        self.assertEqual(PermisoRol.objects.count(), permisos_antes)
        for codigo in CODIGOS_NUEVOS:
            self.assertEqual(
                OpcionMenu.objects.filter(codigo=codigo).count(), 1)
            self.assertEqual(
                PermisoRol.objects.filter(
                    opcion_menu__codigo=codigo).count(),
                len(ROLES_CON_ACCESO),
                f'{codigo}: solo {ROLES_CON_ACCESO} deben tener fila')

    def test_no_pisa_override_manual_de_permiso(self):
        """Un admin le quitó el reporte a jefe_local a mano: la re-corrida
        del command (p. ej. tras el próximo deploy) NO debe devolvérselo."""
        _correr_command()
        override = PermisoRol.objects.get(
            rol='jefe_local',
            opcion_menu__codigo='reporte_ventas_global')
        override.puede_ver = False
        override.puede_exportar = False
        override.save(update_fields=['puede_ver', 'puede_exportar'])

        _correr_command()

        override.refresh_from_db()
        self.assertFalse(override.puede_ver,
                         'la 2ª corrida pisó el override manual de puede_ver')
        self.assertFalse(override.puede_exportar,
                         'la 2ª corrida pisó el override de puede_exportar')

    def test_no_pisa_edicion_manual_de_opcion_menu(self):
        _correr_command()
        opcion = OpcionMenu.objects.get(codigo='inteligencia_compra')
        opcion.nombre = 'Inteligencia de Compra (beta)'
        opcion.save(update_fields=['nombre'])
        _correr_command()
        opcion.refresh_from_db()
        self.assertEqual(opcion.nombre, 'Inteligencia de Compra (beta)')


class MiddlewareMapaFaseBTests(SimpleTestCase):
    """obtener_codigo_opcion resuelve los 6 paths nuevos (página + API)."""

    def setUp(self):
        self.middleware = PermisosMenuMiddleware(lambda request: None)

    def test_paths_nuevos_resuelven_al_codigo_correcto(self):
        casos = {
            '/app/reportes/ventas-global/': 'reporte_ventas_global',
            '/app/api/reportes/ventas-global-empresa/': 'reporte_ventas_global',
            '/app/reportes/productos-origen/': 'reporte_productos_origen',
            '/app/api/reportes/productos-origen/': 'reporte_productos_origen',
            '/app/reportes/inteligencia-compra/': 'inteligencia_compra',
            '/app/api/inteligencia-compra/': 'inteligencia_compra',
        }
        for path, codigo in casos.items():
            self.assertEqual(
                self.middleware.obtener_codigo_opcion(path), codigo,
                f'{path} debe resolver a {codigo}')

    def test_querystring_y_subpaths_tambien_matchean(self):
        # El middleware matchea por substring de request.path; los filtros
        # van por querystring y no afectan el match.
        self.assertEqual(
            self.middleware.obtener_codigo_opcion(
                '/app/api/reportes/ventas-global-empresa/'),
            'reporte_ventas_global')

    def test_no_tapa_rutas_vecinas_sin_mapear(self):
        """plan-liquidación comparte prefijos con inteligencia-compra pero es
        otro permiso: sus rutas NO deben resolver a los códigos nuevos (hoy
        siguen sin mapear; las cubre otra fase)."""
        for path in ('/app/reportes/plan-liquidacion/',
                     '/app/api/plan-liquidacion/'):
            self.assertNotIn(
                self.middleware.obtener_codigo_opcion(path),
                set(CODIGOS_NUEVOS),
                f'{path} no debe quedar gateado por los códigos de Fase B')

    def test_vecinos_existentes_no_cambian(self):
        # Sanidad: las claves nuevas no desplazan a los reportes vecinos.
        self.assertEqual(
            self.middleware.obtener_codigo_opcion(
                '/app/reportes/ventas-internet/'),
            'reporte_ventas_internet')
        self.assertEqual(
            self.middleware.obtener_codigo_opcion(
                '/app/reportes/rendimiento-proveedor/'),
            'reporte_rendimiento_proveedor')
