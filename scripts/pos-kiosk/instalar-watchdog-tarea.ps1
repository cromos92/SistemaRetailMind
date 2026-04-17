# ============================================================================
# instalar-watchdog-tarea.ps1
# Registra watchdog.ps1 como Tarea Programada que arranca con el usuario POS.
# Requiere ejecutarse con el usuario POS o con privilegios admin.
# ============================================================================

$ErrorActionPreference = 'Stop'

$ConfigFile = Join-Path $PSScriptRoot 'config.ps1'
if (-not (Test-Path $ConfigFile)) {
    Write-Error "No existe config.ps1."
    exit 1
}
. $ConfigFile

$WatchdogScript = Join-Path $PSScriptRoot 'watchdog.ps1'
if (-not (Test-Path $WatchdogScript)) {
    Write-Error "No existe watchdog.ps1"
    exit 1
}

$Action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$WatchdogScript`"" `
    -WorkingDirectory $PSScriptRoot

$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Trigger.Delay = 'PT20S'   # 20s después del logon

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$Principal = New-ScheduledTaskPrincipal -UserId ([Environment]::UserName) -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $WatchdogTaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description 'Relanza Chrome POS si se cae o se cierra' `
    -Force | Out-Null

Write-Host "Tarea '$WatchdogTaskName' registrada. Arrancará 20s después del login."
