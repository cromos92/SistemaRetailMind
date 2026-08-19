"""
Tests del formato propio de requerimiento (PDF) y de los campos que lo alimentan.

Cubre:
- creación con origen STOCK (sin cliente) vs CLIENTE (cliente obligatorio)
- cantidad y respaldo de compra
- generación del PDF y su adjunto en el correo al proveedor
- sugerencia de proveedor a partir de la última factura de COMPRA del SKU
- alcance por rol del export a Excel (antes se filtraba todo el holding)
- decisión PARCIAL y fecha de respuesta declarada por el usuario
"""
import json
import shutil
import tempfile
from datetime import date, timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

# Las pruebas que suben fotos escriben en MEDIA_ROOT: sin aislarlo dejan
# archivos sueltos en el media/ real del proyecto.
MEDIA_TEMPORAL = tempfile.mkdtemp(prefix='req_fmt_media_')


class MediaAisladaMixin:
    """Aísla los archivos de prueba en un temporal.

    No basta con `override_settings(MEDIA_ROOT=...)`: el storage del campo se
    resuelve al importar el modelo, así que si el entorno trae credenciales de
    Spaces (el `.env` de trabajo las tiene) los tests suben las fotos de prueba
    al bucket REAL. Por eso se reemplaza el storage del campo, no el setting.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.files.storage import FileSystemStorage
        from app.models import FotoRequerimiento

        cls._campo_imagen = FotoRequerimiento._meta.get_field('imagen')
        cls._storage_original = cls._campo_imagen.storage
        cls._campo_imagen.storage = FileSystemStorage(location=MEDIA_TEMPORAL)

    @classmethod
    def tearDownClass(cls):
        cls._campo_imagen.storage = cls._storage_original
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEMPORAL, ignore_errors=True)

from app.models import (
    Dte, Dte_Productos, Requerimiento, TipoFotoRequerimiento,
)
from app.services.pdf_requerimiento_proveedor import generar_pdf_requerimiento
from app.tests.factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla,
)


class BaseRequerimientos(TestCase):
    """Armado común: admin con sucursal, proveedor y un producto con SKU."""

    def setUp(self):
        self.admin = crear_usuario(
            username='admin_fmt', rol='administrador', email='admin_fmt@test.com',
        )
        self.empresa = crear_empresa(nombre='Holding Formato', rut='76.111.111-1')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='TIENDA-1')
        crear_empresa_user(self.admin, self.empresa, self.sucursal)

        self.proveedor = crear_empresa(
            nombre='Proveedor Formato', rut='77.222.222-2', esProveedor=True,
            correoVendedor='ventas@proveedorformato.cl',
        )
        self.producto, self.producto_talla = crear_producto_con_talla(
            self.sucursal, articulo='ZAPATILLA RUNNING', talla='42',
        )
        self.client.force_login(self.admin)
        sesion = self.client.session
        sesion['idSucursalActual'] = self.sucursal.id
        sesion.save()

    def _crear_requerimiento(self, **kwargs):
        datos = dict(
            tipo='GARANTIA',
            sucursal=self.sucursal,
            usuario_creador=self.admin,
            producto_talla=self.producto_talla,
            sku=str(self.producto_talla.sku),
            nombre_producto='ZAPATILLA RUNNING',
            cliente_nombre='Cliente Test',
            motivo='Despegue de suela a los 20 días',
            proveedor=self.proveedor,
            cantidad=2,
        )
        datos.update(kwargs)
        return Requerimiento.objects.create(**datos)


class CrearRequerimientoOrigenTest(BaseRequerimientos):

    def setUp(self):
        super().setUp()
        self.url = reverse('api_crear_requerimiento')

    def _payload(self, **extra):
        datos = {
            'tipo': 'GARANTIA',
            'sku': str(self.producto_talla.sku),
            'nombre_producto': 'ZAPATILLA RUNNING',
            'motivo': 'Suela despegada',
        }
        datos.update(extra)
        return datos

    def test_origen_stock_no_exige_cliente(self):
        resp = self.client.post(self.url, self._payload(origen='STOCK', cantidad='3'))

        self.assertEqual(resp.status_code, 200, resp.content)
        req = Requerimiento.objects.get(id=resp.json()['requerimiento_id'])
        self.assertEqual(req.origen, 'STOCK')
        self.assertEqual(req.cliente_nombre, '')
        self.assertEqual(req.cantidad, 3)

    def test_origen_cliente_sigue_exigiendo_nombre(self):
        resp = self.client.post(self.url, self._payload(origen='CLIENTE'))

        self.assertEqual(resp.status_code, 400)
        self.assertIn('cliente', resp.json()['error'].lower())

    def test_origen_por_defecto_es_cliente(self):
        """Sin el campo, el comportamiento histórico se mantiene."""
        resp = self.client.post(self.url, self._payload())
        self.assertEqual(resp.status_code, 400)

    def test_guarda_respaldo_de_compra(self):
        resp = self.client.post(self.url, self._payload(
            origen='STOCK',
            numero_factura_compra='8842',
            fecha_factura_compra='2026-05-10',
        ))

        self.assertEqual(resp.status_code, 200, resp.content)
        req = Requerimiento.objects.get(id=resp.json()['requerimiento_id'])
        self.assertEqual(req.numero_factura_compra, '8842')
        self.assertEqual(req.fecha_factura_compra, date(2026, 5, 10))

    def test_tipo_invalido_rechazado(self):
        resp = self.client.post(self.url, self._payload(tipo='CUALQUIER_COSA'))
        self.assertEqual(resp.status_code, 400)

    def test_cantidad_invalida_cae_a_uno(self):
        resp = self.client.post(self.url, self._payload(origen='STOCK', cantidad='-5'))
        req = Requerimiento.objects.get(id=resp.json()['requerimiento_id'])
        self.assertEqual(req.cantidad, 1)

    def test_sucursal_ajena_rechazada(self):
        """La sucursal viene de la sesión: no puede ser de otra empresa."""
        otra = crear_sucursal(empresa=crear_empresa(nombre='Otra', rut='77.999.999-9'),
                              alias='AJENA')
        sesion = self.client.session
        sesion['idSucursalActual'] = otra.id
        sesion.save()

        resp = self.client.post(self.url, self._payload(origen='STOCK'))
        self.assertEqual(resp.status_code, 403)


@override_settings(MEDIA_ROOT=MEDIA_TEMPORAL)
class FormatoPdfTest(MediaAisladaMixin, BaseRequerimientos):

    def test_genera_pdf_con_datos_minimos(self):
        req = self._crear_requerimiento()
        pdf = generar_pdf_requerimiento(req, usuario=self.admin)

        self.assertTrue(pdf.startswith(b'%PDF'))
        # Portada + página de respuesta del proveedor
        self.assertGreaterEqual(pdf.count(b'/Type /Page'), 1)
        self.assertGreater(len(pdf), 2000)

    def test_pdf_sin_cliente_ni_factura_no_revienta(self):
        req = self._crear_requerimiento(
            origen='STOCK', cliente_nombre='', proveedor=None,
            producto_talla=None, subtipo='DESPEGUE_SUELA',
        )
        pdf = generar_pdf_requerimiento(req, usuario=self.admin)
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_endpoint_descarga_pdf(self):
        req = self._crear_requerimiento()
        url = reverse('api_formato_pdf_requerimiento', args=[req.id])

        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertIn('inline', resp['Content-Disposition'])
        self.assertIn(req.numero_requerimiento, resp['Content-Disposition'])

        resp_descarga = self.client.get(url, {'descargar': '1'})
        self.assertIn('attachment', resp_descarga['Content-Disposition'])

    def _adjuntar_foto(self, req, imagen_bytes, nombre='foto_test.jpg', orden=1):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from app.models import FotoRequerimiento
        return FotoRequerimiento.objects.create(
            requerimiento=req,
            imagen=SimpleUploadedFile(nombre, imagen_bytes, content_type='image/jpeg'),
            orden=orden, usuario=self.admin,
        )

    def test_incrusta_fotos_de_celular_rotadas_y_png_con_alpha(self):
        """Los dos formatos que rompen el pipeline si no se normalizan.

        - Foto de celular con EXIF Orientation=6: sin `exif_transpose` entra
          acostada al PDF.
        - PNG con canal alfa: `save(JPEG)` lanza "cannot write mode RGBA as
          JPEG" y la foto se perdería.
        """
        from io import BytesIO
        from PIL import Image as PILImage

        req = self._crear_requerimiento()

        apaisada = PILImage.new('RGB', (1600, 1200), color='#3366aa')
        exif = apaisada.getexif()
        exif[274] = 6  # Orientation: girar 90°
        buf_jpg = BytesIO()
        apaisada.save(buf_jpg, format='JPEG', exif=exif)
        self._adjuntar_foto(req, buf_jpg.getvalue(), 'celular.jpg', orden=1)

        buf_png = BytesIO()
        PILImage.new('RGBA', (800, 600), color=(200, 60, 60, 128)).save(buf_png, format='PNG')
        self._adjuntar_foto(req, buf_png.getvalue(), 'captura.png', orden=2)

        pdf = generar_pdf_requerimiento(req, usuario=self.admin)

        self.assertTrue(pdf.startswith(b'%PDF'))
        # Las DOS fotos quedaron incrustadas (si alguna falla se omite en silencio)
        self.assertEqual(pdf.count(b'/Subtype /Image'), 2)

    def test_foto_ilegible_no_tumba_el_documento(self):
        """Un archivo corrupto se salta y el formato igual se genera."""
        req = self._crear_requerimiento()
        self._adjuntar_foto(req, b'esto no es una imagen', 'rota.jpg')

        pdf = generar_pdf_requerimiento(req, usuario=self.admin)

        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(pdf.count(b'/Subtype /Image'), 0)

    def test_subtipo_display_traduce_el_codigo(self):
        req = self._crear_requerimiento(subtipo='DESPEGUE_SUELA')
        self.assertEqual(req.subtipo_display, 'Despegue de Suela')

        req_error = self._crear_requerimiento(tipo='ERROR_DESPACHO', subtipo='TALLA_INCORRECTA')
        self.assertEqual(req_error.subtipo_display, 'Talla Incorrecta')

        self.assertEqual(self._crear_requerimiento(subtipo=None).subtipo_display, '')


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='sistema@test.cl',
)
class EnvioConFormatoTest(BaseRequerimientos):

    def test_correo_al_proveedor_lleva_el_pdf_adjunto(self):
        req = self._crear_requerimiento(numero_factura_compra='8842')
        url = reverse('api_enviar_a_proveedor', args=[req.id])

        resp = self.client.post(url, data=json.dumps({'correo_copia': ''}),
                                content_type='application/json')

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['formato_pdf_adjunto'])

        correo = mail.outbox[0]
        adjuntos = {nombre: contenido for nombre, contenido, _ in correo.attachments}
        nombre_pdf = f'Requerimiento_{req.numero_requerimiento}.pdf'
        self.assertIn(nombre_pdf, adjuntos)
        self.assertTrue(adjuntos[nombre_pdf].startswith(b'%PDF'))

    def test_asunto_trae_sku_y_factura(self):
        req = self._crear_requerimiento(numero_factura_compra='8842')
        url = reverse('api_enviar_a_proveedor', args=[req.id])

        self.client.post(url, data=json.dumps({}), content_type='application/json')

        asunto = mail.outbox[0].subject
        self.assertIn(req.numero_requerimiento, asunto)
        self.assertIn(str(req.sku), asunto)
        self.assertIn('FAC 8842', asunto)
        self.assertIn('[GARANTIA PRODUCTO]', asunto.upper())

    def test_requerimiento_sin_cliente_renderiza_la_rama_stock(self):
        """La plantilla tiene dos ramas (con y sin cliente): se prueban ambas."""
        req = self._crear_requerimiento(origen='STOCK', cliente_nombre='')
        url = reverse('api_enviar_a_proveedor', args=[req.id])

        resp = self.client.post(url, data=json.dumps({}), content_type='application/json')

        self.assertEqual(resp.status_code, 200, resp.content)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('Detectado en mercadería de la tienda', html)
        self.assertNotIn('Cliente afectado', html)

    def test_historial_deja_rastro_del_formato(self):
        req = self._crear_requerimiento()
        url = reverse('api_enviar_a_proveedor', args=[req.id])

        self.client.post(url, data=json.dumps({}), content_type='application/json')

        comentario = req.historial.filter(accion='ENVIADO_A_PROVEEDOR').first().comentario
        self.assertIn('con formato PDF', comentario)


class RespuestaProveedorTest(BaseRequerimientos):

    def test_parcial_queda_aprobado_no_rechazado(self):
        req = self._crear_requerimiento(estado='ESPERANDO_RESPUESTA')
        url = reverse('api_registrar_respuesta_proveedor', args=[req.id])

        resp = self.client.post(url, data=json.dumps({
            'decision': 'PARCIAL',
            'respuesta': 'Aceptan 1 de 2 pares',
            'motivo': 'El proveedor aprueba parcialmente',
        }), content_type='application/json')

        self.assertEqual(resp.status_code, 200, resp.content)
        req.refresh_from_db()
        self.assertEqual(req.decision_proveedor, 'PARCIAL')
        self.assertEqual(req.estado, 'APROBADO')

    def test_usa_la_fecha_declarada_por_el_usuario(self):
        req = self._crear_requerimiento(estado='ESPERANDO_RESPUESTA')
        url = reverse('api_registrar_respuesta_proveedor', args=[req.id])
        hace_tres_dias = (timezone.localtime() - timedelta(days=3)).strftime('%Y-%m-%dT%H:%M')

        self.client.post(url, data=json.dumps({
            'decision': 'APROBADO',
            'respuesta': 'Aprobado por correo el lunes',
            'motivo': 'Aprobado',
            'fecha_respuesta': hace_tres_dias,
        }), content_type='application/json')

        req.refresh_from_db()
        self.assertEqual(
            timezone.localtime(req.fecha_respuesta_proveedor).strftime('%Y-%m-%dT%H:%M'),
            hace_tres_dias,
        )

    def test_fecha_invalida_no_rompe_el_registro(self):
        req = self._crear_requerimiento(estado='ESPERANDO_RESPUESTA')
        url = reverse('api_registrar_respuesta_proveedor', args=[req.id])

        resp = self.client.post(url, data=json.dumps({
            'decision': 'RECHAZADO',
            'respuesta': 'No procede',
            'motivo': 'Uso indebido',
            'fecha_respuesta': 'cualquier cosa',
        }), content_type='application/json')

        self.assertEqual(resp.status_code, 200)
        req.refresh_from_db()
        self.assertIsNotNone(req.fecha_respuesta_proveedor)
        self.assertEqual(req.estado, 'RECHAZADO')


class EditarRequerimientoTest(BaseRequerimientos):
    """Completar lo que la tienda no sabe: proveedor y factura de compra."""

    def setUp(self):
        super().setUp()
        self.req = self._crear_requerimiento(proveedor=None, cliente_rut='')
        self.url = reverse('api_editar_requerimiento', args=[self.req.id])

    def _editar(self, payload):
        return self.client.post(self.url, data=json.dumps(payload),
                                content_type='application/json')

    def test_administrador_completa_proveedor_y_factura(self):
        resp = self._editar({
            'proveedor_id': self.proveedor.id,
            'numero_factura_compra': '8842',
            'fecha_factura_compra': '2026-05-10',
        })

        self.assertEqual(resp.status_code, 200, resp.content)
        self.req.refresh_from_db()
        self.assertEqual(self.req.proveedor_id, self.proveedor.id)
        self.assertEqual(self.req.numero_factura_compra, '8842')
        self.assertEqual(self.req.fecha_factura_compra, date(2026, 5, 10))

    def test_deja_rastro_en_el_historial(self):
        self._editar({'numero_factura_compra': '8842', 'cliente_rut': '12.345.678-9'})

        hist = self.req.historial.filter(accion='DATOS_ACTUALIZADOS').first()
        self.assertIsNotNone(hist)
        self.assertIn('8842', hist.comentario)
        self.assertIn('12.345.678-9', hist.comentario)
        self.assertEqual(hist.usuario, self.admin)

    def test_no_guarda_ni_registra_si_no_cambio_nada(self):
        self._editar({'motivo': self.req.motivo, 'cantidad': self.req.cantidad})

        self.assertFalse(self.req.historial.filter(accion='DATOS_ACTUALIZADOS').exists())

    def test_campos_no_listados_se_ignoran(self):
        """Un payload malicioso no puede cambiar estado ni sucursal."""
        estado_previo = self.req.estado
        self._editar({'estado': 'COMPLETADO', 'sucursal_id': 999, 'sku': 'HACKEADO'})

        self.req.refresh_from_db()
        self.assertEqual(self.req.estado, estado_previo)
        self.assertEqual(self.req.sucursal_id, self.sucursal.id)
        self.assertNotEqual(self.req.sku, 'HACKEADO')

    def test_completado_ya_no_se_edita(self):
        self.req.estado = 'COMPLETADO'
        self.req.save(update_fields=['estado'])

        resp = self._editar({'numero_factura_compra': '9999'})

        self.assertEqual(resp.status_code, 400)
        self.req.refresh_from_db()
        self.assertIsNone(self.req.numero_factura_compra)

    def test_vendedor_ajeno_no_puede_editar(self):
        otro = crear_usuario(username='vend_ajeno', rol='vendedor', email='v@test.com')
        crear_empresa_user(otro, self.empresa, self.sucursal)
        self.client.force_login(otro)

        resp = self._editar({'numero_factura_compra': '9999'})

        self.assertEqual(resp.status_code, 403)

    def test_jefe_local_edita_solo_su_sucursal(self):
        jefe = crear_usuario(username='jefe_fmt', rol='jefe_local', email='j@test.com')
        crear_empresa_user(jefe, self.empresa, self.sucursal)
        self.client.force_login(jefe)
        self.assertEqual(self._editar({'numero_factura_compra': '111'}).status_code, 200)

        ajena = crear_sucursal(empresa=self.empresa, alias='OTRA-SUC')
        req_ajeno = self._crear_requerimiento(sucursal=ajena, producto_talla=None)
        resp = self.client.post(
            reverse('api_editar_requerimiento', args=[req_ajeno.id]),
            data=json.dumps({'numero_factura_compra': '222'}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 403)


class StorageEvidenciasTest(TestCase):
    """El storage de la evidencia debe degradar con gracia, nunca romper la carga."""

    def setUp(self):
        # El callable cachea la instancia: se limpia entre pruebas.
        from app import storage_backends
        storage_backends._cache_storage = None

    def test_sin_credenciales_usa_el_storage_por_defecto(self):
        from django.core.files.storage import default_storage
        from app.storage_backends import storage_evidencias

        with override_settings(SPACES_HABILITADO=False):
            self.assertIs(storage_evidencias(), default_storage)

    def test_configurado_pero_sin_libreria_cae_al_default_y_no_explota(self):
        """Si falta django-storages, la foto se guarda igual (en disco)."""
        import builtins
        from django.core.files.storage import default_storage
        from app.storage_backends import storage_evidencias

        importar_real = builtins.__import__

        def importar_sin_storages(nombre, *args, **kwargs):
            if nombre.startswith('storages'):
                raise ImportError('simulado: django-storages no instalado')
            return importar_real(nombre, *args, **kwargs)

        with override_settings(SPACES_HABILITADO=True):
            builtins.__import__ = importar_sin_storages
            try:
                self.assertIs(storage_evidencias(), default_storage)
            finally:
                builtins.__import__ = importar_real


class SugerirProveedorTest(BaseRequerimientos):

    def setUp(self):
        super().setUp()
        self.url = reverse('api_sugerir_proveedor_requerimiento')

    def _crear_compra(self, numero=8842, fecha=None):
        dte = Dte.objects.create(
            emisor=self.proveedor,
            receptor=self.empresa,
            sucursal=self.sucursal,
            tipo_documento='FACTURA ELECTRONICA',
            numero_documento=numero,
            fecha_emision=fecha or date(2026, 5, 10),
            fecha_vencimiento=(fecha or date(2026, 5, 10)) + timedelta(days=30),
            tipo_transaccion='COMPRA',
            monto_neto=100000, monto_con_iva=119000,
            estado_pago='PENDIENTE', estado_dte='EMITIDO',
            responsable='Test', diasCredito=30, bultos=1, unidades_productos=4,
        )
        Dte_Productos.objects.create(
            dte=dte, productoTalla=self.producto_talla,
            descripcion='ZAPATILLA RUNNING', costo=25000,
            precio=25000, stock=4,
        )
        return dte

    def test_devuelve_proveedor_y_factura_de_la_ultima_compra(self):
        self._crear_compra(numero=8000, fecha=date(2026, 1, 5))
        ultima = self._crear_compra(numero=8842, fecha=date(2026, 5, 10))

        resp = self.client.get(self.url, {'sku': str(self.producto_talla.sku)})

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data['proveedor']['id'], self.proveedor.id)
        self.assertEqual(data['compra']['numero_documento'], ultima.numero_documento)
        self.assertEqual(data['compra']['fecha_emision'], '2026-05-10')

    def test_sku_sin_compras_devuelve_404(self):
        resp = self.client.get(self.url, {'sku': str(self.producto_talla.sku)})
        self.assertEqual(resp.status_code, 404)

    def test_sku_inexistente_devuelve_404(self):
        resp = self.client.get(self.url, {'sku': '999999999'})
        self.assertEqual(resp.status_code, 404)

    def test_sin_sku_devuelve_400(self):
        self.assertEqual(self.client.get(self.url).status_code, 400)


class BuscarComprasTest(BaseRequerimientos):
    """El buscador con el que quien revisa identifica QUÉ factura reclamar."""

    def setUp(self):
        super().setUp()
        self.url = reverse('api_buscar_compras_requerimiento')
        self.otro_proveedor = crear_empresa(
            nombre='Segundo Proveedor', rut='79.444.444-4', esProveedor=True)

    def _compra(self, proveedor, numero, fecha, cantidad=4, costo=25000):
        dte = Dte.objects.create(
            emisor=proveedor, receptor=self.empresa, sucursal=self.sucursal,
            tipo_documento='FACTURA ELECTRONICA', numero_documento=numero,
            fecha_emision=fecha, fecha_vencimiento=fecha + timedelta(days=30),
            tipo_transaccion='COMPRA', monto_neto=100000, monto_con_iva=119000,
            estado_pago='PENDIENTE', estado_dte='EMITIDO',
            responsable='Test', diasCredito=30, bultos=1, unidades_productos=cantidad,
        )
        Dte_Productos.objects.create(
            dte=dte, productoTalla=self.producto_talla,
            descripcion='ZAPATILLA RUNNING', costo=costo, precio=costo,
            stock=cantidad,
        )
        return dte

    def test_busca_por_sku_y_ordena_de_la_mas_nueva_a_la_mas_vieja(self):
        self._compra(self.proveedor, 8000, date(2026, 1, 5))
        self._compra(self.otro_proveedor, 9100, date(2026, 6, 20))

        resp = self.client.get(self.url, {'q': str(self.producto_talla.sku)})

        self.assertEqual(resp.status_code, 200, resp.content)
        compras = resp.json()['compras']
        self.assertEqual(len(compras), 2)
        self.assertEqual(compras[0]['numero_documento'], 9100)
        self.assertEqual(compras[0]['proveedor'], 'Segundo Proveedor')
        self.assertEqual(compras[1]['numero_documento'], 8000)

    def test_busca_por_nombre_de_articulo(self):
        self._compra(self.proveedor, 8842, date(2026, 5, 10))

        resp = self.client.get(self.url, {'q': 'RUNNING'})

        compras = resp.json()['compras']
        self.assertEqual(len(compras), 1)
        self.assertEqual(compras[0]['numero_documento'], 8842)
        self.assertEqual(compras[0]['cantidad'], 4)
        self.assertEqual(compras[0]['costo_unitario'], 25000)

    def test_ignora_ventas_solo_devuelve_compras(self):
        venta = Dte.objects.create(
            emisor=self.empresa, receptor=self.proveedor, sucursal=self.sucursal,
            tipo_documento='BOLETA ELECTRONICA', numero_documento=777,
            fecha_emision=date(2026, 7, 1), fecha_vencimiento=date(2026, 7, 1),
            tipo_transaccion='VENTA', monto_neto=1000, monto_con_iva=1190,
            estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable='Test', diasCredito=0, bultos=1, unidades_productos=1,
        )
        Dte_Productos.objects.create(
            dte=venta, productoTalla=self.producto_talla,
            descripcion='ZAPATILLA RUNNING', costo=0, precio=39990, stock=1)

        resp = self.client.get(self.url, {'q': str(self.producto_talla.sku)})

        self.assertEqual(resp.json()['compras'], [])

    def test_consulta_muy_corta_rechazada(self):
        self.assertEqual(self.client.get(self.url, {'q': 'a'}).status_code, 400)

    def test_sin_resultados_responde_lista_vacia(self):
        resp = self.client.get(self.url, {'q': 'NO_EXISTE_ESTE_ARTICULO'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['compras'], [])

    def test_usuario_acotado_no_ve_compras_de_otra_empresa(self):
        otra_empresa = crear_empresa(nombre='Holding Ajeno', rut='78.777.777-7')
        ajeno = Dte.objects.create(
            emisor=self.proveedor, receptor=otra_empresa, sucursal=None,
            tipo_documento='FACTURA ELECTRONICA', numero_documento=5555,
            fecha_emision=date(2026, 4, 1), fecha_vencimiento=date(2026, 5, 1),
            tipo_transaccion='COMPRA', monto_neto=1000, monto_con_iva=1190,
            estado_pago='PENDIENTE', estado_dte='EMITIDO',
            responsable='Test', diasCredito=30, bultos=1, unidades_productos=1,
        )
        Dte_Productos.objects.create(
            dte=ajeno, productoTalla=self.producto_talla,
            descripcion='ZAPATILLA RUNNING', costo=1, precio=1, stock=1)
        self._compra(self.proveedor, 8842, date(2026, 5, 10))

        vendedor = crear_usuario(username='vend_compras', rol='vendedor', email='vc@test.com')
        crear_empresa_user(vendedor, self.empresa, self.sucursal)
        self.client.force_login(vendedor)

        numeros = {c['numero_documento']
                   for c in self.client.get(self.url, {'q': str(self.producto_talla.sku)}).json()['compras']}

        self.assertIn(8842, numeros)
        self.assertNotIn(5555, numeros)


class ExportarAlcanceTest(BaseRequerimientos):
    """El export bajaba TODOS los requerimientos del holding a cualquier usuario."""

    def setUp(self):
        super().setUp()
        self.url = reverse('api_exportar_requerimientos')

        self.empresa_ajena = crear_empresa(nombre='Otro Holding', rut='78.333.333-3')
        self.sucursal_ajena = crear_sucursal(empresa=self.empresa_ajena, alias='AJENA')
        self.req_propio = self._crear_requerimiento(motivo='Propio de la tienda')
        self.req_ajeno = self._crear_requerimiento(
            sucursal=self.sucursal_ajena, motivo='De otra empresa', producto_talla=None,
        )

    def _filas(self, response):
        import openpyxl
        from io import BytesIO
        wb = openpyxl.load_workbook(BytesIO(response.content))
        ws = wb.active
        return [
            [celda.value for celda in fila]
            for fila in ws.iter_rows(min_row=2)
        ]

    def test_vendedor_solo_exporta_lo_de_su_alcance(self):
        vendedor = crear_usuario(username='vend_fmt', rol='vendedor',
                                 email='vend@test.com')
        crear_empresa_user(vendedor, self.empresa, self.sucursal)
        self.client.force_login(vendedor)

        filas = self._filas(self.client.get(self.url))

        numeros = {fila[0] for fila in filas}
        self.assertIn(self.req_propio.numero_requerimiento, numeros)
        self.assertNotIn(self.req_ajeno.numero_requerimiento, numeros)

    def test_administrador_sigue_viendo_todo(self):
        filas = self._filas(self.client.get(self.url))
        numeros = {fila[0] for fila in filas}
        self.assertIn(self.req_propio.numero_requerimiento, numeros)
        self.assertIn(self.req_ajeno.numero_requerimiento, numeros)


@override_settings(MEDIA_ROOT=MEDIA_TEMPORAL)
class FotosOrdenTest(MediaAisladaMixin, BaseRequerimientos):
    """Las fotos guiadas no deben perderse por el tope cuando hay adicionales."""

    def setUp(self):
        super().setUp()
        TipoFotoRequerimiento.objects.update_or_create(
            codigo='FOTO_GENERAL',
            defaults={
                'nombre': 'Foto general', 'descripcion_guia': 'General',
                'tipos_requerimiento': ['CONSULTA'], 'es_obligatorio': True, 'orden': 1,
            },
        )

    def _imagen(self, nombre):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from io import BytesIO
        from PIL import Image as PILImage

        buf = BytesIO()
        PILImage.new('RGB', (40, 40), color='red').save(buf, format='JPEG')
        return SimpleUploadedFile(nombre, buf.getvalue(), content_type='image/jpeg')

    def test_la_guiada_entra_aunque_haya_adicionales_de_sobra(self):
        # CONSULTA tope 3: se mandan 3 adicionales + 1 guiada
        resp = self.client.post(reverse('api_crear_requerimiento'), {
            'tipo': 'CONSULTA',
            'origen': 'STOCK',
            'sku': str(self.producto_talla.sku),
            'nombre_producto': 'ZAPATILLA RUNNING',
            'motivo': 'Consulta técnica',
            'foto_adicional_1': self._imagen('a1.jpg'),
            'foto_adicional_2': self._imagen('a2.jpg'),
            'foto_adicional_3': self._imagen('a3.jpg'),
            'foto_FOTO_GENERAL': self._imagen('general.jpg'),
        })

        self.assertEqual(resp.status_code, 200, resp.content)
        req = Requerimiento.objects.get(id=resp.json()['requerimiento_id'])
        codigos = list(req.fotos.values_list('tipo_foto__codigo', flat=True))
        self.assertIn('FOTO_GENERAL', codigos)
        self.assertEqual(req.fotos.count(), 3)
