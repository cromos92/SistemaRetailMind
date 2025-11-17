# 📖 Guía de Usuario - Sistema de Requerimientos

## 🎯 ¿Qué es el Sistema de Requerimientos?

Sistema para gestionar **garantías, devoluciones, cambios y reclamos** de productos de forma organizada y con seguimiento completo.

---

## 👥 ¿Cuál es mi rol?

### 🔍 Cómo saber mi rol:

1. Ir a tu perfil de usuario
2. Ver el campo "Rol"

**Roles disponibles**:
- **Vendedor/Cajero**: Crea y consulta requerimientos
- **Supervisor (Jefe Local)**: Revisa y aprueba casos de su sucursal
- **Administrador**: Gestiona todo el sistema

---

## 📝 GUÍA PARA VENDEDOR/CAJERO

### ¿Cuándo usar este sistema?

- Cliente tiene problema con un producto
- Cliente quiere hacer garantía
- Cliente quiere cambiar o devolver producto
- Cliente presenta un reclamo

### Paso a Paso: Crear Requerimiento

#### 1. Buscar el Documento
```
✅ Pregunta al cliente el número de boleta o ticket
✅ Ingresa el número en "Buscar Documento"
✅ Click "Buscar"
✅ Sistema auto-completa todos los datos
```

**Tip**: Si no tiene documento, puedes continuar sin buscarlo.

#### 2. Seleccionar Producto
```
✅ Si encontró el documento:
   - Activa "Usar producto del documento"
   - Selecciona el producto específico
   
✅ Si NO encontró o producto es otro:
   - Ingresa SKU manualmente
   - O busca por SKU
```

#### 3. Verificar Cliente
```
✅ Datos ya completos desde documento
✅ O busca por RUT si es cliente conocido
✅ O crea cliente nuevo si no existe
```

#### 4. Completar el Requerimiento
```
✅ Selecciona tipo (Garantía, Devolución, Cambio, Reclamo)
✅ Describe el problema
✅ Agrega fotos (hasta 5)
✅ Click "Crear Requerimiento"
```

#### 5. ¿Qué pasa después?
```
✅ Se crea con estado PENDIENTE
✅ Tu supervisor lo revisará
✅ Puedes ver el estado en la lista
✅ Te notificarán cuando haya cambios
```

### Consultar Mis Requerimientos

```
1. Menu → Requerimientos → Lista de Requerimientos
2. Ver todos tus casos
3. Click en número para ver detalle
4. Ver estado actual y historial
```

**Estados que verás**:
- 🟡 Pendiente - Esperando revisión
- 🔵 En Revisión - Supervisor revisando
- 🟣 Esperando Proveedor - Enviado al proveedor
- 🟢 Aprobado - Caso aceptado
- 🔴 Rechazado - No procede
- ✅ Completado - Resuelto

---

## 👨‍💼 GUÍA PARA SUPERVISOR

### Tu Responsabilidad

Revisar y aprobar/rechazar requerimientos de **TU sucursal**.

### Dashboard del Supervisor

```
┌─────────────────────────────────────────┐
│ Total: 45  │ Pendientes: 12 │ +7d: 3   │
└─────────────────────────────────────────┘
```

**Solo ves requerimientos de tu sucursal**

### Paso a Paso: Revisar Requerimiento

#### 1. Ver Pendientes
```
1. Menu → Requerimientos → Lista
2. Filtrar por "Pendientes"
3. Click en un requerimiento
```

#### 2. Revisar el Caso
```
✅ Lee el motivo y descripción
✅ Revisa las fotos adjuntas
✅ Verifica datos del cliente y documento
✅ Decide si procede o no
```

#### 3. Tomar Acción

**Si es caso SIMPLE (cambio directo, devolución clara)**:
```
1. Click "Aprobar"
2. Agrega comentario
3. Confirmar
```

**Si NO procede**:
```
1. Click "Rechazar"
2. Explica el motivo (obligatorio)
3. Confirmar
```

**Si es caso COMPLEJO (requiere proveedor)**:
```
1. Click "Cambiar Estado"
2. Selecciona "En Revisión"
3. Comenta "Requiere aprobación de administración"
4. El administrador lo gestionará
```

### ¿Qué NO puedes hacer?

❌ NO puedes enviar emails a proveedores (solo Admin)  
❌ NO puedes ver requerimientos de otras sucursales  
❌ NO puedes editar requerimientos de vendedores

---

## 👔 GUÍA PARA ADMINISTRADOR

### Tu Responsabilidad

Gestionar **TODOS** los requerimientos, comunicación con proveedores y casos complejos.

### Dashboard del Administrador

```
┌──────────────────────────────────────────────────────────┐
│ Total: 150 │ Pendientes: 25 │ Sin Resp +7d: 8 🔴│ Compl: 98 │
└──────────────────────────────────────────────────────────┘
```

**Ves TODO el sistema - todas las sucursales**

### Flujo: Enviar a Proveedor

#### 1. Identificar Caso
```
1. Ir a Lista de Requerimientos
2. Filtrar "En Revisión"
3. O abrir desde notificación
4. Click en requerimiento
```

#### 2. Verificar Proveedor
```
✅ Verifica que tenga proveedor asignado
✅ Si NO tiene: edita y asigna proveedor
✅ Verifica email del proveedor
```

