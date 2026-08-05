"""
Tests del reporte "Quiebre de talla por sucursal"
(`app/views_modulo_reportes_tallas.py`).

Lo que se sostiene aquí:

1. QUIEBRE REAL — talla con venta en el período y stock 0 hoy se marca.
2. FALSO POSITIVO — una talla que NUNCA se vendió no se marca como quiebre, ni
   siquiera cuando es core del rubro (ahí se usa `core_sin_stock`, que es otra
   señal). Es la mitad del valor del reporte: si marca todo, no sirve.
3. LA ALERTA ACCIONABLE — el quiebre de la tienda se cruza contra el stock de
   las sucursales `es_compradora` y sale la orden de traspaso.
4. SCOPING — pedir una sucursal de otra empresa devuelve 403.
5. TALLAS CORE derivadas de los datos (no de una lista fija).
6. EL CASO GRAVE — estilo con stock > 0 pero TODAS sus tallas core en cero.
7. DISPONIBILIDAD — `dias_disponible` reconstruido del kardex y la bandera
   `demanda_subestimada` que evita la profecía autocumplida de la curva.
8. AGRUPADOR DE ESTILO — dos fichas duplicadas que sólo difieren en la caja del
   código son UN estilo, con las tallas consolidadas.
"""
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, include, path, reverse
from django.utils import timezone

from app.models import (
    AtributoOpcion, Categoria, Movimientos_Producto, Producto, Producto_Talla,
    Productos_Atributos,
)
from app.views_modulo_reportes_tallas import (
    api_reporte_quiebre_talla, normalizar_talla, ver_reporte_quiebre_talla,
    _dias_con_stock,
)

from .factories import (
    crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario,
)

URL_API = '/app/api/reportes/quiebre-talla/'
URL_PAGINA = '/app/reportes/quiebre-talla/'


# ── URLconf de respaldo ────────────────────────────────────────────────────
# Las dos rutas del reporte todavía no están en `app/urls.py` (las agrega el
# usuario; ver informe). Para que los tests puedan ejercitar las vistas ya
# mismo, este módulo se ofrece como ROOT_URLCONF: incluye el urlconf REAL del
# proyecto y sólo antepone las dos rutas si aún no existen. En cuanto se
# registren en `app/urls.py`, el `reverse` de abajo las encuentra, el respaldo
# deja de aplicarse y los tests pasan a recorrer el ruteo de producción sin
# tocar una línea.
urlpatterns = [path('', include('retailmind.urls'))]


def _ruta_ya_registrada(nombre):
    try:
        reverse(nombre)
        return True
    except NoReverseMatch:
        return False


RUTAS_EN_URLS_PY = _ruta_ya_registrada('api_reporte_quiebre_talla')

if not RUTAS_EN_URLS_PY:
    urlpatterns = [
        path('app/reportes/quiebre-talla/', ver_reporte_quiebre_talla,
             name='ver_reporte_quiebre_talla'),
        path('app/api/reportes/quiebre-talla/', api_reporte_quiebre_talla,
             name='api_reporte_quiebre_talla'),
    ] + urlpatterns


def _patch_permisos():
    """
    Los permisos viven en BD y en un test vacío no hay nada configurado. Se
    fuerzan a True para que lo único capaz de devolver 403 sea el SCOPING, que
    es lo que se está probando. (Parchear el atributo de la clase alcanza
    también al middleware, que referencia la misma clase.)
    """
    return mock.patch(
        'app.decorators.PermisoRol.tiene_permiso', return_value=True
    )


