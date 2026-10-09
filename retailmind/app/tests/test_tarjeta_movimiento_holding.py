"""
Tarjeta de movimiento (`/app/tarjeta-movimiento/`) con DOS alcances.

Un mismo artículo vive como una ficha `Producto` por sucursal, en varias
empresas del holding y con el mismo SKU por talla. La tarjeta separa:

  · STOCK (matriz talla × sucursal `distribucion`, ficha `stock_total` /
    `bodegas` / `empresas`, `resumen.stock_actual`, autocompletado y lista de
    candidatos cuando un SKU es ambiguo): TODO el holding, para cualquier
    usuario con al menos una empresa.
  · HISTORIAL (kardex, aperturas, llegadas, línea de vida, totales, costo de la
    ficha, proveedores y `filtros.bodegas`): SOLO las sucursales de las
    empresas del usuario (`ids_sucursales_alcance`; None = ve todo).

Lo que se prueba:
  1. Usuario de UNA empresa con un artículo repartido en las dos: ve el stock
     de todas las bodegas, pero historial y costo solo de las suyas.
  2. Artículo que su empresa no trabaja: 200, sin historial y sin costo.
  3. Maestro / Administrador / Jefe / flag `puede_ver_todas_sucursales` y un
     usuario con las dos empresas asignadas: historial completo.
  4. Sin ninguna empresa vigente: 403 (ni siquiera stock).
  5. Resolución del artículo: primero en sus empresas, luego en el holding;
     ambiguo → candidatos; `?articulo=` abre el elegido.
  6. Autocompletado: todo el holding, varias palabras con Y, stock y bodegas
     del artículo completo, un resultado por artículo.

NO se cubre "variante con producto.sucursal = NULL": `Producto.sucursal` es
NOT NULL (modelo y migración 0001), así que esa fila no puede existir. Ver
`TestProductoSinSucursal`, que avisa si eso cambia.

Ejecutar en BD aislada (NUNCA sin DATABASE_URL: el .env apunta a producción):
    PYTHONDONTWRITEBYTECODE=1 DATABASE_URL='sqlite://:memory:' \
        ../venv/Scripts/python.exe manage.py test app.tests.test_tarjeta_movimiento_holding --noinput -v 2
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from app.models import (
    AtributoOpcion, Categoria, Dte, ModuloSistema, Movimientos_Producto,
    OpcionMenu, PermisoUsuario, Producto, Producto_Talla, Productos_Atributos,
)
from app.tests.factories import (
    crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario,
    otorgar_ver_pantalla,
)
from app.views_modulo_existencias_nuevo import (
    api_buscar_productos_tarjeta_movimiento, api_tarjeta_movimiento,
)


# Artículo repartido en las dos empresas, con el mismo SKU por talla en todas
# sus bodegas (como F35542 en EDEL, PAO y NICK).
ART = 'TM-ADI-001'
SKU_38, SKU_40, SKU_42 = 7901038, 7901040, 7901042
# Artículo que solo trabaja la empresa B.
ART_SOLO_B, SKU_SOLO_B = 'TM-SOLO-B', 7902039
# Mismo número: en A es TM-PROPIO; en B, un artículo legado distinto (TM-LEGADO).
SKU_PROPIO = 7903041
# Ambiguo DENTRO de A (TM-DUP-1 y TM-DUP-2); TM-DUP-2 además tiene stock en B.
SKU_DUP = 7904001
# Ambiguo que solo existe en B (TM-EDEL-1 y TM-EDEL-2).
SKU_EDEL = 7905000
# Dos códigos que solo difieren en mayúsculas: TM-CASO (A) y tm-caso (B).
SKU_CASO = 7906000

# Cifras del artículo ART que se repiten en varias pruebas.
STOCK_HOLDING = 22    # PAO1 3 + PAO2 3 + NICK1 5 + NICK2 11 + IMP 0
STOCK_EMPRESA_A = 6   # PAO1 3 + PAO2 3
STOCK_EMPRESA_B = 16  # NICK1 5 + NICK2 11 + IMP 0
COSTO_A, COSTO_B = 10000, 12000
PRECIO_A, PRECIO_B = 29990, 31990

ALIAS_A = {'PAO1', 'PAO2'}
ALIAS_B = {'NICK1', 'NICK2', 'IMP'}
EMPRESA_A, EMPRESA_B = 'Paola Test', 'Importadora Test'
# Proveedores: el de cada empresa es privado de ella.
PROVEEDOR_A, PROVEEDOR_B = 'Proveedor Alfa', 'Proveedor Beta'


def _ficha(sucursal, articulo, tallas, costo, precio, descripcion, marca=None, categoria=None):
    """
    Una ficha Producto (artículo × sucursal) con una variante por talla.
    `tallas` = [(talla, sku, stock), ...]. Devuelve {talla: Producto_Talla}.
    """
    producto = Producto.objects.create(
        articulo=articulo, descripcion=descripcion, sucursal=sucursal,
        costo=costo, sobreprecio=0, precioventa=precio,
        atributo1=marca, categoria=categoria,
    )
    return {
        talla: Producto_Talla.objects.create(producto=producto, sku=sku, stock=stock, talla=talla)
        for talla, sku, stock in tallas
    }


def _mov(pt, cantidad, concepto, fecha, dte=None, origen=None, destino=None, costo=0):
    return Movimientos_Producto.objects.create(
        ProductoTalla=pt, cantidad=cantidad, concepto=concepto, fecha=fecha,
        estado='COMPLETADO', responsable='Tester', dte=dte, costo=costo,
        sucursal_origen=origen, sucursal_destino=destino,
    )


def _dte_compra(proveedor, receptor, sucursal, numero, fecha):
    return Dte.objects.create(
        emisor=proveedor, receptor=receptor, numero_documento=numero,
        tipo_documento='FACTURA ELECTRONICA',
        monto_neto=Decimal('100000'), monto_con_iva=Decimal('119000'),
        estado_pago='PENDIENTE', estado_dte='ACEPTADO', responsable='Tester',
        fecha_emision=fecha, fecha_vencimiento=fecha + timedelta(days=30),
        diasCredito=30, bultos=1, unidades_productos=10,
        tipo_transaccion='COMPRA', sucursal=sucursal,
    )


@override_settings(
    # Cada movimiento creado dispara el aviso de stock a AllConnected: que no salga nada.
    ALLCONNECTED_WEBHOOK_URL='',
    # Una decena de usuarios por clase: con el hasher por defecto el setup se arrastra.
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class BaseTarjetaHolding(TestCase):
    """
    Holding de prueba con dos empresas:
      A «Paola Test»       → PAO1, PAO2
      B «Importadora Test» → NICK1, NICK2, IMP (centro de distribución)
    """

    @classmethod
    def setUpTestData(cls):
        hoy = timezone.localdate()
        cls.d1 = hoy - timedelta(days=60)
        cls.d2 = hoy - timedelta(days=30)
        cls.d3 = hoy - timedelta(days=10)

        cls.emp_a = crear_empresa(nombre=EMPRESA_A, rut='76.111.111-1')
        cls.emp_b = crear_empresa(nombre=EMPRESA_B, rut='76.222.222-2')
        prov_a = crear_empresa(nombre=PROVEEDOR_A, rut='77.111.111-1', esProveedor=True)
        prov_b = crear_empresa(nombre=PROVEEDOR_B, rut='77.222.222-2', esProveedor=True)

        cls.pao1 = crear_sucursal(empresa=cls.emp_a, alias='PAO1')
        cls.pao2 = crear_sucursal(empresa=cls.emp_a, alias='PAO2')
        cls.nick1 = crear_sucursal(empresa=cls.emp_b, alias='NICK1')
        cls.nick2 = crear_sucursal(empresa=cls.emp_b, alias='NICK2')
        cls.imp = crear_sucursal(empresa=cls.emp_b, alias='IMP',
                                 tipo_sucursal='CENTRO_DISTRIBUCION',
                                 es_centro_distribucion=True)
        cls.ids_a = {cls.pao1.id, cls.pao2.id}
        cls.ids_b = {cls.nick1.id, cls.nick2.id, cls.imp.id}

        # --- Usuarios ----------------------------------------------------------
        # Acotados: rol vendedor (es_rol_administrador = False) y UNA empresa.
        # Su EmpresaUser apunta a PAO1, pero el alcance es la EMPRESA: PAO2 también.
        cls.vend_a = crear_usuario(username='vend-a')
        crear_empresa_user(cls.vend_a, cls.emp_a, cls.pao1)
        cls.vend_b = crear_usuario(username='vend-b')
        crear_empresa_user(cls.vend_b, cls.emp_b, cls.nick1)
        # Acotado, pero con las DOS empresas asignadas.
        cls.vend_ab = crear_usuario(username='vend-ab')
        crear_empresa_user(cls.vend_ab, cls.emp_a, cls.pao1)
        crear_empresa_user(cls.vend_ab, cls.emp_b, cls.nick1)
        # Ven todo: roles de nivel administrador y el flag por usuario.
        cls.maestro = crear_usuario(username='maestro-x', rol='maestro')
        cls.admin = crear_usuario(username='admin-x', rol='administrador')
        crear_empresa_user(cls.admin, cls.emp_a, cls.pao1)
        cls.jefe = crear_usuario(username='jefe-x', rol='jefe')
        cls.con_flag = crear_usuario(username='vend-flag')
        crear_empresa_user(cls.con_flag, cls.emp_a, cls.pao1)
        modulo = ModuloSistema.objects.create(codigo='tests_tarjeta_holding', nombre='Tests')
        opcion = OpcionMenu.objects.create(modulo=modulo, codigo='tests_tarjeta_holding',
                                           nombre='Tests tarjeta')
        PermisoUsuario.objects.create(usuario=cls.con_flag, opcion_menu=opcion,
                                      puede_ver_todas_sucursales=True)
        # Sin empresa vigente: sin EmpresaUser, o con el único dado de baja.
        cls.sin_empresa = crear_usuario(username='sin-empresa')
        cls.empresa_de_baja = crear_usuario(username='empresa-de-baja')
        crear_empresa_user(cls.empresa_de_baja, cls.emp_a, cls.pao1, status=False)

        # --- Catálogo ----------------------------------------------------------
        atributo_marca = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        adidas = AtributoOpcion.objects.create(atributo=atributo_marca, valor='ADIDAS')
        puma = AtributoOpcion.objects.create(atributo=atributo_marca, valor='PUMA')
        nike = AtributoOpcion.objects.create(atributo=atributo_marca, valor='NIKE')
        calzado = Categoria.objects.create(nombre='Calzado')

        # 1) ART en A (PAO1, PAO2) y en B (NICK1, NICK2, IMP). La talla 42 solo
        #    existe en NICK2 y el CD (IMP) tiene la ficha pero sin stock.
        desc_a, desc_b = 'ADILETTE AQUA NEGRO', 'ADILETTE AQUA NEGRO IMPORTADORA'
        pao1 = _ficha(cls.pao1, ART, [('38', SKU_38, 2), ('40', SKU_40, 1)],
                      COSTO_A, PRECIO_A, desc_a, adidas, calzado)
        pao2 = _ficha(cls.pao2, ART, [('38', SKU_38, 3)],
                      COSTO_A, PRECIO_A, desc_a, adidas, calzado)
        nick1 = _ficha(cls.nick1, ART, [('38', SKU_38, 5), ('40', SKU_40, 0)],
                       COSTO_B, PRECIO_B, desc_b, adidas, calzado)
        nick2 = _ficha(cls.nick2, ART, [('40', SKU_40, 7), ('42', SKU_42, 4)],
                       COSTO_B, PRECIO_B, desc_b, adidas, calzado)
        _ficha(cls.imp, ART, [('38', SKU_38, 0)], COSTO_B, PRECIO_B, desc_b, adidas, calzado)

        # Historial de A: cuadra con su stock (PAO1 38=2, PAO1 40=1, PAO2 38=3).
        dte_a = _dte_compra(prov_a, cls.emp_a, cls.pao1, 501, cls.d1)
        _mov(pao1['38'], 6, 'RECEPCION_COMPRA', cls.d1, dte=dte_a, costo=COSTO_A)
        _mov(pao1['40'], 1, 'RECEPCION_COMPRA', cls.d1, dte=dte_a, costo=COSTO_A)
        _mov(pao1['38'], -3, 'TRASPASO_SALIDA', cls.d2, origen=cls.pao1, destino=cls.pao2)
        _mov(pao2['38'], 3, 'TRASPASO_ENTRADA', cls.d2, origen=cls.pao1, destino=cls.pao2)
        _mov(pao1['38'], -1, 'VENTA_PUBLICO', cls.d3)

        # Historial de B: cuadra con su stock (NICK1 38=5 / 40=0, NICK2 40=7 / 42=4).
        dte_b1 = _dte_compra(prov_b, cls.emp_b, cls.nick1, 801, cls.d1)
        dte_b2 = _dte_compra(prov_b, cls.emp_b, cls.nick2, 802, cls.d1)
        _mov(nick1['38'], 5, 'RECEPCION_COMPRA', cls.d1, dte=dte_b1, costo=COSTO_B)
        _mov(nick1['40'], 2, 'RECEPCION_COMPRA', cls.d1, dte=dte_b1, costo=COSTO_B)
        _mov(nick1['40'], -2, 'VENTA_PUBLICO', cls.d3)
        _mov(nick2['40'], 7, 'RECEPCION_COMPRA', cls.d1, dte=dte_b2, costo=COSTO_B)
        _mov(nick2['42'], 4, 'RECEPCION_COMPRA', cls.d1, dte=dte_b2, costo=COSTO_B)

        # 2) Artículo que solo trabaja B (NICK1 4 u, NICK2 2 u).
        solo_n1 = _ficha(cls.nick1, ART_SOLO_B, [('39', SKU_SOLO_B, 4)], 15000, 39990,
                         'SUEDE CLASSIC ROJO', puma, calzado)
        solo_n2 = _ficha(cls.nick2, ART_SOLO_B, [('39', SKU_SOLO_B, 2)], 15000, 41990,
                         'SUEDE CLASSIC ROJO', puma, calzado)
        dte_b3 = _dte_compra(prov_b, cls.emp_b, cls.nick1, 803, cls.d1)
        _mov(solo_n1['39'], 6, 'RECEPCION_COMPRA', cls.d1, dte=dte_b3, costo=15000)
        _mov(solo_n1['39'], -2, 'TRASPASO_SALIDA', cls.d2, origen=cls.nick1, destino=cls.nick2)
        _mov(solo_n2['39'], 2, 'TRASPASO_ENTRADA', cls.d2, origen=cls.nick1, destino=cls.nick2)

        # 3) Resolución del artículo (SKU que calza con más de un artículo).
        _ficha(cls.pao1, 'TM-PROPIO', [('41', SKU_PROPIO, 2)], 20000, 49990,
               'AIR MAX PROPIO', nike, calzado)
        _ficha(cls.nick1, 'TM-LEGADO', [('M', SKU_PROPIO, 9)], 3000, 9990,
               'POLERA LEGADO', None, calzado)
        _ficha(cls.pao1, 'TM-DUP-1', [('40', SKU_DUP, 1)], 5000, 12990,
               'CHALA DUPLICADA UNO', None, calzado)
        _ficha(cls.pao2, 'TM-DUP-2', [('40', SKU_DUP, 2)], 5000, 12990,
               'CHALA DUPLICADA DOS', None, calzado)
        _ficha(cls.nick1, 'TM-DUP-2', [('41', SKU_DUP + 1, 10)], 5000, 12990,
               'CHALA DUPLICADA DOS', None, calzado)
        _ficha(cls.nick2, 'TM-EDEL-1', [('40', SKU_EDEL, 1)], 8000, 19990,
               'BOTIN EDEL UNO', None, calzado)
        _ficha(cls.imp, 'TM-EDEL-2', [('40', SKU_EDEL, 6)], 8000, 19990,
               'BOTIN EDEL DOS', None, calzado)
        _ficha(cls.pao1, 'TM-CASO', [('40', SKU_CASO, 1)], 7000, 15990,
               'ZAPATO CASO MAYUSCULA', None, calzado)
        _ficha(cls.nick2, 'tm-caso', [('40', SKU_CASO, 3)], 7000, 15990,
               'ZAPATO CASO MINUSCULA', None, calzado)

    # --- helpers -------------------------------------------------------------

    def _respuesta(self, user, sucursal=None, **params):
        req = RequestFactory().get('/app/api/tarjeta-movimiento/', params)
        req.user = user
        req.session = {'idSucursalActual': sucursal.id} if sucursal else {}
        return api_tarjeta_movimiento(req)

    def _tarjeta(self, user, sucursal=None, **params):
        """GET a la API de la tarjeta como `user`, con `sucursal` como sucursal de la sesión."""
        resp = self._respuesta(user, sucursal, **params)
        return resp.status_code, json.loads(resp.content)

    def _tarjeta_texto(self, user, sucursal=None, **params):
        """El JSON crudo de la tarjeta, para buscar fugas por texto."""
        resp = self._respuesta(user, sucursal, **params)
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        return resp.content.decode('utf-8')

    def _buscar(self, user, q):
        """GET al autocompletado de la tarjeta."""
        req = RequestFactory().get('/app/api/tarjeta-movimiento/buscar/', {'q': q})
        req.user = user
        req.session = {}
        resp = api_buscar_productos_tarjeta_movimiento(req)
        return resp.status_code, json.loads(resp.content)

    @staticmethod
    def _por_bodega(data):
        return {d['bodega']: d for d in data['distribucion']}


class TestUsuarioDeUnaEmpresa(BaseTarjetaHolding):
    """Caso 1: stock de todo el holding; historial y costo, solo de su empresa."""

    def setUp(self):
        self.status, self.data = self._tarjeta(self.vend_a, self.pao1, sku=str(SKU_38))
        self.assertEqual(self.status, 200, self.data)
        self.assertTrue(self.data['success'])
        self.assertFalse(self.data.get('ambiguo', False), self.data)
        self.assertEqual(self.data['producto']['articulo'], ART)

    def test_matriz_trae_las_bodegas_de_las_dos_empresas(self):
        dist = self._por_bodega(self.data)
        self.assertEqual(set(dist), ALIAS_A | ALIAS_B)
        self.assertEqual(
            {b: d['stock_total'] for b, d in dist.items()},
            {'PAO1': 3, 'PAO2': 3, 'NICK1': 5, 'NICK2': 11, 'IMP': 0},
        )
        # El desglose por talla de una bodega ajena también viene: es stock.
        self.assertEqual({t['talla']: t['stock'] for t in dist['NICK2']['tallas']},
                         {'40': 7, '42': 4})
        self.assertEqual(dist['NICK2']['empresa'], EMPRESA_B)
        self.assertEqual(dist['PAO2']['empresa'], EMPRESA_A)

    def test_con_historial_solo_en_las_bodegas_de_su_empresa(self):
        self.assertEqual(
            {d['bodega']: d['con_historial'] for d in self.data['distribucion']},
            {'PAO1': True, 'PAO2': True, 'NICK1': False, 'NICK2': False, 'IMP': False},
            'PAO2 es de su empresa aunque su EmpresaUser apunte a PAO1',
        )

    def test_kardex_solo_de_sus_bodegas(self):
        movs = self.data['movimientos']
        self.assertEqual(len(movs), 5)
        self.assertEqual({m['bodega_id'] for m in movs}, self.ids_a)
        self.assertEqual({m['bodega'] for m in movs}, ALIAS_A)
        r = self.data['resumen']
        self.assertEqual(r['total_movimientos'], 5)
        self.assertEqual(r['total_vendido'], 1, 'La venta de NICK1 no es de su empresa')
        self.assertEqual(r['total_entradas'], 7)
        self.assertEqual(r['traspasos'], 1)

    def test_llegadas_solo_de_sus_bodegas(self):
        llegadas = self.data['llegadas']
        self.assertEqual({(l['bodega'], l['unidades']) for l in llegadas},
                         {('PAO1', 7), ('PAO2', 3)})
        self.assertTrue({l['bodega_id'] for l in llegadas} <= self.ids_a)
        self.assertEqual(self.data['resumen']['num_llegadas'], 2)
        self.assertEqual(self.data['resumen']['unidades_llegadas'], 10)

    def test_timeline_solo_de_sus_bodegas(self):
        textos = [f"{h['titulo']} {h['detalle']}" for h in self.data['timeline']]
        self.assertTrue(textos, 'Debía haber hitos de su empresa')
        self.assertTrue(any('PAO1' in t for t in textos), textos)
        for alias in ALIAS_B:
            with self.subTest(alias=alias):
                self.assertFalse([t for t in textos if alias in t], textos)

    def test_aperturas_con_fecha_desde_solo_de_sus_bodegas(self):
        status, data = self._tarjeta(self.vend_a, self.pao1, sku=str(SKU_38),
                                     fecha_desde=self.d2.isoformat())
        self.assertEqual(status, 200, data)
        # B también tiene movimientos antes de d2 (recepciones del día d1): no deben aparecer.
        self.assertEqual({(a['bodega'], a['talla']): a['saldo'] for a in data['aperturas']},
                         {('PAO1', '38'): 6, ('PAO1', '40'): 1})
        self.assertTrue({a['bodega_id'] for a in data['aperturas']} <= self.ids_a)
        self.assertEqual({a['sku'] for a in data['aperturas']}, {str(SKU_38), str(SKU_40)})
        self.assertEqual(len(data['movimientos']), 3)
        self.assertEqual({m['bodega_id'] for m in data['movimientos']}, self.ids_a)
        r = data['resumen']
        self.assertEqual(r['saldo_apertura'], 7)
        self.assertEqual(r['saldo_final'], STOCK_EMPRESA_A)
        self.assertEqual(r['saldo_final'], r['stock_historial'])

    def test_filtros_del_kardex(self):
        filtros = self.data['filtros']
        self.assertEqual({b['alias'] for b in filtros['bodegas']}, ALIAS_A)
        self.assertEqual({b['id'] for b in filtros['bodegas']}, self.ids_a)
        # Las tallas son las de TODO el artículo (la 42 solo existe en NICK2).
        self.assertEqual(filtros['tallas'], ['38', '40', '42'])

    def test_alcance_informa_las_bodegas_sin_historial(self):
        alcance = self.data['alcance']
        self.assertFalse(alcance['historial_completo'])
        self.assertFalse(alcance['sin_historial'])
        self.assertEqual(alcance['empresas_historial'], [EMPRESA_A])
        self.assertEqual(set(alcance['bodegas_sin_historial']), ALIAS_B)
        self.assertEqual(len(alcance['bodegas_sin_historial']), len(ALIAS_B))

    def test_resumen_stock_del_holding_y_stock_con_historial(self):
        r = self.data['resumen']
        self.assertEqual(r['stock_actual'], STOCK_HOLDING)
        self.assertEqual(r['stock_historial'], STOCK_EMPRESA_A)
        # El kardex visible cuadra con el stock de SUS bodegas, no con el del holding.
        self.assertEqual(r['saldo_final'], r['stock_historial'])

    def test_ficha_con_costo_de_su_empresa_y_stock_del_holding(self):
        p = self.data['producto']
        self.assertEqual(p['costo'], COSTO_A)
        self.assertEqual(p['precio_venta'], PRECIO_A)
        self.assertEqual(p['descripcion'], 'ADILETTE AQUA NEGRO')
        self.assertEqual(p['marca'], 'ADIDAS')
        self.assertEqual(p['stock_total'], STOCK_HOLDING)
        self.assertEqual(p['num_bodegas'], 5)
        self.assertEqual(p['bodegas'], sorted(ALIAS_A | ALIAS_B))
        self.assertEqual(p['empresas'], [EMPRESA_B, EMPRESA_A])
        self.assertEqual(p['num_skus'], 8)

    def test_no_se_filtra_el_proveedor_ni_el_costo_de_la_otra_empresa(self):
        texto = self._tarjeta_texto(self.vend_a, self.pao1, sku=str(SKU_38))
        self.assertIn(PROVEEDOR_A, texto)
        self.assertNotIn(PROVEEDOR_B, texto)
        self.assertNotIn(COSTO_B, [m['costo'] for m in self.data['movimientos']])
        self.assertNotIn(COSTO_B, [l['costo_unitario'] for l in self.data['llegadas']])

    def test_sesion_en_bodega_ajena_no_muestra_el_costo_ajeno(self):
        """Aunque la sesión apunte a NICK1, la ficha sale de una variante de SU empresa."""
        status, data = self._tarjeta(self.vend_a, self.nick1, sku=str(SKU_38))
        self.assertEqual(status, 200, data)
        self.assertEqual(data['producto']['costo'], COSTO_A)
        self.assertEqual(data['producto']['precio_venta'], PRECIO_A)

    def test_usuario_de_la_otra_empresa_ve_el_espejo(self):
        status, data = self._tarjeta(self.vend_b, self.nick1, sku=str(SKU_38))
        self.assertEqual(status, 200, data)
        self.assertEqual(
            {d['bodega']: d['con_historial'] for d in data['distribucion']},
            {'PAO1': False, 'PAO2': False, 'NICK1': True, 'NICK2': True, 'IMP': True},
        )
        self.assertEqual({m['bodega_id'] for m in data['movimientos']},
                         {self.nick1.id, self.nick2.id})
        self.assertEqual(len(data['movimientos']), 5)
        self.assertEqual(data['producto']['costo'], COSTO_B)
        self.assertEqual(data['resumen']['stock_actual'], STOCK_HOLDING)
        self.assertEqual(data['resumen']['stock_historial'], STOCK_EMPRESA_B)
        self.assertEqual(data['resumen']['saldo_final'], STOCK_EMPRESA_B)
        self.assertEqual(set(data['alcance']['bodegas_sin_historial']), ALIAS_A)
        self.assertEqual(data['alcance']['empresas_historial'], [EMPRESA_B])
        texto = self._tarjeta_texto(self.vend_b, self.nick1, sku=str(SKU_38))
        self.assertNotIn(PROVEEDOR_A, texto)


class TestArticuloSoloEnOtraEmpresa(BaseTarjetaHolding):
    """Caso 2: un artículo que su empresa no trabaja se ve (dónde hay), sin historial ni costo."""

    def _assert_sin_historial(self, data):
        self.assertTrue(data['success'])
        self.assertFalse(data.get('ambiguo', False), data)
        self.assertEqual(data['producto']['articulo'], ART_SOLO_B)
        alcance = data['alcance']
        self.assertTrue(alcance['sin_historial'])
        self.assertFalse(alcance['historial_completo'])
        self.assertEqual(alcance['empresas_historial'], [])
        self.assertEqual(set(alcance['bodegas_sin_historial']), {'NICK1', 'NICK2'})
        self.assertIsNone(data['producto']['costo'])
        self.assertEqual(data['movimientos'], [])
        self.assertEqual(data['llegadas'], [])
        self.assertEqual(data['timeline'], [])
        self.assertEqual(data['aperturas'], [])
        self.assertEqual(data['filtros']['bodegas'], [])

    def test_por_sku_responde_200_con_stock_y_sin_historial(self):
        status, data = self._tarjeta(self.vend_a, self.pao1, sku=str(SKU_SOLO_B))
        self.assertEqual(status, 200, data)
        self._assert_sin_historial(data)
        self.assertEqual(
            {d['bodega']: (d['stock_total'], d['con_historial']) for d in data['distribucion']},
            {'NICK1': (4, False), 'NICK2': (2, False)},
        )
        r = data['resumen']
        self.assertEqual(r['stock_actual'], 6)
        self.assertEqual(r['stock_historial'], 0)
        self.assertEqual(r['saldo_final'], 0)
        self.assertEqual(r['total_movimientos'], 0)
        self.assertEqual(r['num_llegadas'], 0)

    def test_sin_costo_pero_con_precio_y_descripcion(self):
        _, data = self._tarjeta(self.vend_a, self.pao1, sku=str(SKU_SOLO_B))
        p = data['producto']
        self.assertIsNone(p['costo'])
        # El precio es público: sale de la bodega con más stock (NICK1).
        self.assertEqual(p['precio_venta'], 39990)
        self.assertEqual(p['descripcion'], 'SUEDE CLASSIC ROJO')
        self.assertEqual(p['stock_total'], 6)
        self.assertEqual(p['empresas'], [EMPRESA_B])

    def test_con_fecha_desde_tampoco_hay_aperturas(self):
        # B tiene una recepción el día d1, anterior a d2: no puede aparecer como apertura.
        status, data = self._tarjeta(self.vend_a, self.pao1, sku=str(SKU_SOLO_B),
                                     fecha_desde=self.d2.isoformat())
        self.assertEqual(status, 200, data)
        self._assert_sin_historial(data)
        self.assertEqual(data['resumen']['saldo_apertura'], 0)
        self.assertEqual(data['resumen']['saldo_final'], 0)

    def test_por_codigo_o_por_articulo_tambien(self):
        for params in ({'sku': ART_SOLO_B}, {'articulo': ART_SOLO_B},
                       {'articulo': ART_SOLO_B.lower()}):
            with self.subTest(**params):
                status, data = self._tarjeta(self.vend_a, self.pao1, **params)
                self.assertEqual(status, 200, data)
                self._assert_sin_historial(data)

    def test_no_se_filtra_el_proveedor_de_la_otra_empresa(self):
        texto = self._tarjeta_texto(self.vend_a, self.pao1, sku=str(SKU_SOLO_B))
        self.assertNotIn(PROVEEDOR_B, texto)


class TestUsuarioQueVeTodo(BaseTarjetaHolding):
    """Caso 3: quien ve todo (rol o flag) tiene el historial de todas las bodegas."""

    def _ven_todo(self):
        return (self.maestro, self.admin, self.jefe, self.con_flag)

    def test_historial_completo_con_movimientos_de_las_dos_empresas(self):
        for user in self._ven_todo():
            with self.subTest(usuario=user.username):
                status, data = self._tarjeta(user, self.pao1, sku=str(SKU_38))
                self.assertEqual(status, 200, data)
                alcance = data['alcance']
                self.assertTrue(alcance['historial_completo'])
                self.assertFalse(alcance['sin_historial'])
                self.assertEqual(alcance['bodegas_sin_historial'], [])
                self.assertEqual(alcance['empresas_historial'], [EMPRESA_B, EMPRESA_A])
                self.assertTrue(all(d['con_historial'] for d in data['distribucion']))
                self.assertEqual(len(data['movimientos']), 10)
                self.assertEqual({m['bodega_id'] for m in data['movimientos']},
                                 {self.pao1.id, self.pao2.id, self.nick1.id, self.nick2.id})
                self.assertEqual({b['id'] for b in data['filtros']['bodegas']},
                                 self.ids_a | self.ids_b)
                self.assertEqual({p for p in (m['proveedor'] for m in data['movimientos']) if p},
                                 {PROVEEDOR_A, PROVEEDOR_B})
                r = data['resumen']
                self.assertEqual(r['stock_actual'], STOCK_HOLDING)
                self.assertEqual(r['stock_historial'], STOCK_HOLDING)
                self.assertEqual(r['saldo_final'], STOCK_HOLDING)
                self.assertEqual(r['total_vendido'], 3)
                self.assertIsNotNone(data['producto']['costo'])

    def test_aperturas_de_las_dos_empresas(self):
        status, data = self._tarjeta(self.admin, self.pao1, sku=str(SKU_38),
                                     fecha_desde=self.d2.isoformat())
        self.assertEqual(status, 200, data)
        self.assertEqual(
            {(a['bodega'], a['talla']): a['saldo'] for a in data['aperturas']},
            {('PAO1', '38'): 6, ('PAO1', '40'): 1, ('NICK1', '38'): 5,
             ('NICK1', '40'): 2, ('NICK2', '40'): 7, ('NICK2', '42'): 4},
        )
        self.assertEqual(data['resumen']['saldo_apertura'], 25)
        self.assertEqual(data['resumen']['saldo_final'], STOCK_HOLDING)

    def test_costo_sale_de_la_sucursal_actual(self):
        for sucursal, costo in ((self.pao1, COSTO_A), (self.nick1, COSTO_B)):
            with self.subTest(sucursal=sucursal.alias):
                status, data = self._tarjeta(self.admin, sucursal, sku=str(SKU_38))
                self.assertEqual(status, 200, data)
                self.assertEqual(data['producto']['costo'], costo)

    def test_usuario_con_las_dos_empresas_asignadas(self):
        """No es administrador: ve el historial de ambas porque tiene las dos empresas."""
        status, data = self._tarjeta(self.vend_ab, self.pao1, sku=str(SKU_38))
        self.assertEqual(status, 200, data)
        self.assertTrue(data['alcance']['historial_completo'])
        self.assertEqual(data['alcance']['empresas_historial'], [EMPRESA_B, EMPRESA_A])
        self.assertEqual(len(data['movimientos']), 10)
        self.assertEqual(data['resumen']['stock_historial'], STOCK_HOLDING)


class TestSinEmpresa(BaseTarjetaHolding):
    """Caso 4: sin ninguna empresa vigente no se ve nada, ni siquiera stock."""

    def test_sin_empresa_vigente_recibe_403(self):
        for user in (self.sin_empresa, self.empresa_de_baja):
            for params in ({'sku': str(SKU_38)}, {'articulo': ART}, {'sku': str(SKU_SOLO_B)}):
                with self.subTest(usuario=user.username, **params):
                    status, data = self._tarjeta(user, self.pao1, **params)
                    self.assertEqual(status, 403, data)
                    self.assertFalse(data['success'])
                    self.assertNotIn('distribucion', data)
                    self.assertNotIn('producto', data)
                    self.assertNotIn('candidatos', data)

    def test_autocompletado_sin_empresa_no_sugiere_nada(self):
        for user in (self.sin_empresa, self.empresa_de_baja):
            with self.subTest(usuario=user.username):
                status, data = self._buscar(user, 'TM-')
                self.assertEqual(status, 200, data)
                self.assertEqual(data['productos'], [])


class TestResolucionDelArticulo(BaseTarjetaHolding):
    """Caso 5: primero en sus empresas, luego en el holding; si es ambiguo, elegir."""

    def test_sku_propio_no_pregunta_por_el_legado_de_otra_empresa(self):
        status, data = self._tarjeta(self.vend_a, self.pao1, sku=str(SKU_PROPIO))
        self.assertEqual(status, 200, data)
        self.assertFalse(data.get('ambiguo', False), data)
        self.assertEqual(data['producto']['articulo'], 'TM-PROPIO')
        self.assertEqual([d['bodega'] for d in data['distribucion']], ['PAO1'])
        self.assertTrue(data['alcance']['historial_completo'])
        self.assertEqual(data['producto']['costo'], 20000)

    def test_quien_ve_todo_recibe_los_dos_candidatos(self):
        status, data = self._tarjeta(self.admin, self.pao1, sku=str(SKU_PROPIO))
        self.assertEqual(status, 200, data)
        self.assertTrue(data['success'])
        self.assertTrue(data.get('ambiguo'), data)
        self.assertEqual(data['buscado'], str(SKU_PROPIO))
        self.assertNotIn('movimientos', data)
        candidatos = data['candidatos']
        # Más stock primero.
        self.assertEqual([c['articulo'] for c in candidatos], ['TM-LEGADO', 'TM-PROPIO'])
        legado, propio = candidatos
        self.assertEqual((legado['stock'], legado['bodegas'], legado['num_bodegas']),
                         (9, ['NICK1'], 1))
        self.assertEqual((propio['stock'], propio['bodegas'], propio['num_bodegas']),
                         (2, ['PAO1'], 1))
        self.assertEqual({c['sku'] for c in candidatos}, {str(SKU_PROPIO)})
        self.assertEqual(propio['marca'], 'NIKE')
        self.assertEqual(legado['marca'], '-')

    def test_articulo_elegido_abre_la_tarjeta(self):
        status, data = self._tarjeta(self.admin, self.pao1, articulo='TM-LEGADO')
        self.assertEqual(status, 200, data)
        self.assertFalse(data.get('ambiguo', False), data)
        self.assertEqual(data['producto']['articulo'], 'TM-LEGADO')
        self.assertEqual([(d['bodega'], d['stock_total']) for d in data['distribucion']],
                         [('NICK1', 9)])

    def test_articulo_sin_distinguir_mayusculas_si_es_unico(self):
        status, data = self._tarjeta(self.admin, self.pao1, articulo='tm-legado')
        self.assertEqual(status, 200, data)
        self.assertFalse(data.get('ambiguo', False), data)
        self.assertEqual(data['producto']['articulo'], 'TM-LEGADO')
        self.assertEqual(data['resumen']['stock_actual'], 9)

    def test_usuario_abre_por_articulo_uno_que_solo_esta_en_otra_empresa(self):
        status, data = self._tarjeta(self.vend_a, self.pao1, articulo='TM-LEGADO')
        self.assertEqual(status, 200, data)
        self.assertEqual(data['producto']['articulo'], 'TM-LEGADO')
        self.assertTrue(data['alcance']['sin_historial'])
        self.assertIsNone(data['producto']['costo'])
        self.assertEqual(data['movimientos'], [])

    def test_sku_ambiguo_dentro_de_su_empresa_lista_candidatos_con_stock_del_holding(self):
        status, data = self._tarjeta(self.vend_a, self.pao1, sku=str(SKU_DUP))
        self.assertEqual(status, 200, data)
        self.assertTrue(data.get('ambiguo'), data)
        candidatos = data['candidatos']
        self.assertEqual([c['articulo'] for c in candidatos], ['TM-DUP-2', 'TM-DUP-1'])
        dup2, dup1 = candidatos
        # TM-DUP-2: 2 u en PAO2 + 10 u en NICK1 (otra empresa, otro SKU): stock del holding.
        self.assertEqual((dup2['stock'], dup2['bodegas'], dup2['num_bodegas']),
                         (12, ['NICK1', 'PAO2'], 2))
        self.assertEqual(dup2['sku'], str(SKU_DUP))
        self.assertEqual((dup1['stock'], dup1['bodegas']), (1, ['PAO1']))

    def test_sku_ambiguo_que_solo_existe_en_otra_empresa(self):
        status, data = self._tarjeta(self.vend_a, self.pao1, sku=str(SKU_EDEL))
        self.assertEqual(status, 200, data)
        self.assertTrue(data.get('ambiguo'), data)
        self.assertEqual([(c['articulo'], c['stock'], c['bodegas']) for c in data['candidatos']],
                         [('TM-EDEL-2', 6, ['IMP']), ('TM-EDEL-1', 1, ['NICK2'])])
        # Al elegir uno se abre la tarjeta, sin historial (no es de su empresa).
        status, data = self._tarjeta(self.vend_a, self.pao1, articulo='TM-EDEL-2')
        self.assertEqual(status, 200, data)
        self.assertFalse(data.get('ambiguo', False), data)
        self.assertEqual(data['producto']['articulo'], 'TM-EDEL-2')
        self.assertTrue(data['alcance']['sin_historial'])
        self.assertEqual([(d['bodega'], d['con_historial']) for d in data['distribucion']],
                         [('IMP', False)])

    def test_codigo_inexistente_404(self):
        for params in ({'sku': 'NO-EXISTE-XYZ'}, {'sku': '7999999'}, {'articulo': 'NO-EXISTE-XYZ'}):
            with self.subTest(**params):
                status, data = self._tarjeta(self.vend_a, self.pao1, **params)
                self.assertEqual(status, 404, data)
                self.assertFalse(data['success'])

    # --- Borde: códigos que solo difieren en mayúsculas ----------------------
    # Si existen 'TM-CASO' y 'tm-caso' (legado), elegir uno de la lista de
    # candidatos abre ESE: el código exacto gana a la comparación sin
    # mayúsculas (antes `?articulo=` usaba iexact y la lista no se resolvía).

    def test_elegir_candidato_que_solo_difiere_en_mayusculas_abre_ese_articulo(self):
        status, data = self._tarjeta(self.admin, self.pao1, sku=str(SKU_CASO))
        self.assertEqual(status, 200, data)
        self.assertTrue(data.get('ambiguo'), data)
        self.assertEqual({c['articulo'] for c in data['candidatos']}, {'TM-CASO', 'tm-caso'})

        status, data = self._tarjeta(self.admin, self.pao1, articulo='tm-caso')
        self.assertEqual(status, 200, data)
        self.assertFalse(
            data.get('ambiguo', False),
            'Elegir el candidato «tm-caso» vuelve a pedir elegir: la lista nunca se resuelve',
        )
        self.assertEqual(data['producto']['articulo'], 'tm-caso')

    def test_usuario_elige_del_autocompletado_codigo_de_otra_empresa_que_difiere_en_mayusculas(self):
        _, sugerencias = self._buscar(self.vend_a, 'tm-caso')
        self.assertEqual({p['articulo'] for p in sugerencias['productos']}, {'TM-CASO', 'tm-caso'})

        status, data = self._tarjeta(self.vend_a, self.pao1, articulo='tm-caso')
        self.assertEqual(status, 200, data)
        self.assertFalse(data.get('ambiguo', False), data)
        self.assertEqual(
            data['producto']['articulo'], 'tm-caso',
            'Eligió «tm-caso» (NICK2) y la tarjeta abrió «TM-CASO» de su empresa',
        )

    def test_codigo_escrito_exacto_gana_a_la_variante_de_mayusculas(self):
        """Escribir el código tal cual abre ese; en otra capitalización, el canónico (mayúsculas)."""
        for escrito, esperado in (('tm-caso', 'tm-caso'), ('TM-CASO', 'TM-CASO'), ('Tm-Caso', 'TM-CASO')):
            with self.subTest(escrito=escrito):
                status, data = self._tarjeta(self.vend_a, self.pao1, sku=escrito)
                self.assertEqual(status, 200, data)
                self.assertFalse(data.get('ambiguo', False), data)
                self.assertEqual(data['producto']['articulo'], esperado)

    def test_codigo_legado_con_espacio_duro_se_abre_desde_la_lista(self):
        """
        Hay códigos legados con \\xa0 o espacios en los bordes (p. ej. EDEL
        '\\xa05208320403814' junto a su gemelo limpio). Elegido de la lista, el
        código llega tal cual y debe abrir ESA ficha, no la del gemelo ni un 404.
        """
        _ficha(self.nick1, '\xa0TM-NBSP', [('40', 7907001, 2)], 1000, 2990, 'LEGADO NBSP')
        _ficha(self.pao1, 'TM-NBSP', [('40', 7907002, 5)], 1000, 2990, 'LIMPIO')
        _ficha(self.nick2, 'TM-ESPACIO ', [('40', 7907003, 1)], 1000, 2990, 'CON ESPACIO FINAL')

        casos = (
            ('\xa0TM-NBSP', '\xa0TM-NBSP', [('NICK1', 2)]),
            ('TM-NBSP', 'TM-NBSP', [('PAO1', 5)]),
            ('TM-ESPACIO ', 'TM-ESPACIO ', [('NICK2', 1)]),
            # Escrito a mano con espacios alrededor: se recorta y se encuentra igual.
            ('  TM-NBSP  ', 'TM-NBSP', [('PAO1', 5)]),
        )
        for pedido, esperado, distribucion in casos:
            with self.subTest(pedido=repr(pedido)):
                status, data = self._tarjeta(self.admin, self.pao1, articulo=pedido)
                self.assertEqual(status, 200, data)
                self.assertFalse(data.get('ambiguo', False), data)
                self.assertEqual(data['producto']['articulo'], esperado)
                self.assertEqual([(d['bodega'], d['stock_total']) for d in data['distribucion']],
                                 distribucion)


class TestAutocompletadoHolding(BaseTarjetaHolding):
    """Caso 6: el autocompletado busca en todo el holding y agrupa por artículo."""

    @staticmethod
    def _por_articulo(data):
        return {p['articulo']: p for p in data['productos']}

    def test_usuario_ve_articulos_que_solo_existen_en_otra_empresa(self):
        status, data = self._buscar(self.vend_a, 'TM-SOLO')
        self.assertEqual(status, 200, data)
        productos = self._por_articulo(data)
        self.assertEqual(list(productos), [ART_SOLO_B])
        p = productos[ART_SOLO_B]
        self.assertEqual(p['stock'], 6)
        self.assertEqual(p['bodegas'], ['NICK1', 'NICK2'])
        self.assertEqual(p['num_bodegas'], 2)
        self.assertEqual(p['sku'], str(SKU_SOLO_B))
        self.assertEqual(p['marca'], 'PUMA')

    def test_dos_palabras_marca_y_descripcion(self):
        for q in ('adidas adilette', 'adilette adidas', 'ADIDAS Aqua'):
            with self.subTest(q=q):
                status, data = self._buscar(self.vend_a, q)
                self.assertEqual(status, 200, data)
                productos = self._por_articulo(data)
                self.assertEqual(list(productos), [ART])
                p = productos[ART]
                self.assertEqual(p['stock'], STOCK_HOLDING)
                self.assertEqual(p['num_bodegas'], 5)
                # Bodegas CON stock (IMP tiene la ficha en 0): las que responden "¿dónde hay?".
                self.assertEqual(p['bodegas'], ['NICK1', 'NICK2', 'PAO1', 'PAO2'])
                self.assertEqual(p['marca'], 'ADIDAS')
                self.assertIn('ADILETTE', p['descripcion'])

    def test_las_palabras_se_combinan_con_y(self):
        # 'adidas' calza con ART y 'suede' con TM-SOLO-B: ninguno tiene las dos.
        status, data = self._buscar(self.vend_a, 'adidas suede')
        self.assertEqual(status, 200, data)
        self.assertEqual(data['productos'], [])

    def test_stock_y_bodegas_son_del_articulo_completo_no_de_la_fila_que_calzo(self):
        # El SKU de la talla 42 solo existe en NICK2 (4 u); el artículo tiene 22.
        status, data = self._buscar(self.vend_a, str(SKU_42))
        self.assertEqual(status, 200, data)
        self.assertTrue(data['productos'], data)
        p = data['productos'][0]
        self.assertEqual(p['articulo'], ART)
        self.assertEqual(p['stock'], STOCK_HOLDING)
        self.assertEqual(p['bodegas'], ['NICK1', 'NICK2', 'PAO1', 'PAO2'])
        self.assertEqual(p['num_bodegas'], 5)

    def test_un_resultado_por_articulo(self):
        """ART tiene 8 variantes (talla × bodega): no puede copar la lista."""
        status, data = self._buscar(self.vend_a, 'TM-')
        self.assertEqual(status, 200, data)
        articulos = [p['articulo'] for p in data['productos']]
        self.assertEqual(len(articulos), len(set(articulos)), articulos)
        self.assertEqual(set(articulos), {
            ART, ART_SOLO_B, 'TM-PROPIO', 'TM-LEGADO', 'TM-DUP-1', 'TM-DUP-2',
            'TM-EDEL-1', 'TM-EDEL-2', 'TM-CASO', 'tm-caso',
        })

    def test_texto_de_un_caracter_no_busca(self):
        status, data = self._buscar(self.vend_a, 'T')
        self.assertEqual(status, 200, data)
        self.assertEqual(data['productos'], [])


class TestPantallaYApiPorUrl(BaseTarjetaHolding):
    """
    La pantalla y las APIs por su URL real (middleware de permisos + plantilla),
    como las llama el navegador. Un vendedor acotado necesita el permiso de la
    pantalla también para sus APIs.
    """

    def _cliente(self, user, sucursal):
        c = Client()
        c.force_login(user)
        s = c.session
        s['idSucursalActual'] = sucursal.id
        s['idEmpresaActual'] = sucursal.empresa_id
        s['alias'] = sucursal.alias
        s.save()
        return c

    def test_pantalla_trae_el_aviso_de_alcance(self):
        r = self._cliente(self.maestro, self.pao1).get('/app/tarjeta-movimiento/')
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        for fragmento in ('id="avisoAlcance"', 'function renderAvisoAlcance',
                          'function bodegaConHistorial', 'b.con_historial === false',
                          'tm-tag-otra', 'alcanceData = data.alcance'):
            with self.subTest(fragmento=fragmento):
                self.assertIn(fragmento, html)

    def test_api_por_url_para_vendedor_con_permiso_de_pantalla(self):
        otorgar_ver_pantalla('vendedor', 'tarjeta_movimiento_producto')
        c = self._cliente(self.vend_a, self.pao1)

        r = c.get('/app/api/tarjeta-movimiento/', {'sku': SKU_38},
                  HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        data = r.json()
        self.assertFalse(data['alcance']['historial_completo'])
        self.assertEqual(set(data['alcance']['bodegas_sin_historial']), ALIAS_B)
        self.assertEqual(data['resumen']['stock_actual'], STOCK_HOLDING)
        self.assertEqual({m['bodega_id'] for m in data['movimientos']}, self.ids_a)

        r = c.get('/app/api/tarjeta-movimiento/buscar/', {'q': 'TM-SOLO'},
                  HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual([p['articulo'] for p in r.json()['productos']], [ART_SOLO_B])


class TestProductoSinSucursal(SimpleTestCase):
    """
    Caso 7 (variante con producto.sucursal = NULL) NO se puede montar:
    `Producto.sucursal` es NOT NULL. Si algún día pasa a admitir NULL, esta
    prueba falla para recordar cubrir que esa variante no sume stock ni
    aparezca en la matriz (`_filtro_alcance_pt(None)` ya la excluye).
    """

    def test_producto_sucursal_es_obligatoria(self):
        self.assertFalse(Producto._meta.get_field('sucursal').null)
