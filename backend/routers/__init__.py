from .video import router as video_router
from .analysis import router as analysis_router
from .model import router as model_router
from .webrtc import router as webrtc_router
from .voice import router as voice_router

__all__ = ["video_router", "analysis_router", "model_router", "webrtc_router", "voice_router"]

