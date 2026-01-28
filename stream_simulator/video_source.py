"""
Video Source - Common video file reader for all stream types

Provides a unified interface for reading video frames from files
and simulating real-time playback.
"""

import cv2
import numpy as np
import time
import threading
import asyncio
from pathlib import Path
from typing import Optional, Callable, Tuple
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class VideoInfo:
    """Video file metadata"""
    path: str
    width: int
    height: int
    fps: float
    total_frames: int
    duration: float
    
    def __str__(self):
        return f"{Path(self.path).name} ({self.width}x{self.height} @ {self.fps:.1f}fps, {self.duration:.1f}s)"


class VideoSource:
    """
    Video file reader that simulates real-time playback.
    
    Can be used synchronously or asynchronously.
    """
    
    def __init__(
        self, 
        video_path: str, 
        loop: bool = False,  # Changed default to False - video should stop at end
        fps_override: Optional[float] = None,
        resize: Optional[Tuple[int, int]] = None
    ):
        self.video_path = video_path
        self.loop = loop
        self.fps_override = fps_override
        self.resize = resize
        
        # Open video
        self.cap = cv2.VideoCapture(video_path)
        if not self.cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")
        
        # Get properties
        self.original_fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps_override if fps_override else self.original_fps
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.duration = self.total_frames / self.original_fps if self.original_fps > 0 else 0
        
        # State
        self._frame_idx = 0
        self._start_time: Optional[float] = None
        self._running = False
        self._lock = threading.Lock()
        
        # Apply resize if specified
        if resize:
            self.output_width, self.output_height = resize
        else:
            self.output_width, self.output_height = self.width, self.height
        
        logger.info(f"VideoSource initialized: {self.info}")
    
    @property
    def info(self) -> VideoInfo:
        return VideoInfo(
            path=self.video_path,
            width=self.output_width,
            height=self.output_height,
            fps=self.fps,
            total_frames=self.total_frames,
            duration=self.duration
        )
    
    @property
    def frame_interval(self) -> float:
        """Time between frames in seconds"""
        return 1.0 / self.fps if self.fps > 0 else 0.04
    
    def read_frame(self) -> Optional[np.ndarray]:
        """
        Read the next frame (blocking, real-time paced).
        Returns BGR frame or None if video ended.
        """
        with self._lock:
            # Check if video source is still open
            if self.cap is None or not self.cap.isOpened():
                return None
            
            ret, frame = self.cap.read()
            
            if not ret:
                if self.loop:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    self._frame_idx = 0
                    ret, frame = self.cap.read()
                    if not ret:
                        return None
                    logger.debug("Video looped")
                else:
                    logger.info("Video reached end, stopping playback")
                    return None
            
            self._frame_idx += 1
            
            # Resize if needed
            if self.resize and frame is not None:
                frame = cv2.resize(frame, self.resize)
            
            return frame
    
    async def read_frame_async(self) -> Optional[np.ndarray]:
        """Async version of read_frame"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.read_frame)
    
    def read_frame_realtime(self) -> Optional[np.ndarray]:
        """
        Read frame at real-time pace (waits for correct timing).
        """
        if self._start_time is None:
            self._start_time = time.time()
        
        # Calculate expected time for this frame
        expected_time = self._start_time + (self._frame_idx * self.frame_interval)
        current_time = time.time()
        
        # Wait if ahead of schedule
        wait_time = expected_time - current_time
        if wait_time > 0:
            time.sleep(wait_time)
        
        return self.read_frame()
    
    async def read_frame_realtime_async(self) -> Optional[np.ndarray]:
        """Async version with real-time pacing"""
        if self._start_time is None:
            self._start_time = time.time()
        
        expected_time = self._start_time + (self._frame_idx * self.frame_interval)
        current_time = time.time()
        
        wait_time = expected_time - current_time
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        
        return await self.read_frame_async()
    
    def seek(self, timestamp: float):
        """Seek to timestamp (seconds)"""
        with self._lock:
            if self.cap is None or not self.cap.isOpened():
                return
            frame_num = int(timestamp * self.original_fps)
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            self._frame_idx = frame_num
            self._start_time = time.time() - timestamp
    
    def reset(self):
        """Reset to beginning"""
        with self._lock:
            if self.cap is None or not self.cap.isOpened():
                return
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self._frame_idx = 0
            self._start_time = None
    
    def close(self):
        """Release video capture"""
        with self._lock:
            if self.cap:
                self.cap.release()
                self.cap = None
                logger.info("VideoSource closed")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    # Frame generator for streaming
    def frames(self, realtime: bool = True):
        """Generator yielding frames"""
        self._running = True
        self.reset()
        
        while self._running:
            if realtime:
                frame = self.read_frame_realtime()
            else:
                frame = self.read_frame()
            
            if frame is None:
                break
            
            yield frame
    
    async def frames_async(self, realtime: bool = True):
        """Async generator yielding frames"""
        self._running = True
        self.reset()
        
        while self._running:
            if realtime:
                frame = await self.read_frame_realtime_async()
            else:
                frame = await self.read_frame_async()
            
            if frame is None:
                break
            
            yield frame
    
    def stop(self):
        """Stop frame generation"""
        self._running = False


def encode_frame_jpeg(frame: np.ndarray, quality: int = 80) -> bytes:
    """Encode BGR frame to JPEG bytes"""
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    _, buffer = cv2.imencode('.jpg', frame, encode_param)
    return buffer.tobytes()


def encode_frame_png(frame: np.ndarray) -> bytes:
    """Encode BGR frame to PNG bytes"""
    _, buffer = cv2.imencode('.png', frame)
    return buffer.tobytes()





