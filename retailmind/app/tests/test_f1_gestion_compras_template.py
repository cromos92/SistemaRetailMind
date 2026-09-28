"""
Gestión Compras (gestionCompras.html) — guardas de regresión del frontend
(auditoría 2026-09, unidad F1).

La pantalla es casi todo JavaScript en línea; estas pruebas renderizan la
página real y verifican que no vuelvan los defectos corregidos:

- XSS almacenado (B4-06 / B1-11): existe el helper de escape y el resaltado
  de búsqueda lo usa (antes devolvía el nombre crudo al insertar la fila).
- Guardar Recepción bloqueado (B4-01): la validación de facturas usa
  `GestionCompras.urls` (el `urls` suelto no existe fuera del módulo).
- Editar Recepciones (B4-02): el token CSRF sale del `getCookie` global
  (`GestionCompras.getCookie` no existe y abortaba el envío).
- Un solo datalist global de facturas (B4-07), sin datalists por fila.
- Modo «quitar productos» (B4-13) visible solo con permiso de eliminar, y
  solo sobre filas visibles; lo mismo para borrar pendientes (F1-REV-02).
- La factura pre-rellenada no se reenvía con cantidad 0 (F1-REV-01).
- DTE del producto manual obligatorio en el cliente (B4-12).
- Importar Excel envía solo las filas válidas de la vista previa (B4-11).
- El handler de «Expandir todos» de Recepción ya no se engancha al botón
  del modal Vincular (B4-19).
"""
from django.test import Client, TestCase

from app.models import ModuloSistema, OpcionMenu, PermisoRol
from .factories import crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario

TIPOS = ('puede_ver', 'puede_crear', 'puede_editar', 'puede_eliminar', 'puede_exportar', 'puede_aprobar')


def _permiso(rol, codigo, **flags):
    modulo, _ = ModuloSistema.objects.get_or_create(codigo='f1_test', defaults={'nombre': 'F1 test'})
    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo=codigo, defaults={'modulo': modulo, 'nombre': codigo, 'activo': True})
    if not opcion.activo:
        opcion.activo = True
        opcion.save(update_fields=['activo'])
    valores = {t: False for t in TIPOS}
    valores.update(flags)
    PermisoRol.objects.update_or_create(rol=rol, opcion_menu=opcion, defaults=valores)


