# -*- coding: utf-8 -*-
"""Tests del comando `diagnosticar_cola_categorias_v12` (cola de la v1.2).

Dos cosas se prueban aquí, y las dos importan:

1. **El clasificador de confianza.** Es lo que decide si una categoría plana
   tiene un destino obvio (ALTA), uno razonable (MEDIA) o hay que mirarla a
   mano (REVISAR / BAJA). Se prueban los cuatro escalones por separado, más
   los casos que en producción resultaron ser los interesantes: el bucket
   marcado como MIXTO por la propia v1.2, la categoría deportiva (que en el
   árbol nuevo NO tiene hija equivalente porque el deporte pasó a ser
   Especialidad) y el nombre con el encoding roto.

2. **Que el comando no escriba NADA.** Es un diagnóstico sobre la BD de
   producción: se verifica con `CaptureQueriesContext` que no emite ni un
   INSERT/UPDATE/DELETE, que los conteos quedan idénticos, y que no existe
   bandera alguna para aplicar el mapeo.

Correr con BD desechable:
    $env:DATABASE_URL="sqlite:///C:/temp/td2.sqlite3"; python manage.py test app.tests.test_cola_categorias_v12
"""
import csv
import os
import re
import tempfile
from collections import Counter
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from app.models import (
    Categoria, Dte, Dte_Productos, Producto, Producto_Talla, Ticket,
    Ticket_Productos,
)
from app.management.commands.diagnosticar_cola_categorias_v12 import (
    CAMPOS_CSV, EscrituraProhibida, clasificar, es_talla_calzado,
    evaluar_reparo, forma_por_tallas, guard_solo_lectura, normalizar,
    senales_de_categoria, sub_a_nombre_hija, sub_por_texto, tokens,
)
from .factories import crear_empresa, crear_sucursal, crear_vendedor


# ─────────────────────────────────────────────────── helpers de las pruebas

def _hijas(*pares):
    """[(id, nombre, padre), ...] → lista de dicts como la espera `clasificar`."""
    return [{'id': i, 'nombre': n, 'padre': p} for i, n, p in pares]


def _senales(n, keywords=None, sin_keyword=0, calzado=0, ropa=0, marcas=None):
    """Arma el dict de señales por producto como lo hace `construir_filas`.

    `keywords` es {slug_sub: n_productos}; el resto de los productos cae en
    '(sin keyword)'. `calzado`/`ropa` son cuántos tienen talla de cada forma.
    """
    cnt_kw = Counter(keywords or {})
    if sin_keyword:
        cnt_kw['(sin keyword)'] = sin_keyword
    cnt_forma = Counter()
    if calzado:
        cnt_forma['CALZADO'] = calzado
    if ropa:
        cnt_forma['ROPA'] = ropa
    resto = n - calzado - ropa
    if resto > 0:
        cnt_forma['SIN_TALLA'] = resto
    return senales_de_categoria(n, cnt_kw, cnt_forma, Counter(marcas or {}))


HIJAS_REALES = _hijas(
    (84, 'Zapatillas', 'Calzado'),
    (85, 'Botines', 'Calzado'),
    (86, 'Botas', 'Calzado'),
    (87, 'Sandalias y Chalas', 'Calzado'),
    (88, 'Mocasines', 'Calzado'),
    (89, 'Zapatos de Vestir', 'Calzado'),
    (93, 'Ballerinas', 'Calzado'),
    (96, 'Danza', 'Calzado'),
    (97, 'Poleras y Camisetas', 'Ropa'),
    (98, 'Shorts', 'Ropa'),
    (99, 'Buzos y Pantalones', 'Ropa'),
    (100, 'Chaquetas y Polerones', 'Ropa'),
    (101, 'Trajes de Baño', 'Ropa'),
    (103, 'Bolsos y Mochilas', 'Accesorios'),
    (104, 'Gorros', 'Accesorios'),
    (105, 'Medias y Calcetines', 'Accesorios'),
    (106, 'Accesorios de Vestuario', 'Accesorios'),
)


class NormalizacionTests(TestCase):
    """`normalizar` / `tokens`: la base sobre la que se decide la confianza."""

    def test_normalizar_ignora_case_tildes_y_puntuacion(self):
        self.assertEqual(normalizar('Trajes de Baño'), 'TRAJES DE BANO')
        self.assertEqual(normalizar('BUZO- PANTALONES'), 'BUZO PANTALONES')
        self.assertEqual(normalizar('  Gorros  '), 'GORROS')
        self.assertEqual(normalizar(None), '')

    def test_tokens_singulariza_y_bota_conectores(self):
        self.assertEqual(tokens('BUZO- PANTALONES'), tokens('Buzos y Pantalones'))
        self.assertEqual(tokens('Trajes de Baño'), tokens('TRAJE DE BANO'))
        self.assertEqual(tokens('Mocasines'), frozenset({'MOCASIN'}))
        self.assertEqual(tokens('Botas'), tokens('BOTA'))
        self.assertEqual(tokens('Medias y Calcetines'),
                         frozenset({'MEDIA', 'CALCETIN'}))

    def test_tokens_no_colapsa_palabras_distintas(self):
        # el stem es agresivo, pero no puede fundir tipos distintos
        self.assertNotEqual(tokens('Botas'), tokens('Botines'))
        self.assertNotEqual(tokens('Mallas'), tokens('Medias'))
        self.assertNotEqual(tokens('Zapatillas'), tokens('Zapatos de Vestir'))

    def test_tokens_no_confunde_palabras_cortas(self):
        # 'MES' (len 3) no debe quedar en 'M'; 'DOS' tampoco.
        self.assertEqual(tokens('MES'), frozenset({'MES'}))


