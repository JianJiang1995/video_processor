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
PID=$(lsof -t -i:$PORT 2>/dev/null)
if [ -n "$PID" ]; then
    echo "Killing existing process (PID: $PID) on port $PORT..."
    kill -9 $PID 2>/dev/null
    sleep 1
    echo "Existing service stopped."
else
    echo "No existing service found on port $PORT."
fi

# Activate conda environment
eval "$(conda shell.bash hook)"
conda activate vllm

# Set environment variables (customize as needed)
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Run with uvicorn
echo "=========================================="
echo "  Video Stream Analyzer Backend"
echo "=========================================="
echo "API: http://localhost:$PORT"
echo "Docs: http://localhost:$PORT/api/docs"
echo "Press Ctrl+C to stop"
echo ""

python -m uvicorn backend.main:app --host 0.0.0.0 --port $PORT --reload &
UVICORN_PID=$!
wait $UVICORN_PID

