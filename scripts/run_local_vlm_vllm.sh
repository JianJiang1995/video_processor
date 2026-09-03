#!/bin/bash
# Serve the production local VLM (Qwen3-VL-8B-Instruct) with vLLM on port 8010.
#
# Replaces scripts/local_openai_vlm_server.py (transformers, --max-concurrent 1):
# vLLM handles concurrent requests natively and cuts per-call latency from
# ~1.3-3s to ~0.6s, which eliminates the realtime open-vision / clip-review
# queueing timeouts. Same port + served-model-name, so config.json needs no
# changes. Benchmarked 2026-07-09: clip binary recall 0.77 / specificity 0.97 /
# hard-set 20/21, avg 0.60s per 640px image (runs/clip_vlm_binary_benchmark_v2).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

GPU="${VLM_GPU:-2}"
PORT="${VLM_PORT:-8010}"
MODEL_PATH="${VLM_MODEL_PATH:-models/local_vlm/qwen3-vl-8b-instruct}"
SERVED_NAME="${VLM_SERVED_NAME:-Qwen3-VL-8B-Instruct}"

VENV="$ROOT_DIR/../.venv"
export LD_LIBRARY_PATH="$VENV/lib/python3.10/site-packages/nvidia/cu13/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# vllm 0.22 + this flashinfer build crashes in the sampler during warmup; use
# the default torch sampler instead.
export VLLM_USE_FLASHINFER_SAMPLER=0

echo "[vLLM] serving $MODEL_PATH as $SERVED_NAME on GPU $GPU port $PORT"
exec env CUDA_VISIBLE_DEVICES="$GPU" "$VENV/bin/vllm" serve "$MODEL_PATH" \
  --served-model-name "$SERVED_NAME" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --gpu-memory-utilization 0.93 \
  --max-model-len 12288 \
  --trust-remote-code
