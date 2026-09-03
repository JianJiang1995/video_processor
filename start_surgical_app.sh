#!/bin/bash

# Start the complete local backend + Electron application with one command.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8001}"
LOG_DIR="$ROOT_DIR/logs"
BACKEND_LOG="$LOG_DIR/onsite_backend.log"
RUN_PREFLIGHT=0
STARTED_BACKEND=0
BACKEND_PID=""

usage() {
    echo "Usage: $0 [--preflight]"
    echo "  --preflight  Test DeckLink HDMI/auto before starting the full app."
}

for argument in "$@"; do
    case "$argument" in
        --preflight) RUN_PREFLIGHT=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $argument"; usage; exit 2 ;;
    esac
done

cleanup() {
    if [ "$STARTED_BACKEND" = "1" ] && [ -n "$BACKEND_PID" ]; then
        STARTED_BACKEND=0
        echo "Stopping backend started by this launcher..."
        kill -INT -- "-$BACKEND_PID" 2>/dev/null || true
        for _ in $(seq 1 20); do
            if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
                break
            fi
            sleep 0.25
        done
        if kill -0 "$BACKEND_PID" 2>/dev/null; then
            kill -TERM -- "-$BACKEND_PID" 2>/dev/null || true
        fi
        wait "$BACKEND_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    echo "ERROR: run this script from a terminal on the local Ubuntu desktop."
    exit 3
fi

mkdir -p "$LOG_DIR"

echo "=========================================="
echo "  SurgR1 Local Application Launcher"
echo "=========================================="

if [ "$RUN_PREFLIGHT" = "1" ]; then
    echo "Running DeckLink HDMI/auto preflight..."
    "$ROOT_DIR/scripts/decklink_preflight.sh" --connection hdmi --mode auto --wait 6 || {
        echo "WARN: no supported input frame was detected."
        echo "      The app will still start and wait for hot-plugged input."
    }
fi

if command -v curl >/dev/null 2>&1 && curl -fsS "$BACKEND_URL/api/health" >/dev/null 2>&1; then
    echo "[OK] Reusing healthy backend at $BACKEND_URL"
else
    echo "Starting backend; log: $BACKEND_LOG"
    setsid bash "$ROOT_DIR/run_backend.sh" >"$BACKEND_LOG" 2>&1 &
    BACKEND_PID=$!
    STARTED_BACKEND=1

    backend_ready=0
    for _ in $(seq 1 60); do
        if curl -fsS "$BACKEND_URL/api/health" >/dev/null 2>&1; then
            backend_ready=1
            break
        fi
        if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
            break
        fi
        sleep 1
    done

    if [ "$backend_ready" != "1" ]; then
        echo "ERROR: backend did not become ready. Last log lines:"
        tail -n 30 "$BACKEND_LOG" 2>/dev/null || true
        exit 4
    fi
    echo "[OK] Backend is healthy at $BACKEND_URL"
fi

echo "Starting local Electron application..."
bash "$ROOT_DIR/run_electron_local.sh"
