# -*- coding: utf-8 -*-
"""
Tests del command ``backfill_destino_movimientos`` (Fase D de la auditoría de
Reportes 2026-08: deuda "Writer destino NULL").

Cubre las tres cosas que hacen peligroso a un backfill de medio millón de filas:
  1. que el DRY-RUN no escriba NADA (verificado con CaptureQueriesContext);
  2. que las guardas duras no se puedan saltar (EGRESOS, destino ya poblado,
     conceptos de traspaso de dos piernas, tallas huérfanas);
  3. que se pueda deshacer (--revertir) y que sea idempotente.

Correr (BD desechable, NUNCA prod):
    $env:DATABASE_URL="sqlite:///C:/temp/td4.sqlite3"; python manage.py test app.tests.test_backfill_destino_movimientos
"""
import csv
import datetime
import io
import os
import re
import tempfile

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from app.models import Movimientos_Producto, Producto, Producto_Talla, Sucursal
from .factories import (crear_empresa, crear_producto_con_talla, crear_sucursal,
                        setup_entorno_completo)

RE_ESCRITURA = re.compile(r'^\s*(INSERT|UPDATE|DELETE)\b', re.IGNORECASE)


class BackfillDestinoBase(TestCase):

    def setUp(self):
        self.env = setup_entorno_completo()
        self.empresa = self.env['empresa']
        self.suc_a = self.env['sucursal']                  # dueña del SKU A
        self.pt_a = self.env['producto_talla']
        self.suc_b = crear_sucursal(self.empresa, alias='SUC-B')
        _, self.pt_b = crear_producto_con_talla(self.suc_b, articulo='Art B', sku=2000001)
        self.tmpdir = tempfile.mkdtemp()

    # ------------------------- fixtures ------------------------------- #

    def _mov(self, pt=None, destino=None, origen=None, concepto='INGRESO_INICIAL',
             cantidad=5, fecha=None, tipo=None, estado='COMPLETADO'):
        """
        Crea un movimiento sin pasar por save() cuando hace falta forzar un
        tipo_movimiento que el modelo derivaría del signo (save() reescribe
        tipo_movimiento = INGRESO/EGRESO según cantidad).
        """
        mov = Movimientos_Producto.objects.create(
            ProductoTalla=pt or self.pt_a,
            sucursal_origen=origen,
            sucursal_destino=destino,
            cantidad=cantidad,
            concepto=concepto,
            estado=estado,
            fecha=fecha or datetime.date(2024, 5, 10),
        )
        if tipo and mov.tipo_movimiento != tipo:
            Movimientos_Producto.objects.filter(id=mov.id).update(tipo_movimiento=tipo)
            mov.refresh_from_db()
        return mov

    # ------------------------- helpers -------------------------------- #

    def _run(self, *args, aplicar=False, sin_impacto=True):
        out = io.StringIO()
        argv = ['backfill_destino_movimientos', '--snapshot-dir', self.tmpdir]
        if aplicar:
            argv.append('--aplicar')
        if sin_impacto:
            argv.append('--sin-impacto')
        argv.extend(args)
        call_command(*argv, stdout=out, stderr=out)
        return out.getvalue()

    def _snapshot(self, preview=False):
        archivos = [f for f in os.listdir(self.tmpdir)
                    if f.startswith('_backfill_destino_')
                    and f.endswith('_preview.csv') == preview]
        self.assertEqual(len(archivos), 1, f'CSV encontrados: {archivos}')
        return os.path.join(self.tmpdir, archivos[0])

    def _leer_csv(self, ruta):
        with open(ruta, encoding='utf-8', newline='') as f:
            return list(csv.DictReader(f))


# =========================================================================== #
# 1. DRY-RUN
# =========================================================================== #

