# ERP SistemaRetailMind — Auditoría integral y trabajo ejecutado
## 25 de julio de 2026

> Se revisó **el sistema completo, módulo por módulo**, con verificación contra la
> **base de datos de producción** (solo lectura). Documentos por módulo:
>
> | Módulo | Plan | Anexo con fichas |
> |---|---|---|
> | Reportes (34) | [PLAN_REPORTES](PLAN_REPORTES_2026-07-25.md) | [anexo](ANEXO_REPORTES_2026-07-25.md) |
> | Dashboards (17) | [PLAN_DASHBOARDS](PLAN_DASHBOARDS_2026-07-25.md) | [anexo](ANEXO_DASHBOARDS_2026-07-25.md) |
> | Ventas | [PLAN_VENTAS](PLAN_VENTAS_2026-07-25.md) | — |
> | Existencias | [PLAN_EXISTENCIAS](PLAN_EXISTENCIAS_2026-07-25.md) | — |
> | Visión general | [AUDITORIA_ERP_COMPLETA](AUDITORIA_ERP_COMPLETA_2026-07-25.md) | — |
> | 🔴 Seguridad | [SEGURIDAD_URGENTE](SEGURIDAD_URGENTE_2026-07-25.md) | — |

---

## 1. Las 5 cosas que hay que atender primero

### 🔴 1. Credenciales de producción publicadas en GitHub
El repositorio es **público** y `retailmind/.env` está **versionado**: contraseña
del Postgres de producción, la `SECRET_KEY` que firma los JWT de la app móvil, el
token de correo compartido por los tres sistemas y las API keys del canal con
AllConnected. **Verificado a mano.** Plan de rotación en
[SEGURIDAD_URGENTE](SEGURIDAD_URGENTE_2026-07-25.md).

### 🔴 2. Cada despacho a tienda crea stock sin costo FIFO
Desde el 17-abr-2026, **5.911 recepciones de traspaso (25.060 unidades) no
crearon un solo lote FIFO**. Hoy hay **~6.943 unidades vendibles sin costo**
(NICK2: 4.478). El margen real en tiendas es inauditable, y contamina el Plan de
Liquidación y el aging de capital. *No lo arreglé a ciegas: es el flujo de
escritura de stock y necesita pruebas.* Diseño en
[PLAN_EXISTENCIAS](PLAN_EXISTENCIAS_2026-07-25.md) §2.

### 🔴 3. El cobro del POS no es atómico
`registrar_pagos_ticket` (≈620 líneas) **no tiene `transaction.atomic`**. Si falla
a mitad, queda stock consumido, pagos creados y vale debitado con el ticket de
vuelta en PENDIENTE. Encaja con un hecho medido: **151 tickets pagados de 2026,
por $5.657.487, no tienen ningún movimiento de stock**. Probablemente la misma
causa raíz. Diseño en [PLAN_VENTAS](PLAN_VENTAS_2026-07-25.md) §3.1.

### 🔴 4. El menú cuesta 616 consultas SQL en cada página
Medido: 69 verificaciones de permiso (276 queries) + 10 contadores (340 queries)
**por cada página del ERP**. Es el mayor problema de rendimiento del sistema.
Se arregla con una caché por request del árbol de permisos.

### 🔴 5. En NICK1 nadie puede regularizar recepciones — ni el administrador
La pantalla de permisos por sucursal **nunca escribe `puede_aprobar`** (no tiene
ni el checkbox). Las 49 filas de `PermisoSucursal` están en `puede_aprobar=False`,
y ~14 vistas exigen ese permiso. Es el mismo patrón del incidente de enero con
compras (esas 2 filas **siguen** deshabilitadas).

---

## 2. Ejecutado y verificado en esta sesión

### Reportes — dejaron de mentir con plata
| Qué | Antes | Ahora |
|---|---|---|
| Ventas Global | Netas = brutas; NC nunca restadas | **$7.383.449** restados en junio |
| Compras: "ROI" | **19,0%** = la tasa de IVA | Markup teórico **123%** real |
| Compras: scoping | Mezclaba todo el holding | Por `EmpresaUser` |
| Compras: NC | **Sumaban** a la inversión | 55 NC / **$17,2M** ahora restan |
| Compras: deuda | Perdía las facturas con abono parcial | Saldo real **$232,5M** (antes $251,6M) |
| Compras: "cumplimiento" | Medía **pagos** | Mide **entregas** |
| Compras: recepciones | `int(unidades * 0.5)` inventado | Dato real |
| Compras: filtros | Período y Temporada **no hacían nada** | Implementados |
| Docs por Vendedor | Doble conteo POS + boleta | Universo único |
| Productos Vendidos | Neto y bruto mezclados | Criterio único |
| Existencias por Marca | Fuga entre empresas + totales truncados | 403 + aviso |
| Diagnóstico compras | Error siempre | OK — destapó 1.077 DTE sin productos |

### Rendimiento
| Reporte | Antes | Ahora |
|---|---|---|
| FIFO general | 447 q / 193 s; en la sucursal grande **la BD cortaba la conexión → 500** | **1 query / 3,5 s** |
| Compras Integral | 2.727 q / **20,6 min** | **28-30 q / 5,5-20 s** |

