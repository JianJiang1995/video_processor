#!/bin/bash
# Fun-ASR-Nano 实时语音识别服务启动脚本

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# 激活conda环境
source ~/miniconda3/bin/activate asr

# 配置环境变量
export ASR_DEVICE="cuda:0"
export ASR_HOST="0.0.0.0"
export ASR_PORT="8765"

# 关闭占用端口的进程
echo "🔍 检查端口 $ASR_PORT 是否被占用..."
PID=$(lsof -t -i:$ASR_PORT 2>/dev/null)
if [ -n "$PID" ]; then
    echo "⚠️  发现进程 $PID 占用端口 $ASR_PORT，正在关闭..."
    kill -9 $PID 2>/dev/null
    sleep 2
    echo "✅ 已关闭旧进程"
else
    echo "✅ 端口 $ASR_PORT 未被占用"
fi
echo ""

echo "=========================================="
echo "  Fun-ASR-Nano 实时语音识别服务"
echo "  模型: Fun-ASR-Nano-2512 (0.8B参数)"
echo "=========================================="
echo "设备: $ASR_DEVICE"
echo "地址: http://localhost:$ASR_PORT"
echo ""
echo "🌐 Web界面: http://localhost:$ASR_PORT"
echo "📚 API文档: http://localhost:$ASR_PORT/docs"
echo "=========================================="
echo ""

python server.py
