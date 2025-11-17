# Corrección de Métodos de Pago en DTEs

## 🔍 Problema Identificado

Los documentos electrónicos (Boletas y Facturas) generados desde el punto de venta mostraban **"Sin pagos"** en la columna de "Métodos Pago", aunque se habían pagado con tarjeta de crédito u otros métodos.

### Causa Raíz

En la función `generar_dte_desde_ticket()` ubicada en `retailmind/app/views_modulo_ventas.py`, había un error en la línea 1866:

```python
# ❌ INCORRECTO (línea 1866 original)
Dte_Detalle_Pago.objects.create(
    dte=dte,
    metodo_pago=pago.metodo_pago,
    monto=pago.monto,
    tipo_tarjeta=pago.tipo_tarjeta or '',
    voucher=pago.voucher or '',
    observaciones=pago.notas or ''  # ❌ Campo incorrecto
)
```

El modelo `Dte_Detalle_Pago` tiene un campo llamado `notas`, no `observaciones`. Este error causaba una excepción al intentar guardar los pagos, dejando los DTEs sin métodos de pago registrados.

## ✅ Correcciones Realizadas

### 1. Corrección del campo en `generar_dte_desde_ticket()`

**Archivo:** `retailmind/app/views_modulo_ventas.py`  
**Línea:** 1858-1867

```python
# ✅ CORREGIDO
for pago in ticket.pagos.all():
    Dte_Detalle_Pago.objects.create(
        dte=dte,
        metodo_pago=pago.metodo_pago,
        monto=pago.monto,
        tipo_tarjeta=pago.tipo_tarjeta or '',
        voucher=pago.voucher or '',
        notas=pago.notas or ''  # ✅ Campo correcto
    )
```

### 2. Agregado de referencia al ticket

**Archivo:** `retailmind/app/views_modulo_ventas.py`  
**Línea:** 1844

Se agregó el campo `referencias` al DTE para mantener trazabilidad con el ticket original:

```python
dte = Dte.objects.create(
    # ... otros campos ...
    referencias=f'TICKET-{ticket.correlativo}'  # ✅ Nuevo campo
)
```

### 3. Script de migración de datos

**Archivo:** `retailmind/app/management/commands/corregir_pagos_dtes.py`

Se creó un comando de Django para corregir los DTEs existentes que ya están en la base de datos sin métodos de pago.

## 🚀 Cómo Ejecutar la Corrección

### Paso 1: Verificar DTEs afectados (modo dry-run)

Primero, ejecute el comando en modo dry-run para ver qué DTEs serían corregidos **sin hacer cambios**:

```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
python manage.py corregir_pagos_dtes --dry-run
```

Esto mostrará:
- Cuántos DTEs tienen el problema
- Qué pagos se copiarían
- Cuáles no tienen ticket asociado

### Paso 2: Aplicar la corrección

Una vez verificado, ejecute el comando sin `--dry-run` para aplicar los cambios:

```bash
python manage.py corregir_pagos_dtes
```

### Opciones adicionales

**Filtrar por sucursal:**
```bash
python manage.py corregir_pagos_dtes --sucursal 1 --dry-run
```

**Ver ayuda:**
```bash
python manage.py corregir_pagos_dtes --help
```

## 📊 Qué hace el script de corrección

El script:

1. **Busca DTEs problemáticos:**
   - DTEs de tipo VENTA o VENTA_PUBLICO
   - Con estado_pago = 'PAGADO'
   - Que tienen referencia a un ticket
   - **Pero NO tienen métodos de pago registrados**

2. **Extrae el correlativo del ticket** desde el campo `referencias`

3. **Busca el ticket original** en la base de datos

4. **Copia los métodos de pago** del ticket al DTE

5. **Muestra un resumen** de:
   - DTEs corregidos
   - DTEs sin ticket encontrado
   - DTEs con ticket pero sin pagos
   - Errores (si los hay)

## 📝 Ejemplo de salida del script

```
================================================================================
CORRECCIÓN DE PAGOS EN DTEs
================================================================================
MODO DRY-RUN: No se realizarán cambios en la BD

Encontrados 2 DTEs sin métodos de pago registrados

→ DTE #26 (BOLETA ELECTRONICA) - Copiando 1 pago(s) desde Ticket #26
  [DRY-RUN] TARJETA_CREDITO: $21,990

→ DTE #27 (BOLETA ELECTRONICA) - Copiando 1 pago(s) desde Ticket #27
  [DRY-RUN] TARJETA_CREDITO: $8,000

================================================================================
RESUMEN
================================================================================
DTEs procesados: 2
DTEs que se corregirían: 2
DTEs sin ticket encontrado: 0
DTEs con ticket sin pagos: 0

Para aplicar los cambios, ejecute el comando sin --dry-run
```

## 🎯 Resultado Esperado

Después de ejecutar el script, al acceder a:
```
http://localhost:8000/app/ventas/documentos/
```

Los documentos mostrarán correctamente sus métodos de pago en la columna "Métodos Pago":

**Antes:**
```
BOLETA ELECTRONICA  #26  ...  Sin pagos  $21.990  PAGADO
```

**Después:**
```
BOLETA ELECTRONICA  #26  ...  TARJETA_CREDITO  $21.990  PAGADO
```

## ⚠️ Notas Importantes

1. **El script es seguro:** El modo `--dry-run` permite verificar antes de hacer cambios.

2. **Solo afecta DTEs con ticket:** Solo corrige DTEs que tienen una referencia clara a un ticket.

3. **Mantiene integridad:** Usa transacciones de Django para garantizar que todos los pagos se copien correctamente o ninguno.

4. **Nuevos DTEs:** Los DTEs creados **después** de esta corrección ya tendrán los pagos correctamente registrados.

## 🔧 Verificación Post-Corrección

Después de ejecutar el script, verifique:

1. Acceda a http://localhost:8000/app/ventas/documentos/
2. Busque los documentos que antes mostraban "Sin pagos"
3. Verifique que ahora muestren el método de pago correcto
4. Revise el detalle de un documento para confirmar que los pagos están completos

## 📞 Soporte

Si encuentra algún problema o tiene dudas:
- Revise los logs del script
- Ejecute primero en modo `--dry-run`
- Verifique que los tickets asociados tengan pagos registrados

