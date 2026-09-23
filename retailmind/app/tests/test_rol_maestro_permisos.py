"""
Rol Maestro, jerarquía Maestro > Administrador y pantalla de Gestión de Permisos.

Cubre:
1. PermisoRol.tiene_permiso: el Maestro pasa todo (incluso códigos que no
   existen y bloqueos de sucursal); el Administrador respeta su fila.
2. views_permisos: el Administrador no puede tocar el rol Administrador ni el
   Maestro, ni sus propios overrides; el Maestro sí. Copiar/importar ya no
   pierden 'puede_aprobar'. Una sucursal sin fila se muestra sin restricción
   (eliminar/aprobar en True: antes el primer "Guardar" las apagaba).
3. users: un Administrador no puede crear ni modificar a un Maestro, ni
   asignarse sucursales por la API; no se puede dejar al sistema sin Maestro.
4. Comando configurar_rol_maestro: vista previa sin escribir, y --aplicar
   bloquea al Administrador (Conciliación MP + NC) y neutraliza overrides.
5. La migración 0233 creó las opciones de NC con acceso para administrador,
   administración y jefe local (desplegar no le quita nada a nadie).
"""
import json
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from app.decorators import requiere_rol
from app.middleware_permisos import PermisosMenuMiddleware
from app.models import (
    ModuloSistema, OpcionMenu, PermisoRol, PermisoSucursal, PermisoUsuario,
    CODIGO_NC_CLIENTES, CODIGO_NC_TRASPASO, puede_emitir_nota_credito, rol_efectivo,
)
from users.models import Usuario
from .factories import crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario


STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'
TIPOS = ('puede_ver', 'puede_crear', 'puede_editar', 'puede_eliminar', 'puede_exportar', 'puede_aprobar')


def _opcion(codigo, modulo='modulo-test'):
    mod, _ = ModuloSistema.objects.get_or_create(codigo=modulo, defaults={'nombre': 'Módulo test'})
    op, _ = OpcionMenu.objects.get_or_create(codigo=codigo, defaults={'modulo': mod, 'nombre': codigo})
    return op


class TienePermisoMaestroTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='SUC-M')
        self.maestro = crear_usuario(username='maestro', rol='maestro')
        self.admin = crear_usuario(username='admin', rol='administrador')
        self.opcion = _opcion('opcion_x')
        PermisoRol.objects.create(rol='administrador', opcion_menu=self.opcion, **{t: False for t in TIPOS})

    def test_maestro_pasa_todo(self):
        self.assertTrue(PermisoRol.tiene_permiso(self.maestro, 'opcion_x', 'puede_aprobar'))
        self.assertTrue(PermisoRol.tiene_permiso(self.maestro, 'codigo_que_no_existe'))

    def test_bloqueo_de_sucursal_no_afecta_al_maestro(self):
        PermisoSucursal.objects.create(sucursal=self.sucursal, opcion_menu=self.opcion,
                                       habilitado=False, puede_crear=False)
        self.assertTrue(PermisoRol.tiene_permiso(self.maestro, 'opcion_x', 'puede_ver', sucursal_id=self.sucursal.id))

    def test_administrador_respeta_su_fila(self):
        self.assertFalse(PermisoRol.tiene_permiso(self.admin, 'opcion_x', 'puede_ver'))

    def test_rol_efectivo_y_decorador(self):
        self.assertEqual(rol_efectivo(self.maestro), 'administrador')
        self.assertEqual(rol_efectivo(self.admin), 'administrador')

        @requiere_rol('cajero')
        def vista(request):
            from django.http import HttpResponse
            return HttpResponse('ok')

        from django.test import RequestFactory
        req = RequestFactory().get('/x/')
        req.user = self.maestro
        self.assertEqual(vista(req).status_code, 200)

    def test_puede_emitir_nc(self):
        self.assertTrue(puede_emitir_nota_credito(self.maestro))
        nc = OpcionMenu.objects.get(codigo=CODIGO_NC_CLIENTES)
        PermisoRol.objects.filter(rol='administrador', opcion_menu=nc).update(puede_crear=False)
        self.assertFalse(puede_emitir_nota_credito(self.admin))


