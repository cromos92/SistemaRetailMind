# 🔍 ANÁLISIS: Búsqueda en Edición Rápida No Filtra por Sucursal

## 📋 PROBLEMA IDENTIFICADO

La búsqueda en la edición rápida de precios (`/app/gestion-precios/edicion-rapida/`) **NO está filtrando productos por la sucursal del usuario** almacenada en la sesión.

---

## 🔎 ANÁLISIS TÉCNICO

### 1. **URL de Búsqueda**
```
http://localhost:8000/app/gestion-precios/buscar/?search=VU4024T&per_page=20
```

### 2. **Vista Backend: `buscar_productos()`**
**Archivo:** `retailmind/app/views_modulo_gestion_precios.py` (línea 123)

```python
def buscar_productos(request):
    """Buscar productos con filtros avanzados (agrupados por producto, no por talla)"""
    try:
        # Parámetros de búsqueda
        search = request.GET.get('search', '').strip()
        categoria_id = request.GET.get('categoria')
        marca_id = request.GET.get('marca')
        sucursal_id = request.GET.get('sucursal') or request.session.get('idSucursalActual')  # ← LÍNEA 130
        # ... más parámetros ...
        
        # Filtro por sucursal
        if sucursal_id:  # ← LÍNEA 167
            queryset = queryset.filter(sucursal_id=sucursal_id)  # ← LÍNEA 168
```

**✅ La vista SÍ intenta obtener la sucursal de:**
1. Parámetro GET `sucursal` (no enviado por el frontend)
2. Sesión del usuario `request.session.get('idSucursalActual')`

**✅ La vista SÍ aplica el filtro** si `sucursal_id` tiene valor.

---

### 3. **Frontend JavaScript**
**Archivo:** `retailmind/app/templates/vistas/modulo_existencias/edicion_rapida_precios.html` (línea 587)

```javascript
async function buscarProductos() {
    const termino = document.getElementById('quickSearch').value.trim();
    
    if (termino.length < 2) {
        return;
    }
    
    try {
        const response = await fetch(`/app/gestion-precios/buscar/?search=${encodeURIComponent(termino)}&per_page=20`);
        const data = await response.json();
        
        if (data.success) {
            productosDisponibles = data.productos;
            renderizarResultadosBusqueda(data.productos);
        }
    } catch (error) {
        console.error('Error buscando:', error);
    }
}
```

**❌ El frontend NO envía el parámetro `sucursal`**
- Solo envía: `search` y `per_page`
- Depende 100% de que la sesión tenga `idSucursalActual` configurado

---

## 🎯 CAUSAS POSIBLES DEL PROBLEMA

### Causa #1: Sesión No Inicializada (MÁS PROBABLE)
La variable de sesión `request.session['idSucursalActual']` puede estar:
- ❌ No definida (`None`)
- ❌ Vacía
- ❌ Con un valor incorrecto

**¿Cómo verificar?**
Revisar si el usuario tiene una sucursal asignada en su sesión después del login.

**¿Dónde se establece la sesión?**
```python
# En views.py - líneas 8456-8457, 8796-8797, 9158-9159
request.session['idSucursalActual'] = sucursal_id
request.session['idEmpresaActual'] = empresa_id
```

---

### Causa #2: Sesión Perdida en el Request
El navegador puede no estar enviando correctamente las cookies de sesión.

---

### Causa #3: Usuario Sin Sucursal Asignada
El usuario puede no tener una sucursal vinculada en su perfil `EmpresaUser`.

---

## 🛠️ SOLUCIONES PROPUESTAS

### **SOLUCIÓN 1: Agregar Parámetro Sucursal en el Frontend (RECOMENDADA)**

Modificar el JavaScript para que envíe explícitamente la sucursal:

```javascript
async function buscarProductos() {
    const termino = document.getElementById('quickSearch').value.trim();
    
    if (termino.length < 2) {
        return;
    }
    
    try {
        // Obtener sucursal actual desde variable de template Django
        const sucursalId = '{{ request.session.idSucursalActual }}';
        
        // Construir URL con parámetro sucursal
        const url = `/app/gestion-precios/buscar/?search=${encodeURIComponent(termino)}&per_page=20&sucursal=${sucursalId}`;
        
        const response = await fetch(url);
        const data = await response.json();
        
        if (data.success) {
            productosDisponibles = data.productos;
            renderizarResultadosBusqueda(data.productos);
        }
    } catch (error) {
        console.error('Error buscando:', error);
    }
}
```

**Ventajas:**
- ✅ Más explícito
- ✅ No depende de la sesión en el backend
- ✅ Más fácil de debuggear

---

### **SOLUCIÓN 2: Agregar Validación y Logging en Backend**

Modificar la vista `buscar_productos` para registrar y validar mejor:

```python
def buscar_productos(request):
    """Buscar productos con filtros avanzados"""
    try:
        # Parámetros de búsqueda
        search = request.GET.get('search', '').strip()
        sucursal_id = request.GET.get('sucursal') or request.session.get('idSucursalActual')
        
        # 🔍 AGREGAR LOGGING PARA DEBUG
        print(f"🔍 DEBUG BÚSQUEDA PRODUCTOS:")
        print(f"  - search: '{search}'")
        print(f"  - sucursal_id (GET): {request.GET.get('sucursal')}")
        print(f"  - sucursal_id (SESSION): {request.session.get('idSucursalActual')}")
        print(f"  - sucursal_id (FINAL): {sucursal_id}")
        
        # 🚨 VALIDAR QUE HAYA SUCURSAL
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal activa en la sesión',
                'productos': []
            }, status=400)
        
        # Resto del código...
        queryset = Producto.objects.select_related(...).all()
        
        if search:
            queryset = queryset.filter(...)
        
        # Filtro OBLIGATORIO por sucursal
        queryset = queryset.filter(sucursal_id=sucursal_id)  # ← Siempre filtrar
        
        # ... resto del código
```

