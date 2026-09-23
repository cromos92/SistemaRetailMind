"""
Permisos finos de edición de documentos (migración 0234). El dueño pidió que
en el detalle de los documentos no se puedan editar ni eliminar pagos (ni el
documento) salvo el Maestro; `configurar_rol_maestro` deja estos códigos solo
para él.

Cubre:
1. editarPago / eliminarPago (`dte_compras_pagos`): sin la fila → 403 y el
   Dte_Detalle_Pago queda intacto; con la fila → pasa; el Maestro pasa sin fila.
2. agregarNC / eliminarNC: la NC es una fila de pago → mismo permiso.
3. eliminar_dte (`dte_compras_eliminar`): sin la fila → 403 y el DTE no se
   descarta; con ella → soft delete. Un DTE que NO es de compra exige
   `dte_eliminar_documento` aunque se tenga el de compras.
4. Registrar un pago nuevo ("Pagar") NO depende de estos permisos.
5. Las páginas exponen los flags a la plantilla: gestionDteCompras (3 flags)
   y gestion-dte (`puede_eliminar_documento`: administración no, administrador
   sí — antes el botón colgaba de es_admin y administración recibía 403).
"""
import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import Dte, Dte_Detalle_Pago, ModuloSistema, OpcionMenu, PermisoRol
from .factories import crear_empresa, crear_empresa_user, crear_usuario, setup_entorno_completo


STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'
TIPOS = ('puede_ver', 'puede_crear', 'puede_editar', 'puede_eliminar', 'puede_exportar', 'puede_aprobar')
CODIGOS_FINOS = ('dte_compras_pagos', 'dte_compras_eliminar', 'dte_eliminar_documento')


def _opcion(codigo, modulo='documentos'):
    """La migración 0234 ya crea los códigos finos; get_or_create por si el
    test corre contra una BD sin ella y para las opciones de pantalla."""
    mod, _ = ModuloSistema.objects.get_or_create(codigo=modulo, defaults={'nombre': modulo})
    op, _ = OpcionMenu.objects.get_or_create(codigo=codigo, defaults={'modulo': mod, 'nombre': codigo})
    if not op.activo:
        op.activo = True
        op.save(update_fields=['activo'])
    return op


def _permiso(rol, codigo, **flags):
    valores = {t: False for t in TIPOS}
    valores.update(flags)
    PermisoRol.objects.update_or_create(rol=rol, opcion_menu=_opcion(codigo), defaults=valores)


