"""
Regresión: los flujos de edición de precios de verGestionProducto deben dejar
el precio ORIGINAL en HistorialCambioPrecio antes de pisarlo.

Cubre el hoyo detectado 2026-07-29: la Edición rápida y la Edición masiva
(/app/productos/actualizar-masivo/) y el modal de producto existente
(/app/actualizar_producto_existente/) sobrescribían costo/sobreprecio/
precioventa (y los lotes FIFO activos) sin registrar historial.
"""
import json

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from app.models import HistorialCambioPrecio
from app.services.historial_precios import registrar_cambios_precio
from .factories import (
    crear_empresa,
    crear_empresa_user,
    crear_lote_fifo,
    crear_producto_con_talla,
    crear_sucursal,
    crear_usuario,
)


STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class HistorialPreciosEdicionTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa(nombre='Empresa Historial')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='SUC-HIST')
        self.admin = crear_usuario(username='admin-historial', rol='administrador')
        crear_empresa_user(self.admin, self.empresa, self.sucursal)

        self.producto, self.talla = crear_producto_con_talla(
            self.sucursal, articulo='ZAPHIST01', sku=9100001,
            costo=10000, sobreprecio=14000, precioventa=19990,
        )

        self.client = Client()
        self.client.force_login(self.admin)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    # ---------- helper directo ----------

    def test_helper_crea_una_fila_por_campo_cambiado(self):
        anteriores = {'costo': 10000, 'sobreprecio': 14000, 'precioventa': 19990}
        self.producto.costo = 12000
        self.producto.precioventa = 24990
        # sobreprecio queda igual → no debe generar fila
        creadas = registrar_cambios_precio(
            self.producto, anteriores, usuario=self.admin,
            motivo='Test unitario', tipo_cambio='MANUAL',
        )
        self.assertEqual(creadas, 2)
        motivos = set(
            HistorialCambioPrecio.objects.filter(producto=self.producto)
            .values_list('motivo', flat=True)
        )
        self.assertEqual(motivos, {'[COSTO] Test unitario', '[PRECIO_VENTA] Test unitario'})
        fila_pv = HistorialCambioPrecio.objects.get(
            producto=self.producto, motivo__startswith='[PRECIO_VENTA]'
        )
        self.assertEqual(fila_pv.precio_anterior, 19990)
        self.assertEqual(fila_pv.precio_nuevo, 24990)

    def test_helper_sin_cambios_no_crea_filas(self):
        anteriores = {'costo': 10000, 'sobreprecio': 14000, 'precioventa': 19990}
        creadas = registrar_cambios_precio(self.producto, anteriores, usuario=self.admin)
        self.assertEqual(creadas, 0)
        self.assertEqual(HistorialCambioPrecio.objects.count(), 0)

    # ---------- /app/productos/actualizar-masivo/ (Edición rápida) ----------

    def _post_actualizar_masivo(self, producto_ids, propagar=False, **precios):
        campos = {'aplicar_precios': True}
        campos.update(precios)
        return self.client.post(
            reverse('actualizar_productos_masivo'),
            data=json.dumps({
                'producto_ids': producto_ids,
                'campos': campos,
                'propagar_sucursales': propagar,
            }),
            content_type='application/json',
        )

    def test_edicion_rapida_registra_historial_y_lotes(self):
        lote = crear_lote_fifo(
            self.talla, cantidad=5, costo_unitario=10000,
            sobreprecio_unitario=14000, precio_venta_unitario=19990,
        )
        resp = self._post_actualizar_masivo(
            [self.producto.id], costo=12000, sobreprecio=16000, precioventa=24990,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])

        filas = HistorialCambioPrecio.objects.filter(producto=self.producto)
        self.assertEqual(filas.count(), 3)
        self.assertEqual(set(filas.values_list('tipo_cambio', flat=True)), {'MANUAL'})

        fila_pv = filas.get(motivo__startswith='[PRECIO_VENTA]')
        self.assertEqual(fila_pv.precio_anterior, 19990)
        self.assertEqual(fila_pv.precio_nuevo, 24990)
        self.assertEqual(fila_pv.lotes_afectados, 1)
        self.assertEqual(fila_pv.usuario, self.admin)

        fila_costo = filas.get(motivo__startswith='[COSTO]')
        self.assertEqual(fila_costo.precio_anterior, 10000)
        self.assertEqual(fila_costo.precio_nuevo, 12000)

        lote.refresh_from_db()
        self.assertEqual(lote.precio_venta_unitario, 24990)

    def test_edicion_rapida_sin_cambio_de_valores_no_registra(self):
        resp = self._post_actualizar_masivo(
            [self.producto.id], costo=10000, sobreprecio=14000, precioventa=19990,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(HistorialCambioPrecio.objects.count(), 0)

    def test_edicion_propagada_registra_por_cada_ficha(self):
        sucursal2 = crear_sucursal(empresa=self.empresa, alias='SUC-HIST2')
        producto2, _ = crear_producto_con_talla(
            sucursal2, articulo='ZAPHIST01', sku=9100002,
            costo=9000, sobreprecio=13000, precioventa=18990,
        )
        resp = self._post_actualizar_masivo(
            [self.producto.id], propagar=True,
            costo=12000, sobreprecio=16000, precioventa=24990,
        )
        self.assertEqual(resp.status_code, 200)
        # 3 campos × 2 fichas = 6 filas, cada una con SU valor anterior
        self.assertEqual(HistorialCambioPrecio.objects.count(), 6)
        fila_pv2 = HistorialCambioPrecio.objects.get(
            producto=producto2, motivo__startswith='[PRECIO_VENTA]'
        )
        self.assertEqual(fila_pv2.precio_anterior, 18990)
        self.assertIn('propagada', fila_pv2.motivo)

    # ---------- /app/actualizar_producto_existente/ ----------

    def test_actualizar_producto_existente_registra_historial(self):
        resp = self.client.post(
            reverse('actualizar_producto_existente'),
            data=json.dumps({
                'producto_id': self.producto.id,
                'actualizar_precios': True,
                'costo': 11000,
                'sobreprecio': 15000,
                'precioventa': 21990,
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)

        filas = HistorialCambioPrecio.objects.filter(producto=self.producto)
        self.assertEqual(filas.count(), 3)
        self.assertEqual(set(filas.values_list('tipo_cambio', flat=True)), {'ACTUALIZACION_MANUAL'})
        fila_pv = filas.get(motivo__startswith='[PRECIO_VENTA]')
        self.assertEqual(fila_pv.precio_anterior, 19990)
        self.assertEqual(fila_pv.precio_nuevo, 21990)
