"""
TXT Acepta de las NC de Recepción DTE agrupado como la factura de emisionDTE.

La factura del traspaso sale una línea por variante (artículo + marca + color)
con el desglose de tallas ("2:38 1:39 3:40") y sin SKU. Las NC que se emiten
desde /app/recepcion-dte/ salían cada una a su manera: el ajuste del emisor y
la regularización una línea por SKU, y la descarga agrupando solo por artículo
(mezclando colores). Ahora todas usan la misma agrupación que la factura.

Casos:
1. El detalle de la NC es idéntico al de la factura para las mismas líneas.
2. La descarga /app/dte/<id>/txt-acepta/ de una NC de traspaso sale agrupada
   y separa colores; sin SKU.
3. El ajuste del emisor ("Ajustar") escribe el TXT agrupado.
4. Una NC de venta (no traspaso) no entra en este formato.
"""
import json
import os
import tempfile
from decimal import Decimal
from unittest import mock

from django.test import TestCase, Client, override_settings

from app.models import (
    AtributoOpcion, Dte, Dte_Productos, Movimientos_Producto, Producto_Talla,
    Productos_Atributos,
)
from app.views import (
    _detalle_txt_nc_traspaso, _es_nc_de_traspaso, _items_txt_desde_dte_productos,
)
from app.views_modulo_documentos import construir_detalle_txt_desde_dte_productos
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla, crear_correlativo,
)


def _patch_permisos():
    return (
        mock.patch('app.views.PermisoRol.tiene_permiso', return_value=True),
        mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True),
    )


def _lineas_detalle_txt(contenido):
    """Líneas de <Detalle> del TXT: entre el separador '~' y la siguiente
    línea que no es de ítem. Cada una arranca con IndExe y luego NmbItem."""
    lineas = contenido.splitlines()
    inicio = lineas.index('~') + 1
    detalle = []
    for linea in lineas[inicio:]:
        campos = linea.split('|')
        # Las líneas de ítem tienen cantidad numérica en la 4ª posición.
        if len(campos) > 4 and campos[3].isdigit():
            detalle.append(linea)
        else:
            break
    return detalle


