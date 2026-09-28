"""
Regularización de recepciones de traspaso (unidad V4, auditoría 2026-09).

Cubre los escritores de /app/dte/regularizar_producto/, la NC masiva,
"Cancelar regularización", "Llegó todo" y las lecturas asociadas:

- B8-01  Cancelar revierte la devolución al origen de "Regularizar con NC" en
         guías (antes quedaba rotulada DEVOLUCION_NC y no se revertía: cada ciclo
         cancelar + regularizar sumaba otra vez al origen).
- B8-02  Solo el origen o el destino operan el traspaso (regularizar, masiva,
         cancelar); Emitir NC solo el origen; tallas del cliente validadas.
- B8-03  La NC masiva cierra el DTE (antes quedaba RECEPCIONADO_PARCIAL).
- B8-04  La masiva no devuelve dañadas al origen.
- B8-05  Emitir NC valida 1 <= cantidad <= faltante + dañada.
- B8-06  Ajustar cantidad: tope y estado calculado en el servidor.
- B8-08 / B10-03  La NC con referencias JSON aparece en el listado y en el
         documento imprimible.
- B8-10  Cancelar no hace desaparecer el faltante cuando hay dañadas.
- B8-11  Una sola solicitud viva por línea; la NC la cierra (EJECUTADA).
- B8-13  Las entradas de stock de la regularización crean lote FIFO y cancelar
         lo consume.
- B8-15  El PDF no se cae con '<' en la búsqueda.
- B10-01 El cambio directo de producto está deshabilitado.
- B10-07 Fechas de NC y movimientos en hora de Chile.
- B10-14 buscar_productos_emisor sin costo y acotado.

Revisión adversarial (26-sep): el lote devuelto al origen conserva la fecha del
TRASPASO_SALIDA (B8-13); cerrar sin NC libera la solicitud viva (B8-11);
ENVIAR_CAMBIO rechaza cantidades < 1 (B8-05); Cancelar no reabre líneas
cerradas con "Llegó todo" (B8-01/B8-10); `tab` desconocido en el PDF (B8-15).

Se usan permisos REALES (OpcionMenu + PermisoRol del rol jefe_local), no
mocks: el middleware de permisos mapea estas URLs.
"""
import json
import shutil
import tempfile
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.db.models import Sum
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, LoteProducto, ModuloSistema, Movimientos_Producto,
    OpcionMenu, PermisoRol, Producto_Talla, Productos_Recepcionados,
    Solicitud_Regularizacion,
)

from .factories import (
    crear_correlativo, crear_empresa, crear_empresa_user,
    crear_producto_con_talla, crear_sucursal, crear_usuario,
)

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'
_MEDIA_TMP = tempfile.mkdtemp(prefix='v4_regularizacion_media_')

URL_REG = '/app/dte/regularizar_producto/'
URL_MASIVA = '/app/dte/regularizar_dte_masivo/'
URL_CANCELAR = '/app/dte/cancelar_regularizacion/'
URL_LLEGO_TODO = '/app/dte/anular_regularizacion_dte/'
URL_LISTADO = '/app/dte/obtener_productos_regularizar/'
URL_BUSCAR_EMISOR = '/app/dte/buscar_productos_emisor/'
URL_PDF = '/app/dte/exportar_productos_regularizar_pdf/'


