"""
«Ajustar stock ya» con la toma abierta (pedido del usuario, 09-10: los ecommerce venden
el stock de las tiendas y no pueden esperar al fin de la revisión).

- El Maestro lleva al stock lo contado; la toma sigue en conteo. No toca lo que espera
  reconteo ni lo no contado.
- La tienda (también el jefe de local, que solo tiene Ver) sigue contando y recontando;
  lo que cambie queda «por ajustar» hasta que el Maestro lo autorice (otro ajuste o
  aprobar la toma, que después solo mueve lo que falta).
- Recontar una línea ya ajustada descuenta las ventas del medio pero NO el ajuste de la
  propia toma (no es una venta).

Correr (sin tocar la BD del .env):
    DATABASE_URL='sqlite://:memory:' python manage.py test app.tests.test_toma_ajuste_anticipado
"""
import json
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from app.models import (
    Movimientos_Producto, OpcionMenu, PermisoRol, Producto, Producto_Talla, TomaInventario, TomaInventarioDetalle,
)
from app.views_gestion_inventarios import _ejecutar_ajustes_background, _iniciar_tarea_ajustes
from .factories import crear_empresa_user, crear_usuario
from .test_toma_inventario_informe import BaseTomaContadaAnoche


class AjusteAnticipadoTest(BaseTomaContadaAnoche):

    def setUp(self):
        super().setUp()
        self.maestro = crear_usuario(username='maestro_aa', rol='maestro')
        crear_empresa_user(self.maestro, self.empresa, self.sucursal)
        opcion = OpcionMenu.objects.get(codigo='gestion_inventarios')
        PermisoRol.objects.create(rol='jefe_local', opcion_menu=opcion, puede_ver=True, puede_crear=False,
                                  puede_editar=False, puede_eliminar=False, puede_exportar=False, puede_aprobar=False)
        self.jefe = crear_usuario(username='jefe_aa', rol='jefe_local')
        crear_empresa_user(self.jefe, self.empresa, self.sucursal)

        # SOBRA 1→2 (+1) · FALTA 3→2 (−1) · GRANDE 4→1 (−3: pide reconteo) · NO CONTADO 2 (no se pistolea)
        self.pt_sobra = self._pt('SOBRA', 9800001, 1)
        self.pt_falta = self._pt('FALTA', 9800002, 3)
        self.pt_grande = self._pt('GRANDE', 9800003, 4)
        self.pt_no = self._pt('NO CONTADO', 9800004, 2)
        # Productos de antes del corte, como en una tienda real: el «saldo inicial legacy» que
        # el kardex crea en el primer movimiento se fecha con la creación del producto
        Producto.objects.filter(producto_talla__in=[self.pt_sobra, self.pt_falta, self.pt_grande, self.pt_no]).update(
            fecha_creacion=self.corte - timedelta(days=30))
        data = self.client.post(reverse('api_crear_inventario'), data=json.dumps({
            'nombre': 'Completo', 'tipo_inventario': 'COMPLETO', 'conteo_tienda_cerrada': True,
            'fecha_corte': self.corte.strftime('%Y-%m-%dT%H:%M'), 'filtros': {'solo_con_stock': True},
        }), content_type='application/json').json()
        self.assertTrue(data['success'], data)
        self.toma = TomaInventario.objects.get(id=data['inventario_id'])
        self.assertTrue(self._importar_pistola(self.toma.id, 'sku,stock\n9800001,2\n9800002,2\n9800003,1\n')['success'])
        self.assertTrue(self.toma.detalles.get(sku='9800003').reconteo_requerido)

    # ---------- helpers ----------

    def _como(self, usuario):
        self.client.force_login(usuario)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

    def _ajustar_ya(self):
        """POST como la pantalla; el hilo se lanza en on_commit (que en TestCase no corre),
        así que el worker se ejecuta aquí mismo con el log que abrió la vista."""
        with self.captureOnCommitCallbacks(execute=False):
            resp = self._post('api_ajustar_stock_ya', self.toma.id)
        if resp.get('success') and not resp.get('already_running'):
            log = self.toma.ajustes_anticipados().latest('created_at')
            _ejecutar_ajustes_background(self.toma.id, self.maestro.id, cerrar_conexion=False, parcial=True, log_id=log.id)
        return resp

    def _stock(self, pt):
        return Producto_Talla.objects.get(pk=pt.pk).stock

    def _movs_toma(self):
        return Movimientos_Producto.objects.filter(referencia_externa=self.toma.numero_inventario)

    def _analisis(self):
        return self.client.get(reverse('api_analisis_inventario', args=[self.toma.id])).json()['analisis']

    # ---------- tests ----------

    def test_maestro_ajusta_lo_contado_y_la_toma_sigue_abierta(self):
        self._como(self.maestro)
        previa = self._analisis()['ajuste_anticipado']
        self.assertTrue(previa['puede'])
        self.assertFalse(previa['ajustada'])
        # GRANDE espera reconteo: no entra en el ajuste anticipado
        self.assertEqual((previa['por_ajustar']['lineas'], previa['por_ajustar']['unidades_suben'],
                          previa['por_ajustar']['unidades_bajan']), (2, 1, 1))

        resp = self._ajustar_ya()
        self.assertTrue(resp['success'], resp)

        self.assertEqual((self._stock(self.pt_sobra), self._stock(self.pt_falta)), (2, 2))
        self.assertEqual((self._stock(self.pt_grande), self._stock(self.pt_no)), (4, 2))  # no se tocan
        self.toma.refresh_from_db()
        self.assertEqual(self.toma.estado, 'EN_CONTEO')
        self.assertEqual(sorted(self._movs_toma().values_list('cantidad', flat=True)), [-1, 1])
        sobra = self.toma.detalles.get(sku='9800001')
        self.assertEqual((sobra.ajuste_aplicado, sobra.diferencia_aplicada, sobra.por_ajustar), (True, 1, 0))
        self.assertFalse(self.toma.detalles.get(sku='9800003').ajuste_aplicado)

        log = self.toma.ajustes_anticipados().get()
        self.assertEqual((log.datos_adicionales['estado'], log.datos_adicionales['ajustes_aplicados']), ('COMPLETADO', 2))
        despues = self._analisis()['ajuste_anticipado']
        self.assertTrue(despues['ajustada'])
        self.assertEqual(despues['por_ajustar']['lineas'], 0)

        # Nada más por ajustar: no mueve nada
        otra = self._ajustar_ya()
        self.assertFalse(otra['success'])
        self.assertTrue(otra['sin_cambios'])
        self.assertEqual(self._movs_toma().count(), 2)

    def test_solo_el_maestro_ajusta_con_la_toma_abierta(self):
        # self.user es administrador (BaseTomaInventarioTest): opera la toma pero no ajusta con ella abierta
        self.assertFalse(self._analisis()['ajuste_anticipado']['puede'])
        resp = self.client.post(reverse('api_ajustar_stock_ya', args=[self.toma.id]), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self._como(self.jefe)
        resp = self.client.post(reverse('api_ajustar_stock_ya', args=[self.toma.id]), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual((self._stock(self.pt_sobra), self._stock(self.pt_falta)), (1, 3))
        self.assertFalse(self._movs_toma().exists())

    def test_reconteo_de_lo_ajustado_queda_por_ajustar_y_lo_autoriza_el_maestro(self):
        self._como(self.maestro)
        self.assertTrue(self._ajustar_ya()['success'])
        self.assertEqual(self._stock(self.pt_falta), 2)

        # Después del ajuste se vende un par de FALTA (la tienda está abierta)
        ahora = timezone.localtime()
        Movimientos_Producto.objects.create(
            ProductoTalla=self.pt_falta, cantidad=-1, concepto='VENTA_PUBLICO', sucursal_origen=self.sucursal,
            responsable='POS', fecha=ahora.date(), hora=ahora.time(),
        )
        Producto_Talla.objects.filter(pk=self.pt_falta.pk).update(stock=1)

        # El jefe de local encuentra el par que no se contó: hoy hay 2 (eran 3 al corte, se vendió 1)
        self._como(self.jefe)
        det = self.toma.detalles.get(sku='9800002')
        resp = self._post('api_registrar_reconteo', self.toma.id,
                          {'reconteos': [{'detalle_id': det.id, 'stock_reconteo': 2}]})
        self.assertTrue(resp['success'], resp)
        self.assertEqual(resp['por_autorizar'], ['9800002'])
        det.refresh_from_db()
        # La venta se descuenta; el ajuste de la toma (−1) NO cuenta como venta
        self.assertEqual((det.stock_fisico, det.diferencia, det.por_ajustar), (3, 0, 1))
        self.assertEqual(self._stock(self.pt_falta), 1)  # contar no mueve stock

        self._como(self.maestro)
        pa = self._analisis()['ajuste_anticipado']['por_ajustar']
        self.assertEqual((pa['lineas'], pa['unidades_suben'], pa['correcciones']), (1, 1, 1))
        self.assertTrue(self._ajustar_ya()['success'])
        self.assertEqual(self._stock(self.pt_falta), 2)  # = lo que hay físicamente
        det.refresh_from_db()
        self.assertEqual((det.diferencia_aplicada, det.por_ajustar), (0, 0))
        correccion = self._movs_toma().filter(ProductoTalla=self.pt_falta).order_by('-id').first()
        self.assertEqual(correccion.cantidad, 1)
        self.assertIn('Corrección', correccion.observaciones)

    def test_jefe_de_local_cuenta_pero_no_decide(self):
        self._como(self.jefe)
        pagina = self.client.get(reverse('detalle_inventario', args=[self.toma.id]))
        self.assertTrue(pagina.context['solo_revision'])
        self.assertTrue(pagina.context['puede_contar'])
        html = pagina.content.decode()
        self.assertIn('id="inputEscaner"', html)
        self.assertIn('id="btnGuardar"', html)
        for boton in ('id="btnFinalizar"', 'id="barraSeleccion"', 'id="btnAjustarYa"', 'id="btnAprobar"'):
            self.assertNotIn(boton, html, boton)

        det_no = self.toma.detalles.get(sku='9800004')
        resp = self._post('api_registrar_conteo', self.toma.id, {'conteos': [{'detalle_id': det_no.id, 'stock_fisico': 2}]})
        self.assertTrue(resp['success'], resp)
        det_no.refresh_from_db()
        self.assertEqual((det_no.contado, det_no.stock_fisico), (True, 2))
        det_grande = self.toma.detalles.get(sku='9800003')
        resp = self._post('api_registrar_reconteo', self.toma.id,
                          {'reconteos': [{'detalle_id': det_grande.id, 'stock_reconteo': 4}]})
        self.assertTrue(resp['success'], resp)
        self.assertTrue(self._importar_pistola(self.toma.id, 'sku,stock\n9800004,2\n')['success'])

        for nombre, payload in (
            ('api_excluir_detalles_inventario', {'ids': [det_no.id], 'excluir': True}),
            ('api_resolver_no_contados', {'accion': 'faltante'}),
            ('api_finalizar_conteo', {}),
            ('api_aprobar_inventario', {}),
            ('api_cancelar_inventario', {'motivo': 'x'}),
        ):
            resp = self.client.post(reverse(nombre, args=[self.toma.id]), data=json.dumps(payload),
                                    content_type='application/json')
            self.assertEqual(resp.status_code, 403, nombre)
        # Ve las líneas en pares, sin plata
        linea = self.client.get(reverse('api_productos_conteo', args=[self.toma.id])).json()['productos'][0]
        self.assertNotIn('costo_unitario', linea)
        self.assertIn('por_ajustar', linea)

    def test_lo_ajustado_no_se_pisa_desde_la_tabla_ni_se_excluye(self):
        self._como(self.maestro)
        self.assertTrue(self._ajustar_ya()['success'])
        self.client.force_login(self.user)  # administrador

        sobra = self.toma.detalles.get(sku='9800001')
        resp = self._post('api_registrar_conteo', self.toma.id, {'conteos': [{'detalle_id': sobra.id, 'stock_fisico': 5}]})
        self.assertEqual(resp['conteos_realizados'], 0)
        self.assertIn('Recontar', resp['errores'][0])
        # anotaciones con la misma cantidad sí
        resp = self._post('api_registrar_conteo', self.toma.id,
                          {'conteos': [{'detalle_id': sobra.id, 'stock_fisico': 2, 'ubicacion': 'Bodega 2'}]})
        self.assertEqual(resp['conteos_realizados'], 1)
        sobra.refresh_from_db()
        self.assertEqual((sobra.stock_fisico, sobra.ubicacion, sobra.por_ajustar), (2, 'Bodega 2', 0))

        resp = self.client.post(reverse('api_excluir_detalle_inventario', args=[self.toma.id, sobra.id]),
                                data=json.dumps({'excluir': True}), content_type='application/json').json()
        self.assertFalse(resp['success'])
        no = self.toma.detalles.get(sku='9800004')
        resp = self._post('api_excluir_detalles_inventario', self.toma.id, {'ids': [sobra.id, no.id], 'excluir': True})
        self.assertTrue(resp['success'], resp)
        self.assertEqual((resp['actualizados'], resp['ya_ajustadas']), (1, ['9800001']))
        sobra.refresh_from_db()
        self.assertFalse(sobra.excluir_de_analisis)

        # Un archivo nuevo no pisa lo ajustado (se recuenta); lo demás sí se carga
        resp = self._importar_pistola(self.toma.id, 'sku,stock\n9800001,5\n9800003,1\n')
        self.assertTrue(resp['success'], resp)
        self.assertEqual([y['sku'] for y in resp['ya_ajustados']], ['9800001'])
        sobra.refresh_from_db()
        self.assertEqual(sobra.stock_fisico, 2)

    def test_aprobar_tras_el_ajuste_es_del_maestro_y_aplicar_no_repite(self):
        self._como(self.maestro)
        self.assertTrue(self._ajustar_ya()['success'])

        self.client.force_login(self.user)  # administrador: recuenta, cuenta lo que falta y cierra
        grande = self.toma.detalles.get(sku='9800003')
        self.assertTrue(self._post('api_registrar_reconteo', self.toma.id,
                                   {'reconteos': [{'detalle_id': grande.id, 'stock_reconteo': 3}]})['success'])
        no = self.toma.detalles.get(sku='9800004')
        self.assertTrue(self._post('api_registrar_conteo', self.toma.id,
                                   {'conteos': [{'detalle_id': no.id, 'stock_fisico': 2}]})['success'])
        for url in ('api_finalizar_conteo', 'api_enviar_aprobacion'):
            self.assertTrue(self._post(url, self.toma.id)['success'], url)
        self.assertFalse(self._analisis()['puede_aprobar'])
        resp = self._post('api_aprobar_inventario', self.toma.id)
        self.assertFalse(resp['success'])
        self.assertIn('Maestro', resp['error'])

        self._como(self.maestro)
        self.assertTrue(self._post('api_aprobar_inventario', self.toma.id)['success'])
        self.toma.refresh_from_db()
        tarea, iniciada = _iniciar_tarea_ajustes(self.toma, self.maestro)
        self.assertTrue(iniciada)
        _ejecutar_ajustes_background(self.toma.id, self.maestro.id, cerrar_conexion=False)

        self.toma.refresh_from_db()
        self.assertEqual(self.toma.estado, 'COMPLETADO')
        # Lo ajustado antes no se repite: solo entra lo de GRANDE (4 → 3 = −1)
        self.assertEqual([self._stock(p) for p in (self.pt_sobra, self.pt_falta, self.pt_grande, self.pt_no)], [2, 2, 3, 2])
        self.assertEqual(sorted(self._movs_toma().values_list('cantidad', flat=True)), [-1, -1, 1])

    def test_lineas_viejas_ajustadas_no_vuelven_a_pendiente(self):
        # Antes de este cambio no existía diferencia_aplicada: NULL + ajuste_aplicado = ya movió `diferencia`
        sobra = self.toma.detalles.get(sku='9800001')
        TomaInventarioDetalle.objects.filter(pk=sobra.pk).update(ajuste_aplicado=True, diferencia_aplicada=None)
        sobra.refresh_from_db()
        self.assertEqual((sobra.diferencia_ya_aplicada, sobra.por_ajustar), (1, 0))
        self.assertNotIn(sobra.id, self.toma.lineas_por_ajustar().values_list('id', flat=True))

    # ---------- «Reconteo masivo» (Maestro): quedarse con la pistola sin recontar ----------

    def _vender_grande_hoy(self):
        Movimientos_Producto.objects.create(
            ProductoTalla=self.pt_grande, cantidad=-1, concepto='VENTA_PUBLICO', sucursal_origen=self.sucursal,
            responsable='POS', fecha=self.venta_hoy.date(), hora=self.venta_hoy.time(),
        )
        Producto_Talla.objects.filter(pk=self.pt_grande.pk).update(stock=3)

    def test_reconteo_masivo_acepta_la_pistola_sin_recontar(self):
        self._como(self.maestro)
        self.assertIn('id="btnReconteoMasivo"', self.client.get(reverse('detalle_inventario', args=[self.toma.id])).content.decode())
        self._vender_grande_hoy()  # GRANDE: pistola 1 anoche, hoy se vendió ese par
        grande, sobra, no = (self.toma.detalles.get(sku=s) for s in ('9800003', '9800001', '9800004'))

        prep = self._post('api_preparar_reconteo_masivo', self.toma.id, {'ids': [grande.id, sobra.id, no.id]})
        self.assertTrue(prep['success'], prep)
        lineas = {l['sku']: l for l in prep['lineas']}
        self.assertEqual(set(lineas), {'9800003', '9800001'})
        self.assertEqual([o['sku'] for o in prep['omitidas']], ['9800004'])  # sin contar
        g = lineas['9800003']
        self.assertEqual((g['sistema'], g['contado'], g['movido'], g['hoy'], g['espera_reconteo']), (4, 1, -1, 0, True))
        self.assertFalse(lineas['9800001']['espera_reconteo'])

        # Lo que se dejó igual se acepta; aceptar algo que no esperaba reconteo no hace nada
        resp = self._post('api_registrar_reconteo', self.toma.id, {'reconteos': [
            {'detalle_id': grande.id, 'aceptar_conteo': True, 'observaciones': 'sin tiempo'},
            {'detalle_id': sobra.id, 'aceptar_conteo': True},
        ]})
        self.assertTrue(resp['success'], resp)
        self.assertEqual((resp['aceptados'], resp['encontrados'], resp['reconteos_realizados']), (['9800003'], [], 1))
        self.assertIn('no esperaba reconteo', resp['errores'][0])
        grande.refresh_from_db()
        # El conteo queda como lo leyó la pistola (la venta de hoy NO se descuenta otra vez)
        self.assertEqual((grande.stock_fisico, grande.diferencia, grande.reconteo_requerido, grande.stock_reconteo),
                         (1, -3, False, 0))
        self.assertIn('Conteo de pistola aceptado sin recontar', grande.observaciones)
        self.assertIn('sin tiempo', grande.observaciones)
        self.assertFalse(self.toma.reconteos_pendientes().exists())
        self.assertIn('aceptados según la pistola', self.toma.logs.filter(tipo_accion='RECONTEO').latest('created_at').descripcion)

        # Ya no espera reconteo: entra en «Ajustar stock ya» y el stock queda en lo de la pistola menos la venta
        self.assertTrue(self._ajustar_ya()['success'])
        self.assertEqual(self._stock(self.pt_grande), 0)

    def test_reconteo_masivo_mezcla_aceptar_y_recontar(self):
        self._como(self.maestro)
        self._vender_grande_hoy()
        grande = self.toma.detalles.get(sku='9800003')
        falta = self.toma.detalles.get(sku='9800002')
        # FALTA (pistola 2) se recontó de verdad: hay 3 → encontrado; GRANDE se acepta
        resp = self._post('api_registrar_reconteo', self.toma.id, {'reconteos': [
            {'detalle_id': grande.id, 'aceptar_conteo': True},
            {'detalle_id': falta.id, 'stock_reconteo': 3},
        ]})
        self.assertTrue(resp['success'], resp)
        self.assertFalse(resp['errores'])
        self.assertEqual((resp['aceptados'], resp['encontrados'], resp['reconteos_realizados']), (['9800003'], ['9800002'], 2))
        falta.refresh_from_db()
        self.assertEqual((falta.stock_fisico, falta.diferencia), (3, 0))

    def test_reconteo_masivo_es_solo_del_maestro(self):
        grande = self.toma.detalles.get(sku='9800003')
        # Administrador: opera la toma pero no se salta el reconteo
        self.assertNotIn('id="btnReconteoMasivo"',
                         self.client.get(reverse('detalle_inventario', args=[self.toma.id])).content.decode())
        resp = self.client.post(reverse('api_preparar_reconteo_masivo', args=[self.toma.id]),
                                data=json.dumps({'ids': [grande.id]}), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        resp = self._post('api_registrar_reconteo', self.toma.id,
                          {'reconteos': [{'detalle_id': grande.id, 'aceptar_conteo': True}]})
        self.assertEqual(resp['reconteos_realizados'], 0)
        self.assertIn('Maestro', resp['errores'][0])
        self._como(self.jefe)
        resp = self._post('api_registrar_reconteo', self.toma.id,
                          {'reconteos': [{'detalle_id': grande.id, 'aceptar_conteo': True}]})
        self.assertEqual(resp['reconteos_realizados'], 0)
        grande.refresh_from_db()
        self.assertTrue(grande.reconteo_requerido)
        self.assertIsNone(grande.stock_reconteo)
