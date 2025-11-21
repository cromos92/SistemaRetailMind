# ========================================================================
# Script PowerShell para Re-Importar Histórico Completo de Laravel
# ========================================================================
# 
# Este script automatiza el proceso de:
# 1. Limpiar datos existentes
# 2. Importar histórico completo desde MySQL (Laravel)
# 
# Uso:
#   .\reimportar_historico.ps1
#   .\reimportar_historico.ps1 -BatchSize 3000
#   .\reimportar_historico.ps1 -Limit 1000 -BatchSize 500  # Para pruebas
#   .\reimportar_historico.ps1 -SinDtes  # Sin importar DTEs
# ========================================================================

param(
    [int]$BatchSize = 2000,
    [int]$Limit = 0,
    [switch]$SinDtes,
    [switch]$DryRun
)

# Colores
$ColorExito = "Green"
$ColorAdvertencia = "Yellow"
$ColorError = "Red"
$ColorInfo = "Cyan"

# Banner
Write-Host "========================================================================" -ForegroundColor $ColorInfo
Write-Host "🔄 RE-IMPORTACIÓN DE HISTÓRICO COMPLETO" -ForegroundColor $ColorInfo
Write-Host "========================================================================" -ForegroundColor $ColorInfo
Write-Host ""

# Verificar directorio
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

Write-Host "📁 Directorio de trabajo: $scriptPath" -ForegroundColor $ColorInfo
Write-Host ""

# Configuración
Write-Host "⚙️  CONFIGURACIÓN:" -ForegroundColor $ColorInfo
Write-Host "  • Batch Size: $BatchSize" -ForegroundColor $ColorInfo
if ($Limit -gt 0) {
    Write-Host "  • Límite de registros: $Limit (MODO PRUEBA)" -ForegroundColor $ColorAdvertencia
}
if ($SinDtes) {
    Write-Host "  • Importar DTEs: NO" -ForegroundColor $ColorAdvertencia
} else {
    Write-Host "  • Importar DTEs: SÍ" -ForegroundColor $ColorInfo
}
if ($DryRun) {
    Write-Host "  • Modo: DRY-RUN (simulación)" -ForegroundColor $ColorAdvertencia
}
Write-Host ""

# Confirmación
Write-Host "⚠️  ADVERTENCIA: Esta operación eliminará TODOS los datos importados previamente." -ForegroundColor $ColorAdvertencia
Write-Host ""
$confirmacion = Read-Host "¿Desea continuar? (SI/NO)"

if ($confirmacion -ne "SI") {
    Write-Host ""
    Write-Host "❌ Operación cancelada por el usuario." -ForegroundColor $ColorError
    exit 1
}

Write-Host ""
Write-Host "========================================================================" -ForegroundColor $ColorInfo
Write-Host "PASO 1: LIMPIEZA DE DATOS EXISTENTES" -ForegroundColor $ColorInfo
Write-Host "========================================================================" -ForegroundColor $ColorInfo
Write-Host ""

$tiempoInicio = Get-Date

# Ejecutar limpieza
if ($DryRun) {
    Write-Host "🧪 [DRY-RUN] Simulando limpieza de datos..." -ForegroundColor $ColorAdvertencia
} else {
    Write-Host "🗑️  Ejecutando limpieza..." -ForegroundColor $ColorInfo
    & ..\venv\Scripts\python.exe manage.py clean_migration_data --confirm
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "❌ ERROR: Falló la limpieza de datos." -ForegroundColor $ColorError
        exit $LASTEXITCODE
    }
}

Write-Host ""
Write-Host "✅ Limpieza completada exitosamente" -ForegroundColor $ColorExito
Write-Host ""

# Pausa breve
Start-Sleep -Seconds 2

Write-Host "========================================================================" -ForegroundColor $ColorInfo
Write-Host "PASO 2: IMPORTACIÓN DE HISTÓRICO" -ForegroundColor $ColorInfo
Write-Host "========================================================================" -ForegroundColor $ColorInfo
Write-Host ""

# Construir comando de importación
$comandoImportacion = "..\venv\Scripts\python.exe manage.py migrate_from_laravel --batch-size $BatchSize"

if ($Limit -gt 0) {
    $comandoImportacion += " --limit $Limit"
}

if ($DryRun) {
    $comandoImportacion += " --dry-run"
}

if ($SinDtes) {
    $comandoImportacion += " --tables empresas clientes sucursales atributos categorias productos producto_talla movimientos"
}

Write-Host "🚀 Ejecutando importación..." -ForegroundColor $ColorInfo
Write-Host "   Comando: $comandoImportacion" -ForegroundColor $ColorInfo
Write-Host ""

# Ejecutar importación
Invoke-Expression $comandoImportacion

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "❌ ERROR: Falló la importación de datos." -ForegroundColor $ColorError
    exit $LASTEXITCODE
}

$tiempoFin = Get-Date
$duracion = $tiempoFin - $tiempoInicio

Write-Host ""
Write-Host "========================================================================" -ForegroundColor $ColorExito
Write-Host "✅ IMPORTACIÓN COMPLETADA EXITOSAMENTE" -ForegroundColor $ColorExito
Write-Host "========================================================================" -ForegroundColor $ColorExito
Write-Host ""
Write-Host "⏱️  Tiempo total: $duracion" -ForegroundColor $ColorInfo
Write-Host ""

# Mostrar log de errores
if (Test-Path "migration_errors.log") {
    $erroresCount = (Get-Content "migration_errors.log" | Measure-Object -Line).Lines
    
    if ($erroresCount -gt 10) {
        Write-Host "⚠️  Se encontraron errores durante la importación." -ForegroundColor $ColorAdvertencia
        Write-Host "📄 Ver log completo: migration_errors.log" -ForegroundColor $ColorInfo
        Write-Host ""
        Write-Host "📋 Últimas 10 líneas del log:" -ForegroundColor $ColorInfo
        Get-Content "migration_errors.log" -Tail 10
    } else {
        Write-Host "✅ Pocos o ningún error detectado." -ForegroundColor $ColorExito
    }
} else {
    Write-Host "✅ No se generaron errores." -ForegroundColor $ColorExito
}

Write-Host ""
Write-Host "========================================================================" -ForegroundColor $ColorInfo
Write-Host "🎉 PROCESO COMPLETADO" -ForegroundColor $ColorExito
Write-Host "========================================================================" -ForegroundColor $ColorInfo
Write-Host ""

# Próximos pasos
Write-Host "📋 PRÓXIMOS PASOS:" -ForegroundColor $ColorInfo
Write-Host "  1. Revisar migration_errors.log si hay errores" -ForegroundColor $ColorInfo
Write-Host "  2. Verificar conteo de registros en PostgreSQL" -ForegroundColor $ColorInfo
Write-Host "  3. Ejecutar ANALYZE en PostgreSQL para actualizar estadísticas" -ForegroundColor $ColorInfo
Write-Host ""