def _otorgar_permisos(rol):
    """recepcion_dte (pantalla + aprobar + exportar) y NC de traspaso."""
    modulo, _ = ModuloSistema.objects.get_or_create(
        codigo='compras', defaults={'nombre': 'Compras'},
    )
    permisos = {
        'recepcion_dte': dict(
            puede_ver=True, puede_crear=True, puede_editar=True,
            puede_aprobar=True, puede_exportar=True,
        ),
        'emitir_nota_credito_traspaso': dict(puede_ver=True, puede_crear=True),
    }
    for codigo, valores in permisos.items():
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo=codigo,
            defaults={'modulo': modulo, 'nombre': codigo, 'activo': True},
        )
        PermisoRol.objects.update_or_create(
            rol=rol, opcion_menu=opcion, defaults=valores,
        )


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST, MEDIA_ROOT=_MEDIA_TMP)
class _BaseRegularizacionV4(TestCase):
    SKU = 7700
    STOCK_ORIGEN = 20

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA_TMP, ignore_errors=True)

    def setUp(self):
        self.emp_a = crear_empresa(nombre='Emp A', rut='76.111.111-1')
        self.emp_b = crear_empresa(nombre='Emp B', rut='77.222.222-2')
        self.emp_c = crear_empresa(nombre='Emp C', rut='78.333.333-3')
        self.origen = crear_sucursal(self.emp_a, alias='ORI')
        self.destino = crear_sucursal(self.emp_a, alias='DES')        # misma empresa
        self.destino_b = crear_sucursal(self.emp_b, alias='DESB')     # otra empresa
        self.ajena = crear_sucursal(self.emp_c, alias='AJE')          # ni origen ni destino

        _otorgar_permisos('jefe_local')
        self.user = crear_usuario(username='jefe-v4', rol='jefe_local')
        for suc in (self.origen, self.destino, self.destino_b, self.ajena):
            crear_empresa_user(self.user, suc.empresa, suc)

        _, self.talla_origen = crear_producto_con_talla(
            self.origen, articulo='Zap V4', sku=self.SKU,
            stock=self.STOCK_ORIGEN, costo=1000,
        )
        _, self.talla_destino = crear_producto_con_talla(
            self.destino, articulo='Zap V4', sku=self.SKU, stock=0, costo=1000,
        )
        _, self.talla_destino_b = crear_producto_con_talla(
            self.destino_b, articulo='Zap V4', sku=self.SKU, stock=0, costo=1000,
        )
        _, self.talla_ajena = crear_producto_con_talla(
            self.ajena, articulo='Zap Ajena', sku=9900, stock=5, costo=1000,
        )
        crear_correlativo(self.origen, tipo_dte='NOTA DE CREDITO')

        self.client = Client()
        self.client.force_login(self.user)
        self._folio = 16000

    # ── helpers ────────────────────────────────────────────────────────────
    def _sesion(self, sucursal):
        session = self.client.session
        session['idSucursalActual'] = sucursal.id
        session['idEmpresaActual'] = sucursal.empresa_id
        session['alias'] = sucursal.alias
        session.save()

    def _post(self, url, payload, sucursal):
        self._sesion(sucursal)
        return self.client.post(
            url, data=json.dumps(payload), content_type='application/json',
        )

    def _stock(self, talla):
        return Producto_Talla.objects.get(id=talla.id).stock

    def _lotes(self, talla):
        return LoteProducto.objects.filter(
            producto_talla=talla, activo=True,
        ).aggregate(t=Sum('cantidad_disponible'))['t'] or 0

    def _traspaso(self, destino, cantidad, tipo_documento='GUIA', numero=None):
        """Traspaso despachado (el origen ya descontó), como `emitir_dte`."""
        self._folio += 1
        dte = Dte.objects.create(
            emisor=self.origen.empresa,
            receptor=destino.empresa,
            numero_documento=numero or self._folio,
            tipo_documento=tipo_documento,
            monto_neto=Decimal(cantidad * 1000),
            monto_con_iva=Decimal(cantidad * 1190),
            estado_pago='PENDIENTE',
            estado_dte='RECEPCIONADO_PARCIAL',
            responsable='tester',
            fecha_emision='2026-09-01',
            fecha_vencimiento='2026-09-01',
            diasCredito=0,
            bultos=1,
            unidades_productos=cantidad,
            tipo_transaccion='TRASPASO',
            sucursal=self.origen,
        )
        linea = Dte_Productos.objects.create(
            dte=dte, productoTalla=self.talla_origen, descripcion='Zap V4',
            costo=1000, sobreprecio=0, precio=1000, stock=cantidad, activo=True,
        )
        Movimientos_Producto.objects.create(
            dte=dte, ProductoTalla=self.talla_origen,
            sucursal_origen=self.origen, sucursal_destino=destino,
            cantidad=-cantidad, concepto='TRASPASO_SALIDA',
            tipo_movimiento='EGRESO', estado='COMPLETADO', responsable='tester',
        )
        Producto_Talla.objects.filter(id=self.talla_origen.id).update(
            stock=self.STOCK_ORIGEN - cantidad,
        )
        return dte, linea

    def _recepcion(self, dte, linea, destino, esperada, arribado, faltante,
                   danada=0, sobrante=0, estado=None, talla_destino=None):
        """Línea recepcionada; replica los movimientos que deja la recepción."""
        talla_destino = talla_destino or (
            self.talla_destino if destino == self.destino else self.talla_destino_b
        )
        if arribado:
            Movimientos_Producto.objects.create(
                dte=dte, ProductoTalla=talla_destino,
                sucursal_origen=self.origen, sucursal_destino=destino,
                cantidad=arribado, concepto='TRASPASO_ENTRADA',
                tipo_movimiento='INGRESO', estado='COMPLETADO', responsable='tester',
            )
            buenas = arribado - danada
            Producto_Talla.objects.filter(id=talla_destino.id).update(stock=buenas)
        if danada:
            Movimientos_Producto.objects.create(
                dte=dte, ProductoTalla=talla_destino,
                sucursal_origen=destino, sucursal_destino=None,
                cantidad=-danada, concepto='PERDIDA_DETERIORO',
                tipo_movimiento='EGRESO', estado='COMPLETADO', responsable='tester',
            )
        if estado is None:
            estado = 'FALTANTE' if arribado == 0 else 'RECEPCIONADO_PARCIAL'
        return Productos_Recepcionados.objects.create(
            dte=dte, dte_producto=linea, producto_talla=self.talla_origen,
            sucursal_destino=destino, cantidad_esperada=esperada,
            stockArribado=arribado, cantidad_faltante=faltante,
            cantidad_danada=danada, cantidad_sobrante=sobrante,
            estado=estado, observaciones='', recepcionado_por='tester',
        )


class RegularizarGuiaCancelarTest(_BaseRegularizacionV4):
    """B8-01: en una GUIA "Sí, generar NC" no emite NC; cancelar debe revertir."""

    def setUp(self):
        super().setUp()
        self.dte, self.linea = self._traspaso(self.destino, 3, tipo_documento='GUIA')
        self.rec = self._recepcion(self.dte, self.linea, self.destino,
                                   esperada=3, arribado=2, faltante=1)

    def _regularizar_con_nc(self):
        return self._post(URL_REG, {
            'producto_id': self.rec.id,
            'tipo_regularizacion': 'REGULARIZAR_CON_NC',
            'hacer_nc': True,
            'cantidad_nc': 1,
            'motivo_nc': 'no llegó',
        }, self.destino)

    def test_ciclo_regularizar_cancelar_no_duplica_stock_origen(self):
        base = self._stock(self.talla_origen)  # 17 tras despachar 3

        resp = self._regularizar_con_nc()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(resp.json()['nc_generada'])
        self.assertEqual(resp.json()['tipo'], 'REGULARIZAR_SIN_NC')
        self.assertEqual(self._stock(self.talla_origen), base + 1)
        mov = Movimientos_Producto.objects.filter(
            dte=self.dte, ProductoTalla=self.talla_origen, cantidad__gt=0,
        ).latest('id')
        self.assertEqual(mov.concepto, 'REGULARIZACION_TRASPASO')

        resp = self._post(URL_CANCELAR, {'producto_id': self.rec.id, 'motivo': 'error'}, self.destino)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['canceladas'][0]['stock_revertido'], 1)
        self.assertEqual(self._stock(self.talla_origen), base)

        resp = self._regularizar_con_nc()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.talla_origen), base + 1,
                         'El faltante real es 1: el origen no puede subir 2 veces.')

    def test_cancelar_revierte_devolucion_nc_legacy_sobre_el_documento(self):
        """Filas ya creadas con el bug: DEVOLUCION_NC con dte = la guía."""
        base = self._stock(self.talla_origen)
        Movimientos_Producto.objects.create(
            dte=self.dte, ProductoTalla=self.talla_origen,
            sucursal_origen=self.destino, sucursal_destino=self.origen,
            cantidad=1, concepto='DEVOLUCION_NC', tipo_movimiento='INGRESO',
            estado='COMPLETADO', responsable='tester',
        )
        Producto_Talla.objects.filter(id=self.talla_origen.id).update(stock=base + 1)
        Productos_Recepcionados.objects.filter(id=self.rec.id).update(estado='REGULARIZADO')

        resp = self._post(URL_CANCELAR, {'producto_id': self.rec.id, 'motivo': 'error'}, self.destino)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['canceladas'][0]['stock_revertido'], 1)
        self.assertEqual(self._stock(self.talla_origen), base)


