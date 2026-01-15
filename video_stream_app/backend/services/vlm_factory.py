"""
VLM Factory - Factory pattern for VLM client selection

根据 config.json 中的 summarization_provider 配置自动选择使用:
- 'glm': 本地 Qwen/GLM vLLM 服务（需要启动本地服务器）
- 'gemini': Google Gemini 3.0 Flash API（云端，只需 API Key）

使用方式:
    from services.vlm_factory import get_vlm_client, ensure_vlm_available

    # 获取当前配置的 VLM 客户端
    client = get_vlm_client()
    
    # 确保服务可用
    client = await ensure_vlm_available()
    
    # 使用统一接口
    result = await client.integrate_analysis_results(
        frame_analyses=...,
        images=...,
        history_context=...
    )
"""
import logging
from typing import Union, Optional

logger = logging.getLogger(__name__)

# Load config
from pathlib import Path
import json

CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"


def load_config() -> dict:
    """Load configuration from config.json"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    return {}


def get_summarization_provider() -> str:
    """
    获取当前配置的 VLM 提供商
    
    读取 config.json 中的 window_analysis.provider 配置
    
    Returns:
        'gemini' | 'qwen' | 'glm'
    """
    config = load_config()
    # 优先读取新的 window_analysis.provider 配置
    window_analysis = config.get("window_analysis", {})
    provider = window_analysis.get("provider", "")
    
    # 兼容旧的 summarization_provider 配置
    if not provider:
        provider = config.get("summarization_provider", "glm")
    
    return provider


def get_vlm_client():
    """
    获取当前配置的 VLM 客户端
    
    根据 config.json 中的 window_analysis.provider 配置返回对应的客户端实例。
    
    可选值:
        - 'gemini': Google Gemini 3.0 Flash API (云端)
        - 'qwen': 本地 Qwen3-VL-8B (vLLM)
        - 'glm': 本地 GLM-4.6V-Flash (vLLM)
    
    Returns:
        GeminiClient 或 GLMClient 实例
    """
    provider = get_summarization_provider()
    
    if provider == "gemini":
        from .gemini_client import get_gemini_client
        logger.info("[VLMFactory] Using Gemini 3.0 Flash API (cloud)")
        return get_gemini_client()
    elif provider in ("qwen", "glm"):
        from .glm_client import get_glm_client
        logger.info(f"[VLMFactory] Using local {provider.upper()} vLLM service")
        return get_glm_client()
    else:
        # 默认使用本地 GLM
        from .glm_client import get_glm_client
        logger.warning(f"[VLMFactory] Unknown provider '{provider}', falling back to GLM")
        return get_glm_client()


async def ensure_vlm_available():
    """
    确保 VLM 服务可用
    
    Returns:
        可用的 VLM 客户端
        
    Raises:
        HTTPException: 如果服务不可用
    """
    provider = get_summarization_provider()
    
    if provider == "gemini":
        from .gemini_client import ensure_gemini_available
        return await ensure_gemini_available()
    else:
        from .glm_client import ensure_glm_available
        return await ensure_glm_available()


async def check_vlm_health() -> dict:
    """
    检查当前 VLM 服务健康状态
    
    Returns:
        包含健康状态信息的字典
    """
    provider = get_summarization_provider()
    
    try:
        client = get_vlm_client()
        is_healthy = await client.check_health()
        
        if provider == "gemini":
            return {
                "provider": "gemini",
                "provider_name": "Google Gemini 3.0 Flash",
                "available": is_healthy,
                "model_name": client.model_name,
                "thinking_level": getattr(client, 'thinking_level', 'low'),
                "api_type": "cloud"
            }
        else:
            return {
                "provider": provider,
                "provider_name": f"Local {provider.upper()} vLLM",
                "available": is_healthy,
                "api_url": getattr(client, 'api_url', ''),
                "model_name": getattr(client, 'model_name', ''),
                "api_type": "local"
            }
    except Exception as e:
        return {
            "provider": provider,
            "available": False,
            "error": str(e)
        }


def get_history_manager(session_id: str):
    """
    获取会话对应的历史窗口管理器
    
    Args:
        session_id: 会话 ID
        
    Returns:
        WindowHistoryManager 实例
    """
    provider = get_summarization_provider()
    
    if provider == "gemini":
        from .gemini_client import get_history_manager as gemini_get_history_manager
        return gemini_get_history_manager(session_id)
    else:
        from .glm_client import get_history_manager as glm_get_history_manager
        return glm_get_history_manager(session_id)


def cleanup_session_resources(session_id: str) -> None:
    """
    清理会话相关资源
    
    Args:
        session_id: 会话 ID
    """
    provider = get_summarization_provider()
    
    if provider == "gemini":
        from .gemini_client import cleanup_session_resources as gemini_cleanup
        gemini_cleanup(session_id)
    else:
        from .glm_client import cleanup_session_resources as glm_cleanup
        glm_cleanup(session_id)

