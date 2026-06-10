"""
Text-to-Speech Service
Converts summary text to audio using OpenAI TTS or other providers
"""
import asyncio
import base64
from io import BytesIO
from pathlib import Path
from typing import Optional, Dict, Any, Union
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TTSService:
    """
    Text-to-Speech service for reading summaries aloud
    
    Supports:
    - OpenAI TTS API
    - Local TTS fallback (future)
    """
    
    VOICES = {
        "alloy": "Neutral, balanced voice",
        "echo": "Warm, engaging voice",
        "fable": "British accent, narrative style",
        "onyx": "Deep, authoritative voice",
        "nova": "Friendly, conversational voice",
        "shimmer": "Clear, professional voice"
    }
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        voice: str = "alloy",
        model: str = "tts-1",
        output_dir: Optional[Path] = None
    ):
        """
        Initialize TTS Service
        
        Args:
            api_key: OpenAI API key
            base_url: Optional custom API base URL
            voice: Voice to use (alloy, echo, fable, onyx, nova, shimmer)
            model: TTS model (tts-1, tts-1-hd)
            output_dir: Directory to save audio files
        """
        self.api_key = api_key
        self.base_url = base_url
        self.voice = voice
        self.model = model
        self.output_dir = Path(output_dir) if output_dir else Path("./tts_output")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self._client = None
    
    def _get_client(self):
        """Get or create OpenAI client"""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                
                kwargs = {}
                if self.api_key:
                    kwargs['api_key'] = self.api_key
                if self.base_url:
                    kwargs['base_url'] = self.base_url
                
                self._client = AsyncOpenAI(**kwargs)
            except ImportError:
                raise RuntimeError("OpenAI package not installed. Run: pip install openai")
        
        return self._client
    
    async def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        speed: float = 1.0,
        save_to_file: bool = False,
        filename: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Synthesize speech from text
        
        Args:
            text: Text to convert to speech
            voice: Override default voice
            speed: Speech speed (0.25 to 4.0)
            save_to_file: Whether to save audio to file
            filename: Custom filename (without extension)
            
        Returns:
            Dict with audio data and metadata
        """
        if not text.strip():
            return {
                "success": False,
                "error": "Empty text provided"
            }
        
        voice = voice or self.voice
        
        try:
            client = self._get_client()
            
            # Call TTS API
            response = await client.audio.speech.create(
                model=self.model,
                voice=voice,
                input=text,
                speed=speed
            )
            
            # Get audio bytes
            audio_bytes = response.content
            
            # Optionally save to file
            file_path = None
            if save_to_file:
                if filename is None:
                    import time
                    filename = f"tts_{int(time.time())}"
                
                file_path = self.output_dir / f"{filename}.mp3"
                with open(file_path, 'wb') as f:
                    f.write(audio_bytes)
                logger.info(f"Saved TTS audio to: {file_path}")
            
            # Convert to base64 for web playback
            audio_base64 = base64.b64encode(audio_bytes).decode()
            
            return {
                "success": True,
                "audio_base64": audio_base64,
                "audio_format": "mp3",
                "file_path": str(file_path) if file_path else None,
                "voice": voice,
                "text_length": len(text),
                "estimated_duration": len(text) / 15  # Rough estimate: 15 chars/sec
            }
            
        except Exception as e:
            logger.error(f"TTS synthesis error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def synthesize_summary(
        self,
        summary: str,
        window_id: int,
        session_id: str,
        save_to_file: bool = True
    ) -> Dict[str, Any]:
        """
        Synthesize audio for a window summary
        
        Args:
            summary: Summary text
            window_id: Window identifier
            session_id: Session identifier
            save_to_file: Whether to save audio file
            
        Returns:
            Dict with synthesis result
        """
        filename = f"{session_id}_window_{window_id:04d}"
        
        result = await self.synthesize(
            text=summary,
            save_to_file=save_to_file,
            filename=filename
        )
        
        result['window_id'] = window_id
        result['session_id'] = session_id
        
        return result
    
    async def batch_synthesize(
        self,
        texts: list,
        max_concurrent: int = 3
    ) -> list:
        """
        Synthesize multiple texts concurrently
        
        Args:
            texts: List of text strings
            max_concurrent: Maximum concurrent API calls
            
        Returns:
            List of synthesis results
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def synthesize_with_semaphore(text: str, idx: int):
            async with semaphore:
                result = await self.synthesize(text, filename=f"batch_{idx:04d}")
                result['index'] = idx
                return result
        
        tasks = [synthesize_with_semaphore(text, i) for i, text in enumerate(texts)]
        results = await asyncio.gather(*tasks)
        
        return results
    
    def get_available_voices(self) -> Dict[str, str]:
        """Get available TTS voices"""
        return self.VOICES.copy()
    
    def set_voice(self, voice: str):
        """Set default voice"""
        if voice not in self.VOICES:
            raise ValueError(f"Unknown voice: {voice}. Available: {list(self.VOICES.keys())}")
        self.voice = voice


# Factory function
def create_tts_service(
    api_key: str = None,
    base_url: str = None,
    voice: str = "alloy",
    output_dir: str = None
) -> TTSService:
    """Create TTS service instance"""
    return TTSService(
        api_key=api_key,
        base_url=base_url,
        voice=voice,
        output_dir=Path(output_dir) if output_dir else None
    )




