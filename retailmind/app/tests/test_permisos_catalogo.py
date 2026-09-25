"""
Catálogo de permisos (app/permisos_catalogo): obliga a documentar qué controla
cada casillero en cada pantalla.

1. Toda opción activa del sistema (tras migraciones + inicializar_permisos)
   tiene ficha, y toda ficha corresponde a una opción que existe.
2. Todo permiso que el CÓDIGO exige (lo encuentra el escáner estático) está
   descrito en la ficha de esa opción: si alguien agrega un
   @requiere_permiso('x', 'puede_crear') nuevo sin documentarlo, este test falla.
3. Las fichas están bien formadas: textos cortos, casilleros válidos, rutas
   que resuelven, dependencias que existen.
4. La API de la pantalla entrega la ficha con cada opción.
"""
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import Resolver404, resolve

from app.models import OpcionMenu
from app.permisos_catalogo import CATALOGO, MODULOS, TIPOS, descripcion, texto_permiso
from app.permisos_catalogo.escaner import escanear
from app.tests.factories import crear_usuario

CAMPOS_VALIDOS = {'pantalla', 'ruta', 'resumen', 'permisos', 'depende_de', 'notas', 'modulo_catalogo'}


class CatalogoPermisosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('inicializar_permisos', stdout=StringIO())
        cls.activos = {o.codigo: o for o in OpcionMenu.objects.filter(activo=True)}

    def test_todos_los_modulos_cargados(self):
        cargados = {f['modulo_catalogo'] for f in CATALOGO.values()}
        self.assertEqual(cargados, set(MODULOS), 'Falta algún módulo del catálogo (¿error de import?)')

    def test_toda_opcion_activa_tiene_ficha(self):
        faltan = sorted(c for c in self.activos if c not in CATALOGO)
        self.assertEqual(faltan, [], f'Opciones sin ficha en app/permisos_catalogo: {faltan}')

    def test_toda_ficha_corresponde_a_una_opcion(self):
        sobran = sorted(c for c in CATALOGO if c not in self.activos)
        self.assertEqual(sobran, [], f'Fichas de opciones que no existen o están inactivas: {sobran}')

    def test_todo_permiso_exigido_por_el_codigo_esta_documentado(self):
        sin_documentar = []
        for (codigo, tipo), usos in escanear().items():
            if codigo not in self.activos:
                continue  # códigos inexistentes los reporta el diagnóstico, no este test
            if tipo not in (CATALOGO[codigo].get('permisos') or {}):
                donde = usos[0]
                sin_documentar.append(f"{codigo}.{tipo} (p. ej. {donde['archivo']}:{donde['linea']})")
        self.assertEqual(sin_documentar, [],
                         'Permisos que el código exige pero el catálogo no describe:\n  ' + '\n  '.join(sin_documentar))

    def test_fichas_bien_formadas(self):
        errores = []
        for codigo, ficha in CATALOGO.items():
            extra = set(ficha) - CAMPOS_VALIDOS
            if extra:
                errores.append(f'{codigo}: campos desconocidos {sorted(extra)}')
            if not (ficha.get('pantalla') or '').strip():
                errores.append(f'{codigo}: falta pantalla')
            if not (ficha.get('resumen') or '').strip():
                errores.append(f'{codigo}: falta resumen')
            for tipo, texto in (ficha.get('permisos') or {}).items():
                if tipo not in TIPOS:
                    errores.append(f'{codigo}: casillero inválido {tipo}')
                if not (texto or '').strip() or len(texto) > 160:
                    errores.append(f'{codigo}.{tipo}: texto vacío o de más de 160 caracteres')
            for dep in ficha.get('depende_de') or []:
                if dep not in CATALOGO:
                    errores.append(f'{codigo}: depende_de {dep} no existe en el catálogo')
            ruta = (ficha.get('ruta') or '').strip()
            if ruta and '<' not in ruta:
                try:
                    resolve(ruta)
                except Resolver404:
                    errores.append(f'{codigo}: la ruta {ruta} no existe')
        self.assertEqual(errores, [], '\n  '.join([''] + errores))

    def test_descripcion_y_texto(self):
        ficha = descripcion('cuadratura_caja')
        self.assertIsNotNone(ficha)
        self.assertIn('puede_ver', ficha['permisos'])
        self.assertEqual(set(ficha['permisos']) | set(ficha['sin_efecto']), set(TIPOS))
        self.assertTrue(texto_permiso('cuadratura_caja', 'puede_ver'))
        self.assertIsNone(descripcion('no_existe'))


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class CatalogoEnLaApiTest(TestCase):
    def test_obtener_permisos_rol_trae_la_ficha(self):
        call_command('inicializar_permisos', stdout=StringIO())
        c = Client()
        c.force_login(crear_usuario(username='maestro-cat', rol='maestro'))
        r = c.get('/app/permisos/obtener-permisos-rol/?rol=cajero', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200)
        opciones = [op for m in r.json()['modulos'] for op in m['opciones']]
        con_ficha = [op for op in opciones if op.get('catalogo')]
        self.assertEqual(len(con_ficha), len(opciones), 'Toda opción debe llegar con su ficha del catálogo')
        cuadratura = next(op for op in opciones if op['codigo'] == 'cuadratura_caja')
        self.assertIn('resumen', cuadratura['catalogo'])
        self.assertIn('sin_efecto', cuadratura['catalogo'])
