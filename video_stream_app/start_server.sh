#!/bin/bash
# ============================================
# GPU 服务器一键启动脚本
# 启动 FastAPI Backend + SurgR1 vLLM
#
# 用法:
#   bash start_server.sh                    # 默认启动
#   bash start_server.sh --surgr1-gpu 3     # 指定 SurgR1 使用 GPU 3
#   bash start_server.sh --backend-only     # 只启动后端
#   bash start_server.sh --stop             # 停止所有服务
# ============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 默认配置
BACKEND_PORT=8001
SURGR1_PORT=9003
SURGR1_GPU="${SURGR1_GPU:-3}"
BACKEND_CONDA="vllm"
SURGR1_CONDA="vllm"
BACKEND_ONLY=false
STOP_MODE=false

# PID 文件
PID_DIR="$SCRIPT_DIR/.pids"
BACKEND_PID_FILE="$PID_DIR/backend.pid"
SURGR1_PID_FILE="$PID_DIR/surgr1.pid"

# 日志
LOG_DIR="$SCRIPT_DIR/logs"
BACKEND_LOG="$LOG_DIR/backend.log"
SURGR1_LOG="$LOG_DIR/surgr1.log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Parse args
for arg in "$@"; do
  case $arg in
    --surgr1-gpu=*) SURGR1_GPU="${arg#*=}" ;;
    --surgr1-gpu) shift; SURGR1_GPU="$2" ;;
    --backend-only) BACKEND_ONLY=true ;;
    --stop) STOP_MODE=true ;;
    --help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "  --surgr1-gpu=N     SurgR1 使用的 GPU 编号 (默认: 3)"
      echo "  --backend-only     只启动后端，不启动 SurgR1"
      echo "  --stop             停止所有服务"
      echo ""
      echo "环境变量:"
      echo "  SURGR1_GPU=N       同 --surgr1-gpu"
      echo "  BACKEND_PORT=N     后端端口 (默认: 8001)"
      echo "  SURGR1_PORT=N      SurgR1 端口 (默认: 9003)"
      exit 0
      ;;
  esac
done

mkdir -p "$PID_DIR" "$LOG_DIR"

