# Mejora en Gestión de Usuarios - Asignación de Sucursal

## Descripción
Se ha implementado la funcionalidad para asignar sucursales a los usuarios desde el modal de edición en la página de gestión de usuarios (`http://localhost:8000/users/gestion/`).

## Cambios Realizados

### 1. Backend - Vista `obtener_usuario` (users/views.py)
- **Modificación**: Se agregó la lógica para obtener las sucursales disponibles del usuario
- **Funcionalidad**: 
  - Busca todas las sucursales asignadas al usuario mediante la tabla `EmpresaUser`
  - Identifica cuál es la sucursal activa actual
  - Retorna la información en el JSON de respuesta

### 2. Backend - Nueva Vista `asignar_sucursal_sesion` (users/views.py)
- **Funcionalidad**: Permite asignar una sucursal a la sesión del usuario
- **Proceso**:
  1. Verifica que el usuario tenga acceso a la sucursal seleccionada
  2. Desactiva todas las sucursales del usuario en `EmpresaUser`
  3. Activa la sucursal seleccionada
  4. Si es el usuario actual, actualiza las variables de sesión:
     - `idEmpresaActual`
     - `empresaActual`
     - `nombreEmpresaActual`
     - `rutEmpresaActual`
     - `idSucursalActual`
     - `sucursalActual`
     - `alias`
     - `direccionSucursal`

### 3. URL - Nueva ruta (users/urls.py)
```python
path('asignar-sucursal/<int:usuario_id>/', views.asignar_sucursal_sesion, name='asignar_sucursal_sesion')
```

### 4. Frontend - Modal de Edición (gestion_usuarios.html)
- **Agregado**: Nueva sección "Gestión de Sucursal" en el modal de editar usuario
- **Elementos**:
  - Select con las sucursales disponibles para el usuario
  - Indicador de sucursal activa actual
  - Botón "Asignar a Sesión" para establecer la sucursal seleccionada
- **Visibilidad**: Solo se muestra si el usuario tiene sucursales asignadas

### 5. JavaScript - Funcionalidad (gestion_usuarios.html)
- **Modificación en `editarUsuario()`**: 
  - Carga y muestra las sucursales disponibles del usuario
  - Preselecciona la sucursal activa si existe
- **Nuevo manejador**: 
  - Botón "Asignar a Sesión" que llama al endpoint de asignación
  - Si es el usuario actual, recarga la página después de asignar para actualizar la sesión

## Flujo de Uso

1. El administrador hace clic en "Editar" en un usuario
2. El modal se abre y muestra la sección de "Gestión de Sucursal" (si el usuario tiene sucursales)
3. Se muestra un select con todas las sucursales disponibles del usuario
4. Se indica cuál es la sucursal activa actual (si existe)
5. El administrador selecciona una sucursal del dropdown
6. Hace clic en "Asignar a Sesión"
7. El sistema:
   - Verifica permisos
   - Actualiza la base de datos (marca la sucursal como activa)
   - Si es el usuario actual, actualiza la sesión inmediatamente
   - Muestra mensaje de éxito
8. Si es el usuario actual, la página se recarga automáticamente para reflejar los cambios

## Casos de Uso

### Usuario sin sucursal por defecto
- Al abrir el modal de edición, el select mostrará "Sin sucursal asignada"
- El administrador puede seleccionar una sucursal y asignarla
- Una vez asignada, se convierte en la sucursal activa del usuario

### Usuario con sucursal por defecto
- Al abrir el modal, el select muestra la sucursal activa preseleccionada
- Se muestra un indicador con checkmark verde indicando la sucursal activa
- El administrador puede cambiar a otra sucursal si es necesario

### Edición del usuario actual (sesión propia)
- Al asignar una sucursal, se actualiza inmediatamente la sesión
- La página se recarga automáticamente después de 1.5 segundos
- Esto asegura que toda la interfaz refleje la nueva sucursal activa

## Seguridad
- Solo usuarios con permisos de edición pueden asignar sucursales
- Se verifica que el usuario tenga acceso a la sucursal seleccionada mediante `EmpresaUser`
- Uso de CSRF token en las peticiones POST
- Validación de permisos en el backend

## Notas Técnicas
- Se utiliza la tabla `EmpresaUser` para gestionar la relación usuario-sucursal
- El campo `active` en `EmpresaUser` indica cuál es la sucursal activa del usuario
- Solo puede haber una sucursal activa por usuario a la vez
- Las variables de sesión siguen la convención existente en el sistema

## Archivos Modificados
1. `retailmind/users/views.py` - Vistas de backend
2. `retailmind/users/urls.py` - Rutas URL
3. `retailmind/users/templates/users/gestion_usuarios.html` - Interfaz de usuario

## Pruebas Sugeridas
1. ✅ Editar un usuario sin sucursal asignada
2. ✅ Asignar una sucursal desde el modal
3. ✅ Editar un usuario con sucursal asignada
4. ✅ Cambiar la sucursal activa
5. ✅ Editar el propio usuario y verificar que la sesión se actualice
6. ✅ Verificar que otros módulos respeten la nueva sucursal asignada
7. ✅ Probar con usuarios sin permisos de edición

## Mejoras Futuras (Opcionales)
- Agregar notificación visual cuando cambia la sucursal en la barra superior
- Permitir asignar sucursal desde la creación del usuario
- Agregar historial de cambios de sucursal
- Mostrar en la tabla de usuarios la sucursal activa de cada uno

