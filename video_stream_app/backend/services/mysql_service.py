"""
MySQL Database Service
Stores analysis results and chat history.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Float, Enum, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

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


# SQLAlchemy Base
Base = declarative_base()


# ============================================================================
# Database Models (3 tables)
# ============================================================================

class VideoSession(Base):
    """视频会话表 - 每个视频流/文件一条记录"""
    __tablename__ = "video_sessions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, nullable=False, index=True, comment="会话ID")
    
    # 视频信息
    video_name = Column(String(256), nullable=True, comment="视频名称")
    video_path = Column(String(512), nullable=True, comment="视频路径或流地址")
    video_type = Column(String(32), default="local", comment="类型: local/stream")
    
    # 视频属性
    duration = Column(Float, nullable=True, comment="视频时长(秒)")
    fps = Column(Float, nullable=True, comment="帧率")
    width = Column(Integer, nullable=True, comment="宽度")
    height = Column(Integer, nullable=True, comment="高度")
    total_frames = Column(Integer, nullable=True, comment="总帧数")
    
    # 会话文件夹 (存储帧图片)
    storage_path = Column(String(512), nullable=True, comment="会话存储文件夹路径")
    
    # 状态
    status = Column(String(32), default="active", comment="状态: active/paused/completed")
    
    # 元数据
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AnalysisResult(Base):
    """分析结果表 - SurgR1 + GLM 分析结果"""
    __tablename__ = "analysis_results"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # 会话信息
    session_id = Column(String(64), nullable=False, index=True, comment="会话/流ID")
    video_name = Column(String(256), nullable=True, comment="视频名称")
    
    # 帧/窗口信息
    frame_idx = Column(Integer, nullable=True, comment="帧索引")
    timestamp = Column(Float, nullable=True, index=True, comment="视频时间戳(秒)")
    window_id = Column(Integer, nullable=True, index=True, comment="5秒窗口ID")
    window_start = Column(Float, nullable=True, comment="窗口开始时间")
    window_end = Column(Float, nullable=True, comment="窗口结束时间")
    
    # 分析类型: frame (单帧SurgR1) / window (GLM窗口总结)
    analysis_type = Column(String(16), default="frame", comment="类型: frame/window")
    
    # SurgR1 分析结果
    tool_localization = Column(Text, nullable=True, comment="bbox定位结果 (JSON)")
    surgical_action = Column(Text, nullable=True, comment="手术动作描述")
    surgical_phase = Column(Text, nullable=True, comment="手术阶段")
    
    # GLM 总结
    glm_summary = Column(Text, nullable=True, comment="GLM总结文本")
    
    # 其他结构化数据 (hem_loc, gauze 等)
    others_data = Column(Text, nullable=True, comment="JSON格式的其他数据")

    # 图片路径 (相对于会话文件夹)
    image_path = Column(String(512), nullable=True, comment="帧图片路径")
    image_saved = Column(Integer, default=0, comment="帧图片是否已保存: 0/1")
    
    # 元数据
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    processing_time = Column(Float, nullable=True, comment="处理耗时(秒)")


class ChatHistory(Base):
    """对话历史表 - ASR + GLM 回答"""
    __tablename__ = "chat_history"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # 会话信息
    session_id = Column(String(64), nullable=False, index=True, comment="会话ID")
    
    # 消息内容
    role = Column(String(16), nullable=False, comment="角色: user/assistant/system")
    content = Column(Text, nullable=False, comment="消息内容")
    
    # 音频信息
    audio_duration = Column(Float, nullable=True, comment="音频时长(秒)")
    
    # 关联的分析上下文
    context_analysis_ids = Column(Text, nullable=True, comment="关联的analysis_results IDs (JSON数组)")
    
    # 元数据
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class CompressedSummary(Base):
    """压缩总结表 - 多个窗口总结的压缩版本"""
    __tablename__ = "compressed_summaries"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    # 会话信息
    session_id = Column(String(64), nullable=False, index=True, comment="会话ID")
    
    # 窗口范围
    window_start_id = Column(Integer, nullable=False, comment="起始窗口ID")
    window_end_id = Column(Integer, nullable=False, comment="结束窗口ID")
    window_count = Column(Integer, nullable=False, comment="压缩的窗口数量")
    
    # 时间范围
    time_start = Column(Float, nullable=True, comment="起始时间戳(秒)")
    time_end = Column(Float, nullable=True, comment="结束时间戳(秒)")
    
    # 压缩总结内容
    compressed_text = Column(Text, nullable=False, comment="压缩后的总结文本")
    
    # 原始窗口总结ID列表
    source_window_ids = Column(Text, nullable=True, comment="原始窗口ID列表 (JSON数组)")
    
    # 元数据
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    processing_time = Column(Float, nullable=True, comment="压缩处理耗时(秒)")


# ============================================================================
# Database Service
# ============================================================================

class MySQLService:
    """MySQL database service"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if MySQLService._initialized:
            return
        
        config = load_config()
        mysql_config = config.get("database", {}).get("mysql", {})
        
        # Build MySQL connection URL
        host = mysql_config.get("host", "localhost")
        port = mysql_config.get("port", 3306)
        user = mysql_config.get("user", "root")
        password = mysql_config.get("password", "")
        database = mysql_config.get("database", "video_analyzer")
        
        if password:
            self.database_url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"
        else:
            self.database_url = f"mysql+pymysql://{user}@{host}:{port}/{database}?charset=utf8mb4"
        
        self._engine = None
        self._SessionLocal = None
        
        MySQLService._initialized = True
        logger.info(f"[MySQLService] Initialized with database: {database}@{host}:{port}")
    
    @property
    def engine(self):
        """Get or create database engine"""
        if self._engine is None:
            try:
                self._engine = create_engine(
                    self.database_url,
                    pool_pre_ping=True,
                    pool_recycle=3600,
                    echo=False
                )
                logger.info("[MySQLService] Database engine created")
            except Exception as e:
                logger.error(f"[MySQLService] Failed to create engine: {e}")
                raise
        return self._engine
    
    @property
    def SessionLocal(self):
        """Get session factory"""
        if self._SessionLocal is None:
            self._SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        return self._SessionLocal
    
    @contextmanager
    def get_session(self):
        """Context manager for database sessions"""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    
    # ========================================================================
    # Video Session Operations
    # ========================================================================
    
    def create_video_session(
        self,
        session_id: str,
        video_name: str = None,
        video_path: str = None,
        video_type: str = "local",
        duration: float = None,
        fps: float = None,
        width: int = None,
        height: int = None,
        total_frames: int = None,
        storage_path: str = None
    ) -> Dict[str, Any]:
        """创建视频会话"""
        with self.get_session() as session:
            video_session = VideoSession(
                session_id=session_id,
                video_name=video_name,
                video_path=video_path,
                video_type=video_type,
                duration=duration,
                fps=fps,
                width=width,
                height=height,
                total_frames=total_frames,
                storage_path=storage_path,
                status="active"
            )
            session.add(video_session)
            session.flush()
            return {
                "id": video_session.id,
                "session_id": video_session.session_id,
                "video_name": video_session.video_name,
                "storage_path": video_session.storage_path,
                "created_at": video_session.created_at.isoformat()
            }
    
    def get_video_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取视频会话"""
        with self.get_session() as session:
            vs = session.query(VideoSession).filter(
                VideoSession.session_id == session_id
            ).first()
            if not vs:
                return None
            return {
                "id": vs.id,
                "session_id": vs.session_id,
                "video_name": vs.video_name,
                "video_path": vs.video_path,
                "video_type": vs.video_type,
                "duration": vs.duration,
                "fps": vs.fps,
                "width": vs.width,
                "height": vs.height,
                "total_frames": vs.total_frames,
                "storage_path": vs.storage_path,
                "status": vs.status,
                "created_at": vs.created_at.isoformat() if vs.created_at else None,
                "updated_at": vs.updated_at.isoformat() if vs.updated_at else None
            }
    
    def update_video_session(self, session_id: str, **kwargs) -> bool:
        """更新视频会话"""
        with self.get_session() as session:
            vs = session.query(VideoSession).filter(
                VideoSession.session_id == session_id
            ).first()
            if not vs:
                return False
            for key, value in kwargs.items():
                if hasattr(vs, key):
                    setattr(vs, key, value)
            return True
    
    def list_video_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """列出所有视频会话"""
        with self.get_session() as session:
            results = session.query(VideoSession).order_by(
                VideoSession.created_at.desc()
            ).limit(limit).all()
            return [
                {
                    "id": vs.id,
                    "session_id": vs.session_id,
                    "video_name": vs.video_name,
                    "video_path": vs.video_path,
                    "video_type": vs.video_type,
                    "duration": vs.duration,
                    "storage_path": vs.storage_path,
                    "status": vs.status,
                    "created_at": vs.created_at.isoformat() if vs.created_at else None
                }
                for vs in results
            ]
    
    def delete_video_session(self, session_id: str) -> Dict[str, Any]:
        """删除单个视频会话及其所有关联数据
        
        Returns:
            Dict with deleted counts for each table and storage_path for file cleanup
        """
        import shutil
        
        result = {
            "session_id": session_id,
            "session_deleted": False,
            "analysis_deleted": 0,
            "chat_deleted": 0,
            "storage_deleted": False,
            "storage_path": None
        }
        
        with self.get_session() as session:
            # 1. Get session info first (for storage_path)
            vs = session.query(VideoSession).filter(
                VideoSession.session_id == session_id
            ).first()
            
            if vs:
                result["storage_path"] = vs.storage_path
                
                # 2. Delete analysis results
                analysis_count = session.query(AnalysisResult).filter(
                    AnalysisResult.session_id == session_id
                ).delete()
                result["analysis_deleted"] = analysis_count
                
                # 3. Delete chat history
                chat_count = session.query(ChatHistory).filter(
                    ChatHistory.session_id == session_id
                ).delete()
                result["chat_deleted"] = chat_count
                
                # 4. Delete video session
                session.delete(vs)
                result["session_deleted"] = True
                
                # 5. Delete storage folder if exists
                if vs.storage_path:
                    storage_path = Path(vs.storage_path)
                    if storage_path.exists():
                        try:
                            shutil.rmtree(storage_path)
                            result["storage_deleted"] = True
                            logger.info(f"[MySQL] Deleted storage folder: {storage_path}")
                        except Exception as e:
                            logger.warning(f"[MySQL] Failed to delete storage folder: {e}")
                
                logger.info(f"[MySQL] Deleted session {session_id}: {result}")
            else:
                logger.warning(f"[MySQL] Session not found: {session_id}")
        
        return result
    
    def delete_all_video_sessions(self) -> Dict[str, Any]:
        """删除所有视频会话及其所有关联数据
        
        Returns:
            Dict with total deleted counts
        """
        import shutil
        
        result = {
            "sessions_deleted": 0,
            "analysis_deleted": 0,
            "chat_deleted": 0,
            "storage_deleted": 0
        }
        
        with self.get_session() as session:
            # 1. Get all sessions first (for storage_paths)
            all_sessions = session.query(VideoSession).all()
            storage_paths = [vs.storage_path for vs in all_sessions if vs.storage_path]
            
            # 2. Delete all analysis results
            result["analysis_deleted"] = session.query(AnalysisResult).delete()
            
            # 3. Delete all chat history
            result["chat_deleted"] = session.query(ChatHistory).delete()
            
            # 4. Delete all video sessions
            result["sessions_deleted"] = session.query(VideoSession).delete()
            
            # 5. Delete storage folders
            for storage_path in storage_paths:
                path = Path(storage_path)
                if path.exists():
                    try:
                        shutil.rmtree(path)
                        result["storage_deleted"] += 1
                    except Exception as e:
                        logger.warning(f"[MySQL] Failed to delete storage folder {path}: {e}")
            
            logger.info(f"[MySQL] Deleted all sessions: {result}")
        
        return result
    
    # ========================================================================
    # Analysis Results Operations
    # ========================================================================
    
    def save_analysis(
        self,
        session_id: str,
        video_name: str = None,
        frame_idx: int = None,
        timestamp: float = None,
        window_id: int = None,
        window_start: float = None,
        window_end: float = None,
        analysis_type: str = "frame",
        tool_localization: str = None,
        surgical_action: str = None,
        surgical_phase: str = None,
        glm_summary: str = None,
        others_data: Dict[str, Any] = None,
        image_path: str = None,
        image_saved: int = 0,
        processing_time: float = None
    ) -> int:
        """保存分析结果，返回ID
        
        使用 upsert 逻辑：如果同一 session_id + timestamp 的记录已存在，则更新；否则创建新记录。
        这样可以避免因视频循环或 R1 重试导致的重复记录。
        """
        # 将 others_data 转换为 JSON 字符串
        others_json = json.dumps(others_data) if others_data else None

        with self.get_session() as session:
            # 检查是否已存在同一 session_id + timestamp 的记录
            existing = None
            if timestamp is not None:
                # 使用四舍五入到 0.1 秒精度来匹配
                ts_lower = round(timestamp, 1) - 0.05
                ts_upper = round(timestamp, 1) + 0.05
                existing = session.query(AnalysisResult).filter(
                    AnalysisResult.session_id == session_id,
                    AnalysisResult.timestamp >= ts_lower,
                    AnalysisResult.timestamp <= ts_upper
                ).first()
            
            if existing:
                # 更新现有记录
                if video_name is not None:
                    existing.video_name = video_name
                if frame_idx is not None:
                    existing.frame_idx = frame_idx
                if window_id is not None:
                    existing.window_id = window_id
                if window_start is not None:
                    existing.window_start = window_start
                if window_end is not None:
                    existing.window_end = window_end
                if analysis_type is not None:
                    existing.analysis_type = analysis_type
                if tool_localization is not None:
                    existing.tool_localization = tool_localization
                if surgical_action is not None:
                    existing.surgical_action = surgical_action
                if surgical_phase is not None:
                    existing.surgical_phase = surgical_phase
                if glm_summary is not None:
                    existing.glm_summary = glm_summary
                if others_json is not None:
                    existing.others_data = others_json
                if image_path is not None:
                    existing.image_path = image_path
                if image_saved is not None:
                    existing.image_saved = image_saved
                if processing_time is not None:
                    existing.processing_time = processing_time
                session.flush()
                return existing.id
            else:
                # 创建新记录
                result = AnalysisResult(
                    session_id=session_id,
                    video_name=video_name,
                    frame_idx=frame_idx,
                    timestamp=timestamp,
                    window_id=window_id,
                    window_start=window_start,
                    window_end=window_end,
                    analysis_type=analysis_type,
                    tool_localization=tool_localization,
                    surgical_action=surgical_action,
                    surgical_phase=surgical_phase,
                    glm_summary=glm_summary,
                    others_data=others_json,
                    image_path=image_path,
                    image_saved=image_saved,
                    processing_time=processing_time
                )
                session.add(result)
                session.flush()
                return result.id
    
    def get_analyses(
        self,
        session_id: str,
        start_time: float = None,
        end_time: float = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """获取分析结果"""
        with self.get_session() as session:
            query = session.query(AnalysisResult).filter(
                AnalysisResult.session_id == session_id
            )
            
            if start_time is not None:
                query = query.filter(AnalysisResult.timestamp >= start_time)
            if end_time is not None:
                query = query.filter(AnalysisResult.timestamp <= end_time)
            
            results = query.order_by(AnalysisResult.timestamp.desc()).limit(limit).all()
            
            return [
                {
                    "id": r.id,
                    "session_id": r.session_id,
                    "video_name": r.video_name,
                    "frame_idx": r.frame_idx,
                    "timestamp": r.timestamp,
                    "window_id": r.window_id,
                    "window_start": r.window_start,
                    "window_end": r.window_end,
                    "analysis_type": r.analysis_type,
                    "tool_localization": r.tool_localization,
                    "surgical_action": r.surgical_action,
                    "surgical_phase": r.surgical_phase,
                    "glm_summary": r.glm_summary,
                    "others": json.loads(r.others_data) if r.others_data else None,
                    "image_path": r.image_path,
                    "image_saved": r.image_saved,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "processing_time": r.processing_time
                }
                for r in results
            ]
    
    def get_frame_at_timestamp(
        self,
        session_id: str,
        timestamp: float,
        tolerance: float = 0.5
    ) -> Optional[Dict[str, Any]]:
        """获取最接近指定时间戳的已保存帧"""
        with self.get_session() as session:
            # 查找在容差范围内且有保存图片的帧
            result = session.query(AnalysisResult).filter(
                AnalysisResult.session_id == session_id,
                AnalysisResult.analysis_type == "frame",
                AnalysisResult.image_saved == 1,
                AnalysisResult.timestamp >= timestamp - tolerance,
                AnalysisResult.timestamp <= timestamp + tolerance
            ).order_by(
                # 按时间差排序，取最近的 (使用 func.abs 而非 .abs())
                func.abs(AnalysisResult.timestamp - timestamp)
            ).first()
            
            if not result:
                # 如果容差内没有，取最近的一个
                result = session.query(AnalysisResult).filter(
                    AnalysisResult.session_id == session_id,
                    AnalysisResult.analysis_type == "frame",
                    AnalysisResult.image_saved == 1,
                    AnalysisResult.timestamp <= timestamp
                ).order_by(AnalysisResult.timestamp.desc()).first()
            
            if not result:
                return None
            
            return {
                "id": result.id,
                "frame_idx": result.frame_idx,
                "timestamp": result.timestamp,
                "tool_localization": result.tool_localization,
                "surgical_action": result.surgical_action,
                "surgical_phase": result.surgical_phase,
                "image_path": result.image_path
            }
    
    def get_window_summary_at_timestamp(
        self,
        session_id: str,
        timestamp: float
    ) -> Optional[Dict[str, Any]]:
        """获取包含指定时间戳的窗口总结"""
        with self.get_session() as session:
            # 查找包含该时间戳的窗口
            result = session.query(AnalysisResult).filter(
                AnalysisResult.session_id == session_id,
                AnalysisResult.analysis_type == "window",
                AnalysisResult.window_start <= timestamp,
                AnalysisResult.window_end > timestamp
            ).first()
            
            if not result:
                # 如果没有精确匹配，取最近的窗口
                result = session.query(AnalysisResult).filter(
                    AnalysisResult.session_id == session_id,
                    AnalysisResult.analysis_type == "window",
                    AnalysisResult.window_start <= timestamp
                ).order_by(AnalysisResult.window_start.desc()).first()
            
            if not result:
                return None
            
            return {
                "id": result.id,
                "window_id": result.window_id,
                "window_start": result.window_start,
                "window_end": result.window_end,
                "glm_summary": result.glm_summary,
                "others": json.loads(result.others_data) if result.others_data else None,
                "surgical_phase": result.surgical_phase
            }
    
    def get_all_window_summaries(
        self,
        session_id: str
    ) -> List[Dict[str, Any]]:
        """获取会话的所有窗口总结"""
        with self.get_session() as session:
            results = session.query(AnalysisResult).filter(
                AnalysisResult.session_id == session_id,
                AnalysisResult.analysis_type == "window"
            ).order_by(AnalysisResult.window_id.asc()).all()
            
            return [
                {
                    "id": r.id,
                    "window_id": r.window_id,
                    "window_start": r.window_start,
                    "window_end": r.window_end,
                    "glm_summary": r.glm_summary,
                    "others": json.loads(r.others_data) if r.others_data else None,
                    "surgical_phase": r.surgical_phase
                }
                for r in results
            ]
    
    def get_recent_context(self, session_id: str, limit: int = 10) -> str:
        """获取最近的分析结果作为上下文"""
        analyses = self.get_analyses(session_id, limit=limit)
        
        if not analyses:
            return "暂无手术图像分析记录。"
        
        context_parts = ["## 最近的手术分析记录\n"]
        for i, a in enumerate(analyses[:limit]):
            ts = a.get("timestamp", 0) or 0
            context_parts.append(f"### 分析 {i+1} (时间: {ts:.1f}秒)")
            if a.get("surgical_phase"):
                context_parts.append(f"- 手术阶段: {a['surgical_phase'][:200]}")
            if a.get("surgical_action"):
                context_parts.append(f"- 手术动作: {a['surgical_action'][:200]}")
            if a.get("tool_localization"):
                context_parts.append(f"- 工具定位: {a['tool_localization'][:200]}")
            if a.get("glm_summary"):
                context_parts.append(f"- 总结: {a['glm_summary'][:300]}")
            context_parts.append("")
        
        return "\n".join(context_parts)
    
    # ========================================================================
    # Chat History Operations
    # ========================================================================
    
    def save_chat(
        self,
        session_id: str,
        role: str,
        content: str,
        audio_duration: float = None,
        context_analysis_ids: List[int] = None
    ) -> int:
        """保存对话消息"""
        with self.get_session() as session:
            msg = ChatHistory(
                session_id=session_id,
                role=role,
                content=content,
                audio_duration=audio_duration,
                context_analysis_ids=json.dumps(context_analysis_ids) if context_analysis_ids else None
            )
            session.add(msg)
            session.flush()
            return msg.id
    
    def get_chat_history(
        self,
        session_id: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """获取对话历史"""
        with self.get_session() as session:
            results = session.query(ChatHistory).filter(
                ChatHistory.session_id == session_id
            ).order_by(ChatHistory.created_at.desc()).limit(limit).all()
            
            return [
                {
                    "id": r.id,
                    "role": r.role,
                    "content": r.content,
                    "audio_duration": r.audio_duration,
                    "created_at": r.created_at.isoformat() if r.created_at else None
                }
                for r in reversed(results)  # 按时间正序返回
            ]
    
    def clear_chat(self, session_id: str):
        """清除对话历史"""
        with self.get_session() as session:
            session.query(ChatHistory).filter(
                ChatHistory.session_id == session_id
            ).delete()
    
    # ========================================================================
    # Compressed Summary Operations
    # ========================================================================
    
    def save_compressed_summary(
        self,
        session_id: str,
        window_start_id: int,
        window_end_id: int,
        compressed_text: str,
        time_start: float = None,
        time_end: float = None,
        source_window_ids: List[int] = None,
        processing_time: float = None
    ) -> int:
        """保存压缩总结"""
        with self.get_session() as session:
            summary = CompressedSummary(
                session_id=session_id,
                window_start_id=window_start_id,
                window_end_id=window_end_id,
                window_count=window_end_id - window_start_id + 1,
                time_start=time_start,
                time_end=time_end,
                compressed_text=compressed_text,
                source_window_ids=json.dumps(source_window_ids) if source_window_ids else None,
                processing_time=processing_time
            )
            session.add(summary)
            session.flush()
            logger.info(f"[MySQLService] Saved compressed summary {summary.id} for windows {window_start_id}-{window_end_id}")
            return summary.id
    
    def get_compressed_summaries(
        self,
        session_id: str,
        limit: int = None
    ) -> List[Dict[str, Any]]:
        """获取会话的所有压缩总结"""
        with self.get_session() as session:
            query = session.query(CompressedSummary).filter(
                CompressedSummary.session_id == session_id
            ).order_by(CompressedSummary.window_start_id.asc())
            
            if limit:
                query = query.limit(limit)
            
            results = query.all()
            
            return [
                {
                    "id": r.id,
                    "window_start_id": r.window_start_id,
                    "window_end_id": r.window_end_id,
                    "window_count": r.window_count,
                    "time_start": r.time_start,
                    "time_end": r.time_end,
                    "compressed_text": r.compressed_text,
                    "created_at": r.created_at.isoformat() if r.created_at else None
                }
                for r in results
            ]
    
    def get_compressed_summaries_context(
        self,
        session_id: str,
        limit: int = None
    ) -> str:
        """获取压缩总结作为上下文字符串"""
        summaries = self.get_compressed_summaries(session_id, limit)
        
        if not summaries:
            return "暂无手术记录总结。"
        
        context_parts = ["## 手术记录总结\n"]
        for i, s in enumerate(summaries):
            time_range = ""
            if s.get("time_start") is not None and s.get("time_end") is not None:
                s_min, s_sec = divmod(int(s['time_start']), 60)
                e_min, e_sec = divmod(int(s['time_end']), 60)
                time_range = f" ({s_min}:{s_sec:02d} - {e_min}:{e_sec:02d})"
            
            context_parts.append(f"### 阶段 {i+1}{time_range}")
            context_parts.append(s["compressed_text"])
            context_parts.append("")
        
        return "\n".join(context_parts)
    
    def get_last_compressed_window_id(self, session_id: str) -> Optional[int]:
        """获取最后一个被压缩的窗口ID"""
        with self.get_session() as session:
            result = session.query(func.max(CompressedSummary.window_end_id)).filter(
                CompressedSummary.session_id == session_id
            ).scalar()
            return result
    
    def get_uncompressed_window_count(self, session_id: str) -> int:
        """获取未被压缩的窗口数量"""
        last_compressed_id = self.get_last_compressed_window_id(session_id)
        
        with self.get_session() as session:
            query = session.query(func.count(AnalysisResult.id)).filter(
                AnalysisResult.session_id == session_id,
                AnalysisResult.analysis_type == "window"
            )
            
            if last_compressed_id is not None:
                query = query.filter(AnalysisResult.window_id > last_compressed_id)
            
            return query.scalar() or 0
    
    def get_uncompressed_windows(self, session_id: str) -> List[Dict[str, Any]]:
        """获取未被压缩的窗口总结"""
        last_compressed_id = self.get_last_compressed_window_id(session_id)
        
        with self.get_session() as session:
            query = session.query(AnalysisResult).filter(
                AnalysisResult.session_id == session_id,
                AnalysisResult.analysis_type == "window"
            )
            
            if last_compressed_id is not None:
                query = query.filter(AnalysisResult.window_id > last_compressed_id)
            
            results = query.order_by(AnalysisResult.window_id.asc()).all()
            
            return [
                {
                    "id": r.id,
                    "window_id": r.window_id,
                    "window_start": r.window_start,
                    "window_end": r.window_end,
                    "glm_summary": r.glm_summary,
                    "others": json.loads(r.others_data) if r.others_data else None,
                    "surgical_phase": r.surgical_phase
                }
                for r in results
            ]
    
    def clear_compressed_summaries(self, session_id: str):
        """清除压缩总结"""
        with self.get_session() as session:
            session.query(CompressedSummary).filter(
                CompressedSummary.session_id == session_id
            ).delete()


# ============================================================================
# Global instance and helpers
# ============================================================================

_mysql_service: Optional[MySQLService] = None


def get_mysql_service() -> MySQLService:
    """Get the global MySQL service instance"""
    global _mysql_service
    if _mysql_service is None:
        _mysql_service = MySQLService()
    return _mysql_service


def init_mysql():
    """Initialize MySQL database"""
    try:
        service = get_mysql_service()
        _ = service.engine  # Trigger connection
        logger.info("[MySQLService] MySQL initialized successfully")
        return True
    except Exception as e:
        logger.error(f"[MySQLService] MySQL initialization failed: {e}")
        return False


# ============================================================================
# Backward compatibility aliases
# ============================================================================

def save_surgr1_analysis(**kwargs) -> int:
    """兼容旧API - 保存SurgR1分析"""
    return get_mysql_service().save_analysis(**kwargs)

def save_glm_summary(session_id: str, summary_text: str, **kwargs) -> int:
    """兼容旧API - 保存GLM总结"""
    return get_mysql_service().save_analysis(
        session_id=session_id,
        glm_summary=summary_text,
        **kwargs
    )

def save_conversation(session_id: str, role: str, content: str, **kwargs) -> int:
    """兼容旧API - 保存对话"""
    return get_mysql_service().save_chat(session_id, role, content, **kwargs)

def get_conversation_history(session_id: str, limit: int = 50) -> List[Dict]:
    """兼容旧API - 获取对话历史"""
    return get_mysql_service().get_chat_history(session_id, limit)

def get_recent_surgr1_context(session_id: str, limit: int = 10) -> str:
    """兼容旧API - 获取分析上下文"""
    return get_mysql_service().get_recent_context(session_id, limit)

def get_recent_glm_context(session_id: str, limit: int = 5) -> str:
    """兼容旧API - 获取GLM上下文"""
    return get_mysql_service().get_recent_context(session_id, limit)

def clear_conversation(session_id: str):
    """兼容旧API - 清除对话"""
    get_mysql_service().clear_chat(session_id)