@override_settings(ROOT_URLCONF=__name__)
class BaseQuiebreTalla(TestCase):
    """
    Una empresa con TIENDA + CENTRO DE DISTRIBUCIÓN, y una segunda empresa
    ajena para probar el alcance.
    """

    def setUp(self):
        self.hoy = timezone.localdate()

        # --- Empresa propia: una tienda y un CD que la abastece ---
        self.empresa = crear_empresa(nombre='Empresa Calzado', rut='76.111.111-1')
        self.tienda = crear_sucursal(self.empresa, alias='TIENDA-1',
                                     tipo_sucursal='VENDEDORA')
        self.cd = crear_sucursal(self.empresa, alias='CD-CENTRAL',
                                 tipo_sucursal='CENTRO_DISTRIBUCION',
                                 es_centro_distribucion=True)

        self.user = crear_usuario(username='jefe_tienda', rol='jefe_local')
        crear_empresa_user(self.user, self.empresa, self.tienda)

        # --- Empresa ajena ---
        self.empresa_b = crear_empresa(nombre='Otra Empresa', rut='77.222.222-2')
        self.tienda_b = crear_sucursal(self.empresa_b, alias='TIENDA-AJENA')
        self.user_b = crear_usuario(username='jefe_ajeno', rol='jefe_local')
        crear_empresa_user(self.user_b, self.empresa_b, self.tienda_b)

        # --- Administrador (ve todo por diseño): control de no-regresión ---
        self.admin = crear_usuario(username='admin_holding', rol='administrador')
        crear_empresa_user(self.admin, self.empresa, self.tienda)

        # --- Catálogo base ---
        self.categoria = Categoria.objects.create(nombre='CALZADO')
        atributo = Productos_Atributos.objects.create(nombre='Marca',
                                                      descripcion='Marca')
        self.marca = AtributoOpcion.objects.create(atributo=atributo, valor='NIKE')
        self.marca_otra = AtributoOpcion.objects.create(atributo=atributo,
                                                        valor='ADIDAS')
        self.color = AtributoOpcion.objects.create(atributo=atributo, valor='NEGRO')
        self.color_otro = AtributoOpcion.objects.create(atributo=atributo,
                                                        valor='BLANCO')

        self._sku = 9000000

        self.client = Client()
        self._login(self.user, self.tienda, self.empresa)

    # -- utilidades ---------------------------------------------------------

    def _login(self, usuario, sucursal, empresa):
        self.client.force_login(usuario)
        sesion = self.client.session
        sesion['idSucursalActual'] = sucursal.id
        sesion['idEmpresaActual'] = empresa.id
        sesion.save()

    def _crear_producto(self, sucursal, articulo, tallas, marca=None, color=None,
                        categoria=None, **kwargs):
        """Crea una ficha con varias tallas. `tallas` = {'40': stock, ...}."""
        producto = Producto.objects.create(
            articulo=articulo,
            descripcion=f'{articulo} desc',
            sucursal=sucursal,
            costo=10000, sobreprecio=2000, precioventa=25000,
            categoria=categoria or self.categoria,
            atributo1=marca or self.marca,
            atributo2=color or self.color,
            **kwargs,
        )
        creadas = {}
        for talla, stock in tallas.items():
            self._sku += 1
            creadas[talla] = Producto_Talla.objects.create(
                producto=producto, sku=self._sku, stock=stock, talla=talla,
            )
        return producto, creadas

    def _vender(self, producto_talla, unidades, dias_atras, concepto='VENTA_PUBLICO'):
        """Un movimiento de venta (egreso, cantidad negativa) hace N días."""
        return Movimientos_Producto.objects.create(
            ProductoTalla=producto_talla,
            sucursal_origen=producto_talla.producto.sucursal,
            cantidad=-abs(unidades),
            costo=10000, precio=25000,
            concepto=concepto,
            estado='COMPLETADO',
            fecha=self.hoy - timedelta(days=dias_atras),
        )

    def _get(self, **params):
        params.setdefault('sucursal_id', self.tienda.id)
        with _patch_permisos():
            return self.client.get(URL_API, params)

    def _json(self, **params):
        resp = self._get(**params)
        return resp.status_code, resp.json()

    def _estilo(self, data, articulo):
        for estilo in data['estilos']:
            if estilo['articulo_normalizado'] == articulo.upper():
                return estilo
        return None

    @staticmethod
    def _talla(estilo, talla):
        for fila in estilo['curva']:
            if fila['talla'] == talla:
                return fila
        return None


# ══════════════════════════════════════════════════════════════════════════
# 1. Detección de quiebre
# ══════════════════════════════════════════════════════════════════════════

