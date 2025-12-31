"""
Frame Storage Service
Manages saving and retrieving video frames for each session.
"""
import os
import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Base directory for storing session frames
SESSIONS_STORAGE_BASE = Path("/data2/jj/proj/video_processor/video_stream_app/sessions")


class FrameStorageService:
    """Service for storing and retrieving video frames"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        # Ensure base directory exists
        SESSIONS_STORAGE_BASE.mkdir(parents=True, exist_ok=True)
        logger.info(f"[FrameStorage] Base directory: {SESSIONS_STORAGE_BASE}")
    
    def create_session_folder(self, session_id: str, video_name: str = None) -> str:
        """
        Create a folder for a session with timestamp.
        Returns the folder path.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Clean video name for folder name
        if video_name:
            clean_name = "".join(c for c in video_name if c.isalnum() or c in "._-")
            clean_name = clean_name[:50]  # Limit length
            folder_name = f"{timestamp}_{session_id}_{clean_name}"
        else:
            folder_name = f"{timestamp}_{session_id}"
        
        folder_path = SESSIONS_STORAGE_BASE / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        (folder_path / "frames").mkdir(exist_ok=True)
        (folder_path / "analyzed").mkdir(exist_ok=True)
        
        logger.info(f"[FrameStorage] Created session folder: {folder_path}")
        return str(folder_path)
    
    def save_frame(
        self,
        storage_path: str,
        timestamp: float,
        frame_data: np.ndarray = None,
        frame_base64: str = None,
        frame_idx: int = None,
        subfolder: str = "frames"
    ) -> Optional[str]:
        """
        Save a frame to the session folder.
        
        Args:
            storage_path: Session storage folder path
            timestamp: Video timestamp in seconds
            frame_data: numpy array of the frame (BGR)
            frame_base64: Base64 encoded frame (alternative to frame_data)
            frame_idx: Optional frame index
            subfolder: "frames" for original, "analyzed" for analyzed frames
        
        Returns:
            Relative path to the saved frame (relative to storage_path)
        """
        if not storage_path:
            logger.warning("[FrameStorage] No storage path provided")
            return None
        
        folder = Path(storage_path) / subfolder
        folder.mkdir(parents=True, exist_ok=True)
        
        # Generate filename
        ts_str = f"{timestamp:.2f}".replace(".", "_")
        if frame_idx is not None:
            filename = f"frame_{frame_idx:06d}_ts{ts_str}.jpg"
        else:
            filename = f"ts{ts_str}.jpg"
        
        filepath = folder / filename
        
        try:
            if frame_data is not None:
                # Save from numpy array
                cv2.imwrite(str(filepath), frame_data)
            elif frame_base64:
                # Decode and save from base64
                img_data = base64.b64decode(frame_base64)
                nparr = np.frombuffer(img_data, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None:
                    cv2.imwrite(str(filepath), img)
                else:
                    logger.error("[FrameStorage] Failed to decode base64 image")
                    return None
            else:
                logger.warning("[FrameStorage] No frame data provided")
                return None
            
            # Return relative path
            relative_path = f"{subfolder}/{filename}"
            logger.debug(f"[FrameStorage] Saved frame: {relative_path}")
            return relative_path
            
        except Exception as e:
            logger.error(f"[FrameStorage] Failed to save frame: {e}")
            return None
    
    def get_frame(
        self,
        storage_path: str,
        relative_path: str
    ) -> Optional[str]:
        """
        Get a frame as base64 string.
        
        Args:
            storage_path: Session storage folder path
            relative_path: Relative path to the frame
        
        Returns:
            Base64 encoded frame or None
        """
        if not storage_path or not relative_path:
            return None
        
        filepath = Path(storage_path) / relative_path
        
        if not filepath.exists():
            logger.warning(f"[FrameStorage] Frame not found: {filepath}")
            return None
        
        try:
            with open(filepath, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"[FrameStorage] Failed to read frame: {e}")
            return None
    
    def get_frame_path(
        self,
        storage_path: str,
        relative_path: str
    ) -> Optional[str]:
        """
        Get the absolute path to a frame file.
        
        Args:
            storage_path: Session storage folder path
            relative_path: Relative path to the frame
        
        Returns:
            Absolute path or None
        """
        if not storage_path or not relative_path:
            return None
        
        filepath = Path(storage_path) / relative_path
        
        if filepath.exists():
            return str(filepath)
        return None
    
    def find_nearest_frame(
        self,
        storage_path: str,
        timestamp: float,
        subfolder: str = "frames"
    ) -> Optional[Dict[str, Any]]:
        """
        Find the frame nearest to the given timestamp.
        
        Args:
            storage_path: Session storage folder path
            timestamp: Target timestamp in seconds
            subfolder: Folder to search in
        
        Returns:
            Dict with frame info or None
        """
        if not storage_path:
            return None
        
        folder = Path(storage_path) / subfolder
        if not folder.exists():
            return None
        
        frames = list(folder.glob("*.jpg"))
        if not frames:
            return None
        
        # Parse timestamps from filenames
        best_frame = None
        best_diff = float("inf")
        
        for frame_path in frames:
            # Try to extract timestamp from filename
            name = frame_path.stem
            try:
                if "_ts" in name:
                    ts_part = name.split("_ts")[1]
                    ts_value = float(ts_part.replace("_", "."))
                else:
                    continue
                
                diff = abs(ts_value - timestamp)
                if diff < best_diff:
                    best_diff = diff
                    best_frame = frame_path
            except (ValueError, IndexError):
                continue
        
        if best_frame:
            relative_path = f"{subfolder}/{best_frame.name}"
            return {
                "path": relative_path,
                "absolute_path": str(best_frame),
                "timestamp_diff": best_diff
            }
        
        return None
    
    def list_frames(
        self,
        storage_path: str,
        subfolder: str = "frames"
    ) -> list:
        """List all frames in a session folder"""
        if not storage_path:
            return []
        
        folder = Path(storage_path) / subfolder
        if not folder.exists():
            return []
        
        frames = []
        for frame_path in sorted(folder.glob("*.jpg")):
            frames.append({
                "filename": frame_path.name,
                "path": f"{subfolder}/{frame_path.name}",
                "size": frame_path.stat().st_size
            })
        
        return frames
    
    def list_frames_in_range(
        self,
        storage_path: str,
        start_time: float,
        end_time: float,
        subfolder: str = "frames"
    ) -> list:
        """
        List frames within a time range.
        
        Frame filenames are expected to be in format: frame_XXXXX_TIMESTAMP.jpg
        where TIMESTAMP is the time in seconds (float format with underscore for decimal).
        """
        if not storage_path:
            return []
        
        folder = Path(storage_path) / subfolder
        if not folder.exists():
            return []
        
        frames = []
        for frame_path in sorted(folder.glob("*.jpg")):
            # Try to extract timestamp from filename
            # Expected format: frame_00001_12.500.jpg or frame_00001_12_500.jpg
            filename = frame_path.stem  # e.g., "frame_00001_12.500" or "frame_00001_12_500"
            parts = filename.split("_")
            
            timestamp = None
            if len(parts) >= 3:
                try:
                    # Try format: frame_XXXXX_TIMESTAMP (e.g., frame_00001_12.500)
                    timestamp = float(parts[2])
                except ValueError:
                    # Try format with "ts" prefix: frame_XXXXX_ts12_500 -> 12.500
                    ts_part = parts[2]
                    if ts_part.startswith("ts"):
                        ts_part = ts_part[2:]  # Remove "ts" prefix
                    
                    if len(parts) >= 4:
                        try:
                            # Format: frame_XXXXX_ts12_500 or frame_XXXXX_12_500 -> 12.500
                            timestamp = float(f"{ts_part}.{parts[3]}")
                        except ValueError:
                            pass
                    else:
                        try:
                            timestamp = float(ts_part)
                        except ValueError:
                            pass
            
            # If couldn't parse from filename, try frame index * estimated fps
            if timestamp is None and len(parts) >= 2:
                try:
                    frame_idx = int(parts[1])
                    # Assume ~1 fps for SurgR1 processed frames
                    timestamp = float(frame_idx)
                except ValueError:
                    continue
            
            if timestamp is not None and start_time <= timestamp <= end_time:
                frames.append({
                    "filename": frame_path.name,
                    "path": f"{subfolder}/{frame_path.name}",
                    "timestamp": timestamp,
                    "frame_idx": int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else -1
                })
        
        return sorted(frames, key=lambda x: x.get("timestamp", 0))


# Global instance
_frame_storage_service: Optional[FrameStorageService] = None


def get_frame_storage_service() -> FrameStorageService:
    """Get the global frame storage service instance"""
    global _frame_storage_service
    if _frame_storage_service is None:
        _frame_storage_service = FrameStorageService()
    return _frame_storage_service

