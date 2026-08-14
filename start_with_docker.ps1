$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$DockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$DockerCli = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$Url = "http://127.0.0.1:8000"

function Wait-DockerEngine {
    Write-Host "正在等待 Docker 引擎启动..."
    $deadline = (Get-Date).AddMinutes(5)
    do {
        Start-Sleep -Seconds 3
        try {
            $version = & $DockerCli version --format '{{.Server.Version}}' 2>$null
            if ($LASTEXITCODE -eq 0 -and $version) {
                Write-Host "Docker 引擎已就绪：$version"
                return
            }
        } catch {
        }
    } while ((Get-Date) -lt $deadline)
    throw "Docker Desktop 启动超时，请手动打开一次 Docker Desktop 后重试。"
}

if (-not (Test-Path $DockerDesktop)) {
    throw "未找到 Docker Desktop，请确认已经安装。"
}

if (-not (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)) {
    Write-Host "正在启动 Docker Desktop..."
    Start-Process -FilePath $DockerDesktop
}

Wait-DockerEngine

Set-Location $Root
$env:PATH = "C:\Program Files\Docker\Docker\resources\bin;" + $env:PATH

Write-Host "正在启动 Qdrant 和 Redis..."
& $DockerCli compose up -d qdrant redis
if ($LASTEXITCODE -ne 0) {
    throw "启动 Qdrant/Redis 失败。"
}

$PortInUse = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $PortInUse) {
    Write-Host "正在启动知识库服务..."
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
    Write-Host "知识库服务已经在运行。"
}

Write-Host "启动完成，正在打开浏览器..."
Start-Process $Url
