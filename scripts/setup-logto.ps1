[CmdletBinding()]
param(
    [string]$PublicUrl,
    [string]$Manifest = 'deploy/logto/migration.json',
    [switch]$ConfigureOnly,
    [switch]$Dev
)

$ErrorActionPreference = 'Stop'
$rootDir = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $rootDir '.env'
$exampleEnvFile = Join-Path $rootDir '.env.example'
$runner = Join-Path $PSScriptRoot 'migrate-logto.ps1'
$callerManagementEnv = @{}
foreach ($name in @('LOGTO_M2M_APP_ID', 'LOGTO_M2M_APP_SECRET', 'LOGTO_MANAGEMENT_API_RESOURCE')) {
    $callerManagementEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

if (-not $ConfigureOnly -and -not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker CLI was not found. Install Docker Desktop or pass -ConfigureOnly.'
}

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

function New-DemoPassword {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
        return ([Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_') + 'aA1!')
    } finally {
        $generator.Dispose()
    }
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

$endpoint = if ($values['LOGTO_ENDPOINT']) { $values['LOGTO_ENDPOINT'] } else { 'https://default.logto.app' }
$apiResource = if ($values['LOGTO_API_RESOURCE']) { $values['LOGTO_API_RESOURCE'] } else { 'https://api.lxscope.local' }
$viteEndpoint = if ($values['VITE_LOGTO_ENDPOINT']) { $values['VITE_LOGTO_ENDPOINT'] } else { $endpoint }
$viteResource = if ($values['VITE_LOGTO_API_RESOURCE']) { $values['VITE_LOGTO_API_RESOURCE'] } else { $apiResource }
$port = if ($values['WEB_UI_PORT']) { $values['WEB_UI_PORT'] } else { '8000' }
$defaultPublicUrl = "http://localhost:$port"
if (-not $PublicUrl) { $PublicUrl = if ($values['LXSCOPE_PUBLIC_URL']) { $values['LXSCOPE_PUBLIC_URL'] } else { $defaultPublicUrl } }
$PublicUrl = $PublicUrl.TrimEnd('/')

$migrationEndpoint = if ($values['LOGTO_MIGRATION_ENDPOINT']) { $values['LOGTO_MIGRATION_ENDPOINT'] } else { $endpoint }
$managementResource = if ($values['LOGTO_MANAGEMENT_API_RESOURCE']) { $values['LOGTO_MANAGEMENT_API_RESOURCE'] } else { "$($migrationEndpoint.TrimEnd('/'))/api" }
$m2mAppId = Read-Value 'M2M App ID (with Logto Management API access)' $values['LOGTO_M2M_APP_ID']
$m2mAppSecret = Read-Secret 'M2M App Secret (hidden input)' $values['LOGTO_M2M_APP_SECRET']

[Environment]::SetEnvironmentVariable('LOGTO_ENDPOINT', $endpoint, 'Process')
[Environment]::SetEnvironmentVariable('LOGTO_MIGRATION_ENDPOINT', $migrationEndpoint, 'Process')
[Environment]::SetEnvironmentVariable('LOGTO_API_RESOURCE', $apiResource, 'Process')
[Environment]::SetEnvironmentVariable('VITE_LOGTO_ENDPOINT', $viteEndpoint, 'Process')
[Environment]::SetEnvironmentVariable('VITE_LOGTO_API_RESOURCE', $viteResource, 'Process')
[Environment]::SetEnvironmentVariable('LXSCOPE_PUBLIC_URL', $PublicUrl, 'Process')
[Environment]::SetEnvironmentVariable('LOGTO_MANAGEMENT_API_RESOURCE', $managementResource, 'Process')
[Environment]::SetEnvironmentVariable('LOGTO_M2M_APP_ID', $m2mAppId, 'Process')
[Environment]::SetEnvironmentVariable('LOGTO_M2M_APP_SECRET', $m2mAppSecret, 'Process')
[Environment]::SetEnvironmentVariable('LXSCOPE_AUTH_PROVIDER', 'logto', 'Process')

$manifestPath = if ([System.IO.Path]::IsPathRooted($Manifest)) { $Manifest } else { Join-Path $rootDir $Manifest }
$manifestDocument = $null
if ([System.IO.Path]::GetExtension($manifestPath).ToLowerInvariant() -eq '.json') {
    $manifestDocument = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
}
$organizationSpecs = @($manifestDocument.organizations)
$demoPasswords = @{}
foreach ($organization in $organizationSpecs) {
    foreach ($user in @($organization.users)) {
        $passwordName = [string]$user.password_env
        if ([string]::IsNullOrWhiteSpace($passwordName)) { continue }
        $password = [Environment]::GetEnvironmentVariable($passwordName, 'Process')
        if ([string]::IsNullOrWhiteSpace($password)) {
            $password = New-DemoPassword
            [Environment]::SetEnvironmentVariable($passwordName, $password, 'Process')
        }
        $demoPasswords[$passwordName] = $password
    }
}

$migrationExitCode = 0
try {
    & $runner -Command validate -Manifest $Manifest
    if ($LASTEXITCODE -ne 0) {
        $migrationExitCode = $LASTEXITCODE
    } else {
        & $runner -Command apply -Manifest $Manifest
        $migrationExitCode = $LASTEXITCODE
    }
} finally {
    foreach ($name in $callerManagementEnv.Keys) {
        [Environment]::SetEnvironmentVariable($name, $callerManagementEnv[$name], 'Process')
    }
}
if ($migrationExitCode -ne 0) { exit $migrationExitCode }

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

$envUpdates = @{
    LXSCOPE_AUTH_PROVIDER = 'logto'
    LOGTO_ENDPOINT = $endpoint
    LOGTO_MIGRATION_ENDPOINT = $migrationEndpoint
    LOGTO_MANAGEMENT_API_RESOURCE = $managementResource
    LOGTO_API_RESOURCE = $apiResource
    LXSCOPE_PUBLIC_URL = $PublicUrl
    VITE_LOGTO_ENDPOINT = $viteEndpoint
    VITE_LOGTO_APP_ID = $appId
    VITE_LOGTO_API_RESOURCE = $viteResource
}
foreach ($passwordName in $demoPasswords.Keys) { $envUpdates[$passwordName] = $demoPasswords[$passwordName] }
foreach ($name in @('AGENTSCOPE_JWT_SECRET', 'AGENTSCOPE_DOWNLOAD_SECRET', 'AGENTSCOPE_PASSWORD')) {
    $currentValue = [Environment]::GetEnvironmentVariable($name, 'Process')
    if ([string]::IsNullOrWhiteSpace($currentValue) -or
        $currentValue -eq 'change-me' -or
        $currentValue.StartsWith('replace-with')) {
        $currentValue = New-DemoPassword
        [Environment]::SetEnvironmentVariable($name, $currentValue, 'Process')
    }
    $envUpdates[$name] = $currentValue
}
Set-EnvValues $envUpdates

if ($organizationSpecs.Count -gt 0 -and $demoPasswords.Count -gt 0) {
    $credentialLines = @(
        'lxScope demo accounts (generated locally; keep this file private)',
        'Organization | Username | Email | Role | Password'
    )
    foreach ($organization in $organizationSpecs) {
        foreach ($user in @($organization.users)) {
            $passwordName = [string]$user.password_env
            if ($passwordName -and $demoPasswords.ContainsKey($passwordName)) {
                $credentialLines += "$($organization.name) | $($user.username) | $($user.primary_email) | $($user.role) | $($demoPasswords[$passwordName])"
            }
        }
    }
    $credentialsPath = Join-Path (Join-Path $rootDir 'deploy/logto/generated') 'demo-credentials.txt'
    [System.IO.File]::WriteAllLines($credentialsPath, [string[]]$credentialLines, [System.Text.UTF8Encoding]::new($false))
    Write-Host "[INFO] Demo login credentials: $credentialsPath"
}

Write-Host "[OK] Logto setup/migration complete. SPA App ID saved to .env."
Write-Host "[INFO] Redirect URI: $PublicUrl/auth/callback"
Write-Host "[INFO] Review deploy/logto/generated/identity-map.json if organizations or users were migrated."
if ($ConfigureOnly) {
    Write-Host '[INFO] Configuration only; start or rebuild the app when ready.'
    exit 0
}

Push-Location $rootDir
try {
    $composeArguments = @('compose')
    if ($Dev) {
        $composeArguments += @('-f', 'docker-compose.yml', '-f', 'docker-compose.dev.yml')
    }
    $composeArguments += @('up', '-d', '--build')
    & docker @composeArguments
    $composeExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($composeExitCode -ne 0) {
    exit $composeExitCode
}
if ($Dev) {
    Write-Host '[OK] Docker Compose development services are running with API reload and Vite HMR.'
} else {
    Write-Host '[OK] Docker Compose deployment services are running.'
}
