# 🧾 Sistema de Gestión de Correlativos - RetailMind

## 📋 Descripción

Sistema completo para la gestión de correlativos por tipo de documento y sucursal, con las siguientes características:

### ✨ Funcionalidades Principales

1. **Gestión Completa de Correlativos**
   - ✅ Visualización de todos los correlativos por sucursal y tipo de documento
   - ✅ Creación y edición de correlativos
   - ✅ Renovación automática de rangos
   - ✅ Seguimiento de consumo en tiempo real

2. **Panel de Control Avanzado**
   - ✅ Estadísticas en tiempo real (Total, Activos, Críticos, Agotados)
   - ✅ Filtros por sucursal, tipo de documento y estado
   - ✅ Tabla interactiva con DataTables
   - ✅ Indicadores visuales de progreso y estado

3. **Monitoreo y Alertas**
   - ✅ Estados automáticos: Activo, Crítico, Agotado
   - ✅ Barras de progreso de consumo
   - ✅ Alertas cuando quedan menos de 100 números
   - ✅ Historial de uso por correlativo

4. **Funciones Avanzadas**
   - ✅ Renovación de correlativos con nuevos rangos
   - ✅ Validaciones de integridad (no duplicados)
   - ✅ Integración con el sistema de emisión de documentos
   - ✅ Consumo automático con cada documento emitido

## 🚀 Instalación y Configuración

### 1. Archivos Creados/Modificados

```
retailmind/
├── app/
│   ├── templates/vistas/
│   │   └── gestion_correlativos.html              # ✅ Nuevo - Interfaz principal
│   ├── management/commands/
│   │   └── inicializar_correlativos.py           # ✅ Nuevo - Comando de inicialización
│   ├── models.py                                  # ✅ Modificado - Métodos del modelo Correlativo
│   ├── views.py                                   # ✅ Modificado - Vistas de gestión
│   └── urls.py                                    # ✅ Modificado - URLs del módulo
└── app/templates/layout/
    └── menu.html                                  # ✅ Modificado - Enlace en menú
```

### 2. Modelo Correlativo Mejorado

El modelo `Correlativo` ahora incluye:

```python
class Correlativo(models.Model):
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE)
    tipo_dte = models.CharField(max_length=50)
    inicio = models.IntegerField()
    termino = models.IntegerField()
    fecha_actualizacion = models.DateField(null=True)
    alias = models.CharField(max_length=100)
    responsable = models.CharField(max_length=50)
    
    # Propiedades calculadas
    @property
    def disponibles(self):
        return max(0, self.termino - self.inicio + 1)
    
    @property
    def consumidos(self):
        return max(0, self.inicio - 1)
    
    @property
    def porcentaje_consumo(self):
        if self.total_rango > 0:
            return (self.consumidos / self.total_rango) * 100
        return 0
    
    @property
    def estado(self):
        if self.disponibles <= 0:
            return 'agotado'
        elif self.disponibles <= 100:
            return 'critico'
        else:
            return 'activo'
    
    def obtener_siguiente_numero(self):
        # Lógica de consumo automático
        pass
```

### 3. Inicializar Correlativos

```bash
# Ejecutar desde la carpeta del proyecto Django
python manage.py inicializar_correlativos

# Para forzar la actualización de correlativos existentes
python manage.py inicializar_correlativos --force
```

Este comando creará correlativos para todos los tipos de documento principales:
- **FACTURA ELECTRONICA**: 1 - 100,000
- **BOLETA ELECTRONICA**: 1 - 100,000
- **GUIA**: 1 - 50,000
- **NOTA DE CREDITO**: 1 - 10,000
- **NOTA DE DEBITO**: 1 - 10,000
- **TICKET**: 1 - 999,999

## 🎯 Uso del Sistema

### 1. Acceso al Módulo

Navega a: **Módulo Documentos → Gestión Correlativos**

URL directa: `/app/documentos/gestion-correlativos/`

### 2. Panel Principal

El panel muestra:

- **Estadísticas generales**: Total, Activos, Críticos, Agotados
- **Filtros avanzados**: Por sucursal, tipo de documento y estado
- **Tabla interactiva** con toda la información de correlativos

### 3. Gestión de Correlativos

#### Crear Nuevo Correlativo
1. Clic en **"Nuevo Correlativo"**
2. Seleccionar sucursal y tipo de documento
3. Definir rango (inicio y término)
4. Asignar responsable y alias
5. Guardar

#### Editar Correlativo
1. Clic en el botón **"Editar"** (ícono lápiz)
2. Modificar los campos necesarios
3. Guardar cambios

