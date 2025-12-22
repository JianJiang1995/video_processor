"""
Voice API Routes - ASR and TTS endpoints
Handles speech recognition (ASR) and text-to-speech (TTS) via external APIs.
"""
import asyncio
import json
import time
from typing import Optional, List, Dict
from pathlib import Path
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
import base64
import logging

from ..services.asr_funasr_client import get_asr_client, ensure_asr_available, ASRMonitoringSession
from ..services.tts_cosyvoice_client import get_tts_client, ensure_tts_available
from ..services.conversation_service import ConversationService, create_conversation_service
from ..services.mysql_service import get_mysql_service, init_mysql

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Active conversation sessions
_conversation_sessions: Dict[str, ConversationService] = {}

router = APIRouter(prefix="/api/voice", tags=["voice"])


# ============================================================================
# Request/Response Models
# ============================================================================

class TTSRequest(BaseModel):
    """TTS synthesis request"""
    text: str = Field(..., description="Text to synthesize")
    speaker: Optional[str] = Field(default=None, description="Speaker voice (e.g., 中文女)")


class TTSSummaryRequest(BaseModel):
    """TTS request for window summary"""
    summary: str = Field(..., description="Summary text to synthesize")
    window_id: int = Field(..., description="Window ID")
    session_id: str = Field(..., description="Session ID")


class ASRTranscribeRequest(BaseModel):
    """ASR transcription request with base64 audio"""
    audio_data: str = Field(..., description="Base64 encoded audio data")
    sample_rate: int = Field(default=16000, description="Audio sample rate")
    format: str = Field(default="pcm", description="Audio format (pcm, wav, mp3)")


class KeywordConfigRequest(BaseModel):
    """Keyword configuration request"""
    keywords: List[str] = Field(..., description="Wake words to detect")
    threshold: float = Field(default=0.6, ge=0.0, le=1.0, description="Detection threshold")


class ChatMessage(BaseModel):
    """Chat message for ASR/TTS conversation"""
    role: str = Field(..., description="Message role: user or assistant")
    content: str = Field(..., description="Message content")
    timestamp: Optional[float] = Field(default=None, description="Message timestamp")
    audio_base64: Optional[str] = Field(default=None, description="TTS audio in base64")


# ============================================================================
# TTS Endpoints
# ============================================================================

@router.get("/tts/status")
async def get_tts_status():
    """Check TTS service status"""
    try:
        client = get_tts_client()
        is_healthy = await client.check_health()
        return {
            "available": is_healthy,
            "api_url": client.api_url,
            "default_speaker": client.default_speaker,
            "speakers": client.get_available_speakers()
        }
    except Exception as e:
        return {
            "available": False,
            "error": str(e)
        }


@router.post("/tts/synthesize")
async def synthesize_speech(request: TTSRequest):
    """
    Synthesize speech from text using CosyVoice TTS.
    
    Returns audio in base64 format for web playback.
    """
    try:
        client = await ensure_tts_available()
        
        result = await client.synthesize(
            text=request.text,
            speaker=request.speaker
        )
        
        if result.get("success"):
            return {
                "success": True,
                "audio_base64": result.get("audio_base64"),
                "audio_format": result.get("audio_format", "wav"),
                "speaker": result.get("speaker"),
                "estimated_duration": result.get("estimated_duration")
            }
        else:
            raise HTTPException(500, result.get("error", "TTS synthesis failed"))
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"TTS synthesis error: {e}")
        raise HTTPException(500, str(e))


@router.post("/tts/summary")
async def synthesize_summary(request: TTSSummaryRequest):
    """Synthesize audio for a window summary"""
    try:
        client = await ensure_tts_available()
        
        result = await client.synthesize_summary(
            summary=request.summary,
            window_id=request.window_id,
            session_id=request.session_id
        )
        
        return result
        
    except Exception as e:
        logger.error(f"TTS summary synthesis error: {e}")
        raise HTTPException(500, str(e))


@router.get("/tts/speakers")
async def get_tts_speakers():
    """Get available TTS speakers/voices"""
    client = get_tts_client()
    return client.get_available_speakers()


# ============================================================================
# ASR Endpoints
# ============================================================================

