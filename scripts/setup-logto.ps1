[CmdletBinding()]
param(
    [switch]$SeedDemo
)

$ErrorActionPreference = 'Stop'
$rootDir = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $rootDir '.env'
$frontendEnvFile = Join-Path $rootDir 'examples/web_ui/frontend/.env.local'

function Read-EnvValues {
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

function Set-EnvValue([string]$name, [string]$value, [hashtable]$values) {
    $values[$name] = $value
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    $lines = if (Test-Path -LiteralPath $envFile) { @(Get-Content -LiteralPath $envFile) } else { @() }
    $escapedName = [regex]::Escape($name)
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match "^\s*$escapedName=") {
            $found = $true
            "$name=$value"
        } else {
            $line
        }
    }
    if (-not $found) {
        $updated += "$name=$value"
    }
    Set-Content -LiteralPath $envFile -Value $updated -Encoding utf8
}

function Set-FrontendEnvValue([string]$name, [string]$value) {
    $lines = if (Test-Path -LiteralPath $frontendEnvFile) { @(Get-Content -LiteralPath $frontendEnvFile) } else { @() }
    $escapedName = [regex]::Escape($name)
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match "^\s*$escapedName=") {
            $found = $true
            "$name=$value"
        } else {
            $line
        }
    }
    if (-not $found) {
        $updated += "$name=$value"
    }
    Set-Content -LiteralPath $frontendEnvFile -Value $updated -Encoding utf8
}

function Read-RequiredValue([string]$label, [string]$current) {
    if (-not [string]::IsNullOrWhiteSpace($current)) {
        return $current
    }
    do {
        $value = Read-Host $label
    } while ([string]::IsNullOrWhiteSpace($value))
    return $value.Trim()
}

function Read-SecretValue([string]$label, [string]$current) {
    if (-not [string]::IsNullOrWhiteSpace($current)) {
        return $current
    }
    $secure = Read-Host $label -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

$values = Read-EnvValues
if (-not (Test-Path -LiteralPath $envFile)) {
    New-Item -ItemType File -Path $envFile -Force | Out-Null
}

$logtoEndpoint = if ($values.ContainsKey('LOGTO_ENDPOINT')) { $values['LOGTO_ENDPOINT'] } else { '' }
$logtoEndpoint = if ($logtoEndpoint) { $logtoEndpoint } else { 'http://localhost:3001' }
$m2mAppId = Read-RequiredValue 'M2M App ID（Logto 应用详情页）' ($values['LOGTO_M2M_APP_ID'])
$m2mSecret = Read-SecretValue 'M2M App Secret（不会回显）' ($values['LOGTO_M2M_APP_SECRET'])
$managementResource = if ($values['LOGTO_MANAGEMENT_API_RESOURCE']) { $values['LOGTO_MANAGEMENT_API_RESOURCE'] } else { 'https://default.logto.app/api' }
$apiResource = if ($values['LOGTO_API_RESOURCE']) { $values['LOGTO_API_RESOURCE'] } else { 'https://api.lxscope.local' }
$publicUrl = if ($values['LXSCOPE_PUBLIC_URL']) { $values['LXSCOPE_PUBLIC_URL'] } else { 'http://localhost:8000' }

Set-EnvValue 'LOGTO_ENDPOINT' $logtoEndpoint $values
Set-EnvValue 'LOGTO_M2M_APP_ID' $m2mAppId $values
Set-EnvValue 'LOGTO_M2M_APP_SECRET' $m2mSecret $values
Set-EnvValue 'LOGTO_MANAGEMENT_API_RESOURCE' $managementResource $values
Set-EnvValue 'LOGTO_API_RESOURCE' $apiResource $values
Set-EnvValue 'LXSCOPE_PUBLIC_URL' $publicUrl $values

if ($SeedDemo) {
    $adminPassword = Read-SecretValue '演示管理员密码（不会回显）' ($env:LOGTO_DEMO_ADMIN_PASSWORD)
    $memberPassword = Read-SecretValue '演示成员密码（不会回显）' ($env:LOGTO_DEMO_MEMBER_PASSWORD)
    [Environment]::SetEnvironmentVariable('LOGTO_DEMO_ADMIN_PASSWORD', $adminPassword, 'Process')
    [Environment]::SetEnvironmentVariable('LOGTO_DEMO_MEMBER_PASSWORD', $memberPassword, 'Process')
}

& (Join-Path $PSScriptRoot 'migrate-logto.ps1') validate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$applyArguments = @('apply')
if ($SeedDemo) { $applyArguments += '--seed-demo' }
& (Join-Path $PSScriptRoot 'migrate-logto.ps1') @applyArguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$resultFile = Join-Path $rootDir 'deploy/logto/generated/migration-result.json'
if (Test-Path -LiteralPath $resultFile) {
    $result = Get-Content -LiteralPath $resultFile -Raw | ConvertFrom-Json
    $appId = $result.browser_application.id
    if ($appId) {
        Set-EnvValue 'VITE_AUTH_PROVIDER' 'logto' $values
        Set-EnvValue 'VITE_LOGTO_ENDPOINT' $logtoEndpoint $values
        Set-EnvValue 'VITE_LOGTO_APP_ID' $appId $values
        Set-EnvValue 'VITE_LOGTO_API_RESOURCE' $apiResource $values
        Set-FrontendEnvValue 'VITE_AUTH_PROVIDER' 'logto'
        Set-FrontendEnvValue 'VITE_LOGTO_ENDPOINT' $logtoEndpoint
        Set-FrontendEnvValue 'VITE_LOGTO_APP_ID' $appId
        Set-FrontendEnvValue 'VITE_LOGTO_API_RESOURCE' $apiResource
        Write-Host "[OK] Frontend SPA configuration written: $appId"
    }
}
