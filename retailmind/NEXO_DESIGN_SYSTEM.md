# 🎨 NEXO - Design System & Paleta de Colores

## Resumen Rápido

```
Primario:     #0066FF (Azul eléctrico)
Secundario:   #1A1A2E (Azul oscuro/casi negro)
Accent:       #00D4AA (Verde menta)
Éxito:        #00D4AA
Warning:      #FFB020
Error:        #FF4D4D
Fondo:        #F5F5F7
Blanco:       #FFFFFF
```

---

## 🎯 Colores Principales

### Primarios
| Nombre | Hex | RGB | Uso |
|--------|-----|-----|-----|
| Primary | `#0066FF` | rgb(0, 102, 255) | Botones principales, links, CTAs |
| Primary Dark | `#0052CC` | rgb(0, 82, 204) | Hover en botones primarios |
| Primary Light | `#E6F0FF` | rgb(230, 240, 255) | Backgrounds sutiles, badges |

### Secundarios
| Nombre | Hex | RGB | Uso |
|--------|-----|-----|-----|
| Secondary | `#1A1A2E` | rgb(26, 26, 46) | Sidebar, headers, textos principales |
| Accent | `#00D4AA` | rgb(0, 212, 170) | Destacados, éxito, elementos activos |

---

## ✅ Colores Semánticos

| Estado | Hex | RGB | Uso |
|--------|-----|-----|-----|
| Success | `#00D4AA` | rgb(0, 212, 170) | Confirmaciones, stock OK, completado |
| Warning | `#FFB020` | rgb(255, 176, 32) | Alertas, stock bajo, pendientes |
| Error | `#FF4D4D` | rgb(255, 77, 77) | Errores, sin stock, crítico |
| Info | `#0066FF` | rgb(0, 102, 255) | Información, tips, ayuda |

---

## ⚪ Colores Neutrales (Grises)

| Nombre | Hex | RGB | Uso |
|--------|-----|-----|-----|
| Gray 900 | `#1A1A2E` | rgb(26, 26, 46) | Textos principales |
| Gray 700 | `#4A4A5A` | rgb(74, 74, 90) | Textos secundarios |
| Gray 500 | `#8A8A9A` | rgb(138, 138, 154) | Placeholders, disabled, hints |
| Gray 300 | `#D1D1D9` | rgb(209, 209, 217) | Bordes, divisores |
| Gray 100 | `#F5F5F7` | rgb(245, 245, 247) | Fondos, cards, áreas |
| White | `#FFFFFF` | rgb(255, 255, 255) | Fondo principal |

---

## 📦 CSS Variables

```css
:root {
  /* ========== COLORES PRIMARIOS ========== */
  --nexo-primary: #0066FF;
  --nexo-primary-dark: #0052CC;
  --nexo-primary-light: #E6F0FF;
  
  /* ========== COLORES SECUNDARIOS ========== */
  --nexo-secondary: #1A1A2E;
  --nexo-accent: #00D4AA;
  
  /* ========== COLORES SEMÁNTICOS ========== */
  --nexo-success: #00D4AA;
  --nexo-warning: #FFB020;
  --nexo-error: #FF4D4D;
  --nexo-info: #0066FF;
  
  /* ========== NEUTRALES / GRISES ========== */
  --nexo-gray-900: #1A1A2E;
  --nexo-gray-700: #4A4A5A;
  --nexo-gray-500: #8A8A9A;
  --nexo-gray-300: #D1D1D9;
  --nexo-gray-100: #F5F5F7;
  --nexo-white: #FFFFFF;
  
  /* ========== FONDOS ========== */
  --nexo-bg-primary: #FFFFFF;
  --nexo-bg-secondary: #F5F5F7;
  --nexo-bg-dark: #1A1A2E;
  
  /* ========== TEXTOS ========== */
  --nexo-text-primary: #1A1A2E;
  --nexo-text-secondary: #4A4A5A;
  --nexo-text-muted: #8A8A9A;
  --nexo-text-inverse: #FFFFFF;
  
  /* ========== BORDES ========== */
  --nexo-border-light: #D1D1D9;
  --nexo-border-dark: #4A4A5A;
  
  /* ========== SOMBRAS ========== */
  --nexo-shadow-sm: 0 1px 2px rgba(26, 26, 46, 0.05);
  --nexo-shadow-md: 0 4px 6px rgba(26, 26, 46, 0.1);
  --nexo-shadow-lg: 0 10px 15px rgba(26, 26, 46, 0.1);
  --nexo-shadow-xl: 0 20px 25px rgba(26, 26, 46, 0.15);
  
  /* ========== BORDES REDONDEADOS ========== */
  --nexo-radius-sm: 4px;
  --nexo-radius-md: 8px;
  --nexo-radius-lg: 12px;
  --nexo-radius-xl: 16px;
  --nexo-radius-full: 9999px;
  
  /* ========== TRANSICIONES ========== */
  --nexo-transition-fast: 150ms ease;
  --nexo-transition-normal: 250ms ease;
  --nexo-transition-slow: 350ms ease;
}
```