@router.get("/asr/status")
async def get_asr_status():
    """Check ASR service status"""
    try:
        client = get_asr_client()
        is_healthy = await client.check_health()
        return {
            "available": is_healthy,
            "api_url": client.api_url,
            "ws_url": client.ws_url,
            "keywords": client.keywords,
            "standby_timeout": client.standby_timeout
        }
    except Exception as e:
        return {
            "available": False,
            "error": str(e)
        }


@router.post("/asr/transcribe")
async def transcribe_audio(request: ASRTranscribeRequest):
    """
    Transcribe audio data.
    
    Args:
        audio_data: Base64 encoded audio
        sample_rate: Audio sample rate
        format: Audio format
    """
    try:
        client = await ensure_asr_available()
        
        # Decode base64 audio
        audio_bytes = base64.b64decode(request.audio_data)
        
        result = await client.transcribe_audio_data(
            audio_data=audio_bytes,
            sample_rate=request.sample_rate,
            audio_format=request.format
        )
        
        return result
        
    except Exception as e:
        logger.error(f"ASR transcription error: {e}")
        raise HTTPException(500, str(e))


@router.post("/asr/transcribe/file")
async def transcribe_file(file: UploadFile = File(...)):
    """
    Transcribe an uploaded audio file.
    """
    try:
        client = await ensure_asr_available()
        
        # Save temp file
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            result = await client.transcribe_file(tmp_path)
            return result
        finally:
            # Cleanup
            Path(tmp_path).unlink(missing_ok=True)
        
    except Exception as e:
        logger.error(f"ASR file transcription error: {e}")
        raise HTTPException(500, str(e))


@router.get("/asr/keywords")
async def get_keyword_config():
    """Get current wake word configuration"""
    try:
        client = await ensure_asr_available()
        return await client.get_keyword_config()
    except Exception as e:
        logger.error(f"Failed to get keyword config: {e}")
        raise HTTPException(500, str(e))


@router.post("/asr/keywords")
async def set_keyword_config(request: KeywordConfigRequest):
    """Set wake word configuration"""
    try:
        client = await ensure_asr_available()
        return await client.set_keyword_config(
            keywords=request.keywords,
            threshold=request.threshold
        )
    except Exception as e:
        logger.error(f"Failed to set keyword config: {e}")
        raise HTTPException(500, str(e))


@router.get("/asr/history")
async def get_transcript_history(limit: int = 50):
    """Get transcript history"""
    try:
        client = await ensure_asr_available()
        return await client.get_transcript_history(limit=limit)
    except Exception as e:
        logger.error(f"Failed to get history: {e}")
        raise HTTPException(500, str(e))


@router.delete("/asr/history")
async def clear_transcript_history():
    """Clear transcript history"""
    try:
        client = await ensure_asr_available()
        success = await client.clear_history()
        return {"success": success}
    except Exception as e:
        logger.error(f"Failed to clear history: {e}")
        raise HTTPException(500, str(e))


# ============================================================================
# WebSocket Endpoints for Real-time Voice
# ============================================================================

