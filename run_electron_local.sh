#!/bin/bash
# Run the Ubuntu desktop Electron client against local backend/services.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8001}"

if [ -z "${DISPLAY:-}" ]; then
    echo "ERROR: DISPLAY is not set. Run this from the local Ubuntu desktop session."
    echo "Example: export DISPLAY=:0 (or use the display number of the active local session)"
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

if [ -e "/dev/blackmagic/io0" ]; then
    echo "[Capture] DeckLink driver node detected: /dev/blackmagic/io0"
    if ! gst-inspect-1.0 decklinkvideosrc >/dev/null 2>&1; then
        echo "WARN: DeckLink hardware exists but the GStreamer decklinkvideosrc plugin is unavailable."
    fi
    echo "[Capture] Preflight: $ROOT_DIR/scripts/decklink_preflight.sh --connection hdmi --mode auto --wait 8"
else
    echo "WARN: /dev/blackmagic/io0 is missing; DeckLink capture will not be available."
fi

if [ "${SURGR1_RUN_CAPTURE_PREFLIGHT:-0}" = "1" ]; then
    echo "[Capture] Running optional HDMI preflight before Electron..."
    "$ROOT_DIR/scripts/decklink_preflight.sh" --connection hdmi --mode auto --wait 6 || true
fi

export ELECTRON_DISABLE_SANDBOX=1
export VITE_DEV_SERVER_URL="${VITE_DEV_SERVER_URL:-http://127.0.0.1:5133}"
export VITE_DEFAULT_SOURCE="${VITE_DEFAULT_SOURCE:-capture}"
export VITE_DEFAULT_STREAM_URL="${VITE_DEFAULT_STREAM_URL:-http://127.0.0.1:9001/stream}"
export VITE_AUTO_OPEN_STREAM="${VITE_AUTO_OPEN_STREAM:-1}"
export VITE_AUTO_CONNECT_CAPTURE="${VITE_AUTO_CONNECT_CAPTURE:-1}"

echo "=========================================="
echo "  Surg-R1 Electron Local Mode"
echo "=========================================="
echo "Backend:        $BACKEND_URL"
echo "Frontend:       $VITE_DEV_SERVER_URL"
echo "Default source: $VITE_DEFAULT_SOURCE"
echo "Auto open:      $VITE_AUTO_OPEN_STREAM"
echo "Auto connect:   $VITE_AUTO_CONNECT_CAPTURE"
echo "DISPLAY:        $DISPLAY"
echo ""

npm run electron:local
