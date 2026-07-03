"""
Fase 1 — Retipado de tipo_movimiento y normalización de signos del kardex.

Corrige dos defectos conocidos:
  1. Movimientos MIGRADOS cuyo tipo/signo quedó mal por los scripts de
     migración (_migrar_faltantes_mov.py forzó 'Traspaso'/'Ajuste' → INGRESO
     con cantidad positiva; el signo no basta para detectarlos).
     → El tipo/signo se rederiva cruzando cada fila con su registro ORIGINAL
       en MySQL (vía referencia_externa='MIG:<id>') + la clase del concepto.
  2. Movimientos NATIVOS mal tipados por el save() autoclasificador muerto
     (p.ej. TRASPASO_SALIDA/AJUSTE_NEGATIVO guardados como INGRESO).
     → En nativos el signo escrito por la app SÍ es confiable: tipo := signo.
     También remapea los conceptos nativos fuera de catálogo:
     VENTA → VENTA_PUBLICO, DEVOLUCION → DEVOLUCION_CLIENTE.

SEGURIDAD:
  - Por defecto DRY-RUN: no modifica nada, solo imprime matrices.
  - --apply exige que _fix_conceptos.py --apply ya se haya corrido (aborta si
    detecta remapeos de concepto pendientes) y ejecuta todo en UNA transacción
    con SQL directo (sin señales Django, sin tocar stock/fechas/lotes).
  - Filas con cantidad=0 y casos ambiguos/conflictivos NO se tocan (se listan).

Uso:
    python _fase1_retipado.py            # DRY-RUN
    python _fase1_retipado.py --apply    # aplica (tras _fix_conceptos --apply)
"""
import os
import sys
from collections import Counter

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django  # noqa: E402

django.setup()

from pathlib import Path  # noqa: E402

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

import mysql.connector  # noqa: E402
from django.db import connection, transaction  # noqa: E402

APPLY = '--apply' in sys.argv
MODE = 'APPLY' if APPLY else 'DRY-RUN'

# ============================================================
# Mapeo concepto MySQL -> concepto PG. COPIA de _fix_conceptos.py
# (mantener ambos en sincronía). Se usa para (a) saber el concepto
# final de cada fila migrada y (b) verificar que no queden remapeos
# de concepto pendientes antes de aplicar el retipado.
# ============================================================
MAPEO_CONCEPTOS = {
    'ventaxpublico': 'VENTA_PUBLICO', 'ventaxboleta': 'VENTA_PUBLICO',
    'ventaxboletaelectronica': 'VENTA_PUBLICO', 'ventaxguia': 'VENTA_PUBLICO',
    'venta_publico': 'VENTA_PUBLICO',
    'ventaxfactura': 'VENTA_MAYORISTA', 'venta_mayorista': 'VENTA_MAYORISTA',
    'ventaxguiaxprovedor': 'RECEPCION_COMPRA', 'ventaxfacturaxprovedor': 'RECEPCION_COMPRA',
    'factura-proovedores': 'RECEPCION_COMPRA', 'factura_proovedores': 'RECEPCION_COMPRA',
    'recepcion_compra': 'RECEPCION_COMPRA',
    'ventaxinterna': 'TRASPASO_SUCURSAL', 'factura_interna': 'TRASPASO_SUCURSAL',
    'facturainterna': 'TRASPASO_SUCURSAL', 'guia_despacho': 'TRASPASO_SUCURSAL',
    'guiadespacho': 'TRASPASO_SUCURSAL', 'traspaso_sucursal': 'TRASPASO_SUCURSAL',
    'creacion': 'INGRESO_INICIAL', 'ingreso_inicial': 'INGRESO_INICIAL',
    'ingresoxinicial': 'INGRESO_INICIAL',
    'ajustexinventario': 'AJUSTE_INVENTARIO', 'ajuste_inventario': 'AJUSTE_INVENTARIO',
    'ajuste_positivo': 'AJUSTE_POSITIVO', 'ajuste_negativo': 'AJUSTE_NEGATIVO',
    'correccion_stock': 'CORRECCION_STOCK',
    'movimientoxparametrosxmaestros': 'CORRECCION_STOCK',
    'movimientoxconcepto': 'AJUSTE_INVENTARIO',
    'nota_de_credito': 'DEVOLUCION_CLIENTE', 'notadecredito': 'DEVOLUCION_CLIENTE',
    'movimientoxnotadecredito': 'DEVOLUCION_CLIENTE',
    'anulacion_ticket': 'ANULACION', 'anulacion': 'ANULACION',
    'cancelacion': 'CANCELACION', 'anular_factura': 'ANULACION',
    'anular_guia': 'ANULACION', 'anulaxboleta': 'ANULACION',
    'anulacionxdocumento': 'ANULACION',
}


