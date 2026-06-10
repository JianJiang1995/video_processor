from .video_processor import VideoProcessor, ProcessingState
from .gpt_summarizer import GPTSummarizer
from .sam2_service import SAM2Service
from .tts_service import TTSService

# Legacy model service (now uses SurgR1 client internally)
try:
    from .model_service import VLMModelClient, get_model_service, ensure_model_loaded
except ImportError:
    # Fallback to surgr1 client
    from .surgr1_client import SurgR1Client as VLMModelClient, get_surgr1_client as get_model_service, ensure_surgr1_available as ensure_model_loaded

# New external API clients
from .surgr1_client import SurgR1Client, get_surgr1_client, ensure_surgr1_available
from .sam3_client import SAM3Client, get_sam3_client, ensure_sam3_available
from .tts_cosyvoice_client import CosyVoiceTTSClient, get_tts_client, ensure_tts_available
from .asr_funasr_client import FunASRClient, get_asr_client, ensure_asr_available, ASRMonitoringSession
from .glm_client import GLMClient, get_glm_client, ensure_glm_available

# Gemini 3.0 Flash - Google multimodal analysis
from .gemini_client import GeminiClient, get_gemini_client, ensure_gemini_available

# VLM Factory - Unified interface for window analysis (Gemini/Qwen/GLM)
from .vlm_factory import (
    get_vlm_client, ensure_vlm_available, check_vlm_health,
    get_summarization_provider, get_history_manager, cleanup_session_resources
)

# MySQL database service for analysis storage
from .mysql_service import MySQLService, get_mysql_service, init_mysql

# Conversation service (integrates ASR, GLM, MySQL)
from .conversation_service import ConversationService, create_conversation_service

__all__ = [
    # Core services
    "VideoProcessor", "ProcessingState",
    "GPTSummarizer",
    "SAM2Service",
    "TTSService",
    "VLMModelClient", "get_model_service", "ensure_model_loaded",
    
    # SurgR1 - Surgical image analysis
    "SurgR1Client", "get_surgr1_client", "ensure_surgr1_available",
    
    # SAM3 - Segmentation with bbox
    "SAM3Client", "get_sam3_client", "ensure_sam3_available",
    
    # TTS CosyVoice - Chinese text-to-speech
    "CosyVoiceTTSClient", "get_tts_client", "ensure_tts_available",
    
    # ASR FunASR - Speech recognition
    "FunASRClient", "get_asr_client", "ensure_asr_available", "ASRMonitoringSession",
    
    # GLM - Text summarization
    "GLMClient", "get_glm_client", "ensure_glm_available",
    
    # Gemini - Google multimodal analysis
    "GeminiClient", "get_gemini_client", "ensure_gemini_available",
    
    # VLM Factory - Unified interface (selects Gemini/Qwen/GLM based on config)
    "get_vlm_client", "ensure_vlm_available", "check_vlm_health",
    "get_summarization_provider", "get_history_manager", "cleanup_session_resources",
    
    # MySQL - Analysis storage
    "MySQLService", "get_mysql_service", "init_mysql",
    
    # Conversation - Integrated ASR/GLM/MySQL service
    "ConversationService", "create_conversation_service",
]
