"""
Tests de la reapertura de cotizaciones cuyo documento tributario ya no existe,
más los guards y correcciones asociados.

Contexto: el módulo de cotizaciones NO factura (lo hace el POS al cobrar), así
que cuando el DTE se elimina o se anula por NC total la cotización queda
marcada FACTURADA apuntando a un documento muerto — y desde ahí no se podía
re-facturar, ni editar, ni anular. Caso real: una cotización facturada por error
con BOLETA PAPEL que había que re-emitir como FACTURA ELECTRONICA.

Cubre:
  * `reabrir_cotizacion` y sus guards de inventario.
  * El guard de "documento vivo" en `asignar_sku_pendiente` (y que la reversa
    NO lo exige, porque es el paso previo obligatorio para reabrir).
  * Reversa SELECTIVA de un solo despacho post-factura.
  * Cobertura parcial: el POS ya no carga más unidades que las respaldadas.
  * `DESPACHO_COTIZACION` clasificado como venta y no como pérdida.

Correr en BD local desechable:
    python manage.py test app.tests.test_cotizaciones_reapertura
"""
import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from app.models import (
    Cotizacion_Empresa, Cotizacion_Empresa_Detalle, Cotizacion_Empresa_Detalle_SKU,
    Dte, Historial_Cotizacion, Ticket, Ticket_Productos,
)

from .test_cotizaciones_despacho import DespachoDiferidoBase


class ReaperturaBase(DespachoDiferidoBase):
    """Mismo entorno que el despacho diferido, con el usuario como
    administrador: reabrir una cotización facturada es una corrección sobre el
    árbol documental y solo la puede hacer un administrador."""

    def setUp(self):
        super().setUp()
        self.user.rol = 'administrador'
        self.user.save(update_fields=['rol'])
        # El permiso es por ROL, así que hay que concederlo de nuevo para el rol
        # nuevo: el middleware gatea los endpoints de escritura de cotizaciones.
        self._dar_permiso_cotizaciones(rol='administrador')

    def _reabrir(self, motivo='Facturada por error con boleta de papel', dias=15):
        return self.client.post(
            '/app/api/cotizaciones/reabrir/',
            data=json.dumps({
                'cotizacion_id': self.cotizacion.id,
                'motivo': motivo,
                'dias_validez': dias,
            }),
            content_type='application/json',
        )

    def _revertir(self, motivo='SKU equivocado en la tanda', sku_id=None):
        cuerpo = {'detalle_id': self.detalle.id, 'motivo': motivo}
        if sku_id is not None:
            cuerpo['sku_id'] = sku_id
        return self.client.post(
            '/app/api/cotizaciones/revertir-sku-despachado/',
            data=json.dumps(cuerpo),
            content_type='application/json',
        )

    def _matar_documento(self, descartar=True):
        """Deja el DTE como lo deja `eliminar_documento_venta`."""
        if descartar:
            self.dte.descartado = True
        self.dte.estado_dte = 'ANULADO'
        self.dte.save(update_fields=['descartado', 'estado_dte'])

    def _vencer_cotizacion(self):
        """Fuerza la validez al pasado con UPDATE, sin pasar por save()
        (que recalcularía el estado)."""
        Cotizacion_Empresa.objects.filter(pk=self.cotizacion.pk).update(
            fecha_validez=timezone.localdate() - timedelta(days=30)
        )
        self.cotizacion.refresh_from_db()