#### Renovar Correlativo
1. Clic en el botón **"Renovar"** (ícono reciclar)
2. Definir nuevo rango (sugerencias automáticas)
3. Confirmar renovación

#### Ver Historial
1. Clic en el botón **"Historial"** (ícono reloj)
2. Revisar últimos 50 documentos emitidos
3. Ver detalles de consumo

### 4. Estados y Alertas

#### Estados Automáticos:
- 🟢 **Activo**: Más de 100 números disponibles
- 🟡 **Crítico**: Entre 1 y 100 números disponibles
- 🔴 **Agotado**: 0 números disponibles

#### Indicadores Visuales:
- **Barra de progreso**: Muestra porcentaje de consumo
- **Badges de estado**: Colores según criticidad
- **Contadores**: Disponibles vs. consumidos

## 🔧 Integración con el Sistema

### 1. Consumo Automático

El sistema consume automáticamente los correlativos cuando se emiten documentos:

```python
# En views.py - Ejemplo de uso
correlativo_numero = obtener_siguiente_correlativo(sucursal, 'FACTURA ELECTRONICA')

# El correlativo se actualiza automáticamente:
# - inicio += 1
# - fecha_actualizacion = hoy
# - Se valida disponibilidad
```

### 2. Validaciones Integradas

- **Unicidad**: Un correlativo por tipo de documento por sucursal
- **Rangos válidos**: Inicio siempre menor que término
- **Disponibilidad**: Verificación antes de emitir documentos
- **Renovación automática**: Si se agota, se extiende automáticamente

### 3. APIs Disponibles

```python
# URLs disponibles:
/app/documentos/gestion-correlativos/           # Vista principal
/app/correlativos/guardar/                      # Crear/editar
/app/correlativos/obtener/<id>/                 # Obtener datos
/app/correlativos/renovar/                      # Renovar rango
/app/correlativos/historial/<id>/               # Ver historial
```

## 📊 Características Técnicas

### 1. Tecnologías Utilizadas

- **Backend**: Django con modelos mejorados
- **Frontend**: Bootstrap 5 + DataTables + SweetAlert2
- **Base de datos**: Optimizada con índices y relaciones
- **JavaScript**: Funciones AJAX para interactividad

### 2. Rendimiento

- **Consultas optimizadas** con `select_related()`
- **Paginación** en tablas grandes
- **Filtros eficientes** a nivel de base de datos
- **Carga asíncrona** de historiales

### 3. Seguridad

- **Validaciones** en frontend y backend
- **Protección CSRF** en formularios
- **Permisos** de usuario requeridos
- **Transacciones atómicas** para consistencia

## 🎨 Interfaz de Usuario

### 1. Diseño Moderno

- **Gradientes** y efectos visuales
- **Iconografía** consistente con Bootstrap Icons
- **Responsive design** para todos los dispositivos
- **Tema coherente** con el sistema RetailMind

### 2. Experiencia de Usuario

- **Navegación intuitiva** con breadcrumbs
- **Feedback visual** inmediato
- **Modales informativos** para acciones críticas
- **Tooltips** explicativos en botones

### 3. Accesibilidad

- **Etiquetas semánticas** en formularios
- **Contraste adecuado** en colores
- **Navegación por teclado** habilitada
- **Textos descriptivos** en elementos interactivos

## 🔮 Funcionalidades Futuras

### Próximas Mejoras:
- 📈 **Dashboard analítico** con gráficos de consumo
- 📧 **Notificaciones automáticas** cuando correlativos estén por agotarse
- 📋 **Exportación** de reportes en Excel/PDF
- 🔄 **Sincronización** con sistemas externos (SII)
- 📱 **App móvil** para gestión remota
- 🤖 **Predicción inteligente** de renovaciones necesarias

## 🆘 Soporte y Troubleshooting

### Problemas Comunes:

1. **Error: "Correlativo agotado"**
   - Solución: Renovar el correlativo desde la interfaz

2. **No aparecen correlativos**
   - Solución: Ejecutar `python manage.py inicializar_correlativos`

3. **Duplicados en creación**
   - Solución: El sistema previene automáticamente duplicados

### Logs y Debug:

```python
# Para debug, revisar en views.py las funciones:
- gestion_correlativos()
- guardar_correlativo()
- obtener_siguiente_correlativo()
```

---

## 📞 Contacto

Para soporte técnico o mejoras, contacta al equipo de desarrollo de RetailMind.

**¡El sistema de gestión de correlativos está listo para usar! 🎉**
