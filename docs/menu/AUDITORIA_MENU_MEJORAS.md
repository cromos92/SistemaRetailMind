# Auditoria integral del menu RetailMind

Fecha: 2026-06-12  
Fuente principal: `retailmind/app/templates/layout/menu.html`  
Alcance: frontend, backend, permisos, rutas, entrega de informacion y viabilidad por opcion visible del menu.

## Resumen ejecutivo

El menu actual contiene 65 entradas crudas extraidas del HTML. Al quitar duplicados condicionales, como enlaces directos que se muestran cuando el usuario tiene una sola opcion disponible, quedan cerca de 60 opciones funcionales. El mapa historico `MAPA_MENU_SISTEMA.md` esta desactualizado: no incluye ecommerce, revision de arqueos/depositos, varios reportes, busqueda por sucursal, tarjeta de movimiento, despacho a sucursales, trazabilidad, guias de talla e integraciones ecommerce.

Los mayores problemas transversales son:

1. Templates excesivamente grandes con CSS/JS inline: `verGestionProductos.html` supera 15k lineas, `generacionVentas.html` 12k, `gestionCompras.html` 9.9k, `gestion_cambios_devoluciones.html` 9.2k, `gestionDteCompras.html` 6.6k, `recepcion_dte.html` 6.2k y `emisionDTE.html` 6.1k.
2. Mezcla de rutas hardcodeadas (`/app/...`) y `{% url %}` en menu y templates. Esto dificulta renombrar rutas, auditar permisos y detectar enlaces rotos.
3. El filtro `user|puede_ver_opcion` no usa `request.session`, por diseno declarado en `permisos_tags.py`; por lo tanto el menu puede mostrar opciones que una sucursal bloquea y el usuario termina en bloqueo backend.
4. `Guias de Talla` aparece en el menu sin wrapper de permiso propio. Deberia tener `puede_ver_opcion:'ver_guias_talla'` o un codigo de permiso consistente.
5. `Ventas Internet` esta visualmente colgada de `reporte_productos_vendidos`; conviene permiso propio (`reporte_ventas_internet`) para gobierno y auditoria.
6. Hay muchos `print()` en backend de operacion critica, especialmente ventas, compras, reportes, usuarios y empresas. Deben migrarse a `logger`.
7. La informacion se entrega de forma muy dispar entre modulos: algunos tienen KPIs, filtros, estados vacios y exportacion; otros son pantallas cortas sin contexto ni resumen.
8. El sistema ya tiene patrones NEXO y modulos (`module-header`, `kpi-card`, `pagination-controls`), pero no se aplican de forma uniforme.

## Prioridad global sugerida

| Prioridad | Mejora | Impacto | Viabilidad |
|---|---|---:|---:|
| P0 | Corregir permisos visibles del menu: `Guias de Talla`, `Ventas Internet`, y menu sensible a sucursal | Alto | Media |
| P0 | Reemplazar prints de flujos criticos POS/DTE/stock por logger | Alto | Alta |
| P1 | Normalizar rutas hardcodeadas a `{% url %}` en menu y templates principales | Alto | Media |
| P1 | Dividir JS inline de templates gigantes por modulo | Alto | Media |
| P1 | Unificar estados vacios, carga, errores y exportacion en reportes y dashboards | Alto | Alta |
| P1 | Agregar resumen operativo por pantalla: ultima actualizacion, filtros activos, sucursal/empresa y totales | Alto | Alta |
| P2 | Crear componentes reutilizables para filtros, paginacion, KPIs y tablas | Medio | Media |
| P2 | Revisar performance de consultas con listados amplios y endpoints JSON | Medio | Media |
| P3 | Actualizar `MAPA_MENU_SISTEMA.md` desde el menu real | Medio | Alta |

## Hallazgos transversales tecnicos

