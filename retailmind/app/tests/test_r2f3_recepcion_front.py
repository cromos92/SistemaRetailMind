"""
Unidad R2F3 (ronda 2) — Frontend de Recepción DTE, modal de regularización,
DTEs en limbo y trazabilidad_dte.js.

Todo el cambio vive en el JS de las plantillas. Estos tests renderizan las
páginas reales y, si hay `node`, ejecutan las funciones reales con stubs
mínimos de DOM / jQuery / Swal (TZ=America/Santiago):

recepcion_dte.html
  1. B6-03/B6-04: 409 `lineas_sin_ficha` cierra el modal, avisa y recarga la
     lista; 409 `documento_cambio` recarga el detalle y la fila.
  2. B7-02: los textos post-recepción dicen «devolución pendiente» y
     «Mis Regularizaciones» (el stock no se mueve al emitir).
  3. B7-10: «Despachos recibidos» muestra el origen en la vista global y solo
     ofrece Emitir NC en las filas propias.
  4. «Corregir» y «Rehabilitar» dependen de recepcion_dte.puede_editar.
  5. ?dte_id= de la campana abre ese documento.
  6. Sobrante puro: el emisor ve «Esperando decisión del destino», no Emitir NC.
  7. B10-15: fechas 'YYYY-MM-DD' como fecha local (sin el día anterior) y
     diasDesde sin el día de más.
_modal_regularizar.html
  9.  B10-06: el textarea no se precarga con el historial.
  10. B10-02: «Enviar producto de cambio» busca en buscar_productos_emisor con
      recepcion_id.
  11. B8-05: EMITIR_NC envía faltante + dañada y confirma el monto con IVA.
dtes_en_limbo.html
  12. «Corregir» solo con líneas `corregible`; chip «NC esperando devolución».
trazabilidad_dte.js
  13. CC-10: el diagnóstico de una NC se pide con nc_id.

Ejecutar (BD aislada):
    DATABASE_URL=postgres://postgres:admin@localhost:5432/retail_r2f3 \
    python manage.py test app.tests.test_r2f3_recepcion_front --keepdb
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.template.loader import get_template
from django.test import TestCase, override_settings

from app.templatetags import permisos_tags
from .factories import crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'
MODAL = 'vistas/modulo_compras/_modal_regularizar.html'
JS_TRAZABILIDAD = Path(settings.BASE_DIR) / 'app' / 'static' / 'js' / 'trazabilidad_dte.js'


def _scripts_inline(html):
    return re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)


def _funcion(codigo, nombre):
    """Fuente de una función de primer nivel (termina en una línea '}')."""
    m = re.search(r'^function %s\(' % re.escape(nombre), codigo, re.M)
    if not m:
        raise AssertionError(f'no se encontró function {nombre}(')
    fin = codigo.index('\n}\n', m.start()) + 2
    return codigo[m.start():fin]


def _correr_node(test, codigo):
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / 'r2f3.js'
        ruta.write_text(codigo, encoding='utf-8')
        r = subprocess.run(['node', str(ruta)], capture_output=True, text=True, timeout=60,
                           encoding='utf-8', env=dict(os.environ, TZ='America/Santiago'))
    test.assertEqual(r.returncode, 0, r.stderr or r.stdout)
    return json.loads(r.stdout.strip().splitlines()[-1])


def _permiso_vista():
    """Pantalla y endpoints: todo concedido (decoradores y helpers de la vista)."""
    return mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True)


def _permiso_editar(valor):
    """recepcion_dte.puede_editar resuelto por el tag {% tiene_permiso %}."""
    def _resolver(self, codigo, tipo='puede_ver'):
        if codigo == 'recepcion_dte' and tipo == 'puede_editar':
            return valor
        return True
    return mock.patch.object(permisos_tags._PermisosDelRequest, 'resolver', autospec=True,
                             side_effect=_resolver)


# ─────────────────────────────────────────────────────────────────────────────
# recepcion_dte.html
# ─────────────────────────────────────────────────────────────────────────────

# jQuery / Swal / modal mínimos para correr funciones sueltas de la página.
_PRELUDIO_RECEPCION = r"""
global.window = global;
const calls = [];
const swals = [];
global.Swal = {
  fire(a, b, c) { swals.push(typeof a === 'object' ? a : { title: a, html: b, icon: c });
    return Promise.resolve({ isConfirmed: false }); },
  close() { calls.push('swal.close'); }, showLoading() {},
  mixin() { return { fire(o) { swals.push(o); } }; },
};
const dom = {};
function jq(sel) {
  const st = dom[sel] || (dom[sel] = { html: '', appended: [] });
  const p = new Proxy({}, { get(_, k) {
    if (k === 'append') return (h) => { st.appended.push(String(h)); return p; };
    if (k === 'empty') return () => { st.appended = []; return p; };
    if (k === 'html') return (h) => { if (h === undefined) return st.html; st.html = String(h); return p; };
    if (k === 'text') return (t) => { if (t === undefined) return st.text || ''; st.text = String(t); return p; };
    if (k === 'val') return (v) => { if (v === undefined) return st.val || ''; st.val = v; return p; };
    if (k === 'is') return () => false;
    if (k === 'length') return 1;
    return () => p;
  } });
  return p;
}
let ajaxPlan = null;
const ajaxLlamadas = [];
function diferido(r) {
  const d = {
    done(fn) { if (r && r.ok) fn(r.data); return d; },
    fail(fn) { if (!r || !r.ok) fn(r ? r.xhr : { status: 0 }); return d; },
    always(fn) { fn(); return d; },
  };
  return d;
}
global.$ = (sel) => jq(sel);
$.ajax = (opts) => { ajaxLlamadas.push(opts); return diferido(ajaxPlan); };
global.modalDetalle = { hide() { calls.push('hide'); }, show() { calls.push('show'); } };
global.getCookie = () => 'csrf';
"""


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class RecepcionDteR2F3Test(TestCase):
    URL = '/app/recepcion-dte/'

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa(nombre='Red R2F3', rut='76.431.000-7')
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='R2F3-SUC')
        cls.user = crear_usuario(username='r2f3_cajero', rol='administrador')
        crear_empresa_user(cls.user, cls.empresa, cls.sucursal)

    def setUp(self):
        self.client.force_login(self.user)
        s = self.client.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s['alias'] = 'R2F3-SUC'
        s.save()

    def _pagina(self, editar=True):
        with _permiso_vista(), _permiso_editar(editar):
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode('utf-8')

    def _script(self, html=None):
        scripts = _scripts_inline(html or self._pagina())
        return next(s for s in scripts if 'function procesarRecepcion(' in s)

    def _node_o_skip(self):
        if not shutil.which('node'):
            self.skipTest('node no está instalado')

    # ── 4. permiso de edición ────────────────────────────────────────────
    def test_constante_puede_editar_sigue_el_permiso(self):
        self.assertIn('const PUEDE_EDITAR_RECEPCION_DTE = true;', self._pagina(editar=True))
        self.assertIn('const PUEDE_EDITAR_RECEPCION_DTE = false;', self._pagina(editar=False))

    def test_corregir_y_rehabilitar_se_gatean(self):
        self._node_o_skip()
        script = self._script()
        codigo = _PRELUDIO_RECEPCION + '\n'.join(
            _funcion(script, n) for n in ('accionesGrupoRegularizar', 'rehabilitarDTE', 'abrirCorreccionDTE')
        ) + r"""
