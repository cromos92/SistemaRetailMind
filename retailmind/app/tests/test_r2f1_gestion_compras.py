"""
Gestión Compras (gestionCompras.html) — ronda 2, unidad R2F1: el frontend
consume los contratos nuevos del backend.

Dos grupos de pruebas:

1. Render de la página: el JS en línea lee los campos nuevos y no vuelven
   los defectos corregidos (etiqueta «Finalizada» para compras sin fechas,
   «Por crear (N)» contando filas, eliminar sin mode=check, exportar con
   window.location, 409 de guardar_recepcion sin confirmación, etc.).
2. Contratos: las respuestas reales de las vistas traen exactamente los
   campos que ese JS lee (si el backend cambia la forma, esto avisa antes de
   que la pantalla se rompa en silencio).

La verificación funcional (clics, Swal, payloads) está en el smoke de Chrome
headless de la unidad (scratchpad r2f1/smoke.py), no aquí.

Ejecutar (BD de test aislada):
    python manage.py test app.tests.test_r2f1_gestion_compras --keepdb
"""
import json

from django.test import TestCase
from django.utils import timezone

from app.models import (
    Compras, Compras_Producto, Compras_Producto_Talla, Dte, Productos_Recepcionados,
)
from .factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla, crear_sucursal,
    crear_usuario, otorgar_ver_pantalla,
)


def _rut(numero):
    suma, mult = 0, 2
    for d in reversed(str(numero)):
        suma += int(d) * mult
        mult = mult + 1 if mult < 7 else 2
    resto = 11 - (suma % 11)
    dv = '0' if resto == 11 else 'K' if resto == 10 else str(resto)
    return f'{numero}-{dv}'


class _BaseR2F1(TestCase):
    ROL = 'administrador'

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa(nombre='Nosotros R2F1', rut=_rut(76420001))
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='R2F1-SUC')
        cls.proveedor = crear_empresa(nombre='Proveedor R2F1', rut=_rut(77420001), esProveedor=True)
        cls.otro_proveedor = crear_empresa(nombre='Otro <b>Prov</b>', rut=_rut(77420002), esProveedor=True)
        cls.maestro = crear_usuario(username='r2f1_maestro', rol='maestro')
        cls.user = crear_usuario(username='r2f1_admin', rol=cls.ROL)
        for u in (cls.maestro, cls.user):
            crear_empresa_user(u, cls.empresa, cls.sucursal)
        otorgar_ver_pantalla(cls.ROL, 'gestion_compras', puede_crear=True, puede_editar=True,
                             puede_eliminar=True, puede_exportar=True)

    def _login(self, usuario):
        self.client.force_login(usuario)
        s = self.client.session
        s['idEmpresaActual'] = self.empresa.id
        s['idSucursalActual'] = self.sucursal.id
        s['alias'] = self.sucursal.alias
        s['nombreUsuario'] = usuario.username
        s.save()

    def setUp(self):
        self._login(self.user)
        self.hoy = timezone.localdate()

    def _post(self, url, data):
        return self.client.post(url, data=json.dumps(data), content_type='application/json')

    def _dte(self, numero, emisor, unidades=0, tipo='FACTURA ELECTRONICA', tipo_transaccion='COMPRA'):
        return Dte.objects.create(
            emisor=emisor, receptor=self.empresa, numero_documento=numero,
            tipo_documento=tipo, monto_con_iva=119000, monto_neto=100000, descuento=0,
            estado_pago='Pendiente', estado_dte='ACEPTADO', responsable='test',
            fecha_emision=self.hoy, fecha_vencimiento=self.hoy, diasCredito=0, bultos=0,
            unidades_productos=unidades, tipo_transaccion=tipo_transaccion, sucursal=self.sucursal,
        )

    def _compra(self, nombre='OC R2F1', **extra):
        return Compras.objects.create(
            empresa=self.proveedor, nombre=nombre, correlativo=1, responsable='tester',
            temporada='Invierno', fecha=self.hoy, estado='ACTIVA', **extra)

    def _linea(self, compra, nombre='ART-1', tallas=(('40', 5),), costo=1000):
        cp = Compras_Producto.objects.create(
            compras=compra, nombre=nombre, atributo1='MARCA', atributo2='NEGRO',
            atributo3='HOMBRE', atributo4='', costo=costo, precioSugerido=costo * 2)
        return cp, [Compras_Producto_Talla.objects.create(compra_producto=cp, stock=s, talla=t)
                    for t, s in tallas]

    def _recepcion(self, cpt, cantidad, dte=None, producto_talla=None):
        return Productos_Recepcionados.objects.create(
            compra_producto_talla=cpt, stockArribado=cantidad, dte=dte, producto_talla=producto_talla)