- `retailmind/app/views.py` sigue siendo monolitico con mas de 32k lineas y 314 funciones. Hay logica aun duplicada o reexportada en archivos por modulo.
- `views_modulo_ventas.py` tiene casi 18k lineas, 632 usos de `JsonResponse` y cerca de 196 `print()`. Es el mayor riesgo operativo por mezclar POS, pagos, stock, DTE, cambios, cuadratura y dashboards.
- `views_modulo_reportes.py` tiene mas de 9k lineas y muchos endpoints agregadores; requiere estandarizar filtros, permisos y exportaciones.
- `views_modulo_gestion_precios.py`, `views_modulo_compras.py` y `views_resumen_existencias.py` tambien tienen `print()` o depuracion en flujos que deberian usar logger.
- Hay rutas nuevas en menu que no estan en el mapa historico; si gestion de permisos depende de `OpcionMenu`, conviene tener seed/migracion controlada o comando de auditoria no destructivo para detectar desalineaciones.

## Mejoras por opcion de menu

Leyenda: esfuerzo S/M/L, riesgo Bajo/Medio/Alto, prioridad P0-P3.

### Dashboard

| Opcion | Mejora recomendada | Viabilidad | Prioridad |
|---|---|---|---|
| Dashboard General | Mostrar empresa/sucursal activa, rango de fechas visible, hora de ultima actualizacion y acceso rapido a pendientes operativos: DTE, stock critico, arqueos abiertos. Unificar `dashboard_home.html`/`dashboard_general.html` si ambos siguen vivos. | M / Medio | P1 |
| Dashboard Ventas | Priorizar indicadores accionables: ventas hoy vs meta, tickets pendientes de pago/DTE, diferencias de caja y ventas por canal. Agregar estado vacio para sucursales sin ventas y exportacion del resumen. | M / Medio | P1 |
| Dashboard Compras | Convertir KPIs en decisiones: facturas pendientes, recepciones incompletas, proveedores con atraso, compras por temporada. Mantener drill-down consistente hacia Gestion Compras y DTE Compras. | M / Medio | P1 |
| Dashboard Existencias - Productos | Resumir stock critico, productos sin precio, productos sin guia de talla y articulos excluidos de analitica. Usar los mismos filtros de existencias/reportes. | M / Medio | P1 |
| Dashboard Existencias - FIFO / Lotes | Explicar alertas FIFO con accion directa: lotes antiguos, costo promedio, stock negativo o sin lote. Agregar exportacion y fecha de calculo. | M / Medio | P2 |
| Dashboard Documentos | Separar emitidos, recibidos, rechazados y pendientes Acepta/SII. Debe enlazar a Gestion DTE o Recepcion con filtros preaplicados. | S / Bajo | P1 |
| Dashboard Despachos | Mostrar cola por estado: pendiente, en transito, recibido, rechazado. Debe permitir filtrar por origen/destino y saltar a trazabilidad. | M / Medio | P2 |
| Dashboard Requerimientos | Incluir SLA, antiguedad, tipo, responsable y pendientes proveedor. Sin esto es solo tablero descriptivo. | S / Bajo | P2 |

### Ventas

| Opcion | Mejora recomendada | Viabilidad | Prioridad |
|---|---|---|---|
| Ticket de Venta | Reducir incertidumbre: mostrar correlativo disponible, sucursal activa, tipo DTE posible y estado de impresora/QZ antes de vender. Reemplazar prints de debug en backend por logger. | M / Medio | P0 |
| Cambios y Devoluciones | Convertir el flujo largo en pasos visibles: buscar documento, validar ventana/reglas, seleccionar productos, autorizar, devolver/generar NC. Agregar resumen de impacto en stock y caja antes de confirmar. | L / Alto | P1 |
| Generar Venta | Es pantalla POS critica. Mejorar foco teclado/lector, estados offline/online, mensajes de stock insuficiente y confirmacion de pagos. Separar JS inline de `generacionVentas.html`. | L / Alto | P0 |
| Consulta Documentos | Enfatizar trazabilidad de DTE/ticket: estado pago, estado DTE, vendedor, sucursal, acciones permitidas y razon de acciones deshabilitadas. | M / Medio | P1 |
| Cuadratura y Arqueo | Guiar cierre paso a paso: ventas teoricas, medios de pago, depositos, diferencias, aprobacion/reapertura. Marcar datos recalculados vs guardados. | M / Medio | P0 |
| Revision Arqueos y Depositos | Agregar cola priorizada: diferencias altas, depositos sin comprobante, reaperturas, vencidos. Permitir filtros por riesgo y exportacion. | M / Medio | P1 |
| POS Transbank | Mostrar estado del dispositivo, puerto, ultimo ACK, ultima transaccion, modo SDK/simple y recuperacion ante falla. Unificar mensajes con `pos-transbank.css`. | M / Medio | P0 |

