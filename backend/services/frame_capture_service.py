"""
Frame Capture Service - 独立的帧捕获服务

将帧捕获与分析解耦：
- 帧捕获在**独立线程**中运行，不受 asyncio 事件循环阻塞影响
- 分析服务从已保存的帧中读取
- 确保帧存储完整性，不受分析延迟影响

关键设计：
- 使用 threading.Thread 而非 asyncio.Task
- 使用 time.sleep() 而非 asyncio.sleep()
- 独立于主事件循环运行
"""

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime

import cv2
import numpy as np
import platform

from .frame_storage_service import get_frame_storage_service, FrameStorageService
from .decklink_capture import DeckLinkCapture
from .local_video_source import PacedVideoCapture, resolve_video_source

logger = logging.getLogger(__name__)

# 加载配置
CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"

# 调试日志路径
DEBUG_LOG_PATH = Path("/data2/jj/proj/video_processor/.cursor/debug.log")


def _log_debug(msg: str, data: dict):
    """写入调试日志"""

def load_frame_capture_config() -> Dict[str, Any]:
    """加载帧捕获配置"""
    default_config = {
        "fps": 25,
        "quality": 85,
        "preview_fps": 10,
        "preview_quality": 40,
        "live_fps": 5,
        "live_quality": 68,
        "live_preview_fps": 2,
        "live_retention_seconds": 300,
        "prune_every_frames": 50
    }
    
    try:
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
            frame_storage_config = config.get("video_processing", {}).get("frame_storage", {})
            return {**default_config, **frame_storage_config}
    except Exception as e:
        logger.warning(f"[FrameCaptureService] Failed to load config: {e}, using defaults")
        return default_config


@dataclass
class FrameCaptureState:
    """帧捕获状态"""
    session_id: str
    storage_path: str
    video_source: str
    is_realtime_stream: bool
    
    # 运行状态
    is_running: bool = False
    should_stop: bool = False
    
    # 统计信息
    start_time: float = 0.0
    frames_captured: int = 0
    frames_saved: int = 0
    last_capture_time: float = 0.0
    
    # 配置
    capture_fps: int = 25
    preview_fps: int = 10
    quality: int = 85
    preview_quality: int = 40
    retention_seconds: int = 0
    prune_every_frames: int = 50


# 全局帧捕获线程注册表
_capture_threads: Dict[str, threading.Thread] = {}
_capture_states: Dict[str, FrameCaptureState] = {}
_capture_lock = threading.Lock()


def open_video_source(video_source: str) -> Optional[cv2.VideoCapture]:
    """
    打开视频源（支持多种格式）
    
    支持:
    - device://0 - 本地摄像头
    - rtsp://... - RTSP流
    - http://... - HTTP流（如MJPEG）
    - /path/to/video.mp4 - 本地文件
    """
    try:
        resolved = resolve_video_source(video_source)
        if resolved.is_simulator and resolved.source != video_source:
            cap = PacedVideoCapture(resolved.source, fps=resolved.fps, loop=True)
            if cap.isOpened():
                logger.info(
                    "[FrameCaptureService] Using paced simulator source: %s @ %.1ffps",
                    resolved.source,
                    cap.get(cv2.CAP_PROP_FPS),
                )
                return cap
            logger.error(f"[FrameCaptureService] Failed to open simulator source: {resolved.source}")
            return None

        if video_source.startswith("decklink://"):
            return DeckLinkCapture(video_source)

        if video_source.startswith("device://"):
            device_spec = video_source.replace("device://", "")
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
        elif video_source.startswith(("rtsp://", "http://", "https://")):
            # 网络流
            cap = cv2.VideoCapture(video_source)
            # 设置缓冲区大小以减少延迟
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)
        else:
            # 本地文件
            cap = cv2.VideoCapture(video_source)
        
        if cap.isOpened():
            return cap
        else:
            logger.error(f"[FrameCaptureService] Failed to open video source: {video_source}")
            return None
    except Exception as e:
        logger.error(f"[FrameCaptureService] Error opening video source: {e}")
        return None


