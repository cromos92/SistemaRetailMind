"""
Tests del flujo de recepción de traspasos: mercadería DAÑADA y RECHAZO.

Cubren dos agujeros de kardex detectados en la auditoría de recepción:

1. `confirmar_recepcion_api` restaba las unidades dañadas del ingreso pero NO
   escribía ningún movimiento por ellas. Salían del origen con TRASPASO_SALIDA
   y no entraban a ninguna parte: el único rastro era
   `Productos_Recepcionados.cantidad_danada`, que ningún reporte de kardex mira.
   Ahora se escribe un par TRASPASO_ENTRADA (+dañadas) / PERDIDA_DETERIORO
   (-dañadas), que deja el saldo del SKU igual y la merma auditable.

2. `rechazar_recepcion_api` marcaba los movimientos de salida y nunca devolvía
   el stock: la mercadería quedaba fuera de las dos bodegas. Ahora vuelve al
   origen siguiendo el patrón de `cancelar_dte_traspaso_api`, de forma
   idempotente, y las funciones que también revierten stock
   (`rehabilitar_dte_rechazado_api`, `cancelar_dte_traspaso_api`,
   `ajustar_dte_emisor_api`) quedan coordinadas para no acreditar dos veces.
"""
import json
from decimal import Decimal
from unittest import mock

from django.db.models import Sum
from django.test import TestCase, Client

from app.models import (
    Dte, Dte_Productos, Producto_Talla, Movimientos_Producto,
    Productos_Recepcionados, LoteProducto,
)
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla, crear_correlativo,
)


def _patch_permisos():
    """Los decoradores de permiso pegan a la BD de permisos; en tests van a True."""
    return (
        mock.patch('app.views.PermisoRol.tiene_permiso', return_value=True),
        mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True),
    )


def _crear_traspaso(sucursal_origen, sucursal_destino, talla, cantidad,
                    numero=9000, tipo_documento='GUIA'):
    """Traspaso EMITIDO con una línea, replicando lo que hace `emitir_dte`."""
    dte = Dte.objects.create(
        emisor=sucursal_origen.empresa,
        receptor=sucursal_destino.empresa,
        numero_documento=numero,
        tipo_documento=tipo_documento,
        monto_neto=Decimal(cantidad * 1000),
        monto_con_iva=Decimal(cantidad * 1190),
        estado_pago='PENDIENTE',
        estado_dte='EMITIDO',
        responsable='tester',
        fecha_emision='2026-08-01',
        fecha_vencimiento='2026-08-01',
        diasCredito=0,
        bultos=1,
        unidades_productos=cantidad,
        tipo_transaccion='TRASPASO',
        sucursal=sucursal_origen,
    )
    linea = Dte_Productos.objects.create(
        dte=dte, productoTalla=talla, descripcion='Producto Test',
        costo=100, sobreprecio=0, precio=1000, stock=cantidad, activo=True,
    )
    Movimientos_Producto.objects.create(
        dte=dte, ProductoTalla=talla,
        sucursal_origen=sucursal_origen, sucursal_destino=sucursal_destino,
        cantidad=-cantidad, concepto='TRASPASO_SALIDA',
        tipo_movimiento='EGRESO', estado='COMPLETADO', responsable='tester',
    )
    Producto_Talla.objects.filter(id=talla.id).update(
        stock=Producto_Talla.objects.get(id=talla.id).stock - cantidad
    )
    return dte, linea


class _BaseTraspasoTest(TestCase):
    SKU = 5001
    STOCK_ORIGEN = 20

    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.origen)

        _, self.talla_origen = crear_producto_con_talla(
            self.origen, articulo='Zap Test', sku=self.SKU,
            stock=self.STOCK_ORIGEN, costo=100,
        )
        _, self.talla_destino = crear_producto_con_talla(
            self.destino, articulo='Zap Test D', sku=self.SKU, stock=0, costo=100,
        )
        crear_correlativo(self.origen, tipo_dte='AJUSTE TRASPASO')

        self.client = Client()
        self.client.force_login(self.user)

    def _sesion(self, sucursal):
        session = self.client.session
        session['idSucursalActual'] = sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session['alias'] = sucursal.alias
        session.save()

    def _stock(self, talla):
        return Producto_Talla.objects.get(id=talla.id).stock

    def _kardex(self, talla):
        """Suma con signo del kardex COMPLETADO del SKU en su sucursal."""
        return Movimientos_Producto.objects.filter(
            ProductoTalla_id=talla.id, estado='COMPLETADO',
        ).aggregate(t=Sum('cantidad'))['t'] or 0


