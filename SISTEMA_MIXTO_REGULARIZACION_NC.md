# 🏛️ Sistema Mixto de Regularización con Notas de Crédito

## 🎯 Qué es el Sistema Mixto

El sistema **DETECTA AUTOMÁTICAMENTE** si la regularización requiere Nota de Crédito o es un ajuste interno simple, basándose en si emisor y receptor son la misma empresa o no.

---

## 🔍 Detección Automática

### Regla de Negocio:
```python
if dte.emisor.id == dte.receptor.id:
    # MISMA EMPRESA → Ajuste Interno ✅
    tipo = 'AJUSTE_INTERNO'
    genera_nc = False
else:
    # EMPRESAS DIFERENTES → Nota de Crédito 📄
    tipo = 'NOTA_CREDITO'
    genera_nc = True
```

---

## 📊 Escenarios Reales

### Escenario 1: EDEL → PAO1 (Misma Empresa)

**Contexto:**
```
Emisor: EDEL (Empresa RetailMind - ID: 5)
Receptor: PAO1 (Empresa RetailMind - ID: 5)
→ Misma empresa ✅
```

**Proceso:**
```
1. Recepción con problemas:
   - Esperado: 10
   - Recibido: 7
   - Faltante: 3

2. En Regularización:
   ┌────────────────────────────────────┐
   │ ✏️ Ajuste Interno                 │
   │ Emisor y receptor son la misma    │
   │ empresa. Se realizará ajuste      │
   │ simple sin generar NC.            │
   └────────────────────────────────────┘
   
3. Al regularizar (ingresar 3 faltantes):
   ✅ Stock += 3
   ✅ Movimiento: REGULARIZACION_TRASPASO
   ✅ Estado: REGULARIZADO
   ❌ NO se genera NC
```

---

### Escenario 2: EDEL → NICK1 (Empresas Diferentes)

**Contexto:**
```
Emisor: EDEL (Empresa RetailMind - ID: 5)
Receptor: NICK1 (Empresa NickStore - ID: 6)
→ Empresas diferentes ⚠️
```

**Proceso:**
```
1. Recepción con problemas:
   - Esperado: 10
   - Recibido: 7
   - Faltante: 3

2. En Regularización:
   ┌────────────────────────────────────┐
   │ 📄 Se Generará Nota de Crédito    │
   │ Emisor y receptor son empresas    │
   │ diferentes. Se generará NC        │
   │ automática que cumple normativa   │
   │ SII.                              │
   └────────────────────────────────────┘
   
3. Al regularizar:
   ✅ Se genera NC automáticamente:
      - Número: Correlativo automático
      - Monto: 3 unidades × precio
      - Documento afectado: DTE #1092
      - Motivo: "Productos faltantes"
   ✅ Stock NO cambia (pérdida documentada)
   ✅ Estado: REGULARIZADO
   ✅ NC queda registrada en sistema
```

---

## 🎨 Interfaz Visual

### En `/app/regularizar-recepciones/`

**Tabla con Indicador:**
```
┌─────────────────────────────────────────────────────────┐
│ DTE │ Producto │ Esperado │ Recibido │ Tipo Reg.        │
├─────┼──────────┼──────────┼──────────┼──────────────────┤
│ 1092│ Zapati...│    10    │     7    │ ✏️ Ajuste      │← Verde
│ 1093│ Polera...│     8    │     5    │ 📄 NC          │← Rojo
└─────────────────────────────────────────────────────────┘

Badges:
- Verde (✏️ Ajuste): Misma empresa → Ajuste simple
- Rojo (📄 NC): Empresas diferentes → Genera NC
```

**Modal de Regularización:**
```
Si MISMA empresa:
┌────────────────────────────────────┐
│ ✏️ Ajuste Interno                 │
│ ✅ Ajuste simple sin NC            │
│ ✅ Solo actualiza stock            │
│ ✅ Más rápido                      │
└────────────────────────────────────┘

Si DIFERENTES empresas:
┌────────────────────────────────────┐
│ 📄 Se Generará Nota de Crédito    │
│ ⚠️ NC automática (SII)             │
│ ⚠️ Monto se descuenta              │
│ ⚠️ Registro tributario formal      │
└────────────────────────────────────┘
```

---

## 🔧 Casos de Uso

### Caso 1: Ajuste Interno (EDEL → PAO1)

**Problema:** Faltaron 3 unidades

