# -*- coding: utf-8 -*-
"""
READ-ONLY: (1) por que la deduplicacion no reconocio la ficha 139144 como la
misma que la 39694, y (2) cuantos articulos mas estan duplicados en produccion.
NO escribe nada.

Uso (desde retailmind/):
  python manage.py shell -c "exec(open('_diag_duplicados_catalogo_readonly.py', encoding='utf-8').read())"
"""
import operator
from collections import defaultdict
from datetime import timedelta
from functools import reduce

from django.db.models import Count, Sum, Q
from django.db.models.functions import Upper, Trim
from django.utils import timezone

from app.models import (
    Producto, Producto_Talla, AtributoOpcion, LoteProducto, Movimientos_Producto,
)

FICHA_A = 39694     # la original (EDEL, creada 04/10/24)
FICHA_B = 139144    # la duplicada (EDEL, creada 14/08/26)
SKUS_VIEJOS = [4805091, 4805092, 4805093, 4805094, 4805095,
               4805096, 4805097, 4805098, 4805099, 4805100]
SKUS_NUEVOS = [4839897, 4839898, 4839900, 4839902, 4839904,
               4839905, 4839906, 4839908, 4839909, 4839910]

HACE30 = timezone.now() - timedelta(days=30)

def sep(t):
    print('\n' + '=' * 112)
    print(t)
    print('=' * 112)


# ----------------------------------------------------------------------
# 1. POR QUE NO DEDUPLICO: diff campo a campo
# ----------------------------------------------------------------------
sep('1. FICHA %s (original) vs FICHA %s (duplicada): DIFERENCIAS CAMPO A CAMPO' % (FICHA_A, FICHA_B))
print("""La identidad con la que el sistema decide "este producto ya existe" es:
  articulo normalizado + atributo1 (marca) + atributo2 (color) + atributo3 (genero)
  + categoria + sucursal    -- ver app/utils_producto_match.py: fichas_por_identidad()
Cualquier campo de esa lista que aparezca marcado con <<< es la razon del duplicado.
""")

a = Producto.objects.filter(id=FICHA_A).first()
b = Producto.objects.filter(id=FICHA_B).first()

CLAVES_IDENTIDAD = {'articulo', 'atributo1', 'atributo2', 'atributo3', 'categoria', 'sucursal'}

if not a or not b:
    print('  Falta alguna de las dos fichas (a=%s b=%s). Revisar ids.' % (bool(a), bool(b)))
else:
    print('%-24s %-34s %-34s %s' % ('campo', 'ficha %s' % FICHA_A, 'ficha %s' % FICHA_B, ''))
    print('-' * 112)
    for f in a._meta.fields:
        nombre = f.name
        if f.is_relation:
            va = getattr(a, nombre + '_id')
            vb = getattr(b, nombre + '_id')
            oa = getattr(a, nombre)
            ob = getattr(b, nombre)
            sa = '%s (id %s)' % (getattr(oa, 'valor', None) or getattr(oa, 'nombre', None)
                                 or getattr(oa, 'alias', None) or oa, va) if oa else 'None'
            sb = '%s (id %s)' % (getattr(ob, 'valor', None) or getattr(ob, 'nombre', None)
                                 or getattr(ob, 'alias', None) or ob, vb) if ob else 'None'
            distinto = (va != vb)
        else:
            va, vb = getattr(a, nombre), getattr(b, nombre)
            sa, sb = repr(va), repr(vb)
            distinto = (va != vb)
        marca = ''
        if distinto:
            marca = '<<< DIFERENTE  *** ROMPE LA IDENTIDAD ***' if nombre in CLAVES_IDENTIDAD else '<<< distinto'
        print('%-24s %-34s %-34s %s' % (nombre, sa[:34], sb[:34], marca))

    # el articulo normalizado, que es lo que realmente se compara
    from app.utils_producto_match import normalizar_articulo, fichas_por_identidad
    print('\n  articulo normalizado A: %r' % normalizar_articulo(a.articulo))
    print('  articulo normalizado B: %r' % normalizar_articulo(b.articulo))
    print('  coinciden: %s' % (normalizar_articulo(a.articulo) == normalizar_articulo(b.articulo)))

    # replicar la busqueda de identidad tal cual la hace el codigo, para cada ficha
    for f in (a, b):
        encontradas = fichas_por_identidad(f.articulo, f.atributo1_id, f.atributo2_id,
                                           f.atributo3_id, f.categoria_id, f.sucursal_id)
        print('  fichas_por_identidad() usando los datos de la ficha %s -> %s'
              % (f.id, [x.id for x in encontradas]))
    print('\n  Si cada llamada devuelve SOLO su propia ficha, esta confirmado: el sistema')
    print('  las considera productos DISTINTOS y por eso creo una nueva en vez de reusar.')


