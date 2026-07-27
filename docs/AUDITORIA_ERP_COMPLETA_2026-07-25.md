# Auditoría integral del ERP — 25 de julio de 2026

> Revisión módulo por módulo del sistema completo, con verificación contra la
> **base de datos de producción** (solo lectura) y arreglos aplicados.
>
> Documentos relacionados:
> - [PLAN_REPORTES_2026-07-25.md](PLAN_REPORTES_2026-07-25.md) + [anexo](ANEXO_REPORTES_2026-07-25.md) (34 reportes)
> - [PLAN_DASHBOARDS_2026-07-25.md](PLAN_DASHBOARDS_2026-07-25.md) + [anexo](ANEXO_DASHBOARDS_2026-07-25.md) (17 dashboards)
> - [SEGURIDAD_URGENTE_2026-07-25.md](SEGURIDAD_URGENTE_2026-07-25.md) 🔴 credenciales expuestas

---

## 1. Qué se arregló y está verificado

Todos los cambios se probaron invocando las vistas reales contra producción y
contrastando con oráculos independientes. `manage.py check` limpio.

### 1.1 Reportes que mentían con plata

| Reporte | Antes | Ahora |
|---|---|---|
| **Ventas Global** | "Ventas netas" = brutas: las NC no se restaban nunca | Devoluciones **$7.383.449** en junio; netas $170,5M — cuadra con el oráculo |
| **Compras Integral** | "ROI Estimado **19,0%**" = la tasa de IVA; "Margen $131,7M" = el IVA soportado | **Markup teórico 123%** desde el precio sugerido de la OC |
| **Compras Integral** | Mezclaba las compras de **todo el holding** | Scoping por `EmpresaUser`: 517 docs / $644,8M para EDEL vs 519 del holding |
| **Compras Integral** | NC de proveedor **sumaban** a la inversión | 55 NC por **$17,2M** ahora restan; 9 descartados y 1 rechazado fuera |
| **Compras Integral** | Deuda leía `estado_pago` → las facturas con abono parcial **desaparecían** | Saldo real: $232,5M (antes $251,6M). Aparecen los estados `Abonado` ($17,7M) que nadie contaba |
| **Compras Integral** | "Cumplimiento del proveedor" medía **pagos**, no entregas | Unidades recibidas / pedidas. SUCCES CHILE 85,1% verificado |
| **Compras Integral** | Recepciones parciales con `int(unidades * 0.5)  # Estimación` | Dato real de `Productos_Recepcionados` |
| **Compras Integral** | Filtros *Período* y *Temporada* **no hacían nada** | Implementados de verdad |
| **Documentos por Vendedor** | Doble conteo: sumaba tickets POS **y** sus propias boletas | Universo único compartido con el agregado que explica |
| **Productos Vendidos** | Mezclaba facturas en **neto** con boletas en **bruto** | Criterio único con IVA |
| **Existencias por Marca** | Se podía ver stock de **otra empresa** pasando su id | **403** |
| **Existencias por Marca** | El límite recortaba columnas: totales falsos sin avisar | Límite por artículo + aviso de truncamiento |
| **Diagnóstico de compras** | Consultaba campos inexistentes → error siempre | HTTP 200. Destapó **1.077 DTE de compra sin productos** |

### 1.2 Rendimiento: dos reportes pasaron de inservibles a usables

| Reporte | Antes | Ahora |
|---|---|---|
| **FIFO general** | 447 queries / 193 s para 111 filas; en la sucursal grande ~34.000 queries y **la BD cortaba la conexión → 500** | **1 query / 3,5 s** para 8.519 filas (x149 en queries, x50 en tiempo) |
| **Compras Integral** | 2.727 queries / **20,6 minutos** | **28-30 queries / 5,5-20,7 s** |

### 1.3 Seguridad: fugas entre empresas cerradas

- **58 rutas** de API de reportes no pasaban por el middleware de permisos
  (cualquier rol autenticado leía montos y comisiones pidiendo el JSON directo).
  Ahora resuelven al mismo permiso que su página. Verificado sobre las 829 URLs
  del proyecto: 58 cambios, todos de "sin permiso" a un código válido, **cero
  regresiones**.
- Las rutas de los reportes de existencias en el mapa de permisos estaban
  **obsoletas** (apuntaban a URLs que ya no existen), así que el permiso no se
  aplicaba. Corregidas.
