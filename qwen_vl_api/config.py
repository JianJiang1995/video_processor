"""
Qwen3-VL API 配置管理
"""
import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelConfig:
    path: str = "/data2/ckpt/Qwen3-VL-8B-Instruct"
    served_model_name: str = "Qwen3-VL-8B"
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
    dtype: str = "bfloat16"
    enable_prefix_caching: bool = False
    enforce_eager: bool = False
    limit_mm_per_prompt: str = '{"image": 10}'


@dataclass
class InferenceConfig:
    temperature: float = 0.7
    top_p: float = 0.8
    max_tokens: int = 2048


@dataclass 
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/qwen_vl_server.log"


@dataclass
class QwenVLConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    gpu: GPUConfig = field(default_factory=GPUConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    vllm: VLLMConfig = field(default_factory=VLLMConfig)
    inference: InferenceConfig = field(default_factory=InferenceConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def load_config(config_path: Optional[str] = None) -> QwenVLConfig:
    """加载配置文件"""
    if config_path is None:
        config_path = Path(__file__).parent / "config.yaml"
    
    config_path = Path(config_path)
    
    if not config_path.exists():
        print(f"Warning: Config file not found: {config_path}")
        return QwenVLConfig()
    
    with open(config_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    
    config = QwenVLConfig()
    
    if 'model' in data:
        config.model = ModelConfig(**data['model'])
    if 'gpu' in data:
        config.gpu = GPUConfig(**data['gpu'])
    if 'server' in data:
        config.server = ServerConfig(**data['server'])
    if 'vllm' in data:
        config.vllm = VLLMConfig(**data['vllm'])
    if 'inference' in data:
        config.inference = InferenceConfig(**data['inference'])
    if 'logging' in data:
        config.logging = LoggingConfig(**data['logging'])
    
    return config