class RecepcionDanadoKardexTest(_BaseTraspasoTest):
    """FIX 1: la mercadería dañada deja rastro en el kardex."""

    def _recepcionar(self, dte, linea, esperada, recibida, danada):
        self._sesion(self.destino)
        p1, p2 = _patch_permisos()
        with p1, p2:
            return self.client.post(
                '/app/dte/confirmar_recepcion/',
                data=json.dumps({
                    'dte_id': dte.id,
                    'productos': [{
                        'dte_producto_id': linea.id,
                        'cantidad_esperada': esperada,
                        'cantidad_recepcionada': recibida,
                        'cantidad_danada': danada,
                        'estado': 'RECEPCIONADO_DANADO',
                        'observaciones': 'Caja aplastada',
                    }],
                }),
                content_type='application/json',
            )

    def test_danado_genera_movimiento_de_perdida(self):
        dte, linea = _crear_traspaso(
            self.origen, self.destino, self.talla_origen, 10, numero=9001,
        )
        resp = self._recepcionar(dte, linea, 10, 10, 3)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'], resp.json())

        perdidas = Movimientos_Producto.objects.filter(
            dte=dte, concepto='PERDIDA_DETERIORO',
        )
        self.assertEqual(perdidas.count(), 1, 'Falta el movimiento de pérdida por deterioro')
        perdida = perdidas.first()
        self.assertEqual(perdida.cantidad, -3)
        self.assertEqual(perdida.tipo_movimiento, 'EGRESO')
        self.assertEqual(perdida.estado, 'COMPLETADO')
        self.assertEqual(perdida.ProductoTalla_id, self.talla_destino.id)
        # La pérdida ocurre en el destino: los reportes de merma agrupan por
        # sucursal_origen.
        self.assertEqual(perdida.sucursal_origen_id, self.destino.id)
        self.assertEqual(perdida.responsable, self.user.username)
        self.assertIn('9001', perdida.observaciones)

        # Contrapartida: la mercadería SÍ llegó, entra y se da de baja.
        entradas = Movimientos_Producto.objects.filter(
            dte=dte, concepto='TRASPASO_ENTRADA', estado='COMPLETADO',
        )
        self.assertEqual(entradas.count(), 2)
        self.assertEqual(entradas.aggregate(t=Sum('cantidad'))['t'], 10)

        # El stock del destino NO cambia respecto del comportamiento anterior.
        self.assertEqual(self._stock(self.talla_destino), 7)

    def test_kardex_cuadra_despues_de_recepcion_con_danado(self):
        dte, linea = _crear_traspaso(
            self.origen, self.destino, self.talla_origen, 10, numero=9002,
        )
        resp = self._recepcionar(dte, linea, 10, 10, 3)
        self.assertEqual(resp.status_code, 200, resp.content)

        # 1) Kardex del destino == stock del destino.
        self.assertEqual(self._kardex(self.talla_destino), self._stock(self.talla_destino))
        # 2) Kardex del origen == stock del origen (20 - 10).
        self.assertEqual(self._kardex(self.talla_origen), -10)
        self.assertEqual(self._stock(self.talla_origen), self.STOCK_ORIGEN - 10)
        # 3) El documento completo explica la diferencia: lo único que la
        #    empresa perdió son las 3 unidades dañadas.
        total_dte = Movimientos_Producto.objects.filter(
            dte=dte, estado='COMPLETADO',
        ).aggregate(t=Sum('cantidad'))['t']
        self.assertEqual(total_dte, -3)

    def test_danado_no_genera_lote_fifo(self):
        """Las unidades dañadas no pueden quedar disponibles para vender."""
        dte, linea = _crear_traspaso(
            self.origen, self.destino, self.talla_origen, 10, numero=9003,
        )
        resp = self._recepcionar(dte, linea, 10, 10, 3)
        self.assertEqual(resp.status_code, 200, resp.content)

        lotes = LoteProducto.objects.filter(producto_talla_id=self.talla_destino.id)
        self.assertEqual(lotes.count(), 1)
        self.assertEqual(lotes.first().cantidad_inicial, 7)
        self.assertEqual(
            lotes.aggregate(t=Sum('cantidad_disponible'))['t'],
            self._stock(self.talla_destino),
        )

    def test_linea_totalmente_danada_tambien_deja_rastro(self):
        """El caso que antes se saltaba entero por el `continue`."""
        dte, linea = _crear_traspaso(
            self.origen, self.destino, self.talla_origen, 4, numero=9004,
        )
        resp = self._recepcionar(dte, linea, 4, 4, 4)
        self.assertEqual(resp.status_code, 200, resp.content)

        self.assertEqual(self._stock(self.talla_destino), 0)
        self.assertEqual(
            Movimientos_Producto.objects.filter(
                dte=dte, concepto='PERDIDA_DETERIORO',
            ).aggregate(t=Sum('cantidad'))['t'],
            -4,
        )
        self.assertEqual(self._kardex(self.talla_destino), 0)
        self.assertEqual(
            Productos_Recepcionados.objects.get(dte=dte).cantidad_danada, 4,
        )

    def test_recepcion_sin_danado_no_escribe_perdida(self):
        dte, linea = _crear_traspaso(
            self.origen, self.destino, self.talla_origen, 5, numero=9005,
        )
        resp = self._recepcionar(dte, linea, 5, 5, 0)
        self.assertEqual(resp.status_code, 200, resp.content)

        self.assertFalse(
            Movimientos_Producto.objects.filter(concepto='PERDIDA_DETERIORO').exists()
        )
        self.assertEqual(self._stock(self.talla_destino), 5)
        self.assertEqual(self._kardex(self.talla_destino), 5)


