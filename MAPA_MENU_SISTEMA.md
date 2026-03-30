# MAPA DE MENÚ - SISTEMA RETAILMIND (NEXO)

> Documento para rutear y revisar cada vista del sistema en orden.
> Permiso requerido entre paréntesis. Ruta URL al final de cada línea.

---

## 1. DASHBOARD

| # | Opción del Menú | Permiso | URL | Revisado |
|---|-----------------|---------|-----|----------|
| 1.1 | Dashboard General | `dashboard_general` | `/app/home/` | [ ] |
| 1.2 | Dashboard Ventas | `dashboard_ventas` | `/app/ventas/dashboard-mejorado/` | [ ] |
| 1.3 | Dashboard Compras | `dashboard_compras_estrategico` | `/app/verDashboardComprasMejorado/` | [ ] |
| 1.4 | **Dashboard Existencias** (sub-menú) | | | |
| 1.4.1 | --- Productos | `dashboard_productos` | `/app/dashboard_productos_mejorado/` | [ ] |
| 1.4.2 | --- FIFO / Lotes | `dashboard_fifo` | `/app/dashboard_fifo/` | [ ] |
| 1.5 | Dashboard Documentos | *(sin permiso)* | `/app/dashboard-documentos/` | [ ] |
| 1.6 | Dashboard Caja | *(sin permiso)* | `/app/dashboard-caja/` | [ ] |
| 1.7 | Dashboard Requerimientos | *(sin permiso)* | `/app/dashboard-requerimientos/` | [ ] |
| 1.8 | Dashboard CRM | *(sin permiso)* | `/app/dashboard-crm/` | [ ] |
| 1.9 | Dashboard Integral | *(sin permiso)* | `/app/dashboard-integral/` | [ ] |

---

## 2. MÓDULO VENTAS

| # | Opción del Menú | Permiso | URL | Revisado |
|---|-----------------|---------|-----|----------|
| 2.1 | Ticket de Venta | `ticket_venta` | `/app/ticket-venta/` | [ ] |
| 2.2 | Cambios y Devoluciones | `cambios_devoluciones` | `/app/ventas/cambios-devoluciones/` | [ ] |
| 2.3 | Generar Venta (POS Dashboard) | `pos_dashboard` | `/app/pos-dashboard/` | [ ] |
| 2.4 | Consulta Documentos | `gestion_documentos_ventas` | `/app/ventas/documentos/` | [ ] |
| 2.5 | Cuadratura y Arqueo | `cuadratura_caja` | `/app/ventas/cuadratura-caja/` | [ ] |
| 2.6 | POS Transbank (SDK Oficial) | `pos_transbank` | `/app/pos/transbank/` | [ ] |

---

## 3. MÓDULO DOCUMENTOS

| # | Opción del Menú | Permiso | URL | Revisado |
|---|-----------------|---------|-----|----------|
| 3.1 | Emisión DTE | `emision_dte` | `/app/emisionDTE/` | [ ] |
| 3.2 | Gestión DTE | `gestion_dte` | `/app/documentos/gestion-dte/` | [ ] |
| 3.3 | Recepción Documentos | `recepcion_dte` | `/app/recepcion-dte/` | [ ] |
| 3.4 | Regularizar Recepciones | `regularizar_recepciones` | `/app/regularizar-recepciones/` | [ ] |
| 3.5 | Gestión Cotizaciones | `gestion_cotizaciones` | `/app/cotizaciones/` | [ ] |
| 3.6 | Gestión Correlativos | `gestion_correlativos` | `/app/documentos/gestion-correlativos/` | [ ] |
| 3.7 | Gestión Créditos | `gestion_creditos` | `/app/documentos/gestion-creditos/` | [ ] |

---

## 4. MÓDULO EXISTENCIAS

| # | Opción del Menú | Permiso | URL | Revisado |
|---|-----------------|---------|-----|----------|
| 4.1 | Gestión Producto | `gestion_producto` | `/app/verGestionProducto/` | [ ] |
| 4.2 | Gestión de Precios | `edicion_rapida_precios` | `/app/gestion-precios/edicion-rapida/` | [ ] |
| 4.3 | Alertas de Precios | `revisar_cambios_precios` | `/app/gestion-precios/revisar-pendientes/` | [ ] |
| 4.4 | Movimientos Por Sucursal | `movimientos_producto` | `/app/verMovimientosProducto/` | [ ] |
| 4.5 | Gestión de Inventarios | `gestion_inventarios` | `/app/gestion-inventarios/` | [ ] |
| 4.6 | Impresión Etiquetas Zebra | `gestion_etiquetas_zebra` | `/app/etiquetas/` | [ ] |

---

## 5. MÓDULO COMPRAS

| # | Opción del Menú | Permiso | URL | Revisado |
|---|-----------------|---------|-----|----------|
| 5.1 | Gestión Compras | `gestion_compras` | `/app/verGestionCompras/` | [ ] |
| 5.2 | Gestión Documentos Compras | `gestion_dte_compras` | `/app/verGestionDteCompras/` | [ ] |
| 5.3 | Predicción de Compras | `prediccion_compras` | `/app/prediccion/` | [ ] |

---

## 6. MÓDULO REQUERIMIENTOS

| # | Opción del Menú | Permiso | URL | Revisado |
|---|-----------------|---------|-----|----------|
| 6.1 | Requerimientos | `lista_requerimientos` | `/app/requerimientos/` | [ ] |

---

## 7. MÓDULO REPORTES

