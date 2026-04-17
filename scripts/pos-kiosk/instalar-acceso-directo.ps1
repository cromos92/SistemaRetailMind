# ============================================================================
# instalar-acceso-directo.ps1
# Crea "NEXO POS.lnk" en el escritorio que ejecuta chrome-kiosk.ps1.
# ============================================================================

$ErrorActionPreference = 'Stop'

$ConfigFile = Join-Path $PSScriptRoot 'config.ps1'
if (-not (Test-Path $ConfigFile)) {
    Write-Error "No existe config.ps1."
    exit 1
}
. $ConfigFile

$KioskScript = Join-Path $PSScriptRoot 'chrome-kiosk.ps1'
if (-not (Test-Path $KioskScript)) {
    Write-Error "No encontré chrome-kiosk.ps1 en $PSScriptRoot"
    exit 1
}

$Desktop   = [Environment]::GetFolderPath('Desktop')
$StartMenu = [Environment]::GetFolderPath('StartMenu')

$Targets = @(
    (Join-Path $Desktop   'NEXO POS.lnk'),
    (Join-Path $StartMenu 'Programs\NEXO POS.lnk')
)

$Shell = New-Object -ComObject WScript.Shell

foreach ($Target in $Targets) {
    $ParentDir = Split-Path $Target -Parent
    if (-not (Test-Path $ParentDir)) {
        New-Item -ItemType Directory -Path $ParentDir -Force | Out-Null
    }

    $Shortcut = $Shell.CreateShortcut($Target)
    $Shortcut.TargetPath       = 'powershell.exe'
    $Shortcut.Arguments        = "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$KioskScript`""
    $Shortcut.WorkingDirectory = $PSScriptRoot
    $Shortcut.IconLocation     = "$ChromePath,0"
    $Shortcut.WindowStyle      = 7   # minimizado (el loader PS, no Chrome)
    $Shortcut.Description      = 'NEXO Retail POS en modo kiosko'
    $Shortcut.Save()

    Write-Host "Acceso directo creado: $Target"
}
