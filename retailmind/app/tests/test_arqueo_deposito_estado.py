"""
Registrar un depósito no debe cambiar el veredicto del conteo del arqueo.

Los tres endpoints de depósito (`agregar_deposito_arqueo`,
`confirmar_deposito` y `crear_deposito_multidia`) decidían
CERRADO / CON_DIFERENCIAS con `ArqueoCaja.diferencia_efectivo_real`, que resta
el teórico DOS veces en cuanto existe un depósito:

    efectivo_en_caja         = físico - depósitos - fondo_fijo
    diferencia_efectivo_real = efectivo_en_caja - teórico

Como el conteo se hace ANTES de depositar, un día perfectamente cuadrado
quedaba marcado CON_DIFERENCIAS por el solo hecho de haber depositado.
"""
import json
from decimal import Decimal

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from app.models import ArqueoCaja, DepositoBancario
from app.views_modulo_ventas import (
    ESTADOS_ARQUEO_RECALCULABLES_POR_DEPOSITO,
    TOLERANCIA_ARQUEO_EFECTIVO,
    _reevaluar_estado_arqueo_por_deposito,
    _sucursales_permitidas,
    crear_deposito_multidia,
    listar_arqueos_para_deposito,
    registrar_comprobante_supervisor,
)

from .factories import crear_sucursal, setup_entorno_completo

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