class DryRunTest(BackfillDestinoBase):

    def test_dry_run_no_escribe_nada_en_la_bd(self):
        """La guarda central: sin --aplicar, 0 INSERT/UPDATE/DELETE."""
        for _ in range(3):
            self._mov()
        self._mov(pt=self.pt_b)

        with CaptureQueriesContext(connection) as ctx:
            salida = self._run()

        escrituras = [q['sql'] for q in ctx.captured_queries
                      if RE_ESCRITURA.match(q['sql'] or '')]
        self.assertEqual(escrituras, [], f'el dry-run escribió: {escrituras[:3]}')
        self.assertIn('DRY-RUN', salida)
        self.assertEqual(
            Movimientos_Producto.objects.filter(sucursal_destino__isnull=True).count(), 4)

    def test_dry_run_deja_csv_preview_con_lo_que_haria(self):
        m1 = self._mov()
        m2 = self._mov(pt=self.pt_b)
        self._run()

        filas = self._leer_csv(self._snapshot(preview=True))
        self.assertEqual(len(filas), 2)
        por_id = {int(f['id']): f for f in filas}
        self.assertEqual(por_id[m1.id]['destino_new'], str(self.suc_a.id))
        self.assertEqual(por_id[m1.id]['sucursal_duena'], str(self.suc_a.id))
        self.assertEqual(por_id[m1.id]['destino_old'], '')
        self.assertEqual(por_id[m2.id]['destino_new'], str(self.suc_b.id))
        # columnas exigidas por el diseño
        for col in ('id', 'concepto', 'fecha', 'ProductoTalla_id',
                    'destino_old', 'destino_new', 'sucursal_duena'):
            self.assertIn(col, filas[0])

    def test_dry_run_sin_candidatos_no_deja_csv(self):
        self._mov(destino=self.suc_a)  # ya poblado
        salida = self._run()
        self.assertEqual([f for f in os.listdir(self.tmpdir) if f.endswith('.csv')], [])
        self.assertIn('0 candidatos', salida)


# =========================================================================== #
# 2. GUARDAS DURAS
# =========================================================================== #

