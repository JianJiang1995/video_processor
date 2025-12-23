"""
Application Configuration
"""
import os
import json
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional, Dict, Any
from dotenv import load_dotenv

# Load .env from parent project directory
# config.py -> backend -> video_stream_app -> Video_Processor
PROJECT_ROOT = Path(__file__).parent.parent.parent  # /data2/jj/proj/video_processor
ENV_FILE = PROJECT_ROOT / ".env"
if ENV_FILE.exists():
    load_dotenv(ENV_FILE)
    print(f"[Config] Loaded environment from: {ENV_FILE}")

# Load config.json
APP_ROOT = Path(__file__).parent.parent  # video_stream_app
CONFIG_JSON = APP_ROOT / "config.json"


def load_json_config() -> Dict[str, Any]:
    """Load configuration from config.json"""
    if CONFIG_JSON.exists():
        with open(CONFIG_JSON, 'r') as f:
            config = json.load(f)
            print(f"[Config] Loaded config from: {CONFIG_JSON}")
            return config
    return {}


# Load JSON config
_json_config = load_json_config()


def get_mysql_url() -> str:
    """Build MySQL connection URL from config.json"""
    mysql_config = _json_config.get("database", {}).get("mysql", {})
    host = mysql_config.get("host", "localhost")
    port = mysql_config.get("port", 3306)
    user = mysql_config.get("user", "root")
    password = mysql_config.get("password", "")
    database = mysql_config.get("database", "video_analyzer")
    
    if password:
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"
    return f"mysql+pymysql://{user}@{host}:{port}/{database}?charset=utf8mb4"


class Settings(BaseSettings):
    """Application settings"""
    
    # Application
    APP_NAME: str = "Video Stream Analyzer"
    DEBUG: bool = True
    VLM_PRELOAD: bool = False
    
    # Database (MySQL)
    DATABASE_URL: str = get_mysql_url()
    
    # Video Processing (from config.json)
    WINDOW_DURATION: float = _json_config.get("video_processing", {}).get("window_duration", 5.0)
    SAMPLE_INTERVAL: float = _json_config.get("video_processing", {}).get("sample_interval", 1.0)
    
    # OpenAI/GPT Settings (loaded from /data2/jj/proj/video_processor/.env)
    OPENAI_API_KEY: Optional[str] = os.environ.get("OPENAI_API_KEY")
    OPENAI_BASE_URL: Optional[str] = os.environ.get("OPENAI_BASE_URL")
    GPT_MODEL: str = "gpt-4o"
    
    # SurgR1 Service (surgical image analysis with 3 questions)
    SURGR1_API_URL: str = _json_config.get("services", {}).get("surgr1", {}).get("api_url", "http://localhost:9003")
    
    # GLM-4.6V-Flash Service (for summarization and integration)
    GLM_API_URL: str = _json_config.get("services", {}).get("glm", {}).get("api_url", "http://localhost:8000/v1")
    GLM_MODEL_NAME: str = _json_config.get("services", {}).get("glm", {}).get("model_name", "GLM-4.6V-Flash")
    GLM_TEMPERATURE: float = _json_config.get("services", {}).get("glm", {}).get("temperature", 0.7)
    GLM_MAX_TOKENS: int = _json_config.get("services", {}).get("glm", {}).get("max_tokens", 1000)
    
    # SAM3 Service (bbox to mask segmentation)
    SAM3_API_URL: str = _json_config.get("services", {}).get("sam3", {}).get("api_url", "http://localhost:9004")
    
    # TTS CosyVoice Service (Chinese text-to-speech)
    TTS_API_URL: str = _json_config.get("services", {}).get("tts", {}).get("api_url", "http://localhost:50000")
    TTS_SPEAKER: str = _json_config.get("services", {}).get("tts", {}).get("speaker", "中文女")
    TTS_ENABLED: bool = True
    TTS_VOICE: str = "alloy"  # OpenAI TTS voice (fallback)
    
    # ASR FunASR Service (speech recognition)
    ASR_API_URL: str = _json_config.get("services", {}).get("asr", {}).get("api_url", "http://localhost:8765")
    ASR_WS_URL: str = _json_config.get("services", {}).get("asr", {}).get("ws_url", "ws://localhost:8765")
    ASR_KEYWORDS: list = _json_config.get("services", {}).get("asr", {}).get("keywords", ["你好小助", "小助小助"])
    ASR_STANDBY_TIMEOUT: int = _json_config.get("services", {}).get("asr", {}).get("standby_timeout", 180)
    
    # Legacy VLM settings (kept for compatibility)
    VLM_API_URL: str = _json_config.get("services", {}).get("surgr1", {}).get("api_url", "http://localhost:9003")
    VLM_MODEL_NAME: str = "SurgR1"
    VLM_MODEL_PATH: str = ""
    VLM_MODEL_PORT: int = 9003
    
    # SAM2 Settings (legacy, use SAM3 instead)
    SAM2_MODEL_PATH: Optional[str] = None
    SAM2_ENABLED: bool = False
    
    # File paths
    UPLOAD_DIR: Path = Path("./uploads")
    OUTPUT_DIR: Path = Path("./output")
    
    # CORS
    CORS_ORIGINS: list = ["http://localhost:5174", "http://127.0.0.1:5174", "http://localhost:3000", "http://localhost:5176"]
    
    class Config:
        env_file = ".env"


settings = Settings()

# Ensure directories exist
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# System prompt for surgical video analysis (based on annotate_video logic)
ANALYSIS_SYSTEM_PROMPT = """You are an expert surgical video analyst specializing in laparoscopic procedures. Your task is to analyze a video segment (5-second window) and generate a concise narrative summary.

## Your Task

Given frames from a 5-second video window, you must:
1. **Synthesize Information**: Understand the temporal progression across frames
2. **Generate Narrative**: Produce a clear, concise description of what happens in this segment

## Output Format

Provide a single paragraph (2-4 sentences) describing:
- Current surgical phase or action
- Tools visible and their usage
- Key observations about the procedure

## Guidelines

1. **Be Concise**: Focus on the most important observations
2. **Use Temporal Markers**: "Initially", "Then", "Throughout"
3. **Maintain Clinical Accuracy**: Use proper surgical terminology
4. **Be Confident**: Write as an observer seeing one clear reality

## Example Output

"This segment shows the Calot Triangle Dissection phase. A grasper retracts the gallbladder while a hook dissects the tissue plane, exposing the cystic duct. The surgeon maintains careful dissection around the critical structures."
"""