def concepto_final(c_norm, tipo_mysql, concepto_actual):
    if 'cambio' in c_norm and 'producto' in c_norm:
        return ('CAMBIO_PRODUCTO_ENTRADA'
                if (tipo_mysql or '').lower() == 'ingreso'
                else 'CAMBIO_PRODUCTO_SALIDA')
    if c_norm == 'egreso':
        return 'AJUSTE_NEGATIVO'
    if c_norm == 'ingreso':
        return 'AJUSTE_POSITIVO'
    return MAPEO_CONCEPTOS.get(c_norm) or concepto_actual


# Clase de dirección de cada concepto. Los AMBIGUOS pueden ir en ambas
# direcciones (traspasos legacy de una sola pierna, correcciones, reversas),
# ahí manda el tipo original de MySQL y, si tampoco es claro, no se toca.
EGRESO_SET = {
    'VENTA_PUBLICO', 'VENTA_MAYORISTA', 'VENTA_DIRECTA', 'TRASPASO_SALIDA',
    'AJUSTE_NEGATIVO', 'PERDIDA_ROBO', 'PERDIDA_DETERIORO',
    'DONACION_ENTREGADA', 'DEVOLUCION_PROVEEDOR', 'AJUSTE_INVENTARIO_SALIDA',
    'DESPACHO_COTIZACION', 'SOBRANTE_DEVUELTO', 'CAMBIO_PRODUCTO_SALIDA',
    'SOBRANTE_ABSORBIDO_ORIGEN',
}
INGRESO_SET = {
    'INGRESO_INICIAL', 'INGRESO_MANUAL', 'RECEPCION_COMPRA',
    'REPOSICION_STOCK', 'DEVOLUCION_CLIENTE', 'TRASPASO_ENTRADA',
    'REGULARIZACION_TRASPASO', 'AJUSTE_POSITIVO', 'DONACION_RECIBIDA',
    'AJUSTE_INVENTARIO_ENTRADA', 'SOBRANTE_INGRESO', 'RECEPCION_SOBRANTE',
    'DEVOLUCION_NC', 'DEVOLUCION_NC_POST_RECEPCION', 'ANULACION',
    'ANULACION_TICKET', 'CAMBIO_PRODUCTO_ENTRADA', 'REPARACION_STOCK_HISTORICO',
}
REMAP_NATIVOS = {'VENTA': 'VENTA_PUBLICO', 'DEVOLUCION': 'DEVOLUCION_CLIENTE'}

print('=' * 80)
print(f'FASE 1 — RETIPADO tipo_movimiento / signos (modo: {MODE})')
print('=' * 80)

# ============================================================
# [1] MySQL: id -> (concepto_norm, tipo_original)
# ============================================================
print('\n[1] Cargando originales de MySQL...')
conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'), port=int(os.getenv('MYSQL_PORT', 3306)),
    database=os.getenv('MYSQL_DATABASE'), user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'), connection_timeout=300)
cur = conn.cursor()
cur.execute("SELECT id, concepto, tipo_movimiento FROM movimiento_productos")
mysql_data = {}
for mid, c, t in cur:
    c_norm = (c or '').lower().strip().replace(' ', '_')
    mysql_data[mid] = (c_norm, (t or '').strip())
cur.close()
conn.close()
print(f'  {len(mysql_data):,} filas MySQL')

# ============================================================
# [2] PG: filas migradas
# ============================================================
print('\n[2] Cargando filas migradas de PG...')
with connection.cursor() as c:
    c.execute("""
        SELECT id, substring(referencia_externa, 5)::int, concepto,
               tipo_movimiento, cantidad
        FROM app_movimientos_producto
        WHERE referencia_externa LIKE 'MIG:%'
    """)
    pg_mig = c.fetchall()