class DeteccionQuiebreTest(BaseQuiebreTalla):

    def setUp(self):
        super().setUp()
        # ZAP-100: la 40 se vendió y quedó en cero (QUIEBRE), la 41 sigue con
        # stock, la 46 nunca se vendió y está en cero (NO es quiebre).
        self.producto, self.tallas = self._crear_producto(
            self.tienda, 'ZAP-100', {'40': 0, '41': 5, '46': 0})
        self._vender(self.tallas['40'], 12, dias_atras=30)
        self._vender(self.tallas['41'], 8, dias_atras=20)

    def test_detecta_quiebre_real(self):
        status, data = self._json(dias=90)
        self.assertEqual(status, 200)
        self.assertTrue(data['success'])

        estilo = self._estilo(data, 'ZAP-100')
        self.assertIsNotNone(estilo, 'el estilo con quiebre debe aparecer')

        talla_40 = self._talla(estilo, '40')
        self.assertIsNotNone(talla_40)
        self.assertTrue(talla_40['quiebre'])
        self.assertEqual(talla_40['stock'], 0)
        self.assertEqual(talla_40['vendidas'], 12)
        self.assertEqual(data['resumen']['tallas_quebradas'], 1)

    def test_no_marca_quiebre_en_talla_con_stock(self):
        _status, data = self._json(dias=90)
        estilo = self._estilo(data, 'ZAP-100')
        talla_41 = self._talla(estilo, '41')
        self.assertIsNotNone(talla_41)
        self.assertFalse(talla_41['quiebre'])

    def test_no_marca_quiebre_en_talla_que_nunca_se_vendio(self):
        """
        La 46 tiene stock 0 igual que la 40, pero jamás vendió una unidad: no
        es venta perdida, es surtido que este estilo no tiene. No puede
        aparecer marcada (ni siquiera aparecer en la curva).
        """
        _status, data = self._json(dias=90)
        estilo = self._estilo(data, 'ZAP-100')

        quebradas = {t['talla'] for t in estilo['curva'] if t['quiebre']}
        self.assertEqual(quebradas, {'40'})

        talla_46 = self._talla(estilo, '46')
        if talla_46 is not None:          # si apareciera, jamás como quiebre
            self.assertFalse(talla_46['quiebre'])

    def test_min_venta_sube_el_umbral_de_relevancia(self):
        """Con `min_venta` alto, 12 unidades dejan de ser 'venta relevante'."""
        _status, data = self._json(dias=90, min_venta=50)
        self.assertEqual(data['resumen']['tallas_quebradas'], 0)

    def test_venta_fuera_del_periodo_no_cuenta(self):
        """La 40 vendió hace 30 días: con ventana de 7 días no hay quiebre."""
        _status, data = self._json(dias=7)
        self.assertEqual(data['resumen']['tallas_quebradas'], 0)

    def test_producto_excluido_de_analitica_no_aparece(self):
        producto, tallas = self._crear_producto(
            self.tienda, 'ZAP-OCULTO', {'40': 0}, excluir_de_analitica=True)
        self._vender(tallas['40'], 30, dias_atras=10)

        _status, data = self._json(dias=90, solo_quiebres=0)
        self.assertIsNone(self._estilo(data, 'ZAP-OCULTO'))

    def test_devolucion_no_se_cuenta_como_venta(self):
        """
        Sólo los conceptos de `CONCEPTOS_VENTA` alimentan la curva. Un
        reingreso por devolución no puede inventar demanda.
        """
        producto, tallas = self._crear_producto(self.tienda, 'ZAP-DEV', {'44': 0})
        Movimientos_Producto.objects.create(
            ProductoTalla=tallas['44'], sucursal_origen=self.tienda,
            cantidad=5, costo=10000, precio=25000,
            concepto='DEVOLUCION_CLIENTE', estado='COMPLETADO',
            fecha=self.hoy - timedelta(days=5),
        )
        _status, data = self._json(dias=90, solo_quiebres=0)
        self.assertIsNone(self._estilo(data, 'ZAP-DEV'))


# ══════════════════════════════════════════════════════════════════════════
# 2. La alerta accionable: quiebre en tienda + stock en el CD
# ══════════════════════════════════════════════════════════════════════════

