import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from app.models.predicciones import *
from app.models.catalogo import Producto, Producto_Talla
from django.db.models import Count, Sum, Q

print("=" * 60)
print("ESTADO COMPLETO DE TABLAS DE PREDICCIÓN")
print("=" * 60)

print(f"\n  ClasificacionABC:    {ClasificacionABC.objects.count()}")
print(f"  VelocidadHistorica:  {VelocidadHistorica.objects.count()}")
print(f"  CurvaTalles:         {CurvaTalles.objects.count()}")
print(f"  PrediccionDemanda:   {PrediccionDemanda.objects.count()}")
print(f"  SugerenciaCompra:    {SugerenciaCompra.objects.count()}")
print(f"  AlertaVelocidad:     {AlertaVelocidad.objects.count()}")
print(f"  AlertaQuiebreTalle:  {AlertaQuiebreTalle.objects.count()}")

print("\n--- ABC por clase ---")
for row in ClasificacionABC.objects.values('clasificacion_abc').annotate(c=Count('id')).order_by('clasificacion_abc'):
    print(f"  {row['clasificacion_abc']}: {row['c']}")

print("\n--- XYZ por clase ---")
for row in ClasificacionABC.objects.values('clasificacion_xyz').annotate(c=Count('id')).order_by('clasificacion_xyz'):
    print(f"  {row['clasificacion_xyz']}: {row['c']}")

print("\n--- Alertas velocidad por urgencia ---")
for row in AlertaVelocidad.objects.filter(resuelta=False).values('urgencia').annotate(c=Count('id')).order_by('urgencia'):
    print(f"  {row['urgencia']}: {row['c']}")

print("\n--- Alertas quiebre por urgencia ---")
for row in AlertaQuiebreTalle.objects.filter(resuelta=False).values('urgencia').annotate(c=Count('id')).order_by('urgencia'):
    print(f"  {row['urgencia']}: {row['c']}")

print("\n--- KPIs (lo que mostraría el dashboard) ---")
vel = AlertaVelocidad.objects.filter(resuelta=False)
quiebre = AlertaQuiebreTalle.objects.filter(resuelta=False)
sug = SugerenciaCompra.objects.filter(aprobada=False)
print(f"  Alertas Críticas: {vel.filter(urgencia='CRITICA').count() + quiebre.filter(urgencia='CRITICA').count()}")
print(f"  Alertas Velocidad: {vel.count()}")
print(f"  Quiebres Talle: {quiebre.count()}")
print(f"  Sugerencias Pendientes: {sug.count()}")
print(f"  Unidades a Pedir: {sug.aggregate(t=Sum('unidades_a_pedir'))['t'] or 0}")
print(f"  Productos Clase A: {ClasificacionABC.objects.filter(clasificacion_abc='A').count()}")

print("\n--- Por qué Sugerencias = 0? ---")
pred_sample = PrediccionDemanda.objects.order_by('-unidades_predichas')[:5]
for p in pred_sample:
    talles = list(Producto_Talla.objects.filter(producto=p.articulo).values_list('talla', flat=True))
    cat = p.articulo.categoria.nombre if p.articulo.categoria else 'SIN'
    genero = p.articulo.atributo3.valor if p.articulo.atributo3 else 'SIN'
    marca_id = p.articulo.atributo1_id
    curvas = CurvaTalles.objects.filter(marca_id=marca_id, categoria__icontains=cat[:5] if cat != 'SIN' else 'SIN', genero__icontains=genero[:5] if genero != 'SIN' else 'SIN').count()
    print(f"  Pred {p.articulo.articulo}: {p.unidades_predichas}u, talles={talles}, cat={cat}, gen={genero}, curvas_match={curvas}")

print("\n--- Muestra CurvaTalles ---")
for c in CurvaTalles.objects.all()[:10]:
    print(f"  marca={c.marca_id}, cat={c.categoria}, gen={c.genero}, talle={c.talle}, %={float(c.porcentaje)*100:.1f}%")
