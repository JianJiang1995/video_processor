"""
Video Analysis API Routes
Handles GPT summarization, SAM2 masks, and TTS
"""
import asyncio
import time
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
import json
from PIL import Image
from io import BytesIO
import base64

from ..database import (
    get_db, get_video_session, get_video_session_by_id,
    create_frame_analysis, get_frames_by_session,
    create_window_summary, get_summaries_by_session, get_summary_for_timestamp
)
from ..services.video_processor import VideoProcessor, build_frame_context
from ..services.gpt_summarizer import GPTSummarizer
from ..services.glm_summarizer import get_glm_summarizer
from ..services.sam2_service import SAM2Service
from ..services.tts_service import TTSService
from ..services.model_service import get_model_service, ensure_model_loaded
from ..services.surgr1_client import get_surgr1_client, ensure_surgr1_available
from ..services.sam3_client import get_sam3_client, ensure_sam3_available
from ..services.glm_client import get_glm_client, ensure_glm_available
from ..services.tts_cosyvoice_client import get_tts_client, ensure_tts_available
from ..services.mysql_service import get_mysql_service
from ..services.frame_storage_service import get_frame_storage_service
from ..config import settings, ANALYSIS_SYSTEM_PROMPT

import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def get_frame_attr(frame, attr: str, default=None):
    """Helper to get attribute from frame - handles both dict and object."""
    if isinstance(frame, dict):
        return frame.get(attr, default)
    return getattr(frame, attr, default)

# Global service instances
gpt_summarizer: Optional[GPTSummarizer] = None
sam2_service: Optional[SAM2Service] = None
tts_service: Optional[TTSService] = None

# Global cancellation flags for analysis tasks
# Key: session_id, Value: bool (True = should cancel)
analysis_cancellation_flags: dict = {}

# Global flags for continuous SurgR1 processing
# Key: session_id, Value: bool (True = running)
surgr1_continuous_flags: dict = {}

# Active SurgR1 task references for cancellation
# Key: session_id, Value: list of tasks
active_surgr1_tasks: dict = {}

# Global stream start times for time synchronization
# Key: session_id, Value: float (unix timestamp when processing started)
stream_start_times: dict = {}

# Global SAM3 streaming sessions
# Key: session_id, Value: dict with sam3_session_id, last_frame, consistency_checker, etc.
sam3_streaming_sessions: dict = {}

# Store latest SAM3 segmented frames for quick access
# Key: session_id, Value: dict with timestamp, image_base64, etc.
sam3_latest_frames: dict = {}

# Import consistency checker
from ..services.sam3_consistency import (
    SAM3ConsistencyChecker, 
    SAM3State, 
    ConsistencyConfig,
    parse_bboxes_from_surgr1
)

# Frame capture flags for playback
# Key: session_id, Value: bool (True = running)
frame_capture_flags: dict = {}


async def frame_capture_for_playback(
    session_id: str,
    video_source: str,
    is_realtime_stream: bool,
    stream_start_time: float
):
    """
    Independent frame capture task that runs at 5 FPS for smooth loop playback.
    This runs in parallel with SurgR1 analysis which is slower (~1 fps).
    """
    import cv2
    import time as time_module
    
    FRAME_SAVE_INTERVAL = 0.2  # 5 FPS
    
    # Mark as running
    frame_capture_flags[session_id] = True
    
    # Open a separate video capture
    cap = cv2.VideoCapture(video_source)
    if not cap.isOpened():
        logger.warning(f"[FrameCapture] Could not open video source for session {session_id}")
        return
    
    logger.info(f"[FrameCapture] Started frame capture task for session {session_id} at 5 FPS")
    
    saved_frame_idx = 0
    last_save_time = -FRAME_SAVE_INTERVAL
    
    try:
        # Get or create storage path once
        mysql_service = get_mysql_service()
        video_session = mysql_service.get_video_session(session_id)
        storage_path = video_session.get("storage_path") if video_session else None
        
        if not storage_path:
            frame_storage = get_frame_storage_service()
            video_name = video_session.get("video_name", "stream") if video_session else "stream"
            storage_path = frame_storage.create_session_folder(session_id, video_name)
            mysql_service.update_video_session(session_id, storage_path=storage_path)
            logger.info(f"[FrameCapture] Created storage folder: {storage_path}")
        
        frame_storage = get_frame_storage_service()
        
        while frame_capture_flags.get(session_id, False) and surgr1_continuous_flags.get(session_id, False):
            ret, bgr_frame = cap.read()
            
            if not ret:
                if is_realtime_stream:
                    await asyncio.sleep(0.05)
                    continue
                else:
                    # End of file - restart
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
            
            # Calculate current time
            if is_realtime_stream:
                current_time = time_module.time() - stream_start_time
            else:
                fps = cap.get(cv2.CAP_PROP_FPS) or 30
                frame_pos = cap.get(cv2.CAP_PROP_POS_FRAMES)
                current_time = frame_pos / fps
            
            # Check if we should save this frame
            if current_time - last_save_time >= FRAME_SAVE_INTERVAL:
                last_save_time = current_time
                
                try:
                    frame_storage.save_frame(
                        storage_path=storage_path,
                        timestamp=current_time,
                        frame_data=bgr_frame,
                        frame_idx=saved_frame_idx,
                        subfolder="frames"
                    )
                    saved_frame_idx += 1
                    
                    if saved_frame_idx % 25 == 0:  # Log every 5 seconds
                        logger.info(f"[FrameCapture] Saved {saved_frame_idx} frames for session {session_id}")
                except Exception as e:
                    logger.warning(f"[FrameCapture] Failed to save frame: {e}")
            
            # Small sleep to not overwhelm CPU
            await asyncio.sleep(0.02)  # ~50 fps read rate, save at 5 fps
    
    except asyncio.CancelledError:
        logger.info(f"[FrameCapture] Task cancelled for session {session_id}")
    except Exception as e:
        logger.error(f"[FrameCapture] Error: {e}")
    finally:
        frame_capture_flags[session_id] = False
        if cap is not None:
            cap.release()
        logger.info(f"[FrameCapture] Stopped for session {session_id}, saved {saved_frame_idx} frames")


def get_gpt_summarizer() -> GPTSummarizer:
    """Get or create GPT summarizer"""
    global gpt_summarizer
    if gpt_summarizer is None:
        gpt_summarizer = GPTSummarizer(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            model=settings.GPT_MODEL
        )
    return gpt_summarizer


def get_sam2_service() -> SAM2Service:
    """Get or create SAM2 service"""
    global sam2_service
    if sam2_service is None:
        sam2_service = SAM2Service(
            model_path=settings.SAM2_MODEL_PATH
        )
    return sam2_service


def get_tts_service() -> TTSService:
    """Get or create TTS service"""
    global tts_service
    if tts_service is None:
        tts_service = TTSService(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            voice=settings.TTS_VOICE,
            output_dir=settings.OUTPUT_DIR / "tts"
        )
    return tts_service


class AnalyzeWindowRequest(BaseModel):
    session_id: str
    start_time: float
    use_chinese: bool = False


class SummarizeRequest(BaseModel):
    text: str
    use_chinese: bool = False


class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None


class SAM2Request(BaseModel):
    session_id: str
    timestamp: float
    auto_detect: bool = True


class VLMAnalyzeRequest(BaseModel):
    session_id: str
    start_time: float
    use_vlm: bool = True  # Use local VLM instead of GPT


class ImageAnalysisRequest(BaseModel):
    """Request for image analysis API"""
    session_id: str
    start_time: float
    analysis_type: str = "all"  # "all", "phase", "action", "tools"


class IntegrateAnalysisRequest(BaseModel):
    """Request for integrating analysis results"""
    session_id: str
    start_time: float
    use_glm: bool = True  # Use GLM-4.6V-Flash instead of GPT


class ProcessVideoSurgR1GLMRequest(BaseModel):
    """Request for processing video with SurgR1 + GLM"""
    session_id: str
    use_chinese: bool = False
    use_glm_multimodal: bool = False  # Use GLM with images


class FrameData(BaseModel):
    """Frame data for batch analysis"""
    frame_idx: int
    timestamp: float
    image_base64: Optional[str] = None  # Base64 encoded image


class AnalyzeFramesBatchRequest(BaseModel):
    """Request for batch frame analysis from frontend queue"""
    session_id: str
    frames: List[FrameData]


@router.post("/analyze-frames-batch")
async def analyze_frames_batch(
    request: AnalyzeFramesBatchRequest,
    db: Session = Depends(get_db)
):
    """
    Batch analyze frames sent from frontend queue.
    
    This endpoint receives frames from the frontend AnalysisQueue
    and processes them in batch for efficiency.
    
    For real-time stream mode, frames include base64 images.
    For video file mode, frames are extracted from the video.
    """
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    if not request.frames:
        return {"success": True, "results": [], "message": "No frames to analyze"}
    
    try:
        surgr1_client = await ensure_surgr1_available()
        
        # Prepare frames for batch processing
        batch_frames = []
        
        for frame_data in request.frames:
            if frame_data.image_base64:
                # Decode base64 image
                import base64
                from io import BytesIO
                image_bytes = base64.b64decode(frame_data.image_base64)
                image = Image.open(BytesIO(image_bytes))
                batch_frames.append({
                    "image": image,
                    "frame_idx": frame_data.frame_idx,
                    "timestamp": frame_data.timestamp
                })
            else:
                # Extract frame from video file
                processor = VideoProcessor(
                    video_path=session["video_path"],
                    window_duration=settings.WINDOW_DURATION,
                    sample_interval=settings.SAMPLE_INTERVAL
                )
                frame = processor.extract_frame(frame_data.timestamp)
                if frame:
                    batch_frames.append({
                        "image": frame.image,
                        "frame_idx": frame_data.frame_idx,
                        "timestamp": frame_data.timestamp
                    })
        
        if not batch_frames:
            return {"success": True, "results": [], "message": "No valid frames"}
        
        # Batch analyze all frames
        results = await surgr1_client.analyze_frames_batch(
            frames=batch_frames,
            analysis_type="all",
            session_id=request.session_id,
            save_to_mysql=True
        )
        
        # Save to SQLite database
        for result in results:
            create_frame_analysis(
                db=db,
                session_id=session["session_id"],
                frame_idx=result.get("frame_idx"),
                timestamp=result.get("timestamp"),
                tool_localization=result.get("tools", ""),
                surgical_action=result.get("action", ""),
                surgical_phase=result.get("phase", "")
            )
        
        logger.info(f"Batch analyzed {len(results)} frames for session {request.session_id}")
        
        return {
            "success": True,
            "results": [
                {
                    "frame_idx": r.get("frame_idx"),
                    "timestamp": r.get("timestamp"),
                    "tool_localization": r.get("tools", ""),
                    "surgical_action": r.get("action", ""),
                    "surgical_phase": r.get("phase", ""),
                    "window_id": int(r.get("timestamp", 0) / settings.WINDOW_DURATION)
                }
                for r in results
            ],
            "batch_size": len(results)
        }
        
    except Exception as e:
        logger.error(f"Batch frame analysis failed: {e}")
        raise HTTPException(500, f"Batch analysis failed: {str(e)}")


