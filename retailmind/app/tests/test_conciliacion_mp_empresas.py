"""
Conciliación Mercado Pago por empresa: pestañas «Liberaciones y banco» y
«Asignación de retiros» (services/conciliacion_mp_empresas.py).

Correr en BD desechable:
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_conciliacion_mp_empresas
"""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import MercadoPagoCuenta, RetiroMercadoPago, TicketDetallePago, Ticket
from app.services import conciliacion_mp_empresas as emp
from app.services import conciliacion_mp_service as conc

from .factories import crear_empresa, crear_sucursal, crear_usuario, crear_vendedor
from .test_conciliacion_mp import REPORTE_PARCIAL
from .test_mercadopago_pos import _config, _transaccion


class _Base(TestCase):
    """Dos empresas, cada una con su cuenta MP: A (tiendas A1 y A2) y B (tienda B1)."""

    def setUp(self):
        cache.clear()
        self.hoy = timezone.localdate()
        self.emp_a = crear_empresa(nombre='Calzados A', rut='76.111.111-1')
        self.emp_b = crear_empresa(nombre='Deportes B', rut='77.222.222-2')
        MercadoPagoCuenta.objects.create(empresa=self.emp_a, mp_user_id='111')
        MercadoPagoCuenta.objects.create(empresa=self.emp_b, mp_user_id='222')
        self.a1 = crear_sucursal(empresa=self.emp_a, alias='A1')
        self.a2 = crear_sucursal(empresa=self.emp_a, alias='A2')
        self.b1 = crear_sucursal(empresa=self.emp_b, alias='B1')
        self.cfg_a1 = _config(self.a1, nombre='Caja 1', external_pos_id='PA1')
        self.cfg_a2 = _config(self.a2, nombre='Caja 1', external_pos_id='PA2')
        self.cfg_b1 = _config(self.b1, nombre='Caja 1', external_pos_id='PB1')
        self.vendedor = crear_vendedor(empresa=self.emp_a)

    def _retiro(self, cfg, wid, monto, dias=0, **kw):
        return RetiroMercadoPago.objects.create(config=cfg, withdrawal_id=wid, monto=monto,
                                                fecha=self.hoy - timedelta(days=dias), **kw)

    def _cobro(self, cfg, corr, monto, neto, **kw):
        return _transaccion(cfg, correlativo=str(corr), monto=monto, monto_neto=neto, canal='POINT',
                            consumida=True, payment_id_mp=str(corr), **kw)


class EmpresasTest(_Base):

    def test_agrupa_cajas_por_cuenta_y_filtra_por_tienda(self):
        todas = emp.empresas_mp()
        self.assertEqual([e['nombre'] for e in todas], ['Calzados A', 'Deportes B'])
        a = todas[0]
        self.assertEqual(sorted(a['configs']), sorted([self.cfg_a1.id, self.cfg_a2.id]))
        self.assertEqual(a['tiendas'], ['A1', 'A2'])
        self.assertEqual(a['rut'], '76.111.111-1')
        solo_b = emp.empresas_mp(self.b1.id)
        self.assertEqual([e['nombre'] for e in solo_b], ['Deportes B'])


