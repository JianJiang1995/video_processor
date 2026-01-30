"""
Video Stream Processor
Processes video in 5-second windows with frame analysis and GPT summarization
"""
import cv2
import time
import asyncio
import base64
import json
from io import BytesIO
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Callable, AsyncGenerator
from pathlib import Path
from PIL import Image
from enum import Enum
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ProcessingState(str, Enum):
    """Video processing states"""
    IDLE = "idle"
    LOADING = "loading"
    PLAYING = "playing"
    PAUSED = "paused"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class FrameData:
    """Single frame data"""
    frame_idx: int
    timestamp: float
    image: Image.Image
    
    def to_base64(self, format: str = "JPEG", quality: int = 85) -> str:
        """Convert image to base64 string"""
        buffer = BytesIO()
        self.image.save(buffer, format=format, quality=quality)
        return base64.b64encode(buffer.getvalue()).decode()


@dataclass
class WindowData:
    """5-second window data"""
    window_id: int
    start_time: float
    end_time: float
    frames: List[FrameData] = field(default_factory=list)
    
    @property
    def frame_count(self) -> int:
        return len(self.frames)
    
    def get_images(self) -> List[Image.Image]:
        """Get all frame images"""
        return [f.image for f in self.frames]
    
    def get_base64_images(self) -> List[str]:
        """Get all frames as base64"""
        return [f.to_base64() for f in self.frames]


