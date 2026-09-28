"""
Tests del dashboard de inicio (`app/views_dashboard_home.py`).

Cubre lo agregado el 26-sep-2026 al convertir el home en "tablero del día":

1. `calcular_kpis_ventas` compara HOY contra AYER y contra HACE 7 DÍAS
   (mismo día de la semana pasada) HASTA LA MISMA HORA (A1-02), con la MISMA
   base que "hoy": tickets PAGADOS, sin los de CAMBIO_DEVOLUCION, de la
   sucursal, por `created_at`. Si el día base no tuvo ventas a esa hora la
   variación es None (no se inventa +100 %). La hora se inyecta (`ahora`)
   para que el test no dependa del reloj.
2. `obtener_bloques_dashboard` cachea los bloques CAROS 5 minutos por
   (sucursal, empresa, día): segunda llamada = acierto con la misma hora de
   cálculo; `forzar=True` recalcula; un cálculo incompleto no se cachea.
3. Las alertas que el usuario resuelve (cambios, caja, precios, DTEs,
   compras, requerimientos) van en vivo aunque la foto de 5 min esté en
   caché (A1-01).
4. El enlace «Ver capital inmovilizado» sólo aparece con permiso
   dashboard_fifo (A1-03) y la línea base del aviso de ventas nuevas es el
   número pintado (A1-05).
5. Facturas de proveedor vencidas / por vencer (B15-11): mismo universo que
   Gestión Documentos Compras y sólo con permiso gestion_dte_compras.

El alias de caché `ventas` se reemplaza por un LocMem propio (A1-06): con
REDIS_URL, `clear()` sobre el alias real vaciaría la base Redis compartida
con `catalogo` y `throttle`, y las claves por id podrían chocar con datos
reales.
"""
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from app.models import CambioDevolucion, Dte, Dte_Detalle_Pago, Ticket
from app.views_dashboard_home import (
    _cache_home,
    calcular_kpis_pagos_proveedor,
    calcular_kpis_ventas,
    obtener_bloques_dashboard,
)

from .factories import (
    crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario,
    crear_vendedor, otorgar_ver_pantalla,
)

CACHES_TEST = {
    **settings.CACHES,
    'ventas': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'test-dashboard-home',
    },
}
STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


class _BaseHomeTest(TestCase):

    def setUp(self):
        self.hoy = timezone.localdate()
        self.inicio_semana = self.hoy - timedelta(days=self.hoy.weekday())
        self.inicio_mes = self.hoy.replace(day=1)
        self.mes_pasado_inicio = (self.inicio_mes - timedelta(days=1)).replace(day=1)
        self.mes_pasado_fin = self.inicio_mes - timedelta(days=1)
        # Hora de corte fija para las comparaciones de hoy (A1-02).
        self.ahora = timezone.make_aware(datetime.combine(self.hoy, time(15, 0)))

        self.empresa = crear_empresa(nombre='Empresa Home', rut='76.111.222-3')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='HOME-1')
        self.otra_sucursal = crear_sucursal(empresa=self.empresa, alias='HOME-2')
        self.vendedor = crear_vendedor(nombre='Vendedor Home', empresa=self.empresa)
        self._correlativo = 0

    def _ticket(self, total, dia, *, sucursal=None, estado='PAGADO',
                modulo_origen='VENTA_PUBLICO', hora=time(12, 0)):
        """Ticket cuyo `created_at` cae a la `hora` (12:00) del día pedido.

        `created_at` es auto_now_add: se ajusta con update() después de crear.
        """
        self._correlativo += 1
        ticket = Ticket.objects.create(
            vendedor=self.vendedor,
            sucursal=sucursal or self.sucursal,
            correlativo=self._correlativo,
            subTotal=total,
            total=total,
            responsable='test',
            estado=estado,
            modulo_origen=modulo_origen,
        )
        momento = timezone.make_aware(datetime.combine(dia, hora))
        Ticket.objects.filter(pk=ticket.pk).update(created_at=momento)
        return ticket

    def _kpis(self, sucursal_id=None):
        return calcular_kpis_ventas(
            sucursal_id or self.sucursal.id, self.hoy, self.inicio_semana,
            self.inicio_mes, self.mes_pasado_inicio, self.mes_pasado_fin,
            ahora=self.ahora,
        )


