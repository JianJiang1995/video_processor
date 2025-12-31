#!/bin/bash
# CosyVoice vLLM TTS 服务启动脚本
# 使用 config.yaml 配置文件

set -e

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/config.yaml"

# 设置 Python 路径
export PYTHONPATH="${SCRIPT_DIR}:${SCRIPT_DIR}/third_party/Matcha-TTS:${PYTHONPATH}"

# 从 config.yaml 读取配置
if [ -f "$CONFIG_FILE" ]; then
    # 使用 Python 解析 YAML（更可靠）
    GPU_DEVICE=$(python3 -c "import yaml; print(yaml.safe_load(open('$CONFIG_FILE'))['vllm']['gpu_device'])" 2>/dev/null || echo "0")
    PORT=$(python3 -c "import yaml; print(yaml.safe_load(open('$CONFIG_FILE'))['server']['port'])" 2>/dev/null || echo "50000")
    MODEL_DIR=$(python3 -c "import yaml; print(yaml.safe_load(open('$CONFIG_FILE'))['model']['model_dir'])" 2>/dev/null || echo "pretrained_models/FunAudioLLM/Fun-CosyVoice3-0___5B-2512")
    GPU_MEM=$(python3 -c "import yaml; print(yaml.safe_load(open('$CONFIG_FILE'))['vllm']['gpu_memory_utilization'])" 2>/dev/null || echo "0.4")
else
    echo "警告: 配置文件 $CONFIG_FILE 不存在，使用默认值"
    GPU_DEVICE=0
    PORT=50000
    MODEL_DIR="pretrained_models/FunAudioLLM/Fun-CosyVoice3-0___5B-2512"
    GPU_MEM=0.4
fi

# 允许命令行参数覆盖配置
while [[ $# -gt 0 ]]; do
    case $1 in
        --gpu)
            GPU_DEVICE="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --model)
            MODEL_DIR="$2"
            shift 2
            ;;
        --config)
            CONFIG_FILE="$2"
            shift 2
            ;;
        -h|--help)
            echo "用法: $0 [选项]"
            echo ""
            echo "选项:"
            echo "  --gpu NUM     GPU 设备 ID (默认: 从 config.yaml 读取)"
            echo "  --port NUM    服务端口 (默认: 从 config.yaml 读取)"
            echo "  --model PATH  模型目录 (默认: 从 config.yaml 读取)"
            echo "  --config PATH 配置文件路径 (默认: config.yaml)"
            echo "  -h, --help    显示帮助信息"
            exit 0
            ;;
        *)
            echo "未知选项: $1"
            exit 1
            ;;
    esac
done

# 设置 CUDA 设备
export CUDA_VISIBLE_DEVICES="${GPU_DEVICE}"

echo "=============================================="
echo "  CosyVoice vLLM TTS 服务"
echo "=============================================="
echo "配置文件: ${CONFIG_FILE}"
echo "GPU 设备: ${GPU_DEVICE}"
echo "GPU 显存利用率: ${GPU_MEM}"
echo "服务端口: ${PORT}"
echo "模型目录: ${MODEL_DIR}"
echo "=============================================="
echo ""

# 检查 conda 环境
if command -v conda &> /dev/null; then
    # 尝试激活 cosyvoice_vllm 环境
    if conda env list | grep -q "cosyvoice_vllm"; then
        echo "激活 conda 环境: cosyvoice_vllm"
        eval "$(conda shell.bash hook)"
        conda activate cosyvoice_vllm
    fi
fi

# 启动服务
echo "正在启动服务..."
python -u "${SCRIPT_DIR}/runtime/python/fastapi/server_vllm.py" \
    --config "${CONFIG_FILE}"
