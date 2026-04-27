"""
Tests para generación y parseo de archivos TXT DTE en formato Acepta.
Cubre: factura con/sin descuento, boleta, descuentos globales, parser, retrocompatibilidad.
"""
from types import SimpleNamespace

from django.test import TestCase
from app.views_modulo_documentos import (
    generar_txt_dte_acepta,
    generar_txt_boleta_acepta,
    parsear_txt_acepta,
    construir_nombre_y_descripcion_item,
    construir_detalle_txt_desde_dte_productos,
    truncar_campo_sii,
    MAX_LENGTHS_SII,
)


def _datos_factura_base():
    """Datos mínimos para generar una factura tipo 33."""
    return {
        'documento': {
            'tipo_documento': 33,
            'folio': 100,
            'fecha_emision': '2026-04-07',
            'forma_pago': 1,
            'fecha_vencimiento': '2026-04-07',
        },
        'emisor': {
            'rut': '76123456-7',
            'razon_social': 'Empresa Test',
            'giro': 'Comercio',
            'acteco': '461000',
            'sucursal': 'CASA MATRIZ',
            'codigo_sucursal': '',
            'direccion': 'Av Test 123',
            'comuna': 'Santiago',
            'ciudad': 'Santiago',
            'codigo_vendedor': 'V01',
            'nombre_impresora_factura': 'factura',
        },
        'receptor': {
            'rut': '77888999-K',
            'razon_social': 'Cliente Test',
            'giro': 'Comercio',
            'contacto': '',
            'direccion': 'Calle 1',
            'comuna': 'Las Condes',
            'ciudad': 'Santiago',
        },
        'totales': {
            'monto_neto': 100000,
            'monto_exento': 0,
            'tasa_iva': 19,
            'iva': 19000,
            'monto_total': 119000,
        },
        'detalle': [],
    }


def _datos_boleta_base():
    """Datos mínimos para generar una boleta tipo 39."""
    return {
        'documento': {
            'tipo_documento': 39,
            'folio': 200,
            'fecha_emision': '2026-04-07',
            'ind_servicio': 3,
        },
        'emisor': {
            'rut': '76123456-7',
            'razon_social': 'Empresa Test',
            'giro': 'Comercio',
            'acteco': '461000',
            'direccion': 'Av Test 123',
            'comuna': 'Santiago',
            'ciudad': 'Santiago',
            'sucursal': 'CASA MATRIZ',
            'codigo_vendedor': 'V01',
            'nombre_vendedor': 'Vendedor Test',
            'metodos_pago': 'Efectivo',
            'correlativo_ticket': '1001',
            'nombre_impresora_boleta': 'boleta',
        },
        'receptor': {
            'rut': '66666666-6',
            'razon_social': 'CONSUMIDOR FINAL',
            'giro': '',
            'direccion': '',
            'comuna': '',
            'ciudad': '',
        },
        'totales': {
            'monto_total': 119000,
        },
        'detalle': [],
    }


class TestFacturaConDescuentoItem(TestCase):
    """Factura con descuento por ítem: pipes 7 y 8 deben tener valores."""

    def test_detalle_incluye_descuento_pct_y_monto(self):
        datos = _datos_factura_base()
        datos['detalle'] = [{
            'codigo': 'SKU001',
            'nombre': 'Producto A',
            'descripcion': '',
            'cantidad': 2,
            'unidad': 'UN',
            'precio_unitario': 50000,
            'descuento_pct': 10.0,
            'monto_descuento': 10000,
            'monto_item': 90000,
        }]
        txt = generar_txt_dte_acepta(datos)
        lineas = txt.split('\n')

        detalle_lineas = []
        en_detalle = False
        for l in lineas:
            if l.strip() == '~':
                if en_detalle:
                    break
                en_detalle = True
                continue
            if en_detalle:
                detalle_lineas.append(l)

        self.assertEqual(len(detalle_lineas), 1)
        campos = detalle_lineas[0].split('|')
        # Campo 7: descuento_pct, Campo 8: monto_descuento
        self.assertEqual(campos[6], '10.00')
        self.assertEqual(campos[7], '10000')
        self.assertEqual(campos[8], '90000')


