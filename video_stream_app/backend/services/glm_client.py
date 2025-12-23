"""
GLM-4.6V-Flash API Client - Text Summarization Service
Calls the external GLM API for multimodal text summarization.
Used for integrating multiple surgical image analyses into coherent summaries.
"""
import asyncio
import json
import logging
import base64
import time
from pathlib import Path
from io import BytesIO
from typing import List, Optional, Dict, Any, Union
from PIL import Image
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Try to import API logger
try:
    from ..middleware import log_glm_call
except ImportError:
    def log_glm_call(*args, **kwargs):
        pass

# Load config
CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"
# Background knowledge file is in glm_api folder
BACKGROUND_PATH = Path(__file__).parent.parent.parent.parent / "glm_api" / "background.txt"


def load_config() -> dict:
    """Load configuration from config.json"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    return {}


def load_background_knowledge() -> str:
    """Load background knowledge from background.txt for surgical domain context"""
    if BACKGROUND_PATH.exists():
        with open(BACKGROUND_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    logger.warning(f"[GLMClient] Background knowledge file not found: {BACKGROUND_PATH}")
    return ""


# Cache background knowledge at module load time
_background_knowledge: str = None


def get_background_knowledge() -> str:
    """Get cached background knowledge"""
    global _background_knowledge
    if _background_knowledge is None:
        _background_knowledge = load_background_knowledge()
        if _background_knowledge:
            logger.info(f"[GLMClient] Loaded background knowledge ({len(_background_knowledge)} chars)")
    return _background_knowledge


class GLMClient:
    """
    GLM-4.6V-Flash API Client
    
    Calls the external GLM-4.6V-Flash service via vLLM (OpenAI-compatible API)
    for multimodal content summarization and text generation.
    
    Features:
    - Text summarization
    - Multi-image analysis integration
    - Temporal/sequential analysis of video frames
    - Disable thinking mode for faster responses
    """
    
    # System prompt to disable thinking mode (直接回答，不思考)
    NO_THINKING_PROMPT = "请直接回答问题，不需要进行思考过程的展示。给出简洁、直接的回答。"
    
    def __init__(
        self,
        api_url: str = None,
        model_name: str = None,
        temperature: float = None,
        max_tokens: int = None,
        timeout: float = 180.0,
        disable_thinking: bool = True  # 默认禁用思考模式加速
    ):
        config = load_config()
        glm_config = config.get("services", {}).get("glm", {})
        
        self.api_url = (api_url or glm_config.get("api_url", "http://localhost:8000/v1")).rstrip('/')
        self.model_name = model_name or glm_config.get("model_name", "GLM-4.6V-Flash")
        self.temperature = temperature or glm_config.get("temperature", 0.7)
        self.max_tokens = max_tokens or glm_config.get("max_tokens", 1000)
        self.timeout = timeout
        self.disable_thinking = disable_thinking
        self._client = None
        
        logger.info(f"[GLMClient] Initialized with API: {self.api_url}")
        logger.info(f"[GLMClient] Model: {self.model_name}")
        logger.info(f"[GLMClient] Thinking mode: {'disabled' if disable_thinking else 'enabled'}")
    
    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
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
            logger.warning(f"[GLMClient] Health check failed: {e}")
            return False
    
    def _image_to_base64_url(self, image: Union[str, Image.Image, bytes, Path]) -> str:
        """Convert image to base64 data URL for OpenAI-compatible API"""
        if isinstance(image, str):
            if image.startswith("data:image") or image.startswith("http"):
                return image
            # Treat as file path
            image = Path(image)
        
        if isinstance(image, Path):
            if image.exists():
                with open(image, "rb") as f:
                    image_data = f.read()
                suffix = image.suffix.lower()
                mime_type = 'image/jpeg' if suffix in ['.jpg', '.jpeg'] else 'image/png'
                b64 = base64.b64encode(image_data).decode()
                return f"data:{mime_type};base64,{b64}"
            else:
                raise ValueError(f"Image file not found: {image}")
        
        if isinstance(image, bytes):
            b64 = base64.b64encode(image).decode()
            return f"data:image/jpeg;base64,{b64}"
        
        if isinstance(image, Image.Image):
            if image.mode == 'RGBA':
                image = image.convert('RGB')
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=85)
            b64 = base64.b64encode(buffer.getvalue()).decode()
            return f"data:image/jpeg;base64,{b64}"
        
        raise ValueError(f"Unsupported image type: {type(image)}")
    
    async def chat(
        self,
        message: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        disable_thinking: Optional[bool] = None
    ) -> Dict[str, Any]:
        """
        Simple text chat without images.
        
        Args:
            message: User message
            system_prompt: Optional system prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            disable_thinking: Override thinking mode setting
            
        Returns:
            Dict with response text
        """
        start_time = time.time()
        temperature = temperature or self.temperature
        max_tokens = max_tokens or self.max_tokens
        disable_thinking = disable_thinking if disable_thinking is not None else self.disable_thinking
        
        messages = []
        
        # Add no-thinking prompt if disabled
        if disable_thinking:
            base_prompt = self.NO_THINKING_PROMPT
            if system_prompt:
                base_prompt = f"{self.NO_THINKING_PROMPT}\n\n{system_prompt}"
            messages.append({"role": "system", "content": base_prompt})
        elif system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        messages.append({"role": "user", "content": message})
        
        try:
            payload = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            
            response = await self.client.post(
                f"{self.api_url}/chat/completions",
                json=payload
            )
            response.raise_for_status()
            
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            duration_ms = (time.time() - start_time) * 1000
            
            # Log the API call
            log_glm_call(
                prompt_preview=message[:100],
                response={"text": text},
                duration_ms=duration_ms
            )
            
            return {
                "success": True,
                "text": text,
                "tokens_used": tokens,
                "model": self.model_name,
                "thinking_disabled": disable_thinking,
                "duration_ms": duration_ms
            }
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            log_glm_call(
                prompt_preview=message[:100],
                response={},
                duration_ms=duration_ms,
                error=str(e)
            )
            logger.error(f"[GLMClient] Chat failed: {e}")
            return {
                "success": False,
                "text": f"[Error: {str(e)}]",
                "error": str(e)
            }
    
    async def chat_with_context(
        self,
        user_query: str,
        surgical_context: str = "",
        conversation_history: List[Dict[str, str]] = None,
        system_prompt: Optional[str] = None,
        disable_thinking: bool = True,
        include_background: bool = True
    ) -> Dict[str, Any]:
        """
        Chat with surgical context from MySQL database.
        
        Args:
            user_query: User's question
            surgical_context: Context from SurgR1 analysis results
            conversation_history: Previous conversation messages
            system_prompt: Custom system prompt
            disable_thinking: Disable thinking for faster response
            include_background: Whether to include domain background knowledge
            
        Returns:
            Dict with response
        """
        if system_prompt is None:
            # Build system prompt with background knowledge
            base_prompt = """你是一个专业的腹腔镜胆囊切除手术助手，可以回答关于当前手术过程的问题。

