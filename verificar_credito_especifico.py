#!/usr/bin/env python
"""
Verificar si existe el crédito CR-2025-0001 y ver detalles

Uso:
    python manage.py shell < verificar_credito_especifico.py
"""

from app.models import CreditoTrabajador
from django.utils import timezone

print("=" * 70)
print("VERIFICACIÓN DE CRÉDITO CR-2025-0001")
print("=" * 70)

# Buscar el crédito específico
try:
    credito = CreditoTrabajador.objects.get(numero_credito='CR-2025-0001')
    
    print("\n✅ SÍ EXISTE el crédito CR-2025-0001")
    print("\nDETALLES:")
    print(f"   ID: {credito.id}")
    print(f"   Número: {credito.numero_credito}")
    print(f"   Trabajador: {credito.trabajador.nombre}")
    print(f"   RUT: {credito.trabajador.rut or 'N/A'}")
    print(f"   Monto Solicitado: ${credito.monto_solicitado:,.0f}")
    print(f"   Estado: {credito.get_estado_display()}")
    print(f"   Fecha Solicitud: {credito.fecha_solicitud.strftime('%d/%m/%Y %H:%M')}")
    print(f"   Empresa: {credito.empresa_origen.nombre}")
    print(f"   Sucursal: {credito.sucursal.alias}")
    
    print("\n" + "=" * 70)
    print("SOLUCIÓN:")
    print("=" * 70)
    print("\nEste crédito YA EXISTE. Para crear uno nuevo:")
    print("\n   OPCIÓN 1 - Eliminar este crédito (si es de prueba):")
    print("   1. Ve al admin: http://127.0.0.1:8000/admin/app/creditotrabajador/")
    print(f"   2. Busca el ID: {credito.id}")
    print("   3. Elimínalo")
    print("\n   OPCIÓN 2 - Renumerar este crédito:")
    print("   Ejecuta en el shell:")
    print(f"   >>> from app.models import CreditoTrabajador")
    print(f"   >>> c = CreditoTrabajador.objects.get(id={credito.id})")
    print(f"   >>> c.numero_credito = 'CR-2025-0001-OLD'")
    print(f"   >>> c.save()")
    
except CreditoTrabajador.DoesNotExist:
    print("\n❌ NO EXISTE el crédito CR-2025-0001")
    print("\nEsto es extraño... el error dice que existe pero no lo encuentro.")
    print("\nRevisando todos los créditos del 2025:")
    
    creditos_2025 = CreditoTrabajador.objects.filter(
        numero_credito__startswith='CR-2025'
    ).order_by('numero_credito')
    
    if creditos_2025.exists():
        print(f"\nEncontrados {creditos_2025.count()} crédito(s) del 2025:")
        for c in creditos_2025:
            print(f"   • {c.numero_credito} | ID: {c.id} | {c.trabajador.nombre}")
    else:
        print("\n   No hay ningún crédito del 2025")

# Mostrar cuál sería el próximo número
print("\n" + "=" * 70)
print("PRÓXIMO NÚMERO QUE SE GENERARÍA:")
print("=" * 70)

año_actual = timezone.now().year
ultimo_credito = CreditoTrabajador.objects.filter(
    numero_credito__startswith=f'CR-{año_actual}'
).order_by('-numero_credito').first()

if ultimo_credito:
    try:
        ultimo_num = int(ultimo_credito.numero_credito.split('-')[-1])
        proximo_num = ultimo_num + 1
        print(f"\n   Último crédito: {ultimo_credito.numero_credito}")
        print(f"   Próximo número: CR-{año_actual}-{proximo_num:04d}")
    except:
        print(f"\n   Último crédito: {ultimo_credito.numero_credito}")
        print("   ⚠️ Error al calcular próximo número")
else:
    print(f"\n   No hay créditos del {año_actual}")
    print(f"   Próximo número: CR-{año_actual}-0001")

print("\n" + "=" * 70)

