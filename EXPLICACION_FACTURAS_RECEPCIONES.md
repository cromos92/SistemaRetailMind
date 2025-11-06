# 📋 Explicación Completa: Facturas en Recepciones de Productos

## 🔍 ¿Qué significan los mensajes en la pantalla de recepción?

### 1. "Factura asociada: 22"

**¿Qué es?**
- Es un mensaje de **fallback** cuando el sistema no puede mostrar el número de una factura
- Aparece cuando existe un `dte_id` (ID de factura) en el registro de recepción, pero el DTE fue eliminado o no existe

**¿Por qué aparece?**

Código responsable (línea 718 de `gestionCompras.html`):
```javascript
selectedFacturaValue = asociada ? asociada.numero : `Factura asociada: ${item.factura_id}`;
```

**Significado:**
- `item.factura_id = 22` → Hay una referencia al DTE con ID 22
- Pero `asociada` es `undefined` → El DTE no se encontró en la lista
- Entonces muestra: `"Factura asociada: 22"`

**Causas posibles:**
1. ✅ El DTE con ID 22 fue eliminado de la base de datos
2. ✅ La recepción tiene `dte_id = 22` pero ese registro ya no existe
3. ✅ Problema de integridad referencial (FK huérfana)

---

### 2. "160000 / 5"

**¿Qué es?**
- Es un **badge informativo** que muestra: `NÚMERO_FACTURA / TOTAL_UNIDADES`
- Aparece en la sección "Facturas Asociadas" de cada producto

**Formato:**
```
160000 / 5
   ↑      ↑
   │      └─ Total de unidades recepcionadas con esta factura
   └──────── Número de documento de la factura (campo numero_documento del DTE)
```

**Código responsable (líneas 745-747 de `gestionCompras.html`):**
```javascript
${(item.facturas_asociadas || []).map(f => `
    <span class="badge bg-secondary">${f.numero} / ${f.total}</span>
`).join(' ')}
```

**Significado en tu ejemplo:**
- **160000** = Número de la factura (DTE)
- **5** = 5 unidades en total han sido recepcionadas usando esa factura

**Datos que lo generan (views.py, líneas 3245-3249):**
```python
mapa_recepciones[key].append({
    'factura_id': r['dte__id'],
    'numero': r['dte__numero_documento'],  # ← Este es el 160000
    'total': r['total']                     # ← Este es el 5
})
```

---

## 🏗️ Estructura de Datos

### Modelo `Productos_Recepcionados`

```python
class Productos_Recepcionados(models.Model):
    compra_producto_talla = ForeignKey(Compras_Producto_Talla)  # Qué producto/talla
    producto_talla = ForeignKey(Producto_Talla)                  # Producto final (opcional)
    dte = ForeignKey(Dte)                                        # ← FACTURA ASOCIADA
    stockArribado = IntegerField()                               # Cantidad recepcionada
    fecha = DateField()
    estado = CharField()  # RECEPCIONADO_OK, RECEPCIONADO_PARCIAL, etc.
```

### Modelo `Dte` (Documento Tributario Electrónico)

```python
class Dte(models.Model):
    numero_documento = IntegerField()   # ← El número que aparece en "160000 / 5"
    tipo_documento = CharField()        # FACTURA_ELECTRONICA, GUIA_DESPACHO, etc.
    monto_con_iva = DecimalField()
    emisor = ForeignKey(Empresa)        # Proveedor
    receptor = ForeignKey(Empresa)      # Tu empresa
    fecha_emision = DateField()
```

---

## 🔄 Flujo de Recepción con Facturas

### Paso 1: Cargar productos para recepcionar
```python
# En views.py - función recepcionar_compra
# Obtiene las facturas del proveedor con saldo disponible
facturas_proveedor = Dte.objects.filter(
    tipo_transaccion='COMPRA',
    emisor=compra.empresa,
    receptor=compra.empresa
).values('id', 'numero_documento', 'monto_con_iva')
```

### Paso 2: Usuario ingresa cantidad y selecciona factura
- En el modal de recepción, cada producto/talla tiene:
  - Campo "Recepcionado" (cantidad)
  - Campo "Factura" (datalist con facturas disponibles)

### Paso 3: Guardar recepción
```python
# En views.py - función guardar_recepcion
Productos_Recepcionados.objects.create(
    compra_producto_talla=compra_talla,
    producto_talla=None,
    dte_id=factura_id,  # ← Se guarda el ID de la factura seleccionada
    stockArribado=cantidad
)
```

