"""
ANÁLISIS DE CUADRATURA DE MOVIMIENTOS
MySQL (Laravel: dbHoldingTebes.movimiento_productos)
   vs
PostgreSQL (Django: retail.app_movimientos_producto)

Genera un reporte lado a lado para detectar:
  - Totales globales
  - Movimientos por sucursal
  - Movimientos por tipo (Ingreso / Egreso / Traspaso)
  - Movimientos por concepto mapeado
  - Movimientos por año / mes
  - Diferencias absolutas y porcentuales
  - Movimientos omitidos (SKUs no encontrados)
"""

import os
import sys
from collections import defaultdict
from decimal import Decimal

# Cargar .env manualmente sin depender de python-dotenv (que falla con QZ_PRIVATE_KEY multilinea)
ENV_PATH = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            # Saltar líneas del certificado
            if line.startswith('-----') or line.startswith('MII') or 'CERTIFICATE' in line:
                continue
            key, _, value = line.partition('=')
            os.environ.setdefault(key.strip(), value.strip())

try:
    import mysql.connector
except ImportError:
    print('[ERROR] Falta mysql-connector-python. Instalar con: pip install mysql-connector-python')
    sys.exit(1)

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print('[ERROR] Falta psycopg2. Instalar con: pip install psycopg2-binary')
    sys.exit(1)


# ============================================================================
# MAPEOS (deben coincidir con migrate_from_laravel.py)
# ============================================================================

TIPO_MOVIMIENTO_MAP = {
    'Ingreso': 'INGRESO',
    'Egreso': 'EGRESO',
    'Egresso': 'EGRESO',
    'Traspaso': 'TRASPASO',
}

CONCEPTO_MAP = {
    'Creacion': 'INGRESO_INICIAL',
    'Creacion Productos': 'INGRESO_INICIAL',
    'Compra': 'RECEPCION_COMPRA',
    'Devolucion': 'DEVOLUCION_CLIENTE',
    'Venta': 'VENTA_PUBLICO',
    'VentaXPublico': 'VENTA_PUBLICO',
    'VentaxBoleta': 'VENTA_PUBLICO',
    'VentaxFactura': 'VENTA_MAYORISTA',
    'VentaXInterna': 'TRASPASO_SUCURSAL',
    'Concepto': 'TRASPASO_SUCURSAL',
    'MovimientoxCambioProducto': '_CAMBIO_PRODUCTO',
    'MovimientoxNotaDeCredito': 'DEVOLUCION_CLIENTE',
    'Ajuste': '_AJUSTE',
    'MovimientoxParametrosxMaestros': 'CORRECCION_STOCK',
    'EdicionxConcepto': 'CORRECCION_STOCK',
}


def resolver_concepto(concepto_mysql, tipo_mysql):
    base = CONCEPTO_MAP.get(concepto_mysql, 'CORRECCION_STOCK')
    tipo = TIPO_MOVIMIENTO_MAP.get(tipo_mysql, 'INGRESO')
    if base == '_AJUSTE':
        return 'AJUSTE_POSITIVO' if tipo == 'INGRESO' else 'AJUSTE_NEGATIVO'
    if base == '_CAMBIO_PRODUCTO':
        return 'CAMBIO_PRODUCTO_ENTRADA' if tipo == 'INGRESO' else 'CAMBIO_PRODUCTO_SALIDA'
    return base


# ============================================================================
# CONEXIONES
# ============================================================================

def conectar_mysql():
    return mysql.connector.connect(
        host=os.getenv('MYSQL_HOST'),
        port=int(os.getenv('MYSQL_PORT', 3306)),
        database=os.getenv('MYSQL_DATABASE'),
        user=os.getenv('MYSQL_USER'),
        password=os.getenv('MYSQL_PASSWORD'),
        connection_timeout=60,
    )


def conectar_pg():
    return psycopg2.connect(
        host=os.getenv('PG_HOST', 'localhost'),
        port=os.getenv('PG_PORT', '5432'),
        dbname=os.getenv('PG_DATABASE', 'retail'),
        user=os.getenv('PG_USER', 'postgres'),
        password=os.getenv('PG_PASSWORD', ''),
    )


# ============================================================================
# FORMATO
# ============================================================================

def fmt(n):
    if n is None:
        return '-'
    try:
        return f"{int(n):>10,}"
    except (ValueError, TypeError):
        return str(n)


def pct(a, b):
    if not b:
        return '  n/a  '
    diff = (a - b) / b * 100
    return f"{diff:+6.2f}%"


