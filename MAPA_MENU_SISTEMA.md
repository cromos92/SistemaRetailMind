# Mapa de menu - Sistema RetailMind NEXO

Fecha de actualizacion: 2026-06-12

Documento operativo para revisar rutas, permisos y vistas del menu principal. El menu usa permisos calculados con `puede_ver_opcion_tag`, sensible a la sucursal activa.

## Convenciones

- `Permiso`: codigo esperado en `OpcionMenu.codigo`.
- `Ruta`: URL visible en `menu.html`. Cuando existe `{% url %}`, se deja el nombre de ruta entre llaves.
- `Estado`: `OK` si existe en el menu actual; `Auditar` si conviene revisar ruta hardcodeada, duplicidad o nombre funcional.
- Este archivo no crea permisos ni rutas. Para validar contra BD use `python manage.py auditar_menu_permisos`.

## 1. Dashboard

| # | Opcion | Permiso | Ruta | Estado |
|---|---|---|---|---|
| 1.1 | Dashboard General | `dashboard_general` | `{% url 'verHome' %}` | OK |
| 1.2 | Dashboard Ventas | `dashboard_ventas` | `{% url 'dashboard_ventas_mejorado' %}` | OK |
| 1.3 | Dashboard Compras | `dashboard_compras_estrategico` | `{% url 'verDashboardComprasMejorado' %}` | OK |
| 1.4 | Dashboard Existencias - Productos | `dashboard_productos` | `{% url 'dashboard_productos_mejorado' %}` | OK |
| 1.5 | Dashboard Existencias - FIFO / Lotes | `dashboard_fifo` | `{% url 'dashboard_fifo' %}` | OK |
| 1.6 | Dashboard Documentos | `dashboard_documentos` | `{% url 'dashboard_documentos' %}` | OK |
| 1.7 | Dashboard Despachos | `dashboard_despachos` | `{% url 'dashboard_despachos' %}` | OK |
| 1.8 | Dashboard Requerimientos | `dashboard_requerimientos` | `{% url 'dashboard_requerimientos' %}` | OK |

## 2. Ventas

| # | Opcion | Permiso | Ruta | Estado |
|---|---|---|---|---|
| 2.1 | Acceso POS Dashboard | `pos_dashboard` | `{% url 'pos_dashboard' %}` | OK |
| 2.2 | Ticket de Venta | `ticket_venta` | `{% url 'ticket_venta' %}` | OK |
| 2.3 | Cambios y Devoluciones | `cambios_devoluciones` | `{% url 'gestion_cambios_devoluciones' %}` | OK |
| 2.4 | Generar Venta | `pos_dashboard` | `{% url 'pos_dashboard' %}` | OK |
| 2.5 | Consulta Documentos | `gestion_documentos_ventas` | `{% url 'gestion_ventas_documentos' %}` | OK |
| 2.6 | Cuadratura y Arqueo | `cuadratura_caja` | `{% url 'cuadratura_caja' %}` | OK |
| 2.7 | Revision Arqueos y Depositos | `revision_arqueos` | `{% url 'revision_arqueos' %}` | OK |
| 2.8 | POS Transbank | `pos_transbank` | `{% url 'gestion_transbank_pos_sdk' %}` | OK |

## 3. Ecommerce

| # | Opcion | Permiso | Ruta | Estado |
|---|---|---|---|---|
| 3.1 | Pedidos Ecommerce | `ecommerce_pedidos_todos` | `{% url 'pedidos_ecommerce_list' %}` | OK |

## 4. Documentos

| # | Opcion | Permiso | Ruta | Estado |
|---|---|---|---|---|
| 4.1 | Emision DTE | `emision_dte` | `{% url 'emision_dte' %}` | OK |
| 4.2 | Emision por Concepto | `emision_dte` | `{% url 'emision_dte_concepto' %}` | OK |
| 4.3 | Gestion DTE | `gestion_dte` | `{% url 'gestion_dte' %}` | OK |
| 4.4 | Recepcion Documentos | `recepcion_dte` | `{% url 'recepcion_dte' %}` | OK |
| 4.5 | Gestion Cotizaciones | `gestion_cotizaciones` | `{% url 'gestion_cotizaciones' %}` | OK |
| 4.6 | Gestion Correlativos | `gestion_correlativos` | `{% url 'gestion_correlativos' %}` | OK |
| 4.7 | Gestion Creditos | `gestion_creditos` | `{% url 'gestion_creditos_documentos' %}` | OK |

