#!/bin/bash
# Start All Video Stream Analyzer Services
# Runs backend and frontend, then tails backend log

cd "$(dirname "$0")"

echo "=========================================="
echo "  Video Stream Analyzer - Service Startup"
echo "=========================================="
echo ""

# Create logs directory
mkdir -p logs

# Start Backend in background
echo "[1/2] Starting Backend..."
bash run_backend.sh > logs/backend.log 2>&1 &
BACKEND_PID=$!
echo "  Backend started (PID: $BACKEND_PID)"
echo "  Log: logs/backend.log"
echo ""

# Wait a moment for backend to initialize
sleep 2

# Start Frontend in background
echo "[2/2] Starting Frontend..."
bash run_frontend.sh > logs/frontend.log 2>&1 &
FRONTEND_PID=$!
echo "  Frontend started (PID: $FRONTEND_PID)"
echo "  Log: logs/frontend.log"
echo ""

echo "=========================================="
echo "  Services Started"
echo "=========================================="
echo "  Backend PID:  $BACKEND_PID"
echo "  Frontend PID: $FRONTEND_PID"
echo ""
echo "  Press Ctrl+C to stop viewing logs"
echo "  (Services will continue running in background)"
echo "=========================================="
echo ""
echo ">>> Tailing Backend Log <<<"
echo ""

# Tail backend log (keeps script running)
tail -f logs/backend.log
