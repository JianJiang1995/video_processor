#!/bin/bash
# SurgR1 容器入口脚本
#
# 环境变量：
#   MODELSCOPE_API_TOKEN  — ModelScope 私有模型访问 Token
#   MODELSCOPE_MODEL_ID   — 模型 ID，默认 lonsirky/SurgR1
#   MODEL_CACHE_DIR       — 模型缓存目录，默认 /model-cache
#
# 逻辑：
#   1. 检查 /model-cache 是否已有模型文件（*.safetensors）
#   2. 没有 → 从 ModelScope 下载到 /model-cache
#   3. 生成 /app/config.json，model.path 指向缓存目录
#   4. 启动 python main.py

set -e

TEMPLATE="/app/config.json.template"
CONFIG="/app/config.json"
MODEL_CACHE_DIR="${MODEL_CACHE_DIR:-/model-cache}"
MODEL_ID="${MODELSCOPE_MODEL_ID:-lonsirky/SurgR1}"

echo "============================================"
echo " SurgR1 Container Starting"
echo " Model ID : $MODEL_ID"
echo " Cache Dir: $MODEL_CACHE_DIR"
echo "============================================"

# ────────────────────────────────────────────────
# Step 1: 检查模型是否已缓存
# ────────────────────────────────────────────────
NEED_DOWNLOAD=true
if ls "${MODEL_CACHE_DIR}"/*.safetensors 2>/dev/null | grep -q .; then
    echo "[entrypoint] Model cache found at ${MODEL_CACHE_DIR}, skipping download."
    NEED_DOWNLOAD=false
fi

# ────────────────────────────────────────────────
# Step 2: 从 ModelScope 下载模型（首次启动）
# ────────────────────────────────────────────────
if [ "$NEED_DOWNLOAD" = true ]; then
    echo "[entrypoint] No cached model found. Downloading from ModelScope..."

    if [ -z "$MODELSCOPE_API_TOKEN" ]; then
        echo "[entrypoint] ERROR: MODELSCOPE_API_TOKEN is not set."
        exit 1
    fi

    mkdir -p "$MODEL_CACHE_DIR"

    python3 - <<PYEOF
import os
from modelscope import snapshot_download

token = os.environ["MODELSCOPE_API_TOKEN"]
model_id = "$MODEL_ID"
cache_dir = "$MODEL_CACHE_DIR"

print(f"[entrypoint] Downloading {model_id} -> {cache_dir}")
snapshot_download(
    model_id=model_id,
    cache_dir=cache_dir,
    local_dir=cache_dir,
    token=token,
)
print("[entrypoint] Download complete.")
PYEOF

fi

# ────────────────────────────────────────────────
# Step 3: 生成 config.json，写入正确的 model.path
# ────────────────────────────────────────────────
if [ ! -f "$TEMPLATE" ]; then
    echo "[entrypoint] ERROR: $TEMPLATE not found"
    exit 1
fi

python3 - <<PYEOF
import json

with open("$TEMPLATE") as f:
    cfg = json.load(f)

cfg["model"]["path"] = "$MODEL_CACHE_DIR"
cfg["server"]["host"] = "0.0.0.0"

with open("$CONFIG", "w") as f:
    json.dump(cfg, f, indent=4, ensure_ascii=False)

print(f"[entrypoint] config.json written: model.path = {cfg['model']['path']}")
PYEOF

# ────────────────────────────────────────────────
# Step 4: 启动服务
# ────────────────────────────────────────────────
echo "[entrypoint] Starting SurgR1 server..."
exec python main.py