class AlertaReposicionDesdeCDTest(BaseQuiebreTalla):

    def setUp(self):
        super().setUp()
        self.producto, self.tallas = self._crear_producto(
            self.tienda, 'ZAP-200', {'40': 0, '41': 0})
        self._vender(self.tallas['40'], 10, dias_atras=25)
        self._vender(self.tallas['41'], 6, dias_atras=25)

        # El CD tiene el MISMO estilo, con el código escrito en otra caja y con
        # espacios: el agrupador normalizado debe reconocerlo igual.
        self._crear_producto(self.cd, ' zap-200 ', {'40': 7, '42': 3})

    def test_quiebre_con_stock_en_cd_se_marca_reponible(self):
        _status, data = self._json(dias=90)
        estilo = self._estilo(data, 'ZAP-200')
        self.assertIsNotNone(estilo)
        self.assertTrue(estilo['reponible'])

        talla_40 = self._talla(estilo, '40')
        self.assertTrue(talla_40['quiebre'])
        self.assertEqual(talla_40['stock_cd'], 7)
        self.assertEqual([r['sucursal'] for r in talla_40['reposicion']],
                         ['CD-CENTRAL'])

    def test_quiebre_sin_stock_en_cd_no_promete_traspaso(self):
        _status, data = self._json(dias=90)
        estilo = self._estilo(data, 'ZAP-200')
        talla_41 = self._talla(estilo, '41')
        self.assertTrue(talla_41['quiebre'])
        self.assertEqual(talla_41['stock_cd'], 0)
        self.assertEqual(talla_41['reposicion'], [])

    def test_resumen_cuenta_los_quiebres_reponibles(self):
        _status, data = self._json(dias=90)
        self.assertEqual(data['resumen']['tallas_quebradas'], 2)
        self.assertEqual(data['resumen']['quiebres_reponibles'], 1)
        self.assertEqual(data['resumen']['unidades_disponibles_cd'], 7)

    def test_stock_de_otra_tienda_no_cuenta_como_reposicion(self):
        """
        Sólo las sucursales `es_compradora` proponen traspaso. El stock de otra
        tienda vendedora no es reposición: está en piso de venta.
        """
        otra_tienda = crear_sucursal(self.empresa, alias='TIENDA-2',
                                     tipo_sucursal='VENDEDORA')
        self._crear_producto(otra_tienda, 'ZAP-200', {'41': 99})

        _status, data = self._json(dias=90)
        estilo = self._estilo(data, 'ZAP-200')
        talla_41 = self._talla(estilo, '41')
        self.assertEqual(talla_41['stock_cd'], 0)

    def test_cd_de_otro_color_no_se_cruza(self):
        """El color es parte del estilo: un par blanco no repone uno negro."""
        self._crear_producto(self.cd, 'ZAP-200', {'41': 50}, color=self.color_otro)

        _status, data = self._json(dias=90)
        estilo = self._estilo(data, 'ZAP-200')
        self.assertEqual(self._talla(estilo, '41')['stock_cd'], 0)

    def test_cd_de_otra_marca_no_se_cruza(self):
        self._crear_producto(self.cd, 'ZAP-200', {'41': 50}, marca=self.marca_otra)

        _status, data = self._json(dias=90)
        estilo = self._estilo(data, 'ZAP-200')
        self.assertEqual(self._talla(estilo, '41')['stock_cd'], 0)


# ══════════════════════════════════════════════════════════════════════════
# 3. Tallas core derivadas de los datos + el caso grave
# ══════════════════════════════════════════════════════════════════════════

