"""
Frame Storage Service
Manages saving and retrieving video frames for each session.

OPTIMIZATION: Uses a JSON index file for O(1) frame lookup instead of directory scanning.
Each session folder has a frames_index.json that is updated when frames are saved.
"""
import os
import json
import base64
import bisect
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Base directory for storing session frames
SESSIONS_STORAGE_BASE = Path("/data2/jj/proj/video_processor/video_stream_app/sessions")

# Index file name
FRAMES_INDEX_FILE = "frames_index.json"

# Preview frame settings for fast loop playback
PREVIEW_WIDTH = 640  # Preview frame width (height scales proportionally)
PREVIEW_QUALITY = 40  # JPEG quality for preview frames (lower = smaller file)


class FrameIndexCache:
    """
    In-memory cache for frame index to speed up lookups.
    Maintains a sorted list of (timestamp, filename) tuples for each storage path.
    """
    
    def __init__(self):
        # Key: (storage_path, subfolder), Value: sorted list of (timestamp, filename)
        self._cache: Dict[Tuple[str, str], List[Tuple[float, str]]] = {}
        self._lock = threading.RLock()
    
    def get_index(self, storage_path: str, subfolder: str) -> List[Tuple[float, str]]:
        """Get cached frame index, building it if not exists"""
        key = (storage_path, subfolder)
        
        with self._lock:
            if key not in self._cache:
                self._build_index(storage_path, subfolder)
            return self._cache.get(key, [])
    
    def add_frame(self, storage_path: str, subfolder: str, timestamp: float, filename: str):
        """Add a new frame to the cache (maintains sorted order) and periodically save to file"""
        key = (storage_path, subfolder)
        
        with self._lock:
            if key not in self._cache:
                self._cache[key] = []
            
            # Use bisect to insert in sorted order
            index = self._cache[key]
            # Find insertion point based on timestamp
            insert_pos = bisect.bisect_left([t for t, _ in index], timestamp)
            index.insert(insert_pos, (timestamp, filename))
            
            # Save index to file every 50 frames (5 seconds at 10fps) for durability
            if len(index) % 50 == 0:
                self._save_index_to_file(storage_path, subfolder)
    
    def invalidate(self, storage_path: str, subfolder: str = None):
        """Invalidate cache for a storage path"""
        with self._lock:
            if subfolder:
                key = (storage_path, subfolder)
                self._cache.pop(key, None)
            else:
                # Invalidate all subfolders for this storage path
                keys_to_remove = [k for k in self._cache if k[0] == storage_path]
                for k in keys_to_remove:
                    del self._cache[k]
    
    def _build_index(self, storage_path: str, subfolder: str):
        """
        Build index - first try loading from JSON index file (fast),
        fall back to directory scan if file doesn't exist.
        """
        key = (storage_path, subfolder)
        folder = Path(storage_path) / subfolder
        
        if not folder.exists():
            self._cache[key] = []
            return
        
        # Try loading from JSON index file first (O(1) - very fast)
        index_file = Path(storage_path) / f"{subfolder}_{FRAMES_INDEX_FILE}"
        if index_file.exists():
            try:
                with open(index_file, "r") as f:
                    data = json.load(f)
                    frames = [(item["timestamp"], item["filename"]) for item in data]
                    frames.sort(key=lambda x: x[0])
                    self._cache[key] = frames
                    logger.debug(f"[FrameIndexCache] Loaded index from file for {key}: {len(frames)} frames")
                    return
            except Exception as e:
                logger.warning(f"[FrameIndexCache] Failed to load index file: {e}, falling back to scan")
        
        # Fallback: scan directory (slow, but only happens once if index file missing)
        frames = []
        for frame_path in folder.glob("*.jpg"):
            timestamp = self._parse_timestamp(frame_path.stem)
            if timestamp is not None:
                frames.append((timestamp, frame_path.name))
        
        # Sort by timestamp
        frames.sort(key=lambda x: x[0])
        self._cache[key] = frames
        logger.debug(f"[FrameIndexCache] Built index by scanning for {key}: {len(frames)} frames")
        
        # Save to index file for next time
        self._save_index_to_file(storage_path, subfolder)
    
    def _save_index_to_file(self, storage_path: str, subfolder: str):
        """Save current index to JSON file for fast loading"""
        key = (storage_path, subfolder)
        if key not in self._cache:
            return
        
        index_file = Path(storage_path) / f"{subfolder}_{FRAMES_INDEX_FILE}"
        try:
            data = [{"timestamp": ts, "filename": fn} for ts, fn in self._cache[key]]
            with open(index_file, "w") as f:
                json.dump(data, f)
            logger.debug(f"[FrameIndexCache] Saved index to file: {index_file}")
        except Exception as e:
            logger.warning(f"[FrameIndexCache] Failed to save index file: {e}")
    
    def _parse_timestamp(self, filename_stem: str) -> Optional[float]:
        """Parse timestamp from filename stem"""
        parts = filename_stem.split("_")
        
        if len(parts) >= 3:
            try:
                return float(parts[2])
            except ValueError:
                ts_part = parts[2]
                if ts_part.startswith("ts"):
                    ts_part = ts_part[2:]
                
                if len(parts) >= 4:
                    try:
                        return float(f"{ts_part}.{parts[3]}")
                    except ValueError:
                        pass
                else:
                    try:
                        return float(ts_part)
                    except ValueError:
                        pass
        
        # Fallback: try frame index
        if len(parts) >= 2:
            try:
                return float(int(parts[1]))
            except ValueError:
                pass
        
        return None
    
    def find_nearest(self, storage_path: str, subfolder: str, target_time: float) -> Optional[Tuple[float, str, float]]:
        """
        Find frame nearest to target time using binary search.
        Returns (timestamp, filename, time_diff) or None.
        """
        index = self.get_index(storage_path, subfolder)
        if not index:
            return None
        
        # Binary search for closest timestamp
        timestamps = [t for t, _ in index]
        pos = bisect.bisect_left(timestamps, target_time)
        
        # Check both pos-1 and pos for closest
        candidates = []
        if pos > 0:
            candidates.append(pos - 1)
        if pos < len(index):
            candidates.append(pos)
        
        if not candidates:
            return None
        
        # Find the one with smallest difference
        best_idx = min(candidates, key=lambda i: abs(timestamps[i] - target_time))
        ts, filename = index[best_idx]
        return (ts, filename, abs(ts - target_time))
    
    def get_frames_in_range(self, storage_path: str, subfolder: str, start: float, end: float) -> List[Tuple[float, str]]:
        """Get all frames in a time range using binary search"""
        index = self.get_index(storage_path, subfolder)
        if not index:
            return []
        
        timestamps = [t for t, _ in index]
        
        # Find range using binary search
        start_pos = bisect.bisect_left(timestamps, start)
        end_pos = bisect.bisect_right(timestamps, end)
        
        return index[start_pos:end_pos]