**Regularización:**
```sql
-- Solo se crea movimiento
INSERT INTO app_movimientos_producto
(cantidad, concepto, tipo_movimiento, observaciones)
VALUES
(3, 'REGULARIZACION_TRASPASO', 'INGRESO', 'Ajuste interno +3');

-- Se actualiza stock
UPDATE app_producto_talla
SET stock = stock + 3
WHERE id = 123;

-- Se actualiza recepción
UPDATE app_productos_recepcionados
SET estado = 'REGULARIZADO',
    stockArribado = 10
WHERE id = 456;
```

**NO se genera NC** ✅

---

### Caso 2: Con Nota de Crédito (EDEL → NICK1)

**Problema:** Faltaron 3 unidades × $10.000 = $30.000

**Regularización:**
```sql
-- 1. Se genera DTE de tipo NC
INSERT INTO app_dte
(numero_documento, tipo_documento, es_nota_credito, 
 documento_afectado_id, motivo_nc, monto_neto, monto_con_iva,
 emisor_id, receptor_id)
VALUES
(5001, 'NOTA DE CREDITO', TRUE, 
 1092, 'Productos faltantes - SKU: ZAP-42', 30000, 35700,
 5, 6);

-- 2. Se crea detalle de NC
INSERT INTO app_dte_productos
(dte_id, productoTalla_id, descripcion, precio, stock)
VALUES
(5001, 123, 'NC: Zapatilla Nike Talla 42', 10000, 3);

-- 3. Se actualiza recepción
UPDATE app_productos_recepcionados
SET estado = 'REGULARIZADO',
    observaciones = observaciones || '\n[2025-10-27] Regularizado con NC (NC #5001)'
WHERE id = 456;

-- 4. NO se actualiza stock (pérdida documentada formalmente)
```

**Se genera NC automáticamente** ✅

---

## 📋 Propiedades del Modelo Dte

```python
class Dte:
    # Campos nuevos
    es_nota_credito = BooleanField()
    documento_afectado = ForeignKey('self')  # DTE que afecta
    motivo_nc = TextField()
    
    # Propiedades calculadas
    @property
    def es_misma_empresa(self):
        return self.emisor_id == self.receptor_id
    
    @property
    def requiere_nota_credito(self):
        return not self.es_misma_empresa and self.tipo_transaccion == 'TRASPASO'
```

**Uso:**
```python
dte = Dte.objects.get(numero_documento=1092)

if dte.es_misma_empresa:
    print("Ajuste interno")  # EDEL → PAO1
else:
    print("Requiere NC")     # EDEL → NICK1

if dte.requiere_nota_credito:
    generar_nota_credito_automatica(...)
```

---

## 🎯 Flujo Completo

```
EMISIÓN (EDEL → NICK1)
  Emisor: Empresa A (ID: 5)
  Receptor: Empresa B (ID: 6)
  ↓
RECEPCIÓN CON PROBLEMAS (NICK1)
  Esperado: 10
  Recibido: 7
  Faltante: 3
  Estado: RECEPCIONADO_PARCIAL
  ↓
REGULARIZACIÓN (NICK1)
  Sistema detecta: emisor ≠ receptor
  ↓
  ⚠️ GENERA NC AUTOMÁTICA:
    NC #5001
    Monto: $35.700 (3 × $10.000 + IVA)
    Documento afectado: DTE #1092
    Motivo: "Productos faltantes - SKU: ZAP-42"
  ↓
  ✅ NC queda registrada
  ✅ Recepción marcada como REGULARIZADO
  ✅ Cumple normativa SII
  ✅ Auditable
```

---

## 📊 Comparación

| Aspecto | Ajuste Interno | Nota de Crédito |
|---------|---------------|----------------|
| **Cuándo** | Misma empresa | Empresas diferentes |
| **Genera NC** | ❌ No | ✅ Sí (automática) |
| **Actualiza stock** | ✅ Sí | ❌ No (pérdida) |
| **Cumple SII** | N/A | ✅ Sí |
| **Velocidad** | ⚡ Rápido | ⏱️ Normal |
| **Trazabilidad** | ✅ Movimiento | ✅ NC + Movimiento |
| **Ejemplo** | EDEL → PAO1 | EDEL → NICK1 |

---

## 🔍 Consultas SQL Útiles

