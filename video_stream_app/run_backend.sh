#!/bin/bash
# Run Video Stream Analyzer Backend

cd "$(dirname "$0")"

PORT=8001

# 处理 Ctrl+C 信号
cleanup() {
    echo ""
    echo "Stopping backend..."
    kill -TERM $UVICORN_PID 2>/dev/null
    wait $UVICORN_PID 2>/dev/null
    echo "Backend stopped."
    exit 0
}
trap cleanup SIGINT SIGTERM

# Kill existing process on the port
echo "Checking for existing service on port $PORT..."
PID=$(lsof -t -i:$PORT 2>/dev/null || true)
if [ -n "$PID" ]; then
    echo "Killing existing process (PID: $PID) on port $PORT..."
    kill -9 $PID 2>/dev/null
    sleep 1
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

# Source YOLO GPU assignment from start_surgr1_yolo.sh (if exists)
if [ -f "$(pwd)/.env.yolo" ]; then
    set -a
    source "$(pwd)/.env.yolo"
    set +a
    echo "[Env] Loaded YOLO_DEVICE=${YOLO_DEVICE}"
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
    python -m uvicorn backend.main:app --host 0.0.0.0 --port $PORT --reload &
else
    echo "[Mode] Uvicorn reload disabled (recommended for MJPEG/SSE long-running tests)"
    python -m uvicorn backend.main:app --host 0.0.0.0 --port $PORT &
fi
UVICORN_PID=$!
wait $UVICORN_PID
