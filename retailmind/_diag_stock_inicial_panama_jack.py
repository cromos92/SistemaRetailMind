"""
_diag_stock_inicial_panama_jack.py  --  SOLO LECTURA (ningun write/delete)

Responde: de donde sale el "stock inicial / recibido" de los reportes de
existencias, con que fecha se calcula, y por que puede quedar POR DEBAJO del
stock actual (que es aritmeticamente imposible si el kardex estuviera completo).

Compara las DOS definiciones que hoy conviven en el codigo:

  A) /app/reportes/existencias-sucursal/  -> campo `stock_inicial` ("Recibido hist.")
     views_modulo_reportes.py:3230  -- SUM(cantidad) de Movimientos_Producto
     con concepto in conceptos_ingreso, estado=COMPLETADO, POR SKU (talla).
     NO exige cantidad>0 y NO incluye los conceptos de traspaso legacy.

  B) /app/reportes/movimientos-sucursal/ -> campo `inicial` ("Recib.")
     views_modulo_reportes.py:5662 (_mapa_entrada) -- compras + traspasos_in,
     exige cantidad>0, y atribuye la entrada a COALESCE(destino, origen).

Uso:
    python _diag_stock_inicial_panama_jack.py
    python _diag_stock_inicial_panama_jack.py "SKECHERS"
"""
import os
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django  # noqa: E402
django.setup()

from django.db.models import Sum, Count, Min, Max, Q  # noqa: E402
from app.models import (  # noqa: E402
    Producto, Producto_Talla, Movimientos_Producto, LoteProducto, Sucursal,
)
from app.constants_kardex import (  # noqa: E402
    CONCEPTOS_ABASTECIMIENTO, CONCEPTOS_TRASPASO_ENTRADA,
    CONCEPTOS_TRASPASO_LEGACY, CONCEPTOS_TRASPASO_SALIDA, CONCEPTOS_VENTA,
)

MARCA = sys.argv[1] if len(sys.argv) > 1 else 'PANAMA JACK'

EXTRA_ENTRADA = (
    'CAMBIO_PRODUCTO_ENTRADA', 'AJUSTE_POSITIVO',
    'AJUSTE_INVENTARIO_ENTRADA', 'SOBRANTE_INGRESO', 'DONACION_RECIBIDA',
)
# Definicion A (existencias-sucursal, views_modulo_reportes.py:3223)
CONCEPTOS_A = list(CONCEPTOS_ABASTECIMIENTO + CONCEPTOS_TRASPASO_ENTRADA + EXTRA_ENTRADA)
# Definicion B (movimientos-sucursal, views_modulo_reportes.py:5644 + 5704)
CONCEPTOS_B_COMPRAS = list(CONCEPTOS_ABASTECIMIENTO + EXTRA_ENTRADA)
CONCEPTOS_B_TIN = list(CONCEPTOS_TRASPASO_ENTRADA + CONCEPTOS_TRASPASO_LEGACY)


def sep(titulo):
    print('\n' + '=' * 78)
    print(titulo)
    print('=' * 78)


# ---------------------------------------------------------------- universo
productos = (
    Producto.objects
    .filter(atributo1__valor__icontains=MARCA, excluir_de_analitica=False)
    .select_related('sucursal', 'atributo1')
)
prod_ids = list(productos.values_list('id', flat=True))
sep(f'MARCA "{MARCA}"  ->  {len(prod_ids)} productos (excluir_de_analitica=False)')
if not prod_ids:
    print('Sin productos. Revisa el nombre exacto de la marca.')
    sys.exit(0)

sucursales = {s.id: (s.alias or s.nombre or f'suc{s.id}') for s in Sucursal.objects.all()}
prod_suc = {p.id: p.sucursal_id for p in productos}
prod_art = {p.id: p.articulo for p in productos}

tallas = Producto_Talla.objects.filter(producto_id__in=prod_ids)
talla_prod = {}
talla_stock = {}
for t in tallas.values('id', 'producto_id', 'stock', 'sku', 'talla'):
    talla_prod[t['id']] = t['producto_id']
    talla_stock[t['id']] = t['stock'] or 0

