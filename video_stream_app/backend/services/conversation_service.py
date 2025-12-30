"""
Conversation Service
Integrates ASR (wake word monitoring), GLM (response generation), and MySQL (context storage).

Flow:
1. Continuous wake word monitoring via ASR
2. When wake word detected, enter active listening mode
3. Collect user input, validate it's not noise
4. Send user query + surgical context from MySQL to GLM
5. Return GLM response (with TTS audio)
6. If no valid input for N seconds, return to monitoring mode
"""
import asyncio
import json
import logging
import time
import re
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ConversationService:
    """
    Manages the conversation flow between ASR, GLM, and MySQL.
    """
    
    # Minimum length for valid user input (filter out noise)
    # Increased to 3 to filter out short hallucinations like "对。"
    MIN_VALID_INPUT_LENGTH = 3
    
    # Patterns that indicate noise or invalid input
    NOISE_PATTERNS = [
        r'^[啊嗯呃哦嘿哈呢吧的了么嘛]+[。，！？]?$',  # Just interjections with optional punctuation
        r'^\.+$',  # Just dots
        r'^\s*$',  # Empty or whitespace
        r'^[0-9]+$',  # Just numbers
        r'^[。，、！？；：""''（）【】…—\s]+$',  # Only punctuation
        r'^(对|是|好|行|嗯|哦|啊|谢谢)[。，]?$',  # Common ASR hallucinations
        r'^(对对|好好|是是|嗯嗯)[。，]?$',  # Repeated single chars
        r'^(对的|好的|是的|行的)[。，]?$',  # Common confirmations (often hallucinations)
    ]
    
    # Silence timeout before returning to monitoring mode (seconds)
    SILENCE_TIMEOUT = 5.0
    
    def __init__(
        self,
        session_id: str,
        standby_timeout: int = 180,  # 3 minutes standby after activation
        on_response: Callable[[Dict[str, Any]], None] = None,
        on_mode_change: Callable[[str], None] = None
    ):
        self.session_id = session_id
        self.standby_timeout = standby_timeout
        self.on_response = on_response
        self.on_mode_change = on_mode_change
        
        self.mode = "idle"  # idle, monitoring, listening, processing
        self.last_activity_time = None
        self.activation_time = None
        
        # Service references (lazy loaded)
        self._mysql_service = None
        self._glm_client = None
        self._tts_client = None
    
    @property
    def mysql_service(self):
        if self._mysql_service is None:
            from .mysql_service import get_mysql_service
            self._mysql_service = get_mysql_service()
        return self._mysql_service
    
    @property
    def glm_client(self):
        if self._glm_client is None:
            from .glm_client import get_glm_client
            self._glm_client = get_glm_client()
        return self._glm_client
    
    @property
    def tts_client(self):
        if self._tts_client is None:
            from .tts_cosyvoice_client import get_tts_client
            self._tts_client = get_tts_client()
        return self._tts_client
    
    def is_valid_input(self, text: str) -> bool:
        """Check if the input is valid (not noise)"""
        if not text:
            return False
        
        text = text.strip()
        
        # Check minimum length
        if len(text) < self.MIN_VALID_INPUT_LENGTH:
            return False
        
        # Check noise patterns
        for pattern in self.NOISE_PATTERNS:
            if re.match(pattern, text):
                logger.debug(f"[ConversationService] Filtered noise: {text}")
                return False
        
        return True
    
    def set_mode(self, mode: str):
        """Set conversation mode"""
        if mode != self.mode:
            old_mode = self.mode
            self.mode = mode
            logger.info(f"[ConversationService] Mode: {old_mode} -> {mode}")
            
            if mode == "listening":
                self.activation_time = time.time()
            
            if self.on_mode_change:
                self.on_mode_change(mode)
    
    def check_standby_timeout(self) -> bool:
        """Check if standby timeout has been reached"""
        if self.activation_time and self.mode in ["listening", "processing"]:
            elapsed = time.time() - self.activation_time
            if elapsed > self.standby_timeout:
                logger.info(f"[ConversationService] Standby timeout reached ({elapsed:.1f}s)")
                return True
        return False
    
    def check_silence_timeout(self) -> bool:
        """Check if silence timeout has been reached"""
        if self.last_activity_time and self.mode == "listening":
            elapsed = time.time() - self.last_activity_time
            if elapsed > self.SILENCE_TIMEOUT:
                return True
        return False
    
    async def handle_wakeword_detected(self, keyword: str) -> Dict[str, Any]:
        """Handle wake word detection"""
        self.set_mode("listening")
        self.last_activity_time = time.time()
        
        response = {
            "type": "wakeword_detected",
            "keyword": keyword,
            "message": f"已唤醒，请说话...",
            "timestamp": time.time()
        }
        
        # Save to conversation history
        self.mysql_service.save_conversation(
            session_id=self.session_id,
            role="system",
            content=f"[唤醒词: {keyword}]"
        )
        
        return response
    
    async def handle_user_input(self, text: str) -> Dict[str, Any]:
        """
        Handle user input after wake word activation.
        
        Returns response with GLM answer and optional TTS audio.
        """
        self.last_activity_time = time.time()
        
        # Validate input
        if not self.is_valid_input(text):
            logger.debug(f"[ConversationService] Invalid input ignored: {text}")
            return {
                "type": "invalid_input",
                "text": text,
                "message": "输入无效，请重新说话"
            }
        
        self.set_mode("processing")
        
        # Save user message
        self.mysql_service.save_conversation(
            session_id=self.session_id,
            role="user",
            content=text
        )
        
        # Get surgical context from MySQL
        surgical_context = self.mysql_service.get_recent_surgr1_context(
            session_id=self.session_id,
            limit=10
        )
        
        # Also get recent GLM summaries
        glm_context = self.mysql_service.get_recent_glm_context(
            session_id=self.session_id,
            limit=5
        )
        
        full_context = surgical_context
        if glm_context:
            full_context += "\n\n" + glm_context
        
        # Get GLM response
        try:
            glm_result = await self.glm_client.chat_with_context(
                user_query=text,
                surgical_context=full_context,
                disable_thinking=True  # 禁用思考模式加速
            )
            
            if glm_result.get("success"):
                response_text = glm_result.get("text", "")
                
                # Save GLM response to MySQL
                glm_id = self.mysql_service.save_glm_summary(
                    session_id=self.session_id,
                    summary_text=response_text,
                    summary_type="conversation",
                    user_query=text,
                    tokens_used=glm_result.get("tokens_used"),
                    thinking_disabled=True
                )
                
                # Save assistant message
                self.mysql_service.save_conversation(
                    session_id=self.session_id,
                    role="assistant",
                    content=response_text,
                    glm_summary_id=glm_id
                )
                
                # Generate TTS audio
                tts_result = await self.tts_client.synthesize(response_text)
                audio_base64 = tts_result.get("audio_base64") if tts_result.get("success") else None
                
                self.set_mode("listening")  # Back to listening for follow-up
                
                result = {
                    "type": "response",
                    "success": True,
                    "user_query": text,
                    "response_text": response_text,
                    "audio_base64": audio_base64,
                    "audio_format": "wav",
                    "glm_id": glm_id,
                    "timestamp": time.time()
                }
                
                if self.on_response:
                    self.on_response(result)
                
                return result
                
            else:
                error_msg = glm_result.get("error", "GLM响应失败")
                self.set_mode("listening")
                return {
                    "type": "error",
                    "success": False,
                    "error": error_msg,
                    "timestamp": time.time()
                }
                
        except Exception as e:
            logger.error(f"[ConversationService] Error processing input: {e}")
            self.set_mode("listening")
            return {
                "type": "error",
                "success": False,
                "error": str(e),
                "timestamp": time.time()
            }
    
    async def handle_silence(self) -> Dict[str, Any]:
        """Handle silence timeout - return to monitoring mode"""
        self.set_mode("monitoring")
        
        return {
            "type": "back_to_monitoring",
            "message": "未检测到有效输入，返回监控模式",
            "timestamp": time.time()
        }
    
    async def handle_standby_timeout(self) -> Dict[str, Any]:
        """Handle standby timeout - return to monitoring mode"""
        self.set_mode("monitoring")
        
        return {
            "type": "back_to_monitoring",
            "message": f"待机超时（{self.standby_timeout}秒），返回监控模式",
            "timestamp": time.time()
        }
    
    def get_conversation_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get conversation history from MySQL"""
        return self.mysql_service.get_conversation_history(
            session_id=self.session_id,
            limit=limit
        )
    
    def clear_conversation(self):
        """Clear conversation history"""
        self.mysql_service.clear_conversation(self.session_id)


# Factory function
def create_conversation_service(
    session_id: str,
    standby_timeout: int = 180,
    on_response: Callable = None,
    on_mode_change: Callable = None
) -> ConversationService:
    """Create a conversation service instance"""
    return ConversationService(
        session_id=session_id,
        standby_timeout=standby_timeout,
        on_response=on_response,
        on_mode_change=on_mode_change
    )