class TestFacturaSinDescuento(TestCase):
    """Factura sin descuento: pipes 7 y 8 deben estar vacíos pero presentes."""

    def test_detalle_pipes_vacios_presentes(self):
        datos = _datos_factura_base()
        datos['detalle'] = [{
            'codigo': 'SKU002',
            'nombre': 'Producto B',
            'descripcion': '',
            'cantidad': 1,
            'unidad': 'UN',
            'precio_unitario': 100000,
            'descuento_pct': 0,
            'monto_descuento': 0,
            'monto_item': 100000,
        }]
        txt = generar_txt_dte_acepta(datos)
        lineas = txt.split('\n')

        en_detalle = False
        detalle_lineas = []
        for l in lineas:
            if l.strip() == '~':
                if en_detalle:
                    break
                en_detalle = True
                continue
            if en_detalle:
                detalle_lineas.append(l)

        self.assertEqual(len(detalle_lineas), 1)
        campos = detalle_lineas[0].split('|')
        # Campos vacíos (descuento_pct y monto_descuento)
        self.assertEqual(campos[6], '')
        self.assertEqual(campos[7], '')
        self.assertEqual(campos[8], '100000')


class TestAgrupacionDetalleDteExistente(TestCase):
    """El TXT de DTE existente agrupa por variante, no solo por artículo."""

    def _dte_producto(self, articulo, marca, color, costo, talla, stock=1):
        producto = SimpleNamespace(
            articulo=articulo,
            costo=costo,
            atributo1=SimpleNamespace(valor=marca),
            atributo2=SimpleNamespace(valor=color),
        )
        producto_talla = SimpleNamespace(producto=producto, talla=talla)
        return SimpleNamespace(
            productoTalla=producto_talla,
            descripcion='',
            costo=costo,
            precio=10000,
            precio_unitario=10000,
            descuento_pct=0,
            descuento_monto=0,
            monto_item=stock * 10000,
            stock=stock,
        )

    def test_mismo_articulo_distinto_color_o_costo_genera_lineas_distintas(self):
        detalle = construir_detalle_txt_desde_dte_productos([
            self._dte_producto('POLERA', 'ACME', 'AZUL', 5000, 'M', stock=2),
            self._dte_producto('POLERA', 'ACME', 'ROJO', 5000, 'L', stock=1),
            self._dte_producto('POLERA', 'ACME', 'AZUL', 6500, 'XL', stock=1),
        ], tipo_numerico=33)

        self.assertEqual(len(detalle), 3)
        self.assertEqual([item['cantidad'] for item in detalle], [2, 1, 1])
        self.assertIn('AZUL 2:M', detalle[0]['nombre'])
        self.assertIn('ROJO 1:L', detalle[1]['nombre'])
        self.assertIn('AZUL 1:XL', detalle[2]['nombre'])


class TestBoletaSinCamposDescuento(TestCase):
    """Boleta: detalle NO tiene campos de descuento por ítem."""

    def test_detalle_boleta_sin_campos_descuento(self):
        datos = _datos_boleta_base()
        datos['detalle'] = [{
            'codigo': 'SKU003',
            'nombre': 'Producto C',
            'cantidad': 1,
            'unidad': 'UN',
            'precio_unitario': 119000,
            'monto_item': 119000,
        }]
        txt = generar_txt_boleta_acepta(datos)
        lineas = txt.split('\n')

        en_detalle = False
        detalle_lineas = []
        for l in lineas:
            if l.strip() == '~':
                if en_detalle:
                    break
                en_detalle = True
                continue
            if en_detalle:
                detalle_lineas.append(l)

        self.assertEqual(len(detalle_lineas), 1)
        campos = detalle_lineas[0].split('|')
        # Boleta: INT1|cod||nombre||qty|UN|precio|monto|}
        self.assertEqual(campos[0], 'INT1')
        self.assertEqual(len(campos), 10)  # 9 fields + }
        self.assertEqual(campos[7], '119000')
        self.assertEqual(campos[8], '119000')


