# Módulo de Ventas — Auditoría y plan (2026-07-25)

> Análisis de 6 frentes: POS y cobro, cliente/fidelización en caja, rebaja de
> stock y flujo operativo, caja y cuadratura, cambios y documentos, menú.
>
> ⚠️ Este módulo se usa **todos los días**. Los cambios aplicados hoy son
> deliberadamente conservadores; los de más riesgo quedan documentados con su
> diseño, sin ejecutar.

---

## 1. Lo bueno (para no romperlo)

El armado del ticket está **bien resuelto**: foco automático en el SKU, Modo
Bolsa (F2) que agrega con un solo Enter, validación de stock por línea y por
total del SKU, bloqueo de precio bajo el de lista, y un bloque
`@media (max-height:800px)` hecho a propósito para 1366×768. La caja tiene
atajos completos y señalizados (F4 pago rápido efectivo, F5-F9 métodos,
Numpad para denominaciones con vuelto y desglose de billetes) y borrador en
`localStorage` que sobrevive un refresh.

---

## 2. Corregido hoy

### 2.1 El canje de puntos estaba muerto en caja 🔴
Los botones y el Enter del "Vale de Puntos" usan `onclick`/`onkeydown` inline,
que se resuelven contra `window`, pero `validarVale` y `limpiarVale` viven
dentro del closure del módulo: **cada intento lanzaba `ReferenceError`**. Es
exactamente el patrón que ya mordió antes en este proyecto. Se exponen ahora en
`window`. Barrí además las otras 59 llamadas inline del template: no hay más
casos.

### 2.2 Endpoint de creación de tickets abierto a internet 🔴
`/app/api/crear-ticket/` era `@csrf_exempt` **sin autenticación**, y aceptaba
`sucursal_id` desde el body: cualquiera podía crear tickets y **quemar
correlativos fiscales**. No tiene ni un consumidor en todo el repositorio (la
caja usa `crear_ticket_pendiente_pos`), así que se le agregó `@login_required`.

> Si mañana algún dispositivo externo reclama, se revierte en 10 segundos — pero
> lo correcto sería darle autenticación propia, no volver a abrirlo.

---

## 3. P0 — Lo más grave, NO ejecutado (requiere tu decisión)

### 3.1 El cobro no es atómico
`registrar_pagos_ticket` (≈620 líneas) **no tiene `transaction.atomic` en
ninguna parte**. En ese flujo se consume stock FIFO, se crean los pagos, se
debita el vale de puntos y se emite el DTE.

**Escenario real de corrupción**: el segundo producto no tiene stock. Para
entonces ya se consumió FIFO del primero, ya se crearon los pagos y ya se debitó
el vale. El ticket vuelve a PENDIENTE — con el stock rebajado y la caja
descuadrada.

**Por qué no lo toqué a ciegas**: envolver toda la función incluiría llamadas
externas (emisión de DTE, Transbank, impresión). Eso mantendría locks de base de
datos durante peticiones HTTP lentas y, peor, podría revertir el registro de un
DTE que el SII ya aceptó.

**Diseño propuesto** (una sesión con pruebas, no a ciegas):
1. Separar la función en tres bloques: preparación → **mutación de BD** → efectos
   externos.
2. Envolver **solo el bloque de mutación** en `transaction.atomic`, con
   `select_for_update()` sobre las tallas involucradas para evitar carreras.
3. Dejar la emisión del DTE y la impresión **fuera** de la transacción, con
   reintento idempotente.
4. Agregar una clave de idempotencia por ticket para que el doble clic en Cobrar
   no cobre dos veces.

### 3.2 Otros hallazgos altos del análisis
- Si las referencias de una factura salen inválidas, `finalizarVenta` retorna sin
  rehabilitar el botón: **la venta queda trabada hasta recargar la página**.
- El botón **"Reimprimir Ticket" apunta a una URL que no existe (404)**, y la
  función de impresión térmica de boleta está sombreada por una redefinición: si
  la impresora falla, no hay reimpresión desde el POS.
- La ruta `ticket_pago_pos` renderiza un template que **no existe** en el repo.

---

## 4. Pantallas de 15 pulgadas

**El armado del ticket entra completo** en 1366×768: sin scroll horizontal, la
tabla oculta la columna Descripción bajo 1400px y la barra de totales queda fija
abajo.

**El Paso 3 de la caja no entra.** Contra ~530px útiles, la columna derecha apila
hero de saldo + 4 celdas + CTA + fidelización + vale + botón F4 + métodos
principales + ingreso manual + créditos + orden de compra + lista de pagos: se
pasa **250-400px**. En la práctica el cajero **no ve "Créditos/Especiales" ni la
lista de "Pagos Registrados"** sin hacer scroll — y esa lista es justo donde
verifica que no cobró de más.

**Agravante**: `pos-kiosk.css` está **activo por defecto** y sube la tipografía
base a 17px con targets de 48-64px, pero su propio encabezado declara que fue
afinado para **1920×1080** y en sus 734 líneas **no tiene ni una media query** de
ancho o alto. En 1366×768 se está aplicando una escala pensada para una pantalla
2,4× más grande en área.

**Propuesta**: agregar a `pos-kiosk.css` un bloque
`@media (max-height: 800px), (max-width: 1400px)` que baje la tipografía base a
15px y compacte el Paso 3 en dos columnas, dejando la lista de pagos siempre
visible.

---

## 5. Fidelización en el cobro (lo que pediste)

Hoy el cajero **no ve el tipo de cliente** al cobrar. Propuesta concreta, en
orden de valor:

1. **Chip de tipo de cliente** junto al RUT, apenas se identifica: color por tipo
   (`tipo_cliente` ya existe en el modelo de CRM).
2. **Una sola acción para identificar**: al tipear el RUT, resolver en un solo
   llamado nombre + tipo + puntos disponibles + gift cards vigentes, y mostrarlo
   en una línea sobre el resumen.
3. **Alta rápida inline** si el RUT no existe (nombre + correo), sin salir del
   cobro.
4. Con el vale de puntos ya operativo (§2.1), el ciclo queda cerrado.

---

## 6. Stock y flujo operativo

El análisis confirma el diseño: **el ticket no descuenta stock** (queda
PENDIENTE); el descuento FIFO ocurre **al cobrar**, en `registrar_pagos_ticket`.
Eso es correcto conceptualmente — el problema es la falta de atomicidad de §3.1,
que puede dejar stock rebajado sin venta.

Pendiente de verificar con datos (quedó fuera de esta pasada): comparar stock
plano contra suma de lotes FIFO en una muestra amplia, para confirmar que el
parche de junio-2026 sigue conteniendo el drift.
