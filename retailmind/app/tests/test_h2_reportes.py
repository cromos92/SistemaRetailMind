"""
Unidad H2 (26-sep-2026): correcciones en reportes, ingresos por proveedor y
predicción de compras.

* A6-05  Productos vendidos: un CAMBIO CON DIFERENCIA no resta dos veces el
         monto ni suma el costo del producto devuelto; el neteo de
         devoluciones también descuenta el costo.
* A6-01  La tira de familias de reportes solo ofrece lo que el rol puede abrir.
* A6-04  Existencias por sucursal sale en un orden fijo (marca, artículo).
* CC-06  Ingresos por proveedor: estado_pago sin distinguir mayúsculas, NC
         fuera de "pendientes" y pendiente de ingreso medido DTE por DTE.
* CC-05  Predicción: configuración con rangos + permiso de edición, aprobar
         acotado a la vista y con permiso, y la clasificación ABC borra los
         pares (temporada, año) exactos.
* B15-07 Sugerencias: tránsito 's/d' (None) cuando la OC no está vinculada.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.sessions.backends.db import SessionStore
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    AtributoOpcion, CambioDevolucion, CambioDevolucionDetalle, ClasificacionABC,
    ConfiguracionPrediccion, Dte, Dte_Productos, Movimientos_Producto,
    Productos_Atributos, SugerenciaCompra, Ticket, Ticket_Productos,
)
from app.tests.factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla, crear_sucursal,
    crear_usuario, crear_vendedor, otorgar_ver_pantalla,
)


def _dte(emisor, sucursal, numero, tipo_documento, monto, tipo_transaccion, **extra):
    hoy = timezone.localdate()
    campos = dict(
        emisor=emisor, receptor=None, numero_documento=numero,
        tipo_documento=tipo_documento, monto_con_iva=monto,
        monto_neto=round(monto / 1.19), descuento=0,
        estado_pago='PAGADO', estado_dte='EMITIDO', responsable='test',
        fecha_emision=hoy, fecha_vencimiento=hoy, diasCredito=0, bultos=0,
        unidades_productos=1, tipo_transaccion=tipo_transaccion,
        sucursal=sucursal, hora=timezone.localtime().time(),
    )
    campos.update(extra)
    return Dte.objects.create(**campos)


class _Base(TestCase):
    rol = 'administrador'
    pantallas = ()

    def setUp(self):
        self.hoy = timezone.localdate()
        self.empresa = crear_empresa(nombre='Empresa H2', rut='76.123.456-0')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='H2-TIENDA')
        self.user = crear_usuario(username='h2_user', rol=self.rol)
        crear_empresa_user(self.user, self.empresa, self.sucursal)
        if self.pantallas:
            otorgar_ver_pantalla(self.rol, *self.pantallas,
                                 puede_editar=True, puede_aprobar=True, puede_exportar=True)
        self.client = Client()
        self.client.force_login(self.user)
        s = self.client.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s['alias'] = self.sucursal.alias
        s.save()

    def _get(self, url):
        r = self.client.get(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        return json.loads(r.content)


# ---------------------------------------------------------------- A6-05
class ProductosVendidosCambioConDiferenciaTest(_Base):
    pantallas = ('reporte_productos_vendidos',)

    def _ticket(self, correlativo, total, **extra):
        return Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=correlativo,
            subTotal=total, total=total, responsable='test', estado='PAGADO', **extra)

    def test_cambio_con_diferencia_no_resta_dos_veces_ni_suma_costo(self):
        self.vendedor = crear_vendedor(empresa=self.empresa)
        prod_a, pt_a = crear_producto_con_talla(self.sucursal, articulo='H2-ORIG', sku=880001)
        prod_b, pt_b = crear_producto_con_talla(self.sucursal, articulo='H2-NUEVO', sku=880002)

        # Venta original: 2 u de A con boleta (el ticket queda con DTE generado).
        venta = self._ticket(9001, 159980, dte_generado=True, folio_dte=5001,
                             modulo_origen='VENTA_PUBLICO')
        linea_a = Ticket_Productos.objects.create(
            idTicket=venta, ProductoTalla=pt_a, stock=2, precio=79990,
            precio_original=79990, subtotal=159980)
        boleta = _dte(self.empresa, self.sucursal, 5001, 'BOLETA ELECTRONICA', 159980, 'VENTA_PUBLICO')
        Dte_Productos.objects.create(dte=boleta, productoTalla=pt_a, descripcion='A', costo=37750,
                                     precio=79990, monto_item=159980, stock=2)

        # Cambio con diferencia: devuelve 1 A y se lleva 1 B. La boleta de la
        # diferencia trae la línea NEGATIVA del producto devuelto.
        nuevo = self._ticket(9002, 20000, dte_generado=True, folio_dte=5002,
                             modulo_origen='CAMBIO_DEVOLUCION')
        boleta_dif = _dte(self.empresa, self.sucursal, 5002, 'BOLETA ELECTRONICA', 20000, 'VENTA_PUBLICO')
        Dte_Productos.objects.create(dte=boleta_dif, productoTalla=pt_a, descripcion='A dev', costo=37750,
                                     precio=-79990, monto_item=-79990, stock=1)
        Dte_Productos.objects.create(dte=boleta_dif, productoTalla=pt_b, descripcion='B', costo=50000,
                                     precio=99990, monto_item=99990, stock=1)
        cambio = CambioDevolucion.objects.create(
            ticket_original=venta, ticket_nuevo=nuevo, sucursal=self.sucursal,
            numero_operacion='CD-H2-1', tipo_operacion='CAMBIO_CON_DIFERENCIA',
            estado='COMPLETADO', fecha_limite_cambio=self.hoy + timedelta(days=30),
            fecha_ejecucion=timezone.now(), monto_original=79990, monto_nuevo=99990,
            diferencia_monto=20000, motivo_principal='TALLA_INCORRECTA', solicitado_por=self.user)
        CambioDevolucionDetalle.objects.create(
            cambio_devolucion=cambio, producto_original=linea_a, cantidad_original=1,
            producto_nuevo=pt_b, cantidad_nueva=1, precio_nuevo=99990,
            precio_original_unitario=79990, condicion_producto='PERFECTO')

        d = self._get('/app/api/reportes/productos-vendidos/?tipo_flujo=custom'
                      f'&fecha_inicio={self.hoy}&fecha_fin={self.hoy}&top_n=50')
        self.assertTrue(d['success'], d)
        filas = {p['articulo']: p for p in d['productos']}
        a, b = filas['H2-ORIG'], filas['H2-NUEVO']
        # Realidad: 2 vendidas − 1 cambiada = 1 unidad neta de A.
        self.assertEqual((a['unidades'], a['monto'], a['costo'], a['margen']),
                         (1, 79990, 37750, 42240))
        self.assertEqual(a['unidades_devueltas'], 1)
        # El producto nuevo cuenta una sola vez, con su costo.
        self.assertEqual((b['unidades'], b['monto'], b['costo']), (1, 99990, 50000))
        self.assertEqual(d['kpis']['total_margen'], 42240 + 49990)

    def test_cambio_revertido_no_netea(self):
        self.vendedor = crear_vendedor(empresa=self.empresa)
        _, pt_a = crear_producto_con_talla(self.sucursal, articulo='H2-REV', sku=880011)
        venta = self._ticket(9101, 50000, dte_generado=True, folio_dte=5101,
                             modulo_origen='VENTA_PUBLICO')
        linea = Ticket_Productos.objects.create(
            idTicket=venta, ProductoTalla=pt_a, stock=1, precio=50000,
            precio_original=50000, subtotal=50000)
        boleta = _dte(self.empresa, self.sucursal, 5101, 'BOLETA ELECTRONICA', 50000, 'VENTA_PUBLICO')
        Dte_Productos.objects.create(dte=boleta, productoTalla=pt_a, descripcion='A', costo=20000,
                                     precio=50000, monto_item=50000, stock=1)
        cambio = CambioDevolucion.objects.create(
            ticket_original=venta, sucursal=self.sucursal, numero_operacion='CD-H2-2',
            tipo_operacion='CAMBIO_CON_DIFERENCIA', estado='REVERTIDO',
            fecha_limite_cambio=self.hoy + timedelta(days=30), fecha_ejecucion=timezone.now(),
            monto_original=50000, monto_nuevo=50000, diferencia_monto=0,
            motivo_principal='TALLA_INCORRECTA', solicitado_por=self.user)
        CambioDevolucionDetalle.objects.create(
            cambio_devolucion=cambio, producto_original=linea, cantidad_original=1,
            precio_original_unitario=50000, condicion_producto='PERFECTO')
        d = self._get('/app/api/reportes/productos-vendidos/?tipo_flujo=custom'
                      f'&fecha_inicio={self.hoy}&fecha_fin={self.hoy}&top_n=50')
        fila = {p['articulo']: p for p in d['productos']}['H2-REV']
        self.assertEqual((fila['unidades'], fila['monto'], fila['costo'], fila['unidades_devueltas']),
                         (1, 50000, 20000, 0))


# ---------------------------------------------------------------- A6-01
class FamiliaReportesPermisosTest(TestCase):

    def _render(self, user, familia, activo):
        request = RequestFactory().get('/app/reportes/ventas-sucursal/')
        request.user = user
        request.session = SessionStore()
        return render_to_string('vistas/modulo_reportes/_familia_reportes.html',
                                {'familia': familia, 'activo': activo}, request=request)

    def test_cajero_solo_ve_los_reportes_que_puede_abrir(self):
        cajero = crear_usuario(username='h2_cajero', rol='cajero')
        otorgar_ver_pantalla('cajero', 'reporte_ventas_sucursal')
        html = self._render(cajero, 'ventas', 'documentos')
        self.assertIn(reverse('ver_reporte_ventas_sucursal'), html)
        # El reporte activo se pinta siempre (el usuario ya está en él).
        self.assertIn(reverse('ver_documentos_emitidos'), html)
        for nombre in ('ver_reporte_ventas_comparativo', 'ver_reporte_ventas_global',
                       'ver_reporte_ventas_internet', 'ver_reporte_productos_vendidos'):
            self.assertNotIn(reverse(nombre), html, nombre)

    def test_maestro_ve_toda_la_familia(self):
        maestro = crear_usuario(username='h2_maestro', rol='maestro')
        html = self._render(maestro, 'compras', 'compras')
        for nombre in ('ver_reporte_compras', 'ver_reporte_rendimiento_proveedor',
                       'verReporteDespachosProveedor', 'ver_reporte_productos_origen',
                       'ver_inteligencia_compra'):
            self.assertIn(reverse(nombre), html, nombre)


# ---------------------------------------------------------------- A6-04
class ExistenciasSucursalOrdenTest(_Base):
    pantallas = ('reporte_existencias_sucursal',)

    def test_filas_ordenadas_por_marca_y_articulo(self):
        attr = Productos_Atributos.objects.create(nombre='Marca', descripcion='m')
        zeta = AtributoOpcion.objects.create(atributo=attr, valor='ZETA')
        alfa = AtributoOpcion.objects.create(atributo=attr, valor='ALFA')
        crear_producto_con_talla(self.sucursal, articulo='Z-1', sku=881001, stock=3, atributo1=zeta)
        crear_producto_con_talla(self.sucursal, articulo='B-2', sku=881002, stock=2, atributo1=alfa)
        crear_producto_con_talla(self.sucursal, articulo='A-9', sku=881003, stock=1, atributo1=alfa)
        crear_producto_con_talla(self.sucursal, articulo='SIN-STOCK', sku=881004, stock=0, atributo1=alfa)
        d = self._get(f'/app/api/reporte-existencias-sucursal/?sucursal_id={self.sucursal.id}')
        self.assertTrue(d['success'], d)
        orden = [(f['marca'], f['articulo']) for f in d['datos']]
        self.assertEqual(orden, [('ALFA', 'A-9'), ('ALFA', 'B-2'), ('ZETA', 'Z-1')])
        self.assertEqual(d['resumen']['sin_stock'], 1)


# ---------------------------------------------------------------- CC-06
class IngresosPorProveedorTest(_Base):
    pantallas = ('reporte_despachos_proveedor',)

    def test_pendientes_sin_mayusculas_sin_nc_y_pendiente_por_dte(self):
        proveedor = crear_empresa(nombre='Proveedor H2', rut='77.777.777-7', esProveedor=True)
        _, pt = crear_producto_con_talla(self.sucursal, articulo='H2-ING', sku=882001, stock=0)
        con_lineas = _dte(proveedor, None, 700, 'FACTURA ELECTRONICA', 100000, 'COMPRA',
                          receptor=self.empresa, estado_pago='Pendiente')
        Dte_Productos.objects.create(dte=con_lineas, productoTalla=pt, descripcion='x',
                                     precio=1000, stock=10)
        sin_lineas = _dte(proveedor, None, 701, 'FACTURA ELECTRONICA', 50000, 'COMPRA',
                          receptor=self.empresa, estado_pago='Pagado')
        _dte(proveedor, None, 702, 'NOTA DE CREDITO', 5000, 'COMPRA',
             receptor=self.empresa, estado_pago='Pendiente')
        for dte, cant in ((con_lineas, 4), (sin_lineas, 5)):
            Movimientos_Producto.objects.create(
                dte=dte, ProductoTalla=pt, sucursal_origen=self.sucursal, cantidad=cant,
                costo=1000, fecha=self.hoy, concepto='RECEPCION_COMPRA', estado='COMPLETADO',
                tipo_movimiento='INGRESO')

        d = self._get(f'/app/reporte_despachos_por_proveedor/?fecha_inicio={self.hoy}&fecha_fin={self.hoy}')
        r = d['resumen']
        self.assertEqual(r['total_dtes'], 3)
        self.assertEqual(r['dtes_pendientes'], 1, "'Pendiente' cuenta; la NC no es una factura por pagar")
        self.assertEqual(r['dtes_pagados'], 1)
        self.assertEqual(r['total_unidades_ingresadas'], 9)
        self.assertEqual(r['total_unidades_en_dtes'], 10)
        self.assertEqual(r['total_unidades_pendientes'], 6, 'se mide DTE por DTE: 10 − 4')
        self.assertEqual(r['dtes_sin_detalle'], 2)
        self.assertEqual(r['unidades_ingresadas_sin_detalle'], 5)
        self.assertEqual(r['total_costo_ingresado'], r['total_monto_compras'])
        filas = {f['dte_numero']: f for f in d['data']}
        self.assertFalse(filas[700]['sin_detalle'])
        self.assertEqual(filas[700]['unidades_pendientes_ingreso'], 6)
        self.assertTrue(filas[701]['sin_detalle'])

    def test_filtros_mal_formados_responden_400_json(self):
        url = '/app/reporte_despachos_por_proveedor/'
        for qs in ('proveedor_id=abc', 'fecha_inicio=xx', 'fecha_fin=2026-13-45',
                   'fecha_inicio=2026-02-30'):
            r = self.client.get(f'{url}?{qs}', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            self.assertEqual(r.status_code, 400, qs)
            d = json.loads(r.content)
            self.assertFalse(d['success'], qs)
            self.assertTrue(d['error'], qs)

    def test_page_enorme_se_acota_a_la_ultima_pagina(self):
        proveedor = crear_empresa(nombre='Proveedor H2 pag', rut='78.888.888-8', esProveedor=True)
        _dte(proveedor, None, 710, 'FACTURA ELECTRONICA', 10000, 'COMPRA', receptor=self.empresa)
        d = self._get('/app/reporte_despachos_por_proveedor/'
                      f'?fecha_inicio={self.hoy}&page=999999999999999999999')
        self.assertEqual(d['pagination']['current_page'], 1)
        self.assertEqual([f['dte_numero'] for f in d['data']], [710])
        # Sin resultados también responde (página 1 vacía), no un 500.
        d = self._get('/app/reporte_despachos_por_proveedor/'
                      f'?proveedor_id={proveedor.id + 100000}&page=999999999999999999999')
        self.assertEqual((d['pagination']['current_page'], d['data']), (1, []))


# ------------------------------------------------ XSS en los JS de los tableros
class PlantillasEscapanTextoLibreTest(TestCase):
    """Texto libre de la BD (nombre de proveedor, alias, motivo de rechazo) no se
    concatena crudo en HTML en las pantallas de la unidad."""

    def _src(self, rel):
        import os
        import app
        ruta = os.path.join(os.path.dirname(app.__file__), 'templates', *rel.split('/'))
        with open(ruta, encoding='utf-8') as f:
            return f.read()

    def test_filtro_de_proveedores_no_arma_html(self):
        src = self._src('vistas/modulo_reportes/reporte_ingresos_proveedor.html')
        self.assertNotIn('>${proveedor.nombre}', src)
        self.assertIn(".text(`${proveedor.nombre} (${proveedor.rut})`)", src)

    def test_dashboard_despachos_escapa_motivo_y_alias(self):
        src = self._src('vistas/modulo_dashboards/dashboard_despachos.html')
        for crudo in ("title=\"${d.motivo_rechazo || ''}\"", '${f.origen}', '${f.destino}',
                      '${e.origen}', '${s.sucursal}', "${r.alias || '-'}",
                      "${d.sucursal__alias || '-'}", "${r.dte__sucursal__alias || '-'}"):
            self.assertNotIn(crudo, src, crudo)
        self.assertIn('escEficacia(d.motivo_rechazo)', src)

    def test_dashboard_documentos_escapa_proveedor_y_alias(self):
        src = self._src('vistas/modulo_dashboards/dashboard_documentos.html')
        self.assertIn("escDoc((p.emisor__nombre || '').substring(0, 28))", src)
        self.assertIn('escDoc(alias)', src)


# ---------------------------------------------------------------- CC-05 / B15-07
class PrediccionConfiguracionTest(_Base):
    pantallas = ('prediccion_compras',)
    url = '/app/api/prediccion/configuracion/'

    def _post(self, body, client=None):
        return (client or self.client).post(self.url, data=json.dumps(body),
                                            content_type='application/json')

    def test_rechaza_valores_fuera_de_rango_y_no_guarda(self):
        config = ConfiguracionPrediccion.get_config()
        original = config.dias_historico_analisis
        r = self._post({'dias_historico_analisis': 0, 'factor_seguridad_clase_a': -1,
                        'sellthrough_default': 150})
        self.assertEqual(r.status_code, 400)
        detalle = json.loads(r.content)['detalle']
        self.assertEqual(len(detalle), 3, detalle)
        config.refresh_from_db()
        self.assertEqual(config.dias_historico_analisis, original)

    def test_cv_x_debe_ser_menor_que_cv_y(self):
        r = self._post({'umbral_cv_x': 1.5, 'umbral_cv_y': 1.0})
        self.assertEqual(r.status_code, 400)

    def test_guarda_valores_validos(self):
        r = self._post({'dias_historico_analisis': 365, 'factor_seguridad_clase_a': 1.2})
        self.assertEqual(r.status_code, 200, r.content)
        config = ConfiguracionPrediccion.get_config()
        self.assertEqual(config.dias_historico_analisis, 365)
        self.assertEqual(config.factor_seguridad_clase_a, Decimal('1.20'))

    def test_sin_puede_editar_no_modifica(self):
        otro = crear_usuario(username='h2_solo_ver', rol='jefe_local')
        otorgar_ver_pantalla('jefe_local', 'prediccion_compras')
        cliente = Client()
        cliente.force_login(otro)
        r = self._post({'dias_historico_analisis': 400}, client=cliente)
        self.assertEqual(r.status_code, 403)


class PrediccionAprobarYTransitoTest(_Base):
    pantallas = ('prediccion_compras',)

    def _sugerencia(self, sucursal, sku):
        _, pt = crear_producto_con_talla(sucursal, articulo=f'H2-S{sku}', sku=sku, stock=0)
        return SugerenciaCompra.objects.create(
            articulo_talle=pt, unidades_sugeridas=10, stock_actual=0, unidades_a_pedir=10)

    def test_aprobar_solo_la_sucursal_de_la_vista(self):
        otra = crear_sucursal(empresa=self.empresa, alias='H2-OTRA')
        propia = self._sugerencia(self.sucursal, 883001)
        ajena = self._sugerencia(otra, 883002)
        r = self.client.post('/app/api/prediccion/aprobar-sugerencia/',
                             data=json.dumps({'ids': [propia.id, ajena.id]}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(json.loads(r.content)['aprobadas'], 1)
        propia.refresh_from_db()
        ajena.refresh_from_db()
        self.assertTrue(propia.aprobada)
        self.assertFalse(ajena.aprobada)

    def test_aprobar_exige_permiso(self):
        sug = self._sugerencia(self.sucursal, 883003)
        otro = crear_usuario(username='h2_sin_aprobar', rol='jefe_local')
        otorgar_ver_pantalla('jefe_local', 'prediccion_compras')
        cliente = Client()
        cliente.force_login(otro)
        s = cliente.session
        s['idSucursalActual'] = self.sucursal.id
        s.save()
        r = cliente.post('/app/api/prediccion/aprobar-sugerencia/',
                         data=json.dumps({'ids': [sug.id]}), content_type='application/json')
        self.assertEqual(r.status_code, 403)
        sug.refresh_from_db()
        self.assertFalse(sug.aprobada)

    def test_transito_sin_oc_vinculada_es_sd(self):
        self._sugerencia(self.sucursal, 883004)
        d = self._get('/app/api/prediccion/sugerencias/')
        fila = d['sugerencias'][0]
        self.assertIsNone(fila['en_transito_hoy'], 'sin OC vinculada al SKU el tránsito es s/d')
        self.assertEqual(fila['a_pedir_hoy'], 10)
        self.assertEqual(d['transito_medible'], 0)


class ClasificacionAbcParesTest(TestCase):
    """CC-05 (b): zip() de dos sets emparejaba temporadas y años al azar."""

    def test_reclasificar_varias_temporadas_y_anios(self):
        from app.services.prediccion_compras import calcular_clasificacion_abc_xyz
        empresa = crear_empresa(nombre='Empresa ABC', rut='76.999.888-1')
        tienda = crear_sucursal(empresa=empresa, alias='ABC-1')
        combos = [('verano', 2026), ('invierno', 2026), ('invierno', 2023), ('verano', 2023)]
        hoy = timezone.localdate()
        productos = []
        for i, (temp, anio) in enumerate(combos):
            prod, pt = crear_producto_con_talla(tienda, articulo=f'ABC-{i}', sku=884000 + i,
                                                temporada=temp, anio_temporada=anio)
            productos.append(prod)
            Movimientos_Producto.objects.create(
                ProductoTalla=pt, sucursal_origen=tienda, cantidad=-(i + 1), precio=10000,
                costo=5000, fecha=hoy - timedelta(days=3), concepto='VENTA_PUBLICO',
                estado='COMPLETADO')
            # Clasificación previa del mismo (artículo, temporada, año).
            ClasificacionABC.objects.create(articulo=prod, temporada=temp, anio=anio,
                                            clasificacion_abc='C', clasificacion_xyz='Z')

        n = calcular_clasificacion_abc_xyz()
        self.assertEqual(n, 4)
        self.assertEqual(ClasificacionABC.objects.count(), 4)
        # Todas reescritas por el run (la de más venta queda A).
        self.assertEqual(
            ClasificacionABC.objects.get(articulo=productos[3]).clasificacion_abc, 'A')
