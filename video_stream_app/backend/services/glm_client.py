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
    max_concurrent: int = 16  # 最大并发请求数
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
    cvs_status: str = "未评估"  # "未评估", "未达成", "部分达成", "已达成"


class WindowHistoryManager:
    """
    窗口历史摘要管理器
    
    使用滑动窗口机制保存最近10个窗口的摘要，
    为GLM分析提供时序上下文。
    """
    
    MAX_HISTORY_SIZE = 10
    
    # 手术阶段顺序定义
    PHASE_ORDER = {
        "Preparation": 0,
        "准备阶段": 0,
        "CalotTriangleDissection": 1,
        "肝胆三角解剖阶段": 1,
        "Calot三角分离": 1,
        "ClippingCutting": 2,
        "夹闭切断阶段": 2,
        "GallbladderDissection": 3,
        "胆囊分离阶段": 3,
        "GallbladderRetraction": 4,
        "胆囊牵拉阶段": 4,
        "CleaningCoagulation": 5,
        "清洁凝血阶段": 5,
        "GallbladderPackaging": 6,
        "胆囊取出阶段": 6,
    }
    
    # 阶段中文名称映射
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
    
    def __init__(self):
        self._history: List[WindowSummary] = []
        self._lock = asyncio.Lock()
    
    async def add_summary(self, summary: WindowSummary) -> None:
        """
        添加新的窗口摘要
        
        新摘要添加到末尾，如果超过最大数量则移除最旧的
        """
        async with self._lock:
            self._history.append(summary)
            if len(self._history) > self.MAX_HISTORY_SIZE:
                self._history.pop(0)  # 移除最旧的
    
    async def get_history(self) -> List[WindowSummary]:
        """获取历史摘要列表"""
        async with self._lock:
            return list(self._history)
    
    async def clear(self) -> None:
        """清空历史"""
        async with self._lock:
            self._history.clear()
    
    def get_phase_order(self, phase: str) -> int:
        """获取阶段的顺序号"""
        return self.PHASE_ORDER.get(phase, -1)
    
    def get_phase_cn_name(self, phase: str) -> str:
        """获取阶段的中文名称"""
        return self.PHASE_CN_NAMES.get(phase, phase)
    
    async def build_history_context(self) -> str:
        """
        构建历史上下文字符串供GLM使用
        
        Returns:
            格式化的历史摘要上下文
        """
        history = await self.get_history()
        if not history:
            return ""
        
        context_lines = ["## 之前窗口分析历史（按时间顺序）\n"]
        
        for i, ws in enumerate(history, 1):
            phase_cn = self.get_phase_cn_name(ws.dominant_phase)
            context_lines.append(f"### 窗口 {ws.window_id}（{ws.start_time:.1f}s - {ws.end_time:.1f}s）")
            context_lines.append(f"- 阶段：{phase_cn}")
            context_lines.append(f"- CVS状态：{ws.cvs_status}")
            if ws.tools:
                context_lines.append(f"- 工具：{', '.join(ws.tools[:5])}")
            context_lines.append(f"- 摘要：{ws.summary}")
            context_lines.append("")
        
        return "\n".join(context_lines)


