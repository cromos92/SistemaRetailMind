# 🏢 Gestión de Sucursales - Documentación

## 📋 Descripción

Sistema completo de gestión de sucursales que permite crear, editar, listar y administrar sucursales desde el menú de configuración.

## ✨ Características Implementadas

### 1. **Modelo Sucursal Extendido**
Se ha ampliado el modelo `Sucursal` con los siguientes campos:

- **Básicos:**
  - `alias` (obligatorio): Identificador corto de la sucursal
  - `nombre`: Nombre completo de la sucursal
  
- **Ubicación:**
  - `direccion` (obligatorio): Dirección completa
  - `comuna`: Comuna donde se ubica
  - `ciudad`: Ciudad donde se ubica
  
- **Contacto:**
  - `telefono`: Número de teléfono
  - `email`: Correo electrónico
  
- **Estado:**
  - `activa`: Indica si la sucursal está activa o no
  
- **Metadata:**
  - `created_at`: Fecha y hora de creación
  - `updated_at`: Última actualización

### 2. **Vista de Gestión**
Interfaz completa con:
- ✅ Lista de todas las sucursales
- 🔍 Búsqueda y filtrado avanzado
- ➕ Creación de nuevas sucursales
- ✏️ Edición de sucursales existentes
- 🔴 Desactivación de sucursales
- ✅ Reactivación de sucursales desactivadas
- 📄 Paginación automática

### 3. **API REST**
Endpoints disponibles:

```
GET  /app/gestion-sucursales/                      # Vista principal
GET  /app/gestion-sucursales/listar/               # Listar con filtros
POST /app/gestion-sucursales/crear/                # Crear sucursal
GET  /app/gestion-sucursales/<id>/                 # Obtener detalles
POST /app/gestion-sucursales/editar/<id>/          # Editar sucursal
POST /app/gestion-sucursales/eliminar/<id>/        # Desactivar sucursal
POST /app/gestion-sucursales/activar/<id>/         # Reactivar sucursal
```

## 🚀 Cómo Usar

### Acceso al Sistema

1. **Desde el menú lateral:**
   ```
   Configuración > Gestión Sucursales
   ```

2. **URL directa:**
   ```
   http://localhost:8000/app/gestion-sucursales/
   ```

### Crear Nueva Sucursal

1. Click en botón "Nueva Sucursal"
2. Completar formulario:
   - **Alias** (*obligatorio*): Identificador corto (ej: "Sucursal Centro")
   - **Nombre**: Nombre completo (opcional)
   - **Dirección** (*obligatorio*): Dirección completa
   - **Comuna**: Comuna (opcional)
   - **Ciudad**: Ciudad (opcional)
   - **Teléfono**: Número de contacto (opcional)
   - **Email**: Correo electrónico (opcional)
   - **Estado**: Marcar si está activa (por defecto: activa)
3. Click en "Guardar Sucursal"

### Editar Sucursal

1. En la tabla de sucursales, click en botón de editar (📝)
2. Modificar los campos necesarios
3. Click en "Guardar Sucursal"

### Buscar y Filtrar

- **Búsqueda por texto:** Ingresa texto en el campo de búsqueda (busca en alias, nombre, dirección, comuna, ciudad, teléfono)
- **Filtro por estado:** Usa el selector para ver:
  - Todas las sucursales
  - Solo activas
  - Solo inactivas

### Desactivar Sucursal

1. Click en botón rojo (❌) junto a la sucursal
2. Confirmar acción
3. La sucursal quedará inactiva pero no se eliminará
   - Si tiene datos relacionados (productos, tickets, correlativos), solo se desactivará
   - Si no tiene datos relacionados, se eliminará físicamente

### Reactivar Sucursal

1. Filtra por "Solo inactivas"
2. Click en botón verde (✅) junto a la sucursal
3. Confirmar acción

## 🛠️ Instalación y Configuración

### 1. Aplicar Migraciones

```bash
python manage.py migrate
```

Esta migración agregará los nuevos campos al modelo Sucursal existente sin perder datos.

### 2. Verificar Permisos

Asegúrate de que el usuario tenga acceso al módulo de configuración.

### 3. Probar Funcionalidad

1. Accede a la gestión de sucursales
2. Verifica que las sucursales existentes se carguen correctamente
3. Prueba crear una nueva sucursal

## 📊 Campos del Modelo

### Campos Obligatorios ⚠️

- `alias`: Identificador único dentro de la empresa
- `direccion`: Dirección física de la sucursal
- `empresa`: Relación con la empresa (se asigna automáticamente)

### Campos Opcionales ℹ️

- `nombre`: Nombre oficial completo
- `comuna`: Comuna de ubicación
- `ciudad`: Ciudad de ubicación
- `telefono`: Número de contacto
- `email`: Correo electrónico
- `activa`: Estado (activa/inactiva)

## 🔒 Seguridad

- ✅ Requiere autenticación (`@login_required`)
- ✅ Solo puede ver/editar sucursales de su empresa
- ✅ Validación de duplicados por alias
- ✅ Protección CSRF en formularios
- ✅ Logging de todas las operaciones

## 🎨 Interfaz

- **Diseño responsive:** Funciona en desktop, tablet y móvil
- **Iconos descriptivos:** Facilita la identificación de acciones
- **Alertas visuales:** Confirmaciones y mensajes de error claros
- **Badges de estado:** Visualización rápida del estado de cada sucursal
- **Paginación intuitiva:** Navegación fácil entre páginas

## 🐛 Resolución de Problemas

### Error: "No tienes una empresa asignada"
**Solución:** Verifica que el usuario tenga un registro en `EmpresaUser` con una empresa asignada.

### Error al crear: "Ya existe una sucursal con ese alias"
**Solución:** Cambia el alias por uno único. Los alias deben ser únicos dentro de cada empresa.

### La tabla no carga sucursales
**Solución:** 
1. Verifica la consola del navegador (F12)
2. Revisa los logs del servidor
3. Asegúrate de que las migraciones se aplicaron correctamente

## 📝 Notas Importantes

1. **Migración segura:** La migración agrega campos con valores por defecto, no afecta datos existentes
2. **Compatibilidad:** Compatible con el modelo existente, no rompe funcionalidad actual
3. **Performance:** Las consultas incluyen `select_related` para optimizar rendimiento
4. **Validaciones:** Se validan alias únicos y campos obligatorios antes de guardar

## 🔄 Próximas Mejoras Sugeridas

- [ ] Exportar lista de sucursales a Excel/PDF
- [ ] Importar sucursales desde archivo CSV
- [ ] Dashboard con estadísticas por sucursal
- [ ] Gestión de permisos por sucursal
- [ ] Historial de cambios en sucursales
- [ ] Configuración de horarios de atención por sucursal

## 📞 Soporte

Para reportar problemas o sugerencias, contacta al equipo de desarrollo.

---

**Versión:** 1.0.0  
**Fecha:** Noviembre 2024  
**Desarrollado para:** RetailMind Sistema

