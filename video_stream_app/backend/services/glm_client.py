"""
GLM-4.6V-Flash API Client - Text Summarization Service
Calls the external GLM API for multimodal text summarization.
Used for integrating multiple surgical image analyses into coherent summaries.

支持并发批量处理:
- summarize_windows_concurrent: 并发摘要多个窗口
- 使用 asyncio.Semaphore 控制并发数
- 自动重试失败的请求
"""
import asyncio
import json
import logging
import base64
import time
from pathlib import Path
from io import BytesIO
from typing import List, Optional, Dict, Any, Union, Tuple
from PIL import Image
import httpx
from dataclasses import dataclass

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


@dataclass
class GLMConcurrentConfig:
    """GLM 并发处理配置"""
    max_concurrent: int = 3  # 最大并发请求数
    retry_count: int = 2     # 失败重试次数
    retry_delay: float = 0.5 # 重试延迟（秒）


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
        disable_thinking: bool = True,  # 默认禁用思考模式加速
        max_concurrent: int = 3  # 最大并发数
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
        
        # 并发配置
        self.concurrent_config = GLMConcurrentConfig(max_concurrent=max_concurrent)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        
        logger.info(f"[GLMClient] Initialized with API: {self.api_url}, max_concurrent: {max_concurrent}")
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
    
    def _analyze_consistency(self, frame_analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        分析帧间一致性，按照temporal_analyze.py的逻辑
        
        参数:
            frame_analyses: 帧分析结果列表
            
        返回:
            一致性分析结果字典
        """
        phases = []
        actions = []
        tools = []
        
        for analysis in frame_analyses:
            phases.append(analysis.get('phase', '') or '')
            actions.append(analysis.get('action', '') or '')
            tools.append(analysis.get('tools', '') or '')
        
        # 图像级一致性：统计唯一值
        unique_phases = set(p for p in phases if p)
        unique_actions = set(a for a in actions if a)
        
        # 相邻一致性：统计转换次数
        phase_transitions = sum(1 for i in range(len(phases) - 1) if phases[i] != phases[i + 1])
        action_transitions = sum(1 for i in range(len(actions) - 1) if actions[i] != actions[i + 1])
        
        # 工具分析：统计有工具的帧数
        frames_with_tools = sum(1 for tool in tools if tool and 'null' not in tool.lower() and len(tool) > 10)
        
        # 计算主导阶段
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
                "动作多样性": len(unique_actions) / len(actions) if actions else 0
            },
            "相邻一致性": {
                "阶段转换次数": phase_transitions,
                "阶段稳定性": 1 - (phase_transitions / (num_frames - 1)) if num_frames > 1 else 1.0,
                "动作转换次数": action_transitions,
                "动作稳定性": 1 - (action_transitions / (num_frames - 1)) if num_frames > 1 else 1.0
            },
            "工具分析": {
                "有工具帧数": frames_with_tools,
                "工具出现率": frames_with_tools / num_frames if num_frames else 0
            }
        }
    
    def _build_surgr1_context(
        self,
        frame_analyses: List[Dict[str, Any]],
        consistency_analysis: Dict[str, Any]
    ) -> str:
        """
        按照temporal_analyze.py的build_llm_context逻辑构建上下文
        
        参数:
            frame_analyses: 帧分析结果列表
            consistency_analysis: 一致性分析结果
            
        返回:
            格式化的上下文字符串
        """
        # 获取时间范围
        timestamps = [a.get('timestamp', 0) for a in frame_analyses]
        start_time = min(timestamps) if timestamps else 0
        end_time = max(timestamps) if timestamps else 0
        
        context = f"## 片段信息\n"
        context += f"- 时间范围：{start_time:.2f}秒 到 {end_time:.2f}秒\n"
        context += f"- 总帧数：{len(frame_analyses)}\n\n"
        
        # 添加一致性指标
        img_cons = consistency_analysis["图像级一致性"]
        adj_cons = consistency_analysis["相邻一致性"]
        tool_analysis = consistency_analysis["工具分析"]
        
        context += f"## 数据质量指标\n"
        context += f"- 主导阶段：{img_cons['主导阶段']}\n"
        context += f"- 阶段分布：{img_cons['阶段分布']}\n"
        context += f"- 阶段稳定性：{adj_cons['阶段稳定性']:.1%}\n"
        context += f"- 动作稳定性：{adj_cons['动作稳定性']:.1%}\n"
        context += f"- 工具出现率：{tool_analysis['工具出现率']:.1%}\n\n"
        
        # 添加逐帧标注
        context += f"## 逐帧标注\n\n"
        
        for i, analysis in enumerate(frame_analyses):
            timestamp = analysis.get('timestamp', 0)
            context += f"### 第{i+1}帧（时间：{timestamp:.2f}秒）\n"
            
            phase = analysis.get('phase', '') or ''
            action = analysis.get('action', '') or ''
            tools = analysis.get('tools', '') or ''
            
            context += f"**手术阶段：** {phase}\n"
            context += f"**手术动作：** {action}\n"
            # 工具定位截取前200字符
            tools_display = tools[:200] + "..." if len(tools) > 200 else tools
            context += f"**工具定位：** {tools_display}\n\n"
        
        return context
    
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
        整合多帧分析结果为连贯的叙事摘要
        按照temporal_analyze.py和video_analyze_prompt.txt的逻辑
        只使用文本输入，输出纯中文叙事
        
        参数:
            frame_analyses: 帧分析结果列表，包含阶段、动作、工具
            images: 忽略此参数，只使用纯文本
            system_prompt: 忽略此参数，使用内置提示词
            temperature: 采样温度
            max_tokens: 最大生成长度
            include_background: 是否包含领域背景知识
            
        返回:
            包含整合摘要的字典
        """
        # 执行一致性分析
        consistency_analysis = self._analyze_consistency(frame_analyses)
        
        # 构建内部分析上下文（供模型理解，但不要求输出）
        internal_context = self._build_internal_context(frame_analyses, consistency_analysis)
        
        # 使用完整的中文叙事提示词（聚焦动作和CVS状态）
        system_prompt = """你是一名专业的腹腔镜胆囊切除术视频分析专家。根据逐帧标注生成简洁的中文叙事，重点描述手术动作和安全关键视角状态。

## 安全关键视角三标准

CVS确认需同时满足：
1. 仅两个管状结构连接胆囊（胆囊管和胆囊动脉）
2. 肝胆三角清理干净，可见底部肝脏
3. 胆囊下1/3已从肝床分离

## 输出要求

直接输出一段流畅的中文叙事（2-4句），描述：
1. 当前手术阶段和主要动作
2. 使用的工具及操作方式
3. CVS状态评估（如适用）

## 禁止内容

- 不要输出片段时长、帧数、时间戳
- 不要输出"这是一段...视频片段"这类开头
- 不要输出帧编号或分析指标
- 不要使用英文

## 工具和阶段中文名称

工具：抓钳、电钩、剪刀、钛夹钳、冲吸器、双极电凝
阶段：准备阶段、肝胆三角解剖阶段、夹闭切断阶段、胆囊分离阶段、胆囊牵拉阶段、清洁凝血阶段、胆囊取出阶段

## 时序处理（内部）

- 工具出现<10%帧视为误检，忽略
- 以工具定位为权威来源
- 内部解决矛盾，输出统一叙事

## 示例输出

"当前处于肝胆三角解剖阶段，抓钳牵拉胆囊暴露肝胆三角区域，电钩沿胆囊壁进行精细分离。肝胆三角区域逐步清晰，可见胆囊管和胆囊动脉两个管状结构，CVS第一标准部分达成。"

"胆囊分离阶段，电钩沿胆囊板分离胆囊与肝床连接，抓钳持续牵拉提供张力。分离操作稳定推进，视野清晰。" """
        
        # 构建用户消息（包含帧数据供模型内部分析）
        prompt_text = f"""根据以下逐帧标注，描述当前手术动作和CVS状态：

{internal_context}

直接输出叙事，不要输出时长、帧数或分析过程。"""
        
        # 只使用纯文本聊天
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
            "consistency_analysis": consistency_analysis,
            "error": result.get("error")
        }
    
    def _build_internal_context(
        self,
        frame_analyses: List[Dict[str, Any]],
        consistency_analysis: Dict[str, Any]
    ) -> str:
        """
        构建供模型内部分析的上下文（模型应综合后只输出叙事）
        不包含时长、帧数等信息
        """
        # 添加一致性指标供参考
        img_cons = consistency_analysis["图像级一致性"]
        tool_analysis = consistency_analysis["工具分析"]
        
        context = f"主导阶段：{img_cons['主导阶段']}\n"
        context += f"工具出现率：{tool_analysis['工具出现率']:.0%}\n\n"
        
        # 逐帧数据（简化格式）
        context += "帧标注：\n"
        for i, analysis in enumerate(frame_analyses):
            phase = analysis.get('phase', '') or ''
            action = analysis.get('action', '') or ''
            tools = analysis.get('tools', '') or ''
            # 截取工具信息
            if len(tools) > 120:
                tools = tools[:120] + "..."
            context += f"[{phase}] {action} | {tools}\n"
        
        return context
    
    # ==================== 并发处理方法 ====================
    
    async def summarize_windows_concurrent(
        self,
        windows: List[Dict[str, Any]],
        max_concurrent: int = None
    ) -> List[Dict[str, Any]]:
        """
        并发摘要多个窗口 - 高性能版本
        
        使用 asyncio.gather + Semaphore 并发处理多个窗口，
        比串行处理快 N 倍（N = 并发数）
        
        Args:
            windows: 窗口列表，每个包含:
                - window_id: 窗口 ID
                - frame_analyses: 帧分析结果列表
                - images: 可选图片列表（多模态）
            max_concurrent: 最大并发数，None 使用默认配置
            
        Returns:
            摘要结果列表（按原始顺序）
        """
        if not windows:
            return []
        
        # 使用指定的并发数或默认配置
        semaphore = self._semaphore
        if max_concurrent and max_concurrent != self.concurrent_config.max_concurrent:
            semaphore = asyncio.Semaphore(max_concurrent)
        
        start_time = time.time()
        
        async def summarize_with_semaphore(window: Dict[str, Any], index: int) -> Tuple[int, Dict[str, Any]]:
            """带信号量控制的单窗口摘要"""
            async with semaphore:
                try:
                    result = await self.integrate_analysis_results(
                        frame_analyses=window.get("frame_analyses", []),
                        images=window.get("images")
                    )
                    return index, {
                        "window_id": window.get("window_id"),
                        "success": result.get("success", False),
                        "summary": result.get("summary", ""),
                        "tokens_used": result.get("tokens_used", 0),
                        "error": result.get("error")
                    }
                except Exception as e:
                    logger.warning(f"[GLMClient] Concurrent summarization failed for window {window.get('window_id')}: {e}")
                    return index, {
                        "window_id": window.get("window_id"),
                        "success": False,
                        "summary": f"[Error: {str(e)}]",
                        "error": str(e)
                    }
        
        # 创建所有任务并并发执行
        tasks = [
            summarize_with_semaphore(window, i) 
            for i, window in enumerate(windows)
        ]
        
        # 并发执行
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 按原始顺序排序结果
        sorted_results = [None] * len(windows)
        success_count = 0
        
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"[GLMClient] Unexpected error in concurrent summarization: {result}")
                continue
            
            index, data = result
            sorted_results[index] = data
            if data.get("success"):
                success_count += 1
        
        # 填充失败的结果
        for i, r in enumerate(sorted_results):
            if r is None:
                sorted_results[i] = {
                    "window_id": windows[i].get("window_id"),
                    "success": False,
                    "summary": "[Unknown error]",
                    "error": "Unknown error"
                }
        
        elapsed = time.time() - start_time
        logger.info(
            f"[GLMClient] Concurrent summarization completed: "
            f"{success_count}/{len(windows)} windows in {elapsed:.2f}s"
        )
        
        return sorted_results
    
    async def chat_concurrent(
        self,
        messages: List[Dict[str, Any]],
        max_concurrent: int = None
    ) -> List[Dict[str, Any]]:
        """
        并发处理多个聊天请求
        
        Args:
            messages: 消息列表，每个包含:
                - message: 用户消息
                - system_prompt: 可选系统提示
                - temperature: 可选温度
            max_concurrent: 最大并发数
            
        Returns:
            响应列表（按原始顺序）
        """
        if not messages:
            return []
        
        semaphore = self._semaphore
        if max_concurrent and max_concurrent != self.concurrent_config.max_concurrent:
            semaphore = asyncio.Semaphore(max_concurrent)
        
        async def chat_with_semaphore(msg: Dict[str, Any], index: int) -> Tuple[int, Dict[str, Any]]:
            async with semaphore:
                try:
                    result = await self.chat(
                        message=msg.get("message", ""),
                        system_prompt=msg.get("system_prompt"),
                        temperature=msg.get("temperature")
                    )
                    return index, result
                except Exception as e:
                    return index, {
                        "success": False,
                        "text": f"[Error: {str(e)}]",
                        "error": str(e)
                    }
        
        tasks = [chat_with_semaphore(msg, i) for i, msg in enumerate(messages)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        sorted_results = [None] * len(messages)
        for result in results:
            if isinstance(result, Exception):
                continue
            index, data = result
            sorted_results[index] = data
        
        # 填充失败的结果
        for i, r in enumerate(sorted_results):
            if r is None:
                sorted_results[i] = {
                    "success": False,
                    "text": "[Unknown error]",
                    "error": "Unknown error"
                }
        
        return sorted_results


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

