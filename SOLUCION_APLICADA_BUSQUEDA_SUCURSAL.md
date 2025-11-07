# ✅ SOLUCIÓN APLICADA: Búsqueda en Edición Rápida Filtra por Sucursal

## 📋 RESUMEN DEL PROBLEMA

La búsqueda en **Edición Rápida de Precios** no estaba filtrando productos por la sucursal del usuario almacenada en la sesión.

---

## 🔧 CAMBIOS APLICADOS

### 1. **Backend - Vista `edicion_rapida_precios_view`** ✅

**Archivo:** `retailmind/app/views_modulo_gestion_precios.py`

**Cambios:**
- ✅ Verifica si hay sucursal en la sesión al cargar la página
- ✅ Si no hay sucursal, intenta obtenerla desde `EmpresaUser`
- ✅ Si el usuario no tiene sucursal asignada, redirige a la selección de sucursal
- ✅ Pasa la `sucursal_actual` al template en el contexto

```python
@login_required
def edicion_rapida_precios_view(request):
    """Vista de edición rápida con navegación por Tab"""
    # Verificar sesión y establecer sucursal si no existe
    sucursal_id = request.session.get('idSucursalActual')
    
    if not sucursal_id:
        # Intentar obtener desde EmpresaUser
        empresa_user = EmpresaUser.objects.filter(...).first()
        if empresa_user and empresa_user.sucursal:
            # Establecer en sesión
            request.session['idSucursalActual'] = empresa_user.sucursal.id
            # ...
        else:
            # Redirigir a selección
            return redirect('seleccionar_empresa_sucursal')
    
    context = {
        'sucursal_actual': sucursal_id,
        'alias_sucursal': request.session.get('alias', ''),
    }
    
    return render(request, 'vistas/modulo_existencias/edicion_rapida_precios.html', context)
```

---

### 2. **Backend - Vista `buscar_productos`** ✅

**Archivo:** `retailmind/app/views_modulo_gestion_precios.py`

**Cambios:**
- ✅ Agregado **logging** para debug
- ✅ **Validación obligatoria** de sucursal antes de buscar
- ✅ Retorna error claro si no hay sucursal
- ✅ Filtro por sucursal ahora es **obligatorio**, no condicional

```python
def buscar_productos(request):
    """Buscar productos con filtros avanzados"""
    try:
        # Obtener sucursal
        sucursal_id = request.GET.get('sucursal') or request.session.get('idSucursalActual')
        
        # 🔍 LOGGING PARA DEBUG
        print(f"🔍 DEBUG BÚSQUEDA PRODUCTOS:")
        print(f"  - search: '{search}'")
        print(f"  - sucursal_id (GET): {request.GET.get('sucursal')}")
        print(f"  - sucursal_id (SESSION): {request.session.get('idSucursalActual')}")
        print(f"  - sucursal_id (FINAL): {sucursal_id}")
        
        # 🚨 VALIDAR QUE HAYA SUCURSAL (OBLIGATORIO)
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal activa en la sesión. Por favor, selecciona una sucursal.',
                'productos': [],
                'total': 0
            }, status=400)
        
        # ... resto del código ...
        
        # Filtro OBLIGATORIO por sucursal
        queryset = queryset.filter(sucursal_id=sucursal_id)  # ← Ya no es condicional
```

---

### 3. **Frontend - JavaScript** ✅

**Archivo:** `retailmind/app/templates/vistas/modulo_existencias/edicion_rapida_precios.html`

**Cambios:**
- ✅ Ahora **envía explícitamente** el parámetro `sucursal` en la URL
- ✅ Agregado logging en consola para debugging
- ✅ Maneja errores de sesión con alerta SweetAlert
- ✅ Muestra mensaje amigable si no hay sucursal