def frame_capture_thread_func(
    session_id: str,
    video_source: str,
    storage_path: str,
    is_realtime_stream: bool,
    stream_start_time: float,
    on_frame_saved: Optional[Callable[[str, float, int], None]] = None
):
    """
    帧捕获线程函数 - 在独立线程中运行
    
    关键：使用 time.sleep() 而非 asyncio.sleep()，不受事件循环阻塞影响
    
    Args:
        session_id: 会话ID
        video_source: 视频源路径/URL
        storage_path: 帧存储路径
        is_realtime_stream: 是否为实时流
        stream_start_time: 流开始时间（用于计算时间戳）
        on_frame_saved: 帧保存后的回调函数(session_id, timestamp, frame_idx)
    """
    config = load_frame_capture_config()
    
    # 创建状态对象
    state = FrameCaptureState(
        session_id=session_id,
        storage_path=storage_path,
        video_source=video_source,
        is_realtime_stream=is_realtime_stream,
        capture_fps=int(config.get("live_fps", config["fps"])) if is_realtime_stream else int(config["fps"]),
        preview_fps=int(config.get("live_preview_fps", config["preview_fps"])) if is_realtime_stream else int(config["preview_fps"]),
        quality=int(config.get("live_quality", config["quality"])) if is_realtime_stream else int(config["quality"]),
        preview_quality=int(config["preview_quality"]),
        retention_seconds=int(config.get("live_retention_seconds", 0)) if is_realtime_stream else 0,
        prune_every_frames=int(config.get("prune_every_frames", 50))
    )
    
    with _capture_lock:
        _capture_states[session_id] = state
    
    # 计算帧间隔
    capture_interval = 1.0 / state.capture_fps  # 25fps = 0.04s
    preview_interval = 1.0 / state.preview_fps  # 10fps = 0.1s
    
    logger.info(f"[FrameCaptureService] Starting capture THREAD for session {session_id}")
    logger.info(
        f"[FrameCaptureService] Config: capture_fps={state.capture_fps}, "
        f"preview_fps={state.preview_fps}, quality={state.quality}, "
        f"retention={state.retention_seconds or 'unlimited'}s"
    )
    # 打开视频源
    cap = open_video_source(video_source)
    if not cap or not cap.isOpened():
        logger.error(f"[FrameCaptureService] Cannot open video source: {video_source}")
        state.is_running = False
        return
    
    # 获取帧存储服务
    frame_storage = get_frame_storage_service()
    
    # 初始化状态
    state.is_running = True
    state.start_time = stream_start_time if is_realtime_stream else time.time()
    
    frame_idx = 0
    saved_frame_idx = 0
    last_save_time = -capture_interval
    last_preview_save_time = -preview_interval
    
    # 获取视频FPS（用于本地文件时间计算）
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0    
    # 性能统计
    loop_count = 0
    total_read_time = 0
    total_save_time = 0
    last_stats_time = time.time()
    
    try:
        while not state.should_stop:
            loop_start = time.time()
            
            ret, bgr_frame = cap.read()
            read_time = time.time() - loop_start
            total_read_time += read_time
            
            if not ret:
                if is_realtime_stream:
                    # 实时流读取失败，等待重试
                    time.sleep(0.05)
                    continue
                else:
                    # 本地文件结束，循环播放
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    frame_idx = 0
                    continue
            
            # 计算当前时间戳
            if is_realtime_stream:
                current_time = time.time() - stream_start_time
            else:
                current_time = frame_idx / video_fps
            
            state.last_capture_time = current_time
            state.frames_captured += 1
            
            # 检查是否需要保存帧（根据capture_fps）
            # 注意：对于本地文件，视频帧间隔可能 > 或 < capture_interval
            should_save_frame = (current_time - last_save_time >= capture_interval)
            should_save_preview = (current_time - last_preview_save_time >= preview_interval)
            
            loop_count += 1
            
            if should_save_frame:
                last_save_time = current_time
                save_start = time.time()
                
                try:
                    # 保存原始帧
                    relative_path = frame_storage.save_frame(
                        storage_path=storage_path,
                        timestamp=current_time,
                        frame_data=bgr_frame,
                        frame_idx=saved_frame_idx,
                        subfolder="frames",
                        save_preview=should_save_preview,
                        quality=state.quality
                    )
                    
                    save_time = time.time() - save_start
                    total_save_time += save_time
                    
                    if relative_path:
                        saved_frame_idx += 1
                        state.frames_saved = saved_frame_idx
                        
                        # 更新预览时间（如果同时保存了预览）
                        if should_save_preview:
                            last_preview_save_time = current_time
                        
                        # 回调通知
                        if on_frame_saved:
                            try:
                                on_frame_saved(session_id, current_time, saved_frame_idx - 1)
                            except Exception as e:
                                logger.warning(f"[FrameCaptureService] Callback error: {e}")
                        
                        # 每秒记录一次进度
                        if saved_frame_idx % state.capture_fps == 0:
                            elapsed = current_time
                            logger.info(f"[FrameCaptureService] Session {session_id}: saved {saved_frame_idx} frames at {elapsed:.1f}s")

                        if (
                            is_realtime_stream
                            and state.retention_seconds > 0
                            and saved_frame_idx % max(1, state.prune_every_frames) == 0
                        ):
                            cutoff = max(0.0, current_time - state.retention_seconds)
                            removed = frame_storage.prune_frames_older_than(storage_path, cutoff)
                            if removed["frames"] or removed["preview"]:
                                logger.info(
                                    f"[FrameCaptureService] Pruned old frames for {session_id}: "
                                    f"frames={removed['frames']}, preview={removed['preview']}, cutoff={cutoff:.1f}s"
                                )
                
                except Exception as e:
                    logger.warning(f"[FrameCaptureService] Failed to save frame: {e}")
            
            # 每500帧记录一次统计
            if loop_count % 500 == 0 and loop_count > 0:
                now = time.time()
                elapsed_real = now - last_stats_time
                avg_read = (total_read_time / loop_count) * 1000
                avg_save = (total_save_time / saved_frame_idx) * 1000 if saved_frame_idx > 0 else 0
                actual_fps = 500 / elapsed_real if elapsed_real > 0 else 0
                logger.info(f"[FrameCaptureService] Stats: loop={loop_count}, saved={saved_frame_idx}, ratio={saved_frame_idx/loop_count*100:.1f}%, fps={actual_fps:.1f}")
                last_stats_time = now
            
            frame_idx += 1
            
            # 控制读取速率
            # 本地文件：按视频原始帧率读取，保持与视频同步
            # 实时流：尽可能快地读取
            if not is_realtime_stream:
                # 计算应该等待的时间，以匹配视频帧率
                loop_elapsed = time.time() - loop_start
                video_frame_interval = 1.0 / video_fps
                wait_time = video_frame_interval - loop_elapsed
                if wait_time > 0:
                    time.sleep(wait_time)
            else:
                # 实时流：最小延迟
                time.sleep(0.001)
    
    except Exception as e:
        logger.error(f"[FrameCaptureService] Error in capture thread: {e}")
    finally:
        state.is_running = False
        if cap:
            cap.release()
        
        # 保存最终的索引文件
        try:
            from .frame_storage_service import _frame_index_cache
            _frame_index_cache._save_index_to_file(storage_path, "frames")
            _frame_index_cache._save_index_to_file(storage_path, "preview")
        except Exception as e:
            logger.warning(f"[FrameCaptureService] Failed to save final index: {e}")        
        logger.info(f"[FrameCaptureService] Capture thread stopped for session {session_id}, saved {saved_frame_idx} frames")


