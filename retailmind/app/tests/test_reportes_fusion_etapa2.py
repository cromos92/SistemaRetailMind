"""
Fusión de reportes, segunda etapa (sep-2026). Ver docs/FAMILIAS_REPORTES_2026-09.md §7.

1. Ingresos por Proveedor vive en /app/reportes/ingresos-proveedor/ (mismo nombre
   de URL); la ruta vieja redirige conservando los filtros.
2. El home legacy (views.verHome, /app/home-legacy/) ya no existe.
3. La tira de familia agrupa en pestañas los reportes fusionados (Ventas,
   Stock, Cadena logística) sin perder el gating por permisos.
4. El menú muestra una sola entrada por grupo, a la primera pestaña permitida.
5. Los reportes con filtros compartidos los leen de la URL y la actualizan
   (window.FiltrosReporte), salvo Mercadería en tránsito, que no tiene
   período ni sucursal compartida y deja la URL como llegó.
"""
import os
import re
from io import StringIO

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse

from app import views
from app.tests.factories import (
    crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario, otorgar_ver_pantalla,
)

TEMPLATES = os.path.join(settings.BASE_DIR, 'app', 'templates', 'vistas')

# Reportes que leen y reflejan los filtros compartidos (paso 0 de la fusión).
REPORTES_CON_FILTROS = [
    'modulo_reportes/reporte_ventas_sucursal.html',
    'modulo_reportes/reporte_ventas_comparativo.html',
    'modulo_reportes/reporte_ventas_global.html',
    'modulo_reportes/reporte_ventas_internet.html',
    'modulo_reportes/reporte_productos_vendidos.html',
    'modulo_reportes/documentos_emitidos.html',
    'reporte_existencias.html',
    'modulo_reportes/reporte_existencias_marca.html',
    'modulo_reportes/reporte_existencias_sucursal.html',
    'modulo_reportes/resumen_existencias.html',
    'modulo_reportes/reporte_movimientos_sucursal.html',
    'modulo_reportes/reporte_quiebre_talla.html',
    'modulo_reportes/plan_liquidacion.html',
    'modulo_reportes/reporte_despachos_tiendas.html',
    'modulo_reportes/reporte_diferencias_recepcion.html',
    'modulo_reportes/reporte_compras.html',
    'modulo_reportes/reporte_rendimiento_proveedor.html',
    'modulo_reportes/reporte_ingresos_proveedor.html',
    'modulo_reportes/productos_por_origen.html',
    'modulo_reportes/inteligencia_compra.html',
]


class IngresosProveedorRutaTest(TestCase):

    def test_nombre_de_url_apunta_a_la_ruta_nueva(self):
        self.assertEqual(reverse('verReporteDespachosProveedor'), '/app/reportes/ingresos-proveedor/')
        self.assertIs(resolve('/app/reportes/ingresos-proveedor/').func, views.verReporteDespachosProveedor)

    def test_ruta_vieja_redirige_con_filtros(self):
        maestro = crear_usuario(username='e2_maestro', rol='maestro')
        c = Client()
        c.force_login(maestro)
        r = c.get('/app/verReporteDespachosProveedor/?fecha_inicio=2026-05-01&fecha_fin=2026-05-31')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r['Location'],
                         '/app/reportes/ingresos-proveedor/?fecha_inicio=2026-05-01&fecha_fin=2026-05-31')


class HomeLegacyRetiradoTest(TestCase):

    def test_sin_vista_ni_ruta(self):
        self.assertFalse(hasattr(views, 'verHome'))
        maestro = crear_usuario(username='e2_maestro_home', rol='maestro')
        c = Client()
        c.force_login(maestro)
        self.assertEqual(c.get('/app/home-legacy/').status_code, 404)
        # El nombre 'verHome' sigue siendo el home vigente (dashboard_home).
        self.assertEqual(reverse('verHome'), '/app/home/')


