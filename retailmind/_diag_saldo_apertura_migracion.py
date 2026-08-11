"""
_diag_saldo_apertura_migracion.py  --  SOLO LECTURA

Demuestra por que el reporte "Stock Original vs Stock Actual"
(/app/reportes/movimientos-sucursal/) mostraba Original=0 en SKU que SI tenian
stock antes del periodo.

Causa: el saldo de apertura de la migracion Laravel entro como INGRESO_INICIAL
con referencia_externa='MIGRACION_LARAVEL' y fecha ~2026-01-22. El kardex legacy
ANTERIOR tambien se migro, asi que ese stock esta contado dos veces:

    SUM(todos los movimientos)  !=  Producto_Talla.stock

Como el saldo del periodo se reconstruye rebobinando el stock de hoy
(saldo_inicial = stock_hoy - (entradas - salidas)), cualquier ventana que cruce
la fecha de corte se come la apertura como "entrada" y deja el original en 0 o
negativo.

El fix excluye la apertura de entradas/salidas/posterior en `_saldos_periodo`.
Este script compara ambos calculos.

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
    print('Sin productos para marca "%s"%s' % (MARCA, ' / ' + ARTICULO if ARTICULO else ''))
    sys.exit(0)

print('MARCA "%s"%s | periodo desde %s | %d productos-sucursal\n'
      % (MARCA, ' / ' + ARTICULO if ARTICULO else '', DESDE, len(prod_ids)))

# --- stock real de hoy ------------------------------------------------------
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
apertura_qs = base.filter(concepto='INGRESO_INICIAL',
                          referencia_externa=REF_SALDO_INICIAL_SINTETICO)
sin_apertura = base.exclude(concepto='INGRESO_INICIAL',
                            referencia_externa=REF_SALDO_INICIAL_SINTETICO)

apertura = _sumar(apertura_qs)
todo = _sumar(base)
ent_antes = _sumar(base.filter(fecha__gte=DESDE, cantidad__gt=0))
ent_despues = _sumar(sin_apertura.filter(fecha__gte=DESDE, cantidad__gt=0))
sal = _sumar(base.filter(fecha__gte=DESDE, cantidad__lt=0))

print('%-22s %-8s %7s %8s %9s %9s %9s'
      % ('ARTICULO', 'SUCURSAL', 'stock', 'apertura', 'kardex', 'ORIG.antes', 'ORIG.fix'))
print('-' * 82)

tot = {'stock': 0, 'kardex': 0, 'antes': 0, 'fix': 0, 'apertura': 0}
descuadre = 0
for p in sorted(productos, key=lambda x: (x.articulo, x.sucursal.alias)):
    st = stock.get(p.id, 0)
    ap = apertura.get(p.id, 0)
    kx = todo.get(p.id, 0)
    s = abs(sal.get(p.id, 0))
    antes = st - (ent_antes.get(p.id, 0) - s)
    fix = st - (ent_despues.get(p.id, 0) - s)
    tot['stock'] += st
    tot['kardex'] += kx
    tot['antes'] += antes
    tot['fix'] += fix
    tot['apertura'] += ap
    if kx != st:
        descuadre += 1
    if ARTICULO or st or ap:
        print('%-22s %-8s %7d %8d %9d %9d %9d'
              % (p.articulo[:22], (p.sucursal.alias or '?')[:8], st, ap, kx, antes, fix))

print('-' * 82)
print('%-31s %7d %8d %9d %9d %9d'
      % ('TOTAL', tot['stock'], tot['apertura'], tot['kardex'], tot['antes'], tot['fix']))

print('\nLECTURA:')
print('  stock      = Producto_Talla.stock de HOY (la verdad)')
print('  apertura   = INGRESO_INICIAL con referencia MIGRACION_LARAVEL')
print('  kardex     = SUM(cantidad) de TODOS los movimientos')
print('  ORIG.antes = stock - (entradas - salidas) contando la apertura  [BUG]')
print('  ORIG.fix   = idem excluyendo la apertura                       [FIX]')
print('\n%d de %d filas tienen kardex != stock (doble conteo de la apertura).'
      % (descuadre, len(productos)))
print('Diferencia kardex - stock = %d (deberia ser ~= apertura = %d).'
      % (tot['kardex'] - tot['stock'], tot['apertura']))
print('\nFIN (solo lectura).')
