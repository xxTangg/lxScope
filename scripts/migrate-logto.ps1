[CmdletBinding()]
param(
    [ValidateSet('validate', 'apply', 'export')]
    [string]$Command = 'apply',
    [string]$Manifest = 'deploy/logto/migration.json',
    [string]$Config = 'deploy/logto/config.json',
    [string]$OutputDir = 'deploy/logto/generated',
    [switch]$SeedDemo
)

$ErrorActionPreference = 'Stop'
$rootDir = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $rootDir '.env'
$migrationScript = Join-Path $rootDir 'deploy/logto/migration.py'

function Resolve-ProjectPath([string]$path) {
    if ([System.IO.Path]::IsPathRooted($path)) {
        return $path
    }
    return Join-Path $rootDir $path
}

if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2 -and
                (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                 ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            $current = [Environment]::GetEnvironmentVariable($name, 'Process')
            if ([string]::IsNullOrWhiteSpace($current)) {
                [Environment]::SetEnvironmentVariable($name, $value, 'Process')
            }
        }
    }
}

$pythonName = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { 'python' }
$python = Get-Command $pythonName -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python was not found. Install Python 3.10+ or set PYTHON_BIN."
}
if (-not (Test-Path -LiteralPath $migrationScript)) {
    throw "Migration engine is missing: $migrationScript"
}

$arguments = @(
    $migrationScript,
    $Command,
    '--manifest', (Resolve-ProjectPath $Manifest),
    '--config', (Resolve-ProjectPath $Config),
    '--output-dir', (Resolve-ProjectPath $OutputDir)
)
if ($SeedDemo) {
    $arguments += '--seed-demo'
}

Push-Location $rootDir
try {
    & $python.Source @arguments
    $resultCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $resultCode