#### 3. Enviar Email
```
1. Click "Enviar a Proveedor"
2. Confirmar
3. Sistema:
   ✅ Envía email con fotos
   ✅ Cambia a ESPERANDO_PROVEEDOR
   ✅ Inicia contador de días
```

#### 4. Hacer Seguimiento

**Ver casos sin respuesta**:
```
1. Card "Sin Respuesta +7d" → Click
2. Ver lista filtrada
3. Abrir caso
4. Ver "12 días sin respuesta ⚠️"
```

**Opciones**:
- **Re-enviar Correo**: Envía recordatorio automático
- **Llamar**: Llama al proveedor y registra respuesta manual
- **Registrar Respuesta**: Si proveedor respondió por otro medio

#### 5. Registrar Respuesta

**Cuando proveedor responde** (por email, teléfono, etc.):
```
1. Click "Registrar Respuesta"
2. Seleccionar decisión:
   - ✅ Aprobado
   - ❌ Rechazado
   - ⚠️ Parcial
3. Copiar respuesta del proveedor
4. Verificar fecha (hoy por defecto)
5. Guardar
```

**Sistema**:
- Cambia estado a APROBADO o RECHAZADO
- Registra en historial
- Detiene contador de días
- Notifica al vendedor

#### 6. Completar Caso

**Para casos APROBADOS**:
```
1. Marcar "Iniciar Proceso"
2. Ejecutar cambio/devolución/reparación
3. Cuando esté listo:
   - Click "Completar"
   - Describir resolución final
   - Guardar
```

### Gestión de Alertas

**Card Roja "Sin Respuesta +7d"**:
```
Click → Filtra automáticamente
Ver lista de casos urgentes
Tomar acción:
  - Re-enviar
  - Llamar
  - Escalar
```

---

## 💡 TIPS Y MEJORES PRÁCTICAS

### Para Vendedores

✅ **Siempre busca el documento** - Auto-completa todo  
✅ **Adjunta fotos claras** - Ayuda al proveedor a decidir  
✅ **Describe bien el problema** - Evita idas y vueltas  
✅ **Verifica datos del cliente** - Evita errores  

### Para Supervisores

✅ **Revisa casos en < 24 horas** - Evita que se acumulen  
✅ **Comenta tus decisiones** - Deja registro claro  
✅ **Aprueba solo casos simples** - Escala los complejos  
✅ **Verifica fotos antes de aprobar** - Asegura procedencia  

### Para Administradores

✅ **Monitorea card roja diariamente** - Casos urgentes  
✅ **Llama si > 10 días sin respuesta** - No solo email  
✅ **Registra TODO en el sistema** - Incluso llamadas  
✅ **Revisa estadísticas semanalmente** - Detecta patrones  
✅ **Exporta reportes mensuales** - Para gerencia  

---

## 🆘 PROBLEMAS COMUNES

### P: No puedo enviar a proveedor
**R**: Solo Administradores pueden. Si eres Supervisor, marca el caso como "Requiere proveedor" para que Admin lo gestione.

### P: No encuentro el documento
**R**: 
- Verifica que el número sea correcto
- Prueba buscar por correlativo en lugar de folio
- Verifica que sea de tu sucursal
- Si no existe, continúa sin documento

### P: Cliente no existe en sistema
**R**: 
- Click "Crear Cliente"
- Completa datos
- Guarda
- Datos se auto-completan

### P: Proveedor no responde
**R**:
- Espera 7 días
- Sistema te alertará (card roja)
- Re-envía correo
- O llama y registra respuesta manual

### P: ¿Cómo sé el estado actual?
**R**:
- Ver badge de color en la lista
- Abrir detalle para ver historial completo
- Cada cambio queda registrado

---

## 📱 ACCESO AL SISTEMA

```
1. Inicia sesión en RetailMind
2. Menu lateral → Módulo Requerimientos
3. Opciones:
   - Lista de Requerimientos (ver todos)
   - Crear Requerimiento (nuevo caso)
   - Gestionar Requerimientos (admin)
```

---

## 🎓 VIDEO TUTORIALES SUGERIDOS

### Para crear:

1. **Vendedor - Cómo crear un requerimiento** (5 min)
2. **Supervisor - Cómo revisar y aprobar** (5 min)
3. **Admin - Cómo enviar a proveedor** (5 min)
4. **Admin - Cómo hacer seguimiento** (3 min)
5. **Admin - Dashboard y reportes** (5 min)

---

## ✅ RESUMEN DE FUNCIONALIDADES POR ROL

| Funcionalidad | Vendedor | Supervisor | Admin |
|---------------|----------|------------|-------|
| Crear requerimiento | ✅ | ✅ | ✅ |
| Ver sus propios casos | ✅ | ✅ | ✅ |
| Ver casos de sucursal | Lectura | ✅ | ✅ |
| Ver todas las sucursales | ❌ | ❌ | ✅ |
| Aprobar/Rechazar | ❌ | ✅ | ✅ |
| Enviar a proveedor | ❌ | ❌ | ✅ |
| Registrar respuesta | ❌ | ❌ | ✅ |
| Ver seguimiento proveedor | ❌ | Lectura | ✅ |
| Exportar reportes | ❌ | Parcial | ✅ |
| Cambiar estados | Solo cancelar | Limitado | ✅ |

---

**¿Dudas?** Contacta al administrador del sistema.

**Versión**: 1.0  
**Última actualización**: 17 de Noviembre, 2024

