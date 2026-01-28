"""
Video Export Service
Generates video clips with embedded analysis text on the right side.

Supports two modes:
1. Local video: Extract clips directly from source video using FFmpeg
2. Stream/frames: Combine saved frame images into video using PIL + FFmpeg
"""
import asyncio
import subprocess
import tempfile
import uuid
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# Export storage directory
EXPORTS_BASE = Path("/data2/jj/proj/video_processor/output")

# Text panel settings
TEXT_PANEL_WIDTH = 600  # Width of text panel in pixels
TEXT_FONT_SIZE = 24
TEXT_LINE_SPACING = 8
TEXT_WRAP_WIDTH = 28  # Characters per line for Chinese text

# Global executor for background tasks
_executor = ThreadPoolExecutor(max_workers=2)

# Export task status storage
# Key: task_id, Value: dict with status, progress, results, etc.
export_tasks: Dict[str, Dict[str, Any]] = {}


def get_chinese_font_path() -> str:
    """Find a Chinese-compatible font on the system."""
    font_paths = [
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/arphic/uming.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/System/Library/Fonts/PingFang.ttc',  # macOS
    ]
    
    for font_path in font_paths:
        if Path(font_path).exists():
            return font_path
    
    # Fallback
    return '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def wrap_text_for_display(text: str, width: int = TEXT_WRAP_WIDTH) -> str:
    """
    Wrap text for display. For Chinese text, wraps by character count.
    Returns newline-separated string.
    """
    if not text:
        return ""
    
    # Remove extra whitespace
    text = ' '.join(text.split())
    
    lines = []
    current_line = ""
    char_count = 0
    
    for char in text:
        current_line += char
        char_count += 1
        
        # Wrap at punctuation or width limit
        if char in '。！？；' or char_count >= width:
            lines.append(current_line)
            current_line = ""
            char_count = 0
        elif char in '，、' and char_count >= width * 0.7:
            lines.append(current_line)
            current_line = ""
            char_count = 0
    
    if current_line:
        lines.append(current_line)
    
    # Limit to reasonable number of lines
    return '\n'.join(lines[:40])


