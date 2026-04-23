"""
Tests para los fixes de Notas de Crédito en la cuadratura de caja y en el
generador de NC desde gestión de DTEs.

Cubre:
- `_calcular_cuadratura_data`: filtro que incluye NC con tipo_transaccion ANULACION,
  y descuento de NC por transferencia del total_transferencia teórico.
- `anular_factura_dte`: la NC parcial genera una ÚNICA línea "Devolución parcial"
  (con productoTalla=None) alineada al cabezal, y la NC total copia los productos
  tal cual del DTE original.
"""
import json
from decimal import Decimal

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, Dte_Detalle_Pago,
)
from app.views_modulo_ventas import _calcular_cuadratura_data

from .factories import (
    crear_correlativo, crear_producto_con_talla, setup_entorno_completo,
)

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


def _crear_boleta(env, numero, monto_con_iva, tipo_transaccion='VENTA_PUBLICO',
                  productos=None, receptor=None):
    """Crea una boleta electrónica con un producto, lista para ser anulada."""
    monto_neto = int(round(monto_con_iva / Decimal('1.19')))
    boleta = Dte.objects.create(
        emisor=env['empresa'],
        receptor=receptor,
        numero_documento=numero,
        tipo_documento='BOLETA ELECTRONICA',
        monto_con_iva=monto_con_iva,
        monto_neto=monto_neto,
        descuento=0,
        estado_pago='PAGADO',
        estado_dte='EMITIDO',
        responsable=env['user'].username,
        fecha_emision=timezone.localdate(),
        fecha_vencimiento=timezone.localdate(),
        diasCredito=0,
        bultos=0,
        unidades_productos=1,
        tipo_transaccion=tipo_transaccion,
        sucursal=env['sucursal'],
        es_nota_credito=False,
        hora=timezone.localtime().time(),
    )
    productos = productos or [(env['producto_talla'], 1, int(monto_con_iva))]
    for pt, cantidad, precio in productos:
        Dte_Productos.objects.create(
            dte=boleta,
            productoTalla=pt,
            descripcion='Producto Test',
            costo=0, sobreprecio=0,
            precio=precio,
            stock=cantidad,
            activo=True,
        )
    Dte_Detalle_Pago.objects.create(
        dte=boleta, metodo_pago='EFECTIVO', monto=int(monto_con_iva),
    )
    return boleta