class ReaperturaCotizacionTest(ReaperturaBase):

    def test_no_reabre_si_el_documento_sigue_vivo(self):
        """El guard central: con el DTE EMITIDO, reabrir permitiría facturar dos
        veces la misma mercadería."""
        resp = self._reabrir()
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertIn('sigue vigente', ' '.join(data['bloqueos']).lower())

        self.cotizacion.refresh_from_db()
        self.assertTrue(self.cotizacion.facturada)
        self.assertEqual(self.cotizacion.estado, Cotizacion_Empresa.ESTADO_FACTURADA)

    def test_no_reabre_con_despachos_diferidos_sin_revertir(self):
        """Si ya salió stock por despacho diferido, reabrir dejaría mercadería
        entregada sin documento que la respalde."""
        self._asignar(self.producto_talla, 2)
        self._matar_documento()

        resp = self._reabrir()
        self.assertEqual(resp.status_code, 400)
        bloqueos = ' '.join(resp.json()['bloqueos']).lower()
        self.assertIn('despacho diferido', bloqueos)

        self.cotizacion.refresh_from_db()
        self.assertTrue(self.cotizacion.facturada)

    def test_reabre_cuando_el_documento_murio_y_no_hay_stock_afuera(self):
        """Camino feliz: DTE eliminado y sin despachos → vuelve a VIGENTE,
        suelta el vínculo con el documento muerto y renueva la validez."""
        self._matar_documento()
        self._vencer_cotizacion()

        resp = self._reabrir()
        data = resp.json()
        self.assertTrue(data['success'], data)
        self.assertTrue(data['validez_renovada'])

        self.cotizacion.refresh_from_db()
        self.assertFalse(self.cotizacion.facturada)
        self.assertEqual(self.cotizacion.estado, Cotizacion_Empresa.ESTADO_VIGENTE)
        self.assertIsNone(self.cotizacion.numero_factura)
        self.assertIsNone(self.cotizacion.dte_id)
        self.assertIsNone(self.cotizacion.estado_despacho)
        self.assertFalse(self.cotizacion.despacho_validado)
        self.assertGreaterEqual(self.cotizacion.fecha_validez, timezone.localdate())
        # Lo que importa de verdad: vuelve a ser facturable desde el POS.
        self.assertTrue(self.cotizacion.esta_vigente)

        self.assertTrue(
            Historial_Cotizacion.objects
            .filter(cotizacion=self.cotizacion, accion='MODIFICADA',
                    descripcion__icontains='REABIERTA')
            .exists()
        )

    def test_reabre_cotizacion_zombi_sin_dte(self):
        """FACTURADA sin DTE enlazado: también tiene que poder reabrirse."""
        Cotizacion_Empresa.objects.filter(pk=self.cotizacion.pk).update(dte=None)
        self.cotizacion.refresh_from_db()

        resp = self._reabrir(motivo='Zombi sin documento tributario real')
        self.assertTrue(resp.json()['success'], resp.json())

        self.cotizacion.refresh_from_db()
        self.assertEqual(self.cotizacion.estado, Cotizacion_Empresa.ESTADO_VIGENTE)
        self.assertFalse(self.cotizacion.facturada)

    def test_reabrir_deja_la_cotizacion_editable_y_anulable(self):
        """Las tres puertas cerradas se vuelven a abrir de una sola vez."""
        self._matar_documento()
        self.assertTrue(self._reabrir().json()['success'])

        resp = self.client.post(
            '/app/api/cotizaciones/anular/',
            data=json.dumps({'cotizacion_id': self.cotizacion.id,
                             'motivo': 'El cliente desistio'}),
            content_type='application/json',
        )
        self.assertTrue(resp.json()['success'], resp.json())
        self.cotizacion.refresh_from_db()
        self.assertEqual(self.cotizacion.estado, Cotizacion_Empresa.ESTADO_ANULADA)

    def test_motivo_corto_rechazado(self):
        self._matar_documento()
        resp = self._reabrir(motivo='no')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

        self.cotizacion.refresh_from_db()
        self.assertTrue(self.cotizacion.facturada)

    def test_solo_administrador(self):
        self._matar_documento()
        self.user.rol = 'vendedor'
        self.user.save(update_fields=['rol'])

        resp = self._reabrir()
        self.assertEqual(resp.status_code, 403)

        self.cotizacion.refresh_from_db()
        self.assertTrue(self.cotizacion.facturada)

    def test_listado_marca_documento_muerto_y_habilita_reabrir(self):
        self._matar_documento()
        resp = self.client.get('/app/api/cotizaciones/')
        cot = next(
            c for c in resp.json()['cotizaciones']
            if c['numero_cotizacion'] == 'COT-TEST-0001'
        )
        self.assertTrue(cot['documento_muerto'])
        self.assertTrue(cot['documento_descartado'])
        self.assertTrue(cot['puede_reabrir'])

    def test_listado_no_habilita_reabrir_con_documento_vivo(self):
        resp = self.client.get('/app/api/cotizaciones/')
        cot = next(
            c for c in resp.json()['cotizaciones']
            if c['numero_cotizacion'] == 'COT-TEST-0001'
        )
        self.assertFalse(cot['documento_muerto'])
        self.assertFalse(cot['puede_reabrir'])


