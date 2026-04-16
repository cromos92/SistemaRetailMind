"""
Elimina DTEs fantasma: documentos creados en PostgreSQL durante la migracion
que NO existen en la tabla `dte` de MySQL.

Compara por (n_documento, tipo_documento_mapeado, bodega_inicio) entre MySQL y PG.
Los folios son unicos por tipo_documento + sucursal, asi que la comparacion
debe incluir los 3 campos.

Uso:
    python manage.py limpiar_dtes_fantasma --dry-run
    python manage.py limpiar_dtes_fantasma
    python manage.py limpiar_dtes_fantasma --sucursal PAO1
"""

import os
from pathlib import Path
from collections import defaultdict

from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import connection as pg_conn


MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')

TIPO_DOC_MAP = {
    'FACTURA ELECTRONICA': 'FACTURA ELECTRONICA',
    'DESPACHO ELECTRONICO': 'GUIA',
    'BOLETA ELECTRONICA': 'BOLETA ELECTRONICA',
    'BOLETA': 'BOLETA PAPEL',
    'NOTA DE CREDITO': 'NOTA DE CREDITO',
    'NOTA DE DEBITO': 'NOTA DE DEBITO',
    'FACTURA EXENTA': 'FACTURA EXENTA',
    'GUIA': 'GUIA',
    'GUIA DESPACHO': 'GUIA',
}


def mapear_tipo(tipo_mysql):
    tipo_upper = (tipo_mysql or '').upper().strip()
    if tipo_upper in TIPO_DOC_MAP:
        return TIPO_DOC_MAP[tipo_upper]
    if 'FACTURA' in tipo_upper and 'EXENTA' not in tipo_upper:
        return 'FACTURA ELECTRONICA'
    if 'EXENTA' in tipo_upper:
        return 'FACTURA EXENTA'
    if 'BOLETA' in tipo_upper and 'ELECTRONICA' in tipo_upper:
        return 'BOLETA ELECTRONICA'
    if 'BOLETA' in tipo_upper:
        return 'BOLETA PAPEL'
    if 'DESPACHO' in tipo_upper or 'GUIA' in tipo_upper:
        return 'GUIA'
    if 'CREDITO' in tipo_upper:
        return 'NOTA DE CREDITO'
    if 'DEBITO' in tipo_upper:
        return 'NOTA DE DEBITO'
    return tipo_upper or 'BOLETA ELECTRONICA'


