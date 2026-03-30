import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
django.setup()

from app.models import *
from django.db.models import Count, Sum, Avg

print('=== TABLAS DE PREDICCION ===')
print(f'ClasificacionABC:     {ClasificacionABC.objects.count()}')
print(f'VelocidadHistorica:   {VelocidadHistorica.objects.count()}')
print(f'CurvaTalles:          {CurvaTalles.objects.count()}')
print(f'PrediccionDemanda:    {PrediccionDemanda.objects.count()}')
print(f'SugerenciaCompra:     {SugerenciaCompra.objects.count()}')
print(f'AlertaVelocidad:      {AlertaVelocidad.objects.count()}')
print(f'AlertaQuiebreTalle:   {AlertaQuiebreTalle.objects.count()}')
print(f'StockInicialTemp:     {StockInicialTemporada.objects.count()}')
print(f'ConfigPrediccion:     {ConfiguracionPrediccion.objects.count()}')

print()
print('=== CLASIFICACION ABC ===')
for r in ClasificacionABC.objects.values('clasificacion_abc').annotate(n=Count('id')).order_by('clasificacion_abc'):
    print(f'  {r["clasificacion_abc"]}: {r["n"]}')

print()
print('=== CLASIFICACION XYZ ===')
for r in ClasificacionABC.objects.values('clasificacion_xyz').annotate(n=Count('id')).order_by('clasificacion_xyz'):
    print(f'  {r["clasificacion_xyz"]}: {r["n"]}')

print()
print('=== ALERTAS VELOCIDAD POR URGENCIA ===')
for r in AlertaVelocidad.objects.values('urgencia').annotate(n=Count('id')).order_by('urgencia'):
    print(f'  {r["urgencia"]}: {r["n"]}')

print()
print('=== ALERTAS QUIEBRE POR URGENCIA ===')
for r in AlertaQuiebreTalle.objects.values('urgencia').annotate(n=Count('id')).order_by('urgencia'):
    print(f'  {r["urgencia"]}: {r["n"]}')

print()
print('=== SUGERENCIAS POR ORIGEN ===')
for r in SugerenciaCompra.objects.values('origen').annotate(n=Count('id')).order_by('origen'):
    print(f'  {r["origen"]}: {r["n"]}')

print()
print('=== PREDICCION DEMANDA POR METODO ===')
for r in PrediccionDemanda.objects.values('metodo_usado').annotate(n=Count('id')).order_by('metodo_usado'):
    print(f'  {r["metodo_usado"]}: {r["n"]}')

print()
print('=== VELOCIDAD HISTORICA: MUESTRA ===')
for v in VelocidadHistorica.objects.all()[:5]:
    marca_str = v.marca.valor if v.marca else 'Todas'
    print(f'  marca={marca_str} | cat={v.categoria} | gen={v.genero} | temp={v.temporada} | p50={v.velocidad_semanas_p50} | sell={v.sellthrough_p50} | n={v.total_articulos_base}')

print()
print('=== SAMPLE ALERTAS VELOCIDAD (top urgencia) ===')
for a in AlertaVelocidad.objects.select_related('articulo').order_by('-urgencia')[:5]:
    print(f'  {a.articulo.articulo} | urg={a.urgencia} | stock_rest={a.stock_restante} | sem_stock={a.semanas_stock_restante} | vel_real={a.velocidad_real_semanas} | vel_norm={a.velocidad_normal_semanas} | ratio={a.ratio_velocidad}')

print()
print('=== SAMPLE SUGERENCIAS ===')
for s in SugerenciaCompra.objects.select_related('articulo_talle', 'articulo_talle__producto').all()[:5]:
    art = s.articulo_talle.producto.articulo if s.articulo_talle else '?'
    talla = s.articulo_talle.talla if s.articulo_talle else '?'
    print(f'  {art} | talla={talla} | sugerido={s.unidades_sugeridas} | stock={s.stock_actual} | transito={s.unidades_en_transito} | a_pedir={s.unidades_a_pedir} | origen={s.origen}')

print()
print('=== SAMPLE CLASIFICACION ABC (top ventas) ===')
for c in ClasificacionABC.objects.select_related('articulo').order_by('-ventas_totales_periodo')[:8]:
    print(f'  {c.articulo.articulo} | ABC={c.clasificacion_abc} XYZ={c.clasificacion_xyz} | ventas={c.ventas_totales_periodo} | cv={c.coeficiente_variacion} | cens={c.demanda_censurada}')

print()
print('=== MARCAS EN CLASIFICACION ===')
from app.services.prediccion_compras import _get_marca_nombre
marcas = {}
for c in ClasificacionABC.objects.select_related('articulo__atributo1').all():
    m = _get_marca_nombre(c.articulo)
    marcas[m] = marcas.get(m, 0) + 1
for m, n in sorted(marcas.items(), key=lambda x: -x[1])[:15]:
    print(f'  {m}: {n} productos')

print()
print('=== PRODUCTOS POR TEMPORADA ===')
for r in Producto.objects.values('temporada').annotate(n=Count('id')).order_by('-n')[:10]:
    print(f'  temporada="{r["temporada"]}": {r["n"]}')

print()
print('=== CURVA TALLES MUESTRA ===')
for ct in CurvaTalles.objects.order_by('-porcentaje')[:10]:
    marca_str = ct.marca.valor if ct.marca else 'Todas'
    print(f'  {marca_str} | {ct.categoria} | {ct.genero} | talle={ct.talle} | %={float(ct.porcentaje)*100:.1f}% | base={ct.total_ventas_base}')

print()
print('=== PREDICCION DEMANDA MUESTRA (top) ===')
for p in PrediccionDemanda.objects.select_related('articulo').order_by('-unidades_predichas')[:8]:
    print(f'  {p.articulo.articulo} | unidades={p.unidades_predichas} | metodo={p.metodo_usado} | conf={p.confianza} | temp={p.temporada} anio={p.anio}')

print()
print('=== QUIEBRES TALLE MUESTRA ===')
for q in AlertaQuiebreTalle.objects.select_related('articulo').order_by('-porcentaje_demanda_sin_cubrir')[:5]:
    print(f'  {q.articulo.articulo} | urg={q.urgencia} | agotados={q.talles_agotados} | criticos={q.talles_agotados_criticos} | %sin_cubrir={q.porcentaje_demanda_sin_cubrir} | stock_rest={q.stock_total_restante}')

print()
print('=== RESUMEN GENERAL ===')
sug = SugerenciaCompra.objects.aggregate(total=Sum('unidades_a_pedir'))
pred = PrediccionDemanda.objects.aggregate(total=Sum('unidades_predichas'), avg_conf=Avg('confianza'))
print(f'  Total unidades a pedir (sugerencias): {sug["total"]}')
print(f'  Total unidades predichas (demanda):   {pred["total"]}')
print(f'  Confianza promedio predicciones:       {pred["avg_conf"]}')
