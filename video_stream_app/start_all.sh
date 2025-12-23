#!/bin/bash
# Start All Video Stream Analyzer Services
# 
# Architecture:
#   1. SurgR1 Service (port 9003): 手术图像分析
#   2. GLM Service (port 8000): 时序总结
#   3. Backend (port 8001): FastAPI, video processing
#   4. Frontend (port 5176): Vue.js UI
#   5. SAM3 Service (port 9004): 分割（可选）
#   6. TTS Service (port 50000): 语音合成（可选）

cd "$(dirname "$0")"

echo "=========================================="
echo "  Video Stream Analyzer - Service Startup"
echo "=========================================="
echo ""

# Load config
if [ -f "config.json" ]; then
    SURGR1_PORT=$(python3 -c "import json; print(json.load(open('config.json'))['services']['surgr1']['port'])" 2>/dev/null || echo "9003")
    GLM_PORT=$(python3 -c "import json; print(json.load(open('config.json'))['services']['glm']['port'])" 2>/dev/null || echo "8000")
    SAM3_PORT=$(python3 -c "import json; print(json.load(open('config.json'))['services']['sam3']['port'])" 2>/dev/null || echo "9004")
    TTS_PORT=$(python3 -c "import json; print(json.load(open('config.json'))['services']['tts']['port'])" 2>/dev/null || echo "50000")
    BACKEND_PORT=$(python3 -c "import json; print(json.load(open('config.json'))['services']['backend']['port'])" 2>/dev/null || echo "8001")
    FRONTEND_PORT=$(python3 -c "import json; print(json.load(open('config.json'))['services']['frontend']['port'])" 2>/dev/null || echo "5176")
else
    SURGR1_PORT=9003
    GLM_PORT=8000
    SAM3_PORT=9004
    TTS_PORT=50000
    BACKEND_PORT=8001
    FRONTEND_PORT=5176
fi

echo "Ports:"
echo "  SurgR1 (核心):  $SURGR1_PORT"
echo "  GLM (核心):     $GLM_PORT"
echo "  Backend:        $BACKEND_PORT"
echo "  Frontend:       $FRONTEND_PORT"
echo "  SAM3 (可选):    $SAM3_PORT"
echo "  TTS (可选):     $TTS_PORT"
echo ""

# Function to check if a service is LISTENING on the port
check_port() {
    lsof -i :$1 -sTCP:LISTEN > /dev/null 2>&1
    return $?
}

# Create logs directory
mkdir -p logs

# Check external services (SurgR1, GLM, SAM3, TTS)
echo "[1/4] External Services Status..."
echo ""

echo "  🔬 SurgR1 (port $SURGR1_PORT):"
if check_port $SURGR1_PORT; then
    echo "     ✓ Running"
else
    echo "     ✗ Not running - Start with: cd ../SurgR1_api && python main.py"
fi

echo "  🤖 GLM (port $GLM_PORT):"
if check_port $GLM_PORT; then
    echo "     ✓ Running"
else
    echo "     ✗ Not running - Start with: cd ../glm_api && bash start.sh"
fi

echo "  🎯 SAM3 (port $SAM3_PORT):"
if check_port $SAM3_PORT; then
    echo "     ✓ Running"
else
    echo "     ○ Not running (optional)"
fi

echo "  🔊 TTS (port $TTS_PORT):"
if check_port $TTS_PORT; then
    echo "     ✓ Running"
else
    echo "     ○ Not running (optional)"
fi
echo ""

# Start Backend
echo "[2/4] Backend API..."
if check_port $BACKEND_PORT; then
    echo "  ✓ Already running on port $BACKEND_PORT"
else
    # Check critical dependencies before starting
    echo "  Checking dependencies..."
    MISSING_DEPS=""
    python3 -c "import pymysql" 2>/dev/null || MISSING_DEPS="$MISSING_DEPS pymysql"
    python3 -c "import fastapi" 2>/dev/null || MISSING_DEPS="$MISSING_DEPS fastapi"
    python3 -c "import uvicorn" 2>/dev/null || MISSING_DEPS="$MISSING_DEPS uvicorn"
    python3 -c "import sqlalchemy" 2>/dev/null || MISSING_DEPS="$MISSING_DEPS sqlalchemy"
    
    if [ -n "$MISSING_DEPS" ]; then
        echo "  ⚠️  Missing dependencies:$MISSING_DEPS"
        echo "  Installing from requirements.txt..."
        pip install -q -r requirements.txt 2>/dev/null
        if [ $? -ne 0 ]; then
            echo "  ✗ Failed to install dependencies. Run: pip install -r requirements.txt"
            echo ""
        fi
    fi
    
    echo "  Starting..."
    cd "$(dirname "$0")"
    nohup python -m uvicorn backend.main:app --host 0.0.0.0 --port $BACKEND_PORT > logs/backend.log 2>&1 &
    BACKEND_PID=$!
    sleep 3
    if check_port $BACKEND_PORT; then
        echo "  ✓ Started (PID: $BACKEND_PID)"
    else
        echo "  ✗ Failed to start. Check logs/backend.log for details:"
        tail -5 logs/backend.log 2>/dev/null | sed 's/^/     /'
    fi
fi
echo ""

# Start Frontend
echo "[3/4] Frontend..."
if check_port $FRONTEND_PORT; then
    echo "  ✓ Already running on port $FRONTEND_PORT"
else
    echo "  Starting..."
    cd frontend
    nohup npm run dev -- --port $FRONTEND_PORT > ../logs/frontend.log 2>&1 &
    sleep 3
    cd ..
    if check_port $FRONTEND_PORT; then
        echo "  ✓ Started"
    else
        echo "  ✗ Failed to start. Check logs/frontend.log"
    fi
fi
echo ""

# Summary
echo "[4/4] Service Summary"
echo "=========================================="
echo ""
echo "  🎬 Frontend:      http://localhost:$FRONTEND_PORT"
echo "  🔧 Backend API:   http://localhost:$BACKEND_PORT/api/docs"
echo ""
echo "  External Services (需要单独启动):"
echo "  🔬 SurgR1:        http://localhost:$SURGR1_PORT/docs"
echo "  🤖 GLM:           http://localhost:$GLM_PORT/v1"
echo "  🎯 SAM3:          http://localhost:$SAM3_PORT/docs (可选)"
echo "  🔊 TTS:           http://localhost:$TTS_PORT (可选)"
echo ""
echo "=========================================="
echo ""