class ClasificadorTests(TestCase):
    """Los cuatro niveles de confianza, uno por uno."""

    # ── ALTA: nombre idéntico
    def test_alta_nombre_identico(self):
        r = clasificar(18, 'GORROS', HIJAS_REALES)
        self.assertEqual(r['confianza'], 'ALTA')
        self.assertEqual(r['fuente'], 'exacto')
        self.assertEqual(r['hija'], 'Gorros')
        self.assertEqual(r['padre'], 'Accesorios')

    def test_alta_ignora_tildes_y_case(self):
        r = clasificar(9999, 'trajes de bano', HIJAS_REALES)
        self.assertEqual(r['confianza'], 'ALTA')
        self.assertEqual(r['hija'], 'Trajes de Baño')

    # ── MEDIA: normalización
    def test_media_por_normalizacion_plural(self):
        r = clasificar(13, 'MOCASIN', HIJAS_REALES)
        self.assertEqual(r['confianza'], 'MEDIA')
        self.assertEqual(r['fuente'], 'normalizado')
        self.assertEqual(r['hija'], 'Mocasines')

    def test_media_por_normalizacion_con_conector(self):
        r = clasificar(22, 'BUZO- PANTALONES', HIJAS_REALES)
        self.assertEqual(r['confianza'], 'MEDIA')
        self.assertEqual(r['fuente'], 'normalizado')
        self.assertEqual(r['hija'], 'Buzos y Pantalones')

    # ── MEDIA: contención de tokens
    def test_media_por_token_contenido(self):
        # id inexistente en MAPEO_DIRECTO → el único escalón que puede
        # resolverlo es la contención de tokens.
        r = clasificar(99990, 'CHALAS', HIJAS_REALES)
        self.assertEqual(r['confianza'], 'MEDIA')
        self.assertEqual(r['fuente'], 'token')
        self.assertEqual(r['hija'], 'Sandalias y Chalas')

    def test_el_mapeo_v12_gana_al_token_cuando_ambos_resuelven(self):
        # cat 14 = CHALAS sí está en MAPEO_DIRECTO: manda el mapeo histórico
        # (mismo destino, pero la fuente declarada debe ser la de más autoridad).
        r = clasificar(14, 'CHALAS', HIJAS_REALES)
        self.assertEqual(r['fuente'], 'mapeo_v12')
        self.assertEqual(r['hija'], 'Sandalias y Chalas')

    # ── MEDIA: mapeo histórico v1.2
    def test_media_por_mapeo_v12(self):
        # cat 24 = CALCETAS → (Ropa, 'Calcetines y Medias') → 'Medias y Calcetines'
        r = clasificar(24, 'CALCETAS', HIJAS_REALES)
        self.assertEqual(r['confianza'], 'MEDIA')
        self.assertEqual(r['fuente'], 'mapeo_v12')
        self.assertEqual(r['hija'], 'Medias y Calcetines')

    def test_media_rescata_el_nombre_con_encoding_roto(self):
        # cat 20 en producción se llama 'TRAJE DE BAÃ‘O' (mojibake): ningún
        # escalón de nombre puede calzar, pero el mapeo por id sí.
        r = clasificar(20, 'TRAJE DE BAÃ‘O', HIJAS_REALES)
        self.assertEqual(r['confianza'], 'MEDIA')
        self.assertEqual(r['fuente'], 'mapeo_v12')
        self.assertEqual(r['hija'], 'Trajes de Baño')

    # ── El cajón de sastre del pipeline viejo: `IDS_DEFAULT_ZAPATILLAS` NO es
    #    un calce de nombre, así que por sí solo NO puede salir MEDIA/MAPEAR.
    #    Es la fila más cara de la cola (RAMA CASUAL: 546 productos, 55% del
    #    impacto) y salía MEDIA→Zapatillas sin una sola señal que la apoyara.
    def test_el_default_historico_sin_respaldo_sale_revisar(self):
        senales = _senales(546, keywords={}, sin_keyword=546,
                           calzado=150, ropa=27, marcas={'HUALUNAOTE': 94,
                                                         'NIKE PANAMA': 60,
                                                         'ADIDAS': 43})
        r = clasificar(1, 'RAMA CASUAL', HIJAS_REALES, senales)
        self.assertEqual(r['confianza'], 'REVISAR')
        self.assertEqual(r['fuente'], 'default_v12_sin_respaldo')
        self.assertIsNone(r['hija_id'])
        self.assertEqual(r['hija'], '')
        # la candidata descartada no se pierde: queda anotada
        self.assertEqual(r['candidatas'], ['Zapatillas'])
        self.assertIn('DEFAULT', r['motivo'])

    def test_el_default_sin_ninguna_senal_tampoco_es_mapeable(self):
        # sin `senales` (llamada sólo por nombre) no hay evidencia que evaluar
        r = clasificar(1, 'RAMA CASUAL', HIJAS_REALES)
        self.assertEqual(r['confianza'], 'REVISAR')
        self.assertEqual(r['fuente'], 'default_v12_sin_respaldo')

    def test_el_default_lo_reclasifican_las_keywords_de_los_productos(self):
        # el 80% de los artículos dice BOTIN → manda el producto, no la lista:
        # el destino NO es Zapatillas
        senales = _senales(100, keywords={'botines': 80}, sin_keyword=20,
                           calzado=90, ropa=0)
        r = clasificar(1, 'RAMA CASUAL', HIJAS_REALES, senales)
        self.assertEqual(r['confianza'], 'MEDIA')
        self.assertEqual(r['fuente'], 'productos_keyword')
        self.assertEqual(r['hija'], 'Botines')

    def test_el_default_se_sostiene_si_forma_y_marcas_lo_respaldan(self):
        senales = _senales(100, keywords={}, sin_keyword=100,
                           calzado=85, ropa=0,
                           marcas={'NIKE': 40, 'ADIDAS': 25, 'GENERICA': 35})
        r = clasificar(1, 'RAMA CASUAL', HIJAS_REALES, senales)
        self.assertEqual(r['confianza'], 'MEDIA')
        self.assertEqual(r['fuente'], 'default_v12_respaldado')
        self.assertEqual(r['hija'], 'Zapatillas')

    def test_el_default_no_se_sostiene_con_forma_pero_sin_marcas(self):
        # misma forma de calzado, pero marcas que no son del rubro
        senales = _senales(100, keywords={}, sin_keyword=100,
                           calzado=85, ropa=0,
                           marcas={'HUALUNAOTE': 60, 'AGTA': 40})
        r = clasificar(1, 'RAMA CASUAL', HIJAS_REALES, senales)
        self.assertEqual(r['confianza'], 'REVISAR')
        self.assertEqual(r['fuente'], 'default_v12_sin_respaldo')

    def test_el_default_no_se_sostiene_con_marcas_pero_sin_forma(self):
        # las marcas son de calzado pero sólo 1 de cada 4 tiene talla de zapato
        senales = _senales(100, keywords={}, sin_keyword=100,
                           calzado=25, ropa=0,
                           marcas={'NIKE': 60, 'ADIDAS': 40})
        r = clasificar(1, 'RAMA CASUAL', HIJAS_REALES, senales)
        self.assertEqual(r['confianza'], 'REVISAR')
        self.assertEqual(r['fuente'], 'default_v12_sin_respaldo')

    # ── REVISAR: ambigüedad por homónimas
    def test_revisar_cuando_hay_hijas_homonimas(self):
        hijas = _hijas((1, 'Guantes', 'Accesorios'), (2, 'Guantes', 'Calzado'))
        r = clasificar(9998, 'GUANTES', hijas)
        self.assertEqual(r['confianza'], 'REVISAR')
        self.assertEqual(r['fuente'], 'exacto_ambiguo')
        self.assertIsNone(r['hija_id'])
        self.assertEqual(sorted(r['candidatas']), ['Guantes', 'Guantes'])

    def test_revisar_cuando_el_token_calza_con_varias(self):
        hijas = _hijas((1, 'Medias y Calcetines', 'Accesorios'),
                       (2, 'Medias Deportivas', 'Ropa'))
        r = clasificar(9997, 'MEDIAS', hijas)
        self.assertEqual(r['confianza'], 'REVISAR')
        self.assertEqual(r['fuente'], 'token_ambiguo')
        self.assertEqual(len(r['candidatas']), 2)

    # ── REVISAR: casos propios de la v1.2
    def test_revisar_bucket_mixto_declarado_por_la_v12(self):
        # cat 2 = VESTIR, marcada revisar=True en MAPEO_DIRECTO
        r = clasificar(2, 'VESTIR', HIJAS_REALES)
        self.assertEqual(r['confianza'], 'REVISAR')
        self.assertEqual(r['fuente'], 'mapeo_v12_mixto')
        self.assertEqual(r['hija'], 'Zapatos de Vestir')  # se propone, con reparo
        self.assertIn('MIXTO', r['motivo'])

    def test_revisar_categoria_deportiva_no_tiene_hija(self):
        # cat 9 = RAMA FOOTBALL → (Deportes, Fútbol): en v1.2 el deporte es
        # ESPECIALIDAD, no categoría.
        r = clasificar(9, 'RAMA FOOTBALL', HIJAS_REALES)
        self.assertEqual(r['confianza'], 'REVISAR')
        self.assertEqual(r['fuente'], 'especialidad')
        self.assertIsNone(r['hija_id'])
        self.assertIn('ESPECIALIDAD', r['motivo'])

    # ── BAJA: sin candidata
    def test_baja_sin_candidata(self):
        r = clasificar(33, 'REINA', HIJAS_REALES)
        self.assertEqual(r['confianza'], 'BAJA')
        self.assertEqual(r['fuente'], 'ninguna')
        self.assertIsNone(r['hija_id'])
        self.assertEqual(r['candidatas'], [])

    def test_baja_cuando_el_mapeo_apunta_a_una_hija_que_no_existe(self):
        # cat 34 = GATEADORES → 'Gateadores', que no está en esta lista de hijas
        r = clasificar(34, 'GATEADORES', HIJAS_REALES)
        self.assertEqual(r['confianza'], 'BAJA')
        self.assertIsNone(r['hija_id'])

    def test_toda_confianza_es_de_las_cuatro_declaradas(self):
        for cid, nombre in ((18, 'GORROS'), (13, 'MOCASIN'), (2, 'VESTIR'),
                            (33, 'REINA'), (9, 'RAMA FOOTBALL')):
            r = clasificar(cid, nombre, HIJAS_REALES)
            self.assertIn(r['confianza'], ('ALTA', 'MEDIA', 'REVISAR', 'BAJA'))


