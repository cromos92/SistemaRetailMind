# 📋 EXPLICACIÓN: Sistema de Regularización de Recepciones

## 🎯 URLs Analizadas

1. **`http://localhost:8000/app/recepcion-dte/`** - Recepción y registro de problemas
2. **`http://localhost:8000/app/regularizar-recepciones/`** - Resolución de problemas registrados

---

## 🔄 FLUJO COMPLETO: Desde Recepción hasta Regularización

### ETAPA 1: Recepción de DTE con Problemas
**URL:** `http://localhost:8000/app/recepcion-dte/`

#### ¿Qué hace cuando hay productos por regularizar?

Cuando el receptor (destino) recibe mercadería y encuentra problemas:

```python
# Vista: recepcion_dte (línea 52)
# Función: confirmar_recepcion_api (línea 322)

# PASO 1: Receptor ingresa las cantidades:
- cantidad_recepcionada: Lo que llegó físicamente
- cantidad_esperada: Lo que decía el DTE
- cantidad_danada: Productos dañados/rotos
- cantidad_faltante: Productos que nunca llegaron
```

#### Estados que se generan según el problema:

| Situación | Estado Generado | ¿Ingresa Stock? |
|-----------|----------------|-----------------|
| Todo llegó OK | `RECEPCIONADO` | ✅ SÍ (toda la cantidad) |
| Llegó menos de lo esperado | `RECEPCIONADO_PARCIAL` | ✅ SÍ (solo lo que llegó) |
| Llegó dañado | `RECEPCIONADO_DANADO` | ❌ NO (no ingresa lo dañado) |
| No llegó nada | `FALTANTE` | ❌ NO |

#### Ejemplo práctico:

```javascript
// DTE dice: 10 unidades del producto SKU-123
// Receptor recibe: 7 OK, 2 dañadas, 1 faltante

{
  cantidad_esperada: 10,
  cantidad_recepcionada: 7,  // Solo las buenas
  cantidad_danada: 2,
  cantidad_faltante: 1,
  observaciones: "2 productos llegaron con la caja rota, 1 no vino"
}

// RESULTADO:
// - Stock ingresado en destino: +7 unidades
// - Stock en origen: -10 (ya salió cuando emitieron)
// - Estado: RECEPCIONADO_PARCIAL
// - ⚠️ QUEDA PENDIENTE DE REGULARIZAR: 3 unidades (2 dañadas + 1 faltante)
```

#### ¿Qué pasa con el stock en esta etapa?

```python
# En confirmar_recepcion_api (línea ~400-500):

# 1. Solo ingresa lo que llegó OK
cantidad_a_ingresar = cantidad_recepcionada - cantidad_danada

if cantidad_a_ingresar > 0:
    # INGRESA stock al destino
    producto_destino.stock += cantidad_a_ingresar
    
    # Crea movimiento de INGRESO
    Movimientos_Producto.objects.create(
        tipo_movimiento='INGRESO',
        cantidad=cantidad_a_ingresar,
        sucursal_destino=sucursal_destino
    )

# 2. Lo dañado/faltante NO ingresa
# 3. Se crea registro en Productos_Recepcionados con el problema
```

#### ⚠️ Situación después de recepcionar con problemas:

```
ORIGEN (emisor):
- Stock: -10 unidades (ya salieron cuando emitió el DTE)

DESTINO (receptor):
- Stock: +7 unidades (solo las OK)

DIFERENCIA: 3 unidades "perdidas" → NECESITA REGULARIZACIÓN
```

---

## 🛠️ ETAPA 2: Regularización de Problemas
**URL:** `http://localhost:8000/app/regularizar-recepciones/`

### ¿Qué hace esta pantalla?

Muestra los productos con problemas y permite al **EMISOR** (sucursal origen) resolverlos.