class GuardDocumentoMuertoTest(ReaperturaBase):

    def test_no_se_puede_despachar_contra_un_dte_eliminado(self):
        """Sin este guard se sacaba stock 'para completar' un documento que ya
        no existe."""
        self._matar_documento()

        resp = self._asignar(self.producto_talla, 2)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('ELIMINADO', resp.json()['error'])

        self.producto_talla.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 10)

    def test_no_se_puede_despachar_contra_un_dte_anulado(self):
        self._matar_documento(descartar=False)

        resp = self._asignar(self.producto_talla, 2)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('ANULADO', resp.json()['error'])

        self.producto_talla.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 10)

    def test_revertir_si_funciona_con_dte_eliminado(self):
        """La reversa NO exige documento vivo a propósito: es el paso
        obligatorio antes de reabrir, así que exigirlo trabaría esa mercadería
        para siempre."""
        self._asignar(self.producto_talla, 2)
        self._matar_documento()

        resp = self._revertir(motivo='Se elimino el documento, se re-emite')
        self.assertTrue(resp.json()['success'], resp.json())

        self.producto_talla.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 10)

        # Y con el stock de vuelta, ahora sí se puede reabrir: es la secuencia
        # completa de recuperación.
        self.assertTrue(self._reabrir().json()['success'])


class ReversaSelectivaTest(ReaperturaBase):

    def test_revertir_un_solo_sku_deja_vivo_el_otro(self):
        """Dos tandas con SKUs distintos: corregir una no debe deshacer la otra.
        Antes la reversa era todo-o-nada por ítem y había que rehacer el
        despacho que estaba bien."""
        self._asignar(self.producto_talla, 2)    # SKU 1 x2
        self._asignar(self.producto_talla2, 3)   # SKU 2 x3 → ítem completo

        self.detalle.refresh_from_db()
        self.assertEqual(self.detalle.unidades_pendientes_despacho, 0)

        fila_mala = Cotizacion_Empresa_Detalle_SKU.objects.get(
            detalle=self.detalle,
            producto_talla=self.producto_talla2,
            asignado_post_factura=True,
        )

        resp = self._revertir(
            motivo='Se despacho el SKU equivocado en la segunda tanda',
            sku_id=fila_mala.id,
        )
        self.assertTrue(resp.json()['success'], resp.json())

        # Solo el SKU 2 volvió a stock
        self.producto_talla.refresh_from_db(fields=['stock'])
        self.producto_talla2.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 8)   # sigue despachado
        self.assertEqual(self.producto_talla2.stock, 5)  # reintegrado

        # El despacho bueno sobrevive y el saldo es solo lo revertido
        self.detalle.refresh_from_db()
        self.assertEqual(self.detalle.unidades_despachadas_post_factura, 2)
        self.assertEqual(self.detalle.unidades_pendientes_despacho, 3)
        self.assertTrue(
            Cotizacion_Empresa_Detalle_SKU.objects.filter(
                detalle=self.detalle,
                producto_talla=self.producto_talla,
                asignado_post_factura=True,
            ).exists()
        )

        # La línea del DTE vuelve a esperar: se completa recién al cerrar todo.
        self.linea_dte.refresh_from_db()
        self.assertTrue(self.linea_dte.es_pendiente_despacho)

    def test_reversa_total_sigue_funcionando_sin_sku_id(self):
        """Compatibilidad: sin `sku_id` se revierte todo el ítem, como antes."""
        self._asignar(self.producto_talla, 2)
        self._asignar(self.producto_talla2, 3)

        self.assertTrue(self._revertir().json()['success'])

        self.producto_talla.refresh_from_db(fields=['stock'])
        self.producto_talla2.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 10)
        self.assertEqual(self.producto_talla2.stock, 5)

        self.detalle.refresh_from_db()
        self.assertTrue(self.detalle.es_producto_pendiente)
        self.assertEqual(self.detalle.unidades_pendientes_despacho, 5)

    def test_sku_id_inexistente_es_rechazado(self):
        self._asignar(self.producto_talla, 2)

        resp = self._revertir(motivo='fila que no existe', sku_id=999999)
        self.assertEqual(resp.status_code, 400)

        self.producto_talla.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 8)


