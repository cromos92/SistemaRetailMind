# POS Kiosk · Setup Windows 10 LTSC

Scripts para configurar un Celeron J1900 con Windows 10 Enterprise LTSC como
terminal POS kiosko apuntando al Django remoto.

## Contenido

| Archivo | Qué hace |
|---|---|
| `chrome-kiosk.ps1` | Lanza Chrome en modo kiosko contra la URL configurada. |
| `instalar-acceso-directo.ps1` | Crea el `.lnk` en el escritorio y en el menú inicio del usuario POS. |
| `instalar-autoinicio.ps1` | Agrega el kiosko al arranque de Windows del usuario POS. |
| `politicas-windows.reg` | Bloquea atajos peligrosos (Ctrl+Alt+Del, Alt+Tab, F11, tecla Windows). |
| `watchdog.ps1` | Mantiene Chrome vivo: si el proceso cae, lo relanza. |
| `instalar-watchdog-tarea.ps1` | Registra `watchdog.ps1` como Tarea Programada de Windows. |
| `config.example.ps1` | Plantilla de configuración (URL, rutas, usuario POS). |

## Orden de instalación recomendado

```powershell
# 1) Copia la plantilla y ajusta URL/usuario/rutas
Copy-Item scripts\pos-kiosk\config.example.ps1 scripts\pos-kiosk\config.ps1
notepad scripts\pos-kiosk\config.ps1

# 2) Aplicar políticas de Windows (requiere admin)
reg import scripts\pos-kiosk\politicas-windows.reg

# 3) Instalar acceso directo en el escritorio del usuario POS
powershell -ExecutionPolicy Bypass -File scripts\pos-kiosk\instalar-acceso-directo.ps1

# 4) Configurar autologin + autoarranque del kiosko
powershell -ExecutionPolicy Bypass -File scripts\pos-kiosk\instalar-autoinicio.ps1

# 5) Registrar el watchdog que relanza Chrome si se cae
powershell -ExecutionPolicy Bypass -File scripts\pos-kiosk\instalar-watchdog-tarea.ps1

# 6) Reinicia la máquina. Debería arrancar directo al POS.
Restart-Computer
```

## URL del POS

Por defecto apuntamos a:

```
https://retail.webappsolutions.cl/app/ticket-venta/?kiosk=1
```

El parámetro `?kiosk=1` activa el context processor `pos_kiosk_context`,
que:

- Fija el viewport a 1920x1080.
- Agrega `class="pos-kiosk sidebar-disabled"` al `<body>`.
- Carga `pos-kiosk.css` (targets 48–64 px, fuentes 16–18 px, sin `:hover`).
- Oculta el sidebar de 163 KB.

## Atajos para salir del kiosko (modo técnico)

- Toca 5 veces el logo NEXO dentro de 2 s → abre prompt para PIN.
- `Ctrl+Shift+Alt+Q` (no bloqueado por políticas) → cierra Chrome.
- Desde otro equipo: RDP al J1900 como administrador.

## Desinstalar

```powershell
powershell -ExecutionPolicy Bypass -File scripts\pos-kiosk\desinstalar.ps1
```
