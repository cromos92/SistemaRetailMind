# 🎯 GUÍA RÁPIDA - SISTEMA DE CUADRATURA v2

## 🚀 INICIO RÁPIDO

### Para Cajeras:

1. **Ingresa al sistema** → `http://127.0.0.1:8000/app/ventas/cuadratura-caja/`

2. **Sigue los 4 pasos**:

```
PASO 1: Ver Totales ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
│                                                  │
│  Sistema carga automáticamente:                 │
│  💵 Efectivo: $500,000                          │
│  💳 Tarjetas: $350,000                          │
│  🌐 Internet: $150,000                          │
│  📊 Total: $1,000,000                           │
│                                                  │
│  [Siguiente: Contar Efectivo →]                 │
└──────────────────────────────────────────────────┘
         ↓
PASO 2: Contar Caja ━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
│                                                  │
│  A. EFECTIVO                B. CIERRE POS       │
│  ─────────────              ─────────────       │
│  Teórico: $500,000          Teórico: $350,000   │
│  Contado: [ $495,000 ]      Cierre: [ $348,500 ]│
│  ❌ -$5,000                  ❌ -$1,500          │
│                                                  │
│  [Siguiente: Registrar Depósitos →]             │
└──────────────────────────────────────────────────┘
         ↓
PASO 3: Depósitos ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
│                                                  │
│  Fecha: [ 03/11/2025 ]                          │
│  Monto: [ $300,000 ]                            │
│  Banco: [ BancoEstado ▼ ]                       │
│  Comp:  [ 123456 ]                              │
│                                                  │
│  [+ Agregar Depósito]                           │
│                                                  │
│  Depósitos Registrados:                         │
│  🏦 $300,000 - BancoEstado [X]                  │
│                                                  │
│  Efectivo en caja: $195,000                     │
│                                                  │
│  [Siguiente: Ver Resultado →]                   │
└──────────────────────────────────────────────────┘
         ↓
PASO 4: Resultado ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
│                                                  │
│         [ICONO GRANDE]                          │
│      ✅ ¡Cuadratura Excelente!                  │
│      Diferencia Total: -$6,500                  │
│                                                  │
│  Detalles:                                      │
│  • Diferencia Efectivo: ❌ -$5,000              │
│  • Diferencia POS: ❌ -$1,500                   │
│                                                  │
│  Observaciones: [_____________________]         │
│                                                  │
│  [💾 Guardar Cuadratura] [🖨️ Imprimir]         │
└──────────────────────────────────────────────────┘
```

---

## 📝 EJEMPLO PRÁCTICO

### Escenario Real:

**Fecha:** 03/11/2025  
**Cajera:** María González  
**Sucursal:** Local Centro

#### Al final del día:

1. **Sistema dice:**
   - Efectivo vendido: $500,000
   - Tarjetas: $350,000

2. **María cuenta:**
   - Efectivo en caja: $495,000 (faltante $5,000)
   - Revisa cierre POS: $348,500 (faltante $1,500)

3. **María depositó:**
   - $300,000 en BancoEstado
   - Comprobante: 123456

4. **Resultado:**
   - Efectivo que queda en caja: $195,000
   - Diferencia total: -$6,500
   - Estado: ⚠️ Con diferencias menores

5. **María guarda:**
   - Observaciones: "Revisado con supervisor, diferencias menores aprobadas"
   - Click en "Guardar"
   - ✅ Sistema crea Arqueo #1234

---

## 🎨 GUÍA VISUAL DE COLORES

```
┌─────────────────────────────────────────────────┐
│  SEMÁFORO DE DIFERENCIAS                        │
├─────────────────────────────────────────────────┤
│                                                 │
│  ✅ Verde = Perfecto ($0 diferencia)           │
│  ⚠️ Amarillo = Sobrante (revisar)              │
│  ❌ Rojo = Faltante (revisar)                  │
│                                                 │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│  TARJETAS DE INFORMACIÓN                        │
├─────────────────────────────────────────────────┤
│                                                 │
│  🟢 Verde = Efectivo                           │
│  🔵 Azul = Tarjetas/POS                        │
│  🔷 Celeste = Venta Internet                   │
│  ⚫ Negro = Total General                      │
│                                                 │
└─────────────────────────────────────────────────┘
```

---

## 💡 TIPS Y TRUCOS

### Para Cajeras:

1. **Desglose de Billetes (Opcional)**
   - Si quieres, puedes desglosar billetes y monedas
   - El sistema suma automáticamente
   - No es obligatorio