class TallasCoreTest(BaseQuiebreTalla):

    def setUp(self):
        super().setUp()
        # Curva del rubro (CALZADO × NIKE) en esta tienda:
        #   40 → 40 uds, 39 → 30 uds, 45 → 2 uds   (total 72)
        # Con umbral 80% (objetivo 57,6) el núcleo es {40, 39}: la 45 es punta.
        self.producto, self.tallas = self._crear_producto(
            self.tienda, 'ZAP-300', {'39': 0, '40': 0, '45': 12})
        self._vender(self.tallas['39'], 30, dias_atras=40)
        self._vender(self.tallas['40'], 40, dias_atras=35)
        self._vender(self.tallas['45'], 2, dias_atras=15)

    def test_core_se_deriva_de_la_venta_no_de_una_lista_fija(self):
        _status, data = self._json(dias=180)
        estilo = self._estilo(data, 'ZAP-300')
        self.assertEqual(set(estilo['tallas_core']), {'39', '40'})
        self.assertTrue(self._talla(estilo, '40')['es_core'])
        self.assertFalse(self._talla(estilo, '45')['es_core'])

    def test_umbral_core_configurable(self):
        """Con 50% el núcleo se encoge a la talla que sola supera la mitad."""
        _status, data = self._json(dias=180, umbral_core=50)
        estilo = self._estilo(data, 'ZAP-300')
        self.assertEqual(set(estilo['tallas_core']), {'40'})

    def test_estilo_con_stock_pero_todas_las_core_en_cero_es_critico(self):
        """
        El caso más grave: 12 unidades en la vitrina (se ve 'sano' en cualquier
        KPI agregado) y ninguna de ellas en las tallas que sostienen la venta.
        """
        _status, data = self._json(dias=180)
        estilo = self._estilo(data, 'ZAP-300')
        self.assertTrue(estilo['critico'])
        self.assertGreater(estilo['stock_total'], 0)
        self.assertEqual(data['resumen']['estilos_criticos'], 1)

    def test_no_es_critico_si_alguna_core_tiene_stock(self):
        Producto_Talla.objects.filter(id=self.tallas['40'].id).update(stock=4)
        _status, data = self._json(dias=180, solo_quiebres=0)
        estilo = self._estilo(data, 'ZAP-300')
        self.assertFalse(estilo['critico'])

    def test_core_sin_venta_propia_no_es_quiebre_pero_si_hueco(self):
        """
        Un estilo nuevo que nunca vendió la 39 (core del rubro) y no la tiene:
        `core_sin_stock` lo señala, pero `quiebre` NO — no hay venta perdida
        demostrable, hay surtido faltante.
        """
        producto, tallas = self._crear_producto(
            self.tienda, 'ZAP-400', {'40': 0, '39': 0})
        self._vender(tallas['40'], 5, dias_atras=10)   # sólo vendió la 40

        _status, data = self._json(dias=180)
        estilo = self._estilo(data, 'ZAP-400')
        talla_39 = self._talla(estilo, '39')
        self.assertIsNotNone(talla_39, 'la core sin stock debe verse')
        self.assertTrue(talla_39['es_core'])
        self.assertTrue(talla_39['core_sin_stock'])
        self.assertFalse(talla_39['quiebre'])
        self.assertEqual(talla_39['vendidas'], 0)


# ══════════════════════════════════════════════════════════════════════════
# 4. Corrección por disponibilidad (la profecía autocumplida)
# ══════════════════════════════════════════════════════════════════════════