### Ver NCs Generadas
```sql
SELECT 
    nc.numero_documento AS nc_numero,
    nc.fecha_emision,
    nc.motivo_nc,
    original.numero_documento AS dte_original,
    nc.monto_con_iva,
    e.nombre AS emisor,
    r.nombre AS receptor
FROM app_dte nc
INNER JOIN app_dte original ON nc.documento_afectado_id = original.id
INNER JOIN app_empresa e ON nc.emisor_id = e.id
INNER JOIN app_empresa r ON nc.receptor_id = r.id
WHERE nc.es_nota_credito = TRUE
ORDER BY nc.fecha_emision DESC;
```

### Ver Productos que Generaron NC
```sql
SELECT 
    pr.id,
    d.numero_documento AS dte_original,
    nc.numero_documento AS nc_generada,
    pt.sku,
    pr.cantidad_faltante,
    nc.monto_con_iva AS monto_nc,
    pr.observaciones
FROM app_productos_recepcionados pr
INNER JOIN app_dte d ON pr.dte_id = d.id
LEFT JOIN app_dte nc ON nc.documento_afectado_id = d.id AND nc.es_nota_credito = TRUE
INNER JOIN app_producto_talla pt ON pr.producto_talla_id = pt.id
WHERE pr.estado = 'REGULARIZADO'
  AND nc.id IS NOT NULL
ORDER BY pr.fecha_regularizacion DESC;
```

---

## ✅ Instalación

### 1. Ejecutar Migración SQL
```bash
# Ejecuta: MIGRACION_NOTAS_CREDITO.sql
```

Esto agrega:
- `es_nota_credito` (boolean)
- `documento_afectado_id` (foreign key)
- `motivo_nc` (text)

### 2. Verificar Campos
```sql
SELECT column_name FROM information_schema.columns
WHERE table_name = 'app_dte'
  AND column_name IN ('es_nota_credito', 'documento_afectado_id', 'motivo_nc');
```

### 3. Reiniciar Servidor
```bash
# Ctrl+C y volver a correr el servidor
```

---

## 🚀 Cómo Probar

### Test 1: Ajuste Interno
```
1. Emitir DTE: EDEL → PAO1 (misma empresa)
2. Recepcionar con problemas (7 de 10)
3. Ir a /app/regularizar-recepciones/
4. Ver badge verde "Ajuste" ✅
5. Abrir regularización
6. Ver alerta verde "Ajuste Interno" ✅
7. Ajustar cantidad
8. Confirmar
9. Verificar: NO se generó NC ✅
```

### Test 2: Con Nota de Crédito
```
1. Emitir DTE: EDEL (Emp. A) → NICK1 (Emp. B)
2. Recepcionar con problemas (7 de 10)
3. Ir a /app/regularizar-recepciones/
4. Ver badge rojo "NC" ✅
5. Abrir regularización
6. Ver alerta roja "Se Generará NC" ✅
7. Ajustar cantidad
8. Confirmar
9. Verificar en BD: Se generó NC ✅
```

### Verificar NC Generada:
```sql
SELECT * FROM app_dte 
WHERE es_nota_credito = TRUE
ORDER BY id DESC
LIMIT 5;
```

Deberías ver:
- `es_nota_credito = TRUE`
- `documento_afectado_id = [ID del DTE original]`
- `motivo_nc = "Productos faltantes..."`
- `tipo_documento = "NOTA DE CREDITO"`

---

## 📄 Estructura de Nota de Crédito

```json
{
  "numero_documento": 5001,
  "tipo_documento": "NOTA DE CREDITO",
  "es_nota_credito": true,
  "documento_afectado_id": 1092,
  "motivo_nc": "Productos faltantes - SKU: ZAP-42",
  "emisor_id": 5,
  "receptor_id": 6,
  "monto_neto": 30000,
  "monto_con_iva": 35700,
  "fecha_emision": "2025-10-27",
  "estado_dte": "EMITIDO",
  "tipo_transaccion": "TRASPASO",
  "referencias": "NC por regularización DTE #1092. Productos faltantes..."
}
```

**Detalle de NC:**
```json
{
  "dte_id": 5001,
  "productoTalla_id": 123,
  "descripcion": "NC: Zapatilla Nike Air Talla 42",
  "precio": 10000,
  "stock": 3,
  "costo": 8000,
  "activo": true
}
```

---

## 🎨 Interfaz de Usuario

### Vista: `/app/regularizar-recepciones/`

