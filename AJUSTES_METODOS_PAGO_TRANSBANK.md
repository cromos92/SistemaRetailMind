# Ajustes de Métodos de Pago Transbank y Cuadratura

**Fecha:** 26 de enero de 2026  
**Objetivo:** Unificar el manejo de métodos de pago con tarjeta para Transbank/Getnet y mantener compatibilidad con datos migrados.

---

## 📋 Contexto

La empresa solo usa **Transbank** y **Getnet** para procesar pagos con tarjeta de débito y crédito. Sin embargo, existían métodos de pago genéricos (`TARJETA_DEBITO`, `TARJETA_CREDITO`) que se mezclaban con los métodos específicos de Transbank (`TBK_DEBITO_POS`, `TBK_CREDITO_POS`).

### Problema Identificado

El campo `tipo_tarjeta` NO es necesario para métodos de Transbank, ya que el `metodo_pago` ya define si es débito o crédito:

- ✅ `metodo_pago = TBK_DEBITO_POS` → Es débito (tipo_tarjeta no importa)
- ✅ `metodo_pago = TBK_CREDITO_POS` → Es crédito (tipo_tarjeta no importa)
- ✅ `metodo_pago = VENTA_INTERNET` + `tipo_tarjeta = "Paris"` → SÍ usa tipo_tarjeta para plataforma
- ✅ `metodo_pago = TARJETA_COMERCIAL` + `tipo_tarjeta = "HITES"` → SÍ usa tipo_tarjeta para empresa

### Solución Implementada

- **NO** solicitar `tipo_tarjeta` para métodos TBK_DEBITO_POS y TBK_CREDITO_POS
- **SÍ** basarse únicamente en `metodo_pago` para clasificar en cuadratura
- **SÍ** solicitar `tipo_tarjeta` para VENTA_INTERNET, TARJETA_COMERCIAL, CREDITO_EXTERNO

## 🔧 Cambios Realizados

### 1. **Modales de POS Dashboard** (`generacionVentas.html`)

**Antes:**
- Botón "Débito" → usaba `TARJETA_DEBITO`
- Botón "Crédito" → usaba `TARJETA_CREDITO`

**Ahora:**
- Botón "Débito" → usa `TBK_DEBITO_POS` (más específico)
- Botón "Crédito" → usa `TBK_CREDITO_POS` (más específico)
- Se agregó etiqueta "Transbank" debajo de cada botón para claridad

**Cambios en el modal de pago:**
- Se añadió soporte para métodos `TBK_*` en la validación de campos
- **Para métodos TBK:** NO se muestra el campo "Tipo de Tarjeta" (no es necesario)
- **Para VENTA_INTERNET:** Se muestra "Plataforma" (Paris, Ripley, etc.)
- **Para TARJETA_COMERCIAL:** Se muestra "Empresa" (Hites, etc.)

### 2. **Cuadratura de Caja** (`views_modulo_ventas.py`)

Se actualizaron **3 funciones** para procesar correctamente todos los métodos de pago:

#### a) `_calcular_cuadratura_data()` - Líneas 4140-4170

```python
# Tickets
elif metodo == 'TARJETA_DEBITO':
    # ✅ Se considera Transbank (datos migrados y genéricos)
    cuadratura_data['total_tarjeta_debito'] += monto
    cuadratura_data['total_transbank'] += monto
elif metodo == 'TARJETA_CREDITO':
    # ✅ Se considera Transbank (datos migrados y genéricos)
    cuadratura_data['total_tarjeta_credito'] += monto
    cuadratura_data['total_transbank'] += monto
elif metodo == 'TBK_DEBITO_POS':
    # ✅ Transbank POS Débito
    cuadratura_data['total_tarjeta_debito'] += monto
    cuadratura_data['total_transbank'] += monto
elif metodo == 'TBK_CREDITO_POS':
    # ✅ Transbank POS Crédito
    cuadratura_data['total_tarjeta_credito'] += monto
    cuadratura_data['total_transbank'] += monto
elif metodo == 'TBK_PREPAGO_POS':
    # ✅ Transbank POS Prepago (va a débito por convención)
    cuadratura_data['total_tarjeta_debito'] += monto
    cuadratura_data['total_transbank'] += monto
```

```python
# DTEs - Clasificación SOLO por metodo_pago, tipo_tarjeta no se usa
elif metodo_upper in ['TBK_DEBITO_POS', 'TARJETA_DEBITO']:
    cuadratura_data['total_tarjeta_debito'] += monto
    cuadratura_data['total_transbank'] += monto

elif metodo_upper in ['TBK_CREDITO_POS', 'TARJETA_CREDITO']:
    cuadratura_data['total_tarjeta_credito'] += monto
    cuadratura_data['total_transbank'] += monto
```

#### b) `generar_cuadratura_caja()` - Líneas 4850-4920

Se aplicó la misma lógica para calcular totales teóricos al generar cuadraturas.

