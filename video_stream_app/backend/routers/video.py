"""
Video Streaming and Control API Routes
"""
import os
import asyncio
import time
import logging
import threading
import urllib.request
import subprocess
import glob
from urllib.parse import urlparse
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends, BackgroundTasks, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
import json
import cv2

logger = logging.getLogger(__name__)

from ..database import get_db, create_video_session, get_video_session, get_all_sessions, update_session_status
from ..services.video_processor import VideoProcessor, ProcessingState
from ..services.mysql_service import get_mysql_service
from ..services.frame_storage_service import get_frame_storage_service
from ..services.decklink_capture import DeckLinkCapture
from ..services.local_video_source import (
    SIMULATOR_URI,
    get_simulator_source,
    resolve_video_source,
)
from ..config import settings

router = APIRouter(prefix="/api/video", tags=["video"])

# Store active processors
active_processors = {}

SIMULATED_CAPTURE_URL = os.getenv("SURGR1_SIMULATOR_STREAM_URL", "http://127.0.0.1:9001/stream")
SIMULATED_CAPTURE_NAME = "手术室采集卡模拟源"


def _get_simulator_info(stream_url: str = SIMULATED_CAPTURE_URL, timeout: float = 1.0) -> Optional[dict]:
    parsed = urlparse(stream_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    try:
        info_url = f"{parsed.scheme}://{parsed.netloc}/info"
        with urllib.request.urlopen(info_url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        logger.debug(f"[CaptureSimulator] /info unavailable: {e}")
        return None


def _simulator_capture_device() -> Optional[dict]:
    source = get_simulator_source(SIMULATED_CAPTURE_URL)
    if not source:
        return None

    return {
        "device_id": -1,
        "device_name": SIMULATED_CAPTURE_NAME,
        "device_path": SIMULATOR_URI,
        "width": int(source.width or 1920),
        "height": int(source.height or 1080),
        "fps": float(source.fps or 30.0),
        "backend": "simulator",
        "default_mode": "1080p30",
        "supported_modes": ["1080p30"],
        "is_simulated": True,
    }


class DisplayStreamState:
    """Shared latest-frame reader for UI playback.

    The UI should not queue every MJPEG frame. This reader continuously keeps
    only the latest encoded JPEG; slow clients naturally drop old frames.
    """

    def __init__(self, session_id: str, video_path: str, fps: float, quality: int, max_width: int):
        self.session_id = session_id
        self.video_path = video_path
        self.fps = max(1.0, float(fps))
        self.quality = int(quality)
        self.max_width = int(max_width or 0)
        self.latest_jpeg: Optional[bytes] = None
        self.sequence = 0
        self.clients = 0
        self.running = False
        self.last_client_time = time.time()
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self._local_file_mode = False

    def _resolve_source(self):
        resolved = resolve_video_source(self.video_path)
        if resolved.is_simulator and resolved.source != self.video_path:
            logger.info("[DisplayStream] Using simulator source file directly: %s", resolved.source)
            return resolved.source, True, float(resolved.fps or self.fps)
        return resolved.source, False, self.fps

    def start(self):
        with self.lock:
            self.clients += 1
            self.last_client_time = time.time()
            if self.running:
                return
            self.running = True
            self.thread = threading.Thread(
                target=self._reader_loop,
                name=f"display-stream-{self.session_id[:6]}",
                daemon=True,
            )
            self.thread.start()

    def release(self):
        with self.lock:
            self.clients = max(0, self.clients - 1)
            self.last_client_time = time.time()

    def stop(self):
        with self.lock:
            self.running = False

    def snapshot(self):
        with self.lock:
            return self.sequence, self.latest_jpeg

    def _prepare_frame(self, frame):
        if self.max_width:
            h, w = frame.shape[:2]
            if w > self.max_width:
                new_h = max(1, int(h * (self.max_width / w)))
                frame = cv2.resize(frame, (self.max_width, new_h), interpolation=cv2.INTER_AREA)
        return frame

    def _reader_loop(self):
        cap = None
        try:
            source_path, self._local_file_mode, source_fps = self._resolve_source()
            cap = _open_live_video_source(source_path)
            if not cap.isOpened():
                logger.error(f"[DisplayStream] Cannot open video: {source_path}")
                return

            encode_params = [cv2.IMWRITE_JPEG_QUALITY, self.quality]
            publish_fps = min(self.fps, max(1.0, float(source_fps or self.fps)))
            min_interval = 1.0 / publish_fps
            last_publish = 0.0
            next_frame_time = time.perf_counter()
            logger.info(
                f"[DisplayStream] Started {self.session_id}: fps={publish_fps:.1f}, "
                f"quality={self.quality}, max_width={self.max_width or 'source'}, "
                f"source={'file' if self._local_file_mode else 'stream'}"
            )

            while True:
                with self.lock:
                    if not self.running:
                        break
                    if self.clients <= 0 and time.time() - self.last_client_time > 3:
                        self.running = False
                        break

                if self._local_file_mode:
                    now_perf = time.perf_counter()
                    if now_perf < next_frame_time:
                        time.sleep(min(0.01, next_frame_time - now_perf))
                        continue
                elif last_publish:
                    wait_time = min_interval - (time.time() - last_publish)
                    if wait_time > 0:
                        time.sleep(min(wait_time, 0.02))

                ret, frame = cap.read()
                if not ret:
                    if self._local_file_mode:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        next_frame_time = time.perf_counter() + min_interval
                    else:
                        time.sleep(0.02)
                    continue

                last_publish = time.time()

                if self._local_file_mode:
                    next_frame_time = max(next_frame_time + min_interval, time.perf_counter())

                frame = self._prepare_frame(frame)
                ok, jpeg = cv2.imencode(".jpg", frame, encode_params)
                if not ok:
                    continue

                with self.lock:
                    self.latest_jpeg = jpeg.tobytes()
                    self.sequence += 1
        except Exception as e:
            logger.warning(f"[DisplayStream] Reader stopped for {self.session_id}: {e}")
        finally:
            if cap is not None:
                cap.release()
            with self.lock:
                self.running = False
            logger.info(f"[DisplayStream] Ended {self.session_id}")


display_streams = {}


def _open_live_video_source(video_path: str):
    """Open a live display source, including local capture-card device URIs."""
    import platform

    resolved = resolve_video_source(video_path)
    if resolved.is_simulator and resolved.source != video_path:
        cap = cv2.VideoCapture(resolved.source, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    if video_path.startswith("decklink://"):
        return DeckLinkCapture(video_path)

    if video_path.startswith("device://"):
        device_spec = video_path.replace("device://", "")
        try:
            device_id = int(device_spec)
            if platform.system() == "Linux":
                cap = cv2.VideoCapture(f"/dev/video{device_id}", cv2.CAP_V4L2)
            else:
                cap = cv2.VideoCapture(device_id, cv2.CAP_DSHOW if platform.system() == "Windows" else 0)
        except ValueError:
            if platform.system() == "Windows":
                cap = cv2.VideoCapture(f"video={device_spec}", cv2.CAP_DSHOW)
            else:
                cap = cv2.VideoCapture(device_spec)
    else:
        cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)

    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


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
    video_path: Optional[str] = None
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


class CaptureDeviceConnectRequest(BaseModel):
    """Request to connect to a local capture card device"""
    device_id: int = 0  # Device index (0, 1, 2, ...)
    device_name: str = ""  # Optional device name for Windows DirectShow
    backend: str = "auto"  # auto, decklink, v4l2, default
    mode: str = "1080p30"  # DeckLink display mode
    auto_analyze: bool = True


def _list_capture_devices():
    """
    List available video capture devices.
    Returns a list of device info dicts.
    """
    devices = []

    simulated_device = _simulator_capture_device()
    if simulated_device:
        devices.append(simulated_device)

    decklink_modes = [
        "1080p30", "1080p2997", "1080p25", "1080p24", "1080p50", "1080p60", "1080p5994",
        "720p60", "720p5994", "720p50",
        "2160p30", "2160p2997", "2160p25", "2160p24",
    ]

    try:
        if glob.glob("/dev/blackmagic/io*"):
            monitor = subprocess.run(
                ["gst-device-monitor-1.0", "Video/Source"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = monitor.stdout + monitor.stderr
            if "DeckLink" in output or "decklink" in output.lower():
                devices.append({
                    "device_id": 0,
                    "device_name": "DeckLink Mini Recorder 4K",
                    "device_path": "/dev/blackmagic/io0",
                    "width": 1920,
                    "height": 1080,
                    "fps": 30.0,
                    "backend": "decklink",
                    "default_mode": "1080p30",
                    "supported_modes": decklink_modes,
                })
    except Exception as e:
        logger.debug(f"[Capture] DeckLink detection skipped: {e}")
    
    # Try to detect devices by index (works on Linux and Windows)
    for i in range(10):  # Check first 10 indices
        try:
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                # Get device info
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                
                # Try to read a frame to verify it's a real device
                ret, _ = cap.read()
                cap.release()
                
                if ret and width > 0 and height > 0:
                    devices.append({
                        "device_id": i,
                        "device_name": f"Capture Device {i}",
                        "width": width,
                        "height": height,
                        "fps": fps,
                        "backend": "default"
                    })
        except Exception:
            pass
    
    # On Linux, also check /dev/video* devices
    import platform
    if platform.system() == "Linux":
        v4l2_devices = glob.glob("/dev/video*")
        for dev_path in v4l2_devices:
            try:
                # Extract device number
                dev_num = int(dev_path.replace("/dev/video", ""))
                # Check if already in list
                if not any(d["device_id"] == dev_num for d in devices):
                    cap = cv2.VideoCapture(dev_path, cv2.CAP_V4L2)
                    if cap.isOpened():
                        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                        ret, _ = cap.read()
                        cap.release()
                        
                        if ret and width > 0 and height > 0:
                            devices.append({
                                "device_id": dev_num,
                                "device_name": f"V4L2 Device ({dev_path})",
                                "device_path": dev_path,
                                "width": width,
                                "height": height,
                                "fps": fps,
                                "backend": "v4l2"
                            })
            except Exception:
                pass
    
    return devices


def _open_simulator_capture():
    source = get_simulator_source(SIMULATED_CAPTURE_URL)
    if not source:
        return None, "本地手术室采集卡模拟视频不可用，请检查 SURGR1_SIMULATOR_VIDEO 或 stream_simulator/media"

    cap = cv2.VideoCapture(source.source, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return None, f"无法打开本地手术室采集卡模拟源: {source.source}"

    ret, _ = cap.read()
    if not ret:
        cap.release()
        return None, f"无法读取本地手术室采集卡模拟源: {source.source}"

    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    return cap, None


def _open_capture_device(device_id: int, device_name: str = ""):
    """
    Open a capture device by ID or name.
    Supports Windows DirectShow and Linux V4L2.
    """
    import platform
    
    cap = None
    error = None
    
    try:
        if device_name.startswith("decklink:"):
            mode = device_name.split(":", 1)[1] or "1080p30"
            cap = DeckLinkCapture(f"decklink://{device_id}?mode={mode}")
            if not cap.isOpened():
                return None, f"无法打开 DeckLink 采集设备 {device_id} ({mode})"
            ret, _ = cap.read()
            if not ret:
                cap.release()
                return None, f"无法从 DeckLink 采集设备读取帧，请确认输入源模式为 {mode}"
            return cap, None

        if platform.system() == "Windows" and device_name:
            # Windows DirectShow with device name
            cap = cv2.VideoCapture(f"video={device_name}", cv2.CAP_DSHOW)
        elif platform.system() == "Linux":
            # Linux V4L2
            dev_path = f"/dev/video{device_id}"
            cap = cv2.VideoCapture(dev_path, cv2.CAP_V4L2)
        else:
            # Default: use device index
            cap = cv2.VideoCapture(device_id)
        
        if not cap or not cap.isOpened():
            return None, f"无法打开采集设备 {device_id}"
        
        # Verify by reading a frame
        ret, _ = cap.read()
        if not ret:
            cap.release()
            return None, f"无法从采集设备 {device_id} 读取帧"
        
        return cap, None
        
    except Exception as e:
        if cap:
            cap.release()
        return None, str(e)


@router.get("/capture-devices")
async def list_capture_devices():
    """
    List available video capture devices (capture cards, webcams, etc.)
    
    Returns a list of detected devices with their capabilities.
    """
    loop = asyncio.get_event_loop()
    
    try:
        devices = await asyncio.wait_for(
            loop.run_in_executor(None, _list_capture_devices),
            timeout=10.0
        )
    except asyncio.TimeoutError:
        devices = []
    
    return {
        "success": True,
        "devices": devices,
        "count": len(devices),
        "hint": "使用 device_id 连接采集卡，如: POST /api/video/connect-capture"
    }


@router.post("/connect-capture")
async def connect_to_capture_device(
    request: CaptureDeviceConnectRequest,
    db: Session = Depends(get_db)
):
    """
    Connect to a local video capture device (capture card, webcam).
    
    This allows direct capture from devices like Blackmagic DeckLink
    without needing to convert to RTSP/HTTP stream first.
    
    Args:
        device_id: Device index (0, 1, 2, ...)
        device_name: Optional device name for Windows DirectShow
        auto_analyze: Whether to start analysis automatically
    """
    loop = asyncio.get_event_loop()
    backend = (request.backend or "auto").lower()
    probe_name = request.device_name
    if backend == "decklink":
        probe_name = f"decklink:{request.mode or '1080p30'}"

    try:
        if backend == "simulator":
            cap, error = await asyncio.wait_for(
                loop.run_in_executor(None, _open_simulator_capture),
                timeout=10.0,
            )
        else:
            cap, error = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    _open_capture_device,
                    request.device_id,
                    probe_name,
                ),
                timeout=10.0,
            )
    except asyncio.TimeoutError:
        raise HTTPException(408, f"连接采集设备超时 (10秒)")
    
    if error or cap is None:
        raise HTTPException(400, f"无法连接采集设备: {error or '未知错误'}")
    
    # Get device properties
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if backend == "simulator":
        simulator_source = get_simulator_source(SIMULATED_CAPTURE_URL)
        fps = float((simulator_source.fps if simulator_source else None) or fps or 30.0)
        width = int((simulator_source.width if simulator_source else None) or width or 1920)
        height = int((simulator_source.height if simulator_source else None) or height or 1080)
    
    # Create a device URL for internal use
    # Format: device://{device_id} or device://{device_name}
    if backend == "simulator":
        device_url = SIMULATOR_URI
    elif backend == "decklink":
        mode = request.mode or "1080p30"
        device_url = f"decklink://{request.device_id}?mode={mode}"
    else:
        device_url = f"device://{request.device_id}"
    if request.device_name and backend not in {"decklink", "simulator"}:
        device_url = f"device://{request.device_name}"
    
    device_display_name = request.device_name or f"Capture Device {request.device_id}"
    if backend == "simulator":
        device_display_name = SIMULATED_CAPTURE_NAME
    elif backend == "decklink":
        device_display_name = f"{device_display_name} ({request.mode or '1080p30'})"
    
    # Create database session
    session = create_video_session(
        db=db,
        video_path=device_url,
        video_name=f"📹 {device_display_name}",
        duration=0,  # Live capture has no fixed duration
        fps=fps,
        width=width,
        height=height,
        total_frames=0
    )
    
    session_id = session["session_id"]
    video_name = f"📹 {device_display_name}"
    
    # Create session storage folder
    frame_storage = get_frame_storage_service()
    storage_path = frame_storage.create_session_folder(session_id, device_display_name)
    
    # Save to MySQL with storage path
    mysql_service = get_mysql_service()
    try:
        mysql_service.create_video_session(
            session_id=session_id,
            video_name=video_name,
            video_path=device_url,
            video_type="capture",  # New type for capture devices
            fps=fps,
            width=width,
            height=height,
            storage_path=storage_path
        )
    except Exception as e:
        logger.warning(f"Failed to save capture session to MySQL: {e}")
    
    # Mark as processing (live)
    update_session_status(db, session_id, "processing")
    
    logger.info(f"[Capture] Connected to device {request.device_id}: {width}x{height} @ {fps}fps")
    
    return {
        "session_id": session_id,
        "video_name": video_name,
        "video_path": device_url,
        "duration": 0,
        "fps": fps,
        "width": width,
        "height": height,
        "storage_path": storage_path,
        "message": f"成功连接采集设备: {device_display_name}"
    }


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
            video_path=s.get("video_path"),
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
    
    raw_video_path = session.get("video_path", "")
    resolved = resolve_video_source(raw_video_path)
    video_path = Path(resolved.source)
    if not video_path.exists():
        raise HTTPException(404, "Video file not found")
    
    return FileResponse(
        path=str(video_path),
        media_type="video/mp4",
        filename=session.get("video_name", "video.mp4")
    )


@router.get("/mjpeg-proxy/{session_id}")
async def mjpeg_proxy_stream(
    session_id: str,
    fps: float = Query(25.0, ge=1.0, le=60.0, description="Target FPS for streaming"),
    quality: int = Query(85, ge=50, le=100, description="JPEG quality (50-100)"),
    max_width: int = Query(0, ge=0, le=3840, description="Resize frames wider than this value; 0 keeps source size"),
    passthrough: bool = Query(False, description="For HTTP MJPEG sources, forward bytes without decode/resize/re-encode"),
    show_yolo: bool = Query(False, description="Enable YOLO tool detection overlay (默认关闭：bbox 闪烁影响观看，YOLO 仍在窗口分析里使用)"),
    db: Session = Depends(get_db)
):
    """
    MJPEG proxy stream with real-time frame pacing.
    
    Converts any video source (RTSP, HTTP, local file) to MJPEG format
    with proper timing for smooth playback in browsers.
    
    Args:
        session_id: Video session ID
        fps: Target frames per second (default 25)
        quality: JPEG compression quality (default 85)
        max_width: Downscale displayed frames to reduce bandwidth/render load
        passthrough: Preserve original HTTP MJPEG bytes; disables fps/quality/max_width
    """
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    video_path = session.get("video_path", "")
    if not video_path:
        raise HTTPException(400, "No video path for session")
    
    # Get source FPS for reference
    source_fps = session.get("fps", 25.0) or 25.0
    
    # ============================================================
    # Fast path：HTTP/HTTPS MJPEG 源（如 stream_simulator）可以"字节透传"，
    # 不走 cv2 解码/再 JPEG 编码。这样：
    #   - 不占 CPU/GPU 做双重编解码
    #   - 原始帧序直接转发，节奏由 simulator 决定（它已经做好 25fps pacing）
    #   - 实测 simulator 源站 p95=54ms / max=59ms 非常稳 → 浏览器也能跟上
    # 只保留 cv2 路径给本地文件 / RTSP / device://（这些格式不是现成 MJPEG）。
    # ============================================================
    if passthrough and video_path.startswith(("http://", "https://")) and not show_yolo:
        import httpx as _httpx
        async def passthrough_mjpeg():
            # stream_simulator 直出 multipart/x-mixed-replace，我们只转发字节。
            # trust_env=False：backend 启动时 export 了 https_proxy（给 Gemini 用），
            # 千万不能把 localhost:9001 流量也走那个代理。
            try:
                async with _httpx.AsyncClient(timeout=None, trust_env=False) as client:
                    async with client.stream("GET", video_path, headers={"Accept": "*/*"}) as resp:
                        async for chunk in resp.aiter_raw(chunk_size=8192):
                            if chunk:
                                yield chunk
            except Exception as e:
                logger.warning(f"[MJPEG Proxy passthrough] closed: {e}")
        return StreamingResponse(
            passthrough_mjpeg(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Access-Control-Allow-Origin": "*",
                "X-Accel-Buffering": "no",
            },
        )

    async def generate_mjpeg_frames():
        """Generator that yields MJPEG frames with real-time pacing (cv2 path, for
        local files / RTSP / device URIs that need re-encoding)"""
        cap = None
        # YOLO bbox overlay：默认关闭（show_yolo=False）。
        # YOLO 仍在 analysis.py 的窗口分析里被 expert_fusion 使用，
        # 只是不在实时视频流上画 bbox —— 避免跟踪器冷启动卡顿和 bbox 闪烁。
        yolo_svc = None
        try:
            # Open video source (works for files, RTSP, HTTP streams, device://)
            cap = _open_live_video_source(video_path)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)  # Minimize buffer for lower latency
            
            if not cap.isOpened():
                logger.error(f"[MJPEG Proxy] Cannot open video: {video_path}")
                return
            
            # Calculate frame interval for real-time pacing
            actual_fps = cap.get(cv2.CAP_PROP_FPS) or source_fps
            target_fps = min(fps, actual_fps)  # Don't exceed source FPS
            frame_interval = 1.0 / target_fps

            logger.info(
                f"[MJPEG Proxy] Starting stream for {session_id} at {target_fps:.1f} FPS, "
                f"quality={quality}, max_width={max_width or 'source'}"
            )

            # Pacing state：start_time 在"第一次成功 cap.read()"之后再设，
            # 否则 HTTP/RTSP 首次连接耗时会让第 0 帧起就"已经迟到"，导致后续
            # N 帧追赶式 burst（用户看到开头巨快）。后续偏差超过 1 个 frame_interval
            # 就直接丢弃积压并重锚相位，避免"烧帧追赶"式卡顿。
            start_time: Optional[float] = None
            frame_idx = 0
            consecutive_errors = 0

            # cap.read() 与 cv2.imencode 都是同步 C 调用，直接在 async 生成器里 run
            # 会阻塞事件循环（实测 max stall 300+ ms，就是用户看到的"中间卡顿"）。
            # 把它们 offload 到一个专用线程池，让事件循环全程自由。
            import concurrent.futures as _cf
            executor = _cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"mjpeg-{session_id[:6]}")
            loop = asyncio.get_event_loop()
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]

            def _sync_read():
                return cap.read()

            def _prepare_frame(f):
                if max_width and f is not None:
                    h, w = f.shape[:2]
                    if w > max_width:
                        new_h = max(1, int(h * (max_width / w)))
                        f = cv2.resize(f, (max_width, new_h), interpolation=cv2.INTER_AREA)
                return f

            def _sync_encode(f):
                f = _prepare_frame(f)
                return cv2.imencode('.jpg', f, encode_params)

            try:
                while True:
                    ret, frame = await loop.run_in_executor(executor, _sync_read)

                    if not ret:
                        consecutive_errors += 1
                        if consecutive_errors > 10:
                            logger.warning(f"[MJPEG Proxy] Too many read errors, stopping")
                            break
                        await asyncio.sleep(0.01)
                        continue

                    consecutive_errors = 0

                    # 首帧：把 start_time 锚定到真正出第一帧的时刻
                    if start_time is None:
                        start_time = time.time()

                    target_time = start_time + (frame_idx * frame_interval)
                    current_time = time.time()
                    wait_time = target_time - current_time

                    if wait_time > 0:
                        await asyncio.sleep(wait_time)
                    elif wait_time < -frame_interval:
                        # 偏差超过一个帧间隔 → 重锚相位，避免 burst 补帧
                        start_time = current_time - frame_idx * frame_interval

                    success, jpeg_data = await loop.run_in_executor(executor, _sync_encode, frame)
                    if not success:
                        continue

                    jpeg_bytes = jpeg_data.tobytes()
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(jpeg_bytes)).encode() + b"\r\n"
                        b"\r\n" + jpeg_bytes + b"\r\n"
                    )

                    frame_idx += 1

                    # Log progress periodically
                    if frame_idx % 250 == 0:
                        elapsed = time.time() - start_time
                        actual_rate = frame_idx / elapsed if elapsed > 0 else 0
                        logger.debug(f"[MJPEG Proxy] {session_id}: {frame_idx} frames, {actual_rate:.1f} fps")
            finally:
                executor.shutdown(wait=False)

        except asyncio.CancelledError:
            logger.info(f"[MJPEG Proxy] Stream cancelled for {session_id}")
        except Exception as e:
            logger.error(f"[MJPEG Proxy] Error: {e}")
        finally:
            if cap is not None:
                cap.release()
            logger.info(f"[MJPEG Proxy] Stream ended for {session_id}")
    
    return StreamingResponse(
        generate_mjpeg_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Access-Control-Allow-Origin": "*",
            # 防止反向代理/Vite dev server 把流式响应缓到一定大小才 flush
            # 造成"开头一批帧一起涌出"的视觉 burst。
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/display-mjpeg/{session_id}")
async def display_mjpeg_stream(
    session_id: str,
    fps: float = Query(20.0, ge=1.0, le=30.0, description="Display FPS"),
    quality: int = Query(68, ge=40, le=90, description="JPEG quality"),
    max_width: int = Query(1280, ge=320, le=1920, description="Display max width"),
    db: Session = Depends(get_db),
):
    """Latest-frame MJPEG stream for UI playback.

    Unlike /mjpeg-proxy, this endpoint never queues old frames for the client.
    If the browser or network stalls, the next emitted frame is the newest one.
    """
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    video_path = session.get("video_path", "")
    if not video_path:
        raise HTTPException(400, "No video path for session")

    key = (session_id, video_path, int(fps), int(quality), int(max_width))
    state = display_streams.get(key)
    if state is None or not state.running:
        state = DisplayStreamState(session_id, video_path, fps, quality, max_width)
        display_streams[key] = state

    async def generate_latest_frames():
        state.start()
        last_seq = -1
        frame_interval = 1.0 / max(1.0, fps)
        try:
            while True:
                seq, jpeg = state.snapshot()
                if jpeg and seq != last_seq:
                    last_seq = seq
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n"
                        b"\r\n" + jpeg + b"\r\n"
                    )
                await asyncio.sleep(frame_interval)
        except asyncio.CancelledError:
            pass
        finally:
            state.release()

    return StreamingResponse(
        generate_latest_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Access-Control-Allow-Origin": "*",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/ws-display/{session_id}")
