"""
Video Streaming and Control API Routes
"""
import os
import asyncio
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, BackgroundTasks, Query
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
import json
import cv2

from ..database import get_db, create_video_session, get_video_session, get_all_sessions, update_session_status
from ..services.video_processor import VideoProcessor, ProcessingState
from ..services.mysql_service import get_mysql_service
from ..services.frame_storage_service import get_frame_storage_service
from ..config import settings

router = APIRouter(prefix="/api/video", tags=["video"])

# Store active processors
active_processors = {}


class VideoUploadResponse(BaseModel):
    session_id: str
    video_name: str
    duration: float
    fps: float
    width: int
    height: int
    message: str


class SessionInfo(BaseModel):
    session_id: str
    video_name: str
    duration: float
    status: str
    current_position: float
    is_paused: bool


class ControlRequest(BaseModel):
    action: str  # play, pause, resume, stop, seek
    position: Optional[float] = None


class StreamConnectRequest(BaseModel):
    stream_url: str
    auto_analyze: bool = True


@router.post("/upload", response_model=VideoUploadResponse)
async def upload_video(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """Upload a video file for processing"""
    
    # Validate file type
    allowed_types = [".mp4", ".avi", ".mov", ".mkv", ".webm"]
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_types:
        raise HTTPException(400, f"Unsupported file type: {file_ext}")
    
    # Save file
    upload_path = settings.UPLOAD_DIR / file.filename
    with open(upload_path, "wb") as f:
        content = await file.read()
        f.write(content)
    
    # Get video metadata
    cap = cv2.VideoCapture(str(upload_path))
    if not cap.isOpened():
        raise HTTPException(400, "Cannot read video file")
    
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = total_frames / fps if fps > 0 else 0
    cap.release()
    
    # Create database session
    session = create_video_session(
        db=db,
        video_path=str(upload_path),
        video_name=file.filename,
        duration=duration,
        fps=fps,
        width=width,
        height=height,
        total_frames=total_frames
    )
    
    return VideoUploadResponse(
        session_id=session["session_id"],
        video_name=file.filename,
        duration=duration,
        fps=fps,
        width=width,
        height=height,
        message="Video uploaded successfully"
    )


@router.post("/load")
async def load_video_from_path(
    video_path: str,
    db: Session = Depends(get_db)
):
    """Load a video from existing path"""
    
    path = Path(video_path)
    if not path.exists():
        raise HTTPException(404, f"Video not found: {video_path}")
    
    # Get video metadata
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise HTTPException(400, "Cannot read video file")
    
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = total_frames / fps if fps > 0 else 0
    cap.release()
    
    # Create database session (in-memory)
    session = create_video_session(
        db=db,
        video_path=str(path),
        video_name=path.name,
        duration=duration,
        fps=fps,
        width=width,
        height=height,
        total_frames=total_frames
    )
    
    session_id = session["session_id"]
    video_name = path.name
    
    # Create session storage folder
    frame_storage = get_frame_storage_service()
    storage_path = frame_storage.create_session_folder(session_id, video_name)
    
    # Save to MySQL with storage path
    mysql_service = get_mysql_service()
    try:
        mysql_service.create_video_session(
            session_id=session_id,
            video_name=video_name,
            video_path=str(path),
            video_type="local",
            duration=duration,
            fps=fps,
            width=width,
            height=height,
            total_frames=total_frames,
            storage_path=storage_path
        )
    except Exception as e:
        print(f"[Video] Warning: Failed to save session to MySQL: {e}")
    
    return {
        "session_id": session_id,
        "video_name": video_name,
        "duration": duration,
        "fps": fps,
        "width": width,
        "height": height,
        "storage_path": storage_path
    }


def _sync_open_stream(stream_url: str):
    """Synchronous stream open - runs in thread pool"""
    try:
        # Set OpenCV timeout options for network streams
        cap = cv2.VideoCapture(stream_url, cv2.CAP_FFMPEG)
        # Set read timeout to 5 seconds
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
        
        if not cap.isOpened():
            return None, "无法打开视频流"
        
        # Try to read one frame to verify connection
        ret, _ = cap.read()
        if not ret:
            cap.release()
            return None, "无法读取视频流帧"
        
        return cap, None
    except Exception as e:
        return None, str(e)


@router.post("/connect-stream")
async def connect_to_stream(
    request: StreamConnectRequest,
    db: Session = Depends(get_db)
):
    """Connect to a live video stream (RTSP, HTTP, etc.)"""
    
    stream_url = request.stream_url
    
    # Run blocking cv2.VideoCapture in thread pool with timeout
    loop = asyncio.get_event_loop()
    
    try:
        cap, error = await asyncio.wait_for(
            loop.run_in_executor(None, _sync_open_stream, stream_url),
            timeout=10.0  # 10 second timeout
        )
    except asyncio.TimeoutError:
        raise HTTPException(408, f"连接超时 (10秒): {stream_url}")
    
    if error or cap is None:
        raise HTTPException(400, f"无法连接视频流: {stream_url} - {error or '未知错误'}")
    
    # Get stream properties
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0  # Default to 25fps if not available
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    
    # Extract stream name from URL
    from urllib.parse import urlparse
    parsed = urlparse(stream_url)
    stream_name = parsed.path.split('/')[-1] or f"stream_{parsed.hostname}"
    
    # Create database session (in-memory)
    session = create_video_session(
        db=db,
        video_path=stream_url,
        video_name=f"🔴 {stream_name}",
        duration=0,  # Live stream has no fixed duration
        fps=fps,
        width=width,
        height=height,
        total_frames=0
    )
    
    session_id = session["session_id"]
    video_name = f"🔴 {stream_name}"
    
    # Create session storage folder
    frame_storage = get_frame_storage_service()
    storage_path = frame_storage.create_session_folder(session_id, stream_name)
    
    # Save to MySQL with storage path
    mysql_service = get_mysql_service()
    try:
        mysql_service.create_video_session(
            session_id=session_id,
            video_name=video_name,
            video_path=stream_url,
            video_type="stream",
            fps=fps,
            width=width,
            height=height,
            storage_path=storage_path
        )
    except Exception as e:
        print(f"[Video] Warning: Failed to save session to MySQL: {e}")
    
    # Mark as processing (live)
    update_session_status(db, session_id, "processing")
    
    return {
        "session_id": session_id,
        "video_name": video_name,
        "video_path": stream_url,  # Include for frontend compatibility
        "stream_url": stream_url,
        "fps": fps,
        "width": width,
        "height": height,
        "storage_path": storage_path,
        "is_live": True,
        "message": "Connected to live stream"
    }


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """List all video sessions"""
    sessions = get_all_sessions(db, limit=limit)
    
    return [
        SessionInfo(
            session_id=s["session_id"],
            video_name=s["video_name"],
            duration=s.get("duration", 0),
            status=s.get("status", "unknown"),
            current_position=s.get("current_position", 0),
            is_paused=s.get("is_paused", False)
        )
        for s in sessions
    ]


@router.get("/session/{session_id}")
async def get_session_info(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get session information"""
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    return {
        "session_id": session["session_id"],
        "video_name": session["video_name"],
        "video_path": session.get("video_path", ""),
        "duration": session.get("duration", 0),
        "fps": session.get("fps", 0),
        "width": session.get("width", 0),
        "height": session.get("height", 0),
        "total_frames": session.get("total_frames", 0),
        "status": session.get("status", "unknown"),
        "current_position": session.get("current_position", 0),
        "is_paused": session.get("is_paused", False),
        "created_at": session.get("created_at", "")
    }


@router.post("/control/{session_id}")
async def control_video(
    session_id: str,
    request: ControlRequest,
    db: Session = Depends(get_db)
):
    """Control video playback (play, pause, resume, stop, seek)"""
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    action = request.action.lower()
    
    if action == "play":
        update_session_status(db, session_id, "processing", is_paused=False)
        return {"status": "playing", "position": session.get("current_position", 0)}
    
    elif action == "pause":
        update_session_status(db, session_id, "paused", is_paused=True)
        if session_id in active_processors:
            active_processors[session_id].pause()
        return {"status": "paused", "position": session.get("current_position", 0)}
    
    elif action == "resume":
        update_session_status(db, session_id, "processing", is_paused=False)
        if session_id in active_processors:
            active_processors[session_id].resume()
        return {"status": "playing", "position": session.get("current_position", 0)}
    
    elif action == "stop":
        update_session_status(db, session_id, "stopped", current_position=0, is_paused=False)
        if session_id in active_processors:
            active_processors[session_id].stop()
            del active_processors[session_id]
        return {"status": "stopped", "position": 0}
    
    elif action == "seek":
        if request.position is None:
            raise HTTPException(400, "Position required for seek")
        position = max(0, min(request.position, session.get("duration", 0)))
        update_session_status(db, session_id, session.get("status", "processing"), current_position=position)
        if session_id in active_processors:
            active_processors[session_id].seek(position)
        return {"status": session.get("status", "processing"), "position": position}
    
    else:
        raise HTTPException(400, f"Unknown action: {action}")


@router.get("/stream/{session_id}")
async def stream_video(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Stream video file for playback"""
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    video_path = Path(session.get("video_path", ""))
    if not video_path.exists():
        raise HTTPException(404, "Video file not found")
    
    return FileResponse(
        path=str(video_path),
        media_type="video/mp4",
        filename=session.get("video_name", "video.mp4")
    )


@router.get("/frame/{session_id}")
async def get_frame(
    session_id: str,
    timestamp: float = Query(..., ge=0),
    db: Session = Depends(get_db)
):
    """Get a single frame at timestamp"""
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    processor = VideoProcessor(
        video_path=session.get("video_path", ""),
        window_duration=settings.WINDOW_DURATION
    )
    
    frame = processor.extract_frame(timestamp)
    if frame is None:
        raise HTTPException(400, "Cannot extract frame")
    
    return {
        "frame_idx": frame.frame_idx,
        "timestamp": frame.timestamp,
        "image_base64": frame.to_base64()
    }


@router.get("/thumbnail/{session_id}")
async def get_thumbnail(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get video thumbnail (first frame)"""
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    processor = VideoProcessor(video_path=session.get("video_path", ""))
    frame = processor.extract_frame(0)
    
    if frame is None:
        raise HTTPException(400, "Cannot extract thumbnail")
    
    # Resize for thumbnail
    thumb = frame.image.copy()
    thumb.thumbnail((320, 180))
    
    from io import BytesIO
    import base64
    
    buffer = BytesIO()
    thumb.save(buffer, format="JPEG", quality=75)
    thumb_base64 = base64.b64encode(buffer.getvalue()).decode()
    
    return {
        "thumbnail": thumb_base64,
        "width": thumb.width,
        "height": thumb.height
    }

