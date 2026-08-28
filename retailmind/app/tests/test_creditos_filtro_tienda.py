"""
Tests del filtro por tienda de Gestión de Créditos y del PDF de uso de cupos.

Contexto
--------
Un `CreditoTrabajador` tiene DOS sucursales que casi nunca coinciden:

  * `CreditoTrabajador.sucursal`            -> dónde se EMITIÓ el cupo
  * `PagoCreditoTrabajador.sucursal_cobro`  -> dónde se GASTÓ en el POS

Medido en producción: 17 de los 24 usos registrados ocurrieron en una tienda
distinta a la emisora. El filtro de la pantalla sólo miraba la emisora, así que
preguntar por una tienda devolvía los cupos que ella entregó — no lo que se
consumió ahí — y el PDF exportaba justamente lo que no se estaba buscando.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from app.models import Cliente, CreditoTrabajador, PagoCreditoTrabajador, Sucursal
from app.views_modulo_creditos import (
    CRITERIO_SUCURSAL_EMITIDA,
    CRITERIO_SUCURSAL_USADA,
    _queryset_creditos_filtrado,
    _filas_pdf_creditos,
)

from .factories import crear_sucursal, setup_entorno_completo

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


class _FakeRequest:
    """Request mínimo: `_queryset_creditos_filtrado` sólo usa user + session."""

    def __init__(self, user, session):
        self.user = user
        self.session = session
        self.GET = {}


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class FiltroTiendaCreditosTest(TestCase):

    def setUp(self):
        self.env = setup_entorno_completo()
        self.user = self.env['user']
        self.cliente = Cliente.objects.create(
            nombre='Juan', apellido='Perez', rut='11.111.111-1')
        self.user.rol = 'administrador'
        self.user.save(update_fields=['rol'])
        self.emisora = self.env['sucursal']
        self.tienda_uso = crear_sucursal(empresa=self.env['empresa'], alias='PAO4')
        Sucursal.objects.filter(pk=self.tienda_uso.pk).update(
            direccion='Calle Comercio 45',
        )
        self.tienda_uso.refresh_from_db()
        self.session = {
            'idEmpresaActual': self.env['empresa'].id,
            'idSucursalActual': self.emisora.id,
        }

    def _crear_credito(self, numero, sucursal, monto=150000, aprobado=None):
        return CreditoTrabajador.objects.create(
            numero_credito=numero,
            beneficiario=self.cliente,
            empresa_origen=self.env['empresa'],
            sucursal=sucursal,
            monto_solicitado=Decimal(monto),
            monto_aprobado=Decimal(aprobado) if aprobado is not None else None,
            estado='ACTIVO',
            solicitado_por=self.user,
            fecha_vencimiento=timezone.localdate() + timedelta(days=30),
        )

    def _usar(self, credito, sucursal_cobro, monto, boleta='BOLETA ELECTRONICA-410020'):
        return PagoCreditoTrabajador.objects.create(
            credito=credito,
            numero_pago=f'P-{credito.numero_credito}-{monto}',
            monto_pago=Decimal(monto),
            fecha_pago=timezone.localdate(),
            metodo_pago='CREDITO_TRABAJADOR',
            referencia_pago=boleta,
            registrado_por=self.user,
            sucursal_cobro=sucursal_cobro,
        )

    def _filtrar(self, **data):
        req = _FakeRequest(self.user, self.session)
        data.setdefault('alcance', 'todas')
        qs, alcance, error = _queryset_creditos_filtrado(req, data)
        self.assertIsNone(error, error)
        return qs

    # ---------- el caso que motivó el cambio ----------

    def test_credito_gastado_en_otra_tienda_aparece_al_filtrar_por_donde_se_uso(self):
        credito = self._crear_credito('CR-0001', self.emisora)
        self._usar(credito, self.tienda_uso, 110000)

        usados = self._filtrar(sucursal_id=self.tienda_uso.id,
                               criterio_sucursal=CRITERIO_SUCURSAL_USADA)
        self.assertEqual(list(usados), [credito])

        # Y NO aparece al preguntar por los cupos que esa tienda emitió.
        emitidos = self._filtrar(sucursal_id=self.tienda_uso.id,
                                 criterio_sucursal=CRITERIO_SUCURSAL_EMITIDA)
        self.assertEqual(list(emitidos), [])

    def test_criterio_emitida_conserva_el_comportamiento_anterior(self):
        credito = self._crear_credito('CR-0002', self.emisora)
        self._usar(credito, self.tienda_uso, 50000)

        emitidos = self._filtrar(sucursal_id=self.emisora.id,
                                 criterio_sucursal=CRITERIO_SUCURSAL_EMITIDA)
        self.assertEqual(list(emitidos), [credito])
        # En la emisora no se gastó nada.
        self.assertEqual(
            list(self._filtrar(sucursal_id=self.emisora.id,
                               criterio_sucursal=CRITERIO_SUCURSAL_USADA)),
            [],
        )

    def test_sin_criterio_explicito_se_asume_emitida(self):
        """Compatibilidad: un enlace viejo sin `criterio_sucursal` no cambia."""
        credito = self._crear_credito('CR-0003', self.emisora)
        self._usar(credito, self.tienda_uso, 50000)
        self.assertEqual(list(self._filtrar(sucursal_id=self.emisora.id)), [credito])

    def test_dos_usos_en_la_misma_tienda_no_duplican_el_credito(self):
        """Sin `distinct()` el join contra `pagos` devolvía el crédito 2 veces."""
        credito = self._crear_credito('CR-0004', self.emisora)
        self._usar(credito, self.tienda_uso, 40000, 'BOLETA ELECTRONICA-1')
        self._usar(credito, self.tienda_uso, 60000, 'BOLETA ELECTRONICA-2')

        qs = self._filtrar(sucursal_id=self.tienda_uso.id,
                           criterio_sucursal=CRITERIO_SUCURSAL_USADA)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(list(qs), [credito])

    def test_abono_en_efectivo_no_cuenta_como_uso(self):
        """Sólo CREDITO_TRABAJADOR/CREDITO_EXTERNO son consumo del cupo."""
        credito = self._crear_credito('CR-0005', self.emisora)
        PagoCreditoTrabajador.objects.create(
            credito=credito, numero_pago='ABONO-1', monto_pago=Decimal(30000),
            fecha_pago=timezone.localdate(), metodo_pago='EFECTIVO',
            registrado_por=self.user, sucursal_cobro=self.tienda_uso,
        )
        self.assertEqual(
            list(self._filtrar(sucursal_id=self.tienda_uso.id,
                               criterio_sucursal=CRITERIO_SUCURSAL_USADA)),
            [],
        )

    def test_sucursal_invalida_devuelve_error_y_no_revienta(self):
        req = _FakeRequest(self.user, self.session)
        qs, _, error = _queryset_creditos_filtrado(
            req, {'alcance': 'todas', 'sucursal_id': 'abc'})
        self.assertIsNone(qs)
        self.assertIn('Sucursal', error)

    def test_todas_incluye_creditos_de_sucursal_inactiva(self):
        """
        El alcance 'todas' enumeraba sólo las sucursales `activa=True`, así que
        un crédito de una tienda desactivada desaparecía del listado y del PDF
        sin ningún aviso.
        """
        cerrada = crear_sucursal(empresa=self.env['empresa'], alias='CERRADA')
        Sucursal.objects.filter(pk=cerrada.pk).update(activa=False)
        credito = self._crear_credito('CR-0006', cerrada)

        self.assertIn(credito, list(self._filtrar()))


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class FilasPdfCreditosTest(TestCase):
    """El PDF debe traer el cupo, lo consumido y la tienda con dirección."""

    def setUp(self):
        self.env = setup_entorno_completo()
        self.user = self.env['user']
        self.cliente = Cliente.objects.create(
            nombre='Juan', apellido='Perez', rut='11.111.111-1')
        self.emisora = self.env['sucursal']
        self.tienda_uso = crear_sucursal(empresa=self.env['empresa'], alias='PAO4')
        Sucursal.objects.filter(pk=self.tienda_uso.pk).update(
            direccion='Calle Comercio 45',
        )
        self.tienda_uso.refresh_from_db()

    def _credito_con_uso(self, numero, solicitado, aprobado, usos):
        credito = CreditoTrabajador.objects.create(
            numero_credito=numero,
            beneficiario=self.cliente,
            empresa_origen=self.env['empresa'],
            sucursal=self.emisora,
            monto_solicitado=Decimal(solicitado),
            monto_aprobado=Decimal(aprobado) if aprobado is not None else None,
            estado='ACTIVO',
            solicitado_por=self.user,
            fecha_vencimiento=timezone.localdate() + timedelta(days=30),
        )
        for idx, monto in enumerate(usos, start=1):
            PagoCreditoTrabajador.objects.create(
                credito=credito, numero_pago=f'{numero}-{idx}',
                monto_pago=Decimal(monto), fecha_pago=timezone.localdate(),
                metodo_pago='CREDITO_TRABAJADOR',
                referencia_pago=f'BOLETA ELECTRONICA-41002{idx}',
                registrado_por=self.user, sucursal_cobro=self.tienda_uso,
            )
        return credito

    def test_fila_trae_solicitado_consumido_boleta_y_tienda_con_direccion(self):
        self._credito_con_uso('CR-1001', 200000, 150000, [110000])
        filas = _filas_pdf_creditos(CreditoTrabajador.objects.all())['filas']
        uso = [f for f in filas if f['tipo'] == 'uso']
        self.assertEqual(len(uso), 1)
        fila = uso[0]
        # Solicitado = lo APROBADO cuando existe (es el cupo real disponible).
        self.assertEqual(fila['solicitado'], Decimal('150000'))
        self.assertEqual(fila['consumido'], Decimal('110000'))
        self.assertEqual(fila['monto'], Decimal('110000'))
        self.assertEqual(fila['boleta'], 'BE-410021')
        self.assertEqual(fila['sucursal'], 'PAO4 — Calle Comercio 45')

    def test_solicitado_cae_al_monto_pedido_si_no_hay_aprobado(self):
        self._credito_con_uso('CR-1002', 90000, None, [10000])
        filas = _filas_pdf_creditos(CreditoTrabajador.objects.all())['filas']
        fila = [f for f in filas if f['tipo'] == 'uso'][0]
        self.assertEqual(fila['solicitado'], Decimal('90000'))

    def test_consumido_es_del_credito_completo_no_del_uso_suelto(self):
        """Con 2 usos, cada fila muestra su monto pero el consumido total."""
        self._credito_con_uso('CR-1003', 200000, 200000, [40000, 60000])
        filas = _filas_pdf_creditos(CreditoTrabajador.objects.all())['filas']
        usos = [f for f in filas if f['tipo'] == 'uso']
        self.assertEqual(len(usos), 2)
        self.assertEqual([f['monto'] for f in usos], [Decimal('40000'), Decimal('60000')])
        for f in usos:
            self.assertEqual(f['consumido'], Decimal('100000'))
            self.assertEqual(f['solicitado'], Decimal('200000'))


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class ExportarPdfCreditosTest(TestCase):
    """El PDF tiene que RENDERIZARSE: el layout pasó de 9 a 8 columnas."""

    def setUp(self):
        self.env = setup_entorno_completo()
        self.user = self.env['user']
        self.cliente = Cliente.objects.create(
            nombre='Juan', apellido='Perez', rut='11.111.111-1')
        self.user.rol = 'administrador'
        self.user.save(update_fields=['rol'])
        self.tienda_uso = crear_sucursal(empresa=self.env['empresa'], alias='PAO4')
        Sucursal.objects.filter(pk=self.tienda_uso.pk).update(
            direccion='Calle Comercio 45, Santiago Centro',
        )
        self.client = Client()
        self.client.login(username='testuser', password='TestPass123!')
        session = self.client.session
        session['idEmpresaActual'] = self.env['empresa'].id
        session['idSucursalActual'] = self.env['sucursal'].id
        session.save()

        credito = CreditoTrabajador.objects.create(
            numero_credito='CR-2001', beneficiario=self.cliente,
            empresa_origen=self.env['empresa'], sucursal=self.env['sucursal'],
            monto_solicitado=Decimal(200000), monto_aprobado=Decimal(150000),
            estado='ACTIVO', solicitado_por=self.user,
            fecha_vencimiento=timezone.localdate() + timedelta(days=30),
        )
        PagoCreditoTrabajador.objects.create(
            credito=credito, numero_pago='CR-2001-1', monto_pago=Decimal(110000),
            fecha_pago=timezone.localdate(), metodo_pago='CREDITO_TRABAJADOR',
            referencia_pago='BOLETA ELECTRONICA-410020',
            registrado_por=self.user, sucursal_cobro=self.tienda_uso,
        )
        # Un segundo crédito SIN uso: ejercita la fila 'sin_uso'.
        CreditoTrabajador.objects.create(
            numero_credito='CR-2002', beneficiario=self.cliente,
            empresa_origen=self.env['empresa'], sucursal=self.env['sucursal'],
            monto_solicitado=Decimal(50000), estado='ACTIVO',
            solicitado_por=self.user,
            fecha_vencimiento=timezone.localdate() + timedelta(days=30),
        )

    def test_pdf_se_genera_con_el_layout_de_8_columnas(self):
        resp = self.client.get('/app/api/creditos/exportar-pdf/', {'alcance': 'todas'})
        self.assertEqual(resp.status_code, 200, resp.content[:400])
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertTrue(resp.content.startswith(b'%PDF'))
        self.assertGreater(len(resp.content), 1500)

    def test_pdf_filtrado_por_tienda_de_uso(self):
        resp = self.client.get('/app/api/creditos/exportar-pdf/', {
            'alcance': 'todas',
            'sucursal_id': self.tienda_uso.id,
            'criterio_sucursal': CRITERIO_SUCURSAL_USADA,
        })
        self.assertEqual(resp.status_code, 200, resp.content[:400])
        self.assertTrue(resp.content.startswith(b'%PDF'))

    def test_endpoint_de_tiendas_para_el_filtro(self):
        resp = self.client.get('/app/api/creditos/sucursales-filtro/', {'alcance': 'todas'})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        por_alias = {s['alias']: s for s in data['sucursales']}
        self.assertIn('PAO4', por_alias)
        self.assertEqual(por_alias['PAO4']['usos_recibidos'], 1)
        self.assertEqual(por_alias['PAO4']['creditos_emitidos'], 0)
        self.assertIn('Calle Comercio 45', por_alias['PAO4']['label'])
