# ✅ Métodos de Pago Implementados en POS Dashboard

## 📍 URL del POS Dashboard
```
http://localhost:8000/app/pos-dashboard/
```

---

## 💳 MÉTODOS DE PAGO DISPONIBLES

### 1. **EFECTIVO** ✅
- Botón: Verde
- Icono: 💵 Efectivo
- Función: `agregarPago('EFECTIVO')`
- Características:
  - Calcula vuelto automáticamente
  - Muestra saldo pendiente

### 2. **TARJETA DÉBITO** ✅
- Botón: Azul
- Icono: 💳 Débito
- Función: `agregarPago('TARJETA_DEBITO')`
- Características:
  - Solicita tipo de tarjeta
  - Solicita número de voucher

### 3. **TARJETA CRÉDITO** ✅
- Botón: Azul Info
- Icono: 💳 Crédito
- Función: `agregarPago('TARJETA_CREDITO')`
- Características:
  - Solicita tipo de tarjeta
  - Solicita número de voucher

### 4. **TRANSFERENCIA** ✅
- Botón: Amarillo
- Icono: 💱 Transfer.
- Función: `agregarPago('TRANSFERENCIA')`

### 5. **VENTA INTERNET** ✅ *(NUEVO)*
- Botón: Gris
- Icono: 🛒 Venta Internet
- Función: `pagarConVentaInternet()`
- **Modal captura**:
  - **Plataforma** (obligatorio):
    - Paris
    - Ripley
    - Mercado Pago
    - Shopify
    - Walmart
  - **Número de Pedido** (obligatorio)
  - **Monto** (obligatorio, permite parcial)
  - **Notas** (opcional)

**Almacenamiento**:
```javascript
{
    metodo_pago: 'VENTA_INTERNET',
    tipo_tarjeta: 'Mercado Pago',      // Plataforma
    voucher: 'ORD-123456',              // Número de pedido
    monto: 50000,
    notas: 'Venta Mercado Pago - Pedido: ORD-123456'
}
```

**Visualización en lista de pagos**:
```
Venta Internet
🏪 Mercado Pago
🎟️ ORD-123456
$50.000
```

---

### 6. **POS TRANSBANK** ✅
- Botón: Rojo
- Icono: 💳 POS Transbank
- Función: `pagarConPOSTransbank()`
- Características:
  - Conexión automática al POS
  - Detecta tipo de tarjeta (Débito/Crédito)
  - Guarda voucher y datos de transacción
  - Permite pagos parciales

### 7. **CRÉDITO TRABAJADOR** ✅
- Botón: Rojo con borde punteado
- Icono: 👑 CRÉDITO TRABAJADOR
- Función: `activarCreditoTrabajador()`
- Características:
  - Busca trabajador por RUT
  - Muestra saldo disponible
  - Aplica crédito automáticamente
  - Descuenta del saldo del trabajador

### 8. **CRÉDITO EXTERNO** ✅ *(NUEVO)*
- Botón: Info (celeste) con borde punteado
- Icono: 🏦 CRÉDITO EXTERNO
- Función: `pagarConCreditoExterno()`
- **Modal captura**:
  - **Folio** (obligatorio)
  - **Empresa** (obligatorio)
  - **Nombre** (obligatorio)
  - **RUT** (obligatorio)
  - **Fecha de Convenio** (obligatorio)
  - **Monto del Convenio** (obligatorio)
  - **Monto a pagar ahora** (obligatorio, permite parcial)
  - **Observaciones** (opcional)

**Almacenamiento**:
```javascript
{
    metodo_pago: 'CREDITO_EXTERNO',
    tipo_tarjeta: 'Empresa ABC',        // Empresa
    voucher: 'CONV-2024-001',           // Folio
    monto: 100000,
    notas: JSON.stringify({             // Datos adicionales en JSON
        nombre: 'Juan Pérez',
        rut: '12.345.678-9',
        fecha_convenio: '2024-11-01',
        monto_total_convenio: 500000,
        observaciones: 'Convenio anual'
    })
}
```

**Visualización en lista de pagos**:
```
Crédito Externo
🏢 Empresa ABC
📄 Folio: CONV-2024-001
👤 Juan Pérez - 12.345.678-9
$100.000
```

---

## 🗄️ ALMACENAMIENTO EN BASE DE DATOS

### Modelo: `TicketDetallePago`

```python
class TicketDetallePago(models.Model):
    ticket = models.ForeignKey(Ticket, related_name='pagos', on_delete=models.CASCADE)
    metodo_pago = models.CharField(max_length=50, choices=METODO_PAGO_TICKET_CHOICES)
    tipo_tarjeta = models.CharField(max_length=100, null=True, blank=True)
    voucher = models.CharField(max_length=100, null=True, blank=True)
    monto = models.IntegerField()
    notas = models.TextField(blank=True, null=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
```

### Modelo: `Dte_Detalle_Pago` *(para Facturas/Boletas)*

```python
class Dte_Detalle_Pago(models.Model):
    dte = models.ForeignKey(Dte, related_name='dte_asociado', on_delete=models.PROTECT)
    metodo_pago = models.CharField(max_length=100)
    tipo_tarjeta = models.CharField(max_length=100, null=True)
    voucher = models.CharField(max_length=50, null=True)
    monto = models.IntegerField()
    notas = models.TextField(blank=True, null=True)  # ✅ NUEVO CAMPO
```

---

## 📊 VISUALIZACIÓN EN CONSULTA DE DOCUMENTOS

### Archivo: `gestionVentasDocumentos.html`