stock_por_prod = defaultdict(int)
for tid, st in talla_stock.items():
    stock_por_prod[talla_prod[tid]] += st

stock_total = sum(stock_por_prod.values())
print(f'SKUs (Producto_Talla): {len(talla_stock)}   Stock actual total: {stock_total}')

# ------------------------------------------------- movimientos de la marca
movs = Movimientos_Producto.objects.filter(
    ProductoTalla__producto_id__in=prod_ids, estado='COMPLETADO'
)

sep('1) TODOS los conceptos que tocan la marca (que hay realmente en el kardex)')
print(f'{"concepto":<32}{"movs":>8}{"SUM(cant)":>12}{"cant>0":>9}{"cant<0":>9}'
      f'{"1a fecha":>13}{"ult fecha":>13}')
filas = (
    movs.values('concepto')
    .annotate(n=Count('id'), s=Sum('cantidad'), f1=Min('fecha'), f2=Max('fecha'))
    .order_by('-n')
)
for f in filas:
    c = f['concepto']
    pos = movs.filter(concepto=c, cantidad__gt=0).count()
    neg = movs.filter(concepto=c, cantidad__lt=0).count()
    marca_a = 'A' if c in CONCEPTOS_A else ' '
    marca_b = 'B' if (c in CONCEPTOS_B_COMPRAS or c in CONCEPTOS_B_TIN) else ' '
    print(f'[{marca_a}{marca_b}] {c:<28}{f["n"]:>8}{f["s"] or 0:>12}{pos:>9}{neg:>9}'
          f'{str(f["f1"]):>13}{str(f["f2"]):>13}')
print('\n[A]=lo cuenta existencias-sucursal como "Recibido hist."')
print('[B]=lo cuenta movimientos-sucursal como "Inicial (Recib.)"')

# -------------------------------------------------------- def A por talla
sep('2) DEFINICION A -- "Recibido hist." de /reportes/existencias-sucursal/')
a_por_talla = {
    r['ProductoTalla_id']: r['total'] or 0
    for r in movs.filter(concepto__in=CONCEPTOS_A)
    .values('ProductoTalla_id').annotate(total=Sum('cantidad'))
}
a_por_prod = defaultdict(int)
for tid, v in a_por_talla.items():
    a_por_prod[talla_prod[tid]] += v
print(f'Recibido hist. total (def A): {sum(a_por_talla.values())}  vs stock actual {stock_total}')

skus_sin_respaldo = [
    (tid, talla_stock[tid], a_por_talla.get(tid, 0))
    for tid in talla_stock
    if talla_stock[tid] > 0 and a_por_talla.get(tid, 0) < talla_stock[tid]
]
und_faltante = sum(st - rec for _, st, rec in skus_sin_respaldo)
print(f'SKUs con stock>0 donde RECIBIDO < STOCK ACTUAL: {len(skus_sin_respaldo)}'
      f'  ({und_faltante} unidades sin respaldo en el kardex)')
sin_ningun_mov = [x for x in skus_sin_respaldo if x[2] == 0]
print(f'   de esos, con recibido == 0 (ni un movimiento de entrada): {len(sin_ningun_mov)}')

# -------------------------------------------------------- def B por prod/suc
sep('3) DEFINICION B -- "Inicial (Recib.)" de /reportes/movimientos-sucursal/')


def mapa_entrada(qs):
    """Replica _mapa_entrada de views_modulo_reportes.py:5662."""
    m = defaultdict(lambda: defaultdict(int))
    for r in (qs.filter(sucursal_destino_id__isnull=False)
              .values('ProductoTalla__producto_id', 'sucursal_destino_id')
              .annotate(total=Sum('cantidad'))):
        m[r['ProductoTalla__producto_id']][r['sucursal_destino_id']] += abs(r['total'] or 0)
    for r in (qs.filter(sucursal_destino_id__isnull=True)
              .values('ProductoTalla__producto_id', 'sucursal_origen_id')
              .annotate(total=Sum('cantidad'))):
        m[r['ProductoTalla__producto_id']][r['sucursal_origen_id']] += abs(r['total'] or 0)
    return m


