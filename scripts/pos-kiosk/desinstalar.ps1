# ============================================================================
# desinstalar.ps1
# Revierte todo lo que instalaron los scripts del kiosko en el usuario actual.
# No toca politicas-windows.reg (usa el archivo .reg separado para revertir).
# ============================================================================

$ErrorActionPreference = 'SilentlyContinue'

$ConfigFile = Join-Path $PSScriptRoot 'config.ps1'
if (Test-Path $ConfigFile) { . $ConfigFile }

$Desktop   = [Environment]::GetFolderPath('Desktop')
$StartMenu = [Environment]::GetFolderPath('StartMenu')
$Startup   = [Environment]::GetFolderPath('Startup')

# 1) Quitar accesos directos
@(
    (Join-Path $Desktop   'NEXO POS.lnk'),
    (Join-Path $StartMenu 'Programs\NEXO POS.lnk'),
    (Join-Path $Startup   'NEXO POS (autoinicio).lnk')
) | ForEach-Object {
    if (Test-Path $_) {
        Remove-Item $_ -Force
        Write-Host "Removido: $_"
    }
}

# 2) Desregistrar watchdog
if ($WatchdogTaskName) {
    try {
        Unregister-ScheduledTask -TaskName $WatchdogTaskName -Confirm:$false -ErrorAction Stop
        Write-Host "Tarea '$WatchdogTaskName' eliminada."
    } catch {
        Write-Host "Tarea '$WatchdogTaskName' no existía o no se pudo eliminar."
    }
}

# 3) Matar Chrome kiosko
Get-Process chrome -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -like '*NEXO*' -or $_.Path -like '*chrome.exe' } |
    Stop-Process -Force -ErrorAction SilentlyContinue

# 4) Recordatorio
Write-Host ""
Write-Host "Para revertir las políticas Registry:"
Write-Host "  reg delete HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer /f"
Write-Host "  reg delete HKLM\SOFTWARE\Policies\Google\Chrome /f"
Write-Host "  reg delete `"HKLM\SYSTEM\CurrentControlSet\Control\Keyboard Layout`" /v `"Scancode Map`" /f"
Write-Host "  (y reinicia)"