class ComparacionHoyTest(_BaseHomeTest):

    def test_hoy_contra_ayer_y_hace_7_dias_a_la_misma_hora(self):
        ayer = self.hoy - timedelta(days=1)
        hace_7 = self.hoy - timedelta(days=7)

        self._ticket(30000, self.hoy)
        self._ticket(20000, self.hoy)
        self._ticket(100000, ayer)
        self._ticket(25000, hace_7)
        # Después de la hora de corte (15:00): cuenta sólo en el día completo.
        self._ticket(40000, ayer, hora=time(18, 0))
        self._ticket(5000, hace_7, hora=time(20, 30))
        # Ruido que NO debe contar: otra sucursal, cambio/devolución, no pagado.
        self._ticket(999999, ayer, sucursal=self.otra_sucursal)
        self._ticket(5000, self.hoy, modulo_origen='CAMBIO_DEVOLUCION')
        self._ticket(7000, ayer, estado='PENDIENTE')

        r = self._kpis()

        self.assertEqual(r['hoy'], 50000)
        self.assertEqual(r['tickets_hoy'], 2)
        self.assertEqual(r['hora_corte'], '15:00')
        self.assertEqual(r['ayer'], 100000)
        self.assertEqual(r['tickets_ayer'], 1)
        self.assertEqual(r['ayer_dia_completo'], 140000)
        self.assertEqual(r['hace_7_dias'], 25000)
        self.assertEqual(r['tickets_hace_7_dias'], 1)
        self.assertEqual(r['hace_7_dias_dia_completo'], 30000)
        self.assertEqual(r['fecha_hace_7_dias'], hace_7)
        self.assertEqual(r['variacion_ayer'], -50.0)
        self.assertEqual(r['tendencia_ayer'], 'down')
        self.assertEqual(r['variacion_7d'], 100.0)
        self.assertEqual(r['tendencia_7d'], 'up')

    def test_sin_base_a_esta_hora_no_inventa_variacion(self):
        self._ticket(40000, self.hoy)
        # Ayer sólo vendió DESPUÉS de la hora de corte: a esta hora no hay base.
        self._ticket(90000, self.hoy - timedelta(days=1), hora=time(19, 0))

        r = self._kpis()

        self.assertEqual(r['hoy'], 40000)
        self.assertEqual(r['ayer'], 0)
        self.assertEqual(r['ayer_dia_completo'], 90000)
        self.assertEqual(r['hace_7_dias'], 0)
        self.assertIsNone(r['variacion_ayer'])
        self.assertIsNone(r['variacion_7d'])
        self.assertEqual(r['tendencia_ayer'], 'stable')
        self.assertEqual(r['tendencia_7d'], 'stable')


@override_settings(CACHES=CACHES_TEST)
class CacheBloquesTest(_BaseHomeTest):

    def setUp(self):
        super().setUp()
        _cache_home().clear()   # LocMem propio (CACHES_TEST), no el Redis compartido

    def tearDown(self):
        _cache_home().clear()

    def test_segunda_llamada_es_acierto_y_forzar_recalcula(self):
        args = (self.sucursal.id, self.empresa.id, self.hoy, self.inicio_mes)

        bloques_1, calculado_1, desde_cache_1 = obtener_bloques_dashboard(*args)
        bloques_2, calculado_2, desde_cache_2 = obtener_bloques_dashboard(*args)
        bloques_3, calculado_3, desde_cache_3 = obtener_bloques_dashboard(*args, forzar=True)

        self.assertFalse(desde_cache_1)
        self.assertTrue(desde_cache_2)
        self.assertEqual(calculado_2, calculado_1)
        self.assertEqual(set(bloques_2), set(bloques_1))
        self.assertFalse(desde_cache_3)
        self.assertGreaterEqual(calculado_3, calculado_1)
        # Sólo los bloques caros van a la foto de 5 min; los que alimentan
        # alertas resolubles se calculan en vivo (A1-01).
        self.assertEqual(
            set(bloques_1),
            {'stock', 'operaciones', 'top_productos', 'salud_inventario', 'pagos_proveedor'},
        )

    def test_otra_sucursal_no_comparte_cache(self):
        obtener_bloques_dashboard(self.sucursal.id, self.empresa.id, self.hoy, self.inicio_mes)
        _, _, desde_cache = obtener_bloques_dashboard(
            self.otra_sucursal.id, self.empresa.id, self.hoy, self.inicio_mes,
        )
        self.assertFalse(desde_cache)

    def test_calculo_incompleto_no_se_cachea(self):
        args = (self.sucursal.id, self.empresa.id, self.hoy, self.inicio_mes)
        with mock.patch('app.views_dashboard_home.calcular_kpis_salud_inventario',
                        side_effect=Exception('falla transitoria')):
            bloques, _, desde_cache_1 = obtener_bloques_dashboard(*args)
            _, _, desde_cache_2 = obtener_bloques_dashboard(*args)
        self.assertIsNone(bloques['salud_inventario'])
        self.assertFalse(desde_cache_1)
        self.assertFalse(desde_cache_2)