class TestDescuentoGlobalPorcentaje(TestCase):
    """Descuento global por porcentaje en factura."""

    def test_linea_descuento_global_porcentaje(self):
        datos = _datos_factura_base()
        datos['detalle'] = [{
            'codigo': 'SKU001', 'nombre': 'Prod', 'descripcion': '',
            'cantidad': 1, 'unidad': 'UN', 'precio_unitario': 100000,
            'monto_item': 100000,
        }]
        datos['descuentos_recargos'] = [{
            'tpo_mov': 'D',
            'glosa_dr': 'Dcto 10%',
            'tpo_valor': '%',
            'valor_dr': 10,
            'ind_exe_dr': None,
        }]
        txt = generar_txt_dte_acepta(datos)
        # Formato factura: D|glosa|%|10||}  (sin NroLinDR, con CodRef vacío)
        self.assertIn('D|Dcto 10%|%|10|||}', txt)


class TestDescuentoGlobalMonto(TestCase):
    """Descuento global por monto fijo en factura."""

    def test_linea_descuento_global_monto(self):
        datos = _datos_factura_base()
        datos['detalle'] = [{
            'codigo': 'SKU001', 'nombre': 'Prod', 'descripcion': '',
            'cantidad': 1, 'unidad': 'UN', 'precio_unitario': 100000,
            'monto_item': 100000,
        }]
        datos['descuentos_recargos'] = [{
            'tpo_mov': 'D',
            'glosa_dr': 'Descuento Fijo',
            'tpo_valor': '$',
            'valor_dr': 5000,
            'ind_exe_dr': 1,
        }]
        txt = generar_txt_dte_acepta(datos)
        # Formato factura: D|glosa|$|5000|1||} — IndExeDR=1, CodRef vacío (6 campos)
        # Mismo formato que ejemplo oficial Acepta: D|Descuento|$|10|1||}
        self.assertIn('D|Descuento Fijo|$|5000|1||}', txt)


class TestRecargoGlobal(TestCase):
    """Recargo global en factura."""

    def test_linea_recargo_global(self):
        datos = _datos_factura_base()
        datos['detalle'] = [{
            'codigo': 'SKU001', 'nombre': 'Prod', 'descripcion': '',
            'cantidad': 1, 'unidad': 'UN', 'precio_unitario': 100000,
            'monto_item': 100000,
        }]
        datos['descuentos_recargos'] = [{
            'tpo_mov': 'R',
            'glosa_dr': 'Recargo envio',
            'tpo_valor': '$',
            'valor_dr': 3000,
            'ind_exe_dr': None,
        }]
        txt = generar_txt_dte_acepta(datos)
        # Formato factura: R|glosa|$|3000||}  (sin NroLinDR, con CodRef vacío)
        self.assertIn('R|Recargo envio|$|3000|||}', txt)


class TestParserFactura(TestCase):
    """Parser lee correctamente una factura con descuento."""

    def test_roundtrip_factura(self):
        datos = _datos_factura_base()
        datos['detalle'] = [{
            'codigo': 'SKU001',
            'nombre': 'Producto A',
            'descripcion': '',
            'cantidad': 2,
            'unidad': 'UN',
            'precio_unitario': 50000,
            'descuento_pct': 10.0,
            'monto_descuento': 10000,
            'monto_item': 90000,
        }]
        datos['descuentos_recargos'] = [{
            'tpo_mov': 'D',
            'glosa_dr': 'Dcto General',
            'tpo_valor': '$',
            'valor_dr': 5000,
            'ind_exe_dr': 1,
        }]
        txt = generar_txt_dte_acepta(datos)
        parsed = parsear_txt_acepta(txt)

        self.assertEqual(parsed['documento']['tipo_documento'], 33)
        self.assertEqual(parsed['documento']['folio'], 100)
        self.assertEqual(len(parsed['detalle']), 1)
        item = parsed['detalle'][0]
        self.assertEqual(item['cantidad'], 2)
        self.assertEqual(item['precio_unitario'], 50000)
        self.assertEqual(item['descuento_pct'], 10.0)
        self.assertEqual(item['monto_descuento'], 10000)
        self.assertEqual(item['monto_item'], 90000)
        self.assertEqual(len(parsed['descuentos_recargos']), 1)
        dr = parsed['descuentos_recargos'][0]
        self.assertEqual(dr['tpo_mov'], 'D')
        self.assertEqual(dr['valor_dr'], 5000)