class CoberturaParcialTest(DespachoDiferidoBase):
    """El POS ya no carga más unidades que las respaldadas por el SKU.

    Caso real en producción (COT-202607-0001): una línea de 5 unidades con UNA
    sola fila de SKU en cantidad 1. El POS descontaba 5 de ese SKU y el módulo
    igual ofrecía despachar las 4 restantes: las mismas unidades salían dos
    veces.
    """

    def setUp(self):
        super().setUp()
        # Cotización VIGENTE aparte, con cobertura parcial (1 de 5 respaldada).
        # `_validar_cobertura_skus` ya impide crearla así por la API, pero en la
        # base hay datos históricos con esta forma.
        hoy = timezone.localdate()
        self.cot_parcial = Cotizacion_Empresa.objects.create(
            sucursal=self.sucursal,
            cliente=self.empresa,
            vendedor=self.vendedor,
            usuario_creador=self.user,
            numero_cotizacion='COT-TEST-PARCIAL',
            fecha_emision=hoy,
            fecha_validez=hoy + timedelta(days=15),
            total=100000,
        )
        self.item_parcial = Cotizacion_Empresa_Detalle.objects.create(
            cotizacion=self.cot_parcial,
            numero_linea=1,
            descripcion='Balon futbol N5',
            cantidad=5,
            precio_unitario=20000,
            subtotal=100000,
        )
        Cotizacion_Empresa_Detalle_SKU.objects.create(
            detalle=self.item_parcial,
            producto_talla=self.producto_talla,
            cantidad=1,
            costo_unitario=15000,
            precio_unitario=20000,
            asignado_post_factura=False,
        )

    def test_el_pos_recibe_solo_las_unidades_respaldadas(self):
        resp = self.client.get(
            f'/app/api/cotizaciones/cargar-como-ticket/{self.cot_parcial.id}/'
        )
        data = resp.json()
        self.assertTrue(data['success'], data)

        productos = data['ticket']['productos']
        con_sku = [p for p in productos if p.get('sku')]
        pendientes = [p for p in productos if p.get('es_pendiente_despacho')]

        # La línea del SKU lleva 1, no 5.
        self.assertEqual(len(con_sku), 1)
        self.assertEqual(con_sku[0]['cantidad'], 1)

        # Las 4 sin respaldo se facturan como despacho diferido.
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(pendientes[0]['cantidad'], 4)
        self.assertTrue(data['tiene_pendientes'])

        # Y el total de unidades sigue siendo el cotizado.
        self.assertEqual(sum(p['cantidad'] for p in productos), 5)

    def test_preflight_valida_stock_por_la_cantidad_de_la_fila(self):
        """El pre-flight replicaba el bug: exigía stock del primer SKU por la
        cantidad COMPLETA del ítem. Ahora pide lo que realmente va a salir."""
        from app.views_modulo_cotizaciones import evaluar_items_cotizacion

        # Stock 10 > 1 requerido: no debe reportar falta de stock.
        ev = evaluar_items_cotizacion(self.cot_parcial, self.sucursal.id)
        self.assertEqual(ev['items_sin_stock'], 0)
        self.assertEqual(ev['items_cobertura_parcial'], 1)


