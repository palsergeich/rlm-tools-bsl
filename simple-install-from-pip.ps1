<#
.SYNOPSIS
    Install or update rlm-tools-bsl from PyPI as a Windows service (HTTP MCP server).

.DESCRIPTION
    Downloads and installs rlm-tools-bsl from PyPI, registers it as a Windows
    service, starts the server, and verifies the health endpoint.

    If the service is already installed, stops it, upgrades the package,
    and restarts.

    Prerequisites (install before running):
      - Python 3.10+  https://python.org  (check "Add Python to PATH")
      - uv            https://docs.astral.sh/uv/

    Optional LLM env vars (for llm_query helper):
      Set system environment variables or pass --EnvFile:
        RLM_LLM_BASE_URL, RLM_LLM_API_KEY, RLM_LLM_MODEL  (OpenAI-compatible)
        ANTHROPIC_API_KEY                                    (Anthropic API)
      Without LLM keys all core features still work (find_module, grep, xml parsing).

    Must be run as Administrator.

.PARAMETER BindHost
    Host to bind the HTTP server (default: 127.0.0.1)

.PARAMETER Port
    Port for the HTTP server (default: 9000)

.PARAMETER EnvFile
    Path to .env file. If omitted, looks for .env in the script directory.

.PARAMETER NativeTls
    Use system TLS certificates instead of uv's built-in ones.
    Required in corporate networks where a proxy/firewall replaces TLS certificates.

.EXAMPLE
    PowerShell -ExecutionPolicy Bypass -File .\simple-install-from-pip.ps1

.EXAMPLE
    PowerShell -ExecutionPolicy Bypass -File .\simple-install-from-pip.ps1 -Port 9001 -EnvFile "C:\Users\me\.env"

.EXAMPLE
    PowerShell -ExecutionPolicy Bypass -File .\simple-install-from-pip.ps1 -NativeTls
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
$isAdmin = $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "Run this script as Administrator (right-click -> Run as Administrator)."
    exit 1
}

# --- Check uv ---
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv not found. Install it:`n  PowerShell: irm https://astral.sh/uv/install.ps1 | iex`nThen re-run this script."
    exit 1
}

# --- Check Python ---
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found. Install Python 3.10+ from https://python.org (check 'Add Python to PATH')."
    exit 1
}

# --- Step 1: Stop and uninstall existing service if running ---
$isUpdate = $false
if (Get-Command rlm-tools-bsl -ErrorAction SilentlyContinue) {
    $isUpdate = $true
    Write-Host ""
    Write-Host "=== Existing installation detected -- upgrading ===" -ForegroundColor Cyan
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
}

Write-Host ""
if ($isUpdate) {
    Write-Host "=== Step 1: Upgrade rlm-tools-bsl from PyPI ===" -ForegroundColor Cyan
} else {
    Write-Host "=== Step 1: Install rlm-tools-bsl from PyPI ===" -ForegroundColor Cyan
}

$uvInstallArgs = @("tool", "install", "rlm-tools-bsl[service]", "--force", "--upgrade")
if ($NativeTls) { $uvInstallArgs += "--native-tls" }
& uv @uvInstallArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Installation failed. If you see TLS certificate errors, re-run with -NativeTls flag."
    exit 1
}

# Also update the global Python that the Windows service uses.
# shutil.which() in _service_win.py finds this exe, not the uv tool one.
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
$globalExe = if ($pythonCmd) { $pythonCmd.Source } else { "" }
$globalPython = if ($globalExe) { & $globalExe -c "import sys; print(sys.executable)" 2>$null } else { "" }
if ($globalPython -and (Test-Path $globalPython)) {
    Write-Host "Updating global Python package ($globalPython)..." -ForegroundColor Cyan
    $uvPipArgs = @("pip", "install", "rlm-tools-bsl", "--upgrade", "--python", $globalPython)
    if ($NativeTls) { $uvPipArgs += "--native-tls" }
    & uv @uvPipArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Global Python update failed - service may run an older version."
    }
}

# Ensure rlm-tools-bsl is in PATH for this session
if (-not (Get-Command rlm-tools-bsl -ErrorAction SilentlyContinue)) {
    Write-Host "Adding uv tool bin directory to PATH..." -ForegroundColor Yellow
    $uvBinDir = (& uv tool dir --bin 2>$null)
    if ($uvBinDir -and (Test-Path $uvBinDir)) {
        $env:PATH = "$uvBinDir;$env:PATH"
    }
    & uv tool update-shell 2>$null
}

Write-Host ""
Write-Host "=== Step 2: Register service ===" -ForegroundColor Cyan

$installArgs = @("service", "install", "--host", $BindHost, "--port", "$Port")

if ($EnvFile) {
    $installArgs += @("--env", $EnvFile)
} elseif (Test-Path (Join-Path $PSScriptRoot ".env")) {
    $resolvedEnv = (Resolve-Path (Join-Path $PSScriptRoot ".env")).Path
    Write-Host "Found .env: $resolvedEnv"
    $installArgs += @("--env", $resolvedEnv)
} else {
    Write-Host "No .env found - service will start without it (set LLM keys as system env vars if needed)."
}

& rlm-tools-bsl @installArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Service registration failed."
    exit 1
}

Write-Host ""
Write-Host "=== Step 3: Start service ===" -ForegroundColor Cyan
rlm-tools-bsl service start
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
        Write-Host "Server is responding (HTTP $($response.StatusCode)). OK." -ForegroundColor Green
        $ok = $true
        break
    } catch {
        if ($null -ne $_.Exception.Response) {
            $code = [int]$_.Exception.Response.StatusCode
            Write-Host "Server is responding (HTTP $code). OK." -ForegroundColor Green
            $ok = $true
            break
        }
        if ($attempt -lt 4) {
            Write-Host "  Attempt $attempt/4: not ready yet, retrying..." -ForegroundColor Yellow
        }
    }
}

if (-not $ok) {
    Write-Warning "Server is not responding at $url after 4 attempts"
    Write-Warning "Check status: rlm-tools-bsl service status"
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Done! HTTP MCP server is running." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Version:  $(& rlm-tools-bsl --version 2>&1)"
Write-Host "Endpoint: $url"
Write-Host ""
Write-Host "Add to .claude.json / mcp.json:"
Write-Host ""
Write-Host "{`n  `"mcpServers`": {`n    `"rlm-tools-bsl`": {`n      `"type`": `"http`",`n      `"url`": `"$url`"`n    }`n  }`n}"
Write-Host ""
Write-Host "Service management:"
Write-Host "  rlm-tools-bsl service status"
Write-Host "  rlm-tools-bsl service stop"
Write-Host "  rlm-tools-bsl service start"
Write-Host "  rlm-tools-bsl service uninstall"
Write-Host ""
Write-Host "Update to latest version:"
Write-Host "  PowerShell -ExecutionPolicy Bypass -File .\simple-install-from-pip.ps1"
