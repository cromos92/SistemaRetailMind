"""
F2 — Pantalla «Gestión Documentos Compras» (gestionDteCompras.html).

La lógica corregida vive en el JS de la plantilla (elegibilidad de Pago Masivo
sin distinguir mayúsculas, candado contra doble envío, filtros, fechas locales);
aquí se cubre lo que el servidor entrega a esa pantalla:

1. La plantilla renderiza con los ganchos nuevos (botón con id para el candado
   del pago masivo, filtro «Al Día», helpers de escape/fecha local) y sin el
   `$('.alert-info').html(...)` global que pisaba otros avisos (B5-13).
2. El enlace a la importación XML (B11-15) solo se ofrece a quien puede crear en
   `gestion_dte_compras` (la vista exige ese mismo permiso).
3. Contrato de /app/cargarDteCompra/: los campos que la fila usa para decidir la
   elegibilidad y el monto de Pago Masivo siguen llegando, con estado_pago tal
   cual está en la BD (el front lo normaliza).
"""
import json
from datetime import timedelta

from django.template.loader import get_template
from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import Dte, Dte_Detalle_Pago, ModuloSistema, OpcionMenu, PermisoRol
from .factories import crear_empresa, crear_empresa_user, crear_usuario, setup_entorno_completo


STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'
TIPOS = ('puede_ver', 'puede_crear', 'puede_editar', 'puede_eliminar', 'puede_exportar', 'puede_aprobar')
URL_XML = '/app/compras/importar-xml-dte/'


