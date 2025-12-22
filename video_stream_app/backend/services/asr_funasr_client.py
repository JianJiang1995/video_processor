"""
FunASR API Client - Speech Recognition Service
Calls the external FunASR API for Chinese speech recognition.
Supports:
1. WebSocket real-time streaming recognition
2. HTTP REST API file recognition
3. Wake word detection for monitoring mode
"""
import asyncio
import json
import logging
import base64
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
import httpx
import websockets
from datetime import datetime

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


class FunASRClient:
    """
    FunASR API Client
    
    Calls the external FunASR service for Chinese speech recognition.
    
    Features:
    - Long-term monitoring mode with wake word detection
    - Standby mode after wake word (N minutes)
    - Real-time streaming recognition via WebSocket
    - File-based recognition via HTTP API
    """
    
    def __init__(
        self,
        api_url: str = None,
        ws_url: str = None,
        keywords: List[str] = None,
        standby_timeout: int = None,
        timeout: float = 60.0
    ):
        config = load_config()
        asr_config = config.get("services", {}).get("asr", {})
        
        self.api_url = (api_url or asr_config.get("api_url", "http://localhost:8765")).rstrip('/')
        self.ws_url = (ws_url or asr_config.get("ws_url", "ws://localhost:8765")).rstrip('/')
        self.keywords = keywords or asr_config.get("keywords", ["你好小助", "小助小助", "开始识别"])
        self.standby_timeout = standby_timeout or asr_config.get("standby_timeout", 180)  # 3 minutes
        self.timeout = timeout
        self._client = None
        
        # State for monitoring mode
        self.is_monitoring = False
        self.is_activated = False
        self.last_activation_time = None
        
        logger.info(f"[FunASR] Initialized with API: {self.api_url}")
        logger.info(f"[FunASR] Keywords: {self.keywords}")
        logger.info(f"[FunASR] Standby timeout: {self.standby_timeout}s")
    
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
        """Check if ASR service is available"""
        try:
            response = await self.client.get(f"{self.api_url}/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"[FunASR] Health check failed: {e}")
            return False
    
    async def transcribe_file(
        self,
        audio_file_path: str
    ) -> Dict[str, Any]:
        """
        Transcribe an audio file.
        
        Args:
            audio_file_path: Path to the audio file
            
        Returns:
            Dict with transcription result
        """
        try:
            with open(audio_file_path, 'rb') as f:
                files = {
                    "file": (Path(audio_file_path).name, f, "audio/wav")
                }
                
                response = await self.client.post(
                    f"{self.api_url}/api/transcribe/file",
                    files=files
                )
            
            response.raise_for_status()
            data = response.json()
            
            return {
                "success": data.get("success", False),
                "text": data.get("data", {}).get("text", ""),
                "duration": data.get("data", {}).get("duration", 0),
                "processing_time": data.get("data", {}).get("processing_time", 0),
                "keyword_detected": data.get("data", {}).get("keyword_detected")
            }
            
        except Exception as e:
            logger.error(f"[FunASR] File transcription failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "text": ""
            }
    
    async def transcribe_audio_data(
        self,
        audio_data: bytes,
        sample_rate: int = 16000,
        audio_format: str = "pcm"
    ) -> Dict[str, Any]:
        """
        Transcribe audio data.
        
        Args:
            audio_data: Raw audio bytes
            sample_rate: Sample rate (default 16000)
            audio_format: Audio format (pcm, wav, mp3, etc.)
            
        Returns:
            Dict with transcription result
        """
        try:
            audio_base64 = base64.b64encode(audio_data).decode()
            
            payload = {
                "audio_data": audio_base64,
                "sample_rate": sample_rate,
                "format": audio_format
            }
            
            response = await self.client.post(
                f"{self.api_url}/api/transcribe",
                json=payload
            )
            
            response.raise_for_status()
            data = response.json()
            
            return {
                "success": data.get("success", False),
                "text": data.get("data", {}).get("text", ""),
                "duration": data.get("data", {}).get("duration", 0),
                "processing_time": data.get("data", {}).get("processing_time", 0),
                "keyword_detected": data.get("data", {}).get("keyword_detected")
            }
            
        except Exception as e:
            logger.error(f"[FunASR] Audio transcription failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "text": ""
            }
    
    async def get_keyword_config(self) -> Dict[str, Any]:
        """Get current keyword configuration"""
        try:
            response = await self.client.get(f"{self.api_url}/api/keyword/config")
            response.raise_for_status()
            data = response.json()
            return data.get("data", {})
        except Exception as e:
            logger.error(f"[FunASR] Failed to get keyword config: {e}")
            return {}
    
    async def set_keyword_config(
        self,
        keywords: List[str],
        threshold: float = 0.6
    ) -> Dict[str, Any]:
        """Set keyword configuration"""
        try:
            payload = {
                "keywords": keywords,
                "threshold": threshold
            }
            
            response = await self.client.post(
                f"{self.api_url}/api/keyword/config",
                json=payload
            )
            
            response.raise_for_status()
            data = response.json()
            return data.get("data", {})
            
        except Exception as e:
            logger.error(f"[FunASR] Failed to set keyword config: {e}")
            return {}
    
    async def get_transcript_history(
        self,
        limit: int = 50
    ) -> Dict[str, Any]:
        """Get transcript history"""
        try:
            response = await self.client.get(
                f"{self.api_url}/api/history",
                params={"limit": limit}
            )
            response.raise_for_status()
            data = response.json()
            return data.get("data", {})
        except Exception as e:
            logger.error(f"[FunASR] Failed to get history: {e}")
            return {}
    
    async def clear_history(self) -> bool:
        """Clear transcript history"""
        try:
            response = await self.client.delete(f"{self.api_url}/api/history")
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[FunASR] Failed to clear history: {e}")
            return False
    
    def get_stream_ws_url(self) -> str:
        """Get WebSocket URL for streaming recognition"""
        return f"{self.ws_url}/ws/stream"
    
    def get_wakeword_ws_url(self) -> str:
        """Get WebSocket URL for wake word mode"""
        return f"{self.ws_url}/ws/wakeword"
    
    async def create_stream_connection(
        self,
        on_result: Callable[[Dict[str, Any]], None] = None,
        on_error: Callable[[Exception], None] = None
    ):
        """
        Create a WebSocket connection for streaming recognition.
        
        This is a generator that yields transcription results.
        
        Usage:
            async for result in client.create_stream_connection():
                print(result["text"])
        """
        try:
            async with websockets.connect(self.get_stream_ws_url()) as ws:
                # Start session
                await ws.send(json.dumps({"action": "start", "config": {}}))
                
                while True:
                    message = await ws.recv()
                    data = json.loads(message)
                    
                    if data.get("type") == "event":
                        event = data.get("data", {}).get("event", "")
                        if event == "session_completed":
                            break
                    
                    if data.get("type") == "result":
                        result = {
                            "text": data.get("data", {}).get("text", ""),
                            "is_final": data.get("data", {}).get("is_final", False),
                            "keyword_detected": data.get("data", {}).get("keyword_detected"),
                            "timestamp": data.get("timestamp")
                        }
                        
                        if on_result:
                            on_result(result)
                        yield result
                        
        except Exception as e:
            logger.error(f"[FunASR] Stream connection error: {e}")
            if on_error:
                on_error(e)
            raise


class ASRMonitoringSession:
    """
    ASR Monitoring Session
    
    Manages a long-running ASR session with:
    - Continuous monitoring for wake words
    - Standby mode after activation (N minutes)
    - Automatic return to monitoring mode after timeout
    """
    
    def __init__(
        self,
        asr_client: FunASRClient,
        standby_timeout: int = 180,
        on_wakeword: Callable[[Dict[str, Any]], None] = None,
        on_transcript: Callable[[Dict[str, Any]], None] = None,
        on_mode_change: Callable[[str], None] = None
    ):
        self.client = asr_client
        self.standby_timeout = standby_timeout
        self.on_wakeword = on_wakeword
        self.on_transcript = on_transcript
        self.on_mode_change = on_mode_change
        
        self.mode = "idle"  # idle, monitoring, standby
        self.is_running = False
        self.last_activity_time = None
        self._ws = None
    
    async def start(self):
        """Start the monitoring session"""
        self.is_running = True
        self._set_mode("monitoring")
        
        try:
            async with websockets.connect(
                self.client.get_wakeword_ws_url()
            ) as ws:
                self._ws = ws
                
                await ws.send(json.dumps({"action": "start_listening"}))
                
                while self.is_running:
                    try:
                        message = await asyncio.wait_for(
                            ws.recv(),
                            timeout=1.0
                        )
                        
                        data = json.loads(message)
                        await self._handle_message(data)
                        
                    except asyncio.TimeoutError:
                        # Check standby timeout
                        if self.mode == "standby" and self.last_activity_time:
                            elapsed = (datetime.now() - self.last_activity_time).total_seconds()
                            if elapsed > self.standby_timeout:
                                self._set_mode("monitoring")
                        continue
                        
        except Exception as e:
            logger.error(f"[ASRMonitoring] Session error: {e}")
            raise
        finally:
            self._ws = None
            self._set_mode("idle")
    
    async def stop(self):
        """Stop the monitoring session"""
        self.is_running = False
        if self._ws:
            try:
                await self._ws.send(json.dumps({"action": "stop"}))
            except:
                pass
    
    async def send_audio(self, audio_data: bytes):
        """Send audio data to the WebSocket"""
        if self._ws:
            audio_base64 = base64.b64encode(audio_data).decode()
            await self._ws.send(json.dumps({
                "action": "audio",
                "audio_data": audio_base64
            }))
    
    async def _handle_message(self, data: Dict[str, Any]):
        """Handle incoming WebSocket message"""
        msg_type = data.get("type", "")
        
        if msg_type == "wakeword_detected":
            self._set_mode("standby")
            self.last_activity_time = datetime.now()
            
            if self.on_wakeword:
                self.on_wakeword(data.get("data", {}))
        
        elif msg_type == "result":
            result = data.get("data", {})
            self.last_activity_time = datetime.now()
            
            if self.on_transcript:
                self.on_transcript(result)
        
        elif msg_type == "back_to_listening":
            self._set_mode("monitoring")
    
    def _set_mode(self, mode: str):
        """Set the current mode"""
        if mode != self.mode:
            self.mode = mode
            logger.info(f"[ASRMonitoring] Mode changed to: {mode}")
            
            if self.on_mode_change:
                self.on_mode_change(mode)


# Global client instance
_asr_client: Optional[FunASRClient] = None


def get_asr_client() -> FunASRClient:
    """Get the global ASR client instance"""
    global _asr_client
    if _asr_client is None:
        _asr_client = FunASRClient()
    return _asr_client


async def ensure_asr_available() -> FunASRClient:
    """Get client and verify service is available"""
    client = get_asr_client()
    
    is_healthy = await client.check_health()
    if not is_healthy:
        logger.warning("[FunASR] ASR service may not be available")
    
    return client



