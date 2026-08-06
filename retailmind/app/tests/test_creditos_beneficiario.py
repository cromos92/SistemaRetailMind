"""
Tests del documento de identidad del beneficiario de créditos.

Un extranjero sin cédula chilena no tenía cómo quedar identificado en la ficha
(el único campo era el RUT) y por eso no se le podía crear un crédito. Estos
tests cubren el alta y la edición con pasaporte:

  1. Alta con pasaporte: se guarda normalizado y sin arrastrar RUT.
  2. El pasaporte también es único: no se pisan dos beneficiarios distintos.
  3. El RUT sigue siendo el default y se valida el dígito verificador.
  4. Cambiar de RUT a pasaporte (y viceversa) deja un solo documento vigente.
  5. Editar una ficha migrada con RUT mal formado no queda bloqueada.
  6. El listado y el detalle del crédito muestran el documento que corresponde.
"""
import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from app.models import (
    Cliente, CreditoTrabajador, ModuloSistema, OpcionMenu, PermisoRol,
)
from app.views_modulo_creditos import _serializar_beneficiario

from .factories import crear_empresa, crear_sucursal, crear_usuario


def _habilitar_permiso_creditos(rol='administrador'):
    """El middleware de permisos exige `gestion_creditos.puede_ver` en estos endpoints.

    En una BD de test recién creada no existe ningún `PermisoRol`, así que sin
    esto todo responde 403 (JSON) o 302 (navegación) antes de llegar a la vista.
    """
    modulo, _ = ModuloSistema.objects.get_or_create(
        codigo='administracion', defaults={'nombre': 'Administración', 'orden': 9})
    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo='gestion_creditos',
        defaults={'modulo': modulo, 'nombre': 'Gestión de Créditos', 'orden': 1})
    PermisoRol.objects.update_or_create(
        rol=rol, opcion_menu=opcion,
        defaults={'puede_ver': True, 'puede_crear': True, 'puede_editar': True})
    return opcion


class DocumentoClienteModelTest(TestCase):
    """La ficha sabe qué documento mostrar sin tocar la BD."""

    def test_rut_es_el_documento_por_defecto(self):
        cliente = Cliente(nombre='Juan', apellido='Pérez', rut='11.111.111-1')
        self.assertEqual(cliente.tipo_documento, 'RUT')
        self.assertEqual(cliente.numero_documento, '11.111.111-1')
        self.assertEqual(cliente.documento_display, '11.111.111-1')

    def test_pasaporte_se_muestra_etiquetado(self):
        cliente = Cliente(
            nombre='Ana', apellido='Rodríguez',
            tipo_documento='PASAPORTE', pasaporte='AB1234567',
        )
        self.assertEqual(cliente.numero_documento, 'AB1234567')
        self.assertEqual(cliente.documento_display, 'Pasaporte AB1234567')

    def test_pasaporte_con_pais_lo_incluye(self):
        cliente = Cliente(
            nombre='Ana', apellido='Rodríguez',
            tipo_documento='PASAPORTE', pasaporte='AB1234567',
            pais_documento='Venezuela',
        )
        self.assertEqual(cliente.documento_display, 'Pasaporte AB1234567 (Venezuela)')

    def test_ficha_sin_documento_no_rompe(self):
        cliente = Cliente(nombre='Sin', apellido='Documento')
        self.assertEqual(cliente.numero_documento, '')
        self.assertEqual(cliente.documento_display, '')