class GateParteTraspasoTest(_BaseRegularizacionV4):
    """B8-02: una sucursal que no es origen ni destino no opera el traspaso."""

    def setUp(self):
        super().setUp()
        self.dte, self.linea = self._traspaso(
            self.destino_b, 2, tipo_documento='FACTURA ELECTRONICA',
        )
        self.rec = self._recepcion(self.dte, self.linea, self.destino_b,
                                   esperada=2, arribado=0, faltante=2)

    def test_ajena_no_puede_mercaderia_encontrada(self):
        resp = self._post(URL_REG, {
            'producto_id': self.rec.id,
            'tipo_regularizacion': 'MERCADERIA_ENCONTRADA',
            'cantidad_encontrada': 1,
        }, self.ajena)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertEqual(self._stock(self.talla_destino_b), 0)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.cantidad_faltante, 2)

    def test_ajena_no_puede_nc_masiva(self):
        resp = self._post(URL_MASIVA, {
            'dte_id': self.dte.id, 'dte_numero': self.dte.numero_documento,
            'productos_ids': [self.rec.id], 'motivo': 'x',
        }, self.ajena)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertFalse(Dte.objects.filter(documento_afectado=self.dte).exists())

    def test_ajena_no_puede_cancelar(self):
        Productos_Recepcionados.objects.filter(id=self.rec.id).update(estado='REGULARIZADO')
        resp = self._post(URL_CANCELAR, {'producto_id': self.rec.id, 'motivo': 'x'}, self.ajena)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.estado, 'REGULARIZADO')

    def test_destino_no_emite_nc_pero_origen_si(self):
        payload = {
            'producto_id': self.rec.id, 'tipo_regularizacion': 'EMITIR_NC',
            'cantidad_nc': 2, 'motivo_nc': 'no llegó', 'ejecutar_nc': True,
        }
        resp = self._post(URL_REG, payload, self.destino_b)
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertFalse(Dte.objects.filter(documento_afectado=self.dte).exists())

        resp = self._post(URL_REG, payload, self.origen)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(Dte.objects.filter(documento_afectado=self.dte, es_nota_credito=True).exists())

    def test_destino_si_puede_mercaderia_encontrada(self):
        resp = self._post(URL_REG, {
            'producto_id': self.rec.id,
            'tipo_regularizacion': 'MERCADERIA_ENCONTRADA',
            'cantidad_encontrada': 1,
        }, self.destino_b)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.talla_destino_b), 1)

    def test_maestro_opera_desde_cualquier_sucursal(self):
        maestro = crear_usuario(username='maestro-v4', rol='maestro')
        crear_empresa_user(maestro, self.emp_c, self.ajena)
        self.client.force_login(maestro)
        resp = self._post(URL_REG, {
            'producto_id': self.rec.id,
            'tipo_regularizacion': 'MERCADERIA_ENCONTRADA',
            'cantidad_encontrada': 1,
        }, self.ajena)
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_enviar_cambio_rechaza_talla_de_otra_sucursal(self):
        resp = self._post(URL_REG, {
            'producto_id': self.rec.id, 'tipo_regularizacion': 'ENVIAR_CAMBIO',
            'producto_envio_id': self.talla_ajena.id, 'cantidad_envio': 1,
            'motivo_envio': 'cambio', 'ejecutar_envio': True,
        }, self.origen)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(self._stock(self.talla_ajena), 5)
        self.assertFalse(Dte.objects.filter(documento_afectado=self.dte).exists())


class MasivaTest(_BaseRegularizacionV4):
    """B8-03 (cierre del DTE), B8-04 (dañadas no vuelven), B8-13 (lotes)."""

    def setUp(self):
        super().setUp()
        self.dte, self.linea = self._traspaso(
            self.destino_b, 3, tipo_documento='FACTURA ELECTRONICA',
        )

    def test_masiva_cierra_el_dte_y_crea_lote(self):
        rec = self._recepcion(self.dte, self.linea, self.destino_b,
                              esperada=3, arribado=1, faltante=2)
        base = self._stock(self.talla_origen)
        resp = self._post(URL_MASIVA, {
            'dte_id': self.dte.id, 'dte_numero': self.dte.numero_documento,
            'productos_ids': [rec.id], 'motivo': 'no llegaron',
        }, self.origen)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['dte_completado'])
        self.dte.refresh_from_db()
        self.assertEqual(self.dte.estado_dte, 'RECEPCIONADO_COMPLETO')
        self.assertEqual(self._stock(self.talla_origen), base + 2)
        self.assertEqual(self._lotes(self.talla_origen), 2)

    def test_masiva_no_devuelve_danadas_al_origen(self):
        rec = self._recepcion(self.dte, self.linea, self.destino_b,
                              esperada=3, arribado=2, faltante=1, danada=1)
        base = self._stock(self.talla_origen)
        resp = self._post(URL_MASIVA, {
            'dte_id': self.dte.id, 'dte_numero': self.dte.numero_documento,
            'productos_ids': [rec.id], 'motivo': 'faltante y rota',
        }, self.origen)
        self.assertEqual(resp.status_code, 200, resp.content)
        nc = Dte.objects.get(documento_afectado=self.dte, es_nota_credito=True)
        self.assertEqual(nc.unidades_productos, 2, 'La NC acredita faltante + dañada.')
        self.assertEqual(self._stock(self.talla_origen), base + 1,
                         'Solo la faltante vuelve al origen.')


