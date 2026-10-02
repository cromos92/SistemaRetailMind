"""
Tests de la lógica pura del endpoint externo GET /api/precios-referencia/
(app/services/precios_referencia.py). Sin BD: SimpleTestCase, mismo estilo que
TestCuadraturaDetalleNeto en test_txt_dte.py.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.test import SimpleTestCase

from app.services.precios_referencia import (
    FUENTE_HISTORIAL,
    FUENTE_LIQUIDACION,
    FUENTE_PVP_ACTUAL,
    armar_referencia_sku,
    descuento_vigente_pct,
    elegir_liquidacion,
    es_cambio_pvp,
    precio_original_referencia,
    pvp_maximo_en_ventana,
    restar_meses,
    ultima_rebaja,
    ultimo_cambio,
)

UTC = dt_timezone.utc


def _dt(anio, mes, dia, hora=12):
    return datetime(anio, mes, dia, hora, 0, tzinfo=UTC)


def _cambio(id_, anterior, nuevo, fecha, motivo='Edición rápida', tipo='MANUAL',
            usuario='jtebes'):
    return {
        'id': id_, 'precio_anterior': anterior, 'precio_nuevo': nuevo,
        'tipo_cambio': tipo, 'fecha_cambio': fecha, 'motivo': motivo,
        'usuario': usuario,
    }


class TestEsCambioPvp(SimpleTestCase):
    def test_etiquetas_de_costo_y_sobreprecio_no_son_pvp(self):
        self.assertFalse(es_cambio_pvp('[COSTO] Recepción factura 123'))
        self.assertFalse(es_cambio_pvp('[SOBREPRECIO] ajuste'))
        self.assertFalse(es_cambio_pvp('  [COSTO] con espacios'))

    def test_motivo_autogenerado_sin_texto_libre(self):
        self.assertFalse(es_cambio_pvp('Cambio de COSTO'))
        self.assertFalse(es_cambio_pvp('Cambio de SOBREPRECIO'))
        self.assertTrue(es_cambio_pvp('Cambio de PRECIO_VENTA'))

    def test_filas_de_pvp(self):
        self.assertTrue(es_cambio_pvp('[PRECIO_VENTA] Edición masiva'))
        self.assertTrue(es_cambio_pvp('Sincronización automática desde edición rápida en PAO1'))
        self.assertTrue(es_cambio_pvp('Campaña #12 Liquidación invierno'))
        self.assertTrue(es_cambio_pvp(None))
        self.assertTrue(es_cambio_pvp(''))

    def test_texto_libre_que_menciona_costo_sigue_siendo_pvp(self):
        # Edición rápida: el usuario escribe el motivo; la fila es de precioventa.
        self.assertTrue(es_cambio_pvp('cambio de costo del proveedor'))
        self.assertTrue(es_cambio_pvp('Cambio de COSTO y precio por proveedor'))


class TestRestarMeses(SimpleTestCase):
    def test_doce_meses(self):
        self.assertEqual(restar_meses(_dt(2026, 10, 2), 12), _dt(2025, 10, 2))

    def test_cruza_anio(self):
        self.assertEqual(restar_meses(_dt(2026, 1, 15), 1), _dt(2025, 12, 15))
        self.assertEqual(restar_meses(_dt(2026, 2, 10), 26), _dt(2023, 12, 10))

    def test_acota_dia_a_fin_de_mes(self):
        self.assertEqual(restar_meses(_dt(2026, 3, 31), 1), _dt(2026, 2, 28))
        self.assertEqual(restar_meses(_dt(2024, 3, 31), 1), _dt(2024, 2, 29))

    def test_conserva_hora_y_zona(self):
        tz = dt_timezone(timedelta(hours=-3))
        origen = datetime(2026, 10, 2, 18, 45, tzinfo=tz)
        resultado = restar_meses(origen, 6)
        self.assertEqual(resultado, datetime(2026, 4, 2, 18, 45, tzinfo=tz))
        self.assertEqual(resultado.tzinfo, tz)


class TestUltimoCambioYRebaja(SimpleTestCase):
    def test_ultimo_cambio_por_fecha_y_desempate_por_id(self):
        a = _cambio(1, 100, 90, _dt(2026, 5, 1))
        b = _cambio(7, 90, 80, _dt(2026, 6, 1))
        c = _cambio(9, 90, 80, _dt(2026, 6, 1))  # misma fecha (sync de otra ficha)
        self.assertIs(ultimo_cambio([b, a, c]), c)
        self.assertIsNone(ultimo_cambio([]))

    def test_ultima_rebaja_ignora_subidas(self):
        rebaja = _cambio(1, 59990, 49990, _dt(2026, 3, 1))
        subida = _cambio(2, 49990, 54990, _dt(2026, 8, 1))
        self.assertIs(ultima_rebaja([subida, rebaja]), rebaja)

    def test_sin_rebajas(self):
        self.assertIsNone(ultima_rebaja([_cambio(1, 100, 120, _dt(2026, 1, 1))]))
        self.assertIsNone(ultima_rebaja([]))


class TestPvpMaximoEnVentana(SimpleTestCase):
    DESDE = _dt(2025, 10, 2)

    def test_considera_precio_anterior(self):
        cambios = [_cambio(1, 69990, 49990, _dt(2026, 3, 1))]
        self.assertEqual(
            pvp_maximo_en_ventana(cambios, self.DESDE),
            {'precio': 69990, 'fecha': _dt(2026, 3, 1)},
        )

    def test_excluye_fuera_de_ventana_e_incluye_el_borde(self):
        cambios = [
            _cambio(1, 99990, 79990, _dt(2025, 10, 1)),   # fuera
            _cambio(2, 79990, 59990, self.DESDE),          # justo en el borde
        ]
        self.assertEqual(
            pvp_maximo_en_ventana(cambios, self.DESDE),
            {'precio': 79990, 'fecha': self.DESDE},
        )

    def test_empate_gana_la_observacion_mas_reciente(self):
        cambios = [
            _cambio(1, 59990, 69990, _dt(2025, 12, 1)),
            _cambio(2, 69990, 49990, _dt(2026, 3, 1)),
        ]
        self.assertEqual(pvp_maximo_en_ventana(cambios, self.DESDE)['fecha'], _dt(2026, 3, 1))

    def test_ignora_ceros_y_sin_datos(self):
        self.assertEqual(
            pvp_maximo_en_ventana([_cambio(1, 0, 19990, _dt(2026, 1, 1))], self.DESDE),
            {'precio': 19990, 'fecha': _dt(2026, 1, 1)},
        )
        self.assertIsNone(pvp_maximo_en_ventana([], self.DESDE))
        self.assertIsNone(
            pvp_maximo_en_ventana([_cambio(1, 50000, 40000, _dt(2024, 1, 1))], self.DESDE)
        )


class TestElegirLiquidacion(SimpleTestCase):
    def test_vacio(self):
        self.assertIsNone(elegir_liquidacion([]))
        self.assertIsNone(elegir_liquidacion([None]))

    def test_mayor_precio_original(self):
        a = {'campana_id': 1, 'precio_original': 39990, 'fecha_aplicacion': _dt(2026, 9, 1)}
        b = {'campana_id': 2, 'precio_original': 44990, 'fecha_aplicacion': _dt(2026, 8, 1)}
        self.assertIs(elegir_liquidacion([a, b]), b)

    def test_empate_aplicacion_mas_reciente_y_sin_fecha_pierde(self):
        a = {'campana_id': 1, 'precio_original': 39990, 'fecha_aplicacion': _dt(2026, 9, 1)}
        b = {'campana_id': 2, 'precio_original': 39990, 'fecha_aplicacion': _dt(2026, 9, 5)}
        c = {'campana_id': 3, 'precio_original': 39990, 'fecha_aplicacion': None}
        self.assertIs(elegir_liquidacion([a, c, b]), b)


class TestPrecioOriginalReferencia(SimpleTestCase):
    def test_pvp_actual_es_el_mayor(self):
        self.assertEqual(precio_original_referencia(59990, 49990, 54990),
                         (59990, FUENTE_PVP_ACTUAL))

    def test_liquidacion(self):
        self.assertEqual(precio_original_referencia(27993, 39990, 35990),
                         (39990, FUENTE_LIQUIDACION))

    def test_historial(self):
        self.assertEqual(precio_original_referencia(49990, None, 69990),
                         (69990, FUENTE_HISTORIAL))

    def test_empates_prefieren_pvp_y_luego_liquidacion(self):
        self.assertEqual(precio_original_referencia(39990, 39990, 39990),
                         (39990, FUENTE_PVP_ACTUAL))
        self.assertEqual(precio_original_referencia(27993, 39990, 39990),
                         (39990, FUENTE_LIQUIDACION))

    def test_sin_datos(self):
        self.assertEqual(precio_original_referencia(19990), (19990, FUENTE_PVP_ACTUAL))
        self.assertEqual(precio_original_referencia(None), (0, FUENTE_PVP_ACTUAL))


class TestDescuentoVigentePct(SimpleTestCase):
    def test_redondeo_a_un_decimal(self):
        self.assertEqual(descuento_vigente_pct(39990, 59990), Decimal('33.3'))
        self.assertEqual(descuento_vigente_pct(27993, 39990), Decimal('30.0'))

    def test_half_up_no_bancario(self):
        # 1 - 99950/100000 = 0.05 % exacto → 0.1 (round() bancario daría 0.0)
        self.assertEqual(descuento_vigente_pct(99950, 100000), Decimal('0.1'))

    def test_devuelve_decimal(self):
        self.assertIsInstance(descuento_vigente_pct(39990, 59990), Decimal)

    def test_sin_rebaja_o_datos_rotos_es_cero(self):
        self.assertEqual(descuento_vigente_pct(59990, 59990), Decimal('0.0'))
        self.assertEqual(descuento_vigente_pct(69990, 59990), Decimal('0.0'))
        self.assertEqual(descuento_vigente_pct(0, 59990), Decimal('0.0'))
        self.assertEqual(descuento_vigente_pct(59990, 0), Decimal('0.0'))
        self.assertEqual(descuento_vigente_pct(None, None), Decimal('0.0'))


class TestArmarReferenciaSku(SimpleTestCase):
    VENTANA = _dt(2025, 10, 2)

    def test_liquidacion_activa_y_filas_de_costo_ignoradas(self):
        cambios = [
            _cambio(10, 39990, 27993, _dt(2026, 9, 20),
                    motivo='Campaña #5 Liquidación invierno', tipo='CAMPANA_LIQUIDACION'),
            # Posterior, pero es de COSTO: no puede ser el "último cambio de PVP".
            _cambio(11, 15000, 18000, _dt(2026, 9, 25), motivo='[COSTO] Recepción'),
        ]
        liq = {
            'campana_id': 5, 'nombre': 'Liquidación invierno', 'estado': 'ACTIVA',
            'tipo_regla': 'PORCENTAJE', 'fecha_inicio': _dt(2026, 9, 20),
            'fecha_fin': None, 'precio_original': 39990, 'precio_liquidacion': 27993,
            'estado_item': 'APLICADO', 'fecha_aplicacion': _dt(2026, 9, 20),
        }
        ref = armar_referencia_sku(27993, cambios, liq, self.VENTANA)

        self.assertEqual(ref['pvp_actual'], 27993)
        self.assertEqual(ref['ultimo_cambio_pvp']['precio_anterior'], 39990)
        self.assertEqual(ref['ultimo_cambio_pvp']['precio_nuevo'], 27993)
        self.assertEqual(ref['ultimo_cambio_pvp']['tipo'], 'CAMPANA_LIQUIDACION')
        self.assertEqual(ref['ultimo_cambio_pvp']['fecha'], '2026-09-20')
        self.assertEqual(ref['pvp_maximo_ventana'], {'precio': 39990, 'fecha': '2026-09-20'})
        self.assertEqual(ref['pvp_antes_ultima_rebaja'], 39990)
        self.assertEqual(ref['liquidacion']['campana_id'], 5)
        self.assertEqual(ref['liquidacion']['precio_original'], 39990)
        self.assertEqual(ref['liquidacion']['precio_liquidacion'], 27993)
        self.assertIsNone(ref['liquidacion']['fecha_fin'])
        # Empate LIQUIDACION/HISTORIAL en 39990 → LIQUIDACION.
        self.assertEqual(ref['precio_original_referencia'], 39990)
        self.assertEqual(ref['fuente_original'], FUENTE_LIQUIDACION)
        self.assertEqual(ref['descuento_vigente_pct'], Decimal('30.0'))

    def test_historial_rebaja_sin_liquidacion(self):
        cambios = [
            _cambio(1, 59990, 69990, _dt(2025, 12, 1)),
            _cambio(2, 69990, 49990, _dt(2026, 3, 1), tipo='SINCRONIZACION'),
        ]
        ref = armar_referencia_sku(49990, cambios, None, self.VENTANA)

        self.assertEqual(ref['pvp_maximo_ventana'], {'precio': 69990, 'fecha': '2026-03-01'})
        self.assertEqual(ref['pvp_antes_ultima_rebaja'], 69990)
        self.assertEqual(ref['fecha_ultima_rebaja'], '2026-03-01')
        self.assertIsNone(ref['liquidacion'])
        self.assertEqual(ref['precio_original_referencia'], 69990)
        self.assertEqual(ref['fuente_original'], FUENTE_HISTORIAL)
        self.assertEqual(ref['descuento_vigente_pct'], Decimal('28.6'))

    def test_historial_viejo_fuera_de_ventana(self):
        cambios = [_cambio(1, 89990, 49990, _dt(2024, 6, 1))]
        ref = armar_referencia_sku(49990, cambios, None, self.VENTANA)

        # El último cambio se informa aunque esté fuera de la ventana…
        self.assertEqual(ref['ultimo_cambio_pvp']['fecha'], '2024-06-01')
        self.assertEqual(ref['pvp_antes_ultima_rebaja'], 89990)
        # …pero no alimenta el original de referencia.
        self.assertIsNone(ref['pvp_maximo_ventana'])
        self.assertEqual(ref['precio_original_referencia'], 49990)
        self.assertEqual(ref['fuente_original'], FUENTE_PVP_ACTUAL)
        self.assertEqual(ref['descuento_vigente_pct'], Decimal('0.0'))

    def test_solo_filas_de_costo_equivale_a_sin_historial(self):
        cambios = [_cambio(1, 15000, 18000, _dt(2026, 9, 1), motivo='Cambio de COSTO')]
        ref = armar_referencia_sku(29990, cambios, None, self.VENTANA)
        self.assertIsNone(ref['ultimo_cambio_pvp'])
        self.assertIsNone(ref['pvp_maximo_ventana'])
        self.assertIsNone(ref['pvp_antes_ultima_rebaja'])
        self.assertIsNone(ref['fecha_ultima_rebaja'])
        self.assertEqual(ref['fuente_original'], FUENTE_PVP_ACTUAL)

    def test_sin_datos(self):
        ref = armar_referencia_sku(19990, [], None, self.VENTANA)
        self.assertEqual(ref, {
            'pvp_actual': 19990,
            'ultimo_cambio_pvp': None,
            'pvp_maximo_ventana': None,
            'pvp_antes_ultima_rebaja': None,
            'fecha_ultima_rebaja': None,
            'liquidacion': None,
            'precio_original_referencia': 19990,
            'fuente_original': FUENTE_PVP_ACTUAL,
            'descuento_vigente_pct': Decimal('0.0'),
        })

    def test_liquidacion_nxm_no_cambia_precio(self):
        liq = {
            'campana_id': 8, 'nombre': '2x1 poleras', 'estado': 'ACTIVA',
            'tipo_regla': 'NXM', 'fecha_inicio': _dt(2026, 9, 1),
            'fecha_fin': _dt(2026, 10, 31), 'precio_original': 14990,
            'precio_liquidacion': None, 'estado_item': 'APLICADO',
            'fecha_aplicacion': _dt(2026, 9, 1),
        }
        ref = armar_referencia_sku(14990, [], liq, self.VENTANA)
        self.assertIsNone(ref['liquidacion']['precio_liquidacion'])
        self.assertEqual(ref['liquidacion']['fecha_fin'], '2026-10-31')
        self.assertEqual(ref['fuente_original'], FUENTE_PVP_ACTUAL)
        self.assertEqual(ref['descuento_vigente_pct'], Decimal('0.0'))

    def test_formateadores_inyectados(self):
        cambios = [_cambio(1, 59990, 49990, _dt(2026, 3, 1))]
        ref = armar_referencia_sku(
            49990, cambios, None, self.VENTANA,
            fmt_fecha=lambda d: f'F:{d.day}', fmt_fecha_hora=lambda d: f'FH:{d.hour}',
        )
        self.assertEqual(ref['ultimo_cambio_pvp']['fecha'], 'F:1')
        self.assertEqual(ref['ultimo_cambio_pvp']['fecha_hora'], 'FH:12')
        self.assertEqual(ref['ultimo_cambio_pvp']['usuario'], 'jtebes')
        self.assertEqual(ref['pvp_maximo_ventana']['fecha'], 'F:1')