def _crear_nc_directa(env, numero, monto_con_iva, tipo_transaccion,
                      metodo_pago_nc='EFECTIVO', documento_afectado=None):
    """Crea directamente una NC con su pago asociado (sin pasar por la vista)."""
    monto_neto = int(round(monto_con_iva / Decimal('1.19')))
    nc = Dte.objects.create(
        emisor=env['empresa'],
        receptor=documento_afectado.receptor if documento_afectado else None,
        numero_documento=numero,
        tipo_documento='NOTA DE CREDITO',
        monto_con_iva=monto_con_iva,
        monto_neto=monto_neto,
        descuento=0,
        estado_pago='PAGADO',
        estado_dte='EMITIDO',
        responsable=env['user'].username,
        fecha_emision=timezone.localdate(),
        fecha_vencimiento=timezone.localdate(),
        diasCredito=0,
        bultos=0,
        unidades_productos=0,
        tipo_transaccion=tipo_transaccion,
        sucursal=env['sucursal'],
        es_nota_credito=True,
        documento_afectado=documento_afectado,
        hora=timezone.localtime().time(),
    )
    if metodo_pago_nc:
        Dte_Detalle_Pago.objects.create(
            dte=nc, metodo_pago=metodo_pago_nc, monto=int(monto_con_iva),
        )
    return nc


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class CuadraturaNotaCreditoTest(TestCase):
    """Verifica que `_calcular_cuadratura_data` refleje correctamente las NC."""

    def setUp(self):
        self.env = setup_entorno_completo()
        self.hoy = timezone.localdate().strftime('%Y-%m-%d')

    def test_nc_anulacion_aparece_en_resumen(self):
        """
        REGRESIÓN Fix #1 — Una NC con tipo_transaccion='ANULACION' debe
        contabilizarse en `total_notas_credito` y `cantidad_notas_credito`
        aunque no descuente del efectivo (no tiene pago asociado).
        Antes estaba fuera del filtro por tipo_transaccion.
        """
        _crear_nc_directa(
            self.env, numero=9001, monto_con_iva=15000,
            tipo_transaccion='ANULACION', metodo_pago_nc=None,
        )
        c = _calcular_cuadratura_data(self.env['sucursal'], self.hoy)

        self.assertEqual(c['cantidad_notas_credito'], 1)
        self.assertEqual(int(c['total_notas_credito']), 15000)
        # Sin pago en efectivo → no descuenta del teórico
        self.assertEqual(int(c['total_nc_efectivo']), 0)
        self.assertEqual(int(c['total_nc_transferencia']), 0)

    def test_nc_devolucion_efectivo_resta_del_efectivo(self):
        """REGRESIÓN — NC por devolución en efectivo resta de total_efectivo."""
        boleta = _crear_boleta(self.env, numero=1001, monto_con_iva=30000)
        _crear_nc_directa(
            self.env, numero=9002, monto_con_iva=2000,
            tipo_transaccion='DEVOLUCION', metodo_pago_nc='EFECTIVO',
            documento_afectado=boleta,
        )

        c = _calcular_cuadratura_data(self.env['sucursal'], self.hoy)

        # Efectivo: boleta $30.000 - NC $2.000 = $28.000
        self.assertEqual(int(c['total_nc_efectivo']), 2000)
        self.assertEqual(int(c['total_efectivo']), 28000)
        self.assertEqual(int(c['total_notas_credito']), 2000)

    def test_nc_devolucion_transferencia_resta_del_teorico_transferencia(self):
        """
        Fix #3 — NC por transferencia debe restar de `total_transferencia`.
        Antes sólo sumaba en `total_nc_transferencia` sin restarse, dejando
        el teórico de transferencias inflado.
        """
        # Boleta pagada originalmente por transferencia
        boleta = Dte.objects.create(
            emisor=self.env['empresa'], receptor=None,
            numero_documento=1010, tipo_documento='BOLETA ELECTRONICA',
            monto_con_iva=50000, monto_neto=42017,
            descuento=0, estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable=self.env['user'].username,
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            diasCredito=0, bultos=0, unidades_productos=1,
            tipo_transaccion='VENTA_PUBLICO',
            sucursal=self.env['sucursal'], es_nota_credito=False,
            hora=timezone.localtime().time(),
        )
        Dte_Productos.objects.create(
            dte=boleta, productoTalla=self.env['producto_talla'],
            descripcion='Prod', costo=0, sobreprecio=0,
            precio=50000, stock=1, activo=True,
        )
        Dte_Detalle_Pago.objects.create(
            dte=boleta, metodo_pago='TRANSFERENCIA', monto=50000,
        )
        _crear_nc_directa(
            self.env, numero=9003, monto_con_iva=10000,
            tipo_transaccion='DEVOLUCION', metodo_pago_nc='TRANSFERENCIA',
            documento_afectado=boleta,
        )

        c = _calcular_cuadratura_data(self.env['sucursal'], self.hoy)

        # Transferencia teórica: boleta $50.000 - NC $10.000 = $40.000
        self.assertEqual(int(c['total_nc_transferencia']), 10000)
        self.assertEqual(int(c['total_transferencia']), 40000)
        self.assertEqual(int(c['total_nc_efectivo']), 0)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class AnularFacturaDteTest(TestCase):
    """
    Verifica el comportamiento de `anular_factura_dte` en los dos casos:
    NC total (copia productos 1:1) y NC parcial (una sola línea 'Devolución').
    """

    def setUp(self):
        self.env = setup_entorno_completo()
        crear_correlativo(
            self.env['sucursal'], tipo_dte='NOTA DE CREDITO', inicio=5000,
        )
        self.client = Client()
        self.client.login(username='testuser', password='TestPass123!')
        session = self.client.session
        session['idSucursalActual'] = self.env['sucursal'].id
        session['idEmpresaActual'] = self.env['empresa'].id
        session['nombreUsuario'] = 'testuser'
        session.save()

    def _post_anular(self, dte_id, **kwargs):
        body = {
            'dte_id': dte_id,
            'tipo_anulacion': kwargs.get('tipo_anulacion', 'DEVOLUCION'),
            'metodo_devolucion': kwargs.get('metodo_devolucion', 'EFECTIVO_CAJA'),
            'monto_nc': kwargs.get('monto_nc'),
            'cliente_nombre': kwargs.get('cliente_nombre', ''),
            'cliente_rut': kwargs.get('cliente_rut', ''),
            'motivo': kwargs.get('motivo', 'Cliente se arrepiente de la compra'),
        }
        return self.client.post(
            reverse('anular_factura_dte'),
            data=json.dumps(body),
            content_type='application/json',
        )

    def test_nc_parcial_crea_una_sola_linea_devolucion(self):
        """
        Fix #2 — NC parcial sobre boleta con zapatilla a $30.000: debe generar
        una única línea `Dte_Productos` con productoTalla=None y precio=2000
        (el monto de la NC), NO copiar el producto completo a $30.000.
        """
        boleta = _crear_boleta(self.env, numero=2001, monto_con_iva=30000)
        resp = self._post_anular(
            dte_id=boleta.id,
            tipo_anulacion='DEVOLUCION',
            metodo_devolucion='EFECTIVO_CAJA',
            monto_nc=2000,
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        nc = Dte.objects.filter(
            documento_afectado=boleta, es_nota_credito=True,
        ).first()
        self.assertIsNotNone(nc)
        self.assertEqual(int(nc.monto_con_iva), 2000)

        lineas = list(nc.dte_productos.all())
        self.assertEqual(
            len(lineas), 1,
            'NC parcial debe crear exactamente 1 línea conceptual',
        )
        linea = lineas[0]
        self.assertIsNone(
            linea.productoTalla,
            'La línea de NC parcial no debe asociar un ProductoTalla real',
        )
        self.assertEqual(int(linea.precio), 2000)
        self.assertEqual(int(linea.stock), 1)
        self.assertIn('Devolución parcial', linea.descripcion)
        self.assertIn('#2001', linea.descripcion)

        # La boleta no debe marcarse ANULADO (queda vigente para otra NC)
        boleta.refresh_from_db()
        self.assertEqual(boleta.estado_dte, 'EMITIDO')

    def test_nc_total_copia_productos_del_dte_original(self):
        """
        REGRESIÓN — NC por el monto total debe copiar los productos 1:1 del
        DTE original (cabezal y detalle cuadran) y marcar la boleta como ANULADO.
        """
        boleta = _crear_boleta(self.env, numero=2002, monto_con_iva=30000)
        resp = self._post_anular(
            dte_id=boleta.id,
            tipo_anulacion='DEVOLUCION',
            metodo_devolucion='EFECTIVO_CAJA',
            monto_nc=30000,
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        nc = Dte.objects.filter(
            documento_afectado=boleta, es_nota_credito=True,
        ).first()
        self.assertIsNotNone(nc)
        self.assertEqual(int(nc.monto_con_iva), 30000)

        lineas = list(nc.dte_productos.all())
        self.assertEqual(len(lineas), 1)
        linea = lineas[0]
        # NC total: copia 1:1 → conserva el ProductoTalla original
        self.assertEqual(linea.productoTalla_id, self.env['producto_talla'].id)
        self.assertEqual(int(linea.precio), 30000)
        self.assertEqual(int(linea.stock), 1)

        boleta.refresh_from_db()
        self.assertEqual(boleta.estado_dte, 'ANULADO')

    def test_nc_parcial_aparece_en_cuadratura(self):
        """
        Integración del Fix #1 + #2 — Una NC parcial de DEVOLUCION/EFECTIVO
        emitida por la vista real debe aparecer en la cuadratura del mismo día,
        restando del efectivo teórico por el monto parcial (no por el total
        del DTE original).
        """
        boleta = _crear_boleta(self.env, numero=2003, monto_con_iva=30000)
        resp = self._post_anular(
            dte_id=boleta.id,
            tipo_anulacion='DEVOLUCION',
            metodo_devolucion='EFECTIVO_CAJA',
            monto_nc=2000,
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        hoy = timezone.localdate().strftime('%Y-%m-%d')
        c = _calcular_cuadratura_data(self.env['sucursal'], hoy)

        self.assertEqual(c['cantidad_notas_credito'], 1)
        self.assertEqual(int(c['total_notas_credito']), 2000)
        self.assertEqual(int(c['total_nc_efectivo']), 2000)
        # Efectivo bruto boleta = $30.000; NC efectivo = $2.000
        # → efectivo teórico = $30.000 - $2.000 = $28.000
        self.assertEqual(int(c['total_efectivo']), 28000)
