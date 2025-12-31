#!/bin/bash
# GLM-4.6V-Flash 环境配置脚本
# ============================================
# 
# 环境要求:
#   - vLLM >= 0.12.0
#   - transformers >= 5.0.0rc0
#   - CUDA 兼容 GPU (建议 >= 24GB 显存)

set -e

ENV_NAME="glm46v"
PYTHON_VERSION="3.11"

echo "=============================================="
echo "  GLM-4.6V-Flash 环境配置"
echo "=============================================="

# 检查conda
if ! command -v conda &> /dev/null; then
    echo "❌ Error: conda not found"
    echo "请先安装 Miniconda 或 Anaconda"
    exit 1
fi

source "$(conda info --base)/etc/profile.d/conda.sh"

# 创建或更新环境
if conda env list | grep -q "^${ENV_NAME} "; then
    echo "环境 ${ENV_NAME} 已存在，正在更新..."
    conda activate ${ENV_NAME}
else
    echo "创建新环境: ${ENV_NAME} (Python ${PYTHON_VERSION})"
    conda create -n ${ENV_NAME} python=${PYTHON_VERSION} -y
    conda activate ${ENV_NAME}
fi

echo ""
echo "安装依赖..."

# 安装核心依赖
pip install --upgrade pip
pip install "vllm>=0.12.0" -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install "transformers>=5.0.0rc0" -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install httpx pyyaml pillow aiofiles -i https://pypi.tuna.tsinghua.edu.cn/simple

echo ""
echo "验证安装..."
python -c "import vllm; print(f'✅ vLLM: {vllm.__version__}')"
python -c "import transformers; print(f'✅ transformers: {transformers.__version__}')"

echo ""
echo "=============================================="
echo "  环境配置完成!"
echo "=============================================="
echo ""
echo "使用方法:"
echo "  conda activate ${ENV_NAME}"
echo "  ./start.sh"

