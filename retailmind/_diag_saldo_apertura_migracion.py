"""
_diag_saldo_apertura_migracion.py  --  SOLO LECTURA

Reproduce fuera del navegador las columnas del reporte
"Stock Original vs Stock Actual" (/app/reportes/movimientos-sucursal/) y muestra
el doble conteo del saldo de apertura de la migracion Laravel.

EL PROBLEMA DE DATOS
--------------------
En Movimientos_Producto conviven dos origenes que se pisan:

  1. el kardex legacy importado (2022-2025), referencia_externa='MIG:<id>'
  2. el SALDO DE APERTURA de la migracion: concepto='INGRESO_INICIAL' con
     referencia_externa='MIGRACION_LARAVEL', fechado ~2026-01-22

La apertura NO es una recepcion: es la foto de arranque, y YA INCLUYE el saldo
que dejaron los movimientos legacy. Por eso

    SUM(cantidad de todos los movimientos)  !=  Producto_Talla.stock

y la diferencia es justo la apertura. La columna "kardex-stock" de abajo lo
mide fila por fila.

LO QUE MUESTRA EL REPORTE
-------------------------
No hay fotos historicas de stock en ninguna tabla, asi que el saldo se
reconstruye rebobinando el stock de HOY contra el kardex:

    Actual   = stock_hoy - (movimientos posteriores a "hasta")
    ya_tenia = Actual - (entradas - salidas) del periodo
    Original = ya_tenia + entradas          <- todo lo que tuvo disponible
    Salio    = Original - Actual = salidas  <- identidad, no aproximacion

La apertura se excluye de entradas/salidas/posterior: si no, "ya_tenia" sale 0
y el desglose miente (diria que llego en el periodo cuando ya estaba ahi).

Uso:
    python _diag_saldo_apertura_migracion.py
    python _diag_saldo_apertura_migracion.py "NEW WALK" 2026-01-01
    python _diag_saldo_apertura_migracion.py "NEW WALK" 2026-01-01 1066-1-CA
"""
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django  # noqa: E402
django.setup()

from django.db.models import Sum  # noqa: E402
from app.models import Producto, Producto_Talla, Movimientos_Producto  # noqa: E402
from app.constants_kardex import REF_SALDO_INICIAL_SINTETICO  # noqa: E402

MARCA = sys.argv[1] if len(sys.argv) > 1 else 'NEW WALK'
DESDE = datetime.strptime(sys.argv[2], '%Y-%m-%d').date() if len(sys.argv) > 2 \
    else datetime.strptime('2026-01-01', '%Y-%m-%d').date()
ARTICULO = sys.argv[3] if len(sys.argv) > 3 else None

productos = (Producto.objects
             .filter(atributo1__valor__icontains=MARCA, excluir_de_analitica=False)
             .select_related('sucursal'))
if ARTICULO:
    productos = productos.filter(articulo=ARTICULO)
productos = list(productos)
prod_ids = [p.id for p in productos]

if not prod_ids:
    print('Sin productos para marca "%s"%s'
          % (MARCA, ' / ' + ARTICULO if ARTICULO else ''))
    sys.exit(0)

print('MARCA "%s"%s | periodo desde %s (sin "hasta") | %d productos-sucursal\n'
      % (MARCA, ' / ' + ARTICULO if ARTICULO else '', DESDE, len(prod_ids)))

# --- stock real de hoy: la unica verdad conocida ----------------------------
stock = defaultdict(int)
for r in (Producto_Talla.objects.filter(producto_id__in=prod_ids)
          .values('producto_id').annotate(s=Sum('stock'))):
    stock[r['producto_id']] = r['s'] or 0


def _sumar(qs):
    out = defaultdict(int)
    for r in qs.values('ProductoTalla__producto_id').annotate(t=Sum('cantidad')):
        out[r['ProductoTalla__producto_id']] = r['t'] or 0
    return out


base = Movimientos_Producto.objects.filter(
    ProductoTalla__producto_id__in=prod_ids, estado='COMPLETADO')
# Mismo criterio que _saldos_periodo() en views_modulo_reportes.py.
sin_apertura = base.exclude(concepto='INGRESO_INICIAL',
                            referencia_externa=REF_SALDO_INICIAL_SINTETICO)

apertura = _sumar(base.filter(concepto='INGRESO_INICIAL',
                              referencia_externa=REF_SALDO_INICIAL_SINTETICO))
kardex_total = _sumar(base)
entradas = _sumar(sin_apertura.filter(fecha__gte=DESDE, cantidad__gt=0))
salidas = _sumar(sin_apertura.filter(fecha__gte=DESDE, cantidad__lt=0))

print('%-20s %-8s %8s %7s %7s %9s %7s %7s'
      % ('ARTICULO', 'SUCURSAL', 'apertura', 'k-stock',
         'yatenia', 'ORIGINAL', 'ACTUAL', 'SALIO'))
print('-' * 82)

tot = defaultdict(int)
descuadre = 0
filas = 0
for p in sorted(productos, key=lambda x: (x.articulo, x.sucursal.alias or '')):
    actual = stock.get(p.id, 0)
    ent = entradas.get(p.id, 0)
    sal = abs(salidas.get(p.id, 0))
    ya_tenia = actual - (ent - sal)
    original = ya_tenia + ent
    ap = apertura.get(p.id, 0)
    gap = kardex_total.get(p.id, 0) - actual

    tot['apertura'] += ap
    tot['gap'] += gap
    tot['ya'] += ya_tenia
    tot['orig'] += original
    tot['act'] += actual
    tot['salio'] += original - actual
    if gap:
        descuadre += 1
    if ARTICULO or actual or ap:
        filas += 1
        print('%-20s %-8s %8d %7d %7d %9d %7d %7d'
              % (p.articulo[:20], (p.sucursal.alias or '?')[:8],
                 ap, gap, ya_tenia, original, actual, original - actual))

print('-' * 82)
print('%-29s %8d %7d %7d %9d %7d %7d'
      % ('TOTAL (%d filas)' % filas, tot['apertura'], tot['gap'],
         tot['ya'], tot['orig'], tot['act'], tot['salio']))

print('\nCOLUMNAS')
print('  apertura = INGRESO_INICIAL con referencia MIGRACION_LARAVEL')
print('  k-stock  = SUM(todos los movimientos) - stock real  <- el doble conteo')
print('  yatenia  = saldo al %s (rebobinado desde el stock de hoy)' % DESDE)
print('  ORIGINAL = yatenia + entradas del periodo   <- columna del reporte')
print('  ACTUAL   = stock de hoy                     <- columna del reporte')
print('  SALIO    = ORIGINAL - ACTUAL, debe ser igual a las salidas del periodo')

print('\n%d de %d filas tienen kardex != stock.' % (descuadre, len(productos)))
print('Suma de esa diferencia = %d | suma de la apertura = %d'
      % (tot['gap'], tot['apertura']))
print('Si ambos numeros se parecen, el descuadre ES la apertura contada dos veces.')
if tot['salio'] < 0:
    print('\nOJO: SALIO total dio NEGATIVO (%d). Eso ya no lo explica la apertura:'
          '\nes descuadre entre Producto_Talla.stock y Movimientos_Producto.'
          % tot['salio'])
print('\nFIN (solo lectura).')
