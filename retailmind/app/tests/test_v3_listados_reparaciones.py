"""
Unidad V3 — listados de Recepción DTE, herramientas de reparación y comandos
de saneamiento de traspasos.

1. historial_recepciones_api (B6-07): muestra lo que la sucursal RECIBIÓ, con
   el origen real; no lo que emitió.
2. recepciones_pendientes_api (B6-11): 'pendientes' no cuenta RECHAZADOS.
3. confirmar_recepcion_api (B14-05): enlaza movimiento_ingreso.
4. api_ncs_sin_stock (CC-10): ignora empresa_id ajenas (también con
   membresía revocada) y filtra por nc_id.
5. reparar_nc_stock / api_crear_stock_destino_manual (CC-10): la capa FIFO
   acompaña al stock plano y la reparación no se aplica dos veces aunque dos
   envíos pasen el chequeo inicial.
6. Comando traspaso_recalcular_estado (B7-06/B14-12): dry-run no escribe;
   --apply cierra solo los DTE sin líneas abiertas ni devolución física
   pendiente.
7. Comando traspaso_consumir_pendientes_despacho (CC-08): no reasigna un
   despacho ya consumido por un pendiente cerrado.
"""
import datetime
import json
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase, Client
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, Producto_Talla, Movimientos_Producto,
    Productos_Recepcionados, LoteProducto, PendienteDespacho,
)
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla,
)


def _permisos():
    return mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True)


class _Base(TestCase):
    SKU = 94001

    def setUp(self):
        self.user = crear_usuario(username='v3list', rol='administrador')
        self.empresa = crear_empresa()
        self.origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.origen)
        _, self.t_origen = crear_producto_con_talla(
            self.origen, articulo='Zap L', sku=self.SKU, stock=20, costo=100)
        _, self.t_destino = crear_producto_con_talla(
            self.destino, articulo='Zap L', sku=self.SKU, stock=0, costo=100)
        self.client = Client()
        self.client.force_login(self.user)
        self._folio = 80000

    def _traspaso(self, cantidad=5, estado='EMITIDO', tipo_documento='GUIA',
                  emisor_suc=None, destino_suc=None, talla=None):
        emisor_suc = emisor_suc or self.origen
        destino_suc = destino_suc or self.destino
        talla = talla or self.t_origen
        self._folio += 1
        dte = Dte.objects.create(
            emisor=emisor_suc.empresa, receptor=destino_suc.empresa,
            numero_documento=self._folio, tipo_documento=tipo_documento,
            monto_neto=Decimal(cantidad * 1000), monto_con_iva=Decimal(cantidad * 1190),
            estado_pago='PENDIENTE', estado_dte=estado, responsable='tester',
            fecha_emision='2026-09-01', fecha_vencimiento='2026-09-01',
            diasCredito=0, bultos=1, unidades_productos=cantidad,
            tipo_transaccion='TRASPASO', sucursal=emisor_suc,
        )
        dp = Dte_Productos.objects.create(
            dte=dte, productoTalla=talla, descripcion='Zap L',
            costo=100, sobreprecio=0, precio=1000, stock=cantidad, activo=True,
        )
        Movimientos_Producto.objects.create(
            dte=dte, ProductoTalla=talla,
            sucursal_origen=emisor_suc, sucursal_destino=destino_suc,
            cantidad=-cantidad, costo=100, concepto='TRASPASO_SALIDA',
            tipo_movimiento='EGRESO', estado='COMPLETADO', responsable='tester',
        )
        Producto_Talla.objects.filter(id=talla.id).update(
            stock=Producto_Talla.objects.get(id=talla.id).stock - cantidad)
        return dte, dp

    def _sesion(self, sucursal, empresa=None):
        session = self.client.session
        session['idSucursalActual'] = sucursal.id
        session['idEmpresaActual'] = (empresa or sucursal.empresa).id
        session['alias'] = sucursal.alias
        session.save()

    @staticmethod
    def _lotes(talla):
        return LoteProducto.objects.filter(
            producto_talla_id=talla.id, activo=True,
        ).aggregate(t=Sum('cantidad_disponible'))['t'] or 0

    def _confirmar_completo(self, dte, dp):
        self._sesion(self.destino)
        with _permisos():
            return self.client.post('/app/dte/confirmar_recepcion/', data=json.dumps({
                'dte_id': dte.id,
                'productos': [{
                    'dte_producto_id': dp.id, 'cantidad_esperada': dp.stock,
                    'cantidad_recepcionada': dp.stock, 'cantidad_danada': 0,
                    'estado': 'RECEPCIONADO_OK', 'observaciones': '',
                }],
            }), content_type='application/json')


