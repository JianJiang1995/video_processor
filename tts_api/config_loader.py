#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTS API 配置加载模块
统一管理 vLLM、服务器和模型配置
"""

import os
import yaml
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 50000


@dataclass
class VLLMConfig:
    gpu_memory_utilization: float = 0.4
    gpu_device: int = 0
    skip_tokenizer_init: bool = True
    enable_prompt_embeds: bool = True


@dataclass
class ModelConfig:
    model_dir: str = "pretrained_models/FunAudioLLM/Fun-CosyVoice3-0___5B-2512"
    load_vllm: bool = True
    load_trt: bool = False
    fp16: bool = True


@dataclass
class AudioConfig:
    sample_rate: int = 22050
    max_val: float = 0.8


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class TTSConfig:
    server: ServerConfig
    vllm: VLLMConfig
    model: ModelConfig
    audio: AudioConfig
    logging: LoggingConfig
    config_dir: str = ""  # 配置文件所在目录

    def get_model_dir(self) -> str:
        """获取模型目录的绝对路径"""
        model_dir = self.model.model_dir
        if os.path.isabs(model_dir):
            return model_dir
        return os.path.join(self.config_dir, model_dir)


def load_config(config_path: Optional[str] = None) -> TTSConfig:
    """
    加载配置文件
    
    Args:
        config_path: 配置文件路径，如果为 None 则使用默认路径
        
    Returns:
        TTSConfig 配置对象
    """
    if config_path is None:
        # 默认配置文件路径：tts_api/config.yaml
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    
    config_dir = os.path.dirname(os.path.abspath(config_path))
    
    if not os.path.exists(config_path):
        print(f"警告: 配置文件 {config_path} 不存在，使用默认配置")
        return TTSConfig(
            server=ServerConfig(),
            vllm=VLLMConfig(),
            model=ModelConfig(),
            audio=AudioConfig(),
            logging=LoggingConfig(),
            config_dir=config_dir
        )
    
    with open(config_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    
    server_data = data.get('server', {})
    vllm_data = data.get('vllm', {})
    model_data = data.get('model', {})
    audio_data = data.get('audio', {})
    logging_data = data.get('logging', {})
    
    return TTSConfig(
        server=ServerConfig(
            host=server_data.get('host', '0.0.0.0'),
            port=server_data.get('port', 50000)
        ),
        vllm=VLLMConfig(
            gpu_memory_utilization=vllm_data.get('gpu_memory_utilization', 0.4),
            gpu_device=vllm_data.get('gpu_device', 0),
            skip_tokenizer_init=vllm_data.get('skip_tokenizer_init', True),
            enable_prompt_embeds=vllm_data.get('enable_prompt_embeds', True)
        ),
        model=ModelConfig(
            model_dir=model_data.get('model_dir', 'pretrained_models/FunAudioLLM/Fun-CosyVoice3-0___5B-2512'),
            load_vllm=model_data.get('load_vllm', True),
            load_trt=model_data.get('load_trt', False),
            fp16=model_data.get('fp16', True)
        ),
        audio=AudioConfig(
            sample_rate=audio_data.get('sample_rate', 22050),
            max_val=audio_data.get('max_val', 0.8)
        ),
        logging=LoggingConfig(
            level=logging_data.get('level', 'INFO')
        ),
        config_dir=config_dir
    )


# 全局配置实例（延迟加载）
_config: Optional[TTSConfig] = None


def get_config(config_path: Optional[str] = None) -> TTSConfig:
    """获取全局配置实例"""
    global _config
    if _config is None:
        _config = load_config(config_path)
    return _config


def reload_config(config_path: Optional[str] = None) -> TTSConfig:
    """重新加载配置"""
    global _config
    _config = load_config(config_path)
    return _config


if __name__ == '__main__':
    # 测试配置加载
    config = load_config()
    print(f"服务器配置: {config.server}")
    print(f"vLLM配置: {config.vllm}")
    print(f"模型配置: {config.model}")
    print(f"模型目录: {config.get_model_dir()}")