### Paso 4: Mostrar en pantalla
```javascript
// Al cargar la tabla de recepción
// Se construye el array facturas_asociadas con todas las recepciones previas
facturas_asociadas: [
    { numero: 160000, total: 5 },
    { numero: 170000, total: 3 }
]
```

---

## 🔧 Soluciones a Problemas Comunes

### Problema 1: "Factura asociada: 22" aparece en lugar del número

**Diagnóstico:**
```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind
.\venv\Scripts\python.exe .\retailmind\manage.py diagnosticar_facturas_recepciones --verbose
```

**Solución 1: Limpiar referencias huérfanas**
```bash
.\venv\Scripts\python.exe .\retailmind\manage.py diagnosticar_facturas_recepciones --reparar
```
Esto establecerá `dte_id = NULL` en las recepciones que tengan un DTE inexistente.

**Solución 2: Restaurar el DTE eliminado**
Si tienes un backup, restaura el DTE con ID 22.

**Solución 3: Manual desde shell de Django**
```python
python manage.py shell

from app.models import Productos_Recepcionados

# Ver recepciones problemáticas
recepciones = Productos_Recepcionados.objects.filter(dte_id=22)
print(f"Recepciones afectadas: {recepciones.count()}")

# Limpiar (establecer factura a None)
recepciones.update(dte_id=None)
```

---

### Problema 2: Facturas duplicadas o mal asignadas

**Prevención:**
El sistema valida en el frontend que solo se seleccionen facturas válidas:

```javascript
// En gestionCompras.html líneas 1273-1286
$datalist.find('option').each(function () {
    if (
        ($(this).val() || '').replace(/\s+/g, '').toLowerCase() === 
        facturaNumero.replace(/\s+/g, '').toLowerCase()
    ) {
        facturaId = $(this).data('id') || null;
    }
});
if (!facturaId) {
    errorFactura = true;
    facturaInput.addClass('is-invalid');
}
```

---

### Problema 3: No aparecen facturas en el datalist

**Causa:** No hay DTEs del proveedor con saldo disponible

**Solución:** Crear DTEs de compra primero

1. Ir a "Gestión de DTEs de Compras"
2. Crear un DTE con:
   - Emisor: El proveedor
   - Receptor: Tu empresa
   - Tipo: FACTURA_ELECTRONICA o GUIA_DESPACHO
   - Monto y productos

3. Ese DTE estará disponible para asociar en recepciones

---

## 📊 Consultas SQL Útiles

### Ver recepciones con facturas

```sql
SELECT 
    pr.id,
    pr.stockArribado,
    pr.dte_id,
    d.numero_documento as factura,
    d.monto_con_iva,
    cp.nombre as producto
FROM app_productos_recepcionados pr
LEFT JOIN app_dte d ON pr.dte_id = d.id
LEFT JOIN app_compras_producto_talla cpt ON pr.compra_producto_talla_id = cpt.id
LEFT JOIN app_compras_producto cp ON cpt.compra_producto_id = cp.id
WHERE pr.dte_id IS NOT NULL;
```

### Ver facturas con recepciones agrupadas

```sql
SELECT 
    d.numero_documento as factura,
    d.monto_con_iva,
    COUNT(pr.id) as total_recepciones,
    SUM(pr.stockArribado) as total_unidades
FROM app_dte d
LEFT JOIN app_productos_recepcionados pr ON pr.dte_id = d.id
WHERE d.tipo_transaccion = 'COMPRA'
GROUP BY d.id, d.numero_documento, d.monto_con_iva
ORDER BY total_unidades DESC;
```

### Buscar recepciones huérfanas

```sql
SELECT 
    pr.id,
    pr.dte_id,
    pr.stockArribado,
    cp.nombre as producto
FROM app_productos_recepcionados pr
LEFT JOIN app_dte d ON pr.dte_id = d.id
LEFT JOIN app_compras_producto_talla cpt ON pr.compra_producto_talla_id = cpt.id
LEFT JOIN app_compras_producto cp ON cpt.compra_producto_id = cp.id
WHERE pr.dte_id IS NOT NULL AND d.id IS NULL;
```

---

## 🎯 Caso de Uso Completo: Zapatillas ADIDAS

### Escenario
Tienes una compra de zapatillas ADIDAS con las siguientes tallas:
- Talla 39: 5 pares
- Talla 40: 5 pares
- Talla 41: 5 pares
- Talla 42: 5 pares

### Datos en la tabla `app_compras`
```
id | nombre              | empresa_id | temporada
22 | Compra Verano 2025  | 5          | Verano 2025
```

