"""
Unidad R2V2 (ronda 2, módulo Compras) — documentos de compra, pagos y detalle
de DTE en views.py.

Cubre:
1. cargarDteCompra exige puede_ver en gestion_dte_compras O gestion_compras
   (antes cualquier logueado listaba por POST los DTE de su empresa).
   vista_detalle_dte / api_detalle_dte_completo: cualquiera de las pantallas
   que abren el detalle (Gestión DTE, Gestión Compras, Recepción DTE, Gestión
   Producto, Trazabilidad).
2. cargarDteCompra: fechas faltantes o imposibles → 400, nunca 500 (B4-09).
3. Contrato F2 de cargarDteCompra: saldo (monto - todos los pagos, half-up),
   incidencias_activas, filtro 'al_dia', universo del KPI en los filtros de
   vencimiento (B3-05 / B15-06) y B15-12 (con_ingreso, recepcionado, compra,
   es_por_concepto) sin consultas por fila.
4. facturasPendientesPorMes: sin NC (B3-05) y CSV sin fórmulas (CC-16).
5. api_detalle_dte_completo: total_pagado con las NC; subtotal = monto_item en
   la COMPRA importada (es_manual) (B11-12).
8. crearDteCompras / actualizarDteCompras: emisión futura rechazada (B5-14).
9. enviar_comprobante_pago (CC-15).

Ejecutar (BD aislada):
    DATABASE_URL=postgres://postgres:admin@localhost:5432/retail_r2v2 \
    python manage.py test app.tests.test_r2v2_documentos_compra --keepdb
"""
import csv
import io
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.models import (
    Compras, Compras_Producto, Compras_Producto_Talla, Dte, Dte_Detalle_Pago, Dte_Incidencia,
    Dte_Productos, ModuloSistema, Movimientos_Producto, OpcionMenu, PermisoRol, Productos_Recepcionados,
)
from .factories import crear_empresa, crear_empresa_user, crear_producto_con_talla, crear_sucursal, crear_usuario

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'
TIPOS = ('puede_ver', 'puede_crear', 'puede_editar', 'puede_eliminar', 'puede_exportar', 'puede_aprobar')
CODIGOS = ('gestion_dte_compras', 'gestion_compras', 'recepcion_dte', 'gestion_producto',
           'trazabilidad_producto', 'dte_compras_pagos')


def _fijar_permiso(rol, codigo, **flags):
    """PermisoRol explícito (todos los flags en False salvo los pedidos), para
    no depender de lo que siembren las migraciones en la BD de test."""
    modulo, _ = ModuloSistema.objects.get_or_create(codigo='tests_r2v2', defaults={'nombre': 'Tests R2V2'})
    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo=codigo, defaults={'modulo': modulo, 'nombre': codigo, 'activo': True})
    if not opcion.activo:
        opcion.activo = True
        opcion.save(update_fields=['activo'])
    valores = {t: False for t in TIPOS}
    valores.update(flags)
    PermisoRol.objects.update_or_create(rol=rol, opcion_menu=opcion, defaults=valores)