class ComposicionTest(_Base):

    def test_partes_suman_el_monto(self):
        r = self._retiro(self.cfg_a1, 'W1', 20000, estado='CONCILIADO', raw_reporte={
            'por_caja': [{'caja': 'A1 · Caja 1', 'monto': 9000}, {'caja': 'A1 · MP manual', 'monto': 4000},
                         {'caja': 'Sin caja (online, link de pago u otro)', 'monto': 3000},
                         {'caja': 'A2 · Caja 1', 'monto': 2000}, {'caja': 'Saldo anterior', 'monto': 2000}],
            # 3.000 sin caja + 1.500 de la A2 sin venta en el sistema + 2.000 saldo anterior
            'sin_venta': 6500, 'sin_local': {'900': 3000, '901': 1500}, 'quedan': 0})
        c = emp.composicion_retiro(r)
        self.assertEqual(c['partes'], {'pos': 9500, 'manual': 4000, 'otros': 4500, 'anterior': 2000, 'sin_explicar': 0})
        self.assertEqual(sum(c['partes'].values()), 20000)
        self.assertEqual(c['identificado'], 13500)
        self.assertEqual([t['tienda'] for t in c['tiendas']], ['A1', 'A2'])
        self.assertEqual((c['tiendas'][0]['monto'], c['tiendas'][0]['manual']), (13000, 4000))
        # La operación 901 se asoció después a una venta: deja de contar como «sin venta».
        c2 = emp.composicion_retiro(r, con_cobro=frozenset({'901'}))
        self.assertEqual((c2['partes']['pos'], c2['partes']['otros']), (11000, 3000))

    def test_retiro_sin_desglose_usa_sus_ventas(self):
        r = self._retiro(self.cfg_a1, 'WL', 10000, raw_reporte={'archivos': ['a.csv']})
        c = emp.composicion_retiro(r, neto_amarradas=7000)
        self.assertEqual((c['partes']['pos'], c['partes']['sin_explicar'], c['con_desglose']), (7000, 3000, False))
        self.assertIsNone(emp.cuanto_quedo(r))

    def test_total_o_parcial(self):
        guardado = self._retiro(self.cfg_a1, 'WQ', 1000, raw_reporte={'por_caja': [{'caja': 'Saldo anterior', 'monto': 1000}], 'quedan': 500})
        self.assertEqual(emp.cuanto_quedo(guardado), 500)
        # Retiros aplicados antes de guardar `quedan`: se lee del detalle.
        viejo = self._retiro(self.cfg_a1, 'WV', 1000, estado='CONCILIADO',
                             detalle_diferencia='Retiro parcial: quedaron $36.400.000 disponibles en Mercado Pago.',
                             raw_reporte={'por_caja': [{'caja': 'Saldo anterior', 'monto': 1000}]})
        self.assertEqual(emp.cuanto_quedo(viejo), 36400000)
        todo = self._retiro(self.cfg_a1, 'WT', 1000, estado='CONCILIADO',
                            raw_reporte={'por_caja': [{'caja': 'A1 · Caja 1', 'monto': 1000}]})
        self.assertEqual(emp.cuanto_quedo(todo), 0)
        fila = emp.describir_retiros([viejo, todo])
        self.assertEqual([f['tipo'] for f in fila], ['PARCIAL', 'TOTAL'])

    def test_el_reporte_guarda_lo_que_quedo(self):
        _transaccion(self.cfg_a1, correlativo='1', payment_id_mp='601', monto=61000, canal='POINT', consumida=True)
        _transaccion(self.cfg_a1, correlativo='2', payment_id_mp='602', monto=71000, canal='POINT', consumida=True)
        conc.procesar_reporte_liberaciones(conc.leer_csv(REPORTE_PARCIAL), self.cfg_a1, aplicar=True, archivo='r1.csv')
        manual = RetiroMercadoPago.objects.get(withdrawal_id='P-MANUAL')
        self.assertEqual(manual.raw_reporte['quedan'], 50000)
        self.assertEqual(emp.describir_retiros([manual])[0]['tipo'], 'PARCIAL')