def create_text_image_pil(
    text: str,
    width: int = TEXT_PANEL_WIDTH,
    height: int = 1080
) -> Optional[Path]:
    """
    Create an image with text using PIL/Pillow (supports Chinese).
    
    Args:
        text: Text to display
        width: Image width
        height: Image height
        
    Returns:
        Path to created image file or None
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        
        # Create white background
        img = Image.new('RGB', (width, height), 'white')
        draw = ImageDraw.Draw(img)
        
        # Try to use a Chinese-compatible font
        font_size = TEXT_FONT_SIZE
        font_path = get_chinese_font_path()
        
        font = None
        if Path(font_path).exists():
            try:
                font = ImageFont.truetype(font_path, font_size)
            except Exception:
                pass
        
        if font is None:
            font = ImageFont.load_default()
        
        # Wrap and draw text
        wrapped_text = wrap_text_for_display(text, width=TEXT_WRAP_WIDTH)
        
        # Draw text
        y_position = 30
        line_height = font_size + TEXT_LINE_SPACING
        for line in wrapped_text.split('\n'):
            draw.text((20, y_position), line, fill='black', font=font)
            y_position += line_height
            
            if y_position > height - 50:
                break  # Don't exceed image height
        
        # Save to temporary file
        img_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        img.save(img_file.name, 'PNG')
        img_file.close()
        
        return Path(img_file.name)
        
    except ImportError:
        logger.error("PIL/Pillow not installed. Install with: pip install Pillow")
        return None
    except Exception as e:
        logger.error(f"Error creating text image: {e}")
        return None


def extract_clip_from_local_video(
    source_video: str,
    start_time: float,
    end_time: float,
    text: str,
    output_path: Path
) -> bool:
    """
    Extract clip from local video file and add text panel on the right.
    
    Uses FFmpeg with textfile method for proper Chinese character support.
    
    Args:
        source_video: Path to source video file
        start_time: Start time in seconds
        end_time: End time in seconds
        text: Analysis text to embed
        output_path: Output video path
        
    Returns:
        True if successful
    """
    try:
        duration = end_time - start_time
        
        # Wrap text
        text_wrapped = wrap_text_for_display(text, width=TEXT_WRAP_WIDTH)
        
        # Create temporary text file for ffmpeg (better Chinese support)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', 
                                        suffix='.txt', delete=False) as f:
            text_file = Path(f.name)
            f.write(text_wrapped)
        
        font_file = get_chinese_font_path()
        
        # Build ffmpeg command using textfile instead of text parameter
        cmd = [
            'ffmpeg',
            '-ss', str(start_time),
            '-t', str(duration),
            '-i', source_video,
            '-vf',
            # Add white padding on right, then add text from file
            f"pad=iw+{TEXT_PANEL_WIDTH}:ih:0:0:white,"
            f"drawtext=fontfile={font_file}:"
            f"textfile={text_file}:"
            f"fontcolor=black:fontsize={TEXT_FONT_SIZE}:x=w-{TEXT_PANEL_WIDTH-20}:y=30:"
            f"line_spacing={TEXT_LINE_SPACING}",
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '23',
            '-y',
            str(output_path)
        ]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        # Clean up text file
        text_file.unlink()
        
        if result.returncode == 0:
            logger.info(f"[VideoExport] Created clip: {output_path}")
            return True
        else:
            logger.error(f"[VideoExport] ffmpeg error: {result.stderr[-500:]}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error(f"[VideoExport] ffmpeg timeout for {output_path}")
        return False
    except Exception as e:
        logger.error(f"[VideoExport] Error extracting clip: {e}")
        return False


def create_clip_from_frames(
    frame_paths: List[str],
    text: str,
    output_path: Path,
    fps: float = 10.0
) -> bool:
    """
    Create video clip from frame images with text panel on the right.
    
    Uses PIL to combine frames with text image, then FFmpeg to create video.
    
    Args:
        frame_paths: List of frame image paths
        text: Analysis text to embed
        output_path: Output video path
        fps: Frames per second
        
    Returns:
        True if successful
    """
    if not frame_paths:
        logger.error("[VideoExport] No frames provided")
        return False
    
    try:
        from PIL import Image
        
        # Step 1: Create text image using PIL
        text_img_path = create_text_image_pil(text, width=TEXT_PANEL_WIDTH, height=1080)
        if not text_img_path:
            logger.error("[VideoExport] Failed to create text image")
            return False
        
        # Step 2: Create combined frames (video frame + text image side by side)
        temp_combined_dir = Path(tempfile.mkdtemp())
        combined_frames = []
        
        for i, frame_path in enumerate(frame_paths):
            if not Path(frame_path).exists():
                logger.warning(f"[VideoExport] Frame not found: {frame_path}")
                continue
                
            # Open original frame
            frame_img = Image.open(frame_path)
            frame_width, frame_height = frame_img.size
            
            # Open text image and resize to match frame height
            text_img = Image.open(text_img_path)
            text_img_resized = text_img.resize((TEXT_PANEL_WIDTH, frame_height))
            
            # Create combined image (frame on left, text on right)
            combined_width = frame_width + TEXT_PANEL_WIDTH
            combined_img = Image.new('RGB', (combined_width, frame_height), 'white')
            combined_img.paste(frame_img, (0, 0))
            combined_img.paste(text_img_resized, (frame_width, 0))
            
            # Save combined frame
            combined_frame_path = temp_combined_dir / f"frame_{i:06d}.png"
            combined_img.save(combined_frame_path)
            combined_frames.append(combined_frame_path)
        
        # Clean up text image
        text_img_path.unlink()
        
        if not combined_frames:
            logger.error("[VideoExport] No combined frames created")
            return False
        
        # Step 3: Create video from combined frames using concat demuxer
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            list_file = Path(f.name)
            for combined_frame in combined_frames:
                f.write(f"file '{combined_frame}'\n")
                f.write(f"duration {1/fps}\n")
            # Add last frame again to ensure proper duration
            if combined_frames:
                f.write(f"file '{combined_frames[-1]}'\n")
        
        cmd = [
            'ffmpeg', '-f', 'concat', '-safe', '0', '-i', str(list_file),
            '-vsync', 'vfr', '-pix_fmt', 'yuv420p',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-y', str(output_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        list_file.unlink()
        
        # Clean up combined frames
        for frame in combined_frames:
            try:
                frame.unlink()
            except Exception:
                pass
        try:
            temp_combined_dir.rmdir()
        except Exception:
            pass
        
        if result.returncode == 0:
            logger.info(f"[VideoExport] Created clip from frames: {output_path}")
            return True
        else:
            logger.error(f"[VideoExport] Failed to create video: {result.stderr[-500:]}")
            return False
            
    except ImportError:
        logger.error("[VideoExport] PIL/Pillow not installed")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"[VideoExport] ffmpeg timeout for {output_path}")
        return False
    except Exception as e:
        logger.error(f"[VideoExport] Error creating video from frames: {e}")
        import traceback
        traceback.print_exc()
        return False


class VideoExportService:
    """Service for exporting video clips with analysis text."""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if VideoExportService._initialized:
            return
        VideoExportService._initialized = True
        
        # Ensure export directory exists
        EXPORTS_BASE.mkdir(parents=True, exist_ok=True)
        logger.info(f"[VideoExport] Export base directory: {EXPORTS_BASE}")
    
    def create_export_task(self, session_id: str, window_ids: List[int]) -> str:
        """
        Create a new export task.
        
        Args:
            session_id: Video session ID
            window_ids: List of window IDs to export
            
        Returns:
            task_id for tracking progress
        """
        task_id = str(uuid.uuid4())[:8]
        
        export_tasks[task_id] = {
            "task_id": task_id,
            "session_id": session_id,
            "window_ids": window_ids,
            "status": "pending",
            "progress": 0,
            "total": len(window_ids),
            "completed": 0,
            "failed": 0,
            "results": [],
            "created_at": datetime.now().isoformat(),
            "error": None
        }
        
        return task_id
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get export task status."""
        return export_tasks.get(task_id)
    
    async def export_clips(
        self,
        task_id: str,
        session_id: str,
        window_summaries: List[Dict[str, Any]],
        video_session: Dict[str, Any],
        max_workers: int = 4
    ) -> Dict[str, Any]:
        """
        Export video clips for selected windows with parallel processing.
        
        Args:
            task_id: Export task ID
            session_id: Video session ID
            window_summaries: List of window summary data
            video_session: Video session info from database
            max_workers: Number of parallel workers (default: 4)
            
        Returns:
            Export result with download links
        """
        # Update task status
        if task_id in export_tasks:
            export_tasks[task_id]["status"] = "processing"
        
        # Create output directory for this session
        output_dir = EXPORTS_BASE / session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        
        video_type = video_session.get("video_type", "stream")
        video_path = video_session.get("video_path")
        storage_path = video_session.get("storage_path")
        fps = video_session.get("fps", 10.0)
        
        results = []
        completed = 0
        failed = 0
        
        # Prepare export tasks for parallel execution
        async def export_single_window(window, idx):
            window_id = window.get("window_id", idx)
            start_time = window.get("window_start", 0)
            end_time = window.get("window_end", 5)
            summary_text = window.get("glm_summary", "无分析内容")
            
            output_filename = f"{session_id}_window{window_id}_t{int(start_time)}-{int(end_time)}s.mp4"
            output_path = output_dir / output_filename
            
            logger.info(f"[VideoExport] Processing window {window_id}: {start_time:.1f}s - {end_time:.1f}s")
            
            success = False
            
            try:
                if video_type == "local" and video_path and Path(video_path).exists():
                    success = await asyncio.get_event_loop().run_in_executor(
                        _executor,
                        extract_clip_from_local_video,
                        video_path,
                        start_time,
                        end_time,
                        summary_text,
                        output_path
                    )
                elif storage_path:
                    frame_paths = self._get_frame_paths_for_window(storage_path, start_time, end_time)
                    if frame_paths:
                        success = await asyncio.get_event_loop().run_in_executor(
                            _executor,
                            create_clip_from_frames,
                            frame_paths,
                            summary_text,
                            output_path,
                            fps
                        )
                    else:
                        logger.warning(f"[VideoExport] No frames found for window {window_id}")
            except Exception as e:
                logger.error(f"[VideoExport] Error processing window {window_id}: {e}")
                success = False
            
            return {
                "window_id": window_id,
                "filename": output_filename,
                "download_url": f"/api/analysis/download-clip/{session_id}/{output_filename}" if success else None,
                "start_time": start_time,
                "end_time": end_time,
                "status": "success" if success else "failed"
            }
        
        # Create semaphore to limit concurrent tasks
        semaphore = asyncio.Semaphore(max_workers)
        
        async def limited_export(window, idx):
            async with semaphore:
                return await export_single_window(window, idx)
        
        # Execute all tasks concurrently with semaphore limit
        logger.info(f"[VideoExport] Starting parallel export of {len(window_summaries)} windows (max {max_workers} concurrent)")
        
        tasks = [
            limited_export(window, i)
            for i, window in enumerate(window_summaries)
        ]
        
        # Process results as they complete
        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)
            
            if result["status"] == "success":
                completed += 1
            else:
                failed += 1
            
            # Update progress
            if task_id in export_tasks:
                export_tasks[task_id]["completed"] = completed
                export_tasks[task_id]["failed"] = failed
                export_tasks[task_id]["progress"] = int((completed + failed) / len(window_summaries) * 100)
                export_tasks[task_id]["results"] = sorted(results, key=lambda x: x.get("window_id", 0))
        
        # Sort results by window_id
        results = sorted(results, key=lambda x: x.get("window_id", 0))
        
        # Mark task as complete
        if task_id in export_tasks:
            export_tasks[task_id]["status"] = "completed"
            export_tasks[task_id]["progress"] = 100
            export_tasks[task_id]["results"] = results
        
        logger.info(f"[VideoExport] Export complete: {completed} success, {failed} failed")
        
        return {
            "task_id": task_id,
            "session_id": session_id,
            "status": "completed",
            "total": len(window_summaries),
            "completed": completed,
            "failed": failed,
            "results": results
        }
    
    def _get_frame_paths_for_window(
        self,
        storage_path: str,
        start_time: float,
        end_time: float
    ) -> List[str]:
        """Get list of frame paths for a time window."""
        from .frame_storage_service import get_frame_storage_service
        
        frame_storage = get_frame_storage_service()
        frames = frame_storage.list_frames_in_range(storage_path, start_time, end_time, "frames")
        
        frame_paths = []
        for frame_info in frames:
            filename = frame_info.get("filename", "")
            if filename:
                frame_path = Path(storage_path) / "frames" / filename
                if frame_path.exists():
                    frame_paths.append(str(frame_path))
        
        return sorted(frame_paths)
    
    def get_export_file_path(self, session_id: str, filename: str) -> Optional[Path]:
        """Get the full path to an exported file."""
        file_path = EXPORTS_BASE / session_id / filename
        if file_path.exists():
            return file_path
        return None
    
    def list_exports(self, session_id: str) -> List[Dict[str, Any]]:
        """List all exported files for a session."""
        export_dir = EXPORTS_BASE / session_id
        if not export_dir.exists():
            return []
        
        exports = []
        for file_path in export_dir.glob("*.mp4"):
            exports.append({
                "filename": file_path.name,
                "size": file_path.stat().st_size,
                "created_at": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(),
                "download_url": f"/api/analysis/download-clip/{session_id}/{file_path.name}"
            })
        
        return sorted(exports, key=lambda x: x.get("filename", ""))


# Global instance
_video_export_service: Optional[VideoExportService] = None


def get_video_export_service() -> VideoExportService:
    """Get the global video export service instance."""
    global _video_export_service
    if _video_export_service is None:
        _video_export_service = VideoExportService()
    return _video_export_service