```python
# Vista: regularizar_recepciones (línea 865)
# Función backend: obtener_productos_regularizar (línea 991)

# CONSULTA productos pendientes:
Productos_Recepcionados.objects.filter(
    estado__in=[
        'RECEPCIONADO_PARCIAL',
        'RECEPCIONADO_DANADO', 
        'FALTANTE',
        'EN_REGULARIZACION',
        'EN_SOLICITUD_REGULARIZACION'
    ]
)

# Filtra por:
# 1. Productos que ESTA SUCURSAL ENVIÓ (es el emisor)
# 2. Que fueron recepcionados con problemas
```

### Opciones de Regularización

#### OPCIÓN 1: Emitir Nota de Crédito (NC)

**¿Cuándo usar?** Cuando el emisor acepta devolver el dinero por productos dañados/faltantes.

```python
# Función: regularizar_producto_api - tipo 'EMITIR_NC' (línea 1687)

# 1. CREA NOTA DE CRÉDITO
nota_credito = Dte.objects.create(
    tipo_documento='NOTA DE CREDITO',
    es_nota_credito=True,
    documento_afectado=dte_original,
    monto_neto=cantidad_nc * precio_unitario,
    # ... otros campos
)

# 2. DEVUELVE STOCK A ORIGEN
producto_origen.stock += cantidad_nc  # ✅ Recupera el stock
producto_origen.save()

# 3. CREA MOVIMIENTO DE INGRESO en origen
Movimientos_Producto.objects.create(
    dte=nota_credito,
    ProductoTalla=producto_origen,
    sucursal_destino=dte_original.sucursal,  # Vuelve al emisor
    cantidad=cantidad_nc,  # Positivo
    concepto='DEVOLUCION_NC',
    tipo_movimiento='INGRESO',
    estado='COMPLETADO'
)

# 4. ACTUALIZA ESTADO DE RECEPCIÓN
recepcion.estado = 'REGULARIZADO'
recepcion.fecha_regularizacion = timezone.now()
```

**Resultado:**
```
ANTES DE NC:
- Origen: -10 (salieron)
- Destino: +7 (entraron solo las buenas)
- Diferencia: -3 ❌

DESPUÉS DE NC por 3 unidades:
- Origen: -10 + 3 = -7 ✅ (correcto)
- Destino: +7 ✅ (correcto)
- Diferencia: 0 ✅ (cuadrado)
```

---

#### OPCIÓN 2: Regularización MASIVA (Nota de Crédito por múltiples productos)

**¿Cuándo usar?** Cuando el DTE completo tiene problemas en varios productos.

```python
# Función: regularizar_dte_masivo (línea 2318)

# Ejemplo: DTE #1234 con 5 productos, 3 tienen problemas

productos_nc = [
    {'producto': 'SKU-A', 'cantidad_nc': 2, 'precio': 1000},
    {'producto': 'SKU-B', 'cantidad_nc': 3, 'precio': 1500},
    {'producto': 'SKU-C', 'cantidad_nc': 1, 'precio': 2000}
]

# GENERA 1 SOLA NC CON TODOS LOS PRODUCTOS
total_neto = (2*1000) + (3*1500) + (1*2000) = 8500
iva = 8500 * 0.19 = 1615
total = 10115

# Para CADA producto:
for prod_nc in productos_nc:
    # 1. Devuelve stock a origen
    producto_origen.stock += cantidad
    
    # 2. Crea movimiento INGRESO
    Movimientos_Producto.objects.create(
        concepto='DEVOLUCION_NC',
        tipo_movimiento='INGRESO'
    )
    
    # 3. Marca como REGULARIZADO
    recepcion.estado = 'REGULARIZADO'
```

**Ventaja:** Una sola NC en lugar de múltiples, más eficiente administrativamente.

---

#### OPCIÓN 3: Cambio por Producto Nuevo

**¿Cuándo usar?** Cuando el emisor acepta enviar reemplazo.