class GuardasTest(BackfillDestinoBase):

    def test_jamas_toca_filas_con_destino_ya_poblado(self):
        ya = self._mov(destino=self.suc_b)          # destino "equivocado" a propósito
        pendiente = self._mov()
        self._run(aplicar=True)

        ya.refresh_from_db()
        pendiente.refresh_from_db()
        self.assertEqual(ya.sucursal_destino_id, self.suc_b.id,
                         'pisó un destino que ya existía')
        self.assertEqual(pendiente.sucursal_destino_id, self.suc_a.id)

    def test_jamas_toca_egresos(self):
        egr = self._mov(cantidad=-4, concepto='VENTA_PUBLICO', origen=self.suc_a)
        self.assertEqual(egr.tipo_movimiento, 'EGRESO')
        salida = self._run(aplicar=True)

        egr.refresh_from_db()
        self.assertIsNone(egr.sucursal_destino_id)
        self.assertIn('0 candidatos', salida)

    def test_egreso_con_tipo_mal_etiquetado_igual_queda_fuera(self):
        """Filas legacy con cantidad<0 pero tipo=INGRESO no existen hoy en prod;
        si aparecieran, el filtro es por tipo_movimiento y el signo no las cuela."""
        egr = self._mov(cantidad=-3, concepto='AJUSTE_INVENTARIO', tipo='EGRESO')
        self._run(aplicar=True)
        egr.refresh_from_db()
        self.assertIsNone(egr.sucursal_destino_id)

    def test_conceptos_de_traspaso_de_dos_piernas_excluidos(self):
        """TRASPASO_ENTRADA cumple la regla sólo en el 89% de prod: no se toca."""
        te = self._mov(concepto='TRASPASO_ENTRADA', origen=self.suc_b)
        rt = self._mov(concepto='REGULARIZACION_TRASPASO', origen=self.suc_b)
        ok = self._mov(concepto='TRASPASO_SUCURSAL', origen=self.suc_b)
        self._run(aplicar=True)

        te.refresh_from_db(); rt.refresh_from_db(); ok.refresh_from_db()
        self.assertIsNone(te.sucursal_destino_id)
        self.assertIsNone(rt.sucursal_destino_id)
        self.assertEqual(ok.sucursal_destino_id, self.suc_a.id)

    def test_conceptos_prohibidos_pedidos_explicitamente_son_error(self):
        """
        El motivo declarado importa: NO es "traspaso de dos piernas"
        (TRASPASO_SUCURSAL también tiene dos y SÍ entra). Es que en esos
        conceptos el writer YA puebla la columna en el 100 % de sus filas.
        """
        with self.assertRaises(CommandError) as cm:
            self._run('--conceptos', 'TRASPASO_ENTRADA')
        msg = str(cm.exception)
        self.assertIn('YA usa sucursal_destino', msg)
        self.assertNotIn('dos piernas', msg)

    def test_concepto_inexistente_es_error(self):
        with self.assertRaises(CommandError) as cm:
            self._run('--conceptos', 'CONCEPTO_QUE_NO_EXISTE')
        self.assertIn('no declarados', str(cm.exception))

    def test_talla_huerfana_se_salta_y_se_reporta(self):
        """Si la talla (o su producto/sucursal) no se resuelve, la fila NO se toca."""
        huerfano = self._mov(pt=self.pt_b)
        sano = self._mov()
        # Se rompe el puente talla→producto→sucursal sin borrar el movimiento:
        # Producto.sucursal es NOT NULL, así que se apunta a un id inexistente.
        # Se restaura en el finally para que el check de FKs del teardown de
        # SQLite no enmascare el resultado real del test.
        fantasma = Sucursal.objects.order_by('-id').first().id + 999
        Producto.objects.filter(id=self.pt_b.producto_id).update(sucursal_id=fantasma)
        try:
            salida = self._run(aplicar=True)

            huerfano.refresh_from_db(); sano.refresh_from_db()
            self.assertIsNone(huerfano.sucursal_destino_id)
            self.assertEqual(sano.sucursal_destino_id, self.suc_a.id)
            self.assertIn('SALTADOS', salida)
            self.assertIn(f'mov={huerfano.id}', salida)
        finally:
            Producto.objects.filter(id=self.pt_b.producto_id).update(
                sucursal_id=self.suc_b.id)

    def test_movimientos_de_otra_sucursal_reciben_su_propia_duena(self):
        """Nunca se escribe una sucursal ajena: el destino sale del SKU, fila a fila."""
        a = self._mov(pt=self.pt_a, origen=self.suc_b)
        b = self._mov(pt=self.pt_b, origen=self.suc_a)
        self._run(aplicar=True)

        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual(a.sucursal_destino_id, self.suc_a.id)
        self.assertEqual(b.sucursal_destino_id, self.suc_b.id)

    def test_no_cambia_ningun_otro_campo(self):
        m = self._mov(cantidad=7, origen=self.suc_b, concepto='CORRECCION_STOCK')
        antes = Movimientos_Producto.objects.filter(id=m.id).values().first()
        self._run(aplicar=True)
        despues = Movimientos_Producto.objects.filter(id=m.id).values().first()

        self.assertEqual(despues.pop('sucursal_destino_id'), self.suc_a.id)
        antes.pop('sucursal_destino_id')
        # updated_at es auto_now: bulk_update NO lo toca (no pasa por save())
        self.assertEqual(antes, despues)


# =========================================================================== #
# 3. FILTROS
# =========================================================================== #

class FiltrosTest(BackfillDestinoBase):

    def test_filtro_por_fechas(self):
        viejo = self._mov(fecha=datetime.date(2019, 3, 1))
        medio = self._mov(fecha=datetime.date(2024, 6, 15))
        nuevo = self._mov(fecha=datetime.date(2026, 8, 1))

        self._run('--desde', '2024-01-01', '--hasta', '2025-12-31', aplicar=True)

        viejo.refresh_from_db(); medio.refresh_from_db(); nuevo.refresh_from_db()
        self.assertIsNone(viejo.sucursal_destino_id)
        self.assertEqual(medio.sucursal_destino_id, self.suc_a.id)
        self.assertIsNone(nuevo.sucursal_destino_id)

    def test_desde_posterior_a_hasta_es_error(self):
        with self.assertRaises(CommandError) as cm:
            self._run('--desde', '2026-01-01', '--hasta', '2025-01-01')
        self.assertIn('posterior', str(cm.exception))

    def test_fecha_invalida_es_error(self):
        with self.assertRaises(CommandError):
            self._run('--desde', '2026-13-99')

    def test_filtro_por_conceptos(self):
        a = self._mov(concepto='ANULACION_TICKET')
        b = self._mov(concepto='INGRESO_INICIAL')
        self._run('--conceptos', 'ANULACION_TICKET', aplicar=True)

        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual(a.sucursal_destino_id, self.suc_a.id)
        self.assertIsNone(b.sucursal_destino_id)

    def test_limite_procesa_solo_los_primeros_por_id(self):
        movs = [self._mov() for _ in range(5)]
        self._run('--limite', '2', aplicar=True)

        for m in movs:
            m.refresh_from_db()
        self.assertEqual([m.sucursal_destino_id for m in movs],
                         [self.suc_a.id, self.suc_a.id, None, None, None])
        self.assertEqual(len(self._leer_csv(self._snapshot())), 2)