compras_map = mapa_entrada(movs.filter(concepto__in=CONCEPTOS_B_COMPRAS, cantidad__gt=0))
tin_map = mapa_entrada(movs.filter(cantidad__gt=0, concepto__in=CONCEPTOS_B_TIN))

b_por_prod_suc = defaultdict(int)
for mp in (compras_map, tin_map):
    for pid, d in mp.items():
        for sid, v in d.items():
            b_por_prod_suc[(pid, sid)] += v

b_total = sum(b_por_prod_suc.values())
print(f'Inicial total (def B): {b_total}  vs stock actual {stock_total}')

# La UI compara la columna "Recib." de una sucursal contra el "Rest." de esa
# MISMA sucursal, asi que el descuadre hay que medirlo por (producto, sucursal).
desc_ps = []
for pid, st in stock_por_prod.items():
    sid = prod_suc[pid]
    ini = b_por_prod_suc.get((pid, sid), 0)
    if st > 0 and ini < st:
        desc_ps.append((pid, sid, st, ini))
print(f'(producto, sucursal) con stock>0 donde INICIAL < RESTANTE: {len(desc_ps)}'
      f'  ({sum(st - ini for _, _, st, ini in desc_ps)} unidades)')

# ---------------------------------------------- causa 1: entrada en otra suc
sep('4) CAUSA -- entradas atribuidas a una sucursal distinta a la del SKU')
cruzados = defaultdict(int)
for pid, d in list(compras_map.items()) + list(tin_map.items()):
    dueno = prod_suc.get(pid)
    for sid, v in d.items():
        if sid != dueno:
            cruzados[(sucursales.get(sid, sid), sucursales.get(dueno, dueno))] += v
if cruzados:
    print('Unidades cuya ENTRADA quedo contada en una sucursal != la del Producto:')
    for (suc_mov, suc_prod), v in sorted(cruzados.items(), key=lambda x: -x[1])[:20]:
        print(f'   entrada en {suc_mov:<12} pero el SKU vive en {suc_prod:<12}: {v} und')
    print('   -> en esas filas la sucursal dueña muestra Recib. bajo y Rest. alto.')
else:
    print('Ninguna. Todas las entradas caen en la sucursal dueña del SKU.')

# ---------------------------------------------- causa 2: stock sin kardex
sep('5) CAUSA -- stock que existe pero NUNCA tuvo movimiento de entrada')
tallas_con_mov = set(
    movs.filter(concepto__in=CONCEPTOS_A + list(CONCEPTOS_TRASPASO_LEGACY), cantidad__gt=0)
    .values_list('ProductoTalla_id', flat=True).distinct()
)
huerfanos = [tid for tid, st in talla_stock.items() if st > 0 and tid not in tallas_con_mov]
print(f'SKUs con stock>0 y CERO movimientos de entrada de cualquier tipo: {len(huerfanos)}'
      f'  ({sum(talla_stock[t] for t in huerfanos)} unidades)')
if huerfanos:
    prods_h = sorted({talla_prod[t] for t in huerfanos})
    print('Productos afectados (primeros 15) con su fecha_creacion:')
    for p in Producto.objects.filter(id__in=prods_h[:15]).select_related('sucursal'):
        u = sum(talla_stock[t] for t in huerfanos if talla_prod[t] == p.id)
        print(f'   #{p.id:<7} {p.articulo:<22} {(p.sucursal.alias or ""):<10} '
              f'{u:>4} und   creado={p.fecha_creacion}')

# ---------------------------------------------- causa 3: fecha del movimiento
sep('6) FECHA -- que fecha usa el "inicial" y cuando llego realmente el stock')
rango = movs.filter(concepto__in=CONCEPTOS_A, cantidad__gt=0).aggregate(
    f1=Min('fecha'), f2=Max('fecha'), c1=Min('created_at'), c2=Max('created_at'))