你可以访问最近的手术图像分析记录，包括：
- 手术阶段（Phase Recognition）：Preparation, CalotTriangleDissection, ClippingCutting, GallbladderDissection, GallbladderRetraction, CleaningCoagulation, GallbladderPackaging
- 手术动作（Action Recognition）：医生正在进行的操作
- 工具定位（Tool Recognition）：当前使用的手术器械位置
- 组织识别（Tissue Recognition）：可见的解剖结构
- CVS评估（Critical View of Safety）：安全关键视角的三个标准

请根据提供的分析记录和用户的问题给出准确、简洁的回答。
如果用户询问"之前做了什么"或类似问题，请总结最近的手术操作。
如果用户询问CVS相关问题，请基于三个标准进行判断。"""
            
            # Append background knowledge if enabled
            if include_background:
                background = get_background_knowledge()
                if background:
                    # Extract key sections for context (avoid full doc in every request)
                    system_prompt = f"""{base_prompt}

## 领域知识参考

你具有以下腹腔镜胆囊切除术的专业知识：
- 手术包含7个阶段：Preparation → CalotTriangleDissection → ClippingCutting → GallbladderDissection → GallbladderRetraction → CleaningCoagulation → GallbladderPackaging
- CVS（安全关键视角）三个标准必须全部满足才能确认CVS=TRUE：
  1. 只有两个管状结构（胆囊管和胆囊动脉）连接到胆囊
  2. 肝胆三角区域已清理，可见底下肝脏
  3. 胆囊下1/3已从肝床分离
