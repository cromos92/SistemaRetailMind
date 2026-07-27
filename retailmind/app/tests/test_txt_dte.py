"""
Tests para generación y parseo de archivos TXT DTE en formato Acepta.
Cubre: factura con/sin descuento, boleta, descuentos globales, parser, retrocompatibilidad.
"""
import json
from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase
from app.views_modulo_ventas import cuadrar_detalle_neto, generar_dte_desde_ticket
from app.views_modulo_documentos import (
    generar_txt_dte_acepta,
    generar_txt_boleta_acepta,
    generar_txt_nota_credito_acepta,
    construir_datos_txt_desde_dte,
    parsear_txt_acepta,
    construir_nombre_y_descripcion_item,
    construir_detalle_txt_desde_dte_productos,
    normalizar_detalle_para_tipo,
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


class TestCuadraturaDetalleNeto(SimpleTestCase):
    """
    El detalle de una factura debe cuadrar con el MntNeto del header:
    sum(MontoItem) - descuentos + recargos == MntNeto. Si no, Acepta rechaza.
    """

    def _detalle(self, *montos):
        return [{'nombre': f'Item {i}', 'monto_item': m}
                for i, m in enumerate(montos, start=1)]

    def _suma(self, detalle):
        return sum(int(d['monto_item']) for d in detalle)

    def test_sin_residuo_no_toca_nada(self):
        detalle = self._detalle(1000, 2000, 3000)
        residuo = cuadrar_detalle_neto(detalle, 6000)
        self.assertEqual(residuo, 0)
        self.assertEqual(self._suma(detalle), 6000)

    def test_absorbe_residuo_positivo_en_la_ultima_linea(self):
        # Caso real: 3 líneas de $9.990 IVA-incl -> round(9990/1.19)=8395 c/u
        # => suma 25185, pero el header hace round(29970/1.19)=25185... si el
        # total difiere en $1 el detalle queda descuadrado.
        detalle = self._detalle(8395, 8395, 8395)
        residuo = cuadrar_detalle_neto(detalle, 25186)
        self.assertEqual(residuo, 1)
        self.assertEqual(self._suma(detalle), 25186)
        self.assertEqual(detalle[-1]['monto_item'], 8396)
        # las líneas anteriores no se tocan
        self.assertEqual(detalle[0]['monto_item'], 8395)

    def test_absorbe_residuo_negativo(self):
        detalle = self._detalle(8395, 8395, 8395)
        residuo = cuadrar_detalle_neto(detalle, 25183)
        self.assertEqual(residuo, -2)
        self.assertEqual(self._suma(detalle), 25183)

    def test_descuento_global_entra_en_la_ecuacion(self):
        # sum(items) - dcto debe dar el neto
        detalle = self._detalle(10000, 5000)
        dr = [{'tpo_mov': 'D', 'valor_dr': 1000}]
        residuo = cuadrar_detalle_neto(detalle, 14000, dr)
        self.assertEqual(residuo, 0)
        self.assertEqual(self._suma(detalle) - 1000, 14000)

    def test_descuento_global_con_residuo(self):
        detalle = self._detalle(10000, 5000)
        dr = [{'tpo_mov': 'D', 'valor_dr': 1000}]
        cuadrar_detalle_neto(detalle, 13998, dr)
        self.assertEqual(self._suma(detalle) - 1000, 13998)

    def test_recargo_global_suma(self):
        detalle = self._detalle(10000)
        dr = [{'tpo_mov': 'R', 'valor_dr': 500}]
        residuo = cuadrar_detalle_neto(detalle, 10500)
        # sin pasar los DR el residuo sería 500; pasándolos, cuadra
        self.assertEqual(residuo, 500)
        detalle2 = self._detalle(10000)
        residuo2 = cuadrar_detalle_neto(detalle2, 10500, dr)
        self.assertEqual(residuo2, 0)

    def test_detalle_vacio_no_revienta(self):
        self.assertEqual(cuadrar_detalle_neto([], 1000), 0)


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


class TestSkuEnDetalleTxt(TestCase):
    """El TXT canónico lleva el SKU del producto (paridad con el POS).

    Regresión ecommerce: al facturar pedidos, el TXT se regenera con el
    generador canónico, que agrupaba por variante y PERDÍA el SKU de
    Producto_Talla — las boletas de internet salían sin SKU. Regla:
    grupo con UN solo SKU → el SKU es el código del ítem y el artículo
    pasa al nombre; varios SKUs → código = artículo y cada tramo del
    desglose de tallas lleva su SKU entre paréntesis.
    """

    def _dp(self, articulo='ZAP01', marca='ACME', color='AZUL', costo=5000,
            talla='42', sku=784512, stock=1, precio=10000):
        producto = SimpleNamespace(
            articulo=articulo,
            costo=costo,
            atributo1=SimpleNamespace(valor=marca),
            atributo2=SimpleNamespace(valor=color),
        )
        producto_talla = SimpleNamespace(producto=producto, talla=talla, sku=sku)
        return SimpleNamespace(
            productoTalla=producto_talla,
            descripcion='',
            costo=costo,
            precio=precio,
            precio_unitario=precio,
            descuento_pct=0,
            descuento_monto=0,
            monto_item=stock * precio,
            stock=stock,
        )

    def test_grupo_con_un_sku_usa_sku_como_codigo(self):
        detalle = construir_detalle_txt_desde_dte_productos(
            [self._dp(sku=784512, talla='42', stock=2)], tipo_numerico=39)

        self.assertEqual(len(detalle), 1)
        self.assertEqual(detalle[0]['codigo'], '784512')
        # El artículo no se pierde: pasa al nombre.
        self.assertIn('ZAP01', detalle[0]['nombre'])
        self.assertIn('2:42', detalle[0]['nombre'])

    def test_grupo_multi_sku_anota_sku_por_talla(self):
        detalle = construir_detalle_txt_desde_dte_productos([
            self._dp(sku=784512, talla='42', stock=2),
            self._dp(sku=784513, talla='43', stock=1),
        ], tipo_numerico=39)

        self.assertEqual(len(detalle), 1, 'misma variante → una sola línea')
        self.assertEqual(detalle[0]['codigo'], 'ZAP01')
        self.assertIn('2:42(784512)', detalle[0]['nombre'])
        self.assertIn('1:43(784513)', detalle[0]['nombre'])

    def test_grupo_con_muchas_tallas_manda_skus_a_descripcion(self):
        """Con 4+ tallas los '(sku)' inflarían el NmbItem sobre los 80 chars del
        SII: el nombre queda con el desglose limpio y los SKUs van al DscItem."""
        detalle = construir_detalle_txt_desde_dte_productos([
            self._dp(sku=784510 + i, talla=str(39 + i), stock=1) for i in range(5)
        ], tipo_numerico=39)

        self.assertEqual(len(detalle), 1)
        self.assertEqual(detalle[0]['codigo'], 'ZAP01')
        self.assertNotIn('(', detalle[0]['nombre'])
        self.assertIn('1:39', detalle[0]['nombre'])
        self.assertTrue(detalle[0]['descripcion'].startswith('SKUs: '))
        for i in range(5):
            self.assertIn(str(784510 + i), detalle[0]['descripcion'])

    def test_sin_atributo_sku_mantiene_comportamiento_previo(self):
        producto = SimpleNamespace(
            articulo='POLERA', costo=5000,
            atributo1=SimpleNamespace(valor='ACME'),
            atributo2=SimpleNamespace(valor='ROJO'),
        )
        pt = SimpleNamespace(producto=producto, talla='M')  # sin atributo .sku
        dp = SimpleNamespace(
            productoTalla=pt, descripcion='', costo=5000,
            precio=10000, precio_unitario=10000, descuento_pct=0,
            descuento_monto=0, monto_item=10000, stock=1,
        )
        detalle = construir_detalle_txt_desde_dte_productos([dp], tipo_numerico=39)

        self.assertEqual(detalle[0]['codigo'], 'POLERA')
        self.assertNotIn('(', detalle[0]['nombre'])

    def test_boleta_txt_lleva_sku_en_codigo_y_nombre(self):
        datos = _datos_boleta_base()
        datos['detalle'] = construir_detalle_txt_desde_dte_productos(
            [self._dp(sku=784512, talla='42', stock=1)], tipo_numerico=39)

        txt = generar_txt_boleta_acepta(datos)
        lineas_prod = [l for l in txt.split('\n') if l.startswith('INT1|')]
        self.assertEqual(len(lineas_prod), 1)
        campos = lineas_prod[0].split('|')
        self.assertEqual(campos[1], '784512', 'CdgItem debe ser el SKU')
        self.assertTrue(campos[3].startswith('784512 '),
                        'NmbItem debe partir con el SKU (paridad con POS)')


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


class TestTxtEcommerceDetalleSumaNeto(TestCase):
    """
    Regresión del bug de TXT de Acepta en pedidos ecommerce.

    El TXT de ecommerce (auto-descarga al facturar y botón de re-descarga) hoy
    delega en `construir_datos_txt_desde_dte`, que arma el detalle con
    `construir_detalle_txt_desde_dte_productos` + `normalizar_detalle_para_tipo`,
    el MISMO camino que /app/ventas/documentos/. Para una FACTURA (33), las
    líneas de `Dte_Productos` vienen CON IVA (bruto) y el detalle del TXT debe
    sumar EXACTAMENTE `monto_neto`.

    El generador viejo de ecommerce hacía `round(precio/1.19)*cantidad` por línea
    sin normalizar, y la suma se desviaba del neto (y perdía descuentos). Estos
    tests reproducen el camino correcto sin tocar la BD (mocks SimpleNamespace,
    igual que TestAgrupacionDetalleDteExistente).
    """

    def _dp(self, articulo, color, precio_bruto, stock=1):
        producto = SimpleNamespace(
            articulo=articulo,
            costo=0,
            atributo1=SimpleNamespace(valor='ACME'),
            atributo2=SimpleNamespace(valor=color),
        )
        producto_talla = SimpleNamespace(producto=producto, talla='U')
        return SimpleNamespace(
            productoTalla=producto_talla,
            descripcion='',
            costo=0,
            precio=precio_bruto,
            precio_unitario=precio_bruto,
            descuento_pct=0,
            descuento_monto=0,
            monto_item=precio_bruto * stock,  # bruto realmente cobrado (= tp.subtotal)
            stock=stock,
        )

    def test_detalle_factura_ecommerce_suma_neto_exacto(self):
        # 3 líneas en base BRUTA (con IVA) y precio que genera residuo de redondeo.
        dps = [
            self._dp('A', 'AZUL', 9999),
            self._dp('B', 'ROJO', 9999),
            self._dp('C', 'VERDE', 9999),
        ]
        total_bruto = sum(dp.monto_item for dp in dps)          # 29997
        monto_neto = round(total_bruto / 1.19)                  # 25208

        detalle = construir_detalle_txt_desde_dte_productos(dps, tipo_numerico=33)
        # 3 variantes distintas (color) → 3 líneas, en base BRUTA antes de normalizar.
        self.assertEqual(len(detalle), 3)
        self.assertEqual(sum(int(i['monto_item']) for i in detalle), total_bruto)

        totales = {'monto_neto': monto_neto, 'monto_total': total_bruto}
        normalizar_detalle_para_tipo(detalle, totales, 33)

        # Tras normalizar el detalle suma EXACTAMENTE el neto (clave para Acepta).
        self.assertEqual(sum(int(i['monto_item']) for i in detalle), monto_neto)

    def test_camino_viejo_round_por_linea_no_cuadraba_con_neto(self):
        # Demuestra por qué el fix importa: el camino viejo (round(precio/1.19)
        # por línea) NO sumaba el neto del documento.
        precios = [9999, 9999, 9999]
        total_bruto = sum(precios)                              # 29997
        monto_neto = round(total_bruto / 1.19)                  # 25208
        suma_vieja = sum(round(p / 1.19) for p in precios)      # 3 * 8403 = 25209
        self.assertNotEqual(suma_vieja, monto_neto)

    def test_detalle_factura_con_descuento_monto_se_conserva(self):
        # Una línea con descuento_monto materializado: construir_detalle debe
        # arrastrar monto_descuento y monto_item (neto de descuento) hacia el TXT.
        dp = self._dp('POLERA', 'AZUL', 10000, stock=2)
        dp.descuento_pct = 10.0
        dp.descuento_monto = 2000          # total línea
        dp.monto_item = 18000              # 2*10000 - 2000
        detalle = construir_detalle_txt_desde_dte_productos([dp], tipo_numerico=33)
        self.assertEqual(len(detalle), 1)
        self.assertEqual(int(detalle[0]['monto_descuento']), 2000)
        self.assertEqual(int(detalle[0]['monto_item']), 18000)
        self.assertEqual(float(detalle[0]['descuento_pct']), 10.0)


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


def _datos_nc_base(tipo_documento=61):
    """Datos mínimos para una Nota de Crédito/Débito (61/56) afecta."""
    return {
        'documento': {
            'tipo_documento': tipo_documento,
            'folio': 408,
            'fecha_emision': '2026-02-28',
            'forma_pago': 2,
            'fecha_vencimiento': '2026-02-28',
        },
        'emisor': {
            'rut': '76123456-7',
            'razon_social': 'Empresa Test',
            'giro': 'Comercio',
            'acteco': '461000',
            'sucursal': 'CASA MATRIZ',
            'direccion': 'Av Test 123',
            'comuna': 'Santiago',
            'ciudad': 'Santiago',
            'codigo_vendedor': 'V01',
        },
        'receptor': {
            'rut': '77888999-K',
            'razon_social': 'Cliente Test',
            'giro': 'Comercio',
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
        'detalle': [{
            'codigo': 'SRV', 'nombre': 'Servicio', 'descripcion': '',
            'cantidad': 1, 'unidad': 'UN', 'precio_unitario': 100000,
            'monto_item': 100000, 'indicador_exencion': '',
        }],
        'referencias': [{
            'tipo_documento': 33, 'folio': '709', 'fecha': '2026-02-20', 'razon': '1',
        }],
    }


class TestNotaCreditoDebitoExencion(TestCase):
    """NC (61) / ND (56): exención heredada del documento referenciado y ND comparte formato Acepta de la NC."""

    def _linea_totales(self, txt):
        # En el formato NC/ND la línea de totales es la 5ª (índice 4):
        # IdDoc, Emisor, Receptor, Transporte, Totales.
        return txt.split('\n')[4].split('|')

    def test_nc_afecta_lleva_iva(self):
        # NC que referencia una factura afecta (33): tasa 19 e IVA presentes.
        datos = _datos_nc_base(61)
        txt = generar_txt_nota_credito_acepta(datos)
        campos = self._linea_totales(txt)
        self.assertEqual(campos[0], '100000')   # neto
        self.assertEqual(campos[2], '19')        # tasa IVA
        self.assertEqual(campos[3], '19000')     # iva
        self.assertEqual(campos[4], '119000')   # total

    def test_nc_exenta_sin_iva(self):
        # NC que anula una factura exenta (34): monto a EXENTO, tasa/IVA vacíos.
        datos = _datos_nc_base(61)
        datos['totales'] = {
            'monto_neto': 0, 'monto_exento': 415000,
            'tasa_iva': 0, 'iva': 0, 'monto_total': 415000,
        }
        datos['detalle'][0].update({
            'precio_unitario': 415000, 'monto_item': 415000, 'indicador_exencion': 1,
        })
        datos['referencias'] = [
            {'tipo_documento': 34, 'folio': '366', 'fecha': '2026-02-28', 'razon': '1'},
        ]
        txt = generar_txt_nota_credito_acepta(datos)
        campos = self._linea_totales(txt)
        self.assertEqual(campos[1], '415000')   # exento
        self.assertEqual(campos[2], '')          # tasa vacía → sin IVA
        self.assertEqual(campos[4], '415000')   # total = exento
        # La línea de detalle (índice 6: 5 cabeceras + '~') lleva IndExe=1.
        detalle = txt.split('\n')[6]
        self.assertEqual(detalle.split('|')[0], '1')

    def test_nd_56_usa_formato_nc_y_escribe_tipo_56(self):
        # El dispatcher rutea el 56 al generador de NC y escribe 56 en el IdDoc.
        datos = _datos_nc_base(56)
        txt = generar_txt_dte_acepta(datos)
        primera_linea = txt.split('\n')[0]
        self.assertTrue(primera_linea.startswith('56|'))

    def test_construir_detalle_marca_indexe_segun_es_exenta(self):
        dp = SimpleNamespace(
            productoTalla=None, descripcion='Arriendo Febrero', stock=1,
            precio_unitario=415000, precio=415000,
            descuento_pct=0, descuento_monto=0, monto_item=415000,
        )
        # NC/ND exenta → IndExe=1 por línea.
        detalle_exenta = construir_detalle_txt_desde_dte_productos(
            [dp], tipo_numerico=61, es_exenta=True)
        self.assertEqual(detalle_exenta[0]['indicador_exencion'], 1)

        # NC/ND afecta → sin IndExe.
        detalle_afecta = construir_detalle_txt_desde_dte_productos(
            [dp], tipo_numerico=61, es_exenta=False)
        self.assertEqual(detalle_afecta[0]['indicador_exencion'], '')

        # Backward-compat: Factura Exenta (34) sigue marcando IndExe sin el flag.
        detalle_34 = construir_detalle_txt_desde_dte_productos([dp], tipo_numerico=34)
        self.assertEqual(detalle_34[0]['indicador_exencion'], 1)


class TestConstruirDatosTxtDesdeDteExencion(TestCase):
    """Camino real BD→TXT (`construir_datos_txt_desde_dte`): detección de exención
    de NC/ND a partir de los montos persistidos. Cubre el fix donde una NC/ND que
    referencia un documento exento debe emitirse sin IVA aunque su tipo (61/56) no
    lo indique."""

    def setUp(self):
        from app.tests.factories import crear_empresa, crear_sucursal
        from app.models import Dte, Dte_Productos
        self.Dte = Dte
        self.Dte_Productos = Dte_Productos
        self.emisor = crear_empresa(nombre='Emisor', rut='76.000.000-0')
        self.receptor = crear_empresa(nombre='Receptor', rut='77.888.999-K')
        self.sucursal = crear_sucursal(empresa=self.emisor, alias='CASA MATRIZ',
                                       comuna='Antofagasta', ciudad='Antofagasta')

    def _crear_nc(self, tipo_documento, monto_neto, monto_con_iva, ref_tipo):
        """Crea una NC/ND con un ítem de concepto y referencia a un doc original."""
        dte = self.Dte.objects.create(
            emisor=self.emisor,
            receptor=self.receptor,
            numero_documento=5019,
            tipo_documento=tipo_documento,
            monto_neto=monto_neto,
            monto_con_iva=monto_con_iva,
            estado_pago='PENDIENTE',
            estado_dte='EMITIDO',
            responsable='tester',
            fecha_emision=date(2026, 2, 28),
            fecha_vencimiento=date(2026, 2, 28),
            diasCredito=0,
            bultos=0,
            unidades_productos=0,
            tipo_transaccion='VENTA',
            referencias=json.dumps([{
                'tipo_documento': ref_tipo, 'folio': '366',
                'fecha': '2026-02-28', 'razon': '1',
            }]),
            sucursal=self.sucursal,
            es_nota_credito=(tipo_documento == 'NOTA DE CREDITO'),
        )
        self.Dte_Productos.objects.create(
            dte=dte, productoTalla=None, descripcion='[PAR] Arriendo Febrero',
            costo=0, sobreprecio=0, precio=monto_con_iva,
            precio_unitario=monto_con_iva, monto_item=monto_con_iva,
            stock=1, activo=True,
        )
        return dte

    def test_nc_sobre_exenta_se_construye_sin_iva(self):
        # Emitida exenta → monto_con_iva == monto_neto. Debe ir todo a EXENTO.
        dte = self._crear_nc('NOTA DE CREDITO', 415000, 415000, ref_tipo=34)
        datos = construir_datos_txt_desde_dte(dte)

        self.assertEqual(datos['totales']['monto_neto'], 0)
        self.assertEqual(datos['totales']['monto_exento'], 415000)
        self.assertEqual(datos['totales']['tasa_iva'], 0)
        self.assertEqual(datos['totales']['iva'], 0)
        self.assertEqual(datos['totales']['monto_total'], 415000)
        self.assertEqual(datos['detalle'][0]['indicador_exencion'], 1)

        # Y el TXT resultante deja la tasa vacía y el exento poblado.
        txt = generar_txt_dte_acepta(datos)
        campos_totales = txt.split('\n')[4].split('|')
        self.assertEqual(campos_totales[1], '415000')  # exento
        self.assertEqual(campos_totales[2], '')          # tasa vacía

    def test_nc_sobre_afecta_se_construye_con_iva(self):
        # Emitida afecta → monto_con_iva = neto * 1.19. Debe llevar IVA.
        dte = self._crear_nc('NOTA DE CREDITO', 100000, 119000, ref_tipo=33)
        datos = construir_datos_txt_desde_dte(dte)

        self.assertEqual(datos['totales']['monto_neto'], 100000)
        self.assertEqual(datos['totales']['monto_exento'], 0)
        self.assertEqual(datos['totales']['tasa_iva'], 19)
        self.assertEqual(datos['totales']['iva'], 19000)
        self.assertEqual(datos['detalle'][0]['indicador_exencion'], '')

    def test_nota_debito_mapea_a_56_y_hereda_exencion(self):
        # ND exenta: tipo_numerico 56 y exención heredada del doc referenciado.
        dte = self._crear_nc('NOTA DE DEBITO', 415000, 415000, ref_tipo=41)
        datos = construir_datos_txt_desde_dte(dte)

        self.assertEqual(datos['documento']['tipo_documento'], 56)
        self.assertEqual(datos['totales']['monto_exento'], 415000)
        self.assertEqual(datos['totales']['iva'], 0)
        self.assertEqual(datos['detalle'][0]['indicador_exencion'], 1)

        # El dispatcher rutea 56 al formato NC y escribe 56 en el IdDoc.
        txt = generar_txt_dte_acepta(datos)
        self.assertTrue(txt.split('\n')[0].startswith('56|'))


class TestReceptorFacturaTomaDatosDelTicket(TestCase):
    """Regresión: al facturar (típicamente una cotización a empresa) el receptor
    del DTE/TXT debe reflejar la comuna/ciudad/giro del TICKET (lo que el cajero
    vio y corrigió en el POS), NO la ficha Empresa.

    Bug original: `generar_dte_desde_ticket` leía SOLO `receptor.*`, y buscaba la
    Empresa con `filter(rut=...).first()` sin `order_by`. Con ~35 RUTs de ficha
    duplicada, `.first()` era no determinístico ("no se sabía qué comuna tomaba"),
    y con fichas legacy que guardan el RUT CON puntos ni siquiera la encontraba,
    creando una ficha duplicada. Ahora: búsqueda determinística tolerante a puntos
    + el TXT usa los datos del ticket con fallback a la ficha.
    """

    def setUp(self):
        from app.tests.factories import setup_entorno_completo, crear_correlativo
        self.entorno = setup_entorno_completo()
        self.sucursal = self.entorno['sucursal']
        self.vendedor = self.entorno['vendedor']
        self.user = self.entorno['user']
        self.producto_talla = self.entorno['producto_talla']
        # generar_dte_desde_ticket pide el correlativo de FACTURA ELECTRONICA.
        crear_correlativo(self.sucursal, tipo_dte='FACTURA ELECTRONICA')

    def _crear_ticket_factura(self):
        from app.models import Ticket, Ticket_Productos
        ticket = Ticket.objects.create(
            correlativo=5001,
            sucursal=self.sucursal,
            vendedor=self.vendedor,
            subTotal=20000,
            descuento=0,
            total=20000,
            estado='PAGADO',
            responsable=self.user.username,
            cliente_nombre='Cliente Factura SpA',
            cliente_rut='77139990-8',        # normalizado, sin puntos
            cliente_giro='GIRO TICKET',
            cliente_direccion='DIRECCION TICKET 123',
            cliente_comuna='COMUNATICKET',
            cliente_ciudad='CIUDADTICKET',
            modulo_origen='POS',
        )
        Ticket_Productos.objects.create(
            idTicket=ticket,
            ProductoTalla=self.producto_talla,
            stock=1,
            precio=20000,
            precio_original=20000,
            descuento_unitario=0,
            subtotal=20000,
            porcentaje_descuento=0,
        )
        return ticket

    def test_txt_receptor_usa_comuna_ciudad_del_ticket_y_no_de_ficha(self):
        from app.models import Empresa
        from app.tests.factories import crear_empresa
        # Ficha Empresa legacy con RUT CON puntos y datos VIEJOS/distintos.
        receptor = crear_empresa(
            nombre='Cliente Factura', rut='77.139.990-8',
            comuna='COMUNAFICHA', ciudad='CIUDADFICHA', giro='GIRO FICHA',
            direccion='DIRECCION FICHA 999',
        )

        ticket = self._crear_ticket_factura()
        dte = generar_dte_desde_ticket(ticket, 'FACTURA_ELECTRONICA', self.user)

        # 1) Se reusó la ficha existente pese al formato con puntos (no se duplicó).
        self.assertEqual(dte.receptor_id, receptor.id)
        self.assertEqual(
            Empresa.objects.filter(rut__in=['77.139.990-8', '77139990-8']).count(), 1,
            'No debe crearse una ficha Empresa duplicada por el formato del RUT',
        )

        # 2) El TXT del DTE lleva la comuna/ciudad DEL TICKET, no la de la ficha.
        txt = dte.archivo_txt_data['contenido']
        self.assertIn('COMUNATICKET', txt)
        self.assertIn('CIUDADTICKET', txt)
        self.assertNotIn('COMUNAFICHA', txt)
        self.assertNotIn('CIUDADFICHA', txt)

    def test_sin_ficha_previa_crea_receptor_con_datos_del_ticket(self):
        from app.models import Empresa
        self.assertFalse(Empresa.objects.filter(rut='77139990-8').exists())

        ticket = self._crear_ticket_factura()
        dte = generar_dte_desde_ticket(ticket, 'FACTURA_ELECTRONICA', self.user)

        self.assertIsNotNone(dte.receptor_id)
        txt = dte.archivo_txt_data['contenido']
        self.assertIn('COMUNATICKET', txt)
        self.assertIn('CIUDADTICKET', txt)