#### c) `procesar_arqueo_y_registrar_transbank_fisico()` - Líneas 6675-6700

Se actualizó para recalcular correctamente los totales al reabrir arqueos.

### 3. **Modelo de Datos** (`models.py`)

Se agregaron comentarios al choice `METODO_PAGO_TICKET_CHOICES`:

```python
METODO_PAGO_TICKET_CHOICES = [
    ('EFECTIVO', 'Efectivo'),
    # ⚠️ TARJETA_DEBITO y TARJETA_CREDITO son genéricos (datos históricos/migrados)
    # Para nuevas transacciones usar TBK_DEBITO_POS y TBK_CREDITO_POS
    ('TARJETA_DEBITO', 'Tarjeta Débito'),
    ('TARJETA_CREDITO', 'Tarjeta Crédito'),
    # ...
    # Métodos Transbank (usar estos para nuevas transacciones)
    ('TBK_DEBITO_POS', 'Transbank Débito POS'),
    ('TBK_CREDITO_POS', 'Transbank Crédito POS'),
    ('TBK_PREPAGO_POS', 'Transbank Prepago POS'),
    # ...
]
```

---

## ✅ Compatibilidad con Datos Migrados

Los cambios mantienen **100% de compatibilidad** con datos históricos:

- **Datos migrados** que tienen `TARJETA_DEBITO` o `TARJETA_CREDITO` → se procesan correctamente en cuadratura
- **Nuevas transacciones** usan `TBK_DEBITO_POS` y `TBK_CREDITO_POS` (más específicos)
- Ambos métodos suman correctamente a `total_transbank` en la cuadratura

---

## 🎯 Uso del Campo `tipo_tarjeta`

El campo `tipo_tarjeta` **NO se usa** para métodos Transbank. Solo se usa para:

### Para VENTA_INTERNET:
- **Guarda la plataforma:** Paris, Ripley, Mercado Pago, Shopify, Walmart, Falabella
- **Es obligatorio** (validado en el frontend)
- **Se muestra en el modal como:** "Plataforma"

### Para TARJETA_COMERCIAL:
- **Guarda la empresa:** HITES, RIPLEY, ABCDIN, PRESTO, TRICOT
- **Se muestra en el modal como:** "Empresa"

### Para CREDITO_EXTERNO:
- **Guarda el nombre de la entidad crediticia**
- **Se muestra en el modal como:** "Entidad"

### Para métodos Transbank (TBK_DEBITO_POS, TBK_CREDITO_POS):
- ❌ **NO se solicita tipo_tarjeta** (el campo se oculta en el modal)
- ✅ El método de pago ya define si es débito o crédito
- ✅ Solo se solicita voucher/autorización

---

## 📊 Resumen de Métodos de Pago por Categoría

### Transbank (todos suman a `total_transbank`):
- `TBK_DEBITO_POS` → Débito vía POS
- `TBK_CREDITO_POS` → Crédito vía POS
- `TBK_PREPAGO_POS` → Prepago vía POS
- `TBK_POS_INTEGRADO` → Genérico (histórico)
- `TBK_MANUAL` → Genérico manual (histórico)
- `TARJETA_DEBITO` → Genérico débito (migrados)
- `TARJETA_CREDITO` → Genérico crédito (migrados)

### Otros:
- `EFECTIVO`
- `TRANSFERENCIA`
- `CHEQUE`
- `TARJETA_COMERCIAL` (Hites, etc.)
- `VENTA_INTERNET` (Paris, Ripley, etc.)
- `ORDEN_COMPRA`
- `CREDITO_TRABAJADOR`
- `CREDITO_EXTERNO`
- `CONVENIO`
- `OTRO`

---

## 🔍 Verificaciones Recomendadas

1. **Probar nueva venta con Débito:** Verificar que use `TBK_DEBITO_POS`
2. **Probar nueva venta con Crédito:** Verificar que use `TBK_CREDITO_POS`
3. **Generar cuadratura del día:** Verificar que sume correctamente Transbank
4. **Ver documentos históricos:** Confirmar que `TARJETA_DEBITO/CREDITO` se muestran correctamente

---

## 📝 Notas Adicionales

- **No se eliminaron** los métodos `TARJETA_DEBITO` y `TARJETA_CREDITO` del modelo para mantener compatibilidad con datos existentes
- **Los botones del POS** ahora usan los métodos específicos de Transbank
- **La cuadratura** reconoce ambos métodos (genéricos y específicos) y los suma correctamente
- **Getnet** se procesará igual que Transbank (mismo flujo, distinto proveedor)

---

## 🚀 Próximos Pasos (Opcional)

Si en el futuro necesitan diferenciar Transbank de Getnet, se pueden crear nuevos métodos:
- `GETNET_DEBITO_POS`
- `GETNET_CREDITO_POS`

Y ajustar la cuadratura para tener columnas separadas para cada procesador.