print(f'  {len(pg_mig):,} filas PG con MIG:id')

# ============================================================
# [3] Calcular retipados de migrados
#
# Regla de dirección: manda el tipo_movimiento ORIGINAL de MySQL
# (Ingreso/Egreso/Venta); la clase del concepto solo decide cuando el
# tipo MySQL es ambiguo (Traspaso/Ajuste/vacío). Cuando concepto y tipo
# MySQL apuntan en direcciones opuestas es el CONCEPTO el que quedó mal
# mapeado en la migración (ej.: 'VentaxFactura' + Ingreso = la pierna de
# ENTRADA de un traspaso facturado entre sucursales, no una venta) — se
# corrige el concepto donde la semántica es clara y el resto se reporta.
# ============================================================
print('\n[3] Calculando (migrados)...')
a_egreso, a_ingreso = [], []          # ids a corregir tipo/signo por dirección
entrada_facturada = []                # VENTA_MAYORISTA + Ingreso -> TRASPASO_ENTRADA
conceptos_pendientes = 0              # filas cuyo concepto aún no se remapeó
matriz_tipo = Counter()
flips_signo = 0
revisar_concepto = Counter()          # dirección corregida, etiqueta dudosa
mantener_ambiguo = Counter()
cantidad_cero = 0
ya_correctos = 0

for pg_id, mig_id, concepto, tipo, cantidad in pg_mig:
    data = mysql_data.get(mig_id)
    if not data:
        continue
    c_norm, tipo_mysql = data
    cf = concepto_final(c_norm, tipo_mysql, concepto)
    if cf != concepto:
        conceptos_pendientes += 1
    if not cantidad:
        cantidad_cero += 1
        continue

    tm = tipo_mysql.lower()
    mysql_dir = ('EGRESO' if tm in ('egreso', 'egresso', 'venta')
                 else 'INGRESO' if tm == 'ingreso' else None)

    if cf in EGRESO_SET:
        clase = 'EGRESO'
    elif cf in INGRESO_SET:
        clase = 'INGRESO'
    else:
        clase = None

    destino = mysql_dir or clase
    if destino is None:
        mantener_ambiguo[cf] += 1
        continue

    if cf == 'VENTA_MAYORISTA' and destino == 'INGRESO':
        # Pierna de entrada de traspaso facturado: además del tipo/signo,
        # corregir concepto y poblar sucursal_destino para que los reportes
        # de "recibido" la vean.
        entrada_facturada.append(pg_id)
        continue
    if clase and mysql_dir and clase != mysql_dir:
        revisar_concepto[(cf, tipo_mysql)] += 1

    cant_ok = -abs(cantidad) if destino == 'EGRESO' else abs(cantidad)
    if tipo == destino and cantidad == cant_ok:
        ya_correctos += 1
        continue
    if tipo != destino:
        matriz_tipo[(tipo, destino)] += 1
    if cantidad != cant_ok:
        flips_signo += 1
    (a_egreso if destino == 'EGRESO' else a_ingreso).append(pg_id)

# ============================================================
# [4] Nativos: conceptos fuera de catálogo + tipo≠signo
# ============================================================
print('\n[4] Calculando (nativos)...')
with connection.cursor() as c:
    c.execute("""
        SELECT id, concepto, tipo_movimiento, cantidad
        FROM app_movimientos_producto
        WHERE (referencia_externa IS NULL OR referencia_externa NOT LIKE 'MIG:%%')
          AND (concepto IN ('VENTA', 'DEVOLUCION')
               OR (cantidad > 0 AND tipo_movimiento = 'EGRESO')
               OR (cantidad < 0 AND tipo_movimiento = 'INGRESO'))
    """)
    nativos = c.fetchall()

nat_remap = Counter()
nat_remap_ids = {}                    # destino -> [ids]
nat_tipo = Counter()
for pg_id, concepto, tipo, cantidad in nativos:
    destino_c = REMAP_NATIVOS.get(concepto)
    if destino_c:
        nat_remap[(concepto, destino_c)] += 1
        nat_remap_ids.setdefault(destino_c, []).append(pg_id)
    if cantidad:
        destino_t = 'INGRESO' if cantidad > 0 else 'EGRESO'
        if tipo != destino_t:
            nat_tipo[(tipo, destino_t)] += 1
            (a_ingreso if destino_t == 'INGRESO' else a_egreso).append(pg_id)

