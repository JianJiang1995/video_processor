"""
SurgR1 API - Surgical Image Analysis Service
Using vLLM for efficient inference with FastAPI

Provides batch image analysis with three questions per image:
1. Tool localization (bounding boxes)
2. Surgical action description
3. Surgical phase identification
"""
import os
import json
import asyncio
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# vLLM imports
from vllm import LLM, SamplingParams
from PIL import Image

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config() -> dict:
    """Load configuration from config.json"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    # Default config
    return {
        "model": {
            "path": "/data/jj/proj/Laparo/last_cot_qwen2.5/round35/v4-20251112-181725/checkpoint-10609-merged",
            "max_model_len": 8192,
            "gpu_memory_utilization": 0.85,
            "tensor_parallel_size": 1,
            "max_num_seqs": 64,
            "trust_remote_code": True
        },
        "inference": {
            "temperature": 0.1,
            "max_tokens": 1024,
            "top_p": 0.95
        },
        "server": {
            "host": "0.0.0.0",
            "port": 9001
        }
    }


CONFIG = load_config()

# System prompt for surgical CoT reasoning
SYSTEM_PROMPT = """A conversation between User and Surgical Assistant. The user asks a question related, and the Assistant solves it. The assistant first thinks about the reasoning process in the mind and then provides the user with the answer. The reasoning process and answer are enclosed within <think> </think> and <answer> </answer> tags, respectively, i.e., <think> reasoning process here </think><answer> answer here </answer>"""

# Image placeholder for vLLM/Qwen2.5-VL
# IMPORTANT: ms-swift replaces <image> with <|vision_start|><|image_pad|><|vision_end|>
# See: ms-swift/swift/llm/template/template/qwen.py line 248 (Qwen2VLTemplate.replace_tag)
# Training format uses <image>, vLLM/Qwen2.5-VL needs: <|vision_start|><|image_pad|><|vision_end|>
VLLM_IMAGE_PLACEHOLDER = "<|vision_start|><|image_pad|><|vision_end|>"


def load_questions() -> dict:
    """Load questions from config.json and convert <image> to vLLM format"""
    questions_config = CONFIG.get("questions", {})
    questions = {}
    for key, value in questions_config.items():
        if key == "comment":
            continue
        # Replace <image> with vLLM image placeholder
        questions[key] = value.replace("<image>", VLLM_IMAGE_PLACEHOLDER)
    
    # Fallback to default questions if config is empty (matching training format)
    if not questions:
        questions = {
            "surgical_phase": f"Given the laparoscopic cholecystectomy image {VLLM_IMAGE_PLACEHOLDER}, which surgical phase is being performed?",
            "surgical_action": f"Given the laparoscopic cholecystectomy image {VLLM_IMAGE_PLACEHOLDER}, describe the complete surgical action in terms of tool, action, and tissue.",
            "tool_localization": f"Given the laparoscopic cholecystectomy image {VLLM_IMAGE_PLACEHOLDER}, locate all the tools in the format of bbox (x1,y1), (x2,y2)."
        }
    return questions


QUESTIONS = load_questions()


# ============================================================================
# Data Models
# ============================================================================

class ImageAnalysisRequest(BaseModel):
    """Request model for batch image analysis"""
    image_paths: List[str] = Field(..., description="List of image file paths to analyze")
    questions: Optional[List[str]] = Field(
        default=None,
        description="Optional custom questions. If not provided, uses default 3 questions."
    )
    temperature: Optional[float] = Field(default=None, description="Sampling temperature")
    max_tokens: Optional[int] = Field(default=None, description="Maximum tokens to generate")


class SingleImageResult(BaseModel):
    """Result for a single image"""
    image_path: str
    responses: Dict[str, str] = Field(..., description="Question -> Answer mapping")


class BatchAnalysisResponse(BaseModel):
    """Response model for batch analysis"""
    results: List[SingleImageResult]
    total_images: int
    total_questions: int


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    model_loaded: bool
    model_path: str


# ============================================================================
# Global Model Instance
# ============================================================================

class SurgR1Model:
    """Wrapper for vLLM model with surgical image analysis"""
    
    def __init__(self):
        self.llm: Optional[LLM] = None
        self.model_path: str = CONFIG["model"]["path"]
        self.sampling_params: Optional[SamplingParams] = None
    
    def load(self):
        """Load the vLLM model"""
        logger.info(f"Loading model from: {self.model_path}")
        
        model_config = CONFIG["model"]
        
        # Configure for batch processing with PIL images
        # Each prompt has exactly 1 image, but we process many prompts in parallel
        # Qwen2.5-VL image processing settings
        # max_pixels: 1003520 ≈ 1M pixels (default for 7B model)
        # min_pixels: 256 * 28 * 28 = 200704 (minimum image size)
        self.llm = LLM(
            model=self.model_path,
            max_model_len=model_config.get("max_model_len", 8192),
            gpu_memory_utilization=model_config.get("gpu_memory_utilization", 0.85),
            tensor_parallel_size=model_config.get("tensor_parallel_size", 1),
            max_num_seqs=model_config.get("max_num_seqs", 64),
            trust_remote_code=model_config.get("trust_remote_code", True),
            limit_mm_per_prompt={"image": 1},  # Each prompt has 1 image
            mm_processor_kwargs={
                "max_pixels": model_config.get("max_pixels", 1003520),
                "min_pixels": model_config.get("min_pixels", 256 * 28 * 28),
            },
        )
        
        inference_config = CONFIG["inference"]
        self.sampling_params = SamplingParams(
            temperature=inference_config.get("temperature", 0.1),
            max_tokens=inference_config.get("max_tokens", 1024),
            top_p=inference_config.get("top_p", 0.95)
        )
        
        logger.info("Model loaded successfully")
    
    def is_loaded(self) -> bool:
        """Check if model is loaded"""
        return self.llm is not None
    
    def _build_prompt(self, question: str) -> str:
        """Build prompt with system instruction.
        
        Note: The question already contains <|image_pad|> placeholder embedded in text,
        matching the training data format: "Given the laparoscopic surgical image <|image_pad|>, ..."
        """
        return f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n<|im_start|>user\n{question}<|im_end|>\n<|im_start|>assistant\n"
    
    def analyze_batch(
        self,
        image_paths: List[str],
        questions: Optional[List[str]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> List[SingleImageResult]:
        """
        Analyze a batch of images, each with multiple questions.
        Each (image, question) pair is treated as an independent sample.
        
        Args:
            image_paths: List of image file paths
            questions: Optional custom questions (defaults to 3 standard questions)
            temperature: Optional override for sampling temperature
            max_tokens: Optional override for max tokens
            
        Returns:
            List of SingleImageResult, one per image, with responses to all questions
        """
        if not self.is_loaded():
            raise RuntimeError("Model not loaded")
        
        # Use default questions if not provided
        if questions is None:
            questions_dict = QUESTIONS
        else:
            # If custom questions provided, use them with generic keys
            questions_dict = {f"q{i}": q for i, q in enumerate(questions)}
        
        # Build sampling params with optional overrides
        sampling_params = SamplingParams(
            temperature=temperature if temperature is not None else self.sampling_params.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.sampling_params.max_tokens,
            top_p=self.sampling_params.top_p
        )
        
        # Validate image paths first
        valid_paths = []
        for image_path in image_paths:
            if Path(image_path).exists():
                valid_paths.append(image_path)
            else:
                logger.warning(f"Image not found: {image_path}")
        
        if not valid_paths:
            logger.warning("No valid image paths")
            return []
        
        # Build prompts: Each (image, question) is an independent sample
        # Total samples = N_images × M_questions
        # vLLM will load images directly from file paths
        prompts = []
        prompt_metadata = []  # Track (image_idx, question_key, image_path)
        
        # Target size matching training data (CholecInstanceSeg: 854×480)
        # IMPORTANT: Qwen2-VL requires size aligned to 28 (patch_size=14, merge_size=2)
        # fetch_image in qwen_vl_utils aligns to 28: 854->840, 480->476
        TARGET_SIZE = (840, 476)
        
        for img_idx, image_path in enumerate(valid_paths):
            # Load image as PIL Image object and resize to training size
            try:
                pil_image = Image.open(image_path).convert("RGB")
                original_size = pil_image.size
                # Resize to match training data size for accurate bbox prediction
                pil_image = pil_image.resize(TARGET_SIZE, Image.LANCZOS)
                logger.debug(f"Resized {image_path}: {original_size} -> {TARGET_SIZE}")
            except Exception as e:
                logger.error(f"Failed to load image {image_path}: {e}")
                continue
            
            for q_key, question in questions_dict.items():
                prompt_text = self._build_prompt(question)
                # Use PIL Image object for vLLM 0.10+
                prompts.append({
                    "prompt": prompt_text,
                    "multi_modal_data": {
                        "image": pil_image  # PIL Image object
                    }
                })
                prompt_metadata.append((img_idx, q_key, image_path))
        
        total_samples = len(prompts)
        logger.info(f"Batch processing: {len(valid_paths)} images × {len(questions_dict)} questions = {total_samples} samples")
        
        # Send all prompts to vLLM in one batch call
        try:
            outputs = self.llm.generate(prompts, sampling_params)
            logger.info(f"vLLM batch inference completed: {len(outputs)} outputs")
        except Exception as e:
            logger.error(f"vLLM batch generate failed: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            # Return error for all images
            results = []
            for image_path in valid_paths:
                results.append(SingleImageResult(
                    image_path=image_path,
                    responses={k: f"[Error: {str(e)}]" for k in questions_dict.keys()}
                ))
            return results
        
        # Organize results by image
        results_by_image: Dict[int, Dict[str, str]] = {}
        image_paths_map: Dict[int, str] = {}
        
        for output, (img_idx, q_key, image_path) in zip(outputs, prompt_metadata):
            if img_idx not in results_by_image:
                results_by_image[img_idx] = {}
                image_paths_map[img_idx] = image_path
            
            if output.outputs:
                generated_text = output.outputs[0].text
            else:
                generated_text = ""
            results_by_image[img_idx][q_key] = generated_text
        
        # Build final results list (preserving image order)
        results = []
        for img_idx in sorted(results_by_image.keys()):
            results.append(SingleImageResult(
                image_path=image_paths_map[img_idx],
                responses=results_by_image[img_idx]
            ))
        
        logger.info(f"Completed: {len(results)} images, {total_samples} total samples")
        return results


# Global model instance
model = SurgR1Model()


# ============================================================================
# FastAPI Application
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for model loading"""
    # Load model on startup
    logger.info("Starting SurgR1 API server...")
    model.load()
    yield
    # Cleanup on shutdown
    logger.info("Shutting down SurgR1 API server...")