class PhaseConflictResolver:
    """
    手术阶段矛盾检测和解决器
    
    根据胆囊切除术的阶段定义和时序规则，
    检测并处理帧分析中的阶段矛盾。
    """
    
    # 阶段顺序定义
    PHASE_ORDER = WindowHistoryManager.PHASE_ORDER
    
    # 阶段转换规则（允许的转换）
    ALLOWED_TRANSITIONS = {
        "Preparation": ["CalotTriangleDissection", "GallbladderRetraction"],
        "CalotTriangleDissection": ["ClippingCutting", "GallbladderRetraction", "Preparation"],
        "ClippingCutting": ["GallbladderDissection", "CalotTriangleDissection"],
        "GallbladderDissection": ["CleaningCoagulation", "GallbladderRetraction", "ClippingCutting"],
        "GallbladderRetraction": ["CalotTriangleDissection", "GallbladderDissection", "CleaningCoagulation"],
        "CleaningCoagulation": ["GallbladderPackaging", "GallbladderDissection"],
        "GallbladderPackaging": ["CleaningCoagulation"],
    }
    
    # CVS相关规则
    CVS_RULES = {
        "cvs_required_before_clipping": True,  # ClippingCutting前需要CVS确认
        "cvs_phase": "CalotTriangleDissection",  # CVS在此阶段确认
    }
    
    def __init__(self):
        self.last_confirmed_phase: Optional[str] = None
        self.cvs_achieved: bool = False
    
    def normalize_phase(self, phase: str) -> str:
        """将阶段名称标准化"""
        if not phase:
            return "Unknown"
        
        phase_lower = phase.lower().replace(" ", "").replace("_", "")
        
        # 中文到英文映射
        cn_to_en = {
            "准备阶段": "Preparation",
            "肝胆三角解剖阶段": "CalotTriangleDissection",
            "calot三角分离": "CalotTriangleDissection",
            "夹闭切断阶段": "ClippingCutting",
            "胆囊分离阶段": "GallbladderDissection",
            "胆囊牵拉阶段": "GallbladderRetraction",
            "清洁凝血阶段": "CleaningCoagulation",
            "胆囊取出阶段": "GallbladderPackaging",
        }
        
        for cn, en in cn_to_en.items():
            if cn in phase or cn.replace("阶段", "") in phase:
                return en
        
        # 英文标准化
        mapping = {
            "preparation": "Preparation",
            "calottriangled": "CalotTriangleDissection",
            "calottriangledissection": "CalotTriangleDissection",
            "clippingcutting": "ClippingCutting",
            "gallbladderd": "GallbladderDissection",
            "gallbladderdissection": "GallbladderDissection",
            "gallbladderr": "GallbladderRetraction",
            "gallbladderretraction": "GallbladderRetraction",
            "cleaningc": "CleaningCoagulation",
            "cleaningcoagulation": "CleaningCoagulation",
            "gallbladderp": "GallbladderPackaging",
            "gallbladderpackaging": "GallbladderPackaging",
        }
        
        for key, value in mapping.items():
            if key in phase_lower:
                return value
        
        return phase if len(phase) < 50 else "Unknown"
    
    def detect_conflicts(
        self, 
        frame_analyses: List[Dict[str, Any]],
        history_phases: List[str] = None
    ) -> Dict[str, Any]:
        """
        检测帧分析中的阶段矛盾
        
        Args:
            frame_analyses: 当前窗口的帧分析列表
            history_phases: 历史窗口的阶段序列
            
        Returns:
            矛盾检测结果字典
        """
        conflicts = []
        warnings = []
        
        # 提取当前窗口的阶段
        current_phases = []
        for fa in frame_analyses:
            phase = self.normalize_phase(fa.get("phase", ""))
            if phase and phase != "Unknown":
                current_phases.append(phase)
        
        if not current_phases:
            return {"conflicts": [], "warnings": ["当前窗口无有效阶段识别"], "resolved_phase": "Unknown"}
        
        # 统计阶段分布
        phase_counts = {}
        for p in current_phases:
            phase_counts[p] = phase_counts.get(p, 0) + 1
        
        # 主导阶段
        dominant_phase = max(phase_counts.keys(), key=lambda x: phase_counts[x])
        dominant_ratio = phase_counts[dominant_phase] / len(current_phases)
        
        # 检测1：窗口内阶段一致性
        unique_phases = list(phase_counts.keys())
        if len(unique_phases) > 2:
            conflicts.append({
                "type": "window_inconsistency",
                "message": f"窗口内检测到{len(unique_phases)}个不同阶段",
                "phases": unique_phases,
                "dominant": dominant_phase
            })
        
        # 检测2：历史阶段时序矛盾
        if history_phases:
            last_history_phase = history_phases[-1] if history_phases else None
            if last_history_phase and last_history_phase != "Unknown":
                last_order = self.PHASE_ORDER.get(last_history_phase, -1)
                current_order = self.PHASE_ORDER.get(dominant_phase, -1)
                
                # 检查是否为异常回退（超过2个阶段的回退）
                if last_order > 0 and current_order > 0 and last_order - current_order > 2:
                    conflicts.append({
                        "type": "phase_regression",
                        "message": f"阶段异常回退：从{last_history_phase}回退到{dominant_phase}",
                        "from_phase": last_history_phase,
                        "to_phase": dominant_phase
                    })
        
        # 检测3：CVS相关规则
        if dominant_phase == "ClippingCutting" and not self.cvs_achieved:
            warnings.append({
                "type": "cvs_warning",
                "message": "检测到夹闭切断阶段，但CVS尚未确认"
            })
        
        # 解决矛盾：使用主导阶段，但考虑时序一致性
        resolved_phase = dominant_phase
        
        # 如果主导比例低于50%，考虑历史阶段
        if dominant_ratio < 0.5 and history_phases:
            last_phase = history_phases[-1] if history_phases else None
            if last_phase in phase_counts:
                # 历史阶段在当前窗口也存在，可能是过渡期
                resolved_phase = last_phase
                warnings.append({
                    "type": "transition_detected",
                    "message": f"检测到阶段过渡，维持{last_phase}，准备转入{dominant_phase}"
                })
        
        return {
            "conflicts": conflicts,
            "warnings": warnings,
            "resolved_phase": resolved_phase,
            "phase_distribution": phase_counts,
            "dominant_ratio": dominant_ratio
        }
    
    def build_conflict_context(self, conflict_result: Dict[str, Any]) -> str:
        """
        构建矛盾处理的上下文说明
        
        供GLM在生成摘要时参考
        """
        lines = []
        
        if conflict_result.get("conflicts"):
            lines.append("## 阶段矛盾检测")
            for c in conflict_result["conflicts"]:
                lines.append(f"- {c.get('type')}: {c.get('message')}")
            lines.append("")
        
        if conflict_result.get("warnings"):
            lines.append("## 注意事项")
            for w in conflict_result["warnings"]:
                if isinstance(w, dict):
                    lines.append(f"- {w.get('message')}")
                else:
                    lines.append(f"- {w}")
            lines.append("")
        
        resolved = conflict_result.get("resolved_phase", "Unknown")
        ratio = conflict_result.get("dominant_ratio", 0)
        lines.append(f"## 阶段判定")
        lines.append(f"- 确定阶段：{resolved}")
        lines.append(f"- 置信度：{ratio*100:.0f}%")
        
        return "\n".join(lines)
    
    def update_cvs_status(self, summary_text: str) -> bool:
        """
        根据摘要文本更新CVS状态
        
        检测摘要中是否提到CVS达成
        """
        cvs_keywords = ["CVS达成", "CVS已达成", "安全关键视角确认", "三标准满足", "CVS确认"]
        for kw in cvs_keywords:
            if kw in summary_text:
                self.cvs_achieved = True
                return True
        return False


