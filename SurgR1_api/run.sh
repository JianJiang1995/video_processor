#!/bin/bash
# SurgR1 API - Surgical Image Analysis Service
# Uses vLLM with FastAPI for efficient batch inference

set -e

cd "$(dirname "$0")"

# Configuration
CONDA_ENV="vllm"
PORT=9003

echo "╔══════════════════════════════════════════════════════════════════════════════╗"
echo "║                    SurgR1 API - Surgical Image Analysis                      ║"
echo "╚══════════════════════════════════════════════════════════════════════════════╝"
echo ""

# ============================================================================
# 清理旧进程
# ============================================================================
echo "🧹 清理旧进程..."

# 杀掉占用端口的进程
if lsof -i :${PORT} >/dev/null 2>&1; then
    echo "   发现端口 ${PORT} 被占用，正在清理..."
    kill -9 $(lsof -t -i :${PORT}) 2>/dev/null || true
    sleep 2
fi

# 杀掉所有 SurgR1 相关的 vLLM 进程
pkill -9 -f "SurgR1_api.*main.py" 2>/dev/null || true
pkill -9 -f "VLLM::EngineCore" 2>/dev/null || true

# 等待进程完全退出
sleep 2

# 确认端口已释放
if lsof -i :${PORT} >/dev/null 2>&1; then
    echo "❌ 端口 ${PORT} 仍被占用，请手动清理"
    lsof -i :${PORT}
    exit 1
fi
echo "✅ 旧进程已清理"
echo ""

# Check if conda environment exists
if ! conda info --envs | grep -q "^${CONDA_ENV} "; then
    echo "❌ Conda environment '${CONDA_ENV}' not found!"
    echo "   Please create it with: conda create -n ${CONDA_ENV} python=3.10"
    echo "   Then install dependencies: pip install -r requirements.txt"
    exit 1
fi

echo "🔧 Activating conda environment: ${CONDA_ENV}"
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate ${CONDA_ENV}

# Show Python and vLLM versions
echo "📦 Python: $(python --version)"
echo "📦 vLLM: $(python -c 'import vllm; print(vllm.__version__)' 2>/dev/null || echo 'not installed')"
echo ""

# Parse config
CONFIG_FILE="config.json"
if [ -f "$CONFIG_FILE" ]; then
    MODEL_PATH=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['model']['path'])")
    PORT=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['server']['port'])")
    echo "📁 Model: $MODEL_PATH"
    echo "🌐 Port: $PORT"
else
    echo "⚠️  Config file not found, using defaults"
fi

echo ""
echo "🚀 Starting SurgR1 API server..."
echo "═══════════════════════════════════════════════════════════════════════════════"
echo ""

# Use GPU 0 by default (can be overridden: CUDA_VISIBLE_DEVICES=X bash run.sh)
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
echo "🎮 Using GPU: $CUDA_VISIBLE_DEVICES"

# Run the server
python main.py


