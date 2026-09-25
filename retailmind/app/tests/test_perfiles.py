"""
Perfiles (25-sep-2026): Maestro / Administrador / Jefe / Administración / Jefe Local.

Cubre:
1. app.services.perfiles_permisos: la política deja a cada rol como pidió el
   dueño, es idempotente, la vista previa no escribe y neutraliza overrides.
2. Jerarquía: nadie se asigna Maestro ni un rol mayor al suyo; nadie edita a
   un usuario (ni un rol) de nivel igual o mayor, salvo el Maestro.
3. Gates nuevos: devolver por Mercado Pago es un permiso (devolver_mercadopago),
   aprobar garantía ya no exige NC, cambiar el tipo de DTE exige Editar N°.
4. Comando configurar_perfiles y seeder (rol Jefe sembrado).
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from app.models import (
    ModuloSistema, OpcionMenu, PermisoRol, PermisoUsuario,
    CODIGO_DEVOLUCION_MP, CODIGO_NC_CLIENTES, ROL_JEFE, ROL_MAESTRO,
    nivel_rol, puede_asignar_rol, puede_devolver_mercadopago, puede_gestionar_usuario, rol_efectivo,
)
from app.services import perfiles_permisos as perfiles
from app.utils_ventas import puede_cambiar_tipo_dte, puede_editar_campo_dte
from app.views_permisos import _motivo_rol_no_editable, _motivo_usuario_no_editable
from users.views import _motivo_rol_no_asignable, _motivo_usuario_protegido
from .factories import crear_usuario

TIPOS = perfiles.TIPOS
CODIGOS = (
    'gestion_dte', 'gestion_documentos_ventas', 'cuadratura_caja', 'ajuste_stock_rapido', 'cambiar_empresa',
    'devolucion_garantia', 'dte_eliminar_documento', 'dte_editar_fecha', 'dte_editar_numero',
    'dte_editar_pago', 'dte_editar_vendedor', 'dte_editar_folio',
    'dte_editar_tipo_boleta_electronica', 'dte_editar_tipo_boleta_papel',
    'dte_editar_tipo_factura_electronica', 'dte_editar_tipo_factura_exenta',
    'giftcards_listado', 'fidelizacion_cupones', CODIGO_NC_CLIENTES, CODIGO_DEVOLUCION_MP,
)


def _opcion(codigo):
    mod, _ = ModuloSistema.objects.get_or_create(codigo='modulo-perfiles', defaults={'nombre': 'Perfiles'})
    op, _ = OpcionMenu.objects.get_or_create(codigo=codigo, defaults={'modulo': mod, 'nombre': codigo})
    return op


def _fila(rol, codigo):
    return PermisoRol.objects.filter(rol=rol, opcion_menu__codigo=codigo).first()


def _flags(rol, codigo):
    fila = _fila(rol, codigo)
    return {t: getattr(fila, t) for t in TIPOS} if fila else None


def _sembrar(rol, valor=True, codigos=CODIGOS):
    for c in codigos:
        PermisoRol.objects.update_or_create(rol=rol, opcion_menu=_opcion(c),
                                            defaults={t: valor for t in TIPOS})


class PoliticaPerfilesTest(TestCase):
    def setUp(self):
        for c in CODIGOS:
            _opcion(c)

    def test_administrador_todo_menos_lo_bloqueado(self):
        _sembrar('administrador', True)
        cambios = perfiles.aplicar('administrador', escribir=True)
        self.assertTrue(cambios)
        for c in ('gestion_dte', 'gestion_documentos_ventas', 'cuadratura_caja', 'ajuste_stock_rapido'):
            self.assertEqual(_flags('administrador', c), perfiles.TODO, c)
        for c in ('giftcards_listado', 'fidelizacion_cupones', CODIGO_NC_CLIENTES, 'dte_eliminar_documento',
                  CODIGO_DEVOLUCION_MP, 'dte_editar_fecha', 'dte_editar_numero', 'dte_editar_vendedor', 'dte_editar_folio'):
            self.assertEqual(_flags('administrador', c), perfiles.NADA, c)
        # Solo cambiar el medio de pago: ver + editar en el campo y en los tipos.
        for c in ('dte_editar_pago', 'dte_editar_tipo_boleta_electronica', 'dte_editar_tipo_factura_exenta'):
            self.assertEqual(_flags('administrador', c), perfiles._solo('puede_ver', 'puede_editar'), c)
        self.assertEqual(_flags('administrador', 'devolucion_garantia'), perfiles.TODO)

    def test_administrador_recibe_lo_que_no_tenia(self):
        """Base TODO: una opción sin fila para el administrador se crea encendida."""
        self.assertIsNone(_fila('administrador', 'cuadratura_caja'))
        perfiles.aplicar('administrador', escribir=True)
        self.assertEqual(_flags('administrador', 'cuadratura_caja'), perfiles.TODO)
        # ...pero lo bloqueado no se crea (sin fila = sin acceso); si la
        # migración 0233 ya sembró la fila de NC, queda toda apagada.
        self.assertIn(_flags('administrador', CODIGO_NC_CLIENTES), (None, perfiles.NADA))
        # Sin fila previa (la migración 0165 la siembra; se quita para probar): no se crea apagada.
        PermisoRol.objects.filter(rol='administrador', opcion_menu__codigo='giftcards_listado').delete()
        perfiles.aplicar('administrador', escribir=True)
        self.assertIsNone(_fila('administrador', 'giftcards_listado'))

    def test_jefe_como_administrador_con_menos(self):
        perfiles.aplicar(ROL_JEFE, escribir=True)
        self.assertEqual(_flags(ROL_JEFE, 'gestion_dte'), perfiles.TODO)
        self.assertEqual(_flags(ROL_JEFE, 'cambiar_empresa'), perfiles.TODO)
        self.assertIsNone(_fila(ROL_JEFE, 'ajuste_stock_rapido'))
        self.assertIsNone(_fila(ROL_JEFE, 'dte_editar_pago'))
        self.assertIsNone(_fila(ROL_JEFE, 'dte_editar_tipo_boleta_papel'))
        self.assertIsNone(_fila(ROL_JEFE, CODIGO_NC_CLIENTES))
        self.assertEqual(_flags(ROL_JEFE, 'devolucion_garantia'), {**perfiles.TODO, 'puede_aprobar': False})

    def test_jefe_existente_se_le_apaga_lo_prohibido(self):
        _sembrar(ROL_JEFE, True)
        perfiles.aplicar(ROL_JEFE, escribir=True)
        self.assertEqual(_flags(ROL_JEFE, 'ajuste_stock_rapido'), perfiles.NADA)
        self.assertEqual(_flags(ROL_JEFE, 'dte_editar_pago'), perfiles.NADA)
        self.assertFalse(_fila(ROL_JEFE, 'devolucion_garantia').puede_aprobar)
        self.assertTrue(_fila(ROL_JEFE, 'devolucion_garantia').puede_ver)

    def test_administracion_copia_jefe_local_y_cambia_de_tienda(self):
        _sembrar('jefe_local', True, ('gestion_documentos_ventas', 'cuadratura_caja', 'devolucion_garantia'))
        _sembrar('jefe_local', False, ('cambiar_empresa', 'gestion_dte'))
        _sembrar('administracion', True, ('dte_eliminar_documento', 'dte_editar_fecha', 'dte_editar_pago'))
        perfiles.aplicar('administracion', escribir=True)
        self.assertEqual(_flags('administracion', 'gestion_documentos_ventas'), perfiles.TODO)
        # Jefe Local no ve Gestión DTE: Administración tampoco (sin fila o apagada).
        self.assertIn(_flags('administracion', 'gestion_dte'), (None, perfiles.NADA))
        self.assertEqual(_flags('administracion', 'cambiar_empresa'), perfiles._solo('puede_ver'))
        self.assertEqual(_flags('administracion', 'dte_eliminar_documento'), perfiles.NADA)
        self.assertEqual(_flags('administracion', 'dte_editar_fecha'), perfiles.NADA)
        self.assertEqual(_flags('administracion', 'dte_editar_pago'), perfiles.NADA)
        self.assertFalse(_fila('administracion', 'devolucion_garantia').puede_aprobar)
        self.assertTrue(_fila('administracion', 'devolucion_garantia').puede_crear)

    def test_jefe_local_conserva_lo_suyo_sin_documentos(self):
        _sembrar('jefe_local', True, ('gestion_dte', 'dte_editar_fecha', 'dte_editar_pago', 'devolucion_garantia',
                                      CODIGO_DEVOLUCION_MP))
        perfiles.aplicar('jefe_local', escribir=True)
        self.assertEqual(_flags('jefe_local', 'gestion_dte'), perfiles.TODO)
        self.assertEqual(_flags('jefe_local', 'dte_editar_fecha'), perfiles.NADA)
        self.assertEqual(_flags('jefe_local', 'dte_editar_pago'), perfiles.NADA)
        self.assertEqual(_flags('jefe_local', CODIGO_DEVOLUCION_MP), perfiles.NADA)
        self.assertFalse(_fila('jefe_local', 'devolucion_garantia').puede_aprobar)
        # Lo que no tenía (base ACTUAL) no se le regala.
        self.assertIsNone(_fila('jefe_local', 'cambiar_empresa'))

    def test_vista_previa_no_escribe_y_es_idempotente(self):
        _sembrar('administrador', True)
        antes = list(PermisoRol.objects.filter(rol='administrador').values_list('opcion_menu__codigo', *TIPOS))
        cambios = perfiles.aplicar('administrador', escribir=False)
        self.assertTrue(cambios)
        self.assertEqual(antes, list(PermisoRol.objects.filter(rol='administrador').values_list('opcion_menu__codigo', *TIPOS)))
        perfiles.aplicar('administrador', escribir=True)
        self.assertEqual(perfiles.aplicar('administrador', escribir=True), [])
        self.assertEqual(perfiles.aplicar('administrador', escribir=False), [])

    def test_override_en_true_sobre_bloqueado_pasa_a_usar_rol(self):
        _sembrar('administrador', True)
        admin = crear_usuario(username='adm', rol='administrador')
        ov = PermisoUsuario.objects.create(usuario=admin, opcion_menu=_opcion(CODIGO_NC_CLIENTES),
                                           puede_ver=True, puede_crear=True, puede_editar=False)
        ok = PermisoUsuario.objects.create(usuario=admin, opcion_menu=_opcion('gestion_dte'), puede_ver=True)
        cambios = perfiles.aplicar('administrador', escribir=True)
        self.assertTrue(any('override de adm' in c for c in cambios))
        ov.refresh_from_db()
        self.assertIsNone(ov.puede_ver)
        self.assertIsNone(ov.puede_crear)
        self.assertFalse(ov.puede_editar)
        ok.refresh_from_db()
        self.assertTrue(ok.puede_ver)
        self.assertFalse(PermisoRol.tiene_permiso(admin, CODIGO_NC_CLIENTES, 'puede_crear'))

    def test_maestro_no_se_configura(self):
        self.assertEqual(perfiles.aplicar(ROL_MAESTRO, escribir=True), [])
        self.assertFalse(PermisoRol.objects.filter(rol=ROL_MAESTRO).exists())

    def test_solo_faltantes_no_toca_lo_existente(self):
        _sembrar(ROL_JEFE, False, ('gestion_dte',))
        perfiles.aplicar(ROL_JEFE, escribir=True, solo_faltantes=True)
        self.assertEqual(_flags(ROL_JEFE, 'gestion_dte'), perfiles.NADA)
        self.assertEqual(_flags(ROL_JEFE, 'cuadratura_caja'), perfiles.TODO)


class JerarquiaRolesTest(TestCase):
    def setUp(self):
        self.maestro = crear_usuario(username='maestro', rol=ROL_MAESTRO)
        self.admin = crear_usuario(username='admin', rol='administrador')
        self.jefe = crear_usuario(username='jefe', rol=ROL_JEFE)
        self.administracion = crear_usuario(username='adm', rol='administracion')
        self.jefe_local = crear_usuario(username='jl', rol='jefe_local')

    def test_niveles(self):
        self.assertGreater(nivel_rol(ROL_MAESTRO), nivel_rol('administrador'))
        self.assertGreater(nivel_rol('administrador'), nivel_rol(ROL_JEFE))
        self.assertGreater(nivel_rol(ROL_JEFE), nivel_rol('administracion'))
        self.assertGreater(nivel_rol('administracion'), nivel_rol('jefe_local'))
        self.assertEqual(nivel_rol('inventado'), 0)

    def test_nadie_se_asigna_maestro_ni_un_rol_mayor(self):
        for actor in (self.admin, self.jefe, self.administracion, self.jefe_local):
            self.assertFalse(puede_asignar_rol(actor, ROL_MAESTRO), actor.rol)
            self.assertIsNotNone(_motivo_rol_no_asignable(actor, ROL_MAESTRO), actor.rol)
        self.assertFalse(puede_asignar_rol(self.jefe, 'administrador'))
        self.assertIn('nivel mayor', _motivo_rol_no_asignable(self.jefe, 'administrador'))
        self.assertFalse(puede_asignar_rol(self.administracion, ROL_JEFE))
        self.assertFalse(puede_asignar_rol(self.jefe_local, 'administracion'))
        # Igual o menor, sí.
        self.assertTrue(puede_asignar_rol(self.jefe, ROL_JEFE))
        self.assertTrue(puede_asignar_rol(self.jefe, 'administracion'))
        self.assertTrue(puede_asignar_rol(self.admin, ROL_JEFE))
        self.assertTrue(puede_asignar_rol(self.maestro, ROL_MAESTRO))
        self.assertIsNone(_motivo_rol_no_asignable(self.maestro, ROL_MAESTRO))

    def test_gestionar_usuario_solo_hacia_abajo_o_par(self):
        self.assertFalse(puede_gestionar_usuario(self.jefe, self.admin))
        self.assertIn('nivel mayor', _motivo_usuario_protegido(self.jefe, self.admin))
        self.assertFalse(puede_gestionar_usuario(self.admin, self.maestro))
        self.assertTrue(puede_gestionar_usuario(self.admin, self.jefe))
        self.assertTrue(puede_gestionar_usuario(self.jefe, self.jefe_local))
        self.assertTrue(puede_gestionar_usuario(self.maestro, self.admin))
        self.assertIsNone(_motivo_usuario_protegido(self.maestro, self.admin))

    def test_permisos_solo_de_roles_inferiores(self):
        self.assertIsNone(_motivo_rol_no_editable(self.maestro, 'administrador'))
        self.assertIsNone(_motivo_rol_no_editable(self.admin, ROL_JEFE))
        self.assertIsNotNone(_motivo_rol_no_editable(self.admin, 'administrador'))
        self.assertIsNotNone(_motivo_rol_no_editable(self.jefe, 'administrador'))
        self.assertIsNotNone(_motivo_rol_no_editable(self.jefe, ROL_JEFE))
        self.assertIsNone(_motivo_rol_no_editable(self.jefe, 'administracion'))
        self.assertIsNotNone(_motivo_rol_no_editable(self.admin, ROL_MAESTRO))

        self.assertIsNotNone(_motivo_usuario_no_editable(self.jefe, self.admin))
        self.assertIsNotNone(_motivo_usuario_no_editable(self.jefe, self.jefe))
        self.assertIsNone(_motivo_usuario_no_editable(self.jefe, self.jefe_local))
        self.assertIsNone(_motivo_usuario_no_editable(self.maestro, self.admin))

    def test_jefe_es_administrador_para_los_checks_por_rol(self):
        self.assertEqual(rol_efectivo(self.jefe), 'administrador')
        self.assertEqual(rol_efectivo(self.administracion), 'administracion')
        self.assertTrue(self.jefe.tiene_permiso_usuarios('editar'))
        self.assertFalse(self.administracion.tiene_permiso_usuarios('editar'))


class GatesDocumentosTest(TestCase):
    def setUp(self):
        for c in ('dte_editar_numero', 'dte_editar_pago', 'dte_editar_tipo_boleta_electronica',
                  'dte_editar_tipo_boleta_papel', CODIGO_DEVOLUCION_MP):
            _opcion(c)
        self.admin = crear_usuario(username='admin', rol='administrador')
        self.maestro = crear_usuario(username='maestro', rol=ROL_MAESTRO)

    def test_cambiar_tipo_exige_editar_numero(self):
        _sembrar('administrador', False, ('dte_editar_numero',))
        _sembrar('administrador', True, ('dte_editar_pago', 'dte_editar_tipo_boleta_electronica',
                                         'dte_editar_tipo_boleta_papel'))
        # Puede corregir el medio de pago...
        self.assertTrue(puede_editar_campo_dte(self.admin, 'pago', 'BOLETA ELECTRONICA'))
        # ...pero no cambiar boleta electrónica <-> papel sin Editar N°.
        self.assertFalse(puede_cambiar_tipo_dte(self.admin, 'BOLETA ELECTRONICA', 'BOLETA PAPEL'))
        PermisoRol.objects.filter(rol='administrador', opcion_menu__codigo='dte_editar_numero').update(puede_editar=True)
        self.assertTrue(puede_cambiar_tipo_dte(self.admin, 'BOLETA ELECTRONICA', 'BOLETA PAPEL'))
        self.assertTrue(puede_cambiar_tipo_dte(self.maestro, 'BOLETA ELECTRONICA', 'BOLETA PAPEL'))

    def test_devolver_mercadopago_es_permiso(self):
        # La migración 0237 lo deja encendido para administrador (no quita nada al desplegar)...
        self.assertTrue(puede_devolver_mercadopago(self.admin))
        self.assertTrue(puede_devolver_mercadopago(self.maestro))
        # ...la política lo apaga; el Maestro sigue pasando.
        perfiles.aplicar('administrador', escribir=True)
        self.assertFalse(puede_devolver_mercadopago(self.admin))
        self.assertTrue(puede_devolver_mercadopago(self.maestro))
        # Y un override individual lo vuelve a permitir (el Maestro decide por usuario).
        PermisoUsuario.objects.create(usuario=self.admin, opcion_menu=_opcion(CODIGO_DEVOLUCION_MP),
                                      puede_ver=True, puede_crear=True)
        self.assertTrue(puede_devolver_mercadopago(self.admin))

    def test_migracion_0237_sembro_devolver_mercadopago(self):
        opcion = OpcionMenu.objects.get(codigo=CODIGO_DEVOLUCION_MP)
        self.assertEqual(opcion.modulo.codigo, 'documentos')
        roles = set(PermisoRol.objects.filter(opcion_menu=opcion, puede_crear=True).values_list('rol', flat=True))
        self.assertEqual(roles, {'administrador', 'administracion'})


class ComandoConfigurarPerfilesTest(TestCase):
    def setUp(self):
        for c in CODIGOS:
            _opcion(c)
        _sembrar('administrador', True)
        self.admin = crear_usuario(username='javier', rol='administrador', email='jav.teb@gmail.com')

    def _run(self, *args):
        out = StringIO()
        call_command('configurar_perfiles', *args, stdout=out)
        return out.getvalue()

    def test_vista_previa_no_escribe(self):
        salida = self._run('--maestro', 'jav.teb@gmail.com')
        self.assertIn('VISTA PREVIA', salida)
        self.assertIn('javier: administrador -> maestro', salida)
        self.assertIn(CODIGO_NC_CLIENTES, salida)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.rol, 'administrador')
        self.assertEqual(_flags('administrador', CODIGO_NC_CLIENTES), perfiles.TODO)

    def test_aplicar(self):
        salida = self._run('--maestro', 'JAV.TEB@gmail.com', '--rol', 'administrador', '--rol', 'jefe', '--aplicar')
        self.assertIn('Listo', salida)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.rol, ROL_MAESTRO)
        self.assertEqual(_flags('administrador', CODIGO_NC_CLIENTES), perfiles.NADA)
        self.assertEqual(_flags('administrador', 'gestion_dte'), perfiles.TODO)
        self.assertEqual(_flags(ROL_JEFE, 'gestion_dte'), perfiles.TODO)
        self.assertIsNone(_fila(ROL_JEFE, 'ajuste_stock_rapido'))
        # Segunda pasada: nada que hacer.
        self.assertIn('sin cambios', self._run('--rol', 'administrador'))

    def test_usuario_inexistente(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self._run('--maestro', 'nadie@nada.cl')


class SeederJefeTest(TestCase):
    def test_inicializar_permisos_siembra_jefe(self):
        call_command('inicializar_permisos', stdout=StringIO())
        self.assertTrue(PermisoRol.objects.filter(rol=ROL_JEFE, puede_ver=True).exists())
        self.assertEqual(_flags(ROL_JEFE, 'gestion_dte'), perfiles.TODO)
        self.assertIsNone(_fila(ROL_JEFE, 'ajuste_stock_rapido'))
        self.assertIsNone(_fila(ROL_JEFE, CODIGO_NC_CLIENTES))
        self.assertIsNone(_fila(ROL_JEFE, 'giftcards_listado'))
        self.assertFalse(_fila(ROL_JEFE, 'devolucion_garantia').puede_aprobar)
        # El Jefe hereda el límite de descuento del Administrador.
        self.assertEqual(_fila(ROL_JEFE, 'gestion_dte').limite_descuento_porcentaje,
                         _fila('administrador', 'gestion_dte').limite_descuento_porcentaje)