```javascript
async function buscarProductosRapido() {
    const termino = document.getElementById('quickSearch').value.trim();
    
    if (termino.length < 2) {
        return;
    }
    
    try {
        // 🔍 Obtener sucursal desde el contexto de Django
        const sucursalId = '{{ sucursal_actual }}';
        
        // 🔍 Construir URL con parámetro sucursal explícito
        let url = `/app/gestion-precios/buscar/?search=${encodeURIComponent(termino)}&per_page=20`;
        if (sucursalId && sucursalId !== 'None') {
            url += `&sucursal=${sucursalId}`;
        }
        
        console.log('🔍 Buscando productos:', { termino, sucursalId, url });
        
        const response = await fetch(url);
        const data = await response.json();
        
        if (data.success) {
            productosDisponibles = data.productos;
            renderizarResultadosBusqueda(data.productos);
            console.log(`✅ ${data.productos.length} productos encontrados`);
        } else {
            console.error('❌ Error en búsqueda:', data.error);
            if (data.error && data.error.includes('sucursal')) {
                mostrarAlertaSucursal();  // ← Alerta amigable
            }
        }
    } catch (error) {
        console.error('❌ Error buscando:', error);
    }
}
```

---

### 4. **Endpoint de Debug** ✅

**Archivo:** `retailmind/app/views_modulo_gestion_precios.py`

**Nueva función:**
```python
@login_required
def debug_session_precios(request):
    """Endpoint temporal para verificar sesión (SOLO PARA DEBUG)"""
    return JsonResponse({
        'idSucursalActual': request.session.get('idSucursalActual'),
        'idEmpresaActual': request.session.get('idEmpresaActual'),
        'alias': request.session.get('alias'),
        'nombreEmpresaActual': request.session.get('nombreEmpresaActual'),
        'session_keys': list(request.session.keys()),
        'user': request.user.username,
        'empresa_user': {...}  # Info del modelo EmpresaUser
    })
```

**URL agregada:**
```
/app/gestion-precios/debug-session/
```

---

## 🧪 CÓMO PROBAR LA SOLUCIÓN

### **Paso 1: Verificar Sesión**

Abre la consola del navegador (F12) y ejecuta:

```javascript
fetch('/app/gestion-precios/debug-session/')
    .then(r => r.json())
    .then(d => console.table(d));
```

**Resultado esperado:**
```json
{
  "idSucursalActual": 1,
  "idEmpresaActual": 1,
  "alias": "Casa Matriz",
  "nombreEmpresaActual": "Mi Empresa S.A.",
  "user": "admin",
  "empresa_user": {
    "empresa_id": 1,
    "sucursal_id": 1,
    "sucursal_alias": "Casa Matriz",
    "status": true
  }
}
```

**¿Qué verificar?**
- ✅ `idSucursalActual` debe tener un valor numérico (no `null`)
- ✅ `empresa_user` debe existir y tener `sucursal_id`
- ❌ Si `idSucursalActual` es `null`, el usuario no tiene sucursal activa

---

### **Paso 2: Probar Búsqueda con Logs**

1. Abre la página de edición rápida:
   ```
   http://localhost:8000/app/gestion-precios/edicion-rapida/
   ```

2. Abre la consola del navegador (F12) y la consola del servidor (terminal Django)

3. Busca un producto (ej: "VU4024T")

4. **En la consola del navegador** deberías ver:
   ```
   🔍 Buscando productos: { termino: "VU4024T", sucursalId: "1", url: "..." }
   ✅ 3 productos encontrados
   ```

5. **En la consola del servidor** deberías ver:
   ```
   🔍 DEBUG BÚSQUEDA PRODUCTOS:
     - search: 'VU4024T'
     - sucursal_id (GET param): 1
     - sucursal_id (SESSION): 1
     - sucursal_id (FINAL): 1
     - usuario: admin
   ✅ Filtrando por sucursal_id=1
   ```

---

### **Paso 3: Verificar Filtrado por Sucursal**

**Escenario de prueba:**

