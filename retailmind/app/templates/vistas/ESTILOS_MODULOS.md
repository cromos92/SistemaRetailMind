# 🎨 Guía de Estilos - Módulos RetailMind

## Paleta de Colores Principal

```css
/* Colores principales del gradiente header */
--rm-primary: #405189;        /* Azul corporativo */
--rm-accent: #0ab39c;         /* Verde/Teal */

/* Estados */
--rm-success: #0ab39c;
--rm-warning: #ffbe0b;
--rm-danger: #f06548;
--rm-info: #299cdb;

/* Fondos de KPIs */
--rm-kpi-yellow: linear-gradient(135deg, #fff8e1 0%, #ffecb3 100%);
--rm-kpi-red: linear-gradient(135deg, #ffebee 0%, #ffcdd2 100%);
--rm-kpi-orange: linear-gradient(135deg, #fff3e0 0%, #ffe0b2 100%);
--rm-kpi-green: linear-gradient(135deg, #e8f5e9 0%, #c8e6c9 100%);
--rm-kpi-blue: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);

/* Neutros */
--rm-white: #ffffff;
--rm-gray-light: #f8f9fa;
--rm-gray: #e9ecef;
--rm-text: #495057;
```

## Estructura HTML Base de Módulo

```html
<div class="page-content">
    <div class="container-fluid">
        <div class="card shadow">
            <!-- Header con gradiente -->
            <div class="module-header">
                <div class="module-header-left">
                    <div class="module-header-icon">
                        <i class="bi bi-ICONO"></i>
                    </div>
                    <div class="module-header-info">
                        <h5 style="color: white;">Título del Módulo</h5>
                        <div class="module-header-status">
                            <span class="status-dot"></span>
                            Sistema Activo
                        </div>
                    </div>
                </div>
                <div class="module-header-actions">
                    <!-- Botones de acción -->
                </div>
            </div>
            
            <div class="card-body">
                <!-- KPIs (opcional) -->
                <div class="row mb-3">
                    <!-- Panel de KPIs -->
                </div>
                
                <!-- Controles de búsqueda/paginación -->
                <div class="pagination-controls">
                    <!-- Controles -->
                </div>
                
                <!-- Tabla de datos -->
                <div class="table-responsive">
                    <table class="table table-hover">
                        <!-- Contenido -->
                    </table>
                </div>
            </div>
        </div>
    </div>
</div>
```

## CSS Base Requerido

```css
/* ==================== ESTILOS BASE MÓDULOS ==================== */

/* Contenedor principal */
.page-content {
    background: #ffffff !important;
    min-height: calc(100vh - 70px);
}

/* Card principal */
.card.shadow {
    border: none;
    border-radius: 16px;
    overflow: hidden;
    box-shadow: 0 4px 20px rgba(64, 81, 137, 0.1);
    animation: fadeInCard 0.4s ease;
}

@keyframes fadeInCard {
    from { opacity: 0; transform: translateY(20px); }
    to { opacity: 1; transform: translateY(0); }
}

/* Header con gradiente */
.module-header {
    background: linear-gradient(135deg, #405189 0%, #0ab39c 100%);
    color: white;
    padding: 16px 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
}

.module-header-left {
    display: flex;
    align-items: center;
    gap: 14px;
}

.module-header-icon {
    width: 48px;
    height: 48px;
    background: rgba(255,255,255,0.2);
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 24px;
}

.module-header-info h5 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
    color: white;
}

.module-header-status {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    opacity: 0.9;
    margin-top: 4px;
}

.status-dot {
    width: 8px;
    height: 8px;
    background: #4ade80;
    border-radius: 50%;
    animation: pulse 2s infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

.module-header-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
}

.module-header-actions .btn {
    border-radius: 10px;
    font-weight: 500;
    padding: 8px 14px;
    font-size: 13px;
    border: none;
    transition: all 0.2s ease;
}

.module-header-actions .btn:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.2);
}

/* Card body */
.card-body {
    padding: 24px;
    background: white;
}

/* Controles de paginación */
.pagination-controls {
    background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 16px;
    border: 1px solid rgba(64, 81, 137, 0.1);
    box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}

.page-info {
    min-width: 140px;
    text-align: center;
    font-weight: 600;
    background: white;
    border-radius: 8px;
}

/* KPI Cards */
.kpi-card {
    border-radius: 12px;
    border: none;
    transition: all 0.3s ease;
    overflow: hidden;
}

.kpi-card:hover {
    transform: translateY(-4px);
    box-shadow: 0 8px 25px rgba(0,0,0,0.15);
}

.kpi-card .card-body {
    padding: 12px 16px;
}

/* Quick filter buttons */
.quick-filter-btn {
    background: white;
    border: 1px solid rgba(64, 81, 137, 0.2);
    border-radius: 20px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 500;
    color: #405189;
    transition: all 0.2s ease;
    cursor: pointer;
}

.quick-filter-btn:hover {
    background: #405189;
    color: white;
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(64, 81, 137, 0.3);
}

/* Responsive */
@media (max-width: 768px) {
    .pagination-controls .row>div {
        margin-bottom: 10px;
    }
    
    .pagination-controls .btn-group {
        width: 100%;
    }
    
    .module-header {
        padding: 12px 16px;
    }
    
    .module-header-icon {
        width: 40px;
        height: 40px;
        font-size: 20px;
    }
}
```

## Ejemplo de KPI Card

```html
<div class="col-md-3 col-6">
    <div class="kpi-card card h-100" style="background: linear-gradient(135deg, #fff8e1 0%, #ffecb3 100%); border-left: 4px solid #ffbe0b;">
        <div class="card-body text-center py-2">
            <div style="width: 36px; height: 36px; background: linear-gradient(135deg, #ffbe0b 0%, #f0a500 100%); border-radius: 10px; margin: 0 auto 6px; display: flex; align-items: center; justify-content: center;">
                <i class="bi bi-file-earmark-text fs-5 text-white"></i>
            </div>
            <h4 class="mb-0 fw-bold" style="color: #405189;">0</h4>
            <p class="text-muted mb-0 small fw-medium" style="font-size: 0.75rem;">Título KPI</p>
            <h6 class="mb-0 mt-1 fw-bold" style="color: #f06548; font-size: 0.85rem;">$0</h6>
        </div>
    </div>
</div>
```

## Colores por Tipo de KPI

| Tipo | Fondo | Borde | Icono BG |
|------|-------|-------|----------|
| Pendiente/Total | #fff8e1 → #ffecb3 | #ffbe0b | #ffbe0b → #f0a500 |
| Vencido/Error | #ffebee → #ffcdd2 | #f06548 | #f06548 → #d9534f |
| Por Vencer/Warning | #fff3e0 → #ffe0b2 | #fb8c00 | #fb8c00 → #f57c00 |
| OK/Success | #e8f5e9 → #c8e6c9 | #0ab39c | #0ab39c → #099885 |
| Info | #e3f2fd → #bbdefb | #299cdb | #299cdb → #1976d2 |

## Módulos que usan este estilo

- [x] Gestión DTE Compras (`gestionDteCompras.html`)
- [x] Gestión Compras (`gestionCompras.html`)
- [ ] Otros módulos pendientes...

---
*Última actualización: Diciembre 2024*