class SenalesPorProductoTests(TestCase):
    """Keywords y forma de talla: las pistas para las filas REVISAR/BAJA."""

    def test_keyword_reconoce_los_tipos_v12(self):
        self.assertEqual(sub_por_texto('BOTIN FUTBOL PRO'), 'botines')
        self.assertEqual(sub_por_texto('MOCHILA ESCOLAR'), 'mochilas')
        self.assertEqual(sub_por_texto('POLERON CANGURO'), 'chaquetas')
        self.assertEqual(sub_por_texto('ZAPATILLA URBANA'), 'zapatillas')
        self.assertIsNone(sub_por_texto('SET BUNDESLIGA'))
        self.assertIsNone(sub_por_texto(''))

    def test_keyword_prioriza_lo_especifico(self):
        # 'BOTIN' antes que el genérico 'ZAPATO'
        self.assertEqual(sub_por_texto('ZAPATO BOTIN CUERO'), 'botines')

    def test_sub_a_nombre_hija_usa_la_tabla_v12(self):
        self.assertEqual(sub_a_nombre_hija('zapatillas'), ('Zapatillas', 'Calzado'))
        self.assertEqual(sub_a_nombre_hija('medias'),
                         ('Medias y Calcetines', 'Accesorios'))
        self.assertEqual(sub_a_nombre_hija('no-existe'), (None, None))

    def test_talla_de_calzado_reconoce_las_tres_escalas(self):
        for t in ('38', '40,5', '15', '50', '9,0', '8,5', '650', '900', '1600'):
            self.assertTrue(es_talla_calzado(t), t)

    def test_talla_de_calzado_rechaza_lo_que_no_lo_es(self):
        # '00' es talla única; '10' pelado puede ser ropa infantil; 'M' es ropa;
        # '625' no es múltiplo de 50 (no es la escala US x100).
        for t in ('00', '10', 'M', 'XL', '', None, 'UNICA', '3', '625', '2000'):
            self.assertFalse(es_talla_calzado(t), t)

    def test_forma_por_tallas(self):
        self.assertEqual(forma_por_tallas(['38', '39', '40']), 'CALZADO')
        self.assertEqual(forma_por_tallas(['S', 'M', 'L']), 'ROPA')
        self.assertEqual(forma_por_tallas(['650', '700', '750']), 'CALZADO')
        self.assertEqual(forma_por_tallas(['00', '00']), '')
        self.assertEqual(forma_por_tallas([]), '')
        self.assertEqual(forma_por_tallas(['38', 'M']), '')  # empate → sin señal