@router.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    """
    WebSocket endpoint for real-time ASR streaming.
    
    Client sends audio chunks, receives transcriptions.
    Uses audio buffering to collect complete sentences before transcription.
    
    Messages:
    - Client -> Server: {"action": "audio", "audio_data": "base64..."}
    - Server -> Client: {"type": "result", "data": {"text": "...", "is_final": bool}}
    """
    await websocket.accept()
    logger.info("Voice stream WebSocket connected")
    
    client = get_asr_client()
    
    # Audio buffering configuration
    audio_buffer = bytearray()
    SAMPLE_RATE = 16000  # 16kHz
    BYTES_PER_SAMPLE = 2  # 16-bit audio
    MIN_AUDIO_DURATION = 0.8  # Minimum 0.8 seconds before transcription
    MAX_AUDIO_DURATION = 15.0  # Maximum buffer 15 seconds
    SILENCE_THRESHOLD = 800  # RMS threshold for silence detection (higher = less noise)
    SILENCE_DURATION = 0.6  # 0.6 seconds of silence = end of sentence
    
    min_buffer_size = int(MIN_AUDIO_DURATION * SAMPLE_RATE * BYTES_PER_SAMPLE)
    max_buffer_size = int(MAX_AUDIO_DURATION * SAMPLE_RATE * BYTES_PER_SAMPLE)
    
    last_speech_time = time.time()
    is_speaking = False
    silence_start_time = None
    last_interim_time = 0
    
    def calculate_rms(audio_bytes: bytes) -> float:
        """Calculate RMS of audio for silence detection"""
        import struct
        if len(audio_bytes) < 2:
            return 0
        samples = struct.unpack(f'<{len(audio_bytes)//2}h', audio_bytes)
        if not samples:
            return 0
        return (sum(s*s for s in samples) / len(samples)) ** 0.5
    
    async def process_buffer():
        """Process accumulated audio buffer"""
        nonlocal audio_buffer, is_speaking, silence_start_time
        if len(audio_buffer) < min_buffer_size:
            audio_buffer.clear()
            is_speaking = False
            silence_start_time = None
            return None
        
        audio_bytes = bytes(audio_buffer)
        audio_buffer.clear()
        is_speaking = False
        silence_start_time = None
        
        result = await client.transcribe_audio_data(audio_bytes)
        text = result.get("text", "").strip()
        
        # Filter out noise - require at least 2 characters
        if text and len(text) >= 2:
            return {
                "text": text,
                "is_final": True,
                "keyword_detected": result.get("keyword_detected")
            }
        return None
    
    try:
        while True:
            try:
                # Use timeout to check for silence-based sentence end
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=0.2
                )
                message = json.loads(data)
                action = message.get("action", "")
                
                if action == "audio":
                    audio_base64 = message.get("audio_data", "")
                    if audio_base64:
                        audio_bytes = base64.b64decode(audio_base64)
                        current_time = time.time()
                        
                        # Check if this chunk has speech
                        rms = calculate_rms(audio_bytes)
                        
                        if rms > SILENCE_THRESHOLD:
                            # Has speech - add to buffer
                            audio_buffer.extend(audio_bytes)
                            last_speech_time = current_time
                            is_speaking = True
                            silence_start_time = None
                            
                            # Send interim result every 2 seconds while speaking
                            if len(audio_buffer) > min_buffer_size and (current_time - last_interim_time) > 2.0:
                                last_interim_time = current_time
                                partial_result = await client.transcribe_audio_data(bytes(audio_buffer))
                                partial_text = partial_result.get("text", "").strip()
                                if partial_text and len(partial_text) >= 2:
                                    await websocket.send_json({
                                        "type": "result",
                                        "data": {
                                            "text": partial_text,
                                            "is_final": False
                                        }
                                    })
                        else:
                            # Silence detected
                            if is_speaking:
                                # Still add to buffer to capture trailing audio
                                audio_buffer.extend(audio_bytes)
                                
                                if silence_start_time is None:
                                    silence_start_time = current_time
                                elif (current_time - silence_start_time) > SILENCE_DURATION:
                                    # Enough silence - end of sentence
                                    if len(audio_buffer) > min_buffer_size:
                                        result = await process_buffer()
                                        if result:
                                            await websocket.send_json({
                                                "type": "result",
                                                "data": result
                                            })
                                    else:
                                        audio_buffer.clear()
                                        is_speaking = False
                                        silence_start_time = None
                        
                        # Force process if buffer too large
                        if len(audio_buffer) >= max_buffer_size:
                            result = await process_buffer()
                            if result:
                                await websocket.send_json({
                                    "type": "result",
                                    "data": result
                                })
                
                elif action == "stop":
                    # Process remaining buffer
                    if len(audio_buffer) > min_buffer_size:
                        result = await process_buffer()
                        if result:
                            await websocket.send_json({
                                "type": "result",
                                "data": result
                            })
                    
                    await websocket.send_json({
                        "type": "event",
                        "data": {"event": "session_completed"}
                    })
                    break
                    
            except asyncio.TimeoutError:
                # Timeout - check if we should process buffer due to silence
                current_time = time.time()
                if is_speaking and silence_start_time and (current_time - silence_start_time) > SILENCE_DURATION:
                    if len(audio_buffer) > min_buffer_size:
                        result = await process_buffer()
                        if result:
                            await websocket.send_json({
                                "type": "result",
                                "data": result
                            })
                
    except WebSocketDisconnect:
        logger.info("Voice stream WebSocket disconnected")
    except Exception as e:
        logger.error(f"Voice stream WebSocket error: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "data": {"error": str(e)}
            })
        except:
            pass


