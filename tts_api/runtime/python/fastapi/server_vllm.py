#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CosyVoice vLLM FastAPI 服务
支持从 config.yaml 读取配置
优化：预加载 speaker embedding 加速推理
"""

import os
import sys
import argparse
import logging

# 设置进程名称，方便在 ps/htop 中识别
try:
    import setproctitle
    setproctitle.setproctitle("tts_api [CosyVoice vLLM]")
except ImportError:
    pass

import uvicorn
import torch
import numpy as np
import librosa
import tempfile
import shutil
from fastapi import FastAPI, UploadFile, Form, File, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

# 添加项目根目录到路径
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(ROOT_DIR, '..', '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'third_party', 'Matcha-TTS'))

# 导入配置加载器
from config_loader import load_config, TTSConfig

# 注册 vLLM 模型
from vllm import ModelRegistry
from cosyvoice.vllm.cosyvoice2 import CosyVoice2ForCausalLM
from cosyvoice.cli.cosyvoice import AutoModel

ModelRegistry.register_model("CosyVoice2ForCausalLM", CosyVoice2ForCausalLM)

logging.getLogger('matplotlib').setLevel(logging.WARNING)
logging.getLogger('numba').setLevel(logging.WARNING)
logging.getLogger('librosa').setLevel(logging.WARNING)

app = FastAPI(title="CosyVoice TTS API", description="CosyVoice vLLM 加速的 TTS 服务")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# 全局变量
cosyvoice = None
config: TTSConfig = None

# 预加载的 speaker ID
DEFAULT_SPEAKER_ID = "default_female"
DEFAULT_PROMPT_TEXT = "You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。"


def save_upload_to_temp(upload_file: UploadFile) -> str:
    """将上传的文件保存到临时文件，返回路径"""
    suffix = os.path.splitext(upload_file.filename)[1] if upload_file.filename else '.wav'
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(upload_file.file, tmp)
        return tmp.name


def postprocess(speech, top_db=60, hop_length=220, win_length=440):
    """后处理音频"""
    max_val = config.audio.max_val if config else 0.8
    speech, _ = librosa.effects.trim(
        speech, top_db=top_db,
        frame_length=win_length,
        hop_length=hop_length
    )
    if speech.abs().max() > max_val:
        speech = speech / speech.abs().max() * max_val
    sample_rate = config.audio.sample_rate if config else 22050
    speech = torch.concat([speech, torch.zeros(1, int(sample_rate * 0.2))], dim=1)
    return speech


def generate_data(model_output):
    """生成音频数据流"""
    for i in model_output:
        speech = postprocess(i['tts_speech'])
        tts_audio = (speech.numpy() * (2 ** 15)).astype(np.int16).tobytes()
        yield tts_audio


@app.get("/")
async def root():
    """API 根路径"""
    available_spks = []
    if cosyvoice:
        try:
            available_spks = cosyvoice.list_available_spks()
        except:
            pass
    return {
        "service": "CosyVoice TTS API",
        "version": "1.0",
        "status": "running",
        "available_speakers": available_spks,
        "config": {
            "model_dir": config.get_model_dir() if config else None,
            "gpu_device": config.vllm.gpu_device if config else None,
            "gpu_memory_utilization": config.vllm.gpu_memory_utilization if config else None
        }
    }


@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "healthy", "model_loaded": cosyvoice is not None}


@app.get("/speakers")
async def list_speakers():
    """列出可用的说话人"""
    if not cosyvoice:
        raise HTTPException(status_code=500, detail="模型未初始化")
    try:
        spks = cosyvoice.list_available_spks()
        return {"speakers": spks, "default": DEFAULT_SPEAKER_ID}
    except Exception as e:
        return {"speakers": [DEFAULT_SPEAKER_ID], "default": DEFAULT_SPEAKER_ID}


@app.post("/inference_zero_shot")
@app.get("/inference_zero_shot")
async def inference_zero_shot(
    tts_text: str = Form(...),
    prompt_text: str = Form(...),
    prompt_wav: UploadFile = File(...)
):
    """
    零样本语音合成（3s极速复刻）
    
    Args:
        tts_text: 要合成的文本
        prompt_text: 参考音频对应的文本
        prompt_wav: 参考音频文件
    """
    temp_path = None
    try:
        if not cosyvoice:
            raise HTTPException(status_code=500, detail="模型未初始化")
        
        # 保存上传文件到临时路径（CosyVoice 内部需要文件路径）
        temp_path = save_upload_to_temp(prompt_wav)
        
        # 使用临时文件路径调用推理
        model_output = cosyvoice.inference_zero_shot(tts_text, prompt_text, temp_path, stream=True)
        
        # 生成数据后清理临时文件
        def generate_with_cleanup():
            try:
                for chunk in generate_data(model_output):
                    yield chunk
            finally:
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)
        
        return StreamingResponse(generate_with_cleanup())
    except Exception as e:
        # 出错时也要清理临时文件
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/inference_sft")
async def inference_sft(
    tts_text: str = Form(...),
    spk_id: str = Form(default="中文女")
):
    """
    预训练音色语音合成
    
    Args:
        tts_text: 要合成的文本
        spk_id: 说话人ID（中文女/中文男/英文女/英文男/日语男/粤语女/韩语女）
    """
    try:
        if not cosyvoice:
            raise HTTPException(status_code=500, detail="模型未初始化")
        
        available_spks = cosyvoice.list_available_spks()
        
        # 检查模型是否支持 SFT 模式
        if available_spks and len(available_spks) > 0:
            # CosyVoice-300M-SFT 模型：使用真正的 SFT 推理
            use_spk_id = spk_id if spk_id in available_spks else available_spks[0]
            logging.info(f"SFT 模式 - 使用音色: {use_spk_id}")
            model_output = cosyvoice.inference_sft(tts_text, use_spk_id, stream=True)
        else:
            # CosyVoice3 模型：使用 zero_shot + 预加载 speaker
            use_spk_id = DEFAULT_SPEAKER_ID if DEFAULT_SPEAKER_ID in cosyvoice.list_available_spks() else None
            if use_spk_id:
                logging.info(f"Zero-shot 模式 - 使用预加载 speaker: {use_spk_id}")
                model_output = cosyvoice.inference_zero_shot(
                    tts_text, '', '', zero_shot_spk_id=use_spk_id, stream=True
                )
            else:
                raise HTTPException(status_code=500, detail="没有可用的说话人")
        
        return StreamingResponse(generate_data(model_output))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


def preload_default_speaker():
    """预加载默认的 speaker embedding"""
    global cosyvoice
    
    default_prompt_wav = os.path.join(PROJECT_ROOT, 'asset', 'zero_shot_prompt.wav')
    
    if not os.path.exists(default_prompt_wav):
        logging.warning(f"默认 prompt 音频不存在: {default_prompt_wav}")
        return False
    
    try:
        logging.info(f"预加载默认 speaker: {DEFAULT_SPEAKER_ID}")
        logging.info(f"Prompt 音频: {default_prompt_wav}")
        
        # 使用 add_zero_shot_spk 预加载 speaker embedding
        # 这样后续调用不需要每次都处理 prompt 音频
        result = cosyvoice.add_zero_shot_spk(
            DEFAULT_PROMPT_TEXT,
            default_prompt_wav,
            DEFAULT_SPEAKER_ID
        )
        
        if result:
            logging.info(f"✓ Speaker '{DEFAULT_SPEAKER_ID}' 预加载成功！")
            # 保存 speaker info 以便重启后使用
            cosyvoice.save_spkinfo()
            logging.info("Speaker info 已保存")
            return True
        else:
            logging.warning(f"Speaker 预加载失败")
            return False
            
    except Exception as e:
        logging.error(f"预加载 speaker 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    global cosyvoice, config
    
    parser = argparse.ArgumentParser(description='CosyVoice vLLM TTS 服务')
    parser.add_argument('--config', type=str, default=None, help='配置文件路径')
    parser.add_argument('--port', type=int, default=None, help='服务端口 (覆盖配置文件)')
    parser.add_argument('--model_dir', type=str, default=None, help='模型目录 (覆盖配置文件)')
    parser.add_argument('--gpu', type=int, default=None, help='GPU 设备 ID (覆盖配置文件)')
    args = parser.parse_args()
    
    # 加载配置
    config_path = args.config
    if config_path is None:
        config_path = os.path.join(PROJECT_ROOT, 'config.yaml')
    
    config = load_config(config_path)
    
    # 命令行参数覆盖配置文件
    if args.port is not None:
        config.server.port = args.port
    if args.model_dir is not None:
        config.model.model_dir = args.model_dir
    if args.gpu is not None:
        config.vllm.gpu_device = args.gpu
    
    # 设置 GPU 设备
    os.environ['CUDA_VISIBLE_DEVICES'] = str(config.vllm.gpu_device)
    
    # 设置日志级别
    log_level = getattr(logging, config.logging.level.upper(), logging.INFO)
    logging.basicConfig(level=log_level, format='%(asctime)s - %(levelname)s - %(message)s')
    
    model_dir = config.get_model_dir()
    
    logging.info("=" * 60)
    logging.info("CosyVoice vLLM TTS 服务启动")
    logging.info("=" * 60)
    logging.info(f"配置文件: {config_path}")
    logging.info(f"模型目录: {model_dir}")
    logging.info(f"GPU 设备: {config.vllm.gpu_device}")
    logging.info(f"GPU 显存利用率: {config.vllm.gpu_memory_utilization}")
    logging.info(f"服务端口: {config.server.port}")
    logging.info("=" * 60)
    
    # 初始化模型
    try:
        logging.info("正在加载 CosyVoice 模型...")
        cosyvoice = AutoModel(
            model_dir=model_dir,
            load_vllm=config.model.load_vllm,
            load_trt=config.model.load_trt,
            fp16=config.model.fp16,
            vllm_config=config.vllm
        )
        logging.info("模型加载完成！")
        
        # 预加载默认 speaker embedding
        logging.info("正在预加载默认 speaker...")
        preload_default_speaker()
        
    except Exception as e:
        logging.error(f"模型加载失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # 启动服务
    uvicorn.run(app, host=config.server.host, port=config.server.port)


if __name__ == '__main__':
    main()