@router.post("/analyze-window")
async def analyze_window(
    request: AnalyzeWindowRequest,
    db: Session = Depends(get_db)
):
    """Analyze a 5-second window and generate summary using GPT"""
    
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Create video processor
    processor = VideoProcessor(
        video_path=session["video_path"],
        window_duration=settings.WINDOW_DURATION,
        sample_interval=settings.SAMPLE_INTERVAL
    )
    
    # Extract window
    window = processor.extract_window(request.start_time)
    
    if window.frame_count == 0:
        raise HTTPException(400, "No frames extracted for this window")
    
    # Get GPT summarizer
    summarizer = get_gpt_summarizer()
    if request.use_chinese:
        summarizer.use_chinese = True
    
    # Build context and generate summary
    context = build_frame_context(window)
    
    result = await summarizer.summarize_window(
        images=window.get_images(),
        context=context,
        system_prompt=ANALYSIS_SYSTEM_PROMPT
    )
    
    if result["success"]:
        # Save to database
        summary = create_window_summary(
            db=db,
            session_id=session["session_id"],
            window_id=window.window_id,
            start_time=window.start_time,
            end_time=window.end_time,
            summary_text=result["summary"],
            summary_chinese=result["summary"] if request.use_chinese else None
        )
        
        return {
            "window_id": window.window_id,
            "start_time": window.start_time,
            "end_time": window.end_time,
            "frame_count": window.frame_count,
            "summary": result["summary"],
            "summary_id": summary.id
        }
    else:
        raise HTTPException(500, f"Summarization failed: {result.get('error', 'Unknown error')}")


@router.post("/analyze-window-vlm")
async def analyze_window_with_vlm(
    request: VLMAnalyzeRequest,
    db: Session = Depends(get_db)
):
    """
    Analyze a 5-second window using local VLM model (vLLM)
    
    Uses the local Qwen2.5-VL model for frame-by-frame analysis,
    then generates a summary.
    """
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Create video processor
    processor = VideoProcessor(
        video_path=session["video_path"],
        window_duration=settings.WINDOW_DURATION,
        sample_interval=settings.SAMPLE_INTERVAL
    )
    
    # Extract window
    window = processor.extract_window(request.start_time)
    
    if window.frame_count == 0:
        raise HTTPException(400, "No frames extracted for this window")
    
    # Get VLM model service
    vlm_service = await ensure_model_loaded()
    
    # Analyze each frame
    frame_analyses = []
    for frame in window.frames:
        analysis = await vlm_service.analyze_frame(frame.image, analysis_type="all")
        frame_analyses.append({
            "frame_idx": frame.frame_idx,
            "timestamp": frame.timestamp,
            **analysis
        })
        
        # Save frame analysis to database
        create_frame_analysis(
            db=db,
            session_id=session["session_id"],
            frame_idx=frame.frame_idx,
            timestamp=frame.timestamp,
            tool_localization=analysis.get("tools", ""),
            surgical_action=analysis.get("action", ""),
            surgical_phase=analysis.get("phase", "")
        )
    
    # Generate summary using GLM-4.6V-Flash (fallback to GPT if GLM unavailable)
    summary_text = "Analysis completed."
    model_used = "VLM"
    
    try:
        # Try GLM-4.6V-Flash first (纯文本输入，全中文输出)
        glm_summarizer = get_glm_summarizer()
        is_healthy = await glm_summarizer.check_health()
        
        if is_healthy:
            # Integrate using GLM (按照temporal_analyze.py的逻辑)
            result = await glm_summarizer.integrate_analysis_results(
                frame_analyses=frame_analyses,
                images=None,  # GLM只使用文本输入
                system_prompt=None  # 使用内置的全中文提示词
            )
            
            if result["success"]:
                summary_text = result["summary"]
                model_used = "VLM + GLM-4.6V-Flash"
            else:
                raise Exception(f"GLM整合失败: {result.get('error')}")
        else:
            raise Exception("GLM服务不可用")
            
    except Exception as e:
        # Fallback to GPT
        logger.warning(f"GLM integration failed, falling back to GPT: {e}")
        context = build_frame_context(window, frame_analyses)
        summarizer = get_gpt_summarizer()
        result = await summarizer.summarize_window(
            images=window.get_images(),
            context=context,
            system_prompt=ANALYSIS_SYSTEM_PROMPT
        )
        
        if result["success"]:
            summary_text = result["summary"]
            model_used = "VLM + GPT"
        else:
            summary_text = result.get("summary", "Analysis completed.")
            model_used = "VLM"
    
    # Save summary
    summary = create_window_summary(
        db=db,
        session_id=session["session_id"],
        window_id=window.window_id,
        start_time=window.start_time,
        end_time=window.end_time,
        summary_text=summary_text,
        tools_detected=[f.get("tools", "") for f in frame_analyses],
        key_actions=[f.get("action", "") for f in frame_analyses]
    )
    
    return {
        "window_id": window.window_id,
        "start_time": window.start_time,
        "end_time": window.end_time,
        "frame_count": window.frame_count,
        "frame_analyses": frame_analyses,
        "summary": summary_text,
        "summary_id": summary.id,
        "model": model_used
    }


@router.post("/process-video")
async def process_full_video(
    session_id: str,
    background_tasks: BackgroundTasks,
    use_chinese: bool = False,
    db: Session = Depends(get_db)
):
    """Start processing entire video in background"""
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Add background task
    background_tasks.add_task(
        process_video_task,
        session_id=session_id,
        video_path=session["video_path"],
        db_session_id=session["session_id"],
        use_chinese=use_chinese
    )
    
    return {
        "message": "Processing started",
        "session_id": session_id,
        "estimated_windows": int(session["duration"] / settings.WINDOW_DURATION) + 1
    }