### Datos en `app_compras_producto`
```
id | compras_id | nombre     | atributo1 | atributo2 | costo  | precioSugerido
45 | 22         | ZAPATILLA  | ADIDAS    | NEGRO     | 10000  | 21990
```

### Datos en `app_compras_producto_talla`
```
id | compra_producto_id | talla | stock
78 | 45                 | 39    | 5
79 | 45                 | 40    | 5
80 | 45                 | 41    | 5
81 | 45                 | 42    | 5
```

### Recepción con factura 160000

Cuando recibes los productos y seleccionas la factura 160000, se crea:

**Tabla `app_productos_recepcionados`:**
```
id | compra_producto_talla_id | dte_id | stockArribado
33 | 78                       | 150    | 5
34 | 79                       | 150    | 5
35 | 80                       | 150    | 5
36 | 81                       | 150    | 5
```

**Tabla `app_dte` (la factura):**
```
id  | numero_documento | tipo_documento       | monto_con_iva | emisor_id | receptor_id
150 | 160000           | FACTURA_ELECTRONICA  | 800000        | 5         | 1
```

### Lo que ves en pantalla

```
┌─────────────────────────────────────────────────────────────────────┐
│ Artículo: DH45-001  │  ZAPATILLA ADIDAS NEGRO  │  Talla: 39       │
│ Costo: $10.000  │  Precio: $21.990  │  Stock: 5                   │
│ Recepcionado: [5]  │  Factura: [160000]                           │
│                                                                     │
│ Facturas asociadas: [160000 / 5] ← Aquí aparece el badge          │
└─────────────────────────────────────────────────────────────────────┘
```

**Interpretación:**
- **160000** = Número de la factura seleccionada
- **5** = 5 unidades de talla 39 fueron recepcionadas con esa factura

---

## 🚀 Comandos de Mantenimiento Creados

### 1. Diagnóstico general
```bash
python manage.py diagnosticar_facturas_recepciones --verbose
```
Muestra:
- Recepciones huérfanas
- DTEs sin número
- Estadísticas por factura
- Top facturas más usadas

### 2. Reparación automática
```bash
python manage.py diagnosticar_facturas_recepciones --reparar
```
Limpia automáticamente referencias a DTEs inexistentes.

### 3. Investigación específica
```bash
python manage.py investigar_recepcion_zapatillas
```
Busca productos ADIDAS y analiza sus recepciones en detalle.

---

## 📝 Conclusiones

1. **"Factura asociada: 22"** indica que el DTE con ID 22 fue eliminado pero las recepciones aún lo referencian

2. **"160000 / 5"** es información válida que muestra:
   - Factura #160000
   - 5 unidades recepcionadas en total con esa factura

3. **El sistema funciona correctamente**, pero puede haber inconsistencias por:
   - Eliminación de DTEs
   - Migraciones de datos
   - Ediciones manuales en la base de datos

4. **Usa los comandos de diagnóstico regularmente** para mantener la integridad de los datos

---

## 💡 Mejoras Futuras Sugeridas

### 1. Protección contra eliminación
Agregar validación antes de eliminar un DTE:

```python
def eliminar_dte(request, dte_id):
    dte = get_object_or_404(Dte, id=dte_id)
    
    # Verificar si tiene recepciones asociadas
    recepciones = Productos_Recepcionados.objects.filter(dte_id=dte_id).count()
    if recepciones > 0:
        return JsonResponse({
            'success': False,
            'error': f'No se puede eliminar. Hay {recepciones} recepciones asociadas.'
        })
    
    dte.delete()
    return JsonResponse({'success': True})
```

### 2. Soft delete para DTEs
En lugar de eliminar, marcar como inactivo:

```python
class Dte(models.Model):
    # ... campos existentes ...
    activo = models.BooleanField(default=True)
    fecha_eliminacion = models.DateTimeField(null=True, blank=True)
    eliminado_por = models.CharField(max_length=100, null=True, blank=True)
```

### 3. Auditlogging
Registrar cambios en recepciones:

```python
class RecepcionLog(models.Model):
    recepcion = models.ForeignKey(Productos_Recepcionados)
    accion = models.CharField(max_length=50)  # CREADO, MODIFICADO, ELIMINADO
    dte_anterior = models.IntegerField(null=True)
    dte_nuevo = models.IntegerField(null=True)
    usuario = models.CharField(max_length=100)
    fecha = models.DateTimeField(auto_now_add=True)
```

---

*Documento generado automáticamente por el sistema de diagnóstico RetailMind*

