"""
Script de diagnóstico para verificar por qué no aparecen DTEs en recepción.
Ejecutar desde: python manage.py shell < DIAGNOSTICO_RECEPCION_DTE.py
"""

from app.models import Dte, Movimientos_Producto, Sucursal
from django.db.models import Q

print("\n" + "="*80)
print("🔍 DIAGNÓSTICO: ¿Por qué no aparecen DTEs en recepción?")
print("="*80 + "\n")

# 1. Verificar DTEs de tipo TRASPASO
print("1️⃣ DTEs con tipo_transaccion='TRASPASO':")
dtes_traspaso = Dte.objects.filter(tipo_transaccion='TRASPASO')
print(f"   Total: {dtes_traspaso.count()}")

if dtes_traspaso.exists():
    for dte in dtes_traspaso[:5]:  # Mostrar primeros 5
        print(f"   - DTE #{dte.numero_documento}")
        print(f"     Estado DTE: {dte.estado_dte}")
        print(f"     Fecha Recepción: {dte.fecha_recepcion or 'NULL'}")
        print(f"     Sucursal: {dte.sucursal.alias if dte.sucursal else 'Sin sucursal'}")
        print()

# 2. Verificar movimientos PENDIENTE_RECEPCION
print("\n2️⃣ Movimientos con estado='PENDIENTE_RECEPCION':")
movs_pendientes = Movimientos_Producto.objects.filter(estado='PENDIENTE_RECEPCION')
print(f"   Total: {movs_pendientes.count()}")

if movs_pendientes.exists():
    for mov in movs_pendientes[:5]:
        print(f"   - Movimiento ID: {mov.id}")
        print(f"     Concepto: {mov.concepto}")
        print(f"     Tipo: {mov.tipo_movimiento}")
        print(f"     DTE: #{mov.dte.numero_documento if mov.dte else 'Sin DTE'}")
        print(f"     Origen: {mov.sucursal_origen.alias if mov.sucursal_origen else 'Sin origen'}")
        print(f"     Destino: {mov.sucursal_destino.alias if mov.sucursal_destino else 'Sin destino'}")
        print()

# 3. Verificar movimientos con estados antiguos
print("\n3️⃣ Movimientos con estado='PENDIENTE' (estado antiguo):")
movs_pendiente_viejo = Movimientos_Producto.objects.filter(
    estado='PENDIENTE',
    concepto='TRASPASO_SALIDA'
)
print(f"   Total: {movs_pendiente_viejo.count()}")

if movs_pendiente_viejo.exists():
    print("   ⚠️ PROBLEMA DETECTADO:")
    print("   Tienes movimientos con el estado antiguo 'PENDIENTE'")
    print("   Necesitas actualizarlos a 'PENDIENTE_RECEPCION'")
    print("\n   📝 SQL para corregir:")
    print("   UPDATE app_movimientos_producto")
    print("   SET estado = 'PENDIENTE_RECEPCION'")
    print("   WHERE estado = 'PENDIENTE'")
    print("     AND concepto = 'TRASPASO_SALIDA'")
    print("     AND tipo_movimiento = 'TRASPASO';")
    print()
    
    for mov in movs_pendiente_viejo[:3]:
        print(f"   - Movimiento ID: {mov.id}")
        print(f"     DTE: #{mov.dte.numero_documento if mov.dte else 'Sin DTE'}")
        print(f"     Origen: {mov.sucursal_origen.alias if mov.sucursal_origen else 'Sin origen'}")
        print(f"     Destino: {mov.sucursal_destino.alias if mov.sucursal_destino else 'Sin destino'}")
        print()

# 4. Query completa que usa la API
print("\n4️⃣ Simulando query de recepciones_pendientes_api:")
print("   Condiciones:")
print("   - tipo_transaccion='TRASPASO'")
print("   - estado_dte='EMITIDO'")
print("   - fecha_recepcion__isnull=True")
print("   - dte_movimientos__concepto='TRASPASO_SALIDA'")
print("   - dte_movimientos__tipo_movimiento='EGRESO'")
print("   - dte_movimientos__estado='PENDIENTE_RECEPCION'")
print()

sucursales = Sucursal.objects.all()[:3]
for suc in sucursales:
    print(f"\n   Para sucursal: {suc.alias} (ID: {suc.id})")
    
    query = Dte.objects.filter(
        tipo_transaccion='TRASPASO',
        estado_dte='EMITIDO',
        fecha_recepcion__isnull=True,
        dte_movimientos__concepto='TRASPASO_SALIDA',
        dte_movimientos__tipo_movimiento='EGRESO',
        dte_movimientos__estado='PENDIENTE_RECEPCION',
        dte_movimientos__sucursal_destino_id=suc.id
    ).distinct()
    
    print(f"   Resultado: {query.count()} DTEs")
    
    if query.exists():
        for dte in query[:2]:
            print(f"   ✅ DTE #{dte.numero_documento} - {dte.tipo_documento}")

# 5. Resumen y Solución
print("\n" + "="*80)
print("📋 RESUMEN Y SOLUCIÓN")
print("="*80)

total_traspaso = Dte.objects.filter(tipo_transaccion='TRASPASO').count()
total_pendiente_nuevo = Movimientos_Producto.objects.filter(estado='PENDIENTE_RECEPCION').count()
total_pendiente_viejo = Movimientos_Producto.objects.filter(estado='PENDIENTE', concepto='TRASPASO_SALIDA').count()

print(f"\n📊 Estadísticas:")
print(f"   DTEs TRASPASO: {total_traspaso}")
print(f"   Movimientos PENDIENTE_RECEPCION (nuevo): {total_pendiente_nuevo}")
print(f"   Movimientos PENDIENTE (viejo): {total_pendiente_viejo}")

if total_pendiente_viejo > 0 and total_pendiente_nuevo == 0:
    print("\n🔧 SOLUCIÓN DETECTADA:")
    print("   Tienes DTEs emitidos con el estado antiguo.")
    print("   Necesitas ejecutar esta SQL para actualizarlos:")
    print()
    print("   UPDATE app_movimientos_producto")
    print("   SET estado = 'PENDIENTE_RECEPCION',")
    print("       tipo_movimiento = 'EGRESO'")
    print("   WHERE estado = 'PENDIENTE'")
    print("     AND concepto = 'TRASPASO_SALIDA'")
    print("     AND tipo_movimiento = 'TRASPASO';")
    print()
elif total_pendiente_nuevo == 0 and total_traspaso == 0:
    print("\n💡 SOLUCIÓN:")
    print("   No tienes DTEs de traspaso emitidos.")
    print("   Emite un DTE interno desde /app/emisionDTE/ y vuelve a verificar.")
    print()
else:
    print("\n✅ Todo parece correcto.")
    print("   Verifica que estés conectado a la sucursal correcta.")
    print()

print("="*80 + "\n")

