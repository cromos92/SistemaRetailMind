"""
Tests del resumen de KPIs de vencimiento de DTEs de compra
(obtener_resumen_pendientes_anio): clasificación vencido / por vencer / al día,
exclusión de pagados y robustez sin DTEs.

Ejecutar (en entorno con BD de test, NO producción):
    python manage.py test app.tests.test_dte_compras_kpi
"""
import json
from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.utils import timezone

from app.models import Dte
from app.tests.factories import crear_empresa, crear_usuario
from app.views_modulo_compras import obtener_resumen_pendientes_anio


class TestKpiPendientesDteCompra(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.proveedor = crear_empresa(nombre='Proveedor X', rut='77.111.111-1')
        cls.nuestra = crear_empresa(nombre='Nosotros SpA', rut='76.222.222-2')
        cls.user = crear_usuario(username='kpi-tester')

    def setUp(self):
        self.hoy = timezone.localdate()

    def _dte(self, num, venc_offset_dias, estado_pago='Pendiente', monto=119000):
        return Dte.objects.create(
            emisor=self.proveedor, receptor=self.nuestra,
            numero_documento=num, tipo_documento='FACTURA ELECTRONICA',
            monto_neto=int(monto / 1.19), monto_con_iva=monto,
            estado_pago=estado_pago, estado_dte='EMITIDO',
            fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy + timedelta(days=venc_offset_dias),
            tipo_transaccion='COMPRA',
        )

    def _resumen(self):
        req = RequestFactory().get('/app/api/resumen-pendientes-anio/')
        req.user = self.user
        req.session = {'idEmpresaActual': self.nuestra.id}
        return json.loads(obtener_resumen_pendientes_anio(req).content)

    def test_endpoint_no_crashea_sin_dtes(self):
        r = self._resumen()
        self.assertTrue(r['success'])
        self.assertEqual(r['cantidad_pendientes'], 0)
        self.assertEqual(r['vencidos'], 0)

    def test_clasificacion_por_vencimiento(self):
        self._dte(1001, -5)    # vencido (fecha pasada)
        self._dte(1002, 3)     # por vencer (<= 7 días)
        self._dte(1003, 30)    # al día (> 7 días)
        self._dte(1004, 10, estado_pago='Pagado')  # NO cuenta como pendiente

        r = self._resumen()
        self.assertTrue(r['success'])
        self.assertEqual(r['cantidad_pendientes'], 3)
        self.assertEqual(r['vencidos'], 1)
        self.assertEqual(r['por_vencer_pronto'], 1)
        self.assertEqual(r['al_dia'], 1)
        # Los tres buckets deben sumar exactamente los pendientes
        self.assertEqual(
            r['vencidos'] + r['por_vencer_pronto'] + r['al_dia'],
            r['cantidad_pendientes'])

    def test_parcial_cuenta_como_pendiente(self):
        self._dte(2001, 20, estado_pago='Parcial')
        r = self._resumen()
        self.assertEqual(r['cantidad_pendientes'], 1)
        self.assertEqual(r['al_dia'], 1)

    def test_monto_pendiente_suma_saldos(self):
        self._dte(3001, 30, monto=119000)
        self._dte(3002, 30, monto=238000)
        r = self._resumen()
        self.assertEqual(r['cantidad_pendientes'], 2)
        self.assertEqual(int(r['monto_pendiente']), 357000)
