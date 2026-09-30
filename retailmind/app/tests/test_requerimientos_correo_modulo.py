"""
Requerimientos (30-sep): correo FIJO del módulo, correo recordado por
proveedor y fotos después del alta.

- El correo del módulo (Configuración) manda sobre el correo personal de quien
  envía: copia de control, Reply-To, botones Aprobar/Rechazar y contacto.
- El correo escrito al enviar se recuerda para el proveedor y los siguientes
  envíos van ahí, los haga quien los haga.
- La ficha deja subir, reemplazar y quitar fotos mientras el caso está abierto.
"""
import json
import shutil
import tempfile
from io import BytesIO

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from app.models import (
    ConfiguracionRequerimientos, CorreoProveedorRequerimiento,
    FotoRequerimiento, Requerimiento, TipoFotoRequerimiento,
)
from app.tests.factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
)

MEDIA_TEMPORAL = tempfile.mkdtemp(prefix='req_correo_modulo_')


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='sistema@test.cl',
    CORREO_BUZON_RESPUESTAS='',
    CORREO_BASE_URL='',
)
class BaseCorreoModulo(TestCase):

    def setUp(self):
        self.admin = crear_usuario(
            username='admin_cm', rol='administrador', email='personal.admin@test.com')
        self.empresa = crear_empresa(nombre='Holding CM')
        self.sucursal = crear_sucursal(empresa=self.empresa)
        crear_empresa_user(self.admin, self.empresa, self.sucursal)

        self.proveedor = crear_empresa(
            nombre='Proveedor CM', rut='77.222.222-2', esProveedor=True,
            correoVendedor='ficha@proveedor.cl', correoIntercambio='', correoAdministrador='',
        )
        self.req = Requerimiento.objects.create(
            tipo='PRODUCTO_FALLADO', sucursal=self.sucursal, usuario_creador=self.admin,
            sku='555', nombre_producto='Zapatilla CM', cliente_nombre='Cliente CM',
            motivo='Suela despegada', proveedor=self.proveedor,
        )
        self.client.force_login(self.admin)

    def _post_json(self, url, payload=None):
        return self.client.post(url, data=json.dumps(payload or {}),
                                content_type='application/json')

    def _enviar(self, payload=None, req=None):
        req = req or self.req
        return self._post_json(reverse('api_enviar_a_proveedor', args=[req.id]), payload)


class CorreoModuloEnvioTest(BaseCorreoModulo):

    def test_configurado_manda_sobre_el_correo_del_usuario(self):
        ConfiguracionRequerimientos.objects.create(pk=1, correo_modulo='fallados@miempresa.cl')

        # Aunque el POST traiga otra copia (clientes viejos), manda el del módulo.
        resp = self._enviar({'correo_copia': 'otra@cosa.cl'})

        self.assertTrue(resp.json()['success'], resp.json())
        self.assertEqual(len(mail.outbox), 2)
        al_proveedor, copia = mail.outbox
        self.assertEqual(al_proveedor.reply_to, ['fallados@miempresa.cl'])
        self.assertEqual(copia.to, ['fallados@miempresa.cl'])
        cuerpo = al_proveedor.alternatives[0][0]
        self.assertIn('mailto:fallados@miempresa.cl', cuerpo)
        self.assertNotIn('personal.admin@test.com', cuerpo)
        self.assertEqual(resp.json()['correo_respuesta'], 'fallados@miempresa.cl')

    def test_mismo_correo_lo_envie_quien_lo_envie(self):
        ConfiguracionRequerimientos.objects.create(pk=1, correo_modulo='fallados@miempresa.cl')
        otro_admin = crear_usuario(username='admin2_cm', rol='administrador',
                                   email='otra.persona@test.com')
        crear_empresa_user(otro_admin, self.empresa, self.sucursal)
        self.client.force_login(otro_admin)

        self._enviar()

        self.assertEqual(mail.outbox[0].reply_to, ['fallados@miempresa.cl'])
        self.assertEqual(mail.outbox[1].to, ['fallados@miempresa.cl'])

    def test_sin_configurar_se_mantiene_el_comportamiento_anterior(self):
        resp = self._enviar()

        self.assertTrue(resp.json()['success'])
        self.assertEqual(mail.outbox[1].to, ['personal.admin@test.com'])
        self.assertIn('personal.admin@test.com', mail.outbox[0].reply_to)