- 常见工具：Grasper(抓钳), Hook(电钩), Scissors(剪刀), Clipper(钛夹钳), Irrigator(冲吸器), Bipolar(双极电凝)
- 关键组织：Cystic Duct(胆囊管), Cystic Artery(胆囊动脉), Gallbladder(胆囊), Calot Triangle(Calot三角), Cystic Plate(胆囊板)"""
                else:
                    system_prompt = base_prompt
            else:
                system_prompt = base_prompt
        
        # Build message with context
        full_message = user_query
        if surgical_context:
            full_message = f"{surgical_context}\n\n用户问题: {user_query}"
        
        result = await self.chat(
            message=full_message,
            system_prompt=system_prompt,
            disable_thinking=disable_thinking
        )
        
        return result
    
    async def analyze_image(
        self,
        image: Union[str, Image.Image, bytes, Path],
        question: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Analyze a single image.
        
        Args:
            image: Image (path, PIL Image, bytes, or URL)
            question: Question about the image
            system_prompt: Optional system prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens
            
        Returns:
            Dict with analysis result
        """
        temperature = temperature or self.temperature
        max_tokens = max_tokens or self.max_tokens
        
        try:
            image_url = self._image_to_base64_url(image)
            
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            
            messages.append({
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": question}
                ]
            })
            
            payload = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            
            response = await self.client.post(
                f"{self.api_url}/chat/completions",
                json=payload
            )
            response.raise_for_status()
            
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            
            return {
                "success": True,
                "text": text,
                "tokens_used": tokens,
                "model": self.model_name
            }
            
        except Exception as e:
            logger.error(f"[GLMClient] Image analysis failed: {e}")
            return {
                "success": False,
                "text": f"[Error: {str(e)}]",
                "error": str(e)
            }
    
    async def analyze_multiple_images(
        self,
        images: List[Union[str, Image.Image, bytes, Path]],
        question: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Analyze multiple images together.
        
        Args:
            images: List of images
            question: Question about the images
            system_prompt: Optional system prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens
            
        Returns:
            Dict with analysis result
        """
        temperature = temperature or self.temperature
        max_tokens = max_tokens or self.max_tokens
        
        try:
            content = []
            
            # Add images (limit to 10)
            for img in images[:10]:
                image_url = self._image_to_base64_url(img)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": image_url, "detail": "low"}
                })
            
            content.append({"type": "text", "text": question})
            
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": content})
            
            payload = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            
            response = await self.client.post(
                f"{self.api_url}/chat/completions",
                json=payload
            )
            response.raise_for_status()
            
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            
            return {
                "success": True,
                "text": text,
                "tokens_used": tokens,
                "model": self.model_name,
                "image_count": len(images[:10])
            }
            
        except Exception as e:
            logger.error(f"[GLMClient] Multi-image analysis failed: {e}")
            return {
                "success": False,
                "text": f"[Error: {str(e)}]",
                "error": str(e)
            }
    
    async def summarize_window(
        self,
        images: List[Image.Image],
        context: str = "",
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Generate summary for a video window (5-second segment).
        
        Args:
            images: List of frame images
            context: Additional context (e.g., frame analysis results)
            system_prompt: Custom system prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens
            
        Returns:
            Dict with summary
        """
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
        
        prompt_text = "Analyze these video frames and provide a concise summary."
        if context:
            prompt_text += f"\n\nAdditional context:\n{context}"
        
        result = await self.analyze_multiple_images(
            images=images,
            question=prompt_text,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        return {
            "success": result.get("success", False),
            "summary": result.get("text", ""),
            "model": self.model_name,
            "tokens_used": result.get("tokens_used", 0),
            "error": result.get("error")
        }
    
    async def integrate_analysis_results(
        self,
        frame_analyses: List[Dict[str, Any]],
        images: Optional[List[Image.Image]] = None,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        include_background: bool = True
    ) -> Dict[str, Any]:
        """
        Integrate multiple frame analysis results into a coherent summary.
        
        Args:
            frame_analyses: List of frame analysis results with phase, action, tools
            images: Optional list of frame images for visual context
            system_prompt: Custom system prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens
            include_background: Whether to include domain background knowledge
            
        Returns:
            Dict with integrated summary
        """
        if system_prompt is None:
            system_prompt = """You are an expert surgical video analyst specializing in Laparoscopic Cholecystectomy. You will receive frame-by-frame analysis results from a 5-second video window. Your task is to synthesize these analyses into a coherent, concise narrative summary.

## Domain Knowledge

This is a Laparoscopic Cholecystectomy procedure with 7 phases:
- Preparation, CalotTriangleDissection, ClippingCutting, GallbladderDissection, GallbladderRetraction, CleaningCoagulation, GallbladderPackaging

Key anatomical structures: Cystic Duct, Cystic Artery, Gallbladder, Calot Triangle, Cystic Plate

Tools: Grasper, Hook, Scissors, Clipper, Irrigator, Bipolar Forceps

## Critical View of Safety (CVS)

CVS = TRUE only when ALL three criteria are met:
1. Only two tubular structures (cystic duct + cystic artery) connect to gallbladder
2. Hepatocystic triangle cleared - liver visible through triangle
3. Lower third of gallbladder detached from liver bed

## Temporal Consistency Rules

When synthesizing frame analyses:
1. Resolve intra-frame contradictions (e.g., conflicting tool/phase predictions within same frame)
2. Resolve inter-frame contradictions (e.g., sudden phase changes, tools appearing/disappearing)
3. Use majority voting or temporal smoothing for inconsistent predictions
4. Phase progression should be logical (rarely goes backward except for complications)

## Your Task

Given multiple frame analyses, integrate them into a single paragraph (2-4 sentences) that:
1. Describes the overall surgical phase/action (resolve contradictions)
2. Identifies key tools and their usage patterns
3. Highlights important observations including CVS status if relevant

## Guidelines

- Synthesize temporal information across frames
- Resolve contradictions between frames using temporal consistency
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
        prompt_text = "Please integrate the following frame-by-frame analysis results into a coherent summary:\n\n" + context
        
        if images:
            result = await self.analyze_multiple_images(
                images=images,
                question=prompt_text,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens
            )
        else:
            result = await self.chat(
                message=prompt_text,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens
            )
        
        return {
            "success": result.get("success", False),
            "summary": result.get("text", ""),
            "model": self.model_name,
            "tokens_used": result.get("tokens_used", 0),
            "frame_count": len(frame_analyses),
            "error": result.get("error")
        }


# Global client instance
_glm_client: Optional[GLMClient] = None


def get_glm_client() -> GLMClient:
    """Get the global GLM client instance"""
    global _glm_client
    if _glm_client is None:
        _glm_client = GLMClient()
    return _glm_client


async def ensure_glm_available() -> GLMClient:
    """Get client and verify service is available"""
    client = get_glm_client()
    
    is_healthy = await client.check_health()
    if not is_healthy:
        logger.warning("[GLMClient] GLM service may not be available")
    
    return client