class _BaseArqueoDepositoTest(TestCase):
    """Helpers compartidos: crear arqueos con montos pisados y depositarles."""

    def setUp(self):
        self.env = setup_entorno_completo()
        self.hoy = timezone.localdate()

    def _arqueo(self, teorico, fisico, fondo=0, estado='CERRADO', transbank=0,
                fecha=None, sucursal=None):
        arqueo = ArqueoCaja.objects.create(
            fecha_arqueo=fecha or self.hoy, sucursal=sucursal or self.env['sucursal'],
            usuario_responsable=self.env['user'], estado=estado,
        )
        ArqueoCaja.objects.filter(pk=arqueo.pk).update(
            total_efectivo_teorico=teorico,
            total_efectivo_fisico=fisico,
            fondo_fijo_snapshot=fondo,
            diferencia_efectivo=fisico - (teorico + fondo),
            diferencia_transbank=transbank,
            estado=estado,
        )
        arqueo.refresh_from_db()
        return arqueo

    def _depositar(self, arqueo, monto):
        DepositoBancario.objects.create(
            arqueo=arqueo, fecha_deposito=self.hoy, monto=monto,
            monto_declarado=monto, monto_confirmado=monto, banco='ESTADO',
            numero_comprobante='X1', declarado_por=self.env['user'],
            fecha_declaracion=timezone.now(), registrado_por=self.env['user'],
            verificado=True, verificado_por=self.env['user'],
            fecha_verificacion=timezone.now(),
        )
        arqueo.refresh_from_db()
        return arqueo

    def _sucursal_b(self):
        """Segunda sucursal de la misma empresa (la pill "otra tienda")."""
        return crear_sucursal(empresa=self.env['empresa'], alias='SUC-B')

    def _como(self, rol):
        self.env['user'].rol = rol
        self.env['user'].save(update_fields=['rol'])


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class EstadoArqueoTrasDepositoTest(_BaseArqueoDepositoTest):

    # ---------- el bug reportado ----------

    def test_dia_perfecto_sigue_cerrado_despues_de_depositar(self):
        """El caso exacto que se reportó: teórico 122.460, fondo 20.000,
        contado 142.460 (Dif. Conteo $0). Depositar los 122.460 lo marcaba
        CON_DIFERENCIAS."""
        arqueo = self._arqueo(teorico=122460, fisico=142460, fondo=20000)
        self.assertEqual(arqueo.diferencia_efectivo, 0)

        self._depositar(arqueo, 122460)
        # La property vieja "prueba" que el día está descuadrado...
        self.assertEqual(arqueo.diferencia_efectivo_real, -122460)
        # ...pero el conteo sigue cuadrado y el estado no debe moverse.
        self.assertEqual(_reevaluar_estado_arqueo_por_deposito(arqueo), 'CERRADO')
        arqueo.refresh_from_db()
        self.assertEqual(arqueo.estado, 'CERRADO')

    def test_faltante_real_sigue_marcado_con_diferencias(self):
        """No se blanquea nada: si faltó plata en el conteo, sigue marcado."""
        arqueo = self._arqueo(teorico=100000, fisico=90000, estado='CERRADO')
        self.assertEqual(arqueo.diferencia_efectivo, -10000)
        self._depositar(arqueo, 90000)
        self.assertEqual(
            _reevaluar_estado_arqueo_por_deposito(arqueo), 'CON_DIFERENCIAS')

    def test_diferencia_dentro_de_tolerancia_cuadra(self):
        arqueo = self._arqueo(
            teorico=100000, fisico=100000 - TOLERANCIA_ARQUEO_EFECTIVO,
            estado='CON_DIFERENCIAS')
        self.assertEqual(
            _reevaluar_estado_arqueo_por_deposito(arqueo), 'CERRADO')

    def test_diferencia_de_transbank_tambien_descuadra(self):
        arqueo = self._arqueo(teorico=100000, fisico=100000, transbank=50000)
        self.assertEqual(
            _reevaluar_estado_arqueo_por_deposito(arqueo), 'CON_DIFERENCIAS')

    def test_corrige_un_arqueo_mal_marcado(self):
        """Un arqueo que cuadra pero quedó CON_DIFERENCIAS vuelve a CERRADO."""
        arqueo = self._arqueo(
            teorico=50000, fisico=50000, estado='CON_DIFERENCIAS')
        self.assertEqual(_reevaluar_estado_arqueo_por_deposito(arqueo), 'CERRADO')
        arqueo.refresh_from_db()
        self.assertEqual(arqueo.estado, 'CERRADO')

    # ---------- no pisar el avance del supervisor ----------

    def test_no_degrada_un_arqueo_revisado_ni_con_deposito_confirmado(self):
        """Estos estados son avance auditado del supervisor: el veredicto
        automático los borraba."""
        for estado in ('REVISADO', 'DEPOSITO_CONFIRMADO', 'DEPOSITO_DECLARADO'):
            with self.subTest(estado=estado):
                arqueo = self._arqueo(
                    teorico=100000, fisico=50000, estado=estado)
                ArqueoCaja.objects.filter(pk=arqueo.pk).update(estado=estado)
                arqueo.refresh_from_db()
                self.assertEqual(
                    _reevaluar_estado_arqueo_por_deposito(arqueo), estado)
                arqueo.refresh_from_db()
                self.assertEqual(arqueo.estado, estado)
                arqueo.delete()

    def test_estados_recalculables_son_los_esperados(self):
        self.assertEqual(
            set(ESTADOS_ARQUEO_RECALCULABLES_POR_DEPOSITO),
            {'ABIERTO', 'CERRADO', 'CON_DIFERENCIAS'},
        )

    def test_depositar_de_mas_no_altera_el_veredicto(self):
        """Un depósito por encima del teórico tampoco descuadra el conteo."""
        arqueo = self._arqueo(teorico=100000, fisico=100000)
        self._depositar(arqueo, 250000)
        self.assertEqual(_reevaluar_estado_arqueo_por_deposito(arqueo), 'CERRADO')


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class ListarArqueosParaDepositoTest(_BaseArqueoDepositoTest):
    """El modal multi-día debe ofrecer también los arqueos REVISADO (o con
    depósito declarado/confirmado) que quedaron con efectivo pendiente —
    p.ej. cuando se eliminó un depósito DESPUÉS de la revisión del
    supervisor (`eliminar_deposito_bancario` no degrada REVISADO a
    propósito). Antes el filtro por estado los escondía y no quedaba
    NINGUNA vía en la UI para asignarles el saldo: el comprobante único
    también rechaza un segundo depósito si ya hay uno verificado.
    """

    def setUp(self):
        super().setUp()
        # El endpoint exige rol supervisor.
        self.env['user'].rol = 'administrador'
        self.env['user'].save(update_fields=['rol'])

    def _listar(self, **params):
        rf = RequestFactory()
        request = rf.get(
            '/app/api/cuadratura/deposito-multidia/arqueos-disponibles/', params
        )
        request.user = self.env['user']
        request.session = {'idSucursalActual': self.env['sucursal'].id}
        data = json.loads(listar_arqueos_para_deposito(request).content)
        self.assertTrue(data['success'], data.get('error'))
        return data['arqueos']

    def _fila(self, filas, arqueo):
        return next((f for f in filas if f['id'] == arqueo.id), None)

    def test_revisado_con_pendiente_aparece(self):
        """El caso NICK2 26-ago-2026: revisado OK, se eliminó un depósito y
        quedó debiendo $125.940 — tiene que aparecer para el multi-día."""
        arqueo = self._arqueo(teorico=167200, fisico=167200, estado='REVISADO')
        self._depositar(arqueo, 41260)

        fila = self._fila(self._listar(), arqueo)

        self.assertIsNotNone(fila)
        self.assertEqual(fila['pendiente_depositar'], 125940)
        self.assertEqual(fila['estado'], 'Revisado por Supervisor')

    def test_revisado_totalmente_depositado_no_aparece(self):
        """Un REVISADO sin deuda no ensucia la lista del multi-día."""
        arqueo = self._arqueo(teorico=167200, fisico=167200, estado='REVISADO')
        self._depositar(arqueo, 167200)

        self.assertIsNone(self._fila(self._listar(), arqueo))

    def test_estados_base_se_listan_aunque_no_deban_plata(self):
        """Comportamiento histórico intacto: CERRADO se lista siempre."""
        arqueo = self._arqueo(teorico=100000, fisico=100000, estado='CERRADO')
        self._depositar(arqueo, 100000)

        self.assertIsNotNone(self._fila(self._listar(), arqueo))


    def test_respeta_sucursal_id_de_la_pill(self):
        """En Revisión de Arqueos la pill puede ser otra tienda que la de
        sesión: el modal multi-día tiene que listar ESA tienda. Antes leía
        sólo la sesión y "faltaban días" para la tienda seleccionada."""
        b = self._sucursal_b()
        arqueo_a = self._arqueo(teorico=100000, fisico=100000)
        arqueo_b = self._arqueo(teorico=50000, fisico=50000, sucursal=b)

        filas = self._listar(sucursal_id=b.id)

        self.assertIsNotNone(self._fila(filas, arqueo_b))
        self.assertIsNone(self._fila(filas, arqueo_a))

    def test_sucursal_no_permitida_cae_a_la_de_sesion(self):
        self._como('administracion')   # sólo ve las sucursales asignadas
        b = self._sucursal_b()
        arqueo_a = self._arqueo(teorico=100000, fisico=100000)
        arqueo_b = self._arqueo(teorico=50000, fisico=50000, sucursal=b)

        filas = self._listar(sucursal_id=b.id)

        self.assertIsNotNone(self._fila(filas, arqueo_a))
        self.assertIsNone(self._fila(filas, arqueo_b))


