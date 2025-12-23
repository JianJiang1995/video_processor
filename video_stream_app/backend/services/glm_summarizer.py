"""
GLM-4.6V-Flash Summarization Service
Uses GLM-4.6V-Flash via vLLM server for multimodal summarization
"""
import asyncio
import base64
import json
from io import BytesIO
from typing import List, Optional, Dict, Any, Union
from PIL import Image
import httpx
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GLMSummarizer:
    """
    GLM-4.6V-Flash based summarization service
    
    Uses GLM-4.6V-Flash via vLLM server (OpenAI-compatible API)
    for multimodal content summarization and integration.
    """
    
    def __init__(
        self,
        api_url: str = "http://localhost:8000/v1",
        model_name: str = "GLM-4.6V-Flash",
        temperature: float = 0.7,
        max_tokens: int = 1000
    ):
        """
        Initialize GLM Summarizer
        
        Args:
            api_url: vLLM server API URL (OpenAI-compatible)
            model_name: Model name on the server
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
        """
        self.api_url = api_url.rstrip('/')
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = None
        
        logger.info(f"[GLMSummarizer] Initialized with API: {self.api_url}")
        logger.info(f"[GLMSummarizer] Model: {self.model_name}")
    
    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=180.0)
        return self._client
    
    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def check_health(self) -> bool:
        """Check if GLM service is available"""
        try:
            response = await self.client.get(f"{self.api_url}/models")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"[GLMSummarizer] Health check failed: {e}")
            return False
    
    def _image_to_base64_url(self, image: Union[str, Image.Image, bytes]) -> str:
        """Convert image to base64 data URL for OpenAI-compatible API"""
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
    
    async def summarize_window(
        self,
        images: List[Image.Image],
        context: str = "",
        system_prompt: str = None,
        max_tokens: int = None,
        temperature: float = None
    ) -> Dict[str, Any]:
        """
        Generate summary for a video window using GLM-4.6V-Flash
        
        Args:
            images: List of frame images
            context: Additional context about the frames (e.g., frame analysis results)
            system_prompt: Custom system prompt
            max_tokens: Maximum response tokens
            temperature: Sampling temperature
            
        Returns:
            Dict with summary text and metadata
        """
        max_tokens = max_tokens or self.max_tokens
        temperature = temperature or self.temperature
        
        # Default system prompt for surgical video analysis
        if system_prompt is None:
            system_prompt = """You are an expert surgical video analyst specializing in laparoscopic procedures. Your task is to analyze a video segment (5-second window) and generate a concise narrative summary.

## Your Task

Given frames from a 5-second video window, you must:
1. **Synthesize Information**: Understand the temporal progression across frames
2. **Generate Narrative**: Produce a clear, concise description of what happens in this segment

## Output Format

Provide a single paragraph (2-4 sentences) describing:
- Current surgical phase or action
- Tools visible and their usage
- Key observations about the procedure

## Guidelines

1. **Be Concise**: Focus on the most important observations
2. **Use Temporal Markers**: "Initially", "Then", "Throughout"
3. **Maintain Clinical Accuracy**: Use proper surgical terminology
4. **Be Confident**: Write as an observer seeing one clear reality

Output only the summary, no additional formatting."""
        
        # Build message content with images
        content = []
        
        # Add images (GLM-4.6V supports multiple images)
        max_images = min(len(images), 10)  # Support up to 10 images
        step = max(1, len(images) // max_images) if len(images) > max_images else 1
        
        for i in range(0, len(images), step):
            if len(content) >= max_images * 2:  # Each image adds 2 items
                break
            
            image_url = self._image_to_base64_url(images[i])
            content.append({
                "type": "image_url",
                "image_url": {"url": image_url, "detail": "low"}
            })
        
        # Add context text
        prompt_text = "Analyze these video frames and provide a concise summary."
        if context:
            prompt_text += f"\n\nAdditional context:\n{context}"
        
        content.append({"type": "text", "text": prompt_text})
        
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content}
            ],
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        try:
            response = await self.client.post(
                f"{self.api_url}/chat/completions",
                json=payload
            )
            response.raise_for_status()
            
            data = response.json()
            summary_text = data["choices"][0]["message"]["content"].strip()
            token_count = data.get("usage", {}).get("total_tokens", 0)
            
            return {
                "success": True,
                "summary": summary_text,
                "model": self.model_name,
                "tokens_used": token_count
            }
            
        except Exception as e:
            logger.error(f"[GLMSummarizer] Summarization error: {e}")
            return {
                "success": False,
                "summary": f"[Error generating summary: {str(e)}]",
                "model": self.model_name,
                "error": str(e)
            }
    
    async def integrate_analysis_results(
        self,
        frame_analyses: List[Dict[str, Any]],
        images: Optional[List[Image.Image]] = None,
        system_prompt: str = None,
        max_tokens: int = None,
        temperature: float = None
    ) -> Dict[str, Any]:
        """
        Integrate multiple frame analysis results into a coherent summary
        
        Args:
            frame_analyses: List of frame analysis results, each containing:
                - frame_idx, timestamp
                - phase, action, tools (or other analysis fields)
            images: Optional list of frame images for visual context
            system_prompt: Custom system prompt
            max_tokens: Maximum response tokens
            temperature: Sampling temperature
            
        Returns:
            Dict with integrated summary
        """
        max_tokens = max_tokens or self.max_tokens
        temperature = temperature or self.temperature
        
        # Default system prompt for integration
        if system_prompt is None:
            system_prompt = """You are an expert surgical video analyst. You will receive frame-by-frame analysis results from a 5-second video window. Your task is to synthesize these analyses into a coherent, concise narrative summary.

## Your Task

Given multiple frame analyses, integrate them into a single paragraph (2-4 sentences) that:
1. Describes the overall surgical phase/action
2. Identifies key tools and their usage patterns
3. Highlights important observations

## Guidelines

- Synthesize temporal information across frames
- Focus on the most important and consistent observations
- Use proper surgical terminology
- Be concise and clear

Output only the summary, no additional formatting."""
        
        # Build context from frame analyses
        context_parts = ["## Frame-by-Frame Analysis Results\n"]
        for i, analysis in enumerate(frame_analyses):
            context_parts.append(f"### Frame {i+1} (t={analysis.get('timestamp', 0):.2f}s)")
            if analysis.get('phase'):
                context_parts.append(f"**Phase:** {analysis['phase']}")
            if analysis.get('action'):
                context_parts.append(f"**Action:** {analysis['action']}")
            if analysis.get('tools'):
                context_parts.append(f"**Tools:** {analysis['tools'][:200]}")
            context_parts.append("")
        
        context = "\n".join(context_parts)
        
        # Build message content
        content = []
        
        # Add images if provided
        if images:
            max_images = min(len(images), 5)
            step = max(1, len(images) // max_images) if len(images) > max_images else 1
            
            for i in range(0, len(images), step):
                if len(content) >= max_images * 2:
                    break
                image_url = self._image_to_base64_url(images[i])
                content.append({
                    "type": "image_url",
                    "image_url": {"url": image_url, "detail": "low"}
                })
        
        # Add context text
        prompt_text = "Please integrate the following frame-by-frame analysis results into a coherent summary:\n\n" + context
        content.append({"type": "text", "text": prompt_text})
        
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content}
            ],
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        try:
            response = await self.client.post(
                f"{self.api_url}/chat/completions",
                json=payload
            )
            response.raise_for_status()
            
            data = response.json()
            summary_text = data["choices"][0]["message"]["content"].strip()
            token_count = data.get("usage", {}).get("total_tokens", 0)
            
            return {
                "success": True,
                "summary": summary_text,
                "model": self.model_name,
                "tokens_used": token_count,
                "frame_count": len(frame_analyses)
            }
            
        except Exception as e:
            logger.error(f"[GLMSummarizer] Integration error: {e}")
            return {
                "success": False,
                "summary": f"[Error integrating results: {str(e)}]",
                "model": self.model_name,
                "error": str(e)
            }


# Global instance
_glm_summarizer: Optional[GLMSummarizer] = None


def get_glm_summarizer() -> GLMSummarizer:
    """Get the global GLM summarizer instance"""
    global _glm_summarizer
    if _glm_summarizer is None:
        # Try to load settings, but use defaults if unavailable
        try:
            from ..config import settings
            _glm_summarizer = GLMSummarizer(
                api_url=settings.GLM_API_URL,
                model_name=settings.GLM_MODEL_NAME,
                temperature=settings.GLM_TEMPERATURE,
                max_tokens=settings.GLM_MAX_TOKENS
            )
        except Exception:
            # Use defaults from glm_client
            from .glm_client import get_glm_client
            glm_client = get_glm_client()
            _glm_summarizer = GLMSummarizer(
                api_url=glm_client.api_url,
                model_name=glm_client.model_name,
                temperature=glm_client.temperature,
                max_tokens=glm_client.max_tokens
            )
    return _glm_summarizer

