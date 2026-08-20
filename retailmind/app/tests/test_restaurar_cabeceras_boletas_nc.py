# -*- coding: utf-8 -*-
"""
Tests del command restaurar_cabeceras_boletas_nc (backfill P0-5: cabeceras de
boletas rotas por anular_factura_dte / NC por línea).

Correr:
    python manage.py test app.tests.test_restaurar_cabeceras_boletas_nc --settings=test_settings_sqlite
"""
import csv
import io
import os
import re
import tempfile
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.models import Dte, Dte_Detalle_Pago, Dte_Productos
from .factories import setup_entorno_completo

RE_ESCRITURA = re.compile(r'^\s*(INSERT|UPDATE|DELETE)\b', re.IGNORECASE)


class RestaurarCabecerasBase(TestCase):

    def setUp(self):
        self.env = setup_entorno_completo()
        self.tmpdir = tempfile.mkdtemp()
        self._folio = 90000

    # ------------------------- fixtures ------------------------------- #

    def _dte(self, cab, neto, unidades, tipo='BOLETA ELECTRONICA', **kw):
        self._folio += 1
        defaults = dict(
            emisor=self.env['empresa'],
            receptor=None,
            numero_documento=self._folio,
            tipo_documento=tipo,
            monto_con_iva=cab,
            monto_neto=neto,
            descuento=0,
            estado_pago='PAGADO',
            estado_dte='EMITIDO',
            responsable='test',
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            diasCredito=0,
            bultos=0,
            unidades_productos=unidades,
            tipo_transaccion='VENTA_PUBLICO',
            sucursal=self.env['sucursal'],
            es_nota_credito=False,
            hora=timezone.localtime().time(),
        )
        defaults.update(kw)
        return Dte.objects.create(**defaults)

    def _linea(self, dte, precio, stock, activo, monto_item, descuento_monto=None):
        return Dte_Productos.objects.create(
            dte=dte, productoTalla=self.env['producto_talla'],
            descripcion='linea test', costo=0, sobreprecio=0,
            precio=precio, stock=stock, activo=activo,
            monto_item=monto_item, descuento_monto=descuento_monto,
        )

    def _pago(self, dte, monto, metodo='EFECTIVO'):
        return Dte_Detalle_Pago.objects.create(dte=dte, metodo_pago=metodo, monto=monto)

    def _nc(self, afectado, redujo=True, estado='EMITIDO', descartado=False,
            lineas=None, monto=0):
        self._folio += 1
        nc = self._dte(
            cab=monto, neto=0, unidades=0, tipo='NOTA DE CREDITO',
            es_nota_credito=True, documento_afectado=afectado,
            redujo_lineas_documento=redujo, estado_dte=estado,
            descartado=descartado, tipo_transaccion='VENTA_PUBLICO',
        )
        for stock, precio in (lineas or []):
            Dte_Productos.objects.create(
                dte=nc, productoTalla=self.env['producto_talla'],
                descripcion='linea NC', costo=0, sobreprecio=0,
                precio=precio, stock=stock, activo=True, monto_item=stock * precio,
            )
        return nc

    def _boleta_total(self):
        """Devolución TOTAL estilo prod: cabecera $0, todas las líneas inactivas,
        monto_item intacto, pago real $199.950, NC por línea que acreditó 3 uds."""
        b = self._dte(cab=0, neto=0, unidades=0)
        self._linea(b, precio=49990, stock=0, activo=False, monto_item=99980)
        self._linea(b, precio=99970, stock=0, activo=False, monto_item=99970)
        self._pago(b, 199950)
        nc = self._nc(b, lineas=[(2, 49990), (1, 99970)], monto=199950)
        return b, nc

    def _boleta_parcial(self):
        """Devolución PARCIAL estilo prod: quedó 1 línea activa con 2 uds y la
        cabecera reescrita como Σ(activas)×1.19; pago real $30.000."""
        b = self._dte(cab=23800, neto=20000, unidades=2)
        self._linea(b, precio=10000, stock=2, activo=True, monto_item=30000)
        self._pago(b, 30000)
        nc = self._nc(b, lineas=[(1, 10000)], monto=11900)
        return b, nc

    # ------------------------- helpers -------------------------------- #

    def _run(self, *args, aplicar=False):
        out = io.StringIO()
        argv = ['restaurar_cabeceras_boletas_nc', '--snapshot-dir', self.tmpdir]
        if aplicar:
            argv.append('--aplicar')
        argv.extend(args)
        call_command(*argv, stdout=out)
        return out.getvalue()

    def _snapshot_path(self):
        archivos = [f for f in os.listdir(self.tmpdir)
                    if f.startswith('_restauracion_cabeceras_')
                    and not f.endswith('_preview.csv')]
        self.assertEqual(len(archivos), 1, f'snapshots encontrados: {archivos}')
        return os.path.join(self.tmpdir, archivos[0])

    def _leer_csv(self, ruta):
        with open(ruta, encoding='utf-8', newline='') as f:
            return list(csv.DictReader(f))


