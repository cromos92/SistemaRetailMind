"""
«Recalcular teóricos del rango» de Revisión de Arqueos
(`recalcular_teoricos_masivo`).

Caso que lo origina (02-10-2026): la NC 3654 de NICK2 imputada al 17-09 no
movía el `Ef. Teórico` del arqueo cerrado, y la única salida era abrir el
detalle de cada arqueo y apretar «Actualizar Teórico» uno por uno.

El endpoint lista los arqueos del rango, los SIMULA (rollback: no escribe) y
APLICA sólo los que el usuario elige, en lotes chicos.
"""
import json
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import ArqueoCaja, LogAccionCaja, ObservacionArqueo, Ticket, TicketDetallePago
from app.views_modulo_ventas import MAX_ARQUEOS_POR_LOTE_RECALCULO, MAX_DIAS_RECALCULO_MASIVO

from .factories import crear_sucursal, crear_usuario, setup_entorno_completo

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class RecalculoTeoricosMasivoTest(TestCase):

    def setUp(self):
        self.env = setup_entorno_completo()
        self.sucursal = self.env['sucursal']
        self.otra = crear_sucursal(self.env['empresa'], alias='OTRA')
        self.hoy = timezone.localdate()
        self.admin = crear_usuario(username='admin_arqueos', rol='administrador')
        self.client = self._cliente(self.admin)
        self.url = reverse('recalcular_teoricos_masivo')

    def _cliente(self, usuario):
        client = Client()
        client.force_login(usuario)
        session = client.session
        session['idSucursalActual'] = self.sucursal.id
        session['idEmpresaActual'] = self.env['empresa'].id
        session.save()
        return client

    def _post(self, payload, client=None):
        return (client or self.client).post(
            self.url, data=json.dumps(payload), content_type='application/json')

    def _ticket_efectivo(self, correlativo, monto, sucursal=None, fecha=None):
        ticket = Ticket.objects.create(
            sucursal=sucursal or self.sucursal, correlativo=correlativo,
            subTotal=monto, total=monto, estado='PAGADO',
            vendedor=self.env['vendedor'], responsable='Test',
        )
        # `Ticket.fecha` es auto_now: se fuerza con update().
        Ticket.objects.filter(pk=ticket.pk).update(fecha=fecha or self.hoy)
        TicketDetallePago.objects.create(ticket=ticket, metodo_pago='EFECTIVO', monto=monto)

    def _arqueo(self, efectivo_teorico, sucursal=None, fecha=None, estado='REVISADO'):
        arqueo = ArqueoCaja.objects.create(
            fecha_arqueo=fecha or self.hoy, sucursal=sucursal or self.sucursal,
            usuario_responsable=self.env['user'], estado=estado,
        )
        # save() recalcula el físico desde billetes: los teóricos van por update().
        ArqueoCaja.objects.filter(pk=arqueo.pk).update(
            estado=estado, total_efectivo_teorico=efectivo_teorico,
            total_efectivo_fisico=efectivo_teorico, diferencia_efectivo=0,
        )
        arqueo.refresh_from_db()
        return arqueo

    # ---------- permisos y validaciones ----------

    def test_solo_administracion(self):
        cajero = crear_usuario(username='cajero_arq', rol='vendedor')
        resp = self._post({'modo': 'listar', 'fecha_desde': str(self.hoy),
                           'fecha_hasta': str(self.hoy), 'sucursal_id': self.sucursal.id},
                          client=self._cliente(cajero))
        self.assertEqual(resp.status_code, 403)

    def test_rango_maximo(self):
        desde = self.hoy - timedelta(days=MAX_DIAS_RECALCULO_MASIVO)
        resp = self._post({'modo': 'listar', 'fecha_desde': str(desde),
                           'fecha_hasta': str(self.hoy), 'sucursal_id': 'all'})
        self.assertEqual(resp.status_code, 400)

    def test_lote_maximo(self):
        ids = list(range(1, MAX_ARQUEOS_POR_LOTE_RECALCULO + 2))
        resp = self._post({'modo': 'simular', 'arqueo_ids': ids, 'sucursal_id': 'all'})
        self.assertEqual(resp.status_code, 400)

    # ---------- listar ----------

    def test_listar_respeta_rango_y_sucursal(self):
        a_hoy = self._arqueo(0)
        a_ayer = self._arqueo(0, fecha=self.hoy - timedelta(days=1))
        self._arqueo(0, fecha=self.hoy - timedelta(days=10))      # fuera de rango
        self._arqueo(0, sucursal=self.otra)                         # otra sucursal

        resp = self._post({'modo': 'listar', 'fecha_desde': str(self.hoy - timedelta(days=1)),
                           'fecha_hasta': str(self.hoy), 'sucursal_id': self.sucursal.id})

        self.assertEqual(resp.status_code, 200, resp.content)
        ids = [a['id'] for a in resp.json()['arqueos']]
        self.assertEqual(ids, [a_ayer.id, a_hoy.id])

    # ---------- simular ----------

    def test_simular_informa_cambios_sin_escribir(self):
        self._ticket_efectivo(1, 50000)
        arqueo = self._arqueo(122460)

        resp = self._post({'modo': 'simular', 'arqueo_ids': [arqueo.id],
                           'sucursal_id': self.sucursal.id})

        self.assertEqual(resp.status_code, 200, resp.content)
        fila = resp.json()['resultados'][0]
        self.assertTrue(fila['hay_cambios'])
        self.assertEqual(fila['cambios']['total_efectivo_teorico'],
                         {'antes': 122460, 'despues': 50000})
        # Físico 122.460 contra teórico nuevo 50.000.
        self.assertEqual(fila['dif_efectivo_antes'], 0)
        self.assertEqual(fila['dif_efectivo_despues'], 72460)
        arqueo.refresh_from_db()
        self.assertEqual(arqueo.total_efectivo_teorico, 122460)
        self.assertEqual(arqueo.diferencia_efectivo, 0)
        self.assertFalse(ObservacionArqueo.objects.filter(arqueo=arqueo).exists())

    def test_simular_ignora_arqueos_de_otra_sucursal(self):
        ajeno = self._arqueo(122460, sucursal=self.otra)

        resp = self._post({'modo': 'simular', 'arqueo_ids': [ajeno.id],
                           'sucursal_id': self.sucursal.id})

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['resultados'], [])

    # ---------- aplicar ----------

    def test_aplicar_exige_motivo(self):
        arqueo = self._arqueo(122460)
        resp = self._post({'modo': 'aplicar', 'arqueo_ids': [arqueo.id],
                           'sucursal_id': self.sucursal.id, 'razon': ''})
        self.assertEqual(resp.status_code, 400)
        arqueo.refresh_from_db()
        self.assertEqual(arqueo.total_efectivo_teorico, 122460)

    def test_aplicar_actualiza_teorico_bitacora_y_log(self):
        self._ticket_efectivo(2, 50000)
        arqueo = self._arqueo(122460)

        resp = self._post({'modo': 'aplicar', 'arqueo_ids': [arqueo.id],
                           'sucursal_id': self.sucursal.id, 'razon': 'NC posterior al cierre'})

        self.assertEqual(resp.status_code, 200, resp.content)
        arqueo.refresh_from_db()
        self.assertEqual(arqueo.total_efectivo_teorico, 50000)
        self.assertEqual(arqueo.diferencia_efectivo, 72460)
        # No cambia el estado de un arqueo ya revisado.
        self.assertEqual(arqueo.estado, 'REVISADO')
        obs = ObservacionArqueo.objects.get(arqueo=arqueo, tipo='SISTEMA')
        self.assertIn('recálculo masivo: NC posterior al cierre', obs.texto)
        self.assertEqual(obs.usuario, self.admin)
        self.assertTrue(LogAccionCaja.objects.filter(
            arqueo=arqueo, accion='RECALCULAR_TEORICOS').exists())

    def test_aplicar_sin_cambios_no_deja_bitacora(self):
        arqueo = self._arqueo(0)

        resp = self._post({'modo': 'aplicar', 'arqueo_ids': [arqueo.id],
                           'sucursal_id': self.sucursal.id, 'razon': 'revisión mensual'})

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(resp.json()['resultados'][0]['hay_cambios'])
        self.assertFalse(ObservacionArqueo.objects.filter(arqueo=arqueo).exists())
