#!/usr/bin/env bash
# rlm-tools-bsl -- quick install as a systemd --user service
#
# Prerequisites:
#   Python 3.10+  https://python.org
#   uv            https://docs.astral.sh/uv/
#
# Optional LLM env vars (for llm_query helper):
#   Create .env next to this script, or set environment variables:
#     RLM_LLM_BASE_URL, RLM_LLM_API_KEY, RLM_LLM_MODEL  (OpenAI-compatible)
#     ANTHROPIC_API_KEY                                    (Anthropic API)
#   Without LLM keys all core features still work (find_module, grep, xml parsing).
#
# Usage:
#   ./simple-install.sh                    # auto-detect .env in script dir
#   ./simple-install.sh /path/to/.env      # explicit .env path
#   RLM_PORT=3000 ./simple-install.sh      # custom port
#   UV_NATIVE_TLS=true ./simple-install.sh # corporate proxy with TLS replacement
#
# After install, to enable autostart without login:
#   loginctl enable-linger $USER

set -euo pipefail

BIND_HOST="${RLM_HOST:-127.0.0.1}"
PORT="${RLM_PORT:-9000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_ARG=""

# --- Detect .env ---
if [ -n "${1:-}" ]; then
    ENV_ARG="--env $1"
    echo "Using .env: $1"
elif [ -f "$SCRIPT_DIR/.env" ]; then
    ENV_ARG="--env $SCRIPT_DIR/.env"
    echo "Found .env: $SCRIPT_DIR/.env"
else
    echo "No .env found - service will start without it."
    echo "(Set LLM keys as system env vars if needed)"
fi

# --- Check uv ---
if ! command -v uv &>/dev/null; then
    echo "ERROR: uv not found. Install it:"
    echo ""
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo ""
    exit 1
fi

# --- Check Python ---
if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
    echo "ERROR: Python not found. Install Python 3.10+ from https://python.org"
    exit 1
fi

# --- Step 1: Install ---
echo ""
echo "=== Step 1: Install rlm-tools-bsl ==="
UV_EXTRA_ARGS=()
if [ "${UV_NATIVE_TLS:-}" = "true" ]; then
    UV_EXTRA_ARGS+=("--native-tls")
fi
uv tool install "${SCRIPT_DIR}[service]" --force "${UV_EXTRA_ARGS[@]}"

# Ensure rlm-tools-bsl is in PATH for this session
if ! command -v rlm-tools-bsl &>/dev/null; then
    echo "Adding uv tool bin directory to PATH..."
    UV_BIN_DIR="$(uv tool dir --bin 2>/dev/null || true)"
    if [ -n "$UV_BIN_DIR" ] && [ -d "$UV_BIN_DIR" ]; then
        export PATH="$UV_BIN_DIR:$PATH"
    fi
    uv tool update-shell 2>/dev/null || true
fi

# --- Step 2: Register service ---
echo ""
echo "=== Step 2: Register service ==="
# shellcheck disable=SC2086
rlm-tools-bsl service install --host "$BIND_HOST" --port "$PORT" $ENV_ARG

# --- Step 3: Start ---
echo ""
echo "=== Step 3: Start service ==="
rlm-tools-bsl service start

# --- Step 4: Verify ---
echo ""
echo "=== Step 4: Verify ==="
echo "Waiting for server to start..."
sleep 3

URL="http://${BIND_HOST}:${PORT}/mcp"
echo "Checking $URL ..."

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$URL" 2>/dev/null || true)

if [ -n "$HTTP_CODE" ] && [ "$HTTP_CODE" != "000" ]; then
    echo "Server is responding (HTTP $HTTP_CODE). OK."
else
    echo "WARN: Server is not responding at $URL"
    echo "Check status: rlm-tools-bsl service status"
    exit 1
fi

# --- Done ---
echo ""
echo "========================================"
echo " Done! HTTP MCP server is running."
echo "========================================"
echo ""
echo "Endpoint: $URL"
echo ""
echo "Add to .claude.json / mcp.json:"
echo ""
cat <<EOF
{
  "mcpServers": {
    "rlm-tools-bsl": {
      "type": "http",
      "url": "$URL"
    }
  }
}
EOF
echo ""
echo "To enable autostart without login: loginctl enable-linger \$USER"
echo ""
echo "Service management:"
echo "  rlm-tools-bsl service status"
echo "  rlm-tools-bsl service stop"
echo "  rlm-tools-bsl service start"
echo "  rlm-tools-bsl service uninstall"
