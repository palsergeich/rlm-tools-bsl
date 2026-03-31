# Docker Build & Test for rlm-tools-bsl (Windows PowerShell)

$ErrorActionPreference = "Stop"

Write-Host "🐳 Docker Build & Test for rlm-tools-bsl"
Write-Host "=========================================="

# 1. Check Docker is installed
Write-Host "`n[1/5] Checking Docker installation..." -ForegroundColor Yellow
if (-not (docker --version)) {
    Write-Host "✗ Docker not found" -ForegroundColor Red
    exit 1
}
$dockerVersion = docker --version
Write-Host "✓ $dockerVersion" -ForegroundColor Green

# 2. Build image
Write-Host "`n[2/5] Building Docker image..." -ForegroundColor Yellow
docker build -t rlm-tools-bsl:test .
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ Build failed" -ForegroundColor Red
    exit 1
}
Write-Host "✓ Image built successfully" -ForegroundColor Green

# 3. Create test data directory
Write-Host "`n[3/5] Creating test data directory..." -ForegroundColor Yellow
if (-not (Test-Path "test-data")) {
    New-Item -ItemType Directory -Path "test-data" | Out-Null
}
Set-Content -Path "test-data\test.bsl" -Value "" -Encoding UTF8

# 4. Test server startup
Write-Host "`n[4/5] Testing server startup (HTTP mode)..." -ForegroundColor Yellow
Write-Host "Starting container..."

# Get absolute path
$testDataPath = (Get-Item "test-data").FullName

$output = docker run -d `
    -p 9000:9000 `
    -v "${testDataPath}:/data:ro" `
    --name rlm-test `
    rlm-tools-bsl:test `
    --transport streamable-http `
    --host 0.0.0.0 `
    --port 9000 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ Failed to start container" -ForegroundColor Red
    exit 1
}

$containerId = $output[0]
Write-Host "Container ID: $containerId"
Write-Host "Waiting for server to start..."
Start-Sleep -Seconds 3

# Check if container is running
$running = docker ps | Select-String $containerId
if (-not $running) {
    Write-Host "✗ Container exited prematurely" -ForegroundColor Red
    docker logs rlm-test
    docker rm -f rlm-test 2>$null
    exit 1
}

Write-Host "✓ Server started" -ForegroundColor Green

# 5. Test health check
Write-Host "`n[5/5] Testing health check..." -ForegroundColor Yellow
$healthOk = $false
for ($i = 0; $i -lt 10; $i++) {
    try {
        $response = curl.exe -s http://localhost:9000/health -ErrorAction SilentlyContinue
        if ($?) {
            $healthOk = $true
            break
        }
    }
    catch { }
    Start-Sleep -Seconds 1
}

if ($healthOk) {
    Write-Host "✓ Health check passed" -ForegroundColor Green
}
else {
    Write-Host "✗ Health check failed" -ForegroundColor Red
    docker logs rlm-test
    docker rm -f rlm-test 2>$null
    exit 1
}

# Cleanup
Write-Host "`nCleaning up test container..." -ForegroundColor Yellow
docker rm -f rlm-test | Out-Null

Write-Host "`n✅ All tests passed!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Prepare your 1C sources in .\data\ directory"
Write-Host "  2. Run: docker-compose up -d"
Write-Host "  3. Add to your MCP config:"
Write-Host '     {"mcpServers": {"rlm-tools-bsl": {"type": "http", "url": "http://127.0.0.1:9000/mcp"}}}'
Write-Host ""
Write-Host "For more details, see docs/DOCKER.md"
