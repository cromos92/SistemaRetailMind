"""
Resetea TODAS las secuencias de PostgreSQL a MAX(id)+1 de cada tabla.

CRITICO despues de migracion masiva: sin esto, el sistema dara error
'duplicate key value' al crear registros nuevos porque las secuencias
no se actualizaron durante los bulk_create/INSERT con IDs explicitos.

Este script es SEGURO: solo ejecuta SELECT setval(), no modifica datos.

Uso:
  python _reset_secuencias.py          # Ver que se haria (dry-run)
  python _reset_secuencias.py --apply  # Aplicar reset
"""
import os, sys
os.chdir(r'c:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind\retailmind')
sys.path.insert(0, os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django; django.setup()

from django.db import connection

APPLY = "--apply" in sys.argv
MODE = "APPLY" if APPLY else "DRY-RUN"

print("=" * 80)
print(f"RESET SECUENCIAS PostgreSQL (modo: {MODE})")
print("=" * 80)

# ============================================================
# [1] Buscar todas las secuencias del schema public
# ============================================================
print("\n[1] Descubriendo tablas con secuencias...")
with connection.cursor() as cur:
    # Query: todas las columnas que usan nextval()
    cur.execute("""
        SELECT
            c.table_schema,
            c.table_name,
            c.column_name,
            pg_get_serial_sequence(c.table_schema || '.' || c.table_name, c.column_name) as seq_name
        FROM information_schema.columns c
        WHERE c.column_default LIKE 'nextval%'
          AND c.table_schema = 'public'
        ORDER BY c.table_name, c.column_name
    """)
    secuencias = cur.fetchall()

print(f"  Encontradas: {len(secuencias)} secuencias")

# ============================================================
# [2] Para cada secuencia: calcular MAX y nuevo valor
# ============================================================
print("\n[2] Calculando nuevos valores...")
print(f"\n{'Tabla':<50} {'Col':<12} {'MAX actual':>12} {'Seq actual':>12} {'Accion'}")
print("-" * 110)

resets = []
for schema, tabla, col, seq_name in secuencias:
    if not seq_name:
        continue

    with connection.cursor() as cur:
        # MAX(id) actual
        cur.execute(f'SELECT COALESCE(MAX("{col}"), 0) FROM "{schema}"."{tabla}"')
        max_id = cur.fetchone()[0]

        # Valor actual de la secuencia
        cur.execute(f"SELECT last_value, is_called FROM {seq_name}")
        last_value, is_called = cur.fetchone()

    seq_actual = last_value if is_called else 0
    nuevo_valor = max_id + 1

    # Solo actualizar si la secuencia esta por debajo del max
    if seq_actual <= max_id:
        accion = f"setval -> {nuevo_valor:,}"
        resets.append((seq_name, nuevo_valor, tabla, col))
    else:
        accion = "OK (sin cambio)"

    print(f"{tabla:<50} {col:<12} {max_id:>12,} {seq_actual:>12,} {accion}")

print(f"\n  Secuencias a resetear: {len(resets)}")

# ============================================================
# [3] APPLY
# ============================================================
if not APPLY:
    print(f"\n" + "=" * 80)
    print(f"DRY-RUN. Para aplicar: python _reset_secuencias.py --apply")
    print("=" * 80)
else:
    if not resets:
        print("\nNo hay secuencias que resetear. Todas estan actualizadas.")
        sys.exit(0)

    print(f"\n[3] Aplicando reset a {len(resets)} secuencias...")
    with connection.cursor() as cur:
        for seq_name, valor, tabla, col in resets:
            cur.execute(f"SELECT setval(%s, %s, false)", [seq_name, valor])
    print(f"\nOK - Secuencias reseteadas")
    print("=" * 80)