class TxtNcRecepcionAgrupadoTest(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.origen)
        crear_correlativo(self.origen, tipo_dte='NOTA DE CREDITO')
        crear_correlativo(self.origen, tipo_dte='AJUSTE TRASPASO')

        marca = Productos_Atributos.objects.create(nombre='Marca', descripcion='m')
        color = Productos_Atributos.objects.create(nombre='Color', descripcion='c')
        nike = AtributoOpcion.objects.create(atributo=marca, valor='NIKE')
        negro = AtributoOpcion.objects.create(atributo=color, valor='NEGRO')
        beige = AtributoOpcion.objects.create(atributo=color, valor='BEIGE')

        prod_negro, self.t38 = crear_producto_con_talla(
            self.origen, articulo='ZAP-1', talla='38', sku=7000038, stock=20,
            atributo1=nike, atributo2=negro,
        )
        self.t39 = Producto_Talla.objects.create(producto=prod_negro, sku=7000039, stock=20, talla='39')
        self.t40 = Producto_Talla.objects.create(producto=prod_negro, sku=7000040, stock=20, talla='40')
        _, self.b38 = crear_producto_con_talla(
            self.origen, articulo='ZAP-1', talla='38', sku=8000038, stock=20,
            atributo1=nike, atributo2=beige,
        )

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.origen.id
        session['idEmpresaActual'] = self.empresa.id
        session['alias'] = self.origen.alias
        session.save()

    def _traspaso(self, numero=5000):
        cantidades = [(self.t38, 2), (self.t39, 1), (self.t40, 3), (self.b38, 1)]
        total = sum(c for _, c in cantidades)
        dte = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=numero, tipo_documento='FACTURA ELECTRONICA',
            monto_neto=Decimal(total * 1000), monto_con_iva=Decimal(total * 1190),
            estado_pago='PENDIENTE', estado_dte='EMITIDO', responsable='tester',
            fecha_emision='2026-09-01', fecha_vencimiento='2026-09-01',
            diasCredito=0, bultos=1, unidades_productos=total,
            tipo_transaccion='TRASPASO', sucursal=self.origen,
        )
        lineas = []
        for talla, cantidad in cantidades:
            lineas.append(Dte_Productos.objects.create(
                dte=dte, productoTalla=talla, descripcion=f'ZAP-1 - Talla {talla.talla}',
                costo=100, sobreprecio=0, precio=1000, stock=cantidad, activo=True,
            ))
            Movimientos_Producto.objects.create(
                dte=dte, ProductoTalla=talla,
                sucursal_origen=self.origen, sucursal_destino=self.destino,
                cantidad=-cantidad, concepto='TRASPASO_SALIDA',
                tipo_movimiento='EGRESO', estado='COMPLETADO', responsable='tester',
            )
        return dte, lineas

    def _nc_total(self, dte, lineas, numero=9500):
        total = sum(l.stock for l in lineas)
        nc = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=numero, tipo_documento='NOTA DE CREDITO',
            monto_neto=Decimal(total * 1000), monto_con_iva=Decimal(total * 1190),
            estado_pago='PAGADO', estado_dte='EMITIDO', responsable='tester',
            fecha_emision='2026-09-02', fecha_vencimiento='2026-09-02',
            diasCredito=0, bultos=0, unidades_productos=total,
            tipo_transaccion='ANULACION', sucursal=self.origen,
            es_nota_credito=True, documento_afectado=dte,
            referencias=json.dumps([{'tipo_documento': 33, 'folio': dte.numero_documento,
                                     'fecha': '2026-09-01', 'razon': 1}]),
        )
        for l in lineas:
            Dte_Productos.objects.create(
                dte=nc, productoTalla=l.productoTalla, descripcion=l.descripcion,
                costo=l.costo, sobreprecio=0, precio=l.precio, stock=l.stock, activo=True,
            )
        return nc

    # ------------------------------------------------------------------
    def test_detalle_nc_identico_al_de_la_factura(self):
        dte, lineas = self._traspaso()
        detalle_factura = construir_detalle_txt_desde_dte_productos(lineas, 33)
        detalle_nc = _detalle_txt_nc_traspaso(_items_txt_desde_dte_productos(lineas))

        self.assertEqual(detalle_nc, detalle_factura)
        # Una línea por color, con el desglose de tallas y sin SKU.
        self.assertEqual(len(detalle_nc), 2)
        negro = next(d for d in detalle_nc if 'NEGRO' in d['nombre'])
        self.assertEqual(negro['codigo'], 'ZAP-1')
        self.assertIn('2:38 1:39 3:40', negro['nombre'])
        self.assertEqual(negro['cantidad'], 6)
        self.assertEqual(negro['monto_item'], 6000)
        self.assertNotIn('7000038', json.dumps(detalle_nc))

    def test_descarga_txt_de_nc_de_traspaso_sale_agrupada(self):
        dte, lineas = self._traspaso(numero=5100)
        nc = self._nc_total(dte, lineas, numero=9501)
        self.assertTrue(_es_nc_de_traspaso(nc))

        p1, p2 = _patch_permisos()
        with p1, p2:
            resp = self.client.get(f'/app/dte/{nc.id}/txt-acepta/')
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        contenido = resp.content.decode('utf-8')

        detalle = _lineas_detalle_txt(contenido)
        self.assertEqual(len(detalle), 2, contenido)
        self.assertTrue(any('ZAP-1 NIKE NEGRO 2:38 1:39 3:40' in l for l in detalle), detalle)
        self.assertTrue(any('ZAP-1 NIKE BEIGE 1:38' in l for l in detalle), detalle)
        for sku in ('7000038', '7000039', '7000040', '8000038'):
            self.assertNotIn(sku, contenido)

    def test_ajuste_del_emisor_escribe_el_txt_agrupado(self):
        dte, lineas = self._traspaso(numero=5200)
        with tempfile.TemporaryDirectory() as media:
            with override_settings(MEDIA_ROOT=media):
                p1, p2 = _patch_permisos()
                with p1, p2:
                    resp = self.client.post(
                        '/app/dte/ajustar_traspaso/',
                        data=json.dumps({
                            'dte_id': dte.id,
                            'ajustes': [
                                {'dte_producto_id': lineas[0].id, 'nueva_cantidad': 0},  # -2 t38
                                {'dte_producto_id': lineas[2].id, 'nueva_cantidad': 1},  # -2 t40
                            ],
                            'motivo': 'error de bodega',
                        }),
                        content_type='application/json',
                    )
                self.assertEqual(resp.status_code, 200, resp.content)
                carpeta = os.path.join(media, 'documentos_electronicos', 'nc')
                archivos = os.listdir(carpeta)
                self.assertEqual(len(archivos), 1, archivos)
                with open(os.path.join(carpeta, archivos[0]), encoding='utf-8') as f:
                    contenido = f.read()

        detalle = _lineas_detalle_txt(contenido)
        self.assertEqual(len(detalle), 1, contenido)
        self.assertIn('ZAP-1 NIKE NEGRO 2:38 2:40', detalle[0])
        self.assertNotIn('7000038', contenido)

    def test_nc_de_venta_no_usa_el_formato_de_recepcion(self):
        dte, lineas = self._traspaso(numero=5300)
        dte.tipo_transaccion = 'VENTA'
        dte.save(update_fields=['tipo_transaccion'])
        nc = self._nc_total(dte, lineas, numero=9502)
        self.assertFalse(_es_nc_de_traspaso(nc))
