"""
Dashboards KPI (`views_dashboards_kpi`):

* Documentos: las notas de crédito RESTAN en "Monto neto" y en los listados
  por monto (por tipo, top emisores); bruto y NC viajan aparte.
* Despachos: eficacia de despacho por tienda (vendido a 30 d / despachado,
  proxy por (tienda, sku) topado en lo despachado) y días emisión→recepción
  por tienda.
"""
import json
from datetime import timedelta
from unittest import skipUnless

from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone

from app.models import Dte, ModuloSistema, Movimientos_Producto, OpcionMenu, PermisoRol
from app.tests.factories import (
    crear_empresa, crear_producto_con_talla, crear_sucursal, crear_usuario,
)


def _dte(empresa, sucursal, numero, tipo_documento, monto, tipo_transaccion,
         es_nc=False, **extra):
    hoy = timezone.localdate()
    campos = dict(
        emisor=empresa, receptor=None, numero_documento=numero,
        tipo_documento=tipo_documento, monto_con_iva=monto,
        monto_neto=round(monto / 1.19), descuento=0,
        estado_pago='PAGADO', estado_dte='EMITIDO', responsable='test',
        fecha_emision=hoy, fecha_vencimiento=hoy, diasCredito=0, bultos=0,
        unidades_productos=1, tipo_transaccion=tipo_transaccion,
        sucursal=sucursal, es_nota_credito=es_nc,
        hora=timezone.localtime().time(),
    )
    campos.update(extra)
    return Dte.objects.create(**campos)


def _mov(pt, origen, cantidad, fecha, concepto, destino=None, dte=None):
    return Movimientos_Producto.objects.create(
        dte=dte, ProductoTalla=pt, sucursal_origen=origen, sucursal_destino=destino,
        cantidad=cantidad, costo=1000, fecha=fecha, concepto=concepto, estado='COMPLETADO',
    )


# La eficacia de despachos es una consulta cruda de PostgreSQL (GREATEST/LEAST,
# fecha + entero): en SQLite no corre. Correr con la BD local de PostgreSQL.
SOLO_POSTGRES = skipUnless(connection.vendor == 'postgresql', 'SQL crudo de PostgreSQL')