var PUEDE_EMITIR_NC_TRASPASO = true, PUEDE_REGULARIZAR_DTE = true, TITULO_SIN_PERMISO_NC = 'x';
var PUEDE_EDITAR_RECEPCION_DTE = false;
const g = { pendientes: [1], soyEmisor: true, dteId: 5, faltante: 1, danada: 0, esFacturaOBoleta: false };
const out = {};
out.sinPermiso = accionesGrupoRegularizar('5', g);
rehabilitarDTE(5, '10');
out.swalRehab = swals.length ? swals[swals.length - 1].title : null;
abrirCorreccionDTE(5);
out.swalCorr = swals.length ? swals[swals.length - 1].title : null;
PUEDE_EDITAR_RECEPCION_DTE = true;
out.conPermiso = accionesGrupoRegularizar('5', g);
console.log(JSON.stringify(out));
"""
        out = _correr_node(self, codigo)
        self.assertNotIn('abrirCorreccionDTE(', out['sinPermiso'])
        self.assertIn('abrirCorreccionDTE(5)', out['conPermiso'])
        self.assertEqual(out['swalRehab'], 'Sin permiso')
        self.assertEqual(out['swalCorr'], 'Sin permiso')

    def test_botones_rehabilitar_condicionados_en_plantilla(self):
        script = self._script()
        # Las 4 filas que ofrecen Rehabilitar dependen del permiso.
        self.assertEqual(script.count('onclick="rehabilitarDTE('), 4)
        self.assertEqual(script.count('PUEDE_EDITAR_RECEPCION_DTE'), 9)

    # ── 1. 409 de confirmar_recepcion ────────────────────────────────────
    def _procesar_con(self, respuesta_json, estado_fresco='EMITIDO'):
        script = self._script()
        codigo = _PRELUDIO_RECEPCION + '\n'.join(
            _funcion(script, n) for n in ('_msgError', '_escapeHtmlDte', 'procesarRecepcion')
        ) + r"""
var productosVerificacion = [{ dte_producto_id: 1, cantidad_esperada: 2, cantidad_recepcionada: 2,
  cantidad_danada: 0, cantidad_faltante: 0, cantidad_sobrante: 0, estado: 'RECEPCIONADO_OK', observaciones: '' }];
var documentoSeleccionado = { id: 5, numero_documento: 10, sucursal_origen: 'ORI' };
window.modoVista = 'recibidos';
function _udsNcQueIngresan() { return 0; }
function actualizarResumenVerificacion() {}
function invalidarKpisRecepcion() { calls.push('kpis'); }
function recargarVistaActiva() { calls.push('recargar'); }
function cargarHistorial() {}
function renderTablaRecepciones() { calls.push('render'); }
function _recargarDetalleCambiado(f) { calls.push('detalle:' + f.estado_dte); }
function _obtenerDocumentoFresco() { calls.push('fresco');
  return { then(ok) { ok({ id: 5, estado_dte: ESTADO_FRESCO }); } }; }
