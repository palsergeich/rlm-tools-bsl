<#
.SYNOPSIS
    Reinstall rlm-tools-bsl Windows service (stop -> uninstall -> rebuild -> install -> start).

.DESCRIPTION
    Use after updating source code (git pull) or fixing bugs.
    Stops the running service, removes it, cleans uv cache,
    reinstalls the package, and re-registers the service.

    Must be run as Administrator.

.PARAMETER BindHost
    Host to bind the HTTP server (default: 127.0.0.1)

.PARAMETER Port
    Port for the HTTP server (default: 9000)

.PARAMETER EnvFile
    Path to .env file. If omitted, looks for .env in the script directory.

.PARAMETER NativeTls
    Use system TLS certificates instead of uv's built-in ones.

.EXAMPLE
    PowerShell -ExecutionPolicy Bypass -File .\reinstall-service.ps1

.EXAMPLE
    PowerShell -ExecutionPolicy Bypass -File .\reinstall-service.ps1 -Port 9001 -EnvFile "C:\Users\me\.env"
#>

param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 9000,
    [string]$EnvFile = "",
    [switch]$NativeTls
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# --- Check Administrator ---
$currentPrincipal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Run this script as Administrator (right-click -> Run as Administrator)."
    exit 1
}

# --- Check uv ---
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv not found. Install it first."
    exit 1
}

Write-Host ""
Write-Host "=== Step 1: Stop & uninstall service ===" -ForegroundColor Cyan
try {
    & rlm-tools-bsl service stop 2>$null
    Write-Host "Service stopped."
} catch {
    Write-Host "Service was not running (OK)."
}
try {
    & rlm-tools-bsl service uninstall 2>$null
    Write-Host "Service uninstalled."
} catch {
    Write-Host "Service was not installed (OK)."
}

Write-Host ""
Write-Host "=== Step 2: Clean stale installs & rebuild ===" -ForegroundColor Cyan

# Remove stale dist-info and .pth from user site-packages (Roaming)
# and dangling ~*dist-info from global site-packages.
# These leftovers can shadow the correct version after reinstall.
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
$exePath = if ($pythonCmd) { $pythonCmd.Source } else { "python" }
$globalSitePackages = & $exePath -c "import site; print(site.getsitepackages()[0])" 2>$null
$userSitePackages = & $exePath -c "import site; print(site.getusersitepackages())" 2>$null

foreach ($sp in @($globalSitePackages, $userSitePackages)) {
    if (-not $sp -or -not (Test-Path $sp)) { continue }
    # Remove dangling ~*rlm* dirs (failed uninstalls) and old dist-info
    Get-ChildItem -Path $sp -Directory -Filter "*rlm_tools_bsl*" -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "  Removing stale: $($_.FullName)" -ForegroundColor Yellow
        Remove-Item -Recurse -Force $_.FullName
    }
    # Remove stale .pth files
    Get-ChildItem -Path $sp -File -Filter "*rlm_tools_bsl*" -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "  Removing stale: $($_.FullName)" -ForegroundColor Yellow
        Remove-Item -Force $_.FullName
    }
}

# Remove stale dist/ from source tree (can confuse uv)
$distDir = Join-Path $PSScriptRoot "dist"
if (Test-Path $distDir) {
    Write-Host "  Removing stale dist/: $distDir" -ForegroundColor Yellow
    Remove-Item -Recurse -Force $distDir
}

& uv cache clean rlm-tools-bsl
$uvInstallArgs = @("tool", "install", "${PSScriptRoot}[service]", "--force", "--reinstall")
if ($NativeTls) { $uvInstallArgs += "--native-tls" }
& uv @uvInstallArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Build failed."
    exit 1
}

# Ensure rlm-tools-bsl is in PATH for this session
if (-not (Get-Command rlm-tools-bsl -ErrorAction SilentlyContinue)) {
    $uvBinDir = (& uv tool dir --bin 2>$null)
    if ($uvBinDir -and (Test-Path $uvBinDir)) {
        $env:PATH = "$uvBinDir;$env:PATH"
    }
}

Write-Host ""
Write-Host "=== Step 3: Install & start service ===" -ForegroundColor Cyan

$installArgs = @("service", "install", "--host", $BindHost, "--port", "$Port")

if ($EnvFile) {
    $installArgs += @("--env", $EnvFile)
} elseif (Test-Path (Join-Path $PSScriptRoot ".env")) {
    $resolvedEnv = (Resolve-Path (Join-Path $PSScriptRoot ".env")).Path
    Write-Host "Found .env: $resolvedEnv"
    $installArgs += @("--env", $resolvedEnv)
}

& rlm-tools-bsl @installArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Service registration failed."
    exit 1
}

& rlm-tools-bsl service start
if ($LASTEXITCODE -ne 0) {
    Write-Error "Service start failed."
    exit 1
}

Write-Host ""
Write-Host "=== Step 4: Verify ===" -ForegroundColor Cyan
Write-Host "Waiting for server to start (watchdog may need up to 10s)..."

$url = "http://${BindHost}:${Port}/mcp"
$ok = $false
for ($attempt = 1; $attempt -le 4; $attempt++) {
    Start-Sleep -Seconds 3
    try {
        $response = Invoke-WebRequest -Uri $url -Method GET -TimeoutSec 5 -ErrorAction Stop
        Write-Host "Server responding (HTTP $($response.StatusCode)). OK." -ForegroundColor Green
        $ok = $true
        break
    } catch {
        if ($null -ne $_.Exception.Response) {
            $code = [int]$_.Exception.Response.StatusCode
            Write-Host "Server responding (HTTP $code). OK." -ForegroundColor Green
            $ok = $true
            break
        }
        if ($attempt -lt 4) {
            Write-Host "  Attempt $attempt/4: not ready yet, retrying..." -ForegroundColor Yellow
        }
    }
}

if (-not $ok) {
    Write-Warning "Server not responding at $url after 4 attempts"
    Write-Warning "Check: rlm-tools-bsl service status"
    Write-Warning "Logs:  ~/.config/rlm-tools-bsl/logs/server.log"
    exit 1
}

Write-Host ""
Write-Host "Done! Service reinstalled and running at $url" -ForegroundColor Green
Write-Host "Version: $(& rlm-tools-bsl --version 2>&1)"
Write-Host "Logs:    ~/.config/rlm-tools-bsl/logs/server.log"