class _DashboardBase(TestCase):
    def setUp(self):
        self.user = crear_usuario(username='kpi', rol='administrador')
        # Las APIs cuelgan del permiso de pantalla (middleware_permisos);
        # is_superuser no otorga nada, hay que dar el PermisoRol.
        modulo = ModuloSistema.objects.create(codigo='dashboards_test', nombre='Dashboards', orden=1)
        for codigo in ('dashboard_documentos', 'dashboard_despachos'):
            opcion = OpcionMenu.objects.create(modulo=modulo, codigo=codigo, nombre=codigo, activo=True)
            PermisoRol.objects.create(rol='administrador', opcion_menu=opcion, puede_ver=True)
        self.empresa = crear_empresa()
        self.cd = crear_sucursal(self.empresa, alias='CD-TEST',
                                 tipo_sucursal='CENTRO_DISTRIBUCION', es_centro_distribucion=True)
        self.tienda = crear_sucursal(self.empresa, alias='TIENDA-TEST')
        self.client = Client()
        self.client.force_login(self.user)
        s = self.client.session
        s['idSucursalActual'] = self.cd.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()

    def _get(self, url):
        r = self.client.get(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        return json.loads(r.content)


class DocumentosNotasCreditoTest(_DashboardBase):

    def test_nc_resta_en_monto_neto_y_en_listados(self):
        hoy = timezone.localdate()
        _dte(self.empresa, self.cd, 1, 'FACTURA ELECTRONICA', 100000, 'VENTA')
        _dte(self.empresa, self.cd, 2, 'FACTURA ELECTRONICA', 50000, 'VENTA')
        # NC guardada en POSITIVO (como en prod): debe restar.
        _dte(self.empresa, self.cd, 3, 'NOTA DE CREDITO', 30000, 'NOTA_CREDITO', es_nc=True)

        d = self._get(f'/app/api/dashboard-documentos/datos/?fecha_inicio={hoy}&fecha_fin={hoy}')
        k = d['kpis']
        self.assertEqual(k['total_dtes'], 3)
        self.assertEqual(k['notas_credito'], 1)
        self.assertEqual(k['monto_bruto'], 150000.0)
        self.assertEqual(k['monto_nc'], 30000.0)
        self.assertEqual(k['monto_total'], 120000.0, 'monto_total debe ser el NETO (bruto - NC)')
        self.assertEqual(k['ticket_promedio'], 75000, 'bruto / documentos que no son NC')
        self.assertEqual(k['pct_recepcionados'], k['pct_aceptados'])

        por_tipo = {t['tipo_documento']: t['monto'] for t in d['por_tipo']}
        self.assertEqual(por_tipo['NOTA DE CREDITO'], -30000.0)
        self.assertEqual(por_tipo['FACTURA ELECTRONICA'], 150000.0)
        self.assertEqual(d['top_emisores'][0]['monto'], 120000.0)
        self.assertEqual(d['evolucion'][0]['monto'], 120000.0)

    def test_nc_de_proveedor_no_es_deuda_ni_compra_vencida(self):
        """A5-01: la NC de compra queda 'Pendiente' y nace vencida; no debe
        inflar Por Pagar, Vencido ni el conteo de Compras Vencidas."""
        hoy = timezone.localdate()
        ayer = hoy - timedelta(days=1)
        _dte(self.empresa, None, 20, 'FACTURA ELECTRONICA', 100000, 'COMPRA',
             estado_pago='Pendiente', fecha_emision=ayer, fecha_vencimiento=ayer)
        url = f'/app/api/dashboard-documentos/datos/?fecha_inicio={ayer}&fecha_fin={hoy}'
        antes = self._get(url)
        self.assertEqual(antes['kpis']['monto_por_pagar'], 100000.0)
        self.assertEqual(antes['kpis']['dtes_vencidos'], 1)

        _dte(self.empresa, None, 21, 'NOTA DE CREDITO', 30000, 'COMPRA',
             estado_pago='Pendiente', fecha_emision=ayer, fecha_vencimiento=ayer)
        d = self._get(url)
        k = d['kpis']
        self.assertEqual(k['monto_por_pagar'], 100000.0)
        self.assertEqual(k['monto_pendiente_pago'], 100000.0)
        self.assertEqual(k['monto_vencido'], 100000.0)
        self.assertEqual(k['dtes_vencidos'], 1)
        prov = d['top_proveedores'][0]
        self.assertEqual((prov['cantidad'], prov['pendientes']), (2, 1),
                         'la NC cuenta como documento, pero no como factura pendiente')

    def test_nc_guardada_en_negativo_tambien_resta(self):
        """A5-08: 6 NC históricas tienen monto_con_iva negativo; deben restar
        igual que las positivas (con -F() pasaban a sumar)."""
        hoy = timezone.localdate()
        _dte(self.empresa, self.cd, 30, 'FACTURA ELECTRONICA', 100000, 'VENTA')
        _dte(self.empresa, self.cd, 31, 'NOTA DE CREDITO', -20000, 'NOTA_CREDITO', es_nc=True)
        d = self._get(f'/app/api/dashboard-documentos/datos/?fecha_inicio={hoy}&fecha_fin={hoy}')
        self.assertEqual(d['kpis']['monto_nc'], 20000.0)
        self.assertEqual(d['kpis']['monto_total'], 80000.0)
        por_tipo = {t['tipo_documento']: t['monto'] for t in d['por_tipo']}
        self.assertEqual(por_tipo['NOTA DE CREDITO'], -20000.0)

    def test_estado_pago_agrupa_sin_mayusculas(self):
        """A5-07: 'Pendiente' y 'PENDIENTE' son una sola porción de la dona."""
        hoy = timezone.localdate()
        _dte(self.empresa, None, 40, 'FACTURA ELECTRONICA', 1000, 'COMPRA', estado_pago='Pendiente')
        _dte(self.empresa, self.cd, 41, 'GUIA', 2000, 'TRASPASO', estado_pago='PENDIENTE')
        _dte(self.empresa, None, 42, 'FACTURA ELECTRONICA', 500, 'COMPRA', estado_pago='Pagado')
        d = self._get(f'/app/api/dashboard-documentos/datos/?fecha_inicio={hoy}&fecha_fin={hoy}')
        estados = {e['estado_pago']: (e['cantidad'], e['monto']) for e in d['por_estado_pago']}
        self.assertEqual(estados, {'PENDIENTE': (2, 3000.0), 'PAGADO': (1, 500.0)})

    def test_nc_emitidas_en_una_sola_serie(self):
        """A5-04: NOTA_CREDITO / ANULACION / DEVOLUCION (NC emitidas) van en
        la serie 'NC_EMITIDA'; la NC de proveedor sigue neteando Compras."""
        hoy = timezone.localdate()
        _dte(self.empresa, self.cd, 50, 'BOLETA ELECTRONICA', 10000, 'VENTA_PUBLICO')
        _dte(self.empresa, self.cd, 51, 'NOTA DE CREDITO', 1000, 'NOTA_CREDITO', es_nc=True)
        _dte(self.empresa, self.cd, 52, 'NOTA DE CREDITO', 2000, 'ANULACION', es_nc=True)
        _dte(self.empresa, None, 53, 'FACTURA ELECTRONICA', 50000, 'COMPRA')
        _dte(self.empresa, None, 54, 'NOTA DE CREDITO', 5000, 'COMPRA')
        d = self._get(f'/app/api/dashboard-documentos/datos/?fecha_inicio={hoy}&fecha_fin={hoy}')
        series = {e['tipo_transaccion']: e['monto'] for e in d['evolucion_transaccion']}
        self.assertEqual(series, {
            'VENTA_PUBLICO': 10000.0, 'NC_EMITIDA': -3000.0, 'COMPRA': 45000.0,
        })


@SOLO_POSTGRES
class DespachosEficaciaTest(_DashboardBase):

    def test_eficacia_por_tienda_y_dias_recepcion(self):
        hoy = timezone.localdate()
        emision = hoy - timedelta(days=12)
        # Catálogo POR SUCURSAL: el mismo sku es un Producto_Talla distinto en
        # el CD y en la tienda; el cruce despacho→venta es por sku.
        _, cd_a = crear_producto_con_talla(self.cd, sku=555001, stock=100)
        _, t_a = crear_producto_con_talla(self.tienda, sku=555001, stock=0)
        _, cd_b = crear_producto_con_talla(self.cd, sku=555002, stock=100, articulo='B')
        _, t_b = crear_producto_con_talla(self.tienda, sku=555002, stock=0, articulo='B')
        _, t_otro = crear_producto_con_talla(self.tienda, sku=555009, stock=5, articulo='Otro')

        guia = _dte(self.empresa, self.cd, 10, 'GUIA', 0, 'TRASPASO',
                    fecha_emision=emision, fecha_recepcion=emision + timedelta(days=2),
                    estado_dte='RECEPCIONADO_COMPLETO', unidades_productos=12)
        _mov(cd_a, self.cd, -10, emision, 'TRASPASO_SALIDA', destino=self.tienda, dte=guia)
        _mov(cd_b, self.cd, -2, emision, 'TRASPASO_SALIDA', destino=self.tienda, dte=guia)
        _mov(t_a, self.cd, 10, emision + timedelta(days=2), 'TRASPASO_ENTRADA', destino=self.tienda, dte=guia)
        _mov(t_b, self.cd, 2, emision + timedelta(days=2), 'TRASPASO_ENTRADA', destino=self.tienda, dte=guia)

        # Ventas en la tienda (sucursal_origen = tienda):
        _mov(t_a, self.tienda, -2, emision + timedelta(days=3), 'VENTA_PUBLICO')   # cuenta
        _mov(t_a, self.tienda, -1, emision + timedelta(days=5), 'VENTA_PUBLICO')   # cuenta
        _mov(t_a, self.tienda, -1, emision - timedelta(days=1), 'VENTA_PUBLICO')   # antes del despacho: NO
        _mov(t_b, self.tienda, -5, emision + timedelta(days=6), 'VENTA_PUBLICO')   # 5 vendidas, tope 2
        _mov(t_otro, self.tienda, -2, emision + timedelta(days=4), 'VENTA_PUBLICO')  # otro sku: NO
        _mov(t_a, self.cd, -3, emision + timedelta(days=4), 'VENTA_PUBLICO')       # otra sucursal: NO

        ini, fin = emision - timedelta(days=1), emision + timedelta(days=1)
        d = self._get(f'/app/api/dashboard-despachos/datos/?fecha_inicio={ini}&fecha_fin={fin}')
        k = d['kpis']
        self.assertEqual(k['total_traspasos'], 1)
        self.assertEqual(k['eficacia_despachado'], 12)
        self.assertEqual(k['eficacia_vendido'], 5, '3 del sku A + 2 del sku B (topado en lo despachado)')
        self.assertEqual(k['eficacia_30d'], 41.7)
        self.assertEqual(k['avg_dias_recepcion'], 2.0)

        filas = {f['tienda']: f for f in d['eficacia_por_tienda']}
        t = filas['TIENDA-TEST']
        self.assertEqual((t['despachado'], t['vendido_30d'], t['eficacia']), (12, 5, 41.7))
        self.assertEqual(t['dias_recepcion'], 2.0)
        self.assertEqual(t['traspasos_recepcionados'], 1)
        self.assertNotIn('CD-TEST', filas, 'el CD no recibe despachos: no debe aparecer como tienda')

    def test_sin_despachos_devuelve_cero(self):
        hoy = timezone.localdate()
        d = self._get(f'/app/api/dashboard-despachos/datos/?fecha_inicio={hoy}&fecha_fin={hoy}')
        self.assertEqual(d['kpis']['eficacia_30d'], 0.0)
        self.assertEqual(d['kpis']['eficacia_despachado'], 0)
        self.assertEqual(d['eficacia_por_tienda'], [])
        self.assertFalse(d['kpis']['eficacia_parcial'])

    def test_eficacia_parcial_con_ventana_abierta(self):
        """A5-02: lo despachado hace < 30 d todavía puede venderse: la cifra no
        cambia, pero se rotula PARCIAL con las unidades en ventana abierta."""
        hoy = timezone.localdate()
        _, cd_a = crear_producto_con_talla(self.cd, sku=556001, stock=100)
        _, t_a = crear_producto_con_talla(self.tienda, sku=556001, stock=0)
        vieja = _dte(self.empresa, self.cd, 60, 'GUIA', 0, 'TRASPASO', fecha_emision=hoy - timedelta(days=40))
        nueva = _dte(self.empresa, self.cd, 61, 'GUIA', 0, 'TRASPASO', fecha_emision=hoy - timedelta(days=5))
        _mov(cd_a, self.cd, -4, hoy - timedelta(days=40), 'TRASPASO_SALIDA', destino=self.tienda, dte=vieja)
        _mov(cd_a, self.cd, -6, hoy - timedelta(days=5), 'TRASPASO_SALIDA', destino=self.tienda, dte=nueva)
        _mov(t_a, self.tienda, -2, hoy - timedelta(days=3), 'VENTA_PUBLICO')

        ini = hoy - timedelta(days=45)
        d = self._get(f'/app/api/dashboard-despachos/datos/?fecha_inicio={ini}&fecha_fin={hoy}')
        k = d['kpis']
        self.assertEqual((k['eficacia_despachado'], k['eficacia_vendido'], k['eficacia_30d']), (10, 2, 20.0))
        self.assertTrue(k['eficacia_parcial'])
        self.assertEqual(k['eficacia_uds_ventana_abierta'], 6)
        fila = {f['tienda']: f for f in d['eficacia_por_tienda']}['TIENDA-TEST']
        self.assertEqual(fila['uds_ventana_abierta'], 6)

        # Período cerrado (todo despachado hace > 30 d): no es parcial.
        fin = hoy - timedelta(days=35)
        d = self._get(f'/app/api/dashboard-despachos/datos/?fecha_inicio={ini}&fecha_fin={fin}')
        self.assertFalse(d['kpis']['eficacia_parcial'])
        self.assertEqual(d['kpis']['eficacia_despachado'], 4)

    def test_eficacia_solo_cuenta_despachos_desde_cd(self):
        """A5-03: una devolución tienda→CD no es un despacho: el CD no debe
        aparecer como tienda con 0 % y lo despachado no se infla."""
        hoy = timezone.localdate()
        emision = hoy - timedelta(days=40)
        _, cd_a = crear_producto_con_talla(self.cd, sku=557001, stock=100)
        _, t_a = crear_producto_con_talla(self.tienda, sku=557001, stock=5)
        ida = _dte(self.empresa, self.cd, 70, 'GUIA', 0, 'TRASPASO', fecha_emision=emision)
        vuelta = _dte(self.empresa, self.tienda, 71, 'GUIA', 0, 'TRASPASO', fecha_emision=emision)
        _mov(cd_a, self.cd, -8, emision, 'TRASPASO_SALIDA', destino=self.tienda, dte=ida)
        _mov(t_a, self.tienda, -3, emision, 'TRASPASO_SALIDA', destino=self.cd, dte=vuelta)

        d = self._get(f'/app/api/dashboard-despachos/datos/?fecha_inicio={emision}&fecha_fin={emision}')
        self.assertEqual(d['kpis']['eficacia_despachado'], 8)
        filas = {f['tienda']: f for f in d['eficacia_por_tienda']}
        self.assertNotIn('CD-TEST', filas)

    def test_tienda_solo_con_recepciones_no_marca_eficacia_cero(self):
        """A5-03: una tienda que aparece solo por días de recepción (sin
        despachos desde CD en el período) lleva eficacia None, no 0 %."""
        hoy = timezone.localdate()
        emision = hoy - timedelta(days=40)
        otra = crear_sucursal(self.empresa, alias='TIENDA-2')
        _, t_a = crear_producto_con_talla(self.tienda, sku=558001, stock=5)
        guia = _dte(self.empresa, self.tienda, 80, 'GUIA', 0, 'TRASPASO', fecha_emision=emision,
                    fecha_recepcion=emision + timedelta(days=1), estado_dte='RECEPCIONADO_COMPLETO')
        # Tienda → tienda: tiene destino (y días de recepción) pero no sale de un CD.
        _mov(t_a, self.tienda, -2, emision, 'TRASPASO_SALIDA', destino=otra, dte=guia)
        s = self.client.session
        s['idSucursalActual'] = otra.id
        s.save()
        d = self._get(f'/app/api/dashboard-despachos/datos/?fecha_inicio={emision}&fecha_fin={emision}')
        fila = {f['tienda']: f for f in d['eficacia_por_tienda']}['TIENDA-2']
        self.assertEqual(fila['despachado'], 0)
        self.assertIsNone(fila['eficacia'])
        self.assertEqual(fila['traspasos_recepcionados'], 1)


@SOLO_POSTGRES
class DespachosRecepcionesConteoTest(_DashboardBase):

    def test_recepciones_no_se_duplican_por_lineas_de_kardex(self):
        """A5-06: con sucursal activa el scoping une con dte_movimientos; el
        pk__in debe dejar una fila por recepción aunque la guía tenga varias
        líneas de kardex hacia la tienda."""
        from app.models import Productos_Recepcionados
        hoy = timezone.localdate()
        _, cd_a = crear_producto_con_talla(self.cd, sku=559001, stock=100)
        _, t_a = crear_producto_con_talla(self.tienda, sku=559001, stock=0)
        guia = _dte(self.empresa, self.cd, 90, 'GUIA', 0, 'TRASPASO', fecha_emision=hoy)
        for _ in range(3):
            _mov(cd_a, self.cd, -1, hoy, 'TRASPASO_SALIDA', destino=self.tienda, dte=guia)
        Productos_Recepcionados.objects.create(
            dte=guia, producto_talla=t_a, stockArribado=3, cantidad_esperada=3,
            estado='RECEPCIONADO_OK', fecha_recepcion=timezone.now())
        Productos_Recepcionados.objects.create(
            dte=guia, producto_talla=t_a, stockArribado=0, cantidad_esperada=1,
            estado='FALTANTE', fecha_recepcion=timezone.now())
        s = self.client.session
        s['idSucursalActual'] = self.tienda.id
        s.save()
        d = self._get(f'/app/api/dashboard-despachos/datos/?fecha_inicio={hoy}&fecha_fin={hoy}')
        k = d['kpis']
        self.assertEqual((k['total_recepciones'], k['total_faltantes'], k['tasa_exito']), (2, 1, 50.0))
        self.assertEqual(d['por_sucursal'][0]['total'], 2)
        self.assertEqual(d['tendencia'][0]['total'], 2)