class TestFacturaFormaPagoYReferencias(TestCase):
    """Factura debe respetar forma de pago, referencias y vendedor impreso."""

    def test_factura_con_credito_y_referencias(self):
        datos = _datos_factura_base()
        datos['documento']['forma_pago'] = 2
        datos['documento']['fecha_vencimiento'] = '2026-05-07'
        datos['referencias'] = [{
            'tipo_documento': '801',
            'folio': 'OC-123',
            'fecha': '2026-04-01',
            'razon': 'Orden de compra',
        }]
        datos['detalle'] = [{
            'codigo': 'SKU010',
            'nombre': 'Producto Ref',
            'descripcion': '',
            'cantidad': 1,
            'unidad': 'UN',
            'precio_unitario': 100000,
            'monto_item': 100000,
        }]

        txt = generar_txt_dte_acepta(datos)
        lineas = txt.split('\n')
        encabezado = lineas[0].split('|')

        self.assertEqual(encabezado[6], '2')
        self.assertEqual(encabezado[7], '2026-05-07')
        self.assertIn('801|| OC-123 | 2026-04-01|Orden de compra|}', txt)

    def test_factura_puede_imprimir_vendedor_pos(self):
        datos = _datos_factura_base()
        datos['emisor']['vendedor_impresion'] = 'Juan Perez'
        datos['detalle'] = [{
            'codigo': 'SKU011',
            'nombre': 'Producto Vendedor',
            'descripcion': '',
            'cantidad': 1,
            'unidad': 'UN',
            'precio_unitario': 100000,
            'monto_item': 100000,
        }]

        txt = generar_txt_dte_acepta(datos)
        lineas = txt.split('\n')
        emisor_linea = lineas[1].split('|')
        lineas_util = [l for l in lineas if l and l not in ('~', '\\')]
        linea_final = lineas_util[-1].split('|')

        self.assertEqual(emisor_linea[9], 'Juan Perez')
        self.assertEqual(linea_final[0], 'Juan Perez')


class TestParserBoleta(TestCase):
    """Parser lee correctamente una boleta (sin campos descuento en detalle)."""

    def test_roundtrip_boleta(self):
        datos = _datos_boleta_base()
        datos['detalle'] = [{
            'codigo': 'SKU003',
            'nombre': 'Producto C',
            'cantidad': 3,
            'unidad': 'UN',
            'precio_unitario': 10000,
            'monto_item': 30000,
        }]
        txt = generar_txt_boleta_acepta(datos)
        parsed = parsear_txt_acepta(txt)

        self.assertEqual(parsed['documento']['tipo_documento'], 39)
        self.assertEqual(len(parsed['detalle']), 1)
        item = parsed['detalle'][0]
        self.assertEqual(item['cantidad'], 3)
        self.assertEqual(item['precio_unitario'], 10000)
        self.assertEqual(item['monto_item'], 30000)
        self.assertNotIn('descuento_pct', item)


