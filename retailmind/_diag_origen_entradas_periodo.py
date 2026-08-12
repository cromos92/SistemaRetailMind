"""
_diag_origen_entradas_periodo.py  --  SOLO LECTURA

Responde tres preguntas sobre el reporte "Stock Original vs Stock Actual"
(/app/reportes/movimientos-sucursal/):

  1. CUANDO fue realmente la carga de la migracion Laravel.
     Imprime los INGRESO_INICIAL por mes, separando los que llevan la marca
     referencia_externa='MIGRACION_LARAVEL' de los que NO. Si aparece un bloque
     grande SIN marca (por ejemplo en abril/mayo 2026), ese bloque se esta
     contando como "entrada real" y el reporte lo suma al Original.

  2. DE QUE esta compuesto el "Original" de la ventana.
     Original = ya_tenia + entro. "ya_tenia" es el saldo al dia Desde e incluye
     TODO el historico por definicion. Este script separa los dos sumandos para
     ver cual manda.

  3. DE DONDE viene lo que entro en la ventana: despachos desde bodega
     (traspasos), compras a proveedor, o cargas sinteticas.

Uso:
    python _diag_origen_entradas_periodo.py
    python _diag_origen_entradas_periodo.py "PASSER" 2026-01-01 2026-12-31
    python _diag_origen_entradas_periodo.py "NEW WALK" 2026-01-01
"""
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django  # noqa: E402
django.setup()

from django.db.models import Count, Sum  # noqa: E402
from django.db.models.functions import TruncMonth  # noqa: E402
from app.models import Producto, Producto_Talla, Movimientos_Producto, Sucursal  # noqa: E402
from app.constants_kardex import REF_SALDO_INICIAL_SINTETICO  # noqa: E402


def _fecha(arg, defecto):
    if not arg:
        return defecto
    return datetime.strptime(arg, '%Y-%m-%d').date()


MARCA = sys.argv[1] if len(sys.argv) > 1 else 'PASSER'
DESDE = _fecha(sys.argv[2] if len(sys.argv) > 2 else None,
               datetime.strptime('2026-01-01', '%Y-%m-%d').date())
HASTA = _fecha(sys.argv[3] if len(sys.argv) > 3 else None, None)

filtro_fecha = {'fecha__gte': DESDE}
if HASTA:
    filtro_fecha['fecha__lte'] = HASTA

print('=' * 78)
print('1) CUANDO SE CARGO LA MIGRACION  (todos los INGRESO_INICIAL del sistema)')
print('=' * 78)
print('Si la migracion fue en abril/mayo, deberia verse el bloque grande ahi.')
print('La columna "sin marca" es la peligrosa: esos NO se excluyen y el reporte')
print('los cuenta como mercaderia que llego de verdad.\n')

filas = (Movimientos_Producto.objects
         .filter(concepto='INGRESO_INICIAL', estado='COMPLETADO')
         .annotate(mes=TruncMonth('fecha'))
         .values('mes')
         .annotate(n=Count('id'), und=Sum('cantidad'))
         .order_by('mes'))
marcados = (Movimientos_Producto.objects
            .filter(concepto='INGRESO_INICIAL', estado='COMPLETADO',
                    referencia_externa=REF_SALDO_INICIAL_SINTETICO)
            .annotate(mes=TruncMonth('fecha'))
            .values('mes')
            .annotate(n=Count('id'), und=Sum('cantidad')))
mapa_marcado = {m['mes']: m for m in marcados}

print('%-10s %10s %12s %10s %12s' % ('MES', 'mov.marca', 'und.marca',
                                     'mov.SIN', 'und.SIN'))
print('-' * 60)
tot_sin = 0
for f in filas:
    mm = mapa_marcado.get(f['mes'], {'n': 0, 'und': 0})
    n_sin = f['n'] - (mm['n'] or 0)
    u_sin = (f['und'] or 0) - (mm['und'] or 0)
    tot_sin += u_sin
    print('%-10s %10d %12d %10d %12d'
          % (f['mes'].strftime('%Y-%m') if f['mes'] else '?',
             mm['n'] or 0, mm['und'] or 0, n_sin, u_sin))
print('-' * 60)
print('Unidades de INGRESO_INICIAL SIN la marca MIGRACION_LARAVEL: %d' % tot_sin)
print('(Si un mes concentra un bloque enorme sin marca, ESE es el saldo de')
print(' apertura real y hay que marcarlo o excluirlo por fecha.)')