class EmitirNCCantidadTest(_BaseRegularizacionV4):
    """B8-05: la NC individual no puede ser por 0, negativa ni exceder el tope."""

    def setUp(self):
        super().setUp()
        self.dte, self.linea = self._traspaso(
            self.destino_b, 3, tipo_documento='FACTURA ELECTRONICA',
        )
        self.rec = self._recepcion(self.dte, self.linea, self.destino_b,
                                   esperada=3, arribado=2, faltante=1)

    def _emitir(self, cantidad, rec=None):
        return self._post(URL_REG, {
            'producto_id': (rec or self.rec).id, 'tipo_regularizacion': 'EMITIR_NC',
            'cantidad_nc': cantidad, 'motivo_nc': 'no llegó', 'ejecutar_nc': True,
        }, self.origen)

    def test_cantidades_invalidas_no_emiten(self):
        base = self._stock(self.talla_origen)
        for cantidad in (0, -2, 2):
            resp = self._emitir(cantidad)
            self.assertEqual(resp.status_code, 400, (cantidad, resp.content))
        self.assertFalse(Dte.objects.filter(documento_afectado=self.dte).exists())
        self.assertEqual(self._stock(self.talla_origen), base)
        self.rec.refresh_from_db()
        self.assertNotEqual(self.rec.estado, 'REGULARIZADO')

    def test_linea_de_sobrante_puro_no_emite(self):
        rec = self._recepcion(self.dte, self.linea, self.destino_b,
                              esperada=3, arribado=5, faltante=0, sobrante=2,
                              estado='RECEPCIONADO_SOBRANTE')
        resp = self._emitir(0, rec=rec)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(Dte.objects.filter(documento_afectado=self.dte).exists())

    def test_cantidad_valida_emite_devuelve_faltante_y_crea_lote(self):
        base = self._stock(self.talla_origen)
        resp = self._emitir(1)
        self.assertEqual(resp.status_code, 200, resp.content)
        nc = Dte.objects.get(documento_afectado=self.dte, es_nota_credito=True)
        self.assertGreater(nc.monto_con_iva, 0)
        self.assertEqual(self._stock(self.talla_origen), base + 1)
        self.assertEqual(self._lotes(self.talla_origen), 1)


class AjustarCantidadTest(_BaseRegularizacionV4):
    """B8-06: el servidor pone el tope y calcula el estado."""

    def setUp(self):
        super().setUp()
        self.dte, self.linea = self._traspaso(self.destino, 2, tipo_documento='GUIA')
        self.rec = self._recepcion(self.dte, self.linea, self.destino,
                                   esperada=2, arribado=1, faltante=1)

    def _ajustar(self, nueva, estado='CUALQUIER_COSA'):
        return self._post(URL_REG, {
            'producto_id': self.rec.id, 'tipo_regularizacion': 'AJUSTAR',
            'nueva_cantidad': nueva, 'nuevo_estado': estado,
        }, self.destino)

    def test_no_supera_lo_esperado_ni_baja(self):
        for nueva in (50, 1, 0):
            resp = self._ajustar(nueva)
            self.assertEqual(resp.status_code, 400, (nueva, resp.content))
        self.assertEqual(self._stock(self.talla_destino), 1)

    def test_estado_lo_decide_el_servidor(self):
        resp = self._ajustar(2)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.estado, 'REGULARIZADO')
        self.assertEqual(self._stock(self.talla_destino), 2)
        self.assertEqual(self._lotes(self.talla_destino), 1)


