"""Script temporal para verificar movimientos en MySQL"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Cargar .env
env_path = Path(__file__).parent / 'retailmind' / '.env'
load_dotenv(env_path)

import mysql.connector

try:
    conn = mysql.connector.connect(
        host=os.getenv('MYSQL_HOST'),
        port=int(os.getenv('MYSQL_PORT', 3306)),
        database=os.getenv('MYSQL_DATABASE'),
        user=os.getenv('MYSQL_USER'),
        password=os.getenv('MYSQL_PASSWORD'),
        connection_timeout=30
    )
    print('Conexion MySQL OK')
    
    cursor = conn.cursor(dictionary=True)
    
    # Buscar movimientos por SKUs del producto 5BM00539-125
    skus = [4828359, 4828351, 4828352, 4828353, 4828354, 4828355, 4828356, 4828357, 4828358]
    
    placeholders = ','.join(['%s'] * len(skus))
    cursor.execute(f'''
        SELECT id, codigo_asociado, cantidad, fecha, tipo_movimiento, concepto
        FROM movimiento_productos
        WHERE codigo_asociado IN ({placeholders})
        ORDER BY fecha
        LIMIT 20
    ''', skus)
    
    movimientos = cursor.fetchall()
    print(f'Movimientos encontrados para SKUs del producto 5BM00539-125: {len(movimientos)}')
    for m in movimientos:
        print(f"  ID: {m['id']}, SKU: {m['codigo_asociado']}, Fecha: {m['fecha']}, Tipo: {m['tipo_movimiento']}, Concepto: {m['concepto']}")
    
    # Contar total de movimientos en MySQL
    cursor.execute('SELECT COUNT(*) as total FROM movimiento_productos')
    total = cursor.fetchone()['total']
    print(f'\nTotal movimientos en MySQL: {total}')
    
    # Ver rango de fechas
    cursor.execute('SELECT MIN(fecha) as min_fecha, MAX(fecha) as max_fecha FROM movimiento_productos')
    fechas = cursor.fetchone()
    print(f"Rango fechas: {fechas['min_fecha']} a {fechas['max_fecha']}")
    
    # Buscar si existe la talla table en MySQL con ese articulo
    print('\n--- Buscando articulo 5BM00539-125 en tabla talla ---')
    cursor.execute("SELECT codigo_asociado, articulo, alias, size, stock FROM talla WHERE articulo = '5BM00539-125' LIMIT 10")
    tallas = cursor.fetchall()
    print(f'Tallas encontradas: {len(tallas)}')
    for t in tallas:
        print(f"  SKU: {t['codigo_asociado']}, Articulo: {t['articulo']}, Alias: {t['alias']}, Talla: {t['size']}, Stock: {t['stock']}")
    
    conn.close()
except Exception as e:
    print(f'Error: {e}')
