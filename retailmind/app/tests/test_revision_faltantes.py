"""
Faltantes por revisar (después de aplicar una toma): el jefe de local reporta lo
que encontró o confirma que no está; el MAESTRO confirma lo encontrado y lo repone
al stock (AJUSTE_INVENTARIO_ENTRADA con referencia a la toma + lote FIFO) o lo
rechaza. La plata ($ venta / $ costo) solo la ven el Maestro y el Administrador.

Correr (sin tocar la BD del .env):
    DATABASE_URL='sqlite://:memory:' python manage.py test app.tests.test_revision_faltantes
"""
import json

from django.urls import reverse

from app.models import (
    LoteProducto, ModuloSistema, Movimientos_Producto, OpcionMenu, PermisoRol, TomaInventario,
)
from app.views_gestion_inventarios import _ejecutar_ajustes_background, _iniciar_tarea_ajustes
from .factories import crear_empresa_user, crear_usuario
from .test_toma_inventario_informe import BaseTomaContadaAnoche

VER_EDITAR = {'puede_ver': True, 'puede_crear': False, 'puede_editar': True,
              'puede_eliminar': False, 'puede_exportar': True, 'puede_aprobar': False}


class RevisionFaltantesTest(BaseTomaContadaAnoche):

    def setUp(self):
        super().setUp()
        modulo = ModuloSistema.objects.get(codigo='existencias')
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='revision_faltantes_inventario',
            defaults={'modulo': modulo, 'nombre': 'Faltantes por revisar', 'url_name': 'revision_faltantes'},
        )
        for rol in ('administrador', 'jefe_local'):
            PermisoRol.objects.get_or_create(rol=rol, opcion_menu=opcion, defaults=VER_EDITAR)
        self.jefe = crear_usuario(username='jefelocal', rol='jefe_local')
        crear_empresa_user(self.jefe, self.empresa, self.sucursal)
        self.maestro = crear_usuario(username='maestro_inv', rol='maestro')
        crear_empresa_user(self.maestro, self.empresa, self.sucursal)

        # Toma aplicada: A no apareció (3 → faltante, recontado 0), B se contó 1 de 2, C cuadra
        self.pt_a = self._pt('GUANTE CH216', 9500001, 3, precio=29990)
        self.pt_b = self._pt('PINCHE', 9500002, 2, precio=3990)
        self.pt_c = self._pt('EXACTO', 9500003, 1)
        data = self.client.post(reverse('api_crear_inventario'), data=json.dumps({
            'nombre': 'Completo', 'tipo_inventario': 'COMPLETO', 'conteo_tienda_cerrada': True,
            'fecha_corte': self.corte.strftime('%Y-%m-%dT%H:%M'), 'filtros': {'solo_con_stock': True},
        }), content_type='application/json').json()
        self.toma = TomaInventario.objects.get(id=data['inventario_id'])
        self.assertTrue(self._importar_pistola(self.toma.id, 'sku,stock\n9500002,1\n9500003,1\n')['success'])
        self.assertTrue(self._post('api_resolver_no_contados', self.toma.id, {'accion': 'faltante'})['success'])
        self.assertTrue(self._post('api_finalizar_conteo', self.toma.id)['success'])
        det_a = self.toma.detalles.get(sku='9500001')
        self._post('api_registrar_reconteo', self.toma.id, {'reconteos': [{'detalle_id': det_a.id, 'stock_reconteo': 0}]})
        for url in ('api_enviar_aprobacion', 'api_aprobar_inventario'):
            self.assertTrue(self._post(url, self.toma.id)['success'], url)
        _iniciar_tarea_ajustes(self.toma, self.user)
        _ejecutar_ajustes_background(self.toma.id, self.user.id, cerrar_conexion=False)
        self.toma.refresh_from_db()
        self.assertEqual(self.toma.estado, 'COMPLETADO')
        self.pt_a.refresh_from_db()
        self.assertEqual(self.pt_a.stock, 0)
        self.det_a = self.toma.detalles.get(sku='9500001')
        self.det_b = self.toma.detalles.get(sku='9500002')

    def _como(self, usuario):
        self.client.force_login(usuario)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

    def _post_json(self, nombre, *args, **payload):
        return self.client.post(reverse(nombre, args=args), data=json.dumps(payload), content_type='application/json')

    def test_lista_por_urgencia(self):
        self._como(self.jefe)
        self.assertEqual(self.client.get(reverse('revision_faltantes')).status_code, 200)
        resp = self.client.get(reverse('api_revision_faltantes')).json()
        self.assertTrue(resp['success'], resp)
        self.assertEqual([i['sku'] for i in resp['items']], ['9500001', '9500002'])  # A (3 u., Alta) primero
        self.assertEqual(resp['items'][0]['urgencia'], 'ALTA')
        self.assertEqual(resp['items'][0]['motivo'], 'No apareció en la pistola')
        self.assertEqual(resp['resumen']['pendiente'], 2)
        self.assertFalse(resp['puede_reponer'])
        # El jefe de local ve pares, no plata
        self.assertFalse(resp['ver_valores'])
        self.assertNotIn('valor_venta', resp['resumen'])
        self.assertEqual({k for i in resp['items'] for k in i if k.startswith('valor')}, set())
        # El administrador sí ve la plata, pero no repone (eso es del Maestro)
        self._como(self.user)
        resp = self.client.get(reverse('api_revision_faltantes')).json()
        self.assertTrue(resp['ver_valores'])
        self.assertIn('valor_venta', resp['items'][0])
        self.assertFalse(resp['puede_reponer'])

    def test_jefe_reporta_y_maestro_confirma(self):
        self._como(self.jefe)
        # No puede reportar más de lo que se descontó
        resp = self._post_json('api_reportar_faltante', self.det_a.id, estado='ENCONTRADO', cantidad=5).json()
        self.assertFalse(resp['success'])
        resp = self._post_json('api_reportar_faltante', self.det_a.id, estado='ENCONTRADO', cantidad=2,
                               nota='estaba en bodega').json()
        self.assertTrue(resp['success'], resp)
        self.assertEqual((resp['item']['revision_estado'], resp['item']['revision_cantidad']), ('ENCONTRADO', 2))
        self.assertNotIn('valor_venta', resp['item'])
        # El jefe de local no repone al stock
        self.assertEqual(self._post_json('api_reponer_faltante', self.det_a.id).status_code, 403)
        self.pt_a.refresh_from_db()
        self.assertEqual(self.pt_a.stock, 0)

        # El aviso llega al listado de Gestión de Inventarios del administrador
        self._como(self.user)
        listado = self.client.get(reverse('api_obtener_inventarios')).json()
        self.assertEqual(listado['faltantes_por_reponer'], 1)
        fila = next(i for i in listado['inventarios'] if i['id'] == self.toma.id)
        self.assertEqual((fila['faltantes_por_revisar'], fila['faltantes_por_reponer']), (1, 1))
        # …pero el administrador tampoco lo repone: lo confirma el Maestro
        self.assertEqual(self._post_json('api_reponer_faltante', self.det_a.id).status_code, 403)

        self._como(self.maestro)
        resp = self._post_json('api_reponer_faltante', self.det_a.id).json()
        self.assertTrue(resp['success'], resp)
        self.assertEqual(resp['item']['revision_estado'], 'REPUESTO')
        self.pt_a.refresh_from_db()
        self.assertEqual(self.pt_a.stock, 2)
        mov = Movimientos_Producto.objects.filter(
            ProductoTalla=self.pt_a, concepto='AJUSTE_INVENTARIO_ENTRADA', referencia_externa=self.toma.numero_inventario
        ).get()
        self.assertEqual(mov.cantidad, 2)
        self.assertIn('Encontrado después del inventario', mov.observaciones)
        lote = LoteProducto.objects.filter(producto_talla=self.pt_a, movimiento=mov).get()
        self.assertEqual(lote.cantidad_disponible, 2)

        # Idempotente: no se repone dos veces ni se cambia lo repuesto
        self.assertFalse(self._post_json('api_reponer_faltante', self.det_a.id).json()['success'])
        self.assertFalse(self._post_json('api_reportar_faltante', self.det_a.id, estado='CONFIRMADO').json()['success'])
        self.pt_a.refresh_from_db()
        self.assertEqual(self.pt_a.stock, 2)

    def test_confirmar_y_deshacer(self):
        self._como(self.jefe)
        resp = self._post_json('api_reportar_faltante', self.det_b.id, estado='CONFIRMADO').json()
        self.assertEqual(resp['item']['revision_estado'], 'CONFIRMADO')
        # Sin «encontrado» no hay nada que reponer
        self._como(self.maestro)
        self.assertFalse(self._post_json('api_reponer_faltante', self.det_b.id).json()['success'])
        self._como(self.jefe)
        resp = self._post_json('api_reportar_faltante', self.det_b.id, estado='').json()
        self.assertEqual(resp['item']['revision_estado'], '')
        self.assertEqual(resp['item']['revision_por'], '')

    def test_solo_faltantes_de_tomas_aplicadas(self):
        # Una línea que cuadró (C) no es un faltante revisable
        det_c = self.toma.detalles.get(sku='9500003')
        self._como(self.jefe)
        resp = self._post_json('api_reportar_faltante', det_c.id, estado='ENCONTRADO', cantidad=1)
        self.assertEqual(resp.status_code, 404)

    def test_maestro_rechaza_lo_encontrado(self):
        self._como(self.jefe)
        self.assertTrue(self._post_json('api_reportar_faltante', self.det_a.id, estado='ENCONTRADO', cantidad=3).json()['success'])
        # Ni el jefe de local ni el administrador pueden rechazar
        self.assertEqual(self._post_json('api_rechazar_encontrado', self.det_a.id, motivo='no está').status_code, 403)
        self._como(self.user)
        self.assertEqual(self._post_json('api_rechazar_encontrado', self.det_a.id, motivo='no está').status_code, 403)

        self._como(self.maestro)
        self.assertFalse(self._post_json('api_rechazar_encontrado', self.det_a.id).json()['success'])  # motivo obligatorio
        resp = self._post_json('api_rechazar_encontrado', self.det_a.id, motivo='en bodega no había nada').json()
        self.assertTrue(resp['success'], resp)
        self.assertEqual((resp['item']['revision_estado'], resp['item']['revision_cantidad']), ('', None))
        self.assertIn('Rechazado por', resp['item']['revision_nota'])
        self.assertIn('en bodega no había nada', resp['item']['revision_nota'])
        self.pt_a.refresh_from_db()
        self.assertEqual(self.pt_a.stock, 0)  # el faltante sigue descontado
        # Ya no está «encontrado»: no se puede reponer ni volver a rechazar
        self.assertFalse(self._post_json('api_reponer_faltante', self.det_a.id).json()['success'])
        self.assertFalse(self._post_json('api_rechazar_encontrado', self.det_a.id, motivo='x').json()['success'])