@router.websocket("/ws/wakeword")
async def websocket_wakeword(websocket: WebSocket):
    """
    WebSocket endpoint for wake word monitoring mode.
    
    Monitors for wake word, then enters standby mode for N minutes.
    
    Messages:
    - Client -> Server: {"action": "start_listening"}
    - Client -> Server: {"action": "audio", "audio_data": "base64..."}
    - Server -> Client: {"type": "wakeword_detected", "data": {"keyword": "..."}}
    - Server -> Client: {"type": "result", "data": {"text": "...", "is_final": bool}}
    - Server -> Client: {"type": "back_to_listening", "data": {"message": "..."}}
    """
    await websocket.accept()
    logger.info("Wake word monitoring WebSocket connected")
    
    client = get_asr_client()
    mode = "listening"  # listening, active
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            action = message.get("action", "")
            
            if action == "start_listening":
                mode = "listening"
                await websocket.send_json({
                    "type": "event",
                    "data": {
                        "event": "listening_started",
                        "keywords": client.keywords
                    }
                })
            
            elif action == "audio":
                audio_base64 = message.get("audio_data", "")
                if audio_base64:
                    audio_bytes = base64.b64decode(audio_base64)
                    result = await client.transcribe_audio_data(audio_bytes)
                    
                    text = result.get("text", "")
                    keyword_detected = result.get("keyword_detected")
                    
                    if mode == "listening":
                        # Check for wake word
                        if keyword_detected:
                            mode = "active"
                            await websocket.send_json({
                                "type": "wakeword_detected",
                                "data": keyword_detected
                            })
                        elif text:
                            # Send listening text for debugging
                            await websocket.send_json({
                                "type": "listening_text",
                                "data": {"text": text}
                            })
                    else:
                        # Active mode - send transcription
                        if text:
                            await websocket.send_json({
                                "type": "result",
                                "data": {
                                    "text": text,
                                    "is_final": True
                                }
                            })
            
            elif action == "back_to_listening":
                mode = "listening"
                await websocket.send_json({
                    "type": "back_to_listening",
                    "data": {"message": "Returned to monitoring mode"}
                })
            
            elif action == "stop":
                await websocket.send_json({
                    "type": "event",
                    "data": {"event": "session_completed"}
                })
                break
                
    except WebSocketDisconnect:
        logger.info("Wake word WebSocket disconnected")
    except Exception as e:
        logger.error(f"Wake word WebSocket error: {e}")


# ============================================================================
# Chat Interface Endpoints
# ============================================================================

# ============================================================================
# Conversation with GLM + MySQL Context
# ============================================================================

def get_conversation_service(session_id: str) -> ConversationService:
    """Get or create conversation service for a session"""
    if session_id not in _conversation_sessions:
        _conversation_sessions[session_id] = create_conversation_service(session_id)
    return _conversation_sessions[session_id]


@router.get("/chat/{session_id}/history")
async def get_chat_history(session_id: str, limit: int = 50):
    """Get chat history for a session from MySQL"""
    try:
        mysql = get_mysql_service()
        history = mysql.get_conversation_history(session_id, limit)
        return {
            "session_id": session_id,
            "messages": history,
            "total": len(history)
        }
    except Exception as e:
        logger.error(f"Failed to get chat history: {e}")
        return {
            "session_id": session_id,
            "messages": [],
            "error": str(e)
        }


@router.post("/chat/{session_id}/send")
async def send_chat_message(session_id: str, message: ChatMessage):
    """
    Send a chat message and get GLM response with TTS.
    
    Uses conversation service to:
    1. Retrieve surgical context from MySQL
    2. Send query to GLM (with thinking disabled for speed)
    3. Generate TTS audio for response
    4. Save everything to MySQL
    """
    import time
    message.timestamp = message.timestamp or time.time()
    
    if message.role == "user" and message.content:
        try:
            conv_service = get_conversation_service(session_id)
            
            # Process user input through conversation service
            result = await conv_service.handle_user_input(message.content)
            
            if result.get("success"):
                response = ChatMessage(
                    role="assistant",
                    content=result.get("response_text", ""),
                    timestamp=time.time(),
                    audio_base64=result.get("audio_base64")
                )
                
                return {
                    "success": True,
                    "user_message": message.model_dump(),
                    "response": response.model_dump(),
                    "glm_id": result.get("glm_id")
                }
            else:
                return {
                    "success": False,
                    "user_message": message.model_dump(),
                    "error": result.get("error", "Processing failed")
                }
            
        except Exception as e:
            logger.error(f"Chat response error: {e}")
            return {
                "success": False,
                "user_message": message.model_dump(),
                "error": str(e)
            }
    
    return {
        "success": True,
        "message": message.model_dump()
    }