### Ecommerce

| Opcion | Mejora recomendada | Viabilidad | Prioridad |
|---|---|---|---|
| Pedidos | Hacer visible el ciclo completo: recibido, SKU matcheado, sucursal sugerida, facturado, error de stock, cancelado. Agregar filtros por subestado y acciones masivas con prevalidacion. | M / Medio | P1 |

### Documentos

| Opcion | Mejora recomendada | Viabilidad | Prioridad |
|---|---|---|---|
| Emision DTE | `emisionDTE.html` es muy grande. Separar JS por responsabilidades: receptor, productos, correlativo, validaciones, TXT Acepta. Mostrar prechequeo de correlativo/certificado/sucursal antes de emitir. | L / Alto | P0 |
| Emision por Concepto | Estandarizar con Emision DTE: preview de documento, impuestos, receptor, forma de pago y errores accionables. | M / Medio | P1 |
| Gestion DTE | Fortalecer tabla: filtros por estado SII/Acepta, tipo, sucursal, vendedor y diferencias. Acciones deshabilitadas deben explicar permiso o estado que las bloquea. | M / Medio | P1 |
| Recepcion Documentos | El flujo es complejo. Separar pendientes, historial, rechazados y regularizacion con contadores visibles. Mostrar diferencias por producto y decision sugerida. | L / Alto | P0 |
| Gestion Cotizaciones | Integrar embudo: borrador, enviada, aceptada, facturada, vencida. Mostrar conversion a venta/DTE y bloquear acciones segun estado. | M / Medio | P1 |
| Gestion Correlativos | Agregar semaforo por tipo DTE/sucursal: disponible, bajo, vencido, sin CAF. Alertas anticipadas y exportacion son prioritarias. | S / Bajo | P0 |
| Gestion Creditos | Simplificar datos de deuda: cupo, saldo, mora, historial de pagos y riesgo. Separar cliente/trabajador si ambos conviven historicamente. | M / Medio | P1 |

### Existencias

| Opcion | Mejora recomendada | Viabilidad | Prioridad |
|---|---|---|---|
| Gestion Producto | `verGestionProductos.html` es el mayor template. Dividir JS/CSS, crear subflujos: crear producto, editar variacion, precios, stock, guias de talla, exclusion analitica. Agregar resumen de calidad de catalogo. | L / Alto | P0 |
| Gestion de Precios | Mostrar impacto antes de aplicar: productos afectados, margen anterior/nuevo, stock con alerta y sucursales sincronizadas. Agregar bitacora visible. | M / Medio | P1 |
| Alertas de Precios | Convertir en bandeja de aprobacion: origen del cambio, impacto, usuario, sucursal, fecha y accion masiva. | M / Medio | P1 |
| Movimientos Por Sucursal | Mejorar lectura: saldo inicial, entradas, salidas, ajustes, traspasos y saldo final por periodo. Enlazar a tarjeta de movimiento. | M / Medio | P1 |
| Gestion de Inventarios | Buen candidato a wizard: configurar, congelar corte, conteo, diferencias, aprobacion, aplicar ajustes. Mostrar progreso y errores de importacion de pistola. | M / Medio | P1 |
| Impresion Etiquetas Zebra | Mostrar previsualizacion de etiqueta, origen del precio/codigo, historial de impresiones y estado de impresora. | M / Medio | P2 |
| Buscar Producto Sucursal | Orientarla a consulta rapida: stock por sucursal, precio, ubicacion, ultima venta, ultimo ingreso y accion para traspaso. | S / Bajo | P1 |
| Tarjeta Movimiento Producto | Agregar selector de SKU con autocompletado, exportacion clara y explicacion de saldos. Debe ser el detalle oficial desde reportes y movimientos. | S / Bajo | P1 |
| Despacho a Sucursales | Hacer visible origen/destino, stock disponible, documento generado, recepcion esperada y estado. Integrar con DTE/recepcion. | M / Medio | P1 |
| Trazabilidad Completa | Mostrar timeline unico por SKU: compra, recepcion, traspaso, venta, devolucion, ajuste. Debe permitir copiar folios y abrir documentos. | M / Medio | P1 |
| Modificacion Precios y Costos | Alto riesgo: exigir previsualizacion, permisos explicitos, motivo obligatorio y bitacora. Mostrar impacto en margen y precio sugerido. | M / Alto | P0 |
| Guias de Talla | Envolver en permiso de menu. Mostrar marcas sin guia, productos afectados y validacion de tallas antes de crear productos. Reutilizar clase NEXO en vez de estilos propios aislados. | S / Medio | P0 |