class LiberacionesPorEmpresaTest(_Base):

    def _datos(self):
        w1 = self._retiro(self.cfg_a2, 'W1', 14550, estado='CONCILIADO', visto_en_cartola=True, raw_reporte={
            'por_caja': [{'caja': 'A1 · Caja 1', 'monto': 9700}, {'caja': 'A2 · Caja 1', 'monto': 4850}],
            'sin_venta': 0, 'quedan': 0, 'instante': timezone.now().isoformat()})
        self._cobro(self.cfg_a1, 1, 10000, 9700, retiro=w1)
        self._cobro(self.cfg_a2, 2, 5000, 4850, retiro=w1)
        self._cobro(self.cfg_a1, 3, 2000, 1940, money_release_date=timezone.now() + timedelta(days=3))
        self._cobro(self.cfg_a2, 4, 3000, 2910, money_release_date=timezone.now() - timedelta(hours=2))
        self._retiro(self.cfg_a1, 'W-VIEJO', 50000, dias=60)
        self._retiro(self.cfg_b1, 'WB', 68000)
        self._cobro(self.cfg_b1, 50, 70000, 68000)

    def test_por_empresa_con_sus_retiros(self):
        self._datos()
        d = emp.liberaciones_por_empresa(str(self.hoy - timedelta(days=6)), str(self.hoy))
        a, b = d['empresas']
        self.assertEqual(a['nombre'], 'Calzados A')
        self.assertEqual([r['withdrawal_id'] for r in a['retiros']], ['W1'])   # W-VIEJO queda fuera del período
        w1 = a['retiros'][0]
        self.assertEqual((w1['ventas_pos'], w1['tipo'], w1['etapa'], w1['partes']['pos']), (2, 'TOTAL', 'ABONADO', 14550))
        self.assertEqual((a['hoy']['por_liberar'], a['hoy']['disponible']), (1940, 2910))
        self.assertEqual((a['periodo']['retirado'], a['periodo']['abonado'], a['periodo']['en_transito']), (14550, 14550, 0))
        self.assertEqual(a['periodo']['cobrado_neto'], 9700 + 4850 + 1940 + 2910)
        self.assertEqual(a['ultimo_retiro']['monto'], 14550)
        self.assertEqual([r['withdrawal_id'] for r in b['retiros']], ['WB'])
        self.assertEqual(b['hoy']['disponible'], 68000)
        # Retiro viejo sin abono: aviso de arrastre.
        self.assertEqual(a['arrastre_sin_abonar_n'], 1)

    def test_con_tienda_solo_su_empresa_y_su_parte(self):
        self._datos()
        d = emp.liberaciones_por_empresa(None, None, sucursal_id=self.a2.id)
        self.assertEqual([e['nombre'] for e in d['empresas']], ['Calzados A'])
        self.assertEqual(d['empresas'][0]['retiros'][0]['de_la_tienda'], 4850)

    def test_asignacion_de_una_empresa(self):
        self._datos()
        self._retiro(self.cfg_a1, 'W0', 100000, estado='CONCILIADO',
                     detalle_diferencia='Retiro parcial: quedaron $5.000 disponibles en Mercado Pago.',
                     raw_reporte={'por_caja': [{'caja': 'Saldo anterior', 'monto': 100000}], 'sin_venta': 100000,
                                  'instante': (timezone.now() - timedelta(hours=5)).isoformat()})
        d = emp.asignacion_empresa(self.hoy.strftime('%Y-%m'))
        self.assertEqual(d['empresa']['nombre'], 'Calzados A')
        self.assertEqual([e['nombre'] for e in d['empresas']], ['Calzados A', 'Deportes B'])
        # En orden: el más antiguo primero.
        ids = [r['withdrawal_id'] for r in d['retiros']]
        self.assertEqual(ids, ['W0', 'W1'] if d['retiros'][0]['hora'] <= d['retiros'][1]['hora'] else ['W1', 'W0'])
        w0 = next(r for r in d['retiros'] if r['withdrawal_id'] == 'W0')
        self.assertEqual((w0['tipo'], w0['quedan'], w0['partes']['anterior']), ('PARCIAL', 5000, 100000))
        self.assertEqual((d['queda']['disponible'], d['queda']['por_liberar']), (2910, 1940))
        self.assertEqual(d['resumen']['neto'], 9700 + 4850 + 1940 + 2910)
        self.assertTrue(all(c['sucursal'] in ('A1', 'A2') for c in d['cobros']))
        clave_b = next(e['clave'] for e in d['empresas'] if e['nombre'] == 'Deportes B')
        b = emp.asignacion_empresa(self.hoy.strftime('%Y-%m'), clave_b)
        self.assertEqual([r['withdrawal_id'] for r in b['retiros']], ['WB'])
        self.assertEqual(b['resumen']['neto'], 68000)


class ApiEmpresasTest(_Base):

    def setUp(self):
        super().setUp()
        self._retiro(self.cfg_a1, 'W1', 9700, raw_reporte={'por_caja': [{'caja': 'A1 · Caja 1', 'monto': 9700}], 'sin_venta': 0})
        self._retiro(self.cfg_b1, 'WB', 5000)

    def _cliente(self, usuario, sucursal):
        c = Client()
        c.force_login(usuario)
        s = c.session
        s['idSucursalActual'] = sucursal.id
        s.save()
        return c

    def test_admin_ve_todas_y_la_asignacion(self):
        c = self._cliente(crear_usuario(username='adm_emp', rol='administrador'), self.a1)
        with mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True):
            d = c.get(reverse('api_conciliacion_empresas_mp')).json()
            self.assertEqual([e['nombre'] for e in d['empresas']], ['Calzados A', 'Deportes B'])
            a = c.get(reverse('api_conciliacion_asignacion_empresa_mp'), {'mes': 'x'}).json()
            self.assertEqual((a['success'], a['mes']), (True, self.hoy.strftime('%Y-%m')))
            det = c.get(reverse('api_conciliacion_retiro_detalle_mp', args=['W1'])).json()
            self.assertEqual(det['resumen']['partes']['pos'], 9700)
            pagina = c.get(reverse('dineros_mercadopago'))
            self.assertContains(pagina, 'id="empresasBanco"')
            self.assertContains(pagina, '¿Cómo se asigna una venta a un retiro?')

    def test_vendedor_ve_solo_su_empresa_y_no_la_asignacion(self):
        c = self._cliente(crear_usuario(username='vend_emp', rol='vendedor'), self.b1)
        with mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True):
            d = c.get(reverse('api_conciliacion_empresas_mp'), {'sucursal_id': self.a1.id}).json()
            self.assertEqual([e['nombre'] for e in d['empresas']], ['Deportes B'])
            self.assertEqual(c.get(reverse('api_conciliacion_asignacion_empresa_mp')).status_code, 403)
