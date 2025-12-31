#!/bin/bash
# GLM-4.6V-Flash vLLM API Server 停止脚本
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.yaml"

# 从配置文件读取端口
SERVER_PORT=$(grep -A1 "^server:" "${CONFIG_FILE}" | grep "port:" | awk '{print $2}' | tr -d ' ')
SERVER_PORT=${SERVER_PORT:-8000}

echo "=============================================="
echo "  停止 GLM-4.6V-Flash vLLM API Server"
echo "=============================================="

# 检查端口占用
EXISTING_PID=$(lsof -ti:${SERVER_PORT} 2>/dev/null || true)

if [ -n "$EXISTING_PID" ]; then
    echo "发现服务 (PID: ${EXISTING_PID})"
    echo "正在关闭..."
    
    kill ${EXISTING_PID} 2>/dev/null || true
    sleep 2
    
    if kill -0 ${EXISTING_PID} 2>/dev/null; then
        echo "强制关闭..."
        kill -9 ${EXISTING_PID} 2>/dev/null || true
        sleep 1
    fi
    
    echo "✅ 服务已停止"
else
    echo "端口 ${SERVER_PORT} 上没有运行的服务"
fi

# 清理残留进程
GLM_PIDS=$(pgrep -f "vllm.*GLM-4.6V-Flash" 2>/dev/null || true)
if [ -n "$GLM_PIDS" ]; then
    echo "清理残留进程: ${GLM_PIDS}"
    echo "${GLM_PIDS}" | xargs kill 2>/dev/null || true
    sleep 1
    echo "${GLM_PIDS}" | xargs kill -9 2>/dev/null 2>&1 || true
    echo "✅ 已清理"
fi

echo "完成"

