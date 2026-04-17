"""
Arregla el campo 'concepto' de movimientos migrados desde MySQL.

SEGURIDAD:
- Por defecto: DRY-RUN (solo muestra que cambiaria, NO modifica nada)
- Solo toca movimientos con referencia_externa LIKE 'MIG:%' (migrados de MySQL)
- NO toca movimientos creados por la app Django
- NO toca tipo_movimiento, cantidad, fecha, ni ningun otro campo
- Solo usa valores de concepto que existen en CONCEPTO_MOVIMIENTO_CHOICES
- UPDATE directo en SQL (no dispara signals de Django)

Uso:
  python _fix_conceptos.py              # DRY-RUN (solo muestra)
  python _fix_conceptos.py --apply      # EJECUTA los UPDATES
"""
import os, sys
os.chdir(r'c:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind\retailmind')
sys.path.insert(0, os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django; django.setup()
import time
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import mysql.connector
from django.db import connection, transaction

APPLY = "--apply" in sys.argv
MODE = "APPLY" if APPLY else "DRY-RUN"

# ============================================================
# MAPEO: MySQL concepto -> PG concepto (valores del CHOICES)
# ============================================================
# NOTA: Todos los valores destino existen en CONCEPTO_MOVIMIENTO_CHOICES
MAPEO_CONCEPTOS = {
    # === Ventas al publico ===
    'ventaxpublico':              'VENTA_PUBLICO',
    'ventaxboleta':               'VENTA_PUBLICO',
    'ventaxboletaelectronica':    'VENTA_PUBLICO',
    'ventaxguia':                 'VENTA_PUBLICO',  # Venta con guia de despacho
    'venta_publico':              'VENTA_PUBLICO',  # idempotente

    # === Ventas con factura ===
    'ventaxfactura':              'VENTA_MAYORISTA',
    'venta_mayorista':            'VENTA_MAYORISTA',  # idempotente

    # === Compras a proveedor / Recepcion ===
    'ventaxguiaxprovedor':        'RECEPCION_COMPRA',
    'ventaxfacturaxprovedor':     'RECEPCION_COMPRA',
    'factura-proovedores':        'RECEPCION_COMPRA',
    'factura_proovedores':        'RECEPCION_COMPRA',
    'recepcion_compra':           'RECEPCION_COMPRA',  # idempotente

    # === Traspasos entre sucursales ===
    'ventaxinterna':              'TRASPASO_SUCURSAL',
    'factura_interna':            'TRASPASO_SUCURSAL',
    'facturainterna':             'TRASPASO_SUCURSAL',
    'guia_despacho':              'TRASPASO_SUCURSAL',
    'guiadespacho':               'TRASPASO_SUCURSAL',
    'traspaso_sucursal':          'TRASPASO_SUCURSAL',  # idempotente

    # === Creacion / stock inicial ===
    'creacion':                   'INGRESO_INICIAL',
    'ingreso_inicial':            'INGRESO_INICIAL',  # idempotente
    'ingresoxinicial':            'INGRESO_INICIAL',

    # === Cambios de producto (logica especial abajo) ===

    # === Ajustes ===
    'ajustexinventario':          'AJUSTE_INVENTARIO',
    'ajuste_inventario':          'AJUSTE_INVENTARIO',  # idempotente
    'ajuste_positivo':            'AJUSTE_POSITIVO',
    'ajuste_negativo':            'AJUSTE_NEGATIVO',
    'correccion_stock':           'CORRECCION_STOCK',
    'movimientoxparametrosxmaestros': 'CORRECCION_STOCK',
    'movimientoxconcepto':        'AJUSTE_INVENTARIO',

    # === Devoluciones / Notas de credito ===
    'nota_de_credito':            'DEVOLUCION_CLIENTE',
    'notadecredito':              'DEVOLUCION_CLIENTE',
    'movimientoxnotadecredito':   'DEVOLUCION_CLIENTE',

    # === Anulaciones ===
    'anulacion_ticket':           'ANULACION',
    'anulacion':                  'ANULACION',
    'cancelacion':                'CANCELACION',
    'anular_factura':             'ANULACION',
    'anular_guia':                'ANULACION',
    'anulaxboleta':               'ANULACION',
    'anulacionxdocumento':        'ANULACION',

    # === Genericos (mantener por tipo, no mapear aqui) ===
    # 'egreso', 'ingreso' → se manejan con lógica especial abajo
}

# NOTA: AJUSTE_INVENTARIO no esta oficialmente en CHOICES pero ya hay 1.4M registros con ese valor.
# Lo respetamos para no romper lo que ya existe.


print("=" * 80)
print(f"FIX CONCEPTOS MOVIMIENTOS (modo: {MODE})")
print("=" * 80)
if not APPLY:
    print("  Este script NO modifica nada. Solo muestra lo que cambiaria.")
    print("  Para aplicar: python _fix_conceptos.py --apply")

# ============================================================
# [1] Cargar mapeo de MySQL
# ============================================================
print("\n[1] Cargando conceptos originales de MySQL...")
t0 = time.time()
conn = mysql.connector.connect(
    host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT", 3306)),
    database=os.getenv("MYSQL_DATABASE"), user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"), connection_timeout=300)
cursor = conn.cursor()
cursor.execute("""
    SELECT id, concepto, tipo_movimiento
    FROM movimiento_productos
    WHERE concepto IS NOT NULL
""")
mysql_data = {}  # mysql_id -> (concepto_mysql, tipo_mysql)
for mid, c, t in cursor:
    mysql_data[mid] = (c, t)
cursor.close()
conn.close()
print(f"  [{time.time()-t0:.1f}s] {len(mysql_data):,} movimientos MySQL")

# ============================================================
# [2] Cargar movimientos PG migrados (con MIG:id)
# ============================================================
print("\n[2] Cargando movimientos PG migrados...")
t0 = time.time()
with connection.cursor() as cur:
    cur.execute("""
        SELECT id, substring(referencia_externa, 5)::int as mysql_id, concepto
        FROM app_movimientos_producto
        WHERE referencia_externa LIKE 'MIG:%'
    """)
    pg_movs = [(r[0], r[1], r[2]) for r in cur]
print(f"  [{time.time()-t0:.1f}s] {len(pg_movs):,} movimientos PG con MIG:id")

# ============================================================
# [3] Calcular cambios
# ============================================================
print("\n[3] Calculando cambios...")
t0 = time.time()

from collections import Counter
cambios_por_tipo = Counter()  # (origen, destino) -> count
updates = []  # [(pg_id, nuevo_concepto), ...]
sin_mapeo = Counter()  # concepto_mysql -> count
no_cambian = 0

for pg_id, mysql_id, concepto_actual in pg_movs:
    data = mysql_data.get(mysql_id)
    if not data:
        continue
    concepto_mysql, tipo_mysql = data

    # Normalizar: lowercase, sin espacios
    c_norm = (concepto_mysql or '').lower().strip().replace(' ', '_')

    # Caso especial: cambio de producto (depende del tipo)
    if 'cambio' in c_norm and 'producto' in c_norm:
        if tipo_mysql and tipo_mysql.lower() == 'ingreso':
            nuevo = 'CAMBIO_PRODUCTO_ENTRADA'
        else:
            nuevo = 'CAMBIO_PRODUCTO_SALIDA'
    # Caso especial: Egreso/Ingreso genericos -> AJUSTE
    elif c_norm == 'egreso':
        nuevo = 'AJUSTE_NEGATIVO'
    elif c_norm == 'ingreso':
        nuevo = 'AJUSTE_POSITIVO'
    else:
        nuevo = MAPEO_CONCEPTOS.get(c_norm)

    if not nuevo:
        sin_mapeo[concepto_mysql] += 1
        continue

    if nuevo == concepto_actual:
        no_cambian += 1
        continue

    cambios_por_tipo[(concepto_actual, nuevo)] += 1
    updates.append((pg_id, nuevo))

print(f"  [{time.time()-t0:.1f}s]")
print(f"  A actualizar:  {len(updates):,}")
print(f"  Ya correctos:  {no_cambian:,}")
print(f"  Sin mapeo:     {sum(sin_mapeo.values()):,} ({len(sin_mapeo)} conceptos distintos)")

# Mostrar cambios por tipo
print(f"\n[4] Resumen de cambios (PG actual -> PG nuevo):")
print(f"{'PG Actual':<30} {'PG Nuevo':<30} {'Count':>12}")
print("-" * 80)
for (actual, nuevo), n in sorted(cambios_por_tipo.items(), key=lambda x: -x[1]):
    print(f"{actual:<30} {nuevo:<30} {n:>12,}")

# Conceptos sin mapeo
if sin_mapeo:
    print(f"\n[!] Conceptos MySQL sin mapeo (quedan como estan):")
    for c, n in sin_mapeo.most_common(20):
        print(f"  {c:<40} {n:>10,}")

# ============================================================
# [5] APLICAR UPDATES (si --apply)
# ============================================================
if not APPLY:
    print(f"\n" + "=" * 80)
    print(f"DRY-RUN TERMINADO. Para aplicar los {len(updates):,} cambios:")
    print(f"  python _fix_conceptos.py --apply")
    print("=" * 80)
else:
    if not updates:
        print("\nNo hay cambios que aplicar.")
        sys.exit(0)

    print(f"\n[5] Aplicando {len(updates):,} UPDATES (transaccion)...")
    t0 = time.time()

    # Agrupar por concepto destino para hacer UPDATEs en lotes
    por_concepto = {}
    for pg_id, nuevo in updates:
        por_concepto.setdefault(nuevo, []).append(pg_id)

    total_actualizados = 0
    with transaction.atomic():
        with connection.cursor() as cur:
            for concepto_nuevo, ids in por_concepto.items():
                batch_size = 10000
                for i in range(0, len(ids), batch_size):
                    lote = ids[i:i+batch_size]
                    cur.execute(
                        "UPDATE app_movimientos_producto SET concepto = %s WHERE id = ANY(%s)",
                        [concepto_nuevo, lote]
                    )
                    total_actualizados += cur.rowcount

    print(f"  [{time.time()-t0:.1f}s] UPDATE completado: {total_actualizados:,} registros")

    print(f"\n" + "=" * 80)
    print("OK - Fix de conceptos aplicado")
    print("=" * 80)
