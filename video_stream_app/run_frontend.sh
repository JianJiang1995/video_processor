#!/bin/bash
# Run Video Stream Analyzer Frontend

cd "$(dirname "$0")/frontend"

# Default port
PORT=5174

# 处理 Ctrl+C 信号
cleanup() {
    echo ""
    echo "Stopping frontend..."
    kill -TERM $NPM_PID 2>/dev/null
    wait $NPM_PID 2>/dev/null
    echo "Frontend stopped."
    exit 0
}
trap cleanup SIGINT SIGTERM

# Kill any existing process on the port
kill_existing_process() {
    local port=$1
    local pid=$(lsof -t -i:$port 2>/dev/null)
    if [ -n "$pid" ]; then
        echo "正在关闭端口 $port 上的现有服务 (PID: $pid)..."
        kill -9 $pid 2>/dev/null
        sleep 1
        echo "已关闭。"
    fi
}

# Kill processes on potential ports (5174-5177)
for p in 5174; do
    kill_existing_process $p
done

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    npm install
fi

echo "=========================================="
echo "  Video Stream Analyzer Frontend"
echo "=========================================="
echo "App: http://localhost:$PORT"
echo "Press Ctrl+C to stop"
echo ""

npm run dev &
NPM_PID=$!
wait $NPM_PID