async def websocket_display_stream(
    websocket: WebSocket,
    session_id: str,
    fps: float = Query(18.0, ge=1.0, le=30.0),
    quality: int = Query(64, ge=40, le=90),
    max_width: int = Query(960, ge=320, le=1920),
    db: Session = Depends(get_db),
):
    """Latest-frame WebSocket display stream.

    This is the browser preview path for remote/X11 testing. It sends binary
    JPEG frames and keeps no per-client backlog, so a slow renderer naturally
    drops old frames instead of drifting behind realtime.
    """
    session = get_video_session(db, session_id)
    if not session:
        await websocket.close(code=1008, reason="Session not found")
        return

    video_path = session.get("video_path", "")
    if not video_path:
        await websocket.close(code=1008, reason="No video path")
        return

    await websocket.accept()

    key = (session_id, video_path, int(fps), int(quality), int(max_width))
    state = display_streams.get(key)
    if state is None or not state.running:
        state = DisplayStreamState(session_id, video_path, fps, quality, max_width)
        display_streams[key] = state

    state.start()
    last_seq = -1
    frame_interval = 1.0 / max(1.0, fps)
    try:
        while True:
            seq, jpeg = state.snapshot()
            if jpeg and seq != last_seq:
                last_seq = seq
                await websocket.send_bytes(jpeg)
            await asyncio.sleep(frame_interval)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"[DisplayWS] Closed for {session_id}: {e}")
    finally:
        state.release()


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