# =========================================================================== #
# 4. APLICAR / IDEMPOTENCIA / CHUNKS
# =========================================================================== #

class AplicarTest(BackfillDestinoBase):

    def test_aplicar_escribe_y_es_idempotente(self):
        movs = [self._mov() for _ in range(4)]
        salida1 = self._run(aplicar=True)
        for m in movs:
            m.refresh_from_db()
        self.assertTrue(all(m.sucursal_destino_id == self.suc_a.id for m in movs))
        self.assertIn('0 siguen con destino NULL', salida1)

        # 2ª corrida: no queda nada que hacer y no se escribe
        with CaptureQueriesContext(connection) as ctx:
            salida2 = self._run(aplicar=True)
        escrituras = [q['sql'] for q in ctx.captured_queries
                      if RE_ESCRITURA.match(q['sql'] or '')]
        self.assertEqual(escrituras, [])
        self.assertIn('Nada que aplicar', salida2)

    def test_chunk_chico_escribe_todo_igual(self):
        movs = [self._mov() for _ in range(7)]
        self._run('--chunk', '2', aplicar=True)
        for m in movs:
            m.refresh_from_db()
        self.assertTrue(all(m.sucursal_destino_id == self.suc_a.id for m in movs))

    def test_snapshot_se_escribe_antes_y_cubre_todo_lo_aplicado(self):
        movs = [self._mov() for _ in range(3)]
        self._run(aplicar=True)
        filas = self._leer_csv(self._snapshot())
        self.assertEqual({int(f['id']) for f in filas}, {m.id for m in movs})
        self.assertTrue(all(f['destino_old'] == '' for f in filas))

    def test_cantidad_cero_se_puebla_pero_se_reporta_aparte(self):
        m = self._mov(cantidad=0, concepto='AJUSTE_INVENTARIO')
        salida = self._run(aplicar=True)
        m.refresh_from_db()
        self.assertEqual(m.sucursal_destino_id, self.suc_a.id)
        self.assertIn('cantidad == 0', salida)


# =========================================================================== #
# 5. REVERTIR
# =========================================================================== #

