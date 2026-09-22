#!/bin/bash
# Run Video Stream Analyzer Backend

cd "$(dirname "$0")"

PORT=8001

stop_pid() {
    local pid="$1"
    kill -TERM "$pid" 2>/dev/null || return 0
    for _ in {1..40}; do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 0.2
    done
    kill -KILL "$pid" 2>/dev/null || true
}

# 处理 Ctrl+C 信号
cleanup() {
    echo ""
    echo "Stopping backend..."
    stop_pid "$UVICORN_PID"
    wait $UVICORN_PID 2>/dev/null
    echo "Backend stopped."
    exit 0
}
trap cleanup SIGINT SIGTERM

# Kill only the process listening on the port. Plain `lsof -i:$PORT` also
# returns Electron/Vite clients connected to the API and used to terminate the
# whole desktop app during a backend-only restart.
echo "Checking for existing service on port $PORT..."
PID=$(lsof -t -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
if [ -n "$PID" ]; then
    echo "Killing existing process (PID: $PID) on port $PORT..."
    for old_pid in $PID; do stop_pid "$old_pid"; done
    echo "Existing service stopped."
else
    echo "No existing service found on port $PORT."
fi

# Activate Python environment. Prefer the existing conda env on the dev box,
# but allow Ubuntu-local deployments to use a project venv.
if command -v conda >/dev/null 2>&1 && conda env list | awk '{print $1}' | grep -qx "vllm"; then
    eval "$(conda shell.bash hook)"
    conda activate vllm
elif [ -f "$(pwd)/../.venv/bin/activate" ]; then
    source "$(pwd)/../.venv/bin/activate"
elif [ -f "$(pwd)/.venv/bin/activate" ]; then
    source "$(pwd)/.venv/bin/activate"
else
    echo "[Env] WARNING: no conda env 'vllm' or local .venv found; using system python"
fi

# Set environment variables (customize as needed)
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
export SURG_AGENT_ROOT="${SURG_AGENT_ROOT:-$(cd "$(pwd)/../.." && pwd)/surg_agent}"

# Source default expert GPU assignments, while preserving explicit runtime
# overrides supplied by the desktop launcher.
if [ -f "$(pwd)/.env.yolo" ]; then
    RUNTIME_YOLO_DEVICE="${YOLO_DEVICE:-}"
    RUNTIME_PHASE_DEVICE="${PHASE_DEVICE:-}"
    RUNTIME_TRIPLET_DEVICE="${TRIPLET_DEVICE:-}"
    set -a
    source "$(pwd)/.env.yolo"
    set +a
    [ -n "$RUNTIME_YOLO_DEVICE" ] && export YOLO_DEVICE="$RUNTIME_YOLO_DEVICE"
    [ -n "$RUNTIME_PHASE_DEVICE" ] && export PHASE_DEVICE="$RUNTIME_PHASE_DEVICE"
    [ -n "$RUNTIME_TRIPLET_DEVICE" ] && export TRIPLET_DEVICE="$RUNTIME_TRIPLET_DEVICE"
    echo "[Env] Expert devices: YOLO=${YOLO_DEVICE:-config} PHASE=${PHASE_DEVICE:-config} TRIPLET=${TRIPLET_DEVICE:-config} CLIP=${CLIP_DETECTOR_DEVICE:-config}"
fi

# Proxy for Gemini/OpenAI API access (via Clash Verge/mihomo).
# Override CLASH_HTTP_PROXY/CLASH_SOCKS_PROXY if local Clash ports differ.
CLASH_HTTP_PROXY="${CLASH_HTTP_PROXY:-${https_proxy:-http://127.0.0.1:7897}}"
CLASH_SOCKS_PROXY="${CLASH_SOCKS_PROXY:-${all_proxy:-socks5://127.0.0.1:7897}}"
export https_proxy="${https_proxy:-$CLASH_HTTP_PROXY}"
export http_proxy="${http_proxy:-$CLASH_HTTP_PROXY}"
export HTTPS_PROXY="${HTTPS_PROXY:-$https_proxy}"
export HTTP_PROXY="${HTTP_PROXY:-$http_proxy}"
export all_proxy="${all_proxy:-$CLASH_SOCKS_PROXY}"
export ALL_PROXY="${ALL_PROXY:-$all_proxy}"
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1,100.64.0.0/10,192.168.0.0/16}"
export no_proxy="${no_proxy:-$NO_PROXY}"
echo "[Env] Proxy https_proxy=${https_proxy}"

# Run with uvicorn
echo "=========================================="
echo "  Video Stream Analyzer Backend"
echo "=========================================="
echo "API: http://localhost:$PORT"
echo "Docs: http://localhost:$PORT/api/docs"
echo "Press Ctrl+C to stop"
echo ""

if [ "${BACKEND_RELOAD:-0}" = "1" ]; then
    echo "[Mode] Uvicorn reload enabled (BACKEND_RELOAD=1)"
    python -m uvicorn backend.main:app --host 0.0.0.0 --port $PORT --reload --timeout-graceful-shutdown 5 &
else
    echo "[Mode] Uvicorn reload disabled (recommended for MJPEG/SSE long-running tests)"
    python -m uvicorn backend.main:app --host 0.0.0.0 --port $PORT --timeout-graceful-shutdown 5 &
fi
UVICORN_PID=$!
wait $UVICORN_PID