### Compras

| Opcion | Mejora recomendada | Viabilidad | Prioridad |
|---|---|---|---|
| Gestion Compras | `gestionCompras.html` combina demasiados flujos. Separar compra, recepcion, proveedor, tallas, margenes y vinculacion retroactiva. Resaltar pendientes por recepcionar y errores de SKU. | L / Alto | P0 |
| Gestion Documentos Compras | Mejorar bandeja de DTE: pendiente pago, pagado parcial, NC asociadas, incidencias, recepcion asociada. Unificar filtros con reportes de compras. | L / Alto | P1 |
| Prediccion de Compras | Mostrar confianza, metodo, fecha de calculo y datos usados. Convertir sugerencias a acciones: crear orden, exportar, descartar, recalcular. | M / Medio | P1 |

### Requerimientos

| Opcion | Mejora recomendada | Viabilidad | Prioridad |
|---|---|---|---|
| Requerimientos | Mejorar vista de lista con SLA, responsable, tipo, cliente, ticket asociado y proximo paso. Filtros por prioridad y vencimiento. | S / Bajo | P1 |
| Crear Requerimiento | Mantener formulario por pasos: cliente/ticket/producto, tipo, evidencia, proveedor, confirmacion. Validar RUT y adjuntos con mensajes claros. | S / Bajo | P1 |
| Gestionar Requerimientos | Bandeja de gestion con acciones masivas, respuesta proveedor, estados y trazabilidad. Debe diferenciar operador vs supervisor. | M / Medio | P2 |

### Reportes

| Opcion | Mejora recomendada | Viabilidad | Prioridad |
|---|---|---|---|
| Despachos por Proveedor | Agregar filtros persistentes, totales por proveedor y variacion vs periodo anterior. Exportacion debe incluir filtros aplicados. | S / Bajo | P2 |
| Reporte de Compras | Mostrar compras, recepciones y pagos por separado para evitar mezclar conceptos. Agregar desglose por proveedor, temporada y sucursal. | M / Medio | P1 |
| Rendimiento por Proveedor | Hacerlo accionable: cumplimiento, atraso, tasa de incidencia, margen y productos con mejor rotacion. | M / Medio | P2 |
| Ventas Sucursal | Estandarizar periodo, comparativo y canal. Incluir ticket promedio, unidades, margen si disponible y devoluciones. | M / Medio | P1 |
| Comparativo Ventas | Mostrar comparacion temporal clara: actual vs anterior, variacion absoluta y porcentual, top cambios. | S / Bajo | P2 |
| Productos Vendidos | Incluir margen, rotacion, devoluciones y stock restante. Permitir saltar a tarjeta/trazabilidad. | M / Medio | P1 |
| Ventas Internet | Crear permiso propio. Separar ecommerce real vs metodo de pago internet; el backend ya distingue `ECOMMERCE` y `VENTA_INTERNET`, la UI debe explicarlo. | S / Medio | P0 |
| Documentos Emitidos | Agregar estado tributario, descarga, TXT, errores y correlativo. Filtros por tipo, sucursal y receptor. | S / Bajo | P1 |
| Reporte de Existencias | Unificar con resumen/tarjeta: costo, precio, valorizacion, stock negativo, sin movimiento. | M / Medio | P1 |
| Existencias por Marca | Agregar ranking de marcas sin guia de talla, marcas con sobrestock y margen promedio. | S / Bajo | P2 |
| Existencias por Sucursal | Mostrar diferencias entre sucursales y sugerencias de traspaso. | M / Medio | P1 |
| Resumen Existencias | Debe ser tablero de decisiones: valorizacion, quiebres, sobrestock, excluidos de analitica, historico disponible. | M / Medio | P1 |
| Inicial vs Restante | Renombrar a algo mas claro, por ejemplo `Stock inicial vs restante`. Mostrar formula y rango usado. | S / Bajo | P2 |