def linea(char='-', n=100):
    print(char * n)


def status(mysql_val, pg_val, tolerancia=0):
    if mysql_val is None or pg_val is None:
        return '?'
    mysql_val = int(mysql_val or 0)
    pg_val = int(pg_val or 0)
    if mysql_val == pg_val:
        return 'OK'
    if abs(mysql_val - pg_val) <= tolerancia:
        return '~OK'
    return '!!'


# ============================================================================
# 1. TOTALES GLOBALES
# ============================================================================

def seccion_totales(my_cur, pg_cur):
    print('\n' + '=' * 100)
    print('1. TOTALES GLOBALES — movimiento_productos (MySQL) vs app_movimientos_producto (PostgreSQL)')
    print('=' * 100)

    my_cur.execute('SELECT COUNT(*) AS total, SUM(cantidad) AS unidades FROM movimiento_productos')
    my = my_cur.fetchone()

    pg_cur.execute('SELECT COUNT(*) AS total, SUM(ABS(cantidad)) AS unidades FROM app_movimientos_producto')
    pg = pg_cur.fetchone()

    pg_cur.execute("""SELECT COUNT(*) AS total
                      FROM app_movimientos_producto
                      WHERE referencia_externa LIKE 'MIG:%'""")
    pg_mig = pg_cur.fetchone()['total']

    print(f"{'Métrica':<35} {'MySQL':>15} {'PostgreSQL':>15} {'Diff':>12} {'%':>8}  Estado")
    linea()
    t_my = int(my['total'] or 0)
    t_pg = int(pg['total'] or 0)
    print(f"{'Cantidad registros':<35} {t_my:>15,} {t_pg:>15,} {(t_pg-t_my):>+12,} {pct(t_pg, t_my):>8}  {status(t_my, t_pg)}")

    u_my = int(my['unidades'] or 0)
    u_pg = int(pg['unidades'] or 0)
    print(f"{'Unidades (|cantidad|)':<35} {u_my:>15,} {u_pg:>15,} {(u_pg-u_my):>+12,} {pct(u_pg, u_my):>8}  {status(u_my, u_pg)}")

    print(f"{'Migrados (MIG:...)  en PG':<35} {'-':>15} {pg_mig:>15,}")
    print(f"{'Creados nuevos en PG (no-MIG)':<35} {'-':>15} {(t_pg - pg_mig):>15,}")
    return t_my, t_pg


# ============================================================================
# 2. POR SUCURSAL
# ============================================================================

def seccion_por_sucursal(my_cur, pg_cur):
    print('\n' + '=' * 100)
    print('2. MOVIMIENTOS POR SUCURSAL')
    print('=' * 100)

    my_cur.execute("""
        SELECT COALESCE(alias, 'SIN_ALIAS') AS sucursal,
               COUNT(*) AS total,
               SUM(cantidad) AS unidades
        FROM movimiento_productos
        GROUP BY alias
        ORDER BY sucursal
    """)
    my_rows = {r['sucursal']: r for r in my_cur.fetchall()}

    pg_cur.execute("""
        SELECT COALESCE(s.alias, 'SIN_ALIAS') AS sucursal,
               COUNT(*) AS total,
               SUM(ABS(m.cantidad)) AS unidades
        FROM app_movimientos_producto m
        LEFT JOIN app_sucursal s ON m.sucursal_origen_id = s.id
        GROUP BY s.alias
        ORDER BY sucursal
    """)
    pg_rows = {r['sucursal']: r for r in pg_cur.fetchall()}

    print(f"{'Sucursal':<18} {'MySQL #':>12} {'PG #':>12} {'Diff':>10} {'MySQL uds':>14} {'PG uds':>14} {'Diff uds':>12}  OK")
    linea()
    todas = sorted(set(list(my_rows.keys()) + list(pg_rows.keys())))
    tot_my = tot_pg = tot_my_u = tot_pg_u = 0
    for s in todas:
        mr = my_rows.get(s, {'total': 0, 'unidades': 0})
        pr = pg_rows.get(s, {'total': 0, 'unidades': 0})
        mt = int(mr['total'] or 0)
        pt = int(pr['total'] or 0)
        mu = int(mr['unidades'] or 0)
        pu = int(pr['unidades'] or 0)
        tot_my += mt; tot_pg += pt; tot_my_u += mu; tot_pg_u += pu
        print(f"{s:<18} {mt:>12,} {pt:>12,} {(pt-mt):>+10,} {mu:>14,} {pu:>14,} {(pu-mu):>+12,}  {status(mt, pt)}")
    linea()
    print(f"{'TOTAL':<18} {tot_my:>12,} {tot_pg:>12,} {(tot_pg-tot_my):>+10,} {tot_my_u:>14,} {tot_pg_u:>14,} {(tot_pg_u-tot_my_u):>+12,}  {status(tot_my, tot_pg)}")