### 7A. Reportes Compras

| # | Opción del Menú | Permiso | URL | Revisado |
|---|-----------------|---------|-----|----------|
| 7.1 | Despachos por Proveedor | `reporte_despachos_proveedor` | `/app/verReporteDespachosProveedor/` | [ ] |
| 7.2 | Reporte de Compras | `reporte_compras` | `/app/reportes/compras/` | [ ] |

### 7B. Reportes Ventas

| # | Opción del Menú | Permiso | URL | Revisado |
|---|-----------------|---------|-----|----------|
| 7.3 | Ventas Sucursal | `reporte_ventas_sucursal` | `/app/reportes/ventas-sucursal/` | [ ] |
| 7.4 | Documentos Emitidos | `reporte_documentos_emitidos` | `/app/reportes/documentos-emitidos/` | [ ] |

### 7C. Reportes Existencias

| # | Opción del Menú | Permiso | URL | Revisado |
|---|-----------------|---------|-----|----------|
| 7.5 | Reporte de Existencias | `reporte_existencias` | `/app/reportes/existencias/` | [ ] |
| 7.6 | Existencias por Marca | `reporte_existencias_marca` | `/app/reportes/existencias-marca/` | [ ] |
| 7.7 | Existencias por Sucursal | `reporte_existencias_sucursal` | `/app/reportes/existencias-sucursal/` | [ ] |
| 7.8 | Resumen Existencias | `resumen_existencias` | `/app/reportes/resumen-existencias/` | [ ] |
| 7.9 | Inicial vs Restante | `reporte_movimientos_sucursal` | `/app/reportes/movimientos-sucursal/` | [ ] |

---

## 8. CONFIGURACIÓN

| # | Opción del Menú | Permiso | URL | Revisado |
|---|-----------------|---------|-----|----------|
| 8.1 | Gestión Usuarios | `gestion_usuarios` | `/app/gestion_usuarios/` | [ ] |
| 8.2 | Gestión Sucursales | `gestion_sucursales` | `/app/gestion-sucursales/` | [ ] |
| 8.3 | Gestión Empresas | `gestion_empresas` | `/empresa_management/lista_empresas/` | [ ] |
| 8.4 | Gestión Clientes | `gestion_clientes` | `/empresa_management/lista_clientes/` | [ ] |
| 8.5 | Gestión Vendedores | `gestion_vendedores` | `/app/gestion_vendedores/` | [ ] |
| 8.6 | Gestión Permisos | `gestion_permisos` | `/app/permisos/gestion/` | [ ] |
| 8.7 | Interfaz Prueba Acepta | `interfaz_acepta` | `/app/configuracion/interfaz-prueba-acepta/` | [ ] |

---

## RESUMEN TOTAL

| Módulo | Opciones de menú |
|--------|-----------------|
| 1. Dashboard | 9 |
| 2. Ventas | 6 |
| 3. Documentos | 7 |
| 4. Existencias | 6 |
| 5. Compras | 3 |
| 6. Requerimientos | 1 |
| 7. Reportes | 9 |
| 8. Configuración | 7 |
| **TOTAL** | **48 vistas** |

---

## ORDEN SUGERIDO PARA RUTEAR

```
FASE 1 - CONFIGURACIÓN BASE (para que todo lo demás funcione)
  8.3  Gestión Empresas
  8.2  Gestión Sucursales
  8.1  Gestión Usuarios
  8.6  Gestión Permisos
  8.5  Gestión Vendedores
  8.4  Gestión Clientes

FASE 2 - EXISTENCIAS (catálogo de productos)
  4.1  Gestión Producto
  4.4  Movimientos Por Sucursal
  4.2  Gestión de Precios
  4.3  Alertas de Precios
  4.5  Gestión de Inventarios
  4.6  Impresión Etiquetas Zebra

FASE 3 - COMPRAS (ingreso de mercadería)
  5.1  Gestión Compras
  5.2  Gestión Documentos Compras
  5.3  Predicción de Compras

FASE 4 - DOCUMENTOS (emisión y recepción)
  3.6  Gestión Correlativos
  3.1  Emisión DTE
  3.2  Gestión DTE
  3.3  Recepción Documentos
  3.4  Regularizar Recepciones
  3.5  Gestión Cotizaciones
  3.7  Gestión Créditos

FASE 5 - VENTAS (operación diaria)
  2.1  Ticket de Venta
  2.3  Generar Venta (POS)
  2.6  POS Transbank
  2.4  Consulta Documentos
  2.2  Cambios y Devoluciones
  2.5  Cuadratura y Arqueo

FASE 6 - REPORTES (análisis)
  7.1  Despachos por Proveedor
  7.2  Reporte de Compras
  7.3  Ventas Sucursal
  7.4  Documentos Emitidos
  7.5  Reporte de Existencias
  7.6  Existencias por Marca
  7.7  Existencias por Sucursal
  7.8  Resumen Existencias
  7.9  Inicial vs Restante

FASE 7 - REQUERIMIENTOS
  6.1  Requerimientos

FASE 8 - DASHBOARDS (visualización)
  1.1  Dashboard General
  1.2  Dashboard Ventas
  1.3  Dashboard Compras
  1.4  Dashboard Existencias (Productos + FIFO)
  1.5  Dashboard Documentos
  1.6  Dashboard Caja
  1.7  Dashboard Requerimientos
  1.8  Dashboard CRM
  1.9  Dashboard Integral

FASE 9 - EXTRA
  8.7  Interfaz Prueba Acepta
```
