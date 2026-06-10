#!/usr/bin/env python3
"""
Export Session Clips - 命令行离线导出工具

将指定 session 的分析结果导出为带右侧分析文字面板的独立视频片段。

使用方式:
    python export_session_clips.py <session_id>                    # 导出所有窗口
    python export_session_clips.py <session_id> --windows 0,1,2    # 导出指定窗口
    python export_session_clips.py <session_id> --output /path/to  # 指定输出目录
    python export_session_clips.py <session_id> --workers 4        # 使用4个并行worker
    python export_session_clips.py --list                          # 列出所有可用session
    python export_session_clips.py --list --limit 20               # 列出最近20个session

依赖:
    - MySQL 数据库连接
    - FFmpeg (用于视频生成)
    - Pillow (用于文字渲染)
"""

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
import logging

# Add parent path to import backend services
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Text panel settings
TEXT_PANEL_WIDTH = 800  # Wider panel for better readability
TEXT_FONT_SIZE = 20     # Smaller font to fit more text
TEXT_LINE_SPACING = 6
TEXT_WRAP_WIDTH = 55    # Wider wrap for English text


def load_config() -> dict:
    """Load configuration from config.json"""
    config_path = Path(__file__).parent.parent / "config.json"
    if config_path.exists():
        with open(config_path, 'r') as f:
            return json.load(f)
    return {}


def get_mysql_connection():
    """Get MySQL database connection using SQLAlchemy"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    config = load_config()
    mysql_config = config.get("database", {}).get("mysql", {})
    
    host = mysql_config.get("host", "localhost")
    port = mysql_config.get("port", 3306)
    user = mysql_config.get("user", "root")
    password = mysql_config.get("password", "")
    database = mysql_config.get("database", "video_analyzer")
    
    if password:
        url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"
    else:
        url = f"mysql+pymysql://{user}@{host}:{port}/{database}?charset=utf8mb4"
    
    engine = create_engine(url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    return Session()


def list_sessions(limit: int = 50) -> List[Dict[str, Any]]:
    """List all available video sessions"""
    session = get_mysql_connection()
    try:
        from sqlalchemy import text
        result = session.execute(text("""
            SELECT 
                session_id,
                video_name,
                video_type,
                video_path,
                duration,
                storage_path,
                status,
                created_at
            FROM video_sessions
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"limit": limit})
        
        sessions = []
        for row in result:
            sessions.append({
                "session_id": row.session_id,
                "video_name": row.video_name,
                "video_type": row.video_type,
                "video_path": row.video_path,
                "duration": row.duration,
                "storage_path": row.storage_path,
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None
            })
        return sessions
    finally:
        session.close()


