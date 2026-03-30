import os, django, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from app.services.prediccion_compras import generar_sugerencias_compra
from app.models.predicciones import PrediccionDemanda, SugerenciaCompra

print(f"Predicciones en BD: {PrediccionDemanda.objects.count()}")
print(f"Sugerencias actuales: {SugerenciaCompra.objects.count()}")
print("\nRegenerando sugerencias de compra...")

inicio = time.time()
resultado = generar_sugerencias_compra()
elapsed = round(time.time() - inicio, 1)

print(f"\nSugerencias generadas: {resultado}")
print(f"Tiempo: {elapsed}s")
print(f"Sugerencias en BD ahora: {SugerenciaCompra.objects.count()}")