class RevertirTest(BackfillDestinoBase):

    def _aplicar_y_snapshot(self, n=3):
        movs = [self._mov() for _ in range(n)]
        self._run(aplicar=True)
        return movs, self._snapshot()

    def test_revertir_deja_todo_como_estaba(self):
        movs, snap = self._aplicar_y_snapshot()
        out = io.StringIO()
        call_command('backfill_destino_movimientos', '--revertir', snap, stdout=out)

        for m in movs:
            m.refresh_from_db()
        self.assertTrue(all(m.sucursal_destino_id is None for m in movs))
        self.assertIn('Revertido', out.getvalue())

    def test_revertir_se_niega_si_el_valor_cambio_despues(self):
        movs, snap = self._aplicar_y_snapshot()
        Movimientos_Producto.objects.filter(id=movs[0].id).update(
            sucursal_destino=self.suc_b)

        with self.assertRaises(CommandError) as cm:
            call_command('backfill_destino_movimientos', '--revertir', snap,
                         stdout=io.StringIO())
        self.assertIn('cambiaron después', str(cm.exception))

        # y NADA se revirtió (transacción abortada)
        for m in movs:
            m.refresh_from_db()
        self.assertEqual(movs[0].sucursal_destino_id, self.suc_b.id)
        self.assertEqual(movs[1].sucursal_destino_id, self.suc_a.id)

    def test_revertir_con_forzar_pisa_el_valor_cambiado(self):
        movs, snap = self._aplicar_y_snapshot()
        Movimientos_Producto.objects.filter(id=movs[0].id).update(
            sucursal_destino=self.suc_b)

        out = io.StringIO()
        call_command('backfill_destino_movimientos', '--revertir', snap,
                     '--forzar', stdout=out)
        for m in movs:
            m.refresh_from_db()
        self.assertTrue(all(m.sucursal_destino_id is None for m in movs))
        self.assertIn('--forzar', out.getvalue())

    def test_revertir_ignora_los_que_ya_no_existen(self):
        movs, snap = self._aplicar_y_snapshot()
        Movimientos_Producto.objects.filter(id=movs[0].id).delete()

        out = io.StringIO()
        call_command('backfill_destino_movimientos', '--revertir', snap, stdout=out)
        movs[1].refresh_from_db()
        self.assertIsNone(movs[1].sucursal_destino_id)
        self.assertIn('ya no existen', out.getvalue())

    def test_revertir_rechaza_un_preview(self):
        self._mov()
        self._run()  # dry-run → deja *_preview.csv
        with self.assertRaises(CommandError) as cm:
            call_command('backfill_destino_movimientos',
                         '--revertir', self._snapshot(preview=True), stdout=io.StringIO())
        self.assertIn('PREVIEW', str(cm.exception))

    def test_revertir_rechaza_csv_ajeno(self):
        ruta = os.path.join(self.tmpdir, 'otro.csv')
        with open(ruta, 'w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['dte_id', 'old_neto'])
            w.writeheader()
            w.writerow({'dte_id': 1, 'old_neto': 100})
        with self.assertRaises(CommandError) as cm:
            call_command('backfill_destino_movimientos', '--revertir', ruta,
                         stdout=io.StringIO())
        self.assertIn('no es un', str(cm.exception))

    def test_revertir_archivo_inexistente(self):
        with self.assertRaises(CommandError):
            call_command('backfill_destino_movimientos',
                         '--revertir', os.path.join(self.tmpdir, 'nope.csv'),
                         stdout=io.StringIO())

    def test_ciclo_aplicar_revertir_aplicar(self):
        movs, snap = self._aplicar_y_snapshot(2)
        call_command('backfill_destino_movimientos', '--revertir', snap,
                     stdout=io.StringIO())
        self._run(aplicar=True)
        for m in movs:
            m.refresh_from_db()
        self.assertTrue(all(m.sucursal_destino_id == self.suc_a.id for m in movs))


# =========================================================================== #
# 6. IMPACTO (la sección que decide al usuario)
# =========================================================================== #

class ImpactoTest(BackfillDestinoBase):

    def test_seccion_de_impacto_corre_y_declara_lo_que_no_arregla(self):
        self._mov(fecha=datetime.date(2026, 2, 1))
        salida = self._run('--impacto-corte', '2026-01-01', sin_impacto=False)
        self.assertIn('IMPACTO MEDIDO', salida)
        self.assertIn('kardex puro', salida)
        self.assertIn('diferencias-recepción: NO se mueve', salida)

    def test_impacto_agrupa_por_talla_y_no_por_stock(self):
        """
        Regresión: el clamp `stock_hist <= 0` de `_acumulados_historicos` es POR
        TALLA. Si el GROUP BY del impacto se hiciera por (stock, sucursal), dos
        tallas de la misma sucursal con el mismo stock se fundirían en una y el
        clamp mentiría. Acá hay exactamente ese par.
        """
        _, pt_c = crear_producto_con_talla(self.suc_a, articulo='Art C', sku=3000001)
        self.assertEqual(pt_c.stock, self.pt_a.stock)

        # pt_a: ingreso post-corte con destino NULL → hoy no se revierte, tras
        # el backfill sí (10 − 10 = 0, se cae por el clamp).
        self._mov(pt=self.pt_a, fecha=datetime.date(2026, 2, 1), cantidad=10)
        # pt_c: egreso post-corte → se suma de vuelta en ambos escenarios.
        self._mov(pt=pt_c, fecha=datetime.date(2026, 2, 2), cantidad=-5,
                  concepto='VENTA_PUBLICO', origen=self.suc_a)

        salida = self._run('--impacto-corte', '2026-01-01', sin_impacto=False)

        self.assertIn('tallas con movimientos post-corte: 2', salida)
        m = re.search(r'TOTAL\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+(-?[\d.]+)', salida)
        self.assertIsNotNone(m, f'no se encontró la fila TOTAL en:\n{salida}')
        hoy, fut, oraculo, delta = (int(g.replace('.', '')) for g in m.groups())
        self.assertEqual((hoy, fut, oraculo), (25, 15, 15))
        self.assertEqual(delta, -10)

    def test_impacto_corte_invalido_es_error(self):
        self._mov()
        with self.assertRaises(CommandError):
            self._run('--impacto-corte', 'ayer', sin_impacto=False)

    def test_impacto_no_escribe_nada(self):
        self._mov(fecha=datetime.date(2026, 2, 1))
        with CaptureQueriesContext(connection) as ctx:
            self._run('--impacto-corte', '2026-01-01', sin_impacto=False)
        escrituras = [q['sql'] for q in ctx.captured_queries
                      if RE_ESCRITURA.match(q['sql'] or '')]
        self.assertEqual(escrituras, [])

    def test_contrafactual_sin_traspaso_sucursal_se_mide_aparte(self):
        """
        TRASPASO_SUCURSAL es el 40 % del universo y no tiene NI UNA fila con
        destino poblado en prod: su regla no se puede validar contra los
        writers. El único respaldo numérico es este contrafactual, así que el
        command tiene que imprimirlo, y con el número correcto.

        Escenario: una talla con stock 10 y un ingreso post-corte de 10 u con
        concepto TRASPASO_SUCURSAL y destino NULL.
          - HOY                  : 10 − 0  = 10  (el ingreso no se ve)
          - BACKFILL COMPLETO    : 10 − 10 =  0  (cae por el clamp) == oráculo
          - SIN TRASPASO_SUCURSAL: 10 − 0  = 10  (queda igual que hoy)
        """
        self._mov(pt=self.pt_a, fecha=datetime.date(2026, 2, 1), cantidad=10,
                  concepto='TRASPASO_SUCURSAL', origen=self.suc_b)
        salida = self._run('--impacto-corte', '2026-01-01', sin_impacto=False)

        self.assertIn('Contrafactual TRASPASO_SUCURSAL', salida)
        vals = {}
        for etiqueta, clave in (('BACKFILL SIN TRASPASO_SUCURSAL', 'sin_ts'),
                                ('BACKFILL COMPLETO', 'full'),
                                ('KARDEX PURO', 'oraculo'),
                                ('HOY', 'hoy')):
            m = re.search(r'^\s*' + re.escape(etiqueta) + r'[^\d\n]*([\d.]+)',
                          salida, re.M)
            self.assertIsNotNone(m, f'falta la fila {etiqueta} en:\n{salida}')
            vals[clave] = int(m.group(1).replace('.', ''))
        self.assertEqual(vals['oraculo'], 0)
        self.assertEqual(vals['full'], 0, 'el backfill completo debe cuadrar con el oráculo')
        self.assertEqual(vals['hoy'], 10)
        self.assertEqual(vals['sin_ts'], 10,
                         'sacar TRASPASO_SUCURSAL debe dejar el error intacto')


# =========================================================================== #
# 7. IMPACTO EN EL LISTADO movimientos-sucursal (MIGRA, no suma)
# =========================================================================== #

class ImpactoListadoTest(BackfillDestinoBase):
    """
    El listado (``views.py`` ~:22273) filtra
    ``Q(origen=X, EGRESO) | Q(destino=X, INGRESO) | Q(origen=X, destino IS NULL)``.
    La 3ª cláusula EXIGE destino NULL, así que poblar el destino SACA la fila
    del kardex de la emisora: MIGRA, no suma. Este grupo blinda esa redacción,
    que es la que lee el usuario para decidir.
    """

    def _bloque(self, salida):
        out = {}
        for etiqueta in ('SIN CAMBIO', 'MIGRAN', 'APARECEN'):
            m = re.search(r'([\d.]+) filas \(([\d.]+) u\)\s+' + etiqueta, salida)
            self.assertIsNotNone(m, f'falta la línea {etiqueta} en:\n{salida}')
            out[etiqueta] = (int(m.group(1).replace('.', '')),
                             int(m.group(2).replace('.', '')))
        return out

    def test_clasifica_migran_sin_cambio_y_aparecen(self):
        self._mov(pt=self.pt_a, origen=self.suc_b, cantidad=7)    # migra B -> A
        self._mov(pt=self.pt_a, origen=self.suc_a, cantidad=3)    # sin cambio
        self._mov(pt=self.pt_a, origen=None, cantidad=1)          # aparece

        salida = self._run()
        b = self._bloque(salida)
        self.assertEqual(b['MIGRAN'], (1, 7))
        self.assertEqual(b['SIN CAMBIO'], (1, 3))
        self.assertEqual(b['APARECEN'], (1, 1))

    def test_dice_MIGRAN_y_nunca_que_se_suman_a_las_dos(self):
        self._mov(pt=self.pt_a, origen=self.suc_b)
        salida = self._run()
        self.assertIn('MIGRAN', salida)
        self.assertIn('salen del kardex de la EMISORA', salida)
        self.assertNotIn('sumarían', salida)
        self.assertNotIn('sumarian', salida)

    def test_neto_por_sucursal_pierde_y_gana(self):
        for _ in range(3):
            self._mov(pt=self.pt_a, origen=self.suc_b)   # B pierde 3, A gana 3
        self._mov(pt=self.pt_b, origen=self.suc_a)       # A pierde 1, B gana 1

        salida = self._run()
        filas = {}
        for linea in salida.splitlines():
            m = re.match(r'\s+(SUC-\S+)\s+([\d.]+)\s+([\d.]+)\s+([+-][\d.]+)\s*$', linea)
            if m:
                filas[m.group(1)] = (int(m.group(2).replace('.', '')),
                                     int(m.group(3).replace('.', '')),
                                     int(m.group(4).replace('.', '')))
        self.assertEqual(filas[self.suc_a.alias], (1, 3, +2))
        self.assertEqual(filas[self.suc_b.alias], (3, 1, -2))

    def test_cuenta_el_cruce_de_empresa(self):
        otra = crear_empresa(nombre='Otra Empresa', rut='77.111.111-1')
        suc_c = crear_sucursal(otra, alias='SUC-C')
        _, pt_c = crear_producto_con_talla(suc_c, articulo='Art C', sku=4000001)

        self._mov(pt=pt_c, origen=self.suc_a)     # cruza empresa
        self._mov(pt=self.pt_a, origen=self.suc_b)  # misma empresa

        salida = self._run()
        m = re.search(r'MIGRAN.*?\((\d+) cruzan empresa\)', salida, re.S)
        self.assertIsNotNone(m, salida)
        self.assertEqual(int(m.group(1)), 1)

    def test_sin_candidatos_no_imprime_la_seccion(self):
        salida = self._run()
        self.assertNotIn('listado movimientos-sucursal', salida)


# =========================================================================== #
# 8. ALCANCE DE LA EVIDENCIA POR CONCEPTO
# =========================================================================== #

class AlcanceEvidenciaTest(BackfillDestinoBase):

    def test_avisa_de_los_conceptos_sin_ninguna_fila_poblada(self):
        """
        En prod el 43,81 % del universo (TRASPASO_SUCURSAL 220.697 +
        AJUSTE_INVENTARIO 18.507 + VENTA_PUBLICO 66) no tiene NI UNA fila con
        destino poblado: la tabla "qué escribieron los writers sanos" no lo
        cubre y el command tiene que decirlo en vez de dejar creer que sí.
        """
        self._mov(concepto='TRASPASO_SUCURSAL', cantidad=4)     # sin evidencia
        self._mov(concepto='INGRESO_INICIAL', cantidad=2)       # con evidencia
        self._mov(pt=self.pt_b, concepto='INGRESO_INICIAL', destino=self.suc_b)

        salida = self._run()
        self.assertIn('ALCANCE DE LA EVIDENCIA', salida)
        self.assertIn('TRASPASO_SUCURSAL', salida.split('ALCANCE DE LA EVIDENCIA')[1])
        self.assertNotIn('INGRESO_INICIAL',
                         salida.split('ALCANCE DE LA EVIDENCIA')[1])

    def test_sin_conceptos_ciegos_no_imprime_la_advertencia(self):
        self._mov(concepto='INGRESO_INICIAL')
        self._mov(pt=self.pt_b, concepto='INGRESO_INICIAL', destino=self.suc_b)
        salida = self._run()
        self.assertNotIn('ALCANCE DE LA EVIDENCIA', salida)


# =========================================================================== #
# 9. SNAPSHOT DIR — el CSV de 32 MB no puede caer en el repo
# =========================================================================== #

class SnapshotDirTest(BackfillDestinoBase):

    def _run_sin_snapshot_dir(self, *args):
        out = io.StringIO()
        call_command('backfill_destino_movimientos', '--sin-impacto', *args,
                     stdout=out, stderr=out)
        return out.getvalue()

    def test_default_no_escribe_en_el_directorio_actual_ni_en_el_repo(self):
        self._mov()
        default_dir = os.path.join(tempfile.gettempdir(), 'retailmind_backfill_destino')
        antes = set(os.listdir(default_dir)) if os.path.isdir(default_dir) else set()
        cwd_antes = set(os.listdir(os.getcwd()))
        try:
            salida = self._run_sin_snapshot_dir()
            nuevos = set(os.listdir(default_dir)) - antes
            self.assertEqual(len(nuevos), 1, f'CSV nuevos en el default: {nuevos}')
            self.assertEqual(set(os.listdir(os.getcwd())) - cwd_antes, set(),
                             'el default dejó un archivo en el directorio actual')
            self.assertIn(default_dir, salida)
            raiz = os.path.abspath(str(settings.BASE_DIR))
            self.assertNotEqual(os.path.commonpath([default_dir, raiz]), raiz)
        finally:
            for f in set(os.listdir(default_dir)) - antes:
                os.remove(os.path.join(default_dir, f))

    def test_snapshot_dir_inexistente_es_error(self):
        self._mov()
        with self.assertRaises(CommandError) as cm:
            self._run('--snapshot-dir', os.path.join(self.tmpdir, 'no-existe'))
        self.assertIn('no existe', str(cm.exception))

    def test_avisa_si_la_carpeta_esta_dentro_del_repo(self):
        """Sin candidatos no se escribe ningún CSV: el aviso sale igual."""
        salida = self._run('--snapshot-dir', str(settings.BASE_DIR))
        self.assertIn('DENTRO del repo', salida)
        self.assertIn('.gitignore', salida)
        self.assertEqual(
            [f for f in os.listdir(str(settings.BASE_DIR))
             if f.startswith('_backfill_destino_')], [])

    def test_el_comando_que_sugiere_conserva_el_snapshot_dir(self):
        self._mov()
        salida = self._run('--snapshot-dir', self.tmpdir)
        self.assertIn(f'--snapshot-dir {self.tmpdir}', salida)


# =========================================================================== #
# 10. ESTIMACIÓN DE COSTO — 2 sentencias por lote, no 1 RTT
# =========================================================================== #

class EstimacionTest(BackfillDestinoBase):

    def test_declara_dos_sentencias_por_lote_y_las_mide_por_separado(self):
        for _ in range(4):
            self._mov()
        salida = self._run('--chunk', '2')

        self.assertIn('2 sentencias por lote', salida)
        self.assertIn('re-lectura del lote', salida)
        self.assertIn('UPDATE con CASE', salida)
        self.assertIn('PISO', salida)
        # el RTT vacío se sigue mostrando, pero YA NO es la base del cálculo
        self.assertIn('lo único que medía la versión anterior', salida)
        m = re.search(r'([\d.]+) lotes de ([\d.]+) × 2 sentencias', salida)
        self.assertIsNotNone(m, salida)
        self.assertEqual((int(m.group(1)), int(m.group(2))), (2, 2))

    def test_avisa_si_el_chunk_supera_el_optimo_medido(self):
        self._mov()
        self.assertIn('escala PEOR que lineal', self._run('--chunk', '5000'))
        self.assertNotIn('escala PEOR que lineal', self._run('--chunk', '2000'))

    def test_la_estimacion_no_escribe_nada(self):
        for _ in range(3):
            self._mov()
        with CaptureQueriesContext(connection) as ctx:
            self._run('--chunk', '2')
        escrituras = [q['sql'] for q in ctx.captured_queries
                      if RE_ESCRITURA.match(q['sql'] or '')]
        self.assertEqual(escrituras, [])