### Seguridad y permisos
- **58 rutas de API** de reportes no pasaban por el middleware de permisos.
  Verificado sobre las 829 URLs del proyecto: 58 cambios, **cero regresiones**.
- Kardex, trazabilidad, despacho masivo y precios ahora validan la empresa del
  SKU. El peor caso era "aplicar a todas las sucursales" en precios: propagaba
  cambios a empresas ajenas.
- **`/app/emitir_dte/`** (mueve stock y quema folio del SII) solo tenía
  `@login_required`: ahora exige el permiso de emisión.
- **`/app/api/crear-ticket/`** era `@csrf_exempt` **sin autenticación**: cualquiera
  podía quemar correlativos fiscales. Cerrado.

### Bugs de operación
- **Emitir un DTE con cantidad negativa INFLABA el stock** del origen (la
  validación `disponible < negativo` es falsa). Ahora se rechaza, y también las
  tallas repetidas en el detalle.
- **El canje de puntos estaba muerto en caja**: `validarVale`/`limpiarVale` no
  estaban expuestas en `window` y cada intento lanzaba `ReferenceError`.
- **El botón "Dashboard Compras" del home devolvía JSON crudo.**
- **Dos alertas del home llevaban a 404** — las dos más accionables.
- **Los tickets de cambio se contaban como venta**: $551.300 de más en junio y el
  ticket promedio subestimado en $1.287.
- La **timeline de trazabilidad se ordenaba por texto** `dd/mm/YYYY`, o sea por
  día del mes: 30/01/2024 aparecía antes que 05/12/2026.
- El KPI de correlativos contaba como agotado uno al que le quedaba 1 folio.
- El wizard de emisión mostraba un **paso 3 que no existe** y no respondía.
- **425 líneas de código muerto** eliminadas de `views.py`.

**Verificación**: `manage.py check` limpio · templates compilan · suite de
regresión de reportes **94 PASS / 3 FAIL** (los 3 FAIL son de datos conocidos:
`atributo4` sin poblar y 1,3% del catálogo sin recategorizar). Mejoró desde
90 PASS / 8 WARN.

---

## 3. Lo que queda, por prioridad

**P0 — integridad de datos** (requieren pruebas, no cambios a ciegas)
1. Lotes FIFO en recepción de traspaso + backfill de las 6.943 unidades.
2. Atomicidad del cobro + clave de idempotencia por ticket.
3. Investigar los 151 tickets sin movimiento ($5,6M) y los 3.787 movimientos de
   venta sin ticket ni DTE.
4. Las **1.052 unidades despachadas y nunca recibidas** no se ven en ninguna
   pantalla.
5. **21% de los SKUs** tienen kardex que no cuadra con el stock (delta global
   84.968 u).

**P1 — seguridad y control**
6. CRUD de vendedores, empresas y clientes **sin control de permisos**: un
   vendedor puede cambiarse su comisión por POST directo.
7. **Auditoría en cero**: `LogAcceso` con 0 filas, `fecha_ultimo_acceso` NULL en
   los 25 usuarios, ninguna traza de quién cambió un permiso o una contraseña.
8. **Desactivar un usuario no le impide entrar** (`es_activo` vs `is_active`).
9. Emisión sin `select_for_update`: dos emisiones simultáneas del mismo SKU
   pueden dejar stock negativo.

**P2 — rendimiento**
10. Las 616 queries del menú.
11. `rendimiento-proveedor`: 91 s y atribuye ventas por **coincidencia de texto**.
12. Dashboard de existencias materializa el catálogo completo en memoria.

**P3 — UX y limpieza**
13. En compras, los **totales de la grilla están inflados** (dos `Sum()` sobre
    relaciones distintas): la compra #14 muestra 4.998 unidades vs 4.854 reales.
    La paginación no tiene `ORDER BY`.
14. El **Paso 3 de la caja no entra en 15"**: la lista de "Pagos Registrados"
    queda bajo el pliegue, y `pos-kiosk.css` está activo por defecto pero fue
    afinado para 1920×1080 y **no tiene ni una media query**.
15. En emisión, el botón "Emitir Documento" queda bajo el pliegue en 1366×768.
16. Falta ver el **stock de la sucursal destino** al armar un traspaso.
17. **"DTEs en Limbo" existe y no está en el menú** (cero enlaces en el proyecto).
18. Eliminar los 5 reportes y 4 dashboards redundantes ya identificados.
19. Mostrar el **tipo de cliente** en el cobro (lo pediste explícitamente).

---

## 4. Nota metodológica

Los cambios se hicieron con **agentes en paralelo repartidos por archivo** para
evitar colisiones. Aun así, dos agentes tocaron `views_modulo_reportes.py` a la
vez; el incidente se detectó, el archivo se reconstruyó y se verificó que
conviven las tres tandas de cambios. **Ese archivo (+1.246/−877) conviene
revisarlo con calma antes de commitear.**

Nada se commiteó: todo está en el working tree.