class CorreoProveedorRecordadoTest(BaseCorreoModulo):

    def test_recordar_correo_y_usarlo_en_el_siguiente_envio(self):
        resp = self._enviar({'correo_destino': 'garantias@proveedor.cl', 'recordar_correo': True})
        self.assertTrue(resp.json()['correo_recordado'])
        self.assertEqual(
            CorreoProveedorRequerimiento.objects.get(proveedor=self.proveedor).correo,
            'garantias@proveedor.cl')

        # Otro requerimiento del mismo proveedor, sin escribir correo: va al
        # recordado y no al de la ficha.
        otro = Requerimiento.objects.create(
            tipo='GARANTIA', sucursal=self.sucursal, usuario_creador=self.admin,
            sku='556', nombre_producto='Otra', cliente_nombre='X', motivo='Y',
            proveedor=self.proveedor,
        )
        mail.outbox.clear()
        resp2 = self._enviar(req=otro)
        self.assertEqual(resp2.json()['correo_destino'], 'garantias@proveedor.cl')
        self.assertEqual(mail.outbox[0].to, ['garantias@proveedor.cl'])

    def test_sin_marcar_recordar_no_se_guarda(self):
        self._enviar({'correo_destino': 'puntual@proveedor.cl'})
        self.assertFalse(CorreoProveedorRequerimiento.objects.exists())

    def test_el_recordado_manda_sobre_el_ultimo_destino_en_un_recordatorio(self):
        self._enviar({'correo_destino': 'reboto@proveedor.cl'})
        CorreoProveedorRequerimiento.objects.create(
            proveedor=self.proveedor, correo='corregido@proveedor.cl')
        mail.outbox.clear()

        resp = self._enviar({'es_reenvio': True})

        self.assertEqual(resp.json()['correo_destino'], 'corregido@proveedor.cl')

    def test_endpoint_guardar_correo_proveedor(self):
        url = reverse('api_correo_proveedor_requerimientos', args=[self.proveedor.id])
        resp = self._post_json(url, {'correo': 'nuevo@proveedor.cl'})
        self.assertTrue(resp.json()['success'])
        self.assertEqual(CorreoProveedorRequerimiento.objects.get().correo, 'nuevo@proveedor.cl')

        self.assertEqual(self._post_json(url, {'correo': 'no-es-correo'}).status_code, 400)

        vendedor = crear_usuario(username='vend_cm', rol='vendedor')
        crear_empresa_user(vendedor, self.empresa, self.sucursal)
        self.client.force_login(vendedor)
        self.assertEqual(self._post_json(url, {'correo': 'x@y.cl'}).status_code, 403)

    def test_detalle_expone_el_correo_recordado_y_el_del_modulo(self):
        ConfiguracionRequerimientos.objects.create(pk=1, correo_modulo='fallados@miempresa.cl')
        CorreoProveedorRequerimiento.objects.create(proveedor=self.proveedor, correo='g@p.cl')

        r = self.client.get(reverse('api_detalle_requerimiento', args=[self.req.id])).json()['requerimiento']

        self.assertEqual(r['proveedor']['correo'], 'g@p.cl')
        self.assertEqual(r['proveedor']['correo_guardado'], 'g@p.cl')
        self.assertEqual(r['proveedor']['correo_ficha'], 'ficha@proveedor.cl')
        self.assertEqual(r['correo_modulo'], 'fallados@miempresa.cl')
        self.assertEqual(r['correo_modulo_origen'], 'configurado')


class ConfiguracionCorreoModuloTest(BaseCorreoModulo):

    def setUp(self):
        super().setUp()
        self.url = reverse('api_configuracion_correo_requerimientos')

    def test_admin_lo_define_y_queda_para_todos(self):
        resp = self._post_json(self.url, {'correo_modulo': 'fallados@miempresa.cl'})

        self.assertTrue(resp.json()['success'])
        config = ConfiguracionRequerimientos.objects.get(pk=1)
        self.assertEqual(config.correo_modulo, 'fallados@miempresa.cl')
        self.assertEqual(config.actualizado_por, self.admin)

        datos = self.client.get(self.url).json()['config']
        self.assertEqual(datos['correo_modulo'], 'fallados@miempresa.cl')
        self.assertEqual(datos['origen'], 'configurado')
        self.assertTrue(datos['puede_editar'])

    def test_valida_el_correo(self):
        self.assertEqual(self._post_json(self.url, {'correo_modulo': 'malo'}).status_code, 400)
        self.assertEqual(self._post_json(self.url, {'correo_modulo': ''}).status_code, 400)

    def test_vendedor_lo_ve_pero_no_lo_cambia(self):
        vendedor = crear_usuario(username='vend_cm2', rol='vendedor')
        crear_empresa_user(vendedor, self.empresa, self.sucursal)
        self.client.force_login(vendedor)

        self.assertTrue(self.client.get(self.url).json()['success'])
        self.assertFalse(self.client.get(self.url).json()['config']['puede_editar'])
        self.assertEqual(self._post_json(self.url, {'correo_modulo': 'a@b.cl'}).status_code, 403)