**Tabla de Productos:**
```
┌──────────────────────────────────────────────────────┐
│ DTE  │ Producto    │ Esperado│ Recibido│ Tipo Reg.   │
├──────┼─────────────┼─────────┼─────────┼─────────────┤
│ 1092 │ Zapatilla..│   10    │    7    │ ✏️ Ajuste  │← Verde
│ 1093 │ Polera Ad..│    8    │    5    │ 📄 NC      │← Rojo
└──────────────────────────────────────────────────────┘

Hover sobre badge muestra tooltip:
- Verde: "Ajuste interno (misma empresa)"
- Rojo: "Requiere NC (empresas diferentes)"
```

**Modal de Regularización:**
```
Para EDEL → PAO1 (misma):
┌────────────────────────────────────┐
│ Zapatilla Nike 42                  │
│ DTE #1092                          │
├────────────────────────────────────┤
│ ✏️ AJUSTE INTERNO                 │
│ ✅ Ajuste simple sin NC            │
│ ✅ Mismo RUT emisor/receptor       │
├────────────────────────────────────┤
│ Nueva cantidad: [10]               │
│ Obs: [Llegaron 3 más_____]         │
│ [Regularizar]                      │
└────────────────────────────────────┘

Para EDEL → NICK1 (diferente):
┌────────────────────────────────────┐
│ Zapatilla Nike 42                  │
│ DTE #1092                          │
├────────────────────────────────────┤
│ 📄 SE GENERARÁ NOTA DE CRÉDITO    │
│ ⚠️ NC automática (normativa SII)   │
│ ⚠️ RUTs diferentes                 │
│ ⚠️ Monto: $35.700 (3 × $10.000)   │
├────────────────────────────────────┤
│ Motivo: [Productos faltantes___]   │
│ Obs: [No llegaron completas___]    │
│ [Generar NC y Regularizar]         │
└────────────────────────────────────┘
```

---

## 📞 Vista: "Ver DTEs con Problemas"

En `/app/recepcion-dte/`, ahora hay un botón que muestra modal con:

```
┌──────────────────────────────────────────────────────┐
│ 📄 DTEs con Productos Problemáticos                  │
├──────────────────────────────────────────────────────┤
│ DTE  │ Origen│ OK│ Problemas│ Estado  │ Acciones     │
├──────┼───────┼───┼──────────┼─────────┼──────────────┤
│ 1092 │ EDEL  │ 7 │    3     │ Parcial │ [Regularizar]│
│ 1093 │ MALL  │ 5 │    3     │ Parcial │ [Regularizar]│
└──────────────────────────────────────────────────────┘

- Barra de progreso visual por DTE
- Link directo a regularización
```

---

## 🎯 Archivos Modificados

1. ✅ `retailmind/app/models.py` - Campos NC agregados
2. ✅ `retailmind/app/views.py` - Función `generar_nota_credito_automatica()`
3. ✅ `retailmind/app/views.py` - Vista `regularizar_producto_api` actualizada
4. ✅ `retailmind/app/views.py` - Vista `obtener_dtes_con_problemas` creada
5. ✅ `retailmind/app/urls.py` - URL agregada
6. ✅ `retailmind/app/templates/vistas/modulo_compras/recepcion_dte.html` - Modal DTEs problemas
7. ✅ `retailmind/app/templates/vistas/modulo_compras/regularizar_recepciones.html` - Indicador tipo

### SQL:
8. ✅ `MIGRACION_NOTAS_CREDITO.sql` - Migración de campos NC

---

## 🎉 Beneficios

### Cumplimiento Tributario
- ✅ NCs formales que cumplen SII
- ✅ Trazabilidad completa
- ✅ Documentación correcta
- ✅ Auditable

### Eficiencia Operativa
- ✅ Detección automática (sin pensar)
- ✅ Ajustes rápidos cuando es posible
- ✅ NCs solo cuando es necesario
- ✅ Usuario informado siempre

### Flexibilidad
- ✅ Soporta ambos casos
- ✅ Misma interfaz para todo
- ✅ Sistema decide automáticamente
- ✅ Escalable

---

## 📞 Próximos Pasos

### 1. Ejecutar Migración
```sql
-- Ejecutar: MIGRACION_NOTAS_CREDITO.sql
```

### 2. Probar Sistema
```bash
# Probar ambos escenarios
1. EDEL → PAO1 (ajuste)
2. EDEL → NICK1 (NC)
```

### 3. Vista de NCs (Opcional)
- Crear `/app/consultar-notas-credito/`
- Ver todas las NCs generadas
- Exportar a PDF
- Enviar a SII

---

**Fecha:** 2025-10-27  
**Sistema:** Mixto Inteligente  
**Estado:** ✅ Implementado  
**Normativa:** ✅ Cumple SII