@router.delete("/chat/{session_id}")
async def clear_chat_history(session_id: str):
    """Clear chat history for a session"""
    try:
        mysql = get_mysql_service()
        mysql.clear_conversation(session_id)
        
        if session_id in _conversation_sessions:
            del _conversation_sessions[session_id]
        
        return {"success": True, "session_id": session_id}
    except Exception as e:
        logger.error(f"Failed to clear chat: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# Continuous Monitoring WebSocket with GLM Integration
# ============================================================================

@router.websocket("/ws/conversation")
async def websocket_conversation(websocket: WebSocket):
    """
    WebSocket endpoint for continuous wake word monitoring with GLM integration.
    
    Flow:
    1. Start in monitoring mode (listening for wake words)
    2. When wake word detected, switch to listening mode
    3. Collect user input, validate it's not noise
    4. Send to GLM with MySQL context, get response
    5. Return response with TTS audio
    6. If no valid input for N seconds, return to monitoring mode
    
    Messages:
    - Client -> Server: {"action": "start", "session_id": "..."}
    - Client -> Server: {"action": "audio", "audio_data": "base64..."}
    - Server -> Client: {"type": "mode_change", "mode": "monitoring|listening|processing"}
    - Server -> Client: {"type": "wakeword_detected", "keyword": "..."}
    - Server -> Client: {"type": "transcript", "text": "...", "is_final": bool}
    - Server -> Client: {"type": "response", "text": "...", "audio_base64": "..."}
    - Server -> Client: {"type": "back_to_monitoring"}
    """
    await websocket.accept()
    logger.info("Conversation WebSocket connected")
    
    session_id = None
    conv_service = None
    asr_client = get_asr_client()
    
    mode = "monitoring"
    last_activity_time = time.time()
    accumulated_text = ""
    
    # Timeouts
    SILENCE_TIMEOUT = 5.0  # seconds
    STANDBY_TIMEOUT = 180  # 3 minutes
    
    try:
        while True:
            try:
                # Set timeout for receiving messages
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=1.0
                )
                
                message = json.loads(data)
                action = message.get("action", "")
                
                if action == "start":
                    session_id = message.get("session_id", "default")
                    conv_service = get_conversation_service(session_id)
                    conv_service.set_mode("monitoring")
                    mode = "monitoring"
                    
                    await websocket.send_json({
                        "type": "mode_change",
                        "mode": "monitoring",
                        "keywords": asr_client.keywords
                    })
                
                elif action == "audio" and session_id:
                    audio_base64 = message.get("audio_data", "")
                    if audio_base64:
                        audio_bytes = base64.b64decode(audio_base64)
                        result = await asr_client.transcribe_audio_data(audio_bytes)
                        
                        text = result.get("text", "")
                        keyword_detected = result.get("keyword_detected")
                        
                        if mode == "monitoring":
                            # Check for wake word
                            if keyword_detected:
                                mode = "listening"
                                last_activity_time = time.time()
                                accumulated_text = ""
                                
                                await conv_service.handle_wakeword_detected(
                                    keyword_detected.get("keyword", "")
                                )
                                
                                await websocket.send_json({
                                    "type": "wakeword_detected",
                                    "keyword": keyword_detected.get("keyword"),
                                    "mode": "listening"
                                })
                            elif text:
                                # Filter out common ASR hallucinations before sending
                                # These are short phrases the model outputs when there's no speech
                                hallucination_patterns = ['对', '对。', '嗯', '嗯。', '是', '是的', '好', '好的', '啊', '哦']
                                if text.strip() not in hallucination_patterns and len(text.strip()) > 2:
                                    # Send listening text for debugging
                                    await websocket.send_json({
                                        "type": "listening_text",
                                        "text": text
                                    })
                        
                        elif mode == "listening":
                            if text:
                                # Filter out common ASR hallucinations
                                hallucination_patterns = ['对', '对。', '嗯', '嗯。', '是', '是的', '好', '好的', '啊', '哦', '对对', '好好']
                                text_clean = text.strip()
                                
                                # Only update if it's likely real speech
                                if text_clean not in hallucination_patterns and len(text_clean) > 2:
                                    last_activity_time = time.time()
                                    accumulated_text = text
                                    
                                    await websocket.send_json({
                                        "type": "transcript",
                                        "text": text,
                                        "is_final": False
                                    })
                        
                        elif mode == "processing":
                            # Still processing, ignore audio
                            pass
                
                elif action == "submit_query" and session_id and mode == "listening":
                    # User explicitly submits query
                    query_text = message.get("text", accumulated_text)
                    
                    if conv_service.is_valid_input(query_text):
                        mode = "processing"
                        await websocket.send_json({
                            "type": "mode_change",
                            "mode": "processing"
                        })
                        
                        # Process with GLM
                        result = await conv_service.handle_user_input(query_text)
                        
                        if result.get("success"):
                            await websocket.send_json({
                                "type": "response",
                                "user_query": query_text,
                                "text": result.get("response_text", ""),
                                "audio_base64": result.get("audio_base64"),
                                "audio_format": "wav",
                                "glm_id": result.get("glm_id")
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "error": result.get("error", "Processing failed")
                            })
                        
                        # Back to listening for follow-up
                        mode = "listening"
                        last_activity_time = time.time()
                        accumulated_text = ""
                        
                        await websocket.send_json({
                            "type": "mode_change",
                            "mode": "listening"
                        })
                    else:
                        await websocket.send_json({
                            "type": "invalid_input",
                            "text": query_text
                        })
                
                elif action == "stop":
                    await websocket.send_json({
                        "type": "session_ended"
                    })
                    break
                    
            except asyncio.TimeoutError:
                # Check timeouts
                current_time = time.time()
                
                if mode == "listening":
                    # Check silence timeout
                    if current_time - last_activity_time > SILENCE_TIMEOUT:
                        # If we have accumulated text, try to process it
                        if accumulated_text and conv_service.is_valid_input(accumulated_text):
                            mode = "processing"
                            await websocket.send_json({
                                "type": "mode_change",
                                "mode": "processing"
                            })
                            
                            result = await conv_service.handle_user_input(accumulated_text)
                            
                            if result.get("success"):
                                await websocket.send_json({
                                    "type": "response",
                                    "user_query": accumulated_text,
                                    "text": result.get("response_text", ""),
                                    "audio_base64": result.get("audio_base64"),
                                    "audio_format": "wav"
                                })
                            
                            mode = "listening"
                            last_activity_time = current_time
                            accumulated_text = ""
                        else:
                            # No valid input, back to monitoring
                            mode = "monitoring"
                            await websocket.send_json({
                                "type": "back_to_monitoring",
                                "reason": "silence_timeout"
                            })
                
                # Check standby timeout (for listening mode)
                if mode in ["listening", "processing"]:
                    activation_time = conv_service.activation_time if conv_service else None
                    if activation_time and current_time - activation_time > STANDBY_TIMEOUT:
                        mode = "monitoring"
                        await websocket.send_json({
                            "type": "back_to_monitoring",
                            "reason": "standby_timeout"
                        })
                
                continue
                
    except WebSocketDisconnect:
        logger.info("Conversation WebSocket disconnected")
    except Exception as e:
        logger.error(f"Conversation WebSocket error: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "error": str(e)
            })
        except:
            pass


# ============================================================================
# MySQL Analysis Storage Endpoints
# ============================================================================

@router.get("/analysis/{session_id}/surgr1")
async def get_surgr1_analyses(
    session_id: str,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    limit: int = 100
):
    """Get SurgR1 analysis results from MySQL"""
    try:
        mysql = get_mysql_service()
        results = mysql.get_surgr1_analysis(
            session_id=session_id,
            start_time=start_time,
            end_time=end_time,
            limit=limit
        )
        return {
            "session_id": session_id,
            "results": results,
            "count": len(results)
        }
    except Exception as e:
        logger.error(f"Failed to get SurgR1 analyses: {e}")
        raise HTTPException(500, str(e))


@router.get("/analysis/{session_id}/glm")
async def get_glm_summaries(
    session_id: str,
    summary_type: Optional[str] = None,
    limit: int = 50
):
    """Get GLM summary results from MySQL"""
    try:
        mysql = get_mysql_service()
        results = mysql.get_glm_summaries(
            session_id=session_id,
            summary_type=summary_type,
            limit=limit
        )
        return {
            "session_id": session_id,
            "results": results,
            "count": len(results)
        }
    except Exception as e:
        logger.error(f"Failed to get GLM summaries: {e}")
        raise HTTPException(500, str(e))

