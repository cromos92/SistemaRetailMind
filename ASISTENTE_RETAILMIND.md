# 🤖 Asistente Conversacional RetailMind

## 📋 Descripción

Sistema de asistente conversacional inteligente para RetailMind ERP, potenciado por **Claude (Anthropic)** con trazabilidad mediante **Langfuse**.

## 🚀 Instalación

### 1. Instalar dependencias

```bash
# Activar el entorno virtual
cd C:\DjangoProyects\retailmind\SistemaRetailMind
.\venv\Scripts\activate

# Instalar dependencias
pip install anthropic langfuse
```

### 2. Configurar variables de entorno

Agregar al archivo `.env`:

```env
# API Key de Anthropic (Claude)
# Obtener en: https://console.anthropic.com/
ANTHROPIC_API_KEY=tu_api_key_aqui

# Langfuse para tracing (opcional)
# Obtener en: https://langfuse.com/
LANGFUSE_SECRET_KEY=tu_secret_key
LANGFUSE_PUBLIC_KEY=tu_public_key
LANGFUSE_HOST=https://cloud.langfuse.com
```

### 3. Aplicar migraciones

```bash
cd retailmind
python manage.py migrate assistant
```

### 4. Reiniciar servidor

```bash
python manage.py runserver
```

## 🌐 Acceso

- **URL del chat**: http://localhost:8000/assistant/
- **Admin**: http://localhost:8000/admin/assistant/

## 📡 Endpoints de la API

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/assistant/` | GET | Vista principal del chat |
| `/assistant/api/chat/` | POST | Enviar mensaje al asistente |
| `/assistant/api/feedback/` | POST | Enviar feedback sobre respuestas |
| `/assistant/api/history/` | GET | Obtener historial de conversación |
| `/assistant/api/new/` | POST | Iniciar nueva conversación |
| `/assistant/api/stats/` | GET | Estadísticas (solo admins) |

## 💬 Ejemplo de uso de la API

### Enviar mensaje

```javascript
fetch('/assistant/api/chat/', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken')
    },
    body: JSON.stringify({
        message: '¿Cuánto vendimos hoy?',
        session_id: 'opcional'
    })
})
```

### Respuesta

```json
{
    "success": true,
    "response": "📊 **Resumen de Ventas - Hoy**\n\n...",
    "session_id": "session_123_20251217",
    "message_id": 456
}
```

## 🛠️ Herramientas (Tools) disponibles

El asistente tiene acceso a las siguientes herramientas:

### Ventas
- `get_mis_ventas()` - Ventas de la sucursal
- `get_detalle_ticket()` - Detalle de un ticket
- `get_estadisticas_ventas()` - KPIs de ventas
- `get_productos_mas_vendidos()` - Top productos

### Inventario
- `get_productos_stock_bajo()` - Alertas de stock
- `buscar_producto()` - Buscar productos
- `get_existencias_producto()` - Stock por SKU
- `get_movimientos_kardex()` - Kardex

### Compras/DTEs
- `get_dtes_pendientes()` - Facturas pendientes
- `get_dtes_vencidos()` - Facturas vencidas
- `get_detalle_dte()` - Detalle de factura
- `get_compras_temporada()` - Compras por temporada

### Caja
- `get_cuadratura_dia()` - Cuadratura diaria
- `get_arqueos_pendientes()` - Arqueos con diferencias

### Clientes/Proveedores
- `buscar_cliente()` - Buscar clientes
- `get_historial_cliente()` - Historial de compras
- `get_proveedores()` - Lista de proveedores
- `get_facturas_proveedor()` - Facturas por proveedor

### Otros
- `get_creditos_trabajadores()` - Créditos
- `get_cambios_devoluciones()` - Cambios y devoluciones
- `get_requerimientos_garantia()` - Garantías
- `get_resumen_diario()` - Dashboard del día
- `get_comparativa_ventas()` - Comparativas

## 🔒 Seguridad

- ✅ Todos los datos se filtran por empresa/sucursal del usuario
- ✅ Requiere autenticación (`@login_required`)
- ✅ Protección CSRF
- ✅ Validación de permisos
- ✅ No permite modificaciones, solo consultas

## 📂 Estructura de archivos

```
retailmind/assistant/
├── __init__.py          # Configuración de la app
├── apps.py              # AppConfig
├── models.py            # Modelos de conversación y feedback
├── views.py             # APIs y vistas
├── urls.py              # Rutas
├── tools.py             # Herramientas para Claude
├── prompts.py           # System prompt y configuración
├── agent.py             # Agente con Claude y Langfuse
├── admin.py             # Panel de administración
└── templates/
    └── assistant/
        └── chat.html    # Interfaz del chat
```

## 📊 Modelos de base de datos

- **ConversacionAsistente**: Sesiones de chat
- **MensajeAsistente**: Mensajes individuales
- **FeedbackAsistente**: Calificaciones de usuarios
- **EstadisticasAsistente**: Métricas diarias

## 🎨 Interfaz

La interfaz incluye:
- Chat moderno con burbujas de mensaje
- Indicador de "escribiendo..."
- Sugerencias rápidas (botones)
- Sistema de feedback (👍👎)
- Historial de conversación
- Modo pantalla completa
- Renderizado de Markdown

## 💡 Ejemplos de preguntas

- "¿Cuánto vendimos hoy?"
- "¿Qué productos tienen stock bajo?"
- "¿Cuáles son las facturas vencidas?"
- "Buscar zapatillas Nike talla 42"
- "¿Cómo va la cuadratura de caja?"
- "Dame un resumen del día"
- "Historial del cliente RUT 12.345.678-9"
- "Detalle del ticket 1234"

## 📈 Langfuse (Opcional)

Langfuse proporciona:
- Trazabilidad de cada conversación
- Métricas de uso
- Análisis de llamadas a tools
- Debug de respuestas
- Dashboard de costos

## 🔧 Configuración avanzada

En `settings.py`:

```python
# Límite de mensajes por sesión
ASSISTANT_MAX_MESSAGES_PER_SESSION = 50

# Tiempo de vida de sesión (horas)
ASSISTANT_SESSION_TIMEOUT_HOURS = 24
```

## ⚠️ Solución de problemas

### El asistente no responde
1. Verificar que `ANTHROPIC_API_KEY` esté configurada
2. Verificar conexión a internet
3. Revisar logs del servidor

### Error de permisos
1. Verificar que el usuario esté autenticado
2. Verificar que tenga empresa/sucursal asignada

### No aparece el botón en el menú
Agregar al menú lateral del sistema:
```html
<a href="{% url 'assistant:chat' %}">
    <i class="ri-robot-2-line"></i>
    <span>Asistente</span>
</a>
```

---

**Desarrollado para RetailMind ERP** 🛒
**Powered by Claude (Anthropic)** 🤖