# ============================================================================
# 3. POR TIPO
# ============================================================================

def seccion_por_tipo(my_cur, pg_cur):
    print('\n' + '=' * 100)
    print('3. MOVIMIENTOS POR TIPO (Ingreso / Egreso / Traspaso)')
    print('=' * 100)

    my_cur.execute("""
        SELECT tipo_movimiento, COUNT(*) AS total
        FROM movimiento_productos
        GROUP BY tipo_movimiento
        ORDER BY tipo_movimiento
    """)
    my_rows = my_cur.fetchall()

    my_agg = defaultdict(int)
    for r in my_rows:
        django_tipo = TIPO_MOVIMIENTO_MAP.get(r['tipo_movimiento'], 'INGRESO')
        my_agg[django_tipo] += int(r['total'] or 0)

    pg_cur.execute("""
        SELECT tipo_movimiento, COUNT(*) AS total
        FROM app_movimientos_producto
        GROUP BY tipo_movimiento
        ORDER BY tipo_movimiento
    """)
    pg_agg = {r['tipo_movimiento']: int(r['total'] or 0) for r in pg_cur.fetchall()}

    print(f"{'Tipo Django':<20} {'MySQL':>15} {'PG':>15} {'Diff':>10} {'%':>8}  OK")
    linea()
    todos = sorted(set(list(my_agg.keys()) + list(pg_agg.keys())))
    for t in todos:
        m = my_agg.get(t, 0)
        p = pg_agg.get(t, 0)
        print(f"{t:<20} {m:>15,} {p:>15,} {(p-m):>+10,} {pct(p, m):>8}  {status(m, p)}")

    print('\n   (crudo MySQL tipo_movimiento)')
    for r in my_rows:
        print(f"     {r['tipo_movimiento']:<25}  {int(r['total'] or 0):>10,}")


# ============================================================================
# 4. POR CONCEPTO MAPEADO
# ============================================================================

def seccion_por_concepto(my_cur, pg_cur):
    print('\n' + '=' * 100)
    print('4. MOVIMIENTOS POR CONCEPTO (mapeado MySQL → Django)')
    print('=' * 100)

    my_cur.execute("""
        SELECT concepto, tipo_movimiento, COUNT(*) AS total
        FROM movimiento_productos
        GROUP BY concepto, tipo_movimiento
        ORDER BY total DESC
    """)
    my_agg = defaultdict(int)
    my_crudo = defaultdict(int)
    for r in my_cur.fetchall():
        conc = resolver_concepto(r['concepto'], r['tipo_movimiento'])
        my_agg[conc] += int(r['total'] or 0)
        my_crudo[(r['concepto'], r['tipo_movimiento'])] += int(r['total'] or 0)

    pg_cur.execute("""
        SELECT concepto, COUNT(*) AS total
        FROM app_movimientos_producto
        GROUP BY concepto
        ORDER BY total DESC
    """)
    pg_agg = {r['concepto']: int(r['total'] or 0) for r in pg_cur.fetchall()}

    print(f"{'Concepto Django':<30} {'MySQL (esperado)':>18} {'PG (real)':>15} {'Diff':>10} {'%':>8}  OK")
    linea()
    todos = sorted(set(list(my_agg.keys()) + list(pg_agg.keys())))
    for c in todos:
        m = my_agg.get(c, 0)
        p = pg_agg.get(c, 0)
        print(f"{c:<30} {m:>18,} {p:>15,} {(p-m):>+10,} {pct(p, m):>8}  {status(m, p)}")

    print('\n  Top 15 crudo MySQL (concepto + tipo)')
    for (conc, tipo), tot in sorted(my_crudo.items(), key=lambda x: -x[1])[:15]:
        django = resolver_concepto(conc, tipo)
        print(f"     {conc!s:<35} {tipo!s:<12} {tot:>10,}  → {django}")


# ============================================================================
# 5. POR AÑO / MES
# ============================================================================