class Command(BaseCommand):
    help = 'Elimina DTEs fantasma que no existen en MySQL'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Mostrar que se haria sin ejecutar cambios')
        parser.add_argument('--sucursal', type=str, default=None,
                            help='Filtrar por alias de sucursal (ej: PAO1)')
        parser.add_argument('--detalle', action='store_true',
                            help='Mostrar los primeros 20 DTEs fantasma')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        filtro_suc = options['sucursal']
        mostrar_detalle = options['detalle']

        self.stdout.write('=' * 70)
        self.stdout.write('  LIMPIAR DTEs FANTASMA (existen en PG pero NO en MySQL)')
        self.stdout.write('=' * 70)
        if dry_run:
            self.stdout.write(self.style.WARNING('  [DRY-RUN] No se modificara nada\n'))

        # 1) Conectar a MySQL y cargar DTEs reales con tipo mapeado
        self.stdout.write('[1/5] Conectando a MySQL y cargando DTEs reales...')
        mysql_conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            database=MYSQL_DATABASE,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            ssl_disabled=False,
        )
        cursor = mysql_conn.cursor()
        cursor.execute('SELECT n_documento, tipo_documento, bodega_inicio FROM dte')
        dtes_mysql = set()
        for n_doc, tipo_doc, bodega in cursor:
            if n_doc and bodega:
                tipo_pg = mapear_tipo(tipo_doc)
                dtes_mysql.add((int(n_doc), tipo_pg, bodega.strip()))
        cursor.close()
        mysql_conn.close()
        self.stdout.write(f'  OK: {len(dtes_mysql):,} DTEs en MySQL (n_doc + tipo + bodega)')

        # 2) Cargar DTEs de PostgreSQL
        self.stdout.write('[2/5] Cargando DTEs de PostgreSQL...')
        with pg_conn.cursor() as c:
            where_clause = ""
            if filtro_suc:
                where_clause = f"AND s.alias = '{filtro_suc}'"

            c.execute(f'''
                SELECT d.id, d.numero_documento, d.tipo_documento,
                       COALESCE(s.alias, 'SIN_SUCURSAL'), d.fecha_emision,
                       d.monto_con_iva, d.unidades_productos
                FROM app_dte d
                LEFT JOIN app_sucursal s ON d.sucursal_id = s.id
                WHERE 1=1
                {where_clause}
            ''')
            dtes_pg = c.fetchall()

        self.stdout.write(f'  OK: {len(dtes_pg):,} DTEs en PostgreSQL')

        # 3) Identificar fantasmas
        self.stdout.write('[3/5] Identificando DTEs fantasma...')
        fantasma_ids = []
        fantasma_detalle = []
        por_sucursal = defaultdict(int)
        por_tipo = defaultdict(int)

        for dte_id, num_doc, tipo_doc, alias, fecha, monto, unidades in dtes_pg:
            key = (num_doc, tipo_doc, alias)
            if key not in dtes_mysql:
                fantasma_ids.append(dte_id)
                por_sucursal[alias] += 1
                por_tipo[tipo_doc] += 1
                if len(fantasma_detalle) < 20:
                    fantasma_detalle.append({
                        'id': dte_id, 'num': num_doc, 'tipo': tipo_doc,
                        'suc': alias, 'fecha': fecha, 'monto': monto,
                    })

        self.stdout.write(f'  OK: {len(fantasma_ids):,} DTEs fantasma detectados')

        if not fantasma_ids:
            self.stdout.write(self.style.SUCCESS('\n  No hay DTEs fantasma. Todo limpio'))
            return

        self.stdout.write('\n  Desglose por sucursal:')
        for alias, cnt in sorted(por_sucursal.items(), key=lambda x: -x[1]):
            self.stdout.write(f'    {alias:20s} {cnt:>8,}')

        self.stdout.write('\n  Desglose por tipo:')
        for tipo, cnt in sorted(por_tipo.items(), key=lambda x: -x[1]):
            self.stdout.write(f'    {tipo:30s} {cnt:>8,}')

        if mostrar_detalle or dry_run:
            self.stdout.write('\n  Ejemplos de DTEs fantasma:')
            for d in fantasma_detalle:
                self.stdout.write(
                    f'    #{d["num"]} {d["tipo"]:25s} {d["suc"]:8s} '
                    f'{str(d["fecha"])[:10]:10s} ${d["monto"]:>12,}'
                )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'\n  [DRY-RUN] Se eliminarian {len(fantasma_ids):,} DTEs fantasma'
            ))
            return

        # 4) Eliminar dependencias
        self.stdout.write(f'\n[4/5] Eliminando dependencias de {len(fantasma_ids):,} DTEs...')

        batch_size = 5000
        total_pagos = 0
        total_productos = 0
        total_nc_refs = 0
        total_otros = 0

        with pg_conn.cursor() as c:
            for i in range(0, len(fantasma_ids), batch_size):
                batch = fantasma_ids[i:i + batch_size]
                ids_str = ','.join(str(x) for x in batch)

                c.execute(f'''
                    UPDATE app_dte
                    SET documento_afectado_id = NULL
                    WHERE documento_afectado_id IN ({ids_str})
                ''')
                total_nc_refs += c.rowcount

                c.execute(f'DELETE FROM app_dte_detalle_pago WHERE dte_id IN ({ids_str})')
                total_pagos += c.rowcount

                c.execute(f'DELETE FROM app_dte_productos WHERE dte_id IN ({ids_str})')
                total_productos += c.rowcount

                for tabla in ['app_dte_incidencia', 'app_dte_descuento_recargo',
                              'app_solicitudregularizacion', 'app_dtealertadescartada']:
                    try:
                        c.execute(f'DELETE FROM {tabla} WHERE dte_id IN ({ids_str})')
                        total_otros += c.rowcount
                    except Exception:
                        pass

        self.stdout.write(f'  OK: {total_pagos:,} pagos eliminados')
        self.stdout.write(f'  OK: {total_productos:,} productos DTE eliminados')
        if total_nc_refs:
            self.stdout.write(f'  OK: {total_nc_refs:,} referencias NC desvinculadas')
        if total_otros:
            self.stdout.write(f'  OK: {total_otros:,} otros registros eliminados')

        # 5) Eliminar DTEs fantasma
        self.stdout.write(f'[5/5] Eliminando {len(fantasma_ids):,} DTEs fantasma...')

        total_eliminados = 0
        with pg_conn.cursor() as c:
            for i in range(0, len(fantasma_ids), batch_size):
                batch = fantasma_ids[i:i + batch_size]
                ids_str = ','.join(str(x) for x in batch)
                c.execute(f'DELETE FROM app_dte WHERE id IN ({ids_str})')
                total_eliminados += c.rowcount

        self.stdout.write(self.style.SUCCESS(
            f'\n  OK: {total_eliminados:,} DTEs fantasma eliminados correctamente'
        ))