class CrearBeneficiarioPasaporteTest(TestCase):
    """POST /app/api/creditos/trabajadores/crear/"""

    URL = '/app/api/creditos/trabajadores/crear/'

    def setUp(self):
        self.empresa = crear_empresa()
        self.usuario = crear_usuario(username='admin_creditos', rol='administrador')
        _habilitar_permiso_creditos()
        self.client.force_login(self.usuario)

    def _post(self, **payload):
        return self.client.post(
            self.URL, data=json.dumps(payload), content_type='application/json')

    def test_crea_beneficiario_con_pasaporte(self):
        resp = self._post(
            nombre='Ana Rodríguez',
            tipo_documento='PASAPORTE',
            pasaporte='ab123 4567',
            pais_documento='Venezuela',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])

        cliente = Cliente.objects.get(id=data['trabajador']['id'])
        self.assertEqual(cliente.tipo_documento, 'PASAPORTE')
        # Normalizado: sin espacios y en mayúsculas
        self.assertEqual(cliente.pasaporte, 'AB1234567')
        self.assertIsNone(cliente.rut)
        self.assertEqual(cliente.pais_documento, 'Venezuela')
        self.assertEqual(data['trabajador']['documento'], 'Pasaporte AB1234567 (Venezuela)')

    def test_pasaporte_duplicado_se_rechaza(self):
        Cliente.objects.create(
            nombre='Ana', apellido='Rodríguez',
            tipo_documento='PASAPORTE', pasaporte='AB1234567',
        )
        resp = self._post(
            nombre='Otro Homónimo',
            tipo_documento='PASAPORTE',
            pasaporte='ab1234567',  # mismo documento, otra caja
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('pasaporte', resp.json()['error'].lower())
        self.assertEqual(Cliente.objects.count(), 1)

    def test_pasaporte_vacio_se_rechaza(self):
        """Elegir PASAPORTE y no escribir nada dejaría la ficha sin ningún documento."""
        resp = self._post(nombre='Sin Documento', tipo_documento='PASAPORTE', pasaporte='')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Cliente.objects.exists())

    def test_ficha_solo_con_nombre_sigue_permitida(self):
        """El documento sigue siendo opcional mientras no se elija PASAPORTE."""
        resp = self._post(nombre='Sin Documento')
        self.assertEqual(resp.status_code, 200)
        cliente = Cliente.objects.get(id=resp.json()['trabajador']['id'])
        self.assertEqual(cliente.tipo_documento, 'RUT')
        self.assertIsNone(cliente.rut)
        self.assertIsNone(cliente.pasaporte)

    def test_pasaporte_muy_corto_se_rechaza(self):
        resp = self._post(nombre='Ana Rodríguez', tipo_documento='PASAPORTE', pasaporte='AB1')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Cliente.objects.exists())

    def test_pasaporte_con_simbolos_se_rechaza(self):
        resp = self._post(nombre='Ana Rodríguez', tipo_documento='PASAPORTE', pasaporte='AB/123*')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Cliente.objects.exists())

    def test_rut_sigue_siendo_el_default(self):
        resp = self._post(nombre='Juan Pérez', rut='11.111.111-1')
        self.assertEqual(resp.status_code, 200)
        cliente = Cliente.objects.get(id=resp.json()['trabajador']['id'])
        self.assertEqual(cliente.tipo_documento, 'RUT')
        self.assertEqual(cliente.rut, '11.111.111-1')
        self.assertIsNone(cliente.pasaporte)

    def test_rut_con_digito_verificador_malo_se_rechaza(self):
        resp = self._post(nombre='Juan Pérez', rut='11.111.111-9')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Cliente.objects.exists())

    def test_rut_duplicado_se_rechaza(self):
        Cliente.objects.create(nombre='Juan', apellido='Pérez', rut='11.111.111-1')
        resp = self._post(nombre='Otro Juan', rut='11.111.111-1')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Cliente.objects.count(), 1)


class EditarBeneficiarioDocumentoTest(TestCase):
    """POST /app/api/creditos/trabajadores/actualizar/"""

    URL = '/app/api/creditos/trabajadores/actualizar/'

    def setUp(self):
        self.empresa = crear_empresa()
        self.usuario = crear_usuario(username='admin_creditos_edit', rol='administrador')
        _habilitar_permiso_creditos()
        self.client.force_login(self.usuario)

    def _post(self, **payload):
        return self.client.post(
            self.URL, data=json.dumps(payload), content_type='application/json')

    def test_pasar_de_rut_a_pasaporte_limpia_el_rut(self):
        cliente = Cliente.objects.create(nombre='Ana', apellido='Rodríguez', rut='11.111.111-1')
        resp = self._post(
            trabajador_id=cliente.id,
            nombre='Ana Rodríguez',
            tipo_documento='PASAPORTE',
            pasaporte='AB1234567',
        )
        self.assertEqual(resp.status_code, 200)
        cliente.refresh_from_db()
        self.assertEqual(cliente.tipo_documento, 'PASAPORTE')
        self.assertEqual(cliente.pasaporte, 'AB1234567')
        self.assertIsNone(cliente.rut)

    def test_pasar_de_pasaporte_a_rut_limpia_el_pasaporte(self):
        cliente = Cliente.objects.create(
            nombre='Ana', apellido='Rodríguez',
            tipo_documento='PASAPORTE', pasaporte='AB1234567', pais_documento='Perú',
        )
        resp = self._post(
            trabajador_id=cliente.id,
            nombre='Ana Rodríguez',
            tipo_documento='RUT',
            rut='11.111.111-1',
        )
        self.assertEqual(resp.status_code, 200)
        cliente.refresh_from_db()
        self.assertEqual(cliente.tipo_documento, 'RUT')
        self.assertEqual(cliente.rut, '11.111.111-1')
        self.assertIsNone(cliente.pasaporte)
        self.assertIsNone(cliente.pais_documento)

    def test_pasaporte_de_otro_beneficiario_se_rechaza(self):
        Cliente.objects.create(
            nombre='Ana', apellido='Rodríguez',
            tipo_documento='PASAPORTE', pasaporte='AB1234567',
        )
        otro = Cliente.objects.create(nombre='Luis', apellido='Soto')
        resp = self._post(
            trabajador_id=otro.id,
            nombre='Luis Soto',
            tipo_documento='PASAPORTE',
            pasaporte='AB1234567',
        )
        self.assertEqual(resp.status_code, 400)
        otro.refresh_from_db()
        self.assertIsNone(otro.pasaporte)

    def test_rut_migrado_invalido_no_bloquea_la_edicion(self):
        """Una ficha legacy con RUT mal formado se sigue pudiendo corregir por otro lado."""
        cliente = Cliente.objects.create(nombre='Legacy', apellido='Migrado', rut='11.111.111-9')
        resp = self._post(
            trabajador_id=cliente.id,
            nombre='Legacy Migrado',
            rut='11.111.111-9',
            correo='legacy@test.com',
        )
        self.assertEqual(resp.status_code, 200)
        cliente.refresh_from_db()
        self.assertEqual(cliente.email, 'legacy@test.com')

    def test_cambiar_a_un_rut_invalido_si_se_rechaza(self):
        cliente = Cliente.objects.create(nombre='Legacy', apellido='Migrado', rut='11.111.111-9')
        resp = self._post(trabajador_id=cliente.id, nombre='Legacy Migrado', rut='22.222.222-1')
        self.assertEqual(resp.status_code, 400)
        cliente.refresh_from_db()
        self.assertEqual(cliente.rut, '11.111.111-9')


