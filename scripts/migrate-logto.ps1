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
$originalManagementEnv = @{}

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

function Read-Value([string]$label, [string]$current, [string]$default = '') {
    if (-not [string]::IsNullOrWhiteSpace($current)) { return $current.Trim() }
    if ([Console]::IsInputRedirected) {
        throw "Missing value for $label. Set it in .env or the process environment."
    }
    $suffix = if ($default) { " [$default]" } else { '' }
    $answer = Read-Host "$label$suffix"
    if ([string]::IsNullOrWhiteSpace($answer)) { $answer = $default }
    if ([string]::IsNullOrWhiteSpace($answer)) { throw "A value is required for $label." }
    return $answer.Trim()
}

function Read-Secret([string]$label) {
    if ([Console]::IsInputRedirected) {
        throw "Missing secret for $label. Set it in the process environment for non-interactive use."
    }
    $secure = Read-Host $label -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
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

if ($Command -in @('apply', 'export')) {
    $endpoint = if ($env:LOGTO_MIGRATION_ENDPOINT) { $env:LOGTO_MIGRATION_ENDPOINT } else { $env:LOGTO_ENDPOINT }
    if ([string]::IsNullOrWhiteSpace($endpoint)) {
        throw 'Set LOGTO_ENDPOINT in .env or the process environment before running apply/export.'
    }
    $resourceDefault = "$($endpoint.TrimEnd('/'))/api"
    foreach ($name in @('LOGTO_M2M_APP_ID', 'LOGTO_M2M_APP_SECRET', 'LOGTO_MANAGEMENT_API_RESOURCE')) {
        $originalManagementEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    $appId = Read-Value 'M2M App ID with Logto Management API access' $env:LOGTO_M2M_APP_ID
    $appSecret = $env:LOGTO_M2M_APP_SECRET
    if ([string]::IsNullOrWhiteSpace($appSecret)) { $appSecret = Read-Secret 'M2M App Secret' }
    $managementResource = Read-Value 'Logto Management API Resource indicator' $env:LOGTO_MANAGEMENT_API_RESOURCE $resourceDefault
    [Environment]::SetEnvironmentVariable('LOGTO_M2M_APP_ID', $appId, 'Process')
    [Environment]::SetEnvironmentVariable('LOGTO_M2M_APP_SECRET', $appSecret, 'Process')
    [Environment]::SetEnvironmentVariable('LOGTO_MANAGEMENT_API_RESOURCE', $managementResource, 'Process')
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
    foreach ($name in $originalManagementEnv.Keys) {
        [Environment]::SetEnvironmentVariable($name, $originalManagementEnv[$name], 'Process')
    }
}
exit $resultCode
