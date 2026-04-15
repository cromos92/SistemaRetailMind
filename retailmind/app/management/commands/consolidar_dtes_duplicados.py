"""
Consolida DTEs duplicados por (numero_documento + tipo_documento + sucursal).

Problema: migrate_ventas_pagos crea DTEs "on-the-fly" con fecha de ventas,
y despues migrate_dtes crea el DTE real con fecha correcta de MySQL.
Quedan duplicados con pagos en ambos.

Solucion:
  1. Agrupa por (numero_documento, tipo_documento, sucursal_id)
  2. Conserva el DTE con fecha mas antigua (el real de MySQL)
  3. Mueve pagos unicos del duplicado al DTE correcto
  4. Elimina pagos duplicados
  5. Elimina DTEs duplicados

Uso:
    python manage.py consolidar_dtes_duplicados --dry-run
    python manage.py consolidar_dtes_duplicados
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Consolida DTEs duplicados por (numero_documento + tipo_documento + sucursal)'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Mostrar que se haria sin ejecutar cambios')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        self.stdout.write('=' * 70)
        self.stdout.write('  CONSOLIDAR DTEs DUPLICADOS (por num + tipo + sucursal)')
        self.stdout.write('=' * 70)
        if dry_run:
            self.stdout.write(self.style.WARNING('  [DRY-RUN] No se modificara nada\n'))

        with connection.cursor() as c:
            # =================================================================
            # PASO 1: Identificar el DTE "ganador" por grupo
            # Ganador = fecha_emision mas antigua (el real de MySQL)
            # Si hay empate de fecha, el de menor ID
            # =================================================================
            self.stdout.write('[1/6] Identificando DTEs duplicados...')

            c.execute('''
                CREATE TEMP TABLE dte_ganadores AS
                SELECT DISTINCT ON (numero_documento, tipo_documento, sucursal_id)
                    id AS dte_id_ganador,
                    numero_documento,
                    tipo_documento,
                    sucursal_id,
                    fecha_emision
                FROM app_dte
                WHERE sucursal_id IS NOT NULL
                GROUP BY id, numero_documento, tipo_documento, sucursal_id, fecha_emision
                HAVING (numero_documento, tipo_documento, sucursal_id) IN (
                    SELECT numero_documento, tipo_documento, sucursal_id
                    FROM app_dte
                    WHERE sucursal_id IS NOT NULL
                    GROUP BY numero_documento, tipo_documento, sucursal_id
                    HAVING COUNT(*) > 1
                )
                ORDER BY numero_documento, tipo_documento, sucursal_id,
                         fecha_emision ASC NULLS LAST, id ASC
            ''')

            c.execute('SELECT COUNT(*) FROM dte_ganadores')
            grupos = c.fetchone()[0]
            self.stdout.write(f'  Grupos con duplicados: {grupos:,}')

            if grupos == 0:
                c.execute('DROP TABLE IF EXISTS dte_ganadores')
                self.stdout.write(self.style.SUCCESS('\n  Sin duplicados. Nada que hacer.'))
                return

            # =================================================================
            # PASO 2: Crear mapeo duplicado -> ganador
            # =================================================================
            self.stdout.write('\n[2/6] Creando mapeo duplicado -> ganador...')

            c.execute('''
                CREATE TEMP TABLE dte_duplicados AS
                SELECT
                    d.id AS dte_id_duplicado,
                    g.dte_id_ganador,
                    d.fecha_emision AS fecha_duplicado,
                    g.fecha_emision AS fecha_ganador
                FROM app_dte d
                JOIN dte_ganadores g
                    ON d.numero_documento = g.numero_documento
                    AND d.tipo_documento = g.tipo_documento
                    AND d.sucursal_id = g.sucursal_id
                WHERE d.id != g.dte_id_ganador
            ''')

            c.execute('SELECT COUNT(*) FROM dte_duplicados')
            total_duplicados = c.fetchone()[0]
            self.stdout.write(f'  DTEs duplicados a eliminar: {total_duplicados:,}')

            c.execute('''
                SELECT COUNT(*) FROM dte_duplicados
                WHERE EXTRACT(YEAR FROM fecha_duplicado) = 2026
            ''')
            con_fecha_2026 = c.fetchone()[0]
            self.stdout.write(f'  De los cuales con fecha 2026: {con_fecha_2026:,}')

            # =================================================================
            # PASO 3: Mover pagos unicos al DTE ganador
            # =================================================================
            self.stdout.write('\n[3/6] Moviendo pagos al DTE correcto...')

            c.execute('''
                SELECT COUNT(*) FROM app_dte_detalle_pago p
                JOIN dte_duplicados dd ON p.dte_id = dd.dte_id_duplicado
            ''')
            pagos_en_duplicados = c.fetchone()[0]
            self.stdout.write(f'  Pagos en DTEs duplicados: {pagos_en_duplicados:,}')

            if not dry_run and pagos_en_duplicados > 0:
                c.execute('''
                    UPDATE app_dte_detalle_pago p
                    SET dte_id = dd.dte_id_ganador
                    FROM dte_duplicados dd
                    WHERE p.dte_id = dd.dte_id_duplicado
                      AND NOT EXISTS (
                          SELECT 1 FROM app_dte_detalle_pago p2
                          WHERE p2.dte_id = dd.dte_id_ganador
                            AND p2.voucher = p.voucher
                            AND p2.monto = p.monto
                      )
                ''')
                pagos_movidos = c.rowcount
                self.stdout.write(f'  Pagos movidos (unicos): {pagos_movidos:,}')

            # =================================================================
            # PASO 4: Eliminar pagos duplicados que quedaron en DTEs a borrar
            # =================================================================
            self.stdout.write('\n[4/6] Eliminando pagos duplicados...')

            if not dry_run:
                c.execute('''
                    DELETE FROM app_dte_detalle_pago p
                    USING dte_duplicados dd
                    WHERE p.dte_id = dd.dte_id_duplicado
                ''')
                pagos_eliminados = c.rowcount
                self.stdout.write(f'  Pagos duplicados eliminados: {pagos_eliminados:,}')

            # =================================================================
            # PASO 5: Eliminar productos DTE de los duplicados
            # =================================================================
            self.stdout.write('\n[5/6] Eliminando productos DTE de duplicados...')

            if not dry_run:
                c.execute('''
                    DELETE FROM app_dte_productos dp
                    USING dte_duplicados dd
                    WHERE dp.dte_id = dd.dte_id_duplicado
                ''')
                prods_eliminados = c.rowcount
                self.stdout.write(f'  Productos DTE eliminados: {prods_eliminados:,}')

                c.execute('''
                    DELETE FROM app_dte_incidencia di
                    USING dte_duplicados dd
                    WHERE di.dte_id = dd.dte_id_duplicado
                ''')

                c.execute('''
                    DELETE FROM app_notificaciondte n
                    USING dte_duplicados dd
                    WHERE n.dte_id = dd.dte_id_duplicado
                ''')

                c.execute('''
                    DELETE FROM app_dtealertadescartada a
                    USING dte_duplicados dd
                    WHERE a.dte_id = dd.dte_id_duplicado
                ''')

                c.execute('''
                    DELETE FROM app_descuentorecargo dr
                    USING dte_duplicados dd
                    WHERE dr.dte_id = dd.dte_id_duplicado
                ''')

                # Self-FK: documento_afectado_id (Notas de Credito -> DTE padre)
                self.stdout.write('  Limpiando referencias documento_afectado...')
                c.execute('''
                    UPDATE app_dte
                    SET documento_afectado_id = dd.dte_id_ganador
                    FROM dte_duplicados dd
                    WHERE app_dte.documento_afectado_id = dd.dte_id_duplicado
                ''')
                nc_reasignadas = c.rowcount
                if nc_reasignadas:
                    self.stdout.write(f'  NC reasignadas: {nc_reasignadas:,}')

                # Movimientos que referencian DTEs duplicados
                self.stdout.write('  Limpiando movimientos...')
                c.execute('''
                    UPDATE app_movimientos_producto
                    SET dte_id = dd.dte_id_ganador
                    FROM dte_duplicados dd
                    WHERE app_movimientos_producto.dte_id = dd.dte_id_duplicado
                ''')
                mov_reasignados = c.rowcount
                if mov_reasignados:
                    self.stdout.write(f'  Movimientos reasignados: {mov_reasignados:,}')

                # Lotes que referencian DTEs duplicados
                self.stdout.write('  Limpiando lotes...')
                c.execute('''
                    UPDATE app_loteproducto
                    SET dte_id = dd.dte_id_ganador
                    FROM dte_duplicados dd
                    WHERE app_loteproducto.dte_id = dd.dte_id_duplicado
                ''')
                lotes_reasignados = c.rowcount
                if lotes_reasignados:
                    self.stdout.write(f'  Lotes reasignados: {lotes_reasignados:,}')

                # Tablas opcionales: reasignar FKs a dte_id_ganador
                tablas_fk_opcionales = [
                    ('app_productos_recepcionados', 'dte_id'),
                    ('solicitudes_regularizacion', 'dte_original_id'),
                    ('solicitudes_regularizacion', 'dte_solucion_id'),
                ]
                for tabla, columna in tablas_fk_opcionales:
                    try:
                        c.execute(f'''
                            UPDATE {tabla}
                            SET {columna} = dd.dte_id_ganador
                            FROM dte_duplicados dd
                            WHERE {tabla}.{columna} = dd.dte_id_duplicado
                        ''')
                        if c.rowcount:
                            self.stdout.write(f'  {tabla}.{columna}: {c.rowcount} reasignados')
                    except Exception:
                        pass

            # =================================================================
            # PASO 6: Eliminar DTEs duplicados
            # =================================================================
            self.stdout.write('\n[6/6] Eliminando DTEs duplicados...')

            if not dry_run:
                c.execute('''
                    DELETE FROM app_dte
                    WHERE id IN (SELECT dte_id_duplicado FROM dte_duplicados)
                ''')
                dtes_eliminados = c.rowcount
                self.stdout.write(f'  DTEs eliminados: {dtes_eliminados:,}')
            else:
                dtes_eliminados = total_duplicados
                self.stdout.write(f'  DTEs que se eliminarian: {dtes_eliminados:,}')

            # =================================================================
            # LIMPIEZA Y VERIFICACION
            # =================================================================
            c.execute('DROP TABLE IF EXISTS dte_duplicados')
            c.execute('DROP TABLE IF EXISTS dte_ganadores')

            self.stdout.write('\n' + '=' * 70)
            self.stdout.write('  VERIFICACION FINAL')
            self.stdout.write('=' * 70)

            c.execute('SELECT COUNT(*) FROM app_dte')
            self.stdout.write(f'  DTEs totales:    {c.fetchone()[0]:>10,}')

            c.execute('SELECT COUNT(*) FROM app_dte_detalle_pago')
            self.stdout.write(f'  Pagos totales:   {c.fetchone()[0]:>10,}')

            c.execute('SELECT COUNT(*) FROM app_dte_productos')
            self.stdout.write(f'  DTE productos:   {c.fetchone()[0]:>10,}')

            c.execute('''
                SELECT COUNT(*) FROM (
                    SELECT numero_documento, tipo_documento, sucursal_id
                    FROM app_dte
                    WHERE sucursal_id IS NOT NULL
                    GROUP BY numero_documento, tipo_documento, sucursal_id
                    HAVING COUNT(*) > 1
                ) sub
            ''')
            restantes = c.fetchone()[0]
            if restantes > 0:
                self.stdout.write(self.style.WARNING(
                    f'  Duplicados restantes: {restantes:,}'))
            else:
                self.stdout.write(self.style.SUCCESS(
                    '  Duplicados restantes: 0'))

        self.stdout.write(self.style.SUCCESS('\n  COMPLETADO'))