# Cache background knowledge at module load time
_background_knowledge: str = None
_glm_system_prompt: str = None


def get_background_knowledge() -> str:
    """Get cached background knowledge"""
    global _background_knowledge
    if _background_knowledge is None:
        _background_knowledge = load_background_knowledge()
        if _background_knowledge:
            logger.info(f"[GLMClient] Loaded background knowledge ({len(_background_knowledge)} chars)")
    return _background_knowledge


def get_glm_system_prompt(reload: bool = False) -> str:
    """
    从background.txt中提取GLM窗口分析的system prompt
    
    在 [GLM_SYSTEM_PROMPT_START] 和 [GLM_SYSTEM_PROMPT_END] 之间的内容
    
    Args:
        reload: 是否强制重新加载（用于开发调试）
    """
    global _glm_system_prompt, _background_knowledge
    
    if reload:
        _glm_system_prompt = None
        _background_knowledge = None
    
    if _glm_system_prompt is None:
        background = get_background_knowledge()
        if background:
            start_marker = "[GLM_SYSTEM_PROMPT_START]"
            end_marker = "[GLM_SYSTEM_PROMPT_END]"
            start_idx = background.find(start_marker)
            end_idx = background.find(end_marker)
            
            if start_idx != -1 and end_idx != -1:
                _glm_system_prompt = background[start_idx + len(start_marker):end_idx].strip()
                logger.info(f"[GLMClient] Loaded GLM system prompt ({len(_glm_system_prompt)} chars)")
            else:
                logger.warning("[GLMClient] GLM system prompt markers not found in background.txt")
                _glm_system_prompt = ""
        else:
            _glm_system_prompt = ""
    return _glm_system_prompt


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
        max_concurrent: int = 16  # 最大并发数
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
            
            logger.info(f"[GLMClient] Sending request to {self.api_url}/chat/completions")
            logger.debug(f"[GLMClient] Payload preview: model={self.model_name}, messages={len(messages)}")
            
            response = await self.client.post(
                f"{self.api_url}/chat/completions",
                json=payload
            )
            response.raise_for_status()
            
            logger.info(f"[GLMClient] Received response: status={response.status_code}")
            
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
        include_background: bool = True,
        history_context: Optional[str] = None,
        conflict_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        整合多帧分析结果为连贯的叙事摘要
        支持多模态输入：图片 + R1分析结果
        
        参数:
            frame_analyses: 帧分析结果列表，包含阶段、动作、工具
            images: 帧图片列表，用于GLM多模态验证（如果R1分析不准确，以图片实际内容为准）
            system_prompt: 忽略此参数，使用内置提示词
            temperature: 采样温度
            max_tokens: 最大生成长度
            include_background: 是否包含领域背景知识
            history_context: 之前窗口的摘要历史（最多10个窗口）
            conflict_context: 阶段矛盾检测结果和处理说明
            
        返回:
            包含整合摘要的字典
        """
        # 执行一致性分析
        consistency_analysis = self._analyze_consistency(frame_analyses)
        
        # 构建内部分析上下文（供模型理解，但不要求输出）
        internal_context = self._build_internal_context(frame_analyses, consistency_analysis)
        
        # 从background.txt加载system prompt，确保阶段连续性
        # 每次都尝试重新加载以获取最新的prompt（开发阶段方便调试）
        loaded_prompt = get_glm_system_prompt(reload=True)
        if loaded_prompt:
            system_prompt = loaded_prompt
            logger.debug(f"[GLMClient] Using loaded system prompt from background.txt ({len(loaded_prompt)} chars)")
        else:
            # 如果加载失败，使用简化的默认prompt
            system_prompt = """你是腹腔镜胆囊切除术分析专家。直接描述观察到的手术情况。