class GestionComprasTemplateTest(TestCase):
    URL = '/app/verGestionCompras/'

    def setUp(self):
        self.empresa = crear_empresa(nombre='Empresa F1')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='EDEL')
        self.maestro = crear_usuario(username='maestro_f1', rol='maestro')
        self.admin = crear_usuario(username='admin_f1', rol='administrador')
        for u in (self.maestro, self.admin):
            crear_empresa_user(u, self.empresa, self.sucursal)
        # Administrador: ve y opera Gestión Compras, pero SIN eliminar.
        _permiso('administrador', 'gestion_compras',
                 puede_ver=True, puede_crear=True, puede_editar=True, puede_exportar=True)

    def assertNotIn(self, fragmento, html, msg=None):  # noqa: N802
        # Sin volcar los ~800 KB de la página en el mensaje de error.
        if fragmento in html:
            self.fail(msg or f'{fragmento!r} aparece en la página')

    def assertIn(self, fragmento, html, msg=None):  # noqa: N802
        if fragmento not in html:
            self.fail(msg or f'{fragmento!r} no aparece en la página')

    def _html(self, usuario):
        c = Client()
        c.force_login(usuario)
        s = c.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s['alias'] = self.sucursal.alias
        s.save()
        r = c.get(self.URL)
        self.assertEqual(r.status_code, 200)
        return r.content.decode('utf-8')

    def test_helpers_de_escape_y_resaltado_seguro(self):
        html = self._html(self.maestro)
        self.assertIn('function escHtmlCompras(', html)
        self.assertIn('function resaltarTextoSeguroCompras(', html)
        # Los dos resaltadores delegan en la versión que escapa.
        self.assertIn('return resaltarTextoSeguroCompras(texto, busqueda);', html)
        # El resaltado viejo (devolvía el texto crudo) no vuelve.
        self.assertNotIn("if (!busqueda || !texto) return texto;", html)

    def test_validacion_de_factura_no_usa_urls_suelto(self):
        html = self._html(self.maestro)
        self.assertIn('GestionCompras.urls.validarFacturaProveedor', html)
        self.assertNotIn('url: urls.validarFacturaProveedor', html)

    def test_editar_recepciones_usa_getcookie_global(self):
        html = self._html(self.maestro)
        self.assertNotIn('GestionCompras.getCookie(', html)

    def test_un_solo_datalist_de_facturas(self):
        html = self._html(self.maestro)
        self.assertEqual(html.count('<datalist id="facturas-global-list">'), 1)
        self.assertNotIn('facturas-list-${', html)
        self.assertNotIn('facturas-producto-${', html)

    def test_importar_envia_solo_filas_validas(self):
        html = self._html(self.maestro)
        self.assertIn('function normalizarFilaCSV(', html)
        self.assertIn('const filas = filasValidasCSV.map(', html)

    def test_expandir_todos_sin_handler_de_recepcion(self):
        html = self._html(self.maestro)
        self.assertNotIn("$('#btnExpandirTodos').on('click'", html)
        # El botón del modal Vincular sigue con su onclick propio.
        self.assertIn('onclick="vincRetro_toggleColapsoTodos()"', html)

    def test_modo_quitar_productos_solo_con_permiso_eliminar(self):
        self.assertIn('id="btnToggleEliminar"', self._html(self.maestro))
        html_admin = self._html(self.admin)
        self.assertNotIn('id="btnToggleEliminar"', html_admin)
        self.assertNotIn('id="btnEliminarSeleccionados"', html_admin)
        self.assertIn('const PUEDE_ELIMINAR_COMPRA = false;', html_admin)

    def test_quitar_productos_solo_alcanza_filas_visibles(self):
        # B4-13 (revisión): «Seleccionar todos» y «Eliminar seleccionados» no
        # tocan productos ocultos por los filtros Estado/Sucursal del modal.
        html = self._html(self.maestro)
        self.assertIn('function checksEliminarVisibles(', html)
        self.assertIn("const $checks = checksEliminarVisibles().filter(':checked');", html)
        self.assertNotIn("$('.check-eliminar-producto').prop('checked', checked);", html)

    def test_eliminar_pendientes_solo_con_permiso_eliminar(self):
        # F1-REV-02: borrar líneas pendientes de la compra exige puede_eliminar.
        html_maestro = self._html(self.maestro)
        self.assertIn('id="btnEliminarPendientesSeleccionados"', html_maestro)
        self.assertIn('id="checkAllPendientes"', html_maestro)
        html_admin = self._html(self.admin)
        self.assertNotIn('id="btnEliminarPendientesSeleccionados"', html_admin)
        self.assertNotIn('id="checkAllPendientes"', html_admin)
        # Las casillas por línea y la papelera de Editar Recepciones dependen del mismo permiso.
        self.assertIn('(puedeEliminar && PUEDE_ELIMINAR_COMPRA)', html_admin)
        self.assertIn('(yaCreada || !PUEDE_ELIMINAR_COMPRA)', html_admin)

    def test_factura_prerellenada_sin_cambio_no_se_envia_con_cantidad_cero(self):
        # F1-REV-01: la factura con que se pinta la talla es una sugerencia; con
        # cantidad 0 solo se envía si el usuario la cambió (antes el servidor
        # se la ponía a la recepción SIN DTE de tallas ya creadas).
        html = self._html(self.maestro)
        self.assertIn('data-factura-inicial="${escHtmlCompras(selectedFacturaValue)}"', html)
        self.assertIn(
            "if (cantidad <= 0 && (!facturaNumero || facturaNumero === String(info.facturaInicial || '').trim())) return;",
            html)

    def test_dte_manual_obligatorio_antes_de_enviar(self):
        # B4-12 (revisión): el servidor sigue exigiendo el DTE; el cliente lo
        # pide antes de enviar y no lo rotula como opcional.
        html = self._html(self.maestro)
        self.assertNotIn('DTE de referencia (opcional)', html)
        self.assertIn('Debes seleccionar un DTE de referencia para agregar el producto.', html)
        self.assertNotIn("dte_id: $('#selectDteManual').val() || null", html)

    def test_autorecarga_de_facturas_no_suma_la_lista_inicial(self):
        # B4-07 (revisión): /app/facturas_pendientes/ no filtra NC ni facturas
        # consumidas; la auto-recarga solo suma lo que aparece después de su
        # primera respuesta por proveedor.
        html = self._html(self.maestro)
        self.assertIn('function actualizarDatalistsFacturas(facturas, proveedorId)', html)
        self.assertIn('actualizarDatalistsFacturas(facturas, proveedorPedido);', html)