class EliminarDocumentoConTicketDefaultTest(ReaperturaBase):
    """El ticket con `tipo_dte='TICKET'` (el default, 100% de los casos reales)
    NO debe bloquear la eliminacion del documento."""

    def _crear_papel(self, folio, tipo_dte_ticket=None, con_linea=True):
        papel = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=folio, tipo_documento='BOLETA PAPEL',
            monto_con_iva=29990, monto_neto=25202, descuento=0,
            estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable=self.user.username,
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(), diasCredito=0,
            bultos=1, unidades_productos=1, tipo_transaccion='VENTA_PUBLICO',
            sucursal=self.sucursal, hora=timezone.localtime().time(),
        )
        kwargs = {}
        if tipo_dte_ticket is not None:
            kwargs['tipo_dte'] = tipo_dte_ticket
        ticket = Ticket.objects.create(
            sucursal=self.sucursal, vendedor=self.vendedor,
            correlativo=folio, subTotal=29990, total=29990,
            estado='PAGADO', folio_dte=folio,
            responsable=self.user.username, fecha=timezone.localdate(),
            hora=timezone.localtime().time(),
            **kwargs
        )
        if con_linea:
            Ticket_Productos.objects.create(
                idTicket=ticket, ProductoTalla=self.producto_talla,
                stock=1, precio=29990, subtotal=29990,
            )
        return papel, ticket

    def _eliminar(self, dte_id, motivo='Facturada por error con boleta de papel'):
        return self.client.post(
            '/app/api/ventas/eliminar-documento/',
            data=json.dumps({'documento_id': dte_id, 'motivo': motivo}),
            content_type='application/json',
        )

    def test_eliminar_boleta_papel_con_ticket_tipo_default(self):
        papel, ticket = self._crear_papel(446579)
        self.assertEqual(ticket.tipo_dte, 'TICKET')  # el default del modelo

        resp = self._eliminar(papel.id)
        data = resp.json()
        self.assertTrue(data['success'], data)
        self.assertFalse(data.get('ambiguedad_ticket'))
        # Resolvio el ticket correcto y devolvio su stock
        self.assertTrue(data['ticket_anulado'])
        self.assertEqual(data['ticket_id'], ticket.id)
        self.assertEqual(len(data['stock_devuelto']), 1)

        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, 'ANULADO')
        papel.refresh_from_db()
        self.assertTrue(papel.descartado)
        self.assertEqual(papel.estado_dte, 'ANULADO')

        self.producto_talla.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 11)  # 10 + 1 devuelta

    def test_ticket_marcado_de_otra_serie_si_bloquea(self):
        """Si alguien marco el ticket como de otra serie, el guard frena."""
        papel, _ = self._crear_papel(446580, tipo_dte_ticket='FACTURA_ELECTRONICA')

        resp = self._eliminar(papel.id, motivo='prueba de serie cruzada')
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertTrue(data['ambiguedad_ticket'])

        papel.refresh_from_db()
        self.assertFalse(papel.descartado)
        self.producto_talla.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 10)  # nada se movio

    def test_dos_tickets_con_el_mismo_folio_abortan(self):
        """Ambos con el default: no hay forma de desambiguar -> 409."""
        papel, _ = self._crear_papel(446581)
        Ticket.objects.create(
            sucursal=self.sucursal, vendedor=self.vendedor,
            correlativo=999999, subTotal=29990, total=29990,
            estado='PAGADO', folio_dte=446581,
            responsable=self.user.username, fecha=timezone.localdate(),
            hora=timezone.localtime().time(),
        )

        resp = self._eliminar(papel.id, motivo='dos tickets con el mismo folio')
        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertTrue(data['ambiguedad_ticket'])
        self.assertEqual(len(data['tickets_candidatos']), 2)

        papel.refresh_from_db()
        self.assertFalse(papel.descartado)
        self.producto_talla.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 10)