""" + f"""
var ESTADO_FRESCO = {json.dumps(estado_fresco)};
ajaxPlan = {{ ok: false, xhr: {{ status: 409, responseJSON: {json.dumps(respuesta_json)} }} }};
procesarRecepcion(true);
console.log(JSON.stringify({{ calls, swals, url: ajaxLlamadas[0].url }}));
"""
        return _correr_node(self, codigo)

    def test_409_lineas_sin_ficha_cierra_modal_y_recarga(self):
        self._node_o_skip()
        out = self._procesar_con({
            'success': False, 'lineas_sin_ficha': True,
            'error': '2 línea(s) de este traspaso perdieron su ficha en el origen (ej.: <b>X</b> x1).',
        })
        self.assertEqual(out['url'], '/app/dte/confirmar_recepcion/')
        self.assertIn('hide', out['calls'])
        self.assertIn('recargar', out['calls'])
        self.assertNotIn('fresco', out['calls'])  # recargar el detalle no lo arregla
        self.assertEqual(out['swals'][-1]['title'], 'No se puede recepcionar este documento')
        # Va como `text` (no html): el mensaje del servidor no se interpreta.
        self.assertIn('perdieron su ficha', out['swals'][-1]['text'])

    def test_409_documento_cambio_recarga_detalle_y_fila(self):
        self._node_o_skip()
        out = self._procesar_con({'success': False, 'documento_cambio': True, 'error': 'cambió'})
        self.assertIn('fresco', out['calls'])
        self.assertIn('detalle:EMITIDO', out['calls'])
        self.assertIn('render', out['calls'])
        self.assertNotIn('hide', out['calls'])

    def test_409_documento_cambio_ya_procesado_cierra(self):
        self._node_o_skip()
        out = self._procesar_con({'success': False, 'documento_cambio': True, 'error': 'cambió'},
                                 estado_fresco='RECEPCIONADO_COMPLETO')
        self.assertIn('hide', out['calls'])
        self.assertIn('recargar', out['calls'])

    # ── 2. textos post-recepción ─────────────────────────────────────────
    def test_textos_post_recepcion_hablan_de_devolucion_pendiente(self):
        html = self._pagina()
        self.assertNotIn('Si hay stock insuficiente en destino el ajuste será bloqueado', html)
        self.assertNotIn('La NC <strong>descuenta stock del destino</strong>', html)
        self.assertNotIn('Emitir una NC <strong>descuenta stock del destino</strong>', html)
        script = self._script(html)
        banner = _funcion(script, 'abrirModalAjusteEmisor')
        self.assertIn('devolución pendiente', banner)
        self.assertIn('«Mis Regularizaciones»', banner)
        confirmar = script.split("const htmlConfirm = esPost", 1)[1][:800]
        self.assertIn('devolución pendiente', confirmar)
        self.assertIn('Mis Regularizaciones', confirmar)
        self.assertIn('id="ajusteResDevolucionPendiente"', html)
        self.assertIn("$('#ajusteResDevolucionPendiente').toggleClass('d-none', !resp.es_post_recepcion);", script)

    # ── 3. despachos recibidos ───────────────────────────────────────────
    def test_recepcionados_origen_en_vista_global_y_nc_solo_propias(self):
        self._node_o_skip()
        script = self._script()
        codigo = _PRELUDIO_RECEPCION + '\n'.join(
            _funcion(script, n) for n in ('_escapeHtmlDte', '_esDteDeMiSucursal', 'renderTablaRecepcionados')
        ) + r"""
var SUCURSAL_ACTUAL_ID = 10, SUCURSAL_ACTUAL_ALIAS = 'EDEL';
var PUEDE_REGULARIZAR_DTE = true, TITULO_SIN_PERMISO_NC = 'x';
function _ncTraspasoBloqueada() { return false; }
function _ajusteFormatearPesos(n) { return '$' + n; }
let ALCANCE = 'todas';
function obtenerAlcanceDte() { return ALCANCE; }
window.emitidosData = [
  { id: 1, tipo_documento: 'GUIA', numero_documento: 11, sucursal_origen_id: 10, sucursal_origen: 'EDEL',
    sucursal_destino: 'PAO1', estado_dte: 'RECEPCIONADO_COMPLETO', ncs_stock_pendiente: 1, nc_ids_pendientes: [77] },
  { id: 2, tipo_documento: 'GUIA', numero_documento: 12, sucursal_origen_id: 20, sucursal_origen: '<b>PAO3</b>',
    sucursal_destino: 'PAO1', estado_dte: 'RECEPCIONADO_COMPLETO', ncs_stock_pendiente: 1, nc_ids_pendientes: [78] },
];
const out = {};
renderTablaRecepcionados();
out.global = dom['#recepcionesBody'].appended.slice();
ALCANCE = 'actual';
renderTablaRecepcionados();
out.actual = dom['#recepcionesBody'].appended.slice();
console.log(JSON.stringify(out));
"""
        out = _correr_node(self, codigo)
        propia, ajena = out['global']
        self.assertIn('btn-abrir-ajuste', propia)
        self.assertIn('btn-reparar-nc-stock', propia)
        self.assertIn('desde EDEL', propia)          # vista global: también las propias
        self.assertNotIn('btn-abrir-ajuste', ajena)
        self.assertNotIn('btn-reparar-nc-stock', ajena)
        self.assertIn('desde &lt;b&gt;PAO3&lt;/b&gt;', ajena)
        self.assertNotIn('desde EDEL', out['actual'][0])  # vista de la sucursal: sin origen propio

    # ── 5. ?dte_id= de la campana ────────────────────────────────────────
    def _abrir_desde_url(self, items, puede_recepcionar=True, fallo=False):
        script = self._script()
        codigo = _PRELUDIO_RECEPCION + '\n'.join(
            _funcion(script, n) for n in ('_msgError', '_escapeHtmlDte', '_abrirDocumentoDeUrl')
        ) + f"""