1. Busca un producto que existe en **OTRA** sucursal (no la tuya)
2. **Resultado esperado:** No debería aparecer en los resultados
3. Si aparece, significa que el filtro no está funcionando

**Verificación en base de datos:**
```sql
-- Ver productos con el artículo buscado en todas las sucursales
SELECT 
    p.id,
    p.articulo,
    p.descripcion,
    s.alias AS sucursal
FROM app_producto p
JOIN app_sucursal s ON p.sucursal_id = s.id
WHERE p.articulo LIKE '%VU4024T%';

-- Debería mostrar el producto en múltiples sucursales
-- La búsqueda solo debe devolver el de TU sucursal activa
```

---

## 🎯 COMPORTAMIENTO ESPERADO

### ✅ **Caso 1: Usuario con Sucursal Activa**

1. Usuario inicia sesión
2. Sesión tiene `idSucursalActual=1`
3. Entra a edición rápida → Carga correctamente
4. Busca producto "VU4024T"
5. Backend recibe `sucursal=1` (de GET o SESSION)
6. Filtra: `queryset.filter(sucursal_id=1)`
7. Retorna solo productos de sucursal 1

---

### ✅ **Caso 2: Usuario SIN Sucursal en Sesión**

1. Usuario inicia sesión
2. Sesión NO tiene `idSucursalActual`
3. Entra a edición rápida
4. Vista verifica sesión → No hay sucursal
5. Busca en `EmpresaUser` del usuario
6. **Si encuentra:** Establece en sesión y continúa
7. **Si NO encuentra:** Redirige a `/app/seleccionar-empresa-sucursal/`

---

### ✅ **Caso 3: Búsqueda Sin Sucursal (Error)**

1. Usuario busca sin tener sucursal
2. Backend detecta `sucursal_id=None`
3. Retorna error 400:
   ```json
   {
     "success": false,
     "error": "No hay sucursal activa en la sesión. Por favor, selecciona una sucursal.",
     "productos": [],
     "total": 0
   }
   ```
4. Frontend muestra alerta SweetAlert con opción de ir a selección

---

## 🔍 DEBUGGING AVANZADO

### **Ver Todas las Claves de Sesión**

En la vista o endpoint:
```python
print("🔍 TODAS LAS CLAVES DE SESIÓN:")
for key, value in request.session.items():
    print(f"  {key}: {value}")
```

---

### **Verificar EmpresaUser del Usuario**

```sql
SELECT 
    u.username,
    eu.empresa_id,
    e.razon_social,
    eu.sucursal_id,
    s.alias,
    eu.status
FROM auth_user u
JOIN app_empresauser eu ON u.id = eu.user_id
JOIN app_empresa e ON eu.empresa_id = e.id
LEFT JOIN app_sucursal s ON eu.sucursal_id = s.id
WHERE u.username = 'TU_USUARIO'
  AND eu.status = TRUE;
```

---

### **Forzar Establecer Sesión Manualmente**

Si la sesión se pierde constantemente, puedes forzarla en el middleware o en una vista temporal:

```python
def establecer_sesion_manual(request):
    """Vista temporal para forzar sesión (SOLO PARA DEBUG)"""
    request.session['idSucursalActual'] = 1  # ID de tu sucursal
    request.session['idEmpresaActual'] = 1   # ID de tu empresa
    request.session['alias'] = 'Casa Matriz'
    request.session.save()
    return JsonResponse({'success': True, 'message': 'Sesión establecida'})
```

---

## 📊 COMPARACIÓN: ANTES vs DESPUÉS

### **ANTES** ❌

```javascript
// Frontend
fetch(`/app/gestion-precios/buscar/?search=VU4024T&per_page=20`);
// → No envía sucursal

// Backend
sucursal_id = request.session.get('idSucursalActual')  
# → Puede ser None

if sucursal_id:  # ← Filtro CONDICIONAL
    queryset = queryset.filter(sucursal_id=sucursal_id)
    
# → Si sucursal_id=None, devuelve productos de TODAS las sucursales ❌
```