class ListadoYDocumentoMuestranNCTest(_BaseRegularizacionV4):
    """B8-08 / B10-03 / B8-09: NC con referencias JSON visibles."""

    def setUp(self):
        super().setUp()
        self.dte, self.linea = self._traspaso(
            self.destino_b, 2, tipo_documento='FACTURA ELECTRONICA', numero=16929,
        )
        self.rec = self._recepcion(self.dte, self.linea, self.destino_b,
                                   esperada=2, arribado=0, faltante=2)
        resp = self._post(URL_REG, {
            'producto_id': self.rec.id, 'tipo_regularizacion': 'EMITIR_NC',
            'cantidad_nc': 2, 'motivo_nc': 'no llegó', 'ejecutar_nc': True,
        }, self.origen)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.nc = Dte.objects.get(documento_afectado=self.dte, es_nota_credito=True)
        self.assertTrue(self.nc.referencias.startswith('['))

    def test_listado_regularizados_trae_la_nc(self):
        self._sesion(self.origen)
        resp = self.client.get(URL_LISTADO + '?tab=regularizado')
        self.assertEqual(resp.status_code, 200, resp.content)
        fila = next(p for p in resp.json()['productos'] if p['id'] == self.rec.id)
        self.assertEqual(fila['nc_numero'], self.nc.numero_documento)
        self.assertEqual(fila['nc_id'], self.nc.id)
        self.assertEqual(fila['solucion_aplicada'], f'NC #{self.nc.numero_documento}')

    def test_dte_de_cambio_no_calza_por_prefijo(self):
        """'DTE #169' no es 'DTE #16929'."""
        Dte.objects.create(
            emisor=self.emp_a, receptor=self.emp_b, numero_documento=77,
            tipo_documento='GUIA', monto_neto=0, monto_con_iva=0,
            estado_pago='PENDIENTE', estado_dte='EMITIDO', responsable='t',
            fecha_emision='2026-09-02', fecha_vencimiento='2026-09-02',
            diasCredito=0, bultos=1, unidades_productos=1,
            tipo_transaccion='TRASPASO', sucursal=self.origen,
            referencias='Producto de cambio por DTE #169. NC #1.',
        )
        self._sesion(self.origen)
        resp = self.client.get(URL_LISTADO + '?tab=regularizado')
        fila = next(p for p in resp.json()['productos'] if p['id'] == self.rec.id)
        self.assertIsNone(fila['dte_cambio_numero'])

    def test_documento_imprimible_trae_la_nc(self):
        self._sesion(self.origen)
        resp = self.client.get(f'/app/dte/documento-regularizacion/{self.rec.id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['numero_documento'], self.nc.numero_documento)
        self.assertIsNotNone(resp.context['nc_generada'])
        self.assertEqual(resp.context['producto_original']['cantidad_problema'], 2)

    def test_documento_de_traspaso_ajeno_403(self):
        self._sesion(self.ajena)
        resp = self.client.get(f'/app/dte/documento-regularizacion/{self.rec.id}/')
        self.assertEqual(resp.status_code, 403)


class CancelarConDanadaTest(_BaseRegularizacionV4):
    """B8-10: esperada 3, llegaron 2 (1 dañada), faltó 1."""

    def test_cancelar_conserva_el_faltante(self):
        dte, linea = self._traspaso(self.destino, 3, tipo_documento='GUIA')
        rec = self._recepcion(dte, linea, self.destino,
                              esperada=3, arribado=2, faltante=1, danada=1)
        resp = self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'REGULARIZAR_CON_NC',
            'hacer_nc': False, 'cantidad_nc': 2, 'motivo_nc': 'x',
        }, self.destino)
        self.assertEqual(resp.status_code, 200, resp.content)

        resp = self._post(URL_CANCELAR, {'producto_id': rec.id, 'motivo': 'error'}, self.destino)
        self.assertEqual(resp.status_code, 200, resp.content)
        rec.refresh_from_db()
        self.assertEqual(rec.cantidad_faltante, 1)
        self.assertEqual(rec.stockArribado, 2)
        self.assertEqual(rec.estado, 'RECEPCIONADO_PARCIAL')


class CambioDirectoDeshabilitadoTest(_BaseRegularizacionV4):
    """B10-01 / B8-07: el cambio directo creaba stock de la nada."""

    def test_cambio_directo_400_sin_tocar_stock(self):
        dte, linea = self._traspaso(self.destino, 2, tipo_documento='GUIA')
        rec = self._recepcion(dte, linea, self.destino, esperada=2, arribado=0, faltante=2)
        resp = self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'CAMBIAR_PRODUCTO',
            'es_solicitud': False, 'nuevo_producto_id': self.talla_ajena.id,
        }, self.destino)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(self._stock(self.talla_ajena), 5)
        rec.refresh_from_db()
        self.assertEqual(rec.estado, 'FALTANTE')

    def test_tipo_desconocido_400(self):
        dte, linea = self._traspaso(self.destino, 2, tipo_documento='GUIA')
        rec = self._recepcion(dte, linea, self.destino, esperada=2, arribado=0, faltante=2)
        resp = self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'NO_EXISTE',
        }, self.destino)
        self.assertEqual(resp.status_code, 400, resp.content)


class FechasChileTest(_BaseRegularizacionV4):
    """B10-07: 23:30 del 26-sep en Santiago son las 02:30 UTC del 27."""

    AHORA_UTC = datetime(2026, 9, 27, 2, 30, tzinfo=dt_timezone.utc)

    def test_movimiento_y_nc_con_fecha_local(self):
        dte, linea = self._traspaso(self.destino_b, 3, tipo_documento='FACTURA ELECTRONICA')
        rec = self._recepcion(dte, linea, self.destino_b, esperada=3, arribado=0, faltante=3)
        with mock.patch('django.utils.timezone.now', return_value=self.AHORA_UTC):
            resp = self._post(URL_REG, {
                'producto_id': rec.id, 'tipo_regularizacion': 'MERCADERIA_ENCONTRADA',
                'cantidad_encontrada': 1,
            }, self.destino_b)
            self.assertEqual(resp.status_code, 200, resp.content)
            resp = self._post(URL_REG, {
                'producto_id': rec.id, 'tipo_regularizacion': 'EMITIR_NC',
                'cantidad_nc': 2, 'motivo_nc': 'x', 'ejecutar_nc': True,
            }, self.origen)
            self.assertEqual(resp.status_code, 200, resp.content)
        mov = Movimientos_Producto.objects.filter(
            dte=dte, concepto='REGULARIZACION_TRASPASO',
        ).latest('id')
        self.assertEqual(mov.fecha, date(2026, 9, 26))
        nc = Dte.objects.get(documento_afectado=dte, es_nota_credito=True)
        self.assertEqual(nc.fecha_emision, date(2026, 9, 26))