var PUEDE_RECEPCIONAR_DTE = {json.dumps(puede_recepcionar)};
var documentoSeleccionado = null;
window.location = {{ href: 'http://localhost/app/recepcion-dte/?dte_id=7&tipo=GUIA' }};
window.history = {{ replaceState(a, b, u) {{ calls.push('url:' + u); }} }};
function llenarDetalleDocumento(d) {{ calls.push('llenar:' + d.id); }}
function verDTERecepcionado(id) {{ calls.push('ver:' + id); }}
ajaxPlan = {json.dumps({'ok': False, 'xhr': {'status': 500}} if fallo else {'ok': True, 'data': {'success': True, 'items': items}})};
_abrirDocumentoDeUrl(7);
console.log(JSON.stringify({{ calls, swals, ajax: ajaxLlamadas.map(a => a.data),
  sel: documentoSeleccionado && documentoSeleccionado.id }}));
"""
        return _correr_node(self, codigo)

    def test_dte_id_abre_el_documento_pendiente(self):
        self._node_o_skip()
        out = self._abrir_desde_url([
            {'id': 6, 'estado_dte': 'EMITIDO', 'rol_sucursal_actual': 'destino'},
            {'id': 7, 'estado_dte': 'EMITIDO', 'rol_sucursal_actual': 'destino'},
        ])
        self.assertEqual(out['ajax'][0]['dte_id'], 7)
        self.assertEqual(out['ajax'][0]['alcance'], 'actual')
        self.assertEqual(out['ajax'][0]['fecha_inicio'], '')  # sin el rango del mes
        self.assertIn('url:/app/recepcion-dte/?tipo=GUIA', out['calls'])
        self.assertIn('llenar:7', out['calls'])
        self.assertIn('show', out['calls'])
        self.assertEqual(out['sel'], 7)

    def test_dte_id_origen_o_procesado_abre_solo_lectura(self):
        self._node_o_skip()
        out = self._abrir_desde_url([{'id': 7, 'estado_dte': 'EMITIDO', 'rol_sucursal_actual': 'origen'}])
        self.assertIn('ver:7', out['calls'])
        self.assertNotIn('show', out['calls'])
        out = self._abrir_desde_url([{'id': 7, 'estado_dte': 'RECEPCIONADO_COMPLETO', 'rol_sucursal_actual': 'destino'}])
        self.assertIn('ver:7', out['calls'])

    def test_dte_id_no_disponible_o_sin_permiso(self):
        self._node_o_skip()
        out = self._abrir_desde_url([])
        self.assertEqual(out['swals'][-1]['title'], 'Documento no disponible')
        self.assertNotIn('show', out['calls'])
        out = self._abrir_desde_url([], puede_recepcionar=False)
        self.assertEqual(out['ajax'], [])
        self.assertIn('ver:7', out['calls'])
        out = self._abrir_desde_url([], fallo=True)
        self.assertEqual(out['swals'][-1]['title'], 'No se pudo abrir el documento')

    def test_init_lee_dte_id(self):
        script = self._script()
        self.assertIn("paramsIniciales.get('dte_id')", script)
        self.assertIn('_abrirDocumentoDeUrl(dteIdInicial);', script)
        # El documento fresco también se pide por id exacto.
        self.assertIn('dte_id: docBase.id,', _funcion(script, '_obtenerDocumentoFresco'))

    # ── 6. sobrante puro ─────────────────────────────────────────────────
    def test_emisor_no_ve_emitir_nc_en_sobrante_puro(self):
        self._node_o_skip()
        script = self._script()
        codigo = _PRELUDIO_RECEPCION + _funcion(script, 'accionesLineaRegularizar') + r"""
