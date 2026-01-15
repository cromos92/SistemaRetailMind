"""
Django management command para migrar VENDEDORES desde MySQL (Laravel) a PostgreSQL (Django)

Sistema origen: Laravel + MySQL
Sistema destino: Django + PostgreSQL (RetailMind)

Mapeo:
    MySQL.vendedor.nombres        → Django.Vendedor.nombre
    MySQL.vendedor.rut            → Django.Vendedor.rut
    MySQL.vendedor.bodega         → Django.Vendedor.sucursales (M2M via alias)
    MySQL.vendedor.codigo_interno → Django.Vendedor.codigo_vendedor
    MySQL.vendedor.Fecha          → Django.Vendedor.fecha_nacimiento
    (inferido de sucursal)        → Django.Vendedor.empresa

Uso:
    python manage.py migrate_vendedores
    python manage.py migrate_vendedores --dry-run
"""

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Cargar variables de entorno
env_path = Path(__file__).resolve().parent.parent.parent.parent / '.env'
load_dotenv(env_path)

import mysql.connector
from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Vendedor, Sucursal, Empresa


# ============================================================================
# CONFIGURACIÓN MYSQL
# ============================================================================

MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_PORT = int(os.getenv('MYSQL_PORT', 3306))
MYSQL_DATABASE = os.getenv('MYSQL_DATABASE')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')


# ============================================================================
# MAPEO EMPRESA POR ALIAS (igual que migrate_from_laravel.py)
# ============================================================================

EMPRESA_RUT_MAP = {
    'PAO0': '78503140-7',
    'PAO1': '78503140-7',
    'PAO2': '78503140-7',
    'PAO3': '78503140-7',
    'PAO4': '78503140-7',
    'EDEL': '78503140-7',
    'GILD': '7397811-4',
    'NICK1': '76104936-4',
    'NICK2': '76104936-4',
    'IMP': '76104936-4',
}


