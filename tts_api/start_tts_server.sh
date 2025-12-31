#!/bin/bash
# CosyVoice TTS 服务启动脚本（非 vLLM 版本）
# 使用 config.yaml 配置文件

set -e

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.yaml"

# 设置 Python 路径
export PYTHONPATH="${SCRIPT_DIR}:${SCRIPT_DIR}/third_party/Matcha-TTS:${PYTHONPATH}"

# 从 config.yaml 读取配置
if [ -f "$CONFIG_FILE" ]; then
    PORT=$(python3 -c "import yaml; print(yaml.safe_load(open('$CONFIG_FILE'))['server']['port'])" 2>/dev/null || echo "50000")
    MODEL_DIR=$(python3 -c "import yaml; print(yaml.safe_load(open('$CONFIG_FILE'))['model']['model_dir'])" 2>/dev/null || echo "pretrained_models/CosyVoice-300M-SFT")
else
    echo "警告: 配置文件 $CONFIG_FILE 不存在，使用默认值"
    PORT=50000
    MODEL_DIR="pretrained_models/CosyVoice-300M-SFT"
fi

# 处理相对路径
if [[ ! "$MODEL_DIR" = /* ]]; then
    MODEL_DIR="${SCRIPT_DIR}/${MODEL_DIR}"
fi

echo "=============================================="
echo "  CosyVoice TTS 服务"
echo "=============================================="
echo "配置文件: ${CONFIG_FILE}"
echo "服务端口: ${PORT}"
echo "模型目录: ${MODEL_DIR}"
echo "=============================================="
echo ""

cd "${SCRIPT_DIR}/runtime/python/fastapi"

echo "正在启动服务..."
python server.py \
    --port ${PORT} \
    --model_dir "${MODEL_DIR}"
