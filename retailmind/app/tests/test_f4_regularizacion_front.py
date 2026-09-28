"""
F4 — Frontend de regularización (_modal_regularizar.html) y detalle de DTE
(detalle_dte.html).

Los arreglos viven en el JS de las plantillas; aquí se cubre:

1. El parcial del modal renderiza con los helpers nuevos y sin los cálculos
   viejos: vista previa de NC con IVA como calcular_montos_nc (B10-05), fecha
   sin new Date('YYYY-MM-DD') (B10-15), mensajes de error de {error: true,
   mensaje} (B10-13), aviso de TXT fallido y enlace autenticado en la NC masiva
   (B10-18), ramas de éxito REGULARIZAR_CON_NC / MERCADERIA_ENCONTRADA (B10-12),
   rótulo del botón restaurado (B10-11) y leyenda/texto por caso (B10-17).
2. Si hay `node`, ejecuta los helpers reales del modal y compara la vista
   previa con calcular_montos_nc sobre un DTE de prueba.
3. detalle_dte.html calcula el saldo con TODOS los pagos (NC incluidas) y el
   contrato de /app/api/detalle_dte_completo/ entrega esos pagos (B10-10).
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

from django.template.loader import get_template
from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import Dte, Dte_Detalle_Pago, Dte_Productos
from app.views_modulo_documentos import calcular_montos_nc
from .factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla, crear_usuario,
    setup_entorno_completo,
)

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'
MODAL = 'vistas/modulo_compras/_modal_regularizar.html'


def _render_modal(puede_nc=True):
    return get_template(MODAL).render({'puede_emitir_nc_traspaso': puede_nc})


def _js_helpers(html):
    """Bloque de helpers compartidos del <script> del modal."""
    script = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)[0]
    ini = script.index('// ===== Helpers compartidos del modal =====')
    fin = script.index('// ===== Regularización masiva por DTE')
    return script[ini:fin]


class ModalRegularizarPlantillaTest(TestCase):
    def test_render_con_y_sin_permiso_nc(self):
        self.assertIn('return true;', _render_modal(True))
        self.assertIn('return false;', _render_modal(False))

    def test_vista_previa_nc_con_iva(self):
        html = _render_modal()
        self.assertIn('function montoNcPreview(', html)
        self.assertIn('function _montoNcLinea(', html)
        # Neto e IVA visibles en los dos paneles de NC
        for gancho in ('id="ncPreviewNeto"', 'id="ncPreviewIva"',
                       'id="ncEjecutarPreviewNeto"', 'id="ncEjecutarPreviewIva"'):
            self.assertIn(gancho, html)
        # Cálculos viejos (neto rotulado como total / 1,19 fijo)
        self.assertNotIn('cantidadProblema * precioUnit * 1.19', html)
        self.assertNotIn('const iva = montoNeto * 0.19;', html)
        self.assertNotIn('Number(cantidadProblema * precioOriginal)', html)

    def test_fecha_sin_desfase_utc(self):
        html = _render_modal()
        self.assertIn('fmtFechaISO(productoSeleccionado.dte_fecha)', html)
        self.assertNotIn('new Date(productoSeleccionado.dte_fecha)', html)

    def test_errores_y_respuestas(self):
        html = _render_modal()
        self.assertIn('function _msgErrorReg(', html)
        self.assertIn('.then(_jsonRespuestaReg)', html)
        self.assertNotIn("Swal.fire('Error', data.error || 'Error al procesar', 'error')", html)
        self.assertNotIn("Swal.fire('Error', data.error || 'Error al procesar.', 'error')", html)

    def test_exito_y_boton(self):
        html = _render_modal()
        self.assertIn("data.tipo === 'REGULARIZAR_CON_NC' && data.nc_generada", html)
        self.assertIn("data.tipo === 'MERCADERIA_ENCONTRADA'", html)
        self.assertNotIn("mensaje = 'Producto regularizado correctamente';", html)
        # B10-11: abrirModalRegularizar restaura el rótulo del botón
        cuerpo = html.split('function abrirModalRegularizar(', 1)[1].split('function mostrarPanelRegularizacion(', 1)[0]
        self.assertIn("_btnGuardarReg.innerHTML = '<i class=\"bi bi-check-circle\"></i> Guardar Regularización'", cuerpo)

    def test_nc_masiva_txt(self):
        html = _render_modal()
        self.assertIn('data.txt_generado === false', html)
        self.assertIn('/txt-acepta/', html.split('function procesarRegularizacionDTE(', 1)[1])

    def test_textos_por_caso(self):
        html = _render_modal()
        self.assertIn('id="leyendaOpcionesItems"', html)
        self.assertNotIn('Ajuste interno (guías)', html)
        self.assertIn("'Guía de despacho (sin Nota de Crédito)'", html)
        self.assertNotIn("descripcionTipo.textContent = 'Misma empresa. Puedes regularizar directamente sin solicitudes.'", html)
        # El cambio directo lo rechaza el servidor: el radio queda deshabilitado
        self.assertIn('id="regCambiarProducto" value="CAMBIAR_PRODUCTO" disabled', html)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class ModalRegularizarHelpersNodeTest(TestCase):
    """Ejecuta los helpers reales del modal con node (si está instalado)."""

    def setUp(self):
        if not shutil.which('node'):
            self.skipTest('node no está instalado')
        env = setup_entorno_completo()
        self.empresa = env['empresa']
        self.sucursal = env['sucursal']
        self.destino = crear_empresa(nombre='Receptora F4', rut='76.555.555-5')

    def _nc_servidor(self, tipo, precio, stock, cant, monto_neto, monto_con_iva):
        hoy = timezone.localdate()
        _, pt = crear_producto_con_talla(self.sucursal, articulo='F4 NC', talla='40', sku=9400000 + precio % 1000)
        dte = Dte.objects.create(
            emisor=self.empresa, receptor=self.destino, numero_documento=90000 + precio % 1000,
            tipo_documento=tipo, monto_neto=monto_neto, monto_con_iva=monto_con_iva,
            estado_pago='PENDIENTE', estado_dte='EMITIDO', responsable='test',
            fecha_emision=hoy, fecha_vencimiento=hoy, diasCredito=0, bultos=1,
            unidades_productos=stock, tipo_transaccion='TRASPASO', sucursal=self.sucursal,
        )
        dp = Dte_Productos.objects.create(dte=dte, productoTalla=pt, descripcion='F4', costo=1,
                                          sobreprecio=0, precio=precio, stock=stock, activo=True)
        return calcular_montos_nc(dte, [(dp, cant)])

    def _node(self, codigo):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / 'f4.js'
            ruta.write_text(codigo, encoding='utf-8')
            r = subprocess.run(['node', str(ruta)], capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, TZ='America/Santiago'))
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_vista_previa_igual_al_servidor(self):
        # Factura de traspaso (precio NETO): 2 u x 12.089, como la línea 4018
        neto_f, total_f = self._nc_servidor('FACTURA ELECTRONICA', 12089, 2, 2, 24178, 28772)
        # Boleta (precio CON IVA): 2 u x 11.900
        neto_b, total_b = self._nc_servidor('BOLETA ELECTRONICA', 11900, 2, 2, 20000, 23800)
        js = _js_helpers(_render_modal()) + """
            const f = _montoNcLinea({precio_unitario: 12089, tipo_documento: 'FACTURA ELECTRONICA'}, 2);
            const b = _montoNcLinea({precio_unitario: 11900, tipo_documento: 'BOLETA ELECTRONICA'}, 2);
            console.log(JSON.stringify({
                f, b,
                fecha: fmtFechaISO('2026-05-11'),
                err403: _msgErrorReg({error: true, mensaje: 'Sin permiso puede_aprobar'}),
                errStr: _msgErrorReg({error: 'Tope'}),
                guia: _esGuiaReg({tipo_documento: 'GUIA'}),
                misma: _esMismaEmpresaReg({emisor: 'A SPA', receptor: 'B LTDA'}),
                ncGuia: _admiteNcReg({tipo_documento: 'GUIA'}),
                esc: _escReg('<b>x</b>'),
            }));
        """
        out = self._node(js)
        self.assertEqual((out['f']['neto'], out['f']['total']), (neto_f, total_f))
        self.assertEqual((out['b']['neto'], out['b']['total']), (neto_b, total_b))
        self.assertEqual(out['f']['total'], 28772)  # antes la vista previa mostraba 24.178
        self.assertEqual(out['fecha'], '11-05-2026')
        self.assertEqual(out['err403'], 'Sin permiso puede_aprobar')
        self.assertEqual(out['errStr'], 'Tope')
        self.assertTrue(out['guia'])
        self.assertFalse(out['misma'])
        self.assertFalse(out['ncGuia'])
        self.assertEqual(out['esc'], '&lt;b&gt;x&lt;/b&gt;')


# Stubs mínimos de DOM/Swal/fetch para correr el <script> real del modal en node.
_STUB_NODE = r"""
const els = {};
function mkEl(id) {
  const e = { id, textContent: '', _html: '', value: '', style: {}, attrs: {}, checked: false, disabled: false,
    className: '', title: '', classList: { toggle(){}, add(){}, remove(){}, contains(){ return false; } },
    addEventListener(ev, fn) { (e._ls = e._ls || []).push(fn); }, removeEventListener(){}, dispatchEvent(){}, focus(){},
    click() { (e._ls || []).forEach(f => f()); }, setAttribute(k, v) { e.attrs[k] = v; }, getAttribute(k) { return e.attrs[k]; },
    appendChild(){}, removeChild(){}, querySelector(){ return null; },
    querySelectorAll(sel) {
      const m = /^\[(data-idx-[a-z-]+)\]$/.exec(sel); if (!m) return [];
      const re = new RegExp(m[1] + '="(\\d+)"', 'g'); const out = []; let r;
      while ((r = re.exec(e._html))) { const it = mkEl('i'); it.attrs[m[1]] = r[1]; out.push(it); }
      e._items = out; return out;
    } };
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
  return Promise.resolve({ isConfirmed: true }); }, isVisible() { return false; }, getHtmlContainer() { return null; } };