### Configuracion

| Opcion | Mejora recomendada | Viabilidad | Prioridad |
|---|---|---|---|
| Gestion Usuarios | Mostrar ultima sesion, sucursal activa, asignaciones, estado 2FA/PIN y acciones de seguridad. Reemplazar prints por logger. | M / Medio | P1 |
| Gestion Sucursales | Agregar tablero de configuracion faltante: impresora, Acepta, POS Transbank, tipo sucursal, permisos y estado operativo. | M / Medio | P1 |
| Gestion Empresas | Evitar debug con `print()` en `empresa_management/views.py`. Agregar validacion RUT visible, contactos, sucursales y estado de integracion. | M / Medio | P1 |
| Gestion Clientes | Mejorar ficha con deuda, ultimas compras, documentos, credito, ecommerce y requerimientos. | M / Medio | P2 |
| Gestion Vendedores | Mostrar comision, sucursales asignadas, ventas del periodo y estado. Integrar con reporte de comisiones si aplica. | S / Bajo | P2 |
| Gestion Permisos | Alta prioridad funcional: detectar opciones de menu sin `OpcionMenu`, permisos sin ruta, rutas sin menu y permisos por sucursal que el menu no refleja. | M / Medio | P0 |
| Interfaz Prueba Acepta | Clarificar que es entorno de prueba, no operacion real. Mostrar entrada, salida, errores, trazas y payload sanitizado. | S / Bajo | P2 |
| Integraciones Ecommerce | Mostrar estado de credenciales, ultima sincronizacion, errores recientes, mappings SKU pendientes y accion de prueba controlada. | S / Bajo | P1 |

## Backlog recomendado por fases

### Fase 1: control y seguridad operativa

- Agregar permiso visible para `Guias de Talla`.
- Crear o corregir permiso `reporte_ventas_internet`.
- Auditar que cada entrada del menu exista en `OpcionMenu` y tenga ruta real.
- Sustituir `print()` por `logger = logging.getLogger('app')`, `users` o `empresa_management`.
- Mostrar siempre empresa/sucursal activa en POS, DTE, compras, stock y reportes.

### Fase 2: consistencia de informacion

- Estandarizar encabezados de modulo con titulo, estado, ultima actualizacion y acciones principales.
- Estandarizar filtros activos, estados vacios, loading y mensajes de error.
- Todas las exportaciones deben incluir filtros aplicados y fecha de generacion.
- Agregar enlaces de drill-down entre dashboard, reporte, gestion y trazabilidad.

### Fase 3: reduccion de complejidad frontend

- Extraer JS inline de templates gigantes a archivos en `app/static/js/` por modulo.
- Mantener jQuery/vanilla; no introducir frameworks.
- Reusar clases existentes de NEXO/Velzon y evitar paletas por pantalla.
- Normalizar rutas hardcodeadas a `{% url %}` donde sea posible.

### Fase 4: rendimiento y mantenibilidad backend

- Separar mas logica de `views.py` y `views_modulo_ventas.py` hacia services por dominio.
- Revisar queries de reportes y listados con `select_related`, `prefetch_related`, paginacion y filtros indexables.
- Crear tests focalizados para permisos de menu, reportes criticos, POS/DTE y stock.

## Criterios de aceptacion para futuras mejoras

- Cada opcion del menu tiene permiso propio, ruta nombrada y template identificado.
- Si una sucursal bloquea una opcion, el menu no debe mostrarla o debe explicar el bloqueo antes de navegar.
- Toda pantalla operativa muestra empresa/sucursal activa, fecha/rango y estado de datos.
- Toda accion destructiva o irreversible tiene previsualizacion, motivo y confirmacion.
- Toda tabla relevante tiene busqueda/filtros, estado vacio, paginacion o limite explicito, y exportacion cuando aporta valor.
- Ningun flujo productivo nuevo usa `print()`; todo va por logger configurado.
- Las pantallas nuevas o refactorizadas respetan includes, FBV, jQuery/vanilla y design system existente.
