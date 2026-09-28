"""
R2F2 — «Gestión Documentos Compras» (gestionDteCompras.html), ronda 2.

La pantalla pasó a consumir los contratos nuevos del backend. Aquí se cubre:

1. Ganchos en la fuente de la plantilla: documento base en edición con el id
   del DTE (B5-11), sin rama ND contra un endpoint inexistente (B3-09/B16-04),
   checkbox «Compra por concepto» (B14-08), confirmación del doble pago (B3-07),
   rótulos de compensación en la vista previa del comprobante (B3-14),
   confirmación del reuso de una factura emitida manual (B5-08), escapes del
   modal de proveedores (B11-07) y lectura de ?buscar= al cargar.
2. Helpers de JS ejecutados en node (escape/resaltado, mensaje de error del
   servidor, rótulos de la vista previa).
3. Contratos del servidor que el JS usa: /app/obtenerDTE/ (proveedor de la
   factura), /app/obtener_ncs_disponibles/?proveedor=, /app/cargarDteCompra/
   con la búsqueda que arma «Enlazar a Factura», /app/asociar_nc_existente/,
   /app/registrarPagoDTE/ (409 duplicado), /app/datos_envio_comprobante/
   (hay_compensaciones), /app/asociar_documento_emitido_compensacion/
   (confirmar_reuso / monto_total_emitida) y /app/crearDteCompras/ (por_concepto).

Ejecutar (BD de test aislada):
    python manage.py test app.tests.test_r2f2_gestion_dte_front --keepdb
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta
from unittest import skipUnless

from django.template.loader import get_template
from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import Dte, Dte_Detalle_Pago
from .factories import crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario, otorgar_ver_pantalla


STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'
PLANTILLA = 'vistas/modulo_compras/gestionDteCompras.html'
NODE = shutil.which('node')


def _fuente():
    return get_template(PLANTILLA).template.source


def _funcion_js(fuente, nombre):
    """Extrae `function nombre(...) {...}` de la fuente contando llaves (las
    funciones que se prueban tienen llaves balanceadas también en regex y
    template literals)."""
    inicio = fuente.index(f'function {nombre}(')
    abre = fuente.index('{', inicio)
    nivel = 0
    for i in range(abre, len(fuente)):
        if fuente[i] == '{':
            nivel += 1
        elif fuente[i] == '}':
            nivel -= 1
            if nivel == 0:
                return fuente[inicio:i + 1]
    raise AssertionError(f'No se pudo extraer {nombre}')


def _cuerpo_handler(fuente, marcador, largo=1500):
    inicio = fuente.index(marcador)
    return fuente[inicio:inicio + largo]


# ---------------------------------------------------------------------------
# 1. Fuente de la plantilla
# ---------------------------------------------------------------------------

class PlantillaGanchosR2F2Test(TestCase):

    def test_documento_base_en_edicion_usa_el_id_del_dte(self):
        """B5-11: el cambio de tipo ya no pide los documentos base sin dte_id
        (esa respuesta excluía la guía actual y la desanexaba al guardar)."""
        fuente = _fuente()
        handler = _cuerpo_handler(fuente, "$('#tipoDocumento').on('change'")
        self.assertIn("cargarDocumentosBase($('#modalNuevoDTE').data('dte-id'))", handler)
        self.assertNotIn('cargarDocumentosBase();', fuente)

    def test_sin_rama_nota_de_debito(self):
        """B3-09/B16-04: la ND se guarda como cabecera; nada llama al endpoint
        inexistente ni exige una NC base."""
        fuente = _fuente()
        self.assertNotIn("url: '/app/obtener_ncs_para_nd/'", fuente)
        self.assertNotIn('function cargarNotasCreditoDisponibles(', fuente)
        self.assertNotIn('notaCreditoBase', fuente)
        self.assertNotIn('nota_credito_base_id', fuente)
        self.assertNotIn('Selecciona la Nota de Crédito que corrige esta ND', fuente)

    def test_checkbox_por_concepto(self):
        """B14-08: checkbox sin marcar, visible para factura/ND, enviado como
        por_concepto; al editar solo si se conoce el valor o se tocó."""
        fuente = _fuente()
        self.assertIn('<input class="form-check-input" type="checkbox" id="porConcepto">', fuente)
        self.assertNotIn('id="porConcepto" checked', fuente)
        self.assertIn("const TIPOS_POR_CONCEPTO = ['FACTURA ELECTRONICA', 'FACTURA EXENTA', 'NOTA DE DEBITO'];", fuente)
        self.assertIn("data.por_concepto = TIPOS_POR_CONCEPTO.includes(tipoDocumento) && $porConcepto.is(':checked');", fuente)
        self.assertIn("typeof dteDataEdicion.es_por_concepto === 'boolean' || $porConcepto.data('tocado')", fuente)
        handler = _cuerpo_handler(fuente, "$('#tipoDocumento').on('change'")
        self.assertIn('togglePorConcepto(tipo);', handler)
        # En edición, el valor guardado sale de obtenerDTE o de la fila del listado.
        self.assertIn('filasDtePorId[dte.id] = dte;', fuente)
        editar = _funcion_js(fuente, 'editarDTE')
        self.assertIn('filaListado.es_por_concepto', editar)
        self.assertIn("prop('checked', dte.es_por_concepto === true)", editar)

    def test_pago_individual_confirma_el_duplicado(self):
        """B3-07: el registro declara confirmar_duplicado=false y ante 409
        {duplicado} pregunta y reenvía con true. El lote masivo mantiene su
        candado (botón deshabilitado mientras corre)."""
        fuente = _fuente()
        self.assertIn('if (!pagoId) data.confirmar_duplicado = false;', fuente)
        self.assertIn('xhr.status === 409 && res && res.duplicado === true && !payload.confirmar_duplicado', fuente)
        self.assertIn('enviarPagoIndividual(Object.assign({}, payload, { confirmar_duplicado: true }));', fuente)
        masivo = _funcion_js(fuente, 'procesarPagoMasivo')
        self.assertIn("$btn.prop('disabled', true);", masivo)
        self.assertIn('if (pagoMasivoEnCurso) return;', masivo)

    def test_anexar_nc_filtra_por_proveedor_y_enlazar_lista_facturas(self):
        """B5-07: el modal de NC se abre filtrado por el proveedor de la
        factura; «Enlazar a Factura» (desde la NC) ya no reutiliza ese modal
        con el id de la NC: lista facturas y envía {nc_id, dte_id: factura}."""
        fuente = _fuente()
        anexar = _funcion_js(fuente, 'abrirModalAnexarNCaFactura')
        self.assertIn('/app/obtenerDTE/${dteId}/', anexar)
        self.assertIn('cargarProveedoresParaFiltro(proveedorId);', anexar)
        self.assertIn('cargarNCsDisponibles(proveedorId);', anexar)
        enlazar = _funcion_js(fuente, 'enlazarNCDesdeEditar')
        self.assertNotIn('abrirModalAnexarNCaFactura', enlazar)
        self.assertIn("url: '/app/cargarDteCompra/'", enlazar)
        self.assertIn("tipo_documento: 'FACTURA ELECTRONICA'", enlazar)
        self.assertIn('asociarNCaFactura(ncId, r.value);', enlazar)
        asociar = _funcion_js(fuente, 'asociarNCaFactura')
        self.assertIn('JSON.stringify({ nc_id: ncId, dte_id: facturaId })', asociar)
        self.assertIn('mensajeErrorXhr(xhr,', asociar)

    def test_callbacks_muestran_el_motivo_del_servidor(self):
        """Asociar/desasociar NC, cotización y compensaciones: el error del
        servidor (400/404/409) se muestra en vez de «Error de conexión»."""
        fuente = _fuente()
        self.assertNotIn("Swal.fire('Error', 'Error de conexión', 'error')", fuente)
        self.assertNotIn("'Error de conexión al anexar la NC'", fuente)
        self.assertNotIn("'Error de conexión al asociar'", fuente)
        for nombre in ('desasociarNC', 'desasociarCotizacion', 'desasociarNCDesdeHub',
                       'desasociarCompensacionDesdeHub'):
            self.assertIn('mensajeErrorXhr(xhr,', _funcion_js(fuente, nombre), nombre)
        cot = _cuerpo_handler(fuente, "$('#btnAsociarCotizacion').on('click'", 2000)
        self.assertIn('mensajeErrorXhr(xhr,', cot)

    def test_vista_previa_comprobante_rotula_compensaciones(self):
        fuente = _fuente()
        preview = _funcion_js(fuente, 'renderPreviewComprobante')
        self.assertIn('p.hay_compensaciones === true', preview)
        self.assertIn('TOTAL N/C Y COMPENSACIONES', preview)
        self.assertIn('COMPENSACIÓN N°', preview)

    def test_compensar_emitida_confirma_el_reuso(self):
        """B5-08: 409 requiere_confirmacion → Swal con ya_usado/folios → reenvío
        con confirmar_reuso=true. Campo opcional con el total de la emitida."""
        fuente = _fuente()
        self.assertIn('id="emitidaManualTotal"', fuente)
        self.assertIn('payload.monto_total_emitida = totalEmitida;', fuente)
        self.assertIn('confirmar_reuso: false,', fuente)
        enviar = _funcion_js(fuente, 'enviarCompensacionEmitida')
        self.assertIn('xhr.status === 409 && res && res.requiere_confirmacion && !payload.confirmar_reuso', enviar)
        self.assertIn('{ confirmar_reuso: true }', enviar)
        self.assertIn("folios.map(f => '#' + escHtml(f))", enviar)
        # El diálogo muerto «Forzar Eliminación» ya no existe.
        self.assertNotIn("title: 'Forzar", fuente)
        self.assertNotIn('puede_forzar)', fuente)

    def test_modal_proveedores_escapa(self):
        """B11-07: giro/ciudad escapados; el resaltado escapa antes de marcar."""
        fuente = _fuente()
        self.assertNotIn("${proveedor.giro || '-'}", fuente)
        self.assertNotIn("${proveedor.ciudad || '-'}", fuente)
        self.assertIn("${escHtml(proveedor.giro || '-')}", fuente)
        self.assertIn("${escHtml(proveedor.ciudad || '-')}", fuente)
        resaltar = _funcion_js(fuente, 'resaltarBusquedaProveedores')
        self.assertIn('resaltarBusquedaDTE(texto, busqueda)', resaltar)
        self.assertNotIn("'<span class=\"search-highlight\">$1</span>'", fuente)

    def test_lee_buscar_de_la_url(self):
        fuente = _fuente()
        f = _funcion_js(fuente, 'aplicarBusquedaDesdeUrl')
        self.assertIn("params.get('buscar')", f)
        self.assertIn("$('#searchDTE').val(buscar);", f)
        self.assertIn("$('#tipoFecha').val(tipoFecha);", f)
        self.assertIn('const buscarInicial = aplicarBusquedaDesdeUrl();', fuente)
        self.assertIn("cargarDTEs(1, buscarInicial, '');", fuente)
        # El alta precargada desde el agente PDF (B15-10) sigue ahí.
        self.assertIn('abrirNuevoDteDesdeUrl();', fuente)

    def test_saldo_e_incidencias_del_servidor_si_vienen(self):
        """Si cargarDteCompra trae 'saldo', el pago masivo no consulta
        /app/obtenerDetallePago/ por factura (data-saldo-exacto=1)."""
        fuente = _fuente()
        self.assertIn("data-saldo-exacto=\"${(dte.saldo !== undefined && dte.saldo !== null) ? '1' : '0'}\"", fuente)
        masivo = _funcion_js(fuente, 'abrirModalPagoMasivo')
        self.assertIn("$(this).attr('data-saldo-exacto') !== '1'", masivo)
        self.assertIn('dte.incidencias_activas', _funcion_js(fuente, 'incidenciasActivasDte'))


# ---------------------------------------------------------------------------
# 2. Helpers de JS en node
# ---------------------------------------------------------------------------

@skipUnless(NODE, 'node no está instalado')
class HelpersJsR2F2Test(TestCase):

    def _node(self, codigo):
        fuente = _fuente()
        funciones = '\n'.join(_funcion_js(fuente, n) for n in (
            'escHtml', 'mensajeErrorXhr', 'resaltarBusquedaDTE', 'resaltarBusquedaProveedores',
            'normalizarRutComparable', 'saldoFilaDte', 'renderPreviewComprobante',
        ))
        script = (
            "const salida = {};\n"
            "function $(sel) { return { html: function (s) { salida[sel] = s; } }; }\n"
            + funciones + "\n" + codigo
        )
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
            fh.write(script)
            ruta = fh.name
        try:
            r = subprocess.run([NODE, ruta], capture_output=True, text=True, encoding='utf-8', timeout=60)
        finally:
            os.unlink(ruta)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_resaltado_de_proveedores_escapa_texto_y_termino(self):
        res = self._node(
            "console.log(JSON.stringify({"
            " a: resaltarBusquedaProveedores('<img src=x onerror=alert(1)>', ''),"
            " b: resaltarBusquedaProveedores('ACME <b>x</b>', 'acme'),"
            " c: resaltarBusquedaProveedores('A&B', '<b>'),"
            " d: resaltarBusquedaProveedores(null, 'x')"
            "}));"
        )
        self.assertEqual(res['a'], '&lt;img src=x onerror=alert(1)&gt;')
        self.assertEqual(res['b'], '<span class="search-highlight">ACME</span> &lt;b&gt;x&lt;/b&gt;')
        self.assertEqual(res['c'], 'A&amp;B')
        self.assertEqual(res['d'], '')

    def test_mensaje_error_xhr(self):
        res = self._node(
            "console.log(JSON.stringify({"
            " a: mensajeErrorXhr({status: 400, responseJSON: {error: 'La NC es de otro proveedor.'}}, 'x'),"
            " b: mensajeErrorXhr({status: 403, responseJSON: {error: true, mensaje: 'Sin permiso'}}, 'x'),"
            " c: mensajeErrorXhr({status: 500}, 'Por defecto'),"
            " d: mensajeErrorXhr({status: 0}, 'x').indexOf('No hubo respuesta') === 0"
            "}));"
        )
        self.assertEqual(res['a'], 'La NC es de otro proveedor.')
        self.assertEqual(res['b'], 'Sin permiso')
        self.assertEqual(res['c'], 'Por defecto')
        self.assertTrue(res['d'])

    def test_vista_previa_con_y_sin_compensaciones(self):
        res = self._node(
            "const base = {proveedor: 'P', filas: [{numero: '1', fecha_emision: 'f', monto: '$1',"
            " nc_numeros: '-', nc_valor: '-', cheque: '-', pago_monto: '$1', pago_fecha: '-'}],"
            " totales: {factura: '$1', nc: '$0', pago: '$1'}, empresa: {}};\n"
            "renderPreviewComprobante(Object.assign({}, base, {hay_compensaciones: true}));\n"
            "const con = salida['#envioComprobantePreviewHtml'];\n"
            "renderPreviewComprobante(Object.assign({}, base, {hay_compensaciones: false}));\n"
            "const sin = salida['#envioComprobantePreviewHtml'];\n"
            "console.log(JSON.stringify({con: con, sin: sin}));"
        )
        self.assertIn('TOTAL N/C Y COMPENSACIONES', res['con'])
        self.assertIn('COMPENSACIÓN N°', res['con'])
        self.assertNotIn('TOTAL NOTA DE CRÉDITO', res['con'])
        self.assertIn('TOTAL NOTA DE CRÉDITO', res['sin'])
        self.assertNotIn('COMPENSACI', res['sin'])

    def test_saldo_y_rut(self):
        res = self._node(
            "console.log(JSON.stringify({"
            " s1: saldoFilaDte({saldo: 1500, monto_con_iva: 9999}),"
            " s2: saldoFilaDte({monto_con_iva: 10000, notas_credito: 2000, compensaciones: 500}),"
            " r: normalizarRutComparable('077.100.000-k') === normalizarRutComparable('77100000K')"
            "}));"
        )
        self.assertEqual(res['s1'], 1500)
        self.assertEqual(res['s2'], 7500)
        self.assertTrue(res['r'])


# ---------------------------------------------------------------------------
# 3. Contratos del servidor que consume la pantalla
# ---------------------------------------------------------------------------

@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class ContratosServidorR2F2Test(TestCase):
    ROL = 'administracion'

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa(nombre='Nosotros R2F2', rut='76.210.000-2')
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='R2F2-SUC')
        cls.prov_a = crear_empresa(nombre='Proveedor A R2F2', rut='77.110.000-1', esProveedor=True)
        cls.prov_b = crear_empresa(nombre='Proveedor B R2F2', rut='77.220.000-2', esProveedor=True)
        cls.user = crear_usuario(username='r2f2_admin', rol=cls.ROL)
        crear_empresa_user(cls.user, cls.empresa, cls.sucursal)
        otorgar_ver_pantalla(cls.ROL, 'gestion_dte_compras', puede_crear=True, puede_editar=True, puede_eliminar=True)
        otorgar_ver_pantalla(cls.ROL, 'dte_compras_pagos', puede_editar=True, puede_eliminar=True)

    def setUp(self):
        self.client.force_login(self.user)
        s = self.client.session
        s['idEmpresaActual'] = self.empresa.id
        s['idSucursalActual'] = self.sucursal.id
        s.save()
        self.hoy = timezone.localdate()

    def _dte(self, numero, emisor, tipo='FACTURA ELECTRONICA', monto=100000, estado_pago='PENDIENTE'):
        return Dte.objects.create(
            emisor=emisor, receptor=self.empresa, numero_documento=numero,
            tipo_documento=tipo, monto_con_iva=monto, monto_neto=round(monto / 1.19),
            descuento=0, estado_pago=estado_pago, estado_dte='ACEPTADO', responsable='test',
            fecha_emision=self.hoy, fecha_recepcion=self.hoy,
            fecha_vencimiento=self.hoy + timedelta(days=30), diasCredito=30,
            bultos=0, unidades_productos=0, tipo_transaccion='COMPRA', sucursal=self.sucursal,
        )

    def _post(self, url, data):
        return self.client.post(url, data=json.dumps(data), content_type='application/json',
                                HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_pagina_renderiza_con_buscar(self):
        r = self.client.get('/app/verGestionDteCompras/?buscar=%3Cscript%3E')
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        self.assertIn('id="porConcepto"', html)
        self.assertNotIn('<script>alert', html)

    def test_obtener_dte_entrega_el_proveedor_de_la_factura(self):
        factura = self._dte(8101, self.prov_a)
        r = self.client.get(f'/app/obtenerDTE/{factura.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['receptor_id'], self.prov_a.id)

    def test_ncs_disponibles_filtradas_por_proveedor(self):
        nc_a = self._dte(8201, self.prov_a, tipo='NOTA DE CREDITO', monto=5000)
        self._dte(8202, self.prov_b, tipo='NOTA DE CREDITO', monto=5000)
        r = self.client.get('/app/obtener_ncs_disponibles/', {'proveedor': self.prov_a.id, 'busqueda': ''})
        self.assertEqual(r.status_code, 200, r.content)
        ncs = r.json()['ncs']
        self.assertEqual([n['id'] for n in ncs], [nc_a.id])
        self.assertEqual(ncs[0].get('proveedor_id'), self.prov_a.id)

    def test_busqueda_de_enlazar_nc_lista_facturas_del_proveedor_con_saldo(self):
        """El payload que arma enlazarNCDesdeEditar devuelve las facturas
        pendientes del proveedor (por RUT) y no las pagadas ni las de otro."""
        pendiente = self._dte(8301, self.prov_a, monto=50000)
        pagada = self._dte(8302, self.prov_a, monto=40000, estado_pago='PAGADO')
        otra = self._dte(8303, self.prov_b, monto=30000)
        r = self._post('/app/cargarDteCompra/', {
            'fecha_inicio': '2000-01-01', 'fecha_fin': self.hoy.isoformat(), 'tipo_fecha': 'emision',
            'page': 1, 'page_size': 100, 'search': self.prov_a.rut,
            'tipo_documento': 'FACTURA ELECTRONICA', 'filtro_vencimiento': 'pendientes',
            'solo_incidencias': False, 'incluir_descartados': False,
        })
        self.assertEqual(r.status_code, 200, r.content)
        items = r.json()['items']
        ids = {i['id'] for i in items}
        self.assertIn(pendiente.id, ids)
        self.assertNotIn(pagada.id, ids)
        self.assertNotIn(otra.id, ids)
        fila = next(i for i in items if i['id'] == pendiente.id)
        # Campos que usan saldoFilaDte() y el filtro por RUT del front.
        for campo in ('rut', 'numero_documento', 'fecha_emision', 'monto_con_iva',
                      'notas_credito', 'compensaciones', 'estado', 'descartado'):
            self.assertIn(campo, fila)
        self.assertEqual(fila['rut'], self.prov_a.rut)
        # Si el servidor ya entrega saldo / incidencias_activas / es_por_concepto,
        # el front los usa tal cual (saldoFilaDte, incidenciasActivasDte, editarDTE).
        if 'saldo' in fila:
            self.assertEqual(fila['saldo'], 50000)
        if 'incidencias_activas' in fila:
            self.assertEqual(fila['incidencias_activas'], 0)
        if 'es_por_concepto' in fila:
            self.assertIs(fila['es_por_concepto'], False)

    def test_asociar_nc_con_el_payload_del_front(self):
        factura = self._dte(8401, self.prov_a, monto=50000)
        nc = self._dte(8402, self.prov_a, tipo='NOTA DE CREDITO', monto=10000)
        nc_otro = self._dte(8403, self.prov_b, tipo='NOTA DE CREDITO', monto=10000)

        r = self._post('/app/asociar_nc_existente/', {'nc_id': nc_otro.id, 'dte_id': factura.id})
        self.assertLess(r.status_code, 500)
        datos = r.json()
        self.assertFalse(datos['success'])
        self.assertIsInstance(datos.get('error'), str)  # lo muestra mensajeErrorXhr / response.error

        r = self._post('/app/asociar_nc_existente/', {'nc_id': nc.id, 'dte_id': factura.id})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['success'], r.content)
        self.assertTrue(Dte_Detalle_Pago.objects.filter(dte=factura, metodo_pago='Nota de Crédito', monto=10000).exists())

    def test_registrar_pago_duplicado_pide_confirmacion(self):
        factura = self._dte(8501, self.prov_a, monto=100000)
        pago = {'dte_id': factura.id, 'metodo_pago': 'Transferencia', 'voucher': '', 'monto': 1000,
                'fecha_pago': self.hoy.isoformat(), 'confirmar_duplicado': False}
        r1 = self._post('/app/registrarPagoDTE/', pago)
        self.assertEqual(r1.status_code, 200, r1.content)
        r2 = self._post('/app/registrarPagoDTE/', pago)
        self.assertEqual(r2.status_code, 409, r2.content)
        self.assertTrue(r2.json().get('duplicado'))
        self.assertIsInstance(r2.json().get('error'), str)
        r3 = self._post('/app/registrarPagoDTE/', dict(pago, confirmar_duplicado=True))
        self.assertEqual(r3.status_code, 200, r3.content)
        self.assertEqual(Dte_Detalle_Pago.objects.filter(dte=factura).count(), 2)

    def test_vista_previa_comprobante_trae_hay_compensaciones(self):
        factura = self._dte(8601, self.prov_a, monto=50000)
        r = self.client.get(f'/app/datos_envio_comprobante/{factura.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIs(r.json()['preview']['hay_compensaciones'], False)
        Dte_Detalle_Pago.objects.create(dte=factura, metodo_pago='Compensación con Factura',
                                        voucher='8602', monto=1000, fecha_pago=self.hoy)
        r = self.client.get(f'/app/datos_envio_comprobante/{factura.id}/')
        self.assertIs(r.json()['preview']['hay_compensaciones'], True)

    def test_compensacion_emitida_manual_reuso(self):
        """El front manda confirmar_reuso=false (y el total opcional). El
        servidor puede avisar en el mensaje (200) o pedir confirmación (409
        requiere_confirmacion); con confirmar_reuso=true se registra."""
        f1 = self._dte(8701, self.prov_a, monto=50000)
        f2 = self._dte(8702, self.prov_a, monto=50000)
        base = {'modo': 'manual', 'numero': '99001', 'monto': 10000, 'fecha': '',
                'emisor_label': 'Nosotros', 'confirmar_reuso': False, 'monto_total_emitida': 30000}
        r = self._post('/app/asociar_documento_emitido_compensacion/', dict(base, dte_id=f1.id))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['success'], r.content)

        r = self._post('/app/asociar_documento_emitido_compensacion/', dict(base, dte_id=f2.id))
        self.assertIn(r.status_code, (200, 409), r.content)
        if r.status_code == 409:
            self.assertTrue(r.json().get('requiere_confirmacion'), r.content)
            r = self._post('/app/asociar_documento_emitido_compensacion/',
                           dict(base, dte_id=f2.id, confirmar_reuso=True))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['success'], r.content)

        # El total opcional se respeta: 10.000 + 10.000 + 20.000 > 30.000.
        f3 = self._dte(8703, self.prov_a, monto=50000)
        r = self._post('/app/asociar_documento_emitido_compensacion/',
                       dict(base, dte_id=f3.id, monto=20000, confirmar_reuso=True))
        self.assertEqual(r.status_code, 400, r.content)
        self.assertFalse(r.json()['success'])

    def test_crear_dte_por_concepto(self):
        def payload(numero, **extra):
            d = {
                'receptor_id': self.prov_a.id, 'numero_documento': numero, 'monto_con_iva': 119000,
                'fecha_emision': self.hoy.isoformat(), 'fecha_recepcion': self.hoy.isoformat(),
                'estado_dte': 'ACEPTADO', 'estado_pago': 'Pendiente', 'tipo_documento': 'FACTURA ELECTRONICA',
                'tipo_transaccion': 'COMPRA', 'diasCredito': 30, 'bultos': 0, 'unidades_productos': 0,
                'descuento': 0, 'descuento_con_iva': False, 'motivo_rechazo': None,
                'documento_padre_id': None, 'empresa_receptora_id': self.empresa.id,
                'sucursal_receptora_id': self.sucursal.id,
            }
            d.update(extra)
            return d

        r = self._post('/app/crearDteCompras/', payload(8801, por_concepto=True))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['success'], r.content)
        r = self._post('/app/crearDteCompras/', payload(8802, por_concepto=False))
        self.assertTrue(r.json()['success'], r.content)
        flags = dict(Dte.objects.filter(numero_documento__in=[8801, 8802], emisor=self.prov_a)
                     .values_list('numero_documento', 'es_por_concepto'))
        self.assertEqual(flags, {8801: True, 8802: False})
