# ============================================================================
# Configuración del POS Kiosko
# Copia este archivo a config.ps1 y ajusta los valores.
# ============================================================================

# URL a la que apunta Chrome al arrancar. Ajusta si usas otra pantalla inicial
# (ticket_venta, login, etc.). Siempre incluir ?kiosk=1 para activar el modo.
$PosUrl = 'https://retail.webappsolutions.cl/app/ticket-venta/?kiosk=1'

# Usuario de Windows bajo el que corre el POS (sin dominio).
$PosUser = 'POS'

# Ruta de Chrome. Probamos 3 ubicaciones estándar.
$ChromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LocalAppData\Google\Chrome\Application\chrome.exe"
)
$ChromePath = $ChromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

# Directorio de perfil Chrome dedicado (no toca el perfil de otros usuarios).
$PosProfileDir = "$env:LocalAppData\RetailMindPOS\chrome-profile"

# Segundos entre checks del watchdog.
$WatchdogIntervalSeconds = 10

# Cuántos segundos dejar que Chrome arranque antes del primer check.
$WatchdogGraceSeconds = 25

# Nombre de la Tarea Programada para el watchdog.
$WatchdogTaskName = 'RetailMindPOS-ChromeWatchdog'
