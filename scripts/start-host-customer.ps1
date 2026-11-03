$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root 'host-runtime'

foreach ($line in Get-Content (Join-Path $root '.env')) {
    if ($line -match '^([^#=\s]+)=(.*)$') {
        Set-Item -Path ("Env:" + $matches[1]) -Value $matches[2]
    }
}

$env:REDIS_HOST = '127.0.0.1'
$env:REDIS_PORT = '6379'
$env:AGENTSCOPE_PORT = '8001'
$env:UVICORN_RELOAD = 'false'
$env:QDRANT_PATH = Join-Path $runtime 'customer-qdrant'
$env:LONGXIN_DATA_DIR = Join-Path $runtime 'customer-longxin'
$env:LONGXIN_UPGRADE_DIR = Join-Path $runtime 'customer-longxin'
$env:PYTHONPATH = (Join-Path $root 'src') + ';' + (Join-Path $root 'examples/agent_service')
$env:PLAYWRIGHT_MCP_COMMAND = 'npx'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $runtime 'playwright'
$env:NO_PROXY = '127.0.0.1,localhost,192.168.31.197'
$env:no_proxy = $env:NO_PROXY

$python = Join-Path $root '.host-venv/Scripts/python.exe'
$stdout = Join-Path $runtime 'logs/customer-api.log'
$stderr = Join-Path $runtime 'logs/customer-api.error.log'
Start-Process -FilePath $python `
    -ArgumentList 'main.py' `
    -WorkingDirectory (Join-Path $root 'examples/agent_service') `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden | Out-Null