class HistorialRecepcionesTest(_Base):

    def test_muestra_lo_recibido_con_su_origen_real(self):
        dte, dp = self._traspaso(cantidad=4)
        self.assertEqual(self._confirmar_completo(dte, dp).status_code, 200)

        self._sesion(self.destino)
        with _permisos():
            data = self.client.get('/app/dte/historial_recepciones/?limite=5').json()
        self.assertTrue(data['success'])
        self.assertEqual([i['id'] for i in data['items']], [dte.id])
        self.assertEqual(data['items'][0]['sucursal_origen'], 'ORIGEN')
        self.assertEqual(data['items'][0]['total_unidades'], 4)

        # La sucursal EMISORA no lo ve como "recibido".
        self._sesion(self.origen)
        with _permisos():
            data = self.client.get('/app/dte/historial_recepciones/').json()
        self.assertEqual(data['items'], [])

    def test_limite_invalido_no_revienta(self):
        self._sesion(self.destino)
        with _permisos():
            resp = self.client.get('/app/dte/historial_recepciones/?limite=abc')
        self.assertEqual(resp.status_code, 200)


class RecepcionesPendientesResumenTest(_Base):

    def test_pendientes_no_cuenta_rechazados(self):
        self._traspaso(cantidad=2, estado='EMITIDO')
        self._traspaso(cantidad=3, estado='RECHAZADO')
        self._sesion(self.destino)
        with _permisos():
            resp = self.client.get('/app/dte/recepciones_pendientes/?pagina=abc')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data['resumen']['pendientes'], 1)
        self.assertEqual(data['conteo_estados'].get('RECHAZADO'), 1)
        # B9-12 (R2V3): el bloque `origenes` no tenía consumidor y se borró.
        self.assertNotIn('origenes', data)


class MovimientoIngresoEnlaceTest(_Base):

    def test_recepcion_enlaza_movimiento_ingreso(self):
        dte, dp = self._traspaso(cantidad=5)
        self.assertEqual(self._confirmar_completo(dte, dp).status_code, 200)
        rec = Productos_Recepcionados.objects.get(dte=dte)
        self.assertIsNotNone(rec.movimiento_ingreso_id)
        self.assertEqual(rec.movimiento_ingreso.concepto, 'TRASPASO_ENTRADA')
        self.assertEqual(rec.movimiento_ingreso.ProductoTalla_id, self.t_destino.id)
        self.assertEqual(rec.movimiento_ingreso.cantidad, 5)


