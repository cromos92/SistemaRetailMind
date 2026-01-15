"""
Comando para identificar y eliminar DTEs en PostgreSQL que no existen en MySQL
según (n_documento, sucursal_direccion, fecha) y sin pagos asociados.

Uso:
    python manage.py limpiar_dtes_sin_mysql --dry-run --fecha 2026-01-13 --sucursal NICK1
    python manage.py limpiar_dtes_sin_mysql --confirm --fecha 2026-01-13 --sucursal NICK1
"""
import os
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
import mysql.connector
from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Dte, Sucursal


env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')


class Command(BaseCommand):
    help = 'Elimina DTEs sin respaldo en MySQL (solo sin pagos)'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Solo listar, no borrar')
        parser.add_argument('--confirm', action='store_true', help='Confirmar eliminación')
        parser.add_argument('--fecha', type=str, help='Filtrar por fecha YYYY-MM-DD')
        parser.add_argument('--sucursal', type=str, help='Filtrar por alias de sucursal (ej: NICK1)')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        confirm = options['confirm']
        fecha = options.get('fecha')
        sucursal_alias = options.get('sucursal')

        if not dry_run and not confirm:
            self.stdout.write(self.style.WARNING('Usa --confirm para eliminar o --dry-run para solo listar.'))
            return

        sucursal = None
        if sucursal_alias:
            sucursal = Sucursal.objects.filter(alias=sucursal_alias).first()
            if not sucursal:
                self.stdout.write(self.style.ERROR(f'Sucursal {sucursal_alias} no encontrada.'))
                return

        fecha_obj = None
        if fecha:
            try:
                fecha_obj = datetime.strptime(fecha, '%Y-%m-%d').date()
            except ValueError:
                self.stdout.write(self.style.ERROR('Fecha inválida. Usa formato YYYY-MM-DD.'))
                return

        # Conectar a MySQL
        try:
            mysql_conn = mysql.connector.connect(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                database=MYSQL_DATABASE,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error MySQL: {e}'))
            return

        # Cargar ventas MySQL para comparar
        cursor = mysql_conn.cursor(dictionary=True)
        query = '''
            SELECT n_documento, sucursal, fecha
            FROM ventas
        '''
        filtros = []
        params = []
        if fecha_obj:
            filtros.append('fecha = %s')
            params.append(fecha_obj)
        if sucursal and sucursal.direccion:
            filtros.append('sucursal = %s')
            params.append(sucursal.direccion)
        if filtros:
            query += ' WHERE ' + ' AND '.join(filtros)

        cursor.execute(query, params)
        ventas_mysql = set()
        for row in cursor:
            ventas_mysql.add((str(row['n_documento']).strip(), row['sucursal'] or '', row['fecha']))
        cursor.close()
        mysql_conn.close()

        # Buscar DTEs candidatos a borrar: sin pagos
        dtes_query = Dte.objects.filter(
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
            dte_asociado__isnull=True
        ).select_related('sucursal')

        if fecha_obj:
            dtes_query = dtes_query.filter(fecha_emision=fecha_obj)
        if sucursal:
            dtes_query = dtes_query.filter(sucursal=sucursal)

        candidatos = []
        for dte in dtes_query:
            suc_dir = dte.sucursal.direccion if dte.sucursal else ''
            key = (str(dte.numero_documento), suc_dir, dte.fecha_emision)
            if key not in ventas_mysql:
                candidatos.append(dte)

        self.stdout.write(self.style.WARNING(f'Candidatos sin MySQL: {len(candidatos)}'))
        for dte in candidatos[:20]:
            self.stdout.write(f'  - DTE #{dte.numero_documento} | {dte.sucursal.alias if dte.sucursal else "N/A"} | {dte.fecha_emision}')
        if len(candidatos) > 20:
            self.stdout.write(f'  ... y {len(candidatos) - 20} más')

        if dry_run:
            return

        # Eliminar candidatos
        with transaction.atomic():
            ids = [d.id for d in candidatos]
            Dte.objects.filter(id__in=ids).delete()
        self.stdout.write(self.style.SUCCESS(f'Eliminados: {len(candidatos)}'))
