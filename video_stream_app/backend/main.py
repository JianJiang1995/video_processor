"""
Video Stream Analyzer - FastAPI Backend
Main application entry point
"""
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import logging

from .config import settings
from .database import init_db
from .routers import video_router, analysis_router, model_router, webrtc_router, voice_router
from .middleware import APILoggingMiddleware, api_logger
from .services.frame_storage_service import get_sessions_storage_base

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    description="Real-time surgical video analysis with GPT summarization",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Logging middleware (logs requests, responses, and timing)
app.add_middleware(APILoggingMiddleware)

# Include routers
app.include_router(video_router)
app.include_router(analysis_router)
app.include_router(model_router)
app.include_router(webrtc_router)
app.include_router(voice_router)

# Static files for frontend (if exists)
frontend_path = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_path.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_path / "assets")), name="assets")

# Static files for session frames (direct access for fast playback)
sessions_path = get_sessions_storage_base()
if sessions_path.exists():
    app.mount("/sessions", StaticFiles(directory=str(sessions_path)), name="sessions")
    logger.info(f"Mounted sessions folder: {sessions_path}")


@app.on_event("startup")
async def startup_event():
    """Initialize on startup"""
    logger.info(f"Starting {settings.APP_NAME}")
    logger.info(f"Database: {settings.DATABASE_URL}")
    logger.info(f"Upload directory: {settings.UPLOAD_DIR}")
    logger.info(f"VLM Model: {settings.VLM_MODEL_PATH}")
    logger.info(f"API logs directory: {Path(__file__).parent.parent / 'logs'}")
    
    # Initialize database
    init_db()
    logger.info("Database initialized")
    
    # Optionally preload VLM model
    if settings.VLM_PRELOAD:
        logger.info("Preloading VLM model...")
        try:
            from .services.model_service import ensure_model_loaded
            await ensure_model_loaded()
            logger.info("VLM model preloaded successfully")
        except Exception as e:
            logger.warning(f"Failed to preload VLM model: {e}")

    # 预热专家模型，避免首个窗口分析时在 async event loop 上做几秒钟同步加载
    # (Phase ~200MB + Triplet 290MB + YOLO 追踪 warmup 合计 3-5s；在事件循环上阻塞
    # 会导致正在进行中的 MJPEG 代理生成器卡住，表现为视频流 "先流畅后突然卡死")。
    import asyncio as _asyncio
    async def _preload_experts():
        try:
            loop = _asyncio.get_event_loop()
            def _sync_load():
                from .services.phase_service import get_phase_service
                from .services.triplet_service import get_triplet_service
                from .services.yolo_service import get_yolo_service
                # Ultralytics mutates CUDA_VISIBLE_DEVICES when loading a
                # selected GPU. Initialize torch-native experts first so their
                # physical cuda:N device handles are created before YOLO narrows
                # visibility for its own predictor.
                p = get_phase_service()
                t = get_triplet_service()
                y = get_yolo_service()
                return bool(y), bool(p), bool(t)
            y, p, t = await loop.run_in_executor(None, _sync_load)
            logger.info(f"[Startup] Experts preloaded — yolo={y} phase={p} triplet={t}")
        except Exception as e:
            logger.warning(f"[Startup] Expert preload failed (will lazy-load): {e}")
    _asyncio.create_task(_preload_experts())


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down...")
    
    # Cleanup WebRTC connections
    try:
        from .routers.webrtc import cleanup_connections
        await cleanup_connections()
    except Exception as e:
        logger.warning(f"WebRTC cleanup error: {e}")


@app.get("/")
async def root():
    """Serve frontend or API info"""
    index_path = frontend_path / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {
        "name": settings.APP_NAME,
        "version": "1.0.0",
        "docs": "/api/docs",
        "status": "running"
    }


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "window_duration": settings.WINDOW_DURATION,
        "gpt_model": settings.GPT_MODEL,
        "sam2_enabled": settings.SAM2_ENABLED,
        "tts_enabled": settings.TTS_ENABLED
    }


@app.get("/api/config")
async def get_config():
    """Get public configuration"""
    return {
        "window_duration": settings.WINDOW_DURATION,
        "sample_interval": settings.SAMPLE_INTERVAL,
        "gpt_model": settings.GPT_MODEL,
        "tts_voices": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]
    }


# Serve SPA routes
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serve SPA for any unmatched routes"""
    index_path = frontend_path / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"error": "Frontend not built", "path": full_path}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "video_stream_app.backend.main:app",
        host="0.0.0.0",
        port=8001,
        reload=True
    )
