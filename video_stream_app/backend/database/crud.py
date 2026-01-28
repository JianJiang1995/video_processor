"""
Database CRUD Operations
Simplified for 2-table structure (analysis_results, chat_history)
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid

from .models import (
    _sessions_cache,
    create_session_record,
    get_session_record,
    update_session_record,
    get_all_session_records
)


# ============ Video Session Operations (in-memory) ============

def create_video_session(
    db,  # kept for backward compatibility, not used
    video_path: str,
    video_name: str,
    duration: float = 0,
    fps: float = 0,
    width: int = 0,
    height: int = 0,
    total_frames: int = 0
) -> Dict[str, Any]:
    """Create a new video processing session"""
    session_id = str(uuid.uuid4())[:8]
    
    return create_session_record(
        session_id=session_id,
        video_path=video_path,
        video_name=video_name,
        duration=duration,
        fps=fps,
        width=width,
        height=height,
        total_frames=total_frames,
        status="pending"
    )


def get_video_session(db, session_id: str) -> Optional[Dict[str, Any]]:
    """Get video session by session_id"""
    return get_session_record(session_id)


def get_video_session_by_id(db, id: int) -> Optional[Dict[str, Any]]:
    """Get video session by database id - returns first match"""
    sessions = list(_sessions_cache.values())
    return sessions[id] if id < len(sessions) else None


def get_all_sessions(db, limit: int = 100) -> List[Dict[str, Any]]:
    """Get all video sessions"""
    return get_all_session_records()[:limit]


def update_session_status(
    db,
    session_id: str,
    status: str,
    current_position: Optional[float] = None,
    is_paused: Optional[bool] = None
) -> Optional[Dict[str, Any]]:
    """Update session status"""
    updates = {"status": status}
    if current_position is not None:
        updates["current_position"] = current_position
    if is_paused is not None:
        updates["is_paused"] = is_paused
    
    return update_session_record(session_id, **updates)


# ============ Frame Analysis Operations (use mysql_service) ============

def create_frame_analysis(
    db,
    session_id,  # Can be int (legacy) or str (session_id)
    frame_idx: int,
    timestamp: float,
    tool_localization: str = None,
    surgical_action: str = None,
    surgical_phase: str = None,
    mask_data: str = None,
    raw_response: dict = None
) -> int:
    """Create frame analysis record - saves to MySQL"""
    from ..services.mysql_service import get_mysql_service
    
    # Get session_id string - handle both int (legacy) and str formats
    if isinstance(session_id, int):
        sessions = list(_sessions_cache.values())
        session_str = sessions[session_id]["session_id"] if session_id < len(sessions) else str(session_id)
    else:
        session_str = str(session_id)
    
    return get_mysql_service().save_analysis(
        session_id=session_str,
        frame_idx=frame_idx,
        timestamp=timestamp,
        tool_localization=tool_localization,
        surgical_action=surgical_action,
        surgical_phase=surgical_phase
    )


def get_frames_by_session(
    db,
    session_id,  # Can be int (legacy) or str (session_id)
    start_time: Optional[float] = None,
    end_time: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Get frames for a session"""
    from ..services.mysql_service import get_mysql_service
    
    # Handle both int (legacy index) and str (session_id) formats
    if isinstance(session_id, int):
        sessions = list(_sessions_cache.values())
        session_str = sessions[session_id]["session_id"] if session_id < len(sessions) else str(session_id)
    else:
        session_str = str(session_id)
    
    return get_mysql_service().get_analyses(
        session_id=session_str,
        start_time=start_time,
        end_time=end_time
    )


def get_frame_by_timestamp(
    db,
    session_id,  # Can be int (legacy) or str (session_id)
    timestamp: float,
    tolerance: float = 0.5
) -> Optional[Dict[str, Any]]:
    """Get frame closest to a timestamp"""
    frames = get_frames_by_session(db, session_id, timestamp - tolerance, timestamp + tolerance)
    return frames[0] if frames else None


def _resolve_session_id(session_id) -> str:
    """Helper to convert session_id (int or str) to string session_id"""
    if isinstance(session_id, int):
        sessions = list(_sessions_cache.values())
        return sessions[session_id]["session_id"] if session_id < len(sessions) else str(session_id)
    return str(session_id)


# ============ Window Summary Operations (use mysql_service) ============

def create_window_summary(
    db,
    session_id,  # Can be int (legacy) or str (session_id)
    window_id: int,
    start_time: float,
    end_time: float,
    summary_text: str,
    summary_chinese: str = None,
    tts_audio_path: str = None,
    dominant_phase: str = None,
    tools_detected: list = None,
    key_actions: list = None,
    others_data: Dict[str, Any] = None
) -> int:
    """Create window summary record and trigger compression check"""
    from ..services.mysql_service import get_mysql_service
    
    session_str = _resolve_session_id(session_id)
    
    result_id = get_mysql_service().save_analysis(
        session_id=session_str,
        window_id=window_id,
        window_start=start_time,
        window_end=end_time,
        analysis_type="window",
        glm_summary=summary_text,
        surgical_phase=dominant_phase,
        others_data=others_data
    )
    
    # Trigger async compression check in background
    _trigger_compression_check(session_str)
    
    return result_id


def _trigger_compression_check(session_id: str):
    """Trigger compression check in background (non-blocking)"""
    import asyncio
    import logging
    
    logger = logging.getLogger(__name__)
    
    async def _check_and_compress():
        try:
            from ..services.summary_compressor import check_and_compress_for_session
            result = await check_and_compress_for_session(session_id)
            if result and result.get("success"):
                logger.info(f"[CRUD] Compressed windows {result.get('window_start_id')}-{result.get('window_end_id')} for session {session_id}")
        except Exception as e:
            logger.warning(f"[CRUD] Compression check failed for {session_id}: {e}")
    
    try:
        # Try to get running event loop
        loop = asyncio.get_running_loop()
        # Schedule task in the existing loop
        loop.create_task(_check_and_compress())
    except RuntimeError:
        # No running loop - skip compression (will be done next time)
        pass


def get_summaries_by_session(db, session_id) -> List[Dict[str, Any]]:
    """Get all summaries for a session"""
    from ..services.mysql_service import get_mysql_service
    
    session_str = _resolve_session_id(session_id)
    
    analyses = get_mysql_service().get_analyses(session_id=session_str, limit=1000)
    return [a for a in analyses if a.get("window_id") is not None]


def get_summary_for_timestamp(
    db,
    session_id,  # Can be int (legacy) or str (session_id)
    timestamp: float
) -> Optional[Dict[str, Any]]:
    """Get the summary that covers a specific timestamp"""
    summaries = get_summaries_by_session(db, session_id)
    for s in summaries:
        if s.get("window_start") and s.get("window_end"):
            if s["window_start"] <= timestamp < s["window_end"]:
                return s
    return None


def get_latest_summary(db, session_id) -> Optional[Dict[str, Any]]:
    """Get the most recent summary for a session"""
    summaries = get_summaries_by_session(db, session_id)
    return summaries[0] if summaries else None


def delete_session_data(db, session_id: str) -> bool:
    """Delete all data for a session"""
    if session_id in _sessions_cache:
        del _sessions_cache[session_id]
        return True
    return False
