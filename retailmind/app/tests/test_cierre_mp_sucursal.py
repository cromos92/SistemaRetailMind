"""Tests del cierre Mercado Pago impreso en la máquina Point: QUÉ SUCURSAL sale.

Incidente que los origina (21-09-2026): el cierre sacado desde PAO1 mostraba
una venta del día muy superior a lo que esa tienda había vendido. El papel se
arma con dos fuentes distintas —los cobros MP de la CAJA y la venta del día de
la SUCURSAL de esa caja (`_calcular_cuadratura_data`)— así que basta con que la
caja elegida sea de otra tienda para que todo el bloque «VENTA DEL DÍA» sea de
esa otra tienda.

La caja se elegía en un `<select>` cuyo `selected` del template exigía, además
de ser de la sucursal de sesión, que la caja tuviera `es_principal`. Una
sucursal sin principal marcada dejaba el select SIN opción elegida y el
navegador caía en la PRIMERA de la lista, ordenada por alias: NICK1 estando en
PAO1. El servidor, por su lado, obedecía ese `config_id` sin mirar la sesión.

Sin red: no se toca la API de Mercado Pago (el control se mockea).
"""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from app.models import MercadoPagoConfig
from app.services import mercadopago_service as mp
from app.tests.factories import crear_empresa, crear_sucursal, crear_usuario
from app.views_mercadopago import caja_mp_de_sucursal

DEVICE = 'N950NCD400023750'
OTRO_DEVICE = 'N950NCD400099999'


def _caja(sucursal, nombre='Caja principal', **kwargs):
    defaults = dict(
        nombre=nombre,
        habilitado=True,
        modo='POINT',
        token_env='MP_TOKEN_TEST',
        external_pos_id='POS001',
        device_id=DEVICE,
        es_principal=True,
    )
    defaults.update(kwargs)
    return MercadoPagoConfig.objects.create(sucursal=sucursal, **defaults)


