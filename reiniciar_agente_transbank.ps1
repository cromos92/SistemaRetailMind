# Script para Reiniciar el Agente Transbank POS
# Ejecutar con: powershell -ExecutionPolicy Bypass -File reiniciar_agente_transbank.ps1

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  REINICIANDO AGENTE TRANSBANK POS" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Paso 1: Cerrar todos los procesos relacionados con Transbank
Write-Host "[1/5] Cerrando procesos del agente Transbank..." -ForegroundColor Yellow
$processes = @("tbk_agent", "transbank-pos-agent", "TransbankPOSAgent")
foreach ($proc in $processes) {
    $running = Get-Process -Name $proc -ErrorAction SilentlyContinue
    if ($running) {
        Write-Host "  - Cerrando: $proc" -ForegroundColor White
        Stop-Process -Name $proc -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
}
Write-Host "  ✓ Procesos cerrados" -ForegroundColor Green
Write-Host ""

# Paso 2: Liberar puerto 8090
Write-Host "[2/5] Verificando y liberando puerto 8090..." -ForegroundColor Yellow
$connections = Get-NetTCPConnection -LocalPort 8090 -ErrorAction SilentlyContinue
if ($connections) {
    foreach ($conn in $connections) {
        $pid = $conn.OwningProcess
        Write-Host "  - Liberando puerto 8090 (PID: $pid)" -ForegroundColor White
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    Write-Host "  ✓ Puerto 8090 liberado" -ForegroundColor Green
} else {
    Write-Host "  ✓ Puerto 8090 ya estaba libre" -ForegroundColor Green
}
Write-Host ""

# Paso 3: Buscar la ubicación del agente
Write-Host "[3/5] Buscando instalación del Agente Transbank..." -ForegroundColor Yellow
$possiblePaths = @(
    "C:\Program Files\Transbank\POS Agent\tbk_agent.exe",
    "C:\Program Files (x86)\Transbank\POS Agent\tbk_agent.exe",
    "C:\Transbank\POS Agent\tbk_agent.exe"
)

$agentPath = $null
foreach ($path in $possiblePaths) {
    if (Test-Path $path) {
        $agentPath = $path
        Write-Host "  ✓ Agente encontrado en: $path" -ForegroundColor Green
        break
    }
}

if (-not $agentPath) {
    Write-Host "  ✗ No se encontró el Agente Transbank instalado" -ForegroundColor Red
    Write-Host ""
    Write-Host "SOLUCIONES:" -ForegroundColor Yellow
    Write-Host "  1. Descargar desde: https://www.transbankdevelopers.cl/producto/posintegrado" -ForegroundColor White
    Write-Host "  2. Instalar el agente" -ForegroundColor White
    Write-Host "  3. Ejecutar este script nuevamente" -ForegroundColor White
    Write-Host ""
    Write-Host "  O usar el MODO DEMO en el sistema (no requiere agente)" -ForegroundColor Cyan
    Write-Host ""
    pause
    exit
}
Write-Host ""

# Paso 4: Iniciar el agente
Write-Host "[4/5] Iniciando Agente Transbank..." -ForegroundColor Yellow
Start-Process -FilePath $agentPath -WindowStyle Hidden
Start-Sleep -Seconds 5
Write-Host "  ✓ Agente iniciado" -ForegroundColor Green
Write-Host ""

# Paso 5: Verificar que esté corriendo
Write-Host "[5/5] Verificando estado del agente..." -ForegroundColor Yellow
Start-Sleep -Seconds 2

$agentRunning = Get-Process -Name "tbk_agent","transbank-pos-agent" -ErrorAction SilentlyContinue
$portOpen = Get-NetTCPConnection -LocalPort 8090 -State Listen -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  RESULTADO DEL DIAGNÓSTICO" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($agentRunning) {
    Write-Host "  ✓ Agente Transbank: CORRIENDO" -ForegroundColor Green
} else {
    Write-Host "  ✗ Agente Transbank: NO DETECTADO" -ForegroundColor Red
}

if ($portOpen) {
    Write-Host "  ✓ Puerto 8090: ABIERTO" -ForegroundColor Green
} else {
    Write-Host "  ✗ Puerto 8090: CERRADO" -ForegroundColor Red
}

Write-Host ""

# Verificar terminales COM
Write-Host "  Puertos COM detectados:" -ForegroundColor Cyan
$comPorts = [System.IO.Ports.SerialPort]::GetPortNames()
if ($comPorts) {
    foreach ($port in $comPorts) {
        Write-Host "    - $port" -ForegroundColor White
    }
} else {
    Write-Host "    (Ningún puerto COM detectado)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan

if ($agentRunning -and $portOpen) {
    Write-Host ""
    Write-Host "  ¡ÉXITO! El agente está listo" -ForegroundColor Green
    Write-Host ""
    Write-Host "PRÓXIMOS PASOS:" -ForegroundColor Yellow
    Write-Host "  1. Abrir: http://127.0.0.1:8000/app/pos/transbank/" -ForegroundColor White
    Write-Host "  2. Click en 'Diagnosticar SDK'" -ForegroundColor White
    Write-Host "  3. Click en 'Auto-Detectar Terminales'" -ForegroundColor White
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "  Hubo un problema al iniciar el agente" -ForegroundColor Red
    Write-Host ""
    Write-Host "SOLUCIONES:" -ForegroundColor Yellow
    Write-Host "  1. Reiniciar el PC" -ForegroundColor White
    Write-Host "  2. Ejecutar este script como Administrador" -ForegroundColor White
    Write-Host "  3. Verificar que el terminal POS esté conectado" -ForegroundColor White
    Write-Host "  4. Usar el MODO DEMO en el sistema" -ForegroundColor White
    Write-Host ""
}

Write-Host ""
Write-Host "Presione cualquier tecla para salir..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

