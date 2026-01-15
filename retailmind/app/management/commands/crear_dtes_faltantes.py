"""
Django management command para crear DTEs genéricos desde ventas MySQL
que no tienen DTE correspondiente en PostgreSQL.

Uso:
    python manage.py crear_dtes_faltantes --dry-run
    python manage.py crear_dtes_faltantes
    python manage.py crear_dtes_faltantes --tipo "Boleta"
    python manage.py crear_dtes_faltantes --tipo "Boleta Electronica"
"""

import os
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Sucursal, Dte, Vendedor, Empresa


MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')

# Mapeo de tipo documento MySQL → PostgreSQL
TIPO_DOC_MAP = {
    'Factura Electronica': 'FACTURA ELECTRONICA',
    'Boleta Electronica': 'BOLETA ELECTRONICA',
    'Boleta': 'BOLETA PAPEL',
    'Nota de Credito': 'NOTA DE CREDITO',
}

# Mapeo tipo_documento → tipo_transaccion
TIPO_TRANSACCION_MAP = {
    'BOLETA ELECTRONICA': 'VENTA_PUBLICO',
    'BOLETA PAPEL': 'VENTA_PUBLICO',
    'FACTURA ELECTRONICA': 'VENTA',
    'NOTA DE CREDITO': 'NOTA_CREDITO',
}


