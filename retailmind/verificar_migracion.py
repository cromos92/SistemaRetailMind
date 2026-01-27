"""
Script para verificar migración MySQL -> PostgreSQL
Compara cantidades de registros y stock entre ambas bases de datos
"""
import os
from pathlib import Path

# Cargar variables de entorno desde .env
env_file = Path(__file__).parent / '.env'
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip())

import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

import mysql.connector
from django.db import connection
from django.db.models import Sum, Count
from app.models import (
    Empresa, Sucursal, Vendedor, Producto, Producto_Talla, 
    Movimientos_Producto, Dte, Dte_Productos, Categoria
)

# Intentar importar Atributo si existe
try:
    from app.models import Atributo
except ImportError:
    Atributo = None

# Configuración MySQL
MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'database': os.getenv('MYSQL_DATABASE'),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
}

def get_mysql_connection():
    return mysql.connector.connect(**MYSQL_CONFIG)

def comparar_conteos():
    """Compara conteos de registros entre MySQL y PostgreSQL"""
    print("=" * 80)
    print("📊 COMPARACIÓN DE CONTEOS MySQL vs PostgreSQL")
    print("=" * 80)
    
    mysql_conn = get_mysql_connection()
    mysql_cursor = mysql_conn.cursor(dictionary=True)
    
    comparaciones = [
        # (Nombre, Query MySQL, Modelo Django o Query PG)
        ("Clientes", "SELECT COUNT(*) as total FROM cliente", None),
        ("Sucursales (bodegas)", "SELECT COUNT(DISTINCT bodega) as total FROM bodegas", Sucursal),
        ("Vendedores", "SELECT COUNT(*) as total FROM vendedores", Vendedor),
        ("Categorias (familia)", "SELECT COUNT(*) as total FROM familia", Categoria),
        ("Productos (products)", "SELECT COUNT(*) as total FROM products", Producto),
        ("SKUs (tabla_sku)", "SELECT COUNT(*) as total FROM tabla_sku", Producto_Talla),
        ("Movimientos", "SELECT COUNT(*) as total FROM movimiento_productos", Movimientos_Producto),
        ("DTEs (ventas)", "SELECT COUNT(*) as total FROM ventas", Dte),
        ("DTE Productos", "SELECT COUNT(*) as total FROM productos_dte", Dte_Productos),
    ]
    
    print(f"\n{'Tabla':<35} {'MySQL':>12} {'PostgreSQL':>12} {'Diferencia':>12} {'Estado':<10}")
    print("-" * 85)
    
    total_mysql = 0
    total_pg = 0
    
    for nombre, query_mysql, modelo in comparaciones:
        try:
            # MySQL
            mysql_cursor.execute(query_mysql)
            mysql_count = mysql_cursor.fetchone()['total']
            total_mysql += mysql_count
            
            # PostgreSQL
            if modelo:
                pg_count = modelo.objects.count()
            else:
                pg_count = 0  # Skip si no hay modelo
            total_pg += pg_count
            
            diff = pg_count - mysql_count
            if diff == 0:
                estado = "✓ OK"
            elif diff > 0:
                estado = f"⚠️ +{diff}"
            else:
                estado = f"❌ {diff}"
            
            print(f"{nombre:<35} {mysql_count:>12,} {pg_count:>12,} {diff:>+12,} {estado:<10}")
            
        except Exception as e:
            print(f"{nombre:<35} {'ERROR':>12} {'ERROR':>12} {'-':>12} ❌ {str(e)[:20]}")
    
    print("-" * 85)
    print(f"{'TOTAL':<35} {total_mysql:>12,} {total_pg:>12,} {total_pg - total_mysql:>+12,}")
    
    mysql_cursor.close()
    mysql_conn.close()

def comparar_stock_por_sucursal():
    """Compara movimientos por sucursal entre MySQL y PostgreSQL"""
    print("\n" + "=" * 80)
    print("COMPARACION DE MOVIMIENTOS POR SUCURSAL")
    print("=" * 80)
    
    mysql_conn = get_mysql_connection()
    mysql_cursor = mysql_conn.cursor(dictionary=True)
    
    # MySQL: Movimientos por bodega (alias)
    mysql_cursor.execute("""
        SELECT 
            alias as bodega,
            COUNT(*) as total_movs,
            SUM(CASE WHEN tipo_movimiento = 'Ingreso' THEN cantidad ELSE 0 END) as ingresos,
            SUM(CASE WHEN tipo_movimiento = 'Egreso' THEN cantidad ELSE 0 END) as egresos
        FROM movimiento_productos
        GROUP BY alias
        ORDER BY alias
    """)
    mysql_movs = {row['bodega']: row for row in mysql_cursor.fetchall()}
    
    print(f"\n{'Sucursal':<15} {'MySQL Movs':>12} {'PG Movs':>12} {'MySQL Ingr':>12} {'PG Ingr':>12} {'MySQL Egr':>12} {'PG Egr':>12}")
    print("-" * 100)
    
    from django.db.models import Case, When, IntegerField
    
    for sucursal in Sucursal.objects.all().order_by('alias'):
        mysql_data = mysql_movs.get(sucursal.alias, {'total_movs': 0, 'ingresos': 0, 'egresos': 0})
        
        # PostgreSQL: Contar movimientos
        pg_movs = Movimientos_Producto.objects.filter(sucursal_origen=sucursal).count()
        
        stock_calc = Movimientos_Producto.objects.filter(
            sucursal_origen=sucursal
        ).aggregate(
            ingresos=Sum(Case(
                When(tipo_movimiento='INGRESO', then='cantidad'),
                default=0,
                output_field=IntegerField()
            )),
            egresos=Sum(Case(
                When(tipo_movimiento='EGRESO', then='cantidad'),
                default=0,
                output_field=IntegerField()
            ))
        )
        
        pg_ing = stock_calc['ingresos'] or 0
        pg_egr = stock_calc['egresos'] or 0
        my_ing = mysql_data['ingresos'] or 0
        my_egr = mysql_data['egresos'] or 0
        
        print(f"{sucursal.alias:<15} {mysql_data['total_movs']:>12,} {pg_movs:>12,} {my_ing:>12,} {pg_ing:>12,} {my_egr:>12,} {pg_egr:>12,}")
    
    mysql_cursor.close()
    mysql_conn.close()

