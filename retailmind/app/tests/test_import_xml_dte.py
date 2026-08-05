"""
Tests de la importación de XML DTE de proveedor (entrada de facturas).

Cubre:
  - Parseo correcto y shape compatible con `parsear_txt_acepta`.
  - Encoding ISO-8859-1 (bytes, con y sin BOM, y `str` por tolerancia).
  - Tope de 60 líneas de detalle (XSD del SII).
  - QtyItem OPCIONAL: líneas sin cantidad se detectan y bloquean el confirmar.
  - Cuadratura Σ MontoItem vs totales declarados.
  - Detección de duplicado por (RUT emisor, tipo, folio).
  - Cascada de matching: equivalencia → SKU → nombre → sin match.
  - Que la equivalencia SE APRENDE al confirmar.
  - Que confirmar NO mueve stock.

Ejecutar (BD de test, NUNCA la de producción):
    DATABASE_URL="sqlite:////tmp/t_xmldte.sqlite3" \
        python manage.py test app.tests.test_import_xml_dte
"""
import json
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from app.models import (
    Dte, Dte_Productos, ModuloSistema, OpcionMenu, PermisoRol, Producto_Talla,
    ProveedorProductoEquivalencia,
)
from app.services.dte_xml_parser import (
    MAX_DETALLE_POR_DOCUMENTO, XmlDteError, calcular_cuadratura,
    parsear_xml_dte, parsear_xml_envio, validar_dte_parseado,
)
from app.tests.factories import (
    crear_empresa, crear_producto_con_talla, crear_sucursal, crear_usuario,
)
from app.views_modulo_compras_xml import (
    CONFIANZA_ALTA, CONFIANZA_MEDIA, SIN_MATCH, buscar_dtes_duplicados,
    matchear_linea,
)


# =====================================================================
# FIXTURES XML
# =====================================================================
# Todos se declaran como `str` y se codifican a ISO-8859-1 al usarlos, que es
# como llegan de verdad: el SII exige `encoding="ISO-8859-1"` en el prólogo.
# Llevan tildes y Ñ a propósito para que el test falle si alguien "arregla" el
# encoding decodificando en UTF-8.

XML_FACTURA_NORMAL = """<?xml version="1.0" encoding="ISO-8859-1"?>
<EnvioDTE xmlns="http://www.sii.cl/SiiDte" version="1.0">
  <SetDTE ID="SetDoc">
    <Caratula version="1.0">
      <RutEmisor>76543210-K</RutEmisor>
      <RutReceptor>77000000-1</RutReceptor>
    </Caratula>
    <DTE version="1.0">
      <Documento ID="F1001T33">
        <Encabezado>
          <IdDoc>
            <TipoDTE>33</TipoDTE>
            <Folio>1001</Folio>
            <FchEmis>2026-07-15</FchEmis>
            <FmaPago>2</FmaPago>
            <FchVenc>2026-08-14</FchVenc>
          </IdDoc>
          <Emisor>
            <RUTEmisor>76543210-K</RUTEmisor>
            <RznSoc>DISTRIBUIDORA \xd1U\xd1OA S.A.</RznSoc>
            <GiroEmis>Venta al por mayor de calzado</GiroEmis>
            <Acteco>464902</Acteco>
            <DirOrigen>Av. Irarr\xe1zaval 1234</DirOrigen>
            <CmnaOrigen>\xd1u\xf1oa</CmnaOrigen>
          </Emisor>
          <Receptor>
            <RUTRecep>77000000-1</RUTRecep>
            <RznSocRecep>RETAIL PRUEBA SPA</RznSocRecep>
            <GiroRecep>Venta al por menor</GiroRecep>
          </Receptor>
          <Totales>
            <MntNeto>190000</MntNeto>
            <TasaIVA>19</TasaIVA>
            <IVA>36100</IVA>
            <MntTotal>226100</MntTotal>
          </Totales>
        </Encabezado>
        <Detalle>
          <NroLinDet>1</NroLinDet>
          <CdgItem>
            <TpoCodigo>INT1</TpoCodigo>
            <VlrCodigo>PROV-001</VlrCodigo>
          </CdgItem>
          <CdgItem>
            <TpoCodigo>EAN</TpoCodigo>
            <VlrCodigo>7801234567890</VlrCodigo>
          </CdgItem>
          <NmbItem>ZAPATILLA RUNNING</NmbItem>
          <DscItem>Modelo temporada oto\xf1o</DscItem>
          <QtyItem>10</QtyItem>
          <UnmdItem>UN</UnmdItem>
          <PrcItem>15000</PrcItem>
          <MontoItem>150000</MontoItem>
        </Detalle>
        <Detalle>
          <NroLinDet>2</NroLinDet>
          <CdgItem>
            <TpoCodigo>SKU</TpoCodigo>
            <VlrCodigo>2000001</VlrCodigo>
          </CdgItem>
          <NmbItem>POLERA ALGOD\xd3N</NmbItem>
          <QtyItem>5</QtyItem>
          <PrcItem>8000</PrcItem>
          <MontoItem>40000</MontoItem>
        </Detalle>
        <Referencia>
          <NroLinRef>1</NroLinRef>
          <TpoDocRef>801</TpoDocRef>
          <FolioRef>OC-5566</FolioRef>
          <FchRef>2026-07-01</FchRef>
          <RazonRef>Orden de compra</RazonRef>
        </Referencia>
      </Documento>
    </DTE>
  </SetDTE>
</EnvioDTE>
"""