# ============================================================
# [5] Resumen
# ============================================================
print('\n' + '=' * 80)
print('RESUMEN')
print('=' * 80)
print(f'Migrados ya correctos:            {ya_correctos:>10,}')
print(f'Migrados a corregir:              {len(set(a_egreso)) + len(set(a_ingreso)) - sum(nat_tipo.values()):>10,}')
print(f'  cambios de tipo (old->new):')
for (o, n), k in sorted(matriz_tipo.items(), key=lambda x: -x[1]):
    print(f'    {o:>8} -> {n:<8} {k:>10,}')
print(f'  flips de signo:                 {flips_signo:>10,}')
print(f'Conceptos aún sin remapear:       {conceptos_pendientes:>10,}  (deben ser 0 antes de --apply)')
print(f'Cantidad=0 (no se tocan):         {cantidad_cero:>10,}')
print(f'Ambiguos mantenidos:              {sum(mantener_ambiguo.values()):>10,}  {dict(mantener_ambiguo.most_common(6))}')
print(f'Entradas facturadas -> TRASPASO_ENTRADA: {len(entrada_facturada):>7,}'
      '  (VENTA_MAYORISTA con tipo Ingreso en MySQL)')
print(f'Dirección corregida, concepto a revisar: {sum(revisar_concepto.values()):>7,}')
for (cf, tm), k in revisar_concepto.most_common(8):
    print(f'    {cf:<28} mysql={tm:<10} {k:>8,}')
print(f'Nativos remap concepto:           {sum(nat_remap.values()):>10,}  {dict(nat_remap)}')
print(f'Nativos retipados por signo:      {sum(nat_tipo.values()):>10,}  {dict(nat_tipo)}')

ids_egreso = sorted(set(a_egreso))
ids_ingreso = sorted(set(a_ingreso))
total_updates = (len(ids_egreso) + len(ids_ingreso) + sum(nat_remap.values())
                 + len(entrada_facturada))

if not APPLY:
    print('\n' + '=' * 80)
    print(f'DRY-RUN TERMINADO. {total_updates:,} updates potenciales.')
    print('Para aplicar (tras _fix_conceptos.py --apply):')
    print('  python _fase1_retipado.py --apply')
    print('=' * 80)
    sys.exit(0)

# ============================================================
# [6] APPLY
# ============================================================
if conceptos_pendientes:
    print('\nABORTADO: hay remapeos de concepto pendientes '
          f'({conceptos_pendientes:,}). Corre primero: python _fix_conceptos.py --apply')
    sys.exit(1)

print(f'\n[6] Aplicando {total_updates:,} updates (una transacción)...')
BATCH = 10000
entrada_facturada = sorted(set(entrada_facturada))
with transaction.atomic():
    with connection.cursor() as c:
        for i in range(0, len(entrada_facturada), BATCH):
            c.execute(
                "UPDATE app_movimientos_producto "
                "SET concepto = 'TRASPASO_ENTRADA', tipo_movimiento = 'INGRESO', "
                "    cantidad = ABS(cantidad), "
                "    sucursal_destino_id = COALESCE(sucursal_destino_id, sucursal_origen_id) "
                "WHERE id = ANY(%s)", [entrada_facturada[i:i + BATCH]])
        for destino_c, ids in nat_remap_ids.items():
            for i in range(0, len(ids), BATCH):
                c.execute(
                    'UPDATE app_movimientos_producto SET concepto = %s WHERE id = ANY(%s)',
                    [destino_c, ids[i:i + BATCH]])
        for i in range(0, len(ids_egreso), BATCH):
            c.execute(
                "UPDATE app_movimientos_producto "
                "SET tipo_movimiento = 'EGRESO', cantidad = -ABS(cantidad) "
                "WHERE id = ANY(%s)", [ids_egreso[i:i + BATCH]])
        for i in range(0, len(ids_ingreso), BATCH):
            c.execute(
                "UPDATE app_movimientos_producto "
                "SET tipo_movimiento = 'INGRESO', cantidad = ABS(cantidad) "
                "WHERE id = ANY(%s)", [ids_ingreso[i:i + BATCH]])

print('OK — retipado aplicado')