class TiraGruposTest(TestCase):

    def _render(self, user, familia, activo):
        request = RequestFactory().get('/app/reportes/ventas-sucursal/')
        request.user = user
        request.session = SessionStore()
        return render_to_string('vistas/modulo_reportes/_familia_reportes.html',
                                {'familia': familia, 'activo': activo}, request=request)

    def _grupo(self, html):
        m = re.search(r'<div class="familia-grupo".*?</div>', html, re.S)
        return m.group(0) if m else ''

    def test_maestro_ve_los_tres_grupos_completos(self):
        maestro = crear_usuario(username='e2_maestro_tira', rol='maestro')
        casos = {
            ('ventas', 'global'): ('ver_reporte_ventas_sucursal', 'ver_reporte_ventas_comparativo',
                                   'ver_reporte_ventas_global'),
            ('existencias', 'resumen'): ('ver_reporte_existencias_marca', 'ver_reporte_existencias_sucursal',
                                         'ver_resumen_existencias'),
            ('logistica', 'transito'): ('ver_reporte_despachos_tiendas', 'ver_reporte_mercaderia_transito',
                                        'ver_reporte_diferencias_recepcion'),
        }
        for (familia, activo), nombres in casos.items():
            with self.subTest(familia=familia):
                grupo = self._grupo(self._render(maestro, familia, activo))
                self.assertTrue(grupo, familia)
                for nombre in nombres:
                    self.assertIn(f'href="{reverse(nombre)}"', grupo, nombre)
        # Los reportes fuera del grupo quedan fuera del bloque de pestañas.
        grupo = self._grupo(self._render(maestro, 'ventas', 'global'))
        self.assertNotIn(reverse('ver_reporte_ventas_internet'), grupo)

    def test_grupo_respeta_permisos(self):
        cajero = crear_usuario(username='e2_cajero_tira', rol='cajero')
        otorgar_ver_pantalla('cajero', 'reporte_ventas_comparativo')
        html = self._render(cajero, 'ventas', 'documentos')
        grupo = self._grupo(html)
        self.assertIn(reverse('ver_reporte_ventas_comparativo'), grupo)
        self.assertNotIn(reverse('ver_reporte_ventas_sucursal'), html)
        self.assertNotIn(reverse('ver_reporte_ventas_global'), html)
        # Sin ninguna pestaña visible no se pinta el bloque.
        vendedor = crear_usuario(username='e2_vendedor_tira', rol='vendedor')
        self.assertEqual(self._grupo(self._render(vendedor, 'ventas', 'documentos')), '')

    def test_helper_de_filtros_compartidos(self):
        maestro = crear_usuario(username='e2_maestro_helper', rol='maestro')
        html = self._render(maestro, 'compras', 'compras')
        for pieza in ('window.FiltrosReporte', 'reflejar: reflejar', 'completar: completar',
                      "var TIEMPO = ['fecha_inicio', 'fecha_fin', 'mes', 'anio'];"):
            self.assertIn(pieza, html)


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class MenuGruposTest(TestCase):

    def setUp(self):
        call_command('inicializar_permisos', stdout=StringIO())
        self.empresa = crear_empresa(nombre='Empresa E2', rut='76.555.444-3')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='E2-TIENDA')

    def _menu_reportes(self, user):
        crear_empresa_user(user, self.empresa, self.sucursal)
        c = Client()
        c.force_login(user)
        s = c.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s['alias'] = self.sucursal.alias
        s.save()
        html = c.get(reverse('bienvenida')).content.decode('utf-8')
        ini = html.find('id="moduloReportes"')
        fin = html.find('MÓDULO CONFIGURACIÓN', ini)
        return html[ini:fin] if ini >= 0 else ''

    def test_una_entrada_por_grupo_a_la_primera_pestana_permitida(self):
        from app.models import PermisoRol
        # Partir de cero: inicializar_permisos puede dejar permisos por defecto al rol.
        PermisoRol.objects.filter(rol='cajero').delete()
        otorgar_ver_pantalla('cajero', 'reporte_ventas_comparativo', 'reporte_ventas_global',
                             'resumen_existencias', 'reporte_diferencias_recepcion')
        cajero = crear_usuario(username='e2_cajero_menu', rol='cajero')
        menu = self._menu_reportes(cajero)
        self.assertTrue(menu)
        # Ventas: sin permiso de "por sucursal", la entrada abre Comparativo.
        self.assertIn(f'href="{reverse("ver_reporte_ventas_comparativo")}"', menu)
        self.assertNotIn(f'href="{reverse("ver_reporte_ventas_global")}"', menu)
        self.assertIn('sucursal · comparativo · global', menu)
        # Stock: solo resumen permitido.
        self.assertIn(f'href="{reverse("ver_resumen_existencias")}"', menu)
        self.assertIn('marca · sucursal · resumen', menu)
        # Logística: solo diferencias permitido.
        self.assertIn('id="reportesLogistica"', menu)
        self.assertIn(f'href="{reverse("ver_reporte_diferencias_recepcion")}"', menu)
        self.assertNotIn('Comparativo Ventas', menu)


class ReportesLeenFiltrosDeLaUrlTest(TestCase):

    def test_cada_reporte_lee_y_refleja(self):
        for rel in REPORTES_CON_FILTROS:
            with self.subTest(template=rel):
                with open(os.path.join(TEMPLATES, rel), encoding='utf-8') as f:
                    src = f.read()
                # Algunos templates guardan el helper en un alias (const FR = window.FiltrosReporte).
                self.assertIn('FiltrosReporte', src)
                self.assertRegex(src, r'\.leer\(\)')
                self.assertRegex(src, r'\.reflejar\(')

    def test_transito_no_reescribe_la_url(self):
        with open(os.path.join(TEMPLATES, 'modulo_reportes/reporte_mercaderia_transito.html'),
                  encoding='utf-8') as f:
            src = f.read()
        self.assertNotRegex(src, r'\.reflejar\(')
