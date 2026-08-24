"""
Tests de la FASE D de la auditoría de Reportes (ago-2026) — cierre de la deuda
de la suite de regresión `_test_reportes_readonly.py`.

Los 3 FAIL "de datos" que la suite arrastraba desde julio:

1. `comparativa_mensual / anti_doble_conteo` — era el ORÁCULO el que estaba
   mal, no la vista. El oráculo restaba las NC con
   `.filter(tipo_documento='NOTA DE CREDITO')` sobre un queryset ya acotado a
   `tipo_transaccion='VENTA_PUBLICO'`; una NC de venta nunca lleva ese
   tipo_transaccion, así que ese término valía $0 y el oráculo pedía las
   ventas BRUTAS contra una vista que muestra las NETAS. Acá se prueba, sobre
   un fixture con NC, que el oráculo nuevo (`oraculo_comparativa_mes`) calza
   AL PESO con la vista, que el viejo NO calzaba, y que la garantía F-16
   (tickets con boleta excluidos) sigue viva.
2. `atributo4_poblado` — columna deliberadamente muerta. Se eliminó el cálculo
   y el payload `por_genero` de `obtener_productos_vendidos`; acá se prueba
   que el payload ya no viaja, que los totales no se movieron ni un peso y
   que `genero_id` dejó de ser un filtro (antes vaciaba el reporte entero).
3. `categorias_v12` — cola de datos de la recategorización v1.2, no bug de
   código. `evaluar_categorias_v12` es la función pura que decide PASS/WARN/
   FAIL por % del monto en hijas v1.2; acá se prueban sus 5 ramas.

Correr SOLO contra SQLite:
    $env:DATABASE_URL="sqlite:///C:/temp/td1.sqlite3"
    python manage.py test app.tests.test_fase_d_suite
"""
import importlib.util
import os
import sys
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from app.models import Dte, Dte_Productos, Ticket, Ticket_Productos
from .factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla,
    crear_sucursal, crear_usuario, crear_vendedor,
)


# --------------------------------------------------------------- suite import
# `_test_reportes_readonly.py` vive en retailmind/ (junto a manage.py), fuera
# de cualquier app: se carga por ruta para poder testear sus oráculos y
# evaluadores sin duplicarlos acá (si se duplican, dejan de ser el oráculo que
# realmente corre contra prod).
def _cargar_suite():
    ruta = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        '_test_reportes_readonly.py',
    )
    if 'suite_reportes_readonly' in sys.modules:
        return sys.modules['suite_reportes_readonly']
    spec = importlib.util.spec_from_file_location('suite_reportes_readonly', ruta)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['suite_reportes_readonly'] = mod
    spec.loader.exec_module(mod)
    return mod


SUITE = _cargar_suite()


def _patch_permisos():
    return mock.patch(
        'app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True
    )


def _crear_dte(emisor, receptor, sucursal, numero, monto, tipo_transaccion,
               tipo_documento='BOLETA ELECTRONICA', fecha=None, **kwargs):
    defaults = dict(
        emisor=emisor,
        receptor=receptor,
        numero_documento=numero,
        tipo_documento=tipo_documento,
        monto_neto=Decimal(int(monto / Decimal('1.19'))),
        monto_con_iva=Decimal(monto),
        estado_pago='PAGADO',
        estado_dte='EMITIDO',
        responsable='tester',
        fecha_emision=fecha or timezone.localdate(),
        fecha_vencimiento=fecha or timezone.localdate(),
        diasCredito=0,
        bultos=1,
        unidades_productos=1,
        tipo_transaccion=tipo_transaccion,
        sucursal=sucursal,
        descartado=False,
    )
    defaults.update(kwargs)
    return Dte.objects.create(**defaults)


def _crear_ticket(sucursal, vendedor, total, dte_generado, correlativo,
                  cuando=None, modulo='VENTA_PUBLICO', estado='PAGADO'):
    tk = Ticket.objects.create(
        vendedor=vendedor, sucursal=sucursal, correlativo=correlativo,
        estado=estado, subTotal=total, total=total, responsable='tester',
        modulo_origen=modulo, dte_generado=dte_generado,
    )
    if cuando is not None:  # created_at es auto_now_add
        Ticket.objects.filter(id=tk.id).update(created_at=cuando)
        tk.refresh_from_db()
    return tk


# ============================================================ (1) COMPARATIVA

