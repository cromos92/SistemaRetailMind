# Documentación — SistemaRetailMind

Documentación técnica organizada por dominio. Para las reglas del proyecto y el
stack, ver [`CLAUDE.md`](../CLAUDE.md) en la raíz.

## 🛒 Ecommerce / AllConnected
Integración con los ecommerces externos vía **AllConnected** (`paola.cl`, `realsport.cl`).

- [Credenciales y conexión](ecommerce-allconnected/CREDENCIALES_ECOMMERCE_ALLCONNECTED.md)
- [Análisis de integración RetailMind ↔ AllConnected ↔ Realsport](ecommerce-allconnected/ANALISIS_INTEGRACION_RETAILMIND_ALLCONNECTED_REALSPORT.md)
- [Integración de ventas](ecommerce-allconnected/INTEGRACION_ALLCONNECTED_VENTAS.md)
- [Endpoint de pedidos](ecommerce-allconnected/ENDPOINT_ALLCONNECTED_PEDIDOS.md)
- [API de ventas por internet](ecommerce-allconnected/API_VENTAS_INTERNET_INTEGRACION.md)
- [Integración (general)](ecommerce-allconnected/integracion-allconnected.md)
- [Match de fotos Realsport](ecommerce-allconnected/MATCH_FOTOS_REALSPORT_RETAILMIND.md)
- [Integración de fechas](ecommerce-allconnected/INTEGRACION_FECHAS_ALLCONECTED.md)

## 🎁 Fidelización / App móvil
- [Contrato de la app Flutter](fidelizacion/APP_FLUTTER_FIDELIZACION.md)
- [Análisis de compra desde la app](fidelizacion/ANALISIS_COMPRA_APP.md)
- [Consumo de la aplicación](fidelizacion/CONSUMO_APLICACION.md)

## 🚀 Despliegue
- [DigitalOcean](despliegue/DESPLIEGUE_DIGITALOCEAN.md)
- [Railway](despliegue/RAILWAY_DEPLOYMENT.md)

## 💳 POS / Transbank
- [Protocolo ACK (Transbank)](pos-transbank/PROTOCOLO_ACK_TRANSBANK.md)
- [Protocolo ACK (completo)](pos-transbank/PROTOCOLO_ACK_COMPLETO.md)

## 🧾 Ventas
- [Integración del módulo de ventas](ventas/INTEGRACION_MODULO_VENTAS.md)
- [Descuento de stock por sucursal](ventas/STOCK_DESCUENTO_POR_SUCURSAL.md)

## 📟 POS táctil
- [Diagnóstico POS touch](pos-tactil/DIAGNOSTICO_POS_TOUCH.md)

## 🧭 Menú / Navegación
- [Mapa del menú del sistema](menu/MAPA_MENU_SISTEMA.md)
- [Auditoría y mejoras del menú](menu/AUDITORIA_MENU_MEJORAS.md)

## 🔍 Auditorías
- [Trazabilidad y dashboards](auditorias/AUDITORIA_TRAZABILIDAD_Y_DASHBOARDS.md)

---

## Docs que viven fuera de `docs/` (por convención / referenciados por CLAUDE.md)

- [`CLAUDE.md`](../CLAUDE.md), [`AGENTS.md`](../AGENTS.md) — instrucciones para agentes
- [`retailmind/NEXO_DESIGN_SYSTEM.md`](../retailmind/NEXO_DESIGN_SYSTEM.md) — design system NEXO
- [`retailmind/app/templates/vistas/ESTILOS_MODULOS.md`](../retailmind/app/templates/vistas/ESTILOS_MODULOS.md) — estructura de módulos
- `retailmind/app/management/commands/*.md` — seguridad y guías de comandos (junto al código)
- `retailmind/empresa_management/README.md` — README de la app
- `scripts/pos-kiosk/README.md`, `scripts/CHECKLIST_POS_TACTIL.md` — operación POS kiosk
