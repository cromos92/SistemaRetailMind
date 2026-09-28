"""
Unidad F3 — Frontend de Recepción DTE (vistas/modulo_compras/recepcion_dte.html).

La pantalla es casi toda JS inline; estos tests renderizan la página real y
fijan lo que se corrigió en el template para que no vuelva:

- B9-02: el motivo de rechazo y las observaciones (texto libre de OTRA
  sucursal) se escapan antes de insertarse como HTML; el alias de la sucursal
  que se inyecta en el JS va con escapejs.
- B9-08: la pestaña «Despachos recibidos» (antes «Recepcionados») usa un
  endpoint que exige recepcion_dte.puede_aprobar: sin ese permiso no se pinta.
- B9-03: los textos de rechazo describen lo que hace el backend (el stock
  vuelve al origen al rechazar).
- B9-14 / B9-15 / B9-11: paginación con d-none, título y contador en nodos
  separados, y el select de estado con EN_REGULARIZACION (lo fija un KPI).
"""
import re
from unittest import mock

from django.test import TestCase, Client

from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
)


def _permisos(aprobar=True):
    """PermisoRol.tiene_permiso concedido salvo, opcionalmente, puede_aprobar."""
    def _side_effect(*args, **kwargs):
        tipo = kwargs.get('tipo_permiso')
        if tipo is None and len(args) >= 3:
            tipo = args[2]
        if tipo == 'puede_aprobar':
            return aprobar
        return True
    return mock.patch('app.decorators.PermisoRol.tiene_permiso', side_effect=_side_effect)


class RecepcionDteTemplateF3Test(TestCase):
    URL = '/app/recepcion-dte/'

    def setUp(self):
        self.user = crear_usuario(username='f3cajero', rol='administrador')
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa, alias='F3SUC')
        crear_empresa_user(self.user, self.empresa, self.sucursal)
        self.client = Client()
        self.client.force_login(self.user)
        self._sesion('F3SUC')

    def _sesion(self, alias):
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session['alias'] = alias
        session.save()

    def _pagina(self, aprobar=True):
        with _permisos(aprobar=aprobar):
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode('utf-8')

    # ── B9-08 ─────────────────────────────────────────────────────────────
    def test_pestana_despachos_recibidos_requiere_aprobar(self):
        sin_aprobar = self._pagina(aprobar=False)
        self.assertNotIn('data-vista="recepcionados"', sin_aprobar)
        con_aprobar = self._pagina(aprobar=True)
        self.assertIn('data-vista="recepcionados"', con_aprobar)

    # ── B9-02 ─────────────────────────────────────────────────────────────
    def test_motivo_y_observaciones_se_escapan(self):
        html = self._pagina()
        # Las inserciones crudas que ejecutaban el <img onerror> ya no existen.
        self.assertNotIn('${fuenteMotivo.substring(0, 80)}', html)
        self.assertNotIn("${doc.observaciones || doc.referencias || ''}", html)
        self.assertNotIn('<em>${a.motivo}</em>', html)
        self.assertNotIn('title="${motivo}">${motivo.substring(0,60)}', html)
        self.assertIn('_escapeHtmlDte(fuenteMotivo.substring(0, 80))', html)
        self.assertIn("_escapeHtmlDte(doc.observaciones || doc.referencias || '')", html)
        # El rechazo ya no manda un `usuario` que el backend ignoraba.
        self.assertNotIn("usuario: '", html)

    def test_alias_de_sucursal_se_inyecta_escapado_en_el_js(self):
        self._sesion('X"</script><script>alert(1)</script>')
        html = self._pagina()
        m = re.search(r'const SUCURSAL_ACTUAL_ALIAS = "([^"\n]*)";', html)
        self.assertIsNotNone(m, 'no se encontró la constante del alias')
        self.assertNotIn('<', m.group(1))
        self.assertNotIn('</script><script>alert(1)', html)

    # ── B9-03 ─────────────────────────────────────────────────────────────
    def test_textos_de_rechazo_dicen_que_el_stock_vuelve_al_origen(self):
        html = self._pagina()
        self.assertNotIn('No mueve stock. El documento vuelve al emisor', html)
        self.assertNotIn('El stock no se modificara', html)
        self.assertNotIn('al <strong>Anular</strong> se libera el stock', html)
        self.assertIn('Devuelve la mercadería al stock del origen al instante', html)

    def test_textos_de_rechazo_sin_fecha_de_corte_inventada(self):
        # La devolución de stock al rechazar entró el 2026-08-04 (bee1f6db),
        # no en julio: los textos no fijan una fecha de corte.
        html = self._pagina()
        self.assertNotIn('jul-2026', html)
        self.assertIn('Los rechazos recientes devuelven el stock al origen al instante', html)

    def test_wizard_nc_distingue_total_y_parcial(self):
        # La NC parcial va a ajustar_traspaso, que devuelve stock al origen en
        # un rechazo antiguo (o se bloquea con 409 si ya volvió): el texto
        # "la NC no mueve stock" solo vale para la total.
        html = self._pagina()
        self.assertNotIn('La NC no mueve stock', html)
        self.assertNotIn('El stock no cambia al emitirla', html)
        self.assertIn('function _ncEsTotal()', html)
        self.assertIn('si ya volvió al rechazarse, el sistema bloqueará la NC parcial', html)

    # ── B9-01 ─────────────────────────────────────────────────────────────
    def test_firma_del_detalle_incluye_nc_pendiente(self):
        # Una NC total emitida con el modal abierto no cambia líneas ni
        # cantidades: solo cantidad_nc_pendiente delata el cambio.
        html = self._pagina()
        self.assertIn('${Number(d.cantidad_nc_pendiente) || 0}', html)

    # ── B9-11 ─────────────────────────────────────────────────────────────
    def test_kpis_se_piden_fuera_de_por_recibir(self):
        # ?vista=regularizar, el switch de alcance y las acciones en otras
        # pestañas dejaban los recuadros en 0 o vencidos.
        html = self._pagina()
        self.assertIn('function _asegurarKpisRecepcion()', html)
        # Llamado desde cambiarVistaUnificada, recargarVistaActiva y el switch.
        self.assertGreaterEqual(html.count('_asegurarKpisRecepcion();'), 3)

    # ── B9-14 / B9-15 / B9-11 ─────────────────────────────────────────────
    def test_paginacion_titulo_y_select_de_estado(self):
        html = self._pagina()
        for id_barra in ('paginationContainer', 'paginationRegularizar'):
            m = re.search(r'<div class="([^"]*)"[^>]*id="%s"' % id_barra, html)
            self.assertIsNotNone(m, id_barra)
            self.assertIn('d-none', m.group(1).split())
            self.assertNotRegex(html, r'id="%s" style="display: none;"' % id_barra)
        self.assertIn('<span id="tituloSeccionDocumentos">', html)
        self.assertIn('<option value="EN_REGULARIZACION">', html)