def seccion_por_anio_mes(my_cur, pg_cur):
    print('\n' + '=' * 100)
    print('5. MOVIMIENTOS POR AÑO (histórico)')
    print('=' * 100)

    my_cur.execute("""
        SELECT YEAR(fecha) AS anio, COUNT(*) AS total, SUM(cantidad) AS uds
        FROM movimiento_productos
        GROUP BY YEAR(fecha)
        ORDER BY anio
    """)
    my_rows = {int(r['anio'] or 0): r for r in my_cur.fetchall()}

    pg_cur.execute("""
        SELECT EXTRACT(YEAR FROM fecha)::int AS anio, COUNT(*) AS total, SUM(ABS(cantidad)) AS uds
        FROM app_movimientos_producto
        GROUP BY EXTRACT(YEAR FROM fecha)
        ORDER BY anio
    """)
    pg_rows = {int(r['anio'] or 0): r for r in pg_cur.fetchall()}

    print(f"{'Año':<8} {'MySQL #':>12} {'PG #':>12} {'Diff':>10} {'%':>8} {'MySQL uds':>14} {'PG uds':>14}  OK")
    linea()
    anios = sorted(set(list(my_rows.keys()) + list(pg_rows.keys())))
    for a in anios:
        mr = my_rows.get(a, {'total': 0, 'uds': 0})
        pr = pg_rows.get(a, {'total': 0, 'uds': 0})
        mt = int(mr['total'] or 0); pt = int(pr['total'] or 0)
        mu = int(mr['uds'] or 0);  pu = int(pr['uds'] or 0)
        print(f"{a:<8} {mt:>12,} {pt:>12,} {(pt-mt):>+10,} {pct(pt, mt):>8} {mu:>14,} {pu:>14,}  {status(mt, pt)}")


def seccion_por_mes_reciente(my_cur, pg_cur):
    print('\n' + '=' * 100)
    print('6. MOVIMIENTOS POR MES (últimos 12 meses)')
    print('=' * 100)

    my_cur.execute("""
        SELECT DATE_FORMAT(fecha, '%Y-%m') AS ym, COUNT(*) AS total, SUM(cantidad) AS uds
        FROM movimiento_productos
        WHERE fecha >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
        GROUP BY DATE_FORMAT(fecha, '%Y-%m')
        ORDER BY ym
    """)
    my_rows = {r['ym']: r for r in my_cur.fetchall()}

    pg_cur.execute("""
        SELECT TO_CHAR(fecha, 'YYYY-MM') AS ym, COUNT(*) AS total, SUM(ABS(cantidad)) AS uds
        FROM app_movimientos_producto
        WHERE fecha >= (CURRENT_DATE - INTERVAL '12 months')
        GROUP BY TO_CHAR(fecha, 'YYYY-MM')
        ORDER BY ym
    """)
    pg_rows = {r['ym']: r for r in pg_cur.fetchall()}

    print(f"{'Año-Mes':<10} {'MySQL #':>12} {'PG #':>12} {'Diff':>10} {'%':>8}  OK")
    linea()
    meses = sorted(set(list(my_rows.keys()) + list(pg_rows.keys())))
    for m in meses:
        mr = my_rows.get(m, {'total': 0})
        pr = pg_rows.get(m, {'total': 0})
        mt = int(mr['total'] or 0); pt = int(pr['total'] or 0)
        print(f"{m:<10} {mt:>12,} {pt:>12,} {(pt-mt):>+10,} {pct(pt, mt):>8}  {status(mt, pt)}")


# ============================================================================
# 7. OMITIDOS (SKUs no migrados)
# ============================================================================

