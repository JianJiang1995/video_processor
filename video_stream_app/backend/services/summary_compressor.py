"""
Summary Compressor Service
压缩多个窗口总结为一个简洁的总结，用于聊天助手上下文。

流程：
1. 检查未压缩的窗口总结数量
2. 当达到阈值（默认5个）时触发压缩
3. 使用配置的VLM进行压缩
4. 保存压缩总结到MySQL
"""
import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load config
CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"
PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_config() -> dict:
    """Load configuration from config.json"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    return {}


def load_compress_prompt() -> str:
    """Load compression prompt from file"""
    config = load_config()
    chat_config = config.get("chat_assistant", {})
    prompt_file = chat_config.get("compress_prompt_file", "prompts/compress_summary_prompt.txt")
    
    # Try relative path from services directory
    prompt_path = Path(__file__).parent / prompt_file
    if not prompt_path.exists():
        # Try absolute path
        prompt_path = Path(prompt_file)
    
    if prompt_path.exists():
        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    # Default prompt if file not found
    logger.warning(f"[SummaryCompressor] Prompt file not found: {prompt_file}, using default")
    return """请将以下多个窗口的手术总结压缩为一个简洁的总结。
保留关键的手术阶段、操作和工具使用信息。

窗口总结：
{window_summaries}