def _perfil(rol, *con_ver, **extra):
    """El rol ve SOLO `con_ver` entre los CODIGOS de este archivo."""
    for codigo in CODIGOS:
        flags = {'puede_ver': True} if codigo in con_ver else {}
        flags.update(extra.get(codigo, {}))
        _fijar_permiso(rol, codigo, **flags)


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa(nombre='Nosotros R2V2', rut='76.420.000-2')
        cls.otra = crear_empresa(nombre='Otra R2V2', rut='76.520.000-2')
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='R2V2-SUC')
        cls.proveedor = crear_empresa(nombre='Proveedor R2V2', rut='77.420.000-2', esProveedor=True,
                                      email='viejo@prov.cl')
        cls.proveedor_b = crear_empresa(nombre='Proveedor B R2V2', rut='77.520.000-2', esProveedor=True)

        # Perfiles: administracion ve Gestión DTE; jefe_local solo Gestión
        # Compras; jefe solo Recepción DTE; cajero solo Trazabilidad;
        # vendedor ninguna.
        _perfil('administracion', 'gestion_dte_compras', 'dte_compras_pagos',
                gestion_dte_compras={'puede_crear': True, 'puede_editar': True})
        _perfil('jefe_local', 'gestion_compras')
        _perfil('jefe', 'recepcion_dte')
        _perfil('cajero', 'trazabilidad_producto')
        _perfil('vendedor')

        cls.maestro = crear_usuario(username='r2v2_maestro', rol='maestro')
        cls.admcion = crear_usuario(username='r2v2_admcion', rol='administracion',
                                    first_name='Ana', last_name='Pagos')
        cls.jefe_local = crear_usuario(username='r2v2_jefelocal', rol='jefe_local')
        cls.jefe = crear_usuario(username='r2v2_jefe', rol='jefe')
        cls.cajero = crear_usuario(username='r2v2_cajero', rol='cajero')
        cls.vendedor = crear_usuario(username='r2v2_vendedor', rol='vendedor')
        for u in (cls.maestro, cls.admcion, cls.jefe_local, cls.jefe, cls.cajero, cls.vendedor):
            crear_empresa_user(u, cls.empresa, cls.sucursal)

    def setUp(self):
        self.hoy = timezone.localdate()
        self._login(self.admcion)

    def _login(self, usuario):
        self.client.force_login(usuario)
        s = self.client.session
        s['idEmpresaActual'] = self.empresa.id
        s['idSucursalActual'] = self.sucursal.id
        s.save()

    def _dte(self, numero, emisor=None, tipo='FACTURA ELECTRONICA', monto=119000, receptor=None,
             estado_pago='Pendiente', estado_dte='ACEPTADO', emision=None, vencimiento=None,
             tipo_transaccion='COMPRA', **extra):
        emision = emision or self.hoy
        return Dte.objects.create(
            emisor=emisor or self.proveedor, receptor=receptor or self.empresa,
            numero_documento=numero, tipo_documento=tipo, monto_con_iva=monto,
            monto_neto=round(float(monto) / 1.19), descuento=0, estado_pago=estado_pago,
            estado_dte=estado_dte, responsable='test', fecha_emision=emision, fecha_recepcion=emision,
            fecha_vencimiento=vencimiento or (emision + timedelta(days=30)), diasCredito=30,
            bultos=0, unidades_productos=1, tipo_transaccion=tipo_transaccion, sucursal=self.sucursal,
            **extra,
        )

    def _post_json(self, url, data, raw=None):
        return self.client.post(
            url, data=raw if raw is not None else json.dumps(data), content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def _lista(self, **kw):
        data = {'fecha_inicio': (self.hoy - timedelta(days=400)).isoformat(),
                'fecha_fin': (self.hoy + timedelta(days=1)).isoformat(),
                'tipo_fecha': 'emision', 'page': 1, 'page_size': 100}
        data.update(kw)
        return self._post_json('/app/cargarDteCompra/', data)

    def _items(self, **kw):
        r = self._lista(**kw)
        self.assertEqual(r.status_code, 200, r.content)
        return {it['numero_documento']: it for it in r.json()['items']}


# ---------------------------------------------------------------------------
# 1. Permisos
# ---------------------------------------------------------------------------
class PermisosTest(_Base):
    def test_cargar_dte_compra_vendedor_sin_permiso_403(self):
        self._dte(5001)
        self._login(self.vendedor)
        r = self._lista()
        self.assertEqual(r.status_code, 403, r.content)
        self.assertNotIn('items', r.json())

    def test_cargar_dte_compra_acepta_cualquiera_de_las_dos_pantallas(self):
        self._dte(5002)
        for usuario in (self.admcion, self.jefe_local, self.maestro):
            with self.subTest(usuario=usuario.username):
                self._login(usuario)
                r = self._lista()
                self.assertEqual(r.status_code, 200, r.content)
                self.assertIn(5002, [it['numero_documento'] for it in r.json()['items']])

    def test_cargar_dte_compra_otras_pantallas_no_bastan(self):
        for usuario in (self.jefe, self.cajero):
            with self.subTest(usuario=usuario.username):
                self._login(usuario)
                self.assertEqual(self._lista().status_code, 403)

    def test_detalle_dte_pagina_y_api_segun_pantalla(self):
        factura = self._dte(5003)
        casos = (
            (self.vendedor, False), (self.admcion, True), (self.jefe_local, True),
            (self.jefe, True), (self.cajero, True), (self.maestro, True),
        )
        for usuario, permitido in casos:
            with self.subTest(usuario=usuario.username):
                self._login(usuario)
                pagina = self.client.get(f'/app/detalle_dte/{factura.id}/')
                api = self.client.get(f'/app/api/detalle_dte_completo/{factura.id}/',
                                      HTTP_X_REQUESTED_WITH='XMLHttpRequest')
                if permitido:
                    self.assertEqual(pagina.status_code, 200)
                    self.assertEqual(api.status_code, 200, api.content)
                else:
                    self.assertEqual(pagina.status_code, 302)
                    self.assertEqual(api.status_code, 403)

    def test_detalle_api_404_explicito(self):
        r = self.client.get('/app/api/detalle_dte_completo/987654321/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.json()['success'])


# ---------------------------------------------------------------------------
# 2. Fechas de cargarDteCompra
# ---------------------------------------------------------------------------
class CargarDteCompraFechasTest(_Base):
    def test_fechas_faltantes_o_imposibles_dan_400(self):
        casos = (
            {},
            {'fecha_inicio': '2026-01-01'},
            {'fecha_inicio': None, 'fecha_fin': None},
            {'fecha_inicio': '2026-13-45', 'fecha_fin': '2026-12-31'},
            {'fecha_inicio': '2026-02-01', 'fecha_fin': '2026-02-30'},
            {'fecha_inicio': 20260101, 'fecha_fin': '2026-12-31'},
            {'fecha_inicio': 'ayer', 'fecha_fin': 'hoy'},
        )
        for data in casos:
            with self.subTest(data=data):
                r = self._post_json('/app/cargarDteCompra/', data)
                self.assertEqual(r.status_code, 400, r.content)
                self.assertIn('Fechas inválidas', r.json()['error'])

    def test_cuerpo_no_json_da_400(self):
        r = self._post_json('/app/cargarDteCompra/', None, raw='{no es json')
        self.assertEqual(r.status_code, 400)

    def test_textos_nulos_no_revientan(self):
        self._dte(5101)
        r = self._lista(search=None, tipo_documento=None, filtro_vencimiento=None)
        self.assertEqual(r.status_code, 200, r.content)


# ---------------------------------------------------------------------------
# 3. Contrato de cargarDteCompra
# ---------------------------------------------------------------------------
class CargarDteCompraContratoTest(_Base):
    def test_saldo_resta_todos_los_pagos_half_up(self):
        f = self._dte(5201, monto=Decimal('100000.50'))
        Dte_Detalle_Pago.objects.create(dte=f, metodo_pago='Transferencia', monto=30000, fecha_pago=self.hoy)
        Dte_Detalle_Pago.objects.create(dte=f, metodo_pago='Nota de Crédito', voucher='77', monto=20000)
        Dte_Detalle_Pago.objects.create(dte=f, metodo_pago='Compensación con Factura', voucher='88', monto=10000)
        sin_pagos = self._dte(5202, monto=Decimal('1000.40'))
        items = self._items()
        self.assertEqual(items[5201]['saldo'], 40001)     # 100000,50 - 60000 → 40000,50 → 40001
        self.assertEqual(items[5202]['saldo'], 1000)      # 1000,40 → 1000
        # Los campos previos siguen llegando igual
        self.assertEqual(items[5201]['notas_credito'], 20000.0)
        self.assertEqual(items[5201]['compensaciones'], 10000.0)
        self.assertEqual(sin_pagos.id, items[5202]['id'])

    def test_incidencias_activas_pendiente_mas_en_gestion(self):
        f = self._dte(5211)
        for estado in ('PENDIENTE', 'EN_GESTION', 'RESUELTO'):
            Dte_Incidencia.objects.create(dte=f, tipo='MERCADERIA', descripcion='x', estado=estado)
        it = self._items()[5211]
        self.assertEqual(it['incidencias_count'], 3)
        self.assertEqual(it['incidencias_pendientes'], 1)
        self.assertEqual(it['incidencias_activas'], 2)

    def _compra_con_talla(self, nombre, estado='ACTIVA'):
        compra = Compras.objects.create(empresa=self.proveedor, nombre=nombre, correlativo=1, responsable='t',
                                        temporada='Invierno', estado=estado)
        prod = Compras_Producto.objects.create(compras=compra, nombre='Zapatilla', atributo1='M', atributo2='C',
                                               atributo3='G', atributo4='', costo=1000, precioSugerido=2000)
        return compra, Compras_Producto_Talla.objects.create(compra_producto=prod, stock=5, talla='40')

    def test_b15_12_ingreso_recepcion_y_compra(self):
        _, pt = crear_producto_con_talla(self.sucursal, articulo='Zap R2V2', talla='40', sku=9_420_001)
        con_ingreso = self._dte(5221)
        Movimientos_Producto.objects.create(dte=con_ingreso, ProductoTalla=pt, sucursal_destino=self.sucursal,
                                            cantidad=2, concepto='RECEPCION_COMPRA', tipo_movimiento='INGRESO')
        solo_recepcion = self._dte(5222)
        compra, cpt = self._compra_con_talla('OC Invierno R2V2')
        Productos_Recepcionados.objects.create(dte=solo_recepcion, compra_producto_talla=cpt, stockArribado=2)
        compra_borrada, cpt_b = self._compra_con_talla('OC borrada', estado='ELIMINADA')
        recep_borrada = self._dte(5223)
        Productos_Recepcionados.objects.create(dte=recep_borrada, compra_producto_talla=cpt_b, stockArribado=1)
        nada = self._dte(5224, es_por_concepto=True)

        items = self._items()
        self.assertTrue(items[5221]['con_ingreso'])
        self.assertFalse(items[5221]['recepcionado'])
        self.assertIsNone(items[5221]['compra_id'])

        self.assertFalse(items[5222]['con_ingreso'])
        self.assertTrue(items[5222]['recepcionado'])
        self.assertEqual(items[5222]['compra_id'], compra.id)
        self.assertEqual(items[5222]['compra_nombre'], 'OC Invierno R2V2')

        # Compra ELIMINADA: no se enlaza (el buscador no la muestra)
        self.assertTrue(items[5223]['recepcionado'])
        self.assertIsNone(items[5223]['compra_id'])

        self.assertEqual((items[5224]['con_ingreso'], items[5224]['recepcionado'], items[5224]['compra_id']),
                         (False, False, None))
        self.assertTrue(items[5224]['es_por_concepto'])
        self.assertFalse(items[5221]['es_por_concepto'])
        self.assertEqual(nada.id, items[5224]['id'])
        self.assertIsNotNone(compra_borrada.id)

    def _poblar(self, desde, n):
        for i in range(n):
            f = self._dte(desde + i)
            Dte_Detalle_Pago.objects.create(dte=f, metodo_pago='Cheque', voucher=str(i), monto=1000,
                                            fecha_pago=self.hoy)
            Dte_Detalle_Pago.objects.create(dte=f, metodo_pago='Nota de Crédito', voucher=f'9{i}', monto=500)
            Dte_Incidencia.objects.create(dte=f, tipo='OTRO', descripcion='x', estado='EN_GESTION')
            _, cpt = self._compra_con_talla(f'OC {desde + i}')
            Productos_Recepcionados.objects.create(dte=f, compra_producto_talla=cpt, stockArribado=1)

    def _consultas_lista(self):
        self._lista()  # calienta las cachés de sesión/menú del middleware
        with CaptureQueriesContext(connection) as ctx:
            r = self._lista()
        self.assertEqual(r.status_code, 200)
        return len(ctx.captured_queries), len(r.json()['items'])

    def test_consultas_no_crecen_con_las_filas(self):
        self._poblar(5300, 2)
        q_pocas, n_pocas = self._consultas_lista()
        self._poblar(5400, 8)
        q_muchas, n_muchas = self._consultas_lista()
        self.assertEqual((n_pocas, n_muchas), (2, 10))
        self.assertEqual(q_pocas, q_muchas)
        self.assertLessEqual(q_muchas, 20)


class CargarDteCompraFiltroVencimientoTest(_Base):
    """Los filtros de las tarjetas usan el MISMO universo que el KPI."""

    def setUp(self):
        super().setUp()
        h = self.hoy
        e = h - timedelta(days=40)
        self.esperado = {
            'vencidos': {6001, 6005},
            'por_vencer': {6002},
            'al_dia': {6003},
        }
        self._dte(6001, emision=e, vencimiento=h - timedelta(days=5))                      # vencida
        self._dte(6002, estado_pago='PENDIENTE', emision=e, vencimiento=h + timedelta(days=3))  # por vencer
        self._dte(6003, emision=e, vencimiento=h + timedelta(days=30))                     # al día
        self._dte(6004, emision=e, vencimiento=h + timedelta(days=8), estado_pago='Pagado')  # pagada: fuera
        abonada = self._dte(6005, estado_pago='Abonado', emision=e, vencimiento=h - timedelta(days=1))
        Dte_Detalle_Pago.objects.create(dte=abonada, metodo_pago='Cheque', monto=19000, fecha_pago=h)
        # Fuera del universo del KPI:
        self._dte(6101, tipo='NOTA DE CREDITO', emision=e, vencimiento=h - timedelta(days=5))
        self._dte(6102, tipo='COTIZACION', emision=e, vencimiento=h - timedelta(days=5))
        self._dte(6103, emision=e, vencimiento=h - timedelta(days=5), descartado=True)
        self._dte(6104, emision=e, vencimiento=h - timedelta(days=5), estado_dte='RECHAZADO')
        self._dte(6105, emision=date(2024, 6, 1), vencimiento=date(2024, 7, 1))            # antes del corte
        pagada_de_hecho = self._dte(6106, emision=e, vencimiento=h - timedelta(days=5))
        Dte_Detalle_Pago.objects.create(dte=pagada_de_hecho, metodo_pago='Transferencia', monto=119000,
                                        fecha_pago=h)
        self._dte(6107, tipo='NOTA DE DEBITO', es_nota_credito=True, emision=e,
                  vencimiento=h - timedelta(days=5))

    def _ids(self, filtro, **kw):
        from app.views_modulo_compras import FECHA_CORTE_PENDIENTES
        r = self._lista(filtro_vencimiento=filtro, fecha_inicio=FECHA_CORTE_PENDIENTES.isoformat(),
                        fecha_fin=(self.hoy + timedelta(days=365)).isoformat(), **kw)
        self.assertEqual(r.status_code, 200, r.content)
        return {it['numero_documento'] for it in r.json()['items']}, r.json()['pagination']['total_count']

    def test_cada_filtro_con_su_universo(self):
        for filtro, esperados in self.esperado.items():
            with self.subTest(filtro=filtro):
                self.assertEqual(self._ids(filtro)[0], esperados)
        pendientes, total = self._ids('pendientes')
        self.assertEqual(pendientes, {6001, 6002, 6003, 6005})
        self.assertEqual(total, 4)

    def test_descartados_fuera_aunque_se_pidan(self):
        self.assertNotIn(6103, self._ids('pendientes', incluir_descartados=True)[0])

    def test_cuadra_con_el_kpi(self):
        self._login(self.maestro)
        kpi = self.client.get('/app/api/resumen-pendientes-anio/').json()
        self.assertTrue(kpi['success'], kpi)
        self.assertEqual(self._ids('pendientes')[1], kpi['cantidad_pendientes'])
        self.assertEqual(self._ids('vencidos')[1], kpi['vencidos'])
        self.assertEqual(self._ids('por_vencer')[1], kpi['por_vencer_pronto'])
        self.assertEqual(self._ids('al_dia')[1], kpi['al_dia'])

    def test_sin_filtro_la_lista_no_cambia(self):
        # La lista normal sigue mostrando NC, cotizaciones y pagadas.
        numeros = set(self._items().keys())
        self.assertTrue({6004, 6101, 6102, 6104, 6106}.issubset(numeros))


# ---------------------------------------------------------------------------
# 4. facturasPendientesPorMes
# ---------------------------------------------------------------------------
class FacturasPendientesPorMesTest(_Base):
    def setUp(self):
        super().setUp()
        self.mes = self.hoy.strftime('%Y-%m')
        self.prov_formula = crear_empresa(nombre='=HYPERLINK("http://x","clic")', rut='77.620.000-2',
                                          esProveedor=True)
        self._dte(7001, monto=100000)
        self._dte(7002, emisor=self.prov_formula, monto=50000)
        self._dte(7003, tipo='NOTA DE CREDITO', monto=30000)

    def _get(self, **params):
        base = {'mes': self.mes, 'tipo_fecha': 'emision', 'tipo_documento': ''}
        base.update(params)
        return self.client.get('/app/facturasPendientesPorMes/', base, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_modo_todos_excluye_nc(self):
        r = self._get()
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(sorted(f['numero_documento'] for f in d['facturas']), [7001, 7002])
        self.assertEqual(d['total_pendiente'], 150000)
        self.assertEqual(d['total_cantidad'], 2)

    def test_csv_neutraliza_formulas(self):
        r = self._get(formato='csv')
        self.assertEqual(r.status_code, 200)
        texto = r.content.decode('utf-8-sig')
        filas = list(csv.reader(io.StringIO(texto), delimiter=';'))
        proveedores = [f[0] for f in filas[1:]]
        self.assertIn("'" + '=HYPERLINK("http://x","clic")', proveedores)
        self.assertFalse(any(p.startswith(('=', '+', '-', '@')) for p in proveedores))
        self.assertNotIn('NOTA DE CREDITO', texto)

    def test_nombre_de_archivo_seguro_y_pdf_con_marcado(self):
        raro = crear_empresa(nombre='A & B "<b>Sur</b>"\r\nX', rut='77.720.000-2', esProveedor=True)
        self._dte(7004, emisor=raro, monto=10000)
        for formato in ('csv', 'pdf'):
            with self.subTest(formato=formato):
                r = self._get(formato=formato, proveedor=raro.id)
                self.assertEqual(r.status_code, 200, r.content[:300])
                cd = r['Content-Disposition']
                nombre = cd.split('filename="', 1)[1].rstrip('"')
                self.assertRegex(nombre, r'^[A-Za-z0-9._-]+$')
        r = self._get(formato='pdf')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')


# ---------------------------------------------------------------------------
# 5. api_detalle_dte_completo
# ---------------------------------------------------------------------------
class DetalleDteCompletoTest(_Base):
    def _detalle(self, dte):
        r = self.client.get(f'/app/api/detalle_dte_completo/{dte.id}/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_saldo_incluye_nota_de_credito(self):
        f = self._dte(8001, monto=100000)
        Dte_Detalle_Pago.objects.create(dte=f, metodo_pago='Nota de Crédito', voucher='55', monto=30000)
        Dte_Detalle_Pago.objects.create(dte=f, metodo_pago='Cheque', voucher='C1', monto=50000,
                                        fecha_pago=self.hoy)
        self.assertEqual(self._detalle(f)['dte']['saldo'], 20000.0)

    def _linea(self, dte, precio=1000, stock=2, monto_item=1800):
        return Dte_Productos.objects.create(dte=dte, descripcion='Línea', precio=precio, stock=stock,
                                            monto_item=monto_item)

    def test_subtotal_usa_monto_item_solo_en_compra_importada(self):
        importada = self._dte(8002, es_manual=True)
        self._linea(importada)
        manual_sin_monto = self._dte(8003, es_manual=True)
        self._linea(manual_sin_monto, monto_item=0)
        normal = self._dte(8004)
        self._linea(normal)
        traspaso = self._dte(8005, emisor=self.empresa, tipo='GUIA', tipo_transaccion='TRASPASO', es_manual=True)
        self._linea(traspaso)
        self.assertEqual(self._detalle(importada)['productos'][0]['subtotal'], 1800)
        self.assertEqual(self._detalle(manual_sin_monto)['productos'][0]['subtotal'], 2000)
        self.assertEqual(self._detalle(normal)['productos'][0]['subtotal'], 2000)
        self.assertEqual(self._detalle(traspaso)['productos'][0]['subtotal'], 2000)


# ---------------------------------------------------------------------------
# 8. Emisión futura en crear / actualizar
# ---------------------------------------------------------------------------
class FechaEmisionFuturaTest(_Base):
    def _payload(self, **kw):
        base = {
            'receptor_id': self.proveedor.id, 'numero_documento': 9001, 'monto_con_iva': 119000,
            'tipo_documento': 'FACTURA ELECTRONICA', 'fecha_emision': self.hoy.isoformat(),
            'fecha_recepcion': self.hoy.isoformat(), 'estado_dte': 'ACEPTADO', 'diasCredito': 30,
            'bultos': 1, 'unidades_productos': 1, 'descuento': 0,
            'empresa_receptora_id': self.empresa.id, 'sucursal_receptora_id': self.sucursal.id,
        }
        base.update(kw)
        return base

    def test_crear_rechaza_futura_y_fecha_imposible(self):
        manana = (self.hoy + timedelta(days=1)).isoformat()
        r = self._post_json('/app/crearDteCompras/', self._payload(fecha_emision=manana))
        self.assertFalse(r.json()['success'])
        self.assertIn('posterior a hoy', r.json()['error'])
        r = self._post_json('/app/crearDteCompras/', self._payload(fecha_emision='2026-02-30'))
        self.assertFalse(r.json()['success'])
        self.assertIn('no es válida', r.json()['error'])
        self.assertFalse(Dte.objects.filter(numero_documento=9001).exists())
        r = self._post_json('/app/crearDteCompras/', self._payload())
        self.assertTrue(r.json()['success'], r.content)

    def _put(self, dte, **kw):
        return self.client.put(f'/app/actualizarDteCompras/{dte.id}/',
                               data=json.dumps(self._payload(numero_documento=dte.numero_documento, **kw)),
                               content_type='application/json', HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_actualizar_rechaza_cambiar_a_futura_pero_no_bloquea_la_guardada(self):
        manana = self.hoy + timedelta(days=1)
        f = self._dte(9002)
        r = self._put(f, fecha_emision=manana.isoformat())
        self.assertFalse(r.json()['success'])
        self.assertIn('posterior a hoy', r.json()['error'])
        f.refresh_from_db()
        self.assertEqual(f.fecha_emision, self.hoy)
        # Ya guardado con fecha de mañana (bug nocturno): se puede editar otro campo
        g = self._dte(9003, emision=manana)
        r = self._put(g, fecha_emision=manana.isoformat(), bultos=7)
        self.assertTrue(r.json()['success'], r.content)
        g.refresh_from_db()
        self.assertEqual(g.bultos, 7)


# ---------------------------------------------------------------------------
# 9. enviar_comprobante_pago (CC-15)
# ---------------------------------------------------------------------------
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   DEFAULT_FROM_EMAIL='no-reply@test.cl')
class EnviarComprobantePagoTest(_Base):
    URL = '/app/enviar_comprobante_pago/'

    def setUp(self):
        super().setUp()
        self.pagada = self._dte(9101, estado_pago='PAGADO')
        Dte_Detalle_Pago.objects.create(dte=self.pagada, metodo_pago='Cheque', voucher='CH-1', monto=119000,
                                        fecha_pago=self.hoy)

    def _enviar(self, **kw):
        data = {'dte_id': self.pagada.id, 'email': 'pagos@prov.cl'}
        data.update(kw)
        return self._post_json(self.URL, data)

    def test_envia_pdf_registra_y_recuerda_el_correo(self):
        r = self._enviar(asunto='Pago factura 9101')
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertTrue(d['success'])
        self.assertTrue(d['correo_actualizado'])
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ['pagos@prov.cl'])
        self.assertEqual(correo.subject, 'Pago factura 9101')
        nombre, contenido, tipo = correo.attachments[0]
        self.assertEqual(tipo, 'application/pdf')
        self.assertTrue(contenido.startswith(b'%PDF'))
        self.pagada.refresh_from_db()
        self.assertEqual(self.pagada.comprobante_enviado_a, 'pagos@prov.cl')
        self.assertIsNotNone(self.pagada.comprobante_enviado_en)
        self.proveedor.refresh_from_db()
        self.assertEqual(self.proveedor.email, 'pagos@prov.cl')

    def test_mismo_correo_no_actualiza_ficha(self):
        r = self._enviar(email='VIEJO@prov.cl')
        self.assertTrue(r.json()['success'], r.content)
        self.assertFalse(r.json()['correo_actualizado'])

    def test_asunto_por_defecto(self):
        self._enviar()
        self.assertIn('Comprobante de pago', mail.outbox[0].subject)

    def test_validaciones_sin_enviar(self):
        pendiente = self._dte(9102)
        ajena = self._dte(9103, receptor=self.otra, estado_pago='PAGADO')
        otro_prov = self._dte(9104, emisor=self.proveedor_b, estado_pago='PAGADO')
        casos = (
            ({'email': ''}, 400, 'correo'),
            ({'email': 'no-es-correo'}, 400, 'no es válido'),
            ({'dte_id': None, 'dte_ids': ['x']}, 400, 'inválida'),
            ({'dte_id': None, 'dte_ids': []}, 400, 'al menos un DTE'),
            ({'dte_id': pendiente.id}, 400, 'facturas pagadas'),
            ({'dte_id': ajena.id}, 404, 'No se encontraron'),
            ({'dte_id': None, 'dte_ids': [self.pagada.id, otro_prov.id]}, 400, 'mismo proveedor'),
        )
        for extra, status, texto in casos:
            with self.subTest(extra=extra):
                r = self._enviar(**extra)
                self.assertEqual(r.status_code, status, r.content)
                self.assertIn(texto, r.json()['error'])
        self.assertEqual(mail.outbox, [])
        ajena.refresh_from_db()
        self.assertIsNone(ajena.comprobante_enviado_en)

    def test_json_invalido_y_metodo(self):
        r = self._post_json(self.URL, None, raw='{roto')
        self.assertEqual(r.status_code, 400)
        r = self.client.get(self.URL, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 405)

    def test_falla_del_correo_no_registra_envio(self):
        with mock.patch('django.core.mail.EmailMultiAlternatives.send', side_effect=OSError('smtp caído')):
            r = self._enviar()
        self.assertEqual(r.status_code, 500)
        self.assertNotIn('smtp', r.json()['error'])
        self.pagada.refresh_from_db()
        self.assertIsNone(self.pagada.comprobante_enviado_en)
        self.proveedor.refresh_from_db()
        self.assertEqual(self.proveedor.email, 'viejo@prov.cl')

    def test_sin_permiso_de_pantalla_403(self):
        self._login(self.vendedor)
        r = self._enviar()
        self.assertEqual(r.status_code, 403)
        self.assertEqual(mail.outbox, [])
