# ============================================================================
# instalar-autoinicio.ps1
# Agrega el launcher del kiosko al arranque del usuario actual (Shell:Startup).
# No configura AutoLogon de Windows (eso se hace con sysinternals Autologon
# o con netplwiz, no vía script sin pedir credenciales en texto plano).
# ============================================================================

$ErrorActionPreference = 'Stop'

$Startup = [Environment]::GetFolderPath('Startup')
$KioskScript = Join-Path $PSScriptRoot 'chrome-kiosk.ps1'
if (-not (Test-Path $KioskScript)) {
    Write-Error "No encontré chrome-kiosk.ps1 en $PSScriptRoot"
    exit 1
}

$Link = Join-Path $Startup 'NEXO POS (autoinicio).lnk'
$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($Link)
$Shortcut.TargetPath       = 'powershell.exe'
$Shortcut.Arguments        = "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$KioskScript`""
$Shortcut.WorkingDirectory = $PSScriptRoot
$Shortcut.WindowStyle      = 7
$Shortcut.Description      = 'NEXO POS (arranque automático)'
$Shortcut.Save()

Write-Host "Autoinicio instalado: $Link"
Write-Host ""
Write-Host "Para configurar AutoLogon (que Windows entre solo al usuario POS"
Write-Host "sin pedir password), usa la herramienta oficial de Sysinternals:"
Write-Host "  https://learn.microsoft.com/sysinternals/downloads/autologon"
Write-Host ""
Write-Host "Alternativa rápida sin admin:"
Write-Host "  control userpasswords2"
Write-Host "  -> destildar 'Los usuarios deben escribir ...'"
