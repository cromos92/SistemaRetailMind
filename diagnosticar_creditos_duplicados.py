#!/usr/bin/env python
"""
Script para diagnosticar créditos duplicados en RetailMind

Uso:
    python manage.py shell < diagnosticar_creditos_duplicados.py

O desde el shell de Django:
    python manage.py shell
    >>> exec(open('diagnosticar_creditos_duplicados.py').read())
"""

from app.models import CreditoTrabajador
from django.db.models import Count

print("=" * 70)
print("DIAGNÓSTICO DE CRÉDITOS DUPLICADOS - RetailMind")
print("=" * 70)

# 1. Estadísticas generales
total_creditos = CreditoTrabajador.objects.count()
numeros_unicos = CreditoTrabajador.objects.values('numero_credito').distinct().count()

print(f"\n📊 ESTADÍSTICAS GENERALES:")
print(f"   Total de créditos en base de datos: {total_creditos}")
print(f"   Números de crédito únicos: {numeros_unicos}")

if total_creditos == numeros_unicos:
    print(f"   ✅ ESTADO: Todos los números son únicos (OK)")
else:
    diferencia = total_creditos - numeros_unicos
    print(f"   ⚠️ ESTADO: Hay {diferencia} crédito(s) con número duplicado")

# 2. Buscar duplicados específicos
duplicados = (
    CreditoTrabajador.objects
    .values('numero_credito')
    .annotate(count=Count('id'))
    .filter(count__gt=1)
    .order_by('numero_credito')
)

if not duplicados:
    print("\n✅ RESULTADO: No se encontraron duplicados")
    print("\n   El sistema está funcionando correctamente.")
    print("   Puede crear nuevos créditos sin problemas.")
else:
    print(f"\n⚠️ RESULTADO: Se encontraron {len(duplicados)} número(s) de crédito duplicado(s)")
    print("\n" + "=" * 70)
    print("DETALLE DE DUPLICADOS:")
    print("=" * 70)
    
    for i, dup in enumerate(duplicados, 1):
        numero = dup['numero_credito']
        cantidad = dup['count']
        
        print(f"\n{i}. NÚMERO DUPLICADO: {numero} ({cantidad} veces)")
        print("-" * 70)
        
        # Mostrar todos los créditos con ese número
        creditos = CreditoTrabajador.objects.filter(numero_credito=numero).order_by('id')
        
        for j, credito in enumerate(creditos, 1):
            print(f"   [{j}] ID: {credito.id}")
            print(f"       Trabajador: {credito.trabajador.nombre}")
            print(f"       RUT: {credito.trabajador.rut or 'N/A'}")
            print(f"       Monto: ${credito.monto_solicitado:,.0f}")
            print(f"       Estado: {credito.get_estado_display()}")
            print(f"       Fecha Solicitud: {credito.fecha_solicitud.strftime('%d/%m/%Y %H:%M')}")
            print(f"       Empresa: {credito.empresa_origen.nombre}")
            print()
    
    print("=" * 70)
    print("\n💡 SOLUCIÓN:")
    print("   1. Ejecute el script: renumerar_creditos_duplicados.py")
    print("      python manage.py shell < renumerar_creditos_duplicados.py")
    print()
    print("   2. O renumere manualmente desde el admin de Django")
    print("=" * 70)

# 3. Mostrar últimos créditos creados
print("\n📋 ÚLTIMOS 10 CRÉDITOS CREADOS:")
print("-" * 70)

ultimos = CreditoTrabajador.objects.order_by('-id')[:10]
for credito in ultimos:
    print(f"   • {credito.numero_credito} | {credito.trabajador.nombre[:30]:30} | "
          f"${credito.monto_solicitado:>10,.0f} | {credito.get_estado_display():15} | "
          f"{credito.fecha_solicitud.strftime('%d/%m/%Y')}")

print("\n" + "=" * 70)
print("FIN DEL DIAGNÓSTICO")
print("=" * 70)