- **`api_tarjeta_movimiento`**, trazabilidad, autocomplete, despacho masivo y los
  endpoints de precios ahora validan la empresa del SKU. Probado con un usuario
  real: pedir un SKU de otra empresa pasó de devolver kardex y costos a **403**.
  El caso más grave era "aplicar a todas las sucursales" en precios: propagaba
  cambios a fichas de empresas ajenas.
- **FIFO general** no validaba sucursal: leía cualquiera desde la sesión.

### 1.4 Dashboards

- El **botón "Dashboard Compras" del home devolvía JSON crudo** en el navegador.
- **Dos alertas del home llevaban a 404** — justo las dos más accionables.
- Los **tickets de cambio/devolución se contaban como venta**: $551.300 de más en
  junio y el ticket promedio subestimado en $1.287 ($40.053 → $41.340 real).
- Rutas duplicadas (`/ventas/dashboard/`, `/dashboard_compras_estrategico/`)
  convertidas en redirecciones a la versión viva.
- **425 líneas de código muerto** eliminadas de `views.py`.

---

## 2. Estado del ERP por módulo

| Módulo | Revisado | Veredicto |
|---|---|---|
| Reportes | 34 reportes | 9 útiles · 15 mejorables · 5 rotos · 5 redundantes |
| Dashboards | 17 | 3 útiles · 8 mejorables · 2 rotos · 4 redundantes |
| Compras | 6 frentes | UX y filtros con problemas serios (ver §3) |
| Ventas | 6 frentes | *análisis en curso* |
| Existencias | 5 frentes | *análisis en curso* |
| Documentos | 5 frentes | *análisis en curso* |
| Administración, configuración, CRM, ecommerce | 5 grupos | *análisis en curso* |

---

## 3. Hallazgos de Compras (auditoría UX completa)

Lo más grave encontrado en el listado de órdenes:

1. **Los totales de la grilla están inflados.** La consulta combina dos `Sum()`
   sobre relaciones distintas en un solo `annotate()`: cuando una talla tiene más
   de una recepción, las unidades y el costo se multiplican. Verificado contra
   producción: la compra #14 muestra **4.998 unidades vs 4.854 reales**. Eso
   corrompe la barra de avance, el badge "Completado" y el filtro de estado.
2. **La paginación no tiene `ORDER BY`** (el modelo `Compras` no define
   `Meta.ordering`): el orden es arbitrario y hay filas que pueden repetirse o
   perderse entre páginas.
3. **Media docena de endpoints sin `@login_required`**, dos de ellos escriben.
4. **Las exportaciones ignoran los filtros** de pantalla y **no excluyen las
   compras ELIMINADA** (hay 19 en producción).
5. Un `id` HTML duplicado deja el contador de resultados congelado en "0".
6. Crear una orden completa cuesta **12-14 clics** en 2 pantallas y 3 modales, y
   al terminar no sugiere el paso siguiente.

---

## 4. Lo que falta para ser un ERP profesional

De la revisión transversal, lo que más se repite:

- **Una sola definición de "venta".** Hoy el home, el dashboard de ventas y el
  reporte responden distinto a la misma pregunta. Debe existir un servicio único
  de métricas de venta que todos consuman.
- **Scoping por empresa uniforme.** Se cerraron los agujeros encontrados, pero el
  patrón correcto (resolver contra `EmpresaUser`, nunca confiar en la sesión)
  debería aplicarse por defecto, no vista por vista.
- **`Ticket.fecha` es `auto_now`** y sigue usándose para filtrar en varias
  pantallas: las ventas se mueven de día solas. Debe usarse `created_at`.
- **Filtros que prometen y no cumplen.** Aparecen en compras, en el dashboard de
  ventas y en reportes. Un filtro visible sin efecto es peor que no tenerlo.
- **Auditoría de cambios**: falta bitácora de quién cambió qué en configuración,
  precios y permisos.

---

## 5. Verificación

```powershell
cd retailmind
python manage.py check                                    # limpio
python _test_reportes_readonly.py --confirmo-prod --rapido  # 90 PASS / 3 FAIL (los 3 son de datos)
```

Nota sobre la suite: los 3 FAIL son de **datos**, no de código
(`atributo4` está 0% poblado y hay 1,3% del catálogo sin recategorizar).

---

## 6. Advertencia sobre el trabajo concurrente

Dos agentes editaron `views_modulo_reportes.py` en paralelo. El incidente se
detectó y el archivo se reconstruyó; se verificó después que conviven las tres
tandas de cambios (compras, ventas y los míos) y que la suite sigue pasando.
**Antes de commitear conviene revisar el diff de ese archivo con calma**
(+1.246 / −877 líneas).