# Global frame index cache
_frame_index_cache = FrameIndexCache()


class FrameStorageService:
    """Service for storing and retrieving video frames"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        # Only initialize once
        if FrameStorageService._initialized:
            return
        FrameStorageService._initialized = True
        
        # Ensure base directory exists
        SESSIONS_STORAGE_BASE.mkdir(parents=True, exist_ok=True)
        logger.info(f"[FrameStorage] Base directory: {SESSIONS_STORAGE_BASE}")
        
        # Reference to the global cache
        self._cache = _frame_index_cache
    
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
        (folder_path / "preview").mkdir(exist_ok=True)  # Low-quality preview for fast playback
        
        logger.info(f"[FrameStorage] Created session folder: {folder_path}")
        return str(folder_path)
    
    def save_frame(
        self,
        storage_path: str,
        timestamp: float,
        frame_data: np.ndarray = None,
        frame_base64: str = None,
        frame_idx: int = None,
        subfolder: str = "frames",
        save_preview: bool = True
    ) -> Optional[str]:
        """
        Save a frame to the session folder.
        Also saves a low-quality preview version for fast loop playback.
        
        Args:
            storage_path: Session storage folder path
            timestamp: Video timestamp in seconds
            frame_data: numpy array of the frame (BGR)
            frame_base64: Base64 encoded frame (alternative to frame_data)
            frame_idx: Optional frame index
            subfolder: "frames" for original, "analyzed" for analyzed frames
            save_preview: If True, also save a low-quality preview version
        
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
            # Get the image as numpy array
            img = None
            if frame_data is not None:
                img = frame_data
            elif frame_base64:
                # Decode from base64
                img_data = base64.b64decode(frame_base64)
                nparr = np.frombuffer(img_data, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is None:
                    logger.error("[FrameStorage] Failed to decode base64 image")
                    return None
            else:
                logger.warning("[FrameStorage] No frame data provided")
                return None
            
            # Save original frame
            cv2.imwrite(str(filepath), img)
            
            # Update cache with new frame
            self._cache.add_frame(storage_path, subfolder, timestamp, filename)
            
            # Save preview version for fast loop playback
            if save_preview and subfolder == "frames":
                self._save_preview_frame(storage_path, timestamp, img, filename)
            
            # Return relative path
            relative_path = f"{subfolder}/{filename}"
            logger.debug(f"[FrameStorage] Saved frame: {relative_path}")
            
            return relative_path
            
        except Exception as e:
            logger.error(f"[FrameStorage] Failed to save frame: {e}")
            return None
    
    def _save_preview_frame(
        self,
        storage_path: str,
        timestamp: float,
        img: np.ndarray,
        filename: str
    ) -> Optional[str]:
        """
        Save a low-quality preview version of the frame for fast loop playback.
        
        Preview frames are:
        - Resized to PREVIEW_WIDTH (maintaining aspect ratio)
        - Saved with lower JPEG quality (PREVIEW_QUALITY)
        
        This reduces file size by ~90% (from ~600KB to ~40KB)
        """
        try:
            preview_folder = Path(storage_path) / "preview"
            preview_folder.mkdir(parents=True, exist_ok=True)
            
            # Resize to preview width maintaining aspect ratio
            h, w = img.shape[:2]
            if w > PREVIEW_WIDTH:
                scale = PREVIEW_WIDTH / w
                new_w = PREVIEW_WIDTH
                new_h = int(h * scale)
                preview_img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
            else:
                preview_img = img
            
            # Save with lower quality
            preview_path = preview_folder / filename
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_QUALITY]
            cv2.imwrite(str(preview_path), preview_img, encode_params)
            
            # Update preview cache
            self._cache.add_frame(storage_path, "preview", timestamp, filename)
            
            return f"preview/{filename}"
            
        except Exception as e:
            logger.warning(f"[FrameStorage] Failed to save preview frame: {e}")
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
        Uses cached index with binary search for O(log n) performance.
        
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
        
        # Use cached index with binary search
        result = self._cache.find_nearest(storage_path, subfolder, timestamp)
        
        if result:
            ts, filename, diff = result
            frame_path = folder / filename
            return {
                "path": f"{subfolder}/{filename}",
                "absolute_path": str(frame_path),
                "timestamp_diff": diff
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
        Uses cached index with binary search for O(log n) range queries.
        
        Frame filenames are expected to be in format: frame_XXXXX_TIMESTAMP.jpg
        where TIMESTAMP is the time in seconds (float format with underscore for decimal).
        """
        if not storage_path:
            return []
        
        folder = Path(storage_path) / subfolder
        if not folder.exists():
            return []
        
        # Use cached index with binary search for efficient range query
        cached_frames = self._cache.get_frames_in_range(storage_path, subfolder, start_time, end_time)
        
        if cached_frames:
            frames = []
            for timestamp, filename in cached_frames:
                # Parse frame_idx from filename
                parts = filename.split("_")
                frame_idx = -1
                if len(parts) >= 2 and parts[1].replace(".jpg", "").isdigit():
                    try:
                        frame_idx = int(parts[1])
                    except ValueError:
                        pass
                
                frames.append({
                    "filename": filename,
                    "path": f"{subfolder}/{filename}",
                    "timestamp": timestamp,
                    "frame_idx": frame_idx
                })
            return frames
        
        # Fallback to old method if cache is empty (shouldn't happen normally)
        logger.debug(f"[FrameStorage] Cache miss for {storage_path}/{subfolder}, falling back to directory scan")
        frames = []
        for frame_path in sorted(folder.glob("*.jpg")):
            filename = frame_path.stem
            parts = filename.split("_")
            
            timestamp = None
            if len(parts) >= 3:
                try:
                    timestamp = float(parts[2])
                except ValueError:
                    ts_part = parts[2]
                    if ts_part.startswith("ts"):
                        ts_part = ts_part[2:]
                    
                    if len(parts) >= 4:
                        try:
                            timestamp = float(f"{ts_part}.{parts[3]}")
                        except ValueError:
                            pass
                    else:
                        try:
                            timestamp = float(ts_part)
                        except ValueError:
                            pass
            
            if timestamp is None and len(parts) >= 2:
                try:
                    frame_idx = int(parts[1])
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

