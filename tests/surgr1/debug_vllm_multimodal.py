#!/usr/bin/env python3
"""
Debug script to test vLLM multimodal input format directly.
Run this with: conda run -n vllm python debug_vllm_multimodal.py
"""
import sys
import os

# Add image path
IMAGE_PATH = "/data2/jj/proj/video_processor/test_data/sample_frame.jpg"

print("=" * 60)
print("vLLM Multimodal Debug Test")
print("=" * 60)

# Check image
if not os.path.exists(IMAGE_PATH):
    print(f"ERROR: Image not found: {IMAGE_PATH}")
    sys.exit(1)
print(f"✓ Image found: {IMAGE_PATH}")

# Import vLLM
print("\nLoading vLLM...")
from vllm import LLM, SamplingParams
import vllm
print(f"✓ vLLM version: {vllm.__version__}")

# Model path
MODEL_PATH = "/data/jj/proj/Laparo/last_cot_qwen2.5/round35/v4-20251112-181725/checkpoint-10609-merged"
print(f"✓ Model path: {MODEL_PATH}")

# Load model (this takes time)
print("\nLoading model (this may take a while)...")
llm = LLM(
    model=MODEL_PATH,
    max_model_len=8192,
    gpu_memory_utilization=0.85,
    trust_remote_code=True,
    limit_mm_per_prompt={"image": 1}
)
print("✓ Model loaded")

# Sampling params
sampling_params = SamplingParams(
    temperature=0.1,
    max_tokens=256,
    top_p=0.95
)

# Test different prompt formats
SYSTEM_PROMPT = "You are a helpful assistant that analyzes surgical images."
QUESTION = "Describe what you see in this image briefly."

# Format 1: With <image> placeholder in question
prompt_v1 = f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n<|im_start|>user\n<image>\n{QUESTION}<|im_end|>\n<|im_start|>assistant\n"

# Format 2: Without any placeholder (relying on multi_modal_data)
prompt_v2 = f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n<|im_start|>user\n{QUESTION}<|im_end|>\n<|im_start|>assistant\n"

# Format 3: With Qwen2-VL style vision tokens
prompt_v3 = f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>\n{QUESTION}<|im_end|>\n<|im_start|>assistant\n"

test_cases = [
    ("Format 1: <image> placeholder", prompt_v1),
    ("Format 2: No placeholder", prompt_v2),
    ("Format 3: <|vision_start|>...<|vision_end|>", prompt_v3),
]

for name, prompt in test_cases:
    print(f"\n{'=' * 60}")
    print(f"Testing: {name}")
    print(f"{'=' * 60}")
    print(f"Prompt (first 200 chars):\n{prompt[:200]}...")
    
    try:
        inputs = [{
            "prompt": prompt,
            "multi_modal_data": {"image": IMAGE_PATH}
        }]
        
        outputs = llm.generate(inputs, sampling_params)
        
        if outputs and outputs[0].outputs:
            response = outputs[0].outputs[0].text
            print(f"\n✓ SUCCESS!")
            print(f"Response (first 300 chars):\n{response[:300]}...")
        else:
            print("\n✗ No output generated")
            
    except Exception as e:
        print(f"\n✗ ERROR: {e}")

print("\n" + "=" * 60)
print("Debug complete!")
print("=" * 60)


