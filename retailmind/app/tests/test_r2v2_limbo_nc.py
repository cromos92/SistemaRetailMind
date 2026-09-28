"""
Unidad R2V2 (ronda 2) — Limbo del emisor y origen de las NC de traspaso.

6. Limbo (B7-01 p2 / B7-02 p2):
   - 'corregir' solo con faltantes en líneas corregibles
     (RECEPCIONADO_PARCIAL / FALTANTE / RECEPCIONADO_DANADO / EN_REGULARIZACION);
     una línea REGULARIZADO conserva cantidad_faltante pero ya se resolvió.
   - cada producto del resumen trae 'corregible'.
   - los RECEPCIONADO_COMPLETO con una NC hija que espera la devolución física
     aparecen (chip «NC esperando devolución», sin acciones).
7. _detectar_origen_nc y el comando corregir_estados_en_regularizacion_sin_accion
   reconocen las NC de regularización en los dos formatos de `referencias`
   (texto hasta el 19-may, JSON desde entonces).

Ejecutar (BD aislada):
    DATABASE_URL=postgres://postgres:admin@localhost:5432/retail_r2v2 \
    python manage.py test app.tests.test_r2v2_limbo_nc --keepdb
"""
import json
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from app.models import Dte, Productos_Recepcionados
from app.views import _detectar_origen_nc, _texto_referencias_nc
from .factories import crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario


def _refs_json(folio):
    return json.dumps([{'tipo_documento': 52, 'folio': folio, 'fecha': '2026-06-01', 'razon': '1'}])