class FotosDespuesDelAltaTest(BaseCorreoModulo):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.files.storage import FileSystemStorage
        # El storage del campo se resuelve al importar el modelo: si el .env
        # trae credenciales de Spaces, sin esto las fotos de prueba suben al
        # bucket real.
        cls._campo = FotoRequerimiento._meta.get_field('imagen')
        cls._storage_original = cls._campo.storage
        cls._campo.storage = FileSystemStorage(location=MEDIA_TEMPORAL)

    @classmethod
    def tearDownClass(cls):
        cls._campo.storage = cls._storage_original
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEMPORAL, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.tipo_defecto, _ = TipoFotoRequerimiento.objects.update_or_create(
            codigo='FOTO_DEFECTO_TEST',
            defaults={'nombre': 'Defecto', 'tipos_requerimiento': ['PRODUCTO_FALLADO'],
                      'es_obligatorio': True, 'activo': True, 'orden': 1},
        )
        self.url = reverse('api_subir_fotos_requerimiento', args=[self.req.id])

    def _imagen(self, nombre='f.jpg'):
        from PIL import Image
        buf = BytesIO()
        Image.new('RGB', (20, 20), color='blue').save(buf, format='JPEG')
        return SimpleUploadedFile(nombre, buf.getvalue(), content_type='image/jpeg')

    def test_subir_reemplazar_y_quitar(self):
        resp = self.client.post(self.url, {'foto_FOTO_DEFECTO_TEST': self._imagen('a.jpg')})
        self.assertTrue(resp.json()['success'], resp.json())
        self.assertEqual(self.req.fotos.count(), 1)

        # Reemplazar la guiada no suma una segunda del mismo tipo
        self.client.post(self.url, {'foto_FOTO_DEFECTO_TEST': self._imagen('b.jpg')})
        self.assertEqual(self.req.fotos.filter(tipo_foto=self.tipo_defecto).count(), 1)

        self.client.post(self.url, {'foto_adicional_1': self._imagen('c.jpg'),
                                    'foto_adicional_2': self._imagen('d.jpg')})
        self.assertEqual(self.req.fotos.count(), 3)

        foto = self.req.fotos.first()
        resp = self._post_json(reverse('api_eliminar_foto_requerimiento', args=[self.req.id, foto.id]))
        self.assertTrue(resp.json()['success'])
        self.assertEqual(self.req.fotos.count(), 2)

        acciones = list(self.req.historial.values_list('accion', flat=True))
        self.assertIn('FOTOS_AGREGADAS', acciones)
        self.assertIn('FOTO_ELIMINADA', acciones)

    def test_respeta_el_maximo_del_tipo(self):
        self.req.tipo = 'CONSULTA'  # tope 3
        self.req.save()
        archivos = {f'foto_adicional_{i}': self._imagen(f'{i}.jpg') for i in range(1, 5)}
        resp = self.client.post(self.url, archivos)
        self.assertTrue(resp.json()['success'])
        self.assertEqual(self.req.fotos.count(), 3)
        self.assertEqual(len(resp.json()['omitidas']), 1)

    def test_rechaza_archivos_que_no_son_imagen(self):
        pdf = SimpleUploadedFile('x.pdf', b'%PDF-1.4', content_type='application/pdf')
        resp = self.client.post(self.url, {'foto_adicional_1': pdf})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(self.req.fotos.exists())

    def test_caso_cerrado_no_admite_cambios(self):
        self.req.estado = 'COMPLETADO'
        self.req.save()
        resp = self.client.post(self.url, {'foto_adicional_1': self._imagen()})
        self.assertEqual(resp.status_code, 403)

    def test_vendedor_solo_en_lo_suyo_y_pendiente(self):
        vendedor = crear_usuario(username='vend_fotos', rol='vendedor')
        crear_empresa_user(vendedor, self.empresa, self.sucursal)
        self.client.force_login(vendedor)
        # No es suyo
        self.assertEqual(self.client.post(self.url, {'foto_adicional_1': self._imagen()}).status_code, 403)

        propio = Requerimiento.objects.create(
            tipo='PRODUCTO_FALLADO', sucursal=self.sucursal, usuario_creador=vendedor,
            sku='9', nombre_producto='P', cliente_nombre='C', motivo='M',
        )
        url = reverse('api_subir_fotos_requerimiento', args=[propio.id])
        self.assertEqual(self.client.post(url, {'foto_adicional_1': self._imagen()}).status_code, 200)

    def test_siguiente_paso_manda_a_subir_fotos_si_es_lo_unico_que_falta(self):
        self.req.numero_factura_compra = '123'
        self.req.save()
        r = self.client.get(reverse('api_detalle_requerimiento', args=[self.req.id])).json()['requerimiento']
        self.assertEqual(r['faltantes'], ['fotos'])
        self.assertEqual(r['siguiente_paso']['accion'], 'fotos')
        self.assertTrue(r['permisos']['puede_gestionar_fotos'])