def comparar_ventas_totales():
    """Compara totales de ventas entre MySQL y PostgreSQL"""
    print("\n" + "=" * 80)
    print("COMPARACION DE VENTAS/DTEs TOTALES")
    print("=" * 80)
    
    mysql_conn = get_mysql_connection()
    mysql_cursor = mysql_conn.cursor(dictionary=True)
    
    # MySQL: Total ventas (usando monto_pagado como total)
    mysql_cursor.execute("""
        SELECT 
            COUNT(*) as total_docs,
            SUM(monto_pagado) as monto_total,
            COUNT(DISTINCT n_documento) as folios_unicos
        FROM ventas
    """)
    mysql_ventas = mysql_cursor.fetchone()
    
    # PostgreSQL (usando monto_con_iva como total)
    pg_ventas = Dte.objects.aggregate(
        total_docs=Count('id'),
        monto_total=Sum('monto_con_iva'),
    )
    pg_folios = Dte.objects.values('numero_documento').distinct().count()
    
    print(f"\n{'Metrica':<30} {'MySQL':>18} {'PostgreSQL':>18} {'Diferencia':>15}")
    print("-" * 85)
    
    metricas = [
        ("Total documentos", mysql_ventas['total_docs'], pg_ventas['total_docs']),
        ("Folios unicos", mysql_ventas['folios_unicos'], pg_folios),
        ("Monto total ($)", mysql_ventas['monto_total'] or 0, pg_ventas['monto_total'] or 0),
    ]
    
    for nombre, mysql_val, pg_val in metricas:
        diff = (pg_val or 0) - (mysql_val or 0)
        print(f"{nombre:<30} {mysql_val:>18,} {pg_val:>18,} {diff:>+15,}")
    
    mysql_cursor.close()
    mysql_conn.close()

def comparar_productos_detalle():
    """Compara productos en detalle"""
    print("\n" + "=" * 80)
    print("COMPARACION DE PRODUCTOS Y SKUs")
    print("=" * 80)
    
    mysql_conn = get_mysql_connection()
    mysql_cursor = mysql_conn.cursor(dictionary=True)
    
    # MySQL: SKUs en tabla_sku (productos agrupados por codigo_asociado)
    mysql_cursor.execute("SELECT COUNT(DISTINCT codigo_asociado) as total FROM tabla_sku")
    mysql_productos_agrupados = mysql_cursor.fetchone()['total']
    
    mysql_cursor.execute("SELECT COUNT(*) as total FROM tabla_sku")
    mysql_skus = mysql_cursor.fetchone()['total']
    
    # PostgreSQL
    pg_productos = Producto.objects.count()
    pg_skus = Producto_Talla.objects.count()
    pg_skus_unicos = Producto_Talla.objects.values('sku').distinct().count()
    
    print(f"\n{'Metrica':<40} {'MySQL':>15} {'PostgreSQL':>15}")
    print("-" * 75)
    print(f"{'Productos agrupados (codigo_asociado)':<40} {mysql_productos_agrupados:>15,} {pg_productos:>15,}")
    print(f"{'SKUs (tabla_sku/producto_talla)':<40} {mysql_skus:>15,} {pg_skus:>15,}")
    print(f"{'SKUs unicos PG (sku)':<40} {'-':>15} {pg_skus_unicos:>15,}")
    
    mysql_cursor.close()
    mysql_conn.close()

def verificar_integridad():
    """Verifica integridad de datos en PostgreSQL"""
    print("\n" + "=" * 80)
    print("🔍 VERIFICACIÓN DE INTEGRIDAD PostgreSQL")
    print("=" * 80)
    
    verificaciones = [
        ("DTEs sin sucursal", Dte.objects.filter(sucursal__isnull=True).count()),
        ("DTEs sin fecha_emision", Dte.objects.filter(fecha_emision__isnull=True).count()),
        ("Movimientos sin sucursal", Movimientos_Producto.objects.filter(sucursal_origen__isnull=True).count()),
        ("Movimientos sin producto", Movimientos_Producto.objects.filter(ProductoTalla__isnull=True).count()),
        ("Productos sin categoria", Producto.objects.filter(categoria__isnull=True).count()),
        ("SKUs sin producto padre", Producto_Talla.objects.filter(producto__isnull=True).count()),
        ("Sucursales sin empresa", Sucursal.objects.filter(empresa__isnull=True).count()),
    ]
    
    print(f"\n{'Verificación':<45} {'Cantidad':>12} {'Estado':<10}")
    print("-" * 70)
    
    for nombre, cantidad in verificaciones:
        estado = "✓ OK" if cantidad == 0 else f"⚠️ Revisar"
        print(f"{nombre:<45} {cantidad:>12,} {estado:<10}")

def main():
    # Configurar encoding para Windows
    import sys
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    
    print("\n" + "=" * 80)
    print("   VERIFICACION DE MIGRACION MySQL -> PostgreSQL")
    print("=" * 80)
    
    try:
        comparar_conteos()
        comparar_productos_detalle()
        comparar_ventas_totales()
        comparar_stock_por_sucursal()
        verificar_integridad()
        
        print("\n" + "=" * 80)
        print("[OK] VERIFICACION COMPLETADA")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