# ----------------------------------------------------------------------
# 1b. ATRIBUTOS GEMELOS (mismo texto, id distinto)
# ----------------------------------------------------------------------
sep('1b. OPCIONES DE ATRIBUTO GEMELAS (mismo valor escrito, id distinto)')
print('Dos filas "SKECHERS" o dos "NEGRO" con id distinto hacen que la identidad no')
print('matchee aunque en pantalla se vea identico.\n')

gemelas = list(AtributoOpcion.objects
               .annotate(v=Upper(Trim('valor')))
               .values('atributo_id', 'v')
               .annotate(n=Count('id'))
               .filter(n__gt=1)
               .order_by('-n')[:40])
if not gemelas:
    print('  (ninguna en todo el sistema)')
else:
    print('  Total de valores con opciones gemelas (top 40 mostrados): %s' % len(gemelas))
    print('  %-10s %-28s %-5s %s' % ('atrib_id', 'valor', 'n', 'ids'))
    for g in gemelas:
        ids = list(AtributoOpcion.objects.filter(atributo_id=g['atributo_id'])
                   .annotate(v=Upper(Trim('valor'))).filter(v=g['v'])
                   .values_list('id', flat=True))
        print('  %-10s %-28s %-5s %s' % (g['atributo_id'], g['v'][:28], g['n'], ids))

if a and b:
    print('\n  Opciones que usan estas 2 fichas:')
    for etiqueta, oa, ob in (('marca (atributo1)', a.atributo1, b.atributo1),
                             ('color (atributo2)', a.atributo2, b.atributo2),
                             ('genero (atributo3)', a.atributo3, b.atributo3)):
        ta = '%s#%s' % (oa.valor, oa.id) if oa else 'None'
        tb = '%s#%s' % (ob.valor, ob.id) if ob else 'None'
        igual_texto = (oa.valor.strip().upper() if oa else None) == (ob.valor.strip().upper() if ob else None)
        igual_id = (oa.id if oa else None) == (ob.id if ob else None)
        alerta = ''
        if igual_texto and not igual_id:
            alerta = '  <<< MISMO TEXTO, ID DISTINTO = OPCION GEMELA'
        print('    %-20s A=%-22s B=%-22s%s' % (etiqueta, ta, tb, alerta))


# ----------------------------------------------------------------------
# 2. ALCANCE 1: fichas duplicadas por (articulo, sucursal)
# ----------------------------------------------------------------------
sep('2. ALCANCE: ARTICULOS CON MAS DE UNA FICHA EN LA MISMA SUCURSAL')

grupos = list(Producto.objects
              .annotate(art=Upper(Trim('articulo')))
              .values('art', 'sucursal_id')
              .annotate(n=Count('id'),
                        nprecios=Count('precioventa', distinct=True),
                        nuevos=Count('id', filter=Q(fecha_creacion__gte=HACE30)),
                        # cuantos valores distintos toma cada campo de IDENTIDAD
                        # dentro del grupo (Count(distinct) ignora NULL, por eso
                        # se cuentan los nulos aparte).
                        na1=Count('atributo1_id', distinct=True),
                        na2=Count('atributo2_id', distinct=True),
                        na3=Count('atributo3_id', distinct=True),
                        ncat=Count('categoria_id', distinct=True),
                        nul1=Count('id', filter=Q(atributo1__isnull=True)),
                        nul2=Count('id', filter=Q(atributo2__isnull=True)),
                        nul3=Count('id', filter=Q(atributo3__isnull=True)),
                        nulcat=Count('id', filter=Q(categoria__isnull=True)))
              .filter(n__gt=1))


def _difiere(g, ndist, nnul):
    """El campo toma mas de un valor dentro del grupo (contando NULL como valor)."""
    valores = g[ndist] + (1 if g[nnul] else 0)
    return valores > 1

total_grupos = len(grupos)
fichas_sobrantes = sum(g['n'] - 1 for g in grupos)
peligrosos = [g for g in grupos if g['nprecios'] > 1]
recientes = [g for g in grupos if g['nuevos'] > 0]