class TestRetrocompatibilidadSinDescuento(TestCase):
    """TXT sin descuentos se parsea sin error; campos descuento = None."""

    def test_factura_sin_descuentos_parsea_ok(self):
        datos = _datos_factura_base()
        datos['detalle'] = [{
            'codigo': 'SKU001',
            'nombre': 'Producto X',
            'descripcion': '',
            'cantidad': 1,
            'unidad': 'UN',
            'precio_unitario': 100000,
            'monto_item': 100000,
        }]
        txt = generar_txt_dte_acepta(datos)
        parsed = parsear_txt_acepta(txt)

        self.assertEqual(parsed['documento']['tipo_documento'], 33)
        self.assertEqual(len(parsed['detalle']), 1)
        item = parsed['detalle'][0]
        self.assertIsNone(item.get('descuento_pct'))
        self.assertIsNone(item.get('monto_descuento'))
        self.assertEqual(item['monto_item'], 100000)
        self.assertEqual(len(parsed['descuentos_recargos']), 0)


class TestNmbItemOverflowVaADscItem(TestCase):
    """
    Cuando el nombre del producto excede 80 chars, el overflow debe quedar en
    DscItem (1000 chars) en lugar de perderse. Verifica el helper nuevo y el
    TXT generado.
    """

    def test_helper_divide_nombre_largo(self):
        item = {
            'sku': 'POL-01',
            'nombre': 'Polera Manga Larga Algodon Premium Color Azul Rey Talla XL Edicion Limitada 2026 Coleccion Primavera',
            'descripcion': 'Material: 100% algodon pima peruano certificado.',
        }
        nmb, dsc = construir_nombre_y_descripcion_item(item, max_nmb=80, max_dsc=1000)

        # NmbItem nunca excede 80
        self.assertLessEqual(len(nmb), MAX_LENGTHS_SII['NmbItem'])
        # Empieza con el SKU
        self.assertTrue(nmb.startswith('POL-01'))
        # DscItem tiene contenido (overflow del nombre + descripción original)
        self.assertGreater(len(dsc), 0)
        self.assertLessEqual(len(dsc), MAX_LENGTHS_SII['DscItem'])
        # La descripción original aparece en DscItem
        self.assertIn('algodon pima peruano', dsc)

    def test_txt_factura_coloca_dsc_item_en_campo_3(self):
        """En factura, el campo 3 de la línea de detalle debe traer el DscItem calculado."""
        datos = _datos_factura_base()
        nombre_largo = 'Zapatilla Running Profesional Amortiguacion Maxima Transpirable Talla 42'
        datos['detalle'] = [{
            'codigo': 'ZAP-RUN-42',
            'nombre': nombre_largo + ' Edicion Deportiva 2026',  # > 80 chars
            'descripcion': 'Incluye plantillas anatomicas',
            'cantidad': 1,
            'unidad': 'UN',
            'precio_unitario': 89990,
            'monto_item': 89990,
        }]
        txt = generar_txt_dte_acepta(datos)
        # Campo 2 (NmbItem) y campo 3 (DscItem) están separados por '|'.
        # Tomo la línea de detalle del TXT (la que empieza con el indicador de exención vacío).
        for linea in txt.split('\n'):
            partes = linea.split('|')
            # Línea de detalle factura tiene 11 campos y campo 2 contiene 'ZAP-RUN-42'
            if len(partes) >= 11 and 'ZAP-RUN-42' in partes[1]:
                nmb_item = partes[1]
                dsc_item = partes[2]
                self.assertLessEqual(len(nmb_item), MAX_LENGTHS_SII['NmbItem'])
                # El overflow o la descripción deben estar presentes
                self.assertTrue(
                    'Edicion' in dsc_item or 'plantillas' in dsc_item,
                    f"DscItem no contiene el overflow ni la descripcion: {dsc_item!r}",
                )
                break
        else:
            self.fail("No se encontró la línea de detalle en el TXT generado")