def get_video_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Get video session info by session_id"""
    session = get_mysql_connection()
    try:
        from sqlalchemy import text
        result = session.execute(text("""
            SELECT 
                session_id,
                video_name,
                video_type,
                video_path,
                duration,
                fps,
                storage_path,
                status
            FROM video_sessions
            WHERE session_id = :session_id
        """), {"session_id": session_id})
        
        row = result.fetchone()
        if row:
            return {
                "session_id": row.session_id,
                "video_name": row.video_name,
                "video_type": row.video_type,
                "video_path": row.video_path,
                "duration": row.duration,
                "fps": row.fps or 10.0,
                "storage_path": row.storage_path,
                "status": row.status
            }
        return None
    finally:
        session.close()


def get_window_summaries(session_id: str) -> List[Dict[str, Any]]:
    """Get all window summaries for a session"""
    session = get_mysql_connection()
    try:
        from sqlalchemy import text
        result = session.execute(text("""
            SELECT 
                window_id,
                window_start,
                window_end,
                glm_summary,
                surgical_phase
            FROM analysis_results
            WHERE session_id = :session_id
              AND analysis_type = 'window'
            ORDER BY window_id ASC
        """), {"session_id": session_id})
        
        summaries = []
        for row in result:
            summaries.append({
                "window_id": row.window_id,
                "window_start": row.window_start,
                "window_end": row.window_end,
                "glm_summary": row.glm_summary or "",
                "surgical_phase": row.surgical_phase
            })
        return summaries
    finally:
        session.close()


def get_chinese_font_path() -> str:
    """Find a Chinese-compatible font on the system"""
    font_paths = [
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/arphic/uming.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/System/Library/Fonts/PingFang.ttc',
    ]
    
    for font_path in font_paths:
        if Path(font_path).exists():
            return font_path
    
    return '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def format_analysis_text(text: str) -> str:
    """
    Format analysis text for better readability.
    Handles <think> tags and Level markers.
    """
    if not text:
        return ""
    
    import re
    
    # Remove <think> and </think> tags but keep content
    text = re.sub(r'</?think>', '', text)
    
    # Add spacing around Level markers
    text = re.sub(r'(Level \d+:)', r'\n\n\1', text)
    
    # Clean up multiple newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


def wrap_text_for_display(text: str, width: int = TEXT_WRAP_WIDTH) -> str:
    """
    Wrap text for display, handling both English and Chinese.
    English: wrap by words
    Chinese: wrap by characters
    """
    if not text:
        return ""
    
    import re
    
    # First format the text (handle tags, levels)
    text = format_analysis_text(text)
    
    # Split into paragraphs
    paragraphs = text.split('\n')
    result_lines = []
    
    for para in paragraphs:
        para = para.strip()
        if not para:
            result_lines.append('')
            continue
        
        # Check if mostly English (ASCII) or Chinese
        ascii_chars = sum(1 for c in para if ord(c) < 128)
        is_english = ascii_chars > len(para) * 0.5
        
        if is_english:
            # English: wrap by words
            words = para.split()
            current_line = ""
            for word in words:
                test_line = f"{current_line} {word}".strip() if current_line else word
                if len(test_line) <= width:
                    current_line = test_line
                else:
                    if current_line:
                        result_lines.append(current_line)
                    # Handle very long words
                    while len(word) > width:
                        result_lines.append(word[:width-1] + '-')
                        word = word[width-1:]
                    current_line = word
            if current_line:
                result_lines.append(current_line)
        else:
            # Chinese: wrap by characters with punctuation awareness
            current_line = ""
            char_count = 0
            
            for char in para:
                current_line += char
                char_count += 1
                
                if char in '。！？；\n' or char_count >= width:
                    result_lines.append(current_line)
                    current_line = ""
                    char_count = 0
                elif char in '，、：' and char_count >= width * 0.7:
                    result_lines.append(current_line)
                    current_line = ""
                    char_count = 0
            
            if current_line:
                result_lines.append(current_line)
    
    # Limit total lines to prevent overflow
    return '\n'.join(result_lines[:60])


def create_text_image(text: str, width: int = TEXT_PANEL_WIDTH, height: int = 1080) -> Optional[Path]:
    """Create an image with text using PIL"""
    try:
        from PIL import Image, ImageDraw, ImageFont
        
        img = Image.new('RGB', (width, height), 'white')
        draw = ImageDraw.Draw(img)
        
        font_path = get_chinese_font_path()
        font = None
        if Path(font_path).exists():
            try:
                font = ImageFont.truetype(font_path, TEXT_FONT_SIZE)
            except Exception:
                pass
        
        if font is None:
            font = ImageFont.load_default()
        
        wrapped_text = wrap_text_for_display(text, width=TEXT_WRAP_WIDTH)
        
        y_position = 30
        line_height = TEXT_FONT_SIZE + TEXT_LINE_SPACING
        for line in wrapped_text.split('\n'):
            draw.text((20, y_position), line, fill='black', font=font)
            y_position += line_height
            
            if y_position > height - 50:
                break
        
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


def extract_clip_from_video(
    source_video: str,
    start_time: float,
    end_time: float,
    text: str,
    output_path: Path
) -> bool:
    """Extract clip from local video file and add text panel"""
    try:
        duration = end_time - start_time
        
        text_wrapped = wrap_text_for_display(text, width=TEXT_WRAP_WIDTH)
        
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', 
                                        suffix='.txt', delete=False) as f:
            text_file = Path(f.name)
            f.write(text_wrapped)
        
        font_file = get_chinese_font_path()
        
        cmd = [
            'ffmpeg',
            '-ss', str(start_time),
            '-t', str(duration),
            '-i', source_video,
            '-vf',
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
            timeout=300
        )
        
        text_file.unlink()
        
        if result.returncode == 0:
            return True
        else:
            logger.error(f"ffmpeg error: {result.stderr[-500:]}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error(f"ffmpeg timeout for {output_path}")
        return False
    except Exception as e:
        logger.error(f"Error extracting clip: {e}")
        return False


def create_clip_from_frames(
    frame_paths: List[str],
    text: str,
    output_path: Path,
    window_duration: float = 15.0
) -> bool:
    """
    Create video clip from frame images with text panel using pure ffmpeg.
    
    Args:
        frame_paths: List of frame image paths
        text: Summary text to display on the right panel
        output_path: Output video path
        window_duration: Target video duration in seconds (used to calculate fps)
    
    Returns:
        True if successful, False otherwise
    """
    if not frame_paths:
        logger.error("No frames provided")
        return False
    
    try:
        # Calculate fps based on frame count and window duration
        # This ensures the output video has the correct duration
        frame_count = len(frame_paths)
        fps = frame_count / window_duration if window_duration > 0 else 10.0
        
        # Prepare text for ffmpeg drawtext filter
        text_wrapped = wrap_text_for_display(text, width=TEXT_WRAP_WIDTH)
        
        # Create temporary text file for drawtext
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', 
                                        suffix='.txt', delete=False) as f:
            text_file = Path(f.name)
            f.write(text_wrapped)
        
        # Create frame list file for ffmpeg concat demuxer
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            list_file = Path(f.name)
            frame_duration = 1.0 / fps
            for frame_path in frame_paths:
                if Path(frame_path).exists():
                    f.write(f"file '{frame_path}'\n")
                    f.write(f"duration {frame_duration}\n")
            # Add last frame again (required by concat demuxer)
            if frame_paths:
                f.write(f"file '{frame_paths[-1]}'\n")
        
        font_file = get_chinese_font_path()
        
        # Build ffmpeg command with concat input + pad + drawtext filters
        # This processes everything in a single ffmpeg pass - much faster than PIL
        cmd = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(list_file),
            '-vf',
            f"pad=iw+{TEXT_PANEL_WIDTH}:ih:0:0:white,"
            f"drawtext=fontfile={font_file}:"
            f"textfile={text_file}:"
            f"fontcolor=black:fontsize={TEXT_FONT_SIZE}:x=w-{TEXT_PANEL_WIDTH-20}:y=30:"
            f"line_spacing={TEXT_LINE_SPACING}",
            '-vsync', 'vfr',
            '-pix_fmt', 'yuv420p',
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
            timeout=300
        )
        
        # Cleanup temp files
        try:
            text_file.unlink()
        except Exception:
            pass
        try:
            list_file.unlink()
        except Exception:
            pass
        
        if result.returncode == 0:
            return True
        else:
            logger.error(f"ffmpeg error: {result.stderr[-500:]}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error(f"ffmpeg timeout for {output_path}")
        return False
    except Exception as e:
        logger.error(f"Error creating video from frames: {e}")
        import traceback
        traceback.print_exc()
        return False


def get_frame_paths_for_window(
    storage_path: str,
    start_time: float,
    end_time: float
) -> List[str]:
    """Get list of frame paths for a time window"""
    frames_dir = Path(storage_path) / "frames"
    if not frames_dir.exists():
        return []
    
    frame_paths = []
    
    for frame_file in sorted(frames_dir.glob("*.jpg")):
        filename = frame_file.stem
        parts = filename.split("_")
        
        timestamp = None
        if len(parts) >= 3:
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
                    timestamp = float(ts_part.replace("_", "."))
                except ValueError:
                    pass
        
        if timestamp is not None and start_time <= timestamp <= end_time:
            frame_paths.append(str(frame_file))
    
    return sorted(frame_paths)


def export_single_window(args: Tuple) -> Tuple[int, bool, str, float]:
    """
    Export a single window - designed to run in parallel process pool.
    
    Args:
        args: Tuple of (window_data, session_id, output_dir, video_type, video_path, storage_path, window_duration)
    
    Returns:
        Tuple of (window_id, success, output_filename, file_size_mb)
    """
    window, session_id, output_dir, video_type, video_path, storage_path, window_duration = args
    
    window_id = window.get("window_id", 0)
    start_time = window.get("window_start", 0)
    end_time = window.get("window_end", 5)
    summary_text = window.get("glm_summary", "无分析内容")
    
    # Calculate actual window duration from database values
    actual_duration = end_time - start_time
    # Use actual duration if available, otherwise use config window_duration
    target_duration = actual_duration if actual_duration > 0 else window_duration
    
    output_filename = f"{session_id}_window{window_id}_t{int(start_time)}-{int(end_time)}s.mp4"
    output_path = Path(output_dir) / output_filename
    
    success = False
    file_size = 0.0
    
    try:
        if video_type == "local" and video_path and Path(video_path).exists():
            success = extract_clip_from_video(
                video_path,
                start_time,
                end_time,
                summary_text,
                output_path
            )
        elif storage_path:
            frame_paths = get_frame_paths_for_window(storage_path, start_time, end_time)
            if frame_paths:
                success = create_clip_from_frames(
                    frame_paths,
                    summary_text,
                    output_path,
                    window_duration=target_duration
                )
        
        if success and output_path.exists():
            file_size = output_path.stat().st_size / (1024 * 1024)
    except Exception as e:
        print(f"[Window {window_id}] Error: {e}")
        success = False
    
    return (window_id, success, output_filename, file_size)


def export_session(
    session_id: str,
    window_ids: Optional[List[int]] = None,
    output_dir: Optional[Path] = None,
    max_workers: int = None
):
    """
    Export session clips with parallel processing.
    
    Args:
        session_id: Session ID to export
        window_ids: Optional list of specific window IDs to export
        output_dir: Output directory (default: exports/<session_id>)
        max_workers: Number of parallel workers (default: CPU count, max 8)
    """
    
    # Load config to get window_duration
    config = load_config()
    window_duration = config.get("video_processing", {}).get("window_duration", 15.0)
    
    # Get session info
    video_session = get_video_session(session_id)
    if not video_session:
        logger.error(f"Session not found: {session_id}")
        return False
    
    logger.info(f"Session: {session_id}")
    logger.info(f"  Video: {video_session.get('video_name')}")
    logger.info(f"  Type: {video_session.get('video_type')}")
    logger.info(f"  Path: {video_session.get('video_path')}")
    logger.info(f"  Storage: {video_session.get('storage_path')}")
    logger.info(f"  Window Duration: {window_duration}s (from config)")
    
    # Get window summaries
    summaries = get_window_summaries(session_id)
    if not summaries:
        logger.error("No analysis results found for this session")
        return False
    
    logger.info(f"  Total windows: {len(summaries)}")
    
    # Filter windows if specified
    if window_ids:
        window_ids_set = set(window_ids)
        summaries = [s for s in summaries if s.get("window_id") in window_ids_set]
        if not summaries:
            logger.error(f"No matching windows found for IDs: {window_ids}")
            return False
        logger.info(f"  Selected windows: {len(summaries)}")
    
    # Set output directory
    if output_dir is None:
        output_dir = Path("/data2/jj/proj/video_processor/output") / session_id
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"  Output: {output_dir}")
    
    video_type = video_session.get("video_type", "stream")
    video_path = video_session.get("video_path")
    storage_path = video_session.get("storage_path")
    
    # Determine number of workers (default max 8 for better parallelism)
    if max_workers is None:
        max_workers = min(multiprocessing.cpu_count(), 8)
    max_workers = min(max_workers, len(summaries))  # No more workers than tasks
    
    logger.info(f"  Workers: {max_workers} (parallel)")
    
    # Prepare task arguments (pass window_duration instead of fps)
    tasks = [
        (window, session_id, str(output_dir), video_type, video_path, storage_path, window_duration)
        for window in summaries
    ]
    
    success_count = 0
    fail_count = 0
    results = []
    
    # Execute in parallel using ProcessPoolExecutor
    print(f"\n开始并行导出 {len(summaries)} 个窗口 (使用 {max_workers} 个进程)...")
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_window = {
            executor.submit(export_single_window, task): task[0].get("window_id", i)
            for i, task in enumerate(tasks)
        }
        
        # Collect results as they complete
        for future in as_completed(future_to_window):
            window_id = future_to_window[future]
            try:
                result = future.result()
                wid, success, filename, file_size = result
                
                if success:
                    print(f"  ✓ Window {wid}: {filename} ({file_size:.1f} MB)")
                    success_count += 1
                else:
                    print(f"  ✗ Window {wid}: {filename} (失败)")
                    fail_count += 1
                
                results.append(result)
                
            except Exception as e:
                print(f"  ✗ Window {window_id}: 异常 - {e}")
                fail_count += 1
    
    # Sort results by window_id for summary
    results.sort(key=lambda x: x[0])
    
    print("\n" + "=" * 60)
    print("EXPORT COMPLETE")
    print(f"  Total: {len(summaries)}")
    print(f"  Success: {success_count}")
    print(f"  Failed: {fail_count}")
    print(f"  Output: {output_dir}")
    print("=" * 60)
    
    return fail_count == 0


def main():
    parser = argparse.ArgumentParser(
        description='Export video clips with analysis text for a session (supports parallel processing)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s abc123                     Export all windows for session abc123
  %(prog)s abc123 --windows 0,1,2     Export only windows 0, 1, 2
  %(prog)s abc123 --output /tmp/out   Export to custom directory
  %(prog)s abc123 --workers 4         Use 4 parallel workers
  %(prog)s --list                     List all available sessions
  %(prog)s --list --limit 10          List last 10 sessions
"""
    )
    
    parser.add_argument(
        'session_id',
        nargs='?',
        help='Session ID to export'
    )
    
    parser.add_argument(
        '--windows', '-w',
        type=str,
        help='Comma-separated list of window IDs to export (e.g., 0,1,2)'
    )
    
    parser.add_argument(
        '--output', '-o',
        type=str,
        help='Output directory for exported videos'
    )
    
    parser.add_argument(
        '--workers', '-j',
        type=int,
        default=None,
        help='Number of parallel workers (default: auto, max 8)'
    )
    
    parser.add_argument(
        '--list', '-l',
        action='store_true',
        help='List all available sessions'
    )
    
    parser.add_argument(
        '--limit',
        type=int,
        default=50,
        help='Maximum number of sessions to list (default: 50)'
    )
    
    args = parser.parse_args()
    
    # Handle --list mode
    if args.list:
        sessions = list_sessions(limit=args.limit)
        if not sessions:
            print("No sessions found.")
            return 0
        
        print(f"\n{'='*80}")
        print(f"Available Sessions (showing {len(sessions)})")
        print(f"{'='*80}")
        
        for s in sessions:
            print(f"\n  Session ID: {s['session_id']}")
            print(f"    Name: {s['video_name'] or '(unnamed)'}")
            print(f"    Type: {s['video_type']}")
            print(f"    Path: {s['video_path'] or 'N/A'}")
            print(f"    Duration: {s['duration']:.1f}s" if s['duration'] else "    Duration: N/A")
            print(f"    Created: {s['created_at']}")
        
        print(f"\n{'='*80}")
        return 0
    
    # Export mode requires session_id
    if not args.session_id:
        parser.print_help()
        return 1
    
    # Parse window IDs if specified
    window_ids = None
    if args.windows:
        try:
            window_ids = [int(x.strip()) for x in args.windows.split(',')]
        except ValueError:
            logger.error(f"Invalid window IDs: {args.windows}")
            return 1
    
    # Parse output directory
    output_dir = None
    if args.output:
        output_dir = Path(args.output)
    
    # Run export with parallel processing
    success = export_session(
        session_id=args.session_id,
        window_ids=window_ids,
        output_dir=output_dir,
        max_workers=args.workers
    )
    
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
