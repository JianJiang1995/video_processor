"""
CosyVoice TTS API Client - Text-to-Speech Service
Calls the external CosyVoice TTS API for Chinese text-to-speech.
Outputs Chinese female voice by default.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any
import httpx
import base64

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load config
CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"


def load_config() -> dict:
    """Load configuration from config.json"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    return {}


class CosyVoiceTTSClient:
    """
    CosyVoice TTS API Client
    
    Calls the external CosyVoice TTS service for text-to-speech synthesis.
    Supports Chinese female voice output by default.
    
    Available speakers:
    - 中文女 (Chinese Female)
    - 中文男 (Chinese Male)
    - 英文女 (English Female)
    - 英文男 (English Male)
    - 日语男 (Japanese Male)
    - 粤语女 (Cantonese Female)
    - 韩语女 (Korean Female)
    """
    
    SPEAKERS = {
        "zh_female": "中文女",
        "zh_male": "中文男",
        "en_female": "英文女",
        "en_male": "英文男",
        "ja_male": "日语男",
        "yue_female": "粤语女",
        "ko_female": "韩语女"
    }
    
    def __init__(
        self,
        api_url: str = None,
        default_speaker: str = None,
        timeout: float = 60.0
    ):
        config = load_config()
        tts_config = config.get("services", {}).get("tts", {})
        
        self.api_url = (api_url or tts_config.get("api_url", "http://localhost:50000")).rstrip('/')
        self.default_speaker = default_speaker or tts_config.get("speaker", "中文女")
        self.timeout = timeout
        self._client = None
        
        logger.info(f"[CosyVoiceTTS] Initialized with API: {self.api_url}")
        logger.info(f"[CosyVoiceTTS] Default speaker: {self.default_speaker}")
    
    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client
    
    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def check_health(self) -> bool:
        """Check if TTS service is available"""
        try:
            response = await self.client.get(f"{self.api_url}/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"[CosyVoiceTTS] Health check failed: {e}")
            return False
    
    async def synthesize(
        self,
        text: str,
        speaker: Optional[str] = None,
        output_file: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Synthesize speech from text using SFT (pretrained) mode.
        
        Args:
            text: Text to synthesize (Chinese text recommended)
            speaker: Speaker voice (default: 中文女)
            output_file: Optional output file path (if provided, saves audio to file)
            
        Returns:
            Dict with audio data (bytes), duration, and metadata
        """
        if not text.strip():
            return {
                "success": False,
                "error": "Empty text provided"
            }
        
        speaker = speaker or self.default_speaker
        
        try:
            payload = {
                "tts_text": text,
                "spk_id": speaker
            }
            
            response = await self.client.post(
                f"{self.api_url}/inference_sft",
                data=payload
            )
            response.raise_for_status()
            
            # Get audio bytes
            audio_bytes = response.content
            
            # Convert to base64 for web playback
            audio_base64 = base64.b64encode(audio_bytes).decode()
            
            # Save to file if requested
            file_path = None
            if output_file:
                file_path = Path(output_file)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(file_path, 'wb') as f:
                    f.write(audio_bytes)
                logger.info(f"[CosyVoiceTTS] Saved audio to: {file_path}")
            
            # Estimate duration (rough: ~5 chars per second for Chinese)
            estimated_duration = len(text) / 5.0
            
            return {
                "success": True,
                "audio_bytes": audio_bytes,
                "audio_base64": audio_base64,
                "audio_format": "wav",
                "sample_rate": 22050,
                "file_path": str(file_path) if file_path else None,
                "speaker": speaker,
                "text_length": len(text),
                "estimated_duration": estimated_duration
            }
            
        except Exception as e:
            logger.error(f"[CosyVoiceTTS] Synthesis failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def clone_voice(
        self,
        text: str,
        prompt_text: str,
        prompt_wav_path: str,
        output_file: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Clone voice using 3-second voice cloning mode (zero-shot).
        
        Args:
            text: Text to synthesize
            prompt_text: Transcript of the prompt audio
            prompt_wav_path: Path to the prompt audio file
            output_file: Optional output file path
            
        Returns:
            Dict with audio data and metadata
        """
        if not text.strip() or not prompt_text.strip():
            return {
                "success": False,
                "error": "Text and prompt_text are required"
            }
        
        try:
            payload = {
                "tts_text": text,
                "prompt_text": prompt_text
            }
            
            with open(prompt_wav_path, 'rb') as f:
                files = {
                    "prompt_wav": ("prompt.wav", f, "application/octet-stream")
                }
                
                response = await self.client.post(
                    f"{self.api_url}/inference_zero_shot",
                    data=payload,
                    files=files
                )
            
            response.raise_for_status()
            
            audio_bytes = response.content
            audio_base64 = base64.b64encode(audio_bytes).decode()
            
            file_path = None
            if output_file:
                file_path = Path(output_file)
                file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(file_path, 'wb') as f:
                    f.write(audio_bytes)
            
            return {
                "success": True,
                "audio_bytes": audio_bytes,
                "audio_base64": audio_base64,
                "audio_format": "wav",
                "sample_rate": 22050,
                "file_path": str(file_path) if file_path else None,
                "mode": "zero_shot",
                "text_length": len(text)
            }
            
        except Exception as e:
            logger.error(f"[CosyVoiceTTS] Voice cloning failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def synthesize_summary(
        self,
        summary: str,
        window_id: int,
        session_id: str,
        output_dir: str = None
    ) -> Dict[str, Any]:
        """
        Synthesize audio for a window summary.
        
        Args:
            summary: Summary text
            window_id: Window identifier
            session_id: Session identifier
            output_dir: Optional output directory
            
        Returns:
            Dict with synthesis result
        """
        if output_dir:
            output_file = Path(output_dir) / f"{session_id}_window_{window_id:04d}.wav"
        else:
            output_file = None
        
        result = await self.synthesize(
            text=summary,
            speaker=self.default_speaker,
            output_file=str(output_file) if output_file else None
        )
        
        result['window_id'] = window_id
        result['session_id'] = session_id
        
        return result
    
    def get_available_speakers(self) -> Dict[str, str]:
        """Get available TTS speakers"""
        return self.SPEAKERS.copy()


# Global client instance
_tts_client: Optional[CosyVoiceTTSClient] = None


def get_tts_client() -> CosyVoiceTTSClient:
    """Get the global TTS client instance"""
    global _tts_client
    if _tts_client is None:
        _tts_client = CosyVoiceTTSClient()
    return _tts_client


async def ensure_tts_available() -> CosyVoiceTTSClient:
    """Get client and verify service is available"""
    client = get_tts_client()
    
    is_healthy = await client.check_health()
    if not is_healthy:
        logger.warning("[CosyVoiceTTS] TTS service may not be available")
    
    return client



