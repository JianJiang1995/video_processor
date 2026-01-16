#!/bin/bash
# Qwen3-VL-8B vLLM API Server 启动脚本
# ============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.yaml"

# 从配置文件读取端口
SERVER_PORT=$(grep -A1 "^server:" "${CONFIG_FILE}" | grep "port:" | awk '{print $2}' | tr -d ' ')
SERVER_PORT=${SERVER_PORT:-8000}

echo "=============================================="
echo "  Qwen3-VL-8B vLLM API Server"
echo "=============================================="

# ===============================
# 检查并关闭已有服务
# ===============================
check_and_stop_existing() {
    echo "检查端口 ${SERVER_PORT} ..."
    
    EXISTING_PID=$(lsof -ti:${SERVER_PORT} 2>/dev/null || true)
    
    if [ -n "$EXISTING_PID" ]; then
        echo "⚠️  端口 ${SERVER_PORT} 被占用 (PID: ${EXISTING_PID})"
        echo "正在关闭..."
        
        kill ${EXISTING_PID} 2>/dev/null || true
        sleep 2
        
        if kill -0 ${EXISTING_PID} 2>/dev/null; then
            kill -9 ${EXISTING_PID} 2>/dev/null || true
            sleep 1
        fi
        
        if lsof -ti:${SERVER_PORT} >/dev/null 2>&1; then
            echo "❌ 无法关闭端口 ${SERVER_PORT}"
            exit 1
        fi
        echo "✅ 已关闭"
    else
        echo "✅ 端口可用"
    fi
    
    # 清理残留进程
    QWEN_PIDS=$(pgrep -f "vllm.*Qwen3-VL" 2>/dev/null || true)
    if [ -n "$QWEN_PIDS" ]; then
        echo "清理残留进程: ${QWEN_PIDS}"
        echo "${QWEN_PIDS}" | xargs kill 2>/dev/null || true
        sleep 1
        echo "${QWEN_PIDS}" | xargs kill -9 2>/dev/null 2>&1 || true
    fi
}

check_and_stop_existing
echo ""

# ===============================
# 激活环境
# ===============================
# 使用与GLM相同的conda环境（需要vLLM支持）
if [ -f "$(conda info --base 2>/dev/null)/etc/profile.d/conda.sh" ]; then
    source "$(conda info --base)/etc/profile.d/conda.sh"
    # 尝试激活 glm46v 环境（已有vLLM），或者其他支持vLLM的环境
    conda activate glm46v 2>/dev/null || conda activate vllm 2>/dev/null || echo "Using current environment"
else
    echo "Warning: conda not found, using current environment"
fi

# 显示环境信息
echo "环境信息:"
python -c "import vllm; print(f'  vLLM: {vllm.__version__}')" 2>/dev/null || echo "  vLLM: not installed"
python -c "import transformers; print(f'  transformers: {transformers.__version__}')" 2>/dev/null || echo "  transformers: not installed"
echo ""

# ===============================
# 启动服务
# ===============================
exec python3 "${SCRIPT_DIR}/server.py" --config "${CONFIG_FILE}"