print('  Grupos (articulo, sucursal) con mas de una ficha : %s' % total_grupos)
print('  Fichas sobrantes (n-1 por grupo)                 : %s' % fichas_sobrantes)
print('  Grupos con PRECIOS DE VENTA DISTINTOS entre sus fichas: %s   <-- los que rompen etiquetas' % len(peligrosos))
print('  Grupos con al menos una ficha creada en los ultimos 30 dias: %s   <-- el problema esta %s'
      % (len(recientes), 'VIVO' if recientes else 'inactivo'))

# ---- 2b. QUE campo de la identidad difiere (diagnostico sistemico) ----
print('\n  QUE ROMPE LA IDENTIDAD en esos grupos (un grupo puede romper por varios campos):')
CAMPOS = [('marca (atributo1)', 'na1', 'nul1'),
          ('color (atributo2)', 'na2', 'nul2'),
          ('genero (atributo3)', 'na3', 'nul3'),
          ('categoria', 'ncat', 'nulcat')]
conteo_campo = {etq: 0 for etq, _, _ in CAMPOS}
identicos = 0
for g in grupos:
    rompio = False
    for etq, nd, nn in CAMPOS:
        if _difiere(g, nd, nn):
            conteo_campo[etq] += 1
            rompio = True
    if not rompio:
        identicos += 1
for etq, _, _ in CAMPOS:
    print('    difieren en %-22s : %s grupos' % (etq, conteo_campo[etq]))
print('    IDENTIDAD IDENTICA (duplicado puro): %s grupos' % identicos)
print('    -> los "identicos" son los que la creacion manual SI bloquea hoy;')
print('       los otros son los que se cuelan porque un id de atributo o la categoria no calza.')

top = sorted(grupos, key=lambda g: (g['nprecios'] > 1, g['nuevos'] > 0, g['n']), reverse=True)[:25]
if top:
    from app.models import Sucursal
    alias = dict(Sucursal.objects.values_list('id', 'alias'))
    q = reduce(operator.or_, [Q(art=g['art'], sucursal_id=g['sucursal_id']) for g in top])
    fichas = list(Producto.objects.annotate(art=Upper(Trim('articulo'))).filter(q)
                  .values('id', 'art', 'sucursal_id', 'precioventa', 'fecha_creacion'))
    stock = dict(Producto_Talla.objects
                 .filter(producto_id__in=[f['id'] for f in fichas])
                 .values_list('producto_id').annotate(s=Sum('stock')))
    por_grupo = defaultdict(list)
    for f in fichas:
        por_grupo[(f['art'], f['sucursal_id'])].append(f)

    print('\n  TOP 25 (primero los que tienen precios distintos y fichas nuevas):')
    print('  %-24s %-14s %s' % ('articulo', 'sucursal', 'fichas -> id/precio/stock/creada'))
    print('  ' + '-' * 104)
    for g in top:
        clave = (g['art'], g['sucursal_id'])
        detalle = []
        for f in sorted(por_grupo.get(clave, []), key=lambda x: x['id']):
            creada = f['fecha_creacion'].strftime('%d/%m/%y') if f['fecha_creacion'] else '-'
            detalle.append('%s/$%s/%su/%s' % (f['id'], f['precioventa'] or 0,
                                              stock.get(f['id'], 0) or 0, creada))
        flag = '  <<< PRECIOS DISTINTOS' if g['nprecios'] > 1 else ''
        print('  %-24s %-14s %s%s' % (g['art'][:24], (alias.get(g['sucursal_id']) or '?')[:14],
                                      ' | '.join(detalle), flag))


# ----------------------------------------------------------------------
# 3. ALCANCE 2: la misma talla repetida dentro de UNA ficha
# ----------------------------------------------------------------------
sep('3. ALCANCE: LA MISMA TALLA REPETIDA DENTRO DE UNA MISMA FICHA (stock partido)')

pares = list(Producto_Talla.objects
             .annotate(t=Upper(Trim('talla')))
             .values('producto_id', 't')
             .annotate(n=Count('id'), s=Sum('stock'),
                       con_stock=Count('id', filter=Q(stock__gt=0)))
             .filter(n__gt=1))

fichas_afectadas = len({p['producto_id'] for p in pares})
partidos = [p for p in pares if p['con_stock'] > 1]
max_pt = Producto_Talla.objects.order_by('-id').values_list('id', flat=True).first() or 0

print('  Pares (ficha, talla) con la talla repetida : %s' % len(pares))
print('  Fichas afectadas                            : %s' % fichas_afectadas)
print('  Pares con stock en MAS DE UNA fila          : %s   <-- inventario efectivamente partido' % len(partidos))
print('  (Producto_Talla no guarda fecha de creacion; el id sirve de proxy. id maximo actual: %s)' % max_pt)

