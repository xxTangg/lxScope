[CmdletBinding()]
param(
    [ValidateSet('validate', 'apply', 'export')]
    [string]$Command = 'apply',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$rootDir = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $rootDir '.env'

if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2) {
                if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                    ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
            }
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}

$pythonBin = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { 'python' }
$migrationScript = Join-Path $rootDir 'deploy/logto/migration.py'
& $pythonBin $migrationScript $Command @Arguments
exit $LASTEXITCODE