class DisponibilidadHistoricaTest(BaseQuiebreTalla):

    def test_dias_disponible_se_reconstruye_del_kardex(self):
        """
        La 40 quedó en cero hace 30 días: con ventana de 90 días debió tener
        stock los 60 días previos (59 + el día del movimiento ya en cero).
        """
        producto, tallas = self._crear_producto(self.tienda, 'ZAP-500', {'40': 0})
        self._vender(tallas['40'], 10, dias_atras=30)

        _status, data = self._json(dias=90)
        talla = self._talla(self._estilo(data, 'ZAP-500'), '40')

        self.assertEqual(talla['dias_periodo'], 90)
        self.assertEqual(talla['dias_disponible'], 59)
        self.assertFalse(talla['demanda_subestimada'])

    def test_disponibilidad_baja_marca_demanda_subestimada(self):
        """
        Vendió 3 unidades y estuvo agotada casi todo el período: esas 3
        unidades NO son la demanda, y el reporte tiene que decirlo antes de que
        alguien compre menos por creerle a la curva cruda.
        """
        producto, tallas = self._crear_producto(self.tienda, 'ZAP-600', {'41': 0})
        self._vender(tallas['41'], 3, dias_atras=85)

        _status, data = self._json(dias=90)
        talla = self._talla(self._estilo(data, 'ZAP-600'), '41')

        self.assertEqual(talla['dias_disponible'], 4)
        self.assertLess(talla['pct_disponible'], 60)
        self.assertTrue(talla['demanda_subestimada'])
        # 3 uds en 4 días de disponibilidad, extrapolado a 90 días.
        self.assertAlmostEqual(talla['venta_ajustada'], 67.5, places=1)
        self.assertGreater(talla['unidades_perdidas'], 0)

    def test_talla_siempre_disponible_no_estima_venta_perdida(self):
        producto, tallas = self._crear_producto(self.tienda, 'ZAP-700', {'42': 20})
        self._vender(tallas['42'], 6, dias_atras=10)

        _status, data = self._json(dias=90, solo_quiebres=0)
        talla = self._talla(self._estilo(data, 'ZAP-700'), '42')

        self.assertEqual(talla['dias_disponible'], 90)
        self.assertEqual(talla['pct_disponible'], 100.0)
        self.assertFalse(talla['demanda_subestimada'])
        self.assertEqual(talla['unidades_perdidas'], 0)

    def test_reconstruccion_incoherente_se_marca_dudosa(self):
        """
        Stock plano 0 pero el kardex dice que entraron unidades y no salieron:
        la reconstrucción pasa por negativo. El número no se oculta, se marca.
        """
        producto, tallas = self._crear_producto(self.tienda, 'ZAP-800', {'43': 0})
        self._vender(tallas['43'], 4, dias_atras=50)
        Movimientos_Producto.objects.create(
            ProductoTalla=tallas['43'], sucursal_origen=self.tienda,
            cantidad=-9, costo=10000, precio=25000,
            concepto='TRASPASO_SALIDA', estado='COMPLETADO',
            fecha=self.hoy - timedelta(days=5),
        )
        # Sin entradas registradas, retroceder desde 0 deja saldos coherentes;
        # forzamos la incoherencia con una salida posterior al stock disponible.
        Movimientos_Producto.objects.create(
            ProductoTalla=tallas['43'], sucursal_origen=self.tienda,
            cantidad=20, costo=10000, precio=25000,
            concepto='RECEPCION_COMPRA', estado='COMPLETADO',
            fecha=self.hoy - timedelta(days=2),
        )
        _status, data = self._json(dias=90, solo_quiebres=0)
        talla = self._talla(self._estilo(data, 'ZAP-800'), '43')
        self.assertTrue(talla['reconstruccion_dudosa'])

    def test_helper_dias_con_stock_sin_movimientos(self):
        """Sin kardex en la ventana, el stock actual se asume constante."""
        desde = self.hoy - timedelta(days=9)
        dias, dudosa = _dias_con_stock(5, {}, desde, self.hoy, self.hoy)
        self.assertEqual(dias, 10)
        self.assertFalse(dudosa)

        dias, dudosa = _dias_con_stock(0, {}, desde, self.hoy, self.hoy)
        self.assertEqual(dias, 0)
        self.assertFalse(dudosa)


# ══════════════════════════════════════════════════════════════════════════
# 5. Agrupador de estilo
# ══════════════════════════════════════════════════════════════════════════

class AgrupadorEstiloTest(BaseQuiebreTalla):

    def test_fichas_duplicadas_se_consolidan_en_un_estilo(self):
        """
        En producción hay fichas duplicadas del mismo código que sólo difieren
        en mayúsculas/espacios. Sin consolidar, la curva de tallas del modelo
        queda partida en dos y ninguna de las dos mitades muestra el quiebre.
        """
        _p1, tallas1 = self._crear_producto(self.tienda, 'ZAP-900', {'40': 0})
        _p2, tallas2 = self._crear_producto(self.tienda, ' zap-900 ', {'41': 3})
        self._vender(tallas1['40'], 10, dias_atras=20)
        self._vender(tallas2['41'], 4, dias_atras=20)

        _status, data = self._json(dias=90)
        coincidencias = [e for e in data['estilos']
                         if e['articulo_normalizado'] == 'ZAP-900']
        self.assertEqual(len(coincidencias), 1, 'debe ser UN solo estilo')

        estilo = coincidencias[0]
        self.assertEqual(estilo['fichas'], 2)
        self.assertEqual(estilo['venta_total'], 14)
        self.assertEqual({t['talla'] for t in estilo['curva']}, {'40', '41'})

    def test_distinto_color_es_otro_estilo(self):
        _p1, tallas1 = self._crear_producto(self.tienda, 'ZAP-950', {'40': 0})
        _p2, tallas2 = self._crear_producto(self.tienda, 'ZAP-950', {'40': 0},
                                            color=self.color_otro)
        self._vender(tallas1['40'], 5, dias_atras=10)
        self._vender(tallas2['40'], 5, dias_atras=10)

        _status, data = self._json(dias=90)
        coincidencias = [e for e in data['estilos']
                         if e['articulo_normalizado'] == 'ZAP-950']
        self.assertEqual(len(coincidencias), 2)
        self.assertEqual({e['color'] for e in coincidencias}, {'NEGRO', 'BLANCO'})

    def test_normalizar_talla(self):
        """Misma talla escrita distinto = una sola columna de la curva."""
        self.assertEqual(normalizar_talla('40'), '40')
        self.assertEqual(normalizar_talla(' 40 '), '40')
        self.assertEqual(normalizar_talla('40.0'), '40')
        self.assertEqual(normalizar_talla('7.5'), '7,5')
        self.assertEqual(normalizar_talla('7,50'), '7,5')
        self.assertEqual(normalizar_talla('xl'), 'XL')
        self.assertEqual(normalizar_talla(''), '—')

    def test_tallas_escritas_distinto_se_consolidan(self):
        _p1, tallas1 = self._crear_producto(self.tienda, 'ZAP-960', {'7,5': 0})
        _p2, tallas2 = self._crear_producto(self.tienda, 'ZAP-960', {'7.5': 0})
        self._vender(tallas1['7,5'], 4, dias_atras=10)
        self._vender(tallas2['7.5'], 6, dias_atras=10)

        _status, data = self._json(dias=90)
        estilo = self._estilo(data, 'ZAP-960')
        self.assertEqual(len(estilo['curva']), 1)
        self.assertEqual(estilo['curva'][0]['talla'], '7,5')
        self.assertEqual(estilo['curva'][0]['vendidas'], 10)


