"""Verificar si los SKUs de bolsas tienen flag de exclusion en MySQL."""
import os, sys
os.chdir(r'c:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind\retailmind')
sys.path.insert(0, os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django; django.setup()
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import mysql.connector

conn = mysql.connector.connect(
    host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
    database=os.getenv("MYSQL_DATABASE"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"))
cursor = conn.cursor(dictionary=True)

# ============================================================
# [1] Columnas de MySQL.talla
# ============================================================
print("=" * 80)
print("[1] Columnas tabla `talla` (MySQL)")
print("=" * 80)
cursor.execute("DESCRIBE talla")
columnas_talla = []
for r in cursor:
    columnas_talla.append(r['Field'])
    # Resaltar columnas sospechosas de exclusion
    nombre = r['Field'].lower()
    marker = ""
    if any(k in nombre for k in ['excl', 'activ', 'descart', 'estado', 'vigen', 'borr', 'delet', 'anul']):
        marker = "  <-- sospechoso"
    print(f"  {r['Field']:<35} {r['Type']}{marker}")

# ============================================================
# [2] Columnas de MySQL.movimiento_productos
# ============================================================
print("\n" + "=" * 80)
print("[2] Columnas tabla `movimiento_productos` (MySQL)")
print("=" * 80)
cursor.execute("DESCRIBE movimiento_productos")
for r in cursor:
    nombre = r['Field'].lower()
    marker = ""
    if any(k in nombre for k in ['excl', 'activ', 'descart', 'estado', 'vigen', 'borr', 'delet', 'anul']):
        marker = "  <-- sospechoso"
    print(f"  {r['Field']:<35} {r['Type']}{marker}")

# ============================================================
# [3] Valor de columnas sospechosas para BOLSAS
# ============================================================
print("\n" + "=" * 80)
print("[3] Buscar SKUs 'bolsa' y sus estados en MySQL")
print("=" * 80)

# Los SKUs problema
SKUS_BOLSAS = [4812816, 4812815, 4744021, 4744022, 4790546, 4790652, 4803316]

# Traer TODO el registro de esos SKUs
placeholders = ','.join(['%s'] * len(SKUS_BOLSAS))
cursor.execute(f"""
    SELECT *
    FROM talla
    WHERE codigo_asociado IN ({placeholders})
    LIMIT 30
""", SKUS_BOLSAS)

rows_talla = list(cursor)
print(f"\nRegistros en MySQL.talla para SKUs bolsas: {len(rows_talla)}")
if rows_talla:
    # Mostrar solo columnas relevantes y las sospechosas
    cols_mostrar = ['codigo_asociado', 'articulo', 'alias', 'stock']
    # Agregar sospechosas
    for c in ['estado', 'activo', 'excluir', 'descartado', 'vigente', 'borrado', 'eliminado']:
        if c in columnas_talla:
            cols_mostrar.append(c)

    print(f"\nColumnas mostradas: {cols_mostrar}")
    for r in rows_talla[:15]:
        valores = {c: r.get(c) for c in cols_mostrar if c in r}
        print(f"  {valores}")

# ============================================================
# [4] Ver concepto y tipo de movimientos faltantes de esos SKUs
# ============================================================
print("\n" + "=" * 80)
print("[4] Tipos/conceptos de movimientos de esos SKUs en MySQL")
print("=" * 80)

cursor.execute(f"""
    SELECT codigo_asociado,
           concepto, tipo_movimiento,
           COUNT(*) as n
    FROM movimiento_productos
    WHERE codigo_asociado IN ({placeholders})
    GROUP BY codigo_asociado, concepto, tipo_movimiento
    ORDER BY n DESC
    LIMIT 30
""", SKUS_BOLSAS)

print(f"\n{'SKU':>10} {'Concepto':<30} {'Tipo':<15} {'Count':>8}")
print("-" * 70)
for r in cursor:
    print(f"{r['codigo_asociado']:>10} {r['concepto']:<30} {r['tipo_movimiento']:<15} {r['n']:>8,}")

# ============================================================
# [5] Ver articulo de estos SKUs
# ============================================================
print("\n" + "=" * 80)
print("[5] Que son estos productos?")
print("=" * 80)
cursor.execute(f"""
    SELECT DISTINCT codigo_asociado, articulo, descripcion, marca
    FROM talla WHERE codigo_asociado IN ({placeholders})
""", SKUS_BOLSAS)
for r in cursor:
    print(f"  SKU {r['codigo_asociado']}: {r['articulo']} - {r['descripcion'] or '?'} "
          f"(marca: {r['marca'] or '?'})")

cursor.close()
conn.close()
