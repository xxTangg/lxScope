$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root 'host-runtime'
$logs = Join-Path $runtime 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null

function Test-ListeningPort([int] $port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

if (-not (Test-ListeningPort 8001)) {
    & (Join-Path $PSScriptRoot 'start-host-customer.ps1')
}

$node = (Get-Command node.exe).Source
$pnpm = (Get-Command pnpm.cmd).Source
$salesRoot = Join-Path $root 'sales-console'

if (-not (Test-ListeningPort 44100)) {
    Start-Process -FilePath $node `
        -ArgumentList 'scripts/start-host-api.mjs' `
        -WorkingDirectory $salesRoot `
        -RedirectStandardOutput (Join-Path $logs 'sales-api.log') `
        -RedirectStandardError (Join-Path $logs 'sales-api.error.log') `
        -WindowStyle Hidden | Out-Null
}

if (-not (Test-ListeningPort 8000)) {
    Start-Process -FilePath $pnpm `
        -ArgumentList '--filter','frontend','exec','vite','preview','--host','0.0.0.0','--port','8000' `
        -WorkingDirectory (Join-Path $root 'examples/web_ui') `
        -RedirectStandardOutput (Join-Path $logs 'customer-web.log') `
        -RedirectStandardError (Join-Path $logs 'customer-web.error.log') `
        -WindowStyle Hidden | Out-Null
}

if (-not (Test-ListeningPort 44101)) {
    Start-Process -FilePath $pnpm `
        -ArgumentList '--filter','admin-web','exec','vite','preview','--host','0.0.0.0','--port','44101' `
        -WorkingDirectory $salesRoot `
        -RedirectStandardOutput (Join-Path $logs 'sales-web.log') `
        -RedirectStandardError (Join-Path $logs 'sales-web.error.log') `
        -WindowStyle Hidden | Out-Null
}
