"""
Corrige el campo sobreprecio de productos migrados desde Laravel/MySQL.

El script original de migración calculó:
    sobreprecio = precioventapublico - costo  (INCORRECTO)

El valor correcto debería ser:
    sobreprecio = preciointerno - costo

Esto causaba que P. Interno (costo + sobreprecio) mostrara el precio de venta
al público en lugar del precio interno real.

Uso:
    python manage.py fix_sobreprecio_from_mysql --dry-run   # ver cambios sin aplicar
    python manage.py fix_sobreprecio_from_mysql              # aplicar corrección
"""

import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Producto


MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')


class Command(BaseCommand):
    help = 'Corrige sobreprecio usando preciointerno de MySQL en lugar de precioventapublico'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Mostrar cambios sin aplicar')
        parser.add_argument('--batch-size', type=int, default=500,
                            help='Tamaño del lote para bulk_update')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']

        if dry_run:
            self.stdout.write(self.style.WARNING('=== MODO DRY-RUN: No se aplicarán cambios ==='))

        self.stdout.write('Conectando a MySQL...')
        try:
            conn = mysql.connector.connect(
                host=MYSQL_HOST, port=MYSQL_PORT, database=MYSQL_DATABASE,
                user=MYSQL_USER, password=MYSQL_PASSWORD,
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'No se pudo conectar a MySQL: {e}'))
            return

        cursor = conn.cursor(dictionary=True, buffered=True)

        query = '''
            SELECT
                articulo, alias,
                MIN(costo) as costo,
                MIN(preciointerno) as preciointerno,
                MIN(precioventapublico) as precioventa
            FROM talla
            WHERE articulo IS NOT NULL
            GROUP BY articulo, marca, color, descripcion, sexo, familia, alias
            ORDER BY articulo
        '''
        cursor.execute(query)
        rows_mysql = cursor.fetchall()
        self.stdout.write(f'Filas MySQL: {len(rows_mysql):,}')

        mysql_lookup = {}
        for row in rows_mysql:
            art = row['articulo']
            alias = row['alias']
            costo_mysql = int(row['costo'] or 0)
            pi = int(row['preciointerno'] or 0)
            pv = int(row['precioventa'] or 0)
            key = (art, alias)
            if key not in mysql_lookup:
                mysql_lookup[key] = {
                    'costo': costo_mysql,
                    'preciointerno': pi,
                    'precioventa': pv,
                }

        cursor.close()
        conn.close()
        self.stdout.write(f'Lookup MySQL construido: {len(mysql_lookup):,} claves únicas')

        productos = Producto.objects.select_related('sucursal').all()
        total = productos.count()
        self.stdout.write(f'Productos en Django: {total:,}')

        corregidos = 0
        sin_match = 0
        ya_correctos = 0
        sin_preciointerno = 0
        batch_update = []
        ejemplos = []

        for prod in productos.iterator(chunk_size=2000):
            alias = prod.sucursal.alias if prod.sucursal else None
            key = (prod.articulo, alias)
            mysql_data = mysql_lookup.get(key)

            if not mysql_data:
                sin_match += 1
                continue

            pi = mysql_data['preciointerno']
            if pi <= 0:
                sin_preciointerno += 1
                continue

            nuevo_sobreprecio = max(0, pi - prod.costo)

            if prod.sobreprecio == nuevo_sobreprecio:
                ya_correctos += 1
                continue

            viejo_sp = prod.sobreprecio
            viejo_pinterno = prod.costo + prod.sobreprecio
            nuevo_pinterno = prod.costo + nuevo_sobreprecio

            if len(ejemplos) < 15:
                ejemplos.append({
                    'id': prod.id,
                    'art': prod.articulo[:20],
                    'alias': alias,
                    'costo': prod.costo,
                    'sp_viejo': viejo_sp,
                    'sp_nuevo': nuevo_sobreprecio,
                    'pi_viejo': viejo_pinterno,
                    'pi_nuevo': nuevo_pinterno,
                    'pv': prod.precioventa,
                })

            if not dry_run:
                prod.sobreprecio = nuevo_sobreprecio
                batch_update.append(prod)
                if len(batch_update) >= batch_size:
                    Producto.objects.bulk_update(batch_update, ['sobreprecio'])
                    batch_update = []

            corregidos += 1

        if batch_update and not dry_run:
            Producto.objects.bulk_update(batch_update, ['sobreprecio'])

        self.stdout.write('')
        self.stdout.write('=' * 100)
        self.stdout.write('RESULTADO:')
        self.stdout.write(f'  Corregidos:          {corregidos:,}')
        self.stdout.write(f'  Ya correctos:        {ya_correctos:,}')
        self.stdout.write(f'  Sin match en MySQL:  {sin_match:,}')
        self.stdout.write(f'  Sin preciointerno:   {sin_preciointerno:,}')
        self.stdout.write('')

        if ejemplos:
            self.stdout.write('EJEMPLOS DE CORRECCIÓN:')
            header = '{:<6} {:<20} {:<8} {:>8} {:>10} {:>10} {:>10} {:>10} {:>10}'.format(
                'ID', 'Articulo', 'Alias', 'Costo',
                'SP.Viejo', 'SP.Nuevo', 'PI.Viejo', 'PI.Nuevo', 'P.Venta'
            )
            self.stdout.write(header)
            self.stdout.write('-' * len(header))
            for e in ejemplos:
                self.stdout.write('{:<6} {:<20} {:<8} {:>8} {:>10} {:>10} {:>10} {:>10} {:>10}'.format(
                    e['id'], e['art'], e['alias'] or '-', e['costo'],
                    e['sp_viejo'], e['sp_nuevo'], e['pi_viejo'], e['pi_nuevo'], e['pv']
                ))

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'\nDRY-RUN: {corregidos:,} productos se corregirían. '
                f'Ejecutar sin --dry-run para aplicar.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'\n{corregidos:,} productos corregidos exitosamente.'
            ))