global.$ = (sel) => ({ text() { return sel === '#ncTotalUnidades' ? '1' : this; }, html() { return this; },
  val() { return 'motivo'; }, show() { return this; }, hide() { return this; }, off() { return this; },
  on() { return this; }, prop() { return this; } });
global.bootstrap = { Modal: class { show(){} static getInstance() { return { hide(){} }; } } };
let plan = []; const llamadas = [];
global.fetch = (url) => { llamadas.push(url); const r = plan.shift();
  return r ? Promise.resolve(r) : Promise.reject(new Error('fetch ' + url)); };
const jr = (o, st) => ({ ok: (st || 200) < 400, status: st || 200, redirected: false,
  headers: { get: () => 'application/json' }, json: () => Promise.resolve(o) });
global.productoSeleccionado = null; global.productosProblemas = [];
global.notificarRegularizacionCompleta = () => {}; global.cargarProductosProblemas = () => {};
const tick = () => new Promise(r => setTimeout(r, 0));
"""


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class ModalRegularizarRevisionTest(TestCase):
    """Arreglos de la revisión adversarial de F4: TXT de la NC masiva según
    quién la emite, Ajustar cantidad entre empresas, faltante del servidor en
    Mercadería encontrada y XSS en búsquedas/mensajes de éxito."""

    def test_plantilla_sin_onclick_con_datos_de_productos(self):
        html = _render_modal()
        for fn in ('seleccionarProductoSolicitud', 'seleccionarProductoEnvioCambio'):
            self.assertNotIn(f'onclick="{fn}(', html)
            self.assertIn(f'function {fn}(', html)
        # El cambio DIRECTO (seleccionarNuevoProducto y su buscador) se retiró
        # en la unidad D (2026-09-26): era inalcanzable (radio deshabilitado).
        self.assertNotIn('function seleccionarNuevoProducto(', html)
        self.assertNotIn('id="opcionCambioDirecto"', html)
        # La confirmación que prometía una NC por "Ajustar cantidad" entre empresas ya no existe
        self.assertNotIn('Cumple normativa SII (empresas diferentes)', html)
        self.assertIn('DIRECTO_EMPRESAS', html)

    def test_comportamiento_en_node(self):
        if not shutil.which('node'):
            self.skipTest('node no está instalado')
        script = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', _render_modal(), re.S)[0]
        codigo = _STUB_NODE + '\n(0, eval)(' + json.dumps(script) + ');\n' + r"""