# ---------------------------------------------------------------------------
productos = list(Producto.objects
                 .filter(atributo1__valor__icontains=MARCA,
                         excluir_de_analitica=False)
                 .select_related('sucursal'))
prod_ids = [p.id for p in productos]
if not prod_ids:
    print('\nSin productos para la marca "%s". FIN.' % MARCA)
    sys.exit(0)

stock = defaultdict(int)
for r in (Producto_Talla.objects.filter(producto_id__in=prod_ids)
          .values('producto_id').annotate(s=Sum('stock'))):
    stock[r['producto_id']] = r['s'] or 0

base = Movimientos_Producto.objects.filter(
    ProductoTalla__producto_id__in=prod_ids, estado='COMPLETADO')
sin_apertura = base.exclude(concepto='INGRESO_INICIAL',
                            referencia_externa=REF_SALDO_INICIAL_SINTETICO)
ventana = sin_apertura.filter(**filtro_fecha)


def _por_producto(qs):
    out = defaultdict(int)
    for r in qs.values('ProductoTalla__producto_id').annotate(t=Sum('cantidad')):
        out[r['ProductoTalla__producto_id']] = r['t'] or 0
    return out


ent = _por_producto(ventana.filter(cantidad__gt=0))
sal = _por_producto(ventana.filter(cantidad__lt=0))
post = _por_producto(sin_apertura.filter(fecha__gt=HASTA)) if HASTA else {}

tot_ya = tot_ent = tot_act = 0
for p in productos:
    actual = stock.get(p.id, 0) - post.get(p.id, 0)
    e = ent.get(p.id, 0)
    s = abs(sal.get(p.id, 0))
    tot_ya += actual - (e - s)
    tot_ent += e
    tot_act += actual

print('\n' + '=' * 78)
print('2) DE QUE SE COMPONE EL "ORIGINAL" DE "%s"' % MARCA)
print('   ventana: desde %s%s' % (DESDE, ' hasta %s' % HASTA if HASTA else ' (sin hasta)'))
print('=' * 78)
orig = tot_ya + tot_ent
print('  ya tenia al %s ......... %10d   <- ESTO es el historico' % (DESDE, tot_ya))
print('  entro en la ventana ......... %10d' % tot_ent)
print('  ' + '-' * 44)
print('  ORIGINAL (columna del reporte) %10d' % orig)
print('  ACTUAL   (columna del reporte) %10d' % tot_act)
print('  SALIO    (Original - Actual)   %10d' % (orig - tot_act))
if orig:
    print('\n  El %.1f%% del "Original" es stock que YA ESTABA antes del %s.'
          % (100.0 * tot_ya / orig, DESDE))
    print('  Si lo que quieres es solo la mercaderia que LLEGO en el periodo,')
    print('  el numero que buscas es "entro en la ventana" = %d.' % tot_ent)

# ---------------------------------------------------------------------------
print('\n' + '=' * 78)
print('3) DE DONDE VIENE LO QUE ENTRO EN LA VENTANA')
print('=' * 78)
print('%-32s %10s %8s' % ('CONCEPTO', 'unidades', 'mov.'))
print('-' * 54)
for r in (ventana.filter(cantidad__gt=0).values('concepto')
          .annotate(u=Sum('cantidad'), n=Count('id')).order_by('-u')):
    print('%-32s %10d %8d' % (r['concepto'], r['u'] or 0, r['n']))

alias = {s.id: (s.alias or '?') for s in Sucursal.objects.all()}
print('\n%-20s %-20s %10s' % ('SUCURSAL ORIGEN', 'SUCURSAL DUENA DEL SKU', 'unidades'))
print('-' * 54)
for r in (ventana.filter(cantidad__gt=0)
          .values('sucursal_origen_id', 'ProductoTalla__producto__sucursal_id')
          .annotate(u=Sum('cantidad')).order_by('-u')[:20]):
    print('%-20s %-20s %10d'
          % (alias.get(r['sucursal_origen_id'], '(sin origen)')[:20],
             alias.get(r['ProductoTalla__producto__sucursal_id'], '?')[:20],
             r['u'] or 0))

print('\nSi ves EDEL (u otra bodega) como SUCURSAL ORIGEN, esos SI son los')
print('despachos a tienda y el reporte los esta tomando. Si el grueso sale con')
print('"(sin origen)" y concepto INGRESO_INICIAL, es carga sintetica.')
print('\nFIN (solo lectura).')