def _permiso(rol, codigo, **flags):
    mod, _ = ModuloSistema.objects.get_or_create(codigo='documentos', defaults={'nombre': 'documentos'})
    op, _ = OpcionMenu.objects.get_or_create(codigo=codigo, defaults={'modulo': mod, 'nombre': codigo})
    if not op.activo:
        op.activo = True
        op.save(update_fields=['activo'])
    valores = {t: False for t in TIPOS}
    valores.update(flags)
    PermisoRol.objects.update_or_create(rol=rol, opcion_menu=op, defaults=valores)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class GestionDteComprasPantallaTest(TestCase):
    def setUp(self):
        env = setup_entorno_completo()
        self.empresa = env['empresa']
        self.sucursal = env['sucursal']
        self.proveedor = crear_empresa(nombre='Proveedor F2', rut='76.222.222-2', esProveedor=True)

        self.maestro = crear_usuario(username='maestro_f2', rol='maestro')
        self.solo_ver = crear_usuario(username='jefe_f2', rol='jefe_local')
        self.con_crear = crear_usuario(username='admcion_f2', rol='administracion')
        for u in (self.maestro, self.solo_ver, self.con_crear):
            crear_empresa_user(u, self.empresa, self.sucursal)

        _permiso('jefe_local', 'gestion_dte_compras', puede_ver=True)
        _permiso('administracion', 'gestion_dte_compras', puede_ver=True, puede_crear=True)

    def _login(self, usuario):
        self.client.force_login(usuario)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _pagina(self, usuario):
        self._login(usuario)
        r = self.client.get('/app/verGestionDteCompras/')
        self.assertEqual(r.status_code, 200)
        return r.content.decode('utf-8')

    def _dte(self, numero, estado_pago='Pendiente', monto=119000):
        hoy = timezone.localdate()
        return Dte.objects.create(
            emisor=self.proveedor, receptor=self.empresa, numero_documento=numero,
            tipo_documento='FACTURA ELECTRONICA', monto_con_iva=monto, monto_neto=100000,
            descuento=0, estado_pago=estado_pago, estado_dte='ACEPTADO',
            responsable='test', fecha_emision=hoy, fecha_recepcion=hoy,
            fecha_vencimiento=hoy + timedelta(days=30), diasCredito=30,
            bultos=0, unidades_productos=1, tipo_transaccion='COMPRA', sucursal=self.sucursal,
        )

    # ---------- 1. plantilla ----------

    def test_plantilla_trae_ganchos_de_los_arreglos(self):
        html = self._pagina(self.maestro)
        # B5-02: candado del pago masivo
        self.assertIn('id="btnProcesarPagoMasivo"', html)
        self.assertIn('let pagoMasivoEnCurso = false;', html)
        # B5-05: cargarDteCompra aún no tiene la rama 'al_dia': la tarjeta «Al Día»
        # abre todos los pendientes con su chip real (no un chip «Al Día» sobre
        # facturas vencidas) y ya no la lista sin filtro (pagados incluidos).
        self.assertNotIn('onclick="cargarDTEsSinFiltro()"', html)
        self.assertNotIn('id="qfAlDia"', html)
        self.assertNotIn("cargarDTEs(1, '', 'al_dia')", html)
        self.assertNotIn('function filtrarAlDia(', html)
        self.assertIn('kpi-card--al-dia card h-100" onclick="filtrarPendientes()"', html)
        # B5-02/B5-06/B5-14: helpers comunes
        for helper in ('function escHtml(', 'function fechaLocalISO(', 'function estadoPagoKey(',
                       'function saldoFilaDte(', 'function incidenciasActivasDte('):
            self.assertIn(helper, html)
        # B5-12: «Ver descartados» con listener propio
        self.assertIn("$('#toggleDescartados').on('change'", html)

        # Sobre la fuente de la plantilla (el layout incluido tiene su propio JS):
        fuente = get_template('vistas/modulo_compras/gestionDteCompras.html').template.source
        # B5-14: ninguna fecha por defecto en UTC
        self.assertNotIn('toISOString().slice(0, 10)', fuente)
        self.assertNotIn("toISOString().split('T')[0]", fuente)
        # B5-13: sin escritura global sobre .alert-info
        self.assertNotIn("$('.alert-info').html(", fuente)
        # B5-14: el historial de pagos del modal Editar lee la fecha como local
        self.assertNotIn('new Date(pago.fecha_pago)', fuente)
        # B5-02: el 403 del middleware trae error:true (booleano), no un texto
        self.assertIn("typeof res.error === 'string'", fuente)

    def test_filtros_kpi_usan_el_universo_del_kpi(self):
        """B5-05: las tarjetas filtran desde el 'desde' de la API y por fecha
        de EMISIÓN, como el KPI (antes: 1-ene del año por recepción)."""
        fuente = get_template('vistas/modulo_compras/gestionDteCompras.html').template.source
        self.assertIn('function aplicarRangoKpi(', fuente)
        self.assertIn('kpiDesdeISO = response.desde;', fuente)
        self.assertIn("$('#tipoFecha').val('emision');", fuente)
        for filtro in ('filtrarPendientes', 'filtrarVencidos', 'filtrarPorVencer'):
            inicio = fuente.index(f'function {filtro}(')
            cuerpo = fuente[inicio:fuente.index('}', inicio)]
            self.assertIn('aplicarRangoKpi();', cuerpo, filtro)
        # «Quitar filtro» vuelve a la vista inicial por recepción
        inicio = fuente.index('function limpiarFiltrosRapidos(')
        self.assertIn("$('#tipoFecha').val('recepcion');", fuente[inicio:inicio + 800])

    def test_textos_de_usuario_se_escapan(self):
        """Voucher, incidencias y nombres de proveedor van por escHtml."""
        fuente = get_template('vistas/modulo_compras/gestionDteCompras.html').template.source
        for crudo in ("${pago.voucher", '${inc.descripcion', '${info.factura_proveedor}',
                      '${nc.proveedor', '<td>${doc.proveedor}', '${dp.proveedor}',
                      '${emp.nombre}', '${pago.metodo_pago}'):
            self.assertNotIn(crudo, fuente, crudo)
        self.assertIn("${escHtml(pago.voucher ?? '-')}", fuente)
        self.assertIn('${escHtml(inc.descripcion)}', fuente)
        self.assertIn('<td>${escHtml(doc.proveedor)}</td>', fuente)
        self.assertIn('data-buscar="${escHtml(filtro)}"', fuente)

    def test_alta_precargada_desde_la_carga_por_pdf(self):
        """El enlace «Registrar esta factura» (carga_factura/web.py) apunta a esta
        pantalla con ?nuevo=1&folio&rut&fecha&neto: la plantilla lo lee."""
        from app.services.carga_factura.web import datos_para_registrar
        url = datos_para_registrar({'folio': 123, 'proveedor_rut': '76.222.222-2',
                                    'fecha_emision': '2026-07-01', 'total_neto': 1000})['url']
        self.assertTrue(url.startswith('/app/verGestionDteCompras/?'), url)
        fuente = get_template('vistas/modulo_compras/gestionDteCompras.html').template.source
        self.assertIn('function abrirNuevoDteDesdeUrl(', fuente)
        self.assertIn('abrirNuevoDteDesdeUrl();', fuente)
        for param in ("params.get('nuevo')", "params.get('folio')", "params.get('rut')",
                      "params.get('fecha')", "params.get('neto')"):
            self.assertIn(param, fuente)
        # El proveedor se elige por RUT: las opciones lo llevan en data-rut
        self.assertIn('data-rut="${escHtml(emp.rut)}"', fuente)
        # La pantalla renderiza con ese querystring
        html = self._pagina_url(self.maestro, url)
        self.assertIn('id="modalNuevoDTE"', html)

    def _pagina_url(self, usuario, url):
        self._login(usuario)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        return r.content.decode('utf-8')

    def test_kpi_entrega_desde_y_la_lista_lo_respeta(self):
        """Contrato que usan las tarjetas: la API del KPI entrega 'desde' y una
        factura vencida emitida el año anterior (dentro del universo del KPI)
        aparece en la lista filtrada con ese mismo rango por emisión."""
        self._login(self.maestro)
        r = self.client.get('/app/api/resumen-pendientes-anio/')
        self.assertEqual(r.status_code, 200, r.content)
        datos = r.json()
        self.assertTrue(datos['success'])
        desde = datos.get('desde')
        self.assertRegex(desde or '', r'^\d{4}-\d{2}-\d{2}$')

        hoy = timezone.localdate()
        from datetime import date
        emision = max(date.fromisoformat(desde), date(hoy.year - 1, 6, 1))
        vieja = self._dte(7101)
        Dte.objects.filter(pk=vieja.pk).update(
            fecha_emision=emision, fecha_recepcion=emision,
            fecha_vencimiento=emision + timedelta(days=30))

        r = self.client.get('/app/api/resumen-pendientes-anio/')
        self.assertGreaterEqual(r.json()['vencidos'], 1)

        r = self.client.post('/app/cargarDteCompra/', data=json.dumps({
            'fecha_inicio': desde, 'fecha_fin': date(hoy.year, 12, 31).isoformat(),
            'tipo_fecha': 'emision', 'page': 1, 'page_size': 20, 'search': '',
            'tipo_documento': '', 'filtro_vencimiento': 'vencidos', 'solo_incidencias': False,
            'incluir_descartados': False,
        }), content_type='application/json', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn(vieja.id, [f['id'] for f in r.json()['items']])

    # ---------- 2. enlace XML ----------

    def test_enlace_xml_visible_para_maestro(self):
        self.assertIn(f'href="{URL_XML}"', self._pagina(self.maestro))

    def test_enlace_xml_visible_con_permiso_de_crear(self):
        self.assertIn(f'href="{URL_XML}"', self._pagina(self.con_crear))

    def test_enlace_xml_oculto_si_solo_puede_ver(self):
        self.assertNotIn(f'href="{URL_XML}"', self._pagina(self.solo_ver))

    # ---------- 3. contrato de cargarDteCompra ----------

    def test_listado_entrega_campos_que_usa_pago_masivo(self):
        pagada = self._dte(7001, estado_pago='PAGADO')
        Dte_Detalle_Pago.objects.create(dte=pagada, metodo_pago='Transferencia', voucher='T-1',
                                        monto=119000, fecha_pago=timezone.localdate())
        pendiente = self._dte(7002)

        self._login(self.maestro)
        hoy = timezone.localdate()
        r = self.client.post('/app/cargarDteCompra/', data=json.dumps({
            'fecha_inicio': (hoy - timedelta(days=5)).isoformat(), 'fecha_fin': hoy.isoformat(),
            'tipo_fecha': 'recepcion', 'page': 1, 'page_size': 20, 'search': '',
            'tipo_documento': '', 'filtro_vencimiento': '', 'solo_incidencias': False,
            'incluir_descartados': False,
        }), content_type='application/json', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content)
        filas = {f['id']: f for f in r.json()['items']}
        self.assertIn(pagada.id, filas)
        self.assertIn(pendiente.id, filas)

        usados = ('tipo', 'estado', 'estado_pago', 'monto_con_iva', 'notas_credito', 'compensaciones',
                  'incidencias_pendientes', 'requiere_factura', 'tiene_factura_anexada',
                  'descartado', 'nombre', 'rut', 'numero_documento', 'fecha_emision')
        for campo in usados:
            self.assertIn(campo, filas[pendiente.id], campo)
        # El front compara sin distinguir mayúsculas: 'PAGADO' debe llegar tal cual
        # (antes solo 'Pagado' dejaba de ofrecerse para Pago Masivo).
        self.assertEqual(filas[pagada.id]['estado_pago'].upper(), 'PAGADO')
        self.assertEqual(filas[pendiente.id]['estado_pago'].upper(), 'PENDIENTE')
