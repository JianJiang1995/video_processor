"""
Database Models for Video Analysis
All data stored in MySQL (2 tables: analysis_results, chat_history)
Session data is persisted to MySQL and cached in memory.
"""
from datetime import datetime
import logging
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from ..config import settings

logger = logging.getLogger(__name__)

# Create engine (MySQL)
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ============================================================================
# Models are defined in mysql_service.py (AnalysisResult, ChatHistory)
# This file provides backward compatibility for existing code
# ============================================================================

class VideoSession(Base):
    """视频会话 - 向后兼容模型（数据存入 analysis_results）"""
    __tablename__ = "video_sessions_compat"
    __table_args__ = {'extend_existing': True}
    
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(64), unique=True, index=True)
    video_path = Column(String(512))
    video_name = Column(String(256))
    duration = Column(Float)
    fps = Column(Float)
    width = Column(Integer)
    height = Column(Integer)
    total_frames = Column(Integer)
    status = Column(String(32), default="pending")
    current_position = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# In-memory session storage (cached from MySQL)
_sessions_cache: dict = {}


def init_db():
    """Initialize MySQL tables used by the analysis pipeline."""
    from ..services.mysql_service import Base as MySQLBase
    from ..services.mysql_service import get_mysql_service

    mysql_svc = get_mysql_service()
    MySQLBase.metadata.create_all(bind=mysql_svc.engine)
    logger.info("[Database] MySQL tables are ready")


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_mysql_service():
    """Lazy import to avoid circular dependency"""
    from ..services.mysql_service import get_mysql_service
    return get_mysql_service()


# ============================================================================
# Session management (in-memory cache with MySQL persistence)
# ============================================================================

def create_session_record(
    session_id: str,
    video_name: str = None,
    video_path: str = None,
    **kwargs
) -> dict:
    """创建会话记录（同时保存到MySQL和内存缓存）"""
    session_data = {
        "session_id": session_id,
        "video_name": video_name,
        "video_path": video_path,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
        **kwargs
    }
    _sessions_cache[session_id] = session_data
    
    # Also save to MySQL for persistence
    try:
        mysql_svc = _get_mysql_service()
        mysql_svc.create_video_session(
            session_id=session_id,
            video_name=video_name,
            video_path=video_path,
            video_type=kwargs.get("video_type", "local"),
            duration=kwargs.get("duration"),
            fps=kwargs.get("fps"),
            width=kwargs.get("width"),
            height=kwargs.get("height"),
            total_frames=kwargs.get("total_frames"),
            storage_path=kwargs.get("storage_path")
        )
        logger.debug(f"[Session] Created session {session_id} in MySQL")
    except Exception as e:
        logger.warning(f"[Session] Failed to save session {session_id} to MySQL: {e}")
    
    return session_data


def get_session_record(session_id: str) -> dict:
    """获取会话记录（优先从内存缓存，否则从MySQL）"""
    # First check memory cache
    if session_id in _sessions_cache:
        return _sessions_cache[session_id]
    
    # If not in cache, try to load from MySQL
    try:
        mysql_svc = _get_mysql_service()
        session_data = mysql_svc.get_video_session(session_id)
        if session_data:
            # Cache it for future access
            _sessions_cache[session_id] = session_data
            logger.debug(f"[Session] Loaded session {session_id} from MySQL")
            return session_data
    except Exception as e:
        logger.warning(f"[Session] Failed to load session {session_id} from MySQL: {e}")
    
    return None


def update_session_record(session_id: str, **kwargs) -> dict:
    """更新会话记录"""
    # Ensure session is in cache (load from MySQL if needed)
    session_data = get_session_record(session_id)
    if not session_data:
        return None
    
    # Update cache
    _sessions_cache[session_id].update(kwargs)
    _sessions_cache[session_id]["updated_at"] = datetime.utcnow().isoformat()
    
    # Also update MySQL
    try:
        mysql_svc = _get_mysql_service()
        mysql_svc.update_video_session(session_id, **kwargs)
    except Exception as e:
        logger.warning(f"[Session] Failed to update session {session_id} in MySQL: {e}")
    
    return _sessions_cache.get(session_id)


def get_all_session_records() -> list:
    """获取所有会话记录（从MySQL加载）"""
    try:
        mysql_svc = _get_mysql_service()
        sessions = mysql_svc.list_video_sessions(limit=100)
        # Update cache with these sessions
        for s in sessions:
            _sessions_cache[s["session_id"]] = s
        return sessions
    except Exception as e:
        logger.warning(f"[Session] Failed to load sessions from MySQL: {e}")
        return list(_sessions_cache.values())
