#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script para limpiar código duplicado en generacionVentas.html
"""

archivo = r'c:\DjangoProyects\retailmind\SistemaRetailMind\retailmind\app\templates\vistas\modulo_ventas\generacionVentas.html'

# Leer archivo
with open(archivo, 'r', encoding='utf-8') as f:
    lineas = f.readlines()

# Buscar el primer cierre correcto de </script> después del código principal
# y eliminar todo hasta el comentario "<!-- Scripts Transbank Web Serial API (Local) -->"
nueva_lista = []
dentro_de_bloque_malo = False
contador_scripts = 0

for i, linea in enumerate(lineas):
    # Contar </script>
    if '</script>' in linea:
        contador_scripts += 1
        nueva_lista.append(linea)
        
        # Después del segundo </script>, activar eliminación
        if contador_scripts == 2:
            dentro_de_bloque_malo = True
            continue
    
    # Detectar inicio del bloque correcto de scripts
    if '<!-- Scripts Transbank Web Serial API (Local) -->' in linea and contador_scripts >= 2:
        dentro_de_bloque_malo = False
        nueva_lista.append(linea)
        continue
    
    # Solo agregar líneas si NO estamos en bloque malo
    if not dentro_de_bloque_malo:
        nueva_lista.append(linea)

# Guardar archivo limpio
with open(archivo, 'w', encoding='utf-8') as f:
    f.writelines(nueva_lista)

print(f"✅ Archivo limpiado. Líneas originales: {len(lineas)}, Líneas finales: {len(nueva_lista)}")
print(f"📊 Líneas eliminadas: {len(lineas) - len(nueva_lista)}")