class Command(BaseCommand):
    help = 'Crea DTEs genéricos para ventas MySQL sin DTE en PostgreSQL'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Solo mostrar qué se crearía')
        parser.add_argument('--batch-size', type=int, default=1000, help='Tamaño del batch')
        parser.add_argument('--tipo', type=str, help='Filtrar por tipo: "Boleta" o "Boleta Electronica"')

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.batch_size = options['batch_size']
        self.tipo_filtro = options.get('tipo')
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('[DRY-RUN] No se guardarán cambios'))
        
        # Conectar a MySQL
        self.stdout.write('[*] Conectando a MySQL...')
        try:
            self.mysql_conn = mysql.connector.connect(
                host=MYSQL_HOST,
                port=MYSQL_PORT,
                database=MYSQL_DATABASE,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD
            )
            self.stdout.write(self.style.SUCCESS('  [OK] Conectado a MySQL'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  [ERROR] MySQL: {e}'))
            return

        self.crear_dtes()
        
        self.mysql_conn.close()
        self.stdout.write(self.style.SUCCESS('\n[COMPLETADO] Creación de DTEs finalizada'))

    def crear_dtes(self):
        """Crea DTEs genéricos para ventas sin DTE"""
        self.stdout.write('\n' + '=' * 70)
        self.stdout.write('[DTEs] CREANDO DTEs GENÉRICOS DESDE VENTAS...')
        self.stdout.write('=' * 70)

        # Obtener emisor por defecto (primera empresa)
        self.stdout.write('  [+] Cargando emisor...')
        emisor = Empresa.objects.first()
        if not emisor:
            self.stdout.write(self.style.ERROR('  [ERROR] No hay empresas registradas'))
            return
        self.stdout.write(f'  [OK] Emisor: {emisor.razon_social}')

        # Pre-cargar sucursales por dirección
        self.stdout.write('  [+] Cargando sucursales...')
        cache_sucursales = {}
        for sucursal in Sucursal.objects.all():
            if sucursal.direccion:
                cache_sucursales[sucursal.direccion] = sucursal
        self.stdout.write(f'  [OK] {len(cache_sucursales)} sucursales')

        # Pre-cargar vendedores por código
        self.stdout.write('  [+] Cargando vendedores...')
        cache_vendedores = {}
        for vendedor in Vendedor.objects.all():
            if vendedor.codigo_vendedor:
                cache_vendedores[vendedor.codigo_vendedor] = vendedor
        self.stdout.write(f'  [OK] {len(cache_vendedores)} vendedores')

        # Pre-cargar DTEs existentes por (numero_documento, monto)
        self.stdout.write('  [+] Cargando DTEs existentes...')
        dtes_existentes = set()
        for dte in Dte.objects.values_list('numero_documento', 'monto_con_iva'):
            monto = int(dte[1] or 0)
            dtes_existentes.add((dte[0], monto))
        self.stdout.write(f'  [OK] {len(dtes_existentes)} DTEs existentes')

        # Obtener ventas únicas de MySQL que no tienen DTE
        cursor = self.mysql_conn.cursor(dictionary=True, buffered=True)
        
        # Filtro por tipo si se especificó
        tipo_where = ""
        if self.tipo_filtro:
            tipo_where = f"WHERE tipo_documento = '{self.tipo_filtro}'"
        
        # Agrupar por (n_documento, sub_total, tipo_documento, sucursal, fecha)
        # para obtener ventas únicas
        cursor.execute(f'''
            SELECT 
                n_documento,
                tipo_documento,
                sub_total,
                sucursal,
                MIN(fecha) as fecha,
                MIN(codigo_vendedor) as codigo_vendedor,
                MIN(nombre_vendedor) as nombre_vendedor
            FROM ventas
            {tipo_where}
            GROUP BY n_documento, sub_total, tipo_documento, sucursal
            ORDER BY fecha
        ''')
        
        ventas_mysql = cursor.fetchall()
        self.stdout.write(f'  [i] Ventas únicas en MySQL: {len(ventas_mysql):,}')

        # Filtrar solo las que no existen en PostgreSQL
        count = 0
        omitidos = 0
        sin_sucursal = 0
        batch = []

        for venta in ventas_mysql:
            n_doc = venta['n_documento']
            sub_total = int(venta['sub_total'] or 0)
            
            # Verificar si ya existe
            if (n_doc, sub_total) in dtes_existentes:
                omitidos += 1
                continue
            
            # Obtener sucursal
            sucursal = cache_sucursales.get(venta['sucursal'])
            if not sucursal:
                sin_sucursal += 1
                # Tomar la primera sucursal como fallback
                if cache_sucursales:
                    sucursal = list(cache_sucursales.values())[0]
                else:
                    continue
            
            # Mapear tipo de documento
            tipo_mysql = venta['tipo_documento'] or 'Boleta'
            tipo_pg = TIPO_DOC_MAP.get(tipo_mysql, 'BOLETA PAPEL')
            tipo_transaccion = TIPO_TRANSACCION_MAP.get(tipo_pg, 'VENTA_PUBLICO')
            
            # Obtener vendedor
            vendedor = None
            if venta['codigo_vendedor']:
                vendedor = cache_vendedores.get(venta['codigo_vendedor'])
            
            # Crear DTE genérico
            if not self.dry_run:
                fecha = venta['fecha']
                batch.append(Dte(
                    numero_documento=n_doc,
                    tipo_documento=tipo_pg,
                    tipo_transaccion=tipo_transaccion,
                    monto_neto=int(sub_total / 1.19),  # Calcular neto aproximado
                    monto_con_iva=sub_total,
                    fecha_emision=fecha,
                    fecha_vencimiento=fecha,  # Misma fecha para boletas/ventas al contado
                    sucursal=sucursal,
                    vendedor=vendedor,
                    emisor=emisor,
                    estado_dte='EMITIDO',
                    estado_pago='PAGADO',
                    bultos=0,
                    unidades_productos=0,
                    diasCredito=0
                ))
                dtes_existentes.add((n_doc, sub_total))
                
                if len(batch) >= self.batch_size:
                    Dte.objects.bulk_create(batch, ignore_conflicts=True)
                    count += len(batch)
                    batch = []
                    self.stdout.write(f'  Creados: {count:,}...')
            else:
                count += 1

        # Guardar batch final
        if batch and not self.dry_run:
            Dte.objects.bulk_create(batch, ignore_conflicts=True)
            count += len(batch)

        cursor.close()

        self.stdout.write(self.style.SUCCESS(f'\n  [OK] DTEs creados: {count:,}'))
        self.stdout.write(f'  [i] Ya existían (omitidos): {omitidos:,}')
        self.stdout.write(f'  [!] Sin sucursal (usaron fallback): {sin_sucursal:,}')