# ══════════════════════════════════════════════════════════════════════════
# 6. Alcance por empresa (scoping)
# ══════════════════════════════════════════════════════════════════════════

class ScopingQuiebreTallaTest(BaseQuiebreTalla):

    def setUp(self):
        super().setUp()
        _p, tallas = self._crear_producto(self.tienda_b, 'ZAP-AJENO', {'40': 0})
        self._vender(tallas['40'], 20, dias_atras=10)

    def test_sucursal_ajena_devuelve_403(self):
        status, data = self._json(sucursal_id=self.tienda_b.id, dias=90)
        self.assertEqual(status, 403)
        self.assertFalse(data['success'])

    def test_usuario_ajeno_no_ve_la_tienda_propia(self):
        self._login(self.user_b, self.tienda_b, self.empresa_b)
        status, data = self._json(sucursal_id=self.tienda.id, dias=90)
        self.assertEqual(status, 403)
        self.assertFalse(data['success'])

    def test_sucursal_invalida_devuelve_403(self):
        status, data = self._json(sucursal_id='no-soy-un-id')
        self.assertEqual(status, 403)
        self.assertFalse(data['success'])

    def test_sin_sucursal_devuelve_400(self):
        with _patch_permisos():
            resp = self.client.get(URL_API, {'dias': 90})
        self.assertEqual(resp.status_code, 400)

    def test_administrador_no_es_bloqueado(self):
        """Control de no-regresión: el rol administrador ve todo por diseño."""
        self._login(self.admin, self.tienda, self.empresa)
        status, data = self._json(sucursal_id=self.tienda_b.id, dias=90)
        self.assertEqual(status, 200)
        self.assertTrue(data['success'])

    def test_cd_fuera_del_alcance_no_aporta_reposicion(self):
        """
        Un CD de otra empresa a la que el usuario NO pertenece no puede
        aparecer como origen de traspaso (filtraría stock ajeno).
        """
        cd_ajeno = crear_sucursal(self.empresa_b, alias='CD-AJENO',
                                  tipo_sucursal='CENTRO_DISTRIBUCION',
                                  es_centro_distribucion=True)
        _p, tallas = self._crear_producto(self.tienda, 'ZAP-XX', {'40': 0})
        self._vender(tallas['40'], 9, dias_atras=10)
        self._crear_producto(cd_ajeno, 'ZAP-XX', {'40': 100})

        _status, data = self._json(dias=90)
        estilo = self._estilo(data, 'ZAP-XX')
        self.assertEqual(self._talla(estilo, '40')['stock_cd'], 0)

    def test_anonimo_no_entra_a_la_pagina(self):
        self.client.logout()
        with _patch_permisos():
            resp = self.client.get(URL_PAGINA)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('next=', resp['Location'])

    def test_pagina_autenticada_responde(self):
        with _patch_permisos():
            resp = self.client.get(URL_PAGINA)
        self.assertEqual(resp.status_code, 200)


