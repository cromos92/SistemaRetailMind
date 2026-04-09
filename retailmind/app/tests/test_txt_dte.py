"""
Tests para generación y parseo de archivos TXT DTE en formato Acepta.
Cubre: factura con/sin descuento, boleta, descuentos globales, parser, retrocompatibilidad.
"""
from django.test import TestCase
from app.views_modulo_documentos import (
    generar_txt_dte_acepta,
    generar_txt_boleta_acepta,
    parsear_txt_acepta,
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
        self.assertIn('D|Dcto 10%|%|10|', txt)


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
        self.assertIn('D|Descuento Fijo|$|5000|1|}', txt)


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
        self.assertIn('R|Recargo envio|$|3000|', txt)


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