class SenalesDeCategoriaTests(TestCase):
    """`senales_de_categoria`: el resumen de lo que dicen los productos."""

    def test_resume_keyword_forma_y_marcas(self):
        s = _senales(200, keywords={'botines': 120, 'botas': 20},
                     sin_keyword=60, calzado=150, ropa=10,
                     marcas={'NIKE': 80, 'HUALUNAOTE': 120})
        self.assertEqual(s['n_productos'], 200)
        self.assertEqual(s['sub_modal'], 'botines')
        self.assertEqual(s['hija_modal'], 'Botines')
        self.assertEqual(s['padre_modal'], 'Calzado')
        self.assertEqual(s['pct_modal'], 60.0)
        self.assertEqual(s['sin_keyword'], 60)
        self.assertEqual(s['pct_sin_keyword'], 30.0)
        self.assertEqual(s['forma_dominante'], 'CALZADO')
        self.assertEqual(s['pct_forma_dominante'], 75.0)
        self.assertEqual(s['marca_modal'], 'HUALUNAOTE')
        # sólo NIKE es marca del rubro calzado según REGLAS_MARCA
        self.assertEqual(s['pct_marcas_calzado'], 40.0)

    def test_forma_mixta_cuando_ninguna_llega_al_70(self):
        s = _senales(100, sin_keyword=100, calzado=50, ropa=50)
        self.assertEqual(s['forma_dominante'], 'MIXTA')

    def test_sin_productos_no_inventa_porcentajes(self):
        s = _senales(0)
        self.assertEqual(s['pct_modal'], 0.0)
        self.assertEqual(s['pct_forma_dominante'], 0.0)
        self.assertEqual(s['forma_dominante'], 'SIN_TALLA')
        self.assertEqual(s['pct_marcas_calzado'], 0.0)


class ReparoTests(TestCase):
    """`evaluar_reparo`: la señal que CONTRADICE la hija propuesta.

    Antes las señales sólo se mostraban cuando NO había destino propuesto —
    o sea, nunca donde hacían falta. Acá se prueba el criterio invertido.
    """

    @staticmethod
    def _prop(hija='Zapatos de Vestir', padre='Calzado', fuente='mapeo_v12'):
        return {'hija': hija, 'padre': padre, 'fuente': fuente,
                'confianza': 'MEDIA', 'hija_id': 1, 'motivo': '', 'candidatas': []}

    def test_sin_hija_propuesta_no_hay_nada_que_contradecir(self):
        nivel, texto = evaluar_reparo(
            {'hija': '', 'padre': '', 'fuente': 'ninguna'},
            _senales(100, sin_keyword=100, ropa=90))
        self.assertEqual((nivel, texto), ('', ''))

    def test_la_forma_contraria_contradice_la_propuesta(self):
        # VESTIR en prod: se propone Calzado y el 44,8% tiene talla de ROPA
        nivel, texto = evaluar_reparo(
            self._prop(), _senales(192, sin_keyword=192, ropa=86, calzado=19))
        self.assertEqual(nivel, 'CONTRADICE')
        self.assertIn('talla ROPA', texto)
        self.assertIn('44.8%', texto)

    def test_la_forma_contraria_sobre_un_producto_es_solo_un_aviso(self):
        # con n=1 un 100% no es una señal: avisa, pero no baja la confianza
        nivel, texto = evaluar_reparo(self._prop(padre='Ropa', hija='Chaquetas y Polerones'),
                                      _senales(1, sin_keyword=1, calzado=1))
        self.assertEqual(nivel, 'AVISO')
        self.assertIn('talla CALZADO', texto)

    def test_la_keyword_a_otro_padre_contradice(self):
        nivel, texto = evaluar_reparo(
            self._prop(hija='Accesorios de Vestuario', padre='Accesorios'),
            _senales(20, keywords={'sandalias': 14}, sin_keyword=6, calzado=14))
        self.assertEqual(nivel, 'CONTRADICE')
        self.assertIn('Sandalias y Chalas', texto)
        self.assertIn('otro padre', texto)

    def test_la_propuesta_sin_ningun_producto_que_la_respalde_avisa(self):
        nivel, texto = evaluar_reparo(
            self._prop(hija='Zapatos de Vestir'),
            _senales(241, sin_keyword=229, keywords={'protecciones': 10,
                                                     'guantes': 2}, calzado=3))
        self.assertEqual(nivel, 'AVISO')
        self.assertIn('no dan ninguna keyword', texto)

    def test_un_nombre_identico_no_necesita_respaldo_de_keywords(self):
        # fuente 'exacto': el nombre ES la evidencia, no se le pide más
        nivel, texto = evaluar_reparo(
            self._prop(hija='Gorros', padre='Accesorios', fuente='exacto'),
            _senales(40, sin_keyword=40))
        self.assertEqual((nivel, texto), ('', ''))

    def test_la_forma_que_coincide_con_el_padre_no_genera_reparo(self):
        nivel, texto = evaluar_reparo(
            self._prop(hija='Botines', padre='Calzado', fuente='exacto'),
            _senales(30, keywords={'botines': 30}, calzado=30))
        self.assertEqual((nivel, texto), ('', ''))


