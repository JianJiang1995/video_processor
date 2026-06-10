#!/bin/bash
# Run the Ubuntu desktop Electron client against local backend/services.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8001}"

if [ -z "${DISPLAY:-}" ]; then
    echo "ERROR: DISPLAY is not set. Run this on the Ubuntu desktop session, or connect with VNC/NoMachine/X11 forwarding."
    echo "Example on a physical desktop: export DISPLAY=:0"
    exit 1
fi

cd "$FRONTEND_DIR"

if [ ! -d "node_modules" ]; then
    echo "Installing frontend dependencies..."
    npm install
fi

if command -v curl >/dev/null 2>&1; then
    if ! curl -fsS "$BACKEND_URL/api/health" >/dev/null 2>&1; then
        echo "WARN: backend is not healthy at $BACKEND_URL."
        echo "      Start it in another terminal with: cd $ROOT_DIR && bash run_backend.sh"
    fi
fi

export ELECTRON_DISABLE_SANDBOX=1
export VITE_DEV_SERVER_URL="${VITE_DEV_SERVER_URL:-http://127.0.0.1:5133}"
export VITE_DEFAULT_SOURCE="${VITE_DEFAULT_SOURCE:-capture}"
export VITE_DEFAULT_STREAM_URL="${VITE_DEFAULT_STREAM_URL:-http://127.0.0.1:9001/stream}"

echo "=========================================="
echo "  Surg-R1 Electron Local Mode"
echo "=========================================="
echo "Backend:        $BACKEND_URL"
echo "Frontend:       $VITE_DEV_SERVER_URL"
echo "Default source: $VITE_DEFAULT_SOURCE"
echo "DISPLAY:        $DISPLAY"
echo ""

npm run electron:local