(async () => {
  const XSS = '<img src=x onerror=alert(1)>';
  const out = {};
  // NC masiva: el endpoint autenticado solo es el botón principal para el emisor
  const resp = { success: true, numero_nc: 948, nc_id: 77, monto_total: 1190, total_unidades: 1,
    archivo_txt_url: '/media/documentos_electronicos/nc/NC.txt', txt_generado: true };
  const dteP = { id: 1, tipo_documento: 'FACTURA ELECTRONICA', emisor: 'EDEL' };
  for (const soy of [true, false]) {
    swals.length = 0; plan = [jr(resp)];
    procesarRegularizacionDTE(947, [Object.assign({ soy_emisor: soy }, dteP)], 5);
    for (let i = 0; i < 6; i++) await tick();
    out['masiva_' + soy] = (/<a href="([^"]+)" download class="btn btn-primary"/.exec(swals[swals.length - 1].html) || [])[1];
  }
  swals.length = 0; plan = [jr(Object.assign({}, resp, { archivo_txt_url: null, txt_generado: false }))];
  procesarRegularizacionDTE(947, [Object.assign({ soy_emisor: false }, dteP)], 5);
  for (let i = 0; i < 6; i++) await tick();
  out.masiva_sin_txt_no_emisor = swals[swals.length - 1].html;
  // AJUSTAR entre empresas: no se envía
  const pE = { id: 9, tipo_documento: 'FACTURA ELECTRONICA', precio_unitario: 100, cantidad_faltante: 3,
    cantidad_danada: 0, cantidad_recibida: 0, cantidad_esperada: 3, producto_nombre: XSS, requiere_nc: true,
    soy_emisor: false, soy_receptor: false };
  productoSeleccionado = pE; tipoRadios = [{ value: 'AJUSTAR', checked: true }]; el('ajustarCantidad').value = '1';
  swals.length = 0; llamadas.length = 0; guardarRegularizacion(); await tick();
  out.ajustar = { titulo: swals[0] && swals[0].title, llamadas: llamadas.length };
  // Mercadería encontrada: faltante del servidor (0) aunque la lista dijera 3
  productoSeleccionado = Object.assign({}, pE, { requiere_nc: false, producto_nombre: 'x' });
  tipoRadios = [{ value: 'MERCADERIA_ENCONTRADA', checked: true }]; el('cantidadMercaderiaEncontrada').value = '2';
  swals.length = 0; plan = [jr({ success: true, tipo: 'MERCADERIA_ENCONTRADA', cantidad_ingresada: 2,
    alerta: 'Se ingresaron 2 unidades al inventario de NICK2. Faltante restante: 0' })];
  guardarRegularizacion(); for (let i = 0; i < 8; i++) await tick();
  out.encontrada = swals[swals.length - 1].title;
  // Éxito CAMBIO_ENVIADO: datos del servidor escapados
  productoSeleccionado = Object.assign({}, pE, { soy_emisor: true });
  tipoRadios = [{ value: 'ENVIAR_CAMBIO', checked: true }];
  el('productoEnvioCambioId').value = '12'; el('cantidadEnvioCambio').value = '1'; el('motivoEnvioCambio').value = 'm';
  swals.length = 0; plan = [jr({ success: true, tipo: 'CAMBIO_ENVIADO', numero_nc: 1, numero_dte_cambio: XSS,
    producto_cambio: XSS, documento_url: '"><img src=x>' })];
  guardarRegularizacion(); for (let i = 0; i < 8; i++) await tick();
  out.cambio = swals[swals.length - 1].html;
  // Búsqueda en el inventario del emisor (otra empresa)
  el('buscarProductoSolicitud').value = 'zap';
  plan = [{ json: () => Promise.resolve({ success: true, productos: [
    { id: 5, sku: 'S1', nombre: 'V22-<664-7A" x=' + XSS, talla: '40', stock: 2, precio: 100 }] }) }];
  buscarProductosEmisor(); for (let i = 0; i < 6; i++) await tick();
  const c = el('resultadosProductosEmisor');
  out.busqueda = c.innerHTML;
  if (c._items && c._items[0]) c._items[0].click();
  out.seleccion = { id: el('productoSolicitudId').value, html: el('productoSolicitudSeleccionado').innerHTML };
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / 'f4_rev.js'
            ruta.write_text(codigo, encoding='utf-8')
            r = subprocess.run(['node', str(ruta)], capture_output=True, text=True, timeout=60,
                               encoding='utf-8', env=dict(os.environ, TZ='America/Santiago'))
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        out = json.loads(r.stdout.strip().splitlines()[-1])

        # B10-18: emisor → endpoint; destino/Maestro → archivo (el endpoint les da 403)
        self.assertEqual(out['masiva_true'], '/app/dte/77/txt-acepta/')
        self.assertEqual(out['masiva_false'], '/media/documentos_electronicos/nc/NC.txt')
        self.assertNotIn('Descárgalo con el botón', out['masiva_sin_txt_no_emisor'])
        self.assertNotIn('/txt-acepta/', out['masiva_sin_txt_no_emisor'])
        self.assertIn('EDEL', out['masiva_sin_txt_no_emisor'])
        # B10-17: Ajustar entre empresas se corta en el cliente
        self.assertEqual(out['ajustar'], {'titulo': 'No disponible', 'llamadas': 0})
        # B10-12: título con el faltante del servidor
        self.assertEqual(out['encontrada'], 'Mercadería encontrada: línea regularizada')
        # XSS
        self.assertNotIn('<img', out['cambio'])
        self.assertIn('&lt;img', out['cambio'])
        self.assertNotIn('onclick=', out['busqueda'])
        self.assertNotIn('<664', out['busqueda'])
        self.assertIn('V22-&lt;664-7A&quot;', out['busqueda'])
        self.assertEqual(out['seleccion']['id'], 5)
        self.assertNotIn('<img', out['seleccion']['html'])


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DetalleDteSaldoTest(TestCase):
    def setUp(self):
        env = setup_entorno_completo()
        self.empresa = env['empresa']
        self.sucursal = env['sucursal']
        self.proveedor = crear_empresa(nombre='Proveedor F4', rut='76.666.666-6', esProveedor=True)
        self.maestro = crear_usuario(username='maestro_f4', rol='maestro')
        crear_empresa_user(self.maestro, self.empresa, self.sucursal)
        self.client.force_login(self.maestro)
        s = self.client.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()
        hoy = timezone.localdate()
        self.dte = Dte.objects.create(
            emisor=self.proveedor, receptor=self.empresa, numero_documento=244242,
            tipo_documento='FACTURA ELECTRONICA', monto_neto=5539010, monto_con_iva=6591598,
            estado_pago='PAGADO', estado_dte='ACEPTADO', responsable='test',
            fecha_emision=hoy, fecha_vencimiento=hoy + timedelta(days=30), diasCredito=30,
            bultos=0, unidades_productos=1, tipo_transaccion='COMPRA', sucursal=self.sucursal,
        )
        Dte_Detalle_Pago.objects.create(dte=self.dte, metodo_pago='Nota de Crédito', monto=632447)
        Dte_Detalle_Pago.objects.create(dte=self.dte, metodo_pago='Cheque', monto=5959151)

    def test_pagina_calcula_saldo_con_todos_los_pagos(self):
        r = self.client.get(f'/app/detalle_dte/{self.dte.id}/')
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        self.assertIn('pagosDte.reduce(', html)
        self.assertNotIn("formatMonto(dte.saldo || 0)", html)
        # Badge de estado de pago sin distinguir mayúsculas ('Pendiente'/'Pagado')
        self.assertIn("estilos[String(estado || '').trim().toUpperCase()]", html)
        self.assertIn('function escapeHtmlDte(', html)

    def test_api_entrega_los_pagos_que_usa_el_saldo(self):
        """Contrato: con NC + cheque que suman el total, el saldo del template es 0."""
        r = self.client.get(f'/app/api/detalle_dte_completo/{self.dte.id}/')
        self.assertEqual(r.status_code, 200)
        d = json.loads(r.content)
        metodos = sorted(p['metodo_pago'] for p in d['pagos'])
        self.assertEqual(metodos, ['Cheque', 'Nota de Crédito'])
        saldo_template = float(d['dte']['monto_con_iva']) - sum(float(p['monto']) for p in d['pagos'])
        self.assertEqual(saldo_template, 0)
