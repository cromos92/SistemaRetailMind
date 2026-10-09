"""
Gestión de Inventarios en MODO REVISIÓN: quien solo tiene Ver (el jefe de local)
revisa las tomas de su tienda en unidades —lo contado, las diferencias y los
faltantes— sin costos, precios, análisis valorizado ni Excel, y sin poder mover
la toma. Fusionar Duplicados (comparte la opción y mueve stock) exige Editar.

Correr (sin tocar la BD del .env):
    DATABASE_URL='sqlite://:memory:' python manage.py test app.tests.test_toma_inventario_revision
"""
import json
import re

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from app.models import Movimientos_Producto, OpcionMenu, PermisoRol, Producto_Talla, TomaInventario
from .factories import crear_empresa_user, crear_sucursal, crear_usuario
from .test_toma_inventario_informe import BaseTomaContadaAnoche

# Por palabra: «venta» también está dentro de «inventario»
CLAVE_PLATA = re.compile(r'(^|_)(costo|venta|valor|precio|pvp|fifo|ttcosto|ttpvp)\d*(_|$)')


def _sin_plata(fila):
    """Claves con plata que quedaron en la fila (debe ser [] en modo revisión)."""
    return [k for k in fila if CLAVE_PLATA.search(k)]


class ModoRevisionJefeLocalTest(BaseTomaContadaAnoche):

    def setUp(self):
        super().setUp()
        opcion = OpcionMenu.objects.get(codigo='gestion_inventarios')
        PermisoRol.objects.create(rol='jefe_local', opcion_menu=opcion, puede_ver=True, puede_crear=False,
                                  puede_editar=False, puede_eliminar=False, puede_exportar=False, puede_aprobar=False)
        self.jefe = crear_usuario(username='jefe_pao', rol='jefe_local')
        crear_empresa_user(self.jefe, self.empresa, self.sucursal)

        # Toma de la tienda del jefe, cargada por el administrador: A sobra, B falta, C cuadra
        self.pt_a = self._pt('SOBRA', 9600001, 1)
        self.pt_b = self._pt('FALTA', 9600002, 3)
        self.pt_c = self._pt('EXACTO', 9600003, 2)
        self.toma = self._crear_toma_con_pistola('sku,stock\n9600001,2\n9600002,1\n9600003,2\n')

    def _crear_toma_con_pistola(self, contenido, sucursal=None):
        data = self.client.post(reverse('api_crear_inventario'), data=json.dumps({
            'nombre': 'Completo', 'tipo_inventario': 'COMPLETO', 'conteo_tienda_cerrada': True,
            'sucursal_id': (sucursal or self.sucursal).id,
            'fecha_corte': self.corte.strftime('%Y-%m-%dT%H:%M'), 'filtros': {'solo_con_stock': True},
        }), content_type='application/json').json()
        self.assertTrue(data['success'], data)
        toma = TomaInventario.objects.get(id=data['inventario_id'])
        self.assertTrue(self._importar_pistola(toma.id, contenido)['success'])
        return toma

    def _como(self, usuario):
        self.client.force_login(usuario)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

    def _get(self, nombre, *args, **params):
        return self.client.get(reverse(nombre, args=args), params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_jefe_revisa_en_unidades_sin_costos(self):
        self._como(self.jefe)

        pagina = self.client.get(reverse('gestion_inventarios'))
        self.assertEqual(pagina.status_code, 200)
        self.assertTrue(pagina.context['solo_revision'])
        self.assertFalse(pagina.context['puede_crear'])

        listado = self._get('api_obtener_inventarios').json()
        self.assertTrue(listado['success'] and listado['solo_revision'], listado)
        fila = next(i for i in listado['inventarios'] if i['id'] == self.toma.id)
        self.assertEqual(_sin_plata(fila), [])
        self.assertEqual((fila['total_diferencias_positivas'], fila['total_diferencias_negativas']), (1, 2))

        detalle = self.client.get(reverse('detalle_inventario', args=[self.toma.id]))
        self.assertEqual(detalle.status_code, 200)
        self.assertTrue(detalle.context['solo_revision'])
        html = detalle.content.decode()
        self.assertIn('Modo revisión', html)
        self.assertNotIn('id="floatingActions"', html)
        self.assertNotIn('id="inputEscaner"', html)
        self.assertNotIn('Antiguo · P. costo', html)

        for orden in ('', 'dif_valor'):  # «$ a costo» cae a «unidades» y no revela costos
            lineas = self._get('api_productos_conteo', self.toma.id, orden=orden).json()
            self.assertTrue(lineas['success'], lineas)
            self.assertEqual({k for p in lineas['productos'] for k in _sin_plata(p)}, set())
        falta = next(p for p in lineas['productos'] if p['sku'] == '9600002')
        self.assertEqual((falta['stock_sistema_ajustado'], falta['stock_fisico'], falta['diferencia']), (3, 1, -2))

        analisis = self._get('api_analisis_inventario', self.toma.id).json()['analisis']
        self.assertIsNone(analisis['resumen_financiero'])
        self.assertTrue(analisis['solo_revision'])
        for clave in ('top_faltantes', 'top_sobrantes', 'analisis_marcas', 'analisis_categorias'):
            self.assertEqual({k for f in analisis[clave] for k in _sin_plata(f)}, set(), clave)
        r = analisis['resumen']
        self.assertEqual((r['sobrantes'], r['faltantes'], r['sin_diferencia'], r['total_contados']), (1, 1, 1, 3))
        self.assertEqual((r['sobrantes_unidades'], r['faltantes_unidades']), (1, 2))

        marcas = self._get('api_informe_marcas_inventario', self.toma.id).json()
        self.assertTrue(marcas['success'], marcas)
        for m in marcas['marcas'] + [marcas['total'], marcas['resumen']]:
            self.assertEqual(_sin_plata(m), [], m)
        self.assertEqual((marcas['total']['ant_stock'], marcas['total']['nue_stock'], marcas['total']['dif']), (6, 5, -1))

        for nombre in ('api_exportar_inventario', 'api_exportar_diferencias_inventario', 'api_informe_final_inventario'):
            self.assertEqual(self._get(nombre, self.toma.id).status_code, 403, nombre)

    def test_jefe_no_mueve_la_toma(self):
        self._como(self.jefe)
        det = self.toma.detalles.get(sku='9600002')
        intentos = [
            ('api_registrar_conteo', {'conteos': [{'detalle_id': det.id, 'stock_fisico': 3}]}),
            ('api_excluir_detalles_inventario', {'ids': [det.id], 'excluir': True}),
            ('api_resolver_no_contados', {'accion': 'faltante'}),
            ('api_finalizar_conteo', {}),
        ]
        for nombre, payload in intentos:
            resp = self.client.post(reverse(nombre, args=[self.toma.id]), data=json.dumps(payload),
                                    content_type='application/json')
            self.assertEqual(resp.status_code, 403, nombre)
        crear = self.client.post(reverse('api_crear_inventario'), data=json.dumps({}), content_type='application/json')
        self.assertEqual(crear.status_code, 403)
        det.refresh_from_db()
        self.assertEqual((det.stock_fisico, det.excluir_de_analisis), (1, False))

    def test_jefe_no_fusiona_duplicados(self):
        self._como(self.jefe)
        resp = self.client.post(reverse('api_ejecutar_fusion'), data=json.dumps({}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertNotEqual(self.client.get(reverse('ver_fusion_duplicados')).status_code, 200)

    def test_jefe_solo_ve_su_tienda(self):
        otra = crear_sucursal(self.empresa, alias='OTRA')
        self._pt('DE OTRA', 9600009, 1, sucursal=otra)
        toma_otra = self._crear_toma_con_pistola('sku,stock\n9600009,1\n', sucursal=otra)
        self._como(self.jefe)
        ids = {i['id'] for i in self._get('api_obtener_inventarios', sucursal='todas').json()['inventarios']}
        self.assertIn(self.toma.id, ids)
        self.assertNotIn(toma_otra.id, ids)
        self.assertEqual(self._get('api_productos_conteo', toma_otra.id).status_code, 403)

    def test_administrador_sigue_viendo_costos(self):
        # self.user es administrador (BaseTomaInventarioTest)
        pagina = self.client.get(reverse('detalle_inventario', args=[self.toma.id]))
        self.assertFalse(pagina.context['solo_revision'])
        self.assertIn('Antiguo · P. costo', pagina.content.decode())
        linea = self._get('api_productos_conteo', self.toma.id).json()['productos'][0]
        self.assertIn('costo_unitario', linea)
        analisis = self._get('api_analisis_inventario', self.toma.id).json()['analisis']
        self.assertIsInstance(analisis['resumen_financiero'], dict)
        self.assertIn('valor_diferencias', analisis['analisis_marcas'][0])
        self.assertIn('ant_costo', self._get('api_informe_marcas_inventario', self.toma.id).json()['total'])
        self.assertEqual(self._get('api_informe_final_inventario', self.toma.id).status_code, 200)
        self.assertEqual(self.client.get(reverse('ver_fusion_duplicados')).status_code, 200)  # Editar: sí fusiona

    def test_consultas_del_listado_y_del_analisis(self):
        # Antes: el análisis hacía ~25 consultas (13 COUNT sueltos) y el listado 5 COUNT para las tarjetas
        with CaptureQueriesContext(connection) as ctx:
            self._get('api_analisis_inventario', self.toma.id)
        conteos = [q['sql'] for q in ctx.captured_queries if 'tomainventariodetalle' in q['sql'].lower()]
        self.assertLessEqual(len(conteos), 8, conteos)
        with CaptureQueriesContext(connection) as ctx:
            self._get('api_obtener_inventarios')
        # (la consulta principal; la del aviso «por reponer» cuenta líneas, no tomas)
        sobre_tomas = [q['sql'] for q in ctx.captured_queries
                       if 'COUNT' in q['sql'].upper() and 'FROM "APP_TOMAINVENTARIO" WHERE' in q['sql'].upper()]
        self.assertEqual(len(sobre_tomas), 1, sobre_tomas)

    def test_jefe_opera_la_toma_pero_ve_solo_pares(self):
        # El Jefe (rol 'jefe') puede operar la toma, pero la plata es solo de Maestro y Administrador
        opcion = OpcionMenu.objects.get(codigo='gestion_inventarios')
        PermisoRol.objects.create(rol='jefe', opcion_menu=opcion, puede_ver=True, puede_crear=True, puede_editar=True,
                                  puede_eliminar=False, puede_exportar=True, puede_aprobar=True)
        jefe = crear_usuario(username='jefe_zona', rol='jefe')
        crear_empresa_user(jefe, self.empresa, self.sucursal)
        self._como(jefe)

        pagina = self.client.get(reverse('detalle_inventario', args=[self.toma.id]))
        self.assertFalse(pagina.context['solo_revision'])
        self.assertFalse(pagina.context['ver_valores'])
        self.assertNotIn('Antiguo · P. costo', pagina.content.decode())
        lineas = self._get('api_productos_conteo', self.toma.id).json()['productos']
        self.assertEqual({k for p in lineas for k in _sin_plata(p)}, set())
        analisis = self._get('api_analisis_inventario', self.toma.id).json()['analisis']
        self.assertIsNone(analisis['resumen_financiero'])
        self.assertEqual((analisis['top_faltantes'], analisis['analisis_marcas']), ([], []))
        marcas = self._get('api_informe_marcas_inventario', self.toma.id).json()
        self.assertEqual(_sin_plata(marcas['total']), [])
        for nombre in ('api_exportar_inventario', 'api_exportar_diferencias_inventario', 'api_informe_final_inventario'):
            self.assertEqual(self._get(nombre, self.toma.id).status_code, 403, nombre)
        # …y sí opera: deja una línea sin ajustar
        det = self.toma.detalles.get(sku='9600002')
        resp = self.client.post(reverse('api_excluir_detalles_inventario', args=[self.toma.id]),
                                data=json.dumps({'ids': [det.id], 'excluir': True}), content_type='application/json').json()
        self.assertTrue(resp['success'], resp)


class ReconteoEnRevisionTest(BaseTomaContadaAnoche):
    """Período de revisión: se recuenta cualquier línea contada, lo vendido entre el conteo y el
    reconteo se descuenta solo, y lo que «aparece» en el reconteo lo aprueba el Maestro."""

    def setUp(self):
        super().setUp()
        self.maestro = crear_usuario(username='maestro_rec', rol='maestro')
        crear_empresa_user(self.maestro, self.empresa, self.sucursal)
        self.pt_vende = self._pt('SE VENDE', 9700001, 3)
        self.pt_falta = self._pt('FALTA TRES', 9700002, 4)
        data = self.client.post(reverse('api_crear_inventario'), data=json.dumps({
            'nombre': 'Completo', 'tipo_inventario': 'COMPLETO', 'conteo_tienda_cerrada': True,
            'fecha_corte': self.corte.strftime('%Y-%m-%dT%H:%M'), 'filtros': {'solo_con_stock': True},
        }), content_type='application/json').json()
        self.toma = TomaInventario.objects.get(id=data['inventario_id'])
        self.assertTrue(self._importar_pistola(self.toma.id, 'sku,stock\n9700001,3\n9700002,1\n')['success'])
        # Venta en el período de revisión (después del conteo de anoche)
        Movimientos_Producto.objects.create(
            ProductoTalla=self.pt_vende, cantidad=-1, concepto='VENTA_PUBLICO', sucursal_origen=self.sucursal,
            responsable='POS', fecha=self.venta_hoy.date(), hora=self.venta_hoy.time(),
        )
        Producto_Talla.objects.filter(pk=self.pt_vende.pk).update(stock=2)

    def _recontar(self, sku, cantidad):
        det = self.toma.detalles.get(sku=sku)
        return self._post('api_registrar_reconteo', self.toma.id,
                          {'reconteos': [{'detalle_id': det.id, 'stock_reconteo': cantidad}]})

    def test_reconteo_descuenta_lo_vendido_despues_del_conteo(self):
        # La línea no estaba marcada (contó 3 = sistema): igual se puede recontar
        self.assertFalse(self.toma.detalles.get(sku='9700001').reconteo_requerido)
        resp = self._recontar('9700001', 2)  # hoy quedan 2: se vendió 1 después del conteo
        self.assertTrue(resp['success'], resp)
        self.assertFalse(resp['errores'])
        det = self.toma.detalles.get(sku='9700001')
        # Antes salía faltante −1 y al aplicar se descontaba otra vez la venta
        self.assertEqual((det.stock_reconteo, det.stock_fisico, det.diferencia), (2, 3, 0))
        self.assertEqual(resp['encontrados'], [])

    def test_lo_encontrado_en_el_reconteo_lo_aprueba_el_maestro(self):
        self.assertTrue(self.toma.detalles.get(sku='9700002').reconteo_requerido)  # 4 → 1
        resp = self._recontar('9700002', 4)
        self.assertEqual(resp['encontrados'], ['9700002'])
        det = self.toma.detalles.get(sku='9700002')
        self.assertEqual(det.diferencia, 0)
        self.assertIn('ENCONTRADO EN RECONTEO: +3', det.observaciones)

        for url in ('api_finalizar_conteo', 'api_enviar_aprobacion'):
            self.assertTrue(self._post(url, self.toma.id)['success'], url)
        analisis = self.client.get(reverse('api_analisis_inventario', args=[self.toma.id])).json()['analisis']
        self.assertEqual([e['sku'] for e in analisis['encontrados_reconteo']], ['9700002'])
        self.assertFalse(analisis['puede_aprobar'])  # el administrador no la aprueba

        resp = self._post('api_aprobar_inventario', self.toma.id)
        self.assertFalse(resp['success'])
        self.assertIn('Maestro', resp['error'])
        self.client.force_login(self.maestro)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()
        resp = self._post('api_aprobar_inventario', self.toma.id)
        self.assertTrue(resp['success'], resp)
