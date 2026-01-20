"""
Chat Audio Notifier - WebSocket-based audio push for chat responses.

When TTS completes in background, this module notifies the frontend
via WebSocket so the audio can be played without blocking text response.
"""
import asyncio
import logging
from typing import Dict, Optional, Set, Callable, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AudioNotification:
    """Audio notification data"""
    session_id: str
    audio_base64: str
    timestamp: float


# Global registry of WebSocket connections per session
# Key: session_id, Value: set of callback functions to notify
_audio_listeners: Dict[str, Set[Callable]] = {}
_pending_audio: Dict[str, AudioNotification] = {}  # For clients that connect after audio is ready


def register_audio_listener(session_id: str, callback: Callable[[str], Any]):
    """
    Register a callback to receive audio notifications for a session.
    
    Args:
        session_id: The session to listen for
        callback: Async function that receives audio_base64 when ready
    """
    if session_id not in _audio_listeners:
        _audio_listeners[session_id] = set()
    _audio_listeners[session_id].add(callback)
    logger.info(f"[AudioNotifier] Registered listener for session {session_id}, total: {len(_audio_listeners[session_id])}")
    
    # Check if there's pending audio for this session
    if session_id in _pending_audio:
        pending = _pending_audio.pop(session_id)
        logger.info(f"[AudioNotifier] Delivering pending audio to new listener")
        asyncio.create_task(_safe_callback(callback, pending.audio_base64))


def unregister_audio_listener(session_id: str, callback: Callable):
    """Unregister an audio listener"""
    if session_id in _audio_listeners:
        _audio_listeners[session_id].discard(callback)
        if not _audio_listeners[session_id]:
            del _audio_listeners[session_id]
        logger.info(f"[AudioNotifier] Unregistered listener for session {session_id}")


async def _safe_callback(callback: Callable, audio_base64: str):
    """Safely call a callback, handling both sync and async"""
    try:
        result = callback(audio_base64)
        if asyncio.iscoroutine(result):
            await result
    except Exception as e:
        logger.error(f"[AudioNotifier] Callback error: {e}")


async def notify_chat_audio_ready(session_id: str, audio_base64: str):
    """
    Notify all listeners that audio is ready for a session.
    
    If no listeners are registered, store the audio for later delivery.
    
    Args:
        session_id: The session ID
        audio_base64: Base64-encoded audio data
    """
    import time
    
    if session_id in _audio_listeners and _audio_listeners[session_id]:
        listeners = list(_audio_listeners[session_id])
        logger.info(f"[AudioNotifier] Notifying {len(listeners)} listeners for session {session_id}")
        
        # Notify all listeners
        tasks = [_safe_callback(cb, audio_base64) for cb in listeners]
        await asyncio.gather(*tasks, return_exceptions=True)
    else:
        # Store for later delivery (expires after 60 seconds)
        logger.info(f"[AudioNotifier] No listeners for session {session_id}, storing audio for later")
        _pending_audio[session_id] = AudioNotification(
            session_id=session_id,
            audio_base64=audio_base64,
            timestamp=time.time()
        )
        
        # Clean up old pending audio
        _cleanup_old_pending()


def _cleanup_old_pending():
    """Remove pending audio older than 60 seconds"""
    import time
    current_time = time.time()
    expired = [
        sid for sid, notif in _pending_audio.items()
        if current_time - notif.timestamp > 60
    ]
    for sid in expired:
        del _pending_audio[sid]
        logger.info(f"[AudioNotifier] Cleaned up expired pending audio for session {sid}")


def get_pending_audio(session_id: str) -> Optional[str]:
    """
    Get and remove pending audio for a session (polling fallback).
    
    Returns:
        audio_base64 if available, None otherwise
    """
    if session_id in _pending_audio:
        notif = _pending_audio.pop(session_id)
        return notif.audio_base64
    return None


def has_pending_audio(session_id: str) -> bool:
    """Check if there's pending audio for a session"""
    return session_id in _pending_audio
