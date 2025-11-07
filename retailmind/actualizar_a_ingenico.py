"""
Script para actualizar todos los terminales POS a Ingenico DESK 3500
Ejecutar desde el directorio retailmind/
"""

import os
import sys
import django

# Configurar Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from app.models import ConfiguracionPOS

print('='*70)
print('  🔧 ACTUALIZACIÓN DE TERMINALES POS A INGENICO DESK 3500')
print('='*70)

# Mostrar terminales actuales
terminales = ConfiguracionPOS.objects.all()
count = terminales.count()

if count == 0:
    print('\n❌ No hay terminales configurados en la base de datos')
    sys.exit(1)

print(f'\n📋 TERMINALES ENCONTRADOS: {count}\n')

for i, t in enumerate(terminales, 1):
    activo = '✅' if t.activo else '❌'
    print(f'{i}. {activo} ID:{t.id} | {t.nombre}')
    print(f'   Tipo actual: {t.get_tipo_pos_display()}')
    print(f'   Puerto: {t.puerto_conexion}')
    print(f'   Sucursal: {t.sucursal.alias}')
    print()

print('='*70)
print('📝 TIPOS DISPONIBLES:')
print('='*70)
print('1. INGENICO_DESK  → Ingenico DESK 3500/5000')
print('2. INGENICO_3500  → Ingenico 3500')
print('3. VERIFONE_VX520 → Verifone VX520')
print('4. OTRO           → Otro tipo')
print('='*70)

# Para Ingenico DESK 3500, usar directamente el tipo 1
tipo_seleccion = input('\n👉 Seleccione el tipo (1-4) [1 para INGENICO_DESK]: ').strip() or '1'

tipos_map = {
    '1': ('INGENICO_DESK', 'Ingenico DESK'),
    '2': ('INGENICO_3500', 'Ingenico 3500'),
    '3': ('VERIFONE_VX520', 'Verifone VX520'),
    '4': ('OTRO', 'Otro')
}

if tipo_seleccion not in tipos_map:
    print('\n❌ Opción inválida')
    sys.exit(1)

tipo_codigo, tipo_nombre = tipos_map[tipo_seleccion]

print(f'\n⚠️  Se actualizarán {count} terminal(es) a: {tipo_nombre}')
confirmacion = input('¿Continuar? (s/n): ').strip().lower()

if confirmacion not in ['s', 'si', 'y', 'yes']:
    print('\n❌ Actualización cancelada')
    sys.exit(0)

# Actualizar
print(f'\n🔄 Actualizando {count} terminal(es)...')

try:
    actualizados = terminales.update(tipo_pos=tipo_codigo)
    
    print(f'\n✅ {actualizados} terminal(es) actualizados exitosamente')
    print('\n📋 RESULTADO:')
    print('='*70)
    
    for t in ConfiguracionPOS.objects.all():
        print(f'✅ {t.nombre} → {t.get_tipo_pos_display()}')
    
    print('='*70)
    print('\n✅ ¡ACTUALIZACIÓN COMPLETADA!')
    print('\n👉 PRÓXIMOS PASOS:')
    print('   1. Refresca la página: http://localhost:8000/app/pos/transbank/')
    print('   2. Verifica que ahora aparezcan como "Ingenico DESK"')
    print('   3. Intenta conectar con "Autodetectar POS"')
    print()
    
except Exception as e:
    print(f'\n❌ Error actualizando: {e}')
    sys.exit(1)

