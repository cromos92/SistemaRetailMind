# ============================================================================
# chrome-kiosk.ps1
# Lanza Chrome en modo kiosko apuntando a $PosUrl definido en config.ps1.
# Uso: powershell -ExecutionPolicy Bypass -File chrome-kiosk.ps1
# ============================================================================

$ErrorActionPreference = 'Stop'

$ConfigFile = Join-Path $PSScriptRoot 'config.ps1'
if (-not (Test-Path $ConfigFile)) {
    Write-Error "No existe config.ps1. Copia config.example.ps1 y edítalo primero."
    exit 1
}
. $ConfigFile

if (-not $ChromePath -or -not (Test-Path $ChromePath)) {
    Write-Error "Google Chrome no se encontró. Edita `$ChromeCandidates en config.ps1."
    exit 1
}

if (-not (Test-Path $PosProfileDir)) {
    New-Item -ItemType Directory -Path $PosProfileDir -Force | Out-Null
}

# Flags de kiosko para J1900 + pantalla táctil:
#   --kiosk                                 Pantalla completa sin chrome del navegador.
#   --app                                   Elimina la URL bar.
#   --incognito                             Sin historial ni sugerencias.
#   --disable-pinch                         Evita zoom con dos dedos.
#   --overscroll-history-navigation=0       No navega atrás al deslizar desde el borde.
#   --start-fullscreen                      Redundante con --kiosk pero seguro.
#   --noerrdialogs                          Sin diálogos modales de Chrome.
#   --disable-session-crashed-bubble        Sin "¿Restaurar pestañas?" al relanzar.
#   --disable-features=TranslateUI          Sin banner de traducir.
#   --disable-infobars                      Sin barra "Chrome controlado por ...".
#   --autoplay-policy=no-user-gesture-required  Sonidos de impresión/beep sin click.
#   --user-data-dir                         Perfil dedicado (permite limpiar sin perder otros).
$ChromeArgs = @(
    '--kiosk',
    ('--app=' + $PosUrl),
    '--incognito',
    '--disable-pinch',
    '--overscroll-history-navigation=0',
    '--start-fullscreen',
    '--noerrdialogs',
    '--disable-session-crashed-bubble',
    '--disable-features=TranslateUI',
    '--disable-infobars',
    '--autoplay-policy=no-user-gesture-required',
    '--disable-pinch-zoom',
    '--force-device-scale-factor=1',
    ('--user-data-dir=' + $PosProfileDir)
)

Start-Process -FilePath $ChromePath -ArgumentList $ChromeArgs -WorkingDirectory (Split-Path $ChromePath)