# Segundo fixture: factura SIN QtyItem ni PrcItem (perfectamente válida para el
# SII) y sin ningún CdgItem en la segunda línea.
XML_FACTURA_SIN_CANTIDAD = """<?xml version="1.0" encoding="ISO-8859-1"?>
<EnvioDTE xmlns="http://www.sii.cl/SiiDte" version="1.0">
  <SetDTE>
    <DTE version="1.0">
      <Documento ID="F2002T33">
        <Encabezado>
          <IdDoc>
            <TipoDTE>33</TipoDTE>
            <Folio>2002</Folio>
            <FchEmis>2026-07-20</FchEmis>
          </IdDoc>
          <Emisor>
            <RUTEmisor>76543210-K</RUTEmisor>
            <RznSoc>DISTRIBUIDORA \xd1U\xd1OA S.A.</RznSoc>
            <GiroEmis>Venta al por mayor</GiroEmis>
          </Emisor>
          <Receptor>
            <RUTRecep>77000000-1</RUTRecep>
            <RznSocRecep>RETAIL PRUEBA SPA</RznSocRecep>
          </Receptor>
          <Totales>
            <MntNeto>80000</MntNeto>
            <TasaIVA>19</TasaIVA>
            <IVA>15200</IVA>
            <MntTotal>95200</MntTotal>
          </Totales>
        </Encabezado>
        <Detalle>
          <NroLinDet>1</NroLinDet>
          <CdgItem>
            <TpoCodigo>INT1</TpoCodigo>
            <VlrCodigo>FLETE</VlrCodigo>
          </CdgItem>
          <NmbItem>SERVICIO DE FLETE</NmbItem>
          <MontoItem>50000</MontoItem>
        </Detalle>
        <Detalle>
          <NroLinDet>2</NroLinDet>
          <NmbItem>CAJA DE CART\xd3N</NmbItem>
          <MontoItem>30000</MontoItem>
        </Detalle>
      </Documento>
    </DTE>
  </SetDTE>
</EnvioDTE>
"""


def _bytes(xml_str):
    """Como llega el archivo real: BYTES en ISO-8859-1."""
    return xml_str.encode('iso-8859-1')


def _xml_con_n_lineas(n, folio=3003):
    """Genera un XML con `n` líneas de detalle (para probar el tope de 60)."""
    lineas = []
    for i in range(1, n + 1):
        lineas.append(
            f'<Detalle><NroLinDet>{i}</NroLinDet>'
            f'<NmbItem>ITEM {i}</NmbItem><QtyItem>1</QtyItem>'
            f'<PrcItem>1000</PrcItem><MontoItem>1000</MontoItem></Detalle>'
        )
    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        '<EnvioDTE xmlns="http://www.sii.cl/SiiDte"><SetDTE><DTE><Documento>'
        '<Encabezado>'
        f'<IdDoc><TipoDTE>33</TipoDTE><Folio>{folio}</Folio>'
        '<FchEmis>2026-07-25</FchEmis></IdDoc>'
        '<Emisor><RUTEmisor>76543210-K</RUTEmisor><RznSoc>PROVEEDOR</RznSoc></Emisor>'
        '<Receptor><RUTRecep>77000000-1</RUTRecep></Receptor>'
        f'<Totales><MntNeto>{n * 1000}</MntNeto><TasaIVA>19</TasaIVA>'
        f'<MntTotal>{int(n * 1000 * 1.19)}</MntTotal></Totales>'
        '</Encabezado>'
        + ''.join(lineas) +
        '</Documento></DTE></SetDTE></EnvioDTE>'
    )


# =====================================================================
# PARSER
# =====================================================================