2. **Número de Lote POS**
   - Aparece en la máquina Transbank
   - Es opcional, pero ayuda a trazabilidad

3. **Múltiples Depósitos**
   - Puedes agregar varios depósitos
   - Útil si depositaste en diferentes bancos
   - O si depositaste dinero del día anterior

4. **Observaciones**
   - Explica cualquier diferencia
   - Ej: "Faltante por vuelto mal dado"
   - Ej: "Sobrante, cliente dejó propina"

### Para Supervisores:

1. **Revisar Diferencias**
   - Diferencias menores ($100-$1,000): Normal
   - Diferencias mayores: Investigar
   - Patrón repetitivo: Capacitar

2. **Depósitos Pendientes**
   - Revisar que se depositen al día siguiente
   - Alertar si acumula más de $1,000,000

3. **Cierre POS**
   - Debe coincidir con sistema
   - Si no, revisar transacciones del día
   - Verificar anulaciones

---

## 🔍 PREGUNTAS FRECUENTES

### ¿Qué hago si me falta efectivo?

1. Revisar el conteo (contar de nuevo)
2. Verificar si hay depósitos sin registrar
3. Revisar si hay gastos del día (compras menores)
4. Agregar observación y guardar
5. Informar al supervisor

### ¿Qué hago si sobra efectivo?

1. Revisar el conteo (contar de nuevo)
2. Verificar si algún cliente dejó propina
3. Verificar si hay ventas sin registrar (poco común)
4. Agregar observación y guardar

### ¿Puedo corregir una cuadratura guardada?

Actualmente no, pero puedes:
- Agregar observaciones detalladas
- Informar al supervisor para ajuste manual en BD
- *Próxima versión incluirá edición*

### ¿Qué pasa si no ingreso el cierre POS?

- El sistema lo marca como "No ingresado"
- La cuadratura se guarda igual
- Recomendado: Siempre ingresar para control

### ¿Puedo hacer cuadratura de días anteriores?

Sí, al cargar el sistema:
- Cambia la fecha en el paso 1
- El sistema cargará ventas de ese día
- Útil si olvidaste hacer cuadratura

---

## 🛠️ SOLUCIÓN DE PROBLEMAS

### "Error al cargar datos del sistema"

**Causas comunes:**
- No hay ventas ese día
- Problema de conexión
- Error en base de datos

**Solución:**
1. Verifica la fecha seleccionada
2. Recarga la página (F5)
3. Si persiste, contacta soporte

### "Ya existe una cuadratura para esta fecha"

**Causa:**
- Ya se guardó una cuadratura

**Solución:**
1. Verifica en el historial
2. Si necesitas modificar, contacta supervisor
3. Para hacer otra, elimina la existente (requiere permisos)

### "No se pudo guardar la cuadratura"

**Causas comunes:**
- Sesión expirada
- Error de conexión
- Falta algún dato requerido

**Solución:**
1. Verifica que ingresaste el efectivo contado
2. Recarga la página y vuelve a intentar
3. Si persiste, anota los datos y contacta soporte

---

## 📊 DATOS QUE SE GUARDAN

Cuando haces click en "Guardar Cuadratura", el sistema guarda:

### Tabla: ArqueoCaja
```
✅ Fecha del arqueo
✅ Usuario responsable (tú)
✅ Sucursal
✅ Efectivo teórico (del sistema)
✅ Efectivo contado (lo que ingresaste)
✅ Diferencia calculada
✅ Todos los totales por método de pago
✅ Observaciones
✅ Estado (Cerrado / Con diferencias)
```

### Tabla: DepositoBancario (por cada depósito)
```
✅ Fecha del depósito
✅ Monto depositado
✅ Banco seleccionado
✅ Número de comprobante
✅ Observaciones
✅ Usuario que registró
```

---

## 📞 SOPORTE

**Problemas técnicos:**
- Contacta al área de TI

**Dudas sobre diferencias:**
- Contacta a tu supervisor

**Problemas con depósitos:**
- Contacta al área de contabilidad

---

## 🎓 CAPACITACIÓN

### Videos tutoriales (próximamente):
1. Cuadratura básica (5 min)
2. Manejo de depósitos (3 min)
3. Solución de problemas comunes (5 min)

### Documentos:
- [MEJORAS_CUADRATURA_v2.md](./MEJORAS_CUADRATURA_v2.md) - Documentación técnica
- Esta guía - Uso diario

---

**Última actualización:** 03/11/2025  
**Versión del sistema:** 2.0  
**Desarrollado con ❤️ priorizando SENCILLEZ**

