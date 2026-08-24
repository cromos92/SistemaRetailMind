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

from app.models import Dte, Dte_Detalle_Pago, Dte_Productos, Ticket
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

    def _ticket(self, dte, correlativo, total):
        return Ticket.objects.create(
            vendedor=self.env['vendedor'], sucursal=self.env['sucursal'],
            correlativo=correlativo, estado='PAGADO', subTotal=total,
            descuento=0, total=total, responsable='caja1',
            modulo_origen='VENTA_PUBLICO', dte_generado=True,
            folio_dte=dte.numero_documento,
        )

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

    def test_unidades_derivadas_de_monto_item_cuando_la_nc_no_las_lleva(self):
        """Patrón dominante en prod (171/228): la NC hija no lleva las unidades
        en sus líneas → se derivan de monto_item/precio (misma fuente que los
        montos)."""
        b = self._dte(cab=0, neto=0, unidades=0)
        self._linea(b, precio=25990, stock=0, activo=False, monto_item=51980)  # 2 uds
        self._pago(b, 51980)
        # NC "por monto": única línea sin unidades reales
        self._nc(b, lineas=None, monto=51980)

        self._run(aplicar=True)
        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('51980'))
        self.assertEqual(b.unidades_productos, 2)  # 51980 / 25990
        fila = self._leer_csv(self._snapshot_path())[0]
        self.assertEqual(fila['unidades_tocadas'], '1')
        self.assertIn('derivado de monto_item/precio', fila['nota_unidades'])

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