class BaseCierreSucursalTest(TestCase):
    """Dos tiendas de la misma empresa, con la de alias menor primera en la
    lista: es el orden real del selector (`order_by('sucursal__alias')`)."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.nick1 = crear_sucursal(empresa=cls.empresa, alias='NICK1')
        cls.pao1 = crear_sucursal(empresa=cls.empresa, alias='PAO1')
        cls.caja_nick1 = _caja(cls.nick1, nombre='Caja NICK1')
        # PAO1 sin la estrella de principal: el caso que rompía el selector.
        cls.caja_pao1 = _caja(cls.pao1, nombre='Caja PAO1',
                              device_id=OTRO_DEVICE, es_principal=False)
        cls.admin = crear_usuario(username='admin_cierre', rol='administrador')
        cls.cajero = crear_usuario(username='cajero_cierre', rol='cajero')

    def _login(self, usuario, sucursal):
        self.client.force_login(usuario)
        session = self.client.session
        session['idSucursalActual'] = sucursal.id
        session.save()


class ResolucionDeCajaTests(BaseCierreSucursalTest):
    """`caja_mp_de_sucursal` es la única regla; nunca devuelve caja ajena."""

    def test_devuelve_la_caja_de_la_sucursal_aunque_no_sea_principal(self):
        self.assertEqual(caja_mp_de_sucursal(self.pao1.id).id, self.caja_pao1.id)
        self.assertEqual(caja_mp_de_sucursal(self.pao1.id, con_maquina=True).id,
                         self.caja_pao1.id)

    def test_prefiere_la_principal_cuando_hay_varias(self):
        segunda = _caja(self.pao1, nombre='Caja 2 PAO1', device_id=OTRO_DEVICE,
                        es_principal=True)
        self.assertEqual(caja_mp_de_sucursal(self.pao1.id, con_maquina=True).id,
                         segunda.id)

    def test_con_maquina_exige_habilitada_y_point(self):
        self.caja_pao1.device_id = ''
        self.caja_pao1.save(update_fields=['device_id'])
        self.assertIsNone(caja_mp_de_sucursal(self.pao1.id, con_maquina=True))
        # Para preseleccionar igual se prefiere la propia antes que una ajena
        self.assertEqual(caja_mp_de_sucursal(self.pao1.id).id, self.caja_pao1.id)

    def test_sin_sucursal_no_inventa_caja(self):
        self.assertIsNone(caja_mp_de_sucursal(None))
        self.assertIsNone(caja_mp_de_sucursal(0))


class PreseleccionDelSelectorTests(BaseCierreSucursalTest):
    """La página tiene que llegar con la caja de la sesión ya elegida."""

    def test_admin_en_pao1_preselecciona_la_caja_de_pao1(self):
        self._login(self.admin, self.pao1)
        resp = self.client.get('/app/pos/transbank/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['config_mp_sesion_id'], self.caja_pao1.id)
        # Y el <option> elegido en el HTML es el de PAO1, no el primero (NICK1)
        html = resp.content.decode('utf-8')
        elegido = f'<option value="{self.caja_pao1.id}" data-sucursal="{self.pao1.id}" selected>'
        self.assertIn(elegido, html)
        self.assertNotIn(f'<option value="{self.caja_nick1.id}" '
                         f'data-sucursal="{self.nick1.id}" selected>', html)

    def test_sucursal_sin_caja_no_preselecciona_ajena(self):
        sin_caja = crear_sucursal(empresa=self.empresa, alias='ZZZ1')
        self._login(self.admin, sin_caja)
        resp = self.client.get('/app/pos/transbank/')
        self.assertIsNone(resp.context['config_mp_sesion_id'])
        self.assertFalse(resp.context['sesion_tiene_caja_mp'])

    def test_sucursal_de_sesion_tambien_sale_de_la_clave_pos(self):
        """`_sucursal_sesion` acepta `idSucursalActualPOS`; la página también."""
        self.client.force_login(self.admin)
        session = self.client.session
        session['idSucursalActualPOS'] = self.pao1.id
        session.save()
        resp = self.client.get('/app/pos/transbank/')
        self.assertEqual(resp.context['sucursal_sesion_id'], self.pao1.id)
        self.assertEqual(resp.context['config_mp_sesion_id'], self.caja_pao1.id)


@mock.patch('app.services.mercadopago_service.imprimir_en_terminal')
@mock.patch('app.services.mercadopago_service.conciliar_cierre_mp',
            return_value={'ok': False, 'error': 'sin red'})
class ImprimirCierreTests(BaseCierreSucursalTest):
    """El endpoint no puede imprimir la venta de otra tienda por omisión."""

    URL = '/app/pos/mercadopago/gestion/terminal/imprimir-cierre/'

    def test_admin_sin_config_id_usa_su_sucursal_de_sesion(self, _m_con, m_imp):
        self._login(self.admin, self.pao1)
        resp = self.client.post(self.URL, {'fecha': '2026-09-20'})
        self.assertEqual(resp.status_code, 200, resp.content)
        datos = resp.json()
        self.assertEqual(datos['sucursal'], 'PAO1')
        self.assertTrue(datos['es_de_tu_sucursal'])
        # Se imprimió con la caja de PAO1, no con la de NICK1
        self.assertEqual(m_imp.call_args.args[0].id, self.caja_pao1.id)

    def test_el_papel_dice_de_que_sucursal_es_la_venta(self, _m_con, m_imp):
        self._login(self.admin, self.pao1)
        self.client.post(self.URL, {'fecha': '2026-09-20'})
        contenido = m_imp.call_args.args[1]
        self.assertIn('PAO1', contenido)
        self.assertNotIn('NICK1', contenido)

    def test_admin_con_caja_ajena_recibe_el_aviso(self, _m_con, m_imp):
        self._login(self.admin, self.pao1)
        resp = self.client.post(self.URL, {'config_id': self.caja_nick1.id,
                                           'fecha': '2026-09-20'})
        self.assertEqual(resp.status_code, 200, resp.content)
        datos = resp.json()
        self.assertEqual(datos['sucursal'], 'NICK1')
        self.assertFalse(datos['es_de_tu_sucursal'])
        self.assertEqual(m_imp.call_args.args[0].id, self.caja_nick1.id)

    def test_no_admin_ignora_el_config_id_que_le_manden(self, _m_con, m_imp):
        """El cajero no tiene selector; un config_id en el POST no lo saca de
        su sucursal (el gate es del servidor, no de la pantalla)."""
        self._login(self.cajero, self.pao1)
        resp = self.client.post(self.URL, {'config_id': self.caja_nick1.id,
                                           'fecha': '2026-09-20'})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['sucursal'], 'PAO1')
        self.assertEqual(m_imp.call_args.args[0].id, self.caja_pao1.id)

    def test_sin_sucursal_en_sesion_no_imprime(self, _m_con, m_imp):
        self.client.force_login(self.admin)
        resp = self.client.post(self.URL, {'fecha': '2026-09-20'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('sucursal en sesión', resp.json()['error'])
        m_imp.assert_not_called()

    def test_caja_inexistente_sigue_siendo_404(self, _m_con, m_imp):
        self._login(self.admin, self.pao1)
        resp = self.client.post(self.URL, {'config_id': 999999, 'fecha': '2026-09-20'})
        self.assertEqual(resp.status_code, 404)
        m_imp.assert_not_called()


class PapelDiceLaSucursalTests(TestCase):
    """El bloque de venta del día tiene que identificarse solo."""

    def _cierre(self, **extra):
        vacio = {'cobros': 0, 'monto': 0, 'devoluciones': 0,
                 'monto_devuelto': 0, 'comisiones': 0}
        caja = {'caja': 'Caja PAO1', 'sucursal': 'PAO1', 'QR': dict(vacio),
                'POINT': dict(vacio), 'medios': {}, 'total_neto': 0}
        caja.update(extra)
        return mp.contenido_cierre_terminal(caja, '2026-09-20')

    def test_nombra_la_sucursal_del_bloque_de_venta(self):
        texto = self._cierre(dia_sucursal=[('EFECTIVO', 120000)], dia_nc=0,
                             dia_total_global=120000)
        self.assertIn('VENTA DEL DIA - TODOS', texto)
        self.assertIn('Sucursal: PAO1', texto)
        self.assertIn('toda la sucursal', texto)

    def test_las_lineas_nuevas_caben_en_el_papel(self):
        texto = self._cierre(dia_sucursal=[('EFECTIVO', 120000)], dia_nc=0,
                             dia_total_global=120000)
        for renglon in texto.replace('{center}', '').replace('{w}', '').split('{br}'):
            self.assertLessEqual(len(renglon.replace('{s}', '')), 32, renglon)

    def test_sin_venta_del_dia_no_aparece_el_bloque(self):
        texto = self._cierre()
        self.assertNotIn('VENTA DEL DIA', texto)
        self.assertNotIn('Sucursal: PAO1', texto)


class ComandoDiagnosticoTests(BaseCierreSucursalTest):
    """`diagnosticar_cierre_mp` es solo lectura y tiene que correr en prod."""

    def _correr(self, *args):
        salida = StringIO()
        call_command('diagnosticar_cierre_mp', *args, stdout=salida, no_color=True)
        return salida.getvalue()

    def test_lista_las_cajas_y_que_resuelve_cada_sucursal(self):
        texto = self._correr()
        self.assertIn('CAJAS MERCADO PAGO', texto)
        self.assertIn('Caja PAO1', texto)
        self.assertIn('QUÉ CAJA RESUELVE CADA SUCURSAL', texto)
        # PAO1 no tiene caja principal: el comando lo señala
        self.assertIn('principal', texto)

    def test_diagnostica_una_sucursal_sin_tocar_la_api(self):
        texto = self._correr('--sucursal', 'PAO1', '--fecha', '2026-09-20', '--papel')
        self.assertIn('CIERRE QUE SALDRIA PARA PAO1', texto)
        self.assertIn('Caja PAO1', texto)
        self.assertIn('VENTA DEL DIA DE PAO1', texto)

    def test_sucursal_inexistente_no_revienta(self):
        texto = self._correr('--sucursal', 'NOEXISTE')
        self.assertIn('No existe la sucursal', texto)
