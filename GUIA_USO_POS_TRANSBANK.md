# 🏪 Guía de Uso - POS Transbank Integrado

## 📋 Descripción
La integración POS Transbank permite procesar pagos con tarjetas directamente desde el sistema RetailMind usando terminales POS físicos de Transbank.

## 🔧 Requisitos Previos

### 1. Hardware Compatible
- **Verifone VX520** 
- **Ingenico 3500**
- **Ingenico DESK**

### 2. Software Requerido
- **Agente Desktop Transbank** (debe estar instalado y ejecutándose)
- **Drivers del terminal POS** específicos para cada modelo
- **Puerto serie disponible** (COM1, COM2, etc. en Windows o /dev/ttyUSB0 en Linux)

### 3. Configuración de Red
- El agente desktop debe estar ejecutándose en `localhost:8090`
- Conexión a internet para cargar el SDK de Transbank

## 🚀 Configuración Inicial

### Paso 1: Instalar Agente Desktop
1. Descargar desde: [GitHub Transbank Developers](https://github.com/TransbankDevelopers/transbank-pos-sdk-web-js)
2. Instalar y configurar para inicio automático
3. Verificar que esté ejecutándose en puerto 8090

### Paso 2: Configurar Terminal POS
1. Ir a **Módulo Ventas > Gestión POS Transbank**
2. Hacer clic en **"Nueva Configuración"**
3. Completar los datos:
   - **Nombre**: Identificador del terminal (ej: "POS Principal")
   - **Tipo POS**: Seleccionar modelo (Verifone VX520, Ingenico 3500, etc.)
   - **Puerto**: Puerto de conexión (COM1, COM2, /dev/ttyUSB0, etc.)
   - **Velocidad**: Velocidad de conexión (por defecto 115200 bps)
   - **Timeout**: Tiempo límite de conexión (por defecto 30 segundos)

### Paso 3: Probar Conexión
1. En la lista de configuraciones, hacer clic en **"Probar Conexión"**
2. El sistema verificará:
   - Conexión con agente desktop
   - Disponibilidad del puerto
   - Comunicación con terminal POS
3. Si es exitoso, el estado cambiará a **"Conectado"**

## 💳 Procesamiento de Ventas

### Opción 1: Venta Directa desde POS
1. En la interfaz POS, seleccionar configuración activa
2. Ingresar monto de la venta
3. Hacer clic en **"Procesar Venta"**
4. Seguir instrucciones en el terminal POS:
   - Insertar/pasar tarjeta
   - Ingresar PIN si es requerido
   - Confirmar monto
5. El sistema procesará automáticamente el resultado

### Opción 2: Integración con Tickets
1. Crear ticket de venta normalmente
2. En el proceso de pago, seleccionar método **"Transbank POS"**
3. El sistema iniciará automáticamente la transacción POS
4. Una vez aprobada, se registrará el pago en el ticket

## 📊 Monitoreo y Logs

### Ver Transacciones
- **Pestaña "Transacciones"**: Historial completo de transacciones
- **Filtros disponibles**: Por fecha, estado, configuración POS
- **Información mostrada**: Monto, estado, código autorización, tipo tarjeta

### Ver Logs Técnicos
- **Pestaña "Logs"**: Registro detallado de comunicación
- **Tipos de eventos**: Conexión, comandos enviados, respuestas, errores
- **Útil para**: Diagnóstico de problemas, auditoría técnica

### Anular Transacciones
- Solo disponible el mismo día de la transacción
- Hacer clic en **"Anular"** en la lista de transacciones
- Confirmar la anulación
- Se creará una nueva transacción de anulación

## ⚠️ Solución de Problemas

### Error: "Agente POS no disponible"
**Causa**: El agente desktop no está ejecutándose
**Solución**: 
1. Verificar que el agente esté instalado
2. Iniciarlo manualmente o reiniciar el servicio
3. Verificar que esté escuchando en puerto 8090

### Error: "Puerto no disponible"
**Causa**: El puerto está ocupado o no existe
**Solución**:
1. Verificar que el terminal esté conectado
2. Comprobar que el puerto sea correcto
3. Cerrar otras aplicaciones que puedan usar el puerto
4. Reiniciar el terminal POS

### Error: "Terminal no responde"
**Causa**: Problema de comunicación con el terminal
**Solución**:
1. Verificar conexión física del terminal
2. Reiniciar el terminal POS
3. Comprobar velocidad de conexión
4. Verificar drivers del terminal

### Error: "Transacción rechazada"
**Causa**: Problema con la tarjeta o transacción
**Solución**:
1. Verificar que la tarjeta esté en buen estado
2. Intentar con otra tarjeta
3. Verificar fondos disponibles
4. Contactar con Transbank si persiste

## 🔒 Seguridad y Auditoría

### Logs de Seguridad
- Todas las transacciones se registran con timestamp
- Se guarda información del usuario operador
- IP de origen de cada transacción
- Códigos de autorización para auditoría

### Datos Sensibles
- **NO se almacenan**: Números completos de tarjeta, PIN
- **SÍ se almacenan**: Últimos 4 dígitos, tipo de tarjeta, códigos de autorización
- Cumplimiento con normativas PCI DSS

## 📞 Soporte Técnico

### Información para Soporte
Al contactar soporte, proporcionar:
1. **Logs del sistema**: Disponibles en pestaña "Logs"
2. **Modelo de terminal**: Verifone VX520, Ingenico, etc.
3. **Código de error**: Si aparece alguno específico
4. **Pasos para reproducir**: Secuencia exacta del problema

### Contactos
- **Soporte Transbank**: [Sitio oficial Transbank](https://www.transbank.cl)
- **Documentación técnica**: [GitHub Developers](https://github.com/TransbankDevelopers)
- **Soporte RetailMind**: Contactar administrador del sistema

## 🔄 Mantenimiento

### Tareas Regulares
- **Limpieza de logs**: Los logs se limpian automáticamente después de 30 días
- **Verificación de conexión**: Probar conexión semanalmente
- **Actualización de drivers**: Mantener drivers del terminal actualizados

### Respaldo de Datos
- Las transacciones se respaldan con el resto de la base de datos
- Los logs técnicos se incluyen en respaldos automáticos
- Configuraciones POS se exportan con configuración del sistema

---

## ✅ Lista de Verificación Rápida

### Antes de usar por primera vez:
- [ ] Agente desktop instalado y ejecutándose
- [ ] Terminal POS conectado y encendido
- [ ] Drivers del terminal instalados
- [ ] Puerto de conexión identificado
- [ ] Configuración POS creada en el sistema
- [ ] Prueba de conexión exitosa

### Para cada venta:
- [ ] Terminal POS encendido y listo
- [ ] Configuración POS activa
- [ ] Monto correcto ingresado
- [ ] Seguir instrucciones del terminal
- [ ] Verificar código de autorización
- [ ] Entregar comprobante al cliente

---

*Última actualización: Septiembre 2025*