**Ventajas:**
- ✅ Detecta cuando no hay sucursal
- ✅ Retorna error claro
- ✅ Logs para debugging

---

### **SOLUCIÓN 3: Verificar y Forzar Sesión al Cargar la Página**

Agregar en la vista de edición rápida:

```python
@login_required
def edicion_rapida_precios_view(request):
    """Vista de edición rápida de precios"""
    
    # 🔍 VERIFICAR SESIÓN
    sucursal_id = request.session.get('idSucursalActual')
    if not sucursal_id:
        # Intentar obtener sucursal del usuario
        try:
            empresa_user = EmpresaUser.objects.filter(
                user=request.user,
                status=True
            ).select_related('sucursal', 'empresa').first()
            
            if empresa_user and empresa_user.sucursal:
                # Establecer en sesión
                request.session['idSucursalActual'] = empresa_user.sucursal.id
                request.session['idEmpresaActual'] = empresa_user.empresa.id
                sucursal_id = empresa_user.sucursal.id
            else:
                # Redirigir a selección de sucursal
                messages.error(request, 'Por favor selecciona una sucursal')
                return redirect('seleccionar_empresa_sucursal')
        except Exception as e:
            messages.error(request, f'Error al obtener sucursal: {str(e)}')
            return redirect('home')
    
    context = {
        'sucursal_actual': sucursal_id,
        # ... resto del contexto
    }
    
    return render(request, 'vistas/modulo_existencias/edicion_rapida_precios.html', context)
```

---

## 🧪 PASOS PARA DEBUGGING

### 1. **Verificar Sesión del Usuario**
Agregar endpoint de debug temporal:

```python
# En views.py
@login_required
def debug_session_sucursal(request):
    """Endpoint temporal para verificar sesión"""
    return JsonResponse({
        'idSucursalActual': request.session.get('idSucursalActual'),
        'idEmpresaActual': request.session.get('idEmpresaActual'),
        'alias': request.session.get('alias'),
        'session_keys': list(request.session.keys()),
        'user': request.user.username,
    })
```

**Llamar desde la consola del navegador:**
```javascript
fetch('/app/debug_session_sucursal/')
    .then(r => r.json())
    .then(d => console.log('SESIÓN:', d));
```

---

### 2. **Agregar Logs en el Backend**
En `views_modulo_gestion_precios.py`, línea 130:

```python
sucursal_id = request.GET.get('sucursal') or request.session.get('idSucursalActual')
print(f"🔍 BUSCAR PRODUCTOS - sucursal_id: {sucursal_id}")
print(f"   GET param: {request.GET.get('sucursal')}")
print(f"   SESSION: {request.session.get('idSucursalActual')}")
```

---

### 3. **Verificar en Base de Datos**
```sql
-- Ver usuarios y sus sucursales asignadas
SELECT 
    u.username,
    e.razon_social AS empresa,
    s.alias AS sucursal,
    eu.status
FROM auth_user u
JOIN app_empresauser eu ON u.id = eu.user_id
JOIN app_empresa e ON eu.empresa_id = e.id
LEFT JOIN app_sucursal s ON eu.sucursal_id = s.id
WHERE u.username = 'TU_USUARIO';
```

---

## 📊 COMPARACIÓN CON OTRAS BÚSQUEDAS

### Búsqueda de Productos en Ticket Venta (QUE SÍ FUNCIONA)
**Archivo:** `views.py` línea 9190

```python
def buscar_productos_sucursal(request):
    """Búsqueda de productos con filtro por sucursal (usada en ticket_venta)"""
    try:
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        
        if not sucursal_id:  # ← VALIDA QUE EXISTA
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal activa en la sesión'
            }, status=400)
        
        # Filtro OBLIGATORIO
        productos_query = Producto.objects.filter(
            sucursal_id=sucursal_id  # ← Siempre filtra
        )
```

**Diferencias:**
1. ✅ **Valida** que `sucursal_id` exista antes de continuar
2. ✅ **Retorna error** si no hay sucursal
3. ✅ **Filtro obligatorio**, no condicional

---

## ✅ RECOMENDACIÓN FINAL

**Aplicar las 3 soluciones en conjunto:**

1. ✅ **Frontend:** Enviar parámetro `sucursal` explícitamente
2. ✅ **Backend:** Agregar validación obligatoria y retornar error si no hay sucursal
3. ✅ **Vista:** Verificar sesión al cargar la página de edición rápida

Esto asegura que:
- La búsqueda SIEMPRE filtre por sucursal
- Se detecten problemas de sesión inmediatamente
- Haya fallbacks en caso de error

---

## 📝 ARCHIVOS A MODIFICAR

1. **`retailmind/app/views_modulo_gestion_precios.py`** (línea 123-273)
   - Agregar validación obligatoria de sucursal
   - Agregar logging para debug

2. **`retailmind/app/templates/vistas/modulo_existencias/edicion_rapida_precios.html`** (línea 587)
   - Agregar parámetro sucursal en la URL de búsqueda

3. **`retailmind/app/views_modulo_gestion_precios.py`** (vista `edicion_rapida_precios_view`)
   - Verificar sesión al cargar la página

---

## 🚀 PRÓXIMOS PASOS

1. Verificar sesión actual del usuario
2. Aplicar Solución 1 (frontend)
3. Aplicar Solución 2 (backend)
4. Probar búsqueda con logs activados
5. Limpiar logs de debug

---

**Fecha:** 2025-11-07  
**Sistema:** RetailMind - Módulo Gestión de Precios

