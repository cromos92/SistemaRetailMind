#!/usr/bin/env python
"""
Script para renumerar automáticamente créditos duplicados en RetailMind

⚠️ ADVERTENCIA: Este script modifica la base de datos
   Haga un backup antes de ejecutar

Uso:
    python manage.py shell < renumerar_creditos_duplicados.py

O desde el shell de Django:
    python manage.py shell
    >>> exec(open('renumerar_creditos_duplicados.py').read())
"""

from app.models import CreditoTrabajador
from django.db.models import Count
from django.db import transaction
import sys

print("=" * 70)
print("RENUMERACIÓN AUTOMÁTICA DE CRÉDITOS DUPLICADOS")
print("=" * 70)

# Buscar duplicados
duplicados = (
    CreditoTrabajador.objects
    .values('numero_credito')
    .annotate(count=Count('id'))
    .filter(count__gt=1)
    .order_by('numero_credito')
)

if not duplicados:
    print("\n✅ No hay créditos duplicados para renumerar")
    print("   El sistema está correcto. No se requiere acción.")
    sys.exit(0)

print(f"\n⚠️ Se encontraron {len(duplicados)} número(s) de crédito duplicado(s)")
print("\nESTRATEGIA DE RENUMERACIÓN:")
print("   • Se mantendrá el crédito más antiguo con su número original")
print("   • Los créditos duplicados más recientes serán renumerados")
print("   • Se asignará el siguiente número disponible de forma secuencial")

print("\n" + "-" * 70)
input("Presione ENTER para continuar o Ctrl+C para cancelar...")
print("-" * 70)

# Procesar renumeración
try:
    with transaction.atomic():
        creditos_renumerados = 0
        cambios_realizados = []
        
        for i, dup in enumerate(duplicados, 1):
            numero = dup['numero_credito']
            cantidad = dup['count']
            
            print(f"\n{i}. Procesando: {numero} ({cantidad} duplicados)")
            
            # Obtener todos los créditos con ese número, ordenados por ID (más antiguo primero)
            creditos = CreditoTrabajador.objects.filter(
                numero_credito=numero
            ).order_by('id')
            
            # Mantener el primero (más antiguo)
            primer_credito = creditos.first()
            print(f"   ✓ Manteniendo: {numero}")
            print(f"     ID: {primer_credito.id} | {primer_credito.trabajador.nombre} | "
                  f"${primer_credito.monto_solicitado:,.0f}")
            
            # Renumerar los demás
            for credito in creditos[1:]:
                # Extraer año del número original
                try:
                    partes = numero.split('-')
                    año = int(partes[1])
                except:
                    año = credito.fecha_solicitud.year
                
                # Buscar el siguiente número disponible para ese año
                # Intentar hasta encontrar uno que no exista
                nuevo_num = 1
                while True:
                    nuevo_numero = f"CR-{año}-{nuevo_num:04d}"
                    
                    # Verificar si ya existe
                    existe = CreditoTrabajador.objects.filter(
                        numero_credito=nuevo_numero
                    ).exclude(id=credito.id).exists()
                    
                    if not existe:
                        break
                    
                    nuevo_num += 1
                    
                    # Seguridad: no intentar más de 10000
                    if nuevo_num > 10000:
                        print(f"   ❌ ERROR: No se pudo encontrar número disponible para año {año}")
                        raise Exception("Límite de búsqueda excedido")
                
                # Guardar cambio
                antiguo_numero = credito.numero_credito
                credito.numero_credito = nuevo_numero
                credito.save()
                
                print(f"   ✏️ Renumerado: {antiguo_numero} → {nuevo_numero}")
                print(f"     ID: {credito.id} | {credito.trabajador.nombre} | "
                      f"${credito.monto_solicitado:,.0f}")
                
                cambios_realizados.append({
                    'id': credito.id,
                    'antiguo': antiguo_numero,
                    'nuevo': nuevo_numero,
                    'trabajador': credito.trabajador.nombre
                })
                
                creditos_renumerados += 1
        
        # Resumen
        print("\n" + "=" * 70)
        print("RESUMEN DE CAMBIOS:")
        print("=" * 70)
        print(f"✅ Se renumeraron {creditos_renumerados} crédito(s)")
        
        if cambios_realizados:
            print("\nDETALLE DE CAMBIOS:")
            print("-" * 70)
            for cambio in cambios_realizados:
                print(f"   • ID {cambio['id']}: {cambio['antiguo']} → {cambio['nuevo']}")
                print(f"     Trabajador: {cambio['trabajador']}")
        
        # Verificación final
        print("\n" + "=" * 70)
        print("VERIFICACIÓN FINAL:")
        print("=" * 70)
        
        total = CreditoTrabajador.objects.count()
        unicos = CreditoTrabajador.objects.values('numero_credito').distinct().count()
        
        print(f"Total de créditos: {total}")
        print(f"Números únicos: {unicos}")
        
        if total == unicos:
            print("✅ ÉXITO: Todos los números son únicos ahora")
        else:
            print(f"⚠️ ADVERTENCIA: Aún hay {total - unicos} duplicado(s)")
        
        print("\n" + "=" * 70)
        print("CAMBIOS GUARDADOS EXITOSAMENTE")
        print("=" * 70)

except Exception as e:
    print("\n" + "=" * 70)
    print("❌ ERROR DURANTE LA RENUMERACIÓN:")
    print("=" * 70)
    print(f"   {str(e)}")
    print("\n   Los cambios NO se guardaron (rollback automático)")
    print("   La base de datos está sin cambios")
    print("=" * 70)
    raise

