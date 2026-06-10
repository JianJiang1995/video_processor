"""
VLM Model Service Client
Calls the external Swift-deployed model API (OpenAI-compatible)
"""
import os
import json
import time
import base64
import asyncio
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from dataclasses import dataclass
from PIL import Image
import httpx
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load config
CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"


def load_config() -> dict:
    """Load configuration from config.json"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    return {}


@dataclass
class InferenceResult:
    """Single inference result"""
    answer: str
    inference_time: float
    token_count: int = 0


class VLMModelClient:
    """
    VLM Model API Client
    
    Connects to Swift-deployed model service (OpenAI-compatible API).
    Supports batch inference for 5-second window processing.
    """
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if VLMModelClient._initialized:
            return
        
        # Load config
        config = load_config()
        model_config = config.get("services", {}).get("model", {})
        analysis_config = config.get("analysis", {})
        
        self.api_url = model_config.get("api_url", "http://localhost:9000/v1")
        self.model_name = model_config.get("model_name", "SurgiCoT")
        
        # Analysis settings
        self.temperature = analysis_config.get("temperature", 0.1)
        self.max_tokens = analysis_config.get("max_tokens", 512)
        self.questions = analysis_config.get("questions", {})
        
        # HTTP client
        self._client = None
        
        VLMModelClient._initialized = True
        logger.info(f"[VLMModelClient] Initialized with API: {self.api_url}")
        logger.info(f"[VLMModelClient] Model: {self.model_name}")
    
    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client
    
    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def check_health(self) -> bool:
        """Check if model service is available"""
        try:
            response = await self.client.get(f"{self.api_url}/models")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"[VLMModelClient] Health check failed: {e}")
            return False
    
    async def get_models(self) -> List[str]:
        """Get available models"""
        try:
            response = await self.client.get(f"{self.api_url}/models")
            if response.status_code == 200:
                data = response.json()
                return [m["id"] for m in data.get("data", [])]
            return []
        except Exception as e:
            logger.error(f"[VLMModelClient] Failed to get models: {e}")
            return []
    
    def _image_to_base64_url(self, image: Union[str, Image.Image, bytes]) -> str:
        """Convert image to base64 data URL for OpenAI API"""
        if isinstance(image, str):
            # Already base64 or URL
            if image.startswith("data:image") or image.startswith("http"):
                return image
            # Raw base64, add prefix
            return f"data:image/jpeg;base64,{image}"
        
        if isinstance(image, bytes):
            b64 = base64.b64encode(image).decode()
            return f"data:image/jpeg;base64,{b64}"
        
        if isinstance(image, Image.Image):
            # Convert PIL to base64
            if image.mode == 'RGBA':
                image = image.convert('RGB')
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=85)
            b64 = base64.b64encode(buffer.getvalue()).decode()
            return f"data:image/jpeg;base64,{b64}"
        
        raise ValueError(f"Unsupported image type: {type(image)}")
    
    async def infer_single(
        self,
        image: Union[str, Image.Image, bytes],
        question: str,
        temperature: float = None,
        max_tokens: int = None
    ) -> InferenceResult:
        """
        Single image inference via OpenAI-compatible API
        
        Args:
            image: Image data
            question: Question text
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            
        Returns:
            InferenceResult with answer
        """
        temperature = temperature or self.temperature
        max_tokens = max_tokens or self.max_tokens
        
        image_url = self._image_to_base64_url(image)
        
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": question}
                    ]
                }
            ],
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        start_time = time.time()
        
        try:
            response = await self.client.post(
                f"{self.api_url}/chat/completions",
                json=payload
            )
            response.raise_for_status()
            
            data = response.json()
            answer = data["choices"][0]["message"]["content"]
            token_count = data.get("usage", {}).get("completion_tokens", 0)
            
            inference_time = time.time() - start_time
            
            return InferenceResult(
                answer=answer,
                inference_time=inference_time,
                token_count=token_count
            )
            
        except Exception as e:
            logger.error(f"[VLMModelClient] Inference failed: {e}")
            return InferenceResult(
                answer=f"[Error: {str(e)}]",
                inference_time=time.time() - start_time,
                token_count=0
            )
    
    async def infer_batch(
        self,
        images: List[Union[str, Image.Image, bytes]],
        questions: List[str],
        temperature: float = None,
        max_tokens: int = None
    ) -> List[InferenceResult]:
        """
        Batch inference for N=5s window (multiple frames)
        
        Sends concurrent requests to the model service.
        
        Args:
            images: List of frame images
            questions: List of questions (same length as images, or single for all)
            
        Returns:
            List of InferenceResult
        """
        # Handle single question for all images
        if len(questions) == 1 and len(images) > 1:
            questions = questions * len(images)
        
        if len(images) != len(questions):
            raise ValueError(f"Images ({len(images)}) and questions ({len(questions)}) mismatch")
        
        logger.info(f"[VLMModelClient] Batch inference: {len(images)} requests")
        start_time = time.time()
        
        # Create tasks for concurrent execution
        tasks = [
            self.infer_single(img, q, temperature, max_tokens)
            for img, q in zip(images, questions)
        ]
        
        results = await asyncio.gather(*tasks)
        
        total_time = time.time() - start_time
        logger.info(f"[VLMModelClient] Batch completed in {total_time:.2f}s")
        
        return results
    
    async def analyze_frame(
        self,
        image: Union[str, Image.Image, bytes],
        analysis_type: str = "all"
    ) -> Dict[str, str]:
        """
        Analyze a surgical frame with predefined questions
        
        Args:
            image: Frame image
            analysis_type: "all", "phase", "action", or "tools"
            
        Returns:
            Dict with analysis results
        """
        questions = self.questions or {
            "phase": "Based on this surgical image, identify the current surgical phase.",
            "action": "Describe the main surgical action being performed.",
            "tools": "Identify all visible surgical instruments."
        }
        
        if analysis_type == "all":
            selected = list(questions.items())
        elif analysis_type in questions:
            selected = [(analysis_type, questions[analysis_type])]
        else:
            raise ValueError(f"Unknown analysis type: {analysis_type}")
        
        results = {}
        for q_type, question in selected:
            result = await self.infer_single(image, question)
            results[q_type] = result.answer
        
        return results
    
    async def analyze_window(
        self,
        images: List[Union[str, Image.Image, bytes]],
        analysis_type: str = "phase"
    ) -> List[Dict[str, Any]]:
        """
        Analyze a 5-second window (N frames) efficiently
        
        Sends batch request for all frames with same question.
        
        Args:
            images: List of frame images
            analysis_type: Type of analysis
            
        Returns:
            List of analysis results per frame
        """
        question = self.questions.get(
            analysis_type,
            "What is happening in this surgical image?"
        )
        
        # Batch inference with same question
        results = await self.infer_batch(
            images=images,
            questions=[question],  # Will be expanded to all images
            temperature=self.temperature,
            max_tokens=self.max_tokens
        )
        
        return [
            {
                "frame_index": i,
                analysis_type: r.answer,
                "inference_time": r.inference_time
            }
            for i, r in enumerate(results)
        ]


# Global client instance
_model_client: Optional[VLMModelClient] = None


def get_model_service() -> VLMModelClient:
    """Get the global model client instance"""
    global _model_client
    if _model_client is None:
        _model_client = VLMModelClient()
    return _model_client


async def ensure_model_loaded() -> VLMModelClient:
    """Get client and verify service is available"""
    client = get_model_service()
    
    # Check health
    is_healthy = await client.check_health()
    if not is_healthy:
        logger.warning("[VLMModelClient] Model service may not be available")
    
    return client