class BuscarProductosEmisorTest(_BaseRegularizacionV4):
    """B10-14: sin costo; solo emisores con traspaso abierto hacia la sesión."""

    def setUp(self):
        super().setUp()
        self.dte, self.linea = self._traspaso(
            self.destino_b, 2, tipo_documento='FACTURA ELECTRONICA',
        )
        self.rec = self._recepcion(self.dte, self.linea, self.destino_b,
                                   esperada=2, arribado=0, faltante=2)

    def _get(self, params, sucursal):
        self._sesion(sucursal)
        return self.client.get(URL_BUSCAR_EMISOR, params)

    def test_receptor_busca_en_su_emisor_sin_costo(self):
        resp = self._get({'query': 'Zap', 'sucursal_emisor_id': self.origen.id}, self.destino_b)
        self.assertEqual(resp.status_code, 200, resp.content)
        productos = resp.json()['productos']
        self.assertTrue(productos)
        self.assertNotIn('costo', productos[0])

    def test_sucursal_sin_traspaso_403(self):
        resp = self._get({'query': 'Zap', 'sucursal_emisor_id': self.ajena.id}, self.destino_b)
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_sucursal_null_400(self):
        resp = self._get({'query': 'Zap', 'sucursal_emisor_id': 'null'}, self.destino_b)
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_por_recepcion_deriva_el_emisor(self):
        resp = self._get({'query': 'Zap', 'recepcion_id': self.rec.id}, self.destino_b)
        self.assertEqual(resp.status_code, 200, resp.content)
        resp = self._get({'query': 'Zap', 'recepcion_id': self.rec.id}, self.ajena)
        self.assertEqual(resp.status_code, 403, resp.content)


class SolicitudesTest(_BaseRegularizacionV4):
    """B8-11: sin duplicados y la NC deja la solicitud EJECUTADA."""

    def test_una_sola_solicitud_y_la_nc_la_cierra(self):
        dte, linea = self._traspaso(self.destino_b, 2, tipo_documento='FACTURA ELECTRONICA')
        rec = self._recepcion(dte, linea, self.destino_b, esperada=2, arribado=0, faltante=2)
        payload = {
            'producto_id': rec.id, 'tipo_regularizacion': 'SOLICITAR_NC',
            'es_solicitud': True, 'tipo_solucion': 'NOTA_CREDITO',
            'justificacion': 'no llegó', 'cantidad_solicitud': 2,
        }
        resp = self._post(URL_REG, payload, self.destino_b)
        self.assertEqual(resp.status_code, 200, resp.content)
        resp = self._post(URL_REG, payload, self.destino_b)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(Solicitud_Regularizacion.objects.filter(producto_recepcionado=rec).count(), 1)

        resp = self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'EMITIR_NC',
            'cantidad_nc': 2, 'motivo_nc': 'x', 'ejecutar_nc': True,
        }, self.origen)
        self.assertEqual(resp.status_code, 200, resp.content)
        sol = Solicitud_Regularizacion.objects.get(producto_recepcionado=rec)
        self.assertEqual(sol.estado, 'EJECUTADA')
        self.assertIsNotNone(sol.nota_credito_id)
        self.assertIsNotNone(sol.fecha_ejecucion)


class LotesRegularizacionTest(_BaseRegularizacionV4):
    """B8-13: stock plano y lotes se mueven juntos (entrada y cancelación)."""

    def test_mercaderia_encontrada_crea_lote_y_cancelar_lo_consume(self):
        dte, linea = self._traspaso(self.destino, 2, tipo_documento='GUIA')
        rec = self._recepcion(dte, linea, self.destino, esperada=2, arribado=0, faltante=2)
        resp = self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'MERCADERIA_ENCONTRADA',
            'cantidad_encontrada': 2,
        }, self.destino)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.talla_destino), 2)
        self.assertEqual(self._lotes(self.talla_destino), 2)

        resp = self._post(URL_CANCELAR, {'producto_id': rec.id, 'motivo': 'error'}, self.destino)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.talla_destino), 0)
        self.assertEqual(self._lotes(self.talla_destino), 0)

    def test_llego_todo_crea_lote_en_destino(self):
        dte, linea = self._traspaso(self.destino, 3, tipo_documento='GUIA')
        self._recepcion(dte, linea, self.destino, esperada=3, arribado=1, faltante=2)
        resp = self._post(URL_LLEGO_TODO, {'dte_id': dte.id, 'motivo': 'estaba'}, self.destino)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.talla_destino), 3)
        self.assertEqual(self._lotes(self.talla_destino), 2,
                         'El lote de la recepción no existe en el test; el del cierre sí.')


class ComandoCerrarDtesTest(_BaseRegularizacionV4):
    """B8-03 (datos históricos): dry-run no escribe; --apply cierra."""

    def test_dry_run_y_apply(self):
        from io import StringIO
        from django.core.management import call_command

        dte, linea = self._traspaso(self.destino_b, 2, tipo_documento='FACTURA ELECTRONICA')
        self._recepcion(dte, linea, self.destino_b, esperada=2, arribado=0,
                        faltante=2, estado='REGULARIZADO')
        dte_abierto, linea2 = self._traspaso(self.destino_b, 1, tipo_documento='FACTURA ELECTRONICA')
        self._recepcion(dte_abierto, linea2, self.destino_b, esperada=1, arribado=0, faltante=1)

        out = StringIO()
        call_command('regularizacion_cerrar_dtes_sin_lineas_abiertas', stdout=out)
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'RECEPCIONADO_PARCIAL')
        self.assertIn('a cerrar): 1', out.getvalue())

        call_command('regularizacion_cerrar_dtes_sin_lineas_abiertas', '--apply', stdout=StringIO())
        dte.refresh_from_db()
        dte_abierto.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'RECEPCIONADO_COMPLETO')
        self.assertEqual(dte_abierto.estado_dte, 'RECEPCIONADO_PARCIAL')