## 5. Existencias

| # | Opcion | Permiso | Ruta | Estado |
|---|---|---|---|---|
| 5.1 | Acceso Gestion Producto | `gestion_producto` | `{% url 'verGestionProducto' %}` | OK |
| 5.2 | Acceso Buscar Producto Sucursal | `buscar_productos_sucursal` | `{% url 'buscar_productos_sucursal' %}` | OK |
| 5.3 | Acceso Movimientos Producto | `movimientos_producto` | `{% url 'verMovimientosProducto' %}` | OK |
| 5.4 | Gestion Producto | `gestion_producto` | `{% url 'verGestionProducto' %}` | OK |
| 5.5 | Gestion de Precios | `edicion_rapida_precios` | `{% url 'edicion_rapida_precios' %}` | OK |
| 5.6 | Alertas de Precios | `revisar_cambios_precios` | `{% url 'revisar_cambios_precios' %}` | OK |
| 5.7 | Movimientos Por Sucursal | `movimientos_producto` | `{% url 'verMovimientosProducto' %}` | OK |
| 5.8 | Gestion de Inventarios | `gestion_inventarios` | `{% url 'gestion_inventarios' %}` | OK |
| 5.9 | Impresion Etiquetas Zebra | `gestion_etiquetas_zebra` | `{% url 'gestion_etiquetas_zebra' %}` | OK |
| 5.10 | Buscar Producto Sucursal | `buscar_productos_sucursal` | `{% url 'buscar_productos_sucursal' %}` | OK |
| 5.11 | Tarjeta Movimiento Producto | `tarjeta_movimiento_producto` | `{% url 'tarjeta_movimiento_producto' %}` | OK |
| 5.12 | Despacho a Sucursales | `despacho_sucursales` | `{% url 'despacho_todas_sucursales' %}` | OK |
| 5.13 | Trazabilidad Completa | `trazabilidad_producto` | `{% url 'trazabilidad_producto' %}` | OK |
| 5.14 | Modificacion Precios y Costos | `modificacion_precios_costos` | `{% url 'modificacion_precios_costos' %}` | OK |
| 5.15 | Guias de Talla | `ver_guias_talla` | `{% url 'ver_guias_talla' %}` | OK |

## 6. Compras

| # | Opcion | Permiso | Ruta | Estado |
|---|---|---|---|---|
| 6.1 | Gestion Compras | `gestion_compras` | `{% url 'verGestionCompras' %}` | OK |
| 6.2 | Gestion Documentos Compras | `gestion_dte_compras` | `{% url 'verGestionDteCompras' %}` | OK |
| 6.3 | Prediccion de Compras | `prediccion_compras` | `{% url 'dashboard_prediccion' %}` | OK |

## 7. Requerimientos

| # | Opcion | Permiso | Ruta | Estado |
|---|---|---|---|---|
| 7.1 | Acceso Requerimientos | `lista_requerimientos` | `{% url 'modulo_requerimientos' %}` | OK |
| 7.2 | Acceso Crear Requerimiento | `crear_requerimiento` | `{% url 'crear_requerimiento_vista' %}` | OK |
| 7.3 | Requerimientos | `lista_requerimientos` | `{% url 'modulo_requerimientos' %}` | OK |
| 7.4 | Crear Requerimiento | `crear_requerimiento` | `{% url 'crear_requerimiento_vista' %}` | OK |
| 7.5 | Gestionar Requerimientos | `gestionar_requerimientos` | `{% url 'gestionar_requerimientos_vista' %}` | OK |

## 8. Reportes

### 8A. Compras

| # | Opcion | Permiso | Ruta | Estado |
|---|---|---|---|---|
| 8.1 | Despachos por Proveedor | `reporte_despachos_proveedor` | `{% url 'verReporteDespachosProveedor' %}` | OK |
| 8.2 | Reporte de Compras | `reporte_compras` | `{% url 'ver_reporte_compras' %}` | OK |
| 8.3 | Rendimiento por Proveedor | `reporte_rendimiento_proveedor` | `{% url 'ver_reporte_rendimiento_proveedor' %}` | OK |

