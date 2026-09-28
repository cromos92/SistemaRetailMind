"""
Importación XML de DTE de proveedor: arreglos de la auditoría (unidad X).

  B11-11/B15-08  estado_pago canónico (utils_estado_pago), nunca lo que mande el cliente
  B11-08         exenta: la base sin IVA es neto + exento
  B11-12         ND 56 no es nota de crédito; cantidades decimales half-up
  B11-09         un código de estilo repetido en varias tallas no se aprende
                 ni da verde con la talla equivocada
  B11-10         el receptor tiene que ser una empresa propia (con sucursal)
  B11-16         tamaño antes de read(), tope de líneas por lote, precarga
  B11-07         el escape del wizard cubre comillas (atributos)

Ejecutar (BD de test, NO producción):
    python manage.py test app.tests.test_x_import_xml_dte
"""
import json
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app import views_modulo_compras_xml as mod
from app.models import Dte, Dte_Productos, Producto_Talla, ProveedorProductoEquivalencia
from app.services.dte_xml_parser import MAX_BYTES_ARCHIVO, parsear_xml_dte
from app.tests.factories import crear_empresa, crear_producto_con_talla
from app.tests.test_import_xml_dte import _BaseXmlDteTest
from app.utils_estado_pago import ESTADO_PAGO_PENDIENTE, q_estado_pago_pendiente


def _xml(tipo=33, folio=5001, totales='', lineas='', referencias='',
         rut_recep='77000000-1'):
    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        '<EnvioDTE xmlns="http://www.sii.cl/SiiDte"><SetDTE><DTE><Documento>'
        '<Encabezado>'
        f'<IdDoc><TipoDTE>{tipo}</TipoDTE><Folio>{folio}</Folio>'
        '<FchEmis>2026-07-25</FchEmis><FchVenc>2026-08-24</FchVenc></IdDoc>'
        '<Emisor><RUTEmisor>76543210-K</RUTEmisor><RznSoc>PROVEEDOR</RznSoc></Emisor>'
        f'<Receptor><RUTRecep>{rut_recep}</RUTRecep></Receptor>'
        f'<Totales>{totales}</Totales>'
        '</Encabezado>'
        + lineas + referencias +
        '</Documento></DTE></SetDTE></EnvioDTE>'
    )


def _linea(nro, nombre, qty, prc, monto, codigos=()):
    cdg = ''.join(f'<CdgItem><TpoCodigo>{t}</TpoCodigo><VlrCodigo>{v}</VlrCodigo></CdgItem>'
                  for t, v in codigos)
    qty_xml = f'<QtyItem>{qty}</QtyItem>' if qty is not None else ''
    return (f'<Detalle><NroLinDet>{nro}</NroLinDet>{cdg}<NmbItem>{nombre}</NmbItem>'
            f'{qty_xml}<PrcItem>{prc}</PrcItem><MontoItem>{monto}</MontoItem></Detalle>')