var PUEDE_REGULARIZAR_DTE = true, PUEDE_EMITIR_NC_TRASPASO = true, TITULO_SIN_PERMISO_NC = 'x';
const base = { id: 3, estado: 'RECEPCIONADO_SOBRANTE', cantidad_faltante: 0, cantidad_danada: 0 };
console.log(JSON.stringify({
  emisorSobrante: accionesLineaRegularizar(Object.assign({}, base, { cantidad_sobrante: 2, soy_emisor: true })),
  receptorSobrante: accionesLineaRegularizar(Object.assign({}, base, { cantidad_sobrante: 2, soy_receptor: true })),
  emisorFaltante: accionesLineaRegularizar(Object.assign({}, base, { estado: 'FALTANTE', cantidad_faltante: 1, soy_emisor: true })),
}));
"""
        out = _correr_node(self, codigo)
        self.assertIn('Esperando decisión del destino', out['emisorSobrante'])
        self.assertNotIn('Emitir NC', out['emisorSobrante'])
        self.assertNotIn('abrirModalRegularizar', out['emisorSobrante'])
        self.assertIn('Resolver sobrante', out['receptorSobrante'])
        self.assertIn('Emitir NC', out['emisorFaltante'])

    # ── 7. fechas ────────────────────────────────────────────────────────
    def test_fechas_iso_sin_dia_anterior(self):
        self._node_o_skip()
        html = self._pagina()
        todo = '\n'.join(_scripts_inline(html))
        script = self._script(html)
        # Ninguna fecha de negocio vuelve a pasar por new Date('YYYY-MM-DD').
        for patron in ('new Date(dte.fecha_emision)', 'new Date(primera.dte_fecha)',
                       'new Date(doc.fecha_emision)', 'new Date(doc.fecha_recepcion)',
                       'new Date(dte.fecha_recepcion)', 'new Date(item.fecha_recepcion)',
                       'new Date(fechaStr)'):
            self.assertNotIn(patron, script)
        codigo = _PRELUDIO_RECEPCION + '\n'.join([
            _funcion(todo, 'fmtFechaISO'), _funcion(script, '_parseFechaDMY'),
            _funcion(script, '_fechaLocalDte'), _funcion(script, 'diasDesde'),
        ]) + r"""
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const hoy = new Date();
const ayer = new Date(hoy.getFullYear(), hoy.getMonth(), hoy.getDate() - 1);
console.log(JSON.stringify({
  fmt: fmtFechaISO('2026-05-11'), fmtDmy: fmtFechaISO('1/5/2026'), fmtVacio: fmtFechaISO(null),
  diaLocal: _fechaLocalDte('2026-05-11').getDate(), dmy: _fechaLocalDte('11/05/2026').getMonth(),
  invalida: _fechaLocalDte('xx'),
  hoy: diasDesde(iso(hoy)), ayer: diasDesde(iso(ayer)), nulo: diasDesde(''),
}));
"""
        out = _correr_node(self, codigo)
        self.assertEqual(out['fmt'], '11-05-2026')
        self.assertEqual(out['fmtDmy'], '01-05-2026')
        self.assertEqual(out['fmtVacio'], '-')
        self.assertEqual(out['diaLocal'], 11)
        self.assertEqual(out['dmy'], 4)
        self.assertIsNone(out['invalida'])
        self.assertEqual(out['hoy'], 0)   # antes: 1 (medianoche UTC = día anterior en Chile)
        self.assertEqual(out['ayer'], 1)
        self.assertIsNone(out['nulo'])

    # ── 13 (extra). un solo manejador de «Reparar» ───────────────────────
    def test_reparar_deja_el_clic_al_modulo_de_trazabilidad(self):
        script = self._script()
        manejador = script.split("$(document).on('click', '.btn-reparar-nc-stock'", 1)[1][:1200]
        self.assertIn("typeof window.TrazabilidadDTE.abrirReparacion === 'function') return;", manejador)
        self.assertIn("document.addEventListener('nc-stock-reparada'", script)


# ─────────────────────────────────────────────────────────────────────────────
# _modal_regularizar.html
# ─────────────────────────────────────────────────────────────────────────────

# Stubs de DOM/Swal/fetch para correr el <script> real del modal (como F4).
_STUB_MODAL = r"""
const els = {};
function mkEl(id) {
  const e = { id, textContent: '', _html: '', value: '', style: {}, attrs: {}, checked: false, disabled: false,
    open: false, className: '', title: '', _cls: new Set(),
    addEventListener(ev, fn) { (e._ls = e._ls || []).push(fn); }, removeEventListener(){}, dispatchEvent(){}, focus(){},
    click() { (e._ls || []).forEach(f => f()); }, setAttribute(k, v) { e.attrs[k] = v; }, getAttribute(k) { return e.attrs[k]; },
    appendChild(){}, removeChild(){}, querySelector(){ return null; },
    querySelectorAll(sel) {
      const m = /^\[(data-idx-[a-z-]+)\]$/.exec(sel); if (!m) return [];
      const re = new RegExp(m[1] + '="(\\d+)"', 'g'); const out = []; let r;
      while ((r = re.exec(e._html))) { const it = mkEl('i'); it.attrs[m[1]] = r[1]; out.push(it); }
      e._items = out; return out;
    } };
  e.classList = { toggle(c, on) { if (on === undefined ? !e._cls.has(c) : on) e._cls.add(c); else e._cls.delete(c); },
    add(c) { e._cls.add(c); }, remove(c) { e._cls.delete(c); }, contains(c) { return e._cls.has(c); } };
  Object.defineProperty(e, 'innerHTML', { configurable: true, get() { return e._html; }, set(v) { e._html = String(v); } });
  return e;
}
function el(id) { return els[id] || (els[id] = mkEl(id)); }
let tipoRadios = [];
global.document = { getElementById: el,
  querySelector(s) { if (s === '#modalRegularizar .btn-success') return el('btnGuardar');
    if (s === '[name=csrfmiddlewaretoken]') return { value: 'x' };
    if (s.startsWith('input[name="tipoRegularizacion"]')) return tipoRadios.find(r => r.checked) || null; return null; },
  querySelectorAll() { return []; }, createElement() { return mkEl('n'); }, body: { appendChild(){}, removeChild(){} }, cookie: '' };
