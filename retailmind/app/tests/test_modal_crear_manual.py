"""
Tests del modal "Crear Producto Manual" de Gestión de Productos.

Cubren los dos defectos que dejaban el formulario a medias al copiar un
producto de la lista, y la sugerencia por proveedor:

1. `buscar_productos_existentes` devolvía los atributos SÓLO por nombre. El
   modal selecciona por ID (`producto.marca_id`), así que marca, color, género y
   categoría llegaban `undefined` y no se copiaba nada salvo el artículo y los
   precios. Tampoco viajaba la especialidad (taxonomía v1.2).
2. El mismo endpoint exponía el catálogo de TODO el holding — costo y PVP
   incluidos — a cualquier usuario autenticado.
3. `sugerencias_por_proveedor` es nuevo: propone marca/categoría/especialidad
   con lo que ese proveedor ya nos vendió, con fallback a las órdenes de compra
   para los proveedores cuyos DTE no traen líneas.
4. Búsqueda avanzada del buscador del modal: multi-término (AND sobre
   artículo/descripción/marca/SKU), marca como filtro adicional, totales
   honestos y "ficha más reciente primero"; y `sugerencia_sku` cuando lo
   tecleado en Identificación es un SKU y no un código de artículo.
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from app.models import (
    AtributoOpcion, Categoria, Compras, Compras_Producto, Dte, Dte_Productos,
    Movimientos_Producto, Producto_Talla, ProductoAtributoValor,
    Productos_Atributos,
)
from .factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla,
    crear_sucursal, crear_usuario,
)


def _patch_permisos():
    """El middleware consulta permisos que una BD de test no tiene: se fuerza a
    True para que lo único capaz de devolver 403 sea el scoping bajo prueba."""
    return mock.patch(
        'app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True
    )


class BaseModalManual(TestCase):
    """Dos empresas del mismo holding + un proveedor con historia de compras."""

    def setUp(self):
        self.empresa_a = crear_empresa(nombre='Empresa A', rut='76.111.111-1')
        self.sucursal_a = crear_sucursal(self.empresa_a, alias='TIENDA-A')
        self.user_a = crear_usuario(username='jefe_a', rol='jefe_local')
        crear_empresa_user(self.user_a, self.empresa_a, self.sucursal_a)

        self.empresa_b = crear_empresa(nombre='Empresa B', rut='77.222.222-2')
        self.sucursal_b = crear_sucursal(self.empresa_b, alias='TIENDA-B')
        self.user_b = crear_usuario(username='jefe_b', rol='jefe_local')
        crear_empresa_user(self.user_b, self.empresa_b, self.sucursal_b)

        self.proveedor = crear_empresa(nombre='ADIDAS CHILE', rut='78.333.333-3',
                                       esProveedor=True)

        self.attr_marca = Productos_Atributos.objects.create(nombre='Marca')
        self.attr_esp = Productos_Atributos.objects.create(nombre='Especialidad')
        self.op_marca = AtributoOpcion.objects.create(atributo=self.attr_marca,
                                                      valor='ADIDAS')
        self.op_esp = AtributoOpcion.objects.create(atributo=self.attr_esp,
                                                    valor='running')
        self.categoria = Categoria.objects.create(nombre='Zapatillas')

        self.prod_a, self.talla_a = crear_producto_con_talla(
            self.sucursal_a, articulo='PREDATOR ELITE', sku=1000001, stock=5,
        )
        self.prod_a.atributo1 = self.op_marca
        self.prod_a.categoria = self.categoria
        self.prod_a.save()
        ProductoAtributoValor.objects.create(
            producto=self.prod_a, atributo=self.attr_esp, opcion=self.op_esp,
        )

        self.prod_b, self.talla_b = crear_producto_con_talla(
            self.sucursal_b, articulo='PREDATOR AJENO', sku=2000001, stock=5,
        )

        self.client = Client()

    def _get(self, user, url, **params):
        self.client.force_login(user)
        with _patch_permisos():
            resp = self.client.get(url, params)
        return resp.status_code, json.loads(resp.content or b'{}')


class BuscarProductosExistentesTest(BaseModalManual):
    URL = '/app/buscar_productos_existentes/'

    def test_devuelve_ids_para_que_el_modal_pueda_seleccionar(self):
        """El modal selecciona por ID: sin IDs, Copiar datos no copiaba nada."""
        status, data = self._get(self.user_a, self.URL, q='PREDATOR ELITE')
        self.assertEqual(status, 200)
        self.assertTrue(data['success'])
        prod = data['productos'][0]
        self.assertEqual(prod['marca_id'], self.op_marca.id)
        self.assertEqual(prod['marca'], 'ADIDAS')
        self.assertEqual(prod['categoria_id'], self.categoria.id)
        self.assertIn('color_id', prod)
        self.assertIn('genero_id', prod)

    def test_incluye_especialidad_v12(self):
        status, data = self._get(self.user_a, self.URL, q='PREDATOR ELITE')
        prod = data['productos'][0]
        self.assertEqual(
            prod['especialidades'],
            [{'id': self.op_esp.id, 'valor': 'running'}],
        )

    def test_producto_sin_especialidad_devuelve_lista_vacia(self):
        crear_producto_con_talla(
            self.sucursal_a, articulo='SIN ESPECIALIDAD', sku=1000009, stock=1,
        )
        status, data = self._get(self.user_a, self.URL, q='SIN ESPECIALIDAD')
        self.assertEqual(data['productos'][0]['especialidades'], [])

    def test_no_ve_productos_de_otra_empresa(self):
        status, data = self._get(self.user_a, self.URL, q='PREDATOR')
        articulos = [p['articulo'] for p in data['productos']]
        self.assertIn('PREDATOR ELITE', articulos)
        self.assertNotIn('PREDATOR AJENO', articulos)

    def test_sin_n_mas_uno_al_crecer_el_catalogo(self):
        """Las tallas salen del prefetch: más productos no son más queries.
        (7 = sesión+user, conteo acotado, productos, tallas, atributo
        Especialidad y sus valores.)"""
        self.client.force_login(self.user_a)
        with _patch_permisos():
            self.client.get(self.URL, {'q': 'PREDATOR ELITE'})  # calienta sesión
            with self.assertNumQueries(7):
                self.client.get(self.URL, {'q': 'PREDATOR ELITE'})
            for i in range(8):
                crear_producto_con_talla(
                    self.sucursal_a, articulo='PREDATOR ELITE %d' % i,
                    sku=1000100 + i, stock=1,
                )
            with self.assertNumQueries(7):
                self.client.get(self.URL, {'q': 'PREDATOR ELITE'})

    def test_termino_vacio_400(self):
        status, _ = self._get(self.user_a, self.URL, q='')
        self.assertEqual(status, 400)


class SugerenciasPorProveedorTest(BaseModalManual):
    URL = '/app/api/sugerencias-proveedor/'

    def _dte_compra_con_linea(self):
        dte = Dte.objects.create(
            emisor=self.proveedor, receptor=self.empresa_a, sucursal=self.sucursal_a,
            tipo_documento='FACTURA ELECTRONICA', tipo_transaccion='COMPRA',
            numero_documento=5001,
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            diasCredito=0, bultos=1, unidades_productos=3,
            responsable='tester', estado_pago='PENDIENTE',
            monto_neto=1000, monto_con_iva=1190, estado_dte='ACEPTADO',
        )
        Dte_Productos.objects.create(
            dte=dte, productoTalla=self.talla_a, descripcion='PREDATOR ELITE',
            costo=1000, sobreprecio=0, precio=2000, stock=3, activo=True,
        )
        return dte

    def test_sugiere_marca_categoria_y_especialidad_desde_dte(self):
        self._dte_compra_con_linea()
        status, data = self._get(self.user_a, self.URL,
                                 proveedor_id=self.proveedor.id)
        self.assertEqual(status, 200)
        self.assertEqual(data['fuente'], 'dte')
        self.assertEqual([m['valor'] for m in data['marcas']], ['ADIDAS'])
        self.assertEqual([c['nombre'] for c in data['categorias']], ['Zapatillas'])
        self.assertEqual([e['valor'] for e in data['especialidades']], ['running'])

    def test_fallback_a_orden_de_compra_si_el_dte_no_trae_lineas(self):
        """El 74% de las compras 2026 son cabecera sin líneas (migración)."""
        compra = Compras.objects.create(
            empresa=self.proveedor, nombre='OC-1', correlativo=1,
            fecha=timezone.localdate(),
        )
        Compras_Producto.objects.create(
            compras=compra, nombre='PREDATOR', atributo1='ADIDAS',
            costo=1000, precioSugerido=2000, fecha=timezone.localdate(),
        )
        status, data = self._get(self.user_a, self.URL,
                                 proveedor_id=self.proveedor.id)
        self.assertEqual(status, 200)
        self.assertEqual(data['fuente'], 'orden_compra')
        self.assertEqual([m['valor'] for m in data['marcas']], ['ADIDAS'])
        self.assertEqual(data['marcas'][0]['id'], self.op_marca.id)

    def test_proveedor_sin_historia_no_sugiere_nada(self):
        otro = crear_empresa(nombre='PROVEEDOR NUEVO', rut='79.444.444-4',
                             esProveedor=True)
        status, data = self._get(self.user_a, self.URL, proveedor_id=otro.id)
        self.assertEqual(status, 200)
        self.assertEqual(data['marcas'], [])
        self.assertEqual(data['categorias'], [])

    def test_no_usa_compras_de_otra_empresa_del_holding(self):
        """La sugerencia sale de MIS compras, no de las de la empresa vecina."""
        self._dte_compra_con_linea()
        status, data = self._get(self.user_b, self.URL,
                                 proveedor_id=self.proveedor.id)
        self.assertEqual(status, 200)
        self.assertEqual(data['marcas'], [])

    def test_proveedor_id_obligatorio(self):
        status, _ = self._get(self.user_a, self.URL)
        self.assertEqual(status, 400)

    def test_proveedor_id_invalido_400(self):
        status, _ = self._get(self.user_a, self.URL, proveedor_id='abc')
        self.assertEqual(status, 400)


class BusquedaAvanzadaModalTest(BaseModalManual):
    """Multi-término, SKU, filtro por marca, totales y orden por reciente."""

    URL = '/app/buscar_productos_existentes/'

    def setUp(self):
        super().setUp()
        self.op_passer = AtributoOpcion.objects.create(atributo=self.attr_marca,
                                                       valor='PASSER')
        self.op_nike = AtributoOpcion.objects.create(atributo=self.attr_marca,
                                                     valor='NIKE')
        # El caso real del usuario: "419" es un código de PASSER, pero también
        # aparece DENTRO de códigos de otras marcas (CW3419-006 de NIKE).
        self.prod_419, self.talla_419 = crear_producto_con_talla(
            self.sucursal_a, articulo='419', sku=4800769, stock=3,
        )
        self.prod_419.atributo1 = self.op_passer
        self.prod_419.save()
        self.prod_nike, _ = crear_producto_con_talla(
            self.sucursal_a, articulo='CW3419-006', sku=4800100, stock=2,
        )
        self.prod_nike.atributo1 = self.op_nike
        self.prod_nike.save()

    def test_multi_termino_cruza_marca_y_codigo(self):
        """"passer 419" encuentra el 419 de PASSER aunque 'PASSER' no esté en
        el artículo ni en la descripción (la marca es un atributo)."""
        status, data = self._get(self.user_a, self.URL, q='passer 419')
        self.assertEqual(status, 200)
        self.assertEqual([p['articulo'] for p in data['productos']], ['419'])

    def test_marca_como_filtro_adicional(self):
        status, data = self._get(self.user_a, self.URL, q='419',
                                 marca=self.op_passer.id)
        self.assertEqual([p['articulo'] for p in data['productos']], ['419'])

    def test_sin_marca_trae_todas_las_coincidencias(self):
        status, data = self._get(self.user_a, self.URL, q='419')
        self.assertEqual({p['articulo'] for p in data['productos']},
                         {'419', 'CW3419-006'})

    def test_busca_por_sku_exacto(self):
        """Antes buscar un SKU no encontraba nada (sólo miraba artículo y
        descripción)."""
        status, data = self._get(self.user_a, self.URL, q='4800769')
        self.assertEqual([p['articulo'] for p in data['productos']], ['419'])

    def test_codigo_exacto_primero_luego_mas_recientes(self):
        """El código exacto manda; dentro del mismo tramo de relevancia va
        primero la ficha creada MÁS recientemente (orden canónico, el mismo de
        la verificación de identidad)."""
        crear_producto_con_talla(
            self.sucursal_a, articulo='419 CLASICO', sku=4800200, stock=1,
        )
        crear_producto_con_talla(
            self.sucursal_a, articulo='419 REEDICION', sku=4800300, stock=1,
        )
        status, data = self._get(self.user_a, self.URL, q='419')
        articulos = [p['articulo'] for p in data['productos']]
        self.assertEqual(articulos[0], '419')
        self.assertLess(articulos.index('419 REEDICION'),
                        articulos.index('419 CLASICO'))

    def test_total_y_hay_mas_con_limite(self):
        for i in range(5):
            crear_producto_con_talla(
                self.sucursal_a, articulo='419-%d' % i, sku=4801000 + i, stock=1,
            )
        status, data = self._get(self.user_a, self.URL, q='419', limite=3)
        self.assertEqual(len(data['productos']), 3)
        self.assertEqual(data['total'], 7)  # 419, CW3419-006 y los 5 nuevos
        self.assertTrue(data['hay_mas'])
        self.assertFalse(data['conteo_parcial'])


class SugerenciaSkuEnIdentificacionTest(BaseModalManual):
    """Teclear/escanear un SKU en "Identificación" decía "código NUEVO" y
    nacía un duplicado con el SKU como código: ahora el backend avisa a qué
    artículo pertenece ese SKU."""

    URL = '/app/verificar_producto_existente/'

    def test_sku_tecleado_sugiere_el_articulo_real(self):
        status, data = self._get(self.user_a, self.URL, articulo='1000001')
        self.assertEqual(status, 200)
        self.assertFalse(data['existe'])
        s = data['sugerencia_sku']
        self.assertEqual(s['articulo'], 'PREDATOR ELITE')
        self.assertEqual(s['sku'], 1000001)
        self.assertEqual(s['talla'], '42')

    def test_numero_que_no_es_sku_no_sugiere_nada(self):
        status, data = self._get(self.user_a, self.URL, articulo='999999999')
        self.assertFalse(data['existe'])
        self.assertIsNone(data['sugerencia_sku'])

    def test_codigo_no_numerico_no_consulta_skus(self):
        status, data = self._get(self.user_a, self.URL, articulo='INEXISTENTE-XL')
        self.assertFalse(data['existe'])
        self.assertIsNone(data['sugerencia_sku'])


class TallasRepetidasVerificacionTest(BaseModalManual):
    """La consolidación de códigos (consolidar_cueca) dejó fichas con la MISMA
    talla varias veces con SKUs distintos: el backend debe marcar cuál fila es
    la vigente (más stock; si todo está en 0, la de kardex más reciente) para
    que el operador sepa en cuál cargar el stock."""

    URL = '/app/verificar_producto_existente/'

    def setUp(self):
        super().setUp()
        self.prod_cueca, self.t35_a = crear_producto_con_talla(
            self.sucursal_a, articulo='CUECA NEGRA', talla='35',
            sku=4900001, stock=0,
        )
        self.t35_b = Producto_Talla.objects.create(
            producto=self.prod_cueca, talla='35', sku=4900002, stock=4,
        )
        self.t36 = Producto_Talla.objects.create(
            producto=self.prod_cueca, talla='36', sku=4900003, stock=1,
        )

    def _tallas(self):
        status, data = self._get(self.user_a, self.URL, articulo='CUECA NEGRA')
        self.assertEqual(status, 200)
        self.assertTrue(data['existe'])
        return {t['sku']: t for t in data['tallas_existentes']}

    def test_marca_la_repetida_con_mas_stock(self):
        tallas = self._tallas()
        self.assertTrue(tallas[4900001]['talla_repetida'])
        self.assertTrue(tallas[4900002]['talla_repetida'])
        self.assertFalse(tallas[4900001]['recomendada'])
        self.assertTrue(tallas[4900002]['recomendada'])
        # La talla única no se marca
        self.assertFalse(tallas[4900003]['talla_repetida'])
        self.assertFalse(tallas[4900003]['recomendada'])

    def test_con_todo_en_cero_gana_el_movimiento_mas_reciente(self):
        Producto_Talla.objects.filter(id=self.t35_b.id).update(stock=0)
        hoy = timezone.localdate()
        Movimientos_Producto.objects.create(
            ProductoTalla=self.t35_a, cantidad=-1,
            fecha=hoy - timedelta(days=10), tipo_movimiento='EGRESO',
        )
        Movimientos_Producto.objects.create(
            ProductoTalla=self.t35_b, cantidad=-1,
            fecha=hoy - timedelta(days=200), tipo_movimiento='EGRESO',
        )
        tallas = self._tallas()
        self.assertTrue(tallas[4900001]['recomendada'])
        self.assertFalse(tallas[4900002]['recomendada'])

    def test_unidades_12m_ignora_movimientos_viejos(self):
        hoy = timezone.localdate()
        Movimientos_Producto.objects.create(
            ProductoTalla=self.t35_b, cantidad=-3,
            fecha=hoy - timedelta(days=30), tipo_movimiento='EGRESO',
        )
        Movimientos_Producto.objects.create(
            ProductoTalla=self.t35_b, cantidad=-9,
            fecha=hoy - timedelta(days=400), tipo_movimiento='EGRESO',
        )
        tallas = self._tallas()
        self.assertEqual(tallas[4900002]['unidades_12m'], 3)
        self.assertEqual(
            tallas[4900002]['ultimo_mov'],
            (hoy - timedelta(days=30)).strftime('%d/%m/%Y'),
        )

    def test_ficha_sin_repetidas_no_marca_nada(self):
        status, data = self._get(self.user_a, self.URL, articulo='PREDATOR ELITE')
        self.assertTrue(data['existe'])
        t = data['tallas_existentes'][0]
        self.assertFalse(t['talla_repetida'])
        self.assertFalse(t['recomendada'])
