"""
El panel Regularizar de /app/recepcion-dte/ tiene que decir a QUÉ BODEGA se despachó.

Reporte 2026-08-19 (FACTURA ELECTRONICA #17098, EDEL como origen): la fila del
documento mostraba "EDEL · EDELMIRA TEBES Y CIA. LTDA." y nada más. El emisor no
tenía forma de saber a qué tienda había despachado ni desde la tabla ni desde el
modal "Ver o corregir", porque `obtener_productos_regularizar` nunca enviaba la
sucursal destino — solo `sucursal_origen` — y el modal de detalle mostraba
"Receptor", que es la razón social y en un traspaso interno suele ser la MISMA
empresa del origen.

Cubre además el fallback: las recepciones anteriores al fix de 2026-07 tienen
`Productos_Recepcionados.sucursal_destino` en NULL, así que el destino debe
derivarse del movimiento TRASPASO_SALIDA del documento.
"""
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase, override_settings

from app.models import (
    Dte, Dte_Productos, Movimientos_Producto, Productos_Recepcionados,
)
from .factories import (
    crear_empresa,
    crear_empresa_user,
    crear_producto_con_talla,
    crear_sucursal,
    crear_usuario,
)

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


def _patch_permisos():
    return (
        mock.patch('app.views.PermisoRol.tiene_permiso', return_value=True),
        mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True),
    )


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class RegularizarMuestraDestinoTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa(nombre='Empresa Reg')
        self.origen = crear_sucursal(empresa=self.empresa, alias='CD-REG')
        self.destino = crear_sucursal(empresa=self.empresa, alias='TIENDA-REG')
        self.user = crear_usuario(username='user-reg', rol='administrador')
        crear_empresa_user(self.user, self.empresa, self.origen)

        _, self.talla = crear_producto_con_talla(
            self.origen, articulo='ZAP-REG', talla='40', sku=4400001, stock=30,
        )

        self.dte = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=17098, tipo_documento='FACTURA ELECTRONICA',
            monto_neto=Decimal('12000'), monto_con_iva=Decimal('14280'),
            estado_pago='PENDIENTE', estado_dte='RECEPCIONADO_PARCIAL',
            responsable='tester', fecha_emision='2026-08-13',
            fecha_vencimiento='2026-08-13', diasCredito=0, bultos=1,
            unidades_productos=12, tipo_transaccion='TRASPASO',
            sucursal=self.origen,
        )
        self.linea = Dte_Productos.objects.create(
            dte=self.dte, productoTalla=self.talla,
            descripcion='ZAP-REG - Talla 40',
            costo=1000, sobreprecio=0, precio=1000, stock=12, activo=True,
        )
        Movimientos_Producto.objects.create(
            dte=self.dte, ProductoTalla=self.talla,
            sucursal_origen=self.origen, sucursal_destino=self.destino,
            cantidad=-12, concepto='TRASPASO_SALIDA',
            tipo_movimiento='EGRESO', estado='COMPLETADO', responsable='tester',
        )

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.origen.id
        session['idEmpresaActual'] = self.empresa.id
        session['alias'] = self.origen.alias
        session.save()

    def _crear_recepcion(self, **kwargs):
        defaults = {
            'dte': self.dte,
            'dte_producto': self.linea,
            'producto_talla': self.talla,
            'sucursal_destino': self.destino,
            'stockArribado': 0,
            'cantidad_esperada': 12,
            'cantidad_danada': 0,
            'cantidad_faltante': 12,
            'cantidad_sobrante': 0,
            'estado': 'FALTANTE',
            'observaciones': '',
            'recepcionado_por': 'tester',
        }
        defaults.update(kwargs)
        return Productos_Recepcionados.objects.create(**defaults)

    def _lineas_regularizar(self):
        p1, p2 = _patch_permisos()
        with p1, p2:
            resp = self.client.get('/app/dte/obtener_productos_regularizar/?tab=pendiente')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'], data)
        return data['productos']

    def test_la_linea_trae_la_bodega_destino(self):
        self._crear_recepcion()
        lineas = self._lineas_regularizar()
        self.assertEqual(len(lineas), 1)
        self.assertEqual(lineas[0]['sucursal_origen'], 'CD-REG')
        self.assertEqual(lineas[0]['sucursal_destino'], 'TIENDA-REG')
        self.assertEqual(lineas[0]['sucursal_destino_id'], self.destino.id)
        self.assertTrue(lineas[0]['soy_emisor'])
        self.assertFalse(lineas[0]['soy_receptor'])

    def test_recepcion_legacy_sin_destino_lo_deriva_del_documento(self):
        """Las filas anteriores al fix de 2026-07 tienen sucursal_destino en NULL."""
        recepcion = self._crear_recepcion()
        Productos_Recepcionados.objects.filter(id=recepcion.id).update(sucursal_destino=None)

        lineas = self._lineas_regularizar()
        self.assertEqual(lineas[0]['sucursal_destino'], 'TIENDA-REG')
        self.assertEqual(lineas[0]['sucursal_destino_id'], self.destino.id)

    def test_el_destino_ve_su_rol_de_receptor(self):
        self._crear_recepcion()
        session = self.client.session
        session['idSucursalActual'] = self.destino.id
        session['alias'] = self.destino.alias
        session.save()

        lineas = self._lineas_regularizar()
        self.assertEqual(len(lineas), 1)
        self.assertFalse(lineas[0]['soy_emisor'])
        self.assertTrue(lineas[0]['soy_receptor'])

    def test_detalle_del_dte_incluye_el_destino(self):
        self._crear_recepcion()
        p1, p2 = _patch_permisos()
        with p1, p2:
            resp = self.client.get(
                f'/app/dte/obtener_detalle_dte_recepcionado/?dte_id={self.dte.id}')
        self.assertEqual(resp.status_code, 200, resp.content)
        dte = resp.json()['dte']
        self.assertEqual(dte['sucursal_origen'], 'CD-REG')
        self.assertEqual(dte['sucursal_destino'], 'TIENDA-REG')

    def test_sin_movimiento_de_salida_no_revienta(self):
        """Fail-soft: sin destino resoluble la fila igual se lista, con '-'."""
        self._crear_recepcion(sucursal_destino=None)
        Movimientos_Producto.objects.filter(dte=self.dte).update(sucursal_destino=None)

        lineas = self._lineas_regularizar()
        self.assertEqual(len(lineas), 1)
        self.assertEqual(lineas[0]['sucursal_destino'], '-')
        self.assertIsNone(lineas[0]['sucursal_destino_id'])
