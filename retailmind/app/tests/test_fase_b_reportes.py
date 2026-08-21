"""
Tests de la FASE B de fixes de la auditoría de reportes (ago-2026).

Cubre:

1. KPIs de salud de `obtener_reporte_existencias_sucursal`: numerador y
   denominador sobre el MISMO universo (sucursal + solo-analítica + marca),
   y "Recibido hist." sin la apertura sintética de la migración Laravel.
2. Scoping del feed `obtener_vendedores_reporte`: un usuario acotado no ve
   vendedores de sucursales de otras empresas (y pedir una ajena da 403).
3. Gates de rol nuevos (`@requiere_permiso`) en ventas-global,
   productos-origen y recepciones/despachos-detallado: fail-closed sin el
   permiso (403 en AJAX, redirect en página) y paso normal con permiso.

Reusa la base de `test_scoping_reportes` (dos empresas simétricas del mismo
holding + `_patch_permisos`, que fuerza `PermisoRol.tiene_permiso=True` para
que lo único que pueda denegar sea lo que se está probando). Los tests de
gates NO parchean: en la BD de test no hay OpcionMenu, así que
`tiene_permiso` devuelve False — exactamente el escenario fail-closed que
existirá en prod hasta correr `inicializar_permisos`.
"""
from datetime import timedelta

from django.utils import timezone

from app.models import Movimientos_Producto, Productos_Atributos, AtributoOpcion, Ticket
from .factories import (
    crear_lote_fifo, crear_producto_con_talla, crear_vendedor,
)
from .test_scoping_reportes import (
    BaseScopingReportes, _crear_movimiento, _patch_permisos,
)


def _envejecer_lote(lote, dias=200):
    """`fecha_ingreso` es auto_now_add: solo se puede retroceder vía update()."""
    vieja = timezone.now() - timedelta(days=dias)
    type(lote).objects.filter(id=lote.id).update(fecha_ingreso=vieja)


class ExistenciasSucursalKpisMismoUniversoTest(BaseScopingReportes):
    """KPIs de salud: mismo universo para numerador y denominador."""

    URL = '/app/api/reporte-existencias-sucursal/'

    def setUp(self):
        super().setUp()
        # --- Universo analítico de la sucursal A ---
        # prod_a/talla_a (stock 50) viene de la base. Lote viejo de 25 u.
        lote_a = crear_lote_fifo(self.talla_a, cantidad=25)
        _envejecer_lote(lote_a)
        # Venta de 4 u dentro de los últimos 30 días.
        _crear_movimiento(self.talla_a, self.sucursal_a, 'VENTA_PUBLICO', -4,
                          tipo_movimiento='EGRESO')
        # Recibido histórico real: 50 u por INGRESO_MANUAL...
        _crear_movimiento(self.talla_a, self.sucursal_a, 'INGRESO_MANUAL', 50)
        # ...más la APERTURA sintética de la migración (30 u), que NO debe
        # contarse: los movimientos legacy ya están en el kardex.
        Movimientos_Producto.objects.create(
            ProductoTalla=self.talla_a,
            sucursal_origen=self.sucursal_a,
            cantidad=30,
            costo=1000,
            precio=2000,
            concepto='INGRESO_INICIAL',
            referencia_externa='MIGRACION_LARAVEL',
            tipo_movimiento='INGRESO',
            estado='COMPLETADO',
            fecha=timezone.localdate(),
        )

        # --- Producto EXCLUIDO de analítica (mismo universo físico) ---
        # Antes del fix, su lote viejo y sus ventas inflaban los KPIs de la
        # sucursal aunque el producto no aparece en la tabla del reporte.
        self.prod_excl, self.talla_excl = crear_producto_con_talla(
            self.sucursal_a, articulo='ART-EXCL', sku=1000002, stock=999,
            excluir_de_analitica=True,
        )
        lote_excl = crear_lote_fifo(self.talla_excl, cantidad=999)
        _envejecer_lote(lote_excl)
        _crear_movimiento(self.talla_excl, self.sucursal_a, 'VENTA_PUBLICO',
                          -100, tipo_movimiento='EGRESO')

        # --- Marca X (para el filtro por marca) ---
        attr = Productos_Atributos.objects.create(
            nombre='Marca', descripcion='Marca')
        self.marca_x = AtributoOpcion.objects.create(
            atributo=attr, valor='MARCA-X')
        self.prod_x, self.talla_x = crear_producto_con_talla(
            self.sucursal_a, articulo='ART-X', sku=1000003, stock=20,
            atributo1=self.marca_x,
        )
        lote_x = crear_lote_fifo(self.talla_x, cantidad=10)
        _envejecer_lote(lote_x)
        _crear_movimiento(self.talla_x, self.sucursal_a, 'VENTA_PUBLICO', -2,
                          tipo_movimiento='EGRESO')

    def _resumen(self, **params):
        params.setdefault('sucursal_id', self.sucursal_a.id)
        status, data = self._json(self.URL, **params)
        self.assertEqual(status, 200)
        self.assertTrue(data['success'])
        return data

    def test_pct_stock_viejo_solo_universo_analitico(self):
        data = self._resumen()
        resumen = data['resumen']
        # Denominador: 50 (ART-A) + 20 (ART-X). Numerador viejo: 25 + 10.
        self.assertEqual(resumen['stock_total'], 70)
        self.assertEqual(resumen['pct_stock_viejo'], 50.0)

    def test_vendidas_30_excluye_no_analitica(self):
        resumen = self._resumen()['resumen']
        # 4 (ART-A) + 2 (ART-X); las 100 del producto excluido NO cuentan.
        self.assertEqual(resumen['vendidas_30'], 6)

    def test_filtro_marca_acota_los_kpis_a_la_marca(self):
        resumen = self._resumen(marca_id=self.marca_x.id)['resumen']
        self.assertEqual(resumen['stock_total'], 20)
        self.assertEqual(resumen['vendidas_30'], 2)          # solo ART-X
        self.assertEqual(resumen['pct_stock_viejo'], 50.0)   # 10 / 20
        # Cobertura con la velocidad DE LA MARCA: 20 / (2/30) = 300 días.
        self.assertEqual(resumen['cobertura_dias'], 300)

    def test_recibido_historico_excluye_apertura_migracion(self):
        data = self._resumen()
        fila = next(f for f in data['datos'] if f['sku'] == str(self.talla_a.sku))
        # 50 del INGRESO_MANUAL; las 30 de la apertura sintética no suman.
        self.assertEqual(fila['stock_inicial'], 50)