class ListadoYCreditoConPasaporteTest(TestCase):
    """El pasaporte tiene que llegar hasta la lista y hasta el crédito."""

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa)
        self.usuario = crear_usuario(username='admin_creditos_lista', rol='administrador')
        _habilitar_permiso_creditos()
        self.client.force_login(self.usuario)
        session = self.client.session
        session['idEmpresaActual'] = self.empresa.id
        session['idSucursalActual'] = self.sucursal.id
        session.save()
        self.cliente = Cliente.objects.create(
            nombre='Ana', apellido='Rodríguez',
            tipo_documento='PASAPORTE', pasaporte='AB1234567',
            empresa=self.empresa, activo=True,
        )

    def test_listado_de_beneficiarios_trae_el_documento(self):
        resp = self.client.get('/app/api/creditos/trabajadores/')
        self.assertEqual(resp.status_code, 200)
        fila = next(t for t in resp.json()['trabajadores'] if t['id'] == self.cliente.id)
        self.assertEqual(fila['tipo_documento'], 'PASAPORTE')
        self.assertEqual(fila['pasaporte'], 'AB1234567')
        self.assertEqual(fila['documento'], 'Pasaporte AB1234567')
        self.assertEqual(fila['rut'], '')

    def test_credito_serializa_el_pasaporte_del_beneficiario(self):
        credito = CreditoTrabajador.objects.create(
            beneficiario=self.cliente,
            empresa_origen=self.empresa,
            sucursal=self.sucursal,
            monto_solicitado=50000,
            fecha_vencimiento=timezone.localdate() + timedelta(days=30),
            motivo_solicitud='Anticipo',
            solicitado_por=self.usuario,
        )
        datos = _serializar_beneficiario(credito)
        self.assertEqual(datos['documento'], 'Pasaporte AB1234567')
        self.assertEqual(datos['tipo_documento'], 'PASAPORTE')
        self.assertEqual(datos['rut'], '')

    def _credito(self, **kwargs):
        defaults = dict(
            beneficiario=self.cliente,
            empresa_origen=self.empresa,
            sucursal=self.sucursal,
            monto_solicitado=50000,
            fecha_vencimiento=timezone.localdate() + timedelta(days=30),
            motivo_solicitud='Anticipo',
            solicitado_por=self.usuario,
        )
        defaults.update(kwargs)
        return CreditoTrabajador.objects.create(**defaults)

    def test_voucher_de_credito_pendiente_no_revienta(self):
        """Un crédito PENDIENTE no tiene autorizador ni monto aprobado (nace así
        cuando quien lo crea no puede aprobar): el voucher igual debe imprimirse."""
        credito = self._credito(estado='PENDIENTE')
        resp = self.client.get(f'/app/api/creditos/imprimir-voucher/{credito.id}/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('Pendiente de aprobación', html)
        self.assertIn('Monto Solicitado', html)
        self.assertIn('50,000', html)  # cae al monto solicitado

    def test_voucher_de_credito_aprobado_muestra_al_autorizador(self):
        credito = self._credito(
            estado='ACTIVO', monto_aprobado=50000,
            autorizado_por=self.usuario, fecha_aprobacion=timezone.now(),
        )
        resp = self.client.get(f'/app/api/creditos/imprimir-voucher/{credito.id}/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('Monto Aprobado', html)
        self.assertNotIn('Pendiente de aprobación', html)
        self.assertIn(self.usuario.get_full_name() or self.usuario.username, html)

    def test_voucher_usa_el_pasaporte_del_beneficiario(self):
        credito = self._credito(estado='ACTIVO', monto_aprobado=50000,
                                autorizado_por=self.usuario)
        resp = self.client.get(f'/app/api/creditos/imprimir-voucher/{credito.id}/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('AB1234567', html)
        self.assertIn('Pasaporte', html)

    def test_validar_codigo_encuentra_por_pasaporte(self):
        resp = self.client.get(
            '/app/api/creditos/trabajadores/validar-codigo/',
            {'codigo': 'ab1234567', 'tipo_documento': 'PASAPORTE'},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['exists'])
        self.assertEqual(data['existente']['codigo'], 'AB1234567')
