#!/usr/bin/env python
"""
Eliminar el crédito CR-2025-0001 que está causando conflicto

Uso:
    python manage.py shell
    >>> exec(open('eliminar_credito_duplicado.py').read())
"""

from app.models import CreditoTrabajador

print("=" * 60)
print("ELIMINAR CRÉDITO CR-2025-0001")
print("=" * 60)

try:
    credito = CreditoTrabajador.objects.get(numero_credito='CR-2025-0001')
    
    print(f"\n✅ Encontrado:")
    print(f"   ID: {credito.id}")
    print(f"   Trabajador: {credito.trabajador.nombre}")
    print(f"   Monto: ${credito.monto_solicitado:,.0f}")
    print(f"   Estado: {credito.get_estado_display()}")
    print(f"   Sucursal: {credito.sucursal.alias}")
    
    # Eliminar
    credito.delete()
    
    print("\n✅ ELIMINADO EXITOSAMENTE")
    print("\nAhora puede crear créditos sin problemas.")
    
except CreditoTrabajador.DoesNotExist:
    print("\n❌ No existe el crédito CR-2025-0001")
    print("El error puede ser otro. Verificando...")
    
    # Mostrar todos los créditos del 2025
    creditos_2025 = CreditoTrabajador.objects.filter(
        numero_credito__startswith='CR-2025'
    ).order_by('numero_credito')
    
    if creditos_2025.exists():
        print(f"\nCréditos del 2025 encontrados: {creditos_2025.count()}")
        for c in creditos_2025:
            print(f"   • {c.numero_credito} | {c.trabajador.nombre} | {c.sucursal.alias}")
    else:
        print("\nNo hay créditos del 2025")

print("\n" + "=" * 60)

