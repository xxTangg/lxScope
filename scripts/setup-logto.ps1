[CmdletBinding()]
param(
    [string]$PublicUrl,
    [string]$Manifest = 'deploy/logto/migration.json',
    [switch]$SeedDemo
)

$ErrorActionPreference = 'Stop'
$rootDir = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $rootDir '.env'
$exampleEnvFile = Join-Path $rootDir '.env.example'
$runner = Join-Path $PSScriptRoot 'migrate-logto.ps1'

function Read-EnvFile {
    $values = @{}
    if (Test-Path -LiteralPath $envFile) {
        foreach ($line in Get-Content -LiteralPath $envFile) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
                $value = $Matches[2].Trim()
                if ($value.Length -ge 2 -and
                    (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                     ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
                $values[$Matches[1]] = $value
            }
        }
    }
    return $values
}

function Read-Value([string]$label, [string]$current, [string]$default = '') {
    if (-not [string]::IsNullOrWhiteSpace($current)) {
        return $current.Trim()
    }
    if ([Console]::IsInputRedirected) {
        throw "Missing value for $label. Set it in .env before running non-interactively."
    }
    $suffix = if ($default) { " [$default]" } else { '' }
    $answer = Read-Host "$label$suffix"
    if ([string]::IsNullOrWhiteSpace($answer)) {
        $answer = $default
    }
    if ([string]::IsNullOrWhiteSpace($answer)) {
        throw "A value is required for $label."
    }
    return $answer.Trim()
}

function Read-Secret([string]$label, [string]$current) {
    if (-not [string]::IsNullOrWhiteSpace($current)) {
        return $current
    }
    if ([Console]::IsInputRedirected) {
        throw "Missing secret for $label. Set it in the process environment before running non-interactively."
    }
    $secure = Read-Host $label -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Set-EnvValues([hashtable]$updates) {
    $lines = if (Test-Path -LiteralPath $envFile) { @(Get-Content -LiteralPath $envFile) } else { @() }
    foreach ($name in $updates.Keys) {
        $escapedName = [regex]::Escape($name)
        $found = $false
        $lines = @(
            foreach ($line in $lines) {
                if ($line -match "^\s*$escapedName=") {
                    $found = $true
                    "$name=$($updates[$name])"
                } else {
                    $line
                }
            }
        )
        if (-not $found) {
            $lines += "$name=$($updates[$name])"
        }
    }
    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllLines($envFile, [string[]]$lines, $encoding)
}

if (-not (Test-Path -LiteralPath $envFile)) {
    if (-not (Test-Path -LiteralPath $exampleEnvFile)) {
        throw "Neither .env nor .env.example exists in $rootDir"
    }
    Copy-Item -LiteralPath $exampleEnvFile -Destination $envFile
}

$values = Read-EnvFile
foreach ($name in $values.Keys) {
    [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process')
}

$endpoint = Read-Value 'Logto endpoint' $values['LOGTO_ENDPOINT']
$apiResource = Read-Value 'lxScope API Resource indicator' $values['LOGTO_API_RESOURCE'] 'https://api.lxscope.local'
$viteEndpoint = if ($values['VITE_LOGTO_ENDPOINT']) { $values['VITE_LOGTO_ENDPOINT'] } else { $endpoint }
$viteResource = if ($values['VITE_LOGTO_API_RESOURCE']) { $values['VITE_LOGTO_API_RESOURCE'] } else { $apiResource }
$port = if ($values['WEB_UI_PORT']) { $values['WEB_UI_PORT'] } else { '8000' }
$defaultPublicUrl = "http://localhost:$port"
if (-not $PublicUrl) {
    $PublicUrl = Read-Value 'Browser-visible lxScope URL (origin only)' $values['LXSCOPE_PUBLIC_URL'] $defaultPublicUrl
}
$PublicUrl = $PublicUrl.TrimEnd('/')

$migrationEndpoint = if ($values['LOGTO_MIGRATION_ENDPOINT']) { $values['LOGTO_MIGRATION_ENDPOINT'] } else { $endpoint }
$managementResource = Read-Value 'Logto Management API Resource indicator' $values['LOGTO_MANAGEMENT_API_RESOURCE'] "$migrationEndpoint/api"
$m2mAppId = Read-Value 'M2M App ID (with Logto Management API access)' $values['LOGTO_M2M_APP_ID']
$m2mAppSecret = Read-Secret 'M2M App Secret (hidden input)' $values['LOGTO_M2M_APP_SECRET']

[Environment]::SetEnvironmentVariable('LOGTO_ENDPOINT', $endpoint, 'Process')
[Environment]::SetEnvironmentVariable('LOGTO_API_RESOURCE', $apiResource, 'Process')
[Environment]::SetEnvironmentVariable('VITE_LOGTO_ENDPOINT', $viteEndpoint, 'Process')
[Environment]::SetEnvironmentVariable('VITE_LOGTO_API_RESOURCE', $viteResource, 'Process')
[Environment]::SetEnvironmentVariable('LXSCOPE_PUBLIC_URL', $PublicUrl, 'Process')
[Environment]::SetEnvironmentVariable('LOGTO_MANAGEMENT_API_RESOURCE', $managementResource, 'Process')
[Environment]::SetEnvironmentVariable('LOGTO_M2M_APP_ID', $m2mAppId, 'Process')
[Environment]::SetEnvironmentVariable('LOGTO_M2M_APP_SECRET', $m2mAppSecret, 'Process')
[Environment]::SetEnvironmentVariable('LXSCOPE_AUTH_PROVIDER', 'logto', 'Process')

if ($SeedDemo) {
    $adminPassword = Read-Secret 'Demo administrator password' $env:LOGTO_DEMO_ADMIN_PASSWORD
    $memberPassword = Read-Secret 'Demo member password' $env:LOGTO_DEMO_MEMBER_PASSWORD
    [Environment]::SetEnvironmentVariable('LOGTO_DEMO_ADMIN_PASSWORD', $adminPassword, 'Process')
    [Environment]::SetEnvironmentVariable('LOGTO_DEMO_MEMBER_PASSWORD', $memberPassword, 'Process')
}

& $runner -Command validate -Manifest $Manifest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $runner -Command apply -Manifest $Manifest -SeedDemo:$SeedDemo
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$outputDir = Join-Path $rootDir 'deploy/logto/generated'
$resultFile = Join-Path $outputDir 'migration-result.json'
if (-not (Test-Path -LiteralPath $resultFile)) {
    throw "Migration finished without a result file: $resultFile"
}
$result = Get-Content -LiteralPath $resultFile -Raw | ConvertFrom-Json
$appId = $result.browser_application.id
if ([string]::IsNullOrWhiteSpace($appId)) {
    throw 'Migration did not return a SPA application ID.'
}

Set-EnvValues @{
    LXSCOPE_AUTH_PROVIDER = 'logto'
    LOGTO_ENDPOINT = $endpoint
    LOGTO_API_RESOURCE = $apiResource
    LXSCOPE_PUBLIC_URL = $PublicUrl
    VITE_LOGTO_ENDPOINT = $viteEndpoint
    VITE_LOGTO_APP_ID = $appId
    VITE_LOGTO_API_RESOURCE = $viteResource
}

Write-Host "[OK] Logto setup/migration complete. SPA App ID saved to .env."
Write-Host "[INFO] Redirect URI: $PublicUrl/auth/callback"
Write-Host "[INFO] Review deploy/logto/generated/identity-map.json if organizations or users were migrated."
Write-Host "[INFO] Rebuild/restart the app for environment changes to take effect."
