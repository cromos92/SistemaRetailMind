# 🔧 Solución al Error 400 en Búsqueda de Productos

## 🚨 Problema Identificado

El error 400 (Bad Request) en `/app/buscar_productos_bodega/` se debe a que **no hay una sesión activa con sucursal configurada**.

## 🔍 Diagnóstico

### 1. Verificar Variables de Sesión

Accede a: `http://127.0.0.1:8000/app/debug_session/`

Deberías ver algo como:
```json
{
    "idSucursalActual": 1,
    "idEmpresaActual": 1,
    "nombreUsuario": "admin",
    "nombreEmpresaActual": "Mi Empresa",
    "rutEmpresaActual": "12345678-9",
    "alias": "Sucursal Principal",
    "all_session_keys": ["idSucursalActual", "idEmpresaActual", ...]
}
```

### 2. Problema Común

Si `idSucursalActual` es `null`, significa que **no hay sucursal activa en la sesión**.

## ✅ Soluciones

### Solución 1: Verificar Login y Empresa Activa

1. **Asegúrate de estar logueado correctamente**
2. **Verifica que tengas una empresa y sucursal asignada**

### Solución 2: Ejecutar Comando de Inicialización

```bash
# Desde la carpeta del proyecto Django
python manage.py inicializar_datos_dte
```

### Solución 3: Crear Empresa y Sucursal Manualmente

Si no tienes datos, crea manualmente:

```python
# En el shell de Django: python manage.py shell
from app.models import Empresa, Sucursal, EmpresaUser
from django.contrib.auth.models import User

# 1. Crear empresa
empresa = Empresa.objects.create(
    nombre="Mi Empresa Test",
    rut="12345678-9",
    nombre_fantasia="Mi Empresa",
    razon_social="Mi Empresa Limitada",
    giro="Comercio",
    direccion="Calle Falsa 123",
    comuna="Santiago",
    ciudad="Santiago",
    esProveedor=False,
    correoVendedor="ventas@miempresa.cl",
    correoIntercambio="intercambio@miempresa.cl",
    correoAdministrador="admin@miempresa.cl"
)

# 2. Crear sucursal
sucursal = Sucursal.objects.create(
    alias="Sucursal Principal",
    direccion="Calle Falsa 123",
    empresa=empresa
)

# 3. Asociar usuario (reemplaza 'admin' con tu usuario)
user = User.objects.get(username='admin')
EmpresaUser.objects.create(
    empresa=empresa,
    sucursal=sucursal,
    user=user,
    status=True,
    active=True
)

print(f"✅ Empresa creada: {empresa.id}")
print(f"✅ Sucursal creada: {sucursal.id}")
```

### Solución 4: Establecer Sesión Manualmente (Temporal)

Si necesitas una solución rápida, agrega esto al inicio de tu vista:

```python
# En views.py - buscar_productos_bodega (TEMPORAL)
def buscar_productos_bodega(request):
    # TEMPORAL: Establecer sesión si no existe
    if not request.session.get('idSucursalActual'):
        sucursal = Sucursal.objects.first()
        if sucursal:
            request.session['idSucursalActual'] = sucursal.id
            request.session['idEmpresaActual'] = sucursal.empresa.id
    
    # ... resto del código
```

## 🔄 Pasos para Resolver

### Paso 1: Verificar Estado Actual
```bash
# Acceder al debug
curl http://127.0.0.1:8000/app/debug_session/
```

### Paso 2: Si no hay datos, ejecutar inicialización
```bash
python manage.py inicializar_datos_dte
```

### Paso 3: Verificar que se crearon los datos
```bash
# En Django shell
python manage.py shell
>>> from app.models import Empresa, Sucursal
>>> print(f"Empresas: {Empresa.objects.count()}")
>>> print(f"Sucursales: {Sucursal.objects.count()}")
```

### Paso 4: Establecer sesión correcta
Esto depende de cómo tu sistema maneja el login. Normalmente se hace en el proceso de autenticación.

## 🎯 Verificación Final

1. **Accede a**: `http://127.0.0.1:8000/app/debug_session/`
2. **Verifica que `idSucursalActual` no sea null**
3. **Prueba la búsqueda de productos**: `http://127.0.0.1:8000/app/emisionDTE/`

## 📝 Notas Importantes

- **El sistema requiere que el usuario tenga una sucursal activa**
- **Las variables de sesión se establecen normalmente durante el login**
- **Si el problema persiste, verifica el proceso de autenticación de tu sistema**

## 🚀 Una vez resuelto

Cuando tengas la sesión correcta, el sistema debería funcionar perfectamente:

1. ✅ Búsqueda de productos funcionará
2. ✅ Filtros por marca/categoría funcionarán  
3. ✅ Selección de tallas funcionará
4. ✅ Emisión de DTE funcionará

---

**¿Necesitas ayuda adicional?** Comparte el resultado de `/app/debug_session/` para un diagnóstico más específico.
