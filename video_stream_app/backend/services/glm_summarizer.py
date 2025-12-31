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
    
    async def integrate_analysis_results(
        self,
        frame_analyses: List[Dict[str, Any]],
        images: Optional[List[Image.Image]] = None,
        system_prompt: str = None,
        max_tokens: int = None,
        temperature: float = None,
        history_context: Optional[str] = None,
        conflict_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        整合多帧分析结果为连贯的叙事摘要
        按照temporal_analyze.py和video_analyze_prompt.txt的逻辑
        只使用文本输入，输出纯中文叙事
        
        参数:
            frame_analyses: 帧分析结果列表，包含阶段、动作、工具
            images: 忽略此参数，只使用纯文本
            system_prompt: 忽略此参数，使用内置提示词
            max_tokens: 最大生成长度
            temperature: 采样温度
            history_context: 之前窗口的摘要历史（最多10个窗口）
            conflict_context: 阶段矛盾检测结果和处理说明
            
        返回:
            包含整合摘要的字典
        """
        max_tokens = max_tokens or self.max_tokens
        temperature = temperature or self.temperature
        
        # 执行一致性分析
        consistency_analysis = self._analyze_consistency(frame_analyses)
        
        # 构建内部分析上下文
        internal_context = self._build_internal_context(frame_analyses, consistency_analysis)
        
        # 简洁直接的中文叙事提示词
        system_prompt = """你是腹腔镜胆囊切除术分析专家。直接描述观察到的手术情况。

输出格式（严格按此结构）：
【阶段】当前手术阶段名称
【操作】正在进行的具体动作
【工具】使用的器械及用途
【CVS】安全关键视角评估（仅在相关时提及）

规则：
- 直接陈述观察结果，禁止任何元描述或说明性文字
- 每项内容简洁明了，避免重复
- 使用中文术语：抓钳、电钩、剪刀、钛夹钳、冲吸器、双极电凝
- 阶段名称：准备、肝胆三角解剖、夹闭切断、胆囊分离、胆囊牵拉、清洁凝血、胆囊取出

示例：
【阶段】肝胆三角解剖
【操作】分离胆囊壁周围组织，暴露Calot三角
【工具】抓钳牵拉胆囊，电钩进行精细分离
【CVS】可见胆囊管和胆囊动脉，第一标准部分达成"""
        
        # 构建简洁的用户消息
        prompt_parts = []
        
        # 添加当前窗口的帧分析数据
        prompt_parts.append("分析数据：")
        prompt_parts.append(internal_context)
        
        # 如果有历史上下文，简洁添加
        if history_context:
            prompt_parts.append("")
            prompt_parts.append("上一窗口：" + history_context.split("摘要：")[-1].strip()[:100] if "摘要：" in history_context else "")
        
        prompt_parts.append("")
        prompt_parts.append("按格式输出观察结果：")
        
        prompt_text = "\n".join(prompt_parts)
        
        # 只使用纯文本
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_text}
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
                "frame_count": len(frame_analyses),
                "consistency_analysis": consistency_analysis
            }
            
        except Exception as e:
            logger.error(f"[GLMSummarizer] Integration error: {e}")
            return {
                "success": False,
                "summary": f"[整合结果出错: {str(e)}]",
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