class Command(BaseCommand):
    help = 'Migra vendedores desde MySQL Laravel a PostgreSQL Django'

    def __init__(self):
        super().__init__()
        self.mysql_conn = None
        self.dry_run = False
        self.stats = {
            'creados': 0,
            'actualizados': 0,
            'omitidos': 0,
            'errores': 0,
        }
        # Cachés
        self.cache_sucursales = {}
        self.cache_empresas = {}

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simular migración sin guardar datos'
        )

    def handle(self, *args, **options):
        self.dry_run = options.get('dry_run', False)
        start_time = datetime.now()

        if self.dry_run:
            self.stdout.write(self.style.WARNING('[DRY-RUN] Modo simulación activado'))

        # Validar configuración
        if not all([MYSQL_HOST, MYSQL_DATABASE, MYSQL_USER]):
            self.stdout.write(self.style.ERROR('[ERROR] Faltan variables MySQL en .env'))
            self.stdout.write('  Requeridas: MYSQL_HOST, MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD')
            return

        # Conectar a MySQL
        try:
            self.mysql_conn = self.connect_mysql()
            self.stdout.write(self.style.SUCCESS('✓ Conexión MySQL establecida'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Error MySQL: {e}'))
            return

        # Pre-cargar cachés
        self.stdout.write('\n📦 Cargando cachés...')
        self.preload_caches()

        # Migrar vendedores
        try:
            if not self.dry_run:
                with transaction.atomic():
                    self.migrate_vendedores()
            else:
                self.migrate_vendedores()

            # Mostrar resumen
            self.show_summary(start_time)

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'[ERROR CRÍTICO] {e}'))
            import traceback
            traceback.print_exc()
        finally:
            if self.mysql_conn:
                self.mysql_conn.close()

    def connect_mysql(self):
        """Conexión a MySQL"""
        return mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            database=MYSQL_DATABASE,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            connection_timeout=60,
            autocommit=True,
        )

    def preload_caches(self):
        """Pre-carga sucursales y empresas"""
        # Sucursales por alias
        for sucursal in Sucursal.objects.select_related('empresa').all():
            self.cache_sucursales[sucursal.alias] = sucursal
        self.stdout.write(f'  ✓ {len(self.cache_sucursales)} sucursales en caché')

        # Empresas por RUT
        for empresa in Empresa.objects.all():
            self.cache_empresas[empresa.rut] = empresa
        self.stdout.write(f'  ✓ {len(self.cache_empresas)} empresas en caché')

    def migrate_vendedores(self):
        """Migra vendedores desde MySQL"""
        self.stdout.write('\n👥 Migrando vendedores...')

        cursor = self.mysql_conn.cursor(dictionary=True)
        
        # Contar total
        cursor.execute('SELECT COUNT(*) as total FROM vendedores')
        total = cursor.fetchone()['total']
        self.stdout.write(f'  📊 Total en MySQL: {total} vendedores')

        # Obtener vendedores
        cursor.execute('''
            SELECT ID, nombres, rut, sucursal, bodega, codigo_interno, Fecha
            FROM vendedores
            ORDER BY ID
        ''')

        for idx, row in enumerate(cursor, 1):
            try:
                self.process_vendedor(row, idx, total)
            except Exception as e:
                self.stats['errores'] += 1
                self.stdout.write(self.style.ERROR(
                    f'  ✗ Error vendedor ID={row["ID"]}: {e}'
                ))

        cursor.close()

    def process_vendedor(self, row, idx, total):
        """Procesa un vendedor individual"""
        mysql_id = row['ID']
        nombres = (row['nombres'] or '').strip()
        rut = (row['rut'] or '').strip()
        bodega = (row['bodega'] or '').strip()
        codigo_interno = row['codigo_interno']
        fecha = row['Fecha']

        # Validaciones básicas
        if not nombres and not rut:
            self.stats['omitidos'] += 1
            return

        # Buscar sucursal por bodega (alias)
        sucursal = self.cache_sucursales.get(bodega)
        
        # Determinar empresa
        empresa = None
        if sucursal:
            empresa = sucursal.empresa
        else:
            # Intentar por mapeo directo
            rut_empresa = EMPRESA_RUT_MAP.get(bodega)
            if rut_empresa:
                empresa = self.cache_empresas.get(rut_empresa)

        # Generar codigo_vendedor
        codigo_vendedor = str(codigo_interno) if codigo_interno else f'MIG-{mysql_id}'

        if self.dry_run:
            # Modo simulación
            self.stdout.write(
                f'  [{idx}/{total}] {nombres} ({rut}) → '
                f'Sucursal: {bodega}, Empresa: {empresa.nombre if empresa else "N/A"}'
            )
            self.stats['creados'] += 1
            return

        # Buscar si ya existe (por RUT o codigo_vendedor)
        vendedor_existente = None
        if rut:
            vendedor_existente = Vendedor.objects.filter(rut=rut).first()
        if not vendedor_existente and codigo_vendedor:
            vendedor_existente = Vendedor.objects.filter(codigo_vendedor=codigo_vendedor).first()

        if vendedor_existente:
            # Actualizar existente
            updated = False
            
            if not vendedor_existente.nombre and nombres:
                vendedor_existente.nombre = nombres
                updated = True
            
            if not vendedor_existente.fecha_nacimiento and fecha:
                vendedor_existente.fecha_nacimiento = fecha
                updated = True
            
            if empresa and not vendedor_existente.empresa:
                vendedor_existente.empresa = empresa
                updated = True

            # Agregar sucursal si no está asignada
            if sucursal and not vendedor_existente.sucursales.filter(id=sucursal.id).exists():
                vendedor_existente.sucursales.add(sucursal)
                updated = True

            if updated:
                vendedor_existente.save()
                self.stats['actualizados'] += 1
            else:
                self.stats['omitidos'] += 1

            if idx % 10 == 0 or idx == total:
                self.stdout.write(f'  [{idx}/{total}] Actualizado: {nombres}')
        else:
            # Crear nuevo vendedor
            vendedor = Vendedor.objects.create(
                codigo_vendedor=codigo_vendedor,
                nombre=nombres or f'Vendedor {mysql_id}',
                rut=rut or None,
                fecha_nacimiento=fecha,
                empresa=empresa,
                activo=True,
                comision=0,
                correo=None,
            )

            # Asignar sucursal (M2M)
            if sucursal:
                vendedor.sucursales.add(sucursal)

            self.stats['creados'] += 1

            if idx % 10 == 0 or idx == total:
                self.stdout.write(self.style.SUCCESS(
                    f'  [{idx}/{total}] ✓ Creado: {nombres} → {bodega}'
                ))

    def show_summary(self, start_time):
        """Muestra resumen final"""
        elapsed = datetime.now() - start_time

        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('📊 RESUMEN MIGRACIÓN VENDEDORES'))
        self.stdout.write('=' * 60)
        self.stdout.write(f'  ✓ Creados:      {self.stats["creados"]:>5}')
        self.stdout.write(f'  ↻ Actualizados: {self.stats["actualizados"]:>5}')
        self.stdout.write(f'  ○ Omitidos:     {self.stats["omitidos"]:>5}')
        self.stdout.write(f'  ✗ Errores:      {self.stats["errores"]:>5}')
        self.stdout.write('-' * 60)
        self.stdout.write(f'  ⏱️  Tiempo: {elapsed}')
        self.stdout.write('=' * 60)

        if self.dry_run:
            self.stdout.write(self.style.WARNING(
                '\n⚠️  Modo DRY-RUN: No se guardaron cambios'
            ))
            self.stdout.write('   Ejecuta sin --dry-run para aplicar la migración')