class _BaseTraspaso(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa(nombre='Red R2V2', rut='76.430.000-3')
        cls.origen = crear_sucursal(empresa=cls.empresa, alias='R2V2-ORI')
        cls.destino = crear_sucursal(empresa=cls.empresa, alias='R2V2-DES')
        cls.maestro = crear_usuario(username='r2v2_limbo', rol='maestro')
        crear_empresa_user(cls.maestro, cls.empresa, cls.origen)

    def setUp(self):
        self.hoy = timezone.localdate()
        self.client.force_login(self.maestro)
        s = self.client.session
        s['idEmpresaActual'] = self.empresa.id
        s['idSucursalActual'] = self.origen.id
        s.save()

    def _traspaso(self, numero, estado='RECEPCIONADO_PARCIAL', **extra):
        datos = dict(
            emisor=self.empresa, receptor=self.empresa, numero_documento=numero, tipo_documento='GUIA',
            monto_con_iva=11900, monto_neto=10000, descuento=0, estado_pago='PENDIENTE', estado_dte=estado,
            responsable='test', fecha_emision=self.hoy - timedelta(days=3), fecha_vencimiento=self.hoy,
            diasCredito=0, bultos=1, unidades_productos=2, tipo_transaccion='TRASPASO', sucursal=self.origen,
        )
        datos.update(extra)
        return Dte.objects.create(**datos)

    def _nc(self, padre, numero, referencias='', tipo_transaccion='TRASPASO', **extra):
        datos = dict(
            emisor=self.empresa, receptor=self.empresa, numero_documento=numero,
            tipo_documento='NOTA DE CREDITO', monto_con_iva=5950, monto_neto=5000, descuento=0,
            estado_pago='PENDIENTE', estado_dte='EMITIDO', responsable='test', fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy, diasCredito=0, bultos=1, unidades_productos=1,
            tipo_transaccion=tipo_transaccion, sucursal=self.origen, es_nota_credito=True,
            documento_afectado=padre, referencias=referencias,
        )
        datos.update(extra)
        return Dte.objects.create(**datos)

    def _linea(self, dte, estado, faltante=2, esperada=2, arribado=0):
        return Productos_Recepcionados.objects.create(
            dte=dte, stockArribado=arribado, cantidad_esperada=esperada, cantidad_faltante=faltante,
            estado=estado,
        )


# ---------------------------------------------------------------------------
# 6. Limbo
# ---------------------------------------------------------------------------
class LimboCorregirTest(_BaseTraspaso):
    def _lista(self):
        r = self.client.get('/app/dte/obtener_limbo_emisor/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content)
        return {it['numero_documento']: it for it in r.json()['items']}

    def _resumen(self, dte):
        r = self.client.get(f'/app/dte/limbo_resumen/{dte.id}/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_lineas_regularizadas_no_ofrecen_corregir(self):
        """B7-01: el DTE 16933 (7 líneas REGULARIZADO con faltante) ofrecía
        'corregir' y el POST volvía a acreditar en destino lo ya devuelto."""
        solo_regularizadas = self._traspaso(30001)
        self._linea(solo_regularizadas, 'REGULARIZADO', faltante=3)
        self._linea(solo_regularizadas, 'RECEPCIONADO_OK', faltante=0, arribado=2)
        mixto = self._traspaso(30002)
        self._linea(mixto, 'REGULARIZADO', faltante=1)
        abierta = self._linea(mixto, 'FALTANTE', faltante=2)

        items = self._lista()
        self.assertNotIn('corregir', items[30001]['acciones_permitidas'])
        self.assertEqual(items[30001]['acciones_permitidas'], ['nc_con_devolucion', 'nc_sin_devolucion'])
        self.assertEqual(items[30001]['faltantes_corregibles'], 0)
        # El resumen de problemas sigue contando todas las líneas (histórico)
        self.assertEqual(items[30001]['resumen_problemas']['faltantes'], 3)
        self.assertIn('corregir', items[30002]['acciones_permitidas'])
        self.assertEqual(items[30002]['faltantes_corregibles'], 2)

        r1 = self._resumen(solo_regularizadas)
        self.assertNotIn('corregir', r1['acciones_permitidas'])
        self.assertTrue(all(p['corregible'] is False for p in r1['productos']))

        r2 = self._resumen(mixto)
        self.assertIn('corregir', r2['acciones_permitidas'])
        corregibles = [p['recepcion_id'] for p in r2['productos'] if p['corregible']]
        self.assertEqual(corregibles, [abierta.id])

    def test_estados_corregibles(self):
        for i, estado in enumerate(('RECEPCIONADO_PARCIAL', 'FALTANTE', 'RECEPCIONADO_DANADO',
                                    'EN_REGULARIZACION')):
            with self.subTest(estado=estado):
                dte = self._traspaso(30100 + i)
                self._linea(dte, estado, faltante=1)
                self.assertTrue(self._resumen(dte)['productos'][0]['corregible'])
        for i, estado in enumerate(('REGULARIZADO', 'RECEPCIONADO_OK', 'RECEPCIONADO_SOBRANTE')):
            with self.subTest(estado=estado):
                dte = self._traspaso(30200 + i)
                self._linea(dte, estado, faltante=1)
                self.assertFalse(self._resumen(dte)['productos'][0]['corregible'])

    def test_fallback_sin_recepciones_no_es_corregible(self):
        from app.models import Dte_Productos
        emitido = self._traspaso(30300, estado='EMITIDO', fecha_emision=self.hoy - timedelta(days=20))
        Dte_Productos.objects.create(dte=emitido, descripcion='x', precio=1000, stock=2)
        r = self._resumen(emitido)
        self.assertEqual(r['acciones_permitidas'], ['nc_con_devolucion'])
        self.assertEqual([p['corregible'] for p in r['productos']], [False])

    def test_completo_con_nc_esperando_devolucion_aparece_sin_acciones(self):
        """B7-02: la NC post-recepción con devolución física sobre un DTE
        COMPLETO quedaba invisible para el emisor."""
        con_pendiente = self._traspaso(30401, estado='RECEPCIONADO_COMPLETO')
        self._nc(con_pendiente, 94001, requiere_devolucion_fisica=True)
        confirmada = self._traspaso(30402, estado='RECEPCIONADO_COMPLETO')
        self._nc(confirmada, 94002, requiere_devolucion_fisica=True,
                 fecha_confirmacion_devolucion=timezone.now())
        anulada = self._traspaso(30403, estado='RECEPCIONADO_COMPLETO')
        self._nc(anulada, 94003, requiere_devolucion_fisica=True, estado_dte='ANULADO')
        sin_nc = self._traspaso(30404, estado='RECEPCIONADO_COMPLETO')

        items = self._lista()
        self.assertIn(30401, items)
        self.assertEqual(items[30401]['ncs_hijas_pendientes_devolucion'], 1)
        self.assertEqual(items[30401]['acciones_permitidas'], [])
        for numero in (30402, 30403, 30404):
            self.assertNotIn(numero, items)
        # La NC hija no aparece como fila propia
        self.assertNotIn(94001, items)

        r = self._resumen(con_pendiente)
        self.assertEqual(r['acciones_permitidas'], [])
        self.assertEqual(r['dte']['ncs_hijas_pendientes_devolucion'], 1)
        self.assertIsNotNone(sin_nc.id)

    def test_filtro_por_estado_completo(self):
        dte = self._traspaso(30501, estado='RECEPCIONADO_COMPLETO')
        self._nc(dte, 94501, requiere_devolucion_fisica=True)
        self._traspaso(30502)
        r = self.client.get('/app/dte/obtener_limbo_emisor/?estado=RECEPCIONADO_COMPLETO',
                            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual([it['numero_documento'] for it in r.json()['items']], [30501])

    def test_otra_sucursal_no_ve_el_resumen(self):
        ajeno = self._traspaso(30601, sucursal=self.destino)
        r = self.client.get(f'/app/dte/limbo_resumen/{ajeno.id}/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403)


# ---------------------------------------------------------------------------
# 7. Origen de la NC en los dos formatos de referencias
# ---------------------------------------------------------------------------
class OrigenNcTest(_BaseTraspaso):
    def test_texto_de_referencias_en_ambos_formatos(self):
        self.assertEqual(_texto_referencias_nc('NC por regularización DTE #5'), 'NC por regularización DTE #5')
        self.assertIn('33', _texto_referencias_nc(_refs_json(33)))
        # JSON con texto escapado (ó) se decodifica
        escapado = json.dumps([{'razon': 'NC por regularización'}])
        self.assertIn('\\u00f3', escapado)
        self.assertIn('regularización', _texto_referencias_nc(escapado))
        self.assertEqual(_texto_referencias_nc('[no es json'), '[no es json')
        self.assertEqual(_texto_referencias_nc(None), '')

    def test_detecta_regularizacion_en_texto_y_json(self):
        padre = self._traspaso(31001)
        nc_texto = self._nc(padre, 95001, referencias='NC por regularización DTE #31001. Faltó talla 40')
        nc_json = self._nc(padre, 95002, referencias=_refs_json(31001))
        nc_anulacion = self._nc(padre, 95003, referencias=_refs_json(31001), tipo_transaccion='ANULACION')
        nc_ajuste = self._nc(padre, 95004, referencias='Ajuste emisor (post) sobre DTE #31001',
                             tipo_transaccion='AJUSTE')

        def meta(nc, con_tipo=False):
            m = {'id': nc.id, 'referencias': nc.referencias}
            if con_tipo:
                m['tipo_transaccion'] = nc.tipo_transaccion
            return m

        self.assertEqual(_detectar_origen_nc(meta(nc_texto)), 'regularizacion')
        self.assertEqual(_detectar_origen_nc(meta(nc_json)), 'regularizacion')
        self.assertEqual(_detectar_origen_nc(meta(nc_anulacion)), 'gestion_dte')
        self.assertEqual(_detectar_origen_nc(meta(nc_ajuste)), 'gestion_dte')
        # Sin id ni tipo: solo decide el texto
        self.assertEqual(_detectar_origen_nc({'referencias': _refs_json(1)}), 'gestion_dte')

    def test_tipo_transaccion_se_lee_una_vez_por_nc(self):
        padre = self._traspaso(31101)
        nc = self._nc(padre, 95101, referencias=_refs_json(31101))
        m = {'id': nc.id, 'referencias': nc.referencias}
        with self.assertNumQueries(1):
            self.assertEqual(_detectar_origen_nc(m), 'regularizacion')
        with self.assertNumQueries(0):
            self.assertEqual(_detectar_origen_nc(m), 'regularizacion')
        self.assertEqual(m['tipo_transaccion'], 'TRASPASO')

    def test_comando_protege_lineas_con_nc_de_regularizacion_json(self):
        con_json = self._traspaso(31201)
        linea_json = self._linea(con_json, 'EN_REGULARIZACION', faltante=2)
        self._nc(con_json, 95201, referencias=_refs_json(31201))

        con_texto = self._traspaso(31202)
        linea_texto = self._linea(con_texto, 'EN_REGULARIZACION', faltante=2)
        self._nc(con_texto, 95202, referencias='NC por regularización DTE #31202')

        solo_anulacion = self._traspaso(31203)
        linea_anul = self._linea(solo_anulacion, 'EN_REGULARIZACION', faltante=2)
        self._nc(solo_anulacion, 95203, referencias=_refs_json(31203), tipo_transaccion='ANULACION')

        sin_nc = self._traspaso(31204)
        linea_sin = self._linea(sin_nc, 'EN_REGULARIZACION', faltante=2)

        salida = StringIO()
        call_command('corregir_estados_en_regularizacion_sin_accion', stdout=salida)  # dry-run
        self.assertIn('protegidos, NO se tocan): 2', salida.getvalue())
        linea_sin.refresh_from_db()
        self.assertEqual(linea_sin.estado, 'EN_REGULARIZACION')

        call_command('corregir_estados_en_regularizacion_sin_accion', '--apply', stdout=StringIO())
        for linea, esperado in ((linea_json, 'EN_REGULARIZACION'), (linea_texto, 'EN_REGULARIZACION'),
                                (linea_anul, 'FALTANTE'), (linea_sin, 'FALTANTE')):
            linea.refresh_from_db()
            self.assertEqual(linea.estado, esperado, linea.dte.numero_documento)