global.window = global;
const swals = [];
global.Swal = { fire(a, b, c) { swals.push(typeof a === 'object' ? a : { title: a, html: b, icon: c });
  return Promise.resolve({ isConfirmed: true }); }, isVisible() { return false; }, getHtmlContainer() { return null; },
  showLoading() {}, close() {} };
global.$ = (sel) => ({ text() { return this; }, html() { return this; },
  val() { return 'motivo'; }, show() { return this; }, hide() { return this; }, off() { return this; },
  on() { return this; }, prop() { return this; } });
global.bootstrap = { Modal: class { show(){} hide(){} static getInstance() { return { hide(){} }; } } };
let plan = []; const llamadas = [];
global.fetch = (url, opts) => { llamadas.push({ url, opts: opts || {} }); const r = plan.shift();
  return r ? Promise.resolve(r) : Promise.reject(new Error('fetch ' + url)); };
const jr = (o, st) => ({ ok: (st || 200) < 400, status: st || 200, redirected: false,
  headers: { get: () => 'application/json' }, json: () => Promise.resolve(o) });
global.productoSeleccionado = null; global.productosProblemas = [];
global.notificarRegularizacionCompleta = () => {}; global.cargarProductosProblemas = () => {};
const tick = () => new Promise(r => setTimeout(r, 0));
"""


def _render_modal(puede_nc=True):
    return get_template(MODAL).render({'puede_emitir_nc_traspaso': puede_nc})


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class ModalRegularizarR2F3Test(TestCase):

    def _node_modal(self, cuerpo):
        if not shutil.which('node'):
            self.skipTest('node no está instalado')
        script = _scripts_inline(_render_modal())[0]
        codigo = _STUB_MODAL + '\n(0, eval)(' + json.dumps(script) + ');\n(async () => {\n' + cuerpo + \
            "\n})().catch(e => { console.error(e); process.exit(1); });\n"
        return _correr_node(self, codigo)

    # ── 9. B10-06 ────────────────────────────────────────────────────────
    def test_observaciones_no_se_precargan_con_el_historial(self):
        html = _render_modal()
        self.assertNotIn("document.getElementById('regObservaciones').value = productoSeleccionado.observaciones", html)
        self.assertIn("document.getElementById('regObservaciones').value = '';", html)
        self.assertIn('id="regObservacionesHistorial"', html)
        self.assertIn("document.getElementById('regObservacionesHistorial').textContent = _histObs;", html)
        # El historial del Swal «ya regularizado» se escapa entero.
        self.assertIn('reg-historial-obs">${_escReg(obs)}</pre>', html)

    def test_observaciones_en_node(self):
        out = self._node_modal(r"""
  const XSS = '[2026-09-01] no llegaron <img src=x onerror=alert(1)>';
  productosProblemas = [{ id: 9, estado: 'FALTANTE', tipo_documento: 'GUIA', cantidad_faltante: 2, cantidad_danada: 0,
    cantidad_recibida: 1, cantidad_esperada: 3, producto_nombre: 'x', sku: '1', observaciones: XSS,
    requiere_nc: false, soy_emisor: false, soy_receptor: true, emisor: 'A', receptor: 'A' }];
  el('regObservaciones').value = 'nota vieja';
  abrirModalRegularizar(9);
  await tick();
  console.log(JSON.stringify({ valor: el('regObservaciones').value,
    hist: el('regObservacionesHistorial').textContent, histHtml: el('regObservacionesHistorial').innerHTML,
    oculto: el('regObservacionesHistorialWrap').classList.contains('d-none') }));
