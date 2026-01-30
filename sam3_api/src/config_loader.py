"""
配置加载模块
从 config.yaml 读取默认设置
"""
import os
import yaml
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# 配置文件路径
CONFIG_PATH = Path(__file__).parent / "config.yaml"

# 默认配置（当配置文件不存在时使用）
DEFAULT_CONFIG = {
    "server": {
        "host": "0.0.0.0",
        "port": 8000
    },
    "visualization": {
        "alpha": 0.4,
        "contour_thickness": 2,
        "return_base64": False
    },
    "tool_colors": {
        "grasper": [0, 255, 127],
        "bipolar": [255, 0, 255],
        "hook": [0, 165, 255],
        "scissors": [255, 255, 0],
        "clipper": [147, 20, 255],
        "irrigator": [255, 191, 0],
        "specimenbag": [0, 255, 255],
        "forceps": [50, 205, 50],
        "needle": [180, 105, 255],
        "suction": [250, 206, 135],
        "default": [128, 128, 128]
    },
    "instance_colors": [
        [0, 255, 127], [255, 0, 255], [0, 165, 255], [255, 255, 0],
        [147, 20, 255], [255, 191, 0], [0, 255, 255], [50, 205, 50]
    ],
    "model": {
        "checkpoint_path": None,
        "device": "cuda"
    }
}

_config: Optional[Dict] = None


def load_config() -> Dict:
    """加载配置文件"""
    global _config
    
    if _config is not None:
        return _config
    
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                _config = yaml.safe_load(f)
            print(f"[Config] 已加载配置文件: {CONFIG_PATH}")
        except Exception as e:
            print(f"[Config] 配置文件加载失败: {e}, 使用默认配置")
            _config = DEFAULT_CONFIG
    else:
        print(f"[Config] 配置文件不存在: {CONFIG_PATH}, 使用默认配置")
        _config = DEFAULT_CONFIG
    
    # 合并默认配置（确保所有字段都存在）
    _config = _merge_config(DEFAULT_CONFIG, _config)
    
    return _config


def _merge_config(default: Dict, override: Dict) -> Dict:
    """递归合并配置，override 覆盖 default"""
    result = default.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_config(result[key], value)
        else:
            result[key] = value
    return result


def get_visualization_config() -> Dict:
    """获取可视化配置"""
    config = load_config()
    return config.get("visualization", DEFAULT_CONFIG["visualization"])


def get_tool_colors() -> Dict[str, Tuple[int, int, int]]:
    """获取工具颜色配置，返回 {label: (B, G, R)} 字典"""
    config = load_config()
    colors = config.get("tool_colors", DEFAULT_CONFIG["tool_colors"])
    return {k: tuple(v) for k, v in colors.items()}


def get_instance_colors() -> List[Tuple[int, int, int]]:
    """获取实例颜色列表"""
    config = load_config()
    colors = config.get("instance_colors", DEFAULT_CONFIG["instance_colors"])
    return [tuple(c) for c in colors]


def get_server_config() -> Dict:
    """获取服务器配置"""
    config = load_config()
    return config.get("server", DEFAULT_CONFIG["server"])


def get_model_config() -> Dict:
    """获取模型配置"""
    config = load_config()
    return config.get("model", DEFAULT_CONFIG["model"])


def get_tool_color(label: str, instance_id: int = 0) -> Tuple[int, int, int]:
    """
    获取指定工具的颜色
    
    Args:
        label: 工具标签
        instance_id: 实例ID（当同一工具有多个实例时用于区分颜色）
    
    Returns:
        (B, G, R) 颜色元组
    """
    tool_colors = get_tool_colors()
    instance_colors = get_instance_colors()
    
    label_lower = label.lower().strip()
    
    # 精确匹配
    if label_lower in tool_colors:
        return tool_colors[label_lower]
    
    # 模糊匹配
    for key in tool_colors:
        if key in label_lower or label_lower in key:
            return tool_colors[key]
    
    # 使用实例颜色
    if instance_colors:
        return instance_colors[instance_id % len(instance_colors)]
    
    # 默认颜色
    return tool_colors.get("default", (128, 128, 128))


# 模块加载时预加载配置
load_config()