print(f'Movimientos_Producto.fecha (la que filtra el reporte): {rango["f1"]} -> {rango["f2"]}')
print(f'created_at real de esos registros:                     '
      f'{rango["c1"]} -> {rango["c2"]}')
lot = LoteProducto.objects.filter(producto_talla__producto_id__in=prod_ids).aggregate(
    f1=Min('fecha_ingreso'), f2=Max('fecha_ingreso'), n=Count('id'),
    disp=Sum('cantidad_disponible'))
print(f'LoteProducto (FIFO): {lot["n"]} lotes, ingreso {lot["f1"]} -> {lot["f2"]}, '
      f'disponible {lot["disp"]}')
pc = productos.aggregate(f1=Min('fecha_creacion'), f2=Max('fecha_creacion'))
print(f'Producto.fecha_creacion: {pc["f1"]} -> {pc["f2"]}')

print('\nEntradas por anio (fecha del movimiento):')
por_anio = defaultdict(int)
for r in (movs.filter(concepto__in=CONCEPTOS_A, cantidad__gt=0)
          .values('fecha').annotate(s=Sum('cantidad'))):
    por_anio[r['fecha'].year] += r['s'] or 0
for anio in sorted(por_anio):
    print(f'   {anio}: {por_anio[anio]:>8} und')

# ---------------------------------------------- causa 4: negativos en def A
sep('7) CAUSA -- movimientos de concepto de ENTRADA con cantidad NEGATIVA')
neg = (movs.filter(concepto__in=CONCEPTOS_A, cantidad__lt=0)
       .values('concepto').annotate(n=Count('id'), s=Sum('cantidad')))
if neg:
    print('La def A suma cantidad sin exigir >0: estos restan del "Recibido hist.".')
    for r in neg:
        print(f'   {r["concepto"]:<30} {r["n"]:>6} movs   {r["s"]:>8} und')
else:
    print('Ninguno.')

# ---------------------------------------------- causa 5: legacy fuera de A
sep('8) CAUSA -- traspasos legacy que la def A NO cuenta pero la B si')
leg = (movs.filter(concepto__in=CONCEPTOS_TRASPASO_LEGACY, cantidad__gt=0)
       .values('concepto').annotate(n=Count('id'), s=Sum('cantidad')))
if leg:
    for r in leg:
        print(f'   {r["concepto"]:<30} {r["n"]:>6} movs  +{r["s"]:>7} und  '
              f'(invisible para existencias-sucursal)')
else:
    print('Ninguno para esta marca.')

# ---------------------------------------------- balance de kardex
sep('9) BALANCE -- SUM(cantidad) de TODO el kardex vs stock actual')
neto = movs.aggregate(s=Sum('cantidad'))['s'] or 0
print(f'SUM(cantidad) de todos los movimientos COMPLETADO: {neto}')
print(f'Stock actual (Producto_Talla.stock):               {stock_total}')
print(f'DIFERENCIA (stock actual - kardex):               {stock_total - neto}')
print('Si la diferencia es > 0, hay stock que entro sin dejar rastro en el kardex.')

# ---------------------------------------------- top ofensores
sep('10) TOP 25 SKUs con mayor "stock actual - recibido hist."')
skus_sin_respaldo.sort(key=lambda x: -(x[1] - x[2]))
print(f'{"sku":<14}{"articulo":<22}{"talla":<8}{"suc":<10}{"stock":>7}{"recib":>7}{"gap":>7}')
info = {t['id']: t for t in tallas.values('id', 'sku', 'talla')}
for tid, st, rec in skus_sin_respaldo[:25]:
    pid = talla_prod[tid]
    i = info[tid]
    print(f'{str(i["sku"]):<14}{prod_art[pid][:21]:<22}{str(i["talla"])[:7]:<8}'
          f'{sucursales.get(prod_suc[pid], "?")[:9]:<10}{st:>7}{rec:>7}{st - rec:>7}')

print('\nFIN (solo lectura, no se modifico nada).')