# ══════════════════════════════════════════════════════════════════════════
# 7. Tope, paginación y contrato de salida
# ══════════════════════════════════════════════════════════════════════════

class TopeYPaginacionTest(BaseQuiebreTalla):

    def setUp(self):
        super().setUp()
        # 5 estilos, todos con quiebre y con ventas distintas para que el tope
        # se lleve los de MAYOR venta (que es el criterio declarado).
        for i in range(5):
            _p, tallas = self._crear_producto(
                self.tienda, f'ZAP-{100 + i}', {'40': 0})
            self._vender(tallas['40'], 10 + i, dias_atras=10)

    def test_limite_trunca_y_avisa(self):
        _status, data = self._json(dias=90, limite=2)
        self.assertTrue(data['resumen']['truncado'])
        self.assertEqual(data['resumen']['estilos_analizados'], 2)
        self.assertEqual(data['resumen']['estilos_totales'], 5)
        self.assertTrue(any('TRUNCADO' in a for a in data['avisos']))

    def test_limite_toma_los_de_mayor_venta(self):
        _status, data = self._json(dias=90, limite=2)
        articulos = {e['articulo_normalizado'] for e in data['estilos']}
        self.assertEqual(articulos, {'ZAP-104', 'ZAP-103'})

    def test_paginacion(self):
        _status, data = self._json(dias=90, page_size=2, page=1)
        self.assertEqual(data['paginacion']['total'], 5)
        self.assertEqual(data['paginacion']['total_paginas'], 3)
        self.assertEqual(len(data['estilos']), 2)

        _status, pagina3 = self._json(dias=90, page_size=2, page=3)
        self.assertEqual(len(pagina3['estilos']), 1)

        vistos = {e['articulo_normalizado'] for e in data['estilos']}
        vistos |= {e['articulo_normalizado'] for e in pagina3['estilos']}
        self.assertEqual(len(vistos), 3)

    def test_sin_ventas_en_el_periodo_responde_vacio_con_aviso(self):
        _status, data = self._json(dias=90, desde='2001-01-01', hasta='2001-03-01')
        self.assertEqual(data['estilos'], [])
        self.assertEqual(data['resumen']['tallas_quebradas'], 0)
        self.assertTrue(any('Sin ventas' in a for a in data['avisos']))

    def test_el_numero_de_queries_no_crece_con_el_catalogo(self):
        """
        Guardia anti N+1: el reporte agrega en la BD (`values().annotate()`), así
        que la cantidad de consultas tiene que ser la MISMA con 5 estilos que
        con 25. Si alguien mete un `.get()` dentro del armado de la curva, este
        test lo caza antes de que el reporte se caiga con el catálogo real.
        """
        with _patch_permisos():
            # Llamada de calentamiento: deja resueltas sesión y usuario para que
            # la comparación mida sólo las consultas del reporte.
            self.client.get(URL_API, {'sucursal_id': self.tienda.id, 'dias': 90})

            with CaptureQueriesContext(connection) as base:
                self.client.get(URL_API, {'sucursal_id': self.tienda.id,
                                          'dias': 90, 'solo_quiebres': 0})

            # 20 estilos más, cada uno con su propia talla quebrada.
            for i in range(20):
                _p, tallas = self._crear_producto(
                    self.tienda, f'ZAP-2{i:02d}', {'40': 0, '41': 2})
                self._vender(tallas['40'], 3, dias_atras=12)

            with CaptureQueriesContext(connection) as ampliado:
                self.client.get(URL_API, {'sucursal_id': self.tienda.id,
                                          'dias': 90, 'solo_quiebres': 0})

        self.assertEqual(
            len(ampliado.captured_queries), len(base.captured_queries),
            'el reporte hace más consultas al crecer el catálogo: hay un N+1',
        )

    def test_parametros_se_devuelven_para_auditar_el_numero(self):
        _status, data = self._json(dias=45, umbral_core=70, min_venta=2)
        params = data['parametros']
        self.assertEqual(params['dias_periodo'], 45)
        self.assertEqual(params['umbral_core'], 70)
        self.assertEqual(params['min_venta'], 2)
        self.assertEqual(params['sucursal'], 'TIENDA-1')
