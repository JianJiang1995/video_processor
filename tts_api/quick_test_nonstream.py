#!/usr/bin/env python3
"""
直接测试 CosyVoice 模型（非流式）- 绕过 HTTP 服务
对比流式 vs 非流式性能
"""
import os
import sys
import time

# 设置路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, 'third_party', 'Matcha-TTS'))

os.environ['CUDA_VISIBLE_DEVICES'] = '0'

from vllm import ModelRegistry
from cosyvoice.vllm.cosyvoice2 import CosyVoice2ForCausalLM
ModelRegistry.register_model("CosyVoice2ForCausalLM", CosyVoice2ForCausalLM)

from cosyvoice.cli.cosyvoice import AutoModel
import torchaudio

# 测试文本
SHORT_TEXT = "你好，这是一个测试。"
LONG_TEXT = "这是一段腹腔镜胆囊切除术的视频片段。片段伊始处于胆囊牵引阶段，视野中主要呈现肝下区。"

def test_nonstream():
    print("=" * 60)
    print("CosyVoice3 性能测试（直接调用，非HTTP）")
    print("=" * 60)
    
    model_dir = os.path.join(SCRIPT_DIR, 'pretrained_models/FunAudioLLM/Fun-CosyVoice3-0___5B-2512')
    prompt_wav = os.path.join(SCRIPT_DIR, 'asset/zero_shot_prompt.wav')
    prompt_text = "You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。"
    
    print(f"\n加载模型: {model_dir}")
    start = time.time()
    
    # 加载模型
    cosyvoice = AutoModel(
        model_dir=model_dir,
        load_vllm=True,
        load_trt=False,
        fp16=True
    )
    print(f"模型加载耗时: {time.time() - start:.2f}s")
    
    # 预加载 speaker embedding
    print("\n预加载 speaker embedding...")
    start = time.time()
    cosyvoice.add_zero_shot_spk(prompt_text, prompt_wav, 'default_female')
    print(f"预加载耗时: {time.time() - start:.2f}s")
    
    # 测试1: 短文本 + 非流式 + 使用预加载的 speaker
    print(f"\n[测试1] 短文本 + 非流式 + 预加载speaker")
    print(f"文本: {SHORT_TEXT}")
    start = time.time()
    
    for i, result in enumerate(cosyvoice.inference_zero_shot(
        SHORT_TEXT, '', '', zero_shot_spk_id='default_female', stream=False
    )):
        audio_len = result['tts_speech'].shape[1] / cosyvoice.sample_rate
        print(f"  chunk {i}: {audio_len:.2f}s")
    
    elapsed = time.time() - start
    print(f"  总耗时: {elapsed:.2f}s")
    
    # 测试2: 长文本 + 非流式 + 使用预加载的 speaker  
    print(f"\n[测试2] 长文本 + 非流式 + 预加载speaker")
    print(f"文本: {LONG_TEXT}")
    start = time.time()
    
    total_audio_len = 0
    for i, result in enumerate(cosyvoice.inference_zero_shot(
        LONG_TEXT, '', '', zero_shot_spk_id='default_female', stream=False
    )):
        audio_len = result['tts_speech'].shape[1] / cosyvoice.sample_rate
        total_audio_len += audio_len
        print(f"  chunk {i}: {audio_len:.2f}s")
    
    elapsed = time.time() - start
    rtf = elapsed / total_audio_len if total_audio_len > 0 else 0
    print(f"  总耗时: {elapsed:.2f}s, 音频时长: {total_audio_len:.2f}s, RTF: {rtf:.2f}")
    
    # 测试3: 对比 - 不使用预加载的 speaker（每次都处理 prompt）
    print(f"\n[测试3] 短文本 + 非流式 + 每次处理prompt（对比）")
    print(f"文本: {SHORT_TEXT}")
    start = time.time()
    
    for i, result in enumerate(cosyvoice.inference_zero_shot(
        SHORT_TEXT, prompt_text, prompt_wav, stream=False
    )):
        audio_len = result['tts_speech'].shape[1] / cosyvoice.sample_rate
        print(f"  chunk {i}: {audio_len:.2f}s")
    
    elapsed = time.time() - start
    print(f"  总耗时: {elapsed:.2f}s")
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

if __name__ == '__main__':
    test_nonstream()



