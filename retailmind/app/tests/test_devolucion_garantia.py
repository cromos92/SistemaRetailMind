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
    Ticket, ConfiguracionPOS, TransaccionPOS,
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

    # ---------- aprobación: arqueo ya cerrado del día imputado ----------

    def _arqueo_cerrado_con_snapshot(self):
        """Arqueo CERRADO del día con los teóricos tal como quedaron al cerrar."""
        from app.models import ArqueoCaja
        c = _calcular_cuadratura_data(self.sucursal, self.hoy_str)
        arqueo = ArqueoCaja.objects.create(
            fecha_arqueo=self.hoy, sucursal=self.sucursal,
            usuario_responsable=self.user, estado='CERRADO',
        )
        # update() y no save(): save() recalcula el físico desde billetes.
        ArqueoCaja.objects.filter(pk=arqueo.pk).update(
            estado='CERRADO',
            total_efectivo_teorico=int(c['total_efectivo']),
            total_efectivo_fisico=int(c['total_efectivo']),
            total_notas_credito_teorico=int(c['total_notas_credito']),
            diferencia_efectivo=0,
        )
        arqueo.refresh_from_db()
        return arqueo

    def test_aprobar_efectivo_recalcula_teorico_de_arqueo_cerrado(self):
        """Caso NICK2 17-09 (NC #3654): la Cuadratura mostraba la NC pero el
        `Ef. Teórico` del arqueo cerrado quedaba congelado en el snapshot."""
        from app.models import ObservacionArqueo
        boleta = _crear_documento(self.env, 5010, [(self.pt, 1, 69990)])
        arqueo = self._arqueo_cerrado_con_snapshot()
        teorico_antes = int(arqueo.total_efectivo_teorico)
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                              'modo': 'CANTIDAD', 'cantidad': 1}])

        service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user,
            metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy,
        )

        arqueo.refresh_from_db()
        self.assertEqual(int(arqueo.total_efectivo_teorico), teorico_antes - 69990)
        self.assertEqual(int(arqueo.total_notas_credito_teorico), 69990)
        # El conteo de ese día ya incluía la plata: queda a la vista como sobrante.
        self.assertEqual(int(arqueo.diferencia_efectivo), 69990)
        self.assertEqual(arqueo.estado, 'CERRADO')
        self.assertTrue(ObservacionArqueo.objects.filter(
            arqueo=arqueo, tipo='SISTEMA', texto__contains='Devolución de dinero').exists())

    def test_aprobar_no_afecta_caja_no_toca_arqueo_cerrado(self):
        from app.models import ObservacionArqueo
        boleta = _crear_documento(self.env, 5011, [(self.pt, 1, 11900)])
        arqueo = self._arqueo_cerrado_con_snapshot()
        teorico_antes = int(arqueo.total_efectivo_teorico)
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                              'modo': 'CANTIDAD', 'cantidad': 1}])

        service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user,
            metodo_devolucion='NO_AFECTA_CAJA', fecha_imputacion=self.hoy,
        )

        arqueo.refresh_from_db()
        self.assertEqual(int(arqueo.total_efectivo_teorico), teorico_antes)
        self.assertFalse(ObservacionArqueo.objects.filter(arqueo=arqueo).exists())

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

    # ---------- venta a crédito ----------

    def _factura_credito(self, numero, cantidad=1, precio=10000, **kwargs):
        """Factura a crédito: sin pago registrado y con la cuenta por cobrar abierta."""
        f = _crear_documento(
            self.env, numero, [(self.pt, cantidad, precio)],
            tipo_documento='FACTURA ELECTRONICA', metodo_pago=None, **kwargs
        )
        Dte.objects.filter(id=f.id).update(estado_pago='PENDIENTE', diasCredito=30)
        f.refresh_from_db()
        return f

    def test_condicion_pago_detecta_credito_y_contado(self):
        contado = _crear_documento(self.env, 5101, [(self.pt, 1, 11900)])
        self.assertFalse(service.condicion_pago_dte(contado)['es_credito'])

        credito = self._factura_credito(5102)
        cond = service.condicion_pago_dte(credito)
        self.assertTrue(cond['es_credito'])
        self.assertTrue(cond['cobro_abierto'])
        self.assertEqual(cond['estado_pago'], 'PENDIENTE')
        self.assertEqual(cond['dias_credito'], 30)

    def test_credito_bloquea_efectivo_de_caja(self):
        f = self._factura_credito(5103)
        dev = self._crear_solicitud(f, [{'dte_producto_id': f.dte_productos.first().id,
                                         'modo': 'CANTIDAD', 'cantidad': 1}])
        with self.assertRaises(service.DevolucionGarantiaError) as ctx:
            service.aprobar_devolucion(
                devolucion_id=dev.id, aprobador=self.user,
                metodo_devolucion='EFECTIVO_CAJA', fecha_imputacion=self.hoy,
            )
        self.assertIn('CRÉDITO', str(ctx.exception))
        dev.refresh_from_db()
        self.assertEqual(dev.estado, 'PENDIENTE')  # queda viva, no se quema

    def test_rebaja_credito_no_toca_efectivo_ni_transferencia(self):
        f = self._factura_credito(5104, cantidad=1, precio=10000)
        dev = self._crear_solicitud(f, [{'dte_producto_id': f.dte_productos.first().id,
                                         'modo': 'CANTIDAD', 'cantidad': 1}])
        _dev, nc, _txt, _w = service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user,
            metodo_devolucion='REBAJA_CREDITO', fecha_imputacion=self.hoy,
        )
        # Es DEVOLUCION (cuenta en la cuadratura), no ANULACION (informativa).
        self.assertEqual(nc.tipo_transaccion, 'DEVOLUCION')
        pago = nc.dte_asociado.get()
        self.assertEqual(pago.metodo_pago, 'CREDITO_EXTERNO')
        self.assertEqual(pago.fecha_pago, self.hoy)

        c = _calcular_cuadratura_data(self.sucursal, self.hoy_str)
        monto = int(nc.monto_con_iva)
        self.assertEqual(int(c['total_nc_credito']), monto)
        self.assertEqual(int(c['total_nc_efectivo']), 0)
        self.assertEqual(int(c['total_nc_transferencia']), 0)
        # Sí aparece como NC del día y rebaja la cuenta por cobrar.
        self.assertEqual(int(c['total_notas_credito']), monto)
        self.assertEqual(int(c['total_credito_externo']), -monto)

    def test_rebaja_credito_se_imputa_a_la_fecha_elegida(self):
        f = self._factura_credito(5105)
        dev = self._crear_solicitud(f, [{'dte_producto_id': f.dte_productos.first().id,
                                         'modo': 'CANTIDAD', 'cantidad': 1}])
        service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user,
            metodo_devolucion='REBAJA_CREDITO', fecha_imputacion=self.hoy,
        )
        manana = (self.hoy + timedelta(days=1)).strftime('%Y-%m-%d')
        self.assertEqual(int(_calcular_cuadratura_data(self.sucursal, manana)['total_nc_credito']), 0)

    def test_preview_bloquea_efectivo_y_sugiere_rebaja_credito(self):
        f = self._factura_credito(5106)
        dev = self._crear_solicitud(f, [{'dte_producto_id': f.dte_productos.first().id,
                                         'modo': 'CANTIDAD', 'cantidad': 1}])
        pv = service.impacto_caja_preview(devolucion=dev, metodo='EFECTIVO_CAJA',
                                          fecha_imputacion=self.hoy)
        self.assertTrue(pv['bloqueado'])
        self.assertEqual(pv['metodo_sugerido'], 'REBAJA_CREDITO')

        ok = service.impacto_caja_preview(devolucion=dev, metodo='REBAJA_CREDITO',
                                          fecha_imputacion=self.hoy)
        self.assertFalse(ok['bloqueado'])
        self.assertTrue(ok['afecta_caja'])

    def test_contado_sigue_permitiendo_efectivo(self):
        """El guard es solo para ventas a crédito: no rompe el flujo normal."""
        boleta = _crear_documento(self.env, 5107, [(self.pt, 1, 11900)])
        dev = self._crear_solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                              'modo': 'CANTIDAD', 'cantidad': 1}])
        pv = service.impacto_caja_preview(devolucion=dev, metodo='EFECTIVO_CAJA',
                                          fecha_imputacion=self.hoy)
        self.assertFalse(pv['bloqueado'])
        self.assertEqual(pv['metodo_sugerido'], 'TRANSFERENCIA_BANCARIA')

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


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DevolucionGarantiaFiltroSucursalTest(TestCase):
    """El listado va acotado a la sucursal activa; el administrador puede
    pedir otra o todas (`?sucursal=`). Nadie más."""

    def setUp(self):
        self.env = setup_entorno_completo()
        self.suc_a = self.env['sucursal']
        self.suc_b = crear_sucursal(empresa=self.env['empresa'], alias='SUC-B')
        pt = self.env['producto_talla']

        def _solicitud(numero, sucursal):
            boleta = _crear_documento(self.env, numero, [(pt, 2, 11900)])
            Dte.objects.filter(id=boleta.id).update(sucursal=sucursal)
            boleta.refresh_from_db()
            return service.crear_solicitud_devolucion(
                dte_original=boleta, sucursal=sucursal, receptor=_receptor(self.env),
                motivo='Garantía', usuario=self.env['user'],
                detalles=[{'dte_producto_id': boleta.dte_productos.first().id,
                           'modo': 'CANTIDAD', 'cantidad': 1}],
            )

        self.dev_a = _solicitud(5300, self.suc_a)
        self.dev_b = _solicitud(5301, self.suc_b)

        modulo, _ = ModuloSistema.objects.get_or_create(
            codigo='ventas', defaults={'nombre': 'Ventas', 'orden': 2})
        self.opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='devolucion_garantia',
            defaults={'modulo': modulo, 'nombre': 'Devolucion por Garantia', 'orden': 3})
        self.url = reverse('api_listar_devoluciones_garantia')

    def _cliente(self, rol):
        user = crear_usuario(username=f'user_filtro_{rol}', rol=rol)
        PermisoRol.objects.update_or_create(
            rol=user.rol, opcion_menu=self.opcion, defaults={'puede_ver': True})
        client = Client()
        client.force_login(user)
        session = client.session
        session['idSucursalActual'] = self.suc_a.id
        session.save()
        return client

    def test_no_admin_solo_ve_su_sucursal_aunque_pida_todas(self):
        client = self._cliente('jefe_local')
        for params in ({}, {'sucursal': 'TODAS'}, {'sucursal': self.suc_b.id}):
            data = client.get(self.url, params).json()
            self.assertEqual(data['total'], 1, params)
            self.assertEqual(data['data'][0]['numero_operacion'], self.dev_a.numero_operacion)

    def test_admin_ve_todas_cuando_lo_pide(self):
        client = self._cliente('administrador')
        # Default: sigue acotado a la sucursal activa.
        self.assertEqual(client.get(self.url).json()['total'], 1)
        # Explícito: todas.
        todas = client.get(self.url, {'sucursal': 'TODAS'}).json()
        self.assertEqual(todas['total'], 2)
        self.assertEqual(todas['pendientes'], 2)  # el KPI sigue el mismo ámbito
        self.assertEqual(
            {d['sucursal'] for d in todas['data']}, {self.suc_a.alias, self.suc_b.alias})
        # Una sucursal puntual.
        una = client.get(self.url, {'sucursal': self.suc_b.id}).json()
        self.assertEqual(una['total'], 1)
        self.assertEqual(una['data'][0]['numero_operacion'], self.dev_b.numero_operacion)

    def test_pagina_renderiza_y_el_selector_es_solo_para_admin(self):
        """Smoke del template + el filtro de sucursal solo para administrador."""
        url = reverse('modulo_devolucion_garantia')

        admin = self._cliente('administrador')
        resp = admin.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('id="filtro-sucursal"', html)
        self.assertIn('Todas las sucursales', html)
        self.assertIn(self.suc_b.alias, html)

        jefe = self._cliente('jefe_local')
        self.assertNotIn('id="filtro-sucursal"', jefe.get(url).content.decode('utf-8'))

    def test_modal_aprobacion_ofrece_rebaja_credito_efectivo_y_mercado_pago(self):
        """Con permiso de aprobar: transferencia, rebaja de crédito, y desde el
        28-09-2026 también efectivo (ya no oculto) y Mercado Pago."""
        user = crear_usuario(username='user_aprob_metodos', rol='administrador')
        PermisoRol.objects.update_or_create(
            rol=user.rol, opcion_menu=self.opcion,
            defaults={'puede_ver': True, 'puede_aprobar': True})
        client = Client()
        client.force_login(user)
        session = client.session
        session['idSucursalActual'] = self.suc_a.id
        session.save()

        html = client.get(reverse('modulo_devolucion_garantia')).content.decode('utf-8')
        self.assertIn('<option value="TRANSFERENCIA_BANCARIA">', html)
        self.assertIn('<option value="REBAJA_CREDITO">', html)
        self.assertIn('<option value="EFECTIVO_CAJA">', html)
        self.assertNotIn('<option value="EFECTIVO_CAJA" hidden>', html)
        self.assertIn('<option value="MERCADO_PAGO">', html)

    def test_admin_puede_abrir_devolucion_de_otra_sucursal(self):
        """Sin esto el filtro "todas" sería un callejón: fila visible, 404 al abrir."""
        client = self._cliente('administrador')
        resp = client.get(reverse('api_ticket_devolucion_garantia', args=[self.dev_b.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['data']['numero_operacion'], self.dev_b.numero_operacion)


class DevolucionGarantiaTransbankTest(TestCase):
    """Cobros con tarjeta por la máquina Transbank.

    Un error de cobro con tarjeta se corrige ANULANDO en la misma máquina (el
    dinero vuelve a la tarjeta), no con una devolución de dinero por este
    módulo. Se advierte siempre que hubo tarjeta, y se bloquea (crear, aprobar
    y preview) cuando la anulación por máquina ya está registrada y cubre todo
    lo pagado con tarjeta: encima de ella la NC pagaría dos veces.
    """

    def setUp(self):
        self.env = setup_entorno_completo()
        self.user = self.env['user']
        self.sucursal = self.env['sucursal']
        self.pt = self.env['producto_talla']
        crear_correlativo(self.sucursal, tipo_dte='NOTA DE CREDITO')
        self.hoy = timezone.localdate()

    def _boleta_tarjeta(self, numero, metodo='TBK_CREDITO_POS'):
        return _crear_documento(self.env, numero, [(self.pt, 2, 11900)], metodo_pago=metodo)

    def _anulacion_pos(self, dte, monto, tipo_dte_ticket='BOLETA_ELECTRONICA'):
        """Registra la venta POS (Ticket con folio_dte) y una ANULACION
        APROBADA por la máquina, como la deja anular_transaccion_pos."""
        ticket = Ticket.objects.create(
            vendedor=self.env['vendedor'], sucursal=self.sucursal,
            correlativo=dte.numero_documento, estado='PAGADO',
            subTotal=int(dte.monto_con_iva), total=int(dte.monto_con_iva),
            responsable=self.user.username, tipo_dte=tipo_dte_ticket,
            folio_dte=dte.numero_documento, dte_generado=True,
        )
        cfg, _ = ConfiguracionPOS.objects.get_or_create(
            sucursal=self.sucursal, nombre='POS TEST',
            defaults={'tipo_pos': 'INGENICO_3500', 'puerto_conexion': 'COM1'},
        )
        return TransaccionPOS.objects.create(
            configuracion_pos=cfg, ticket=ticket,
            ticket_pos=f'TPOS-ANU-{dte.numero_documento}-{monto}',
            monto=monto, tipo_transaccion='ANULACION', estado='APROBADA',
        )

    def _solicitud(self, dte):
        return service.crear_solicitud_devolucion(
            dte_original=dte, sucursal=self.sucursal, receptor=_receptor(self.env),
            motivo='Garantía', usuario=self.user,
            detalles=[{'dte_producto_id': dte.dte_productos.first().id,
                       'modo': 'CANTIDAD', 'cantidad': 1}],
        )

    def test_deteccion_pago_tarjeta_vs_efectivo(self):
        tarjeta = self._boleta_tarjeta(5400)
        efectivo = _crear_documento(self.env, 5401, [(self.pt, 1, 11900)], metodo_pago='EFECTIVO')

        tbk = service.pago_transbank_dte(tarjeta)
        self.assertTrue(tbk['es_transbank'])
        self.assertEqual(tbk['monto_tarjeta'], 23800)
        self.assertFalse(tbk['anulado_completo'])

        self.assertFalse(service.pago_transbank_dte(efectivo)['es_transbank'])

    def test_tarjeta_sin_anulacion_solo_advierte(self):
        boleta = self._boleta_tarjeta(5402)
        dev = self._solicitud(boleta)  # crear NO se bloquea
        preview = service.impacto_caja_preview(
            devolucion=dev, metodo='TRANSFERENCIA_BANCARIA', fecha_imputacion=self.hoy)
        self.assertFalse(preview['bloqueado'])
        self.assertTrue(preview['pago_transbank']['es_transbank'])
        self.assertTrue(any('Transbank' in a for a in preview['advertencias']))

    def test_anulacion_maquina_bloquea_crear(self):
        boleta = self._boleta_tarjeta(5403)
        self._anulacion_pos(boleta, 23800)
        with self.assertRaisesMessage(service.DevolucionGarantiaError,
                                      'ANULADO por la máquina Transbank'):
            self._solicitud(boleta)

    def test_anulacion_maquina_bloquea_aprobar_y_preview(self):
        boleta = self._boleta_tarjeta(5404)
        dev = self._solicitud(boleta)          # la anulación llega DESPUÉS
        self._anulacion_pos(boleta, 23800)

        preview = service.impacto_caja_preview(
            devolucion=dev, metodo='TRANSFERENCIA_BANCARIA', fecha_imputacion=self.hoy)
        self.assertTrue(preview['bloqueado'])
        self.assertTrue(preview['pago_transbank']['anulado_completo'])

        with self.assertRaisesMessage(service.DevolucionGarantiaError,
                                      'ANULADO por la máquina Transbank'):
            service.aprobar_devolucion(
                devolucion_id=dev.id, aprobador=self.user,
                metodo_devolucion='TRANSFERENCIA_BANCARIA', fecha_imputacion=self.hoy)
        dev.refresh_from_db()
        self.assertEqual(dev.estado, 'PENDIENTE')  # sin NC, sigue pendiente
        self.assertIsNone(dev.nota_credito_id)

    def test_anulacion_parcial_no_bloquea(self):
        boleta = self._boleta_tarjeta(5405)
        self._anulacion_pos(boleta, 10000)  # < monto pagado con tarjeta
        dev = self._solicitud(boleta)
        preview = service.impacto_caja_preview(
            devolucion=dev, metodo='TRANSFERENCIA_BANCARIA', fecha_imputacion=self.hoy)
        self.assertFalse(preview['bloqueado'])
        self.assertTrue(any('PARCIAL' in a for a in preview['advertencias']))

    def test_ticket_de_boleta_con_tipo_ticket_tambien_se_encuentra(self):
        """El POS deja el ticket de una boleta electrónica con tipo_dte='TICKET'
        (visto en prod, PAO1 28-09-2026): la anulación por máquina de ese
        ticket también tiene que bloquear."""
        boleta = self._boleta_tarjeta(5408)
        self._anulacion_pos(boleta, 23800, tipo_dte_ticket='TICKET')
        self.assertTrue(service.pago_transbank_dte(boleta)['anulado_completo'])
        with self.assertRaisesMessage(service.DevolucionGarantiaError,
                                      'ANULADO por la máquina Transbank'):
            self._solicitud(boleta)

    def test_anulacion_de_otro_folio_no_afecta(self):
        """El vínculo es por folio+sucursal+tipo: la anulación de OTRA venta
        no contamina este documento."""
        boleta = self._boleta_tarjeta(5406)
        otra = self._boleta_tarjeta(5407)
        self._anulacion_pos(otra, 23800)
        self.assertEqual(service.pago_transbank_dte(boleta)['monto_anulado_pos'], 0)
        self._solicitud(boleta)  # no se bloquea


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DevolucionDineroDirectaTest(TestCase):
    """Devolución DIRECTA (25-09-2026): quien crea la devolución la firma en el
    momento con el código de la barra superior de un Administrador o Maestro;
    se crea y se aprueba en una sola transacción (NC imputada hoy)."""

    def setUp(self):
        self.env = setup_entorno_completo()
        self.sucursal = self.env['sucursal']
        crear_correlativo(self.sucursal, tipo_dte='NOTA DE CREDITO')
        self.boleta = _crear_documento(self.env, 5300, [(self.env['producto_talla'], 2, 11900)])

        modulo, _ = ModuloSistema.objects.get_or_create(
            codigo='ventas', defaults={'nombre': 'Ventas', 'orden': 2})
        self.opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='devolucion_garantia',
            defaults={'modulo': modulo, 'nombre': 'Devolucion de Dinero', 'orden': 3})
        PermisoRol.objects.update_or_create(
            rol='jefe_local', opcion_menu=self.opcion,
            defaults={'puede_ver': True, 'puede_crear': True})
        PermisoRol.objects.update_or_create(
            rol='administrador', opcion_menu=self.opcion,
            defaults={'puede_ver': True, 'puede_crear': True, 'puede_aprobar': True})

        self.jefe = crear_usuario(username='jefe_directa', rol='jefe_local')
        self.admin = crear_usuario(username='admin_directa', rol='administrador')
        self.client = Client()
        self.client.force_login(self.jefe)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()
        self._seq = 0

    def _codigo(self, usuario):
        from app.models import CodigoAutorizacionDinamico
        self._seq += 1
        return CodigoAutorizacionDinamico.objects.create(
            codigo=f'{731000 + self._seq:06d}',
            fecha_hora_inicio=timezone.now() - timedelta(minutes=1),
            fecha_hora_fin=timezone.now() + timedelta(minutes=30),
            generado_por=usuario,
        )

    def _post(self, codigo, cantidad=1, **extra):
        import json as _json
        payload = {
            'folio_dte': self.boleta.numero_documento,
            'productos': [{'dte_producto_id': self.boleta.dte_productos.first().id,
                           'modo': 'CANTIDAD', 'cantidad': cantidad}],
            'rut': '13013448-3', 'nombre': 'Cliente Directo',
            'metodo_solicitado': 'TRANSFERENCIA_BANCARIA',
            'banco': 'Banco Estado', 'tipo_cuenta': 'VISTA', 'numero_cuenta': '123456',
            'cuenta_titular_rut': '13013448-3',
            'motivo': 'Producto fallado',
            'directa': True,
            'codigo_autorizacion': codigo,
        }
        payload.update(extra)
        return self.client.post(
            reverse('api_generar_devolucion_garantia'),
            data=_json.dumps(payload), content_type='application/json',
        )

    def test_directa_con_codigo_de_administrador_genera_la_nc(self):
        from app.models import RegistroAutorizacion
        codigo = self._codigo(self.admin)

        resp = self._post(codigo.codigo)

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertTrue(data['directa'])
        dev = DevolucionGarantia.objects.get(id=data['devolucion_id'])
        self.assertEqual(dev.estado, 'NC_GENERADA')
        self.assertEqual(dev.solicitado_por_id, self.jefe.id)
        self.assertEqual(dev.autorizado_por_id, self.admin.id)
        self.assertEqual(dev.metodo_devolucion, 'TRANSFERENCIA_BANCARIA')
        self.assertEqual(dev.fecha_imputacion_caja, timezone.localdate())
        self.assertTrue(dev.observaciones_aprobacion.startswith('[DIRECTA]'))
        self.assertEqual(data['nc_numero'], dev.nota_credito.numero_documento)
        pago = dev.nota_credito.dte_asociado.get()
        self.assertEqual(pago.metodo_pago, 'TRANSFERENCIA')
        codigo.refresh_from_db()
        self.assertTrue(codigo.usado)
        registro = RegistroAutorizacion.objects.get(exitoso=True)
        self.assertEqual(registro.usuario_autorizador_id, self.admin.id)
        self.assertEqual(registro.codigo_usado_id, codigo.id)
        self.assertEqual(registro.datos_adicionales['operacion'], 'DEVOLUCION_DINERO_DIRECTA')

        listado = self.client.get(reverse('api_listar_devoluciones_garantia')).json()
        self.assertTrue(listado['data'][0]['directa'])

    def test_directa_con_codigo_de_maestro(self):
        maestro = crear_usuario(username='maestro_directa', rol='maestro')

        resp = self._post(self._codigo(maestro).codigo)

        self.assertEqual(resp.status_code, 200, resp.content)
        dev = DevolucionGarantia.objects.get(id=resp.json()['data']['devolucion_id'])
        self.assertEqual(dev.autorizado_por_id, maestro.id)

    def test_directa_rechaza_codigo_de_jefe_de_local(self):
        codigo = self._codigo(self.jefe)

        resp = self._post(codigo.codigo)

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['code'], 'INVALID_AUTH_CODE')
        self.assertFalse(DevolucionGarantia.objects.exists())
        codigo.refresh_from_db()
        self.assertFalse(codigo.usado)

    def test_directa_rechaza_admin_sin_permiso_de_aprobar(self):
        PermisoRol.objects.filter(rol='administrador', opcion_menu=self.opcion).update(puede_aprobar=False)
        codigo = self._codigo(self.admin)

        resp = self._post(codigo.codigo)

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['code'], 'AUTHORIZER_CANNOT_APPROVE')
        self.assertFalse(DevolucionGarantia.objects.exists())
        codigo.refresh_from_db()
        self.assertFalse(codigo.usado)

    def test_directa_codigo_inexistente_no_crea_nada(self):
        resp = self._post('000000')

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(DevolucionGarantia.objects.exists())

    def test_directa_fallida_no_deja_solicitud_ni_quema_el_codigo(self):
        codigo = self._codigo(self.admin)

        resp = self._post(codigo.codigo, cantidad=5)  # se vendieron 2

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(DevolucionGarantia.objects.exists())
        codigo.refresh_from_db()
        self.assertFalse(codigo.usado)

    def test_directa_rechaza_metodo_no_permitido(self):
        codigo = self._codigo(self.admin)

        resp = self._post(codigo.codigo, metodo_solicitado='NO_AFECTA_CAJA')

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(DevolucionGarantia.objects.exists())

    # ----- Efectivo de caja (solo en la directa, 28-09-2026) -----

    def test_directa_en_efectivo_resta_del_efectivo_de_hoy(self):
        codigo = self._codigo(self.admin)

        resp = self._post(codigo.codigo, metodo_solicitado='EFECTIVO_CAJA')

        self.assertEqual(resp.status_code, 200, resp.content)
        dev = DevolucionGarantia.objects.get(id=resp.json()['data']['devolucion_id'])
        self.assertEqual(dev.estado, 'NC_GENERADA')
        self.assertEqual(dev.metodo_devolucion, 'EFECTIVO_CAJA')
        self.assertEqual(dev.metodo_solicitado, 'EFECTIVO_CAJA')
        self.assertEqual(dev.banco, '')  # efectivo no guarda datos bancarios
        pago = dev.nota_credito.dte_asociado.get()
        self.assertEqual(pago.metodo_pago, 'EFECTIVO')
        self.assertEqual(pago.fecha_pago, timezone.localdate())
        cuadratura = _calcular_cuadratura_data(
            self.sucursal, timezone.localdate().strftime('%Y-%m-%d'))
        self.assertEqual(cuadratura['total_nc_efectivo'], int(dev.monto_total))

    def test_directa_en_efectivo_bloqueada_si_la_venta_fue_a_credito(self):
        credito = _crear_documento(
            self.env, 5301, [(self.env['producto_talla'], 1, 11900)], metodo_pago='CREDITO_EXTERNO')
        codigo = self._codigo(self.admin)

        resp = self._post(
            codigo.codigo, metodo_solicitado='EFECTIVO_CAJA', folio_dte=credito.numero_documento,
            productos=[{'dte_producto_id': credito.dte_productos.first().id,
                        'modo': 'CANTIDAD', 'cantidad': 1}],
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn('crédito', resp.json()['error'])
        self.assertFalse(DevolucionGarantia.objects.exists())
        codigo.refresh_from_db()
        self.assertFalse(codigo.usado)

    def test_directa_en_efectivo_bloqueada_si_el_arqueo_de_hoy_esta_cerrado(self):
        from app.models import ArqueoCaja
        arqueo = ArqueoCaja.objects.create(
            fecha_arqueo=timezone.localdate(), sucursal=self.sucursal,
            usuario_responsable=self.admin, estado='CERRADO',
        )
        ArqueoCaja.objects.filter(pk=arqueo.pk).update(estado='CERRADO')
        codigo = self._codigo(self.admin)

        resp = self._post(codigo.codigo, metodo_solicitado='EFECTIVO_CAJA')

        self.assertEqual(resp.status_code, 400)
        self.assertIn('arqueo', resp.json()['error'])
        self.assertFalse(DevolucionGarantia.objects.exists())
        codigo.refresh_from_db()
        self.assertFalse(codigo.usado)

    def test_directa_por_transferencia_con_arqueo_cerrado_solo_avisa(self):
        from app.models import ArqueoCaja
        arqueo = ArqueoCaja.objects.create(
            fecha_arqueo=timezone.localdate(), sucursal=self.sucursal,
            usuario_responsable=self.admin, estado='CERRADO',
        )
        ArqueoCaja.objects.filter(pk=arqueo.pk).update(estado='CERRADO')

        resp = self._post(self._codigo(self.admin).codigo)

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['data']['avisos_caja'])

    def test_directa_en_efectivo_bloqueada_si_parte_se_pago_a_credito(self):
        """Boleta mixta efectivo + crédito trabajador: `es_credito` no la marca,
        pero entregar el total en efectivo pagaría la parte financiada."""
        mixta = _crear_documento(
            self.env, 5302, [(self.env['producto_talla'], 1, 100000)], metodo_pago=None)
        Dte_Detalle_Pago.objects.create(dte=mixta, metodo_pago='EFECTIVO', monto=20000)
        Dte_Detalle_Pago.objects.create(dte=mixta, metodo_pago='CREDITO_TRABAJADOR', monto=80000)
        codigo = self._codigo(self.admin)

        resp = self._post(
            codigo.codigo, metodo_solicitado='EFECTIVO_CAJA', folio_dte=mixta.numero_documento,
            productos=[{'dte_producto_id': mixta.dte_productos.first().id,
                        'modo': 'CANTIDAD', 'cantidad': 1}],
        )

        self.assertEqual(resp.status_code, 400)
        self.assertIn('crédito', resp.json()['error'])
        self.assertFalse(DevolucionGarantia.objects.exists())
        codigo.refresh_from_db()
        self.assertFalse(codigo.usado)

    def test_directa_bloqueada_con_anulacion_parcial_transbank(self):
        from unittest import mock
        parcial = {
            'es_transbank': True, 'monto_tarjeta': 23800, 'monto_anulado_pos': 11900,
            'anulado_completo': False, 'anulaciones_pos': [{'fecha': '28/09/2026', 'monto': 11900}],
        }
        codigo = self._codigo(self.admin)
        with mock.patch.object(service, 'pago_transbank_dte', return_value=parcial):
            resp = self._post(codigo.codigo)

        self.assertEqual(resp.status_code, 400)
        self.assertIn('PARCIAL', resp.json()['error'])
        self.assertFalse(DevolucionGarantia.objects.exists())
        codigo.refresh_from_db()
        self.assertFalse(codigo.usado)

    def test_fallos_de_otro_flujo_con_tipo_otro_no_bloquean_la_directa(self):
        """El permiso temporal de Cambios también registra sus fallos como OTRO:
        no deben contar para el freno de la devolución directa."""
        from app.models import RegistroAutorizacion
        for _ in range(5):
            RegistroAutorizacion.objects.create(
                usuario_solicitante=self.jefe, tipo_operacion='OTRO', exitoso=False,
                descripcion='Intento fallido permiso temporal de cambio',
            )

        resp = self._post(self._codigo(self.admin).codigo)

        self.assertEqual(resp.status_code, 200, resp.content)

    def test_directa_bloquea_tras_cinco_codigos_fallidos(self):
        for intento in ('900001', '900002', '900003', '900004', '900005'):
            self.assertEqual(self._post(intento).status_code, 403)

        resp = self._post(self._codigo(self.admin).codigo)

        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()['code'], 'AUTH_CODE_BLOCKED')

    def test_efectivo_y_mercado_pago_se_ofrecen_en_los_dos_flujos(self):
        """28-09-2026: efectivo ya no depende de «Devolver ahora» (solo de que
        la venta no sea a crédito) y existe el método Mercado Pago. Los radios
        nacen ocultos: los destapa la búsqueda del documento."""
        html = self.client.get(reverse('modulo_devolucion_garantia')).content.decode()
        self.assertIn('id="dg-metodo-efectivo" value="EFECTIVO_CAJA"', html)
        self.assertIn('id="dg-metodo-mp" value="MERCADO_PAGO"', html)
        self.assertNotIn('const permiteEfectivo = directa &&', html)

        # El modal de aprobación (solo con puede_aprobar) también los ofrece.
        self.client.force_login(self.admin)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()
        html = self.client.get(reverse('modulo_devolucion_garantia')).content.decode()
        self.assertIn('<option value="MERCADO_PAGO">', html)
        self.assertIn('<option value="EFECTIVO_CAJA">Efectivo de caja</option>', html)

    def test_directa_por_mercado_pago(self):
        """Venta cobrada con MP devuelta en el momento: la NC resta de MP POS y
        guarda el N° de operación."""
        boleta = _crear_documento(
            self.env, 5310, [(self.env['producto_talla'], 1, 11900)], metodo_pago=None)
        Dte_Detalle_Pago.objects.create(
            dte=boleta, metodo_pago='MP_POINT_DEBITO', tipo_tarjeta='debit_card', monto=11900)
        codigo = self._codigo(self.admin)

        resp = self._post(
            codigo.codigo, metodo_solicitado='MERCADO_PAGO', numero_operacion_mp='177000000555',
            folio_dte=boleta.numero_documento,
            productos=[{'dte_producto_id': boleta.dte_productos.first().id,
                        'modo': 'CANTIDAD', 'cantidad': 1}],
        )

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['numero_operacion_mp'], '177000000555')
        dev = DevolucionGarantia.objects.get(id=data['devolucion_id'])
        self.assertEqual(dev.metodo_devolucion, 'MERCADO_PAGO')
        pago = dev.nota_credito.dte_asociado.get()
        self.assertEqual(pago.metodo_pago, 'MP_POINT_DEBITO')
        self.assertEqual(pago.voucher, '177000000555')

    def test_directa_por_mercado_pago_sin_cobro_mp_no_crea_nada(self):
        codigo = self._codigo(self.admin)

        resp = self._post(codigo.codigo, metodo_solicitado='MERCADO_PAGO',
                          numero_operacion_mp='123')  # la boleta del setUp es EFECTIVO

        self.assertEqual(resp.status_code, 400)
        self.assertIn('Mercado Pago', resp.json()['error'])
        self.assertFalse(DevolucionGarantia.objects.exists())
        codigo.refresh_from_db()
        self.assertFalse(codigo.usado)

    def test_sin_directa_sigue_quedando_pendiente(self):
        resp = self._post('', directa=False)

        self.assertEqual(resp.status_code, 200, resp.content)
        dev = DevolucionGarantia.objects.get()
        self.assertEqual(dev.estado, 'PENDIENTE')
        self.assertIsNone(dev.nota_credito_id)

    def test_pagina_ofrece_devolver_ahora_con_codigo(self):
        resp = self.client.get(reverse('modulo_devolucion_garantia'))

        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('Devolución de Dinero', html)
        self.assertIn('id="dg-res-directa"', html)
        self.assertIn('id="dg-codigo-autorizacion"', html)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DevolucionDineroMercadoPagoTest(TestCase):
    """Devolución por MERCADO PAGO (28-09-2026, caso PAO1 DG-2-202609-0002 /
    NC 5155): la venta se cobró con la Point y la plata se devolvió desde
    Mercado Pago. La NC debe restar del bucket MP POS (no de efectivo ni de
    transferencias), llevar el N° de operación de MP y anotar la devolución en
    el libro de cobros MP."""

    NUMERO_MP = '177000000001'

    def setUp(self):
        from app.models import MercadoPagoConfig, TransaccionMercadoPago
        self.env = setup_entorno_completo()
        self.user = self.env['user']
        self.sucursal = self.env['sucursal']
        self.pt = self.env['producto_talla']
        crear_correlativo(self.sucursal, tipo_dte='NOTA DE CREDITO')
        self.hoy = timezone.localdate()
        self.hoy_str = self.hoy.strftime('%Y-%m-%d')
        # Boleta de 2 u × $11.900 cobrada con la Point (débito), como la del POS.
        self.boleta = _crear_documento(self.env, 6100, [(self.pt, 2, 11900)], metodo_pago=None)
        Dte_Detalle_Pago.objects.create(
            dte=self.boleta, metodo_pago='MP_POINT_DEBITO', tipo_tarjeta='debit_card',
            voucher='PAY01TESTULID0001', monto=23800, notas='MP Point Aut: -')
        self.config = MercadoPagoConfig.objects.create(
            sucursal=self.sucursal, habilitado=True, modo='POINT',
            token_env='MP_TOKEN_TEST', webhook_secret_env='MP_SECRET_TEST',
            external_pos_id='POS001', external_store_id='SUC001',
        )
        self.trx = TransaccionMercadoPago.objects.create(
            config=self.config, sucursal=self.sucursal, correlativo_ticket='12180',
            tipo='VENTA', canal='POINT', external_reference='RM-TEST-12180-c6i01',
            payment_id='PAY01TESTULID0001', payment_id_mp=self.NUMERO_MP,
            metodo_pago_mp='debit_card', monto=23800, estado='APROBADA', consumida=True,
        )

    def _solicitud(self, dte=None, cantidad=1, metodo='MERCADO_PAGO'):
        dte = dte or self.boleta
        transf = metodo == 'TRANSFERENCIA_BANCARIA'
        return service.crear_solicitud_devolucion(
            dte_original=dte, sucursal=self.sucursal, receptor=_receptor(self.env),
            motivo='Falla', usuario=self.user, metodo_solicitado=metodo,
            banco='Banco X' if transf else '',
            tipo_cuenta='VISTA' if transf else '',
            numero_cuenta='123' if transf else '',
            cuenta_titular_rut='11.111.111-1' if transf else '',
            detalles=[{'dte_producto_id': dte.dte_productos.first().id,
                       'modo': 'CANTIDAD', 'cantidad': cantidad}],
        )

    def _aprobar(self, dev, metodo='MERCADO_PAGO', numero=''):
        return service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user, metodo_devolucion=metodo,
            fecha_imputacion=self.hoy, numero_operacion_mp=numero,
        )

    def _cuadratura(self):
        return _calcular_cuadratura_data(self.sucursal, self.hoy_str)

    # ----- detección del cobro MP -----

    def test_detecta_el_cobro_mp_y_su_numero_de_operacion(self):
        mp = service.pago_mercadopago_dte(self.boleta)
        self.assertTrue(mp['es_mp'])
        self.assertEqual(mp['monto_mp'], 23800)
        self.assertEqual(mp['metodo_pago'], 'MP_POINT_DEBITO')
        self.assertEqual(mp['numero_operacion'], self.NUMERO_MP)
        self.assertEqual(mp['disponible'], 23800)
        self.assertEqual(service.metodo_devolucion_sugerido(self.boleta, mp), 'MERCADO_PAGO')

    def test_sin_numero_en_el_sistema_se_lo_pregunta_a_mercado_pago(self):
        """El POS guarda el ULID de Orders (PAY01…), que el panel de MP no
        encuentra; el número real se completa con payments/search."""
        from unittest import mock
        from app.services import mercadopago_service as mp_service
        self.trx.payment_id_mp = ''
        self.trx.save(update_fields=['payment_id_mp'])
        respuesta = mock.Mock(status_code=200)
        respuesta.json.return_value = {'results': [{
            'id': 188000000123, 'status': 'refunded',
            'external_reference': self.trx.external_reference,
            'authorization_code': '326087', 'card': {'last_four_digits': '1281'},
        }]}
        with mock.patch.object(mp_service, '_request', return_value=respuesta) as req:
            mp = service.pago_mercadopago_dte(self.boleta, consultar_api=True)

        self.assertEqual(mp['numero_operacion'], '188000000123')
        self.assertEqual(req.call_args.kwargs['params']['external_reference'],
                         self.trx.external_reference)
        self.trx.refresh_from_db()
        self.assertEqual(self.trx.payment_id_mp, '188000000123')
        self.assertEqual(self.trx.ultimos_4_digitos, '1281')

    def test_si_mercado_pago_no_responde_no_rompe(self):
        from unittest import mock
        from app.services import mercadopago_service as mp_service
        self.trx.payment_id_mp = ''
        self.trx.save(update_fields=['payment_id_mp'])
        with mock.patch.object(mp_service, '_request',
                               side_effect=mp_service.MercadoPagoError('sin red', red=True)):
            mp = service.pago_mercadopago_dte(self.boleta, consultar_api=True)
        self.assertTrue(mp['es_mp'])
        self.assertEqual(mp['numero_operacion'], '')

    # ----- aprobar por Mercado Pago -----

    def test_aprobar_por_mercado_pago_resta_de_mp_pos(self):
        dev = self._solicitud()
        dev, nc, _, _ = self._aprobar(dev)

        pago = nc.dte_asociado.get()
        self.assertEqual(pago.metodo_pago, 'MP_POINT_DEBITO')
        self.assertEqual(pago.tipo_tarjeta, 'debit_card')
        self.assertEqual(pago.voucher, self.NUMERO_MP)  # el del cobro, si no se indica otro
        self.assertEqual(pago.fecha_pago, self.hoy)
        self.assertEqual(dev.metodo_devolucion, 'MERCADO_PAGO')

        c = self._cuadratura()
        self.assertEqual(int(c['total_mercadopago_pos_bruto']), 23800)
        self.assertEqual(int(c['total_nc_mercadopago_pos']), 11900)
        self.assertEqual(int(c['total_mercadopago_pos']), 11900)
        self.assertEqual(int(c['total_mercadopago_pos_debito']), 11900)
        self.assertEqual(int(c['total_nc_transferencia']), 0)
        self.assertEqual(int(c['total_transferencia']), 0)
        self.assertEqual(int(c['total_nc_efectivo']), 0)
        self.assertEqual(int(c['venta_total']), 11900)

        # Libro de cobros MP: devolución parcial anotada, la venta sigue aprobada.
        self.assertEqual([d.monto for d in self.trx.devoluciones.all()], [11900])
        self.trx.refresh_from_db()
        self.assertEqual(self.trx.estado, 'APROBADA')

    def test_devolucion_total_por_mp_deja_el_cobro_devuelto(self):
        dev = self._solicitud(cantidad=2)
        self._aprobar(dev, numero='177000009999')
        self.trx.refresh_from_db()
        self.assertEqual(self.trx.estado, 'DEVUELTA')
        dev.refresh_from_db()
        self.assertEqual(dev.nota_credito.dte_asociado.get().voucher, '177000009999')

    def test_mercado_pago_exige_numero_si_el_sistema_no_lo_conoce(self):
        self.trx.payment_id_mp = ''
        self.trx.save(update_fields=['payment_id_mp'])
        dev = self._solicitud()
        with self.assertRaisesMessage(service.DevolucionGarantiaError, 'N° de operación'):
            self._aprobar(dev)
        dev.refresh_from_db()
        self.assertEqual(dev.estado, 'PENDIENTE')

        _, nc, _, _ = self._aprobar(dev, numero=' 177 000 000 777 ')
        self.assertEqual(nc.dte_asociado.get().voucher, '177000000777')

    def test_mercado_pago_bloqueado_si_la_venta_no_fue_con_mp(self):
        efectivo = _crear_documento(self.env, 6101, [(self.pt, 1, 11900)], metodo_pago='EFECTIVO')
        with self.assertRaisesMessage(service.DevolucionGarantiaError, 'no se cobró con Mercado Pago'):
            self._solicitud(efectivo)

        dev = self._solicitud(efectivo, metodo='TRANSFERENCIA_BANCARIA')
        preview = service.impacto_caja_preview(devolucion=dev, metodo='MERCADO_PAGO',
                                               fecha_imputacion=self.hoy)
        self.assertTrue(preview['bloqueado'])
        with self.assertRaisesMessage(service.DevolucionGarantiaError, 'no se cobró con Mercado Pago'):
            self._aprobar(dev, numero='1')

    def test_no_devuelve_por_mp_mas_de_lo_cobrado_con_mp(self):
        """Boleta mixta: $11.900 efectivo + $11.900 MP. Por MP se devuelve hasta
        lo cobrado con MP; el resto va por el medio con que se pagó."""
        mixta = _crear_documento(self.env, 6102, [(self.pt, 2, 11900)], metodo_pago=None)
        Dte_Detalle_Pago.objects.create(dte=mixta, metodo_pago='EFECTIVO', monto=11900)
        Dte_Detalle_Pago.objects.create(dte=mixta, metodo_pago='MP_POINT_CREDITO',
                                        tipo_tarjeta='credit_card', voucher='555', monto=11900)
        with self.assertRaisesMessage(service.DevolucionGarantiaError, 'hasta $11,900'):
            self._solicitud(mixta, cantidad=2)
        dev = self._solicitud(mixta, cantidad=1)
        preview = service.impacto_caja_preview(devolucion=dev, metodo='MERCADO_PAGO',
                                               fecha_imputacion=self.hoy)
        self.assertFalse(preview['bloqueado'])
        self.assertIn('Mercado Pago POS', preview['descripcion'])
        _, nc, _, _ = self._aprobar(dev)
        self.assertEqual(nc.dte_asociado.get().voucher, '555')  # N° del MP manual
        self.assertEqual(service.pago_mercadopago_dte(mixta)['disponible'], 0)

    # ----- corrección de una devolución ya aprobada (caso PAO1) -----

    def test_corregir_transferencia_a_mercado_pago_mueve_la_caja(self):
        dev = self._solicitud(metodo='TRANSFERENCIA_BANCARIA')
        dev, nc, _, _ = self._aprobar(dev, metodo='TRANSFERENCIA_BANCARIA')
        c = self._cuadratura()
        self.assertEqual(int(c['total_nc_transferencia']), 11900)
        self.assertEqual(int(c['total_transferencia']), -11900)   # lo que se veía en PAO1
        self.assertEqual(int(c['total_mercadopago_pos']), 23800)

        res = service.cambiar_metodo_devolucion(
            devolucion_id=dev.id, usuario=self.user, metodo_nuevo='MERCADO_PAGO',
            motivo='Se devolvió por la app de MP')

        self.assertEqual(res['numero_operacion_mp'], self.NUMERO_MP)
        pago = nc.dte_asociado.get()
        self.assertEqual(pago.metodo_pago, 'MP_POINT_DEBITO')
        self.assertEqual(pago.voucher, self.NUMERO_MP)
        self.assertEqual(pago.fecha_pago, self.hoy)          # conserva la imputación
        c = self._cuadratura()
        self.assertEqual(int(c['total_nc_transferencia']), 0)
        self.assertEqual(int(c['total_transferencia']), 0)
        self.assertEqual(int(c['total_nc_mercadopago_pos']), 11900)
        self.assertEqual(int(c['total_mercadopago_pos']), 11900)
        self.assertEqual(int(c['venta_total']), 11900)       # el total no cambia
        dev.refresh_from_db()
        self.assertEqual(dev.metodo_devolucion, 'MERCADO_PAGO')
        self.assertEqual(dev.metodo_solicitado, 'MERCADO_PAGO')
        self.assertIn('[CORREGIDO', dev.observaciones_aprobacion)
        self.assertEqual(self.trx.devoluciones.count(), 1)

        # Y de vuelta: la devolución sale del libro MP.
        service.cambiar_metodo_devolucion(
            devolucion_id=dev.id, usuario=self.user, metodo_nuevo='TRANSFERENCIA_BANCARIA')
        self.assertEqual(self.trx.devoluciones.count(), 0)
        self.assertEqual(int(self._cuadratura()['total_nc_transferencia']), 11900)

    def test_corregir_rechaza_devolucion_pendiente_o_mismo_metodo(self):
        dev = self._solicitud(metodo='TRANSFERENCIA_BANCARIA')
        with self.assertRaisesMessage(service.DevolucionGarantiaError, 'aprobada'):
            service.cambiar_metodo_devolucion(
                devolucion_id=dev.id, usuario=self.user, metodo_nuevo='MERCADO_PAGO')
        self._aprobar(dev, metodo='TRANSFERENCIA_BANCARIA')
        with self.assertRaisesMessage(service.DevolucionGarantiaError, 'ya está registrada'):
            service.cambiar_metodo_devolucion(
                devolucion_id=dev.id, usuario=self.user, metodo_nuevo='TRANSFERENCIA_BANCARIA')

    def test_comando_dry_run_no_escribe_y_apply_corrige_y_recalcula_arqueo(self):
        from io import StringIO
        from django.core.management import call_command
        from app.models import ArqueoCaja
        dev = self._solicitud(metodo='TRANSFERENCIA_BANCARIA')
        dev, nc, _, _ = self._aprobar(dev, metodo='TRANSFERENCIA_BANCARIA')
        arqueo = ArqueoCaja.objects.create(
            fecha_arqueo=self.hoy, sucursal=self.sucursal, usuario_responsable=self.user,
            estado='CERRADO', total_transferencia_teorico=-11900,
            total_mercadopago_pos_teorico=23800,
        )

        salida = StringIO()
        call_command('corregir_metodo_devolucion_dg', dev.numero_operacion, metodo='MP',
                     usuario=self.user.username, stdout=salida)
        self.assertIn('DRY-RUN', salida.getvalue())
        self.assertEqual(nc.dte_asociado.get().metodo_pago, 'TRANSFERENCIA')

        salida = StringIO()
        call_command('corregir_metodo_devolucion_dg', dev.numero_operacion, metodo='MP',
                     usuario=self.user.username, apply=True, recalcular_arqueo=True, stdout=salida)
        self.assertIn('OK', salida.getvalue())
        self.assertEqual(nc.dte_asociado.get().metodo_pago, 'MP_POINT_DEBITO')
        arqueo.refresh_from_db()
        self.assertEqual(int(arqueo.total_transferencia_teorico), 0)
        self.assertEqual(int(arqueo.total_mercadopago_pos_teorico), 11900)

    # ----- comprobante, detalle y búsqueda -----

    def test_comprobante_trae_correo_y_numero_de_operacion(self):
        receptor = _receptor(self.env)
        receptor.correoVendedor = 'cliente@correo.cl'
        receptor.save(update_fields=['correoVendedor'])
        dev = self._solicitud()
        self._aprobar(dev)

        modulo, _ = ModuloSistema.objects.get_or_create(
            codigo='ventas', defaults={'nombre': 'Ventas', 'orden': 2})
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='devolucion_garantia',
            defaults={'modulo': modulo, 'nombre': 'Devolucion de Dinero', 'orden': 3})
        usuario = crear_usuario(username='user_ticket_mp', rol='jefe_local')
        PermisoRol.objects.update_or_create(
            rol=usuario.rol, opcion_menu=opcion, defaults={'puede_ver': True})
        client = Client()
        client.force_login(usuario)
        session = client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

        data = client.get(reverse('api_ticket_devolucion_garantia', args=[dev.id])).json()['data']
        self.assertEqual(data['cliente']['email'], 'cliente@correo.cl')
        self.assertEqual(data['metodo_solicitado_display'], 'Mercado Pago')
        self.assertEqual(data['mercadopago']['numero_operacion'], self.NUMERO_MP)
        self.assertTrue(data['mercadopago']['devuelto'])
        self.assertIsNone(data['transferencia'])

        detalle = client.get(reverse('detalle_devolucion_garantia', args=[dev.id])).content.decode()
        self.assertIn('cliente@correo.cl', detalle)
        self.assertIn(self.NUMERO_MP, detalle)

        # Búsqueda del listado por el N° de operación de Mercado Pago.
        listado = client.get(reverse('api_listar_devoluciones_garantia'),
                             {'q': self.NUMERO_MP}).json()
        self.assertEqual([d['id'] for d in listado['data']], [dev.id])


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DevolucionDineroRefundApiTest(TestCase):
    """Devolver a la tarjeta por la API de Mercado Pago desde Devolución de
    Dinero (28-09-2026): RetailMind pide el refund a MP (refund-primero, la NC
    solo se emite si MP acepta), la NC queda con el N° del cobro y las filas
    DEVOLUCION del libro MP quedan marcadas como de esta devolución. Mueve
    plata real: permiso `devolver_mercadopago` (Maestro)."""

    NUMERO_MP = '177000000001'
    REFUND_ID = 3361111606

    def setUp(self):
        from app.models import MercadoPagoConfig, TransaccionMercadoPago
        self.env = setup_entorno_completo()
        self.user = self.env['user']
        self.sucursal = self.env['sucursal']
        self.pt = self.env['producto_talla']
        crear_correlativo(self.sucursal, tipo_dte='NOTA DE CREDITO')
        self.hoy = timezone.localdate()
        self.boleta = _crear_documento(self.env, 6200, [(self.pt, 2, 11900)], metodo_pago=None)
        Dte_Detalle_Pago.objects.create(
            dte=self.boleta, metodo_pago='MP_POINT_DEBITO', tipo_tarjeta='debit_card',
            voucher='PAY01TESTULID0002', monto=23800, notas='MP Point Aut: -')
        self.config = MercadoPagoConfig.objects.create(
            sucursal=self.sucursal, habilitado=True, modo='POINT',
            token_env='MP_TOKEN_TEST', webhook_secret_env='MP_SECRET_TEST',
            external_pos_id='POS002', external_store_id='SUC002',
        )
        self.trx = TransaccionMercadoPago.objects.create(
            config=self.config, sucursal=self.sucursal, correlativo_ticket='12181',
            tipo='VENTA', canal='POINT', external_reference='RM-TEST-12181-c6i02',
            payment_id='PAY01TESTULID0002', payment_id_mp=self.NUMERO_MP,
            metodo_pago_mp='debit_card', monto=23800, estado='APROBADA', consumida=True,
        )
        self.llamadas = []

    # ----- utilidades -----

    def _mock_request(self, refund_status=201, refund_json=None, search_results=None):
        """Simula `_request` de mercadopago_service: POST refunds y GET payments/search."""
        from unittest import mock
        llamadas = self.llamadas

        def _fake(config, metodo, path, json_body=None, idempotency_key=None, params=None,
                  timeout=None, cuenta_breaker=True):
            llamadas.append((metodo, path, json_body, params))
            resp = mock.MagicMock()
            if metodo == 'POST' and path.endswith('/refunds'):
                resp.status_code = refund_status
                resp.json.return_value = refund_json if refund_json is not None else {
                    'id': self.REFUND_ID, 'amount': (json_body or {}).get('amount', 23800),
                    'status': 'approved',
                }
            elif metodo == 'GET' and path == '/v1/payments/search':
                resp.status_code = 200
                resp.json.return_value = {'results': search_results or []}
            else:
                resp.status_code = 404
                resp.json.return_value = {'message': f'unexpected {metodo} {path}'}
            return resp
        return mock.patch('app.services.mercadopago_service._request', side_effect=_fake)

    def _solicitud(self, cantidad=1, dte=None):
        dte = dte or self.boleta
        return service.crear_solicitud_devolucion(
            dte_original=dte, sucursal=self.sucursal, receptor=_receptor(self.env),
            motivo='Falla', usuario=self.user, metodo_solicitado='MERCADO_PAGO',
            detalles=[{'dte_producto_id': dte.dte_productos.first().id,
                       'modo': 'CANTIDAD', 'cantidad': cantidad}],
        )

    def _aprobar_api(self, dev):
        return service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user, metodo_devolucion='MERCADO_PAGO',
            fecha_imputacion=self.hoy, devolver_mp_api=True,
        )

    # ----- service -----

    def test_detecta_cuanto_se_puede_devolver_por_api(self):
        mp = service.pago_mercadopago_dte(self.boleta)
        self.assertEqual(mp['devolvible_api'], 23800)

    def test_refund_por_api_emite_la_nc_y_marca_el_libro_mp(self):
        from app.models import TransaccionMercadoPago
        dev = self._solicitud(cantidad=1)

        with self._mock_request():
            dev, nc, _txt, _w = self._aprobar_api(dev)

        posts = [c for c in self.llamadas if c[0] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][1], f'/v1/payments/{self.NUMERO_MP}/refunds')
        self.assertEqual(posts[0][2], {'amount': 11900})

        dev.refresh_from_db()
        self.assertEqual(dev.estado, 'NC_GENERADA')
        self.assertEqual(dev.metodo_devolucion, 'MERCADO_PAGO')
        pago = nc.dte_asociado.get()
        self.assertEqual(pago.metodo_pago, 'MP_POINT_DEBITO')
        self.assertEqual(pago.voucher, self.NUMERO_MP)
        self.assertIn('API', pago.notas)
        self.assertIn(str(self.REFUND_ID), pago.notas)

        refund = TransaccionMercadoPago.objects.get(tipo='DEVOLUCION')
        self.assertEqual(refund.payment_id, str(self.REFUND_ID))
        self.assertEqual(refund.transaccion_origen_id, self.trx.id)
        self.assertTrue(refund.estado_detalle.endswith(f'({dev.numero_operacion})'))
        self.trx.refresh_from_db()
        self.assertEqual(self.trx.estado, 'APROBADA')  # parcial: sigue con saldo

        info = service.mercadopago_de_devolucion(dev)
        self.assertTrue(info['api'])
        self.assertEqual(info['refunds'], [str(self.REFUND_ID)])
        self.assertEqual(info['numero_operacion'], self.NUMERO_MP)
        # Ya no queda nada devolvible por API sobre la parte devuelta.
        self.assertEqual(service.pago_mercadopago_dte(self.boleta)['devolvible_api'], 11900)

    def test_refund_total_deja_el_cobro_devuelto(self):
        dev = self._solicitud(cantidad=2)
        with self._mock_request():
            self._aprobar_api(dev)
        self.trx.refresh_from_db()
        self.assertEqual(self.trx.estado, 'DEVUELTA')

    def test_refund_usa_el_numero_real_y_no_el_ulid(self):
        """Point integrado guarda el ULID `PAY01…` en payment_id; el refund debe
        ir con el número real, buscándolo en MP si no está guardado."""
        self.trx.payment_id_mp = ''
        self.trx.save(update_fields=['payment_id_mp'])
        dev = self._solicitud(cantidad=1)
        busqueda = [{'id': self.NUMERO_MP, 'external_reference': self.trx.external_reference,
                     'status': 'approved'}]

        with self._mock_request(search_results=busqueda):
            dev, nc, _txt, _w = self._aprobar_api(dev)

        posts = [c for c in self.llamadas if c[0] == 'POST']
        self.assertEqual(posts[0][1], f'/v1/payments/{self.NUMERO_MP}/refunds')
        self.assertNotIn('PAY01', posts[0][1])
        self.trx.refresh_from_db()
        self.assertEqual(self.trx.payment_id_mp, self.NUMERO_MP)
        self.assertEqual(nc.dte_asociado.get().voucher, self.NUMERO_MP)

    def test_sin_numero_de_pago_no_hay_refund_ni_nc(self):
        from app.models import TransaccionMercadoPago
        self.trx.payment_id_mp = ''
        self.trx.save(update_fields=['payment_id_mp'])
        dev = self._solicitud(cantidad=1)

        with self._mock_request(search_results=[]):
            with self.assertRaises(service.DevolucionGarantiaError) as ctx:
                self._aprobar_api(dev)

        self.assertIn('N° de pago', str(ctx.exception))
        self.assertFalse([c for c in self.llamadas if c[0] == 'POST'])
        dev.refresh_from_db()
        self.assertEqual(dev.estado, 'PENDIENTE')
        self.assertIsNone(dev.nota_credito_id)
        self.assertFalse(TransaccionMercadoPago.objects.filter(tipo='DEVOLUCION').exists())

    def test_si_mp_rechaza_el_refund_no_se_emite_la_nc(self):
        from app.models import TransaccionMercadoPago
        dev = self._solicitud(cantidad=1)

        with self._mock_request(refund_status=400, refund_json={'message': 'Refund not allowed'}):
            with self.assertRaises(service.DevolucionGarantiaError) as ctx:
                self._aprobar_api(dev)

        self.assertIn('Mercado Pago no pudo devolver', str(ctx.exception))
        self.assertIn('NC NO fue emitida', str(ctx.exception))
        dev.refresh_from_db()
        self.assertEqual(dev.estado, 'PENDIENTE')
        self.assertFalse(Dte.objects.filter(tipo_documento='NOTA DE CREDITO').exists())
        self.assertFalse(TransaccionMercadoPago.objects.filter(tipo='DEVOLUCION').exists())

    def test_cambiar_metodo_bloqueado_tras_refund_por_api(self):
        dev = self._solicitud(cantidad=1)
        with self._mock_request():
            self._aprobar_api(dev)

        with self.assertRaises(service.DevolucionGarantiaError) as ctx:
            service.cambiar_metodo_devolucion(
                devolucion_id=dev.id, usuario=self.user, metodo_nuevo='TRANSFERENCIA_BANCARIA')
        self.assertIn('API', str(ctx.exception))
        self.assertIn(str(self.REFUND_ID), str(ctx.exception))

    def test_mp_manual_no_es_devolvible_por_api(self):
        """Cobro «MP manual» (sin pago en Mercado Pago): no hay qué reembolsar."""
        manual = _crear_documento(self.env, 6201, [(self.pt, 1, 11900)], metodo_pago=None)
        Dte_Detalle_Pago.objects.create(
            dte=manual, metodo_pago='MP_MANUAL_DEBITO', tipo_tarjeta='debit_card',
            voucher='180000000123', monto=11900)
        self.assertTrue(service.pago_mercadopago_dte(manual)['es_mp'])
        self.assertEqual(service.pago_mercadopago_dte(manual)['devolvible_api'], 0)
        dev = self._solicitud(cantidad=1, dte=manual)

        with self._mock_request():
            with self.assertRaises(service.DevolucionGarantiaError) as ctx:
                self._aprobar_api(dev)

        self.assertIn('no se puede devolver por la API', str(ctx.exception))
        self.assertFalse(self.llamadas)
        dev.refresh_from_db()
        self.assertEqual(dev.estado, 'PENDIENTE')

    def test_registrar_devolucion_hecha_a_mano_sigue_igual(self):
        """Sin `devolver_mp_api` no se llama a MP: se registra el N° indicado."""
        dev = self._solicitud(cantidad=1)
        with self._mock_request():
            dev, nc, _txt, _w = service.aprobar_devolucion(
                devolucion_id=dev.id, aprobador=self.user, metodo_devolucion='MERCADO_PAGO',
                fecha_imputacion=self.hoy, numero_operacion_mp='177000000999')
        self.assertFalse(self.llamadas)
        self.assertEqual(nc.dte_asociado.get().voucher, '177000000999')
        self.assertFalse(service.mercadopago_de_devolucion(dev)['api'])

    # ----- endpoints: permiso `devolver_mercadopago` -----

    def _permisos_modulo(self):
        modulo, _ = ModuloSistema.objects.get_or_create(
            codigo='ventas', defaults={'nombre': 'Ventas', 'orden': 2})
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='devolucion_garantia',
            defaults={'modulo': modulo, 'nombre': 'Devolucion de Dinero', 'orden': 3})
        PermisoRol.objects.update_or_create(
            rol='administrador', opcion_menu=opcion,
            defaults={'puede_ver': True, 'puede_crear': True, 'puede_aprobar': True})
        PermisoRol.objects.update_or_create(
            rol='jefe_local', opcion_menu=opcion,
            defaults={'puede_ver': True, 'puede_crear': True})
        # La migración 0237 siembra `devolver_mercadopago` en True para el
        # Administrador (para no quitar nada al desplegar); en producción la
        # política de perfiles lo apaga. Acá se apaga igual: el Maestro pasa
        # siempre, el Administrador no.
        opcion_mp, _ = OpcionMenu.objects.get_or_create(
            codigo='devolver_mercadopago',
            defaults={'modulo': modulo, 'nombre': 'Devolver por Mercado Pago', 'orden': 99})
        PermisoRol.objects.update_or_create(
            rol='administrador', opcion_menu=opcion_mp,
            defaults={'puede_ver': False, 'puede_crear': False})

    def _cliente(self, usuario):
        client = Client()
        client.force_login(usuario)
        session = client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()
        return client

    def test_aprobar_por_api_exige_permiso_devolver_mercadopago(self):
        import json as _json
        self._permisos_modulo()
        admin = crear_usuario(username='admin_sin_mp', rol='administrador')
        dev = self._solicitud(cantidad=1)

        with self._mock_request():
            resp = self._cliente(admin).post(
                reverse('api_aprobar_devolucion_garantia', args=[dev.id]),
                data=_json.dumps({'metodo_devolucion': 'MERCADO_PAGO', 'devolver_mp_api': True,
                                  'fecha_imputacion': self.hoy.strftime('%Y-%m-%d')}),
                content_type='application/json')

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['code'], 'CANNOT_REFUND_MP')
        self.assertFalse(self.llamadas)
        dev.refresh_from_db()
        self.assertEqual(dev.estado, 'PENDIENTE')

    def test_maestro_aprueba_con_refund_por_api(self):
        import json as _json
        self._permisos_modulo()
        maestro = crear_usuario(username='maestro_mp', rol='maestro')
        dev = self._solicitud(cantidad=1)

        with self._mock_request():
            resp = self._cliente(maestro).post(
                reverse('api_aprobar_devolucion_garantia', args=[dev.id]),
                data=_json.dumps({'metodo_devolucion': 'MERCADO_PAGO', 'devolver_mp_api': True,
                                  'fecha_imputacion': self.hoy.strftime('%Y-%m-%d')}),
                content_type='application/json')

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertTrue(data['devuelto_api'])
        self.assertEqual(data['refunds_mp'], [str(self.REFUND_ID)])
        self.assertEqual(data['numero_operacion_mp'], self.NUMERO_MP)
        self.assertEqual(len([c for c in self.llamadas if c[0] == 'POST']), 1)

    def test_pagina_ofrece_refund_por_api_solo_con_permiso(self):
        self._permisos_modulo()
        admin = crear_usuario(username='admin_pagina', rol='administrador')
        html = self._cliente(admin).get(reverse('modulo_devolucion_garantia')).content.decode()
        self.assertIn('const PUEDE_DEVOLVER_MP = false', html)
        self.assertNotIn('id="apr-mp-modo-api"', html)
        maestro = crear_usuario(username='maestro_pagina', rol='maestro')
        html = self._cliente(maestro).get(reverse('modulo_devolucion_garantia')).content.decode()
        self.assertIn('const PUEDE_DEVOLVER_MP = true', html)
        self.assertIn('id="apr-mp-modo-api"', html)
        # El wizard siempre ofrece la opción: en la directa decide quien firma.
        self.assertIn('id="dg-mp-modo-api"', html)

    def _codigo(self, usuario, codigo):
        from app.models import CodigoAutorizacionDinamico
        return CodigoAutorizacionDinamico.objects.create(
            codigo=codigo,
            fecha_hora_inicio=timezone.now() - timedelta(minutes=1),
            fecha_hora_fin=timezone.now() + timedelta(minutes=30),
            generado_por=usuario,
        )

    def _post_directa(self, client, codigo, devolver_api=True):
        import json as _json
        return client.post(
            reverse('api_generar_devolucion_garantia'),
            data=_json.dumps({
                'folio_dte': self.boleta.numero_documento,
                'productos': [{'dte_producto_id': self.boleta.dte_productos.first().id,
                               'modo': 'CANTIDAD', 'cantidad': 1}],
                'rut': '13013448-3', 'nombre': 'Cliente MP', 'motivo': 'Falla',
                'metodo_solicitado': 'MERCADO_PAGO',
                'directa': True, 'codigo_autorizacion': codigo,
                'devolver_mp_api': devolver_api,
            }),
            content_type='application/json')

    def test_directa_con_codigo_de_maestro_devuelve_por_api(self):
        self._permisos_modulo()
        jefe = crear_usuario(username='jefe_directa_mp', rol='jefe_local')
        maestro = crear_usuario(username='maestro_directa_mp', rol='maestro')
        codigo = self._codigo(maestro, '731901')

        with self._mock_request():
            resp = self._post_directa(self._cliente(jefe), codigo.codigo)

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertTrue(data['devuelto_api'])
        self.assertEqual(data['refunds_mp'], [str(self.REFUND_ID)])
        dev = DevolucionGarantia.objects.get(id=data['devolucion_id'])
        self.assertEqual(dev.estado, 'NC_GENERADA')
        self.assertEqual(dev.autorizado_por_id, maestro.id)
        codigo.refresh_from_db()
        self.assertTrue(codigo.usado)

    def test_directa_con_codigo_de_administrador_sin_permiso_mp_se_rechaza(self):
        self._permisos_modulo()
        jefe = crear_usuario(username='jefe_directa_mp2', rol='jefe_local')
        admin = crear_usuario(username='admin_directa_mp', rol='administrador')
        codigo = self._codigo(admin, '731902')

        with self._mock_request():
            resp = self._post_directa(self._cliente(jefe), codigo.codigo)

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['code'], 'AUTHORIZER_CANNOT_REFUND_MP')
        self.assertFalse(self.llamadas)
        self.assertFalse(DevolucionGarantia.objects.exists())
        codigo.refresh_from_db()
        self.assertFalse(codigo.usado)

    def test_directa_si_mp_rechaza_no_queda_nada_ni_se_quema_el_codigo(self):
        self._permisos_modulo()
        jefe = crear_usuario(username='jefe_directa_mp3', rol='jefe_local')
        maestro = crear_usuario(username='maestro_directa_mp3', rol='maestro')
        codigo = self._codigo(maestro, '731903')

        with self._mock_request(refund_status=400, refund_json={'message': 'Refund not allowed'}):
            resp = self._post_directa(self._cliente(jefe), codigo.codigo)

        self.assertEqual(resp.status_code, 400)
        self.assertIn('Mercado Pago no pudo devolver', resp.json()['error'])
        self.assertFalse(DevolucionGarantia.objects.exists())
        codigo.refresh_from_db()
        self.assertFalse(codigo.usado)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DevolucionDineroCorreoTest(TestCase):
    """Enviar el comprobante (PDF 80mm) al correo del cliente (28-09-2026)."""

    def setUp(self):
        self.env = setup_entorno_completo()
        self.sucursal = self.env['sucursal']
        crear_correlativo(self.sucursal, tipo_dte='NOTA DE CREDITO')
        self.boleta = _crear_documento(self.env, 6300, [(self.env['producto_talla'], 1, 11900)])
        self.receptor = _receptor(self.env)
        self.receptor.correoVendedor = ''
        self.receptor.save(update_fields=['correoVendedor'])
        self.dev = service.crear_solicitud_devolucion(
            dte_original=self.boleta, sucursal=self.sucursal, receptor=self.receptor,
            motivo='Producto fallado', usuario=self.env['user'],
            detalles=[{'dte_producto_id': self.boleta.dte_productos.first().id,
                       'modo': 'CANTIDAD', 'cantidad': 1}],
        )
        modulo, _ = ModuloSistema.objects.get_or_create(
            codigo='ventas', defaults={'nombre': 'Ventas', 'orden': 2})
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='devolucion_garantia',
            defaults={'modulo': modulo, 'nombre': 'Devolucion de Dinero', 'orden': 3})
        PermisoRol.objects.update_or_create(
            rol='jefe_local', opcion_menu=opcion, defaults={'puede_ver': True, 'puede_crear': True})
        self.user = crear_usuario(username='jefe_correo', rol='jefe_local')
        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()
        self.url = reverse('api_enviar_comprobante_devolucion_garantia', args=[self.dev.id])

    def _post(self, email='cliente@correo.cl', **extra):
        import json as _json
        body = {'email': email}
        body.update(extra)
        return self.client.post(self.url, data=_json.dumps(body), content_type='application/json')

    def test_envia_pdf_adjunto_y_lo_registra(self):
        from django.core import mail
        from app.models import EnvioCorreo

        resp = self._post()

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['data']['email'], 'cliente@correo.cl')
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ['cliente@correo.cl'])
        self.assertIn(self.dev.numero_operacion, correo.subject)
        self.assertIn(self.dev.numero_operacion, correo.body)
        html = next(c for c, t in correo.alternatives if t == 'text/html')
        self.assertIn(self.dev.numero_operacion, html)
        self.assertIn('DEVOLUCIÓN DE DINERO', html)
        self.assertEqual(len(correo.attachments), 1)
        nombre, contenido, tipo = correo.attachments[0]
        self.assertEqual(nombre, f'Comprobante_{self.dev.numero_operacion}.pdf')
        self.assertEqual(tipo, 'application/pdf')
        self.assertTrue(contenido.startswith(b'%PDF'))

        envio = EnvioCorreo.objects.get(modulo='DEVOLUCION_DINERO', objeto_id=self.dev.id)
        self.assertEqual(envio.destinatario, 'cliente@correo.cl')
        self.assertEqual(envio.estado, 'ENVIADO')
        self.assertEqual(envio.adjuntos, 1)
        self.assertEqual(envio.enviado_por_id, self.user.id)
        # El receptor no tenía correo: queda guardado para la próxima.
        self.receptor.refresh_from_db()
        self.assertEqual(self.receptor.correoVendedor, 'cliente@correo.cl')

    def test_sin_correo_usa_el_del_receptor(self):
        from django.core import mail
        self.receptor.correoVendedor = 'guardado@correo.cl'
        self.receptor.save(update_fields=['correoVendedor'])
        resp = self._post(email='')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(mail.outbox[0].to, ['guardado@correo.cl'])

    def test_correo_invalido(self):
        from django.core import mail
        resp = self._post(email='no-es-correo')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(mail.outbox)

    def test_rechazada_no_se_envia(self):
        from django.core import mail
        service.rechazar_devolucion(devolucion_id=self.dev.id, aprobador=self.env['user'],
                                    motivo_rechazo='No corresponde')
        resp = self._post()
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(mail.outbox)

    def test_aislado_por_sucursal(self):
        otra = crear_sucursal(self.env['empresa'], alias='SUC-CORREO-OTRA')
        session = self.client.session
        session['idSucursalActual'] = otra.id
        session.save()
        self.assertEqual(self._post().status_code, 404)

    def test_aprobada_lleva_la_nc_en_el_comprobante(self):
        from django.core import mail
        service.aprobar_devolucion(
            devolucion_id=self.dev.id, aprobador=self.env['user'],
            metodo_devolucion='TRANSFERENCIA_BANCARIA', fecha_imputacion=timezone.localdate())
        self.dev.refresh_from_db()
        resp = self._post()
        self.assertEqual(resp.status_code, 200, resp.content)
        html = next(c for c, t in mail.outbox[0].alternatives if t == 'text/html')
        self.assertIn(str(self.dev.nota_credito.numero_documento), html)
        self.assertIn('Tu devolución está lista', html)

    def test_detalle_y_listado_muestran_el_envio(self):
        self._post()
        detalle = self.client.get(reverse('detalle_devolucion_garantia', args=[self.dev.id])).content.decode()
        self.assertIn('Comprobante enviado a', detalle)
        self.assertIn('cliente@correo.cl', detalle)
        self.assertIn('enviarComprobanteDG(', detalle)
        fila = self.client.get(reverse('api_listar_devoluciones_garantia')).json()['data'][0]
        self.assertEqual(fila['receptor_email'], 'cliente@correo.cl')
        self.assertEqual(fila['correos_enviados'], 1)

    def test_pdf_del_comprobante(self):
        from app.services.pdf_comprobante_devolucion import generar_comprobante_devolucion_pdf
        from app.services.pdf_guia_preparacion import _paginas
        from app.views_modulo_devolucion_garantia import _payload_comprobante
        pdf = generar_comprobante_devolucion_pdf(_payload_comprobante(self.dev))
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(_paginas(pdf), 1)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DevolucionDineroRefundApiRobustezTest(DevolucionDineroRefundApiTest):
    """Revisión adversarial 28-09: el refund por API no puede duplicarse aunque
    la NC falle después, y el listado no interpola el correo en un onclick."""

    def _mock_request_con_claves(self, respuestas_refund):
        """Como _mock_request, pero cada POST /refunds consume la siguiente
        respuesta de `respuestas_refund` [(status, json), ...] y registra la
        X-Idempotency-Key en `self.claves`."""
        from unittest import mock
        if not hasattr(self, 'claves'):
            self.claves = []
        llamadas, claves = self.llamadas, self.claves
        cola = list(respuestas_refund)

        def _fake(config, metodo, path, json_body=None, idempotency_key=None, params=None,
                  timeout=None, cuenta_breaker=True):
            llamadas.append((metodo, path, json_body, params))
            resp = mock.MagicMock()
            if metodo == 'POST' and path.endswith('/refunds'):
                claves.append(idempotency_key)
                status, js = cola.pop(0) if cola else (201, None)
                resp.status_code = status
                resp.json.return_value = js if js is not None else {
                    'id': self.REFUND_ID + len(claves), 'amount': (json_body or {}).get('amount'),
                    'status': 'approved'}
            else:
                resp.status_code = 200
                resp.json.return_value = {'results': []}
            return resp
        return mock.patch('app.services.mercadopago_service._request', side_effect=_fake)

    def test_reintento_tras_fallo_local_no_pide_otro_refund(self):
        """MP devuelve, la NC falla después (rollback local): el refund queda
        registrado como pendiente de NC y el reintento lo reutiliza."""
        import json as _json
        from unittest import mock
        from app.models import TransaccionMercadoPago
        import app.views as views_mod
        self._permisos_modulo()
        maestro = crear_usuario(username='maestro_retry', rol='maestro')
        client = self._cliente(maestro)
        dev = self._solicitud(cantidad=1)
        original = views_mod.obtener_siguiente_correlativo
        body = _json.dumps({'metodo_devolucion': 'MERCADO_PAGO', 'devolver_mp_api': True,
                            'fecha_imputacion': self.hoy.strftime('%Y-%m-%d')})
        url = reverse('api_aprobar_devolucion_garantia', args=[dev.id])

        with self._mock_request_con_claves([]), \
                mock.patch.object(views_mod, 'obtener_siguiente_correlativo',
                                  side_effect=[RuntimeError('correlativo caído'), None]) as m_corr:
            resp1 = client.post(url, data=body, content_type='application/json')
        self.assertEqual(resp1.status_code, 500)
        self.assertEqual(resp1.json()['code'], 'REFUND_SIN_NC')
        self.assertIn('YA devolvió', resp1.json()['error'])
        dev.refresh_from_db()
        self.assertEqual(dev.estado, 'PENDIENTE')
        huerfano = TransaccionMercadoPago.objects.get(tipo='DEVOLUCION')
        self.assertTrue(huerfano.estado_detalle.startswith(service.MARCA_REFUND_PENDIENTE_NC))
        self.assertEqual(huerfano.monto, 11900)
        # Lo que la API puede resolver: saldo (11.900) + lo ya devuelto sin NC (11.900).
        mp = service.pago_mercadopago_dte(self.boleta)
        self.assertEqual(mp['refund_pendiente_nc'], 11900)
        self.assertEqual(mp['devolvible_api'], 23800)

        with self._mock_request_con_claves([]):
            resp2 = client.post(url, data=body, content_type='application/json')
        self.assertEqual(resp2.status_code, 200, resp2.content)
        # Un solo POST a MP en total: el segundo intento no volvió a pedir el refund.
        self.assertEqual(len([c for c in self.llamadas if c[0] == 'POST']), 1)
        self.assertEqual(TransaccionMercadoPago.objects.filter(tipo='DEVOLUCION').count(), 1)
        fila = TransaccionMercadoPago.objects.get(tipo='DEVOLUCION')
        dev.refresh_from_db()
        self.assertTrue(fila.estado_detalle.endswith(f'({dev.numero_operacion})'))
        self.assertEqual(dev.estado, 'NC_GENERADA')
        self.assertEqual(resp2.json()['data']['refunds_mp'], [fila.payment_id])

    def test_refund_parcial_multi_cobro_persiste_lo_hecho(self):
        """Dos cobros MP: el primer refund pasa y el segundo lo rechaza MP. Lo
        hecho queda en el libro y el reintento solo pide lo que falta."""
        from app.models import TransaccionMercadoPago
        trx2 = TransaccionMercadoPago.objects.create(
            config=self.config, sucursal=self.sucursal, correlativo_ticket='12181',
            tipo='VENTA', canal='POINT', external_reference='RM-TEST-12181-c6i03',
            payment_id_mp='177000000002', metodo_pago_mp='debit_card', monto=10000,
            estado='APROBADA', consumida=True, ticket=None)
        # El cobro de 23.800 del setUp pasa a ser 13.800 + 10.000.
        self.trx.monto = 13800
        self.trx.save(update_fields=['monto'])
        Dte_Detalle_Pago.objects.filter(dte=self.boleta).update(voucher='PAY01TESTULID0002')
        Dte_Detalle_Pago.objects.create(
            dte=self.boleta, metodo_pago='MP_POINT_DEBITO', tipo_tarjeta='debit_card',
            voucher='177000000002', monto=10000)
        Dte_Detalle_Pago.objects.filter(dte=self.boleta, voucher='PAY01TESTULID0002').update(monto=13800)
        self.assertEqual(service.pago_mercadopago_dte(self.boleta)['devolvible_api'], 23800)
        dev = self._solicitud(cantidad=2)  # 23.800
        refund_ctx = {'refunds': []}

        with self._mock_request_con_claves([(201, None), (400, {'message': 'Refund not allowed'})]):
            with self.assertRaises(service.DevolucionGarantiaError):
                service.aprobar_devolucion(
                    devolucion_id=dev.id, aprobador=self.user, metodo_devolucion='MERCADO_PAGO',
                    fecha_imputacion=self.hoy, devolver_mp_api=True, refund_ctx=refund_ctx)
        # El atomic del service revirtió la fila del primer refund…
        self.assertFalse(TransaccionMercadoPago.objects.filter(tipo='DEVOLUCION').exists())
        # …la vista la re-registra fuera del atomic:
        ids = service.persistir_refunds_sin_nc(refund_ctx, causa='MP rechazó el 2°',
                                               numero_operacion=dev.numero_operacion)
        self.assertEqual(len(ids), 1)
        mp = service.pago_mercadopago_dte(self.boleta)
        self.assertEqual(mp['refund_pendiente_nc'], 13800)
        self.assertEqual(mp['devolvible_api'], 23800)  # 10.000 de saldo + 13.800 ya devueltos

        with self._mock_request_con_claves([]):
            dev, nc, _t, _w = service.aprobar_devolucion(
                devolucion_id=dev.id, aprobador=self.user, metodo_devolucion='MERCADO_PAGO',
                fecha_imputacion=self.hoy, devolver_mp_api=True)
        posts = [c for c in self.llamadas if c[0] == 'POST']
        self.assertEqual([p[1] for p in posts], [
            f'/v1/payments/{self.NUMERO_MP}/refunds',
            '/v1/payments/177000000002/refunds',   # rechazado en el 1er intento
            '/v1/payments/177000000002/refunds',   # solo lo que faltaba en el 2°
        ])
        self.assertEqual(TransaccionMercadoPago.objects.filter(tipo='DEVOLUCION').count(), 2)
        for fila in TransaccionMercadoPago.objects.filter(tipo='DEVOLUCION'):
            self.assertTrue(fila.estado_detalle.endswith(f'({dev.numero_operacion})'))
        self.assertEqual(len(service.mercadopago_de_devolucion(dev)['refunds']), 2)

    def test_clave_de_idempotencia_fija_por_devolucion_y_cobro(self):
        dev = self._solicitud(cantidad=1)
        with self._mock_request_con_claves([(400, {'message': 'x'})]):
            with self.assertRaises(service.DevolucionGarantiaError):
                self._aprobar_api(dev)
        with self._mock_request_con_claves([]):
            self._aprobar_api(dev)
        self.assertEqual(len(self.claves), 2)
        self.assertEqual(self.claves[0], self.claves[1])
        self.assertTrue(self.claves[0].endswith(f'-REF-{dev.numero_operacion}'))
        self.assertLessEqual(len(self.claves[0]), 80)

    def test_timeout_de_mp_avisa_que_reintentar_es_seguro(self):
        from unittest import mock
        from app.services import mercadopago_service as mp_service
        dev = self._solicitud(cantidad=1)
        with mock.patch.object(mp_service, '_request',
                               side_effect=mp_service.MercadoPagoError('Mercado Pago no respondió a tiempo.', red=True)):
            with self.assertRaises(service.DevolucionGarantiaError) as ctx:
                self._aprobar_api(dev)
        self.assertIn('no se sabe si alcanzó a devolver', str(ctx.exception))
        self.assertIn('no devolverá dos veces', str(ctx.exception))
        dev.refresh_from_db()
        self.assertEqual(dev.estado, 'PENDIENTE')

    def test_listado_no_interpola_el_correo_en_un_onclick(self):
        self._permisos_modulo()
        admin = crear_usuario(username='admin_xss', rol='administrador')
        html = self._cliente(admin).get(reverse('modulo_devolucion_garantia')).content.decode()
        self.assertIn('js-dg-correo', html)
        self.assertIn('data-email="${esc(d.receptor_email', html)
        self.assertNotIn("enviarComprobanteDG(${d.id}, '${esc(d.receptor_email", html)

    def test_api_generar_rechaza_correo_invalido(self):
        import json as _json
        self._permisos_modulo()
        jefe = crear_usuario(username='jefe_mail_malo', rol='jefe_local')
        resp = self._cliente(jefe).post(
            reverse('api_generar_devolucion_garantia'),
            data=_json.dumps({
                'folio_dte': self.boleta.numero_documento,
                'productos': [{'dte_producto_id': self.boleta.dte_productos.first().id,
                               'modo': 'CANTIDAD', 'cantidad': 1}],
                'rut': '13013448-3', 'nombre': 'Cliente', 'email': 'no-es-correo',
                'metodo_solicitado': 'TRANSFERENCIA_BANCARIA', 'banco': 'B', 'tipo_cuenta': 'VISTA',
                'numero_cuenta': '1', 'cuenta_titular_rut': '13013448-3',
            }), content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('correo', resp.json()['error'].lower())
        self.assertFalse(DevolucionGarantia.objects.exists())


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DevolucionDineroCorreoRobustezTest(DevolucionDineroCorreoTest):
    """Revisión adversarial 28-09 (correo)."""

    def test_correo_demasiado_largo_400(self):
        from django.core import mail
        largo = 'a' * 95 + '@correo.cl'
        resp = self._post(email=largo)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('largo', resp.json()['error'])
        self.assertFalse(mail.outbox)
        self.receptor.refresh_from_db()
        self.assertEqual(self.receptor.correoVendedor, '')

    def test_si_el_envio_falla_no_se_guarda_el_correo_en_el_receptor(self):
        from unittest import mock
        from app.services import correo_service
        with mock.patch.object(correo_service, 'enviar_correo_trazado',
                               side_effect=correo_service.CorreoError('relay caído')):
            resp = self._post()
        self.assertEqual(resp.status_code, 502)
        self.receptor.refresh_from_db()
        self.assertEqual(self.receptor.correoVendedor, '')

    def test_reply_to_va_a_quien_envia(self):
        from django.core import mail
        self.user.email = 'jefe@tienda.cl'
        self.user.save(update_fields=['email'])
        self._post()
        self.assertIn('jefe@tienda.cl', mail.outbox[0].reply_to)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class DevolucionGarantiaInventarioTest(TestCase):
    """Auditoría de caminos 29-09-2026 (H4): al aprobar, el producto devuelto
    vuelve al inventario si está APTO (stock + lote FIFO + kardex DEVOLUCION_NC
    ligado a la NC, vía ReingresoVenta) y, si es NO APTO, no entra a stock y
    queda kardex documental DEVOLUCION_NO_APTA. Por monto no mueve nada."""

    def setUp(self):
        from .factories import crear_lote_fifo
        self.env = setup_entorno_completo()
        self.user = self.env['user']
        self.sucursal = self.env['sucursal']
        self.pt = self.env['producto_talla']  # stock 10, 1 lote de 10
        crear_correlativo(self.sucursal, tipo_dte='NOTA DE CREDITO')
        self.hoy = timezone.localdate()
        self.crear_lote_fifo = crear_lote_fifo

    def _solicitud(self, dte, detalles, **kw):
        return service.crear_solicitud_devolucion(
            dte_original=dte, sucursal=self.sucursal, receptor=_receptor(self.env),
            motivo='Garantía', usuario=self.user, detalles=detalles, **kw,
        )

    def _aprobar(self, dev, **kw):
        return service.aprobar_devolucion(
            devolucion_id=dev.id, aprobador=self.user,
            metodo_devolucion='NO_AFECTA_CAJA', **kw,
        )

    def _stock(self, pt):
        pt.refresh_from_db()
        return pt.stock

    def _lotes(self, pt):
        from app.models import LoteProducto
        return sum(LoteProducto.objects.filter(
            producto_talla=pt, activo=True, agotado=False).values_list('cantidad_disponible', flat=True))

    def _kardex_nc(self, nc, concepto):
        from app.models import Movimientos_Producto
        return list(Movimientos_Producto.objects.filter(dte=nc, concepto=concepto).order_by('id'))

    def test_apto_reingresa_stock_lote_y_kardex_ligado_a_la_nc(self):
        from app.models import LoteProducto
        boleta = _crear_documento(self.env, 6001, [(self.pt, 2, 11900)])
        dev = self._solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                        'modo': 'CANTIDAD', 'cantidad': 1}])
        dev, nc, _t, _w = self._aprobar(dev)

        self.assertEqual(self._stock(self.pt), 11)
        self.assertEqual(self._lotes(self.pt), 11)
        movs = self._kardex_nc(nc, 'DEVOLUCION_NC')
        self.assertEqual(len(movs), 1)
        mov = movs[0]
        self.assertEqual(mov.cantidad, 1)
        self.assertEqual(mov.tipo_movimiento, 'INGRESO')
        self.assertEqual(mov.ProductoTalla_id, self.pt.id)
        self.assertEqual(mov.sucursal_destino_id, self.sucursal.id)  # dueña del SKU
        self.assertEqual(mov.costo, 15000)  # costo del producto (la línea venía en 0)
        self.assertEqual(mov.referencia_externa, dev.numero_operacion)
        lote = LoteProducto.objects.get(movimiento=mov)
        self.assertEqual(lote.cantidad_disponible, 1)
        self.assertEqual(lote.costo_unitario, 15000)
        # Resultado visible para la vista y persistido en las observaciones.
        self.assertEqual(dev.inventario['reingresos'][0]['sku'], self.pt.sku)
        self.assertEqual(dev.inventario['no_aptas'], [])
        self.assertIn('Reingresó a inventario', dev.observaciones_aprobacion)
        self.assertNotIn('NO APTO', dev.observaciones_aprobacion)
        # Y se puede reconstruir desde el kardex (detalle en otra sesión).
        inv = service.inventario_de_devolucion(dev)
        self.assertEqual(inv['reingresos'][0]['cantidad'], 1)
        self.assertEqual(inv['no_aptas'], [])

    def test_no_apto_marcado_por_el_solicitante_no_reingresa(self):
        boleta = _crear_documento(self.env, 6002, [(self.pt, 2, 11900)])
        dp = boleta.dte_productos.first()
        dev = self._solicitud(boleta, [{'dte_producto_id': dp.id, 'modo': 'CANTIDAD',
                                        'cantidad': 1, 'no_apto': True}])
        # La marca vive en el motivo, pero no se muestra.
        self.assertEqual(service.lineas_no_aptas_de(dev), (False, {dp.id}))
        self.assertEqual(service.motivo_limpio(dev.motivo), 'Garantía')

        dev, nc, _t, _w = self._aprobar(dev)

        self.assertEqual(self._stock(self.pt), 10)
        self.assertEqual(self._lotes(self.pt), 10)
        self.assertEqual(self._kardex_nc(nc, 'DEVOLUCION_NC'), [])
        movs = self._kardex_nc(nc, 'DEVOLUCION_NO_APTA')
        self.assertEqual(len(movs), 1)
        self.assertEqual(movs[0].cantidad, 0)
        self.assertEqual(movs[0].tipo_movimiento, 'AJUSTE')
        self.assertEqual(movs[0].sucursal_destino_id, self.sucursal.id)
        self.assertIn('NO APTA: 1 u.', movs[0].observaciones)
        self.assertIn(f'NC #{nc.numero_documento}', movs[0].observaciones)
        # La marca interna no llega a la NC (va al SII/TXT).
        self.assertNotIn('[NO_APTO', nc.motivo_nc)
        self.assertIn('Motivo: Garantía', nc.motivo_nc)
        self.assertEqual(dev.inventario['no_aptas'][0]['cantidad'], 1)
        self.assertIn('NO APTO, sin ingreso a stock', dev.observaciones_aprobacion)
        inv = service.inventario_de_devolucion(dev)
        self.assertEqual(inv['no_aptas'][0]['cantidad'], 1)
        self.assertEqual(inv['reingresos'], [])

    def test_aprobador_manda_sobre_la_marca_del_solicitante(self):
        boleta = _crear_documento(self.env, 6003, [(self.pt, 2, 11900)])
        dp = boleta.dte_productos.first()
        # El solicitante lo marcó no apto; el aprobador lo revisa y lo deja apto.
        dev = self._solicitud(boleta, [{'dte_producto_id': dp.id, 'modo': 'CANTIDAD', 'cantidad': 1}],
                              no_apto=True)
        self.assertEqual(service.lineas_no_aptas_de(dev), (True, set()))
        dev, nc, _t, _w = self._aprobar(dev, lineas_no_aptas=[])
        self.assertEqual(self._stock(self.pt), 11)
        self.assertEqual(len(self._kardex_nc(nc, 'DEVOLUCION_NC')), 1)

        # Y al revés: global no_apto=True al aprobar aunque nadie lo marcó antes.
        boleta2 = _crear_documento(self.env, 6004, [(self.pt, 2, 11900)])
        dev2 = self._solicitud(boleta2, [{'dte_producto_id': boleta2.dte_productos.first().id,
                                          'modo': 'CANTIDAD', 'cantidad': 2}])
        dev2, nc2, _t, _w = self._aprobar(dev2, no_apto=True)
        self.assertEqual(self._stock(self.pt), 11)
        self.assertEqual(len(self._kardex_nc(nc2, 'DEVOLUCION_NO_APTA')), 1)
        self.assertIn('NO APTA: 2 u.', self._kardex_nc(nc2, 'DEVOLUCION_NO_APTA')[0].observaciones)

    def test_modo_monto_no_mueve_inventario(self):
        from app.models import Movimientos_Producto
        boleta = _crear_documento(self.env, 6005, [(self.pt, 1, 39990)])
        dev = self._solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                        'modo': 'MONTO', 'monto': 10000}])
        dev, nc, _t, _w = self._aprobar(dev)
        self.assertEqual(self._stock(self.pt), 10)
        self.assertFalse(Movimientos_Producto.objects.filter(dte=nc).exists())
        self.assertEqual(dev.inventario, {'reingresos': [], 'no_aptas': [], 'sin_reingreso': [], 'avisos': []})
        self.assertEqual(dev.observaciones_aprobacion, '')

    def test_nc_posterior_de_gestion_dte_no_vuelve_a_reingresar_lo_devuelto(self):
        from app.services.reingreso_devolucion import ReingresoVenta
        boleta = _crear_documento(self.env, 6006, [(self.pt, 2, 11900)])
        self.assertEqual(ReingresoVenta(boleta).pendientes(self.pt.id, 2), 2)
        dev = self._solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                        'modo': 'CANTIDAD', 'cantidad': 1}])
        self._aprobar(dev)
        self.assertEqual(self._stock(self.pt), 11)
        # Gestión DTE solo podrá reingresar la unidad que falta.
        self.assertEqual(ReingresoVenta(boleta).pendientes(self.pt.id, 2), 1)

    def test_boleta_con_cambio_previo_reingresa_la_talla_entregada(self):
        """Vendida L (pt), cambiada por M (pt_m) en Cambios: la DG reingresa M
        (lo que el cliente trae), con lote, y avisa. L ya volvió con el cambio."""
        from app.models import (
            CambioDevolucion, CambioDevolucionDetalle, LoteProducto, Ticket, Ticket_Productos,
        )
        _, pt_m = crear_producto_con_talla(self.sucursal, articulo='Zapatilla Test', talla='43',
                                           sku=1000002, stock=10)
        self.crear_lote_fifo(pt_m, cantidad=10, costo_unitario=15000)
        vendedor = self.env['vendedor']

        def _ticket(correlativo, pt):
            t = Ticket.objects.create(
                vendedor=vendedor, sucursal=self.sucursal, correlativo=correlativo,
                estado='PAGADO', subTotal=20000, descuento=0, total=20000,
                responsable=self.user.username,
            )
            Ticket_Productos.objects.create(
                idTicket=t, ProductoTalla=pt, stock=1, precio=20000, precio_original=20000,
                descuento_unitario=0, subtotal=20000,
            )
            return t

        venta = _ticket(700, self.pt)
        boleta = _crear_documento(self.env, 6007, [(self.pt, 1, 20000)])
        boleta.referencias = f'TICKET-{venta.correlativo}'
        boleta.save(update_fields=['referencias'])
        cambio = CambioDevolucion.objects.create(
            ticket_original=venta, ticket_nuevo=_ticket(701, pt_m),
            sucursal=self.sucursal, tipo_operacion='CAMBIO_SIMPLE', estado='COMPLETADO',
            monto_original=20000, monto_nuevo=20000, motivo_principal='TALLA_INCORRECTA',
            solicitado_por=self.user, fecha_limite_cambio=self.hoy, fecha_ejecucion=timezone.now(),
        )
        CambioDevolucionDetalle.objects.create(
            cambio_devolucion=cambio, producto_original=venta.ticket_productos.get(),
            cantidad_original=1, producto_nuevo=pt_m, cantidad_nueva=1,
            precio_nuevo=20000, precio_original_unitario=20000,
            condicion_producto='PERFECTO', apto_para_venta=True,
        )

        dev = self._solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                        'modo': 'CANTIDAD', 'cantidad': 1}])
        dev, nc, _t, _w = self._aprobar(dev)

        self.assertEqual(self._stock(self.pt), 10)   # L no se duplica
        self.assertEqual(self._stock(pt_m), 11)      # vuelve la M del cliente
        self.assertEqual(self._lotes(pt_m), 11)
        movs = self._kardex_nc(nc, 'DEVOLUCION_NC')
        self.assertEqual([(m.ProductoTalla_id, m.cantidad) for m in movs], [(pt_m.id, 1)])
        self.assertTrue(LoteProducto.objects.filter(movimiento=movs[0]).exists())
        self.assertEqual(dev.inventario['reingresos'][0]['cambio'], cambio.numero_operacion)
        self.assertTrue(dev.inventario['avisos'])
        self.assertIn(cambio.numero_operacion, dev.observaciones_aprobacion)

        # Mismo caso NO APTO: el registro documental apunta a la M entregada.
        boleta2 = _crear_documento(self.env, 6008, [(self.pt, 1, 20000)])
        boleta2.referencias = f'TICKET-{venta.correlativo}'
        boleta2.save(update_fields=['referencias'])
        dev2 = self._solicitud(boleta2, [{'dte_producto_id': boleta2.dte_productos.first().id,
                                          'modo': 'CANTIDAD', 'cantidad': 1, 'no_apto': True}])
        dev2, nc2, _t, _w = self._aprobar(dev2)
        self.assertEqual(self._stock(pt_m), 11)
        doc = self._kardex_nc(nc2, 'DEVOLUCION_NO_APTA')
        self.assertEqual([(m.ProductoTalla_id, m.cantidad) for m in doc], [(pt_m.id, 0)])
        self.assertIn('se había cambiado', doc[0].observaciones)

    def test_endpoint_aprobar_recibe_lineas_no_aptas_y_devuelve_inventario(self):
        boleta = _crear_documento(self.env, 6009, [(self.pt, 2, 11900)])
        dp = boleta.dte_productos.first()
        dev = self._solicitud(boleta, [{'dte_producto_id': dp.id, 'modo': 'CANTIDAD', 'cantidad': 1}])
        modulo, _ = ModuloSistema.objects.get_or_create(codigo='ventas', defaults={'nombre': 'Ventas', 'orden': 2})
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo='devolucion_garantia', defaults={'modulo': modulo, 'nombre': 'DG', 'orden': 3})
        PermisoRol.objects.update_or_create(
            rol='administrador', opcion_menu=opcion, defaults={'puede_ver': True, 'puede_aprobar': True})
        user = crear_usuario(username='admin_inv', rol='administrador')
        client = Client()
        client.force_login(user)
        session = client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

        # El detalle expone qué líneas mueven stock y la propuesta del solicitante.
        det = client.get(reverse('api_detalle_solicitud_devolucion_garantia', args=[dev.id])).json()
        self.assertTrue(det['data']['lineas'][0]['mueve_stock'])
        self.assertFalse(det['data']['lineas'][0]['no_apto'])
        self.assertEqual(det['data']['lineas'][0]['dte_producto_id'], dp.id)

        import json as _json
        resp = client.post(
            reverse('api_aprobar_devolucion_garantia', args=[dev.id]),
            data=_json.dumps({'metodo_devolucion': 'NO_AFECTA_CAJA', 'lineas_no_aptas': [dp.id]}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        inv = resp.json()['data']['inventario']
        self.assertEqual(inv['reingresos'], [])
        self.assertEqual(inv['no_aptas'][0]['cantidad'], 1)
        self.assertEqual(self._stock(self.pt), 10)

    def test_endpoint_crear_persiste_no_apto_por_linea(self):
        boleta = _crear_documento(self.env, 6010, [(self.pt, 2, 11900)])
        dp = boleta.dte_productos.first()
        modulo = ModuloSistema.objects.create(codigo='ventas_inv', nombre='Ventas', orden=1)
        opcion = OpcionMenu.objects.create(
            modulo=modulo, codigo='devolucion_garantia', nombre='DG',
            url_name='modulo_devolucion_garantia', orden=1)
        PermisoRol.objects.create(rol='jefe_local', opcion_menu=opcion, puede_ver=True, puede_crear=True)
        user = crear_usuario(username='jefe_inv', rol='jefe_local')
        client = Client()
        client.force_login(user)
        session = client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()
        import json as _json
        resp = client.post(
            reverse('api_generar_devolucion_garantia'),
            data=_json.dumps({
                'folio_dte': boleta.numero_documento,
                'productos': [{'dte_producto_id': dp.id, 'modo': 'CANTIDAD', 'cantidad': 1, 'no_apto': True}],
                'rut': '13013448-3', 'nombre': 'Paola Tebes', 'metodo_solicitado': 'EFECTIVO_CAJA',
                'motivo': 'Suela despegada',
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        dev = DevolucionGarantia.objects.get(id=resp.json()['data']['devolucion_id'])
        self.assertEqual(service.lineas_no_aptas_de(dev), (False, {dp.id}))
        self.assertEqual(service.motivo_limpio(dev.motivo), 'Suela despegada')
        # El comprobante muestra el motivo limpio y marca la línea.
        td = client.get(reverse('api_ticket_devolucion_garantia', args=[dev.id])).json()['data']
        self.assertEqual(td['motivo'], 'Suela despegada')
        self.assertTrue(td['productos'][0]['no_apto'])

    def test_detalle_html_muestra_inventario_y_motivo_limpio(self):
        boleta = _crear_documento(self.env, 6011, [(self.pt, 2, 11900)])
        dev = self._solicitud(boleta, [{'dte_producto_id': boleta.dte_productos.first().id,
                                        'modo': 'CANTIDAD', 'cantidad': 1, 'no_apto': True}])
        self._aprobar(dev)
        from unittest import mock
        client = Client()
        client.force_login(self.user)
        session = client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()
        with mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True), \
                mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True):
            resp = client.get(reverse('detalle_devolucion_garantia', args=[dev.id]))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('No apto para la venta', html)
        self.assertIn('kardex documental DEVOLUCION_NO_APTA', html)
        self.assertNotIn('[NO_APTO', html)