class GestionComprasRenderR2F1Test(_BaseR2F1):
    """El JS de la página consume los campos nuevos (render real)."""

    URL = '/app/verGestionCompras/'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls._html_cache = None

    def assertIn(self, fragmento, html, msg=None):  # noqa: N802 (sin volcar ~800 KB)
        if fragmento not in html:
            self.fail(msg or f'{fragmento!r} no aparece en la página')

    def assertNotIn(self, fragmento, html, msg=None):  # noqa: N802
        if fragmento in html:
            self.fail(msg or f'{fragmento!r} aparece en la página')

    def _html(self):
        self._login(self.maestro)
        r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 200)
        return r.content.decode('utf-8')

    def test_grilla_usa_estado_temporada(self):
        # R2F1-5 / B1-13: «Finalizada» salía para compras sin fechas, por
        # iniciar y en su último día.
        html = self._html()
        self.assertIn('function badgeTemporadaCompra(compra)', html)
        for estado in ("case 'sin_fechas':", "case 'por_iniciar':", "case 'en_curso':", "case 'finalizada':"):
            self.assertIn(estado, html)
        self.assertIn('compra.dias_estado_temporada', html)
        self.assertIn('const diasBadge = badgeTemporadaCompra(compra);', html)
        # El cálculo viejo en línea (todo lo <= 0 era «Finalizada») ya no está.
        self.assertNotIn("${compra.dias_temporada > 0 ? escHtmlCompras(compra.dias_temporada) + ' días' : 'Finalizada'}", html)

    def test_grilla_por_crear_en_articulos_y_unidades(self):
        # R2F1-5 / B1-13 / B4-16: «Por crear (19)» eran 19 tallas de 3 artículos.
        html = self._html()
        self.assertIn('parseInt(compra.productos_por_crear, 10)', html)
        self.assertIn('parseInt(compra.unidades_por_crear, 10)', html)
        self.assertIn('Por crear (${textoPorCrear})', html)
        self.assertNotIn('Por crear (${porCrear})', html)

    def test_eliminar_compra_pide_mode_check_primero(self):
        # R2F1-6: antes confirmaba a ciegas y mandaba force:true.
        html = self._html()
        self.assertIn("JSON.stringify({ compra_id: compraId, mode: 'check' })", html)
        self.assertIn('function motivoBloqueoEliminarCompra(info)', html)
        self.assertIn('info.recepciones_creadas', html)
        self.assertIn('abrirModalEditarRecepcionCompra(compraId, info.nombre || compraNombre)', html)

    def test_editar_recepciones_bloquea_filas_creadas(self):
        # R2F1-6 (segunda parte): input deshabilitado y sin papelera si rec.creado.
        html = self._html()
        self.assertIn('const yaCreada = !!(rec.creado || rec.producto_talla_id);', html)
        self.assertIn("${yaCreada ? 'disabled title=", html)
        self.assertIn('(yaCreada || !PUEDE_ELIMINAR_COMPRA)', html)
        self.assertIn("if ($(this).data('creada')) return;", html)

    def test_guardar_recepcion_maneja_400_y_409(self):
        # R2F1-1: 400 con el motivo y 409 con confirmación y reenvío.
        html = self._html()
        self.assertIn('xhr.status === 409 && resp.needs_confirmation && !confirmarExcesoFactura', html)
        self.assertIn('payloadRecepcion.confirmar_exceso_factura = true', html)
        self.assertIn('continuarGuardarRecepcion(datos, true)', html)
        self.assertIn("swalTextoCompras('No se guardó la recepción', errorXhrCompras(xhr,", html)
        # El «MODO B» (asignación masiva de sucursal) se retiró: era el otro
        # consumidor de guardar_recepcion y no manejaba ni el 400 ni el 409.
        self.assertNotIn('Rellenar cantidad máx. y aplicar', html)
        self.assertEqual(html.count('url: GestionCompras.urls.guardarRecepcion'), 1)

    def test_facturas_desde_la_raiz_y_reparto_por_pendiente(self):
        # R2F1-2 / R2F1-3 / R2F1-8.
        html = self._html()
        self.assertIn('let facturasProveedor = data.facturas_proveedor;', html)
        self.assertIn("${f.fecha ? ' - ' + f.fecha : ''}", html)
        self.assertIn("data-pendiente-por-id='${escHtmlCompras(pendientePorIdStr)}'", html)
        self.assertIn('repartirCantidadRecepcion(cantidad, ids, info.pendientePorId)', html)
        self.assertIn('data-factura-inicial="${escHtmlCompras(selectedFacturaValue)}"', html)

    def test_validar_factura_muestra_coincidencia_escapada(self):
        # R2F1-4.
        html = self._html()
        self.assertIn('const doc = response.factura || response.coincidencia || null;', html)
        self.assertIn('${escHtmlCompras(doc.emisor_nombre)}', html)
        self.assertNotIn('${response.factura.emisor_nombre}', html)

    def test_exportar_con_fetch_y_error_json(self):
        # R2F1-7 / B1-05.
        html = self._html()
        self.assertIn("fetch(`${urlBase}?${params.toString()}`, { credentials: 'same-origin' })", html)
        self.assertIn("tipo.indexOf('application/json') !== -1", html)
        self.assertNotIn("window.location.href = '/app/api/exportar-compras", html)
        self.assertNotIn('El archivo se ha descargado correctamente', html)


