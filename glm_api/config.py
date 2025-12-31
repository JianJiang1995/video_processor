#!/usr/bin/env python3
"""
GLM API 配置加载模块
"""
import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


# 配置文件路径
CONFIG_DIR = Path(__file__).parent
DEFAULT_CONFIG_FILE = CONFIG_DIR / "config.yaml"


@dataclass
class ModelConfig:
    path: str = "/data2/ckpt/GLM-4.6V-Flash"
    served_model_name: str = "GLM-4.6V-Flash"
    trust_remote_code: bool = True


@dataclass
class GPUConfig:
    device_ids: str = "1"


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass
class VLLMConfig:
    gpu_memory_utilization: float = 0.9
    max_model_len: int = 32768
    max_num_seqs: int = 64
    tensor_parallel_size: int = 1
    dtype: str = "auto"
    enable_prefix_caching: bool = False
    enforce_eager: bool = False


@dataclass
class InferenceConfig:
    temperature: float = 0.8
    top_p: float = 0.6
    top_k: int = 2
    repetition_penalty: float = 1.1
    max_tokens: int = 4096


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/glm_server.log"


@dataclass
class GLMConfig:
    """GLM API 完整配置"""
    model: ModelConfig = field(default_factory=ModelConfig)
    gpu: GPUConfig = field(default_factory=GPUConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    vllm: VLLMConfig = field(default_factory=VLLMConfig)
    inference: InferenceConfig = field(default_factory=InferenceConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    
    @classmethod
    def from_yaml(cls, config_path: str = None) -> "GLMConfig":
        """从YAML文件加载配置"""
        if config_path is None:
            config_path = DEFAULT_CONFIG_FILE
        
        config_path = Path(config_path)
        if not config_path.exists():
            print(f"Warning: Config file not found: {config_path}, using defaults")
            return cls()
        
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f) or {}
        
        return cls(
            model=ModelConfig(**data.get('model', {})),
            gpu=GPUConfig(**data.get('gpu', {})),
            server=ServerConfig(**data.get('server', {})),
            vllm=VLLMConfig(**data.get('vllm', {})),
            inference=InferenceConfig(**data.get('inference', {})),
            logging=LoggingConfig(**data.get('logging', {})),
        )
    
    @property
    def api_url(self) -> str:
        """获取API URL"""
        return f"http://localhost:{self.server.port}/v1"


def load_config(config_path: str = None) -> GLMConfig:
    """加载配置的便捷函数"""
    return GLMConfig.from_yaml(config_path)


# 全局配置实例
_config: Optional[GLMConfig] = None


def get_config() -> GLMConfig:
    """获取全局配置实例"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


if __name__ == "__main__":
    # 测试配置加载
    config = load_config()
    print(f"Model path: {config.model.path}")
    print(f"GPU devices: {config.gpu.device_ids}")
    print(f"Server: {config.server.host}:{config.server.port}")
    print(f"API URL: {config.api_url}")