class RestaurarCabecerasTest(RestaurarCabecerasBase):

    # ------------------- selección y montos --------------------------- #

    def test_caso_total_restaura_cabecera_unidades_y_neto(self):
        b, _nc = self._boleta_total()
        salida = self._run(aplicar=True)

        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('199950'))
        # 199950 / 1.19 = 168025.21... → HALF-UP 168025
        self.assertEqual(b.monto_neto, Decimal('168025'))
        self.assertEqual(b.unidades_productos, 3)
        self.assertIn('1 candidatos', salida)
        # Las líneas NO se tocan (tope anti-doble-NC)
        lineas = list(Dte_Productos.objects.filter(dte=b).values_list('stock', 'activo'))
        self.assertEqual(sorted(lineas), [(0, False), (0, False)])

    def test_caso_parcial_restaura_desde_monto_item(self):
        b, _nc = self._boleta_parcial()
        self._run(aplicar=True)

        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('30000'))
        # 30000 / 1.19 = 25210.08... → 25210
        self.assertEqual(b.monto_neto, Decimal('25210'))
        # 2 uds vigentes + 1 acreditada por la NC
        self.assertEqual(b.unidades_productos, 3)
        # La línea sigue con stock 2 / activa
        linea = Dte_Productos.objects.get(dte=b)
        self.assertEqual((linea.stock, linea.activo), (2, True))

    def test_redondeo_half_up_del_neto(self):
        """45998 / 1.19 = 38653.78... → HALF-UP 38654 (int()/truncación daría 38653)."""
        b = self._dte(cab=0, neto=0, unidades=0)
        self._linea(b, precio=45998, stock=0, activo=False, monto_item=45998)
        self._pago(b, 45998)
        self._nc(b, lineas=[(1, 45998)], monto=45998)

        self._run(aplicar=True)
        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('45998'))
        self.assertEqual(b.monto_neto, Decimal('38654'))

    # ------------------- REVISION_MANUAL ------------------------------ #

    def test_outlier_sin_nc_va_a_revision_manual_y_no_se_toca(self):
        sano, _ = self._boleta_total()
        outlier = self._dte(cab=0, neto=0, unidades=0)
        self._linea(outlier, precio=10000, stock=0, activo=False, monto_item=10000)
        self._pago(outlier, 10000)  # sin NC vinculada

        salida = self._run(aplicar=True)

        self.assertIn('SIN_NC_VINCULADA', salida)
        self.assertIn(f'dte={outlier.id}', salida)
        outlier.refresh_from_db()
        self.assertEqual(outlier.monto_con_iva, Decimal('0'))  # intacto
        sano.refresh_from_db()
        self.assertEqual(sano.monto_con_iva, Decimal('199950'))  # el válido sí
        # El CSV solo contiene el candidato válido
        filas = self._leer_csv(self._snapshot_path())
        self.assertEqual([int(f['dte_id']) for f in filas], [sano.id])

    def test_nc_rechazada_no_habilita(self):
        """La NC vinculada debe estar en EMITIDO/ACEPTADO para calificar."""
        b = self._dte(cab=0, neto=0, unidades=0)
        self._linea(b, precio=10000, stock=0, activo=False, monto_item=10000)
        self._pago(b, 10000)
        self._nc(b, estado='RECHAZADO', lineas=[(1, 10000)])

        salida = self._run(aplicar=True)
        self.assertIn('SIN_NC_VINCULADA', salida)
        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('0'))

    def test_fuente_inconsistente_va_a_revision_manual(self):
        b = self._dte(cab=0, neto=0, unidades=0)
        self._linea(b, precio=10000, stock=0, activo=False, monto_item=40000)
        self._pago(b, 50000)  # Σ monto_item (40.000) != Σ pagos (50.000)
        self._nc(b, lineas=[(4, 10000)])

        salida = self._run(aplicar=True)
        self.assertIn('FUENTE_INCONSISTENTE', salida)
        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('0'))
        # No hubo candidatos → no hay snapshot
        self.assertEqual([f for f in os.listdir(self.tmpdir)
                          if not f.endswith('_preview.csv')], [])

    def test_unidades_no_reconstruibles_corrige_solo_el_monto(self):
        """NC por monto (sin líneas) + línea con precio 0: las unidades no se
        pueden reconstruir → se corrige el monto y unidades queda SIN tocar."""
        b = self._dte(cab=0, neto=0, unidades=4)
        self._linea(b, precio=0, stock=0, activo=False, monto_item=15000)
        self._pago(b, 15000)
        self._nc(b, lineas=None, monto=15000)  # NC redujo=True pero sin líneas

        salida = self._run(aplicar=True)
        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('15000'))
        self.assertEqual(b.monto_neto, Decimal('12605'))  # 15000/1.19=12605.04→12605
        self.assertEqual(b.unidades_productos, 4)  # SIN tocar
        self.assertIn('NO reconstruibles', salida)
        fila = self._leer_csv(self._snapshot_path())[0]
        self.assertEqual(fila['unidades_tocadas'], '0')

    # ------------------- filtros -------------------------------------- #

    def test_factura_solo_entra_con_tipo(self):
        fac = self._dte(cab=0, neto=0, unidades=0, tipo='FACTURA ELECTRONICA',
                        tipo_transaccion='VENTA')
        self._linea(fac, precio=20000, stock=0, activo=False, monto_item=20000)
        self._pago(fac, 20000)
        self._nc(fac, lineas=[(1, 20000)], monto=20000)

        self._run(aplicar=True)  # default: solo boletas
        fac.refresh_from_db()
        self.assertEqual(fac.monto_con_iva, Decimal('0'))

        self._run('--tipo', 'todos', aplicar=True)
        fac.refresh_from_db()
        self.assertEqual(fac.monto_con_iva, Decimal('20000'))

    def test_filtro_ids_acota(self):
        b1, _ = self._boleta_total()
        b2, _ = self._boleta_parcial()
        self._run('--ids', str(b1.id), aplicar=True)
        b1.refresh_from_db(); b2.refresh_from_db()
        self.assertEqual(b1.monto_con_iva, Decimal('199950'))
        self.assertEqual(b2.monto_con_iva, Decimal('23800'))  # fuera del filtro

    # ------------------- dry-run == 0 escrituras ---------------------- #

    def test_dry_run_cero_escrituras_en_bd(self):
        b, _ = self._boleta_total()
        with CaptureQueriesContext(connection) as ctx:
            salida = self._run()  # sin --aplicar
        escrituras = [q['sql'] for q in ctx.captured_queries
                      if RE_ESCRITURA.match(q['sql'] or '')]
        self.assertEqual(escrituras, [], f'dry-run escribió en BD: {escrituras}')

        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('0'))
        self.assertIn('DRY-RUN', salida)
        self.assertIn('1 candidatos', salida)
        # Solo dejó el CSV de preview, nunca el snapshot real
        archivos = os.listdir(self.tmpdir)
        self.assertTrue(all(f.endswith('_preview.csv') for f in archivos), archivos)

    # ------------------- aplicar / idempotencia ----------------------- #

    def test_aplicar_es_idempotente(self):
        b1, _ = self._boleta_total()
        b2, _ = self._boleta_parcial()
        salida1 = self._run(aplicar=True)
        self.assertIn('2 candidatos', salida1)
        self.assertIn('OK: 0 siguen con déficit', salida1)

        salida2 = self._run(aplicar=True)
        self.assertIn('0 candidatos', salida2)
        self.assertIn('Nada que aplicar', salida2)
        b1.refresh_from_db(); b2.refresh_from_db()
        self.assertEqual(b1.monto_con_iva, Decimal('199950'))
        self.assertEqual(b2.monto_con_iva, Decimal('30000'))
        # Un solo snapshot (la 2ª corrida no escribe otro)
        self._snapshot_path()

    # ------------------- reversión ------------------------------------ #

    def test_revertir_restaura_valores_previos(self):
        b1, _ = self._boleta_total()
        b2, _ = self._boleta_parcial()
        self._run(aplicar=True)
        ruta = self._snapshot_path()

        call_command('restaurar_cabeceras_boletas_nc', '--revertir', ruta,
                     stdout=io.StringIO())

        b1.refresh_from_db(); b2.refresh_from_db()
        self.assertEqual(b1.monto_con_iva, Decimal('0'))
        self.assertEqual(b1.monto_neto, Decimal('0'))
        self.assertEqual(b1.unidades_productos, 0)
        self.assertEqual(b2.monto_con_iva, Decimal('23800'))
        self.assertEqual(b2.monto_neto, Decimal('20000'))
        self.assertEqual(b2.unidades_productos, 2)

    def test_revertir_se_niega_si_el_valor_cambio_despues(self):
        b1, _ = self._boleta_total()
        b2, _ = self._boleta_parcial()
        self._run(aplicar=True)
        ruta = self._snapshot_path()

        # Alguien tocó la boleta después de la restauración
        Dte.objects.filter(pk=b1.pk).update(monto_con_iva=123456)

        with self.assertRaises(CommandError):
            call_command('restaurar_cabeceras_boletas_nc', '--revertir', ruta,
                         stdout=io.StringIO())
        # Nada se revirtió (transacción completa abortada)
        b1.refresh_from_db(); b2.refresh_from_db()
        self.assertEqual(b1.monto_con_iva, Decimal('123456'))
        self.assertEqual(b2.monto_con_iva, Decimal('30000'))

        # --forzar pisa el valor actual y restaura igual
        call_command('restaurar_cabeceras_boletas_nc', '--revertir', ruta,
                     '--forzar', stdout=io.StringIO())
        b1.refresh_from_db(); b2.refresh_from_db()
        self.assertEqual(b1.monto_con_iva, Decimal('0'))
        self.assertEqual(b2.monto_con_iva, Decimal('23800'))

    def test_revertir_rechaza_csv_preview(self):
        self._boleta_total()
        self._run()  # dry-run deja el preview
        preview = [f for f in os.listdir(self.tmpdir) if f.endswith('_preview.csv')]
        self.assertEqual(len(preview), 1)
        with self.assertRaises(CommandError):
            call_command('restaurar_cabeceras_boletas_nc', '--revertir',
                         os.path.join(self.tmpdir, preview[0]),
                         stdout=io.StringIO())

    # ------------------- snapshot ------------------------------------- #

    def test_snapshot_registra_old_y_new(self):
        b, nc = self._boleta_total()
        self._run(aplicar=True)
        fila = self._leer_csv(self._snapshot_path())[0]
        self.assertEqual(int(fila['dte_id']), b.id)
        self.assertEqual(Decimal(fila['old_con_iva']), Decimal('0'))
        self.assertEqual(Decimal(fila['new_con_iva']), Decimal('199950'))
        self.assertEqual(Decimal(fila['new_neto']), Decimal('168025'))
        self.assertEqual(fila['new_unidades'], '3')
        self.assertEqual(fila['unidades_tocadas'], '1')
        self.assertEqual(fila['fuente'], 'SUM_MONTO_ITEM')
        self.assertEqual(fila['ncs_vinculadas'], str(nc.id))
        self.assertEqual(fila['deficit'], '199950')