class SolicitudGateTest(_BaseRegularizacionV4):
    """Bandeja de solicitudes (sin UI, B16-10): solo las partes / el emisor."""

    def test_decidir_solo_el_emisor(self):
        dte, linea = self._traspaso(self.destino_b, 2, tipo_documento='FACTURA ELECTRONICA')
        rec = self._recepcion(dte, linea, self.destino_b, esperada=2, arribado=0, faltante=2)
        resp = self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'SOLICITAR_NC',
            'justificacion': 'no llegó', 'cantidad_solicitud': 2,
        }, self.destino_b)
        self.assertEqual(resp.status_code, 200, resp.content)
        sol = Solicitud_Regularizacion.objects.get(producto_recepcionado=rec)

        for suc in (self.ajena, self.destino_b):
            resp = self._post('/app/dte/decidir_solicitud/', {
                'solicitud_id': sol.id, 'decision': 'NOTA_CREDITO',
            }, suc)
            self.assertEqual(resp.status_code, 403, (suc.alias, resp.content))
        resp = self._post('/app/dte/decidir_solicitud/', {
            'solicitud_id': sol.id, 'decision': 'NOTA_CREDITO',
        }, self.origen)
        self.assertEqual(resp.status_code, 200, resp.content)

        self._sesion(self.ajena)
        resp = self.client.get(f'/app/dte/obtener_solicitud_producto/{rec.id}/')
        self.assertEqual(resp.status_code, 403, resp.content)


class PdfEscapaTest(_BaseRegularizacionV4):
    """B8-15: '<' + letra rompía el parser de reportlab (500)."""

    def test_busqueda_con_menor_que(self):
        dte, linea = self._traspaso(self.destino, 2, tipo_documento='GUIA')
        self._recepcion(dte, linea, self.destino, esperada=2, arribado=0, faltante=2)
        self._sesion(self.destino)
        resp = self.client.get(URL_PDF, {'tab': 'todos', 'buscar': '<b'})
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.assertEqual(resp['Content-Type'], 'application/pdf')


# ── Revisión adversarial (26-sep) ─────────────────────────────────────────────

class LoteDevolucionConservaAntiguedadTest(_BaseRegularizacionV4):
    """B8-13 (revisión): el lote de las unidades que VUELVEN al origen conserva
    la fecha del despacho (TRASPASO_SALIDA); LoteProducto.fecha_ingreso es
    auto_now_add y sin el update() nacía con la fecha de hoy."""

    SALIDA = datetime(2026, 5, 11, 12, 28)

    def _fechar_salida(self, dte):
        Movimientos_Producto.objects.filter(
            dte=dte, concepto='TRASPASO_SALIDA',
        ).update(fecha=self.SALIDA.date(), hora=self.SALIDA.time())

    def _lote_devolucion(self):
        return LoteProducto.objects.filter(
            producto_talla=self.talla_origen,
            movimiento__concepto__in=['DEVOLUCION_NC', 'REGULARIZACION_TRASPASO'],
        ).latest('id')

    def _fecha_local(self, lote):
        return timezone.localtime(lote.fecha_ingreso).replace(tzinfo=None)

    def test_emitir_nc_lote_con_fecha_de_salida(self):
        dte, linea = self._traspaso(self.destino_b, 2, tipo_documento='FACTURA ELECTRONICA')
        self._fechar_salida(dte)
        rec = self._recepcion(dte, linea, self.destino_b, esperada=2, arribado=0, faltante=2)
        resp = self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'EMITIR_NC',
            'cantidad_nc': 2, 'motivo_nc': 'no llegó', 'ejecutar_nc': True,
        }, self.origen)
        self.assertEqual(resp.status_code, 200, resp.content)
        lote = self._lote_devolucion()
        self.assertEqual(lote.cantidad_inicial, 2)
        self.assertEqual(self._fecha_local(lote), self.SALIDA)

    def test_regularizar_guia_y_masiva_con_fecha_de_salida(self):
        # Regularizar con NC en guía (no emite NC): devolución al origen.
        dte, linea = self._traspaso(self.destino, 3, tipo_documento='GUIA')
        self._fechar_salida(dte)
        rec = self._recepcion(dte, linea, self.destino, esperada=3, arribado=2, faltante=1)
        resp = self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'REGULARIZAR_CON_NC',
            'hacer_nc': True, 'cantidad_nc': 1, 'motivo_nc': 'no llegó',
        }, self.destino)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._fecha_local(self._lote_devolucion()), self.SALIDA)

        # NC masiva.
        dte2, linea2 = self._traspaso(self.destino_b, 2, tipo_documento='FACTURA ELECTRONICA')
        self._fechar_salida(dte2)
        rec2 = self._recepcion(dte2, linea2, self.destino_b, esperada=2, arribado=1, faltante=1)
        resp = self._post(URL_MASIVA, {
            'dte_id': dte2.id, 'dte_numero': dte2.numero_documento,
            'productos_ids': [rec2.id], 'motivo': 'no llegó',
        }, self.origen)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._fecha_local(self._lote_devolucion()), self.SALIDA)

    def test_entrada_al_destino_nace_hoy(self):
        """Mercadería Encontrada entra al DESTINO: fecha de hoy, como la recepción."""
        dte, linea = self._traspaso(self.destino, 2, tipo_documento='GUIA')
        self._fechar_salida(dte)
        rec = self._recepcion(dte, linea, self.destino, esperada=2, arribado=0, faltante=2)
        resp = self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'MERCADERIA_ENCONTRADA',
            'cantidad_encontrada': 1,
        }, self.destino)
        self.assertEqual(resp.status_code, 200, resp.content)
        lote = LoteProducto.objects.filter(producto_talla=self.talla_destino).latest('id')
        self.assertEqual(timezone.localtime(lote.fecha_ingreso).date(), timezone.localdate())


