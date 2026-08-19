"""
Tests de "Llegó todo" (`anular_regularizacion_dte`): cerrar de una pasada las
diferencias de un documento cuando la mercadería sí está.

El endpoint existía desde hacía tiempo pero ningún botón lo llamaba, y estaba
sin blindar: no validaba rol/sucursal, no acotaba las líneas al documento y no
miraba si el DTE ya tenía una NC vigente. Estos tests fijan ese contrato:

1. Feliz: las unidades en disputa entran al stock del DESTINO, las líneas
   quedan RECEPCIONADO_OK y queda un movimiento auditable.
2. Con NC vigente sobre el DTE se bloquea: la NC ya devolvió el valor al
   origen, sumar además al destino duplicaría.
3. Una sucursal que no es ni origen ni destino no puede cerrar el documento.
4. El motivo es obligatorio.
5. Las líneas ya REGULARIZADAS o con solicitud en curso no se tocan.
6. Es idempotente: repetir la llamada no vuelve a sumar stock.
"""
import json
from decimal import Decimal
from unittest import mock

from django.test import TestCase, Client

from app.models import (
    Dte, Dte_Productos, Producto_Talla, Movimientos_Producto,
    Productos_Recepcionados,
)
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla, crear_correlativo,
)

URL = '/app/dte/anular_regularizacion_dte/'


def _patch_permisos():
    """Los decoradores de permiso pegan a la BD de permisos; en tests van a True."""
    return (
        mock.patch('app.views.PermisoRol.tiene_permiso', return_value=True),
        mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True),
    )


