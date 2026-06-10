"""
Gemini 3.1 Pro API Client - Multimodal Analysis Service
Calls Google Gemini API for multimodal surgical video analysis.
Used for integrating multiple surgical image analyses into coherent summaries.

基于官方文档: https://ai.google.dev/gemini-api/docs/gemini-3

支持功能:
- 多模态分析（图片+文本）
- R1帧分析结果整合
- 历史窗口上下文
- thinking_level 控制推理深度
"""
import asyncio
import json
import logging
import base64
import time
import os
import re
from pathlib import Path
from io import BytesIO
from typing import List, Optional, Dict, Any, Union, Tuple
from PIL import Image
from dataclasses import dataclass

# Load .env file for API keys
try:
    from dotenv import load_dotenv
    # Try multiple possible .env locations
    possible_env_paths = [
        Path(__file__).parent.parent.parent / ".env",  # video_stream_app/.env
        Path(__file__).parent.parent.parent.parent / ".env",  # video_processor/.env (project root)
    ]
    for env_path in possible_env_paths:
        if env_path.exists():
            load_dotenv(env_path)
            break
except ImportError:
    pass  # python-dotenv not installed, rely on system env vars

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Try to import Google GenAI SDK
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    logger.warning("[GeminiClient] google-genai SDK not installed. Run: pip install google-genai")

# Load config
CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"
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
    logger.warning(f"[GeminiClient] Background knowledge file not found: {BACKGROUND_PATH}")
    return ""


# Cache background knowledge
_background_knowledge: str = None
_gemini_system_prompt: str = None


def get_background_knowledge() -> str:
    """Get cached background knowledge"""
    global _background_knowledge
    if _background_knowledge is None:
        _background_knowledge = load_background_knowledge()
        if _background_knowledge:
            logger.info(f"[GeminiClient] Loaded background knowledge ({len(_background_knowledge)} chars)")
    return _background_knowledge


def get_gemini_system_prompt(reload: bool = False) -> str:
    """
    从background.txt中提取GLM窗口分析的system prompt
    
    在 [GLM_SYSTEM_PROMPT_START] 和 [GLM_SYSTEM_PROMPT_END] 之间的内容
    """
    global _gemini_system_prompt, _background_knowledge
    
    if reload:
        _gemini_system_prompt = None
        _background_knowledge = None
    
    if _gemini_system_prompt is None:
        background = get_background_knowledge()
        if background:
            start_marker = "[GLM_SYSTEM_PROMPT_START]"
            end_marker = "[GLM_SYSTEM_PROMPT_END]"
            start_idx = background.find(start_marker)
            end_idx = background.find(end_marker)
            
            if start_idx != -1 and end_idx != -1:
                _gemini_system_prompt = background[start_idx + len(start_marker):end_idx].strip()
                logger.info(f"[GeminiClient] Loaded system prompt ({len(_gemini_system_prompt)} chars)")
            else:
                logger.warning("[GeminiClient] System prompt markers not found in background.txt")
                _gemini_system_prompt = ""
        else:
            _gemini_system_prompt = ""
    return _gemini_system_prompt


@dataclass
class GeminiConcurrentConfig:
    """Gemini 并发处理配置"""
    max_concurrent: int = 8  # 最大并发请求数
    retry_count: int = 2     # 失败重试次数
    retry_delay: float = 0.5 # 重试延迟（秒）


@dataclass
class WindowSummary:
    """单个窗口的摘要信息"""
    window_id: int
    start_time: float
    end_time: float
    summary: str
    dominant_phase: str
    tools: List[str]
    cvs_status: str = "未评估"
    others: Dict[str, Any] = None  # 新增: 存储结构化分析数据