top_pares = sorted(pares, key=lambda p: (p['con_stock'] > 1, p['s'] or 0), reverse=True)[:25]
if top_pares:
    pids = [p['producto_id'] for p in top_pares]
    prods = {p.id: p for p in Producto.objects.filter(id__in=pids).select_related('sucursal')}
    filas = defaultdict(list)
    for pt in Producto_Talla.objects.filter(producto_id__in=pids).values('id', 'producto_id', 'talla', 'sku', 'stock'):
        filas[(pt['producto_id'], (pt['talla'] or '').strip().upper())].append(pt)

    print('\n  TOP 25 por stock:')
    print('  %-22s %-14s %-7s %s' % ('articulo', 'sucursal', 'talla', 'filas -> sku/stock (pt_id)'))
    print('  ' + '-' * 104)
    for p in top_pares:
        prod = prods.get(p['producto_id'])
        detalle = ' | '.join('%s/%su (pt %s)' % (f['sku'], f['stock'] or 0, f['id'])
                             for f in sorted(filas.get((p['producto_id'], p['t']), []), key=lambda x: x['id']))
        flag = '  <<< STOCK EN AMBAS' if p['con_stock'] > 1 else ''
        print('  %-22s %-14s %-7s %s%s' % (
            (prod.articulo if prod else '?')[:22],
            ((prod.sucursal.alias if prod and prod.sucursal else '?'))[:14],
            p['t'][:7], detalle, flag))


# ----------------------------------------------------------------------
# 4. EL ARTICULO DEL CASO: donde quedo el stock, los lotes y el kardex
# ----------------------------------------------------------------------
sep('4. 403718L-BKSL: SKUs VIEJOS vs NUEVOS (stock, lotes FIFO y kardex)')

for etiqueta, skus in (('VIEJOS (4805xxx)', SKUS_VIEJOS), ('NUEVOS (4839xxx)', SKUS_NUEVOS)):
    print('\n  --- SKUs %s ---' % etiqueta)
    filas = (Producto_Talla.objects.filter(sku__in=skus)
             .values('producto__sucursal__alias')
             .annotate(n=Count('id'), s=Sum('stock'))
             .order_by('producto__sucursal__alias'))
    print('    stock por sucursal:')
    for f in filas:
        print('      %-14s tallas=%-4s unidades=%s' % (f['producto__sucursal__alias'] or '?', f['n'], f['s'] or 0))

    lotes = (LoteProducto.objects.filter(producto_talla__sku__in=skus)
             .values('producto_talla__producto__sucursal__alias')
             .annotate(n=Count('id'), disp=Sum('cantidad_disponible'))
             .order_by('producto_talla__producto__sucursal__alias'))
    print('    lotes FIFO:')
    if not lotes:
        print('      (ninguno)')
    for l in lotes:
        print('      %-14s lotes=%-4s disponibles=%s'
              % (l['producto_talla__producto__sucursal__alias'] or '?', l['n'], l['disp'] or 0))

    movs = (Movimientos_Producto.objects.filter(ProductoTalla__sku__in=skus)
            .values('concepto', 'tipo_movimiento')
            .annotate(n=Count('id'))
            .order_by('-n')[:12])
    print('    kardex (top conceptos):')
    if not movs:
        print('      (sin movimientos)')
    for m in movs:
        print('      %-28s %-10s %s' % (m['concepto'], m['tipo_movimiento'], m['n']))


# ----------------------------------------------------------------------
# 5. RESUMEN
# ----------------------------------------------------------------------
sep('RESUMEN EJECUTIVO')
print('  1. Fichas duplicadas (articulo, sucursal) ....... %s grupos / %s fichas sobrantes'
      % (total_grupos, fichas_sobrantes))
print('  2. De esos, con PRECIOS DISTINTOS ............... %s  (son los que sacan etiquetas malas)'
      % len(peligrosos))
print('  3. De esos, con fichas creadas en 30 dias ....... %s  (problema %s)'
      % (len(recientes), 'VIVO' if recientes else 'inactivo'))
print('  4. Tallas repetidas dentro de una ficha ......... %s pares en %s fichas'
      % (len(pares), fichas_afectadas))
print('  5. De esos, con stock partido en 2 filas ........ %s' % len(partidos))
print('\n(read-only: no se modifico nada)')