class LlegoTodoTest(TestCase):
    SKU_A = 7001
    SKU_B = 7002

    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.destino = crear_sucursal(self.empresa, alias='DESTINO')
        self.ajena = crear_sucursal(self.empresa, alias='AJENA')
        crear_empresa_user(self.user, self.empresa, self.origen)
        crear_correlativo(self.origen, tipo_dte='AJUSTE TRASPASO')

        _, self.origen_a = crear_producto_con_talla(
            self.origen, articulo='Zap A', sku=self.SKU_A, stock=50, costo=100)
        _, self.destino_a = crear_producto_con_talla(
            self.destino, articulo='Zap A', sku=self.SKU_A, stock=0, costo=100)
        _, self.origen_b = crear_producto_con_talla(
            self.origen, articulo='Zap B', sku=self.SKU_B, stock=50, costo=100)
        _, self.destino_b = crear_producto_con_talla(
            self.destino, articulo='Zap B', sku=self.SKU_B, stock=0, costo=100)

        self.client = Client()
        self.client.force_login(self.user)
        self.dte = self._crear_traspaso()

    # ── helpers ─────────────────────────────────────────────────────────────

    def _crear_traspaso(self, numero=17098):
        """Factura de traspaso EMITIDA con 2 líneas, ya despachada del origen."""
        dte = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=numero, tipo_documento='FACTURA ELECTRONICA',
            monto_neto=Decimal('10000'), monto_con_iva=Decimal('11900'),
            estado_pago='PENDIENTE', estado_dte='EMITIDO', responsable='tester',
            fecha_emision='2026-08-13', fecha_vencimiento='2026-08-13',
            diasCredito=0, bultos=1, unidades_productos=10,
            tipo_transaccion='TRASPASO', sucursal=self.origen,
        )
        Movimientos_Producto.objects.create(
            dte=dte, ProductoTalla=self.origen_a,
            sucursal_origen=self.origen, sucursal_destino=self.destino,
            cantidad=-5, concepto='TRASPASO_SALIDA', tipo_movimiento='EGRESO',
            estado='COMPLETADO', responsable='tester',
        )
        for talla, cant in ((self.origen_a, 5), (self.origen_b, 5)):
            Dte_Productos.objects.create(
                dte=dte, productoTalla=talla, descripcion=talla.producto.articulo,
                costo=100, sobreprecio=0, precio=1000, stock=cant, activo=True,
            )
        return dte

    def _linea_faltante(self, talla_origen, esperada=5, recibida=3, estado='FALTANTE'):
        """Línea recepcionada con faltante: lo no recibido quedó en limbo."""
        return Productos_Recepcionados.objects.create(
            dte=self.dte,
            dte_producto=Dte_Productos.objects.filter(
                dte=self.dte, productoTalla=talla_origen).first(),
            producto_talla=talla_origen,
            stockArribado=recibida,
            cantidad_esperada=esperada,
            cantidad_faltante=esperada - recibida,
            cantidad_danada=0,
            estado=estado,
        )

    def _sesion(self, sucursal):
        session = self.client.session
        session['idSucursalActual'] = sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session['alias'] = sucursal.alias
        session.save()

    def _post(self, sucursal, **payload):
        self._sesion(sucursal)
        cuerpo = {'dte_id': self.dte.id, 'motivo': 'Las cajas estaban en bodega'}
        cuerpo.update(payload)
        p1, p2 = _patch_permisos()
        with p1, p2:
            return self.client.post(URL, data=json.dumps(cuerpo),
                                    content_type='application/json')

    def _stock(self, talla):
        return Producto_Talla.objects.get(id=talla.id).stock

    def _crear_nc(self, estado_dte='EMITIDO', numero=555):
        return Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=numero, tipo_documento='NOTA DE CREDITO ELECTRONICA',
            monto_neto=Decimal('2000'), monto_con_iva=Decimal('2380'),
            estado_pago='PENDIENTE', estado_dte=estado_dte, responsable='tester',
            fecha_emision='2026-08-14', fecha_vencimiento='2026-08-14',
            diasCredito=0, bultos=0, unidades_productos=2,
            tipo_transaccion='TRASPASO', sucursal=self.origen,
            es_nota_credito=True, documento_afectado=self.dte,
        )

    # ── tests ───────────────────────────────────────────────────────────────

    def test_cierra_lineas_y_sube_stock_al_destino(self):
        self._linea_faltante(self.origen_a, esperada=5, recibida=3)   # falta 2
        self._linea_faltante(self.origen_b, esperada=5, recibida=0)   # falta 5

        resp = self._post(self.destino)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'], data)
        self.assertEqual(data['productos_anulados'], 2)
        self.assertEqual(data['stock_actualizado'], 7)

        # Las unidades en disputa aterrizan en el DESTINO, no en el origen.
        self.assertEqual(self._stock(self.destino_a), 2)
        self.assertEqual(self._stock(self.destino_b), 5)
        self.assertEqual(self._stock(self.origen_a), 50)

        for recepcion in Productos_Recepcionados.objects.filter(dte=self.dte):
            self.assertEqual(recepcion.estado, 'RECEPCIONADO_OK')
            self.assertEqual(recepcion.cantidad_faltante, 0)
            self.assertEqual(recepcion.cantidad_danada, 0)
            self.assertEqual(recepcion.stockArribado, recepcion.cantidad_esperada)
            self.assertIn('LLEGÓ TODO', recepcion.observaciones)

        movs = Movimientos_Producto.objects.filter(
            dte=self.dte, concepto='ANULACION_REGULARIZACION')
        self.assertEqual(movs.count(), 2)
        for mov in movs:
            self.assertEqual(mov.tipo_movimiento, 'INGRESO')
            self.assertEqual(mov.estado, 'COMPLETADO')
            self.assertEqual(mov.sucursal_destino_id, self.destino.id)
            self.assertEqual(mov.responsable, self.user.username)
            self.assertIn('Las cajas estaban en bodega', mov.observaciones)

    def test_el_origen_tambien_puede_cerrarlo(self):
        self._linea_faltante(self.origen_a, esperada=5, recibida=3)
        resp = self._post(self.origen)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'])
        self.assertEqual(self._stock(self.destino_a), 2)

    def test_bloquea_si_hay_nc_vigente(self):
        self._linea_faltante(self.origen_a, esperada=5, recibida=3)
        self._crear_nc()

        resp = self._post(self.destino)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('NC #555', resp.json()['error'])
        # Nada se movió.
        self.assertEqual(self._stock(self.destino_a), 0)
        self.assertEqual(
            Productos_Recepcionados.objects.get(producto_talla=self.origen_a).estado,
            'FALTANTE',
        )

    def test_nc_anulada_no_bloquea(self):
        self._linea_faltante(self.origen_a, esperada=5, recibida=3)
        self._crear_nc(estado_dte='ANULADO')

        resp = self._post(self.destino)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.destino_a), 2)

    def test_sucursal_ajena_no_puede_cerrar(self):
        self._linea_faltante(self.origen_a, esperada=5, recibida=3)

        resp = self._post(self.ajena)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertEqual(self._stock(self.destino_a), 0)

    def test_motivo_obligatorio(self):
        self._linea_faltante(self.origen_a, esperada=5, recibida=3)

        resp = self._post(self.destino, motivo='   ')
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(self._stock(self.destino_a), 0)

    def test_no_toca_lineas_ya_resueltas_ni_en_solicitud(self):
        self._linea_faltante(self.origen_a, esperada=5, recibida=3,
                             estado='EN_SOLICITUD_REGULARIZACION')
        self._linea_faltante(self.origen_b, esperada=5, recibida=1,
                             estado='REGULARIZADO')

        resp = self._post(self.destino)
        self.assertEqual(resp.status_code, 404, resp.content)
        self.assertEqual(self._stock(self.destino_a), 0)
        self.assertEqual(self._stock(self.destino_b), 0)

    def test_es_idempotente(self):
        self._linea_faltante(self.origen_a, esperada=5, recibida=3)

        primera = self._post(self.destino)
        self.assertEqual(primera.status_code, 200, primera.content)
        self.assertEqual(self._stock(self.destino_a), 2)

        segunda = self._post(self.destino)
        self.assertEqual(segunda.status_code, 404, segunda.content)
        # El stock no se suma dos veces.
        self.assertEqual(self._stock(self.destino_a), 2)
        self.assertEqual(
            Movimientos_Producto.objects.filter(
                dte=self.dte, concepto='ANULACION_REGULARIZACION').count(),
            1,
        )

    def test_ids_de_otro_documento_no_cierran_lineas_ajenas(self):
        propia = self._linea_faltante(self.origen_a, esperada=5, recibida=3)
        otro_dte = self._crear_traspaso(numero=17099)
        ajena = Productos_Recepcionados.objects.create(
            dte=otro_dte, producto_talla=self.origen_b,
            stockArribado=0, cantidad_esperada=4, cantidad_faltante=4,
            cantidad_danada=0, estado='FALTANTE',
        )

        resp = self._post(self.destino, productos_ids=[propia.id, ajena.id])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['productos_anulados'], 1)
        # La línea del otro documento queda intacta.
        ajena.refresh_from_db()
        self.assertEqual(ajena.estado, 'FALTANTE')
        self.assertEqual(ajena.cantidad_faltante, 4)
        self.assertEqual(self._stock(self.destino_b), 0)