class SolicitudSeLiberaAlCerrarSinNCTest(_BaseRegularizacionV4):
    """B8-11 (revisión): cerrar la línea por Mercadería Encontrada / Ajustar /
    sin NC / "Llegó todo" deja la solicitud CANCELADA; tras cancelar la
    regularización el receptor puede volver a solicitar."""

    def _solicitar(self, rec):
        return self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'SOLICITAR_NC',
            'justificacion': 'no llegó', 'cantidad_solicitud': 2,
        }, self.destino_b)

    def test_mercaderia_encontrada_cancelar_y_volver_a_solicitar(self):
        dte, linea = self._traspaso(self.destino_b, 2, tipo_documento='FACTURA ELECTRONICA')
        rec = self._recepcion(dte, linea, self.destino_b, esperada=2, arribado=0, faltante=2)
        self.assertEqual(self._solicitar(rec).status_code, 200)

        resp = self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'MERCADERIA_ENCONTRADA',
            'cantidad_encontrada': 2,
        }, self.destino_b)
        self.assertEqual(resp.status_code, 200, resp.content)
        sol = Solicitud_Regularizacion.objects.get(producto_recepcionado=rec)
        self.assertEqual(sol.estado, 'CANCELADA')
        self.assertIn('Mercadería Encontrada', sol.decision_emisor)

        resp = self._post(URL_CANCELAR, {'producto_id': rec.id, 'motivo': 'error'}, self.destino_b)
        self.assertEqual(resp.status_code, 200, resp.content)
        resp = self._solicitar(rec)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(
            Solicitud_Regularizacion.objects.filter(
                producto_recepcionado=rec, estado='PENDIENTE',
            ).count(), 1,
        )

    def test_cancelar_libera_solicitud_colgada_de_antes(self):
        """Datos previos: línea REGULARIZADO con una solicitud PENDIENTE viva."""
        dte, linea = self._traspaso(self.destino_b, 2, tipo_documento='FACTURA ELECTRONICA')
        rec = self._recepcion(dte, linea, self.destino_b, esperada=2, arribado=0, faltante=2)
        self.assertEqual(self._solicitar(rec).status_code, 200)
        Productos_Recepcionados.objects.filter(id=rec.id).update(estado='REGULARIZADO')

        resp = self._post(URL_CANCELAR, {'producto_id': rec.id, 'motivo': 'reabrir'}, self.destino_b)
        self.assertEqual(resp.status_code, 200, resp.content)
        sol = Solicitud_Regularizacion.objects.get(producto_recepcionado=rec)
        self.assertEqual(sol.estado, 'CANCELADA')
        self.assertEqual(self._solicitar(rec).status_code, 200)

    def test_solicitud_sigue_viva_si_la_linea_queda_abierta(self):
        dte, linea = self._traspaso(self.destino_b, 2, tipo_documento='FACTURA ELECTRONICA')
        rec = self._recepcion(dte, linea, self.destino_b, esperada=2, arribado=0, faltante=2)
        self.assertEqual(self._solicitar(rec).status_code, 200)
        resp = self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'MERCADERIA_ENCONTRADA',
            'cantidad_encontrada': 1,
        }, self.destino_b)
        self.assertEqual(resp.status_code, 200, resp.content)
        sol = Solicitud_Regularizacion.objects.get(producto_recepcionado=rec)
        self.assertEqual(sol.estado, 'PENDIENTE')


class EnviarCambioCantidadTest(_BaseRegularizacionV4):
    """B8-05/B8-02 (revisión): ENVIAR_CAMBIO con cantidad negativa subía el
    stock del origen y emitía NC + guía con montos negativos."""

    def test_cantidad_negativa_400_sin_escribir(self):
        dte, linea = self._traspaso(self.destino_b, 2, tipo_documento='FACTURA ELECTRONICA')
        rec = self._recepcion(dte, linea, self.destino_b, esperada=2, arribado=0, faltante=2)
        base = self._stock(self.talla_origen)
        n_dtes = Dte.objects.count()
        resp = self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'ENVIAR_CAMBIO',
            'ejecutar_envio': True, 'producto_envio_id': self.talla_origen.id,
            'cantidad_envio': -5, 'motivo_envio': 'prueba negativa',
        }, self.origen)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(self._stock(self.talla_origen), base)
        self.assertEqual(Dte.objects.count(), n_dtes)
        rec.refresh_from_db()
        self.assertEqual(rec.estado, 'FALTANTE')


class CancelarTrasLlegoTodoTest(_BaseRegularizacionV4):
    """B8-01/B8-10 (revisión): Mercadería Encontrada parcial + "Llegó todo" +
    Cancelar reabría la línea con TODO el faltante y un segundo "Llegó todo"
    creaba una unidad fantasma en el destino."""

    def test_no_reabre_linea_cerrada_con_llego_todo(self):
        dte, linea = self._traspaso(self.destino, 2, tipo_documento='GUIA')
        rec = self._recepcion(dte, linea, self.destino, esperada=2, arribado=0, faltante=2)
        resp = self._post(URL_REG, {
            'producto_id': rec.id, 'tipo_regularizacion': 'MERCADERIA_ENCONTRADA',
            'cantidad_encontrada': 1,
        }, self.destino)
        self.assertEqual(resp.status_code, 200, resp.content)
        resp = self._post(URL_LLEGO_TODO, {'dte_id': dte.id, 'motivo': 'estaba'}, self.destino)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.talla_destino), 2)

        resp = self._post(URL_CANCELAR, {'producto_id': rec.id, 'motivo': 'error'}, self.destino)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('Llegó todo', resp.json()['error'])
        rec.refresh_from_db()
        self.assertEqual(rec.estado, 'RECEPCIONADO_OK')
        self.assertEqual(self._stock(self.talla_destino), 2)
        self.assertEqual(self._lotes(self.talla_destino), 2)


class PdfTabDesconocidoTest(_BaseRegularizacionV4):
    """B8-15 (revisión): el `tab` crudo iba al título del PDF."""

    def test_tab_con_menor_que(self):
        dte, linea = self._traspaso(self.destino, 2, tipo_documento='GUIA')
        self._recepcion(dte, linea, self.destino, esperada=2, arribado=0, faltante=2)
        self._sesion(self.destino)
        resp = self.client.get(URL_PDF, {'tab': '<b'})
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertIn('regularizar_pendiente_', resp['Content-Disposition'])
