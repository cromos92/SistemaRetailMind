"""
Tests del módulo Devolución de Dinero por Garantía (flujo de aprobación en
dos pasos + modo cantidad/monto + control de caja).

Cubre el service `devolucion_garantia_service` (crear/aprobar/rechazar/anular,
disponibilidad, razón SII, impacto en cuadratura) y el gate de permiso
(`devolucion_garantia.puede_aprobar`) del endpoint de aprobación.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, Dte_Detalle_Pago, Empresa, Correlativo,
    DevolucionGarantia, ModuloSistema, OpcionMenu, PermisoRol,
)
from app.services import devolucion_garantia_service as service
from app.views_modulo_ventas import _calcular_cuadratura_data

from .factories import (
    setup_entorno_completo, crear_producto_con_talla, crear_correlativo,
    crear_usuario, crear_sucursal,
)

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


def _receptor(env):
    if env.get('receptor_dg') is not None:
        return env['receptor_dg']
    receptor = Empresa.objects.create(
        nombre='Cliente Garantia', rut='11.111.111-1',
        razon_social='Cliente Garantia', nombre_fantasia='Cliente Garantia',
        giro='Particular', direccion='Calle 1', comuna='Santiago', ciudad='Santiago',
        esProveedor=False,
    )
    env['receptor_dg'] = receptor
    return receptor


def _crear_documento(env, numero, lineas, tipo_documento='BOLETA ELECTRONICA',
                     tipo_transaccion='VENTA_PUBLICO', receptor=None,
                     fecha_emision=None, metodo_pago='EFECTIVO'):
    """
    Crea un DTE de venta con líneas explícitas.

    `lineas`: lista de (producto_talla, cantidad, precio_linea_unitario). El
    precio se guarda tal cual (boleta = con IVA, factura = neto); monto_item
    queda en 0 para que `monto_real_linea_dte` caiga al fallback precio*stock
    y la base (BRUTO/NETO) se detecte limpia.
    """
    if receptor is None:
        receptor = _receptor(env)
    total = sum(precio * cant for _, cant, precio in lineas)
    if 'BOLETA' in tipo_documento:
        monto_con_iva = total
        monto_neto = int(round(total / Decimal('1.19')))
    else:
        monto_neto = total
        monto_con_iva = int(round(total * Decimal('1.19')))
    dte = Dte.objects.create(
        emisor=env['empresa'], receptor=receptor, numero_documento=numero,
        tipo_documento=tipo_documento, monto_con_iva=monto_con_iva, monto_neto=monto_neto,
        descuento=0, estado_pago='PAGADO', estado_dte='EMITIDO',
        responsable=env['user'].username,
        fecha_emision=fecha_emision or timezone.localdate(),
        fecha_vencimiento=timezone.localdate(), diasCredito=0, bultos=0,
        unidades_productos=sum(c for _, c, _ in lineas),
        tipo_transaccion=tipo_transaccion, sucursal=env['sucursal'],
        es_nota_credito=False, hora=timezone.localtime().time(),
    )
    for pt, cant, precio in lineas:
        Dte_Productos.objects.create(
            dte=dte, productoTalla=pt, descripcion=pt.producto.articulo,
            costo=0, sobreprecio=0, precio=precio, stock=cant, activo=True,
        )
    if metodo_pago:
        Dte_Detalle_Pago.objects.create(dte=dte, metodo_pago=metodo_pago, monto=monto_con_iva)
    return dte


def _nc_externa_por_talla(env, numero, documento_afectado, productoTalla, cantidad, precio):
    """NC de otra vía (gestión-DTE) con una línea por talla, para probar el
    guard anti-sobre-acreditación."""
    monto = precio * cantidad
    nc = Dte.objects.create(
        emisor=env['empresa'], receptor=documento_afectado.receptor, numero_documento=numero,
        tipo_documento='NOTA DE CREDITO', monto_con_iva=monto,
        monto_neto=int(round(monto / Decimal('1.19'))), descuento=0,
        estado_pago='PAGADO', estado_dte='EMITIDO', responsable=env['user'].username,
        fecha_emision=timezone.localdate(), fecha_vencimiento=timezone.localdate(),
        diasCredito=0, bultos=0, unidades_productos=cantidad, tipo_transaccion='DEVOLUCION',
        sucursal=env['sucursal'], es_nota_credito=True, documento_afectado=documento_afectado,
        hora=timezone.localtime().time(),
    )
    Dte_Productos.objects.create(
        dte=nc, productoTalla=productoTalla, descripcion='[DEV] ext',
        costo=0, sobreprecio=0, precio=precio, stock=cantidad, activo=True,
    )
    return nc


def _nc_externa_conceptual(env, numero, documento_afectado, monto):
    """NC 'corrige montos' de otra vía: línea conceptual sin talla."""
    nc = Dte.objects.create(
        emisor=env['empresa'], receptor=documento_afectado.receptor, numero_documento=numero,
        tipo_documento='NOTA DE CREDITO', monto_con_iva=monto,
        monto_neto=int(round(monto / Decimal('1.19'))), descuento=0,
        estado_pago='PAGADO', estado_dte='EMITIDO', responsable=env['user'].username,
        fecha_emision=timezone.localdate(), fecha_vencimiento=timezone.localdate(),
        diasCredito=0, bultos=0, unidades_productos=0, tipo_transaccion='DEVOLUCION',
        sucursal=env['sucursal'], es_nota_credito=True, documento_afectado=documento_afectado,
        hora=timezone.localtime().time(),
    )
    Dte_Productos.objects.create(
        dte=nc, productoTalla=None, descripcion='[CORRIGE MONTO] ext',
        costo=0, sobreprecio=0, precio=monto, stock=1, activo=True,
    )
    return nc


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DevolucionGarantiaServiceTest(TestCase):

    def setUp(self):
        self.env = setup_entorno_completo()
        self.user = self.env['user']
        self.sucursal = self.env['sucursal']
        self.pt = self.env['producto_talla']  # sku 1000001, precioventa 20000
        crear_correlativo(self.sucursal, tipo_dte='NOTA DE CREDITO')
        self.hoy = timezone.localdate()
        self.hoy_str = self.hoy.strftime('%Y-%m-%d')

    def _crear_solicitud(self, dte, detalles, motivo='Garantía'):
        return service.crear_solicitud_devolucion(
            dte_original=dte, sucursal=self.sucursal, receptor=_receptor(self.env),
            motivo=motivo, usuario=self.user, detalles=detalles,
        )

    # ---------- creación ----------

    def test_crear_solicitud_no_consume_folio_ni_crea_nc(self):
        boleta = _crear_documento(self.env, 5001, [(self.pt, 2, 11900)])
        corr = Correlativo.objects.get(sucursal=self.sucursal, tipo_dte='NOTA DE CREDITO')
        inicio_antes = corr.inicio

        dev = self._crear_solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                              'modo': 'CANTIDAD', 'cantidad': 1}])

        self.assertEqual(dev.estado, 'PENDIENTE')
        self.assertIsNone(dev.nota_credito)
        self.assertEqual(Dte.objects.filter(es_nota_credito=True).count(), 0)
        corr.refresh_from_db()
        self.assertEqual(corr.inicio, inicio_antes)  # folio NC intacto

    # ---------- aprobación: montos ----------

    def test_aprobar_boleta_montos_iva_incluido(self):
        boleta = _crear_documento(self.env, 5002, [(self.pt, 2, 11900)])  # con IVA
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                              'modo': 'CANTIDAD', 'cantidad': 1}])
        dev, nc, _txt, _warns = service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user,
            metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy,
        )
        self.assertEqual(dev.estado, 'NC_GENERADA')
        self.assertEqual(int(nc.monto_con_iva), 11900)
        self.assertEqual(int(nc.monto_neto), 10000)
        self.assertEqual(nc.tipo_transaccion, 'DEVOLUCION')
        pago = nc.dte_asociado.first()
        self.assertEqual(pago.metodo_pago, 'EFECTIVO')
        self.assertEqual(pago.fecha_pago, self.hoy)
        self.assertEqual(dev.autorizado_por_id, self.user.id)
        self.assertIsNotNone(dev.fecha_aprobacion)

    def test_aprobar_factura_montos_neto(self):
        _, pt2 = crear_producto_con_talla(self.sucursal, articulo='Bota', sku=2000002)
        factura = _crear_documento(self.env, 5003, [(pt2, 2, 10000)],
                                   tipo_documento='FACTURA ELECTRONICA', tipo_transaccion='VENTA')
        dev = self._crear_solicitud(factura, [{'dte_producto_id': factura.dte_productos.first().id,
                                              'modo': 'CANTIDAD', 'cantidad': 1}])
        dev, nc, _txt, _warns = service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user,
            metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy,
        )
        self.assertEqual(int(nc.monto_neto), 10000)
        self.assertEqual(int(nc.monto_con_iva), 11900)

    def test_modo_monto_parcial_linea_conceptual_razon_3(self):
        boleta = _crear_documento(self.env, 5004, [(self.pt, 1, 39990)])
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                              'modo': 'MONTO', 'monto': 10000}])
        dev, nc, _txt, _warns = service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user,
            metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy,
        )
        self.assertEqual(int(nc.monto_con_iva), 10000)
        linea = nc.dte_productos.first()
        self.assertIsNone(linea.productoTalla_id)
        self.assertEqual(linea.stock, 1)
        self.assertTrue(linea.descripcion.startswith('[CORRIGE MONTO]'))
        import json
        self.assertEqual(json.loads(nc.referencias)[0]['razon'], '3')

    def test_razon_sii_1_total_sin_nc_previas(self):
        boleta = _crear_documento(self.env, 5005, [(self.pt, 1, 11900)])
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                              'modo': 'CANTIDAD', 'cantidad': 1}])
        _dev, nc, _txt, _warns = service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user,
            metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy,
        )
        import json
        self.assertEqual(json.loads(nc.referencias)[0]['razon'], '1')

    def test_razon_sii_3_total_con_nc_previa(self):
        boleta = _crear_documento(self.env, 5006, [(self.pt, 2, 11900)])
        dp = boleta.dte_productos.first()
        _nc_externa_por_talla(self.env, 9006, boleta, self.pt, 1, 11900)
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': dp.id, 'modo': 'CANTIDAD', 'cantidad': 1}])
        _dev, nc, _txt, _warns = service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user,
            metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy,
        )
        import json
        # Cubre el saldo restante pero hay NC previa viva → razón '3'.
        self.assertEqual(json.loads(nc.referencias)[0]['razon'], '3')

    def test_razon_sii_3_con_otra_solicitud_pendiente(self):
        """Otra solicitud PENDIENTE reserva saldo pero NO convierte a la que se
        aprueba en 'anula documento': la razón compara contra el saldo REAL."""
        import json
        _, pt2 = crear_producto_con_talla(self.sucursal, articulo='Sandalia', sku=3000003)
        boleta = _crear_documento(self.env, 5020, [(self.pt, 1, 11900), (pt2, 1, 23800)])
        dps = {dp.productoTalla_id: dp for dp in boleta.dte_productos.all()}
        dev_a = self._crear_solicitud(boleta, [{'dte_producto_id': dps[self.pt.id].id,
                                                'modo': 'CANTIDAD', 'cantidad': 1}])  # $11.900 pendiente
        dev_b = self._crear_solicitud(boleta, [{'dte_producto_id': dps[pt2.id].id,
                                                'modo': 'CANTIDAD', 'cantidad': 1}])  # $23.800
        # Aprobar B: cubre el monto_restante (35.700-11.900=23.800) pero NO el
        # documento completo → razón '3', no '1'.
        _dev, nc, _txt, _warns = service.aprobar_devolucion(
            devolucion_id=dev_b.id, aprobador=self.user,
            metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy,
        )
        self.assertEqual(json.loads(nc.referencias)[0]['razon'], '3')
        dev_a.refresh_from_db()
        self.assertEqual(dev_a.estado, 'PENDIENTE')  # A sigue aprobable

    def test_cantidad_bloqueada_por_consumo_monto_previo(self):
        """Una devolución MONTO aprobada reduce el $ de la línea: CANTIDAD no
        puede ignorarlo y sobre-devolver (antes pasaba)."""
        boleta = _crear_documento(self.env, 5021, [(self.pt, 2, 11900)])  # línea $23.800
        dp = boleta.dte_productos.first()
        dev_m = self._crear_solicitud(boleta, [{'dte_producto_id': dp.id, 'modo': 'MONTO', 'monto': 15000}])
        service.aprobar_devolucion(devolucion_id=dev_m.id, aprobador=self.user,
                                   metodo_devolucion='NO_AFECTA_CAJA')
        # Quedan $8.800 de la línea: 1 unidad ($11.900) ya no cabe.
        with self.assertRaises(service.DevolucionGarantiaError):
            self._crear_solicitud(boleta, [{'dte_producto_id': dp.id, 'modo': 'CANTIDAD', 'cantidad': 1}])

    def test_mixta_factura_lineas_nc_en_neto(self):
        """NC mixta CANTIDAD+MONTO sobre factura: TODAS las líneas quedan en
        base NETO (mezclar bases descuadraba el TXT)."""
        _, pt2 = crear_producto_con_talla(self.sucursal, articulo='Zapato', sku=4000004)
        factura = _crear_documento(self.env, 5022, [(self.pt, 1, 10000), (pt2, 1, 20000)],
                                   tipo_documento='FACTURA ELECTRONICA', tipo_transaccion='VENTA')
        dps = {dp.productoTalla_id: dp for dp in factura.dte_productos.all()}
        dev = self._crear_solicitud(factura, [
            {'dte_producto_id': dps[self.pt.id].id, 'modo': 'CANTIDAD', 'cantidad': 1},
            {'dte_producto_id': dps[pt2.id].id, 'modo': 'MONTO', 'monto': 5950},  # con IVA
        ])
        _dev, nc, _txt, _warns = service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user,
            metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy,
        )
        linea_cant = nc.dte_productos.filter(productoTalla__isnull=False).first()
        linea_monto = nc.dte_productos.filter(productoTalla__isnull=True).first()
        self.assertEqual(int(linea_cant.precio), 10000)   # neto
        self.assertEqual(int(linea_monto.precio), 5000)   # neto (5950/1.19)
        # Cabecera: neto 15.000 / con IVA 11.900+5.950=17.850
        self.assertEqual(int(nc.monto_neto), 15000)
        self.assertEqual(int(nc.monto_con_iva), 17850)

    def test_boleta_papel_referencia_tipo_35(self):
        """La NC que devuelve una BOLETA PAPEL referencia TpoDocRef=35 (no 39,
        que declararía un folio electrónico inexistente)."""
        import json
        boleta = _crear_documento(self.env, 5023, [(self.pt, 1, 11900)],
                                  tipo_documento='BOLETA PAPEL')
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                              'modo': 'CANTIDAD', 'cantidad': 1}])
        _dev, nc, _txt, _warns = service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user,
            metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy,
        )
        self.assertEqual(json.loads(nc.referencias)[0]['tipo_documento'], 35)

    # ---------- impacto en cuadratura ----------

    def test_no_afecta_caja_es_anulacion_y_no_resta_cuadratura(self):
        boleta = _crear_documento(self.env, 5007, [(self.pt, 2, 11900)])
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                              'modo': 'CANTIDAD', 'cantidad': 1}])
        _dev, nc, _txt, _warns = service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user, metodo_devolucion='NO_AFECTA_CAJA',
        )
        self.assertEqual(nc.tipo_transaccion, 'ANULACION')
        self.assertFalse(nc.dte_asociado.exists())  # sin detalle de pago

        c = _calcular_cuadratura_data(self.sucursal, self.hoy_str)
        self.assertEqual(int(c['total_nc_efectivo']), 0)
        self.assertEqual(int(c['total_notas_credito']), 0)
        self.assertEqual(c['cantidad_notas_credito'], 1)  # informativa: cuenta el doc

    def test_efectivo_resta_en_fecha_imputada(self):
        boleta = _crear_documento(self.env, 5008, [(self.pt, 2, 11900)])
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                              'modo': 'CANTIDAD', 'cantidad': 1}])
        service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user,
            metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy,
        )
        c = _calcular_cuadratura_data(self.sucursal, self.hoy_str)
        self.assertEqual(int(c['total_nc_efectivo']), 11900)

        manana = (self.hoy + timedelta(days=1)).strftime('%Y-%m-%d')
        c2 = _calcular_cuadratura_data(self.sucursal, manana)
        self.assertEqual(int(c2['total_nc_efectivo']), 0)

    # ---------- guards ----------

    def test_guard_sobre_acreditacion_por_linea_con_nc_otra_via(self):
        boleta = _crear_documento(self.env, 5009, [(self.pt, 2, 11900)])
        dp = boleta.dte_productos.first()
        _nc_externa_por_talla(self.env, 9009, boleta, self.pt, 2, 11900)  # consume las 2
        with self.assertRaises(service.DevolucionGarantiaError):
            self._crear_solicitud(boleta, [{'dte_producto_id': dp.id, 'modo': 'CANTIDAD', 'cantidad': 1}])

    def test_guard_saldo_documento_con_nc_conceptual_previa(self):
        boleta = _crear_documento(self.env, 5010, [(self.pt, 1, 23800)])
        dp = boleta.dte_productos.first()
        # NC conceptual previa por 20000: no baja la disponibilidad por talla,
        # pero sí el saldo del documento (restante 3800).
        _nc_externa_conceptual(self.env, 9010, boleta, 20000)
        with self.assertRaises(service.DevolucionGarantiaError):
            self._crear_solicitud(boleta, [{'dte_producto_id': dp.id, 'modo': 'CANTIDAD', 'cantidad': 1}])

    def test_reserva_pendiente_bloquea_segunda_solicitud(self):
        boleta = _crear_documento(self.env, 5011, [(self.pt, 2, 11900)])
        dp = boleta.dte_productos.first()
        self._crear_solicitud(boleta, [{'dte_producto_id': dp.id, 'modo': 'CANTIDAD', 'cantidad': 1}])
        # Ya hay 1 unidad reservada pendiente → solo queda 1 disponible.
        with self.assertRaises(service.DevolucionGarantiaError):
            self._crear_solicitud(boleta, [{'dte_producto_id': dp.id, 'modo': 'CANTIDAD', 'cantidad': 2}])

    def test_aprobar_falla_si_disponibilidad_cambio(self):
        boleta = _crear_documento(self.env, 5012, [(self.pt, 1, 11900)])
        dp = boleta.dte_productos.first()
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': dp.id, 'modo': 'CANTIDAD', 'cantidad': 1}])
        # Antes de aprobar, una NC externa consume la unidad.
        _nc_externa_por_talla(self.env, 9012, boleta, self.pt, 1, 11900)
        with self.assertRaises(service.DevolucionGarantiaError):
            service.aprobar_devolucion(
                devolucion_id=dev.id, aprobador=self.user,
                metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy,
            )
        dev.refresh_from_db()
        self.assertEqual(dev.estado, 'PENDIENTE')  # sigue pendiente

    # ---------- rechazo / anulación ----------

    def test_rechazo_exige_motivo_y_setea_auditoria(self):
        boleta = _crear_documento(self.env, 5013, [(self.pt, 1, 11900)])
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                              'modo': 'CANTIDAD', 'cantidad': 1}])
        with self.assertRaises(service.DevolucionGarantiaError):
            service.rechazar_devolucion(devolucion_id=dev.id, aprobador=self.user, motivo_rechazo='  ')
        dev = service.rechazar_devolucion(devolucion_id=dev.id, aprobador=self.user,
                                          motivo_rechazo='No corresponde garantía')
        self.assertEqual(dev.estado, 'RECHAZADA')
        self.assertEqual(dev.motivo_rechazo, 'No corresponde garantía')
        self.assertIsNotNone(dev.fecha_rechazo)

    def test_anular_solo_solicitante_o_admin(self):
        boleta = _crear_documento(self.env, 5014, [(self.pt, 1, 11900)])
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                              'modo': 'CANTIDAD', 'cantidad': 1}])
        otro = crear_usuario(username='otro_vend', rol='vendedor')
        with self.assertRaises(service.DevolucionGarantiaError):
            service.anular_solicitud(devolucion_id=dev.id, usuario=otro)
        dev = service.anular_solicitud(devolucion_id=dev.id, usuario=self.user)  # solicitante
        self.assertEqual(dev.estado, 'ANULADA')

    def test_estado_final_no_reaprobable(self):
        boleta = _crear_documento(self.env, 5015, [(self.pt, 1, 11900)])
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                              'modo': 'CANTIDAD', 'cantidad': 1}])
        service.aprobar_devolucion(devolucion_id=dev.id, aprobador=self.user,
                                   metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy)
        with self.assertRaises(service.DevolucionGarantiaError):
            service.aprobar_devolucion(devolucion_id=dev.id, aprobador=self.user,
                                       metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy)

    # ---------- método pedido por el cliente / transferencia ----------

    def test_transferencia_exige_datos_bancarios(self):
        boleta = _crear_documento(self.env, 5016, [(self.pt, 1, 11900)])
        with self.assertRaises(service.DevolucionGarantiaError):
            service.crear_solicitud_devolucion(
                dte_original=boleta, sucursal=self.sucursal, receptor=_receptor(self.env),
                motivo='Garantía', usuario=self.user,
                detalles=[{'dte_producto_id': boleta.dte_productos.first().id, 'modo': 'CANTIDAD', 'cantidad': 1}],
                metodo_solicitado='TRANSFERENCIA_BANCARIA',  # sin banco/cuenta/titular
            )

    def test_transferencia_guarda_datos(self):
        boleta = _crear_documento(self.env, 5017, [(self.pt, 1, 11900)])
        dev = service.crear_solicitud_devolucion(
            dte_original=boleta, sucursal=self.sucursal, receptor=_receptor(self.env),
            motivo='Garantía', usuario=self.user,
            detalles=[{'dte_producto_id': boleta.dte_productos.first().id, 'modo': 'CANTIDAD', 'cantidad': 1}],
            metodo_solicitado='TRANSFERENCIA_BANCARIA', banco='BancoEstado',
            tipo_cuenta='CORRIENTE', numero_cuenta='001234567', cuenta_titular_rut='11.111.111-1',
        )
        self.assertEqual(dev.metodo_solicitado, 'TRANSFERENCIA_BANCARIA')
        self.assertEqual(dev.banco, 'BancoEstado')
        self.assertEqual(dev.tipo_cuenta, 'CORRIENTE')
        self.assertEqual(dev.numero_cuenta, '001234567')
        self.assertEqual(dev.cuenta_titular_rut, '11.111.111-1')

    def test_efectivo_no_guarda_datos_bancarios(self):
        boleta = _crear_documento(self.env, 5018, [(self.pt, 1, 11900)])
        dev = service.crear_solicitud_devolucion(
            dte_original=boleta, sucursal=self.sucursal, receptor=_receptor(self.env),
            motivo='Garantía', usuario=self.user,
            detalles=[{'dte_producto_id': boleta.dte_productos.first().id, 'modo': 'CANTIDAD', 'cantidad': 1}],
            metodo_solicitado='EFECTIVO_CAJA', banco='Banco X', numero_cuenta='999',
        )
        self.assertEqual(dev.metodo_solicitado, 'EFECTIVO_CAJA')
        self.assertEqual(dev.banco, '')  # efectivo no persiste datos bancarios
        self.assertEqual(dev.numero_cuenta, '')

    # ---------- receptor ----------

    def test_rut_generico_bloqueado(self):
        Empresa.objects.create(nombre='Consumidor Final', rut='66666666-6',
                               razon_social='Consumidor Final', esProveedor=False)
        with self.assertRaises(service.DevolucionGarantiaError):
            service.resolver_o_crear_receptor(rut='66666666-6', nombre='Consumidor Final')

    def test_persona_natural_sin_giro_queda_particular(self):
        """El SII exige giro en la NC; para persona natural se completa solo."""
        emp = service.resolver_o_crear_receptor(rut='13013448-3', nombre='Paola Tebes')
        self.assertEqual(emp.giro, 'PARTICULAR')

    def test_persona_natural_con_giro_real_no_se_pisa(self):
        emp = service.resolver_o_crear_receptor(
            rut='13013448-3', nombre='Paola Tebes', giro='Servicios profesionales',
        )
        self.assertEqual(emp.giro, 'Servicios profesionales')
        # Al volver a resolver sin giro, se conserva el real (no lo pisa PARTICULAR).
        emp2 = service.resolver_o_crear_receptor(rut='13013448-3', nombre='Paola Tebes')
        self.assertEqual(emp2.giro, 'Servicios profesionales')

    def test_rut_empresa_sin_giro_sigue_fallando(self):
        with self.assertRaises(service.DevolucionGarantiaError):
            service.resolver_o_crear_receptor(rut='76000000-K', nombre='Empresa X SpA')


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DevolucionGarantiaEndpointCrearTest(TestCase):
    """Regresión: el wizard manda `folio_dte` como NÚMERO (viene de
    Dte.numero_documento, un IntegerField) y el endpoint reventaba con
    `'int' object has no attribute 'strip'`."""

    def setUp(self):
        from app.models import ModuloSistema, OpcionMenu, PermisoRol
        self.env = setup_entorno_completo()
        self.sucursal = self.env['sucursal']
        self.boleta = _crear_documento(self.env, 5200, [(self.env['producto_talla'], 2, 11900)])

        modulo = ModuloSistema.objects.create(codigo='ventas_test', nombre='Ventas', orden=1)
        opcion = OpcionMenu.objects.create(
            modulo=modulo, codigo='devolucion_garantia', nombre='Devolución Garantía',
            url_name='modulo_devolucion_garantia', orden=1,
        )
        PermisoRol.objects.create(
            rol='jefe_local', opcion_menu=opcion, puede_ver=True, puede_crear=True,
        )
        self.user = crear_usuario(username='jefe_crea', rol='jefe_local')
        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

    def test_folio_numerico_no_revienta(self):
        import json as _json
        payload = {
            'folio_dte': self.boleta.numero_documento,  # int, como lo manda el wizard
            'productos': [{'dte_producto_id': self.boleta.dte_productos.first().id,
                           'modo': 'CANTIDAD', 'cantidad': 1}],
            'rut': '13013448-3', 'nombre': 'Paola Tebes',
            'metodo_solicitado': 'EFECTIVO_CAJA',
            'motivo': 'Garantía',
        }
        resp = self.client.post(
            reverse('api_generar_devolucion_garantia'),
            data=_json.dumps(payload), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        dev = DevolucionGarantia.objects.get(id=resp.json()['data']['devolucion_id'])
        self.assertEqual(dev.estado, 'PENDIENTE')
        # Persona natural sin giro → se completa solo para que la NC no salga vacía.
        self.assertEqual(dev.receptor.giro, 'PARTICULAR')


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DevolucionGarantiaPermisoAprobadorTest(TestCase):
    """El endpoint de aprobación exige el permiso `devolucion_garantia.puede_aprobar`
    (ya no un rol fijo): un rol sin ese permiso recibe 403 aunque sea jefe_local,
    y cualquier rol al que se le otorgue el permiso puede aprobar."""

    def setUp(self):
        self.env = setup_entorno_completo()
        self.sucursal = self.env['sucursal']
        crear_correlativo(self.sucursal, tipo_dte='NOTA DE CREDITO')
        pt = self.env['producto_talla']
        boleta = _crear_documento(self.env, 5100, [(pt, 2, 11900)])
        self.dev = service.crear_solicitud_devolucion(
            dte_original=boleta, sucursal=self.sucursal, receptor=_receptor(self.env),
            motivo='Garantía', usuario=self.env['user'],
            detalles=[{'dte_producto_id': boleta.dte_productos.first().id,
                       'modo': 'CANTIDAD', 'cantidad': 1}],
        )
        self.url = reverse('api_aprobar_devolucion_garantia', args=[self.dev.id])
        # Opción de menú que gobierna el permiso de aprobar.
        modulo, _ = ModuloSistema.objects.get_or_create(
            codigo='ventas', defaults={'nombre': 'Ventas', 'orden': 2})
        self.opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='devolucion_garantia',
            defaults={'modulo': modulo, 'nombre': 'Devolucion por Garantia', 'orden': 3})

    def _login(self, rol):
        user = crear_usuario(username=f'user_{rol}', rol=rol)
        client = Client()
        client.force_login(user)
        session = client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()
        return client

    def _grant_aprobar(self, rol):
        PermisoRol.objects.update_or_create(
            rol=rol, opcion_menu=self.opcion,
            defaults={'puede_ver': True, 'puede_aprobar': True})

    def test_jefe_local_no_puede_aprobar(self):
        # Jefe de local SIN el permiso puede_aprobar -> 403 (solo puede crear).
        client = self._login('jefe_local')
        resp = client.post(self.url, data='{}', content_type='application/json',
                           HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 403)
        self.dev.refresh_from_db()
        self.assertEqual(self.dev.estado, 'PENDIENTE')

    def test_administrador_puede_aprobar(self):
        self._grant_aprobar('administrador')
        client = self._login('administrador')
        resp = client.post(
            self.url,
            data='{"metodo_devolucion": "EFECTIVO_CAJA", "fecha_imputacion": "%s"}'
                 % timezone.localdate().strftime('%Y-%m-%d'),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.dev.refresh_from_db()
        self.assertEqual(self.dev.estado, 'NC_GENERADA')
        self.assertIsNotNone(self.dev.nota_credito_id)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DevolucionGarantiaTicketTest(TestCase):
    """Payload del comprobante 80mm: de este contrato dependen el generador
    ESC/POS (QZ Tray, modo DEVOLUCION_GARANTIA) y el fallback HTML."""

    def setUp(self):
        self.env = setup_entorno_completo()
        self.sucursal = self.env['sucursal']
        pt = self.env['producto_talla']
        boleta = _crear_documento(self.env, 5200, [(pt, 2, 11900)])
        dp = boleta.dte_productos.first()
        self.dev = service.crear_solicitud_devolucion(
            dte_original=boleta, sucursal=self.sucursal, receptor=_receptor(self.env),
            motivo='Suela despegada', usuario=self.env['user'],
            detalles=[{'dte_producto_id': dp.id, 'modo': 'CANTIDAD', 'cantidad': 1}],
        )
        modulo, _ = ModuloSistema.objects.get_or_create(
            codigo='ventas', defaults={'nombre': 'Ventas', 'orden': 2})
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='devolucion_garantia',
            defaults={'modulo': modulo, 'nombre': 'Devolucion por Garantia', 'orden': 3})
        user = crear_usuario(username='user_ticket_dg', rol='jefe_local')
        PermisoRol.objects.update_or_create(
            rol=user.rol, opcion_menu=opcion, defaults={'puede_ver': True})
        self.client = Client()
        self.client.force_login(user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

    def test_payload_ticket_completo(self):
        url = reverse('api_ticket_devolucion_garantia', args=[self.dev.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['data']

        # El generador ESC/POS despacha por este campo.
        self.assertEqual(data['modulo_origen'], 'DEVOLUCION_GARANTIA')
        self.assertEqual(data['numero_operacion'], self.dev.numero_operacion)
        self.assertEqual(data['estado'], 'PENDIENTE')
        self.assertEqual(data['total'], int(self.dev.monto_total))
        self.assertIsNone(data['nota_credito'])  # aún sin aprobar
        self.assertTrue(data['sucursal']['empresa'])
        self.assertTrue(data['cliente']['nombre'])
        self.assertTrue(data['dte']['folio'])
        self.assertEqual(data['motivo'], 'Suela despegada')

        self.assertEqual(len(data['productos']), 1)
        linea = data['productos'][0]
        self.assertEqual(linea['modo'], 'CANTIDAD')
        self.assertEqual(linea['cantidad'], 1)
        self.assertEqual(linea['subtotal'], 11900)
        self.assertTrue(linea['sku'])

    def test_aislado_por_sucursal(self):
        """Una devolución de otra sucursal no se puede imprimir (anti-IDOR)."""
        otra = crear_sucursal(empresa=self.env['empresa'], alias='OTRA-SUC')
        session = self.client.session
        session['idSucursalActual'] = otra.id
        session.save()
        resp = self.client.get(reverse('api_ticket_devolucion_garantia', args=[self.dev.id]))
        self.assertEqual(resp.status_code, 404)