class PagosProveedorTest(_BaseHomeTest):
    """B15-11: conteo de facturas de proveedor vencidas / por vencer."""

    def setUp(self):
        super().setUp()
        self.proveedor = crear_empresa(nombre='Proveedor Home', rut='77.333.444-5', esProveedor=True)
        self.otra_empresa = crear_empresa(nombre='Otra Home', rut='76.999.888-7')
        self._folio = 5000

    def _compra(self, vencimiento, *, estado_pago='Pendiente', receptor=-1, monto=119000,
                tipo_documento='FACTURA ELECTRONICA', emision=None, es_nota_credito=False,
                estado_dte='RECEPCIONADO_COMPLETO'):
        self._folio += 1
        return Dte.objects.create(
            emisor=self.proveedor,
            receptor=self.empresa if receptor == -1 else receptor,
            numero_documento=self._folio, tipo_documento=tipo_documento,
            monto_con_iva=Decimal(monto), monto_neto=Decimal(monto) / Decimal('1.19'),
            descuento=Decimal(0), estado_pago=estado_pago, estado_dte=estado_dte,
            responsable='test', fecha_emision=emision or self.hoy - timedelta(days=40),
            fecha_vencimiento=vencimiento, diasCredito=30, bultos=0,
            unidades_productos=1, sucursal=self.sucursal, tipo_transaccion='COMPRA',
            descartado=False, es_nota_credito=es_nota_credito,
            hora=timezone.localtime().time(),
        )

    def test_cuenta_vencidas_y_por_vencer_del_universo_de_la_pantalla(self):
        self._compra(self.hoy - timedelta(days=3))                           # vencida
        self._compra(self.hoy + timedelta(days=5), estado_pago='PARCIAL')    # por vencer
        self._compra(self.hoy + timedelta(days=30))                          # al día
        self._compra(self.hoy - timedelta(days=3), estado_pago='Pagado')     # pagada
        self._compra(self.hoy - timedelta(days=3), receptor=self.otra_empresa)  # otra empresa
        self._compra(self.hoy - timedelta(days=3), tipo_documento='NOTA DE CREDITO',
                     es_nota_credito=True)                                   # NC: no es deuda
        self._compra(self.hoy - timedelta(days=3), estado_dte='RECHAZADO')   # rechazada
        self._compra(self.hoy - timedelta(days=3), emision=date(2024, 6, 1)) # antes del corte
        saldada = self._compra(self.hoy - timedelta(days=3))                 # saldo 0
        Dte_Detalle_Pago.objects.create(dte=saldada, metodo_pago='TRANSFERENCIA', monto=119000)

        r = calcular_kpis_pagos_proveedor(self.empresa.id, self.hoy)

        self.assertEqual(r['vencidas'], 1)
        self.assertEqual(r['por_vencer'], 1)
        self.assertEqual(r['dias_aviso'], 7)

    def test_sin_empresa_no_calcula(self):
        self.assertIsNone(calcular_kpis_pagos_proveedor(None, self.hoy))