class WindowHistoryManager:
    """
    窗口历史摘要管理器
    使用滑动窗口机制保存最近N个窗口的摘要（N从配置读取）
    
    新增功能：
    - 阶段连续确认机制：连续N个窗口判断为同一阶段后才确认该阶段
    - 阶段转换验证：使用 ALLOWED_TRANSITIONS 规则表验证阶段转换合法性
    """
    
    # 连续确认阈值：连续N个窗口判断为同一阶段后才确认
    CONSECUTIVE_CONFIRM_THRESHOLD = 2
    
    PHASE_ORDER = {
        "Preparation": 0, "准备阶段": 0,
        "CalotTriangleDissection": 1, "肝胆三角解剖阶段": 1,
        "ClippingCutting": 2, "夹闭切断阶段": 2,
        "GallbladderDissection": 3, "胆囊分离阶段": 3,
        "GallbladderRetraction": 4, "胆囊牵拉阶段": 4,
        "CleaningCoagulation": 5, "清洁凝血阶段": 5,
        "GallbladderPackaging": 6, "胆囊取出阶段": 6,
    }
    
    PHASE_CN_NAMES = {
        "Preparation": "准备阶段",
        "CalotTriangleDissection": "肝胆三角解剖阶段",
        "ClippingCutting": "夹闭切断阶段",
        "GallbladderDissection": "胆囊分离阶段",
        "GallbladderRetraction": "胆囊牵拉阶段",
        "CleaningCoagulation": "清洁凝血阶段",
        "GallbladderPackaging": "胆囊取出阶段",
        "Unknown": "未知阶段"
    }
    
    # 阶段转换规则表：定义每个阶段后允许出现的阶段
    ALLOWED_TRANSITIONS = {
        "Preparation": ["CalotTriangleDissection", "GallbladderRetraction"],
        "CalotTriangleDissection": ["ClippingCutting", "GallbladderRetraction", "Preparation"],
        "ClippingCutting": ["GallbladderDissection", "CalotTriangleDissection"],
        "GallbladderDissection": ["CleaningCoagulation", "GallbladderRetraction", "ClippingCutting"],
        "GallbladderRetraction": ["CalotTriangleDissection", "GallbladderDissection", "CleaningCoagulation"],
        "CleaningCoagulation": ["GallbladderPackaging", "GallbladderDissection"],
        "GallbladderPackaging": ["CleaningCoagulation"],  # 胆囊取出后只允许清洁凝血
    }
    
    # 按手术顺序排列，用于生成阶段进展摘要
    PHASE_DISPLAY_ORDER = [
        "Preparation", "CalotTriangleDissection", "ClippingCutting",
        "GallbladderDissection", "GallbladderRetraction",
        "CleaningCoagulation", "GallbladderPackaging",
    ]
    
    def __init__(self):
        self._history: List[WindowSummary] = []
        self._lock = asyncio.Lock()
        config = load_config()
        self._max_history_size = config.get("window_analysis", {}).get("history_window_count", 10)
        logger.info(f"[WindowHistoryManager] Initialized with max_history_size={self._max_history_size}")
        
        # 阶段连续确认计数器
        self._phase_confirm_count: Dict[str, int] = {}
        self._last_phase: str = None
        
        # 持久标记：记录所有曾经出现过的阶段（不受滑动窗口限制）
        self._reached_phases: set = set()
    
    async def add_summary(self, summary: WindowSummary) -> None:
        async with self._lock:
            self._history.append(summary)
            if len(self._history) > self._max_history_size:
                self._history.pop(0)
            if summary.dominant_phase and summary.dominant_phase != "Unknown":
                self._reached_phases.add(summary.dominant_phase)
    
    async def get_history(self) -> List[WindowSummary]:
        async with self._lock:
            return list(self._history)
    
    async def clear(self) -> None:
        async with self._lock:
            self._history.clear()
            self._phase_confirm_count.clear()
            self._last_phase = None
            self._reached_phases.clear()
    
    def get_phase_cn_name(self, phase: str) -> str:
        return self.PHASE_CN_NAMES.get(phase, phase)
    
    def update_phase_confirm_count(self, phase: str) -> int:
        """
        更新阶段连续确认计数
        
        Args:
            phase: 当前窗口的阶段
            
        Returns:
            该阶段的当前连续确认计数
        """
        if phase == self._last_phase:
            # 与上一个窗口阶段相同，累加计数
            self._phase_confirm_count[phase] = self._phase_confirm_count.get(phase, 0) + 1
        else:
            # 阶段变化，重置当前阶段计数为1
            self._phase_confirm_count[phase] = 1
        
        self._last_phase = phase
        return self._phase_confirm_count[phase]
    
    def is_phase_confirmed(self, phase: str) -> bool:
        """
        判断某阶段是否已被连续确认（达到阈值）
        
        Args:
            phase: 要检查的阶段
            
        Returns:
            True 如果该阶段已连续出现达到阈值次数
        """
        return self._phase_confirm_count.get(phase, 0) >= self.CONSECUTIVE_CONFIRM_THRESHOLD
    
    def validate_phase_transition(self, current_phase: str, history: List[WindowSummary]) -> Tuple[bool, str]:
        """
        验证阶段转换是否合法
        
        基于 ALLOWED_TRANSITIONS 规则表验证：
        - 如果某阶段已被连续确认，则后续阶段必须在其允许列表中
        - 特别地，胆囊取出阶段确认后，只允许清洁凝血阶段
        
        Args:
            current_phase: 当前窗口的阶段
            history: 历史窗口列表
            
        Returns:
            (is_valid, corrected_phase): 是否合法，以及纠正后的阶段
        """
        if not history:
            return True, current_phase
        
        last_phase = history[-1].dominant_phase if history else None
        
        # 规则1：准备阶段不可回退（一旦离开就不能回去）
        history_phases = [h.dominant_phase for h in history]
        if current_phase == "Preparation" and any(p != "Preparation" for p in history_phases):
            logger.warning(f"[WindowHistoryManager] PHASE VIOLATION: 准备阶段不可回退")
            return False, last_phase if last_phase else "CalotTriangleDissection"
        
        # 规则2：基于已确认阶段的转换约束
        # 检查"胆囊取出"是否已被连续确认
        if self.is_phase_confirmed("GallbladderPackaging"):
            allowed = self.ALLOWED_TRANSITIONS.get("GallbladderPackaging", [])
            if current_phase != "GallbladderPackaging" and current_phase not in allowed:
                logger.warning(
                    f"[WindowHistoryManager] PHASE VIOLATION: 胆囊取出阶段已确认（连续{self._phase_confirm_count.get('GallbladderPackaging', 0)}次），"
                    f"当前阶段 {current_phase} 不在允许列表 {allowed} 中"
                )
                corrected = last_phase if last_phase in allowed or last_phase == "GallbladderPackaging" else "CleaningCoagulation"
                return False, corrected
        
        return True, current_phase
    
    async def build_history_context(self) -> str:
        history = await self.get_history()
        if not history:
            return ""
        
        context_lines = []
        
        # 持久阶段进展摘要（不受滑动窗口限制，始终完整）
        if self._reached_phases:
            reached_cn = [
                self.get_phase_cn_name(p)
                for p in self.PHASE_DISPLAY_ORDER
                if p in self._reached_phases
            ]
            context_lines.append(f"## 手术已经历的阶段（全局）：{'→'.join(reached_cn)}")
            context_lines.append("")
        
        # 最近 3 个窗口的详细上下文
        recent_history = history[-3:]
        context_lines.append("## 最近窗口分析（按时间顺序）\n")
        
        for ws in recent_history:
            phase_cn = self.get_phase_cn_name(ws.dominant_phase)
            start_min = int(ws.start_time // 60)
            start_sec = int(ws.start_time % 60)
            end_min = int(ws.end_time // 60)
            end_sec = int(ws.end_time % 60)
            context_lines.append(f"### 窗口 {ws.window_id}（{start_min}:{start_sec:02d} - {end_min}:{end_sec:02d}）")
            context_lines.append(f"- 阶段：{phase_cn}")
            context_lines.append(f"- 摘要：{ws.summary}")
            context_lines.append("")
        
        return "\n".join(context_lines)


class GeminiClient:
    """
    Gemini 3.0 Flash API Client
    
    调用 Google Gemini API 进行多模态手术视频分析。
    
    Features:
    - 多模态分析（图片+文本）
    - thinking_level 控制（low/medium/high）
    - 与 GLMClient 相同的接口
    """
    
    # 默认系统提示（禁用冗长思考）
    DEFAULT_SYSTEM_PROMPT = "请直接回答问题，给出简洁、准确的分析结果。"
    
    def __init__(
        self,
        api_key: str = None,
        model_name: str = None,
        thinking_level: str = None,
        max_tokens: int = None,
        timeout: float = 180.0,
        max_concurrent: int = 8
    ):
        config = load_config()
        gemini_config = config.get("services", {}).get("gemini", {})
        
        # API Key: 优先使用参数，其次环境变量，最后配置文件
        api_key_env = gemini_config.get("api_key_env", "GEMINI_API_KEY")
        self.api_key = api_key or os.environ.get(api_key_env, "")
        
        self.model_name = model_name or gemini_config.get("model_name", "gemini-3.1-pro-preview")
        self.thinking_level = thinking_level or gemini_config.get("thinking_level", "none")
        self.max_tokens = max_tokens or gemini_config.get("max_tokens", 1000)
        self.timeout = timeout
        
        # 并发配置
        self.concurrent_config = GeminiConcurrentConfig(max_concurrent=max_concurrent)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        
        # 重试配置（从配置文件读取）
        self.retry_count = gemini_config.get("retry_count", 2)
        self.retry_delay = gemini_config.get("retry_delay", 0.5)
        
        # 初始化 GenAI 客户端
        self._client = None
        if GENAI_AVAILABLE and self.api_key:
            try:
                self._client = genai.Client(api_key=self.api_key)
                logger.info(f"[GeminiClient] Initialized with model: {self.model_name}, thinking_level: {self.thinking_level}")
            except Exception as e:
                logger.error(f"[GeminiClient] Failed to initialize: {e}")
        elif not GENAI_AVAILABLE:
            logger.error("[GeminiClient] google-genai SDK not available")
        else:
            logger.warning("[GeminiClient] No API key provided")
    
    @property
    def client(self):
        """Get GenAI client"""
        return self._client
    
    async def check_health(self) -> bool:
        """Check if Gemini service is available"""
        if not self._client:
            return False
        try:
            # 尝试列出模型
            response = self._client.models.list()
            return True
        except Exception as e:
            logger.warning(f"[GeminiClient] Health check failed: {e}")
            return False
    
    # 图片压缩配置（用于发送给 Gemini API，减少网络传输时间）
    GEMINI_IMAGE_MAX_WIDTH = 640    # 最大宽度（像素），保持比例缩放
    GEMINI_IMAGE_JPEG_QUALITY = 60  # JPEG 压缩质量（60 足够 Gemini 分析）
    
    def _resize_for_gemini(self, image: Image.Image) -> Image.Image:
        """将图片缩放到适合 Gemini API 的尺寸，减少传输数据量"""
        w, h = image.size
        if w > self.GEMINI_IMAGE_MAX_WIDTH:
            ratio = self.GEMINI_IMAGE_MAX_WIDTH / w
            new_w = self.GEMINI_IMAGE_MAX_WIDTH
            new_h = int(h * ratio)
            image = image.resize((new_w, new_h), Image.LANCZOS)
            logger.debug(f"[GeminiClient] Resized image: {w}x{h} -> {new_w}x{new_h}")
        return image
    
    def _image_to_base64(self, image: Union[str, Image.Image, bytes, Path]) -> bytes:
        """Convert image to bytes for Gemini API (with compression/resize)"""
        if isinstance(image, str):
            if image.startswith("data:image"):
                # Extract base64 from data URL
                _, data = image.split(",", 1)
                return base64.b64decode(data)
            # Treat as file path
            image = Path(image)
        
        if isinstance(image, Path):
            if image.exists():
                # 读取文件后也做 resize 压缩
                pil_img = Image.open(image)
                if pil_img.mode == 'RGBA':
                    pil_img = pil_img.convert('RGB')
                pil_img = self._resize_for_gemini(pil_img)
                buffer = BytesIO()
                pil_img.save(buffer, format="JPEG", quality=self.GEMINI_IMAGE_JPEG_QUALITY)
                return buffer.getvalue()
            else:
                raise ValueError(f"Image file not found: {image}")
        
        if isinstance(image, bytes):
            # 对 raw bytes 也做压缩处理
            pil_img = Image.open(BytesIO(image))
            if pil_img.mode == 'RGBA':
                pil_img = pil_img.convert('RGB')
            pil_img = self._resize_for_gemini(pil_img)
            buffer = BytesIO()
            pil_img.save(buffer, format="JPEG", quality=self.GEMINI_IMAGE_JPEG_QUALITY)
            return buffer.getvalue()
        
        if isinstance(image, Image.Image):
            if image.mode == 'RGBA':
                image = image.convert('RGB')
            image = self._resize_for_gemini(image)
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=self.GEMINI_IMAGE_JPEG_QUALITY)
            return buffer.getvalue()
        
        raise ValueError(f"Unsupported image type: {type(image)}")
    
    def _get_thinking_config(self) -> Any:
        """Get thinking configuration based on thinking_level"""
        if not GENAI_AVAILABLE:
            return None
        
        # "none" 表示关闭思考模式
        # gemini-3-flash-preview 不支持 thinking_level=NONE，
        # 但可以通过 thinking_budget=256 有效禁用 thinking（tokens=0）
        if self.thinking_level == "none":
            # 对于强制 thinking 的模型，thinking_budget=0 完全禁用 thinking
            try:
                return types.ThinkingConfig(thinking_budget=0)
            except Exception:
                return None
        
        return types.ThinkingConfig(thinking_level=self.thinking_level)
    
    async def chat(
        self,
        message: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Simple text chat without images.
        支持 503 错误自动重试
        """
        if not self._client:
            return {
                "success": False,
                "text": "[Error: Gemini client not initialized]",
                "error": "Client not initialized"
            }
        
        start_time = time.time()
        max_tokens = max_tokens or self.max_tokens
        
        # 构建内容
        contents = []
        if system_prompt:
            contents.append(types.Part.from_text(text=f"系统指令: {system_prompt}\n\n"))
        contents.append(types.Part.from_text(text=message))
        
        # 配置
        config = types.GenerateContentConfig(
            thinking_config=self._get_thinking_config(),
            max_output_tokens=max_tokens
        )
        if temperature is not None:
            config.temperature = temperature
        
        # 重试逻辑
        max_retries = self.retry_count + 1
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # 调用 API
                response = self._client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=config
                )
                
                text = response.text if response.text else ""
                duration_ms = (time.time() - start_time) * 1000
                
                if attempt > 0:
                    logger.info(f"[GeminiClient] Chat succeeded after {attempt + 1} attempts, {duration_ms:.0f}ms")
                else:
                    logger.info(f"[GeminiClient] Chat completed in {duration_ms:.0f}ms")
                
                return {
                    "success": True,
                    "text": text,
                    "model": self.model_name,
                    "thinking_level": self.thinking_level,
                    "duration_ms": duration_ms,
                    "retry_attempts": attempt
                }
                
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                
                # 检查是否为可重试的错误
                is_retryable = any(keyword in error_str for keyword in [
                    '503', 'overload', 'unavailable', 'rate limit', '429',
                    'temporarily', 'timeout', 'connection', 'resource exhausted'
                ])
                
                if is_retryable and attempt < max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    logger.warning(f"[GeminiClient] Retryable error (attempt {attempt + 1}/{max_retries}): {e}. Waiting {wait_time:.1f}s...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    break
        
        # 所有重试都失败
        duration_ms = (time.time() - start_time) * 1000
        logger.error(f"[GeminiClient] Chat failed after {max_retries} attempts: {last_error}")
        return {
            "success": False,
            "text": f"[Error: {str(last_error)}]",
            "error": str(last_error),
            "duration_ms": duration_ms,
            "retry_attempts": max_retries - 1
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
        Chat with surgical context from database.
        
        与 GLMClient.chat_with_context 保持接口一致，用于聊天助手。
        
        Args:
            user_query: User's question
            surgical_context: Context from compressed summaries or window summaries
            conversation_history: Previous conversation messages (not used currently)
            system_prompt: Custom system prompt
            disable_thinking: Not applicable for Gemini (ignored)
            include_background: Whether to include domain background knowledge
            
        Returns:
            Dict with response
        """
        if system_prompt is None:
            # Load chat system prompt from external file
            prompt_path = Path(__file__).parent.parent / "prompts" / "chat_system_prompt.txt"
            try:
                system_prompt = prompt_path.read_text(encoding="utf-8").strip()
                logger.debug(f"[GeminiClient] Loaded chat prompt from {prompt_path} ({len(system_prompt)} chars)")
            except FileNotFoundError:
                logger.warning(f"[GeminiClient] Chat prompt file not found: {prompt_path}, using fallback")
                system_prompt = "你是一个专业的腹腔镜胆囊切除手术助手，可以回答关于当前手术过程的问题。请根据提供的手术记录给出准确、简洁的中文回答。"
        
        # Build message with context
        full_message = user_query
        if surgical_context:
            full_message = f"{surgical_context}\n\n用户问题: {user_query}"
        
        result = await self.chat(
            message=full_message,
            system_prompt=system_prompt,
            temperature=0.7,
            max_tokens=1000
        )
        
        return result
    
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
        支持 503 错误自动重试
        """
        if not self._client:
            return {
                "success": False,
                "text": "[Error: Gemini client not initialized]",
                "error": "Client not initialized"
            }
        
        start_time = time.time()
        max_tokens = max_tokens or self.max_tokens
        
        # 构建请求内容（只需构建一次，重试时复用）
        contents = []
        
        # 添加系统提示
        if system_prompt:
            contents.append(types.Part.from_text(text=f"系统指令: {system_prompt}\n\n"))
        
        # 添加图片（限制最多10张）
        limited_images = images[:10]
        for idx, img in enumerate(limited_images):
            image_bytes = self._image_to_base64(img)
            contents.append(types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg"
            ))
        
        # 添加问题
        contents.append(types.Part.from_text(text=question))
        
        # 配置
        config = types.GenerateContentConfig(
            thinking_config=self._get_thinking_config(),
            max_output_tokens=max_tokens
        )
        if temperature is not None:
            config.temperature = temperature
        
        # 重试逻辑
        max_retries = self.retry_count + 1
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # 使用 streaming 模式调用 API（在线程中消费 sync iterator）
                def _stream_collect():
                    """在线程中消费 sync streaming iterator"""
                    chunks = []
                    _first_token_time = None
                    _finish_reason = None
                    for chunk in self._client.models.generate_content_stream(
                        model=self.model_name,
                        contents=contents,
                        config=config
                    ):
                        if _first_token_time is None:
                            _first_token_time = time.time()
                        if chunk.text:
                            chunks.append(chunk.text)
                        if hasattr(chunk, 'candidates') and chunk.candidates:
                            candidate = chunk.candidates[0]
                            if hasattr(candidate, 'finish_reason') and candidate.finish_reason:
                                _finish_reason = str(candidate.finish_reason)
                    return "".join(chunks), _first_token_time, _finish_reason
                
                text, first_token_time, finish_reason = await asyncio.to_thread(_stream_collect)
                duration_ms = (time.time() - start_time) * 1000
                first_token_ms = (first_token_time - start_time) * 1000 if first_token_time else duration_ms
                
                if finish_reason and ('MAX_TOKENS' in finish_reason.upper() or 'LENGTH' in finish_reason.upper()):
                    logger.warning(f"[GeminiClient] Output may be truncated! finish_reason={finish_reason}, output_len={len(text)}")
                
                if attempt > 0:
                    logger.info(f"[GeminiClient] Multi-image analysis (stream) succeeded after {attempt + 1} attempts, {duration_ms:.0f}ms (first_token: {first_token_ms:.0f}ms), {len(limited_images)} images, finish_reason={finish_reason}")
                else:
                    logger.info(f"[GeminiClient] Multi-image analysis (stream) completed in {duration_ms:.0f}ms (first_token: {first_token_ms:.0f}ms), {len(limited_images)} images, finish_reason={finish_reason}")
                
                return {
                    "success": True,
                    "text": text,
                    "model": self.model_name,
                    "image_count": len(limited_images),
                    "duration_ms": duration_ms,
                    "first_token_ms": first_token_ms,
                    "retry_attempts": attempt,
                    "finish_reason": finish_reason
                }
                
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                
                # 检查是否为可重试的错误
                is_retryable = any(keyword in error_str for keyword in [
                    '503', 'overload', 'unavailable', 'rate limit', '429',
                    'temporarily', 'timeout', 'connection', 'resource exhausted'
                ])
                
                if is_retryable and attempt < max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    logger.warning(f"[GeminiClient] Retryable error (attempt {attempt + 1}/{max_retries}): {e}. Waiting {wait_time:.1f}s...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    break
        
        # 所有重试都失败
        duration_ms = (time.time() - start_time) * 1000
        logger.error(f"[GeminiClient] Multi-image analysis failed after {max_retries} attempts: {last_error}")
        return {
            "success": False,
            "text": f"[Error: {str(last_error)}]",
            "error": str(last_error),
            "duration_ms": duration_ms,
            "retry_attempts": max_retries - 1
        }
    
    async def _analyze_images_with_timestamps(
        self,
        images: List[Union[str, Image.Image, bytes, Path]],
        timestamps: List[float],
        question: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        分析带时间戳标注的多张图片（使用 streaming 模式减少首 token 延迟）
        支持 503 错误自动重试
        """
        if not self._client:
            return {
                "success": False,
                "text": "[Error: Gemini client not initialized]",
                "error": "Client not initialized"
            }
        
        start_time = time.time()
        max_tokens = max_tokens or self.max_tokens
        
        # 构建请求内容（只需构建一次，重试时复用）
        contents = []
        
        # 添加系统提示
        if system_prompt:
            contents.append(types.Part.from_text(text=f"系统指令: {system_prompt}\n\n"))
        
        # 为每张图片添加时间戳标注
        for idx, (img, ts) in enumerate(zip(images, timestamps)):
            # 添加时间戳标签
            contents.append(types.Part.from_text(text=
                f"【图片{idx + 1}/{len(images)}】时间：{ts:.1f}秒"
            ))
            # 添加图片
            image_bytes = self._image_to_base64(img)
            contents.append(types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg"
            ))
        
        # 添加问题
        contents.append(types.Part.from_text(text=question))
        
        # 配置
        config = types.GenerateContentConfig(
            thinking_config=self._get_thinking_config(),
            max_output_tokens=max_tokens
        )
        if temperature is not None:
            config.temperature = temperature
        
        # 重试逻辑
        max_retries = self.retry_count + 1
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # 使用 streaming 模式调用 API（在线程中消费 sync iterator）
                def _stream_collect_ts():
                    """在线程中消费 sync streaming iterator"""
                    chunks = []
                    _first_token_time = None
                    _finish_reason = None
                    for chunk in self._client.models.generate_content_stream(
                        model=self.model_name,
                        contents=contents,
                        config=config
                    ):
                        if _first_token_time is None:
                            _first_token_time = time.time()
                        if chunk.text:
                            chunks.append(chunk.text)
                        if hasattr(chunk, 'candidates') and chunk.candidates:
                            candidate = chunk.candidates[0]
                            if hasattr(candidate, 'finish_reason') and candidate.finish_reason:
                                _finish_reason = str(candidate.finish_reason)
                    return "".join(chunks), _first_token_time, _finish_reason
                
                text, first_token_time, finish_reason = await asyncio.to_thread(_stream_collect_ts)
                duration_ms = (time.time() - start_time) * 1000
                first_token_ms = (first_token_time - start_time) * 1000 if first_token_time else duration_ms
                
                if finish_reason and ('MAX_TOKENS' in finish_reason.upper() or 'LENGTH' in finish_reason.upper()):
                    logger.warning(f"[GeminiClient] Output may be truncated! finish_reason={finish_reason}, output_len={len(text)}")
                
                if attempt > 0:
                    logger.info(f"[GeminiClient] Timestamped analysis (stream) succeeded after {attempt + 1} attempts, {duration_ms:.0f}ms (first_token: {first_token_ms:.0f}ms), finish_reason={finish_reason}")
                else:
                    logger.info(f"[GeminiClient] Timestamped analysis (stream) completed in {duration_ms:.0f}ms (first_token: {first_token_ms:.0f}ms), finish_reason={finish_reason}")
                
                return {
                    "success": True,
                    "text": text,
                    "model": self.model_name,
                    "image_count": len(images),
                    "duration_ms": duration_ms,
                    "first_token_ms": first_token_ms,
                    "retry_attempts": attempt,
                    "finish_reason": finish_reason
                }
                
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                
                # 检查是否为可重试的错误（503 过载、429 限流、网络错误等）
                is_retryable = any(keyword in error_str for keyword in [
                    '503', 'overload', 'unavailable', 'rate limit', '429',
                    'temporarily', 'timeout', 'connection', 'resource exhausted'
                ])
                
                if is_retryable and attempt < max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    logger.warning(f"[GeminiClient] Retryable error (attempt {attempt + 1}/{max_retries}): {e}. Waiting {wait_time:.1f}s...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    break
        
        # 所有重试都失败
        duration_ms = (time.time() - start_time) * 1000
        logger.error(f"[GeminiClient] Timestamped analysis (stream) failed after {max_retries} attempts: {last_error}")
        return {
            "success": False,
            "text": f"[Error: {str(last_error)}]",
            "error": str(last_error),
            "duration_ms": duration_ms,
            "retry_attempts": max_retries - 1
        }
    
    def _analyze_consistency(self, frame_analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
        """分析帧间一致性"""
        phases = []
        actions = []
        tools = []
        
        for analysis in frame_analyses:
            phases.append(analysis.get('phase', '') or '')
            actions.append(analysis.get('action', '') or '')
            tools.append(analysis.get('tools', '') or '')
        
        unique_phases = set(p for p in phases if p)
        unique_actions = set(a for a in actions if a)
        
        phase_transitions = sum(1 for i in range(len(phases) - 1) if phases[i] != phases[i + 1])
        action_transitions = sum(1 for i in range(len(actions) - 1) if actions[i] != actions[i + 1])
        
        frames_with_tools = sum(1 for tool in tools if tool and 'null' not in tool.lower() and len(tool) > 10)
        
        phase_counts = {}
        for p in phases:
            if p:
                phase_counts[p] = phase_counts.get(p, 0) + 1
        dominant_phase = max(phase_counts, key=phase_counts.get) if phase_counts else "未知"
        
        num_frames = len(frame_analyses)
        
        return {
            "图像级一致性": {
                "唯一阶段数": len(unique_phases),
                "主导阶段": dominant_phase,
                "阶段分布": phase_counts,
                "唯一动作数": len(unique_actions),
            },
            "相邻一致性": {
                "阶段转换次数": phase_transitions,
                "阶段稳定性": 1 - (phase_transitions / (num_frames - 1)) if num_frames > 1 else 1.0,
                "动作转换次数": action_transitions,
            },
            "工具分析": {
                "有工具帧数": frames_with_tools,
                "工具出现率": frames_with_tools / num_frames if num_frames else 0
            }
        }
    
    def _build_internal_context(
        self,
        frame_analyses: List[Dict[str, Any]],
        consistency_analysis: Dict[str, Any]
    ) -> str:
        """构建供模型内部分析的上下文"""
        img_cons = consistency_analysis["图像级一致性"]
        tool_analysis = consistency_analysis["工具分析"]
        
        context = f"主导阶段：{img_cons['主导阶段']}\n"
        context += f"工具出现率：{tool_analysis['工具出现率']:.0%}\n\n"
        
        context += "帧标注：\n"
        for i, analysis in enumerate(frame_analyses):
            phase = analysis.get('phase', '') or ''
            action = analysis.get('action', '') or ''
            tools = analysis.get('tools', '') or ''
            if len(tools) > 80:
                tools = tools[:80] + "..."
            context += f"[{phase}] {action} | {tools}\n"
        
        return context
    
    def _extract_others_data(self, text: str) -> Dict[str, Any]:
        """
        从输出中提取 [others] 部分的数据
        
        格式: [others]hem_loc=N,gauze=Y/N,bleeding=Y/N,blur=Y/N,out_of_body=Y/N
        
        Returns:
            {
                "hem_loc": int,
                "gauze": bool,
                "bleeding": bool,
                "blur": bool,
                "out_of_body": bool
            }
        """
        others_data = {
            "hem_loc": 0,
            "gauze": False,
            "bleeding": False,
            "blur": False,
            "out_of_body": False
        }
        
        if not text:
            return others_data
        
        # 查找 [others] 部分
        others_match = re.search(r'\[others\]\s*(.+?)(?:\n|$)', text, flags=re.IGNORECASE)
        if others_match:
            others_str = others_match.group(1).strip()

            # 解析 hem_loc=N (Hem-o-lok)
            hem_loc_match = re.search(r'hem_loc\s*=\s*(\d+)', others_str, flags=re.IGNORECASE)
            if hem_loc_match:
                others_data["hem_loc"] = int(hem_loc_match.group(1))
            
            # 解析 gauze=Y/N
            gauze_match = re.search(r'gauze\s*=\s*(Y|N|yes|no|true|false)', others_str, flags=re.IGNORECASE)
            if gauze_match:
                gauze_val = gauze_match.group(1).upper()
                others_data["gauze"] = gauze_val in ("Y", "YES", "TRUE")

            # 解析 bleeding=Y/N
            bleeding_match = re.search(r'bleeding\s*=\s*(Y|N|yes|no|true|false)', others_str, flags=re.IGNORECASE)
            if bleeding_match:
                bleeding_val = bleeding_match.group(1).upper()
                others_data["bleeding"] = bleeding_val in ("Y", "YES", "TRUE")

            # 解析 blur=Y/N
            blur_match = re.search(r'(?:blur|blurry)\s*=\s*(Y|N|yes|no|true|false)', others_str, flags=re.IGNORECASE)
            if blur_match:
                blur_val = blur_match.group(1).upper()
                others_data["blur"] = blur_val in ("Y", "YES", "TRUE")

            # 解析 out_of_body=Y/N
            oob_match = re.search(r'out_of_body\s*=\s*(Y|N|yes|no|true|false)', others_str, flags=re.IGNORECASE)
            if oob_match:
                oob_val = oob_match.group(1).upper()
                others_data["out_of_body"] = oob_val in ("Y", "YES", "TRUE")
            
            logger.info(f"[GeminiClient] Extracted others: {others_data}")
        
        return others_data
    
    def _extract_narrative_output(self, text: str) -> Tuple[str, Dict[str, Any]]:
        """
        从输出中提取叙事内容和 [others] 数据
        
        Returns:
            Tuple[str, Dict]: (summary不含[others], others_data字典)
        """
        others_data = {
            "hem_loc": 0,
            "gauze": False,
            "bleeding": False,
            "blur": False,
            "out_of_body": False
        }
        
        if not text:
            return text, others_data
        
        text = text.strip()
        
        # 先提取 [others] 数据
        others_data = self._extract_others_data(text)
        
        # 移除 [others] 部分
        text = re.sub(r'\[others\]\s*.+?(?:\n|$)', '', text, flags=re.IGNORECASE).strip()
        
        # 1. 提取 <answer>...</answer> 标签内容
        answer_match = re.search(r'<answer>(.*?)</answer>', text, flags=re.DOTALL)
        if answer_match:
            result = answer_match.group(1).strip()
            # 再次移除可能的 [others]
            result = re.sub(r'\[others\]\s*.+?(?:\n|$)', '', result, flags=re.IGNORECASE).strip()
            logger.info(f"[GeminiClient] Extracted <answer>: {len(result)} chars")
            return result, others_data
        
        # 2. 如果只有开始标签，提取后面的内容
        if '<answer>' in text:
            idx = text.find('<answer>') + len('<answer>')
            result = text[idx:].replace('</answer>', '').strip()
            return result, others_data
        
        # 3. 移除思考标签
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        text = re.sub(r'<reasoning>.*?</reasoning>', '', text, flags=re.DOTALL)
        text = re.sub(r'</?(think|reasoning|answer)>', '', text)
        
        text = text.strip()
        
        # 4. 如果以阶段标记开头，直接返回
        if text.startswith('【'):
            return text, others_data
        
        narrative_starts = ['准备阶段', '肝胆三角解剖阶段', '夹闭切断阶段', '胆囊分离阶段', 
                           '胆囊牵拉阶段', '清洁凝血阶段', '胆囊取出阶段',
                           '镜头', '抓钳', '电钩', '可见', '随后', '期间', '至片段']
        for start in narrative_starts:
            if text.startswith(start):
                return text, others_data
        
        # 5. 尝试找到叙事开始位置
        bracket_idx = text.find('【')
        if bracket_idx != -1 and bracket_idx < 50:
            return text[bracket_idx:].strip(), others_data
        
        for start in narrative_starts:
            idx = text.find(start)
            if idx != -1 and idx < 50:
                return text[idx:].strip(), others_data
        
        return text, others_data
    
    async def integrate_analysis_results(
        self,
        frame_analyses: List[Dict[str, Any]],
        images: Optional[List[Image.Image]] = None,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        include_background: bool = True,
        history_context: Optional[str] = None,
        conflict_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        整合多帧分析结果为连贯的叙事摘要
        支持多模态输入：图片 + R1分析结果
        
        参数:
            frame_analyses: 帧分析结果列表
            images: 帧图片列表
            system_prompt: 系统提示词（使用内置提示词）
            temperature: 采样温度
            max_tokens: 最大生成长度
            include_background: 是否包含领域背景知识
            history_context: 之前窗口的摘要历史
            conflict_context: 阶段矛盾检测结果
        """
        # 执行一致性分析
        consistency_analysis = self._analyze_consistency(frame_analyses)
        
        # 构建内部分析上下文
        internal_context = self._build_internal_context(frame_analyses, consistency_analysis)
        
        # 加载 system prompt
        loaded_prompt = get_gemini_system_prompt(reload=True)
        if loaded_prompt:
            system_prompt = loaded_prompt
            logger.debug(f"[GeminiClient] Using loaded system prompt ({len(loaded_prompt)} chars)")
        else:
            # 从配置读取输出字数限制
            config = load_config()
            max_chars = config.get("window_analysis", {}).get("max_output_chars", 300)
            
            system_prompt = f"""你是腹腔镜手术分析系统。基于R1帧标注和图像，生成简洁的手术操作描述。

【输出规则】
1. 禁止提及"外科医生"、"医生"、"术者"，使用被动语态描述操作
2. 只描述：当前阶段、器械动作、组织状态
3. 禁止描述：光线反射、水珠、纹理、阴影、标本袋表面折射/反光等视觉细节
4. 异常情况只在发生时描述：出血/器械碰撞/镜头模糊/烟雾仅在实际发生时提及
   - 禁止描述"无出血"、"无活动性出血"、"无器械碰撞"、"视野清晰"等正常状态
   - blur判断极其严格：标本袋半透明表面、光线反射、局部过曝均不算blur；只有整个视野被烟雾/水雾/血液大面积遮挡才算blur=Y
5. 必须关注Hem-o-lok、纱布（仅在出现时描述）
6. 全中文输出：禁止出现任何英文，包括括号内的英文注释

【输出格式】
【xxx】（不要写"阶段"二字，如【清洁凝血】【胆囊分离】）
简洁操作描述（{max_chars}字以内）
[others]hem_loc=N,gauze=Y/N,bleeding=Y/N,blur=Y/N,out_of_body=Y/N

【阶段名称】准备、肝胆三角解剖、夹闭切断、胆囊分离、胆囊牵拉、清洁凝血、胆囊取出
【工具中文名】抓钳、电钩、剪刀、钛夹钳、冲吸器、双极电凝

【others说明】
- hem_loc: 可见的Hem-o-lok数量，无则填0
- gauze: 是否可见纱布（Y/N）
- bleeding: 是否有明显出血（Y/N）
- blur: 镜头是否被烟雾/水雾/血液大面积遮挡导致视野完全不清（Y/N），标本袋反光/局部过曝不算，默认N
- out_of_body: 镜头是否移出体外（Y/N）"""
            logger.warning("[GeminiClient] Using fallback system prompt")
        
        # 构建用户消息
        prompt_parts = []
        
        # 历史上下文
        if history_context:
            logger.info(f"[GeminiClient] integrate_analysis_results: history_context length = {len(history_context)} chars")
            prompt_parts.append("## 历史窗口分析（必须参考以判断阶段连续性）")
            prompt_parts.append(history_context.strip())
            prompt_parts.append("")
            
            phase_constraints = []
            if "胆囊取出" in history_context:
                phase_constraints.append(
                    "⚠️ 历史已出现「胆囊取出」，当前窗口**仅允许**标注为「清洁凝血」或「胆囊取出」，"
                    "**禁止**标注为「胆囊分离」「胆囊牵拉」「夹闭切断」「肝胆三角解剖」「准备阶段」"
                )
            if any(p in history_context for p in ["肝胆三角解剖", "夹闭切断", "胆囊分离", "胆囊牵拉", "胆囊取出", "清洁凝血"]):
                phase_constraints.append("⚠️ 手术已进入正式阶段，当前窗口禁止回退到「准备阶段」")
            
            if phase_constraints:
                prompt_parts.append("**⚠️ 阶段约束提醒（必须严格遵守！）：**")
                for constraint in phase_constraints:
                    prompt_parts.append(constraint)
                prompt_parts.append("")
        else:
            logger.info("[GeminiClient] integrate_analysis_results: NO history_context provided")
        
        # R1帧分析数据
        prompt_parts.append("【R1帧标注】（器械检测通常准确，请在叙事中体现）")
        prompt_parts.append(internal_context)
        
        prompt_parts.append("")
        prompt_parts.append("请结合R1标注和图片内容，生成当前窗口的时序叙事分析（如有器械必须提及）：")
        
        prompt_text = "\n".join(prompt_parts)
        
        # 获取帧时间戳
        frame_timestamps = [a.get('timestamp', 0) for a in frame_analyses]
        
        # 多模态或纯文本分析
        if images and len(images) > 0:
            num_images = len(images)
            max_images = 10
            
            if num_images <= max_images:
                image_indices = list(range(num_images))
            else:
                image_indices = []
                for i in range(max_images):
                    idx = int(i * (num_images - 1) / (max_images - 1))
                    image_indices.append(idx)
            
            sampled_images = [images[i] for i in image_indices]
            sampled_timestamps = [frame_timestamps[i] if i < len(frame_timestamps) else 0 for i in image_indices]
            
            logger.info(f"[GeminiClient] Using multimodal analysis with {len(sampled_images)} images")
            
            result = await self._analyze_images_with_timestamps(
                images=sampled_images,
                timestamps=sampled_timestamps,
                question=prompt_text,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens
            )
        else:
            logger.info("[GeminiClient] Using text-only analysis (no images provided)")
            result = await self.chat(
                message=prompt_text,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens
            )
        
        # 后处理：提取叙事内容和 [others] 数据
        raw_text = result.get("text", "")
        logger.info(f"[GeminiClient] integrate raw_text length={len(raw_text)}, first 100 chars: {raw_text[:100]}")
        cleaned_text, others_data = self._extract_narrative_output(raw_text)
        logger.info(f"[GeminiClient] integrate cleaned_text length={len(cleaned_text)}")
        
        return {
            "success": result.get("success", False),
            "summary": cleaned_text,
            "others": others_data,  # {"clips": int, "gauze": bool} - 存入数据库，不显示在前端
            "model": self.model_name,
            "tokens_used": result.get("tokens_used", 0),
            "frame_count": len(frame_analyses),
            "consistency_analysis": consistency_analysis,
            "error": result.get("error")
        }
    
    async def integrate_experts_text_only(
        self,
        expert_context: str,
        history_context: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 400,
    ) -> Dict[str, Any]:
        """Stage 1 快速摘要 — 仅用 3 个专家模型的文本结果（YOLO/Phase/Triplet），
        不带图像，用于实时 UX。输出 1-2 句中文总结。

        遵循全局 phase 约束（见 WindowHistoryManager 的 ALLOWED_TRANSITIONS /
        _reached_phases）：通过 history_context 注入的"阶段约束提醒"禁止回退。
        """
        config = load_config()
        max_chars = config.get("window_analysis", {}).get("max_output_chars", 200)

        system_prompt = (
            "你是腹腔镜手术分析系统的实时初稿生成器。输入只有三个专家模型的文本判断"
            "（Phase Expert、YOLO 工具检测、LAM-Lite Triplet），无图像。"
            "请在 1-2 句中文内总结当前窗口正在进行的手术动作。\n\n"
            "【输出规则】\n"
            "1. 严格遵守历史上下文里出现的「阶段约束提醒」——已经走过的阶段不能回退，"
            "   必须按手术生理顺序推进。\n"
            "2. 以 Phase Expert 判断为阶段主准则；其它专家信息用来补充器械与动作。\n"
            "3. 禁止输出任何警告符号（⚠️、！、!、等）、英文、医生称谓。\n"
            "4. 禁止描述光线/反射/水珠/阴影等视觉细节；只描述手术阶段 + 器械 + 动作。\n\n"
            "【输出格式】\n"
            "【阶段中文名】\n"
            f"一段动作描述（{max_chars}字以内，不分段）\n\n"
            "【阶段中文名】只能在：准备、肝胆三角解剖、夹闭切断、胆囊分离、"
            "胆囊牵拉、清洁凝血、胆囊取出 中选一个。"
        )

        parts = []
        if history_context:
            parts.append("## 历史窗口分析（必须参考以判断阶段连续性）")
            parts.append(history_context.strip())

            # 继承 integrate_analysis_results 里的同一套 phase 约束逻辑
            phase_constraints = []
            if "胆囊取出" in history_context:
                phase_constraints.append(
                    "历史已出现「胆囊取出」，当前窗口仅允许标注为「清洁凝血」或「胆囊取出」，"
                    "禁止回退到「胆囊分离」「胆囊牵拉」「夹闭切断」「肝胆三角解剖」「准备阶段」"
                )
            if any(p in history_context for p in [
                "肝胆三角解剖", "夹闭切断", "胆囊分离", "胆囊牵拉", "胆囊取出", "清洁凝血"
            ]):
                phase_constraints.append(
                    "手术已进入正式阶段，当前窗口禁止回退到「准备阶段」"
                )
            if phase_constraints:
                parts.append("## 阶段约束（必须严格遵守）")
                parts.extend(phase_constraints)

        parts.append(expert_context.strip())
        parts.append("基于上述专家文本输出当前窗口的初稿。")
        prompt = "\n\n".join(parts)

        result = await self.chat(
            message=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        raw = result.get("text", "")
        cleaned, _others = self._extract_narrative_output(raw)
        # 防御性过滤：万一模型还是吐了 ⚠️/! 也去掉
        cleaned = (cleaned or raw or "").replace("⚠️", "").replace("⚠", "")
        cleaned = cleaned.strip()
        return {
            "success": result.get("success", False),
            "summary": cleaned,
            "model": self.model_name,
            "tokens_used": result.get("tokens_used", 0),
            "error": result.get("error"),
        }

    def _extract_phase_from_summary(self, summary: str) -> str:
        """从摘要文本中提取手术阶段"""
        phase_mapping = {
            "准备阶段": "Preparation",
            "准备期": "Preparation",
            "肝胆三角解剖": "CalotTriangleDissection",
            "Calot三角": "CalotTriangleDissection",
            "夹闭切断": "ClippingCutting",
            "胆囊分离": "GallbladderDissection",
            "胆囊取出": "GallbladderPackaging",
            "清洁凝血": "CleaningCoagulation",
            "胆囊牵拉": "GallbladderRetraction",
        }
        
        for cn_name, en_name in phase_mapping.items():
            if cn_name in summary:
                return en_name
        
        return "Unknown"
    
    async def summarize_windows_concurrent(
        self,
        windows: List[Dict[str, Any]],
        max_concurrent: int = None,
        session_id: str = None
    ) -> List[Dict[str, Any]]:
        """
        按顺序摘要多个窗口 - 保持阶段连续性
        """
        if not windows:
            return []
        
        start_time = time.time()
        results = []
        success_count = 0
        
        # 获取或创建历史上下文管理器
        if session_id:
            from . import gemini_client as gemini_module
            if not hasattr(gemini_module, '_session_history_managers'):
                gemini_module._session_history_managers = {}
            if session_id not in gemini_module._session_history_managers:
                gemini_module._session_history_managers[session_id] = WindowHistoryManager()
            history_manager = gemini_module._session_history_managers[session_id]
        else:
            history_manager = WindowHistoryManager()
        
        sorted_windows = sorted(windows, key=lambda w: w.get("window_id", 0))
        
        for window in sorted_windows:
            window_id = window.get("window_id")
            frame_count = len(window.get("frame_analyses", []))
            
            try:
                current_history = await history_manager.get_history()
                logger.info(f"[GeminiClient] Processing window {window_id} with {frame_count} frames, history_count={len(current_history)}")
                
                history_context = await history_manager.build_history_context()
                
                result = await self.integrate_analysis_results(
                    frame_analyses=window.get("frame_analyses", []),
                    images=window.get("images"),
                    history_context=history_context
                )
                
                summary = result.get("summary", "")
                dominant_phase = self._extract_phase_from_summary(summary)
                
                # ========== 阶段约束后处理：使用连续确认机制和规则表验证 ==========
                if dominant_phase:
                    # 更新阶段连续确认计数
                    confirm_count = history_manager.update_phase_confirm_count(dominant_phase)
                    logger.debug(f"[GeminiClient] Window {window_id} phase={dominant_phase}, confirm_count={confirm_count}")
                    
                    # 验证阶段转换合法性
                    is_valid, corrected_phase = history_manager.validate_phase_transition(dominant_phase, current_history)
                    
                    if not is_valid:
                        original_phase = dominant_phase
                        dominant_phase = corrected_phase
                        # 更新摘要中的阶段名称
                        original_cn = history_manager.get_phase_cn_name(original_phase)
                        corrected_cn = history_manager.get_phase_cn_name(corrected_phase)
                        if f"【{original_cn}】" in summary:
                            summary = summary.replace(f"【{original_cn}】", f"【{corrected_cn}】")
                        logger.warning(
                            f"[GeminiClient] Window {window_id} PHASE CORRECTED: {original_phase} -> {corrected_phase}"
                        )
                
                if dominant_phase:
                    frame_analyses = window.get("frame_analyses", [])
                    timestamps = [fa.get("timestamp", 0) for fa in frame_analyses if fa.get("timestamp")]
                    start = min(timestamps) if timestamps else window_id * 15.0
                    end = max(timestamps) if timestamps else (window_id + 1) * 15.0
                    
                    await history_manager.add_summary(WindowSummary(
                        window_id=window_id,
                        start_time=start,
                        end_time=end,
                        dominant_phase=dominant_phase,
                        tools=[],
                        cvs_status="",
                        summary=summary[:200]
                    ))
                
                logger.info(f"[GeminiClient] Window {window_id} completed: success={result.get('success')}, phase={dominant_phase}")
                
                results.append({
                    "window_id": window_id,
                    "success": result.get("success", False),
                    "summary": summary,
                    "tokens_used": result.get("tokens_used", 0),
                    "error": result.get("error")
                })
                
                if result.get("success"):
                    success_count += 1
                    
            except Exception as e:
                logger.warning(f"[GeminiClient] Sequential summarization failed for window {window_id}: {e}")
                results.append({
                    "window_id": window_id,
                    "success": False,
                    "summary": f"[Error: {str(e)}]",
                    "error": str(e)
                })
        
        elapsed = time.time() - start_time
        logger.info(f"[GeminiClient] Sequential summarization completed: {success_count}/{len(windows)} windows in {elapsed:.2f}s")
        
        return results

    async def detect_bleeding_bboxes(self, image_path: str) -> List[Dict]:
        """Detect bleeding areas in a surgical image using Gemini.
        Returns list of normalized bbox dicts: {x_center, y_center, width, height, confidence_desc}
        """
        import json as _json

        prompt = """Analyze this laparoscopic surgical image for active bleeding areas.

For each distinct bleeding area you detect, provide the bounding box in NORMALIZED coordinates (0.0 to 1.0 relative to image width/height):
- x_center: horizontal center of the bleeding area
- y_center: vertical center of the bleeding area
- width: width of the bounding box
- height: height of the bounding box

Return ONLY a JSON array. Each element: {"x_center": float, "y_center": float, "width": float, "height": float}
If NO bleeding is detected, return exactly: []

Rules:
- Only mark ACTIVE bleeding (visible blood flow, pooling blood, oozing)
- Do NOT mark: normal tissue color, cauterized tissue, surgical instruments, bile/fluid
- Be conservative: only mark areas you are confident contain active bleeding
- Bounding boxes should tightly fit the bleeding area"""

        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()

            img_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

            response = await asyncio.to_thread(
                self._client.models.generate_content,
                model="gemini-3.1-pro-preview",
                contents=[prompt, img_part],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=512,
                )
            )

            text = response.text.strip()
            # Extract JSON from response (may have markdown code blocks)
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            bboxes = _json.loads(text)
            if not isinstance(bboxes, list):
                return []

            # Validate bbox values
            valid = []
            for bb in bboxes:
                if all(k in bb for k in ("x_center", "y_center", "width", "height")):
                    if all(0 <= bb[k] <= 1 for k in ("x_center", "y_center", "width", "height")):
                        valid.append({
                            "x_center": round(bb["x_center"], 6),
                            "y_center": round(bb["y_center"], 6),
                            "width": round(bb["width"], 6),
                            "height": round(bb["height"], 6),
                        })
            return valid
        except Exception as e:
            logger.error(f"[Gemini] Bleeding detection failed for {image_path}: {e}")
            return []


# Global client instance
_gemini_client: Optional[GeminiClient] = None

# Global history managers per session
_session_history_managers: Dict[str, WindowHistoryManager] = {}


def get_gemini_client() -> GeminiClient:
    """Get the global Gemini client instance"""
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = GeminiClient()
    return _gemini_client


def get_history_manager(session_id: str) -> WindowHistoryManager:
    """获取会话对应的历史窗口管理器"""
    global _session_history_managers
    if session_id not in _session_history_managers:
        _session_history_managers[session_id] = WindowHistoryManager()
        logger.info(f"[GeminiClient] Created history manager for session {session_id}")
    return _session_history_managers[session_id]


def cleanup_session_resources(session_id: str) -> None:
    """清理会话相关资源"""
    global _session_history_managers
    if session_id in _session_history_managers:
        del _session_history_managers[session_id]
        logger.info(f"[GeminiClient] Cleaned up history manager for session {session_id}")


async def ensure_gemini_available() -> GeminiClient:
    """Get client and verify service is available"""
    client = get_gemini_client()
    
    is_healthy = await client.check_health()
    if not is_healthy:
        logger.warning("[GeminiClient] Gemini service may not be available")
    
    return client