class TestParserXmlDte(TestCase):

    def test_shape_compatible_con_parsear_txt_acepta(self):
        datos = parsear_xml_dte(_bytes(XML_FACTURA_NORMAL))
        for clave in ('documento', 'emisor', 'receptor', 'transporte',
                      'totales', 'detalle', 'descuentos_recargos',
                      'referencias', 'observaciones'):
            self.assertIn(clave, datos, f'Falta la clave «{clave}» del shape del TXT')

    def test_encabezado(self):
        d = parsear_xml_dte(_bytes(XML_FACTURA_NORMAL))
        self.assertEqual(d['documento']['tipo_documento'], 33)
        self.assertEqual(d['documento']['folio'], 1001)
        self.assertEqual(d['documento']['fecha_emision'], '2026-07-15')
        self.assertEqual(d['documento']['fecha_emision_date'].isoformat(), '2026-07-15')
        self.assertEqual(d['documento']['tipo_documento_sistema'], 'FACTURA ELECTRONICA')
        self.assertEqual(d['emisor']['rut'], '76543210-K')
        self.assertEqual(d['receptor']['rut'], '77000000-1')
        self.assertEqual(d['totales']['monto_neto'], 190000)
        self.assertEqual(d['totales']['iva'], 36100)
        self.assertEqual(d['totales']['monto_total'], 226100)
        self.assertEqual(d['totales']['tasa_iva'], Decimal('19'))

    def test_encoding_iso_8859_1_preserva_enes_y_tildes(self):
        """El bug clásico: pasar str y/o decodificar en UTF-8 rompe los acentos."""
        d = parsear_xml_dte(_bytes(XML_FACTURA_NORMAL))
        self.assertEqual(d['emisor']['razon_social'], 'DISTRIBUIDORA ÑUÑOA S.A.')
        self.assertEqual(d['emisor']['comuna'], 'Ñuñoa')
        self.assertEqual(d['detalle'][1]['nombre'], 'POLERA ALGODÓN')
        self.assertEqual(d['detalle'][0]['descripcion'], 'Modelo temporada otoño')

    def test_encoding_con_bom_utf8_delante_de_declaracion_iso(self):
        """BOM UTF-8 + declaración ISO-8859-1 hace fallar a expat si no se limpia."""
        crudo = b'\xef\xbb\xbf' + _bytes(XML_FACTURA_NORMAL)
        d = parsear_xml_dte(crudo)
        self.assertEqual(d['documento']['folio'], 1001)

    def test_tolera_str_aunque_traiga_declaracion_de_encoding(self):
        """Pasar `str` es incorrecto, pero no debe reventar."""
        d = parsear_xml_dte(XML_FACTURA_NORMAL)
        self.assertEqual(d['documento']['folio'], 1001)

    def test_tolera_basura_antes_del_prologo(self):
        crudo = b'\r\n\r\n   ' + _bytes(XML_FACTURA_NORMAL)
        self.assertEqual(parsear_xml_dte(crudo)['documento']['folio'], 1001)

    def test_tolera_xml_sin_namespace(self):
        sin_ns = XML_FACTURA_NORMAL.replace(
            ' xmlns="http://www.sii.cl/SiiDte"', '')
        self.assertEqual(parsear_xml_dte(_bytes(sin_ns))['documento']['folio'], 1001)

    def test_tolera_sobre_de_intercambio_que_envuelve_el_enviodte(self):
        envuelto = (
            '<?xml version="1.0" encoding="ISO-8859-1"?>\n'
            '<SobreProveedor><Payload>\n'
            + XML_FACTURA_NORMAL.split('?>', 1)[1] +
            '\n</Payload></SobreProveedor>'
        )
        d = parsear_xml_dte(_bytes(envuelto))
        self.assertEqual(d['documento']['folio'], 1001)

    def test_detalle_y_codigos(self):
        d = parsear_xml_dte(_bytes(XML_FACTURA_NORMAL))
        self.assertEqual(len(d['detalle']), 2)

        l1 = d['detalle'][0]
        self.assertEqual(l1['nro_linea'], 1)
        self.assertEqual(l1['nombre'], 'ZAPATILLA RUNNING')
        self.assertEqual(l1['cantidad'], Decimal('10'))
        self.assertEqual(l1['precio_unitario'], Decimal('15000'))
        self.assertEqual(l1['monto_item'], 150000)
        self.assertEqual(l1['unidad'], 'UN')
        # `codigos` es la clave nueva respecto al TXT: TODOS los CdgItem.
        self.assertEqual(
            l1['codigos'],
            [{'tipo': 'INT1', 'valor': 'PROV-001'},
             {'tipo': 'EAN', 'valor': '7801234567890'}],
        )
        # `codigo` (singular) mantiene el shape del TXT: el primero.
        self.assertEqual(l1['codigo'], 'PROV-001')

    def test_montos_son_decimal_no_float(self):
        d = parsear_xml_dte(_bytes(XML_FACTURA_NORMAL))
        self.assertIsInstance(d['detalle'][0]['cantidad'], Decimal)
        self.assertIsInstance(d['detalle'][0]['precio_unitario'], Decimal)
        self.assertNotIsInstance(d['detalle'][0]['cantidad'], float)

    def test_referencias(self):
        d = parsear_xml_dte(_bytes(XML_FACTURA_NORMAL))
        self.assertEqual(len(d['referencias']), 1)
        ref = d['referencias'][0]
        self.assertEqual(ref['tipo_documento'], '801')
        self.assertEqual(ref['folio'], 'OC-5566')
        self.assertEqual(ref['razon_ref'], 'Orden de compra')

    def test_qty_y_prc_opcionales_quedan_en_none_no_en_cero(self):
        """None significa 'no informado'. Cero significaría 'cero unidades'."""
        d = parsear_xml_dte(_bytes(XML_FACTURA_SIN_CANTIDAD))
        self.assertEqual(len(d['detalle']), 2)
        for linea in d['detalle']:
            self.assertIsNone(linea['cantidad'])
            self.assertIsNone(linea['precio_unitario'])
            self.assertIsNotNone(linea['monto_item'])
        self.assertEqual(d['detalle'][1]['codigos'], [])
        self.assertIsNone(d['detalle'][1]['codigo'])

    def test_archivo_ilegible_lanza_error_controlado(self):
        with self.assertRaises(XmlDteError):
            parsear_xml_dte(b'esto no es xml')
        with self.assertRaises(XmlDteError):
            parsear_xml_dte(b'')

    def test_xml_valido_pero_sin_dte_lanza_error_controlado(self):
        with self.assertRaises(XmlDteError):
            parsear_xml_dte(b'<?xml version="1.0"?><hola><mundo/></hola>')

    def test_envio_con_varios_documentos(self):
        doble = XML_FACTURA_NORMAL.replace(
            '</SetDTE>',
            XML_FACTURA_SIN_CANTIDAD.split('<SetDTE>')[1].split('</SetDTE>')[0]
            + '</SetDTE>')
        documentos = parsear_xml_envio(_bytes(doble))
        self.assertEqual(len(documentos), 2)
        self.assertEqual(
            sorted(d['documento']['folio'] for d in documentos), [1001, 2002])