class ContratosGestionComprasR2F1Test(_BaseR2F1):
    """Las vistas devuelven los campos que lee gestionCompras.html."""

    def test_obtener_compras_estado_temporada_y_por_crear(self):
        sin_fechas = self._compra(nombre='Compra Manual - R2F1')
        en_curso = self._compra(
            nombre='OC en curso', fechaInicioTemporada=self.hoy - timezone.timedelta(days=10),
            fechaTerminoTemporada=self.hoy)
        _cp1, (t1, t2) = self._linea(en_curso, 'ART-A', (('40', 5), ('41', 5)))
        _cp2, (t3,) = self._linea(en_curso, 'ART-B', (('42', 8),))
        for cpt, n in ((t1, 2), (t2, 3), (t3, 4)):
            self._recepcion(cpt, n)
        r = self.client.get(f'/app/obtener_compras/?anio={self.hoy.year}&page=1&page_size=100')
        self.assertEqual(r.status_code, 200)
        filas = {c['id']: c for c in r.json()['compras']}
        for campo in ('estado_temporada', 'dias_estado_temporada', 'productos_por_crear',
                      'unidades_por_crear', 'pendientes_crear', 'dias_temporada'):
            self.assertIn(campo, filas[sin_fechas.id])
        self.assertEqual(filas[sin_fechas.id]['estado_temporada'], 'sin_fechas')
        self.assertIsNone(filas[sin_fechas.id]['dias_estado_temporada'])
        fila = filas[en_curso.id]
        self.assertEqual((fila['estado_temporada'], fila['dias_estado_temporada']), ('en_curso', 0))
        # 3 filas de recepción (pendientes_crear), 2 artículos, 9 unidades.
        self.assertEqual((fila['pendientes_crear'], fila['productos_por_crear'], fila['unidades_por_crear']),
                         (3, 2, 9))

    def test_eliminar_compra_check_trae_lo_que_usa_el_motivo(self):
        compra = self._compra()
        _cp, (t1, t2) = self._linea(compra, tallas=(('40', 5), ('41', 5)))
        _producto, pt = crear_producto_con_talla(self.sucursal, articulo='ART R2F1', talla='40')
        self._recepcion(t1, 2, producto_talla=pt)
        self._recepcion(t2, 3)
        r = self._post('/app/eliminar_compra/', {'compra_id': compra.id, 'mode': 'check'})
        self.assertEqual(r.status_code, 200)
        info = r.json()['info']
        for campo in ('nombre', 'total_productos', 'total_unidades', 'total_recepcionado',
                      'recepciones_creadas', 'unidades_ingresadas', 'unidades_pendientes_crear'):
            self.assertIn(campo, info)
        self.assertEqual((info['total_recepcionado'], info['recepciones_creadas'],
                          info['unidades_ingresadas'], info['unidades_pendientes_crear']), (5, 1, 2, 3))
        compra.refresh_from_db()
        self.assertEqual(compra.estado, 'ACTIVA')  # mode=check no escribe
        # El delete posterior (force) igual bloquea: el front no ofrece forzar.
        r = self._post('/app/eliminar_compra/', {'compra_id': compra.id, 'mode': 'delete', 'force': True})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.json().get('blocked'))

    def test_recepcionar_facturas_en_la_raiz_y_pendiente_por_id(self):
        compra = self._compra()
        # Mismo artículo y talla en dos líneas: la UI las agrupa en una fila.
        _cp1, (a,) = self._linea(compra, 'ART-G', (('40', 3),))
        _cp2, (b,) = self._linea(compra, 'ART-G', (('40', 4),))
        self._recepcion(a, 3)
        factura = self._dte(9101, self.proveedor)
        r = self._post('/app/compra/recepcionar/', {'compra_id': compra.id, 'page': 1, 'page_size': 50,
                                                    'search': '', 'vista_agrupada': True})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        f = [x for x in d['facturas_proveedor'] if x['id'] == factura.id]
        self.assertEqual(len(f), 1)
        self.assertEqual(set(f[0]), {'id', 'numero', 'tipo', 'fecha', 'monto'})
        talla = d['items'][0]['tallas'][0]
        self.assertNotIn('facturas', talla)
        self.assertEqual(sorted(talla['compra_producto_talla_ids']), sorted([a.id, b.id]))
        self.assertEqual(talla['pendiente_por_id'], {str(a.id): 0, str(b.id): 4})

    def test_guardar_recepcion_400_y_409_con_los_campos_del_front(self):
        compra = self._compra()
        _cp, (cpt,) = self._linea(compra, tallas=(('40', 5),))
        factura = self._dte(9201, self.proveedor, unidades=5)
        otra = self._compra(nombre='OC duplicada')
        _cp2, (cpt_o,) = self._linea(otra, tallas=(('40', 5),))
        self._recepcion(cpt_o, 5, dte=factura)
        url = '/app/guardar_recepcion/'
        fila = {'compra_producto_talla_id': cpt.id, 'recepcionado': 2, 'factura_id': factura.id}
        r = self._post(url, {'compra_id': compra.id, 'recepciones': [fila], 'sucursal_destino_id': None})
        self.assertEqual(r.status_code, 409)
        d = r.json()
        self.assertTrue(d['needs_confirmation'])
        self.assertTrue(d['error'])
        self.assertEqual(d['facturas_reutilizadas'][0]['compras'], [otra.id])
        r = self._post(url, {'compra_id': compra.id, 'recepciones': [fila], 'sucursal_destino_id': None,
                             'confirmar_exceso_factura': True})
        self.assertEqual(r.status_code, 200)
        r = self._post(url, {'compra_id': compra.id, 'recepciones': [
            {'compra_producto_talla_id': cpt.id, 'recepcionado': 99, 'factura_id': None}]})
        self.assertEqual(r.status_code, 400)
        self.assertIn('pendiente', r.json()['error'])

    def test_validar_factura_otro_emisor_trae_coincidencia_sin_id(self):
        self._dte(9301, self.otro_proveedor)
        r = self.client.get('/app/validar_factura_proveedor/', {'numero': '9301', 'proveedor_id': self.proveedor.id})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertFalse(d['existe'])
        self.assertFalse(d['tipo_distinto'])
        co = d['coincidencia']
        self.assertNotIn('id', co)
        for campo in ('numero', 'emisor_nombre', 'fecha', 'monto', 'tipo_transaccion'):
            self.assertIn(campo, co)
        self.assertEqual(co['emisor_nombre'], 'Otro <b>Prov</b>')  # el front lo escapa

    def test_exportar_sin_compras_responde_json_y_con_compras_archivo(self):
        r = self.client.get('/app/api/exportar-compras-csv/', {'anio': 2001})
        self.assertIn('application/json', r['Content-Type'])
        self.assertTrue(r.json().get('error'))
        compra = self._compra()
        self._linea(compra)
        r = self.client.get('/app/api/exportar-compras-csv/', {'anio': self.hoy.year})
        self.assertEqual(r.status_code, 200)
        self.assertIn('text/csv', r['Content-Type'])
        self.assertIn(f'filename="compras_{self.hoy.year}.csv"', r['Content-Disposition'])