def seccion_omitidos(my_cur, pg_cur):
    print('\n' + '=' * 100)
    print('7. MOVIMIENTOS OMITIDOS EN LA MIGRACIÓN')
    print('=' * 100)

    # Total MySQL
    my_cur.execute('SELECT COUNT(*) AS t FROM movimiento_productos')
    total_my = int(my_cur.fetchone()['t'] or 0)

    # Migrados (MIG:)
    pg_cur.execute("SELECT COUNT(*) AS t FROM app_movimientos_producto WHERE referencia_externa LIKE 'MIG:%'")
    migrados = int(pg_cur.fetchone()['t'] or 0)

    omitidos = total_my - migrados
    print(f"  Total movimientos en MySQL          : {total_my:>12,}")
    print(f"  Migrados en PG (referencia MIG:...) : {migrados:>12,}")
    print(f"  Omitidos                             : {omitidos:>12,}  ({omitidos/total_my*100:.2f}% si corresponde)")

    print('\n  Principales causas de omisión (MySQL rows sin SKU válido en PG):')

    # 7.1 SKUs en movimiento que no existen en talla
    my_cur.execute("""
        SELECT COUNT(*) AS t
        FROM movimiento_productos m
        LEFT JOIN talla t ON t.codigo_asociado = m.codigo_asociado AND t.alias = m.alias
        WHERE t.codigo_asociado IS NULL
    """)
    sin_sku = int(my_cur.fetchone()['t'] or 0)
    print(f"    • SKU + alias no existen en tabla talla : {sin_sku:>10,}")

    # 7.2 movimientos con alias inválido
    alias_validos = "('PA00','PAO0','PAO1','PAO2','PAO3','PAO4','EDEL','EDEL FALLADOS','GILD','IMP','NICK1','NICK2','NICK3')"
    my_cur.execute(f"""
        SELECT alias, COUNT(*) AS t
        FROM movimiento_productos
        WHERE alias IS NULL OR alias = '' OR alias NOT IN {alias_validos}
        GROUP BY alias
        ORDER BY t DESC
        LIMIT 15
    """)
    print("    • Alias fuera de los 13 válidos:")
    tot_alias = 0
    for r in my_cur.fetchall():
        print(f"         alias={r['alias']!r:<25}  {int(r['t'] or 0):>10,}")
        tot_alias += int(r['t'] or 0)
    print(f"         (subtotal: {tot_alias:,})")


# ============================================================================
# 8. TOP DIFERENCIAS POR SUCURSAL + TIPO (detectar qué área está descuadrada)
# ============================================================================

def seccion_detalle_sucursal_tipo(my_cur, pg_cur):
    print('\n' + '=' * 100)
    print('8. DETALLE SUCURSAL × TIPO (para detectar origen de descuadres)')
    print('=' * 100)

    my_cur.execute("""
        SELECT COALESCE(alias,'?') AS alias, tipo_movimiento, COUNT(*) AS t
        FROM movimiento_productos
        GROUP BY alias, tipo_movimiento
    """)
    my_agg = defaultdict(int)
    for r in my_cur.fetchall():
        tipo = TIPO_MOVIMIENTO_MAP.get(r['tipo_movimiento'], 'INGRESO')
        my_agg[(r['alias'], tipo)] += int(r['t'] or 0)

    pg_cur.execute("""
        SELECT COALESCE(s.alias, '?') AS alias, m.tipo_movimiento, COUNT(*) AS t
        FROM app_movimientos_producto m
        LEFT JOIN app_sucursal s ON s.id = m.sucursal_origen_id
        GROUP BY s.alias, m.tipo_movimiento
    """)
    pg_agg = {(r['alias'], r['tipo_movimiento']): int(r['t'] or 0) for r in pg_cur.fetchall()}

    print(f"{'Sucursal':<16} {'Tipo':<12} {'MySQL':>12} {'PG':>12} {'Diff':>10} {'%':>8}  OK")
    linea()
    claves = sorted(set(list(my_agg.keys()) + list(pg_agg.keys())))
    for (alias, tipo) in claves:
        m = my_agg.get((alias, tipo), 0)
        p = pg_agg.get((alias, tipo), 0)
        if m == 0 and p == 0:
            continue
        marker = status(m, p)
        print(f"{alias:<16} {tipo:<12} {m:>12,} {p:>12,} {(p-m):>+10,} {pct(p, m):>8}  {marker}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    print('Conectando a MySQL ...', end=' ', flush=True)
    my = conectar_mysql(); my_cur = my.cursor(dictionary=True, buffered=True); print('OK')
    print('Conectando a PostgreSQL ...', end=' ', flush=True)
    pg = conectar_pg(); pg_cur = pg.cursor(cursor_factory=RealDictCursor); print('OK')

    try:
        seccion_totales(my_cur, pg_cur)
        seccion_por_sucursal(my_cur, pg_cur)
        seccion_por_tipo(my_cur, pg_cur)
        seccion_por_concepto(my_cur, pg_cur)
        seccion_por_anio_mes(my_cur, pg_cur)
        seccion_por_mes_reciente(my_cur, pg_cur)
        seccion_omitidos(my_cur, pg_cur)
        seccion_detalle_sucursal_tipo(my_cur, pg_cur)

        print('\n' + '=' * 100)
        print('ANÁLISIS FINALIZADO')
        print('=' * 100)
    finally:
        my_cur.close(); my.close()
        pg_cur.close(); pg.close()


if __name__ == '__main__':
    main()
