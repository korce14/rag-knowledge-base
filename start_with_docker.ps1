$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$DockerCli = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$Url = "http://127.0.0.1:8000"

function Wait-DockerEngine {
    Write-Host "Waiting for Docker engine..."
    $deadline = (Get-Date).AddMinutes(5)
    do {
        Start-Sleep -Seconds 3
        try {
            $version = & $DockerCli version --format '{{.Server.Version}}' 2>$null
            if ($LASTEXITCODE -eq 0 -and $version) {
                Write-Host "Docker engine ready: $version"
                return
            }
        } catch {
        }
    } while ((Get-Date) -lt $deadline)
    throw "Docker Desktop startup timed out. Open Docker Desktop once and retry."
}

if (-not (Test-Path $DockerDesktop)) {
    throw "Docker Desktop not found."
}

if (-not (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)) {
    Write-Host "Starting Docker Desktop..."
    Start-Process -FilePath $DockerDesktop
}

Wait-DockerEngine

Set-Location $Root
$env:PATH = "C:\Program Files\Docker\Docker\resources\bin;" + $env:PATH

Write-Host "Starting Qdrant and Redis..."
& $DockerCli compose up -d qdrant redis
if ($LASTEXITCODE -ne 0) {
    throw "Failed to start Qdrant/Redis."
}

$PortInUse = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $PortInUse) {
    Write-Host "Starting knowledge base service..."
    $Python = (Get-Command python).Source
    $Process = Start-Process -FilePath $Python -ArgumentList @(
        "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", "8000"
    ) -WorkingDirectory $Root -WindowStyle Hidden -PassThru

    $deadline = (Get-Date).AddMinutes(2)
    do {
        Start-Sleep -Seconds 1
        try {
            $health = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8000/health" -TimeoutSec 2
            if ($health.StatusCode -eq 200) {
                break
            }
        } catch {
        }
    } while ((Get-Date) -lt $deadline)
} else {
    Write-Host "Knowledge base service is already running."
}

Write-Host "Opening browser..."
Start-Process $Url