@router.delete("/session/{session_id}")
async def delete_session(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Delete a video session and all its associated data (analysis results, chat history, storage files)"""
    
    # Check if session exists
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Stop any active processing
    if session_id in active_processors:
        active_processors[session_id].stop()
        del active_processors[session_id]
    
    # Delete from MySQL (includes storage folder cleanup)
    mysql_service = get_mysql_service()
    result = mysql_service.delete_video_session(session_id)
    
    # Also clean from in-memory cache
    from ..database.crud import delete_session_data
    delete_session_data(db, session_id)
    
    return {
        "success": True,
        "message": f"Session {session_id} deleted successfully",
        "details": result
    }


@router.delete("/sessions/all")
async def delete_all_sessions(
    db: Session = Depends(get_db)
):
    """Delete ALL video sessions and their associated data (analysis results, chat history, storage files)
    
    WARNING: This action is irreversible!
    """
    
    # Stop all active processors
    for session_id in list(active_processors.keys()):
        try:
            active_processors[session_id].stop()
            del active_processors[session_id]
        except Exception:
            pass
    
    # Delete all from MySQL (includes storage folder cleanup)
    mysql_service = get_mysql_service()
    result = mysql_service.delete_all_video_sessions()
    
    # Clear in-memory cache
    from ..database.models import _sessions_cache
    _sessions_cache.clear()
    
    return {
        "success": True,
        "message": "All sessions deleted successfully",
        "details": result
    }
