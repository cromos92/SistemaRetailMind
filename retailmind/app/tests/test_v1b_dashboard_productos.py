"""
Dashboard de productos: alcance por empresa (A3-03, unidad V1B).

'Todas' (sin ?sucursal=) dejó de caer a la sucursal de la sesión y pasó a
devolver el holding completo, con costos y márgenes de las 4 empresas, a
cualquiera con `dashboard_productos`. Ahora el universo es SIEMPRE el alcance
del usuario (ids_sucursales_alcance: None = ve todo para administrador, jefe y
maestro) y un ?sucursal= de otra empresa responde 403.

Ejecutar (BD de test aislada, NO producción):
    python manage.py test app.tests.test_v1b_dashboard_productos --keepdb --noinput
"""
import csv
import io

from django.test import TestCase

from app.models import ModuloSistema, OpcionMenu, PermisoRol

from .factories import (
    crear_empresa, crear_empresa_user, crear_lote_fifo, crear_producto_con_talla,
    crear_sucursal, crear_usuario,
)

API = '/app/dashboard_productos_mejorado_api/'
EXPORT = '/app/exportar_dashboard_productos/'


class AlcanceDashboardProductosTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.emp_a = crear_empresa(nombre='Empresa A', rut='76.110.110-1')
        cls.emp_b = crear_empresa(nombre='Empresa B', rut='76.220.220-2')
        cls.suc_a = crear_sucursal(empresa=cls.emp_a, alias='A-1')
        cls.suc_b = crear_sucursal(empresa=cls.emp_b, alias='B-1')
        _, pt_a = crear_producto_con_talla(cls.suc_a, articulo='ART-A', sku=81001, stock=7)
        _, pt_b = crear_producto_con_talla(cls.suc_b, articulo='ART-B', sku=82001, stock=9)
        crear_lote_fifo(pt_a, cantidad=7)
        crear_lote_fifo(pt_b, cantidad=9)

        modulo = ModuloSistema.objects.create(codigo='v1b_dash', nombre='Dash')
        opcion = OpcionMenu.objects.create(
            modulo=modulo, codigo='dashboard_productos', nombre='Dashboard productos', activo=True)
        PermisoRol.objects.create(
            rol='jefe_local', opcion_menu=opcion, puede_ver=True, puede_exportar=True)

        cls.jefe = crear_usuario(username='v1b_dash_jefe', rol='jefe_local')
        crear_empresa_user(cls.jefe, cls.emp_a, cls.suc_a)
        cls.maestro = crear_usuario(username='v1b_dash_maestro', rol='maestro')
        crear_empresa_user(cls.maestro, cls.emp_a, cls.suc_a)

    def entrar(self, user):
        self.client.force_login(user)
        s = self.client.session
        s['idSucursalActual'] = self.suc_a.id
        s['idEmpresaActual'] = self.emp_a.id
        s.save()

    def get_api(self, **params):
        return self.client.get(API, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def articulos(self, data):
        return {p['nombre'] for p in data['productos']}

    def csv_articulos(self, resp):
        contenido = b''.join(resp.streaming_content).decode('utf-8-sig')
        return {fila['Producto'] for fila in csv.DictReader(io.StringIO(contenido))}

    def test_todas_para_jefe_local_queda_en_su_empresa(self):
        self.entrar(self.jefe)
        r = self.get_api(sucursal='', periodo=30)
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertEqual(self.articulos(data), {'ART-A'})
        self.assertEqual(data['kpis']['total_skus'], 1)
        self.assertEqual({s['id'] for s in data['filtros']['sucursales']}, {self.suc_a.id})
        self.assertEqual({s['id'] for s in data['por_sucursal']}, {self.suc_a.id})

    def test_sucursal_de_otra_empresa_403(self):
        self.entrar(self.jefe)
        self.assertEqual(self.get_api(sucursal=self.suc_b.id).status_code, 403)
        r = self.client.get(EXPORT, {'sucursal': self.suc_b.id})
        self.assertEqual(r.status_code, 403)

    def test_sucursal_basura_cae_al_alcance(self):
        self.entrar(self.jefe)
        r = self.get_api(sucursal='abc')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.articulos(r.json()), {'ART-A'})

    def test_export_sin_parametros_no_trae_otra_empresa(self):
        self.entrar(self.jefe)
        r = self.client.get(EXPORT)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.csv_articulos(r), {'ART-A'})

    def test_maestro_sigue_viendo_todo(self):
        self.entrar(self.maestro)
        data = self.get_api(sucursal='', periodo=30).json()
        self.assertEqual(self.articulos(data), {'ART-A', 'ART-B'})
        self.assertEqual(self.csv_articulos(self.client.get(EXPORT)), {'ART-A', 'ART-B'})