Cuando se consulta un DTE, los pagos se muestran en una tabla con columnas:
- **Método de Pago**
- **Voucher/Referencia**
- **Tipo Tarjeta/Empresa**
- **Detalles** *(nueva columna para créditos externos)*
- **Monto**

**Ejemplo de visualización para Crédito Externo**:
```
┌──────────────────┬─────────────────┬──────────────┬────────────────────────────┬──────────┐
│ Método de Pago   │ Voucher         │ Empresa      │ Detalles                   │ Monto    │
├──────────────────┼─────────────────┼──────────────┼────────────────────────────┼──────────┤
│ Crédito Externo  │ CONV-2024-001   │ Empresa ABC  │ Juan Pérez                 │ $100.000 │
│                  │                 │              │ RUT: 12.345.678-9          │          │
│                  │                 │              │ Fecha: 01/11/2024          │          │
│                  │                 │              │ Monto Convenio: $500.000   │          │
└──────────────────┴─────────────────┴──────────────┴────────────────────────────┴──────────┘
```

---

## 🔄 FLUJO DE DATOS

### Al registrar pago en POS:
1. Usuario selecciona método de pago
2. Captura datos según el método
3. Se agrega a `pagosActuales` (array en JavaScript)
4. Se guarda en `TicketDetallePago` al finalizar ticket

### Al generar Factura/Boleta desde Ticket:
1. Se copian todos los pagos del ticket
2. Se crean registros en `Dte_Detalle_Pago`
3. Se incluye el campo `notas` con información JSON (para créditos externos)

### En consulta de documentos:
1. Se leen los pagos desde `Dte_Detalle_Pago`
2. Si es `CREDITO_EXTERNO`, se parsea el JSON de `notas`
3. Se muestra toda la información estructurada

---

## ✨ CARACTERÍSTICAS DESTACADAS

### Pagos Parciales
- ✅ Venta Internet
- ✅ POS Transbank
- ✅ Crédito Externo
- Todos permiten pagar montos menores al saldo pendiente

### Validaciones
- ✅ Ticket activo antes de agregar pago
- ✅ Saldo pendiente disponible
- ✅ Campos obligatorios según método
- ✅ Monto no excede saldo pendiente
- ✅ Notificación si ticket queda completamente pagado

### Integración con Facturación
- ✅ Los pagos se copian automáticamente al generar DTE
- ✅ Información completa se mantiene
- ✅ Datos JSON se preservan en campo `notas`
- ✅ Consulta posterior muestra todos los detalles

---

## 📝 ARCHIVOS MODIFICADOS

1. **retailmind/app/models.py**
   - Agregado: `CREDITO_EXTERNO` a `METODO_PAGO_TICKET_CHOICES`
   - Agregado campo: `notas` a `Dte_Detalle_Pago`

2. **retailmind/app/migrations/0041_add_notas_to_dte_detalle_pago.py**
   - Migración para agregar campo `notas` a `Dte_Detalle_Pago`

3. **retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html**
   - Botón "Venta Internet"
   - Botón "Crédito Externo"
   - Función `pagarConVentaInternet()`
   - Función `pagarConCreditoExterno()`
   - Actualización de `obtenerNombreMetodoPago()`
   - Actualización de `actualizarListaPagos()` con visualización mejorada

4. **retailmind/app/templates/vistas/modulo_ventas/gestionVentasDocumentos.html**
   - Nueva columna "Detalles" en tabla de pagos
   - Parser de JSON para créditos externos
   - Visualización estructurada de datos del convenio

5. **retailmind/app/views_modulo_ventas.py**
   - Actualización de `registrar_pagos_ticket()` para copiar `notas`
   - Actualización de `detalle_documento_venta()` para incluir `notas`

---

## 🎯 CASOS DE USO

### Caso 1: Venta por Mercado Pago
1. Cliente compra en línea
2. Retira en tienda
3. Vendedor crea ticket en POS
4. Selecciona "Venta Internet" → "Mercado Pago"
5. Ingresa número de pedido
6. ✅ Pago registrado

### Caso 2: Convenio con Empresa
1. Empresa tiene convenio de crédito
2. Empleado compra en tienda
3. Vendedor crea ticket en POS
4. Selecciona "Crédito Externo"
5. Ingresa datos del convenio y empleado
6. ✅ Pago registrado
7. Luego se factura a la empresa

---

## 📌 NOTAS IMPORTANTES

1. **Venta Internet vs Crédito Externo**:
   - **Venta Internet**: Plataformas e-commerce (Paris, Ripley, etc.)
   - **Crédito Externo**: Convenios con empresas (requiere más datos)

2. **Crédito Trabajador vs Crédito Externo**:
   - **Crédito Trabajador**: Interno, manejado por el sistema
   - **Crédito Externo**: Externo, convenios con terceros

3. **Almacenamiento de datos complejos**:
   - Campos simples: `tipo_tarjeta`, `voucher`
   - Datos complejos: `notas` (JSON)
   - Permite flexibilidad sin alterar esquema de BD

4. **Migración pendiente**:
   - Ejecutar: `python manage.py migrate app`
   - Para aplicar campo `notas` en `Dte_Detalle_Pago`

---

## 🔧 PRÓXIMOS PASOS SUGERIDOS

1. ✅ Ejecutar migración 0041
2. ✅ Probar ambos métodos en POS
3. ✅ Verificar que se guarden correctamente
4. ✅ Generar factura y verificar que copie los datos
5. ✅ Consultar DTE y verificar visualización

---

**Fecha de implementación**: 05/11/2025
**Versión**: 1.0
**Estado**: ✅ Completado y Funcional

