"""
Listado de cotizaciones: colas de trabajo (filtros rápidos) y contadores.

`estado` acepta, además de VIGENTE/VENCIDA/FACTURADA/ANULADA:
  - POR_VENCER: vigentes que vencen dentro de 7 días.
  - DESPACHO_PENDIENTE: facturadas con stock por sacar (despacho diferido).
  - DOC_ANULADO: facturadas cuyo documento fue anulado (NC) o eliminado.
Los contadores de `estadisticas` se calculan ANTES del filtro por estado.

Correr en BD local desechable:
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_cotizaciones_listado_filtros
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from app.models import Cotizacion_Empresa, Cotizacion_Empresa_Detalle, Dte

from .factories import setup_entorno_completo


def _patch_permisos():
    return mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True)


class ListadoFiltrosRapidosTest(TestCase):

    def setUp(self):
        self.entorno = setup_entorno_completo()
        self.sucursal = self.entorno['sucursal']
        self.empresa = self.entorno['empresa']
        self.vendedor = self.entorno['vendedor']
        self.user = self.entorno['user']
        hoy = timezone.localdate()

        self.vigente = self._cot('COT-T-0001', validez=hoy + timedelta(days=30))
        self.por_vencer = self._cot('COT-T-0002', validez=hoy + timedelta(days=3))
        self.vencida = self._cot('COT-T-0003', validez=hoy - timedelta(days=2))

        # Facturada con despacho pendiente (documento vivo).
        self.dte_vivo = self._dte(101, 'EMITIDO')
        self.fact_pendiente = self._cot('COT-T-0004', validez=hoy)
        self.fact_pendiente.marcar_como_facturada('101', tiene_pendientes=True, dte=self.dte_vivo)

        # Facturada cuyo documento fue anulado por NC.
        self.dte_nc = self._dte(102, 'ANULADO')
        self.fact_anulada = self._cot('COT-T-0005', validez=hoy)
        self.fact_anulada.marcar_como_facturada('102', tiene_pendientes=False, dte=self.dte_nc)

        # Facturada normal (documento vivo, despacho completo).
        self.dte_ok = self._dte(103, 'EMITIDO')
        self.fact_ok = self._cot('COT-T-0006', validez=hoy)
        self.fact_ok.marcar_como_facturada('103', tiene_pendientes=False, dte=self.dte_ok)

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

    def _cot(self, numero, validez):
        cot = Cotizacion_Empresa.objects.create(
            sucursal=self.sucursal, cliente=self.empresa, vendedor=self.vendedor,
            usuario_creador=self.user, numero_cotizacion=numero,
            fecha_emision=timezone.localdate() - timedelta(days=1), fecha_validez=validez,
            total=10000,
        )
        Cotizacion_Empresa_Detalle.objects.create(
            cotizacion=cot, numero_linea=1, descripcion='Item', cantidad=1,
            precio_unitario=10000, subtotal=10000, es_producto_pendiente=True,
            nombre_producto_pendiente='Item',
        )
        return cot

    def _dte(self, numero, estado_dte):
        hoy = timezone.localdate()
        return Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa, numero_documento=numero,
            tipo_documento='FACTURA ELECTRONICA', monto_con_iva=10000, monto_neto=8403,
            descuento=0, estado_pago='PAGADO', estado_dte=estado_dte,
            responsable=self.user.username, fecha_emision=hoy, fecha_vencimiento=hoy,
            diasCredito=0, bultos=1, unidades_productos=1, tipo_transaccion='VENTA_PUBLICO',
            sucursal=self.sucursal, hora=timezone.localtime().time(),
        )

    def _listar(self, **params):
        with _patch_permisos():
            r = self.client.get(reverse('listar_cotizaciones'), params)
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertTrue(data.get('success'), data)
        return data

    def _numeros(self, data):
        return sorted(c['numero_cotizacion'] for c in data['cotizaciones'])

    def test_contadores_se_calculan_antes_del_filtro_por_estado(self):
        todo = self._listar()
        stats = todo['estadisticas']
        self.assertEqual(stats['total'], 6)
        self.assertEqual(stats['vigentes'], 2)
        self.assertEqual(stats['por_vencer'], 1)
        self.assertEqual(stats['vencidas'], 1)
        self.assertEqual(stats['facturadas'], 3)
        self.assertEqual(stats['despacho_pendiente'], 1)
        self.assertEqual(stats['doc_anulado'], 1)
        # Con un filtro por estado activo, los contadores no cambian.
        filtrado = self._listar(estado='VENCIDA')
        self.assertEqual(filtrado['estadisticas']['facturadas'], 3)
        self.assertEqual(filtrado['estadisticas']['total'], 6)
        self.assertEqual(self._numeros(filtrado), ['COT-T-0003'])

    def test_filtros_rapidos(self):
        self.assertEqual(self._numeros(self._listar(estado='POR_VENCER')), ['COT-T-0002'])
        self.assertEqual(self._numeros(self._listar(estado='DESPACHO_PENDIENTE')), ['COT-T-0004'])
        self.assertEqual(self._numeros(self._listar(estado='DOC_ANULADO')), ['COT-T-0005'])
        self.assertEqual(self._numeros(self._listar(estado='VIGENTE')), ['COT-T-0001', 'COT-T-0002'])
        self.assertEqual(self._numeros(self._listar(estado='FACTURADA')),
                         ['COT-T-0004', 'COT-T-0005', 'COT-T-0006'])

    def test_fila_facturada_con_nc_dice_documento_anulado_y_permite_reabrir(self):
        fila = {c['numero_cotizacion']: c for c in self._listar()['cotizaciones']}['COT-T-0005']
        self.assertTrue(fila['facturada'])
        self.assertTrue(fila['documento_muerto'])
        self.assertFalse(fila['sin_dte_enlazado'])
        self.assertFalse(fila['puede_facturar'])
