"""
Script de prueba para verificar el comando clean_migration_data.py

Este script NO ejecuta el comando, solo verifica:
1. Que el archivo existe
2. Que se puede importar correctamente
3. Que tiene los métodos necesarios
4. Que las importaciones son correctas
"""

import os
import sys
from pathlib import Path

# Agregar el directorio raíz al path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

def verificar_comando():
    """Verifica que el comando está correctamente configurado"""
    
    print("=" * 80)
    print("🔍 VERIFICACIÓN DEL COMANDO clean_migration_data")
    print("=" * 80)
    print()
    
    # 1. Verificar que el archivo existe
    print("1. Verificando que el archivo existe...")
    comando_path = BASE_DIR / 'app' / 'management' / 'commands' / 'clean_migration_data.py'
    
    if not comando_path.exists():
        print(f"   ❌ ERROR: El archivo no existe en {comando_path}")
        return False
    
    print(f"   ✅ El archivo existe: {comando_path}")
    print()
    
    # 2. Verificar que se puede importar
    print("2. Verificando que se puede importar...")
    try:
        from app.management.commands.clean_migration_data import Command
        print("   ✅ El comando se puede importar correctamente")
    except ImportError as e:
        print(f"   ❌ ERROR al importar: {e}")
        return False
    print()
    
    # 3. Verificar que tiene los métodos necesarios
    print("3. Verificando métodos necesarios...")
    metodos_requeridos = [
        'handle',
        '_contar_registros',
        '_mostrar_conteos',
        '_eliminar_datos',
        '_eliminar_tabla',
        '_mostrar_resumen'
    ]
    
    for metodo in metodos_requeridos:
        if hasattr(Command, metodo):
            print(f"   ✅ Método {metodo} encontrado")
        else:
            print(f"   ❌ ERROR: Método {metodo} NO encontrado")
            return False
    print()
    
    # 4. Verificar atributos de la clase
    print("4. Verificando atributos...")
    if hasattr(Command, 'help'):
        print(f"   ✅ Atributo 'help' encontrado")
        print(f"      Descripción: {Command.help.strip()[:80]}...")
    else:
        print(f"   ⚠️  ADVERTENCIA: Atributo 'help' NO encontrado")
    print()
    
    # 5. Verificar que el comando tiene argumentos
    print("5. Verificando método add_arguments...")
    if hasattr(Command, 'add_arguments'):
        print(f"   ✅ Método add_arguments encontrado")
    else:
        print(f"   ⚠️  ADVERTENCIA: Método add_arguments NO encontrado")
    print()
    
    # 6. Leer el contenido del archivo y buscar palabras clave
    print("6. Verificando contenido del archivo...")
    with open(comando_path, 'r', encoding='utf-8') as f:
        contenido = f.read()
    
    palabras_clave = [
        'transaction.atomic',
        'esProveedor=False',
        'Movimientos_Producto',
        'Dte_Detalle_Pago',
        'Dte_Productos',
        'Ticket_Productos',
        '--force'
    ]
    
    for palabra in palabras_clave:
        if palabra in contenido:
            print(f"   ✅ Palabra clave '{palabra}' encontrada")
        else:
            print(f"   ❌ ERROR: Palabra clave '{palabra}' NO encontrada")
            return False
    print()
    
    # 7. Contar líneas de código
    print("7. Estadísticas del archivo...")
    lineas = contenido.split('\n')
    lineas_codigo = [l for l in lineas if l.strip() and not l.strip().startswith('#')]
    lineas_comentarios = [l for l in lineas if l.strip().startswith('#')]
    lineas_vacias = [l for l in lineas if not l.strip()]
    
    print(f"   • Total de líneas: {len(lineas)}")
    print(f"   • Líneas de código: {len(lineas_codigo)}")
    print(f"   • Líneas de comentarios: {len(lineas_comentarios)}")
    print(f"   • Líneas vacías: {len(lineas_vacias)}")
    print()
    
    # 8. Resultado final
    print("=" * 80)
    print("✅ VERIFICACIÓN COMPLETADA EXITOSAMENTE")
    print("=" * 80)
    print()
    print("El comando clean_migration_data.py está correctamente configurado.")
    print()
    print("Para ejecutar el comando:")
    print("  python manage.py clean_migration_data")
    print("  python manage.py clean_migration_data --force")
    print()
    
    return True

if __name__ == '__main__':
    try:
        exito = verificar_comando()
        sys.exit(0 if exito else 1)
    except Exception as e:
        print(f"\n❌ ERROR INESPERADO: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

