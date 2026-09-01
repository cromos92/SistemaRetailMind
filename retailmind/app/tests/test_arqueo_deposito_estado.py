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
    listar_arqueos_para_deposito,
)

from .factories import setup_entorno_completo

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


class _BaseArqueoDepositoTest(TestCase):
    """Helpers compartidos: crear arqueos con montos pisados y depositarles."""

    def setUp(self):
        self.env = setup_entorno_completo()
        self.hoy = timezone.localdate()

    def _arqueo(self, teorico, fisico, fondo=0, estado='CERRADO', transbank=0,
                fecha=None):
        arqueo = ArqueoCaja.objects.create(
            fecha_arqueo=fecha or self.hoy, sucursal=self.env['sucursal'],
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

    def _listar(self):
        rf = RequestFactory()
        request = rf.get(
            '/app/api/cuadratura/deposito-multidia/arqueos-disponibles/'
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