class RestaurarCabecerasViaManualTest(RestaurarCabecerasBase):
    """
    Vía manual explícita `--ids <id> --fuente {monto_item,pagos,ticket}`: la
    única forma de cerrar los documentos que el análisis automático manda a
    REVISION_MANUAL. Fixtures calcados de los 8 casos reales de prod.
    """

    # --------------------------- fixtures ----------------------------- #

    def _legacy_internet(self):
        """dte 836011: boleta legacy migrada. Cabecera pre-NC = pagos = $81.980
        (probado por el motivo_nc "NC parcial ($70.490 de $81.980)"), pero sus
        líneas migradas solo suman $70.490 → el automático la rechaza por
        FUENTE_INCONSISTENTE y la fuente correcta es PAGOS."""
        b = self._dte(cab=0, neto=0, unidades=0)
        self._linea(b, precio=69990, stock=0, activo=False, monto_item=69990)
        self._linea(b, precio=500, stock=0, activo=False, monto_item=500)
        self._pago(b, 81980, metodo='VENTA_INTERNET')
        nc = self._nc(b, lineas=[(1, 69990), (1, 500)], monto=70490)
        return b, nc

    def _nc_anulada_con_ticket(self):
        """dte 2190216: la NC se anuló/descartó DESPUÉS de romper la cabecera.
        Σmonto_item = pagos = ticket = $46.990 → SIN_NC_VINCULADA en el
        automático; con --fuente monto_item cierra y las unidades se
        reconstruyen (las líneas corroboran)."""
        b = self._dte(cab=0, neto=0, unidades=0)
        self._linea(b, precio=46990, stock=0, activo=False, monto_item=46990)
        self._pago(b, 46990, metodo='VENTA_INTERNET')
        self._nc(b, estado='ANULADO', descartado=True,
                 lineas=[(1, 46990)], monto=46990)
        tk = self._ticket(b, correlativo=182886, total=46990)
        return b, tk

    def _marketplace_paga_menos(self):
        """dte 2186646 (WALMART): documento de $36.118 (Σmonto_item == ticket)
        liquidado por el marketplace en $35.991 (−$127). El automático lo
        rechaza por FUENTE_INCONSISTENTE; la fuente correcta es el ticket."""
        b = self._dte(cab=0, neto=0, unidades=0)
        self._linea(b, precio=33605, stock=0, activo=False, monto_item=33605)
        self._linea(b, precio=2513, stock=0, activo=False, monto_item=2513)
        self._pago(b, 35991, metodo='VENTA_INTERNET')
        self._nc(b, lineas=[(1, 33605), (1, 2513)], monto=36118)
        tk = self._ticket(b, correlativo=135095, total=36118)
        return b, tk

    def _con_descuento_global(self):
        """dte 2175143: Σmonto_item $49.980 − descuento global $3.000 = $46.980
        = pagos. Las líneas corroboran el monto elegido vía el descuento, así
        que las unidades SÍ se reconstruyen (y NO hace falta
        --acepto-discrepancia: el descuento global reconcilia las fuentes)."""
        b = self._dte(cab=29738, neto=24990, unidades=1, descuento=3000)
        self._linea(b, precio=24990, stock=0, activo=False, monto_item=24990)
        self._linea(b, precio=24990, stock=1, activo=True, monto_item=24990)
        self._pago(b, 46980, metodo='VENTA_INTERNET')
        self._nc(b, lineas=[(1, 24990)], monto=24990)
        return b

    def _ticket_stale(self):
        """dte 2178247: la cabecera ($173.950) COINCIDE hoy con su ticket
        ($173.950) porque ambas salen del mismo `ticket.total` desincronizado,
        pero las 6 líneas y el pago único de Transbank dicen $188.940. Mover la
        cabecera CREA un descuadre DTE↔ticket de $14.990 (el command no toca
        Ticket): ese es el efecto colateral que hay que avisar."""
        b = self._dte(cab=173950, neto=146176, unidades=6)
        self._linea(b, precio=34790, stock=5, activo=True, monto_item=173950)
        self._linea(b, precio=14990, stock=1, activo=True, monto_item=14990)
        self._pago(b, 188940, metodo='TBK_DEBITO_POS')
        tk = self._ticket(b, correlativo=113441, total=173950)
        return b, tk

    # ------------------------ compuerta --ids ------------------------- #

    def test_fuente_sin_ids_es_error(self):
        self._legacy_internet()
        with self.assertRaises(CommandError) as ctx:
            self._run('--fuente', 'pagos')
        self.assertIn('--ids', str(ctx.exception))
        # tampoco con --aplicar
        with self.assertRaises(CommandError):
            self._run('--fuente', 'pagos', aplicar=True)

    def test_sin_fuente_el_documento_sigue_yendo_a_revision_manual(self):
        b, _nc = self._legacy_internet()
        salida = self._run('--ids', str(b.id), aplicar=True)
        self.assertIn('FUENTE_INCONSISTENTE', salida)
        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('0'))

    # --------------------------- fuentes ------------------------------ #

    def test_fuente_pagos_respeta_la_fuente_elegida(self):
        b, _nc = self._legacy_internet()
        salida = self._run('--ids', str(b.id), '--fuente', 'pagos',
                           '--acepto-discrepancia', aplicar=True)

        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('81980'))    # pagos, NO Σmonto_item
        self.assertEqual(b.monto_neto, Decimal('68891'))       # 81980/1.19 HALF-UP
        self.assertEqual(b.unidades_productos, 0)              # sin corroboración: SIN tocar
        self.assertIn('VÍA MANUAL', salida)
        self.assertIn('OK: 0 siguen con déficit', salida)
        # Las líneas del documento NO se tocan jamás
        lineas = sorted(Dte_Productos.objects.filter(dte=b)
                        .values_list('monto_item', 'stock', 'activo'))
        self.assertEqual(lineas, [(500, 0, False), (69990, 0, False)])
        fila = self._leer_csv(self._snapshot_path())[0]
        self.assertEqual(fila['fuente'], 'MANUAL_PAGOS')
        self.assertEqual(fila['unidades_tocadas'], '0')
        self.assertIn('no corroboran', fila['nota_unidades'])
        self.assertIn('MANUAL[', fila['cross_check'])

    def test_fuente_monto_item_cierra_un_sin_nc_vinculada(self):
        b, _tk = self._nc_anulada_con_ticket()
        salida = self._run('--ids', str(b.id), '--fuente', 'monto_item', aplicar=True)

        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('46990'))
        self.assertEqual(b.monto_neto, Decimal('39487'))       # 46990/1.19 → 39487
        self.assertEqual(b.unidades_productos, 1)              # líneas corroboran
        self.assertIn('OK: 0 siguen con déficit', salida)
        fila = self._leer_csv(self._snapshot_path())[0]
        self.assertEqual(fila['fuente'], 'MANUAL_SUM_MONTO_ITEM')
        self.assertEqual(fila['unidades_tocadas'], '1')
        self.assertIn('corroboran', fila['nota_unidades'])

    def test_fuente_ticket_usa_el_total_del_ticket(self):
        b, _tk = self._marketplace_paga_menos()
        salida = self._run('--ids', str(b.id), '--fuente', 'ticket',
                           '--acepto-discrepancia', aplicar=True)

        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('36118'))    # ticket, no los $35.991
        self.assertEqual(b.monto_neto, Decimal('30351'))
        self.assertEqual(b.unidades_productos, 2)
        self.assertIn('OK: 0 siguen con déficit', salida)
        fila = self._leer_csv(self._snapshot_path())[0]
        self.assertEqual(fila['fuente'], 'MANUAL_TICKET')
        self.assertIn('ticket $36,118', fila['cross_check'])

    def test_fuente_ticket_sin_ticket_resoluble_no_escribe(self):
        b, _nc = self._legacy_internet()          # no tiene ticket
        salida = self._run('--ids', str(b.id), '--fuente', 'ticket', aplicar=True)
        self.assertIn('SIN_TICKET_RESOLUBLE', salida)
        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('0'))

    def test_descuento_global_corrobora_y_reconstruye_unidades(self):
        b = self._con_descuento_global()
        self._run('--ids', str(b.id), '--fuente', 'pagos', aplicar=True)

        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('46980'))
        self.assertEqual(b.monto_neto, Decimal('39479'))       # 46980/1.19 → 39479
        self.assertEqual(b.unidades_productos, 2)              # 1 devuelta + 1 vigente
        self.assertEqual(b.descuento, Decimal('3000'))         # intacto
        fila = self._leer_csv(self._snapshot_path())[0]
        self.assertIn('descuento global', fila['nota_unidades'])

    # ----------------------- guardas de integridad -------------------- #

    def test_fuente_no_baja_una_cabecera(self):
        """monto_item ($70.490) < cabecera pedida por el operador… si el valor
        elegido no mejora la cabecera actual, el documento NO se toca."""
        b, _nc = self._legacy_internet()
        Dte.objects.filter(pk=b.pk).update(monto_con_iva=Decimal('70490'),
                                           monto_neto=Decimal('59235'))
        salida = self._run('--ids', str(b.id), '--fuente', 'monto_item',
                           '--acepto-discrepancia', aplicar=True)
        self.assertIn('FUENTE_NO_MEJORA', salida)
        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('70490'))

    def test_fuente_solo_toca_los_ids_pedidos(self):
        b1, _ = self._legacy_internet()
        b2, _ = self._marketplace_paga_menos()
        self._run('--ids', str(b1.id), '--fuente', 'pagos',
                  '--acepto-discrepancia', aplicar=True)
        b1.refresh_from_db(); b2.refresh_from_db()
        self.assertEqual(b1.monto_con_iva, Decimal('81980'))
        self.assertEqual(b2.monto_con_iva, Decimal('0'))       # intacto

    def test_ids_fuera_de_alcance_se_reportan(self):
        sano, _ = self._boleta_total()
        self._run(aplicar=True)                                # ya restaurado
        salida = self._run('--ids', f'{sano.id},999999', '--fuente', 'pagos')
        self.assertIn('FUERA de alcance', salida)
        self.assertIn('dte=999999: no existe', salida)
        self.assertIn('sin déficit', salida)

    def test_ids_fuera_de_alcance_cubre_las_6_ramas(self):
        """`_reportar_ids_fuera` tiene 6 motivos distintos; los 6 se ejercitan
        en una sola corrida (antes solo se probaban 'no existe' y 'sin déficit',
        justo la rama que importa en la práctica —tipo fuera de --tipo— quedaba
        sin cubrir, y con el --tipo boleta por defecto una factura se salta en
        silencio salvo por este reporte)."""
        # 1) no existe → id inexistente
        # 2) es NOTA DE CREDITO
        madre, nc = self._boleta_total()
        # 3) está descartado
        descartado = self._dte(cab=0, neto=0, unidades=0, descartado=True)
        self._linea(descartado, precio=1000, stock=0, activo=False, monto_item=1000)
        self._pago(descartado, 1000)
        # 4) tipo fuera de --tipo (FACTURA con el --tipo boleta por defecto)
        factura = self._dte(cab=0, neto=0, unidades=0, tipo='FACTURA ELECTRONICA',
                            tipo_transaccion='VENTA')
        self._linea(factura, precio=2000, stock=0, activo=False, monto_item=2000)
        self._pago(factura, 2000)
        # 5) tipo_transaccion que no es venta
        compra = self._dte(cab=0, neto=0, unidades=0, tipo_transaccion='COMPRA')
        self._linea(compra, precio=3000, stock=0, activo=False, monto_item=3000)
        self._pago(compra, 3000)
        # 6) sin déficit (la cabecera ya cubre lo pagado)
        sin_deficit = self._dte(cab=4000, neto=3361, unidades=1)
        self._linea(sin_deficit, precio=4000, stock=1, activo=True, monto_item=4000)
        self._pago(sin_deficit, 4000)

        ids = f'999999,{nc.id},{descartado.id},{factura.id},{compra.id},{sin_deficit.id}'
        salida = self._run('--ids', ids)                       # dry-run, sin --fuente

        self.assertIn('6 id(s) de --ids FUERA de alcance', salida)
        self.assertIn('dte=999999: no existe', salida)
        self.assertIn(f'dte={nc.id} folio={nc.numero_documento}: es NOTA DE CREDITO',
                      salida)
        self.assertIn(f'dte={descartado.id} folio={descartado.numero_documento}: '
                      f'está descartado', salida)
        self.assertIn('tipo FACTURA ELECTRONICA fuera de --tipo', salida)
        self.assertIn('tipo_transaccion COMPRA no es venta', salida)
        self.assertIn('sin déficit', salida)
        # y nada se tocó: 0 candidatos
        self.assertIn('TOTAL: 0 candidatos', salida)
        madre.refresh_from_db()
        self.assertEqual(madre.monto_con_iva, Decimal('0'))

    def test_ids_fuera_de_alcance_tipo_todos_deja_pasar_la_factura(self):
        """Contraprueba de la rama 4: con --tipo todos la MISMA factura ya no
        se reporta como fuera de alcance (entra como candidato)."""
        factura = self._dte(cab=0, neto=0, unidades=0, tipo='FACTURA ELECTRONICA',
                            tipo_transaccion='VENTA')
        self._linea(factura, precio=2000, stock=0, activo=False, monto_item=2000)
        self._pago(factura, 2000)
        self._nc(factura, lineas=[(1, 2000)], monto=2000)

        salida_boleta = self._run('--ids', str(factura.id))
        self.assertIn('tipo FACTURA ELECTRONICA fuera de --tipo', salida_boleta)

        salida_todos = self._run('--ids', str(factura.id), '--tipo', 'todos')
        self.assertNotIn('FUERA de alcance', salida_todos)
        self.assertIn('TOTAL: 1 candidatos', salida_todos)

    # -------------------- dry-run / snapshot / revertir --------------- #

    def test_fuente_dry_run_cero_escrituras(self):
        b, _nc = self._legacy_internet()
        with CaptureQueriesContext(connection) as ctx:
            salida = self._run('--ids', str(b.id), '--fuente', 'pagos',
                               '--acepto-discrepancia')
        escrituras = [q['sql'] for q in ctx.captured_queries
                      if RE_ESCRITURA.match(q['sql'] or '')]
        self.assertEqual(escrituras, [], f'dry-run escribió en BD: {escrituras}')
        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('0'))
        self.assertIn('DRY-RUN', salida)
        # el hint copy/paste reproduce AMBAS banderas
        self.assertIn('--fuente pagos --acepto-discrepancia --aplicar', salida)
        self.assertTrue(all(f.endswith('_preview.csv') for f in os.listdir(self.tmpdir)))

    def test_fuente_revertir_restaura_valores_previos(self):
        b1, _nc = self._legacy_internet()
        b2 = self._con_descuento_global()
        self._run('--ids', f'{b1.id},{b2.id}', '--fuente', 'pagos',
                  '--acepto-discrepancia', aplicar=True)
        ruta = self._snapshot_path()
        b1.refresh_from_db(); b2.refresh_from_db()
        self.assertEqual(b1.monto_con_iva, Decimal('81980'))
        self.assertEqual(b2.monto_con_iva, Decimal('46980'))

        call_command('restaurar_cabeceras_boletas_nc', '--revertir', ruta,
                     stdout=io.StringIO())

        b1.refresh_from_db(); b2.refresh_from_db()
        self.assertEqual(b1.monto_con_iva, Decimal('0'))
        self.assertEqual(b1.monto_neto, Decimal('0'))
        self.assertEqual(b1.unidades_productos, 0)
        self.assertEqual(b2.monto_con_iva, Decimal('29738'))
        self.assertEqual(b2.monto_neto, Decimal('24990'))
        self.assertEqual(b2.unidades_productos, 1)             # revierte la reconstrucción

    def test_fuente_no_aplica_a_revertir(self):
        b, _nc = self._legacy_internet()
        self._run('--ids', str(b.id), '--fuente', 'pagos',
                  '--acepto-discrepancia', aplicar=True)
        ruta = self._snapshot_path()
        with self.assertRaises(CommandError):
            call_command('restaurar_cabeceras_boletas_nc', '--revertir', ruta,
                         '--fuente', 'pagos', stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command('restaurar_cabeceras_boletas_nc', '--revertir', ruta,
                         '--acepto-discrepancia', stdout=io.StringIO())


class CompuertaDiscrepanciaTest(RestaurarCabecerasBase):
    """
    Compuerta de discrepancia: elegir la fuente EQUIVOCADA no se acepta en
    silencio. Caso probado en vivo contra prod el 22-ago: `--ids 836011
    --fuente monto_item` escribía $70.490 cuando los pagos dicen $81.980.
    """

    _legacy_internet = RestaurarCabecerasViaManualTest._legacy_internet
    _nc_anulada_con_ticket = RestaurarCabecerasViaManualTest._nc_anulada_con_ticket
    _marketplace_paga_menos = RestaurarCabecerasViaManualTest._marketplace_paga_menos
    _con_descuento_global = RestaurarCabecerasViaManualTest._con_descuento_global
    _ticket_stale = RestaurarCabecerasViaManualTest._ticket_stale

    # ------------------- la fuente equivocada se BLOQUEA -------------- #

    def test_fuente_equivocada_se_bloquea_y_no_escribe(self):
        """El agujero que reportó el verificador: monto_item ($70.490) contra
        pagos ($81.980). Sin la bandera, NADA se escribe."""
        b, _nc = self._legacy_internet()
        salida = self._run('--ids', str(b.id), '--fuente', 'monto_item', aplicar=True)

        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('0'))        # intacto
        self.assertEqual(b.monto_neto, Decimal('0'))
        self.assertIn('BLOQUEADO POR DISCREPANCIA', salida)
        self.assertIn('DISCREPANCIA_ENTRE_FUENTES', salida)
        self.assertIn('Nada que aplicar', salida)
        # no dejó snapshot
        self.assertEqual([f for f in os.listdir(self.tmpdir)
                          if not f.endswith('_preview.csv')], [])

    def test_el_bloqueo_imprime_las_3_fuentes_lado_a_lado(self):
        b, _nc = self._legacy_internet()
        salida = self._run('--ids', str(b.id), '--fuente', 'monto_item')

        self.assertIn('FUENTES DEL DOCUMENTO (elegida: monto_item)', salida)
        self.assertIn('monto_item', salida)
        self.assertIn('$70,490', salida)                       # la elegida
        self.assertIn('$81,980', salida)                       # los pagos
        self.assertIn('difiere en $+11,490', salida)
        self.assertIn('ticket', salida)
        self.assertIn('no resoluble', salida)                  # 3ª fuente ausente
        # avisa del déficit residual ANTES de escribir
        self.assertIn('de DÉFICIT frente a Σ pagos', salida)
        # y entrega el comando exacto para reconocerlo
        self.assertIn(f'--ids {b.id} --fuente monto_item --acepto-discrepancia', salida)

    def test_el_dry_run_bloqueado_no_ofrece_un_aplicar_que_no_funciona(self):
        """Si todo quedó bloqueado, el hint de cierre NO puede ofrecer el mismo
        comando + --aplicar (volvería a bloquearse): tiene que decir que está
        bloqueado y remitir al comando con --acepto-discrepancia."""
        b, _nc = self._legacy_internet()
        salida = self._run('--ids', str(b.id), '--fuente', 'monto_item')
        self.assertIn('BLOQUEADO(S) por discrepancia entre fuentes', salida)
        self.assertNotIn(f'--ids {b.id} --fuente monto_item --aplicar', salida)
        self.assertIn(f'--ids {b.id} --fuente monto_item --acepto-discrepancia', salida)

    def test_con_la_bandera_escribe_y_deja_rastro_de_la_discrepancia(self):
        b, _nc = self._legacy_internet()
        salida = self._run('--ids', str(b.id), '--fuente', 'monto_item',
                           '--acepto-discrepancia', aplicar=True)

        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('70490'))
        self.assertIn('DISCREPANCIA ACEPTADA', salida)
        self.assertIn('FUENTES DEL DOCUMENTO', salida)
        self.assertIn('de DÉFICIT frente a Σ pagos', salida)   # aviso PRE-escritura
        # el documento sigue con déficit: la verificación post lo dice
        self.assertIn('siguen con déficit', salida)
        fila = self._leer_csv(self._snapshot_path())[0]
        self.assertIn('DISCREPANCIA ACEPTADA', fila['discrepancia'])
        self.assertIn('pagos $81,980', fila['discrepancia'])

    def test_fuentes_de_acuerdo_no_piden_la_bandera(self):
        """Σmonto_item == pagos == ticket → se escribe sin --acepto-discrepancia
        y la columna `discrepancia` del CSV queda vacía."""
        b, _tk = self._nc_anulada_con_ticket()
        salida = self._run('--ids', str(b.id), '--fuente', 'monto_item', aplicar=True)

        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('46990'))
        self.assertNotIn('BLOQUEADO POR DISCREPANCIA', salida)
        self.assertNotIn('DISCREPANCIA ACEPTADA', salida)
        self.assertIn('== coincide', salida)                   # las otras 2 fuentes
        fila = self._leer_csv(self._snapshot_path())[0]
        self.assertEqual(fila['discrepancia'], '')

    def test_descuento_global_reconcilia_y_no_pide_la_bandera(self):
        """Σmonto_item $49.980 vs pagos $46.980 difieren, pero cuadran restando
        el descuento global del documento ($3.000): NO es discrepancia."""
        b = self._con_descuento_global()
        salida = self._run('--ids', str(b.id), '--fuente', 'pagos', aplicar=True)

        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('46980'))
        self.assertNotIn('BLOQUEADO POR DISCREPANCIA', salida)
        self.assertIn('− descuento global $3,000', salida)

    def test_al_reves_monto_item_con_descuento_si_es_discrepancia(self):
        """Contraprueba de la reconciliación: elegir monto_item ($49.980) en el
        mismo documento SÍ contradice a los pagos ($46.980) y se bloquea."""
        b = self._con_descuento_global()
        salida = self._run('--ids', str(b.id), '--fuente', 'monto_item', aplicar=True)

        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('29738'))    # intacto
        self.assertIn('BLOQUEADO POR DISCREPANCIA', salida)
        self.assertIn('difiere en $-3,000', salida)

    def test_marketplace_pide_bandera_por_los_127_pesos(self):
        """WALMART liquidó $35.991 y el documento vale $36.118: la diferencia
        del marketplace (−$127 > ±$5) obliga a reconocerla explícitamente."""
        b, _tk = self._marketplace_paga_menos()
        bloqueado = self._run('--ids', str(b.id), '--fuente', 'ticket', aplicar=True)
        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('0'))
        self.assertIn('BLOQUEADO POR DISCREPANCIA', bloqueado)
        self.assertIn('difiere en $-127', bloqueado)

        ok = self._run('--ids', str(b.id), '--fuente', 'ticket',
                       '--acepto-discrepancia', aplicar=True)
        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('36118'))
        self.assertIn('DISCREPANCIA ACEPTADA', ok)

    def test_bandera_sin_fuente_es_error(self):
        self._legacy_internet()
        with self.assertRaises(CommandError) as ctx:
            self._run('--acepto-discrepancia')
        self.assertIn('--fuente', str(ctx.exception))
        with self.assertRaises(CommandError):
            self._run('--acepto-discrepancia', aplicar=True)

    def test_la_bandera_no_relaja_la_via_automatica(self):
        """--acepto-discrepancia exige --fuente, así que la vía automática (que
        rechaza por FUENTE_INCONSISTENTE) sigue siendo inalcanzable con ella."""
        b, _nc = self._legacy_internet()
        salida = self._run('--ids', str(b.id), aplicar=True)   # automático
        self.assertIn('FUENTE_INCONSISTENTE', salida)
        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('0'))

    # ------------------- efecto colateral DTE↔ticket ------------------ #

    def test_avisa_el_descuadre_dte_ticket_que_se_crea(self):
        """dte 2178247: hoy cabecera == ticket; tras --fuente pagos el DTE sube
        a $188.940 y el ticket sigue en $173.950 (el command no toca Ticket)."""
        b, tk = self._ticket_stale()
        salida = self._run('--ids', str(b.id), '--fuente', 'pagos',
                           '--acepto-discrepancia', aplicar=True)

        b.refresh_from_db(); tk.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('188940'))
        self.assertEqual(tk.total, Decimal('173950'))          # el ticket NO se tocó
        self.assertEqual(tk.subTotal, Decimal('173950'))
        self.assertIn('EFECTO COLATERAL', salida)
        self.assertIn('se CREA un descuadre DTE↔ticket de $+14,990', salida)
        # se repite DESPUÉS de escribir para que no se pierda en el detalle
        self.assertIn('quedaron descuadrados contra su Ticket', salida)
        fila = self._leer_csv(self._snapshot_path())[0]
        self.assertIn('EFECTO COLATERAL', fila['nota_ticket'])

    def test_fuente_ticket_en_el_ticket_stale_no_mejora(self):
        """La fuente contaminada (el propio ticket, $173.950) no supera la
        cabecera actual: el command la rechaza solo, sin necesidad de banderas."""
        b, _tk = self._ticket_stale()
        salida = self._run('--ids', str(b.id), '--fuente', 'ticket', aplicar=True)
        self.assertIn('FUENTE_NO_MEJORA', salida)
        b.refresh_from_db()
        self.assertEqual(b.monto_con_iva, Decimal('173950'))

    def test_cuando_la_escritura_resuelve_un_descuadre_lo_dice(self):
        """Caso opuesto (dte 2190216): la cabecera está en $0 y el ticket en
        $46.990; escribir DEJA a ambos iguales y el command lo informa."""
        b, _tk = self._nc_anulada_con_ticket()
        salida = self._run('--ids', str(b.id), '--fuente', 'monto_item', aplicar=True)
        self.assertIn('queda RESUELTO', salida)
        self.assertNotIn('EFECTO COLATERAL', salida)

    def test_via_automatica_tambien_registra_la_nota_de_ticket(self):
        b, _nc = self._boleta_total()
        self._ticket(b, correlativo=777001, total=199950)
        self._run(aplicar=True)
        fila = self._leer_csv(self._snapshot_path())[0]
        self.assertEqual(fila['discrepancia'], '')
        self.assertIn('RESUELTO', fila['nota_ticket'])
