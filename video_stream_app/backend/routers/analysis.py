"""
Video Analysis API Routes
Handles GPT summarization, SAM2 masks, and TTS
"""
import asyncio
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
import json

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
from ..config import settings, ANALYSIS_SYSTEM_PROMPT

import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

# Global service instances
gpt_summarizer: Optional[GPTSummarizer] = None
sam2_service: Optional[SAM2Service] = None
tts_service: Optional[TTSService] = None

# Global cancellation flags for analysis tasks
# Key: session_id, Value: bool (True = should cancel)
analysis_cancellation_flags: dict = {}


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
        video_path=session.video_path,
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
            session_id=session.id,
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
        video_path=session.video_path,
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
            session_id=session.id,
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
        # Try GLM-4.6V-Flash first
        glm_summarizer = get_glm_summarizer()
        is_healthy = await glm_summarizer.check_health()
        
        if is_healthy:
            # Integrate using GLM
            result = await glm_summarizer.integrate_analysis_results(
                frame_analyses=frame_analyses,
                images=window.get_images(),
                system_prompt=ANALYSIS_SYSTEM_PROMPT
            )
            
            if result["success"]:
                summary_text = result["summary"]
                model_used = "VLM + GLM-4.6V-Flash"
            else:
                raise Exception(f"GLM integration failed: {result.get('error')}")
        else:
            raise Exception("GLM service not available")
            
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
        session_id=session.id,
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
        video_path=session.video_path,
        db_session_id=session.id,
        use_chinese=use_chinese
    )
    
    return {
        "message": "Processing started",
        "session_id": session_id,
        "estimated_windows": int(session.duration / settings.WINDOW_DURATION) + 1
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
        video_path=session.video_path,
        db_session_id=session.id,
        use_chinese=request.use_chinese,
        use_glm_multimodal=request.use_glm_multimodal
    )
    
    return {
        "message": "SurgR1+GLM processing started",
        "session_id": request.session_id,
        "estimated_windows": int(session.duration / settings.WINDOW_DURATION) + 1,
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
            # Step 1: SurgR1 - Analyze each frame
            # ==================================================================
            frame_analyses = []
            
            for frame in window.frames:
                try:
                    # Analyze frame with SurgR1
                    result = await surgr1_client.analyze_frame(
                        image=frame.image,
                        analysis_type="all",
                        session_id=session_id,
                        frame_idx=frame.frame_idx,
                        timestamp=frame.timestamp,
                        save_to_mysql=True
                    )
                    
                    frame_analysis = {
                        "frame_idx": frame.frame_idx,
                        "timestamp": frame.timestamp,
                        "phase": result.get("phase", ""),
                        "action": result.get("action", ""),
                        "tools": result.get("tools", "")
                    }
                    frame_analyses.append(frame_analysis)
                    
                    # Save to SQLite database too
                    create_frame_analysis(
                        db=db,
                        session_id=db_session_id,
                        frame_idx=frame.frame_idx,
                        timestamp=frame.timestamp,
                        tool_localization=result.get("tools", ""),
                        surgical_action=result.get("action", ""),
                        surgical_phase=result.get("phase", "")
                    )
                    
                except Exception as e:
                    logger.warning(f"SurgR1 analysis failed for frame {frame.frame_idx}: {e}")
                    frame_analyses.append({
                        "frame_idx": frame.frame_idx,
                        "timestamp": frame.timestamp,
                        "phase": "",
                        "action": "",
                        "tools": ""
                    })
            
            # ==================================================================
            # Step 2: GLM - Summarize window
            # ==================================================================
            try:
                if use_glm_multimodal:
                    # Use GLM with images
                    result = await glm_client.integrate_analysis_results(
                        frame_analyses=frame_analyses,
                        images=window.get_images(),
                        system_prompt=ANALYSIS_SYSTEM_PROMPT,
                        temperature=0.7,
                        max_tokens=1500
                    )
                else:
                    # Use GLM text-only
                    result = await glm_client.integrate_analysis_results(
                        frame_analyses=frame_analyses,
                        images=None,
                        system_prompt=ANALYSIS_SYSTEM_PROMPT,
                        temperature=0.7,
                        max_tokens=1500
                    )
                
                if result.get("success"):
                    summary_text = result.get("summary", "")
                    
                    # Add Chinese instruction if requested
                    if use_chinese and not any(ord(c) > 127 for c in summary_text[:50]):
                        # Summary is not in Chinese, try to regenerate
                        chinese_prompt = ANALYSIS_SYSTEM_PROMPT + "\n\n请用中文回答。"
                        result = await glm_client.integrate_analysis_results(
                            frame_analyses=frame_analyses,
                            images=window.get_images() if use_glm_multimodal else None,
                            system_prompt=chinese_prompt,
                            temperature=0.7,
                            max_tokens=1500
                        )
                        if result.get("success"):
                            summary_text = result.get("summary", "")
                else:
                    summary_text = f"[GLM Error: {result.get('error', 'Unknown')}]"
                    
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
    frames = get_frames_by_session(db, session.id)
    
    if not frames:
        return {
            "found": False,
            "message": "No frame analyses available yet",
            "timestamp": timestamp
        }
    
    # Find nearest frame to the requested timestamp
    nearest_frame = min(frames, key=lambda f: abs(f.timestamp - timestamp))
    
    # Only return if within 1 second of requested time
    if abs(nearest_frame.timestamp - timestamp) > 1.0:
        return {
            "found": False,
            "message": "No frame analysis near this timestamp",
            "timestamp": timestamp,
            "nearest_timestamp": nearest_frame.timestamp
        }
    
    return {
        "found": True,
        "frame_idx": nearest_frame.frame_idx,
        "timestamp": nearest_frame.timestamp,
        "tool_localization": nearest_frame.tool_localization or "",
        "surgical_action": nearest_frame.surgical_action or "",
        "surgical_phase": nearest_frame.surgical_phase or "",
        "window_id": int(nearest_frame.timestamp / settings.WINDOW_DURATION)
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
        video_path=session.video_path,
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
            session_id=session.id,
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
    
    summaries = get_summaries_by_session(db, session.id)
    
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
    
    summary = get_summary_for_timestamp(db, session.id, timestamp)
    
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
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    async def event_generator():
        last_window_id = -1
        
        while True:
            summaries = get_summaries_by_session(db, session.id)
            
            for s in summaries:
                if s.window_id > last_window_id:
                    data = json.dumps({
                        "window_id": s.window_id,
                        "start_time": s.start_time,
                        "end_time": s.end_time,
                        "summary": s.summary_text
                    })
                    yield f"data: {data}\n\n"
                    last_window_id = s.window_id
            
            # Check if processing is complete or cancelled
            db.refresh(session)
            if session.status == "completed":
                yield f"data: {json.dumps({'status': 'completed'})}\n\n"
                break
            elif session.status == "cancelled":
                yield f"data: {json.dumps({'status': 'cancelled'})}\n\n"
                break
            
            await asyncio.sleep(1)
    
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
    processor = VideoProcessor(video_path=session.video_path)
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
    
    summaries = get_summaries_by_session(db, session.id)
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
        video_path=session.video_path,
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
            session_id=session.id,
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
        video_path=session.video_path,
        window_duration=settings.WINDOW_DURATION,
        sample_interval=settings.SAMPLE_INTERVAL
    )
    
    # Extract window
    window = processor.extract_window(request.start_time)
    
    if window.frame_count == 0:
        raise HTTPException(400, "No frames extracted for this window")
    
    # Get frame analyses from database or analyze on-the-fly
    frame_analyses = []
    db_frames = get_frames_by_session(db, session.id)
    
    # Filter frames for this window
    window_frames = [
        f for f in db_frames
        if window.start_time <= f.timestamp < window.end_time
    ]
    
    if window_frames:
        # Use existing analyses from database
        for db_frame in window_frames:
            frame_analyses.append({
                "frame_idx": db_frame.frame_idx,
                "timestamp": db_frame.timestamp,
                "phase": db_frame.surgical_phase or "",
                "action": db_frame.surgical_action or "",
                "tools": db_frame.tool_localization or ""
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
                raise HTTPException(503, "GLM-4.6V-Flash service is not available")
            
            # Integrate using GLM
            result = await glm_summarizer.integrate_analysis_results(
                frame_analyses=frame_analyses,
                images=window.get_images(),
                system_prompt=ANALYSIS_SYSTEM_PROMPT
            )
            
            if not result["success"]:
                raise HTTPException(500, f"GLM integration failed: {result.get('error', 'Unknown error')}")
            
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
        session_id=session.id,
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