class Migracion0233Test(TestCase):
    def test_opciones_nc_sembradas_sin_quitar_acceso(self):
        for codigo in (CODIGO_NC_CLIENTES, CODIGO_NC_TRASPASO):
            opcion = OpcionMenu.objects.get(codigo=codigo)
            self.assertEqual(opcion.modulo.codigo, 'documentos')
            roles = set(PermisoRol.objects.filter(opcion_menu=opcion, puede_crear=True).values_list('rol', flat=True))
            self.assertEqual(roles, {'administrador', 'administracion', 'jefe_local'})

    def test_middleware_claves_corregidas(self):
        mw = PermisosMenuMiddleware(lambda r: None)
        self.assertEqual(mw.obtener_codigo_opcion('/app/ventas/cambios-devoluciones/'), 'cambios_devoluciones')
        self.assertEqual(mw.obtener_codigo_opcion('/app/ventas/documentos/'), 'gestion_documentos_ventas')
        self.assertEqual(mw.obtener_codigo_opcion('/app/productos-sucursal/'), 'buscar_productos_sucursal')
        self.assertEqual(mw.obtener_codigo_opcion('/app/requerimientos/gestionar/'), 'gestionar_requerimientos')
        self.assertEqual(mw.obtener_codigo_opcion('/app/requerimientos/crear/'), 'crear_requerimiento')
        # Las APIs que usa el POS siguen sin mapear.
        self.assertIsNone(mw.obtener_codigo_opcion('/app/ventas/api/crear-cambio-devolucion/'))
        self.assertIsNone(mw.obtener_codigo_opcion('/app/api/ventas/documentos/'))
        self.assertIsNone(mw.obtener_codigo_opcion('/app/api/productos-sucursal/'))


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class GestionPermisosJerarquiaTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='SUC-P')
        self.maestro = crear_usuario(username='maestro', rol='maestro')
        self.admin = crear_usuario(username='admin', rol='administrador')
        self.otro_admin = crear_usuario(username='admin2', rol='administrador')
        self.cajero = crear_usuario(username='cajero', rol='cajero')
        for u in (self.maestro, self.admin, self.otro_admin, self.cajero):
            crear_empresa_user(u, self.empresa, self.sucursal)
        # El middleware exige gestion_permisos.puede_ver para /app/permisos/.
        gp = _opcion('gestion_permisos', 'configuracion')
        PermisoRol.objects.update_or_create(rol='administrador', opcion_menu=gp, defaults={'puede_ver': True})
        self.opcion = _opcion('opcion_y')

    def _cliente(self, usuario):
        c = Client()
        c.force_login(usuario)
        return c

    def _post(self, cliente, url, data):
        return cliente.post(url, data=json.dumps(data), content_type='application/json',
                            HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def _payload_rol(self, rol, valor):
        return {'rol': rol, 'limite_descuento': 5,
                'permisos': [{'opcion_id': self.opcion.id, 'permisos': {t: valor for t in TIPOS}}]}

    def test_admin_no_edita_rol_administrador_ni_maestro(self):
        c = self._cliente(self.admin)
        r = self._post(c, '/app/permisos/guardar-permisos-masivos/', self._payload_rol('administrador', True))
        self.assertEqual(r.status_code, 403)
        r = self._post(c, '/app/permisos/guardar-permisos-masivos/', self._payload_rol('maestro', True))
        self.assertEqual(r.status_code, 403)
        r = self._post(c, '/app/permisos/guardar-permiso/',
                       {'rol': 'administrador', 'opcion_id': self.opcion.id, 'tipo_permiso': 'puede_ver', 'valor': True})
        self.assertEqual(r.status_code, 403)

    def test_admin_si_edita_roles_inferiores(self):
        c = self._cliente(self.admin)
        r = self._post(c, '/app/permisos/guardar-permisos-masivos/', self._payload_rol('cajero', True))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(PermisoRol.objects.get(rol='cajero', opcion_menu=self.opcion).puede_aprobar)

    def test_maestro_edita_rol_administrador(self):
        c = self._cliente(self.maestro)
        r = self._post(c, '/app/permisos/guardar-permisos-masivos/', self._payload_rol('administrador', False))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(PermisoRol.objects.get(rol='administrador', opcion_menu=self.opcion).puede_ver)

    def test_guardar_permiso_rechaza_campos_arbitrarios(self):
        c = self._cliente(self.maestro)
        r = self._post(c, '/app/permisos/guardar-permiso/',
                       {'rol': 'cajero', 'opcion_id': self.opcion.id, 'tipo_permiso': 'rol', 'valor': 'maestro'})
        self.assertEqual(r.status_code, 400)

    def test_overrides_propios_y_de_admin_solo_maestro(self):
        payload = lambda u: {'usuario_id': u.id, 've_todas_sucursales': False,
                             'permisos': [{'opcion_id': self.opcion.id, 'overrides': {'puede_ver': True}}]}
        c = self._cliente(self.admin)
        self.assertEqual(self._post(c, '/app/permisos/guardar-permisos-usuario/', payload(self.admin)).status_code, 403)
        self.assertEqual(self._post(c, '/app/permisos/guardar-permisos-usuario/', payload(self.otro_admin)).status_code, 403)
        self.assertEqual(self._post(c, '/app/permisos/guardar-permisos-usuario/', payload(self.cajero)).status_code, 200)
        cm = self._cliente(self.maestro)
        self.assertEqual(self._post(cm, '/app/permisos/guardar-permisos-usuario/', payload(self.admin)).status_code, 200)
        self.assertEqual(self._post(cm, '/app/permisos/guardar-permisos-usuario/', payload(self.maestro)).status_code, 403)

    def test_copiar_rol_conserva_aprobar(self):
        PermisoRol.objects.create(rol='jefe_local', opcion_menu=self.opcion, **{t: True for t in TIPOS})
        c = self._cliente(self.admin)
        r = self._post(c, '/app/permisos/copiar-permisos-rol/',
                       {'rol_origen': 'jefe_local', 'rol_destino': 'cajero', 'sobrescribir': True})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(PermisoRol.objects.get(rol='cajero', opcion_menu=self.opcion).puede_aprobar)

    def test_importar_archivo_viejo_no_apaga_aprobar(self):
        PermisoRol.objects.create(rol='cajero', opcion_menu=self.opcion, **{t: True for t in TIPOS})
        archivo = {'version': '1.0', 'tipo': 'permisos_rol', 'rol': 'cajero', 'limite_descuento': 0,
                   'permisos': [{'opcion_codigo': 'opcion_y', 'permisos': {
                       'puede_ver': True, 'puede_crear': False, 'puede_editar': False,
                       'puede_eliminar': False, 'puede_exportar': False}}]}
        r = self._post(self._cliente(self.admin), '/app/permisos/importar/', archivo)
        self.assertEqual(r.status_code, 200, r.content)
        p = PermisoRol.objects.get(rol='cajero', opcion_menu=self.opcion)
        self.assertFalse(p.puede_crear)
        self.assertTrue(p.puede_aprobar)

    def test_sucursal_sin_fila_se_muestra_sin_restriccion(self):
        r = self._cliente(self.admin).get(f'/app/permisos/obtener-permisos-sucursal/?sucursal_id={self.sucursal.id}',
                                          HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200)
        opciones = [op for m in r.json()['modulos'] for op in m['opciones']]
        self.assertTrue(opciones)
        for op in opciones:
            self.assertTrue(op['permisos']['puede_eliminar'])
            self.assertTrue(op['permisos']['puede_aprobar'])

    def test_permiso_efectivo_explica_bloqueo_de_sucursal(self):
        PermisoRol.objects.create(rol='cajero', opcion_menu=self.opcion, puede_ver=True)
        PermisoSucursal.objects.create(sucursal=self.sucursal, opcion_menu=self.opcion, habilitado=False)
        r = self._cliente(self.admin).get(
            f'/app/permisos/obtener-permisos-usuario/?usuario_id={self.cajero.id}&sucursal_id={self.sucursal.id}',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        op = next(o for m in r.json()['modulos'] for o in m['opciones'] if o['codigo'] == 'opcion_y')
        self.assertEqual(op['efectivo']['puede_ver'], {'valor': False, 'motivo': 'SUCURSAL_BLOQUEA'})

    def test_diagnostico_y_pagina(self):
        c = self._cliente(self.admin)
        r = c.get('/app/permisos/diagnostico/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200)
        self.assertIn('alertas', r.json())
        self.assertEqual(c.get('/app/permisos/gestion/').status_code, 200)
        # Rutas viejas que daban 500 (template inexistente) ahora redirigen.
        self.assertEqual(c.get('/app/permisos/estadisticas/').status_code, 302)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class UsuariosJerarquiaTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='SUC-U')
        self.maestro = crear_usuario(username='maestro', rol='maestro')
        self.admin = crear_usuario(username='admin', rol='administrador')
        self.cajero = crear_usuario(username='cajero', rol='cajero')
        for u in (self.maestro, self.admin, self.cajero):
            crear_empresa_user(u, self.empresa, self.sucursal)

    def _post(self, usuario, url, data):
        c = Client()
        c.force_login(usuario)
        return c.post(url, data=json.dumps(data), content_type='application/json',
                      HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_admin_no_asigna_maestro(self):
        r = self._post(self.admin, '/users/api/cambiar-rol-usuario/', {'user_id': self.cajero.id, 'nuevo_rol': 'maestro'})
        self.assertEqual(r.status_code, 403)
        self.cajero.refresh_from_db()
        self.assertEqual(self.cajero.rol, 'cajero')

    def test_admin_no_modifica_maestro(self):
        r = self._post(self.admin, '/users/api/cambiar-rol-usuario/', {'user_id': self.maestro.id, 'nuevo_rol': 'vendedor'})
        self.assertEqual(r.status_code, 403)
        r = self._post(self.admin, f'/users/toggle-estado/{self.maestro.id}/', {})
        self.assertEqual(r.status_code, 403)
        self.maestro.refresh_from_db()
        self.assertEqual(self.maestro.rol, 'maestro')
        self.assertTrue(self.maestro.es_activo)

    def test_rol_inexistente_rechazado(self):
        r = self._post(self.admin, '/users/api/cambiar-rol-usuario/', {'user_id': self.cajero.id, 'nuevo_rol': 'bodeguero'})
        self.assertEqual(r.status_code, 403)

    def test_no_deja_sin_maestro(self):
        otro = crear_usuario(username='maestro2', rol='maestro')
        r = self._post(self.maestro, '/users/api/cambiar-rol-usuario/', {'user_id': otro.id, 'nuevo_rol': 'vendedor'})
        self.assertEqual(r.status_code, 200)
        # Ahora es el único Maestro: no puede quitarse el rol ni desactivarse.
        r = self._post(self.maestro, f'/users/editar/{self.maestro.id}/', {'rol': 'administrador'})
        self.assertEqual(r.status_code, 403)
        self.maestro.refresh_from_db()
        self.assertEqual(self.maestro.rol, 'maestro')

    def test_admin_no_crea_maestro(self):
        r = self._post(self.admin, '/users/crear/', {
            'email': 'nuevo@test.com', 'first_name': 'Nuevo', 'last_name': 'Maestro', 'rol': 'maestro'})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Usuario.objects.filter(email='nuevo@test.com').exists())

    def test_asignar_sucursal_exige_admin(self):
        r = self._post(self.cajero, f'/users/asignar-sucursal/{self.cajero.id}/', {'sucursal_id': self.sucursal.id})
        self.assertEqual(r.status_code, 403)


class ComandoConfigurarRolMaestroTest(TestCase):
    def setUp(self):
        self.admin = crear_usuario(username='admin', rol='administrador')
        self.dueno = crear_usuario(username='dueno', rol='administrador')
        self.dineros = OpcionMenu.objects.filter(codigo='dineros_mercadopago').first() or _opcion('dineros_mercadopago')
        PermisoRol.objects.update_or_create(rol='administrador', opcion_menu=self.dineros,
                                            defaults={t: True for t in TIPOS})
        nc = OpcionMenu.objects.get(codigo=CODIGO_NC_CLIENTES)
        PermisoUsuario.objects.create(usuario=self.admin, opcion_menu=nc, puede_crear=True)

    def _correr(self, *args):
        out = StringIO()
        call_command('configurar_rol_maestro', *args, stdout=out)
        return out.getvalue()

    def test_vista_previa_no_escribe(self):
        self._correr('--maestro', 'dueno')
        self.dueno.refresh_from_db()
        self.assertEqual(self.dueno.rol, 'administrador')
        self.assertTrue(PermisoRol.objects.get(rol='administrador', opcion_menu=self.dineros).puede_ver)

    def test_aplicar(self):
        self._correr('--maestro', 'dueno', '--aplicar')
        self.dueno.refresh_from_db()
        self.assertEqual(self.dueno.rol, 'maestro')
        for codigo in ('dineros_mercadopago', CODIGO_NC_CLIENTES, CODIGO_NC_TRASPASO):
            p = PermisoRol.objects.get(rol='administrador', opcion_menu__codigo=codigo)
            self.assertFalse(any(getattr(p, t) for t in TIPOS), codigo)
        ov = PermisoUsuario.objects.get(usuario=self.admin, opcion_menu__codigo=CODIGO_NC_CLIENTES)
        self.assertIsNone(ov.puede_crear)
        self.assertFalse(puede_emitir_nota_credito(self.admin))
        self.assertTrue(puede_emitir_nota_credito(self.dueno))
        self.assertFalse(PermisoRol.tiene_permiso(self.admin, 'dineros_mercadopago'))
        # Administración y jefe local no se tocan.
        self.assertTrue(PermisoRol.objects.get(rol='administracion', opcion_menu__codigo=CODIGO_NC_CLIENTES).puede_crear)

    def test_edicion_documentos_solo_maestro(self):
        editar_pago = OpcionMenu.objects.get(codigo='dte_editar_pago')
        PermisoRol.objects.update_or_create(rol='administracion', opcion_menu=editar_pago,
                                            defaults={'puede_ver': True, 'puede_editar': True})
        cajero = crear_usuario(username='cajero-ed', rol='cajero')
        PermisoUsuario.objects.create(usuario=cajero, opcion_menu=editar_pago, puede_editar=True)
        self.assertTrue(PermisoRol.tiene_permiso(self.admin, 'dte_eliminar_documento', 'puede_eliminar'))

        self._correr('--maestro', 'dueno', '--aplicar')

        for rol in ('administrador', 'administracion'):
            p = PermisoRol.objects.filter(rol=rol, opcion_menu=editar_pago).first()
            self.assertFalse(p and p.puede_editar, rol)
        self.assertFalse(PermisoRol.tiene_permiso(self.admin, 'dte_eliminar_documento', 'puede_eliminar'))
        self.assertFalse(PermisoRol.tiene_permiso(cajero, 'dte_editar_pago', 'puede_editar'))
        self.dueno.refresh_from_db()
        self.assertTrue(PermisoRol.tiene_permiso(self.dueno, 'dte_editar_pago', 'puede_editar'))

    def test_mantener_edicion_documentos(self):
        self._correr('--aplicar', '--mantener-edicion-documentos')
        self.assertTrue(PermisoRol.tiene_permiso(self.admin, 'dte_eliminar_documento', 'puede_eliminar'))

    def test_permitir_nc_traspasos(self):
        self._correr('--aplicar', '--permitir-nc-traspasos')
        self.assertTrue(PermisoRol.objects.get(rol='administrador', opcion_menu__codigo=CODIGO_NC_TRASPASO).puede_crear)
        self.assertFalse(PermisoRol.objects.get(rol='administrador', opcion_menu__codigo=CODIGO_NC_CLIENTES).puede_crear)