class ClasificacionKardexTest(TestCase):

    def test_despacho_cotizacion_cuenta_como_venta(self):
        """Es mercadería facturada, cobrada y entregada. Estaba en
        CONCEPTOS_PERDIDA (junto a robo y deterioro) y ausente de
        CONCEPTOS_VENTA: ningún reporte de venta la contaba y la predicción de
        compras la leía como merma."""
        from app.constants_kardex import CONCEPTOS_VENTA, CONCEPTOS_PERDIDA
        self.assertIn('DESPACHO_COTIZACION', CONCEPTOS_VENTA)
        self.assertNotIn('DESPACHO_COTIZACION', CONCEPTOS_PERDIDA)

    def test_copias_locales_sincronizadas(self):
        """Hay listas de conceptos de venta hardcodeadas fuera de
        constants_kardex: si alguna se queda atrás, ese módulo sigue sin contar
        el despacho diferido."""
        from app.services.prediccion_compras import CONCEPTOS_VENTA as CV_PRED
        from app.views_prediccion_compras import CONCEPTOS_VENTA_ANALITICA
        from app.views_modulo_reportes import CONCEPTOS_VENTA as CV_REP

        for nombre, lista in [
            ('services.prediccion_compras', CV_PRED),
            ('views_prediccion_compras', CONCEPTOS_VENTA_ANALITICA),
            ('views_modulo_reportes', CV_REP),
        ]:
            self.assertIn('DESPACHO_COTIZACION', lista, nombre)


class TipoTicketPorTipoDteTest(TestCase):
    """El mapa que desambigua el ticket vinculado a un DTE.

    `Ticket.folio_dte` no es único: las series se numeran por tipo de documento
    y en producción hay pares (sucursal, folio_dte) con más de un ticket. Sin
    este mapa, eliminar una BOLETA PAPEL podía devolver el stock del ticket de
    una BOLETA ELECTRONICA con el mismo folio.
    """

    def test_mapa_cubre_los_tipos_de_venta(self):
        from app.utils_ventas import tipo_ticket_para_dte
        self.assertEqual(tipo_ticket_para_dte('BOLETA PAPEL'), 'BOLETA')
        self.assertEqual(
            tipo_ticket_para_dte('BOLETA ELECTRONICA'), 'BOLETA_ELECTRONICA')
        self.assertEqual(
            tipo_ticket_para_dte('FACTURA ELECTRONICA'), 'FACTURA_ELECTRONICA')
        # Tolerante a espacios y minúsculas (los datos vienen de la base).
        self.assertEqual(tipo_ticket_para_dte('  boleta papel '), 'BOLETA')
        self.assertIsNone(tipo_ticket_para_dte('TRASPASO'))
        self.assertIsNone(tipo_ticket_para_dte(None))

    def test_el_default_TICKET_no_contradice_a_ningun_dte(self):
        """El caso REAL de produccion.

        `Ticket.tipo_dte` tiene `default='TICKET'` ("Ticket sin DTE") y
        `registrar_pagos_ticket` nunca lo actualiza al emitir. Medido en prod:
        de 18.964 tickets con `folio_dte`, 18.673 estan en 'TICKET' y 291 en
        'TICKET_COBRO_CAMBIO' - CERO traen el tipo especifico.

        Si el default contara como contradiccion, el guard de
        `eliminar_documento_venta` bloquearia TODAS las eliminaciones reales.
        """
        from app.utils_ventas import tipo_ticket_contradice_dte

        for tipo_doc in ('BOLETA PAPEL', 'BOLETA ELECTRONICA', 'FACTURA ELECTRONICA'):
            for neutro in (None, '', 'TICKET', 'TICKET_COBRO_CAMBIO',
                           'TICKET_DEVOLUCION', 'TICKET_CAMBIO_DIRECTO'):
                self.assertFalse(
                    tipo_ticket_contradice_dte(neutro, tipo_doc),
                    '{!r} no deberia contradecir a {}'.format(neutro, tipo_doc),
                )

    def test_solo_contradice_un_tipo_documental_distinto(self):
        """Un ticket marcado explicitamente como de OTRA serie si contradice."""
        from app.utils_ventas import tipo_ticket_contradice_dte

        self.assertTrue(
            tipo_ticket_contradice_dte('FACTURA_ELECTRONICA', 'BOLETA PAPEL'))
        self.assertTrue(
            tipo_ticket_contradice_dte('BOLETA_ELECTRONICA', 'BOLETA PAPEL'))
        # Coincide -> no contradice.
        self.assertFalse(tipo_ticket_contradice_dte('BOLETA', 'BOLETA PAPEL'))
        # DTE sin equivalencia de ticket (traspaso) -> nada que contradecir.
        self.assertFalse(tipo_ticket_contradice_dte('BOLETA', 'TRASPASO'))
