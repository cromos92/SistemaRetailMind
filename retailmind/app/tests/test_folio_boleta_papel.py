"""
Tests de la ventana de unicidad del folio de BOLETA PAPEL y de la
resincronización automática de los teóricos del arqueo.

Contexto
--------
1. **Folio de talonario.** La BOLETA PAPEL no consume folio CAF del SII: sale
   de un talonario físico cuya numeración se reinicia con cada talonario, así
   que el mismo número reaparece legítimamente año a año. Validar contra todo
   el historial (como se hace con los folios electrónicos) bloqueaba cargas
   correctas. Lo que sí es un error real es repetir el folio dentro del mismo
   período, así que la unicidad se exige en una ventana de
   ±`VENTANA_MESES_FOLIO_REUSABLE` meses alrededor de la fecha del documento.

2. **Teóricos del arqueo.** `ArqueoCaja.total_*_teorico` es un snapshot
   congelado al cerrar la caja. Anular un DTE o emitir una NC después dejaba
   el `Ef. Teórico` mostrando plata que ya no correspondía hasta que alguien
   lo recalculara a mano. `resincronizar_arqueos_por_fechas` lo re-snapshotea
   en el acto dejando traza en la bitácora.
"""
from datetime import timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import (
    ArqueoCaja, Dte, Dte_Detalle_Pago, Dte_Productos, ObservacionArqueo,
    Ticket, TicketDetallePago,
)
from app.utils_folio_dte import (
    VENTANA_MESES_FOLIO_REUSABLE,
    buscar_colisiones_folio,
    mensajes_por_colisiones,
    ventana_folio_reusable,
)
from app.views_modulo_ventas import (
    _validar_folio_destino_dte,
    resincronizar_arqueos_por_fechas,
)

from .factories import setup_entorno_completo

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