app = FastAPI(
    title="SurgR1 API",
    description="Surgical Image Analysis API using vLLM",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy" if model.is_loaded() else "loading",
        model_loaded=model.is_loaded(),
        model_path=model.model_path
    )


@app.get("/v1/models")
async def list_models():
    """OpenAI-compatible models listing"""
    return {
        "object": "list",
        "data": [
            {
                "id": "SurgR1",
                "object": "model",
                "created": 1700000000,
                "owned_by": "local"
            }
        ]
    }


@app.post("/analyze", response_model=BatchAnalysisResponse)
async def analyze_images(request: ImageAnalysisRequest):
    """
    Analyze a batch of images with surgical questions.
    
    Each image is analyzed with 3 questions by default:
    1. Tool localization (bounding boxes)
    2. Surgical action description  
    3. Surgical phase identification
    
    Returns responses in order: [image1, image2, ...] where each image has all 3 responses.
    """
    if not model.is_loaded():
        raise HTTPException(status_code=503, detail="Model not loaded yet")
    
    if not request.image_paths:
        raise HTTPException(status_code=400, detail="No image paths provided")
    
    # Validate paths exist
    valid_paths = []
    for path in request.image_paths:
        if Path(path).exists():
            valid_paths.append(path)
        else:
            logger.warning(f"Image path not found: {path}")
    
    if not valid_paths:
        raise HTTPException(status_code=400, detail="No valid image paths found")
    
    try:
        results = model.analyze_batch(
            image_paths=valid_paths,
            questions=request.questions,
            temperature=request.temperature,
            max_tokens=request.max_tokens
        )
        
        num_questions = len(request.questions) if request.questions else 3
        
        return BatchAnalysisResponse(
            results=results,
            total_images=len(results),
            total_questions=len(results) * num_questions
        )
        
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/analyze_single")
async def analyze_single_image(image_path: str, question: Optional[str] = None):
    """
    Analyze a single image with a single question.
    
    If no question provided, returns all 3 default analysis results.
    """
    if not model.is_loaded():
        raise HTTPException(status_code=503, detail="Model not loaded yet")
    
    if not Path(image_path).exists():
        raise HTTPException(status_code=400, detail=f"Image not found: {image_path}")
    
    try:
        questions = [question] if question else None
        results = model.analyze_batch(
            image_paths=[image_path],
            questions=questions
        )
        
        if results:
            return results[0]
        else:
            raise HTTPException(status_code=500, detail="No results generated")
            
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Run the server"""
    server_config = CONFIG.get("server", {})
    host = server_config.get("host", "0.0.0.0")
    port = server_config.get("port", 9001)
    
    logger.info(f"Starting SurgR1 API on {host}:{port}")
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        workers=1
    )


if __name__ == "__main__":
    main()