```python
# Función: regularizar_producto_api - tipo 'CAMBIAR_PRODUCTO' (línea ~1924-2157)

# CASO A: Envía producto DIFERENTE
if producto_envio_id != recepcion.producto_talla_id:
    # 1. DEVUELVE el problemático (como NC implícita)
    producto_problema.stock += cantidad_problema
    
    Movimientos_Producto.objects.create(
        tipo_movimiento='INGRESO',
        concepto='DEVOLUCION_CAMBIO'
    )
    
    # 2. DESPACHA el nuevo
    producto_nuevo.stock -= cantidad_envio
    
    Movimientos_Producto.objects.create(
        tipo_movimiento='EGRESO',
        concepto='TRASPASO_SALIDA'
    )

# CASO B: Envía MÁS del MISMO (completa faltante)
else:
    # Solo despacha más
    producto_mismo.stock -= cantidad_envio
    
    Movimientos_Producto.objects.create(
        tipo_movimiento='EGRESO',
        concepto='TRASPASO_COMPLEMENTO'
    )
```

**Resultado:**
```
CASO A (producto diferente):
- Producto A problemático: devuelto a origen (+3)
- Producto B nuevo: despachado desde origen (-3)
- Destino recibe: Producto B nuevo

CASO B (mismo producto):
- Origen: envía más unidades (-3)
- Destino: recibe las faltantes (+3)
- Se completa el pedido original
```

---

## ⚠️ SISTEMA DE ALERTAS: ¿Se crean automáticamente?

### ❌ NO hay un sistema de alertas automáticas para regularización

Según el análisis del código:

1. **NO existe modelo `Alerta_Regularizacion`** o similar
2. **Solo existe:** `DteAlertaDescartada` (para descartar notificaciones de DTEs pendientes)
3. **Las "alertas" son:**
   - Registros en `Productos_Recepcionados` con estados problemáticos
   - Contador en el navbar: `obtener_dtes_pendientes_regularizar()`

### ✅ Lo que SÍ existe como "sistema de alertas":

```python
# Función: obtener_dtes_pendientes_regularizar (línea 17947)

# Busca DTEs con problemas para mostrar en el navbar
dtes_query = Dte.objects.filter(
    sucursal_id=sucursal_id,  # Emitidos por esta sucursal
    tipo_transaccion='TRASPASO',
    estado_dte__in=['RECEPCIONADO_PARCIAL', 'EN_REGULARIZACION']
).annotate(
    productos_con_problemas=Count(
        'recepciones',
        filter=Q(recepciones__estado__in=[
            'EN_REGULARIZACION',
            'FALTANTE', 
            'RECEPCIONADO_PARCIAL',
            'RECEPCIONADO_DANADO'
        ])
    )
).filter(productos_con_problemas__gt=0)

# Retorna:
{
    'total_pendientes': 5,  // Total de DTEs con problemas
    'dtes': [
        {
            'numero_documento': '1234',
            'productos_problema': 3,
            'dias_pendiente': 5,
            'urgente': true  // Si > 3 días
        }
    ]
}
```

### 🔔 Notificaciones (pero no implementadas completamente):

```python
# utils.py (línea 47-71)

def notificar_nueva_solicitud(solicitud):
    """Notifica al emisor sobre nueva solicitud"""
    # TODO: Implementar notificación por email o sistema interno
    print(f"✉️ Notificación: Nueva solicitud #{solicitud.numero_solicitud}")
    pass  # ⚠️ Solo imprime en consola, NO envía email ni push

def notificar_solicitud_aprobada(solicitud):
    """Notifica al receptor que su solicitud fue aprobada"""
    # TODO: Implementar notificación
    pass  # ⚠️ No hace nada

def notificar_solucion_ejecutada(solicitud):
    """Notifica al receptor que la solución fue ejecutada"""
    # TODO: Implementar notificación
    pass  # ⚠️ No hace nada
```

---