class NcsSinStockAlcanceTest(_Base):

    def setUp(self):
        super().setUp()
        dte, _dp = self._traspaso(cantidad=5)
        self.nc = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa, numero_documento=555,
            tipo_documento='NOTA DE CREDITO', monto_neto=Decimal('2000'),
            monto_con_iva=Decimal('2380'), estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable='tester', fecha_emision='2026-09-02', fecha_vencimiento='2026-09-02',
            diasCredito=0, bultos=0, unidades_productos=2, tipo_transaccion='ANULACION',
            sucursal=self.origen, es_nota_credito=True, documento_afectado=dte,
        )
        Dte_Productos.objects.create(
            dte=self.nc, productoTalla=self.t_origen, descripcion='Zap L',
            costo=100, sobreprecio=0, precio=1000, stock=2, activo=True,
        )
        # Usuario de OTRA empresa.
        self.otra = crear_empresa(nombre='Otra', rut='77.000.000-0')
        self.suc_otra = crear_sucursal(self.otra, alias='OTRA')
        self.ajeno = crear_usuario(username='v3ajeno', rol='vendedor')
        crear_empresa_user(self.ajeno, self.otra, self.suc_otra)

    def test_propia_empresa_ve_la_nc_y_filtra_por_nc_id(self):
        self._sesion(self.origen)
        with _permisos():
            data = self.client.get(f'/app/api/dte/ncs_sin_stock/?nc_id={self.nc.id}').json()
        self.assertTrue(data['success'], data)
        self.assertEqual([i['nc_id'] for i in data['items']], [self.nc.id])

    def test_empresa_ajena_por_get_se_ignora(self):
        c = Client()
        c.force_login(self.ajeno)
        s = c.session
        s['idSucursalActual'] = self.suc_otra.id
        s['idEmpresaActual'] = self.otra.id
        s.save()
        with _permisos():
            data = c.get(f'/app/api/dte/ncs_sin_stock/?empresa_id={self.empresa.id}').json()
        self.assertTrue(data['success'], data)
        self.assertEqual(data['total'], 0)

    def test_membresia_revocada_no_da_acceso(self):
        # EmpresaUser con status=False en la empresa de la NC: el empresa_id
        # por GET se ignora igual que si no tuviera membresía.
        crear_empresa_user(self.ajeno, self.empresa, self.origen, status=False)
        c = Client()
        c.force_login(self.ajeno)
        s = c.session
        s['idSucursalActual'] = self.suc_otra.id
        s['idEmpresaActual'] = self.otra.id
        s.save()
        with _permisos():
            data = c.get(f'/app/api/dte/ncs_sin_stock/?empresa_id={self.empresa.id}').json()
        self.assertTrue(data['success'], data)
        self.assertEqual(data['total'], 0)

    def test_reparacion_concurrente_no_se_aplica_dos_veces(self):
        # Simula la carrera: otra petición commitea la reparación entre el
        # chequeo de idempotencia inicial (fuera del lock) y el lock de la NC.
        from app import views
        real = views._diagnostico_nc
        estado = {'n': 0}

        def _diag_y_carrera(nc):
            resultado = real(nc)
            estado['n'] += 1
            if estado['n'] == 1:
                Movimientos_Producto.objects.create(
                    dte=nc, ProductoTalla=self.t_origen, cantidad=2, costo=100,
                    concepto='REPARACION_STOCK_HISTORICO', tipo_movimiento='INGRESO',
                    estado='COMPLETADO', responsable='otra-pestaña',
                )
            return resultado

        stock0, lotes0 = (Producto_Talla.objects.get(id=self.t_origen.id).stock,
                          self._lotes(self.t_origen))
        nc = Dte.objects.select_related('documento_afectado', 'sucursal').get(id=self.nc.id)
        with mock.patch('app.views._diagnostico_nc', side_effect=_diag_y_carrera):
            status, payload = views.reparar_nc_stock(
                nc, [{'sku': self.SKU, 'cantidad': 2}], 'tester')
        self.assertEqual(status, 409, payload)
        self.assertTrue(payload.get('ya_reparado'))
        self.assertEqual(Producto_Talla.objects.get(id=self.t_origen.id).stock, stock0)
        self.assertEqual(self._lotes(self.t_origen), lotes0)
        self.assertEqual(Movimientos_Producto.objects.filter(
            dte=self.nc, concepto='REPARACION_STOCK_HISTORICO').count(), 1)

    def test_reparacion_pre_recepcion_repone_lote(self):
        stock0, lotes0 = (Producto_Talla.objects.get(id=self.t_origen.id).stock,
                          self._lotes(self.t_origen))
        self._sesion(self.origen)
        with _permisos():
            resp = self.client.post(
                f'/app/dte/{self.nc.id}/reparar_stock/',
                data=json.dumps({'lineas': [{'sku': self.SKU, 'cantidad': 2}], 'motivo': 'test'}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Producto_Talla.objects.get(id=self.t_origen.id).stock - stock0, 2)
        self.assertEqual(self._lotes(self.t_origen) - lotes0, 2)


class CrearStockDestinoManualLoteTest(_Base):

    def test_stock_manual_en_destino_crea_lote(self):
        dte, _dp = self._traspaso(cantidad=5)
        self._sesion(self.origen)
        with _permisos():
            resp = self.client.post(
                f'/app/api/dte/{dte.id}/crear_stock_destino_manual/',
                data=json.dumps({'items': [{'sku': self.SKU, 'stock_final': 5}],
                                 'motivo': 'Recepción manual test'}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Producto_Talla.objects.get(id=self.t_destino.id).stock, 5)
        self.assertEqual(self._lotes(self.t_destino), 5)


class ComandoRecalcularEstadoTest(_Base):

    def _dte_parcial(self, estados_lineas):
        dte, dp = self._traspaso(cantidad=len(estados_lineas))
        Dte.objects.filter(id=dte.id).update(estado_dte='RECEPCIONADO_PARCIAL')
        for estado in estados_lineas:
            Productos_Recepcionados.objects.create(
                dte=dte, dte_producto=dp, producto_talla=self.t_origen,
                stockArribado=0, cantidad_esperada=1, cantidad_faltante=1,
                cantidad_danada=0, estado=estado,
            )
        return dte

    def test_dry_run_no_escribe_y_apply_cierra_solo_los_resueltos(self):
        resuelto = self._dte_parcial(['RECEPCIONADO_OK', 'REGULARIZADO'])
        abierto = self._dte_parcial(['REGULARIZADO', 'FALTANTE'])

        call_command('traspaso_recalcular_estado', stdout=StringIO())
        resuelto.refresh_from_db()
        self.assertEqual(resuelto.estado_dte, 'RECEPCIONADO_PARCIAL')

        call_command('traspaso_recalcular_estado', '--apply', stdout=StringIO())
        resuelto.refresh_from_db()
        abierto.refresh_from_db()
        self.assertEqual(resuelto.estado_dte, 'RECEPCIONADO_COMPLETO')
        self.assertIn('traspaso_recalcular_estado', resuelto.referencias)
        self.assertEqual(abierto.estado_dte, 'RECEPCIONADO_PARCIAL')

    def test_no_cierra_dte_con_devolucion_fisica_pendiente(self):
        # Hijo (NC / AJUSTE POST) cuya devolución el receptor no confirmó:
        # cerrar la cabecera lo sacaría de "Mis Regularizaciones" (B7-02).
        dte = self._dte_parcial(['RECEPCIONADO_OK', 'REGULARIZADO'])
        Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa, numero_documento=777,
            tipo_documento='AJUSTE TRASPASO POST', monto_neto=Decimal('0'),
            monto_con_iva=Decimal('0'), estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable='tester', fecha_emision='2026-09-02', fecha_vencimiento='2026-09-02',
            diasCredito=0, bultos=0, unidades_productos=1, tipo_transaccion='TRASPASO',
            sucursal=self.origen, documento_afectado=dte,
            requiere_devolucion_fisica=True,
        )
        out = StringIO()
        call_command('traspaso_recalcular_estado', '--apply', stdout=out)
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'RECEPCIONADO_PARCIAL')
        self.assertIn('devolución física pendiente', out.getvalue())