class VendedoresReporteScopingTest(BaseScopingReportes):
    """`obtener_vendedores_reporte` — feed acotado a las empresas del usuario."""

    URL = '/app/api/reportes/vendedores/'

    def setUp(self):
        super().setUp()
        self.vendedor_a = crear_vendedor(
            nombre='Vendedor A', empresa=self.empresa_a, codigo_vendedor='VA1')
        self.vendedor_b = crear_vendedor(
            nombre='Vendedor B', empresa=self.empresa_b, codigo_vendedor='VB1')
        Ticket.objects.create(
            vendedor=self.vendedor_a, sucursal=self.sucursal_a, correlativo=1,
            estado='PAGADO', subTotal=1000, total=1000, responsable='tester')
        Ticket.objects.create(
            vendedor=self.vendedor_b, sucursal=self.sucursal_b, correlativo=1,
            estado='PAGADO', subTotal=2000, total=2000, responsable='tester')

    def _nombres(self, data):
        return {v['nombre'] for v in data['vendedores']}

    def test_sin_filtro_no_ve_vendedores_de_otra_empresa(self):
        status, data = self._json(self.URL)
        self.assertEqual(status, 200)
        self.assertEqual(self._nombres(data), {'Vendedor A'})

    def test_sucursal_ajena_devuelve_403(self):
        status, data = self._json(self.URL, sucursal_id=self.sucursal_b.id)
        self.assertEqual(status, 403)
        self.assertFalse(data['success'])

    def test_sucursal_propia_sigue_funcionando(self):
        status, data = self._json(self.URL, sucursal_id=self.sucursal_a.id)
        self.assertEqual(status, 200)
        self.assertEqual(self._nombres(data), {'Vendedor A'})

    def test_usuario_ajeno_ve_solo_los_suyos(self):
        self._login(self.user_b, self.sucursal_b, self.empresa_b)
        status, data = self._json(self.URL)
        self.assertEqual(status, 200)
        self.assertEqual(self._nombres(data), {'Vendedor B'})

    def test_administrador_sigue_viendo_todo(self):
        self._login(self.admin, self.sucursal_a, self.empresa_a)
        status, data = self._json(self.URL)
        self.assertEqual(status, 200)
        self.assertEqual(self._nombres(data), {'Vendedor A', 'Vendedor B'})


class GatesDePermisoFaseBTest(BaseScopingReportes):
    """
    `@requiere_permiso` nuevos (auditoría ago-2026, P1-10).

    Sin `_patch_permisos` la BD de test no tiene OpcionMenu, así que
    `PermisoRol.tiene_permiso` devuelve False: es el mismo estado fail-closed
    en que quedará prod hasta correr `inicializar_permisos` actualizado.
    AJAX (X-Requested-With) recibe 403 JSON; una página, redirect.
    """

    URL_PAGINA_GLOBAL = '/app/reportes/ventas-global/'
    URL_API_GLOBAL = '/app/api/reportes/ventas-global-empresa/'
    URL_API_ORIGEN = '/app/api/reportes/productos-origen/'
    URL_PAGINA_ORIGEN = '/app/reportes/productos-origen/'
    # recepciones/despachos-detallado fueron ELIMINADOS en Fase C
    # (huérfanos, auditoría 2026-08) — sus tests de gate se retiraron.

    def _get_ajax_sin_permiso(self, url):
        return self.client.get(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_api_ventas_global_sin_permiso_403(self):
        resp = self._get_ajax_sin_permiso(self.URL_API_GLOBAL)
        self.assertEqual(resp.status_code, 403)

    def test_pagina_ventas_global_sin_permiso_redirige(self):
        resp = self.client.get(self.URL_PAGINA_GLOBAL)
        self.assertEqual(resp.status_code, 302)

    def test_api_productos_origen_sin_permiso_403(self):
        resp = self._get_ajax_sin_permiso(self.URL_API_ORIGEN)
        self.assertEqual(resp.status_code, 403)

    def test_pagina_productos_origen_sin_permiso_redirige(self):
        resp = self.client.get(self.URL_PAGINA_ORIGEN)
        self.assertEqual(resp.status_code, 302)

    def test_con_permiso_las_apis_responden(self):
        """Control de no-regresión: con permiso el flujo normal sigue vivo."""
        status, data = self._json(self.URL_API_GLOBAL)
        self.assertEqual(status, 200)
        self.assertTrue(data['success'])

        status, data = self._json(self.URL_API_ORIGEN, anio=self.anio)
        self.assertEqual(status, 200)
        self.assertTrue(data['success'])