输出格式：
【阶段】当前手术阶段名称
【操作】正在进行的具体动作
【工具】使用的器械及用途
【CVS】安全关键视角评估（仅在肝胆三角解剖阶段时评估，其他阶段填"无"）

阶段名称：准备、肝胆三角解剖、夹闭切断、胆囊分离、胆囊牵拉、清洁凝血、胆囊取出
工具中文名：Grasper→抓钳, Hook→电钩, Scissors→剪刀, Clipper→钛夹钳, Irrigator→冲吸器, Bipolar→双极电凝"""
            logger.warning("[GLMClient] Using fallback system prompt")
        
        # 构建用户消息，强调历史上下文
        prompt_parts = []
        
        # 如果有历史上下文（上一窗口信息），优先展示
        if history_context:
            # 提取上一窗口的关键信息
            prev_info = history_context.strip()
            if "摘要：" in prev_info:
                prev_summary = prev_info.split("摘要：")[-1].strip()[:150]
            else:
                prev_summary = prev_info[:150]
            prompt_parts.append(f"【上一窗口分析】{prev_summary}")
            prompt_parts.append("（注意：当前窗口应保持阶段连续性，除非有明显变化）")
            prompt_parts.append("")
        
        # 添加当前窗口的帧分析数据
        prompt_parts.append("【R1分析结果】（可能有误，请结合图片验证）")
        prompt_parts.append(internal_context)
        
        prompt_parts.append("")
        prompt_parts.append("请观察图片实际内容，如果R1分析与图片不符则以图片为准，按格式输出分析结果：")
        
        prompt_text = "\n".join(prompt_parts)
        
        # 判断是否使用多模态（图片+文本）还是纯文本
        if images and len(images) > 0:
            # 使用多模态分析：图片 + R1分析结果
            # 采样图片，最多使用6张（GLM限制）
            max_images = 6
            if len(images) > max_images:
                step = len(images) // max_images
                sampled_images = [images[i] for i in range(0, len(images), step)][:max_images]
            else:
                sampled_images = images
            
            logger.info(f"[GLMClient] Using multimodal analysis with {len(sampled_images)} images")
            
            result = await self.analyze_multiple_images(
                images=sampled_images,
                question=prompt_text,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens
            )
        else:
            # 纯文本分析（没有图片时的fallback）
            logger.info("[GLMClient] Using text-only analysis (no images provided)")
            result = await self.chat(
                message=prompt_text,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens
            )
        
        # 后处理：过滤思考过程，只提取结构化输出
        raw_text = result.get("text", "")
        cleaned_text = self._extract_structured_output(raw_text)
        
        return {
            "success": result.get("success", False),
            "summary": cleaned_text,
            "model": self.model_name,
            "tokens_used": result.get("tokens_used", 0),
            "frame_count": len(frame_analyses),
            "consistency_analysis": consistency_analysis,
            "error": result.get("error")
        }
    
    def _extract_structured_output(self, text: str) -> str:
        """
        从GLM输出中提取结构化内容，过滤掉思考过程
        
        期望格式：
        【阶段】xxx
        【操作】xxx
        【工具】xxx
        【CVS】xxx
        """
        import re
        
        if not text:
            return text
        
        # 定义允许的阶段名称
        valid_phases = ["准备", "肝胆三角解剖", "夹闭切断", "胆囊分离", "胆囊牵拉", "清洁凝血", "胆囊取出"]
        
        # 思考过程的标志词
        thinking_markers = [
            "。现在", "不对", "可能", "应该", "假设", "但", "如果", "那么", 
            "需要", "考虑", "或者", "这里", "看起来", "所以"
        ]
        
        def clean_content(content: str, field_name: str) -> str:
            """清理字段内容，去除思考过程"""
            if not content:
                return content
            
            # 去除首尾空白
            content = content.strip()
            
            # 对于阶段字段，只保留有效阶段名
            if field_name == "阶段":
                for phase in valid_phases:
                    if phase in content:
                        return phase
                # 如果找不到有效阶段，检查是否包含思考内容
                for marker in thinking_markers:
                    if marker in content:
                        return "准备"  # 默认返回准备
                # 只取前10个字符，避免太长
                return content[:10] if len(content) > 10 else content
            
            # 对于其他字段，在思考标志处截断
            for marker in thinking_markers:
                if marker in content:
                    idx = content.find(marker)
                    if idx > 0:
                        content = content[:idx].strip()
                        break
            
            # 只取第一行
            content = content.split('\n')[0].strip()
            
            # 去除尾部的标点（如果后面有句子）
            if content.endswith("，") or content.endswith("。"):
                content = content[:-1]
            
            return content
        
        # 尝试提取【阶段】【操作】【工具】【CVS】【安全】五个字段
        patterns = {
            "阶段": r"【阶段】\s*(.+?)(?=【|$)",
            "操作": r"【操作】\s*(.+?)(?=【|$)",
            "工具": r"【工具】\s*(.+?)(?=【|$)",
            "CVS": r"【CVS】\s*(.+?)(?=【|$)",
            "安全": r"【安全】\s*(.+?)(?=【|$)"
        }
        
        extracted = {}
        for key, pattern in patterns.items():
            match = re.search(pattern, text, re.DOTALL)
            if match:
                content = match.group(1).strip()
                # 清理内容
                extracted[key] = clean_content(content, key)
        
        # 如果成功提取了至少阶段和操作，构建干净的输出
        if "阶段" in extracted and "操作" in extracted:
            phase = extracted.get('阶段', '准备')
            # 确保阶段是有效值
            if phase not in valid_phases:
                for p in valid_phases:
                    if p in phase:
                        phase = p
                        break
                else:
                    phase = "准备"
            
            clean_output = f"【阶段】{phase}\n"
            clean_output += f"【操作】{extracted.get('操作', '')}\n"
            clean_output += f"【工具】{extracted.get('工具', '未识别')}\n"
            clean_output += f"【CVS】{extracted.get('CVS', '未涉及')}"
            
            # 如果有安全问题（出血、器械碰撞），添加到输出
            safety = extracted.get("安全", "正常")
            if safety and safety != "正常" and safety != "无":
                clean_output += f"\n【安全】{safety}"
            
            logger.debug(f"[GLMClient] Extracted structured output from {len(text)} chars")
            return clean_output
        
        # 如果无法提取结构化内容，检查是否以思考过程开头
        thinking_prefixes = [
            "用户现在需要", "首先", "根据", "我需要", "让我", 
            "观察图片", "分析", "现在", "接下来", "好的", "嗯"
        ]
        
        for prefix in thinking_prefixes:
            if text.strip().startswith(prefix):
                # 尝试找到【阶段】开始的位置
                stage_start = text.find("【阶段】")
                if stage_start != -1:
                    # 从【阶段】开始截取
                    return self._extract_structured_output(text[stage_start:])
                else:
                    # 无法找到结构化内容，返回错误提示
                    logger.warning(f"[GLMClient] Could not extract structured output, text starts with thinking")
                    return "【阶段】准备\n【操作】分析中\n【工具】未识别\n【CVS】无"
        
        # 如果文本包含【阶段】但不在开头，尝试提取
        stage_start = text.find("【阶段】")
        if stage_start > 0:
            return self._extract_structured_output(text[stage_start:])
        
        # 返回默认值
        return "【阶段】准备\n【操作】分析中\n【工具】未识别\n【CVS】无"
    
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
                    window_id = window.get("window_id")
                    frame_count = len(window.get("frame_analyses", []))
                    logger.info(f"[GLMClient] Processing window {window_id} with {frame_count} frames")
                    
                    result = await self.integrate_analysis_results(
                        frame_analyses=window.get("frame_analyses", []),
                        images=window.get("images")
                    )
                    
                    logger.info(f"[GLMClient] Window {window_id} completed: success={result.get('success')}")
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

# Global history managers per session
_session_history_managers: Dict[str, WindowHistoryManager] = {}

# Global conflict resolvers per session
_session_conflict_resolvers: Dict[str, PhaseConflictResolver] = {}


def get_glm_client() -> GLMClient:
    """Get the global GLM client instance"""
    global _glm_client
    if _glm_client is None:
        _glm_client = GLMClient()
    return _glm_client


def get_history_manager(session_id: str) -> WindowHistoryManager:
    """
    获取会话对应的历史窗口管理器
    
    每个session_id有独立的历史管理器，保存最近10个窗口的摘要
    """
    global _session_history_managers
    if session_id not in _session_history_managers:
        _session_history_managers[session_id] = WindowHistoryManager()
        logger.info(f"[GLMClient] Created history manager for session {session_id}")
    return _session_history_managers[session_id]


def get_conflict_resolver(session_id: str) -> PhaseConflictResolver:
    """
    获取会话对应的矛盾处理器
    
    每个session_id有独立的矛盾处理器，跟踪阶段进展和CVS状态
    """
    global _session_conflict_resolvers
    if session_id not in _session_conflict_resolvers:
        _session_conflict_resolvers[session_id] = PhaseConflictResolver()
        logger.info(f"[GLMClient] Created conflict resolver for session {session_id}")
    return _session_conflict_resolvers[session_id]


def cleanup_session_resources(session_id: str) -> None:
    """
    清理会话相关资源
    
    在会话结束时调用，释放历史管理器和矛盾处理器
    """
    global _session_history_managers, _session_conflict_resolvers
    if session_id in _session_history_managers:
        del _session_history_managers[session_id]
        logger.info(f"[GLMClient] Cleaned up history manager for session {session_id}")
    if session_id in _session_conflict_resolvers:
        del _session_conflict_resolvers[session_id]
        logger.info(f"[GLMClient] Cleaned up conflict resolver for session {session_id}")


async def ensure_glm_available() -> GLMClient:
    """Get client and verify service is available"""
    client = get_glm_client()
    
    is_healthy = await client.check_health()
    if not is_healthy:
        logger.warning("[GLMClient] GLM service may not be available")
    
    return client