请输出压缩总结："""


class SummaryCompressor:
    """
    窗口总结压缩服务
    
    当积累一定数量的窗口总结后，将它们压缩为一个简洁的总结，
    用于聊天助手的上下文。
    """
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        
        # Load config
        config = load_config()
        chat_config = config.get("chat_assistant", {})
        
        self.compress_window_count = chat_config.get("compress_window_count", 5)
        self.provider = chat_config.get("provider", "gemini")
        self.max_context_summaries = chat_config.get("max_context_summaries", 50)
        
        # Load prompt
        self.compress_prompt = load_compress_prompt()
        
        # Lazy loaded services
        self._mysql_service = None
        self._vlm_client = None
        
        logger.info(f"[SummaryCompressor] Initialized for session {session_id}, "
                   f"compress every {self.compress_window_count} windows, provider: {self.provider}")
    
    @property
    def mysql_service(self):
        if self._mysql_service is None:
            from .mysql_service import get_mysql_service
            self._mysql_service = get_mysql_service()
        return self._mysql_service
    
    @property
    def vlm_client(self):
        if self._vlm_client is None:
            from .vlm_factory import get_vlm_client
            self._vlm_client = get_vlm_client()
        return self._vlm_client
    
    def should_compress(self) -> bool:
        """检查是否需要执行压缩"""
        uncompressed_count = self.mysql_service.get_uncompressed_window_count(self.session_id)
        return uncompressed_count >= self.compress_window_count
    
    async def check_and_compress(self) -> Optional[Dict[str, Any]]:
        """
        检查是否需要压缩，如需要则执行压缩
        
        Returns:
            压缩结果，如果不需要压缩则返回 None
        """
        if not self.should_compress():
            return None
        
        return await self.compress_pending_windows()
    
    async def compress_pending_windows(self) -> Dict[str, Any]:
        """
        压缩待处理的窗口总结
        
        Returns:
            包含压缩结果的字典
        """
        start_time = time.time()
        
        # 获取未压缩的窗口
        windows = self.mysql_service.get_uncompressed_windows(self.session_id)
        
        if not windows:
            return {
                "success": False,
                "error": "No windows to compress"
            }
        
        # 只取前 compress_window_count 个窗口进行压缩
        windows_to_compress = windows[:self.compress_window_count]
        
        logger.info(f"[SummaryCompressor] Compressing {len(windows_to_compress)} windows "
                   f"(IDs: {windows_to_compress[0]['window_id']}-{windows_to_compress[-1]['window_id']})")
        
        # 构建压缩输入
        summaries_text = self._format_windows_for_compression(windows_to_compress)
        
        # 调用 VLM 进行压缩
        try:
            compressed_text = await self._call_vlm_compress(summaries_text)
            
            if not compressed_text:
                return {
                    "success": False,
                    "error": "VLM returned empty response"
                }
            
            # 保存压缩结果
            processing_time = time.time() - start_time
            
            summary_id = self.mysql_service.save_compressed_summary(
                session_id=self.session_id,
                window_start_id=windows_to_compress[0]["window_id"],
                window_end_id=windows_to_compress[-1]["window_id"],
                compressed_text=compressed_text,
                time_start=windows_to_compress[0].get("window_start"),
                time_end=windows_to_compress[-1].get("window_end"),
                source_window_ids=[w["window_id"] for w in windows_to_compress],
                processing_time=processing_time
            )
            
            logger.info(f"[SummaryCompressor] Compression completed in {processing_time:.2f}s, "
                       f"saved as ID {summary_id}")
            
            return {
                "success": True,
                "summary_id": summary_id,
                "window_start_id": windows_to_compress[0]["window_id"],
                "window_end_id": windows_to_compress[-1]["window_id"],
                "compressed_text": compressed_text,
                "processing_time": processing_time
            }
            
        except Exception as e:
            logger.error(f"[SummaryCompressor] Compression failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def _format_windows_for_compression(self, windows: List[Dict[str, Any]]) -> str:
        """格式化窗口总结为压缩输入"""
        parts = []
        for w in windows:
            window_id = w.get("window_id", "?")
            time_start = w.get("window_start", 0)
            time_end = w.get("window_end", 0)
            summary = w.get("glm_summary", "")
            phase = w.get("surgical_phase", "")
            
            s_min, s_sec = divmod(int(time_start), 60)
            e_min, e_sec = divmod(int(time_end), 60)
            part = f"### 窗口 {window_id} ({s_min}:{s_sec:02d} - {e_min}:{e_sec:02d})"
            if phase:
                part += f"\n阶段: {phase}"
            if summary:
                part += f"\n{summary}"
            parts.append(part)
        
        return "\n\n".join(parts)
    
    async def _call_vlm_compress(self, summaries_text: str) -> str:
        """调用 VLM 进行压缩"""
        # 构建完整提示
        prompt = self.compress_prompt.replace("{window_summaries}", summaries_text)
        
        # 调用 VLM
        result = await self.vlm_client.chat(
            message=prompt,
            temperature=0.3,  # 低温度以获得更一致的输出
            max_tokens=800
        )
        
        if result.get("success"):
            return result.get("text", "").strip()
        else:
            raise Exception(result.get("error", "VLM call failed"))
    
    def get_context_for_chat(self) -> str:
        """
        获取聊天上下文
        
        返回所有压缩总结 + 未压缩的最近窗口总结
        """
        # 获取压缩总结
        compressed_context = self.mysql_service.get_compressed_summaries_context(
            self.session_id,
            limit=self.max_context_summaries
        )
        
        # 获取未压缩的窗口（可能还不够压缩阈值）
        uncompressed_windows = self.mysql_service.get_uncompressed_windows(self.session_id)
        
        if not uncompressed_windows:
            return compressed_context
        
        # 添加未压缩的窗口
        recent_parts = ["\n## 最近的手术记录\n"]
        for w in uncompressed_windows:
            time_range = ""
            if w.get("window_start") is not None and w.get("window_end") is not None:
                s_min, s_sec = divmod(int(w['window_start']), 60)
                e_min, e_sec = divmod(int(w['window_end']), 60)
                time_range = f" ({s_min}:{s_sec:02d} - {e_min}:{e_sec:02d})"
            
            recent_parts.append(f"### 窗口 {w['window_id']}{time_range}")
            if w.get("surgical_phase"):
                recent_parts.append(f"阶段: {w['surgical_phase']}")
            if w.get("glm_summary"):
                recent_parts.append(w["glm_summary"])
            recent_parts.append("")
        
        recent_context = "\n".join(recent_parts)
        
        if compressed_context == "暂无手术记录总结。":
            return recent_context
        
        return compressed_context + "\n" + recent_context


# ============================================================================
# Session Management
# ============================================================================

_compressor_instances: Dict[str, SummaryCompressor] = {}


def get_summary_compressor(session_id: str) -> SummaryCompressor:
    """获取或创建 SummaryCompressor 实例"""
    if session_id not in _compressor_instances:
        _compressor_instances[session_id] = SummaryCompressor(session_id)
    return _compressor_instances[session_id]


def cleanup_compressor(session_id: str):
    """清理 SummaryCompressor 实例"""
    if session_id in _compressor_instances:
        del _compressor_instances[session_id]


async def check_and_compress_for_session(session_id: str) -> Optional[Dict[str, Any]]:
    """便捷方法：检查并压缩会话的窗口总结"""
    compressor = get_summary_compressor(session_id)
    return await compressor.check_and_compress()
