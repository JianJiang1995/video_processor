#!/bin/bash
# SAM3 FastAPI 服务启动脚本

# 切换到脚本所在目录
cd "$(dirname "$0")"

# 设置环境变量
export SAM3_CHECKPOINT="${SAM3_CHECKPOINT:-$(pwd)/ckpt/sam3.pt}"
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-9004}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-2}"

# 关闭占用端口的现有服务
echo "检查端口 $PORT 是否被占用..."
EXISTING_PID=$(lsof -t -i:$PORT 2>/dev/null)
if [ -n "$EXISTING_PID" ]; then
    echo "发现端口 $PORT 被进程 $EXISTING_PID 占用，正在关闭..."
    kill -9 $EXISTING_PID 2>/dev/null
    sleep 1
    echo "已关闭现有服务"
else
    echo "端口 $PORT 未被占用"
fi

echo "=================================================="
echo "SAM3 FastAPI 服务"
echo "=================================================="
echo "Conda环境: sam3"
echo "模型权重路径: $SAM3_CHECKPOINT"
echo "服务地址: http://$HOST:$PORT"
echo "=================================================="

# 激活conda环境并启动服务
source /data/jj/miniconda3/etc/profile.d/conda.sh
conda activate sam3

# 切换到src目录运行
cd src
python main.py

