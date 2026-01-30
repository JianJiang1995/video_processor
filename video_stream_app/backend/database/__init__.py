"""
Database module - simplified to 2 tables
"""
from .models import (
    Base, VideoSession, engine, SessionLocal, get_db, init_db,
    create_session_record, get_session_record, update_session_record, get_all_session_records
)
from .crud import (
    create_video_session,
    get_video_session,
    get_video_session_by_id,
    get_all_sessions,
    update_session_status,
    create_frame_analysis,
    get_frames_by_session,
    get_frame_by_timestamp,
    create_window_summary,
    get_summaries_by_session,
    get_summary_for_timestamp,
    get_latest_summary,
    delete_session_data
)

__all__ = [
    # Models
    "Base", "VideoSession", "engine", "SessionLocal", "get_db", "init_db",
    # Session management
    "create_session_record", "get_session_record", "update_session_record", "get_all_session_records",
    # CRUD operations (backward compatible)
    "create_video_session", "get_video_session", "get_video_session_by_id",
    "get_all_sessions", "update_session_status",
    "create_frame_analysis", "get_frames_by_session", "get_frame_by_timestamp",
    "create_window_summary", "get_summaries_by_session",
    "get_summary_for_timestamp", "get_latest_summary", "delete_session_data"
]
