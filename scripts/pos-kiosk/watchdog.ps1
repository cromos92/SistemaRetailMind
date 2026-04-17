# ============================================================================
# watchdog.ps1
# Si Chrome (kiosko del POS) no está corriendo, lo relanza.
# Pensado para correr como Tarea Programada en loop infinito.
# ============================================================================

$ErrorActionPreference = 'SilentlyContinue'

$ConfigFile = Join-Path $PSScriptRoot 'config.ps1'
if (-not (Test-Path $ConfigFile)) {
    Write-EventLog -LogName Application -Source 'NEXO POS' -EventId 101 -EntryType Error `
        -Message "Watchdog no encontró config.ps1" -ErrorAction SilentlyContinue
    exit 1
}
. $ConfigFile

$KioskScript = Join-Path $PSScriptRoot 'chrome-kiosk.ps1'
if (-not (Test-Path $KioskScript)) { exit 1 }

# Esperar grace inicial (Chrome recién abierto en arranque).
Start-Sleep -Seconds $WatchdogGraceSeconds

while ($true) {
    try {
        # Buscamos un chrome.exe cuya línea de comando incluya nuestro user-data-dir.
        # Si no hay, asumimos que el kiosko cayó y lo relanzamos.
        $matches = Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$PosProfileDir*" }

        if (-not $matches) {
            Start-Process -FilePath 'powershell.exe' `
                -ArgumentList @('-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',"`"$KioskScript`"") `
                -WorkingDirectory $PSScriptRoot | Out-Null
        }
    } catch {
        # Ignorar errores intermitentes WMI
    }

    Start-Sleep -Seconds $WatchdogIntervalSeconds
}