class TestBoletaConDescuentoEmiteDscRcgGlobal(TestCase):
    """
    Boleta con descuento DEBE emitir el bloque DscRcgGlobal (tabla 4).
    La sección va DESPUÉS de las observaciones, con el número de línea al
    inicio: {num}|D|glosa|$|valor|}  Ejemplo: 1|D|Descuento|$|2000|}
    El precio unitario y monto_item de cada línea INT1 usa el precio COMPLETO
    (sin descontar); Acepta calcula: sum(items) - sum(descuentos) = total.
    """

    def test_boleta_emite_linea_descuento_global(self):
        datos = _datos_boleta_base()
        datos['detalle'] = [{
            'codigo': 'POL-01',
            'nombre': 'Polera Azul',
            'descripcion': '',
            'cantidad': 1,
            'unidad': 'UN',
            'precio_unitario': 8000,
            'monto_item': 8000,
        }]
        datos['descuentos_recargos'] = [{
            'tpo_mov': 'D',
            'glosa_dr': 'Descuento',
            'tpo_valor': '$',
            'valor_dr': 2000,
        }]
        txt = generar_txt_boleta_acepta(datos)
        # La línea de descuento aparece con número de secuencia al inicio
        self.assertIn('1|D|Descuento|$|2000|}', txt)

    def test_boleta_con_descuento_tiene_4_separadores(self):
        """Boleta con descuentos tiene 4 separadores ~."""
        datos = _datos_boleta_base()
        datos['detalle'] = [{
            'codigo': 'POL-01',
            'nombre': 'Polera Azul',
            'descripcion': '',
            'cantidad': 1,
            'unidad': 'UN',
            'precio_unitario': 8000,
            'monto_item': 8000,
        }]
        datos['descuentos_recargos'] = [{
            'tpo_mov': 'D',
            'glosa_dr': 'Descuento',
            'tpo_valor': '$',
            'valor_dr': 2000,
        }]
        txt = generar_txt_boleta_acepta(datos)
        separadores = [l for l in txt.split('\n') if l.strip() == '~']
        self.assertEqual(
            len(separadores), 4,
            f"Boleta con descuento debe tener 4 separadores ~, tiene {len(separadores)}. TXT:\n{txt}",
        )

    def test_boleta_sin_descuento_tiene_3_separadores(self):
        """Boleta sin descuentos mantiene 3 separadores ~ (retrocompatibilidad)."""
        datos = _datos_boleta_base()
        datos['detalle'] = [{
            'codigo': 'POL-01',
            'nombre': 'Polera Azul',
            'descripcion': '',
            'cantidad': 1,
            'unidad': 'UN',
            'precio_unitario': 8000,
            'monto_item': 8000,
        }]
        txt = generar_txt_boleta_acepta(datos)
        separadores = [l for l in txt.split('\n') if l.strip() == '~']
        self.assertEqual(
            len(separadores), 3,
            f"Boleta sin descuento debe tener 3 separadores ~, tiene {len(separadores)}. TXT:\n{txt}",
        )

    def test_boleta_descuento_va_despues_de_observacion(self):
        """La línea DscRcgGlobal debe aparecer DESPUÉS de la observación."""
        datos = _datos_boleta_base()
        datos['detalle'] = [{
            'codigo': 'POL-01',
            'nombre': 'Polera Azul',
            'descripcion': '',
            'cantidad': 1,
            'unidad': 'UN',
            'precio_unitario': 8000,
            'monto_item': 8000,
        }]
        datos['descuentos_recargos'] = [{
            'tpo_mov': 'D',
            'glosa_dr': 'Descuento',
            'tpo_valor': '$',
            'valor_dr': 2000,
        }]
        txt = generar_txt_boleta_acepta(datos)
        # La línea de descuento debe venir después de la línea de observación
        pos_descuento = txt.find('1|D|Descuento|$|2000|}')
        pos_impresora = txt.find('boleta')  # nombre de impresora en obs
        self.assertGreater(
            pos_descuento, pos_impresora,
            "La línea DscRcgGlobal debe aparecer DESPUÉS de las observaciones.",
        )

    def test_observacion_boleta_incluye_monto_descuento(self):
        datos = _datos_boleta_base()
        datos['detalle'] = [{
            'codigo': 'POL-01',
            'nombre': 'Polera Azul',
            'descripcion': '',
            'cantidad': 1,
            'unidad': 'UN',
            'precio_unitario': 8000,
            'monto_item': 8000,
        }]
        datos['descuentos_recargos'] = [{
            'tpo_mov': 'D',
            'glosa_dr': 'Descuento',
            'tpo_valor': '$',
            'valor_dr': 2000,
        }]
        txt = generar_txt_boleta_acepta(datos)
        self.assertIn('Dc:2000', txt)