## 📊 CONFIRMACIÓN: ¿Se crea alerta al regularizar?

### Respuesta: **❌ NO se crea una alerta formal**

**Lo que SÍ sucede:**

1. ✅ El estado del `Productos_Recepcionados` cambia a `'REGULARIZADO'`
2. ✅ Se registra `fecha_regularizacion` y `regularizado_por`
3. ✅ Se agregan observaciones al campo `observaciones`
4. ✅ La NC queda registrada en el modelo `Dte`
5. ✅ Se crean movimientos de stock en `Movimientos_Producto`

**Lo que NO sucede:**

1. ❌ NO se crea un registro en tabla `Alertas` (no existe esa tabla)
2. ❌ NO se envía email automático al receptor
3. ❌ NO se envía notificación push
4. ❌ NO aparece en un módulo de "notificaciones" independiente

**La "alerta" es implícita:**
- El receptor ve que el estado cambió a `REGULARIZADO`
- Puede ver la NC generada en el módulo de documentos
- El contador del navbar disminuye (ya no aparece como pendiente)

---

## 🎯 RESUMEN FINAL

### http://localhost:8000/app/recepcion-dte/

**Función:** Recibir mercadería y **REGISTRAR problemas**

- ✅ Ingresa stock (solo lo que llegó OK)
- ✅ Crea movimiento de INGRESO en destino
- ✅ Registra productos con problemas en `Productos_Recepcionados`
- ✅ Cambia estado del DTE según problemas encontrados
- ❌ NO resuelve los problemas (solo los documenta)

### http://localhost:8000/app/regularizar-recepciones/

**Función:** **RESOLVER problemas** registrados

**Opciones disponibles:**

1. **Nota de Crédito Individual:**
   - Devuelve stock a origen
   - Crea NC por el valor
   - Marca como REGULARIZADO

2. **Nota de Crédito Masiva:**
   - Una sola NC para múltiples productos
   - Devuelve stock de todos
   - Marca todos como REGULARIZADO

3. **Cambio por Producto:**
   - Devuelve el problemático (si es diferente)
   - Envía producto nuevo/reemplazo
   - Crea nuevos movimientos

### ¿Crea alertas?

**NO crea alertas formales**, pero:
- ✅ Cambia estados en BD
- ✅ Genera documentos (NC)
- ✅ Actualiza contadores en navbar
- ✅ Registra observaciones
- ❌ NO envía notificaciones push/email (funciones vacías)

---

## 🔧 MEJORA SUGERIDA (si quisieras implementar alertas reales):

```python
# Crear modelo de Alertas
class AlertaRegularizacion(models.Model):
    tipo = models.CharField(max_length=50)  # 'NC_EMITIDA', 'CAMBIO_ENVIADO'
    dte = models.ForeignKey(Dte, on_delete=models.CASCADE)
    sucursal_origen = models.ForeignKey(Sucursal, related_name='alertas_enviadas')
    sucursal_destino = models.ForeignKey(Sucursal, related_name='alertas_recibidas')
    mensaje = models.TextField()
    leida = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

# Al regularizar con NC:
AlertaRegularizacion.objects.create(
    tipo='NC_EMITIDA',
    dte=nota_credito,
    sucursal_origen=emisor,
    sucursal_destino=receptor,
    mensaje=f'Se emitió NC #{numero_nc} por ${total_con_iva}',
    leida=False
)

# Endpoint para obtener alertas:
def obtener_alertas_regularizacion(request):
    sucursal_id = request.session.get('idSucursalActual')
    alertas = AlertaRegularizacion.objects.filter(
        sucursal_destino_id=sucursal_id,
        leida=False
    ).order_by('-fecha_creacion')[:10]
    
    return JsonResponse({
        'alertas': list(alertas.values()),
        'total_no_leidas': alertas.count()
    })
```

Pero **actualmente esto NO está implementado** en el sistema.