class ComparativaOraculoTest(TestCase):
    """El oráculo de `anti_doble_conteo` debe replicar el universo REAL del
    gráfico: tickets sin DTE + DTE de venta al público − NC de venta."""

    def setUp(self):
        self.hoy = timezone.localdate()
        self.ahora = timezone.now()
        # Todo dentro del mes en curso: la vista mira los últimos 180 días y
        # bucketea con TruncMonth, así que el mes vivo siempre está en el eje.
        self.fi = self.hoy.replace(day=1)
        self.ff = self.hoy

        self.empresa = crear_empresa(nombre='Empresa D', rut='76.444.444-4')
        self.sucursal = crear_sucursal(self.empresa, alias='TIENDA-D')
        self.vendedor = crear_vendedor('Vendedor D', self.empresa)
        self.vendedor.sucursales.add(self.sucursal)
        self.admin = crear_usuario(username='admin_d', rol='administrador')
        crear_empresa_user(self.admin, self.empresa, self.sucursal)
        self.cliente_emp = crear_empresa(nombre='Cliente D', rut='79.555.555-5')

        # --- SÍ suman ---
        # a) ticket POS sin boleta
        _crear_ticket(self.sucursal, self.vendedor, 100_000, False, 1, self.ahora)
        # b) boleta electrónica de venta al público
        _crear_dte(self.empresa, self.cliente_emp, self.sucursal, 5001,
                   Decimal('500000'), 'VENTA_PUBLICO')
        # --- NO suman (y el oráculo tampoco los puede pedir) ---
        # c) ticket CON boleta: ya está representado en (b) → F-16
        _crear_ticket(self.sucursal, self.vendedor, 500_000, True, 2, self.ahora)
        # d) factura tipo_transaccion='VENTA': el gráfico es de venta al público
        _crear_dte(self.empresa, self.cliente_emp, self.sucursal, 5002,
                   Decimal('700000'), 'VENTA', tipo_documento='FACTURA')
        # e) DTE anulado
        _crear_dte(self.empresa, self.cliente_emp, self.sucursal, 5003,
                   Decimal('900000'), 'VENTA_PUBLICO', estado_dte='ANULADO')
        # f) facturación interna (receptor == emisor)
        _crear_dte(self.empresa, self.empresa, self.sucursal, 5004,
                   Decimal('300000'), 'VENTA_PUBLICO')
        # --- RESTA: nota de crédito real del lado venta ---
        _crear_dte(self.empresa, self.cliente_emp, self.sucursal, 5005,
                   Decimal('80000'), 'ANULACION',
                   tipo_documento='NOTA DE CREDITO')

        self.esperado_neto = 100_000 + 500_000 - 80_000  # 520.000

    def _series_del_mes(self):
        cli = Client()
        cli.force_login(self.admin)
        s = cli.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()
        with _patch_permisos():
            resp = cli.get('/app/api/reportes/comparativa-mensual/')
        self.assertEqual(resp.status_code, 200)
        js = resp.json()
        self.assertTrue(js.get('success'), js)
        label = self.hoy.strftime('%b %Y')
        cats = js['categories']
        self.assertIn(label, cats)
        idx = cats.index(label)
        return sum(float((s_.get('data') or [])[idx]) for s_ in js['series'])

    def test_oraculo_nuevo_calza_al_peso_con_la_vista(self):
        ora = SUITE.oraculo_comparativa_mes(self.fi, self.ff)
        self.assertEqual(ora['tickets_sin_dte'], 100_000)
        self.assertEqual(ora['dte_publico'], 500_000)
        self.assertEqual(ora['nc'], 80_000)
        self.assertEqual(ora['nc_n'], 1)
        self.assertEqual(ora['esperado'], self.esperado_neto)
        self.assertEqual(int(self._series_del_mes()), ora['esperado'])

    def test_el_oraculo_viejo_no_calzaba_por_las_nc(self):
        """Reproduce el bug: pedir las NC con tipo_transaccion='VENTA_PUBLICO'
        devuelve $0 y deja el oráculo en BRUTO."""
        from django.db.models import F, Sum
        base_d = Dte.objects.filter(
            fecha_emision__gte=self.fi, fecha_emision__lte=self.ff,
            tipo_transaccion='VENTA_PUBLICO', sucursal__isnull=False,
        ).exclude(estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO']) \
            .exclude(receptor__isnull=False, receptor_id=F('emisor_id'))
        nc_viejo = base_d.filter(tipo_documento='NOTA DE CREDITO').aggregate(
            m=Sum('monto_con_iva'))['m'] or 0
        self.assertEqual(int(nc_viejo), 0, 'la NC no lleva tt=VENTA_PUBLICO')
        esperado_viejo = 100_000 + 500_000 - int(nc_viejo)
        self.assertEqual(esperado_viejo, 600_000)
        # …y la vista muestra 520.000: exactamente el monto de la NC de delta.
        self.assertEqual(esperado_viejo - int(self._series_del_mes()), 80_000)

    def test_f16_ticket_con_boleta_no_se_cuenta_dos_veces(self):
        dc = SUITE.oraculo_comparativa_doble_conteo(self.fi, self.ff)
        self.assertEqual(dc['tickets_con_dte'], 1)
        self.assertEqual(dc['monto_duplicable'], 500_000)
        total = self._series_del_mes()
        # Si el filtro dte_generado=False se cayera, el mes saldría 1.020.000.
        self.assertEqual(int(total), self.esperado_neto)
        self.assertLess(total, self.esperado_neto + 0.5 * dc['monto_duplicable'])

    def test_facturas_venta_e_internas_fuera_de_ambos_lados(self):
        """La hipótesis 'la vista se come las facturas tt=VENTA' es falsa:
        están fuera del oráculo Y de la vista, por diseño del gráfico."""
        ora = SUITE.oraculo_comparativa_mes(self.fi, self.ff)
        self.assertNotIn(700_000, (ora['dte_publico'], ora['esperado']))
        self.assertEqual(ora['dte_publico'], 500_000)   # sin la interna (300k)
        self.assertEqual(int(self._series_del_mes()), ora['esperado'])


# ================================================== (2) PRODUCTOS VENDIDOS

class ProductosVendidosSinAtributo4Test(TestCase):
    """`por_genero` (atributo4) eliminado: payload fuera, plata intacta."""

    URL = '/app/api/reportes/productos-vendidos/'

    def setUp(self):
        self.hoy = timezone.localdate()
        self.empresa = crear_empresa(nombre='Empresa PV', rut='76.666.666-6')
        self.sucursal = crear_sucursal(self.empresa, alias='TIENDA-PV')
        self.vendedor = crear_vendedor('Vendedor PV', self.empresa)
        self.vendedor.sucursales.add(self.sucursal)
        self.admin = crear_usuario(username='admin_pv', rol='administrador')
        crear_empresa_user(self.admin, self.empresa, self.sucursal)
        self.cliente_emp = crear_empresa(nombre='Cliente PV', rut='79.777.777-7')

        self.prod, self.talla = crear_producto_con_talla(
            self.sucursal, articulo='ART-PV', sku=3000001, stock=20,
        )
        # Venta por ticket sin DTE: 2 u / $40.000
        tk = _crear_ticket(self.sucursal, self.vendedor, 40_000, False, 1,
                           timezone.now())
        Ticket_Productos.objects.create(
            ProductoTalla=self.talla, idTicket=tk, stock=2, precio=20_000,
            subtotal=40_000,
        )
        # Venta por boleta: 3 u / $60.000
        dte = _crear_dte(self.empresa, self.cliente_emp, self.sucursal, 6001,
                         Decimal('60000'), 'VENTA_PUBLICO')
        Dte_Productos.objects.create(
            dte=dte, productoTalla=self.talla, descripcion='ART-PV',
            precio=20_000, precio_unitario=20_000, monto_item=60_000, stock=3,
        )
        self.monto_esperado = 100_000
        self.unidades_esperadas = 5

    def _json(self, **params):
        cli = Client()
        cli.force_login(self.admin)
        s = cli.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()
        base = {'tipo_flujo': 'custom',
                'fecha_inicio': self.hoy.strftime('%Y-%m-%d'),
                'fecha_fin': self.hoy.strftime('%Y-%m-%d')}
        base.update(params)
        with _patch_permisos():
            resp = cli.get(self.URL, base)
        self.assertEqual(resp.status_code, 200)
        js = resp.json()
        self.assertTrue(js.get('success'), js)
        return js

    def test_payload_por_genero_eliminado(self):
        js = self._json()
        self.assertNotIn('por_genero', js)
        self.assertIn('por_sexo', js)
        self.assertIn('por_especialidad', js)

    def test_totales_no_se_movieron(self):
        js = self._json()
        self.assertEqual(js['kpis']['total_monto'], self.monto_esperado)
        self.assertEqual(js['kpis']['total_unidades'], self.unidades_esperadas)
        # las particiones vivas siguen sumando el total
        for dim in ('por_marca', 'por_categoria', 'por_sexo'):
            self.assertEqual(sum(f['monto'] for f in js[dim]),
                             self.monto_esperado, dim)

    def test_filas_de_producto_sin_campo_genero(self):
        js = self._json()
        self.assertTrue(js['productos'])
        for fila in js['productos']:
            self.assertNotIn('genero', fila)
            self.assertNotIn('genero_id', fila)
            self.assertIn('sexo', fila)

    def test_genero_id_ya_no_vacia_el_reporte(self):
        """Antes `genero_id` filtraba por atributo4 (0% poblado) y devolvía un
        reporte en $0. Ahora el parámetro se ignora."""
        js = self._json(genero_id=999999)
        self.assertEqual(js['kpis']['total_monto'], self.monto_esperado)
        self.assertEqual(js['kpis']['total_unidades'], self.unidades_esperadas)


# ==================================================== (3) CATEGORÍAS v1.2

class EvaluarCategoriasV12Test(TestCase):
    """`evaluar_categorias_v12`: PASS / WARN / FAIL sobre el % del MONTO."""

    CENSO = {
        'hijas_ids': {11, 12, 13},
        'planas_vivas_ids': {90, 91},
        'padres_v12': ['Calzado', 'Ropa', 'Accesorios'],
    }

    def _ev(self, filas):
        return SUITE.evaluar_categorias_v12(filas, self.CENSO)

    def test_arbol_limpio_es_pass(self):
        ev = self._ev([{'id': 11, 'nombre': 'Calzado › Zapatillas', 'monto': 800},
                       {'id': 12, 'nombre': 'Ropa › Poleras', 'monto': 200}])
        self.assertIs(ev['ok'], True)
        self.assertEqual(ev['pct_monto_hijas'], 100.0)
        self.assertEqual(ev['n_planas'], 0)

    def test_cola_plana_pequenia_es_warn(self):
        """El caso REAL de prod (jul-2026: 99,87% en hijas, $200.930 de cola)."""
        ev = self._ev([{'id': 11, 'nombre': 'Calzado › Zapatillas', 'monto': 154_928_449},
                       {'id': 90, 'nombre': 'RAMA CASUAL', 'monto': 186_950},
                       {'id': 91, 'nombre': 'RAMA FOOTBALL', 'monto': 13_980}])
        self.assertIsNone(ev['ok'])                       # WARN, no FAIL
        self.assertEqual(ev['n_planas'], 2)
        self.assertGreaterEqual(ev['pct_monto_hijas'], SUITE.UMBRAL_MONTO_HIJAS_V12)
        self.assertIn('cola de recategorización', ev['detalle'])

    def test_plana_grande_es_fail(self):
        ev = self._ev([{'id': 11, 'nombre': 'Calzado › Zapatillas', 'monto': 900},
                       {'id': 90, 'nombre': 'RAMA CASUAL', 'monto': 100}])
        self.assertIs(ev['ok'], False)
        self.assertEqual(ev['pct_monto_hijas'], 90.0)
        self.assertIn('umbral', ev['detalle'])

    def test_categoria_zz_es_fail_aunque_el_monto_alcance(self):
        ev = self._ev([{'id': 11, 'nombre': 'Calzado › Zapatillas', 'monto': 9_990},
                       {'id': 77, 'nombre': '_ZZ_ZAPATILLA VIEJA', 'monto': 10}])
        self.assertIs(ev['ok'], False)
        self.assertEqual(ev['n_zz'], 1)
        self.assertIn('_ZZ_', ev['detalle'])
        self.assertGreaterEqual(ev['pct_monto_hijas'], SUITE.UMBRAL_MONTO_HIJAS_V12)

    def test_sin_filas_es_skip(self):
        ev = self._ev([])
        self.assertEqual(ev['ok'], 'skip')

    def test_umbral_documentado_deja_holgura_sobre_la_cola_real(self):
        """El umbral tiene que estar por DEBAJO de lo medido en prod (99,75%
        el mes vivo) y por encima de una regresión de un dígito porcentual."""
        self.assertLess(SUITE.UMBRAL_MONTO_HIJAS_V12, 99.75)
        self.assertGreaterEqual(SUITE.UMBRAL_MONTO_HIJAS_V12, 95.0)


class CensoCategoriasOraculoTest(TestCase):
    """El censo separa hijas v1.2, planas vivas y _ZZ_ (base del check)."""

    def test_censo_clasifica_las_tres_familias(self):
        from app.models import Categoria, Producto
        padre = Categoria.objects.create(nombre='Calzado D')
        hija = Categoria.objects.create(nombre='Zapatillas D', padre=padre)
        plana = Categoria.objects.create(nombre='RAMA CASUAL D')
        zz = Categoria.objects.create(nombre='_ZZ_VIEJA D')

        empresa = crear_empresa(nombre='Empresa CT', rut='76.888.888-8')
        sucursal = crear_sucursal(empresa, alias='TIENDA-CT')
        for i, cat in enumerate([hija, hija, hija, plana]):
            p, _ = crear_producto_con_talla(
                sucursal, articulo=f'ART-CT-{i}', sku=4000001 + i)
            Producto.objects.filter(id=p.id).update(categoria=cat)

        censo = SUITE.oraculo_censo_categorias()
        self.assertIn(hija.id, censo['hijas_ids'])
        self.assertIn(plana.id, censo['planas_vivas_ids'])
        self.assertNotIn(zz.id, censo['planas_vivas_ids'])
        self.assertNotIn(padre.id, censo['planas_vivas_ids'])
        self.assertGreaterEqual(censo['n_zz'], 1)