---

## 🎨 Tailwind Config

```javascript
// tailwind.config.js
module.exports = {
  theme: {
    extend: {
      colors: {
        nexo: {
          primary: {
            DEFAULT: '#0066FF',
            dark: '#0052CC',
            light: '#E6F0FF',
          },
          secondary: '#1A1A2E',
          accent: '#00D4AA',
          success: '#00D4AA',
          warning: '#FFB020',
          error: '#FF4D4D',
          info: '#0066FF',
          gray: {
            900: '#1A1A2E',
            700: '#4A4A5A',
            500: '#8A8A9A',
            300: '#D1D1D9',
            100: '#F5F5F7',
          },
        },
      },
      boxShadow: {
        'nexo-sm': '0 1px 2px rgba(26, 26, 46, 0.05)',
        'nexo-md': '0 4px 6px rgba(26, 26, 46, 0.1)',
        'nexo-lg': '0 10px 15px rgba(26, 26, 46, 0.1)',
        'nexo-xl': '0 20px 25px rgba(26, 26, 46, 0.15)',
      },
    },
  },
}
```

---

## 🐍 Python/Django Constants

```python
# nexo/constants/colors.py

class NexoColors:
    """Paleta de colores NEXO"""
    
    # Primarios
    PRIMARY = "#0066FF"
    PRIMARY_DARK = "#0052CC"
    PRIMARY_LIGHT = "#E6F0FF"
    
    # Secundarios
    SECONDARY = "#1A1A2E"
    ACCENT = "#00D4AA"
    
    # Semánticos
    SUCCESS = "#00D4AA"
    WARNING = "#FFB020"
    ERROR = "#FF4D4D"
    INFO = "#0066FF"
    
    # Grises
    GRAY_900 = "#1A1A2E"
    GRAY_700 = "#4A4A5A"
    GRAY_500 = "#8A8A9A"
    GRAY_300 = "#D1D1D9"
    GRAY_100 = "#F5F5F7"
    WHITE = "#FFFFFF"
    
    # Mapeo para estados
    STATUS_COLORS = {
        'success': SUCCESS,
        'warning': WARNING,
        'error': ERROR,
        'info': INFO,
        'pending': WARNING,
        'active': SUCCESS,
        'inactive': GRAY_500,
        'critical': ERROR,
    }
    
    # Mapeo para stock
    STOCK_COLORS = {
        'ok': SUCCESS,        # > 30 unidades
        'low': WARNING,       # 10-30 unidades
        'critical': ERROR,    # < 10 unidades
        'out': GRAY_500,      # 0 unidades
    }
```

---

## 🖌️ SCSS Variables

```scss
// _variables.scss

// Primarios
$nexo-primary: #0066FF;
$nexo-primary-dark: #0052CC;
$nexo-primary-light: #E6F0FF;

// Secundarios
$nexo-secondary: #1A1A2E;
$nexo-accent: #00D4AA;

// Semánticos
$nexo-success: #00D4AA;
$nexo-warning: #FFB020;
$nexo-error: #FF4D4D;
$nexo-info: #0066FF;

// Grises
$nexo-gray-900: #1A1A2E;
$nexo-gray-700: #4A4A5A;
$nexo-gray-500: #8A8A9A;
$nexo-gray-300: #D1D1D9;
$nexo-gray-100: #F5F5F7;
$nexo-white: #FFFFFF;

// Mapa de colores para loops
$nexo-colors: (
  "primary": $nexo-primary,
  "secondary": $nexo-secondary,
  "accent": $nexo-accent,
  "success": $nexo-success,
  "warning": $nexo-warning,
  "error": $nexo-error,
  "info": $nexo-info,
);

// Sombras
$nexo-shadow-sm: 0 1px 2px rgba($nexo-secondary, 0.05);
$nexo-shadow-md: 0 4px 6px rgba($nexo-secondary, 0.1);
$nexo-shadow-lg: 0 10px 15px rgba($nexo-secondary, 0.1);
```

---

## 🎯 Uso en Componentes

### Botones

```css
/* Botón Primario */
.btn-primary {
  background-color: var(--nexo-primary);
  color: var(--nexo-white);
  border: none;
  border-radius: var(--nexo-radius-md);
  padding: 10px 20px;
  transition: var(--nexo-transition-fast);
}

.btn-primary:hover {
  background-color: var(--nexo-primary-dark);
}

/* Botón Secundario */
.btn-secondary {
  background-color: var(--nexo-secondary);
  color: var(--nexo-white);
}

/* Botón Success */
.btn-success {
  background-color: var(--nexo-success);
  color: var(--nexo-white);
}

/* Botón Outline */
.btn-outline {
  background-color: transparent;
  color: var(--nexo-primary);
  border: 2px solid var(--nexo-primary);
}

.btn-outline:hover {
  background-color: var(--nexo-primary);
  color: var(--nexo-white);
}
```