class GuardaEscrituraTests(TestCase):
    """La guarda que hace imposible que el comando escriba."""

    def _ejecutar(self, sql):
        def _fake(sql_, params, many, context):
            return 'ok'
        return guard_solo_lectura(_fake, sql, None, False, None)

    def test_deja_pasar_los_select(self):
        self.assertEqual(self._ejecutar('SELECT 1'), 'ok')
        self.assertEqual(self._ejecutar('  select * from app_categoria'), 'ok')
        self.assertEqual(self._ejecutar('SAVEPOINT s1'), 'ok')

    def test_aborta_ante_cualquier_escritura(self):
        for sql in ('INSERT INTO app_categoria VALUES (1)',
                    'UPDATE app_producto SET categoria_id=1',
                    'delete from app_categoria',
                    'DROP TABLE app_categoria',
                    '  ALTER TABLE app_producto ADD COLUMN x int'):
            with self.assertRaises(EscrituraProhibida):
                self._ejecutar(sql)


class ComandoTests(TestCase):
    """El comando de punta a punta contra una BD desechable."""

    @classmethod
    def setUpTestData(cls):
        # Árbol v1.2 mínimo
        cls.calzado = Categoria.objects.create(nombre='Calzado')
        cls.ropa = Categoria.objects.create(nombre='Ropa')
        cls.zapatillas = Categoria.objects.create(nombre='Zapatillas', padre=cls.calzado)
        cls.botines = Categoria.objects.create(nombre='Botines', padre=cls.calzado)
        cls.poleras = Categoria.objects.create(nombre='Poleras y Camisetas',
                                               padre=cls.ropa)
        # Cola: 3 planas vivas + 1 _ZZ_
        cls.plana_alta = Categoria.objects.create(nombre='BOTINES')
        cls.plana_baja = Categoria.objects.create(nombre='REINA')
        cls.plana_vacia = Categoria.objects.create(nombre='RAMA POOL')
        cls.zz = Categoria.objects.create(nombre='_ZZ_RAMA GOLF')

        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(cls.empresa)
        cls.vendedor = crear_vendedor(empresa=cls.empresa)

        def _producto(cat, articulo, costo, stock, talla, sku, excluir=False):
            p = Producto.objects.create(
                articulo=articulo, descripcion=articulo, sucursal=cls.sucursal,
                costo=costo, sobreprecio=0, precioventa=costo * 2,
                categoria=cat, excluir_de_analitica=excluir,
            )
            pt = Producto_Talla.objects.create(producto=p, sku=sku, stock=stock,
                                               talla=talla)
            return p, pt

        # plana_baja: mucho stock caro → debe encabezar la tabla por impacto
        cls.p_baja, cls.pt_baja = _producto(cls.plana_baja, 'REINA 2990',
                                            50000, 10, '38', 900001)
        # plana_alta: poco stock
        cls.p_alta, cls.pt_alta = _producto(cls.plana_alta, 'BOTIN CUERO',
                                            1000, 2, '40', 900002)
        # producto excluido de analítica: NO debe sumar stock ni venta
        cls.p_excl, cls.pt_excl = _producto(cls.plana_alta, 'BOLSA PAPEL',
                                            99999, 100, '00', 900003, excluir=True)
        # hija v1.2 con stock (para el censo)
        cls.p_hija, _ = _producto(cls.zapatillas, 'ZAPATILLA X',
                                  20000, 5, '42', 900004)

        # Venta por ticket sin DTE sobre la plana de BAJA
        cls.ticket = Ticket.objects.create(
            vendedor=cls.vendedor, sucursal=cls.sucursal, correlativo=1,
            estado='PAGADO', subTotal=120000, total=120000,
            responsable='tester', modulo_origen='POS', dte_generado=False,
        )
        Ticket_Productos.objects.create(
            idTicket=cls.ticket, ProductoTalla=cls.pt_baja, stock=3,
            precio=40000, subtotal=120000,
        )
        # Venta de un producto excluido: no debe aparecer en ningún total
        Ticket_Productos.objects.create(
            idTicket=cls.ticket, ProductoTalla=cls.pt_excl, stock=7,
            precio=1000, subtotal=7000,
        )

    def _correr(self, *args):
        out = StringIO()
        call_command('diagnosticar_cola_categorias_v12', *args, stdout=out,
                     stderr=StringIO())
        return out.getvalue()

    # ── la garantía central
    def test_no_emite_ni_una_escritura(self):
        with CaptureQueriesContext(connection) as ctx:
            self._correr('--sin-csv')
        prohibidas = [q['sql'] for q in ctx.captured_queries
                      if (q['sql'] or '').lstrip().upper().startswith(
                          ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'ALTER',
                           'DROP', 'CREATE'))]
        self.assertEqual(prohibidas, [], 'el comando escribió: %s' % prohibidas)
        self.assertGreater(len(ctx.captured_queries), 0)

    def test_no_cambia_ni_una_fila(self):
        antes = (Categoria.objects.count(), Producto.objects.count(),
                 Producto_Talla.objects.count(),
                 sorted(Categoria.objects.values_list('id', 'nombre', 'padre_id')),
                 sorted(Producto.objects.values_list('id', 'categoria_id')))
        self._correr('--sin-csv', '--incluir-zz', '--incluir-vacias')
        despues = (Categoria.objects.count(), Producto.objects.count(),
                   Producto_Talla.objects.count(),
                   sorted(Categoria.objects.values_list('id', 'nombre', 'padre_id')),
                   sorted(Producto.objects.values_list('id', 'categoria_id')))
        self.assertEqual(antes, despues)

    def test_no_existe_bandera_para_aplicar(self):
        # (`--force` no se prueba: argparse lo acepta como abreviatura del
        # `--force-color` que agrega el propio Django, no es una opción nuestra)
        for flag in ('--aplicar', '--apply', '--escribir', '--write'):
            with self.assertRaises(CommandError, msg=flag):
                self._correr(flag)

    def test_el_parser_no_declara_ninguna_opcion_de_escritura(self):
        from app.management.commands.diagnosticar_cola_categorias_v12 import Command
        parser = Command().create_parser('manage.py',
                                         'diagnosticar_cola_categorias_v12')
        propias = {s for a in parser._actions for s in a.option_strings}
        for prohibida in ('--aplicar', '--apply', '--commit', '--escribir',
                          '--write', '--revertir'):
            self.assertNotIn(prohibida, propias)

    # ── contenido del diagnóstico
    def test_censo_separa_hijas_de_planas(self):
        salida = self._correr('--sin-csv')
        self.assertIn('CENSO DEL ÁRBOL v1.2', salida)
        self.assertIn('hijas v1.2  : 3', salida)
        self.assertIn('Calzado, Ropa', salida)
        # 1 producto en hija / 3 en planas vivas → 25,00%
        self.assertIn('recategorización v1.2 al 25.00% de los productos', salida)
        self.assertIn('(1 en hijas / 3 en planas vivas)', salida)

    def test_prioriza_por_impacto(self):
        filas = self._filas_csv()
        vivas = [f for f in filas if f['estado'] == 'PLANA_VIVA']
        self.assertEqual(vivas[0]['categoria'], 'REINA')
        # $ stock 50.000 * 10 + venta 120.000
        self.assertEqual(int(vivas[0]['stock_costo']), 500000)
        self.assertEqual(int(vivas[0]['venta_monto']), 120000)
        self.assertEqual(int(vivas[0]['impacto_pesos']), 620000)

    def test_excluye_de_los_montos_lo_marcado_fuera_de_analitica(self):
        filas = {f['categoria']: f for f in self._filas_csv()}
        alta = filas['BOTINES']
        self.assertEqual(int(alta['productos']), 2)              # incluye la bolsa
        self.assertEqual(int(alta['productos_excluidos_analitica']), 1)
        self.assertEqual(int(alta['stock_unidades']), 2)         # 100 de la bolsa NO
        self.assertEqual(int(alta['stock_costo']), 2000)
        self.assertEqual(int(alta['venta_monto']), 0)            # los $7.000 NO

    def test_propone_la_hija_y_la_accion(self):
        filas = {f['categoria']: f for f in self._filas_csv()}
        self.assertEqual(filas['BOTINES']['confianza'], 'ALTA')
        self.assertEqual(filas['BOTINES']['hija_sugerida'], 'Botines')
        self.assertEqual(filas['BOTINES']['padre_sugerido'], 'Calzado')
        self.assertEqual(filas['BOTINES']['accion_sugerida'], 'MAPEAR')

        self.assertEqual(filas['REINA']['confianza'], 'BAJA')
        self.assertEqual(filas['REINA']['hija_sugerida'], '')
        self.assertEqual(filas['REINA']['accion_sugerida'], 'REVISAR_MANUAL')
        # la señal de talla sí queda registrada para el humano
        self.assertEqual(filas['REINA']['forma_dominante'], 'CALZADO')

        # plana sin productos: se cierra deprecándola, no mapeándola
        self.assertEqual(filas['RAMA POOL']['accion_sugerida'], 'DEPRECAR_ZZ')
        self.assertEqual(int(filas['RAMA POOL']['productos']), 0)

    def test_las_zz_se_reportan_aparte_y_sin_productos(self):
        filas = {f['categoria']: f for f in self._filas_csv()}
        self.assertEqual(filas['_ZZ_RAMA GOLF']['estado'], 'ZZ_DEPRECADA')
        self.assertEqual(filas['_ZZ_RAMA GOLF']['accion_sugerida'], 'YA_DEPRECADA')
        salida = self._correr('--sin-csv')
        self.assertIn('DEPRECADAS _ZZ_', salida)
        self.assertNotIn('ANOMALÍA', salida)

    def test_zz_con_productos_se_marca_como_anomalia(self):
        Producto.objects.create(
            articulo='HUERFANO', descripcion='HUERFANO', sucursal=self.sucursal,
            costo=1, sobreprecio=0, precioventa=2, categoria=self.zz,
        )
        salida = self._correr('--sin-csv')
        self.assertIn('ANOMALÍA', salida)
        self.assertIn('_ZZ_RAMA GOLF', salida)

    def test_las_hijas_y_los_padres_nunca_entran_a_la_cola(self):
        nombres = {f['categoria'] for f in self._filas_csv()}
        for fuera in ('Calzado', 'Ropa', 'Zapatillas', 'Botines',
                      'Poleras y Camisetas'):
            self.assertNotIn(fuera, nombres)

    # ── CSV
    def test_csv_tiene_las_columnas_declaradas_y_una_fila_por_categoria(self):
        filas = self._filas_csv()
        self.assertEqual(list(filas[0].keys()), CAMPOS_CSV)
        self.assertEqual(len(filas), 4)  # 3 planas vivas + 1 _ZZ_

    def test_sin_csv_no_deja_archivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = os.path.join(tmp, 'no_debe_existir.csv')
            self._correr('--sin-csv', '--csv', destino)
            self.assertFalse(os.path.exists(destino))

    def test_ventana_de_venta_configurable(self):
        # Con --dias 1 el ticket de hoy sigue dentro; el CSV no cambia de forma.
        filas = {f['categoria']: f for f in self._filas_csv('--dias', '1')}
        self.assertEqual(int(filas['REINA']['venta_monto']), 120000)

    def test_el_censo_cuenta_tambien_las_categorias_vacias(self):
        # El censo viejo se armaba iterando los ids que aparecían en algún
        # agregado, así que las categorías sin productos DESAPARECÍAN de la
        # cuenta (en prod imprimía HIJA 28 de 29 y PLANA 48 de 49).
        salida = self._correr('--sin-csv')
        tabla = (salida.split('CENSO DEL ÁRBOL')[1]
                 .split('categorías del árbol SIN')[0])
        censo = {}
        for linea in tabla.splitlines():
            partes = linea.split()
            if partes and partes[0] in ('HIJA', 'PLANA', 'ZZ', 'PADRE'):
                censo[partes[0]] = (int(partes[1]), int(partes[2]))
        self.assertEqual(censo['PADRE'][0], 2)   # Calzado, Ropa
        self.assertEqual(censo['HIJA'], (3, 1))  # 3 hijas, sólo Zapatillas con producto
        self.assertEqual(censo['PLANA'], (3, 2))  # 3 planas, RAMA POOL vacía
        self.assertEqual(censo['ZZ'][0], 1)
        # y las vacías se listan con nombre y apellido
        self.assertIn('RAMA POOL', salida.split('SIN ni un producto')[1])
        self.assertIn('Poleras y Camisetas', salida.split('SIN ni un producto')[1])

    def test_la_concentracion_la_suma_el_comando(self):
        # el informe de la primera pasada sumó a mano y se equivocó en $2.282
        salida = self._correr('--sin-csv')
        linea = [ln for ln in salida.splitlines() if 'CONCENTRACIÓN' in ln][0]
        m = re.search(r'(\d+) de las (\d+) categorías con impacto suman '
                      r'([\d,]+) de ([\d,]+)', linea)
        self.assertIsNotNone(m, linea)
        k, n_con_impacto, acum, total = (int(m.group(1)), int(m.group(2)),
                                         int(m.group(3).replace(',', '')),
                                         int(m.group(4).replace(',', '')))
        impactos = sorted((int(f['impacto_pesos']) for f in self._filas_csv()
                           if f['estado'] == 'PLANA_VIVA'
                           and int(f['impacto_pesos']) > 0), reverse=True)
        self.assertEqual(n_con_impacto, len(impactos))
        self.assertEqual(total, sum(impactos))
        self.assertEqual(acum, sum(impactos[:k]))
        self.assertGreaterEqual(acum, 0.95 * total)

    def test_top_cero_no_imprime_ninguna_fila_y_no_descuadra(self):
        salida = self._correr('--sin-csv', '--top', '0')
        self.assertIn('0 de 2 filas', salida)     # BOTINES y REINA tienen datos
        self.assertIn('… 2 filas más', salida)
        self.assertNotIn('REINA ', salida.split('COLA PRIORIZADA')[1]
                         .split('RESUMEN')[0])

    def test_top_recortado_al_total_no_promete_de_mas(self):
        salida = self._correr('--sin-csv', '--top', '99')
        self.assertIn('2 de 2 filas', salida)
        self.assertNotIn('filas más', salida)

    # ── helper
    def _filas_csv(self, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            destino = os.path.join(tmp, 'cola.csv')
            self._correr('--csv', destino, *extra)
            with open(destino, encoding='utf-8-sig') as fh:
                return list(csv.DictReader(fh))


class ReparoEnLaTablaTests(TestCase):
    """La contradicción tiene que verse en la TABLA, no sólo en el CSV."""

    @classmethod
    def setUpTestData(cls):
        # ids explícitos: 1 y 83 son los de `IDS_DEFAULT_ZAPATILLAS`, y hay que
        # poder ejercitar ese escalón de punta a punta.
        cls.calzado = Categoria.objects.create(id=201, nombre='Calzado')
        cls.ropa = Categoria.objects.create(id=202, nombre='Ropa')
        cls.zapatillas = Categoria.objects.create(id=203, nombre='Zapatillas',
                                                  padre=cls.calzado)
        cls.botines = Categoria.objects.create(id=204, nombre='Botines',
                                               padre=cls.calzado)
        cls.poleras = Categoria.objects.create(id=205, nombre='Poleras y Camisetas',
                                               padre=cls.ropa)
        # nombre IDÉNTICO a una hija de Ropa, pero productos con talla de zapato
        cls.plana_contra = Categoria.objects.create(id=206,
                                                    nombre='POLERAS Y CAMISETAS')
        # el cajón de sastre del pipeline viejo, sin ninguna señal
        cls.plana_default = Categoria.objects.create(id=1, nombre='RAMA CASUAL')
        # el mismo cajón, pero con productos que dicen qué son
        cls.plana_default_kw = Categoria.objects.create(id=83, nombre='Casual')

        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(cls.empresa)

        sku = [800000]

        def _prod(cat, articulo, talla):
            sku[0] += 1
            p = Producto.objects.create(
                articulo=articulo, descripcion=articulo, sucursal=cls.sucursal,
                costo=1000, sobreprecio=0, precioventa=2000, categoria=cat,
            )
            Producto_Talla.objects.create(producto=p, sku=sku[0], stock=1,
                                          talla=talla)
            return p

        for i in range(6):
            _prod(cls.plana_contra, 'SET %d' % i, '38')       # talla de calzado
        for i in range(5):
            _prod(cls.plana_default, 'SET ACE %d' % i, '00')  # ninguna señal
        for i in range(5):
            _prod(cls.plana_default_kw, 'BOTIN CUERO %d' % i, '39')

    def _filas(self, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            destino = os.path.join(tmp, 'cola.csv')
            call_command('diagnosticar_cola_categorias_v12', '--csv', destino,
                         *extra, stdout=StringIO(), stderr=StringIO())
            with open(destino, encoding='utf-8-sig') as fh:
                return {f['categoria']: f for f in csv.DictReader(fh)}

    def test_la_contradiccion_se_imprime_destacada_bajo_la_propuesta(self):
        out = StringIO()
        call_command('diagnosticar_cola_categorias_v12', '--sin-csv',
                     stdout=out, stderr=StringIO())
        salida = out.getvalue()
        self.assertIn('CONTRADICE la propuesta', salida)
        self.assertIn('talla CALZADO', salida)
        # el bloque de la fila con la propuesta lleva el reparo pegado abajo
        bloque = salida.split('POLERAS Y CAMISETAS')[1]
        self.assertIn('CONTRADICE', bloque.split('\n')[1])

    def test_una_propuesta_contradicha_deja_de_ser_accionable(self):
        f = self._filas()['POLERAS Y CAMISETAS']
        self.assertEqual(f['reparo_nivel'], 'CONTRADICE')
        self.assertEqual(f['confianza'], 'REVISAR')
        self.assertEqual(f['accion_sugerida'], 'REVISAR_MANUAL')
        self.assertIn('talla CALZADO', f['reparo'])
        # la propuesta sigue a la vista, pero como pregunta
        self.assertEqual(f['hija_sugerida'], 'Poleras y Camisetas')

    def test_el_default_zapatillas_sin_respaldo_no_sale_como_mapear(self):
        f = self._filas()['RAMA CASUAL']
        self.assertEqual(f['confianza'], 'REVISAR')
        self.assertEqual(f['fuente'], 'default_v12_sin_respaldo')
        self.assertEqual(f['accion_sugerida'], 'REVISAR_MANUAL')
        self.assertEqual(f['hija_sugerida'], '')
        self.assertEqual(f['candidatas'], 'Zapatillas')

    def test_el_default_con_productos_que_hablan_se_reclasifica(self):
        f = self._filas()['Casual']
        self.assertEqual(f['confianza'], 'MEDIA')
        self.assertEqual(f['fuente'], 'productos_keyword')
        self.assertEqual(f['hija_sugerida'], 'Botines')   # NO Zapatillas
        self.assertEqual(f['accion_sugerida'], 'MAPEAR')

    def test_ninguna_fila_mapear_queda_contradicha(self):
        # la garantía que le importa a quien filtre el CSV por accion=MAPEAR
        for nombre, f in self._filas().items():
            if f['accion_sugerida'] == 'MAPEAR':
                self.assertNotEqual(f['reparo_nivel'], 'CONTRADICE', nombre)


class CriterioDeVentaTests(TestCase):
    """La venta se mide con la regla COMPLETA de productos-vendidos.

    Incluye llevar a bruto las líneas de DTE guardadas en neto: sin eso el
    total no es el del reporte y la equivalencia que declara el docstring
    sería falsa.
    """

    @classmethod
    def setUpTestData(cls):
        cls.calzado = Categoria.objects.create(nombre='Calzado')
        Categoria.objects.create(nombre='Zapatillas', padre=cls.calzado)
        cls.plana = Categoria.objects.create(nombre='RAMA TENIS')

        cls.empresa = crear_empresa()
        cls.otra = crear_empresa(nombre='Cliente Test', rut='76.111.222-3')
        cls.sucursal = crear_sucursal(cls.empresa)

        cls.prod = Producto.objects.create(
            articulo='ZAP TENIS', descripcion='ZAP TENIS', sucursal=cls.sucursal,
            costo=1000, sobreprecio=0, precioventa=2000, categoria=cls.plana,
        )
        cls.pt = Producto_Talla.objects.create(producto=cls.prod, sku=910001,
                                               stock=0, talla='40')

    def _dte(self, neto, bruto, monto_linea):
        hoy = timezone.localdate()
        dte = Dte.objects.create(
            emisor=self.empresa, receptor=self.otra, numero_documento=1,
            tipo_documento='FACTURA ELECTRONICA', monto_con_iva=bruto,
            monto_neto=neto, descuento=0, estado_pago='Pendiente',
            estado_dte='EMITIDO', responsable='tester', fecha_emision=hoy,
            fecha_vencimiento=hoy, diasCredito=0, bultos=0,
            unidades_productos=1, tipo_transaccion='VENTA',
            sucursal=self.sucursal, es_nota_credito=False,
        )
        Dte_Productos.objects.create(
            dte=dte, productoTalla=self.pt, descripcion='ZAP TENIS', costo=1000,
            sobreprecio=0, precio=monto_linea, monto_item=monto_linea, stock=1,
        )
        return dte

    def _venta(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = os.path.join(tmp, 'cola.csv')
            call_command('diagnosticar_cola_categorias_v12', '--csv', destino,
                         stdout=StringIO(), stderr=StringIO())
            with open(destino, encoding='utf-8-sig') as fh:
                fila = {f['categoria']: f for f in csv.DictReader(fh)}['RAMA TENIS']
        return int(fila['venta_monto'])

    def test_la_linea_guardada_en_neto_se_lleva_a_bruto(self):
        # la línea suma exactamente el neto de la cabecera → está en neto
        self._dte(neto=100000, bruto=119000, monto_linea=100000)
        self.assertEqual(self._venta(), 119000)

    def test_la_linea_que_ya_viene_bruta_no_se_toca(self):
        # la línea empata con el bruto, no con el neto → no se corrige
        self._dte(neto=100000, bruto=119000, monto_linea=119000)
        self.assertEqual(self._venta(), 119000)

    def test_el_documento_exento_no_se_corrige(self):
        # sin IVA (bruto == neto) no hay factor que aplicar
        self._dte(neto=100000, bruto=100000, monto_linea=100000)
        self.assertEqual(self._venta(), 100000)