class SucursalesPermitidasTest(_BaseArqueoDepositoTest):
    """`_sucursales_permitidas` leía `s['sucursal_id']` sobre instancias de
    Sucursal: el error caía en el except y TODO usuario quedaba acotado a su
    sucursal de sesión, con lo que las pills y "Todas" se ignoraban."""

    def _permitidas(self):
        rf = RequestFactory()
        request = rf.get('/')
        request.user = self.env['user']
        request.session = {'idSucursalActual': self.env['sucursal'].id}
        permitidas, _ = _sucursales_permitidas(request)
        return permitidas

    def test_administrador_ve_todas_las_sucursales(self):
        self._como('administrador')
        b = self._sucursal_b()

        permitidas = self._permitidas()

        self.assertIn(self.env['sucursal'].id, permitidas)
        self.assertIn(b.id, permitidas)

    def test_administracion_solo_sus_sucursales_asignadas(self):
        self._como('administracion')
        b = self._sucursal_b()

        permitidas = self._permitidas()

        self.assertIn(self.env['sucursal'].id, permitidas)
        self.assertNotIn(b.id, permitidas)


class CrearDepositoMultidiaSucursalTest(_BaseArqueoDepositoTest):
    """`crear_deposito_multidia` debe grabar en la sucursal de la pill."""

    def setUp(self):
        super().setUp()
        self._como('administrador')

    def _crear(self, arqueo, monto, **extra):
        rf = RequestFactory()
        data = {
            'fecha_deposito': self.hoy.strftime('%Y-%m-%d'),
            'monto_total': monto, 'banco': 'ESTADO', 'numero_comprobante': 'MD-1',
            'desglose': json.dumps([{'arqueo_id': arqueo.id, 'monto': monto}]),
        }
        data.update(extra)
        request = rf.post('/app/api/cuadratura/deposito-multidia/crear/', data)
        request.user = self.env['user']
        request.session = {'idSucursalActual': self.env['sucursal'].id}
        return json.loads(crear_deposito_multidia(request).content)

    def test_graba_en_la_sucursal_de_la_pill(self):
        b = self._sucursal_b()
        arqueo_b = self._arqueo(teorico=50000, fisico=50000, sucursal=b)

        res = self._crear(arqueo_b, 50000, sucursal_id=b.id)

        self.assertTrue(res['success'], res.get('error'))
        self.assertEqual(arqueo_b.depositos.count(), 1)

    def test_sin_sucursal_id_sigue_usando_la_sesion(self):
        b = self._sucursal_b()
        arqueo_b = self._arqueo(teorico=50000, fisico=50000, sucursal=b)

        res = self._crear(arqueo_b, 50000)

        self.assertFalse(res['success'])
        self.assertEqual(arqueo_b.depositos.count(), 0)


