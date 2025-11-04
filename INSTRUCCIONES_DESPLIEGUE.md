# 🚀 INSTRUCCIONES DE DESPLIEGUE - Sistema de Cuadratura v2

## ⚠️ IMPORTANTE - ANTES DE EJECUTAR

Este documento contiene los pasos necesarios para activar las mejoras del sistema de cuadratura.

---

## 📋 CHECKLIST PRE-DESPLIEGUE

Antes de ejecutar las migraciones, verifica:

- [ ] Tienes backup de la base de datos
- [ ] Estás en el entorno correcto (desarrollo/producción)
- [ ] Tienes los permisos necesarios
- [ ] El servidor está en horario de mantenimiento (si es producción)

---

## 🔧 PASO A PASO

### 1. **Crear y Aplicar Migraciones**

```powershell
# Navegar al directorio del proyecto
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind

# Crear las migraciones para el nuevo modelo
python manage.py makemigrations

# Deberías ver algo como:
# Migrations for 'app':
#   app\migrations\0XXX_depositobancario.py
#     - Create model DepositoBancario

# Aplicar las migraciones
python manage.py migrate

# Deberías ver:
# Running migrations:
#   Applying app.0XXX_depositobancario... OK
```

**✅ VERIFICACIÓN:**
```powershell
# Verificar que la tabla se creó
python manage.py dbshell

# En SQLite:
.tables
# Deberías ver: deposito_bancario

# Salir:
.exit
```

---

### 2. **Registrar el Modelo en el Admin (Opcional)**

Si quieres gestionar depósitos desde el admin de Django:

**Archivo:** `retailmind/app/admin.py`

```python
from .models import DepositoBancario

@admin.register(DepositoBancario)
class DepositoBancarioAdmin(admin.ModelAdmin):
    list_display = ['fecha_deposito', 'monto', 'banco', 'arqueo', 'registrado_por']
    list_filter = ['banco', 'fecha_deposito']
    search_fields = ['numero_comprobante', 'observaciones']
    date_hierarchy = 'fecha_deposito'
    readonly_fields = ['fecha_registro']
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('arqueo', 'registrado_por')
```

---

### 3. **Reiniciar el Servidor**

```powershell
# Si el servidor está corriendo, detenerlo (Ctrl+C)
# Luego reiniciarlo:
python manage.py runserver
```

---

### 4. **Probar el Sistema**

#### 4.1 Probar Carga de Datos
1. Ir a: `http://127.0.0.1:8000/app/ventas/cuadratura-caja/`
2. Verificar que carga los totales del sistema (Paso 1)
3. Revisar consola del navegador (F12) para errores

#### 4.2 Probar Paso 2 (Conteo)
1. Ingresar efectivo contado
2. Verificar que muestra diferencia instantánea
3. Ingresar cierre POS
4. Verificar que muestra diferencia de POS

#### 4.3 Probar Paso 3 (Depósitos)
1. Agregar un depósito de prueba:
   - Fecha: Hoy
   - Monto: $100,000
   - Banco: BancoEstado
   - Comprobante: TEST123
2. Verificar que aparece en la lista
3. Verificar que actualiza el resumen de efectivo
4. Probar eliminar el depósito

#### 4.4 Probar Paso 4 (Resultado)
1. Verificar que muestra todas las diferencias
2. Revisar que los totales cuadran
3. Agregar observación de prueba

#### 4.5 Probar Guardado
1. Click en "Guardar Cuadratura"
2. Verificar modal de confirmación
3. Confirmar guardado
4. Verificar mensaje de éxito con ID de arqueo

**IMPORTANTE:** Después de probar, puedes eliminar el registro de prueba desde el admin o desde la BD directamente.

---

## 🐛 TROUBLESHOOTING

### Error: "No module named 'DepositoBancario'"

**Causa:** El modelo no está importado correctamente

**Solución:**
```python
# Verificar en views_modulo_ventas.py:
from .models import DepositoBancario
```

---

### Error: "relation 'deposito_bancario' does not exist"

**Causa:** Las migraciones no se aplicaron

**Solución:**
```powershell
python manage.py migrate
```

---

### Error: "FOREIGN KEY constraint failed"

**Causa:** Intentando crear depósito sin arqueo

**Solución:**
- Verificar que primero se crea el `ArqueoCaja`
- Revisar el orden en la función `guardar_cuadratura_completa()`

---

### Los depósitos no aparecen en el Paso 3

**Causa:** JavaScript no está cargando correctamente

**Solución:**
1. Abrir consola del navegador (F12)
2. Revisar errores en consola
3. Verificar que `mostrarDepositos()` se llama correctamente
4. Limpiar caché del navegador (Ctrl+Shift+Del)