""")
        self.assertEqual(out['valor'], '')
        self.assertIn('no llegaron <img', out['hist'])  # como texto (textContent)
        self.assertEqual(out['histHtml'], '')
        self.assertFalse(out['oculto'])

    # ── 10. B10-02 ───────────────────────────────────────────────────────
    def test_enviar_cambio_busca_en_el_emisor_con_recepcion_id(self):
        html = _render_modal()
        self.assertNotIn("/app/buscar_productos_bodega/?q=", html)
        self.assertNotIn('function buscarProductosReemplazo(', html)
        out = self._node_modal(r"""
  productoSeleccionado = { id: 4018 };
  el('buscarProductoEnvioCambio').value = 'zap';
  plan = [jr({ success: true, productos: [{ id: 5, sku: 'S1', nombre: null, talla: '40', stock: 2, precio: 100 }] })];
  buscarProductosParaEnvio(); for (let i = 0; i < 6; i++) await tick();
  const ok = el('resultadosProductosEnvioCambio').innerHTML;
  const c = el('resultadosProductosEnvioCambio');
  if (c._items && c._items[0]) c._items[0].click();
  const elegido = el('productoEnvioCambioId').value;
  plan = [jr({ error: true, mensaje: 'Solo puedes <b>buscar</b> en tu traspaso.' }, 403)];
  buscarProductosParaEnvio(); for (let i = 0; i < 6; i++) await tick();
  console.log(JSON.stringify({ url: llamadas[0].url, ok, elegido,
    denegado: el('resultadosProductosEnvioCambio').innerHTML }));
""")
        self.assertEqual(out['url'], '/app/dte/buscar_productos_emisor/?query=zap&recepcion_id=4018')
        self.assertIn('S1', out['ok'])
        self.assertNotIn('Error al buscar', out['ok'])
        self.assertEqual(out['elegido'], 5)  # id de Producto_Talla
        self.assertIn('Solo puedes &lt;b&gt;buscar&lt;/b&gt;', out['denegado'])

    # ── 11. B8-05 ────────────────────────────────────────────────────────
    def test_emitir_nc_envia_faltante_mas_danada_y_monto_con_iva(self):
        out = self._node_modal(r"""
  const p = { id: 9, tipo_documento: 'FACTURA ELECTRONICA', precio_unitario: 1000, cantidad_faltante: 1,
    cantidad_danada: 1, requiere_nc: true, soy_emisor: true, dte_numero: 5, producto_nombre: 'x', receptor: 'R',
    estado: 'RECEPCIONADO_PARCIAL', dte_fecha: '2026-05-11' };
  productoSeleccionado = p;
  mostrarPanelRegularizacion('EMITIR_NC');
  const panel = { cant: el('ncEjecutarCantidad').textContent, tipo: el('ncEjecutarTipoProblema').textContent,
    total: el('ncEjecutarPreviewTotal').textContent, fecha: el('ncDteFecha').textContent };
  tipoRadios = [{ value: 'EMITIR_NC', checked: true }];
  el('ncEjecutarMotivo').value = 'faltó y llegó roto';
  plan = [jr({ success: true, tipo: 'NC_EMITIDA', numero_nc: 1 })];
  guardarRegularizacion(); for (let i = 0; i < 8; i++) await tick();
  const envio = llamadas.find(l => l.opts && l.opts.body);
  const confirmacion = swals[0] ? swals[0].html : '';
  // Sobrante puro: no hay nada que acreditar, no se envía.
  swals.length = 0; llamadas.length = 0;
  productoSeleccionado = Object.assign({}, p, { cantidad_faltante: 0, cantidad_danada: 0, cantidad_sobrante: 2 });
  guardarRegularizacion(); await tick();
  console.log(JSON.stringify({ panel, body: envio ? JSON.parse(envio.opts.body) : null, confirmacion,
    sobrante: { titulo: swals[0] && swals[0].title, llamadas: llamadas.length } }));
""")
        self.assertEqual(out['panel']['cant'], 2)
        self.assertEqual(out['panel']['tipo'], 'FALTANTE (1) + DAÑADO (1)')
        self.assertEqual(out['panel']['total'], '2.380')
        self.assertEqual(out['panel']['fecha'], '11-05-2026')
        self.assertIsNotNone(out['body'])
        self.assertEqual(out['body']['cantidad_nc'], 2)
        self.assertEqual(out['body']['tipo_regularizacion'], 'EMITIR_NC')
        self.assertIn('Monto NC (con IVA)', out['confirmacion'])
        self.assertIn('$2.380', out['confirmacion'])
        self.assertEqual(out['sobrante'], {'titulo': 'Sin unidades que acreditar', 'llamadas': 0})


# ─────────────────────────────────────────────────────────────────────────────
# dtes_en_limbo.html
# ─────────────────────────────────────────────────────────────────────────────

_STUB_LIMBO = r"""
const els = {};
function mkEl(id) {
  const e = { id, value: '', textContent: '', innerHTML: '', disabled: false, _cls: new Set(), dataset: {},
    addEventListener() {}, querySelectorAll() { return []; } };
  e.classList = { add(c) { e._cls.add(c); }, remove(c) { e._cls.delete(c); }, contains(c) { return e._cls.has(c); },
    toggle(c, on) { if (on) e._cls.add(c); else e._cls.delete(c); } };
  return e;
}
global.document = { cookie: '', getElementById(id) { return els[id] || (els[id] = mkEl(id)); },
  addEventListener() {}, querySelectorAll() { return []; } };