class RegistrarComprobanteSupervisorTest(_BaseArqueoDepositoTest):
    """"Registrar Depósito" admite varios depósitos por arqueo. Antes rechazaba
    cualquier segundo comprobante verificado, así que un día depositado en
    partes sólo se completaba borrando el primero y recargando el total. Lo
    que se controla ahora es el saldo pendiente, no la cantidad."""

    def setUp(self):
        super().setUp()
        self._como('administrador')

    def _registrar(self, arqueo, monto, **extra):
        rf = RequestFactory()
        data = {
            'arqueo_id': arqueo.id, 'monto': monto, 'banco': 'CHILE',
            'numero_comprobante': f'C-{monto}',
            'fecha_deposito': self.hoy.strftime('%Y-%m-%d'),
        }
        data.update(extra)
        request = rf.post('/app/api/arqueo/comprobante/', data)
        request.user = self.env['user']
        request.session = {'idSucursalActual': self.env['sucursal'].id}
        response = registrar_comprobante_supervisor(request)
        return response.status_code, json.loads(response.content)

    def test_segundo_deposito_parcial_se_acepta(self):
        arqueo = self._arqueo(teorico=100000, fisico=100000)
        self._depositar(arqueo, 40000)

        _, res = self._registrar(arqueo, 60000)

        self.assertTrue(res['success'], res.get('error'))
        self.assertEqual(arqueo.depositos.count(), 2)
        act = res['arqueo_actualizado']
        self.assertEqual(act['pendiente_depositar'], 0)
        self.assertEqual(act['estado_deposito'], 'COMPLETO')
        self.assertEqual(act['cantidad_depositos'], 2)
        self.assertIn('completamente depositado', res['message'])

    def test_cuatro_depositos_parciales(self):
        """No hay tope por cantidad: 3, 4 o los que hagan falta."""
        arqueo = self._arqueo(teorico=100000, fisico=100000)
        for monto in (20000, 20000, 20000):
            _, res = self._registrar(arqueo, monto)
            self.assertTrue(res['success'], res.get('error'))

        _, res = self._registrar(arqueo, 40000)

        self.assertTrue(res['success'], res.get('error'))
        self.assertEqual(arqueo.depositos.count(), 4)
        self.assertEqual(res['arqueo_actualizado']['pendiente_depositar'], 0)

    def test_rechaza_si_ya_esta_cubierto(self):
        arqueo = self._arqueo(teorico=100000, fisico=100000)
        self._depositar(arqueo, 100000)

        _, res = self._registrar(arqueo, 5000)

        self.assertFalse(res['success'])
        self.assertIn('no queda saldo', res['error'])
        self.assertEqual(arqueo.depositos.count(), 1)

    def test_rechaza_monto_sobre_el_pendiente(self):
        arqueo = self._arqueo(teorico=100000, fisico=100000)
        self._depositar(arqueo, 40000)

        # pendiente 60.000 + holgura 10.000 (10% del esperado) < 90.000
        _, res = self._registrar(arqueo, 90000)

        self.assertFalse(res['success'])
        self.assertIn('excede el saldo pendiente', res['error'])
        self.assertEqual(arqueo.depositos.count(), 1)

    def test_rechaza_si_caja_ya_declaro_el_saldo(self):
        """Si el cajero declaró el total y falta confirmarlo, el supervisor
        debe confirmar ese depósito, no registrar otro encima."""
        arqueo = self._arqueo(teorico=100000, fisico=100000)
        DepositoBancario.objects.create(
            arqueo=arqueo, fecha_deposito=self.hoy, monto=0, monto_declarado=100000,
            monto_confirmado=0, banco='ESTADO', declarado_por=self.env['user'],
            fecha_declaracion=timezone.now(), registrado_por=self.env['user'],
            verificado=False,
        )

        _, res = self._registrar(arqueo, 100000)

        self.assertFalse(res['success'])
        self.assertIn('pendientes de confirmar', res['error'])

    def test_sin_teorico_registrado_se_acepta(self):
        """Comportamiento histórico: sin teórico no hay contra qué validar."""
        arqueo = self._arqueo(teorico=0, fisico=0)

        _, res = self._registrar(arqueo, 5000)

        self.assertTrue(res['success'], res.get('error'))

    def test_sucursal_ajena_403(self):
        self._como('administracion')
        arqueo_b = self._arqueo(teorico=50000, fisico=50000, sucursal=self._sucursal_b())

        status, res = self._registrar(arqueo_b, 50000)

        self.assertEqual(status, 403)
        self.assertFalse(res['success'])
        self.assertEqual(arqueo_b.depositos.count(), 0)
