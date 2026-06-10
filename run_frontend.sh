#!/bin/bash
# Run Video Stream Analyzer Frontend

cd "$(dirname "$0")/frontend"

# Default port
PORT=5133

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

# Kill processes on potential ports (5133)
for p in 5133; do
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

# Launch Electron + Vite together (via electron:dev script in package.json)
# Electron requires X display for GUI - ensure $DISPLAY is set (X11 forwarding)
if [ -z "$DISPLAY" ]; then
    echo "⚠️  \$DISPLAY not set. Electron requires X display."
    echo "   For MobaXterm: enable 'X11-Forwarding' in SSH session settings."
    echo "   Falling back to browser-only mode (Vite only)..."
    npm run dev &
    NPM_PID=$!
else
    echo "✓ DISPLAY=$DISPLAY (X11 forwarding OK)"
    # Electron needs --no-sandbox on some Linux envs without user namespaces
    export ELECTRON_DISABLE_SANDBOX=1
    npm run electron:dev &
    NPM_PID=$!
fi

wait $NPM_PID