@override_settings(CACHES=CACHES_TEST, STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class HomeVistaTest(_BaseHomeTest):
    """La vista completa: alertas en vivo, enlace FIFO, pagos a proveedor."""

    def setUp(self):
        super().setUp()
        _cache_home().clear()
        otorgar_ver_pantalla('administrador', 'dashboard_general', 'dashboard_fifo',
                             'gestion_dte_compras')
        otorgar_ver_pantalla('cajero', 'dashboard_general')
        self.admin = crear_usuario(username='home_admin', rol='administrador')
        crear_empresa_user(self.admin, self.empresa, self.sucursal)
        self.cajero = crear_usuario(username='home_cajero', rol='cajero')
        crear_empresa_user(self.cajero, self.empresa, self.sucursal)

    def tearDown(self):
        _cache_home().clear()

    def _home(self, usuario, **params):
        c = Client()
        c.force_login(usuario)
        s = c.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()
        r = c.get('/app/home/', params)
        self.assertEqual(r.status_code, 200)
        return r

    def _alertas(self, r):
        return [a['titulo'] for a in r.context['alertas']]

    def test_alerta_de_cambio_resuelto_desaparece_sin_forzar(self):
        original = self._ticket(30000, self.hoy - timedelta(days=2))
        cambio = CambioDevolucion.objects.create(
            ticket_original=original, sucursal=self.sucursal,
            numero_operacion='CD-HOME-1', tipo_operacion='CAMBIO_PRODUCTO',
            estado='SOLICITADO', fecha_limite_cambio=self.hoy + timedelta(days=30),
            monto_original=30000, monto_nuevo=30000, diferencia_monto=0,
            solicitado_por=self.admin, motivo_principal='CAMBIO_TALLA',
        )

        r1 = self._home(self.admin)
        self.assertIn('1 cambios/devoluciones pendientes', self._alertas(r1))

        CambioDevolucion.objects.filter(pk=cambio.pk).update(estado='COMPLETADO')
        r2 = self._home(self.admin)

        self.assertTrue(r2.context['bloques_desde_cache'])   # la foto sigue en caché...
        self.assertNotIn('1 cambios/devoluciones pendientes', self._alertas(r2))  # ...la alerta no
        self.assertEqual(r2.context['operaciones']['cambios_pendientes'], 0)

    def test_enlace_capital_inmovilizado_solo_con_permiso_fifo(self):
        self.assertContains(self._home(self.admin), 'Ver capital inmovilizado')
        self.assertNotContains(self._home(self.cajero), 'Ver capital inmovilizado')

    def test_linea_base_de_ventas_nuevas_es_el_valor_pintado(self):
        self._ticket(10000, self.hoy, hora=time(0, 1))
        self._ticket(10000, self.hoy, hora=time(0, 2))
        html = self._home(self.admin).content.decode()
        self.assertRegex(html, r'var ticketsBase = 2;')

    def test_ultima_venta_muestra_hora_de_created_at(self):
        """Ticket.hora es auto_now (se reescribe en cada save): el chip
        «Última venta» usa la hora real de la venta (`created_at`)."""
        t = self._ticket(10000, self.hoy, hora=time(0, 1))
        Ticket.objects.filter(pk=t.pk).update(hora=time(23, 59))
        c = Client()
        c.force_login(self.admin)
        s = c.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()
        data = c.get('/app/dashboard/api/ventas-tiempo-real/').json()
        self.assertTrue(data['success'], data)
        self.assertEqual(data['ultima_venta']['hora'], '00:01')

    def test_aviso_facturas_proveedor_solo_con_permiso(self):
        proveedor = crear_empresa(nombre='Prov Vista', rut='77.555.666-7', esProveedor=True)
        Dte.objects.create(
            emisor=proveedor, receptor=self.empresa, numero_documento=8001,
            tipo_documento='FACTURA ELECTRONICA', monto_con_iva=Decimal(119000),
            monto_neto=Decimal(100000), descuento=Decimal(0), estado_pago='Pendiente',
            estado_dte='RECEPCIONADO_COMPLETO', responsable='test',
            fecha_emision=self.hoy - timedelta(days=40),
            fecha_vencimiento=self.hoy - timedelta(days=10), diasCredito=30, bultos=0,
            unidades_productos=1, sucursal=self.sucursal, tipo_transaccion='COMPRA',
            descartado=False, hora=timezone.localtime().time(),
        )

        admin = self._home(self.admin)
        cajero = self._home(self.cajero)

        self.assertTrue(any(t.startswith('Facturas de proveedor por pagar: 1 vencida')
                            for t in self._alertas(admin)))
        self.assertContains(admin, '/app/verGestionDteCompras/')
        self.assertFalse(any('Facturas de proveedor' in t for t in self._alertas(cajero)))
        self.assertIsNone(cajero.context['pagos_proveedor'])