# =====================================================================
# VALIDACIÓN: TOPE 60 LÍNEAS, CANTIDADES, CUADRATURA
# =====================================================================

class TestValidacionXmlDte(TestCase):

    def test_60_lineas_es_valido(self):
        datos = parsear_xml_dte(_bytes(_xml_con_n_lineas(MAX_DETALLE_POR_DOCUMENTO)))
        self.assertEqual(len(datos['detalle']), 60)
        validacion = validar_dte_parseado(datos)
        self.assertTrue(validacion['ok'], validacion['errores'])

    def test_61_lineas_supera_el_tope_del_sii(self):
        datos = parsear_xml_dte(_bytes(_xml_con_n_lineas(MAX_DETALLE_POR_DOCUMENTO + 1)))
        self.assertEqual(len(datos['detalle']), 61)
        validacion = validar_dte_parseado(datos)
        self.assertFalse(validacion['ok'])
        self.assertTrue(
            any('61' in e and 'máximo' in e for e in validacion['errores']),
            validacion['errores'])

    def test_lineas_sin_cantidad_se_reportan(self):
        datos = parsear_xml_dte(_bytes(XML_FACTURA_SIN_CANTIDAD))
        validacion = validar_dte_parseado(datos)
        # No es un error del XML (QtyItem es opcional), es un aviso operativo.
        self.assertTrue(validacion['ok'])
        self.assertEqual(validacion['lineas_sin_cantidad'], [1, 2])
        self.assertTrue(any('QtyItem' in w for w in validacion['warnings']))

    def test_cuadratura_ok(self):
        datos = parsear_xml_dte(_bytes(XML_FACTURA_NORMAL))
        cuadratura = calcular_cuadratura(datos)
        self.assertTrue(cuadratura['verificable'])
        self.assertEqual(cuadratura['suma_items'], Decimal('190000'))
        self.assertEqual(cuadratura['esperado'], Decimal('190000'))
        self.assertEqual(cuadratura['diferencia'], Decimal('0'))
        self.assertTrue(cuadratura['cuadra'])

    def test_cuadratura_detecta_descuadre(self):
        roto = XML_FACTURA_NORMAL.replace(
            '<MntNeto>190000</MntNeto>', '<MntNeto>200000</MntNeto>')
        datos = parsear_xml_dte(_bytes(roto))
        cuadratura = calcular_cuadratura(datos)
        self.assertFalse(cuadratura['cuadra'])
        self.assertEqual(cuadratura['diferencia'], Decimal('-10000'))
        validacion = validar_dte_parseado(datos)
        self.assertTrue(any('Descuadre' in w for w in validacion['warnings']))

    def test_cuadratura_aplica_descuento_global(self):
        con_dr = XML_FACTURA_NORMAL.replace(
            '<Referencia>',
            '<DscRcgGlobal><NroLinDR>1</NroLinDR><TpoMov>D</TpoMov>'
            '<GlosaDR>Descuento comercial</GlosaDR><TpoValor>$</TpoValor>'
            '<ValorDR>10000</ValorDR></DscRcgGlobal><Referencia>'
        ).replace('<MntNeto>190000</MntNeto>', '<MntNeto>180000</MntNeto>')
        datos = parsear_xml_dte(_bytes(con_dr))
        self.assertEqual(len(datos['descuentos_recargos']), 1)
        cuadratura = calcular_cuadratura(datos)
        self.assertEqual(cuadratura['ajuste_global'], Decimal('-10000'))
        self.assertTrue(cuadratura['cuadra'], cuadratura)

    def test_boleta_cuadra_contra_monto_total(self):
        boleta = (XML_FACTURA_NORMAL
                  .replace('<TipoDTE>33</TipoDTE>', '<TipoDTE>39</TipoDTE>')
                  .replace('<MntTotal>226100</MntTotal>', '<MntTotal>190000</MntTotal>'))
        datos = parsear_xml_dte(_bytes(boleta))
        cuadratura = calcular_cuadratura(datos)
        self.assertEqual(cuadratura['base'], 'MntTotal')
        self.assertTrue(cuadratura['cuadra'], cuadratura)


# =====================================================================
# ENTORNO COMÚN PARA MATCHING / VISTAS
# =====================================================================