global.window = global;
global.bootstrap = { Modal: class { show() {} static getInstance() { return { hide() {} }; } } };
global.fetch = () => new Promise(() => {});
"""


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DtesEnLimboR2F3Test(TestCase):
    URL = '/app/dtes-en-limbo/'

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa(nombre='Red R2F3 Limbo', rut='76.432.000-1')
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='R2F3-LIM')
        cls.maestro = crear_usuario(username='r2f3_limbo', rol='maestro')
        crear_empresa_user(cls.maestro, cls.empresa, cls.sucursal)

    def setUp(self):
        self.client.force_login(self.maestro)
        s = self.client.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()

    def _script(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('<option value="RECEPCIONADO_COMPLETO">NC esperando devolución</option>', html)
        return next(s for s in _scripts_inline(html) if 'function renderCorregir(' in s)

    def test_corregir_solo_lineas_corregibles_y_chip(self):
        if not shutil.which('node'):
            self.skipTest('node no está instalado')
        codigo = _STUB_LIMBO + self._script() + r"""
const XSS = '<img src=x onerror=alert(1)>';
const out = {};
detalleActual = {
  dte: { id: 1, numero_documento: 10, tipo_documento: 'GUIA', estado_dte: 'RECEPCIONADO_PARCIAL',
         destino_alias: XSS, unidades_productos: 3, monto_con_iva: 1000, motivo_rechazo: XSS,
         ncs_hijas_pendientes_devolucion: 1 },
  productos: [
    { recepcion_id: 1, sku: 'A', talla: '40', descripcion: XSS, cantidad_esperada: 2, cantidad_recepcionada: 1,
      cantidad_faltante: 1, estado: 'FALTANTE', corregible: true },
    { recepcion_id: 2, sku: 'B', talla: '41', descripcion: 'regularizada', cantidad_esperada: 2,
      cantidad_recepcionada: 1, cantidad_faltante: 1, estado: 'REGULARIZADO', corregible: false },
  ],
  acciones_permitidas: ['corregir', 'nc_con_devolucion', 'nc_sin_devolucion'],
};
renderPasoAccion();
out.paso = document.getElementById('modalResolverBody').innerHTML;
renderCorregir();
out.corregir = document.getElementById('modalResolverBody').innerHTML;
// Sin el flag (backend anterior): criterio viejo, faltante > 0.
detalleActual.productos.forEach(p => { delete p.corregible; });
renderCorregir();
out.legacy = document.getElementById('modalResolverBody').innerHTML;
// Recibido completo con NC esperando devolución: sin acciones.
detalleActual = { dte: Object.assign({}, detalleActual.dte, { estado_dte: 'RECEPCIONADO_COMPLETO', motivo_rechazo: null }),
  productos: [], acciones_permitidas: [] };
renderPasoAccion();
out.completo = document.getElementById('modalResolverBody').innerHTML;
renderTabla([{ id: 1, numero_documento: 10, tipo_documento: 'GUIA', destino_alias: XSS, fecha_emision: '2026-05-11',
  estado_dte: 'RECEPCIONADO_COMPLETO', motivo_rechazo: null, dias_en_limbo: 2,
  resumen_problemas: { faltantes: 0, danados: 0, sobrantes: 0 }, unidades_productos: 2, monto_con_iva: 100,
  acciones_permitidas: [], ncs_hijas_pendientes_devolucion: 1 }]);
out.tabla = document.getElementById('tablaLimbo').innerHTML;
console.log(JSON.stringify(out));
"""
        out = _correr_node(self, codigo)
        self.assertIn('data-recepcion-id="1"', out['corregir'])
        self.assertNotIn('data-recepcion-id="2"', out['corregir'])
        self.assertIn('data-recepcion-id="2"', out['legacy'])
        self.assertIn('1 NC esperando devolución', out['paso'])
        self.assertIn('Mis Regularizaciones', out['paso'])
        self.assertNotIn('<img', out['paso'] + out['corregir'] + out['tabla'])
        self.assertIn('No requiere acción de tu parte', out['completo'])
        self.assertIn('1 NC esperando devolución', out['tabla'])
        self.assertIn('>Recepcionado<', out['tabla'])
        self.assertIn('>Ver</button>', out['tabla'])


# ─────────────────────────────────────────────────────────────────────────────
# trazabilidad_dte.js
# ─────────────────────────────────────────────────────────────────────────────

class TrazabilidadJsR2F3Test(TestCase):
    def test_diagnostico_de_nc_se_pide_con_nc_id(self):
        js = JS_TRAZABILIDAD.read_text(encoding='utf-8')
        self.assertIn('/app/api/dte/ncs_sin_stock/?pagina=${pagina}&page_size=100&nc_id=${encodeURIComponent(ncId)}', js)
        self.assertIn('Number(i.nc_id) === Number(ncId)', js)
        # Mensajes del servidor escapados en el modal de reparación.
        self.assertNotIn("${(data && data.error) || 'No se pudo cargar el diagnóstico.'}", js)
        self.assertIn('<p>${esc(data.message || \'OK\')}</p>', js)