class ComandoConsumirPendientesTest(_Base):
    """traspaso_consumir_pendientes_despacho (CC-08): un despacho que ya
    consumió un pendiente cerrado no se le asigna a otro pendiente vivo."""

    def _pendiente(self, creado, cantidad=1, despachada=0, estado='PENDIENTE'):
        p = PendienteDespacho.objects.create(
            producto_talla=self.t_origen, sucursal_origen=self.origen,
            sucursal_destino=self.destino, cantidad=cantidad,
            cantidad_despachada=despachada, estado=estado,
        )
        PendienteDespacho.objects.filter(id=p.id).update(
            created_at=timezone.make_aware(datetime.datetime.combine(creado, datetime.time(12))))
        return p

    def _salida(self, fecha, cantidad=1):
        mov = Movimientos_Producto.objects.create(
            ProductoTalla=self.t_origen, sucursal_origen=self.origen,
            sucursal_destino=self.destino, cantidad=-cantidad, costo=100,
            concepto='TRASPASO_SALIDA', tipo_movimiento='EGRESO',
            estado='COMPLETADO', responsable='tester',
        )
        Movimientos_Producto.objects.filter(id=mov.id).update(fecha=fecha)
        return mov

    def test_despacho_ya_consumido_por_un_cerrado_no_cubre_al_vivo(self):
        self._pendiente(datetime.date(2026, 4, 20), despachada=1, estado='DESPACHADO')
        vivo = self._pendiente(datetime.date(2026, 4, 21))
        self._salida(datetime.date(2026, 4, 21))

        call_command('traspaso_consumir_pendientes_despacho', '--apply', stdout=StringIO())
        vivo.refresh_from_db()
        self.assertEqual(vivo.estado, 'PENDIENTE')
        self.assertEqual(vivo.cantidad_despachada, 0)

    def test_despacho_adicional_si_cubre_al_vivo_y_dry_run_no_escribe(self):
        self._pendiente(datetime.date(2026, 4, 20), despachada=1, estado='DESPACHADO')
        vivo = self._pendiente(datetime.date(2026, 4, 21))
        self._salida(datetime.date(2026, 4, 21))
        self._salida(datetime.date(2026, 4, 22))

        call_command('traspaso_consumir_pendientes_despacho', stdout=StringIO())
        vivo.refresh_from_db()
        self.assertEqual(vivo.estado, 'PENDIENTE')

        call_command('traspaso_consumir_pendientes_despacho', '--apply', stdout=StringIO())
        vivo.refresh_from_db()
        self.assertEqual(vivo.estado, 'DESPACHADO')
        self.assertEqual(vivo.cantidad_despachada, 1)

    def test_despacho_anterior_al_pendiente_no_lo_cubre(self):
        self._salida(datetime.date(2026, 4, 20))
        vivo = self._pendiente(datetime.date(2026, 4, 21))
        call_command('traspaso_consumir_pendientes_despacho', '--apply', stdout=StringIO())
        vivo.refresh_from_db()
        self.assertEqual(vivo.estado, 'PENDIENTE')
