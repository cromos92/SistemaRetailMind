# 🌐 Acceso a la Interfaz Transbank POS SDK

## ✅ URLs Disponibles

### 1️⃣ **Interfaz Simplificada (RECOMENDADA)** ⭐

```
http://localhost:8000/app/pos/transbank/
```

**Características:**
- ✅ Solo campos necesarios
- ✅ Interfaz limpia y moderna
- ✅ Bootstrap integrado
- ✅ Auto-Conectar en 1 click
- ✅ Instrucciones claras
- ✅ Log en tiempo real

**Campos disponibles:**
- 🚀 Auto-Conectar (recomendado)
- 📍 Listar Puertos
- ✅ Verificar POLL
- ℹ️ Info Puerto
- 🔌 Conectar Manual / Desconectar
- 🔑 Cargar Llaves
- 💳 Procesar Venta
- 📄 Última Venta
- ❌ Anular Transacción
- 📊 Totales del Día
- 🔒 Cerrar Día

---

### 2️⃣ **Interfaz Completa**

```
http://localhost:8000/app/pos/transbank-completo/
```

**Características:**
- ✅ Todas las opciones
- ✅ Más controles
- ✅ Diseño detallado

---

## 🚀 Inicio Rápido

### 1. Inicia el servidor (si no está corriendo):

```bash
venv\Scripts\python.exe retailmind\manage.py runserver
```

### 2. Abre tu navegador en:

```
http://localhost:8000/app/pos/transbank/
```

### 3. Usa la interfaz:

1. **Click en "🚀 Auto-Conectar"**
   - Se conecta automáticamente a COM9 @ 115200
   - Verás: "✅ CONECTADO! Puerto: COM9"

2. **Click en "🔑 Cargar Llaves"**
   - Lee las instrucciones en pantalla
   - Presiona SÍ en el POS cuando pregunte
   - Espera 30-60 segundos
   - Verás el resultado (código '03' si sin GPRS es normal)

3. **Procesar una venta:**
   - Ingresa monto (ej: 1000)
   - El Ticket ID se genera automáticamente
   - Click en "💳 Procesar Venta"
   - Pasa la tarjeta en el POS
   - Verás el resultado

---

## 📊 Tu Configuración Actual

Según el test exitoso:

```
Puerto: COM9
Baudrate: 115200
Terminal: VX 520 GPRS Terminal
Commerce Code: 597029414300
Terminal ID: 75001510
```

---

## 💡 Tips de Uso

### Auto-Conectar (Recomendado)
- Es el método más fácil
- Encuentra automáticamente COM9
- Verifica con POLL
- 1 solo click

### Cargar Llaves
- **Normal:** POS pide confirmación
- **Presiona SÍ** en el POS físico
- **Espera pacientemente** 30-60 segundos
- **Código '03':** POS sin GPRS (SDK funciona bien)

### Ventas
- Monto mínimo: $50 CLP
- Pasa la tarjeta cuando el POS lo solicite
- Guarda el Operation Number para anulaciones

---

## 🎯 Flujo Recomendado

```
1. Abrir: http://localhost:8000/app/pos/transbank/
2. Click: "🚀 Auto-Conectar"
3. Click: "🔑 Cargar Llaves" (presiona SÍ en POS)
4. Hacer ventas con "💳 Procesar Venta"
5. Al final del día: "🔒 Cerrar Día"
```

---

## ✅ Listo

**URL Principal:**
# http://localhost:8000/app/pos/transbank/

**¡Abre esa URL y empieza a usar el POS!** 🚀