### Cards

```css
.card {
  background-color: var(--nexo-white);
  border: 1px solid var(--nexo-gray-300);
  border-radius: var(--nexo-radius-lg);
  box-shadow: var(--nexo-shadow-sm);
  padding: 20px;
}

.card:hover {
  box-shadow: var(--nexo-shadow-md);
}
```

### Alertas

```css
.alert {
  padding: 12px 16px;
  border-radius: var(--nexo-radius-md);
  border-left: 4px solid;
}

.alert-success {
  background-color: rgba(0, 212, 170, 0.1);
  border-color: var(--nexo-success);
  color: var(--nexo-success);
}

.alert-warning {
  background-color: rgba(255, 176, 32, 0.1);
  border-color: var(--nexo-warning);
  color: #996B00;
}

.alert-error {
  background-color: rgba(255, 77, 77, 0.1);
  border-color: var(--nexo-error);
  color: var(--nexo-error);
}

.alert-info {
  background-color: rgba(0, 102, 255, 0.1);
  border-color: var(--nexo-info);
  color: var(--nexo-info);
}
```

### Badges/Tags de Estado

```css
.badge {
  display: inline-block;
  padding: 4px 8px;
  border-radius: var(--nexo-radius-full);
  font-size: 12px;
  font-weight: 600;
}

.badge-success {
  background-color: var(--nexo-success);
  color: var(--nexo-white);
}

.badge-warning {
  background-color: var(--nexo-warning);
  color: var(--nexo-secondary);
}

.badge-error {
  background-color: var(--nexo-error);
  color: var(--nexo-white);
}

.badge-info {
  background-color: var(--nexo-info);
  color: var(--nexo-white);
}
```

### Sidebar

```css
.sidebar {
  background-color: var(--nexo-secondary);
  color: var(--nexo-white);
  width: 260px;
  height: 100vh;
}

.sidebar-item {
  padding: 12px 20px;
  color: var(--nexo-gray-300);
  transition: var(--nexo-transition-fast);
}

.sidebar-item:hover {
  background-color: rgba(255, 255, 255, 0.1);
  color: var(--nexo-white);
}

.sidebar-item.active {
  background-color: var(--nexo-primary);
  color: var(--nexo-white);
}
```

---

## 📊 Estados de Stock (Colores)

```css
/* Stock OK (>30 unidades) */
.stock-ok { color: var(--nexo-success); }
.stock-ok-bg { background-color: rgba(0, 212, 170, 0.1); }

/* Stock Bajo (10-30 unidades) */
.stock-low { color: var(--nexo-warning); }
.stock-low-bg { background-color: rgba(255, 176, 32, 0.1); }

/* Stock Crítico (<10 unidades) */
.stock-critical { color: var(--nexo-error); }
.stock-critical-bg { background-color: rgba(255, 77, 77, 0.1); }

/* Sin Stock (0 unidades) */
.stock-out { color: var(--nexo-gray-500); }
.stock-out-bg { background-color: var(--nexo-gray-100); }
```

---

## 🌙 Modo Oscuro (Opcional)

```css
[data-theme="dark"] {
  --nexo-bg-primary: #0D1117;
  --nexo-bg-secondary: #161B22;
  --nexo-text-primary: #FFFFFF;
  --nexo-text-secondary: #8B949E;
  --nexo-border-light: #30363D;
  
  /* Los colores primarios se mantienen */
  --nexo-primary: #0066FF;
  --nexo-accent: #00D4AA;
  --nexo-success: #00D4AA;
  --nexo-warning: #FFB020;
  --nexo-error: #FF4D4D;
}
```

---

## ✅ Resumen Visual

```
┌─────────────────────────────────────────────────────────┐
│                    NEXO COLOR PALETTE                    │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  PRIMARIOS                                               │
│  ■ #0066FF  Primary (Azul eléctrico)                    │
│  ■ #0052CC  Primary Dark                                 │
│  ■ #E6F0FF  Primary Light                                │
│                                                          │
│  SECUNDARIOS                                             │
│  ■ #1A1A2E  Secondary (Azul oscuro)                     │
│  ■ #00D4AA  Accent (Verde menta)                        │
│                                                          │
│  SEMÁNTICOS                                              │
│  ■ #00D4AA  Success                                      │
│  ■ #FFB020  Warning                                      │
│  ■ #FF4D4D  Error                                        │
│  ■ #0066FF  Info                                         │
│                                                          │
│  GRISES                                                  │
│  ■ #1A1A2E  Gray 900                                     │
│  ■ #4A4A5A  Gray 700                                     │
│  ■ #8A8A9A  Gray 500                                     │
│  ■ #D1D1D9  Gray 300                                     │
│  ■ #F5F5F7  Gray 100                                     │
│  ■ #FFFFFF  White                                        │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

*NEXO Design System v1.0*