class _BasePermisosDocumentos(TestCase):
    def setUp(self):
        env = setup_entorno_completo()
        self.empresa = env['empresa']
        self.sucursal = env['sucursal']
        self.proveedor = crear_empresa(nombre='Proveedor Test', rut='76.111.111-1', esProveedor=True)

        # jefe_local: SIN filas finas (lo que queda tras `configurar_rol_maestro`).
        self.sin_permiso = crear_usuario(username='jefe_sin', rol='jefe_local')
        # administracion: con las filas finas (lo que sembró la migración).
        self.con_permiso = crear_usuario(username='admcion_con', rol='administracion')
        self.maestro = crear_usuario(username='maestro_doc', rol='maestro')
        for u in (self.sin_permiso, self.con_permiso, self.maestro):
            crear_empresa_user(u, self.empresa, self.sucursal)

        PermisoRol.objects.filter(rol__in=('jefe_local', 'maestro'),
                                  opcion_menu__codigo__in=CODIGOS_FINOS).delete()
        _permiso('administracion', 'dte_compras_pagos', puede_ver=True, puede_editar=True, puede_eliminar=True)
        _permiso('administracion', 'dte_compras_eliminar', puede_ver=True, puede_eliminar=True)
        PermisoRol.objects.filter(rol='administracion', opcion_menu__codigo='dte_eliminar_documento').delete()

        self.dte = self._dte(numero=5001)
        self.pago = Dte_Detalle_Pago.objects.create(
            dte=self.dte, metodo_pago='Transferencia', voucher='V-1', monto=40000,
            fecha_pago=timezone.localdate(),
        )
        self.dte.estado_pago = 'Abonado'
        self.dte.save(update_fields=['estado_pago'])

    def _dte(self, numero, tipo_transaccion='COMPRA', monto=119000):
        hoy = timezone.localdate()
        return Dte.objects.create(
            emisor=self.proveedor, receptor=self.empresa, numero_documento=numero,
            tipo_documento='FACTURA ELECTRONICA', monto_con_iva=monto, monto_neto=100000,
            descuento=0, estado_pago='Pendiente', estado_dte='RECEPCIONADO_COMPLETO',
            responsable='test', fecha_emision=hoy, fecha_vencimiento=hoy + timedelta(days=30),
            diasCredito=30, bultos=0, unidades_productos=1,
            tipo_transaccion=tipo_transaccion, sucursal=self.sucursal,
        )

    def _login(self, usuario):
        self.client.force_login(usuario)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _json(self, method, url, data=None):
        return getattr(self.client, method)(
            url, data=json.dumps(data or {}), content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def _editar_pago(self, monto=50000):
        return self._json('put', f'/app/editarPago/{self.pago.id}/', {
            'metodo_pago': 'Cheque', 'voucher': 'V-2', 'monto': monto,
            'fecha_pago': timezone.localdate().isoformat(),
        })


class PagosDocumentoCompraTest(_BasePermisosDocumentos):
    def test_sin_permiso_no_edita_pago_y_queda_intacto(self):
        self._login(self.sin_permiso)
        r = self._editar_pago()
        self.assertEqual(r.status_code, 403)
        body = r.json()
        self.assertFalse(body['success'])
        self.assertIn('Maestro', body['mensaje'])
        self.pago.refresh_from_db()
        self.assertEqual((self.pago.metodo_pago, self.pago.voucher, self.pago.monto),
                         ('Transferencia', 'V-1', 40000))

    def test_sin_permiso_no_elimina_pago(self):
        self._login(self.sin_permiso)
        r = self._json('delete', f'/app/eliminarPago/{self.pago.id}/')
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Dte_Detalle_Pago.objects.filter(id=self.pago.id).exists())
        self.dte.refresh_from_db()
        self.assertEqual(self.dte.estado_pago, 'Abonado')

    def test_con_permiso_edita_y_elimina(self):
        self._login(self.con_permiso)
        r = self._editar_pago(monto=50000)
        self.assertEqual(r.status_code, 200, r.content)
        self.pago.refresh_from_db()
        self.assertEqual((self.pago.metodo_pago, self.pago.monto), ('Cheque', 50000))

        r = self._json('delete', f'/app/eliminarPago/{self.pago.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Dte_Detalle_Pago.objects.filter(id=self.pago.id).exists())
        self.dte.refresh_from_db()
        self.assertEqual(self.dte.estado_pago, 'Pendiente')

    def test_editar_sin_eliminar_no_permite_eliminar(self):
        _permiso('administracion', 'dte_compras_pagos', puede_ver=True, puede_editar=True, puede_eliminar=False)
        self._login(self.con_permiso)
        self.assertEqual(self._editar_pago().status_code, 200)
        r = self._json('delete', f'/app/eliminarPago/{self.pago.id}/')
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Dte_Detalle_Pago.objects.filter(id=self.pago.id).exists())

    def test_maestro_pasa_sin_filas(self):
        self._login(self.maestro)
        self.assertEqual(self._editar_pago().status_code, 200)
        r = self._json('delete', f'/app/eliminarPago/{self.pago.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Dte_Detalle_Pago.objects.filter(id=self.pago.id).exists())

    def test_registrar_pago_nuevo_no_depende_del_permiso(self):
        """«Pagar» sigue disponible para quien no puede editar/eliminar."""
        self._login(self.sin_permiso)
        r = self._json('post', '/app/registrarPagoDTE/', {
            'dte_id': self.dte.id, 'metodo_pago': 'Transferencia', 'voucher': 'V-9',
            'monto': 1000, 'fecha_pago': timezone.localdate().isoformat(),
        })
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(Dte_Detalle_Pago.objects.filter(dte=self.dte, voucher='V-9').exists())


class NotaCreditoComoPagoTest(_BasePermisosDocumentos):
    def test_sin_permiso_no_agrega_ni_elimina_nc(self):
        nc = Dte_Detalle_Pago.objects.create(dte=self.dte, metodo_pago='Nota de Crédito',
                                             voucher='NC-1', monto=1000, notas='x')
        self._login(self.sin_permiso)
        r = self._json('post', '/app/agregarNC/', {
            'dte_id': self.dte.id, 'voucher': 'NC-2', 'monto': 500, 'notas': 'Devolución',
        })
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Dte_Detalle_Pago.objects.filter(voucher='NC-2').exists())

        r = self._json('delete', f'/app/eliminarNC/{nc.id}/')
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Dte_Detalle_Pago.objects.filter(id=nc.id).exists())

    def test_con_permiso_agrega_y_elimina_nc(self):
        self._login(self.con_permiso)
        r = self._json('post', '/app/agregarNC/', {
            'dte_id': self.dte.id, 'voucher': 'NC-2', 'monto': 500, 'notas': 'Devolución',
        })
        self.assertEqual(r.status_code, 200, r.content)
        nc = Dte_Detalle_Pago.objects.get(voucher='NC-2')
        r = self._json('delete', f'/app/eliminarNC/{nc.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Dte_Detalle_Pago.objects.filter(id=nc.id).exists())


class EliminarDocumentoCompraTest(_BasePermisosDocumentos):
    def test_sin_permiso_no_elimina_dte(self):
        self._login(self.sin_permiso)
        r = self._json('delete', f'/app/eliminarDTE/{self.dte.id}/', {'forzar': False})
        self.assertEqual(r.status_code, 403)
        self.dte.refresh_from_db()
        self.assertFalse(self.dte.descartado)

    def test_sin_permiso_tampoco_fuerza_hard_delete(self):
        otro = self._dte(numero=5002)
        self._login(self.sin_permiso)
        r = self._json('delete', f'/app/eliminarDTE/{otro.id}/', {'forzar': True})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Dte.objects.filter(id=otro.id).exists())

    def test_con_permiso_descarta_dte(self):
        self._login(self.con_permiso)
        r = self._json('delete', f'/app/eliminarDTE/{self.dte.id}/', {'forzar': False})
        self.assertEqual(r.status_code, 200, r.content)
        self.dte.refresh_from_db()
        self.assertTrue(self.dte.descartado)

    def test_maestro_descarta_dte(self):
        self._login(self.maestro)
        r = self._json('delete', f'/app/eliminarDTE/{self.dte.id}/', {'forzar': False})
        self.assertEqual(r.status_code, 200, r.content)
        self.dte.refresh_from_db()
        self.assertTrue(self.dte.descartado)

    def test_dte_de_venta_exige_permiso_de_venta(self):
        """El endpoint de compras no sirve de atajo para borrar una venta."""
        venta = self._dte(numero=7001, tipo_transaccion='VENTA_PUBLICO')
        self._login(self.con_permiso)  # tiene dte_compras_eliminar, NO dte_eliminar_documento
        r = self._json('delete', f'/app/eliminarDTE/{venta.id}/', {'forzar': False})
        self.assertEqual(r.status_code, 403)
        venta.refresh_from_db()
        self.assertFalse(venta.descartado)

        _permiso('administracion', 'dte_eliminar_documento', puede_ver=True, puede_eliminar=True)
        r = self._json('delete', f'/app/eliminarDTE/{venta.id}/', {'forzar': False})
        self.assertEqual(r.status_code, 200, r.content)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class FlagsEnPaginasTest(_BasePermisosDocumentos):
    def setUp(self):
        super().setUp()
        # El middleware exige ver cada pantalla.
        for rol in ('jefe_local', 'administracion', 'administrador'):
            _permiso(rol, 'gestion_dte_compras', puede_ver=True)
            _permiso(rol, 'gestion_dte', puede_ver=True)

    def test_gestion_dte_compras_sin_permiso(self):
        self._login(self.sin_permiso)
        r = self.client.get('/app/verGestionDteCompras/')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context['puede_editar_pagos_compra'])
        self.assertFalse(r.context['puede_eliminar_pagos_compra'])
        self.assertFalse(r.context['puede_eliminar_dte_compra'])
        html = r.content.decode('utf-8')
        self.assertIn('const PUEDE_EDITAR_PAGOS_COMPRA = false;', html)
        self.assertIn('const PUEDE_ELIMINAR_DTE_COMPRA = false;', html)

    def test_gestion_dte_compras_maestro(self):
        self._login(self.maestro)
        r = self.client.get('/app/verGestionDteCompras/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context['puede_editar_pagos_compra'])
        self.assertTrue(r.context['puede_eliminar_pagos_compra'])
        self.assertTrue(r.context['puede_eliminar_dte_compra'])
        self.assertIn('const PUEDE_ELIMINAR_PAGOS_COMPRA = true;', r.content.decode('utf-8'))

    def test_gestion_dte_eliminar_documento(self):
        # administracion: es_admin=True pero sin dte_eliminar_documento → sin botón.
        self._login(self.con_permiso)
        r = self.client.get('/app/documentos/gestion-dte/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context['es_admin'])
        self.assertFalse(r.context['puede_eliminar_documento'])
        self.assertNotIn('eliminarBoletaPapel(${dte.id', r.content.decode('utf-8'))

        admin = crear_usuario(username='admin_doc', rol='administrador')
        crear_empresa_user(admin, self.empresa, self.sucursal)
        _permiso('administrador', 'dte_eliminar_documento', puede_ver=True, puede_eliminar=True)
        self._login(admin)
        r = self.client.get('/app/documentos/gestion-dte/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context['puede_eliminar_documento'])
        self.assertIn('eliminarBoletaPapel(${dte.id', r.content.decode('utf-8'))
