import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
django.setup()

from app.models import Vendedor, Empresa

# Datos de los 6 vendedores faltantes (de MySQL ventas)
VENDEDORES = [
    {'codigo': '38', 'nombre': 'Samir Cardenas'},
    {'codigo': '44', 'nombre': 'Angelo Tebes'},
    {'codigo': '50', 'nombre': 'Luz Vera'},
    {'codigo': '53', 'nombre': 'Victor Hugo'},
    {'codigo': '57', 'nombre': 'Miguel Pesoa'},
    {'codigo': '75', 'nombre': 'Cristian Jaramillo'},
]

empresa_pao = Empresa.objects.filter(rut='78503140-7').first()
empresa_nick = Empresa.objects.filter(rut='76104936-4').first()

creados = 0
for v in VENDEDORES:
    existente = Vendedor.objects.filter(codigo_vendedor=v['codigo']).first()
    if existente:
        print(f'  Ya existe: codigo={v["codigo"]} id={existente.id} nombre="{existente.nombre}"')
        continue

    nuevo = Vendedor.objects.create(
        nombre=v['nombre'],
        codigo_vendedor=v['codigo'],
        rut='',
        empresa=empresa_pao,
        activo=True
    )
    print(f'  CREADO: id={nuevo.id} codigo={v["codigo"]} nombre="{v["nombre"]}"')
    creados += 1

print(f'\nTotal creados: {creados}')
print(f'Total vendedores: {Vendedor.objects.count()}')
