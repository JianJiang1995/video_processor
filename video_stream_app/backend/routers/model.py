"""
VLM Model API Routes
FastAPI endpoints for calling Swift-deployed model service
"""
import base64
from io import BytesIO
from typing import List, Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from PIL import Image

from ..services.model_service import get_model_service, ensure_model_loaded
from ..config import settings

router = APIRouter(prefix="/api/model", tags=["model"])


class InferRequest(BaseModel):
    """Single inference request"""
    image_base64: str
    question: str
    temperature: float = 0.1
    max_tokens: int = 512


class BatchInferRequest(BaseModel):
    """Batch inference request for N=5s window"""
    images_base64: List[str]
    questions: List[str]  # Same length as images, or single question for all
    temperature: float = 0.1
    max_tokens: int = 512


class FrameAnalysisRequest(BaseModel):
    """Frame analysis request"""
    image_base64: str
    analysis_type: str = "all"  # "all", "phase", "action", "tools"


class WindowAnalysisRequest(BaseModel):
    """N=5s window analysis request"""
    images_base64: List[str]
    analysis_type: str = "phase"


class InferResponse(BaseModel):
    """Inference response"""
    answer: str
    inference_time: float
    token_count: int


@router.get("/status")
async def get_model_status():
    """Check model service status"""
    client = get_model_service()
    is_healthy = await client.check_health()
    models = await client.get_models() if is_healthy else []
    
    return {
        "healthy": is_healthy,
        "api_url": client.api_url,
        "model_name": client.model_name,
        "available_models": models,
        "config": {
            "window_duration": settings.WINDOW_DURATION,
            "sample_interval": settings.SAMPLE_INTERVAL
        }
    }


@router.get("/health")
async def health_check():
    """Simple health check"""
    client = get_model_service()
    is_healthy = await client.check_health()
    
    if is_healthy:
        return {"status": "ok", "model": client.model_name}
    else:
        raise HTTPException(503, "Model service unavailable")


@router.post("/infer", response_model=InferResponse)
async def infer_single(request: InferRequest):
    """
    Single image inference
    
    Calls Swift-deployed model API with one image and question.
    """
    try:
        client = await ensure_model_loaded()
        
        result = await client.infer_single(
            image=request.image_base64,
            question=request.question,
            temperature=request.temperature,
            max_tokens=request.max_tokens
        )
        
        return InferResponse(
            answer=result.answer,
            inference_time=result.inference_time,
            token_count=result.token_count
        )
        
    except Exception as e:
        raise HTTPException(500, f"Inference failed: {str(e)}")


@router.post("/infer-batch")
async def infer_batch(request: BatchInferRequest):
    """
    Batch inference for N=5s window
    
    Sends multiple images to model service concurrently.
    Useful for processing all frames in a 5-second window.
    """
    try:
        client = await ensure_model_loaded()
        
        results = await client.infer_batch(
            images=request.images_base64,
            questions=request.questions,
            temperature=request.temperature,
            max_tokens=request.max_tokens
        )
        
        return {
            "success": True,
            "count": len(results),
            "window_duration": settings.WINDOW_DURATION,
            "results": [
                {
                    "frame_index": i,
                    "answer": r.answer,
                    "inference_time": r.inference_time,
                    "token_count": r.token_count
                }
                for i, r in enumerate(results)
            ]
        }
        
    except Exception as e:
        raise HTTPException(500, f"Batch inference failed: {str(e)}")


@router.post("/infer-upload")
async def infer_with_upload(
    file: UploadFile = File(...),
    question: str = Form(...),
    temperature: float = Form(0.1),
    max_tokens: int = Form(512)
):
    """
    Inference with file upload
    
    Upload an image file directly instead of base64.
    """
    try:
        contents = await file.read()
        image = Image.open(BytesIO(contents))
        
        client = await ensure_model_loaded()
        
        result = await client.infer_single(
            image=image,
            question=question,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        return {
            "answer": result.answer,
            "inference_time": result.inference_time,
            "token_count": result.token_count,
            "filename": file.filename
        }
        
    except Exception as e:
        raise HTTPException(500, f"Inference failed: {str(e)}")


@router.post("/analyze-frame")
async def analyze_frame(request: FrameAnalysisRequest):
    """
    Analyze a surgical frame with predefined questions
    
    analysis_type options:
    - "all": Returns phase, action, and tools
    - "phase": Only surgical phase
    - "action": Only surgical action
    - "tools": Only tool detection
    """
    try:
        client = await ensure_model_loaded()
        
        results = await client.analyze_frame(
            image=request.image_base64,
            analysis_type=request.analysis_type
        )
        
        return {
            "success": True,
            "analysis_type": request.analysis_type,
            "results": results
        }
        
    except Exception as e:
        raise HTTPException(500, f"Frame analysis failed: {str(e)}")


@router.post("/analyze-window")
async def analyze_window(request: WindowAnalysisRequest):
    """
    Analyze a N=5s video window (multiple frames)
    
    Efficiently processes all frames in a window with batch inference.
    Returns analysis for each frame.
    """
    try:
        client = await ensure_model_loaded()
        
        results = await client.analyze_window(
            images=request.images_base64,
            analysis_type=request.analysis_type
        )
        
        return {
            "success": True,
            "frame_count": len(request.images_base64),
            "window_duration": settings.WINDOW_DURATION,
            "analysis_type": request.analysis_type,
            "frames": results
        }
        
    except Exception as e:
        raise HTTPException(500, f"Window analysis failed: {str(e)}")
