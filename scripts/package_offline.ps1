param(
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $ProjectRoot "docker-compose.yml"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$OutputDir = Join-Path $ProjectRoot "dist\agentscope-offline-$Stamp"
$TarFile = Join-Path $OutputDir "agentscope-images.tar"

function Invoke-Docker {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed with exit code ${LASTEXITCODE}: docker $($Arguments -join ' ')"
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not installed or is not available in PATH."
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

if (-not $SkipBuild) {
    Invoke-Docker @("compose", "-f", $ComposeFile, "build", "agentscope", "web-ui")
}
try {
    Invoke-Docker @("image", "inspect", "redis:7-alpine")
}
catch {
    Invoke-Docker @("pull", "redis:7-alpine")
}
Invoke-Docker @(
    "save"
    "--output"
    $TarFile
    "agentscope-intranet:latest"
    "agentscope-web-ui:latest"
    "redis:7-alpine"
)

Copy-Item $ComposeFile (Join-Path $OutputDir "docker-compose.yml")
Copy-Item (Join-Path $ProjectRoot ".env.example") (Join-Path $OutputDir ".env.example")
Copy-Item (Join-Path $ProjectRoot "OPS_DEPLOYMENT.md") (Join-Path $OutputDir "OPS_DEPLOYMENT.md")

$Hash = (Get-FileHash -Algorithm SHA256 $TarFile).Hash
@{
    generated_at = (Get-Date).ToString("o")
    images = @(
        "agentscope-intranet:latest"
        "agentscope-web-ui:latest"
        "redis:7-alpine"
    )
    image_archive = "agentscope-images.tar"
    sha256 = $Hash
} | ConvertTo-Json | Set-Content (Join-Path $OutputDir "manifest.json") -Encoding UTF8

Write-Host "Offline package created: $OutputDir"
Write-Host "Image archive SHA256: $Hash"