class TestBoletaNoLlevaFchVenc(TestCase):
    """
    Regresión: boletas (39/41) no deben llevar fecha de vencimiento en el
    header. El XSD SII solo permite FchVenc en facturas con pago diferido,
    y el campo 7 de la línea 1 debe quedar vacío.
    """

    def test_fchvenc_ausente_en_boleta(self):
        datos = _datos_boleta_base()
        datos['detalle'] = [{
            'codigo': 'POL-01',
            'nombre': 'Polera',
            'descripcion': '',
            'cantidad': 1,
            'unidad': 'UN',
            'precio_unitario': 8000,
            'monto_item': 8000,
        }]
        txt = generar_txt_boleta_acepta(datos)
        primera_linea = txt.split('\n')[0]
        # Formato esperado: 39|folio|fecha|ind_servicio|||||}
        # (9 campos, campo 7 = FchVenc vacío).
        self.assertTrue(primera_linea.startswith('39|'))
        self.assertFalse(
            '2026-04-07' in primera_linea.split('|')[6:],
            f"FchVenc no debe aparecer en boleta. Línea: {primera_linea}",
        )


class TestObservacionCompactaRespeta90Chars(TestCase):
    """
    La observación final del TXT debe caber en 90 chars para respetar el
    maxLength del XSD SII (RazonRef / TermPagoGlosa).
    """

    def _extraer_obs_boleta(self, txt):
        """
        Extrae el campo 4 (índice 3) de la línea de observación de una boleta.
        Busca la primera línea con 9+ campos (obs) en lugar de asumir posición
        final, ya que la sección DscRcgGlobal puede venir después de la obs.
        """
        lineas_util = [l for l in txt.split('\n') if l and l not in ('~', '\\')]
        for linea in reversed(lineas_util):
            campos = linea.split('|')
            if len(campos) >= 9:
                return campos[3]
        return ''

    def test_observacion_compacta_boleta(self):
        datos = _datos_boleta_base()
        datos['emisor']['metodos_pago'] = (
            'EFECTIVO: $100 - Tarj: VISA - Auth: 12345 | '
            'Transbank Debito POS: $791 - Terminal: XYZ - Op: 456'
        )
        datos['detalle'] = [{
            'codigo': 'POL-01', 'nombre': 'Polera', 'descripcion': '',
            'cantidad': 1, 'unidad': 'UN', 'precio_unitario': 891,
            'monto_item': 891,
        }]
        datos['descuentos_recargos'] = [{
            'tpo_mov': 'D', 'glosa_dr': 'Descuento', 'tpo_valor': '$',
            'valor_dr': 99,
        }]
        txt = generar_txt_boleta_acepta(datos)

        campo_obs = self._extraer_obs_boleta(txt)
        self.assertLessEqual(
            len(campo_obs), 90,
            f"Observación compacta excede 90 chars: {campo_obs!r}",
        )
        # Contiene los marcadores cortos esperados.
        self.assertIn('V:V01', campo_obs)
        self.assertIn('T:1001', campo_obs)
        self.assertIn('EFE:100', campo_obs)
        self.assertIn('TBK:791', campo_obs)

    def test_observacion_boleta_incluye_venta_internet_con_plataforma_y_voucher(self):
        datos = _datos_boleta_base()
        datos['emisor']['metodos_pago'] = (
            'Venta por Internet: $119000 - Tarj: Mercado Pago - Auth: ORD-123456'
        )
        datos['detalle'] = [{
            'codigo': 'POL-02', 'nombre': 'Poleron', 'descripcion': '',
            'cantidad': 1, 'unidad': 'UN', 'precio_unitario': 119000,
            'monto_item': 119000,
        }]

        txt = generar_txt_boleta_acepta(datos)
        lineas_util = [l for l in txt.split('\n') if l and l not in ('~', '\\')]
        campo_obs = lineas_util[-1].split('|')[3]

        self.assertIn('WEB-MERCAD:119000#ORD-123456', campo_obs)


