"""
Comando de prueba para validar mapeo de productos_dte (MySQL) -> DTE (PostgreSQL)

Uso:
    python manage.py test_dte_productos_mapping
    python manage.py test_dte_productos_mapping --limit 5000
    python manage.py test_dte_productos_mapping --show-missing 20
"""

import os
from datetime import datetime, date
from pathlib import Path

from dotenv import load_dotenv
import mysql.connector

from django.conf import settings
from django.core.management.base import BaseCommand

from app.models import Dte


# Cargar .env del proyecto
env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')


def normalize_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


class Command(BaseCommand):
    help = 'Testea el mapeo productos_dte -> DTE (sin insertar nada)'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, help='Limitar cantidad de registros')
        parser.add_argument('--show-missing', type=int, default=10, help='Mostrar ejemplos no mapeados')

    def connect_mysql(self):
        return mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            database=MYSQL_DATABASE,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            connection_timeout=600,
            autocommit=True,
            get_warnings=False,
            use_pure=False,
        )

    def _mapear_tipo_documento(self, tipo_mysql):
        tipo_upper = (tipo_mysql or '').upper().strip()
        mapeo_directo = {
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
        if tipo_upper in mapeo_directo:
            return mapeo_directo[tipo_upper]
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
        if 'CREDITO' in tipo_upper or 'NC' in tipo_upper:
            return 'NOTA DE CREDITO'
        if 'DEBITO' in tipo_upper or 'ND' in tipo_upper:
            return 'NOTA DE DEBITO'
        return 'BOLETA ELECTRONICA'

    def handle(self, *args, **options):
        limit = options.get('limit')
        show_missing = options.get('show_missing', 10)

        if not all([MYSQL_HOST, MYSQL_DATABASE, MYSQL_USER]):
            self.stdout.write(self.style.ERROR('[ERROR] Faltan variables MySQL'))
            return

        # Cargar DTEs MySQL por ID
        mysql_conn = self.connect_mysql()
        cursor_mysql_dte = mysql_conn.cursor(dictionary=True, buffered=True)
        cursor_mysql_dte.execute('''
            SELECT ID, n_documento, tipo_documento, bodega_inicio, fecha_emision
            FROM dte
        ''')
        mysql_dtes_by_id = {}
        for row in cursor_mysql_dte:
            mysql_dtes_by_id[row['ID']] = {
                'numero': row['n_documento'],
                'tipo': row['tipo_documento'],
                'alias': row['bodega_inicio'],
                'fecha': row['fecha_emision'],
            }
        cursor_mysql_dte.close()
        self.stdout.write(self.style.SUCCESS(f'✓ DTEs MySQL en cache: {len(mysql_dtes_by_id):,}'))

        # Caches de DTEs PostgreSQL
        cache_dtes_by_num_tipo = {}
        cache_dtes_by_num_tipo_alias = {}
        cache_dtes_by_num_tipo_date = {}
        cache_dtes_by_num_tipo_alias_date = {}
        cache_dtes_by_num = {}
        for dte in Dte.objects.select_related('sucursal').all():
            alias = dte.sucursal.alias if dte.sucursal else None
            fecha_emision = normalize_date(dte.fecha_emision)
            key_num_tipo = (dte.numero_documento, dte.tipo_documento)
            cache_dtes_by_num_tipo[key_num_tipo] = dte
            if alias:
                cache_dtes_by_num_tipo_alias[(dte.numero_documento, dte.tipo_documento, alias)] = dte
            if fecha_emision:
                cache_dtes_by_num_tipo_date[(dte.numero_documento, dte.tipo_documento, fecha_emision)] = dte
            if alias and fecha_emision:
                cache_dtes_by_num_tipo_alias_date[(dte.numero_documento, dte.tipo_documento, alias, fecha_emision)] = dte
            if dte.numero_documento not in cache_dtes_by_num:
                cache_dtes_by_num[dte.numero_documento] = dte

        self.stdout.write(self.style.SUCCESS(f'✓ DTEs PostgreSQL en cache: {len(cache_dtes_by_num_tipo):,}'))

        # Leer productos_dte
        cursor = mysql_conn.cursor(dictionary=True, buffered=True)
        cursor.execute('SELECT COUNT(*) as total FROM productos_dte')
        total = cursor.fetchone()['total']
        if limit and limit < total:
            total = limit

        cursor.execute(f'''
            SELECT ID, factura_asociada, tipo_documento, bodega_inicio, fecha_creacion, IdDte
            FROM productos_dte
            ORDER BY ID
            {f"LIMIT {limit}" if limit else ""}
        ''')

        matched = 0
        matched_by_iddte = 0
        matched_by_factura = 0
        missing = 0
        missing_samples = []

        for row in cursor:
            dte = None
            if not row['IdDte'] and not row['fecha_creacion']:
                missing += 1
                if len(missing_samples) < show_missing:
                    missing_samples.append({
                        'ID': row['ID'],
                        'IdDte': row['IdDte'],
                        'factura_asociada': row['factura_asociada'],
                        'tipo_documento': row['tipo_documento'],
                        'bodega_inicio': row['bodega_inicio'],
                        'fecha_creacion': row['fecha_creacion'],
                        'motivo': 'sin IdDte y sin fecha_creacion',
                    })
                continue

            # 1) IdDte (ID real de MySQL)
            if row['IdDte']:
                mysql_dte = mysql_dtes_by_id.get(row['IdDte'])
                if mysql_dte:
                    numero = mysql_dte['numero']
                    tipo = self._mapear_tipo_documento(mysql_dte['tipo'] or '')
                    alias = mysql_dte['alias']
                    fecha = normalize_date(mysql_dte['fecha'])
                    if numero:
                        dte = cache_dtes_by_num_tipo_alias_date.get((numero, tipo, alias, fecha))
                        if not dte and alias:
                            dte = cache_dtes_by_num_tipo_alias.get((numero, tipo, alias))
                        if not dte and fecha:
                            dte = cache_dtes_by_num_tipo_date.get((numero, tipo, fecha))
                        if not dte:
                            dte = cache_dtes_by_num_tipo.get((numero, tipo))
                        if not dte:
                            dte = cache_dtes_by_num.get(numero)
                if dte:
                    matched += 1
                    matched_by_iddte += 1
                    continue

            # 2) factura_asociada + tipo_documento + bodega_inicio + fecha_creacion
            if row['factura_asociada']:
                numero = row['factura_asociada']
                tipo = self._mapear_tipo_documento(row['tipo_documento'] or '')
                alias = row['bodega_inicio']
                fecha = normalize_date(row['fecha_creacion'])
                dte = cache_dtes_by_num_tipo_alias_date.get((numero, tipo, alias, fecha))
                if not dte and alias:
                    dte = cache_dtes_by_num_tipo_alias.get((numero, tipo, alias))
                if not dte and fecha:
                    dte = cache_dtes_by_num_tipo_date.get((numero, tipo, fecha))
                if not dte:
                    dte = cache_dtes_by_num_tipo.get((numero, tipo))
                if not dte:
                    dte = cache_dtes_by_num.get(numero)

            if dte:
                matched += 1
                matched_by_factura += 1
            else:
                missing += 1
                if len(missing_samples) < show_missing:
                    missing_samples.append({
                        'ID': row['ID'],
                        'IdDte': row['IdDte'],
                        'factura_asociada': row['factura_asociada'],
                        'tipo_documento': row['tipo_documento'],
                        'bodega_inicio': row['bodega_inicio'],
                        'fecha_creacion': row['fecha_creacion'],
                    })

        cursor.close()
        mysql_conn.close()

        self.stdout.write('\n' + self.style.SUCCESS('=== RESULTADO TEST MAPEOS ==='))
        self.stdout.write(f'Total analizados: {total:,}')
        self.stdout.write(f'Mapeados: {matched:,}')
        self.stdout.write(f'  - por IdDte: {matched_by_iddte:,}')
        self.stdout.write(f'  - por factura_asociada: {matched_by_factura:,}')
        self.stdout.write(f'No mapeados: {missing:,}')

        if missing_samples:
            self.stdout.write('\nEjemplos no mapeados:')
            for sample in missing_samples:
                self.stdout.write(f"  - {sample}")