# ============================================
# Stop function
# ============================================
stop_services() {
  echo -e "${YELLOW}Stopping services...${NC}"

  # Stop backend
  if [ -f "$BACKEND_PID_FILE" ]; then
    PID=$(cat "$BACKEND_PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "  Stopping backend (PID: $PID)..."
      kill "$PID" 2>/dev/null
      sleep 2
      kill -9 "$PID" 2>/dev/null || true
    fi
    rm -f "$BACKEND_PID_FILE"
  fi
  # Also kill by port
  lsof -t -i:$BACKEND_PORT 2>/dev/null | xargs kill -9 2>/dev/null || true

  # Stop SurgR1
  if [ -f "$SURGR1_PID_FILE" ]; then
    PID=$(cat "$SURGR1_PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "  Stopping SurgR1 (PID: $PID)..."
      kill "$PID" 2>/dev/null
      sleep 2
      kill -9 "$PID" 2>/dev/null || true
    fi
    rm -f "$SURGR1_PID_FILE"
  fi
  lsof -t -i:$SURGR1_PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
  pkill -9 -f "SurgR1_api.*main.py" 2>/dev/null || true

  sleep 1
  echo -e "${GREEN}All services stopped${NC}"
}

if [ "$STOP_MODE" = true ]; then
  stop_services
  exit 0
fi

# ============================================
# Print banner
# ============================================
echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       Video Analyzer - GPU Server Services                 ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Backend port:  ${GREEN}$BACKEND_PORT${NC}"
echo -e "  SurgR1 port:   ${GREEN}$SURGR1_PORT${NC}"
echo -e "  SurgR1 GPU:    ${GREEN}$SURGR1_GPU${NC}"
echo ""

# ============================================
# Stop existing services first
# ============================================
stop_services
echo ""

# ============================================
# Activate conda
# ============================================
eval "$(conda shell.bash hook)"

# ============================================
# Start Backend
# ============================================
echo -e "${YELLOW}[1] Starting FastAPI Backend...${NC}"

cd "$SCRIPT_DIR"
conda activate "$BACKEND_CONDA"
export PYTHONPATH="${PYTHONPATH}:${SCRIPT_DIR}"

nohup python -m uvicorn backend.main:app \
  --host 0.0.0.0 \
  --port $BACKEND_PORT \
  --reload \
  > "$BACKEND_LOG" 2>&1 &

BACKEND_PID=$!
echo "$BACKEND_PID" > "$BACKEND_PID_FILE"
echo -e "  PID: $BACKEND_PID"
echo -e "  Log: $BACKEND_LOG"

# Wait for backend to be ready
echo -n "  Waiting for backend..."
for i in $(seq 1 30); do
  if curl -s "http://localhost:$BACKEND_PORT/api/health" > /dev/null 2>&1; then
    echo -e " ${GREEN}ready${NC}"
    break
  fi
  if [ $i -eq 30 ]; then
    echo -e " ${YELLOW}timeout (may still be starting)${NC}"
  fi
  sleep 1
  echo -n "."
done
echo ""

# ============================================
# Start SurgR1
# ============================================
if [ "$BACKEND_ONLY" = false ]; then
  echo -e "${YELLOW}[2] Starting SurgR1 vLLM Service...${NC}"

  cd "$PROJECT_ROOT/SurgR1_api"
  conda activate "$SURGR1_CONDA"
  export CUDA_VISIBLE_DEVICES="$SURGR1_GPU"

  nohup python main.py > "$SURGR1_LOG" 2>&1 &

  SURGR1_PID=$!
  echo "$SURGR1_PID" > "$SURGR1_PID_FILE"
  echo -e "  PID: $SURGR1_PID"
  echo -e "  GPU: $SURGR1_GPU"
  echo -e "  Log: $SURGR1_LOG"

  # SurgR1 takes a while to load the model
  echo -n "  Loading model (this takes 1-2 minutes)..."
  for i in $(seq 1 120); do
    if curl -s "http://localhost:$SURGR1_PORT/health" 2>/dev/null | grep -q "healthy"; then
      echo -e " ${GREEN}ready${NC}"
      break
    fi
    if [ $i -eq 120 ]; then
      echo -e " ${YELLOW}timeout (check log: $SURGR1_LOG)${NC}"
    fi
    if ! kill -0 "$SURGR1_PID" 2>/dev/null; then
      echo -e " ${RED}process died (check log: $SURGR1_LOG)${NC}"
      break
    fi
    sleep 1
    if [ $((i % 10)) -eq 0 ]; then
      echo -n " ${i}s"
    fi
  done
fi

echo ""

# ============================================
# Summary
# ============================================
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Services Started                        ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Get server IP
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$SERVER_IP" ]; then
  SERVER_IP="<server-ip>"
fi

echo -e "  Backend:  ${GREEN}http://${SERVER_IP}:${BACKEND_PORT}${NC}"
echo -e "  SurgR1:   ${GREEN}http://${SERVER_IP}:${SURGR1_PORT}${NC}"
echo -e "  API Docs: ${GREEN}http://${SERVER_IP}:${BACKEND_PORT}/api/docs${NC}"
echo ""
echo -e "  ${BLUE}在 Electron 端的 config.json 中配置:${NC}"
echo ""
echo -e "    services.backend.host = \"${SERVER_IP}\""
echo -e "    services.backend.port = ${BACKEND_PORT}"
echo -e "    services.surgr1.api_url = \"http://${SERVER_IP}:${SURGR1_PORT}\""
echo ""
echo -e "  停止服务: ${YELLOW}bash start_server.sh --stop${NC}"
echo -e "  查看日志: ${YELLOW}tail -f $BACKEND_LOG${NC}"
echo -e "            ${YELLOW}tail -f $SURGR1_LOG${NC}"