class TestObservacionFacturaMinimalista(TestCase):
    """
    Factura (33/34) y Guía (52) deben imprimir solo 'monto en letras + Total
    Productos' en la línea de observación. No deben filtrarse V:/T:/D:/Dc:/
    pagos ni el texto libre de `observaciones` / `observaciones_adicionales`
    del ticket. Eso queda reservado para Boleta.
    """

    def _extraer_linea_observacion_factura(self, txt):
        """Campo 4 (índice 3) de la última línea útil del TXT."""
        lineas_util = [l for l in txt.split('\n') if l and l not in ('~', '\\')]
        return lineas_util[-1].split('|')[3]

    def test_factura_observacion_solo_letras_y_total_productos(self):
        datos = _datos_factura_base()
        datos['emisor']['correlativo_ticket'] = '1001'
        datos['emisor']['metodos_pago'] = 'EFECTIVO: $119000'
        datos['observaciones'] = 'Gracias por su compra'
        datos['observaciones_adicionales'] = 'Entrega en bodega'
        datos['detalle'] = [{
            'codigo': 'SKU001', 'nombre': 'Producto A', 'descripcion': '',
            'cantidad': 2, 'unidad': 'UN', 'precio_unitario': 50000,
            'monto_item': 100000,
        }]
        txt = generar_txt_dte_acepta(datos)
        obs = self._extraer_linea_observacion_factura(txt)

        self.assertIn('PESOS', obs)
        self.assertIn('Total Productos: 2', obs)
        self.assertNotIn('V:', obs)
        self.assertNotIn('T:1001', obs)
        self.assertNotIn('EFE', obs)
        self.assertNotIn('Gracias por su compra', obs)
        self.assertNotIn('Entrega en bodega', obs)

    def test_guia_despacho_observacion_solo_letras_y_total_productos(self):
        datos = _datos_factura_base()
        datos['documento']['tipo_documento'] = 52
        datos['emisor']['correlativo_ticket'] = '2002'
        datos['emisor']['metodos_pago'] = 'EFECTIVO: $119000'
        datos['detalle'] = [{
            'codigo': 'SKU002', 'nombre': 'Producto B', 'descripcion': '',
            'cantidad': 5, 'unidad': 'UN', 'precio_unitario': 23800,
            'monto_item': 119000,
        }]
        txt = generar_txt_dte_acepta(datos)
        obs = self._extraer_linea_observacion_factura(txt)

        self.assertIn('PESOS', obs)
        self.assertIn('Total Productos: 5', obs)
        self.assertNotIn('V:', obs)
        self.assertNotIn('T:2002', obs)
        self.assertNotIn('EFE', obs)


class TestValidacionLargosSII(TestCase):
    """El helper truncar_campo_sii trunca y emite warning."""

    def test_trunca_campo_que_excede_max_length(self):
        # RznSocRecep tiene maxLength 100
        texto_largo = 'X' * 150
        resultado = truncar_campo_sii(texto_largo, 'RznSocRecep')
        self.assertEqual(len(resultado), 100)

    def test_no_trunca_si_cabe(self):
        texto = 'Cliente normal'
        resultado = truncar_campo_sii(texto, 'RznSocRecep')
        self.assertEqual(resultado, texto)

    def test_campo_desconocido_solo_limpia_sin_truncar(self):
        texto = 'X' * 500
        resultado = truncar_campo_sii(texto, 'CampoInexistente')
        # No trunca porque no está en MAX_LENGTHS_SII
        self.assertEqual(len(resultado), 500)