def _crear_dte(env, numero, tipo_documento, fecha, monto_con_iva=10000,
               estado_dte='EMITIDO', descartado=False):
    """DTE de venta mínimo, sin pagos ni líneas (basta para el chequeo de folio)."""
    monto_neto = int(round(monto_con_iva / Decimal('1.19')))
    return Dte.objects.create(
        emisor=env['empresa'],
        numero_documento=numero,
        tipo_documento=tipo_documento,
        monto_con_iva=monto_con_iva,
        monto_neto=monto_neto,
        descuento=0,
        estado_pago='PAGADO',
        estado_dte=estado_dte,
        responsable=env['user'].username,
        fecha_emision=fecha,
        fecha_vencimiento=fecha,
        diasCredito=0,
        bultos=0,
        unidades_productos=1,
        tipo_transaccion='VENTA_PUBLICO',
        sucursal=env['sucursal'],
        es_nota_credito=False,
        descartado=descartado,
        hora=timezone.localtime().time(),
    )


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class VentanaFolioReusableTest(TestCase):
    """La ventana existe sólo para los tipos de talonario físico."""

    def test_boleta_papel_tiene_ventana_simetrica(self):
        ref = timezone.localdate()
        ventana = ventana_folio_reusable('BOLETA PAPEL', ref)
        self.assertIsNotNone(ventana)
        desde, hasta = ventana
        delta = relativedelta(months=VENTANA_MESES_FOLIO_REUSABLE)
        self.assertEqual(desde, ref - delta)
        self.assertEqual(hasta, ref + delta)

    def test_tipos_con_folio_caf_no_tienen_ventana(self):
        """El folio electrónico es único para siempre: no hay ventana."""
        ref = timezone.localdate()
        for tipo in ('BOLETA ELECTRONICA', 'FACTURA ELECTRONICA',
                     'FACTURA EXENTA', 'NOTA DE CREDITO'):
            self.assertIsNone(
                ventana_folio_reusable(tipo, ref),
                f'{tipo} no debería tener ventana de reuso',
            )

    def test_sin_fecha_de_referencia_no_hay_ventana(self):
        """Sin fecha no se puede ubicar la ventana → se cae al criterio estricto."""
        self.assertIsNone(ventana_folio_reusable('BOLETA PAPEL', None))

    def test_normaliza_el_tipo(self):
        ref = timezone.localdate()
        self.assertIsNotNone(ventana_folio_reusable('  boleta papel  ', ref))


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class ColisionesFolioBoletaPapelTest(TestCase):
    """`buscar_colisiones_folio` clasifica cada choque dentro/fuera de ventana."""

    def setUp(self):
        self.env = setup_entorno_completo()
        self.hoy = timezone.localdate()
        # DTE "en edición": es el que se quiere dejar con el folio 5000.
        self.dte = _crear_dte(
            self.env, numero=777, tipo_documento='BOLETA ELECTRONICA',
            fecha=self.hoy,
        )

    def test_colision_dentro_de_ventana_se_marca_bloqueante(self):
        _crear_dte(
            self.env, numero=5000, tipo_documento='BOLETA PAPEL',
            fecha=self.hoy - timedelta(days=30),
        )
        colisiones = buscar_colisiones_folio(
            self.dte, 5000, tipo_destino='BOLETA PAPEL', fecha_ref=self.hoy,
        )
        self.assertEqual(len(colisiones), 1)
        self.assertTrue(colisiones[0]['dentro_ventana'])

    def test_colision_posterior_dentro_de_ventana_tambien_bloquea(self):
        """
        La ventana es simétrica: un duplicado cargado DESPUÉS de la fecha del
        documento es tan real como uno anterior. Con una ventana sólo "hacia
        atrás" este caso pasaba inadvertido.
        """
        _crear_dte(
            self.env, numero=5000, tipo_documento='BOLETA PAPEL',
            fecha=self.hoy + timedelta(days=20),
        )
        colisiones = buscar_colisiones_folio(
            self.dte, 5000, tipo_destino='BOLETA PAPEL', fecha_ref=self.hoy,
        )
        self.assertEqual(len(colisiones), 1)
        self.assertTrue(colisiones[0]['dentro_ventana'])

    def test_colision_fuera_de_ventana_no_es_bloqueante(self):
        """Talonario anterior: mismo número, 8 meses antes. Sólo se informa."""
        _crear_dte(
            self.env, numero=5000, tipo_documento='BOLETA PAPEL',
            fecha=self.hoy - relativedelta(months=8),
        )
        colisiones = buscar_colisiones_folio(
            self.dte, 5000, tipo_destino='BOLETA PAPEL', fecha_ref=self.hoy,
        )
        self.assertEqual(len(colisiones), 1)
        self.assertFalse(colisiones[0]['dentro_ventana'])

    def test_folio_electronico_siempre_es_bloqueante(self):
        """Sin ventana, cualquier antigüedad choca (folio CAF, único para siempre)."""
        _crear_dte(
            self.env, numero=5000, tipo_documento='BOLETA ELECTRONICA',
            fecha=self.hoy - relativedelta(years=4),
        )
        colisiones = buscar_colisiones_folio(
            self.dte, 5000, tipo_destino='BOLETA ELECTRONICA', fecha_ref=self.hoy,
        )
        self.assertEqual(len(colisiones), 1)
        self.assertTrue(colisiones[0]['dentro_ventana'])

    def test_dte_descartado_no_cuenta_como_colision(self):
        _crear_dte(
            self.env, numero=5000, tipo_documento='BOLETA PAPEL',
            fecha=self.hoy, descartado=True,
        )
        colisiones = buscar_colisiones_folio(
            self.dte, 5000, tipo_destino='BOLETA PAPEL', fecha_ref=self.hoy,
        )
        self.assertEqual(colisiones, [])

    def test_mensajes_separan_error_de_advertencia(self):
        _crear_dte(
            self.env, numero=5000, tipo_documento='BOLETA PAPEL',
            fecha=self.hoy - timedelta(days=10),
        )
        _crear_dte(
            self.env, numero=5000, tipo_documento='BOLETA PAPEL',
            fecha=self.hoy - relativedelta(months=10),
        )
        colisiones = buscar_colisiones_folio(
            self.dte, 5000, tipo_destino='BOLETA PAPEL', fecha_ref=self.hoy,
        )
        errores, advertencias = mensajes_por_colisiones(
            self.dte, 5000, colisiones,
            tipo_destino='BOLETA PAPEL', fecha_ref=self.hoy,
        )
        self.assertEqual(len(errores), 1)
        self.assertEqual(len(advertencias), 1)
        self.assertIn(str(VENTANA_MESES_FOLIO_REUSABLE), errores[0])
        self.assertIn('talonario anterior', advertencias[0])


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class ValidarFolioDestinoBoletaPapelTest(TestCase):
    """
    `_validar_folio_destino_dte` es lo que corre al convertir una BOLETA
    ELECTRONICA en BOLETA PAPEL con folio digitado a mano.
    """

    def setUp(self):
        self.env = setup_entorno_completo()
        self.hoy = timezone.localdate()
        self.dte = _crear_dte(
            self.env, numero=412865, tipo_documento='BOLETA ELECTRONICA',
            fecha=self.hoy,
        )

    def test_folio_libre_pasa(self):
        ok, error, advertencias = _validar_folio_destino_dte(
            self.dte, 'BOLETA PAPEL', 3210, fecha_ref=self.hoy,
        )
        self.assertTrue(ok, error)
        self.assertEqual(error, '')
        self.assertEqual(advertencias, [])

    def test_folio_usado_dentro_de_ventana_se_rechaza(self):
        _crear_dte(
            self.env, numero=3210, tipo_documento='BOLETA PAPEL',
            fecha=self.hoy - relativedelta(months=1),
        )
        ok, error, _ = _validar_folio_destino_dte(
            self.dte, 'BOLETA PAPEL', 3210, fecha_ref=self.hoy,
        )
        self.assertFalse(ok)
        self.assertIn('3210', error)

    def test_folio_usado_fuera_de_ventana_pasa_con_advertencia(self):
        """El talonario reinicia numeración: reusar el número es legítimo."""
        _crear_dte(
            self.env, numero=3210, tipo_documento='BOLETA PAPEL',
            fecha=self.hoy - relativedelta(months=9),
        )
        ok, error, advertencias = _validar_folio_destino_dte(
            self.dte, 'BOLETA PAPEL', 3210, fecha_ref=self.hoy,
        )
        self.assertTrue(ok, error)
        self.assertEqual(len(advertencias), 1)
        self.assertIn('3210', advertencias[0])

    def test_ventana_se_centra_en_la_fecha_destino_no_en_la_actual(self):
        """
        Si la misma edición mueve la `fecha_emision`, la ventana tiene que
        mirar el período DESTINO. Con la fecha vieja se dejaba pasar un folio
        que sí choca en el período al que el documento se está moviendo.
        """
        fecha_destino = self.hoy - relativedelta(months=9)
        _crear_dte(
            self.env, numero=3210, tipo_documento='BOLETA PAPEL',
            fecha=fecha_destino - timedelta(days=15),
        )
        # Con la fecha actual del DTE (hoy) el choque queda fuera de ventana.
        ok_hoy, _, adv_hoy = _validar_folio_destino_dte(
            self.dte, 'BOLETA PAPEL', 3210, fecha_ref=self.hoy,
        )
        self.assertTrue(ok_hoy)
        self.assertEqual(len(adv_hoy), 1)
        # Con la fecha a la que se va a mover, el choque bloquea.
        ok_destino, error_destino, _ = _validar_folio_destino_dte(
            self.dte, 'BOLETA PAPEL', 3210, fecha_ref=fecha_destino,
        )
        self.assertFalse(ok_destino)
        self.assertIn('3210', error_destino)

    def test_conversion_a_boleta_electronica_sigue_siendo_estricta(self):
        """No regresión: el folio electrónico choca aunque sea de hace años."""
        papel = _crear_dte(
            self.env, numero=900, tipo_documento='BOLETA PAPEL', fecha=self.hoy,
        )
        _crear_dte(
            self.env, numero=4321, tipo_documento='BOLETA ELECTRONICA',
            fecha=self.hoy - relativedelta(years=3),
        )
        ok, error, _ = _validar_folio_destino_dte(
            papel, 'BOLETA ELECTRONICA', 4321, fecha_ref=self.hoy,
        )
        self.assertFalse(ok)
        self.assertIn('4321', error)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class ResincronizarArqueosTest(TestCase):
    """
    El `Ef. Teórico` del arqueo deja de ser un snapshot muerto: un hecho
    posterior al cierre (anulación, NC) lo actualiza.
    """

    def setUp(self):
        self.env = setup_entorno_completo()
        self.hoy = timezone.localdate()

    def _crear_ticket_efectivo(self, correlativo, monto, fecha=None):
        ticket = Ticket.objects.create(
            sucursal=self.env['sucursal'],
            correlativo=correlativo,
            subTotal=monto,
            total=monto,
            estado='PAGADO',
            vendedor=self.env['vendedor'],
            responsable='Test',
        )
        # `Ticket.fecha` es auto_now: hay que forzarla con update().
        Ticket.objects.filter(pk=ticket.pk).update(fecha=fecha or self.hoy)
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='EFECTIVO', monto=monto,
        )
        ticket.refresh_from_db()
        return ticket

    def _crear_arqueo(self, efectivo_teorico, fecha=None):
        arqueo = ArqueoCaja.objects.create(
            fecha_arqueo=fecha or self.hoy,
            sucursal=self.env['sucursal'],
            usuario_responsable=self.env['user'],
            estado='CERRADO',
        )
        # `save()` recalcula el físico desde las denominaciones; los teóricos
        # se escriben con update() igual que en producción.
        ArqueoCaja.objects.filter(pk=arqueo.pk).update(
            total_efectivo_teorico=efectivo_teorico,
        )
        arqueo.refresh_from_db()
        return arqueo

    def test_actualiza_el_teorico_congelado(self):
        """Arqueo cerrado con teórico viejo → se re-snapshotea al valor real."""
        self._crear_ticket_efectivo(correlativo=1, monto=50000)
        arqueo = self._crear_arqueo(efectivo_teorico=122460)

        resultado = resincronizar_arqueos_por_fechas(
            {self.hoy}, self.env['sucursal'].id,
            usuario=self.env['user'], razon='test',
        )

        arqueo.refresh_from_db()
        self.assertEqual(arqueo.total_efectivo_teorico, 50000)
        self.assertEqual(len(resultado), 1)
        self.assertTrue(resultado[0]['recalculado'])
        self.assertEqual(
            resultado[0]['cambios']['total_efectivo_teorico'],
            {'antes': 122460, 'despues': 50000},
        )

    def test_recalcula_la_diferencia_de_conteo(self):
        """`diferencia_efectivo` = físico - (teórico + fondo fijo)."""
        self._crear_ticket_efectivo(correlativo=2, monto=40000)
        arqueo = self._crear_arqueo(efectivo_teorico=100000)
        ArqueoCaja.objects.filter(pk=arqueo.pk).update(
            total_efectivo_fisico=40000,
        )

        resincronizar_arqueos_por_fechas(
            {self.hoy}, self.env['sucursal'].id, usuario=self.env['user'],
        )

        arqueo.refresh_from_db()
        self.assertEqual(arqueo.total_efectivo_teorico, 40000)
        self.assertEqual(arqueo.diferencia_efectivo, 0)

    def test_deja_traza_en_la_bitacora(self):
        """El cambio automático de un arqueo cerrado tiene que quedar auditado."""
        self._crear_ticket_efectivo(correlativo=3, monto=15000)
        arqueo = self._crear_arqueo(efectivo_teorico=99000)

        resincronizar_arqueos_por_fechas(
            {self.hoy}, self.env['sucursal'].id,
            usuario=self.env['user'],
            razon='anulación de BOLETA ELECTRONICA #258944',
        )

        obs = ObservacionArqueo.objects.filter(arqueo=arqueo, tipo='SISTEMA')
        self.assertEqual(obs.count(), 1)
        texto = obs.first().texto
        self.assertIn('anulación de BOLETA ELECTRONICA #258944', texto)
        self.assertIn('99,000', texto.replace('.', ','))

    def test_es_idempotente_y_no_ensucia_la_bitacora(self):
        """
        Correr el recálculo dos veces seguidas sin que nada cambie en medio
        debe ser un no-op: ni reescribe teóricos ni agrega observaciones. Es
        lo que evita que cada anulación/NC llene la bitácora de ruido cuando
        el arqueo ya está alineado.
        """
        self._crear_ticket_efectivo(correlativo=4, monto=25000)
        arqueo = self._crear_arqueo(efectivo_teorico=25000)

        # Primera pasada: alinea el resto de los teóricos (tickets, cantidades,
        # venta total) que el fixture dejó en 0.
        primera = resincronizar_arqueos_por_fechas(
            {self.hoy}, self.env['sucursal'].id, usuario=self.env['user'],
        )
        self.assertTrue(primera[0]['recalculado'])
        # El efectivo ya estaba bien: no aparece entre los cambios.
        self.assertNotIn('total_efectivo_teorico', primera[0]['cambios'])
        obs_tras_primera = ObservacionArqueo.objects.filter(arqueo=arqueo).count()

        # Segunda pasada: nada que hacer.
        segunda = resincronizar_arqueos_por_fechas(
            {self.hoy}, self.env['sucursal'].id, usuario=self.env['user'],
        )
        self.assertEqual(len(segunda), 1)
        self.assertFalse(segunda[0]['recalculado'])
        self.assertEqual(segunda[0]['cambios'], {})
        self.assertEqual(
            ObservacionArqueo.objects.filter(arqueo=arqueo).count(),
            obs_tras_primera,
        )

    def test_no_toca_arqueos_de_otras_fechas_ni_sucursales(self):
        otra_fecha = self.hoy - timedelta(days=5)
        arqueo_otro_dia = self._crear_arqueo(
            efectivo_teorico=88000, fecha=otra_fecha,
        )
        self._crear_ticket_efectivo(correlativo=5, monto=10000)
        self._crear_arqueo(efectivo_teorico=1)

        resincronizar_arqueos_por_fechas(
            {self.hoy}, self.env['sucursal'].id, usuario=self.env['user'],
        )

        arqueo_otro_dia.refresh_from_db()
        self.assertEqual(arqueo_otro_dia.total_efectivo_teorico, 88000)

    def test_sin_sucursal_no_hace_nada(self):
        self.assertEqual(
            resincronizar_arqueos_por_fechas({self.hoy}, None), [],
        )

    def test_fechas_vacias_no_hace_nada(self):
        self.assertEqual(
            resincronizar_arqueos_por_fechas(
                {None}, self.env['sucursal'].id,
            ),
            [],
        )

    def test_nc_de_devolucion_baja_el_teorico_del_arqueo(self):
        """
        El caso real: caja cerrada con $50.000 teóricos y después se emite
        una NC de devolución en efectivo por $8.000. El arqueo tiene que
        quedar en $42.000 sin intervención manual.
        """
        self._crear_ticket_efectivo(correlativo=6, monto=50000)
        arqueo = self._crear_arqueo(efectivo_teorico=50000)

        venta = _crear_dte(
            self.env, numero=1500, tipo_documento='BOLETA ELECTRONICA',
            fecha=self.hoy, monto_con_iva=50000,
        )
        Dte_Productos.objects.create(
            dte=venta, productoTalla=self.env['producto_talla'],
            descripcion='Producto Test', costo=0, sobreprecio=0,
            precio=50000, stock=1, activo=True,
        )
        nc = _crear_dte(
            self.env, numero=9500, tipo_documento='NOTA DE CREDITO',
            fecha=self.hoy, monto_con_iva=8000,
        )
        nc.tipo_transaccion = 'DEVOLUCION'
        nc.es_nota_credito = True
        nc.documento_afectado = venta
        nc.save(update_fields=[
            'tipo_transaccion', 'es_nota_credito', 'documento_afectado',
        ])
        Dte_Detalle_Pago.objects.create(
            dte=nc, metodo_pago='EFECTIVO', monto=8000, fecha_pago=self.hoy,
        )

        resincronizar_arqueos_por_fechas(
            {self.hoy}, self.env['sucursal'].id,
            usuario=self.env['user'], razon='NC #9500',
        )

        arqueo.refresh_from_db()
        self.assertEqual(arqueo.total_efectivo_teorico, 42000)