---

### **DESPUÉS** ✅

```javascript
// Frontend
const sucursalId = '{{ sucursal_actual }}';  // ← Desde contexto
fetch(`/app/gestion-precios/buscar/?search=VU4024T&per_page=20&sucursal=${sucursalId}`);
// → Envía sucursal explícitamente

// Backend
sucursal_id = request.GET.get('sucursal') or request.session.get('idSucursalActual')

if not sucursal_id:  # ← VALIDACIÓN OBLIGATORIA
    return JsonResponse({'success': False, 'error': '...'}, status=400)
    
queryset = queryset.filter(sucursal_id=sucursal_id)  # ← SIEMPRE filtra
```

---

## 🚨 POSIBLES PROBLEMAS Y SOLUCIONES

### **Problema 1: Sesión Sigue Sin Tener `idSucursalActual`**

**Causa:** El login no está estableciendo la sesión correctamente.

**Solución:**
Verificar que en el proceso de login se establezca:
```python
# En la vista de login o middleware
empresa_user = EmpresaUser.objects.filter(user=user, status=True).first()
if empresa_user:
    request.session['idSucursalActual'] = empresa_user.sucursal.id
    request.session['idEmpresaActual'] = empresa_user.empresa.id
```

---

### **Problema 2: Usuario Sin Sucursal Asignada en `EmpresaUser`**

**Causa:** El registro de `EmpresaUser` no tiene `sucursal_id`.

**Solución SQL:**
```sql
-- Verificar
SELECT * FROM app_empresauser WHERE user_id = X AND status = TRUE;

-- Asignar sucursal si falta
UPDATE app_empresauser 
SET sucursal_id = 1  -- ID de la sucursal deseada
WHERE user_id = X AND status = TRUE;
```

---

### **Problema 3: Cookies de Sesión No se Envían**

**Causa:** Problemas con cookies en el navegador.

**Solución:**
1. Limpiar cookies del dominio
2. Verificar que `settings.py` tenga:
   ```python
   SESSION_COOKIE_HTTPONLY = True
   SESSION_COOKIE_SAMESITE = 'Lax'
   ```

---

## ✅ CHECKLIST FINAL

- [ ] El endpoint `/app/gestion-precios/debug-session/` retorna `idSucursalActual` con valor
- [ ] La búsqueda envía el parámetro `sucursal` en la URL
- [ ] El backend valida y retorna error si no hay sucursal
- [ ] Los logs muestran el `sucursal_id` correcto
- [ ] Los productos retornados pertenecen solo a la sucursal del usuario
- [ ] Si busco un producto de otra sucursal, NO aparece en resultados

---

## 🔄 PRÓXIMOS PASOS (DESPUÉS DE VERIFICAR)

1. **Probar en producción** con usuarios reales
2. **Eliminar logs de debug** una vez confirmado que funciona
3. **Remover endpoint de debug** (`/gestion-precios/debug-session/`)
4. **Documentar** este comportamiento en el manual de usuario

---

## 📝 ARCHIVOS MODIFICADOS

1. ✅ `retailmind/app/views_modulo_gestion_precios.py`
   - Vista `edicion_rapida_precios_view`: Validación de sesión
   - Vista `buscar_productos`: Validación obligatoria y logging
   - Vista `debug_session_precios`: Endpoint de debug (temporal)

2. ✅ `retailmind/app/templates/vistas/modulo_existencias/edicion_rapida_precios.html`
   - Función `buscarProductosRapido()`: Envía parámetro sucursal
   - Función `mostrarAlertaSucursal()`: Manejo de errores

3. ✅ `retailmind/app/urls.py`
   - Agregada URL para endpoint de debug

---

**Fecha:** 2025-11-07  
**Sistema:** RetailMind - Módulo Gestión de Precios  
**Estado:** ✅ SOLUCIÓN APLICADA - LISTA PARA PROBAR