class RechazoDevuelveStockTest(_BaseTraspasoTest):
    """FIX 2: rechazar devuelve el stock al origen, una sola vez."""

    CANTIDAD = 6

    def setUp(self):
        super().setUp()
        self.dte, self.linea = _crear_traspaso(
            self.origen, self.destino, self.talla_origen, self.CANTIDAD,
            numero=9100,
        )
        # Tras emitir, el origen quedó con 20 - 6 = 14.
        self.assertEqual(self._stock(self.talla_origen), self.STOCK_ORIGEN - self.CANTIDAD)

    def _rechazar(self, motivo='Bultos rotos, no se recibe'):
        self._sesion(self.destino)
        p1, p2 = _patch_permisos()
        with p1, p2:
            return self.client.post(
                '/app/dte/rechazar_recepcion/',
                data=json.dumps({'dte_id': self.dte.id, 'motivo_rechazo': motivo}),
                content_type='application/json',
            )

    def _rehabilitar(self):
        self._sesion(self.origen)
        p1, p2 = _patch_permisos()
        with p1, p2:
            return self.client.post(
                '/app/dte/rehabilitar_rechazado/',
                data=json.dumps({'dte_id': self.dte.id, 'observaciones': 'reintento'}),
                content_type='application/json',
            )

    def _cancelar(self, motivo='Se anula el envío'):
        self._sesion(self.origen)
        p1, p2 = _patch_permisos()
        with p1, p2:
            return self.client.post(
                '/app/dte/cancelar_traspaso/',
                data=json.dumps({'dte_id': self.dte.id, 'motivo': motivo}),
                content_type='application/json',
            )

    def test_rechazo_devuelve_stock_al_origen(self):
        resp = self._rechazar()
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'], data)
        self.assertEqual(data['unidades_devueltas'], self.CANTIDAD)

        self.assertEqual(self._stock(self.talla_origen), self.STOCK_ORIGEN)
        self.dte.refresh_from_db()
        self.assertEqual(self.dte.estado_dte, 'RECHAZADO')
        # fecha_recepcion sigue en None para poder rehabilitar.
        self.assertIsNone(self.dte.fecha_recepcion)

        mov = Movimientos_Producto.objects.get(dte=self.dte, concepto='TRASPASO_SALIDA')
        # CANCELADO = "egreso ya revertido" (mismo marcador que la cancelación).
        self.assertEqual(mov.estado, 'CANCELADO')
        self.assertIn('RECHAZADO', mov.observaciones)
        # El kardex del origen vuelve a cuadrar solo: la salida sale del set
        # COMPLETADO, no se escribe un ingreso de contrapartida.
        self.assertEqual(self._kardex(self.talla_origen), 0)
        self.assertEqual(self._stock(self.talla_origen), self.STOCK_ORIGEN)

    def test_rechazo_doble_no_devuelve_stock_dos_veces(self):
        primera = self._rechazar()
        self.assertEqual(primera.status_code, 200, primera.content)
        self.assertEqual(self._stock(self.talla_origen), self.STOCK_ORIGEN)

        segunda = self._rechazar('otra vez')
        self.assertEqual(segunda.status_code, 409, segunda.content)
        self.assertFalse(segunda.json()['success'])
        # Lo importante: el stock NO se acreditó dos veces.
        self.assertEqual(self._stock(self.talla_origen), self.STOCK_ORIGEN)

    def test_cancelar_despues_de_rechazar_no_duplica_stock(self):
        self.assertEqual(self._rechazar().status_code, 200)
        self.assertEqual(self._stock(self.talla_origen), self.STOCK_ORIGEN)

        resp = self._cancelar()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['unidades_revertidas'], 0)
        self.assertEqual(self._stock(self.talla_origen), self.STOCK_ORIGEN)
        self.dte.refresh_from_db()
        self.assertEqual(self.dte.estado_dte, 'CANCELADO')

    def test_cancelar_sin_rechazo_previo_sigue_devolviendo_stock(self):
        """Regresión: no romper el camino que ya funcionaba."""
        resp = self._cancelar()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['unidades_revertidas'], self.CANTIDAD)
        self.assertEqual(self._stock(self.talla_origen), self.STOCK_ORIGEN)

    def test_rehabilitar_vuelve_a_descontar_el_stock(self):
        self.assertEqual(self._rechazar().status_code, 200)
        self.assertEqual(self._stock(self.talla_origen), self.STOCK_ORIGEN)

        resp = self._rehabilitar()
        self.assertEqual(resp.status_code, 200, resp.content)
        # La mercadería vuelve a estar "en tránsito": el origen no la tiene.
        self.assertEqual(self._stock(self.talla_origen), self.STOCK_ORIGEN - self.CANTIDAD)
        mov = Movimientos_Producto.objects.get(dte=self.dte, concepto='TRASPASO_SALIDA')
        self.assertEqual(mov.estado, 'COMPLETADO')
        self.dte.refresh_from_db()
        self.assertEqual(self.dte.estado_dte, 'EMITIDO')

    def test_rehabilitar_bloqueado_si_el_origen_ya_no_tiene_stock(self):
        self.assertEqual(self._rechazar().status_code, 200)
        # El origen vendió lo devuelto mientras el DTE estaba rechazado.
        Producto_Talla.objects.filter(id=self.talla_origen.id).update(stock=2)

        resp = self._rehabilitar()
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertFalse(resp.json()['success'])
        # Ni stock negativo ni movimientos tocados.
        self.assertEqual(self._stock(self.talla_origen), 2)
        mov = Movimientos_Producto.objects.get(dte=self.dte, concepto='TRASPASO_SALIDA')
        self.assertEqual(mov.estado, 'CANCELADO')
        self.dte.refresh_from_db()
        self.assertEqual(self.dte.estado_dte, 'RECHAZADO')

    def test_ajustar_traspaso_rechazado_queda_bloqueado(self):
        """Ajustar en pre-recepción volvería a sumar el stock ya devuelto."""
        self.assertEqual(self._rechazar().status_code, 200)

        self._sesion(self.origen)
        p1, p2 = _patch_permisos()
        with p1, p2:
            resp = self.client.post(
                '/app/dte/ajustar_traspaso/',
                data=json.dumps({
                    'dte_id': self.dte.id,
                    'ajustes': [{'dte_producto_id': self.linea.id, 'nueva_cantidad': 2}],
                    'motivo': 'Ajuste sobre un DTE rechazado',
                }),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(self._stock(self.talla_origen), self.STOCK_ORIGEN)

    def test_no_se_puede_rechazar_un_dte_ya_recepcionado(self):
        self.dte.estado_dte = 'RECEPCIONADO_COMPLETO'
        self.dte.save(update_fields=['estado_dte'])

        resp = self._rechazar()
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(self._stock(self.talla_origen), self.STOCK_ORIGEN - self.CANTIDAD)