class VideoProcessor:
    """
    Video Stream Processor
    
    Processes video in 5-second windows, extracting frames
    and providing them for analysis and summarization.
    """
    
    def __init__(
        self,
        video_path: str,
        window_duration: float = 5.0,
        sample_interval: float = 1.0,
        start_time: float = 0.0,
        end_time: Optional[float] = None
    ):
        """
        Initialize video processor
        
        Args:
            video_path: Path to video file or HTTP stream URL
            window_duration: Duration of each processing window (seconds)
            sample_interval: Frame sampling interval (seconds)
            start_time: Start time for processing
            end_time: End time for processing (None = full video)
        """
        # Check if this is an HTTP stream URL
        self.is_stream = video_path.startswith('http://') or video_path.startswith('https://')
        
        if self.is_stream:
            self.video_path = video_path  # Keep as string for HTTP URLs
            self._video_path_str = video_path
        else:
            self.video_path = Path(video_path)
            self._video_path_str = str(self.video_path)
            if not self.video_path.exists():
                raise FileNotFoundError(f"Video not found: {video_path}")
        
        self.window_duration = window_duration
        self.sample_interval = sample_interval
        self.start_time = start_time
        self.end_time = end_time
        
        # Video metadata
        self._load_video_metadata()
        
        # Processing state
        self.state = ProcessingState.IDLE
        self.current_position = 0.0
        self.is_paused = False
        self._stop_requested = False
        
        # Callbacks
        self.on_frame_processed: Optional[Callable] = None
        self.on_window_complete: Optional[Callable] = None
        self.on_state_change: Optional[Callable] = None
        
    def _load_video_metadata(self):
        """Load video metadata"""
        cap = cv2.VideoCapture(self._video_path_str)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {self._video_path_str}")
        
        self.fps = cap.get(cv2.CAP_PROP_FPS)
        self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # For HTTP streams, total_frames may be 0 or unreliable
        if self.is_stream or self.fps <= 0:
            # Default to 30fps for streams if not detected
            if self.fps <= 0:
                self.fps = 30.0
            # For streams, set a reasonable default duration
            # This will be updated as frames are processed
            self.duration = 3600.0  # 1 hour default for streams
            self.total_frames = int(self.duration * self.fps)
        else:
            self.duration = self.total_frames / self.fps
        
        if self.end_time is None:
            self.end_time = self.duration
        
        cap.release()
        
        video_name = self._video_path_str if self.is_stream else self.video_path.name
        logger.info(f"Video loaded: {video_name}")
        logger.info(f"  Duration: {self.duration:.2f}s, FPS: {self.fps:.2f}, Stream: {self.is_stream}")
        logger.info(f"  Resolution: {self.width}x{self.height}")
        
    def get_metadata(self) -> Dict[str, Any]:
        """Get video metadata"""
        return {
            "path": self._video_path_str,
            "name": self._video_path_str if self.is_stream else self.video_path.name,
            "duration": self.duration,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "total_frames": self.total_frames,
            "window_duration": self.window_duration,
            "sample_interval": self.sample_interval,
            "is_stream": self.is_stream
        }
    
    def _set_state(self, state: ProcessingState):
        """Update processing state"""
        old_state = self.state
        self.state = state
        if self.on_state_change:
            self.on_state_change(old_state, state)
        logger.info(f"State changed: {old_state} -> {state}")
    
    def pause(self):
        """Pause processing"""
        self.is_paused = True
        self._set_state(ProcessingState.PAUSED)
    
    def resume(self):
        """Resume processing"""
        self.is_paused = False
        self._set_state(ProcessingState.PLAYING)
    
    def stop(self):
        """Stop processing"""
        self._stop_requested = True
        self._set_state(ProcessingState.IDLE)
    
    def seek(self, timestamp: float):
        """Seek to timestamp"""
        self.current_position = max(0, min(timestamp, self.duration))
        logger.info(f"Seek to: {self.current_position:.2f}s")
    
    def extract_frame(self, timestamp: float) -> Optional[FrameData]:
        """Extract a single frame at timestamp"""
        cap = cv2.VideoCapture(self._video_path_str)
        
        frame_idx = int(timestamp * self.fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        
        ret, frame = cap.read()
        cap.release()
        
        if not ret:
            return None
        
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb_frame)
        
        return FrameData(
            frame_idx=frame_idx,
            timestamp=timestamp,
            image=pil_image
        )
    
    def extract_window(self, start_time: float) -> WindowData:
        """Extract frames for a 5-second window"""
        cap = cv2.VideoCapture(self._video_path_str)
        
        end_time = min(start_time + self.window_duration, self.duration)
        window_id = int(start_time / self.window_duration)
        
        window = WindowData(
            window_id=window_id,
            start_time=start_time,
            end_time=end_time
        )
        
        current_time = start_time
        while current_time < end_time:
            frame_idx = int(current_time * self.fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            
            ret, frame = cap.read()
            if not ret:
                break
            
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(rgb_frame)
            
            frame_data = FrameData(
                frame_idx=frame_idx,
                timestamp=current_time,
                image=pil_image
            )
            window.frames.append(frame_data)
            
            current_time += self.sample_interval
        
        cap.release()
        logger.info(f"Extracted window {window_id}: {start_time:.2f}s - {end_time:.2f}s ({window.frame_count} frames)")
        
        return window
    
    async def process_stream(self) -> AsyncGenerator[WindowData, None]:
        """
        Process video as a stream of windows
        
        Yields:
            WindowData for each 5-second window
        """
        self._set_state(ProcessingState.PLAYING)
        self._stop_requested = False
        self.current_position = self.start_time
        
        window_id = 0
        
        while self.current_position < self.end_time and not self._stop_requested:
            # Handle pause
            while self.is_paused and not self._stop_requested:
                await asyncio.sleep(0.1)
            
            if self._stop_requested:
                break
            
            # Extract and yield window
            window = self.extract_window(self.current_position)
            
            if self.on_window_complete:
                self.on_window_complete(window)
            
            yield window
            
            # Move to next window
            self.current_position += self.window_duration
            window_id += 1
            
            # Small delay for async processing
            await asyncio.sleep(0.01)
        
        self._set_state(ProcessingState.COMPLETED)
    
    def get_all_windows(self) -> List[WindowData]:
        """Get all windows without streaming (for batch processing)"""
        windows = []
        current_time = self.start_time
        window_id = 0
        
        while current_time < self.end_time:
            window = self.extract_window(current_time)
            windows.append(window)
            current_time += self.window_duration
            window_id += 1
        
        return windows


# Utility function to create frame context for GPT
def build_frame_context(window: WindowData, frame_analyses: List[Dict] = None) -> str:
    """
    Build context string for GPT summarization
    
    Args:
        window: WindowData containing frames
        frame_analyses: Optional list of per-frame analysis results
    """
    context = f"## Video Segment Information\n"
    context += f"- Window ID: {window.window_id}\n"
    context += f"- Time Range: {window.start_time:.2f}s to {window.end_time:.2f}s\n"
    context += f"- Duration: {window.end_time - window.start_time:.2f}s\n"
    context += f"- Total Frames: {window.frame_count}\n\n"
    
    if frame_analyses:
        context += "## Frame-by-Frame Annotations\n\n"
        for i, analysis in enumerate(frame_analyses):
            context += f"### Frame {i+1} (t={window.frames[i].timestamp:.2f}s)\n"
            if analysis.get('surgical_phase'):
                context += f"**Phase:** {analysis['surgical_phase']}\n"
            if analysis.get('surgical_action'):
                context += f"**Action:** {analysis['surgical_action']}\n"
            if analysis.get('tool_localization'):
                context += f"**Tools:** {analysis['tool_localization'][:200]}...\n"
            context += "\n"
    
    return context