@router.post("/process-video-surgr1-glm")
async def process_video_with_surgr1_glm(
    request: ProcessVideoSurgR1GLMRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Start processing video with SurgR1 (frame analysis) + GLM (summary)
    
    Processing flow:
    1. For each 5-second window, extract frames
    2. Use SurgR1 to analyze each frame (tool_localization, surgical_action, surgical_phase)
    3. After SurgR1 completes for all frames in window, use GLM to summarize
    4. Stream results via SSE
    """
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Clear any previous cancellation flag
    analysis_cancellation_flags[request.session_id] = False
    
    # Add background task
    background_tasks.add_task(
        process_video_surgr1_glm_task,
        session_id=request.session_id,
        video_path=session["video_path"],
        db_session_id=session["session_id"],
        use_chinese=request.use_chinese,
        use_glm_multimodal=request.use_glm_multimodal
    )
    
    return {
        "message": "SurgR1+GLM processing started",
        "session_id": request.session_id,
        "estimated_windows": int(session["duration"] / settings.WINDOW_DURATION) + 1,
        "processing_mode": "surgr1_glm"
    }


@router.post("/stop-analysis/{session_id}")
async def stop_analysis(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Stop an ongoing video analysis task
    
    Sets a cancellation flag that the background task will check.
    The task will stop after completing the current window.
    """
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Set cancellation flag
    analysis_cancellation_flags[session_id] = True
    
    # Update session status
    from ..database import update_session_status
    update_session_status(db, session_id, "cancelled")
    
    logger.info(f"Analysis cancellation requested for session {session_id}")
    
    return {
        "message": "Analysis stop requested",
        "session_id": session_id,
        "status": "cancelling"
    }


# ==============================================================================
# Continuous SurgR1 Processing (runs in background when stream starts)
# ==============================================================================

@router.post("/start-surgr1-continuous/{session_id}")
async def start_surgr1_continuous(
    session_id: str,
    background_tasks: BackgroundTasks,
    enable_sam3: bool = True,
    db: Session = Depends(get_db)
):
    """
    Start continuous SurgR1 frame analysis for a video session.
    
    This runs in the background and continuously analyzes frames with SurgR1.
    Results are stored in the database and can be used by GLM summarization later.
    
    If enable_sam3 is True, also creates a SAM3 streaming session for
    real-time segmentation with mask propagation.
    
    Called automatically when entering stream mode.
    """
    try:
        session = get_video_session(db, session_id)
        if not session:
            return {
                "success": False,
                "message": "Session not found",
                "session_id": session_id,
                "status": "error"
            }
        
        # Check if already running
        if surgr1_continuous_flags.get(session_id, False):
            return {
                "success": True,
                "message": "SurgR1 continuous processing already running",
                "session_id": session_id,
                "status": "running",
                "sam3_enabled": session_id in sam3_streaming_sessions
            }
        
        # Initialize SAM3 streaming session if enabled
        sam3_session_id = None
        consistency_checker = None
        
        if enable_sam3:
            try:
                sam3_client = await ensure_sam3_available()
                is_healthy = await sam3_client.check_health()
                
                if is_healthy:
                    result = await sam3_client.create_stream_session(session_id)
                    if result.get("success"):
                        sam3_session_id = result.get("session_id")
                        consistency_checker = SAM3ConsistencyChecker(ConsistencyConfig(
                            forced_refresh_interval=10.0,
                            max_propagate_frames=30,
                            centroid_offset_threshold=0.3,
                            area_change_threshold=0.5
                        ))
                        sam3_streaming_sessions[session_id] = {
                            "sam3_session_id": sam3_session_id,
                            "frame_count": 0,
                            "last_update": 0,
                            "consistency_checker": consistency_checker,
                            "state": "idle"
                        }
                        logger.info(f"SAM3 streaming session created: {sam3_session_id}")
            except Exception as e:
                logger.warning(f"Failed to create SAM3 streaming session: {e}")
        
        # Mark as running
        surgr1_continuous_flags[session_id] = True
        
        # Create background task using asyncio.create_task so it can be cancelled
        # (FastAPI background_tasks cannot be cancelled)
        task = asyncio.create_task(
            surgr1_continuous_task(
                session_id=session_id,
                video_path=session["video_path"],
                db_session_id=session["session_id"],
                sam3_session_id=sam3_session_id
            )
        )
        
        # Store task reference for cancellation
        active_surgr1_tasks[session_id] = [task]
        
        logger.info(f"Started SurgR1 continuous processing for session {session_id}")
        
        import time as time_module
        return {
            "success": True,
            "message": "SurgR1 continuous processing started",
            "session_id": session_id,
            "status": "started",
            "sam3_enabled": sam3_session_id is not None,
            "sam3_session_id": sam3_session_id,
            # Server timestamp for time synchronization with frontend
            "server_time": time_module.time()
        }
        
    except Exception as e:
        logger.error(f"Error starting SurgR1 continuous: {e}")
        return {
            "success": False,
            "message": f"Failed to start: {e}",
            "session_id": session_id,
            "status": "error"
        }


@router.post("/stop-surgr1-continuous/{session_id}")
async def stop_surgr1_continuous(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Stop continuous SurgR1 frame analysis.
    
    Called when leaving stream mode or stopping the session.
    Also cleans up any active SAM3 streaming session.
    
    Note: This endpoint is lenient - returns success even if session
    doesn't exist (for page close cleanup via sendBeacon).
    """
    # Mark as stopped - do this first even if session doesn't exist
    was_running = surgr1_continuous_flags.get(session_id, False)
    surgr1_continuous_flags[session_id] = False
    
    # Clean up stream start time
    if session_id in stream_start_times:
        del stream_start_times[session_id]
    
    # Also try to close SAM3 session if still active
    sam3_info = sam3_streaming_sessions.get(session_id)
    if sam3_info:
        try:
            sam3_client = get_sam3_client()
            sam3_session_id = sam3_info.get("sam3_session_id")
            if sam3_session_id:
                await sam3_client.close_stream_session(sam3_session_id)
        except Exception as e:
            logger.warning(f"Error closing SAM3 session during stop: {e}")
        finally:
            sam3_streaming_sessions.pop(session_id, None)
            sam3_latest_frames.pop(session_id, None)
    
    logger.info(f"Stopped SurgR1 continuous processing for session {session_id}")
    
    return {
        "message": "SurgR1 continuous processing stopped",
        "session_id": session_id,
        "status": "stopped"
    }


@router.get("/surgr1-continuous-status/{session_id}")
async def get_surgr1_continuous_status(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get the status of continuous SurgR1 processing"""
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    is_running = surgr1_continuous_flags.get(session_id, False)
    
    # Get count of analyzed frames
    frames = get_frames_by_session(db, session["session_id"])
    
    # Get SAM3 streaming status
    sam3_info = sam3_streaming_sessions.get(session_id, {})
    
    return {
        "session_id": session_id,
        "is_running": is_running,
        "frames_analyzed": len(frames) if frames else 0,
        "sam3_enabled": session_id in sam3_streaming_sessions,
        "sam3_frames_processed": sam3_info.get("frame_count", 0)
    }


@router.get("/sam3/stream-frame/{session_id}")
async def get_sam3_stream_frame(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Get the latest SAM3 streamed frame for a session.
    
    This returns the most recently processed frame with SAM3 segmentation
    from the streaming pipeline. Much faster than re-processing each frame.
    
    The streaming task continuously updates sam3_latest_frames with
    segmented frames, so this endpoint just returns the cached result.
    
    Response includes:
    - image_base64: The segmented frame
    - propagated: True if mask was propagated (vs. newly generated)
    - state: "idle", "tracking", or "reinit"
    - reinit_reason: Why reinit was triggered (if applicable)
    """
    try:
        session = get_video_session(db, session_id)
        if not session:
            return {
                "success": False,
                "message": "Session not found",
                "streaming_active": False
            }
        
        latest = sam3_latest_frames.get(session_id)
        streaming_info = sam3_streaming_sessions.get(session_id, {})
        
        if not latest:
            # No SAM3 frame available yet
            return {
                "success": False,
                "message": "No SAM3 streamed frame available yet",
                "streaming_active": session_id in sam3_streaming_sessions,
                "state": streaming_info.get("state", "idle")
            }
        
        return {
            "success": True,
            "timestamp": latest.get("timestamp", 0),
            "frame_idx": latest.get("frame_idx", 0),
            "image_base64": latest.get("image_base64"),
            "num_objects": latest.get("num_objects", 0),
            "propagated": latest.get("propagated", False),
            "state": latest.get("state", "unknown"),
            "reinit_reason": latest.get("reinit_reason"),
            "age_seconds": time.time() - latest.get("updated_at", time.time())
        }
    except Exception as e:
        logger.error(f"Error in get_sam3_stream_frame: {e}")
        return {
            "success": False,
            "message": f"Server error: {str(e)}",
            "streaming_active": False
        }


@router.get("/sam3/stream-status/{session_id}")
async def get_sam3_stream_status(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get the status of SAM3 streaming for a session"""
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    sam3_info = sam3_streaming_sessions.get(session_id, {})
    latest = sam3_latest_frames.get(session_id)
    
    # Get consistency checker status if available
    consistency_status = {}
    checker = sam3_info.get("consistency_checker")
    if checker:
        consistency_status = checker.get_status()
    
    return {
        "session_id": session_id,
        "streaming_active": session_id in sam3_streaming_sessions,
        "sam3_session_id": sam3_info.get("sam3_session_id"),
        "frames_processed": sam3_info.get("frame_count", 0),
        "last_update": sam3_info.get("last_update", 0),
        "state": sam3_info.get("state", "unknown"),
        "latest_frame_timestamp": latest.get("timestamp") if latest else None,
        "latest_frame_objects": latest.get("num_objects") if latest else None,
        "consistency": consistency_status
    }


@router.post("/sam3/force-reinit/{session_id}")
async def force_sam3_reinit(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Force SAM3 streaming session to reinitialize.
    
    This manually triggers a reinit on the next key frame.
    Useful when the user notices tracking issues.
    """
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    sam3_info = sam3_streaming_sessions.get(session_id)
    if not sam3_info:
        raise HTTPException(400, "SAM3 streaming not active for this session")
    
    # Reset the consistency checker to force reinit on next check
    checker = sam3_info.get("consistency_checker")
    if checker:
        checker.reset()
        checker.state = SAM3State.REINIT
        logger.info(f"Forced SAM3 reinit for session {session_id}")
        return {
            "success": True,
            "message": "SAM3 will reinitialize on next key frame"
        }
    else:
        return {
            "success": False,
            "message": "No consistency checker available"
        }


@router.get("/sam3/consistency/{session_id}")
async def get_sam3_consistency_status(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Get detailed consistency checker status for debugging.
    
    Returns information about tracked instruments, reinit history, etc.
    """
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    sam3_info = sam3_streaming_sessions.get(session_id, {})
    checker = sam3_info.get("consistency_checker")
    
    if not checker:
        return {
            "available": False,
            "message": "No consistency checker for this session"
        }
    
    status = checker.get_status()
    
    # Add tracked instrument details
    tracked_details = []
    for obj_id, instrument in checker.tracked_instruments.items():
        tracked_details.append({
            "obj_id": obj_id,
            "label": instrument.label,
            "frames_tracked": instrument.frames_tracked,
            "last_area": instrument.last_area,
            "last_centroid": instrument.last_centroid,
            "last_bbox": instrument.last_bbox
        })
    
    return {
        "available": True,
        **status,
        "tracked_instruments": tracked_details,
        "config": {
            "centroid_offset_threshold": checker.config.centroid_offset_threshold,
            "area_change_threshold": checker.config.area_change_threshold,
            "forced_refresh_interval": checker.config.forced_refresh_interval,
            "max_propagate_frames": checker.config.max_propagate_frames
        }
    }


async def surgr1_continuous_task(
    session_id: str,
    video_path: str,
    db_session_id: int,
    sam3_session_id: Optional[str] = None
):
    """
    Background task for continuous SurgR1 frame analysis with SAM3 streaming.
    
    Continuously reads frames from video/stream and:
    1. Analyzes key frames with SurgR1 (every 1 second)
    2. Uses SAM3 to generate segmentation masks
    3. Propagates masks to intermediate frames with SAM3
    4. Uses consistency checker to detect when reinit is needed
    
    This implements the real-time streaming approach from:
    https://github.com/matteo-tafuro/sam3-realtime
    """
    import cv2
    import time as time_module
    
    db = next(get_db())
    sam3_client = None
    consistency_checker = None
    cap = None  # Initialize cap outside try block for proper cleanup
    frame_capture_task = None  # Initialize frame capture task for proper cleanup
    
    try:
        surgr1_client = await ensure_surgr1_available()
        
        # Get SAM3 client and consistency checker if session exists
        if sam3_session_id and session_id in sam3_streaming_sessions:
            try:
                sam3_client = await ensure_sam3_available()
                consistency_checker = sam3_streaming_sessions[session_id].get("consistency_checker")
            except Exception as e:
                logger.warning(f"SAM3 client not available: {e}")
                sam3_client = None
        
        # Open video/stream
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error(f"Cannot open video: {video_path}")
            surgr1_continuous_flags[session_id] = False
            return
        
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        is_realtime_stream = video_path.startswith('http')
        
        # For realtime streams, use wall clock time instead of frame-based time
        # This ensures timestamps match the frontend's elapsed time
        import time as time_module
        stream_start_time = time_module.time() if is_realtime_stream else None
        
        # Store the start time for synchronization with frontend
        if is_realtime_stream:
            stream_start_times[session_id] = stream_start_time
            logger.info(f"Stream start time recorded for {session_id}: {stream_start_time}")
        
        surgr1_interval = 1.0  # SurgR1 analyzes one frame per second
        sam3_interval = 0.1  # SAM3 propagates masks at 10 FPS (propagation is very fast)
        last_surgr1_time = -surgr1_interval  # Ensure first frame is analyzed
        last_sam3_time = 0
        frame_idx = 0
        
        # Start a separate frame capture task for high-FPS saving
        frame_capture_task = asyncio.create_task(
            frame_capture_for_playback(session_id, video_path, is_realtime_stream, stream_start_time)
        )
        
        # Store last known bboxes for SAM3 propagation
        last_bboxes = []
        last_tool_localization = ""
        
        # Track SAM3 initialization state - only reinit when instruments change
        sam3_initialized = False
        sam3_tracked_instruments = set()  # Set of instrument labels being tracked
        
        logger.info(f"SurgR1 continuous task started for {session_id} (SAM3: {sam3_session_id is not None}, realtime_stream: {is_realtime_stream})")
        
        while surgr1_continuous_flags.get(session_id, False):
            ret, bgr_frame = cap.read()
            
            if not ret:
                # For streams, wait and retry; for files, loop
                if is_realtime_stream:
                    await asyncio.sleep(0.1)
                    continue
                else:
                    # End of file - restart from beginning for continuous processing
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    frame_idx = 0
                    last_surgr1_time = -surgr1_interval
                    if consistency_checker:
                        consistency_checker.reset()
                    continue
            
            # For realtime streams, use actual elapsed time (wall clock)
            # For local videos, use frame-based time calculation
            if is_realtime_stream:
                current_time = time_module.time() - stream_start_time
            else:
                current_time = frame_idx / fps
            
            # Convert to PIL Image
            rgb_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
            from PIL import Image
            pil_image = Image.fromarray(rgb_frame)
            
            # Determine if this is a SurgR1 key frame
            is_surgr1_frame = (current_time - last_surgr1_time >= surgr1_interval)
            
            if is_surgr1_frame:
                last_surgr1_time = current_time
                
                try:
                    # Analyze with SurgR1
                    result = await surgr1_client.analyze_frame(
                        image=pil_image,
                        analysis_type="all",
                        session_id=session_id,
                        frame_idx=frame_idx,
                        timestamp=current_time,
                        save_to_mysql=True
                    )
                    
                    # Get storage path for this session from MySQL
                    # (Frames are already saved at higher FPS above, so we just get the path)
                    mysql_service = get_mysql_service()
                    video_session = mysql_service.get_video_session(session_id)
                    storage_path = video_session.get("storage_path") if video_session else None
                    
                    # Frames are already saved by the frame-saving logic above
                    # Just mark that this analysis has an associated frame
                    image_saved = 1 if storage_path else 0
                    image_path = None  # Frame path is derived from timestamp in storage folder
                    
                    # Save analysis to MySQL database
                    mysql_service.save_analysis(
                        session_id=session_id,
                        frame_idx=frame_idx,
                        timestamp=current_time,
                        analysis_type="frame",
                        tool_localization=result.get("tools", ""),
                        surgical_action=result.get("action", ""),
                        surgical_phase=result.get("phase", ""),
                        image_path=image_path,
                        image_saved=image_saved
                    )
                    
                    # Also save to in-memory database (backward compat)
                    create_frame_analysis(
                        db=db,
                        session_id=db_session_id,
                        frame_idx=frame_idx,
                        timestamp=current_time,
                        tool_localization=result.get("tools", ""),
                        surgical_action=result.get("action", ""),
                        surgical_phase=result.get("phase", "")
                    )
                    
                    # Update last known bboxes for SAM3
                    last_tool_localization = result.get("tools", "")
                    last_bboxes = parse_bboxes_from_surgr1(last_tool_localization)
                    
                    # 使用 INFO 级别以确保能看到日志
                    logger.info(f"[SurgR1] Frame {frame_idx} at {current_time:.1f}s, found {len(last_bboxes)} bboxes: {last_bboxes}")
                    
                except Exception as e:
                    logger.warning(f"SurgR1 analysis failed for frame {frame_idx}: {e}")
            
            # SAM3 streaming: process frame with masks
            # Key insight: Only reinit SAM3 when instruments change, otherwise just propagate
            if sam3_client and sam3_session_id and (current_time - last_sam3_time >= sam3_interval):
                last_sam3_time = current_time
                
                try:
                    need_reinit = False
                    reinit_reason = None
                    sam3_result = None
                    
                    # Extract current instrument labels from bboxes
                    current_instruments = set()
                    for bbox in last_bboxes:
                        label = bbox.get("label", "unknown")
                        current_instruments.add(label)
                    
                    # 调试：输出 SAM3 处理状态
                    logger.info(f"[SAM3] Processing: initialized={sam3_initialized}, bboxes={len(last_bboxes)}, instruments={current_instruments}")
                    
                    # Check if we need to reinitialize SAM3
                    if not sam3_initialized and last_bboxes:
                        # First time seeing instruments - initialize
                        need_reinit = True
                        reinit_reason = "first_detection"
                    elif is_surgr1_frame and last_bboxes:
                        # Check if instruments changed (new instruments appeared)
                        new_instruments = current_instruments - sam3_tracked_instruments
                        if new_instruments:
                            need_reinit = True
                            reinit_reason = f"new_instruments: {new_instruments}"
                        # Check if instrument count changed significantly
                        elif len(current_instruments) != len(sam3_tracked_instruments):
                            need_reinit = True
                            reinit_reason = f"count_changed: {len(sam3_tracked_instruments)} -> {len(current_instruments)}"
                        # Also check consistency checker if available
                        elif consistency_checker:
                            decision = consistency_checker.check(
                                current_time=current_time,
                                surgr1_bboxes=last_bboxes,
                                sam3_masks=None
                            )
                            if decision.need_reinit:
                                need_reinit = True
                                reinit_reason = decision.reason
                    
                    # Determine what to send to SAM3
                    if need_reinit and last_bboxes:
                        # Need to reinitialize - close old session and create new
                        logger.info(f"SAM3 reinit triggered: {reinit_reason}")
                        
                        # Close old session if exists
                        if sam3_initialized:
                            try:
                                await sam3_client.close_stream_session(sam3_session_id)
                            except:
                                pass
                        
                        # Create new session
                        new_session_result = await sam3_client.create_stream_session(session_id)
                        if new_session_result.get("success"):
                            sam3_session_id = new_session_result.get("session_id")
                            sam3_streaming_sessions[session_id]["sam3_session_id"] = sam3_session_id
                            
                            # Process frame with bboxes (initialization)
                            # 调试：记录发送给 SAM3 的 bboxes
                            logger.info(f"[SAM3] Initializing with {len(last_bboxes)} bboxes: {last_bboxes}")
                            
                            sam3_result = await sam3_client.process_stream_frame(
                                session_id=sam3_session_id,
                                frame=pil_image,
                                frame_idx=frame_idx,
                                timestamp=current_time,
                                bboxes=last_bboxes
                            )
                            
                            # 调试：记录 SAM3 返回结果
                            logger.info(f"[SAM3] Init result: success={sam3_result.get('success')}, "
                                       f"num_objects={sam3_result.get('num_objects', 0)}, "
                                       f"has_image={bool(sam3_result.get('image_base64'))}")
                            
                            if sam3_result.get("success"):
                                sam3_initialized = True
                                sam3_tracked_instruments = current_instruments.copy()
                                logger.info(f"[SAM3] Initialized successfully, tracking: {sam3_tracked_instruments}")
                            else:
                                logger.warning(f"[SAM3] Initialization failed: {sam3_result.get('error', 'unknown')}")
                            
                            # Update consistency checker
                            if consistency_checker:
                                consistency_checker.update_after_reinit(
                                    current_time=current_time,
                                    bboxes=last_bboxes,
                                    sam3_result=sam3_result
                                )
                        else:
                            logger.error("Failed to create new SAM3 session")
                            continue
                    
                    elif sam3_initialized:
                        # Already initialized - just propagate masks (FAST!)
                        sam3_result = await sam3_client.process_stream_frame(
                            session_id=sam3_session_id,
                            frame=pil_image,
                            frame_idx=frame_idx,
                            timestamp=current_time,
                            bboxes=None  # Propagate only
                        )
                        
                        # Update consistency checker for propagation
                        if consistency_checker:
                            consistency_checker.update_after_propagate(
                                current_time=current_time,
                                sam3_masks=None
                            )
                    
                    # Store result if successful
                    if sam3_result:
                        success = sam3_result.get("success", False)
                        has_image = bool(sam3_result.get("image_base64"))
                        num_objects = sam3_result.get("num_objects", 0)
                        propagated = sam3_result.get("propagated", False)
                        
                        # 调试日志：详细记录 SAM3 结果
                        if success and has_image:
                            logger.debug(f"[SAM3] Frame {frame_idx}: success, {num_objects} objects, propagated={propagated}")
                            
                            # Store latest SAM3 frame for frontend access
                            sam3_latest_frames[session_id] = {
                                "timestamp": current_time,
                                "frame_idx": frame_idx,
                                "image_base64": sam3_result["image_base64"],
                                "num_objects": num_objects,
                                "propagated": propagated,
                                "reinit_reason": reinit_reason,
                                "state": consistency_checker.state.value if consistency_checker else "unknown",
                                "updated_at": time_module.time()
                            }
                            
                            # Update streaming session info
                            if session_id in sam3_streaming_sessions:
                                sam3_streaming_sessions[session_id]["frame_count"] += 1
                                sam3_streaming_sessions[session_id]["last_update"] = time_module.time()
                                sam3_streaming_sessions[session_id]["state"] = \
                                    consistency_checker.state.value if consistency_checker else "unknown"
                        else:
                            # 调试：记录失败原因
                            error_msg = sam3_result.get("error", "unknown")
                            logger.warning(f"[SAM3] Frame {frame_idx}: failed - success={success}, has_image={has_image}, num_objects={num_objects}, error={error_msg}")
                    else:
                        logger.warning(f"[SAM3] Frame {frame_idx}: sam3_result is None")
                            
                except Exception as e:
                    logger.error(f"[SAM3] Frame {frame_idx} exception: {e}")
                    import traceback
                    traceback.print_exc()
            
            frame_idx += 1
            
            # Small delay to prevent CPU overload
            await asyncio.sleep(0.01)
        
        cap.release()
        logger.info(f"SurgR1 continuous task stopped for {session_id}")
    
    except asyncio.CancelledError:
        logger.info(f"SurgR1 continuous task cancelled for {session_id}")
        # Let the finally block handle cleanup
        
    except Exception as e:
        logger.error(f"SurgR1 continuous task error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        surgr1_continuous_flags[session_id] = False
        
        # Cancel frame capture task
        if frame_capture_task and not frame_capture_task.done():
            frame_capture_task.cancel()
            try:
                await frame_capture_task
            except asyncio.CancelledError:
                logger.info(f"Frame capture task cancelled for session {session_id}")
        
        # Clean up task reference
        active_surgr1_tasks.pop(session_id, None)
        
        # CRITICAL: Release video capture to free stream connection
        if cap is not None:
            try:
                cap.release()
                logger.info(f"Released video capture for session {session_id}")
            except Exception as e:
                logger.warning(f"Error releasing video capture: {e}")
        
        # Clean up SAM3 streaming session
        if sam3_client and sam3_session_id:
            try:
                await sam3_client.close_stream_session(sam3_session_id)
            except Exception as e:
                logger.warning(f"Error closing SAM3 session: {e}")
        
        # Clean up stored data
        sam3_streaming_sessions.pop(session_id, None)
        sam3_latest_frames.pop(session_id, None)
        
        db.close()


# ==============================================================================
# GLM-only Summarization (uses existing SurgR1 results)
# ==============================================================================

class GLMSummarizeRequest(BaseModel):
    """Request for GLM-only summarization"""
    session_id: str
    use_chinese: bool = True
    use_glm_multimodal: bool = False


@router.post("/start-glm-summarization")
async def start_glm_summarization(
    request: GLMSummarizeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Start GLM summarization using existing SurgR1 frame analysis results.
    
    This is called when user clicks "开始分析". It uses the SurgR1 results
    that have been continuously collected in the background.
    """
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Check if GLM is available
    try:
        glm_client = await ensure_glm_available()
        is_healthy = await glm_client.check_health()
        if not is_healthy:
            raise HTTPException(503, "GLM service not available")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"GLM service error: {e}")
    
    # Update session status to processing
    from ..database import update_session_status
    update_session_status(db, request.session_id, "processing")
    
    # Clear any previous cancellation flag
    analysis_cancellation_flags[request.session_id] = False
    
    # Get current frame count
    frames = get_frames_by_session(db, session["session_id"])
    frame_count = len(frames) if frames else 0
    
    # Start background task using asyncio.create_task for proper async execution
    # This ensures the task runs in the background and continues processing
    asyncio.create_task(
        glm_summarization_task(
            session_id=request.session_id,
            video_path=session["video_path"],
            db_session_id=session["session_id"],
            use_chinese=request.use_chinese,
            use_glm_multimodal=request.use_glm_multimodal
        )
    )
    
    logger.info(f"[GLM] Started background task for session {request.session_id}")
    
    return {
        "message": "GLM summarization started",
        "session_id": request.session_id,
        "processing_mode": "glm_only",
        "frames_available": frame_count,
        "surgr1_running": surgr1_continuous_flags.get(request.session_id, False)
    }


async def glm_summarization_task(
    session_id: str,
    video_path: str,
    db_session_id: str,  # session_id string, not int
    use_chinese: bool = True,
    use_glm_multimodal: bool = False
):
    """
    Background task for GLM summarization using existing SurgR1 results.
    
    Groups SurgR1 frame analyses into 5-second windows and generates
    summaries using GLM.
    """
    from ..database import update_session_status
    db = next(get_db())
    
    try:
        glm_client = await ensure_glm_available()
        
        processor = VideoProcessor(
            video_path=video_path,
            window_duration=settings.WINDOW_DURATION,
            sample_interval=settings.SAMPLE_INTERVAL
        )
        
        # Wait for SurgR1 results if none exist yet (up to 30 seconds)
        all_frames = None
        wait_count = 0
        max_wait = 30  # seconds
        
        while wait_count < max_wait:
            # Check cancellation
            if analysis_cancellation_flags.get(session_id, False):
                logger.info(f"GLM summarization cancelled while waiting for SurgR1")
                update_session_status(db, session_id, "cancelled")
                return
                
            all_frames = get_frames_by_session(db, db_session_id)
            if all_frames and len(all_frames) > 0:
                break
            
            logger.info(f"Waiting for SurgR1 results... ({wait_count}s)")
            await asyncio.sleep(1)
            wait_count += 1
            db.expire_all()  # Refresh database cache
        
        if not all_frames or len(all_frames) == 0:
            logger.warning(f"No SurgR1 results available for session {session_id} after waiting")
            # Create a placeholder summary to inform user
            create_window_summary(
                db=db,
                session_id=db_session_id,
                window_id=0,
                start_time=0,
                end_time=5,
                summary_text="⚠️ 等待 SurgR1 分析帧... 请确保 SurgR1 服务正在运行。",
                tools_detected=[],
                key_actions=[]
            )
            update_session_status(db, session_id, "completed")
            return
        
        # Group frames by window
        window_frames = {}
        for frame in all_frames:
            # Handle both dict and object access patterns, ensure ts is not None
            ts = frame.get("timestamp") if isinstance(frame, dict) else getattr(frame, "timestamp", None)
            ts = ts if ts is not None else 0  # Ensure ts is never None
            window_id = int(ts / settings.WINDOW_DURATION)
            if window_id not in window_frames:
                window_frames[window_id] = []
            window_frames[window_id].append(frame)
        
        logger.info(f"Processing {len(window_frames)} windows with GLM for session {session_id}")
        
        # Chinese system prompt for surgical video analysis
        CHINESE_SYSTEM_PROMPT = """你是一位专业的腹腔镜胆囊切除术视频分析专家。你将收到一个5秒视频窗口的逐帧分析结果。请将这些分析整合成一个简洁的中文叙述摘要。

## 手术阶段
- Preparation(准备)
- CalotTriangleDissection(Calot三角分离)
- ClippingCutting(夹闭切断)
- GallbladderDissection(胆囊分离)
- GallbladderRetraction(胆囊牵拉)
- CleaningCoagulation(清洁止血)
- GallbladderPackaging(胆囊取出)

## 关键解剖结构
胆囊管、胆囊动脉、胆囊、Calot三角、胆囊板

## 手术器械
抓钳、电钩、剪刀、施夹器、冲洗器、双极电凝

## 你的任务
根据多帧分析结果，用2-4句中文描述：
1. 当前手术阶段和主要操作
2. 使用的器械及操作方式
3. 重要观察发现

请务必使用中文回答！"""
        
        # Track processed windows to continue from where we left off
        processed_windows = set()
        last_window_id = -1
        max_wait_for_new_frames = 120  # Wait up to 120 seconds for new frames
        no_new_frames_count = 0
        loop_count = 0
        
        logger.info(f"[GLM Task] Starting continuous summarization for session {session_id}")
        
        while True:
            loop_count += 1
            
            # Check cancellation flag
            if analysis_cancellation_flags.get(session_id, False):
                logger.info(f"[GLM Task] Cancelled for session {session_id}")
                update_session_status(db, session_id, "cancelled")
                return
            
            # Refresh frames from database
            try:
                all_frames = get_frames_by_session(db, db_session_id)
            except Exception as e:
                logger.error(f"[GLM Task] Failed to get frames: {e}")
                await asyncio.sleep(2)
                continue
            
            # Rebuild window_frames with new data
            window_frames = {}
            for frame in all_frames:
                # Ensure ts is never None
                ts = frame.get("timestamp") if isinstance(frame, dict) else getattr(frame, "timestamp", None)
                ts = ts if ts is not None else 0
                window_id = int(ts / settings.WINDOW_DURATION)
                if window_id not in window_frames:
                    window_frames[window_id] = []
                window_frames[window_id].append(frame)
            
            # Find new windows to process (only windows with at least 2 frames)
            new_windows = [
                wid for wid in sorted(window_frames.keys()) 
                if wid not in processed_windows and len(window_frames[wid]) >= 2
            ]
            
            # Log status every 10 loops
            if loop_count % 10 == 1:
                logger.info(f"[GLM Task] Loop {loop_count}: {len(all_frames)} frames, windows={list(window_frames.keys())}, processed={processed_windows}, new={new_windows}")
            
            if not new_windows:
                no_new_frames_count += 1
                if no_new_frames_count >= max_wait_for_new_frames:
                    logger.info(f"[GLM Task] No new frames for {max_wait_for_new_frames}s, stopping for session {session_id}")
                    break
                await asyncio.sleep(1)
                continue
            
            no_new_frames_count = 0  # Reset counter when we have new windows
            
            # ========== 动态批处理：根据积压窗口数调整并发数 ==========
            # 积压多 → 并发数大；积压少 → 并发数小
            glm_max_concurrent = min(len(new_windows), settings.GLM_MAX_CONCURRENT if hasattr(settings, 'GLM_MAX_CONCURRENT') else 16)
            glm_max_concurrent = max(1, glm_max_concurrent)  # 至少 1
            
            logger.info(f"[GLM Task] Processing {len(new_windows)} new windows with {glm_max_concurrent} concurrent: {new_windows}")
            
            # ========== 1. 准备所有窗口数据 ==========
            windows_to_process = []
            window_metadata = {}
            
            from ..services.temporal_analyze import process_window_for_glm
            
            for window_id in new_windows:
                if analysis_cancellation_flags.get(session_id, False):
                    break
                    
                frames = window_frames[window_id]
                start_time = window_id * settings.WINDOW_DURATION
                end_time = start_time + settings.WINDOW_DURATION
                
                # Build frame analyses for GLM
                frame_analyses = []
                for f in frames:
                    if isinstance(f, dict):
                        frame_analyses.append({
                            "frame_idx": f.get("frame_idx", 0),
                            "timestamp": f.get("timestamp", 0),
                            "phase": f.get("surgical_phase", "") or "",
                            "action": f.get("surgical_action", "") or "",
                            "tools": f.get("tool_localization", "") or ""
                        })
                    else:
                        frame_analyses.append({
                            "frame_idx": getattr(f, "frame_idx", 0),
                            "timestamp": getattr(f, "timestamp", 0),
                            "phase": getattr(f, "surgical_phase", "") or "",
                            "action": getattr(f, "surgical_action", "") or "",
                            "tools": getattr(f, "tool_localization", "") or ""
                        })
                
                # Temporal Analysis
                temporal_result = process_window_for_glm(
                    frame_analyses=frame_analyses,
                    window_id=window_id,
                    window_duration=settings.WINDOW_DURATION
                )
                consistency = temporal_result.get("consistency", {})
                
                logger.info(f"[Temporal] Window {window_id}: {consistency.get('cleaned_data', {})}")
                
                # 添加到并发处理列表
                windows_to_process.append({
                    "window_id": window_id,
                    "frame_analyses": frame_analyses,
                    "images": None  # GLM只使用文本输入
                })
                
                # 存储元数据
                window_metadata[window_id] = {
                    "start_time": start_time,
                    "end_time": end_time,
                    "frame_analyses": frame_analyses,
                    "consistency": consistency
                }
            
            # ========== 2. 并发处理所有窗口 ==========
            if windows_to_process:
                try:
                    import time as time_module
                    batch_start = time_module.time()
                    
                    # 使用 GLM 并发摘要
                    glm_results = await glm_client.summarize_windows_concurrent(
                        windows=windows_to_process,
                        max_concurrent=glm_max_concurrent
                    )
                    
                    batch_elapsed = time_module.time() - batch_start
                    
                    # 保存结果到数据库
                    for result in glm_results:
                        window_id = result.get("window_id")
                        meta = window_metadata.get(window_id, {})
                        
                        if result.get("success"):
                            summary_text = result.get("summary", "")
                        else:
                            summary_text = f"[分析出错: {result.get('error', '未知错误')}]"
                        
                        # Extract dominant phase from temporal analysis
                        cleaned_data = meta.get("consistency", {}).get("cleaned_data", {})
                        dominant_phase = cleaned_data.get("phase", "Unknown")
                        tools_detected = cleaned_data.get("tools", [])
                        frame_analyses = meta.get("frame_analyses", [])
                        
                        # Save summary to database
                        create_window_summary(
                            db=db,
                            session_id=db_session_id,
                            window_id=window_id,
                            start_time=meta.get("start_time", 0),
                            end_time=meta.get("end_time", 0),
                            summary_text=summary_text,
                            dominant_phase=dominant_phase,
                            tools_detected=tools_detected,
                            key_actions=[f.get("action", "")[:200] for f in frame_analyses[:3]]
                        )
                        
                        processed_windows.add(window_id)
                    
                    logger.info(
                        f"[GLM Task] Batch completed: {len(glm_results)} windows in {batch_elapsed:.2f}s "
                        f"({len(glm_results)/batch_elapsed:.1f} windows/s, concurrent={glm_max_concurrent})"
                    )
                    
                except Exception as e:
                    logger.error(f"GLM concurrent summarization failed: {e}")
                    # 回退到串行处理
                    for window_data in windows_to_process:
                        window_id = window_data["window_id"]
                        meta = window_metadata.get(window_id, {})
                        
                        try:
                            result = await glm_client.integrate_analysis_results(
                                frame_analyses=window_data["frame_analyses"],
                                images=None
                            )
                            summary_text = result.get("summary", "") if result.get("success") else "[分析出错]"
                        except Exception as inner_e:
                            summary_text = f"[分析出错: {str(inner_e)}]"
                        
                        cleaned_data = meta.get("consistency", {}).get("cleaned_data", {})
                        create_window_summary(
                            db=db,
                            session_id=db_session_id,
                            window_id=window_id,
                            start_time=meta.get("start_time", 0),
                            end_time=meta.get("end_time", 0),
                            summary_text=summary_text,
                            dominant_phase=cleaned_data.get("phase", "Unknown"),
                            tools_detected=cleaned_data.get("tools", []),
                            key_actions=[]
                        )
                        processed_windows.add(window_id)
            
            # Small delay before checking for more frames
            await asyncio.sleep(2)
        
        update_session_status(db, session_id, "completed")
        logger.info(f"GLM summarization completed for session {session_id}")
        
    except Exception as e:
        import traceback
        logger.error(f"GLM summarization task error: {e}")
        logger.error(f"GLM task traceback: {traceback.format_exc()}")
        update_session_status(db, session_id, "error")
    finally:
        # Clean up cancellation flag
        analysis_cancellation_flags.pop(session_id, None)
        # Clean up session history and conflict resolver resources
        cleanup_session_resources(session_id)
        db.close()


async def process_video_surgr1_glm_task(
    session_id: str,
    video_path: str,
    db_session_id: int,
    use_chinese: bool = False,
    use_glm_multimodal: bool = False
):
    """Background task to process video with SurgR1 + GLM"""
    
    from ..database import update_session_status
    db = next(get_db())
    
    try:
        processor = VideoProcessor(
            video_path=video_path,
            window_duration=settings.WINDOW_DURATION,
            sample_interval=settings.SAMPLE_INTERVAL
        )
        
        # Get SurgR1 and GLM clients
        surgr1_client = await ensure_surgr1_available()
        glm_client = await ensure_glm_available()
        
        async for window in processor.process_stream():
            # Check cancellation flag at the start of each window
            if analysis_cancellation_flags.get(session_id, False):
                logger.info(f"Analysis cancelled for session {session_id} at window {window.window_id}")
                update_session_status(db, session_id, "cancelled")
                return
            # ==================================================================
            # Step 1: SurgR1 - Batch analyze all frames in window
            # ==================================================================
            # Prepare batch request - collect all frames
            batch_frames = [
                {
                    "image": frame.image,
                    "frame_idx": frame.frame_idx,
                    "timestamp": frame.timestamp
                }
                for frame in window.frames
            ]
            
            try:
                # Single batch API call for all frames in window
                frame_analyses = await surgr1_client.analyze_frames_batch(
                    frames=batch_frames,
                    analysis_type="all",
                    session_id=session_id,
                    save_to_mysql=True
                )
                
                # Save to SQLite database
                for result in frame_analyses:
                    create_frame_analysis(
                        db=db,
                        session_id=db_session_id,
                        frame_idx=result.get("frame_idx"),
                        timestamp=result.get("timestamp"),
                        tool_localization=result.get("tools", ""),
                        surgical_action=result.get("action", ""),
                        surgical_phase=result.get("phase", "")
                    )
                    
                logger.info(f"Batch analyzed {len(frame_analyses)} frames in window {window.window_id}")
                
            except Exception as e:
                logger.warning(f"SurgR1 batch analysis failed for window {window.window_id}: {e}")
                # Fallback: create empty analyses
                frame_analyses = [
                    {
                        "frame_idx": frame.frame_idx,
                        "timestamp": frame.timestamp,
                        "phase": "",
                        "action": "",
                        "tools": ""
                    }
                    for frame in window.frames
                ]
            
            # ==================================================================
            # Step 2: GLM - Summarize window (纯文本输入，全中文输出)
            # ==================================================================
            try:
                # GLM只使用文本输入，完全按照temporal_analyze.py的逻辑
                # 输出强制为中文，无需额外处理
                result = await glm_client.integrate_analysis_results(
                    frame_analyses=frame_analyses,
                    images=None,  # GLM只使用文本输入
                    system_prompt=None,  # 使用内置的全中文提示词
                    temperature=0.7,
                    max_tokens=1500
                )
                
                if result.get("success"):
                    summary_text = result.get("summary", "")
                else:
                    summary_text = f"[分析出错: {result.get('error', '未知错误')}]"
                    
            except Exception as e:
                logger.error(f"GLM summarization failed for window {window.window_id}: {e}")
                summary_text = f"[GLM Error: {str(e)}]"
            
            # ==================================================================
            # Step 3: Save summary to database
            # ==================================================================
            create_window_summary(
                db=db,
                session_id=db_session_id,
                window_id=window.window_id,
                start_time=window.start_time,
                end_time=window.end_time,
                summary_text=summary_text,
                tools_detected=[f.get("tools", "")[:200] for f in frame_analyses],
                key_actions=[f.get("action", "")[:200] for f in frame_analyses]
            )
            
            logger.info(f"Completed window {window.window_id} with SurgR1+GLM")
        
        # Update session status
        update_session_status(db, session_id, "completed")
        
    except Exception as e:
        logger.error(f"SurgR1+GLM processing failed: {e}")
        update_session_status(db, session_id, "error")
        raise
    finally:
        # Clean up cancellation flag
        analysis_cancellation_flags.pop(session_id, None)
        db.close()


@router.get("/frame-analysis/{session_id}")
async def get_frame_analysis(
    session_id: str,
    timestamp: float = Query(..., ge=0, description="Frame timestamp"),
    db: Session = Depends(get_db)
):
    """
    Get SurgR1 analysis for a specific frame (nearest to timestamp)
    
    This is used when user drags the progress bar to show single-frame analysis.
    """
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Get all frame analyses for this session
    frames = get_frames_by_session(db, session["session_id"])
    
    if not frames:
        return {
            "found": False,
            "message": "No frame analyses available yet",
            "timestamp": timestamp
        }
    
    # Find nearest frame to the requested timestamp (frames is a list of dicts)
    nearest_frame = min(frames, key=lambda f: abs(f["timestamp"] - timestamp))
    
    # Only return if within 1 second of requested time
    if abs(nearest_frame["timestamp"] - timestamp) > 1.0:
        return {
            "found": False,
            "message": "No frame analysis near this timestamp",
            "timestamp": timestamp,
            "nearest_timestamp": nearest_frame["timestamp"]
        }
    
    return {
        "found": True,
        "frame_idx": nearest_frame["frame_idx"],
        "timestamp": nearest_frame["timestamp"],
        "tool_localization": nearest_frame.get("tool_localization") or "",
        "surgical_action": nearest_frame.get("surgical_action") or "",
        "surgical_phase": nearest_frame.get("surgical_phase") or "",
        "window_id": int(nearest_frame["timestamp"] / settings.WINDOW_DURATION)
    }


@router.post("/analyze-single-frame")
async def analyze_single_frame(
    session_id: str,
    timestamp: float = Query(..., ge=0, description="Frame timestamp"),
    db: Session = Depends(get_db)
):
    """
    Analyze a single frame with SurgR1 on-demand
    
    This is used when user clicks on a specific frame and wants fresh analysis.
    """
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Create video processor
    processor = VideoProcessor(
        video_path=session["video_path"],
        window_duration=settings.WINDOW_DURATION,
        sample_interval=settings.SAMPLE_INTERVAL
    )
    
    # Extract single frame
    frame = processor.extract_frame(timestamp)
    
    if frame is None:
        raise HTTPException(400, f"Could not extract frame at timestamp {timestamp}")
    
    # Get SurgR1 client and analyze
    try:
        surgr1_client = await ensure_surgr1_available()
        
        result = await surgr1_client.analyze_frame(
            image=frame.image,
            analysis_type="all",
            session_id=session_id,
            frame_idx=frame.frame_idx,
            timestamp=frame.timestamp,
            save_to_mysql=True
        )
        
        # Save to SQLite database
        create_frame_analysis(
            db=db,
            session_id=session["session_id"],
            frame_idx=frame.frame_idx,
            timestamp=frame.timestamp,
            tool_localization=result.get("tools", ""),
            surgical_action=result.get("action", ""),
            surgical_phase=result.get("phase", "")
        )
        
        return {
            "success": True,
            "frame_idx": frame.frame_idx,
            "timestamp": frame.timestamp,
            "tool_localization": result.get("tools", ""),
            "surgical_action": result.get("action", ""),
            "surgical_phase": result.get("phase", ""),
            "window_id": int(timestamp / settings.WINDOW_DURATION)
        }
        
    except Exception as e:
        logger.error(f"Single frame analysis failed: {e}")
        raise HTTPException(500, f"Analysis failed: {str(e)}")


async def process_video_task(
    session_id: str,
    video_path: str,
    db_session_id: int,
    use_chinese: bool = False
):
    """Background task to process entire video"""
    
    db = next(get_db())
    
    try:
        processor = VideoProcessor(
            video_path=video_path,
            window_duration=settings.WINDOW_DURATION,
            sample_interval=settings.SAMPLE_INTERVAL
        )
        
        summarizer = get_gpt_summarizer()
        summarizer.use_chinese = use_chinese
        
        async for window in processor.process_stream():
            # Build context
            context = build_frame_context(window)
            
            # Generate summary
            result = await summarizer.summarize_window(
                images=window.get_images(),
                context=context,
                system_prompt=ANALYSIS_SYSTEM_PROMPT
            )
            
            if result["success"]:
                # Save frame analyses
                for frame in window.frames:
                    create_frame_analysis(
                        db=db,
                        session_id=db_session_id,
                        frame_idx=frame.frame_idx,
                        timestamp=frame.timestamp
                    )
                
                # Save summary
                create_window_summary(
                    db=db,
                    session_id=db_session_id,
                    window_id=window.window_id,
                    start_time=window.start_time,
                    end_time=window.end_time,
                    summary_text=result["summary"]
                )
        
        # Update session status
        from ..database import update_session_status
        update_session_status(db, session_id, "completed")
        
    except Exception as e:
        from ..database import update_session_status
        update_session_status(db, session_id, "error")
        raise
    finally:
        db.close()


@router.get("/summaries/{session_id}")
async def get_session_summaries(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get all summaries for a session"""
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    summaries = get_summaries_by_session(db, session["session_id"])
    
    return [
        {
            "window_id": s.window_id,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "summary": s.summary_text,
            "tts_audio_path": s.tts_audio_path
        }
        for s in summaries
    ]


@router.get("/summary-at/{session_id}")
async def get_summary_at_time(
    session_id: str,
    timestamp: float = Query(..., ge=0),
    db: Session = Depends(get_db)
):
    """Get summary for a specific timestamp"""
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    summary = get_summary_for_timestamp(db, session["session_id"], timestamp)
    
    if summary:
        return {
            "window_id": summary.window_id,
            "start_time": summary.start_time,
            "end_time": summary.end_time,
            "summary": summary.summary_text,
            "tts_audio_path": summary.tts_audio_path
        }
    else:
        return {
            "window_id": None,
            "start_time": None,
            "end_time": None,
            "summary": None,
            "message": "No summary available for this timestamp"
        }


@router.get("/stream-summaries/{session_id}")
async def stream_summaries(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Stream summaries as they are generated (SSE)"""
    from ..database import get_session_record
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    async def event_generator():
        last_window_id = -1
        max_iterations = 600  # Max 10 minutes (600 * 1s)
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            
            try:
                summaries = get_summaries_by_session(db, session["session_id"])
                
                for s in summaries:
                    # Handle both dict and object access
                    s_window_id = s.get("window_id", 0) if isinstance(s, dict) else getattr(s, "window_id", 0)
                    if s_window_id is not None and s_window_id > last_window_id:
                        s_start = s.get("window_start", 0) if isinstance(s, dict) else getattr(s, "start_time", 0)
                        s_end = s.get("window_end", 0) if isinstance(s, dict) else getattr(s, "end_time", 0)
                        s_summary = s.get("glm_summary", "") if isinstance(s, dict) else getattr(s, "summary_text", "")
                        
                        data = json.dumps({
                            "window_id": s_window_id,
                            "start_time": s_start,
                            "end_time": s_end,
                            "summary": s_summary
                        })
                        yield f"data: {data}\n\n"
                        last_window_id = s_window_id
                
                # Check if processing is complete or cancelled - reload session from cache/DB
                current_session = get_session_record(session_id)
                if current_session:
                    status = current_session.get("status", "")
                    if status == "completed":
                        yield f"data: {json.dumps({'status': 'completed'})}\n\n"
                        break
                    elif status == "cancelled":
                        yield f"data: {json.dumps({'status': 'cancelled'})}\n\n"
                        break
                
                # Check cancellation flag
                if analysis_cancellation_flags.get(session_id, False):
                    yield f"data: {json.dumps({'status': 'cancelled'})}\n\n"
                    break
                    
            except Exception as e:
                logger.warning(f"[SSE] Error in event_generator: {e}")
            
            await asyncio.sleep(1)
        
        # Send final completed message if we hit max iterations
        if iteration >= max_iterations:
            yield f"data: {json.dumps({'status': 'completed', 'message': 'timeout'})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )


@router.post("/sam2/segment")
async def segment_frame(
    request: SAM2Request,
    db: Session = Depends(get_db)
):
    """Segment surgical instruments in a frame using SAM2"""
    
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Get frame
    processor = VideoProcessor(video_path=session["video_path"])
    frame = processor.extract_frame(request.timestamp)
    
    if frame is None:
        raise HTTPException(400, "Cannot extract frame")
    
    # Get SAM2 service
    sam2 = get_sam2_service()
    
    if request.auto_detect:
        result = sam2.auto_segment_instruments(frame.image)
    else:
        result = sam2.segment_image(frame.image)
    
    return {
        "timestamp": request.timestamp,
        "frame_idx": frame.frame_idx,
        **result
    }


@router.get("/sam2/status")
async def sam2_status():
    """Check SAM2 availability"""
    sam2 = get_sam2_service()
    return {
        "available": sam2.is_available,
        "loaded": sam2._is_loaded,
        "model_path": sam2.model_path
    }


@router.post("/tts/synthesize")
async def synthesize_speech(request: TTSRequest):
    """Convert text to speech"""
    
    tts = get_tts_service()
    
    result = await tts.synthesize(
        text=request.text,
        voice=request.voice,
        save_to_file=True
    )
    
    return result


@router.post("/tts/summary/{session_id}/{window_id}")
async def synthesize_summary(
    session_id: str,
    window_id: int,
    db: Session = Depends(get_db)
):
    """Generate TTS audio for a window summary"""
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    summaries = get_summaries_by_session(db, session["session_id"])
    summary = next((s for s in summaries if s.window_id == window_id), None)
    
    if not summary:
        raise HTTPException(404, "Summary not found")
    
    tts = get_tts_service()
    
    result = await tts.synthesize_summary(
        summary=summary.summary_text,
        window_id=window_id,
        session_id=session_id,
        save_to_file=True
    )
    
    if result["success"] and result.get("file_path"):
        # Update summary with TTS path
        summary.tts_audio_path = result["file_path"]
        db.commit()
    
    return result


@router.get("/tts/voices")
async def get_tts_voices():
    """Get available TTS voices"""
    tts = get_tts_service()
    return tts.get_available_voices()


@router.post("/analyze-images")
async def analyze_images(
    request: ImageAnalysisRequest,
    db: Session = Depends(get_db)
):
    """
    Analyze images from a video window (independent API)
    
    This endpoint extracts frames from a video window and analyzes them
    using the local VLM model. Returns frame-by-frame analysis results.
    """
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Create video processor
    processor = VideoProcessor(
        video_path=session["video_path"],
        window_duration=settings.WINDOW_DURATION,
        sample_interval=settings.SAMPLE_INTERVAL
    )
    
    # Extract window
    window = processor.extract_window(request.start_time)
    
    if window.frame_count == 0:
        raise HTTPException(400, "No frames extracted for this window")
    
    # Get VLM model service
    vlm_service = await ensure_model_loaded()
    
    # Analyze each frame
    frame_analyses = []
    for frame in window.frames:
        analysis = await vlm_service.analyze_frame(
            frame.image,
            analysis_type=request.analysis_type
        )
        
        frame_analysis = {
            "frame_idx": frame.frame_idx,
            "timestamp": frame.timestamp,
            **analysis
        }
        frame_analyses.append(frame_analysis)
        
        # Save frame analysis to database
        create_frame_analysis(
            db=db,
            session_id=session["session_id"],
            frame_idx=frame.frame_idx,
            timestamp=frame.timestamp,
            tool_localization=analysis.get("tools", ""),
            surgical_action=analysis.get("action", ""),
            surgical_phase=analysis.get("phase", "")
        )
    
    return {
        "window_id": window.window_id,
        "start_time": window.start_time,
        "end_time": window.end_time,
        "frame_count": window.frame_count,
        "frame_analyses": frame_analyses,
        "analysis_type": request.analysis_type
    }


@router.post("/integrate-analysis")
async def integrate_analysis_results(
    request: IntegrateAnalysisRequest,
    db: Session = Depends(get_db)
):
    """
    Integrate frame analysis results into a coherent summary
    
    This endpoint takes frame-by-frame analysis results and integrates them
    into a single narrative summary using GLM-4.6V-Flash (or GPT as fallback).
    """
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Create video processor
    processor = VideoProcessor(
        video_path=session["video_path"],
        window_duration=settings.WINDOW_DURATION,
        sample_interval=settings.SAMPLE_INTERVAL
    )
    
    # Extract window
    window = processor.extract_window(request.start_time)
    
    if window.frame_count == 0:
        raise HTTPException(400, "No frames extracted for this window")
    
    # Get frame analyses from database or analyze on-the-fly
    frame_analyses = []
    db_frames = get_frames_by_session(db, session["session_id"])
    
    # Filter frames for this window (db_frames is a list of dicts)
    window_frames = [
        f for f in db_frames
        if window.start_time <= f["timestamp"] < window.end_time
    ]
    
    if window_frames:
        # Use existing analyses from database
        for db_frame in window_frames:
            frame_analyses.append({
                "frame_idx": db_frame["frame_idx"],
                "timestamp": db_frame["timestamp"],
                "phase": db_frame.get("surgical_phase") or "",
                "action": db_frame.get("surgical_action") or "",
                "tools": db_frame.get("tool_localization") or ""
            })
    else:
        # Analyze frames if not in database
        vlm_service = await ensure_model_loaded()
        for frame in window.frames:
            analysis = await vlm_service.analyze_frame(frame.image, analysis_type="all")
            frame_analyses.append({
                "frame_idx": frame.frame_idx,
                "timestamp": frame.timestamp,
                **analysis
            })
    
    if not frame_analyses:
        raise HTTPException(400, "No analysis results available for this window")
    
    # Integrate using GLM-4.6V-Flash or GPT
    if request.use_glm:
        try:
            glm_summarizer = get_glm_summarizer()
            
            # Check GLM service health
            is_healthy = await glm_summarizer.check_health()
            if not is_healthy:
                raise HTTPException(503, "GLM服务不可用")
            
            # Integrate using GLM (纯文本输入，全中文输出)
            # 按照temporal_analyze.py的逻辑
            result = await glm_summarizer.integrate_analysis_results(
                frame_analyses=frame_analyses,
                images=None,  # GLM只使用文本输入
                system_prompt=None  # 使用内置的全中文提示词
            )
            
            if not result["success"]:
                raise HTTPException(500, f"GLM整合失败: {result.get('error', '未知错误')}")
            
            summary_text = result["summary"]
            model_used = "GLM-4.6V-Flash"
            
        except HTTPException:
            raise
        except Exception as e:
            # Fallback to GPT if GLM fails
            logger.warning(f"GLM integration failed, falling back to GPT: {e}")
            request.use_glm = False
    
    if not request.use_glm:
        # Use GPT as fallback
        summarizer = get_gpt_summarizer()
        context = build_frame_context(window, frame_analyses)
        
        result = await summarizer.summarize_window(
            images=window.get_images(),
            context=context,
            system_prompt=ANALYSIS_SYSTEM_PROMPT
        )
        
        if not result["success"]:
            raise HTTPException(500, f"Summarization failed: {result.get('error', 'Unknown error')}")
        
        summary_text = result["summary"]
        model_used = "GPT"
    
    # Save summary to database
    summary = create_window_summary(
        db=db,
        session_id=session["session_id"],
        window_id=window.window_id,
        start_time=window.start_time,
        end_time=window.end_time,
        summary_text=summary_text,
        tools_detected=[f.get("tools", "") for f in frame_analyses],
        key_actions=[f.get("action", "") for f in frame_analyses]
    )
    
    return {
        "window_id": window.window_id,
        "start_time": window.start_time,
        "end_time": window.end_time,
        "frame_count": window.frame_count,
        "frame_analyses": frame_analyses,
        "summary": summary_text,
        "summary_id": summary.id,
        "model": model_used
    }


@router.get("/surgr1/status")
async def surgr1_status():
    """Check SurgR1 service status"""
    try:
        surgr1_client = get_surgr1_client()
        is_healthy = await surgr1_client.check_health()
        return {
            "available": is_healthy,
            "api_url": surgr1_client.api_url
        }
    except Exception as e:
        return {
            "available": False,
            "error": str(e)
        }


@router.get("/glm/status")
async def glm_status():
    """Check GLM-4.6V-Flash service status"""
    try:
        glm_summarizer = get_glm_summarizer()
        is_healthy = await glm_summarizer.check_health()
        return {
            "available": is_healthy,
            "api_url": glm_summarizer.api_url,
            "model_name": glm_summarizer.model_name
        }
    except Exception as e:
        return {
            "available": False,
            "error": str(e)
        }


@router.get("/sam3/status")
async def sam3_status():
    """Check SAM3 service status"""
    try:
        sam3_client = get_sam3_client()
        is_healthy = await sam3_client.check_health()
        return {
            "available": is_healthy,
            "api_url": sam3_client.api_url
        }
    except Exception as e:
        return {
            "available": False,
            "error": str(e)
        }


@router.get("/sam3/segmented-frame/{session_id}")
async def get_sam3_segmented_frame(
    session_id: str,
    timestamp: float = Query(..., ge=0, description="Frame timestamp"),
    alpha: float = Query(0.4, ge=0.0, le=1.0, description="Mask transparency"),
    db: Session = Depends(get_db)
):
    """
    Get a single frame with SAM3 segmentation overlay.
    
    Uses SurgR1 tool_localization to get bounding boxes,
    then SAM3 to generate segmentation masks.
    
    Returns base64 encoded image with segmentation overlay.
    """
    try:
        session = get_video_session(db, session_id)
        if not session:
            return {
                "success": False,
                "message": "Session not found",
                "timestamp": timestamp,
                "has_segmentation": False
            }
        
        # Check if SAM3 is available
        try:
            sam3_client = await ensure_sam3_available()
            is_healthy = await sam3_client.check_health()
            if not is_healthy:
                return {
                    "success": False,
                    "message": "SAM3 service not available",
                    "timestamp": timestamp,
                    "has_segmentation": False
                }
        except Exception as e:
            logger.warning(f"SAM3 service check failed: {e}")
            return {
                "success": False,
                "message": f"SAM3 service error: {e}",
                "timestamp": timestamp,
                "has_segmentation": False
            }
        
        # Create video processor and extract frame
        processor = VideoProcessor(
            video_path=session["video_path"],
            window_duration=settings.WINDOW_DURATION,
            sample_interval=settings.SAMPLE_INTERVAL
        )
        
        frame = processor.extract_frame(timestamp)
        if frame is None:
            return {
                "success": False,
                "timestamp": timestamp,
                "message": f"Could not extract frame at timestamp {timestamp}",
                "has_segmentation": False
            }
        
        # Step 1: Get SurgR1 tool_localization result
        # First try to get from database (frames is a list of dicts)
        frames = get_frames_by_session(db, session["session_id"])
        nearest_frame = None
        if frames:
            nearest_frame = min(frames, key=lambda f: abs(f["timestamp"] - timestamp))
            if abs(nearest_frame["timestamp"] - timestamp) > 1.0:
                nearest_frame = None
        
        tool_localization = ""
        if nearest_frame and nearest_frame.get("tool_localization"):
            tool_localization = nearest_frame["tool_localization"]
        else:
            # Analyze with SurgR1 on-the-fly
            try:
                surgr1_client = await ensure_surgr1_available()
                result = await surgr1_client.analyze_frame(
                    image=frame.image,
                    analysis_type="tools",
                    session_id=session_id,
                    frame_idx=frame.frame_idx,
                    timestamp=frame.timestamp,
                    save_to_mysql=False
                )
                tool_localization = result.get("tools", "")
            except Exception as e:
                logger.warning(f"SurgR1 analysis failed: {e}")
                # Return original frame if SurgR1 fails
                return {
                    "success": False,
                    "timestamp": timestamp,
                    "frame_idx": frame.frame_idx,
                    "message": f"SurgR1 analysis failed: {e}",
                    "image_base64": frame.to_base64(),
                    "has_segmentation": False
                }
        
        if not tool_localization:
            # No tools detected, return original frame
            return {
                "success": True,
                "timestamp": timestamp,
                "frame_idx": frame.frame_idx,
                "message": "No tools detected in frame",
                "image_base64": frame.to_base64(),
                "has_segmentation": False
            }
        
        # Step 2: Parse bboxes and call SAM3
        try:
            result = await sam3_client.segment_from_surgr1(
                image=frame.image,
                surgr1_bbox_output=tool_localization,
                alpha=alpha,
                return_base64=True
            )
            
            if result.get("success") and result.get("image_base64"):
                return {
                    "success": True,
                    "timestamp": timestamp,
                    "frame_idx": frame.frame_idx,
                    "image_base64": result["image_base64"],
                    "has_segmentation": True,
                    "num_objects": result.get("num_objects", 0),
                    "parsed_bboxes": result.get("parsed_bboxes", [])
                }
            else:
                # SAM3 failed, return original frame
                return {
                    "success": False,
                    "timestamp": timestamp,
                    "frame_idx": frame.frame_idx,
                    "message": result.get("error", "SAM3 segmentation failed"),
                    "image_base64": frame.to_base64(),
                    "has_segmentation": False
                }
                
        except Exception as e:
            logger.error(f"SAM3 segmentation failed: {e}")
            try:
                return {
                    "success": False,
                    "timestamp": timestamp,
                    "frame_idx": frame.frame_idx,
                    "message": f"SAM3 error: {e}",
                    "image_base64": frame.to_base64(),
                    "has_segmentation": False
                }
            except:
                return {
                    "success": False,
                    "timestamp": timestamp,
                    "message": f"SAM3 error: {e}",
                    "has_segmentation": False
                }
                
    except Exception as outer_e:
        # Catch any other unhandled exceptions
        logger.error(f"Unexpected error in get_sam3_segmented_frame: {outer_e}")
        return {
            "success": False,
            "timestamp": timestamp,
            "message": f"Server error: {outer_e}",
            "has_segmentation": False
        }


@router.get("/sam3/stream/{session_id}")
async def stream_sam3_segmented_video(
    session_id: str,
    alpha: float = Query(0.4, ge=0.0, le=1.0, description="Mask transparency"),
    fps: float = Query(5.0, ge=1.0, le=30.0, description="Stream FPS"),
    db: Session = Depends(get_db)
):
    """
    Stream video with SAM3 segmentation overlay as MJPEG.
    
    This endpoint provides a continuous MJPEG stream where each frame
    has been processed with SurgR1 (bbox) + SAM3 (segmentation).
    
    Note: This is computationally intensive. Consider caching results.
    """
    import time
    import tempfile
    from pathlib import Path
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Check services availability
    try:
        sam3_client = await ensure_sam3_available()
        surgr1_client = await ensure_surgr1_available()
    except Exception as e:
        raise HTTPException(503, f"Required services not available: {e}")
    
    # Open video
    import cv2
    cap = cv2.VideoCapture(session["video_path"])
    if not cap.isOpened():
        raise HTTPException(400, "Cannot open video file")
    
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_interval = 1.0 / fps  # Target interval between frames
    
    async def generate_frames():
        """Generator that yields MJPEG frames"""
        frame_idx = 0
        last_frame_time = 0
        
        try:
            while True:
                ret, bgr_frame = cap.read()
                if not ret:
                    break
                
                current_time = frame_idx / video_fps
                
                # Rate limiting: skip frames to match target FPS
                if current_time - last_frame_time < frame_interval:
                    frame_idx += 1
                    continue
                
                last_frame_time = current_time
                
                # Convert to PIL Image
                rgb_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
                pil_image = Image.fromarray(rgb_frame)
                
                # Get SurgR1 analysis
                try:
                    surgr1_result = await surgr1_client.analyze_frame(
                        image=pil_image,
                        analysis_type="tools",
                        save_to_mysql=False
                    )
                    tool_localization = surgr1_result.get("tools", "")
                except Exception as e:
                    logger.warning(f"SurgR1 failed for frame {frame_idx}: {e}")
                    tool_localization = ""
                
                output_image = pil_image
                
                # Apply SAM3 segmentation if tools detected
                if tool_localization:
                    try:
                        sam3_result = await sam3_client.segment_from_surgr1(
                            image=pil_image,
                            surgr1_bbox_output=tool_localization,
                            alpha=alpha,
                            return_base64=True
                        )
                        
                        if sam3_result.get("success") and sam3_result.get("image_base64"):
                            # Decode base64 to image
                            import base64
                            from io import BytesIO
                            img_data = base64.b64decode(sam3_result["image_base64"])
                            output_image = Image.open(BytesIO(img_data))
                    except Exception as e:
                        logger.warning(f"SAM3 failed for frame {frame_idx}: {e}")
                
                # Convert to JPEG bytes
                buffer = BytesIO()
                output_image.save(buffer, format="JPEG", quality=80)
                jpeg_bytes = buffer.getvalue()
                
                # Yield as MJPEG frame
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg_bytes)).encode() + b"\r\n"
                    b"\r\n" + jpeg_bytes + b"\r\n"
                )
                
                frame_idx += 1
                
                # Small delay to prevent CPU overload
                await asyncio.sleep(0.01)
                
        finally:
            cap.release()
    
    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


# ==============================================================================
# Frame and Summary Retrieval APIs (for seek/drag operations)
# ==============================================================================

@router.get("/frame-at-timestamp/{session_id}")
async def get_frame_at_timestamp(
    session_id: str,
    timestamp: float = Query(..., description="Target timestamp in seconds"),
    tolerance: float = Query(1.0, description="Time tolerance for finding frame"),
    db: Session = Depends(get_db)
):
    """
    Get the saved frame closest to the specified timestamp.
    
    Used when seeking/dragging in the video player to show the analyzed frame.
    Returns frame image (base64) and analysis results.
    """
    mysql_service = get_mysql_service()
    
    # Get video session info
    video_session = mysql_service.get_video_session(session_id)
    if not video_session:
        raise HTTPException(404, "Session not found")
    
    storage_path = video_session.get("storage_path")
    
    # PRIORITY 1: Try to find frame from storage folder (saved at 5fps for smooth playback)
    if storage_path:
        frame_storage = get_frame_storage_service()
        nearest = frame_storage.find_nearest_frame(storage_path, timestamp)
        if nearest and nearest["timestamp_diff"] <= tolerance:
            image_base64 = frame_storage.get_frame(storage_path, nearest["path"])
            if image_base64:
                # Also try to get analysis data if available
                frame_data = mysql_service.get_frame_at_timestamp(session_id, timestamp, tolerance)
                analysis = None
                if frame_data:
                    analysis = {
                        "tool_localization": frame_data.get("tool_localization"),
                        "surgical_action": frame_data.get("surgical_action"),
                        "surgical_phase": frame_data.get("surgical_phase")
                    }
                return {
                    "success": True,
                    "has_saved_frame": True,
                    "timestamp": timestamp,
                    "actual_timestamp": timestamp - nearest["timestamp_diff"],
                    "image_base64": image_base64,
                    "analysis": analysis
                }
    
    # PRIORITY 2: Try to get from database (for backward compatibility)
    frame_data = mysql_service.get_frame_at_timestamp(session_id, timestamp, tolerance)
    
    if not frame_data:
        # PRIORITY 3: Fallback to live video stream
        video_path = video_session.get("video_path")
        if video_path and (video_path.startswith("http://") or video_path.startswith("https://")):
            try:
                import cv2
                import base64
                cap = cv2.VideoCapture(video_path)
                if cap.isOpened():
                    # Skip first few frames to get fresh frame (avoid cached/buffered frame)
                    frame = None
                    for _ in range(3):  # Read 3 frames, use the last one
                        ret, frame = cap.read()
                        if not ret:
                            break
                    cap.release()
                    if frame is not None:
                        # Encode frame to base64
                        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        image_base64 = base64.b64encode(buffer).decode('utf-8')
                        return {
                            "success": True,
                            "has_saved_frame": False,
                            "is_live_frame": True,
                            "timestamp": timestamp,
                            "actual_timestamp": timestamp,
                            "image_base64": image_base64,
                            "analysis": None,
                            "message": "Live frame from stream (no saved frame available)"
                        }
            except Exception as e:
                logger.warning(f"Failed to get live frame from stream: {e}")
        
        return {
            "success": False,
            "has_saved_frame": False,
            "timestamp": timestamp,
            "message": "No saved frame found for this timestamp"
        }
    
    # Get image from storage
    image_base64 = None
    if storage_path and frame_data.get("image_path"):
        frame_storage = get_frame_storage_service()
        image_base64 = frame_storage.get_frame(storage_path, frame_data["image_path"])
    
    return {
        "success": True,
        "has_saved_frame": bool(image_base64),
        "timestamp": timestamp,
        "actual_timestamp": frame_data.get("timestamp"),
        "frame_idx": frame_data.get("frame_idx"),
        "image_base64": image_base64,
        "analysis": {
            "tool_localization": frame_data.get("tool_localization"),
            "surgical_action": frame_data.get("surgical_action"),
            "surgical_phase": frame_data.get("surgical_phase")
        }
    }


@router.get("/window-summary-at-timestamp/{session_id}")
async def get_window_summary_at_timestamp(
    session_id: str,
    timestamp: float = Query(..., description="Target timestamp in seconds"),
    db: Session = Depends(get_db)
):
    """
    Get the GLM window summary that covers the specified timestamp.
    
    Used when seeking/dragging to show the corresponding analysis summary.
    """
    mysql_service = get_mysql_service()
    
    # Get window summary
    summary = mysql_service.get_window_summary_at_timestamp(session_id, timestamp)
    
    if not summary:
        return {
            "success": False,
            "timestamp": timestamp,
            "window_id": None,
            "summary": None,
            "message": "No window summary found for this timestamp"
        }
    
    return {
        "success": True,
        "timestamp": timestamp,
        "window_id": summary.get("window_id"),
        "window_start": summary.get("window_start"),
        "window_end": summary.get("window_end"),
        "summary": summary.get("glm_summary"),
        "surgical_phase": summary.get("surgical_phase")
    }


@router.get("/all-window-summaries/{session_id}")
async def get_all_window_summaries(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Get all GLM window summaries for a session.
    
    Used to populate the summary list in the UI.
    """
    mysql_service = get_mysql_service()
    
    summaries = mysql_service.get_all_window_summaries(session_id)
    
    return {
        "success": True,
        "session_id": session_id,
        "count": len(summaries),
        "summaries": summaries
    }


@router.get("/frames-in-range/{session_id}")
async def get_frames_in_range(
    session_id: str,
    start: float = Query(..., description="Start timestamp in seconds"),
    end: float = Query(..., description="End timestamp in seconds"),
    db: Session = Depends(get_db)
):
    """
    Get list of saved frames within a time range.
    
    Used for loop playback feature to fetch available frames in a window.
    Returns frame metadata (timestamp, frame_idx) without full image data.
    """
    mysql_service = get_mysql_service()
    
    # Get video session info
    video_session = mysql_service.get_video_session(session_id)
    if not video_session:
        raise HTTPException(404, "Session not found")
    
    storage_path = video_session.get("storage_path")
    
    # Get frames from storage folder (these are saved at higher FPS for smooth playback)
    frames_in_range = []
    if storage_path:
        frame_storage = get_frame_storage_service()
        storage_frames = frame_storage.list_frames_in_range(storage_path, start, end, "frames")
        frames_in_range = [
            {
                "frame_idx": f.get("frame_idx", -1),
                "timestamp": f.get("timestamp"),
                "has_image": True,
                "path": f.get("path")
            }
            for f in storage_frames
        ]
    
    # If no frames in storage, fall back to database
    if not frames_in_range:
        frames = mysql_service.get_analyses(session_id, limit=10000)
        frames_in_range = [
            {
                "frame_idx": f.get("frame_idx"),
                "timestamp": f.get("timestamp"),
                "has_image": f.get("image_saved") == 1,
            }
            for f in frames
            if f.get("analysis_type") == "frame" 
            and f.get("timestamp") is not None
            and start <= f.get("timestamp") <= end
        ]
    
    return {
        "success": True,
        "session_id": session_id,
        "start": start,
        "end": end,
        "count": len(frames_in_range),
        "frames": sorted(frames_in_range, key=lambda x: x.get("timestamp", 0))
    }


@router.get("/session-frames/{session_id}")
async def list_session_frames(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    List all saved frames for a session.
    
    Returns metadata about saved frames for timeline display.
    """
    mysql_service = get_mysql_service()
    
    # Get video session info
    video_session = mysql_service.get_video_session(session_id)
    if not video_session:
        raise HTTPException(404, "Session not found")
    
    storage_path = video_session.get("storage_path")
    
    # Get all frame analyses with saved images
    frames = mysql_service.get_analyses(session_id, limit=10000)
    saved_frames = [
        {
            "frame_idx": f.get("frame_idx"),
            "timestamp": f.get("timestamp"),
            "has_image": f.get("image_saved") == 1,
            "surgical_phase": f.get("surgical_phase")
        }
        for f in frames
        if f.get("analysis_type") == "frame"
    ]
    
    return {
        "success": True,
        "session_id": session_id,
        "storage_path": storage_path,
        "count": len(saved_frames),
        "frames": sorted(saved_frames, key=lambda x: x.get("timestamp", 0))
    }

