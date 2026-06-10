#!/bin/bash
# Deploy SurgiCoT Model Service using Swift
# This creates an OpenAI-compatible API at port 9000

cd "$(dirname "$0")"

# Load config
CONFIG_FILE="config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: config.json not found"
    exit 1
fi

# Parse config using python
MODEL_PATH=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['model']['path'])")
SYSTEM_PROMPT=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['model']['system_prompt_path'])")
PORT=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['services']['model']['port'])")
MODEL_NAME=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['model']['served_model_name'])")
MAX_BATCH=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['model']['max_batch_size'])")
GPU_UTIL=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['model']['gpu_memory_utilization'])")
MAX_PIXELS=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['model']['max_pixels'])")

echo "=========================================="
echo "  SurgiCoT Model Service Deployment"
echo "=========================================="
echo "Model: $MODEL_PATH"
echo "Port: $PORT"
echo "Model Name: $MODEL_NAME"
echo "Max Batch: $MAX_BATCH"
echo "GPU Utilization: $GPU_UTIL"
echo ""

# Set environment variables
export MAX_PIXELS=$MAX_PIXELS
export NPROC_PER_NODE=1
export CUDA_VISIBLE_DEVICES=0

# Deploy using swift
swift deploy \
    --model "$MODEL_PATH" \
    --system "$SYSTEM_PROMPT" \
    --attn_impl flash_attn \
    --infer_backend vllm \
    --max_batch_size $MAX_BATCH \
    --gpu_memory_utilization $GPU_UTIL \
    --host 0.0.0.0 \
    --port $PORT \
    --served_model_name "$MODEL_NAME"