class FrameCaptureService:
    """帧捕获服务管理器 - 使用独立线程而非 asyncio"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        self._config = load_frame_capture_config()
    
    async def start_capture(
        self,
        session_id: str,
        video_source: str,
        storage_path: str,
        is_realtime_stream: bool = True,
        stream_start_time: Optional[float] = None,
        on_frame_saved: Optional[Callable[[str, float, int], None]] = None
    ) -> bool:
        """
        启动帧捕获线程
        
        Args:
            session_id: 会话ID
            video_source: 视频源
            storage_path: 存储路径
            is_realtime_stream: 是否为实时流
            stream_start_time: 流开始时间（实时流必需）
            on_frame_saved: 帧保存回调
        
        Returns:
            是否成功启动
        """
        # 检查是否已有运行中的线程
        with _capture_lock:
            if session_id in _capture_threads:
                thread = _capture_threads[session_id]
                if thread.is_alive():
                    logger.warning(f"[FrameCaptureService] Capture already running for session {session_id}")
                    return False
        
        # 确定开始时间
        if stream_start_time is None:
            stream_start_time = time.time()
        
        # 创建并启动线程
        thread = threading.Thread(
            target=frame_capture_thread_func,
            args=(session_id, video_source, storage_path, is_realtime_stream, stream_start_time, on_frame_saved),
            daemon=True,  # 守护线程，主程序退出时自动终止
            name=f"FrameCapture-{session_id[:8]}"
        )
        
        with _capture_lock:
            _capture_threads[session_id] = thread
        
        thread.start()        
        logger.info(f"[FrameCaptureService] Started capture thread for session {session_id}")
        return True
    
    async def stop_capture(self, session_id: str) -> bool:
        """
        停止帧捕获线程
        
        Args:
            session_id: 会话ID
        
        Returns:
            是否成功停止
        """
        with _capture_lock:
            if session_id in _capture_states:
                _capture_states[session_id].should_stop = True
            
            if session_id in _capture_threads:
                thread = _capture_threads[session_id]
                if thread.is_alive():
                    # 等待线程结束（最多 2 秒）
                    thread.join(timeout=2.0)
                    if thread.is_alive():
                        logger.warning(f"[FrameCaptureService] Thread did not stop gracefully for session {session_id}")
                
                del _capture_threads[session_id]                
                logger.info(f"[FrameCaptureService] Stopped capture for session {session_id}")
                return True
        
        return False
    
    def get_capture_state(self, session_id: str) -> Optional[FrameCaptureState]:
        """获取捕获状态"""
        with _capture_lock:
            return _capture_states.get(session_id)
    
    def is_capturing(self, session_id: str) -> bool:
        """检查是否正在捕获"""
        with _capture_lock:
            if session_id in _capture_states:
                return _capture_states[session_id].is_running
            return False
    
    def get_capture_stats(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取捕获统计信息"""
        state = self.get_capture_state(session_id)
        if not state:
            return None
        
        return {
            "session_id": state.session_id,
            "is_running": state.is_running,
            "frames_captured": state.frames_captured,
            "frames_saved": state.frames_saved,
            "last_capture_time": state.last_capture_time,
            "capture_fps": state.capture_fps,
            "preview_fps": state.preview_fps,
            "storage_path": state.storage_path
        }


# 全局实例
_frame_capture_service: Optional[FrameCaptureService] = None


def get_frame_capture_service() -> FrameCaptureService:
    """获取全局帧捕获服务实例"""
    global _frame_capture_service
    if _frame_capture_service is None:
        _frame_capture_service = FrameCaptureService()
    return _frame_capture_service