---

### Al guardar, sale "Ya existe una cuadratura"

**Causa:** Ya hay un arqueo para esa fecha

**Solución:**
```python
# Opción 1: Eliminar desde admin
# Ir a: http://127.0.0.1:8000/admin/app/arqueocaja/
# Buscar y eliminar el arqueo

# Opción 2: Desde DB
python manage.py dbshell
DELETE FROM arqueo_caja WHERE fecha_arqueo = '2025-11-03' AND sucursal_id = 1;
.exit
```

---

## 📊 VERIFICACIÓN POST-DESPLIEGUE

### Revisar Logs del Servidor

```powershell
# Observar la consola donde corre el servidor
# Deberías ver al guardar:
# ✅ Datos del sistema cargados
# ✅ Guardando cuadratura
# ✅ Cuadratura guardada exitosamente
```

### Revisar Base de Datos

```sql
-- Ver arqueos creados
SELECT * FROM arqueo_caja ORDER BY fecha_arqueo DESC LIMIT 5;

-- Ver depósitos creados
SELECT * FROM deposito_bancario ORDER BY fecha_deposito DESC LIMIT 5;

-- Ver relación entre arqueo y depósitos
SELECT 
    a.fecha_arqueo,
    a.total_efectivo_fisico,
    d.monto AS deposito,
    d.banco,
    d.numero_comprobante
FROM arqueo_caja a
LEFT JOIN deposito_bancario d ON d.arqueo_id = a.id
ORDER BY a.fecha_arqueo DESC;
```

---

## 🎯 CRITERIOS DE ÉXITO

El despliegue es exitoso si:

✅ Las migraciones se aplicaron sin errores  
✅ El modelo `DepositoBancario` aparece en el admin  
✅ La interfaz carga correctamente los 4 pasos  
✅ Se pueden agregar depósitos en el Paso 3  
✅ El selector de banco muestra las 10 opciones  
✅ Las diferencias se calculan en tiempo real  
✅ Al guardar, se crea el registro en `arqueo_caja`  
✅ Al guardar, se crean los registros en `deposito_bancario`  
✅ No hay errores en la consola del navegador  
✅ No hay errores en los logs del servidor  

---

## 📝 NOTAS ADICIONALES

### Sobre el Campo `banco` en DepositoBancario

El campo usa `CharField` con choices, por lo que:
- Se guarda el código (ej: "ESTADO")
- Se muestra el nombre (ej: "BancoEstado")
- Usa `.get_banco_display()` para mostrar el nombre legible

### Sobre la Relación Arqueo → Depósitos

- Un `ArqueoCaja` puede tener múltiples `DepositoBancario`
- Si eliminas un `ArqueoCaja`, se eliminan sus depósitos (CASCADE)
- Usa `.depositos.all()` para obtener depósitos de un arqueo

### Sobre la Validación de Duplicados

La vista `guardar_cuadratura_completa()` valida que no exista otro arqueo para la misma fecha y sucursal. Si necesitas permitir múltiples arqueos por día (ej: turnos), modifica:

```python
# En views_modulo_ventas.py, línea ~2667
# Cambiar de:
arqueo_existente = ArqueoCaja.objects.filter(
    fecha_arqueo=fecha_obj,
    sucursal=sucursal
).first()

# A:
arqueo_existente = None  # Permitir múltiples
```

---

## 🔄 ROLLBACK (Si algo sale mal)

Si necesitas revertir los cambios:

```powershell
# 1. Revertir la migración
python manage.py migrate app 0XXX  # número de migración anterior

# 2. Eliminar archivo de migración
# Ir a: retailmind/app/migrations/
# Eliminar: 0XXX_depositobancario.py

# 3. Reiniciar servidor
python manage.py runserver
```

**IMPORTANTE:** Solo hacer rollback si el sistema está en desarrollo. En producción, contacta al DBA.

---

## 📞 CONTACTO DE SOPORTE

**Desarrollador:** [Tu nombre]  
**Email:** [Tu email]  
**Slack/Teams:** [Tu usuario]

---

## 📚 DOCUMENTACIÓN RELACIONADA

- [MEJORAS_CUADRATURA_v2.md](./MEJORAS_CUADRATURA_v2.md) - Documentación técnica completa
- [GUIA_RAPIDA_CUADRATURA.md](./GUIA_RAPIDA_CUADRATURA.md) - Guía para usuarios finales

---

**Fecha de creación:** 03/11/2025  
**Versión:** 2.0  
**Estado:** ✅ Listo para desplegar