class _BaseXmlDteTest(TestCase):
    """Proveedor + empresa receptora + sucursal + usuario con permiso."""

    def setUp(self):
        # El RUT del proveedor va CON puntos a propósito: el XML lo manda sin
        # puntos y el matcheo tiene que normalizar.
        self.proveedor = crear_empresa(
            nombre='Distribuidora Ñuñoa', rut='76.543.210-K', esProveedor=True)
        self.empresa = crear_empresa(nombre='Retail Prueba', rut='77000000-1')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='CD-TEST')

        self.user = crear_usuario(rol='administrador')
        modulo = ModuloSistema.objects.create(codigo='compras', nombre='Compras')
        self.opcion = OpcionMenu.objects.create(
            modulo=modulo, codigo='gestion_dte_compras', nombre='Gestión DTE Compras')
        PermisoRol.objects.create(
            rol=self.user.rol, opcion_menu=self.opcion,
            puede_ver=True, puede_crear=True,
        )

        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session['nombreUsuario'] = 'Tester'
        session.save()

    # -- helpers -------------------------------------------------------
    def _subir(self, xml_str, nombre='factura.xml'):
        archivo = SimpleUploadedFile(
            nombre, _bytes(xml_str), content_type='text/xml')
        return self.client.post(
            reverse('analizar_xml_dte'),
            {'archivos': archivo, 'sucursal_id': self.sucursal.id},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def _primer_documento(self, xml_str=XML_FACTURA_NORMAL):
        resp = self._subir(xml_str)
        self.assertEqual(resp.status_code, 200, resp.content[:400])
        data = resp.json()
        self.assertTrue(data['success'], data)
        archivo = data['archivos'][0]
        self.assertTrue(archivo['ok'], archivo.get('error'))
        return archivo['documentos'][0]

    def _confirmar(self, doc, lineas, **extra):
        payload = {
            'token': doc['token'],
            'lineas': lineas,
            'sucursal_id': self.sucursal.id,
        }
        payload.update(extra)
        return self.client.post(
            reverse('confirmar_xml_dte'),
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )


# =====================================================================
# CASCADA DE MATCHING
# =====================================================================

class TestCascadaMatching(_BaseXmlDteTest):

    def setUp(self):
        super().setUp()
        self.datos = parsear_xml_dte(_bytes(XML_FACTURA_NORMAL))
        self.linea1 = self.datos['detalle'][0]   # códigos PROV-001 / 780123...
        self.linea2 = self.datos['detalle'][1]   # código 2000001 (numérico)

    def test_a_equivalencia_guardada_gana_y_es_alta(self):
        _, talla = crear_producto_con_talla(
            self.sucursal, articulo='ZAP-RUN', talla='42', sku=5555001)
        ProveedorProductoEquivalencia.objects.create(
            empresa_proveedor=self.proveedor,
            codigo_externo='PROV-001',
            tipo_codigo='INT1',
            descripcion_externa='ZAPATILLA RUNNING',
            producto_talla=talla,
        )
        match = matchear_linea(self.linea1, self.proveedor.id, self.sucursal.id)
        self.assertEqual(match['confianza'], CONFIANZA_ALTA)
        self.assertEqual(match['origen'], 'EQUIVALENCIA')
        self.assertEqual(match['propuesta']['producto_talla_id'], talla.id)
        self.assertEqual(match['codigo_usado'], 'PROV-001')

    def test_b_codigo_numerico_matchea_por_sku(self):
        _, talla = crear_producto_con_talla(
            self.sucursal, articulo='POLERA X', talla='M', sku=2000001)
        match = matchear_linea(self.linea2, self.proveedor.id, self.sucursal.id)
        self.assertEqual(match['confianza'], CONFIANZA_ALTA)
        self.assertEqual(match['origen'], 'SKU')
        self.assertEqual(match['propuesta']['producto_talla_id'], talla.id)

    def test_b_sku_de_otra_sucursal_baja_a_media(self):
        otra = crear_sucursal(empresa=self.empresa, alias='OTRA-SUC')
        crear_producto_con_talla(otra, articulo='POLERA X', talla='M', sku=2000001)
        match = matchear_linea(self.linea2, self.proveedor.id, self.sucursal.id)
        self.assertEqual(match['confianza'], CONFIANZA_MEDIA)
        self.assertEqual(match['origen'], 'SKU')
        self.assertIn('OTRA sucursal', match['motivo'])

    def test_c_nombre_normalizado_matchea_pero_nunca_es_alta(self):
        # El código de la línea 1 no existe como SKU ni como equivalencia, así
        # que solo queda el nombre. El artículo se escribe con otra caja y
        # espacios de más: `normalizar_articulo` debe absorberlo.
        _, talla = crear_producto_con_talla(
            self.sucursal, articulo='  zapatilla   running ', talla='40',
            sku=9000001)
        match = matchear_linea(self.linea1, self.proveedor.id, self.sucursal.id)
        self.assertEqual(match['confianza'], CONFIANZA_MEDIA)
        self.assertEqual(match['origen'], 'NOMBRE')
        self.assertEqual(match['propuesta']['producto_talla_id'], talla.id)

    def test_c_producto_con_varias_tallas_no_propone_pero_ofrece_candidatos(self):
        producto, _ = crear_producto_con_talla(
            self.sucursal, articulo='ZAPATILLA RUNNING', talla='40', sku=9000002)
        Producto_Talla.objects.create(
            producto=producto, sku=9000003, stock=3, talla='41')
        match = matchear_linea(self.linea1, self.proveedor.id, self.sucursal.id)
        self.assertEqual(match['confianza'], CONFIANZA_MEDIA)
        self.assertIsNone(match['propuesta'])
        self.assertEqual(len(match['candidatos']), 2)

    def test_d_sin_coincidencias_queda_en_cola_manual(self):
        match = matchear_linea(self.linea1, self.proveedor.id, self.sucursal.id)
        self.assertEqual(match['confianza'], SIN_MATCH)
        self.assertIsNone(match['propuesta'])
        self.assertIsNone(match['origen'])

    def test_la_equivalencia_manda_sobre_el_sku(self):
        """Si el usuario ya dijo que ese código es OTRO producto, gana él."""
        _, por_sku = crear_producto_con_talla(
            self.sucursal, articulo='POLERA SKU', talla='M', sku=2000001)
        _, elegido = crear_producto_con_talla(
            self.sucursal, articulo='POLERA REAL', talla='L', sku=8888001)
        ProveedorProductoEquivalencia.objects.create(
            empresa_proveedor=self.proveedor,
            codigo_externo='2000001',
            producto_talla=elegido,
        )
        match = matchear_linea(self.linea2, self.proveedor.id, self.sucursal.id)
        self.assertEqual(match['origen'], 'EQUIVALENCIA')
        self.assertEqual(match['propuesta']['producto_talla_id'], elegido.id)
        self.assertNotEqual(match['propuesta']['producto_talla_id'], por_sku.id)


# =====================================================================
# DUPLICADOS
# =====================================================================

class TestDuplicados(_BaseXmlDteTest):

    def _crear_dte_existente(self, folio=1001, tipo='FACTURA ELECTRONICA'):
        return Dte.objects.create(
            emisor=self.proveedor,
            receptor=self.empresa,
            numero_documento=folio,
            tipo_documento=tipo,
            monto_con_iva=226100,
            monto_neto=190000,
            estado_pago='PENDIENTE',
            estado_dte='EMITIDO',
            responsable='Tester',
            fecha_emision='2026-07-15',
            fecha_vencimiento='2026-08-14',
            diasCredito=30,
            bultos=0,
            unidades_productos=15,
            tipo_transaccion='COMPRA',
        )

    def test_buscar_duplicados_normaliza_el_rut(self):
        """El XML trae '76543210-K'; la ficha tiene '76.543.210-K'."""
        dte = self._crear_dte_existente()
        encontrados = buscar_dtes_duplicados(
            '76543210-K', 'FACTURA ELECTRONICA', 1001)
        self.assertEqual([d['dte_id'] for d in encontrados], [dte.id])

    def test_sin_duplicado_si_cambia_el_folio(self):
        self._crear_dte_existente(folio=999)
        self.assertEqual(
            buscar_dtes_duplicados('76543210-K', 'FACTURA ELECTRONICA', 1001), [])

    def test_sin_duplicado_si_cambia_el_tipo(self):
        self._crear_dte_existente(tipo='NOTA DE CREDITO')
        self.assertEqual(
            buscar_dtes_duplicados('76543210-K', 'FACTURA ELECTRONICA', 1001), [])

    def test_dte_descartado_no_cuenta_como_duplicado(self):
        dte = self._crear_dte_existente()
        dte.descartado = True
        dte.save(update_fields=['descartado'])
        self.assertEqual(
            buscar_dtes_duplicados('76543210-K', 'FACTURA ELECTRONICA', 1001), [])

    def test_analizar_marca_el_documento_como_no_confirmable(self):
        self._crear_dte_existente()
        doc = self._primer_documento()
        self.assertTrue(doc['duplicados'])
        self.assertFalse(doc['puede_confirmar'])
        self.assertTrue(any('DUPLICADO' in b.upper() or 'Ya existe' in b
                            for b in doc['bloqueos']), doc['bloqueos'])

    def test_confirmar_rechaza_el_duplicado(self):
        doc = self._primer_documento()
        self._crear_dte_existente()          # aparece DESPUÉS de analizar
        resp = self._confirmar(doc, [
            {'nro_linea': 1, 'cantidad': '10'},
            {'nro_linea': 2, 'cantidad': '5'},
        ])
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Ya existe', resp.json()['error'])


# =====================================================================
# ANÁLISIS (vista) — scoping, cuadratura, cantidades
# =====================================================================

class TestAnalizarXmlDte(_BaseXmlDteTest):

    def test_la_pantalla_renderiza(self):
        """Smoke test del template (pilla errores de sintaxis Django)."""
        resp = self.client.get(reverse('ver_importar_xml_dte'))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(
            resp, 'vistas/modulo_compras/importacion_xml_dte.html')
        self.assertContains(resp, 'No se mueve stock')

    def test_documento_limpio_es_confirmable(self):
        doc = self._primer_documento()
        self.assertTrue(doc['puede_confirmar'], doc['bloqueos'])
        self.assertTrue(doc['proveedor']['encontrado'])
        self.assertEqual(doc['proveedor']['id'], self.proveedor.id)
        self.assertTrue(doc['empresa_receptora']['reconocida'])
        self.assertEqual(doc['empresa_receptora']['id'], self.empresa.id)
        self.assertTrue(doc['validacion']['cuadratura']['cuadra'])
        self.assertEqual(len(doc['detalle']), 2)
        self.assertEqual(doc['resumen_match']['SIN_MATCH'], 2)

    def test_proveedor_inexistente_bloquea_pero_no_revienta(self):
        self.proveedor.delete()
        doc = self._primer_documento()
        self.assertFalse(doc['proveedor']['encontrado'])
        self.assertFalse(doc['puede_confirmar'])
        self.assertTrue(any('no existe en el sistema' in b for b in doc['bloqueos']))

    def test_receptor_ajeno_avisa_sin_reventar(self):
        """Scoping: el RUTRecep del XML no es ninguna empresa del usuario."""
        self.empresa.rut = '99.999.999-9'
        self.empresa.save(update_fields=['rut'])
        doc = self._primer_documento()
        self.assertEqual(doc['empresa_receptora']['reconocida'], False)
        self.assertFalse(doc['puede_confirmar'])
        self.assertTrue(any('no corresponde a ninguna de tus empresas' in b
                            for b in doc['bloqueos']), doc['bloqueos'])

    def test_lineas_sin_qty_llegan_con_cantidad_nula(self):
        doc = self._primer_documento(XML_FACTURA_SIN_CANTIDAD)
        self.assertEqual(doc['validacion']['lineas_sin_cantidad'], [1, 2])
        for linea in doc['detalle']:
            self.assertIsNone(linea['cantidad'])

    def test_archivo_no_xml_no_tumba_el_lote(self):
        malo = SimpleUploadedFile('malo.xml', b'no soy xml', content_type='text/xml')
        bueno = SimpleUploadedFile(
            'bueno.xml', _bytes(XML_FACTURA_NORMAL), content_type='text/xml')
        resp = self.client.post(
            reverse('analizar_xml_dte'),
            {'archivos': [malo, bueno], 'sucursal_id': self.sucursal.id},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(len(data['archivos']), 2)
        self.assertFalse(data['archivos'][0]['ok'])
        self.assertTrue(data['archivos'][1]['ok'])

    def test_sin_permiso_puede_crear_devuelve_403(self):
        PermisoRol.objects.filter(opcion_menu=self.opcion).update(puede_crear=False)
        resp = self._subir(XML_FACTURA_NORMAL)
        self.assertEqual(resp.status_code, 403)


# =====================================================================
# CONFIRMACIÓN: crea el DTE, APRENDE la equivalencia y NO mueve stock
# =====================================================================

class TestConfirmarXmlDte(_BaseXmlDteTest):

    def setUp(self):
        super().setUp()
        _, self.talla_zap = crear_producto_con_talla(
            self.sucursal, articulo='ZAP-RUN-001', talla='42', sku=7000001,
            stock=25)
        _, self.talla_pol = crear_producto_con_talla(
            self.sucursal, articulo='POL-ALG-001', talla='M', sku=7000002,
            stock=40)

    def test_crea_dte_y_lineas_sin_mover_stock(self):
        doc = self._primer_documento()
        stock_zap = Producto_Talla.objects.get(pk=self.talla_zap.pk).stock
        stock_pol = Producto_Talla.objects.get(pk=self.talla_pol.pk).stock

        resp = self._confirmar(doc, [
            {'nro_linea': 1, 'cantidad': '10',
             'producto_talla_id': self.talla_zap.id, 'guardar_equivalencia': True},
            {'nro_linea': 2, 'cantidad': '5',
             'producto_talla_id': self.talla_pol.id, 'guardar_equivalencia': True},
        ])
        self.assertEqual(resp.status_code, 200, resp.content[:400])
        data = resp.json()
        self.assertTrue(data['success'], data)
        self.assertFalse(data['stock_movido'])

        dte = Dte.objects.get(pk=data['dte_id'])
        self.assertEqual(dte.tipo_transaccion, 'COMPRA')
        self.assertTrue(dte.es_manual)
        self.assertFalse(dte.es_por_concepto)
        self.assertEqual(dte.emisor_id, self.proveedor.id)
        self.assertEqual(dte.receptor_id, self.empresa.id)
        self.assertEqual(dte.numero_documento, 1001)
        self.assertEqual(dte.tipo_documento, 'FACTURA ELECTRONICA')
        self.assertEqual(int(dte.monto_con_iva), 226100)
        self.assertEqual(int(dte.monto_neto), 190000)
        self.assertEqual(dte.unidades_productos, 15)
        self.assertEqual(dte.diasCredito, 30)
        self.assertEqual(dte.sucursal_id, self.sucursal.id)

        lineas = list(Dte_Productos.objects.filter(dte=dte).order_by('id'))
        self.assertEqual(len(lineas), 2)
        self.assertEqual(lineas[0].productoTalla_id, self.talla_zap.id)
        self.assertEqual(lineas[0].stock, 10)
        self.assertEqual(lineas[0].precio, 15000)
        self.assertEqual(lineas[0].monto_item, 150000)
        self.assertIn('ZAPATILLA RUNNING', lineas[0].descripcion)

        # LO IMPORTANTE: el stock no se tocó.
        self.assertEqual(
            Producto_Talla.objects.get(pk=self.talla_zap.pk).stock, stock_zap)
        self.assertEqual(
            Producto_Talla.objects.get(pk=self.talla_pol.pk).stock, stock_pol)

    def test_aprende_la_equivalencia_de_cada_linea_resuelta(self):
        doc = self._primer_documento()
        resp = self._confirmar(doc, [
            {'nro_linea': 1, 'cantidad': '10',
             'producto_talla_id': self.talla_zap.id, 'guardar_equivalencia': True},
            {'nro_linea': 2, 'cantidad': '5',
             'producto_talla_id': self.talla_pol.id, 'guardar_equivalencia': True},
        ])
        self.assertEqual(resp.status_code, 200, resp.content[:400])

        # Línea 1 traía DOS CdgItem → dos equivalencias; la línea 2, uno.
        equivalencias = ProveedorProductoEquivalencia.objects.filter(
            empresa_proveedor=self.proveedor)
        self.assertEqual(equivalencias.count(), 3)
        self.assertEqual(
            set(equivalencias.values_list('codigo_externo', flat=True)),
            {'PROV-001', '7801234567890', '2000001'},
        )
        eq = equivalencias.get(codigo_externo='PROV-001')
        self.assertEqual(eq.producto_talla_id, self.talla_zap.id)
        self.assertEqual(eq.tipo_codigo, 'INT1')
        self.assertEqual(eq.descripcion_externa, 'ZAPATILLA RUNNING')
        self.assertEqual(eq.creado_por_id, self.user.id)
        self.assertEqual(eq.veces_usada, 0)

    def test_la_segunda_factura_del_proveedor_entra_sola(self):
        """El punto de todo el módulo: aprender una vez, automatizar después."""
        doc = self._primer_documento()
        self._confirmar(doc, [
            {'nro_linea': 1, 'cantidad': '10',
             'producto_talla_id': self.talla_zap.id, 'guardar_equivalencia': True},
            {'nro_linea': 2, 'cantidad': '5',
             'producto_talla_id': self.talla_pol.id, 'guardar_equivalencia': True},
        ])

        # Misma factura, otro folio: ahora las dos líneas salen en verde.
        otra = XML_FACTURA_NORMAL.replace('<Folio>1001</Folio>', '<Folio>1002</Folio>')
        doc2 = self._primer_documento(otra)
        self.assertEqual(doc2['resumen_match']['ALTA'], 2)
        self.assertEqual(doc2['resumen_match']['SIN_MATCH'], 0)
        self.assertEqual(
            doc2['detalle'][0]['match']['origen'], 'EQUIVALENCIA')
        self.assertEqual(
            doc2['detalle'][0]['match']['propuesta']['producto_talla_id'],
            self.talla_zap.id)

    def test_veces_usada_se_incrementa_al_reconfirmar_el_mismo_codigo(self):
        doc = self._primer_documento()
        self._confirmar(doc, [
            {'nro_linea': 1, 'cantidad': '10',
             'producto_talla_id': self.talla_zap.id, 'guardar_equivalencia': True},
            {'nro_linea': 2, 'cantidad': '5',
             'producto_talla_id': self.talla_pol.id, 'guardar_equivalencia': True},
        ])
        otra = XML_FACTURA_NORMAL.replace('<Folio>1001</Folio>', '<Folio>1003</Folio>')
        doc2 = self._primer_documento(otra)
        self._confirmar(doc2, [
            {'nro_linea': 1, 'cantidad': '10',
             'producto_talla_id': self.talla_zap.id, 'guardar_equivalencia': True},
            {'nro_linea': 2, 'cantidad': '5',
             'producto_talla_id': self.talla_pol.id, 'guardar_equivalencia': True},
        ])
        eq = ProveedorProductoEquivalencia.objects.get(
            empresa_proveedor=self.proveedor, codigo_externo='PROV-001')
        self.assertEqual(eq.veces_usada, 1)
        self.assertEqual(
            ProveedorProductoEquivalencia.objects
            .filter(empresa_proveedor=self.proveedor).count(), 3)

    def test_linea_sin_cantidad_no_puede_confirmarse(self):
        doc = self._primer_documento(XML_FACTURA_SIN_CANTIDAD)
        resp = self._confirmar(doc, [
            {'nro_linea': 1, 'cantidad': '2'},
            {'nro_linea': 2},                     # el usuario no la completó
        ])
        self.assertEqual(resp.status_code, 400)
        self.assertIn('falta la cantidad', resp.json()['error'].lower())
        self.assertEqual(Dte.objects.filter(numero_documento=2002).count(), 0)

    def test_cantidad_completada_a_mano_se_usa(self):
        doc = self._primer_documento(XML_FACTURA_SIN_CANTIDAD)
        resp = self._confirmar(doc, [
            {'nro_linea': 1, 'cantidad': '2'},
            {'nro_linea': 2, 'cantidad': '3'},
        ])
        self.assertEqual(resp.status_code, 200, resp.content[:400])
        dte = Dte.objects.get(pk=resp.json()['dte_id'])
        self.assertEqual(dte.unidades_productos, 5)
        lineas = list(Dte_Productos.objects.filter(dte=dte).order_by('id'))
        self.assertEqual([l.stock for l in lineas], [2, 3])
        # PrcItem tampoco venía: el unitario se deriva de MontoItem / cantidad.
        self.assertEqual(lineas[0].precio, 25000)   # 50000 / 2

    def test_linea_sin_producto_queda_como_solo_texto_y_no_aprende(self):
        doc = self._primer_documento()
        resp = self._confirmar(doc, [
            {'nro_linea': 1, 'cantidad': '10',
             'producto_talla_id': self.talla_zap.id, 'guardar_equivalencia': True},
            {'nro_linea': 2, 'cantidad': '5'},       # sin producto
        ])
        self.assertEqual(resp.status_code, 200, resp.content[:400])
        data = resp.json()
        self.assertEqual(data['lineas_creadas'], 2)
        self.assertEqual(data['lineas_con_producto'], 1)
        self.assertFalse(
            ProveedorProductoEquivalencia.objects
            .filter(codigo_externo='2000001').exists())

    def test_token_alterado_es_rechazado(self):
        doc = self._primer_documento()
        resp = self.client.post(
            reverse('confirmar_xml_dte'),
            data=json.dumps({'token': doc['token'] + 'x', 'lineas': []}),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('alterado', resp.json()['error'])
        self.assertEqual(Dte.objects.filter(numero_documento=1001).count(), 0)

    def test_descuadre_bloquea_salvo_que_se_acepte_explicitamente(self):
        roto = XML_FACTURA_NORMAL.replace(
            '<MntNeto>190000</MntNeto>', '<MntNeto>200000</MntNeto>')
        doc = self._primer_documento(roto)
        lineas = [
            {'nro_linea': 1, 'cantidad': '10',
             'producto_talla_id': self.talla_zap.id},
            {'nro_linea': 2, 'cantidad': '5',
             'producto_talla_id': self.talla_pol.id},
        ]
        resp = self._confirmar(doc, lineas)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('no cuadra', resp.json()['error'])

        resp_ok = self._confirmar(doc, lineas, aceptar_descuadre=True)
        self.assertEqual(resp_ok.status_code, 200, resp_ok.content[:400])