### 8B. Ventas

| # | Opcion | Permiso | Ruta | Estado |
|---|---|---|---|---|
| 8.4 | Ventas Sucursal | `reporte_ventas_sucursal` | `{% url 'ver_reporte_ventas_sucursal' %}` | OK |
| 8.5 | Comparativo Ventas | `reporte_ventas_comparativo` | `{% url 'ver_reporte_ventas_comparativo' %}` | OK |
| 8.6 | Productos Vendidos | `reporte_productos_vendidos` | `{% url 'ver_reporte_productos_vendidos' %}` | OK |
| 8.7 | Ventas Internet | `reporte_ventas_internet` | `{% url 'ver_reporte_ventas_internet' %}` | OK |
| 8.8 | Documentos Emitidos | `reporte_documentos_emitidos` | `{% url 'ver_documentos_emitidos' %}` | OK |

### 8C. Existencias

| # | Opcion | Permiso | Ruta | Estado |
|---|---|---|---|---|
| 8.9 | Reporte de Existencias | `reporte_existencias` | `{% url 'ver_reporte_existencias' %}` | OK |
| 8.10 | Existencias por Marca | `reporte_existencias_marca` | `{% url 'ver_reporte_existencias_marca' %}` | OK |
| 8.11 | Existencias por Sucursal | `reporte_existencias_sucursal` | `{% url 'ver_reporte_existencias_sucursal' %}` | OK |
| 8.12 | Resumen Existencias | `resumen_existencias` | `{% url 'ver_resumen_existencias' %}` | OK |
| 8.13 | Inicial vs Restante | `reporte_movimientos_sucursal` | `{% url 'ver_reporte_movimientos_sucursal' %}` | OK |

## 9. Configuracion

| # | Opcion | Permiso | Ruta | Estado |
|---|---|---|---|---|
| 9.1 | Gestion Usuarios | `gestion_usuarios` | `{% url 'gestion_usuarios' %}` | OK |
| 9.2 | Gestion Sucursales | `gestion_sucursales` | `{% url 'gestion_sucursales' %}` | OK |
| 9.3 | Gestion Empresas | `gestion_empresas` | `{% url 'empresa_management:lista_empresas' %}` | OK |
| 9.4 | Gestion Clientes | `gestion_clientes` | `{% url 'empresa_management:lista_clientes' %}` | OK |
| 9.5 | Gestion Vendedores | `gestion_vendedores` | `{% url 'gestion_vendedores' %}` | OK |
| 9.6 | Gestion Permisos | `gestion_permisos` | `{% url 'gestion_permisos' %}` | OK |
| 9.7 | Interfaz Prueba Acepta | `interfaz_acepta` | `{% url 'interfaz_prueba_acepta' %}` | OK |
| 9.8 | Integraciones Ecommerce | `integraciones_ecommerce` | `{% url 'integraciones_ecommerce' %}` | OK |

## Resumen

| Modulo | Entradas |
|---|---:|
| Dashboard | 8 |
| Ventas | 8 |
| Ecommerce | 1 |
| Documentos | 7 |
| Existencias | 15 |
| Compras | 3 |
| Requerimientos | 5 |
| Reportes | 13 |
| Configuracion | 8 |
| Total bruto del menu | 68 |

El total bruto incluye accesos directos duplicados que apuntan a la misma pantalla, por ejemplo `pos_dashboard`, `gestion_producto`, `buscar_productos_sucursal`, `movimientos_producto`, `lista_requerimientos` y `crear_requerimiento`.

## Pendientes tecnicos de este mapa

- Normalizar rutas hardcodeadas a `{% url %}` por fases, priorizando pantallas criticas: DTE, compras, productos, reportes y configuracion.
- Mantener `inicializar_permisos` y `auditar_menu_permisos` sincronizados con este mapa.
- Revisar si accesos directos y entradas de submenu duplicadas deben compartir permiso o separarse por intencion.
- Ejecutar `python manage.py auditar_menu_permisos --strict` en un entorno con Django instalado y base local disponible.