class TestXImportXmlDte(_BaseXmlDteTest):

    def _confirmar_ok(self, xml, lineas=None, **extra):
        doc = self._primer_documento(xml)
        self.assertTrue(doc['puede_confirmar'], doc['bloqueos'])
        lineas = lineas if lineas is not None else [
            {'nro_linea': l['nro_linea']} for l in doc['detalle']]
        resp = self._confirmar(doc, lineas, **extra)
        self.assertEqual(resp.status_code, 200, resp.content[:500])
        return Dte.objects.get(id=resp.json()['dte_id']), doc

    # ------------------------------------------------ B11-11 / B15-08

    def test_factura_importada_queda_pendiente_como_el_resto_del_flujo(self):
        xml = _xml(totales='<MntNeto>1000</MntNeto><TasaIVA>19</TasaIVA><IVA>190</IVA>'
                           '<MntTotal>1190</MntTotal>',
                   lineas=_linea(1, 'ITEM', 1, 1000, 1000))
        dte, _doc = self._confirmar_ok(xml, estado_pago='cualquier cosa')
        # El valor canónico (no lo que mande el cliente) y visible para el
        # filtro de deuda que usan la lista y los KPI.
        self.assertEqual(dte.estado_pago, ESTADO_PAGO_PENDIENTE)
        self.assertTrue(Dte.objects.filter(q_estado_pago_pendiente(), id=dte.id).exists())

    # ------------------------------------------------------- B11-08

    def test_factura_exenta_neto_igual_al_total(self):
        xml = _xml(tipo=34, totales='<MntExe>50000</MntExe><MntTotal>50000</MntTotal>',
                   lineas=_linea(1, 'LIBRO', 1, 50000, 50000))
        dte, _doc = self._confirmar_ok(xml)
        self.assertEqual(dte.tipo_documento, 'FACTURA EXENTA')
        self.assertEqual(dte.monto_neto, Decimal('50000'))
        self.assertEqual(dte.monto_con_iva - dte.monto_neto, 0)   # IVA derivado

    def test_factura_con_lineas_exentas_iva_derivado_real(self):
        xml = _xml(totales='<MntNeto>1000</MntNeto><MntExe>500</MntExe><TasaIVA>19</TasaIVA>'
                           '<IVA>190</IVA><MntTotal>1690</MntTotal>',
                   lineas=_linea(1, 'AFECTO', 1, 1000, 1000) + _linea(2, 'EXENTO', 1, 500, 500))
        dte, _doc = self._confirmar_ok(xml)
        self.assertEqual(dte.monto_neto, Decimal('1500'))
        self.assertEqual(dte.monto_con_iva - dte.monto_neto, Decimal('190'))
        self.assertIn('Exento $500', dte.referencias)

    def test_factura_normal_sin_exento_no_cambia(self):
        xml = _xml(totales='<MntNeto>1000</MntNeto><TasaIVA>19</TasaIVA><IVA>190</IVA>'
                           '<MntTotal>1190</MntTotal>',
                   lineas=_linea(1, 'ITEM', 1, 1000, 1000))
        dte, _doc = self._confirmar_ok(xml)
        self.assertEqual(dte.monto_neto, Decimal('1000'))
        self.assertFalse(dte.referencias)

    # ------------------------------------------------------- B11-12

    def test_nota_de_debito_no_es_nota_de_credito(self):
        ref = ('<Referencia><NroLinRef>1</NroLinRef><TpoDocRef>33</TpoDocRef>'
               '<FolioRef>280</FolioRef><FchRef>2026-07-01</FchRef></Referencia>')
        tot = '<MntNeto>1000</MntNeto><TasaIVA>19</TasaIVA><IVA>190</IVA><MntTotal>1190</MntTotal>'
        nd, _ = self._confirmar_ok(_xml(tipo=56, folio=7001, totales=tot,
                                        lineas=_linea(1, 'INTERES', 1, 1000, 1000), referencias=ref))
        self.assertEqual(nd.tipo_documento, 'NOTA DE DEBITO')
        self.assertFalse(nd.es_nota_credito)
        nc, _ = self._confirmar_ok(_xml(tipo=61, folio=7002, totales=tot,
                                        lineas=_linea(1, 'DEVOLUCION', 1, 1000, 1000), referencias=ref))
        self.assertEqual(nc.tipo_documento, 'NOTA DE CREDITO')
        self.assertTrue(nc.es_nota_credito)

    def test_cantidad_decimal_se_redondea_half_up_y_avisa(self):
        xml = _xml(totales='<MntNeto>2500</MntNeto><TasaIVA>19</TasaIVA><IVA>475</IVA>'
                           '<MntTotal>2975</MntTotal>',
                   lineas=_linea(1, 'TELA', '2.5', 1000, 2500))
        dte, doc = self._confirmar_ok(xml)
        self.assertTrue(any('no es entera' in w for w in doc['validacion']['warnings']),
                        doc['validacion']['warnings'])
        linea = Dte_Productos.objects.get(dte=dte)
        self.assertEqual(linea.stock, 3)
        self.assertEqual(dte.unidades_productos, 3)

    def test_cantidad_menor_a_media_unidad_exige_escribirla(self):
        xml = _xml(totales='<MntNeto>400</MntNeto><TasaIVA>19</TasaIVA><IVA>76</IVA>'
                           '<MntTotal>476</MntTotal>',
                   lineas=_linea(1, 'CINTA', '0.4', 1000, 400))
        doc = self._primer_documento(xml)
        self.assertTrue(any('no alcanza a una unidad' in w for w in doc['validacion']['warnings']))
        resp = self._confirmar(doc, [{'nro_linea': 1, 'cantidad': '0.4'}])
        self.assertEqual(resp.status_code, 400)
        self.assertIn('no alcanza a una unidad', resp.json()['error'])
        self.assertFalse(Dte.objects.filter(numero_documento=5001).exists())

        resp = self._confirmar(doc, [{'nro_linea': 1, 'cantidad': '1'}])
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.assertEqual(Dte_Productos.objects.get(dte_id=resp.json()['dte_id']).stock, 1)

    def test_cantidad_nan_no_tumba_el_analisis_y_queda_sin_cantidad(self):
        """QtyItem 'NaN' (Decimal lo acepta): el análisis responde 200, la
        línea queda sin cantidad y confirmar exige escribirla."""
        tot = '<MntNeto>1000</MntNeto><TasaIVA>19</TasaIVA><IVA>190</IVA><MntTotal>1190</MntTotal>'
        for qty in ('NaN', 'Infinity'):
            doc = self._primer_documento(_xml(totales=tot, lineas=_linea(1, 'ITEM', qty, 1000, 1000)))
            self.assertEqual(doc['validacion']['lineas_sin_cantidad'], [1])
            self.assertTrue(any('no es un número' in w for w in doc['validacion']['warnings']),
                            doc['validacion']['warnings'])
            self.assertIsNone(doc['detalle'][0]['cantidad'])
        resp = self._confirmar(doc, [{'nro_linea': 1, 'cantidad': 'NaN'}])
        self.assertEqual(resp.status_code, 400, resp.content[:300])
        self.assertIn('falta la cantidad', resp.json()['error'])
        resp = self._confirmar(doc, [{'nro_linea': 1, 'cantidad': '2'}])
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.assertEqual(Dte_Productos.objects.get(dte_id=resp.json()['dte_id']).stock, 2)

    # ------------------------------------------------------- B11-09

    def _dos_tallas(self):
        producto, t40 = crear_producto_con_talla(
            self.sucursal, articulo='J0779', talla='40', sku=6026240)
        t41 = Producto_Talla.objects.create(producto=producto, sku=6026250, stock=0, talla='41')
        return t40, t41

    def _xml_estilo(self, folio):
        lineas = (_linea(1, 'ZAPATILLA J0779', 1, 1000, 1000,
                         [('INT1', 'MOD-XYZ'), ('EAN13', '7800000000011')])
                  + _linea(2, 'ZAPATILLA J0779', 1, 1000, 1000,
                           [('INT1', 'MOD-XYZ'), ('EAN13', '7800000000028')]))
        return _xml(folio=folio, totales='<MntNeto>2000</MntNeto><TasaIVA>19</TasaIVA>'
                                          '<IVA>380</IVA><MntTotal>2380</MntTotal>', lineas=lineas)

    def test_codigo_de_estilo_repetido_no_se_aprende_y_el_ean_manda(self):
        t40, t41 = self._dos_tallas()
        # Factura 1: resuelta a mano, una talla por línea.
        self._confirmar_ok(self._xml_estilo(9001), lineas=[
            {'nro_linea': 1, 'producto_talla_id': t40.id, 'guardar_equivalencia': True},
            {'nro_linea': 2, 'producto_talla_id': t41.id, 'guardar_equivalencia': True},
        ])
        aprendidas = dict(ProveedorProductoEquivalencia.objects.filter(
            empresa_proveedor=self.proveedor).values_list('codigo_externo', 'producto_talla_id'))
        self.assertEqual(aprendidas, {'7800000000011': t40.id, '7800000000028': t41.id})

        # Factura 2 idéntica: cada línea con SU talla, en verde.
        doc = self._primer_documento(self._xml_estilo(9002))
        m1, m2 = (l['match'] for l in doc['detalle'])
        self.assertEqual((m1['confianza'], m1['propuesta']['producto_talla_id']),
                         ('ALTA', t40.id))
        self.assertEqual((m2['confianza'], m2['propuesta']['producto_talla_id']),
                         ('ALTA', t41.id))

    def test_equivalencias_en_conflicto_no_dan_verde(self):
        """Equivalencia vieja ya corrupta (MOD-XYZ → talla 41) y el EAN de la
        línea apunta a la 40: se propone la del EAN en amarillo."""
        t40, t41 = self._dos_tallas()
        for codigo, tipo, pt in (('MOD-XYZ', 'INT1', t41), ('7800000000011', 'EAN13', t40)):
            ProveedorProductoEquivalencia.objects.create(
                empresa_proveedor=self.proveedor, codigo_externo=codigo,
                tipo_codigo=tipo, producto_talla=pt)
        linea = parsear_xml_dte(self._xml_estilo(9003).encode('iso-8859-1'))['detalle'][0]
        match = mod.matchear_linea(linea, self.proveedor.id, self.sucursal.id)
        self.assertEqual(match['confianza'], 'MEDIA')
        self.assertEqual(match['propuesta']['producto_talla_id'], t40.id)
        self.assertEqual(match['codigo_usado'], '7800000000011')
        self.assertEqual({c['producto_talla_id'] for c in match['candidatos']}, {t40.id, t41.id})

        # Sin código global que desempate: sin propuesta, con candidatos.
        ProveedorProductoEquivalencia.objects.filter(codigo_externo='7800000000011').update(
            codigo_externo='OTRO-INT', tipo_codigo='INT2')
        linea['codigos'] = [{'tipo': 'INT1', 'valor': 'MOD-XYZ'},
                            {'tipo': 'INT2', 'valor': 'OTRO-INT'}]
        match = mod.matchear_linea(linea, self.proveedor.id, self.sucursal.id)
        self.assertEqual(match['confianza'], 'MEDIA')
        self.assertIsNone(match['propuesta'])
        self.assertEqual(len(match['candidatos']), 2)

    # ------------------------------------------------------- B11-10

    def test_receptor_debe_ser_empresa_propia_con_sucursal(self):
        # Un cliente (sin sucursal) que el administrador "ve" como empresa activa.
        crear_empresa(nombre='Cliente Persona', rut='18312585-0')
        xml = _xml(rut_recep='18312585-0',
                   totales='<MntNeto>1000</MntNeto><TasaIVA>19</TasaIVA><IVA>190</IVA>'
                           '<MntTotal>1190</MntTotal>',
                   lineas=_linea(1, 'ITEM', 1, 1000, 1000))
        doc = self._primer_documento(xml)
        self.assertFalse(doc['empresa_receptora']['reconocida'])
        self.assertFalse(doc['puede_confirmar'])

        # Tampoco se puede forzar una empresa ajena como receptora.
        ajena = crear_empresa(nombre='Otra Sin Sucursal', rut='11111111-1')
        resp = self._confirmar(doc, [{'nro_linea': 1}], empresa_receptora_id=ajena.id)
        self.assertEqual(resp.status_code, 400)
        # Pero sí la propia.
        resp = self._confirmar(doc, [{'nro_linea': 1}], empresa_receptora_id=self.empresa.id)
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.assertEqual(Dte.objects.get(id=resp.json()['dte_id']).receptor_id, self.empresa.id)

    def test_select_de_receptora_solo_lista_empresas_propias(self):
        crear_empresa(nombre='Cliente Persona XYZ', rut='18312585-0')
        resp = self.client.get(reverse('ver_importar_xml_dte'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([e.id for e in resp.context['empresas']], [self.empresa.id])

    # ------------------------------------------------------- B11-16

    def test_archivo_grande_se_rechaza_antes_de_leerlo(self):
        archivo = mock.Mock()
        archivo.name = 'enorme.xml'
        archivo.size = MAX_BYTES_ARCHIVO + 1
        resultado = mod._analizar_archivo(archivo, self.sucursal.id, [self.empresa])
        self.assertFalse(resultado['ok'])
        self.assertIn('supera el máximo', resultado['error'])
        archivo.read.assert_not_called()

    def test_tope_de_lineas_por_lote(self):
        tot = '<MntNeto>2000</MntNeto><TasaIVA>19</TasaIVA><IVA>380</IVA><MntTotal>2380</MntTotal>'
        lineas = _linea(1, 'A', 1, 1000, 1000) + _linea(2, 'B', 1, 1000, 1000)
        archivos = [SimpleUploadedFile(f'f{i}.xml', _xml(folio=8000 + i, totales=tot,
                                                        lineas=lineas).encode('iso-8859-1'),
                                       content_type='text/xml') for i in range(2)]
        with mock.patch.object(mod, 'MAX_LINEAS_POR_LOTE', 3):
            resp = self.client.post(reverse('analizar_xml_dte'),
                                    {'archivos': archivos, 'sucursal_id': self.sucursal.id})
        data = resp.json()
        self.assertTrue(data['archivos'][0]['ok'])
        self.assertFalse(data['archivos'][1]['ok'])
        self.assertIn('Divide el lote', data['archivos'][1]['error'])

    def test_precarga_no_hace_consultas_por_linea_de_codigo(self):
        """Equivalencias y SKU se leen una vez por documento (antes 1-3 por línea)."""
        detalle = []
        for i in range(1, 21):
            _p, pt = crear_producto_con_talla(self.sucursal, articulo=f'ART-{i}', talla='U',
                                              sku=4400000 + i)
            detalle.append({'nro_linea': i, 'nombre': f'ART-{i}',
                            'codigos': [{'tipo': 'EAN13', 'valor': str(pt.sku)}]})
        with CaptureQueriesContext(connection) as ctx:
            lineas = mod.matchear_detalle(detalle, self.proveedor.id, self.sucursal.id)
        self.assertTrue(all(l['match']['origen'] == 'SKU' and l['match']['confianza'] == 'ALTA'
                            for l in lineas))
        self.assertLessEqual(len(ctx.captured_queries), 3)

    # ------------------------------------------------------- B11-07

    def test_escape_del_wizard_cubre_comillas(self):
        resp = self.client.get(reverse('ver_importar_xml_dte'))
        html = resp.content.decode('utf-8')
        self.assertIn(""".replace(/"/g, '&quot;')""", html)
        self.assertNotIn("return $('<div>').text(", html)

    def test_error_inesperado_de_lectura_no_expone_la_excepcion(self):
        archivo = SimpleUploadedFile('x.xml', b'<a/>', content_type='text/xml')
        with mock.patch.object(mod, 'parsear_xml_envio', side_effect=RuntimeError('detalle interno')):
            resultado = mod._analizar_archivo(archivo, self.sucursal.id, [self.empresa])
        self.assertFalse(resultado['ok'])
        self.assertNotIn('detalle interno', resultado['error'])

    # ------------------------------------- comando de datos históricos

    def _dte_importado(self, tipo, **campos):
        datos = dict(
            emisor=self.proveedor, receptor=self.empresa, numero_documento=4242,
            tipo_documento=tipo, monto_con_iva=1190, monto_neto=1000,
            estado_pago='PENDIENTE', estado_dte='EMITIDO', responsable='xml',
            fecha_emision='2026-07-01', fecha_vencimiento='2026-07-31', diasCredito=30,
            bultos=0, unidades_productos=1, tipo_transaccion='COMPRA',
            es_manual=True, es_por_concepto=False)
        datos.update(campos)
        return Dte.objects.create(**datos)

    def test_comando_corrige_nd_y_exentas_importadas(self):
        from io import StringIO

        from django.core.management import call_command

        nd = self._dte_importado('NOTA DE DEBITO', es_nota_credito=True)
        exenta = self._dte_importado('FACTURA EXENTA', numero_documento=4243,
                                     monto_con_iva=50000, monto_neto=0)
        # Una ND que NO vino del importador (sin es_manual) no se toca.
        ajena = self._dte_importado('NOTA DE DEBITO', numero_documento=4244,
                                    es_nota_credito=True, es_manual=False)

        salida = StringIO()
        call_command('xml_dte_normalizar_importados', stdout=salida)
        self.assertIn('DRY-RUN', salida.getvalue())
        nd.refresh_from_db()
        self.assertTrue(nd.es_nota_credito)

        call_command('xml_dte_normalizar_importados', '--apply', stdout=StringIO())
        nd.refresh_from_db()
        exenta.refresh_from_db()
        ajena.refresh_from_db()
        self.assertFalse(nd.es_nota_credito)
        self.assertEqual(exenta.monto_neto, Decimal('50000'))
        self.assertTrue(ajena.es_nota_credito)
