"""
Analysis Logger - 分析结果日志记录
记录每个会话的 SurgR1 帧分析和 GLM 窗口总结

日志文件保存在: logs/analysis/{session_id}.log

使用方法:
```python
from backend.services.analysis_logger import get_analysis_logger

logger = get_analysis_logger(session_id)
logger.log_r1_frame(frame_idx, timestamp, phase, action, tools)
logger.log_glm_window(window_id, start_time, end_time, summary, images_loaded)
```
"""
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Any

# 日志存储目录
ANALYSIS_LOG_DIR = Path(__file__).parent.parent.parent / "logs" / "analysis"


class AnalysisLogger:
    """会话分析日志记录器"""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.log_dir = ANALYSIS_LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建日志文件 - 使用时间戳命名，session_id 在文件内
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"{timestamp}_{session_id[:8]}.log"
        
        # 设置logger
        self.logger = logging.getLogger(f"analysis.{session_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers = []  # 清除已有handlers
        
        # 文件handler
        fh = logging.FileHandler(self.log_file, encoding='utf-8')
        fh.setLevel(logging.INFO)
        
        # 格式：时间戳 | 消息
        formatter = logging.Formatter('%(asctime)s | %(message)s', datefmt='%H:%M:%S')
        fh.setFormatter(formatter)
        
        self.logger.addHandler(fh)
        
        # 写入头部信息
        self._write_header()
    
    def _write_header(self):
        """写入日志头部"""
        self.logger.info("=" * 80)
        self.logger.info(f"Analysis Log - Session: {self.session_id}")
        self.logger.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.logger.info("=" * 80)
        self.logger.info("")
    
    def log_r1_frame(
        self,
        frame_idx: int,
        timestamp: float,
        phase: str,
        action: str,
        tools: str
    ):
        """
        记录 SurgR1 单帧分析结果（完整输出，不截断）
        
        Args:
            frame_idx: 帧索引
            timestamp: 时间戳（秒）
            phase: 手术阶段
            action: 手术动作
            tools: 工具定位
        """
        self.logger.info(f"[R1] Frame {frame_idx:4d} @ {timestamp:6.2f}s")
        self.logger.info(f"     Phase:")
        # 完整输出 phase，按行分割
        for line in (phase or "N/A").split('\n'):
            self.logger.info(f"       {line}")
        self.logger.info(f"     Action:")
        for line in (action or "N/A").split('\n'):
            self.logger.info(f"       {line}")
        if tools:
            self.logger.info(f"     Tools:")
            for line in tools.split('\n'):
                self.logger.info(f"       {line}")
        self.logger.info("")
    
    def log_glm_window(
        self,
        window_id: int,
        start_time: float,
        end_time: float,
        summary: str,
        images_loaded: int = 0,
        frame_count: int = 0
    ):
        """
        记录 GLM 窗口总结
        
        Args:
            window_id: 窗口 ID
            start_time: 开始时间
            end_time: 结束时间
            summary: GLM 总结文本
            images_loaded: 加载的图片数量
            frame_count: 该窗口的帧数
        """
        self.logger.info("-" * 60)
        self.logger.info(f"[GLM] Window {window_id} ({start_time:.1f}s - {end_time:.1f}s)")
        self.logger.info(f"      Frames: {frame_count}, Images: {images_loaded} (multimodal={images_loaded > 0})")
        self.logger.info("-" * 60)
        
        # 记录完整的 GLM 输出
        for line in summary.split('\n'):
            self.logger.info(f"      {line}")
        
        self.logger.info("-" * 60)
        self.logger.info("")
    
    def log_window_frames(
        self,
        window_id: int,
        frame_analyses: List[dict]
    ):
        """
        记录窗口内所有帧的 R1 分析（完整输出）
        
        每帧在数据库中可能有多条记录（phase/action/tools分析），
        这里按时间戳去重，只显示每个时间点的第一条记录。
        
        Args:
            window_id: 窗口 ID
            frame_analyses: 帧分析列表
        """
        # 按时间戳去重 - 每个时间点只保留第一条记录
        seen_timestamps = set()
        unique_frames = []
        for fa in frame_analyses:
            ts = fa.get("timestamp", 0)
            # 使用4位小数精度避免浮点数比较问题
            ts_key = round(ts, 4)
            if ts_key not in seen_timestamps:
                seen_timestamps.add(ts_key)
                unique_frames.append(fa)
        
        # 按时间戳排序
        unique_frames.sort(key=lambda x: x.get("timestamp", 0))
        
        self.logger.info(f"[Window {window_id}] R1 Frame Analyses ({len(unique_frames)} frames):")
        self.logger.info("-" * 80)
        
        for i, fa in enumerate(unique_frames):
            ts = fa.get("timestamp", 0)
            phase = fa.get("phase", "") or ""
            action = fa.get("action", "") or ""
            tools = fa.get("tools", "") or ""
            
            self.logger.info(f"  Frame {i+1} @ {ts:.2f}s")
            self.logger.info(f"  [Phase]")
            for line in phase.split('\n'):
                self.logger.info(f"    {line}")
            self.logger.info(f"  [Action]")
            for line in action.split('\n'):
                self.logger.info(f"    {line}")
            if tools:
                self.logger.info(f"  [Tools]")
                for line in tools.split('\n'):
                    self.logger.info(f"    {line}")
            self.logger.info("")
        
        self.logger.info("-" * 80)
        self.logger.info("")
    
    def log_info(self, message: str):
        """记录一般信息"""
        self.logger.info(message)
    
    def log_error(self, message: str):
        """记录错误"""
        self.logger.error(f"[ERROR] {message}")
    
    def close(self):
        """关闭日志"""
        self.logger.info("")
        self.logger.info("=" * 80)
        self.logger.info(f"Analysis completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.logger.info("=" * 80)
        
        # 关闭所有handlers
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)


# 全局会话日志管理
_session_loggers: dict = {}


def get_analysis_logger(session_id: str) -> AnalysisLogger:
    """
    获取会话的分析日志记录器
    
    Args:
        session_id: 会话 ID
    
    Returns:
        AnalysisLogger 实例
    """
    global _session_loggers
    
    if session_id not in _session_loggers:
        _session_loggers[session_id] = AnalysisLogger(session_id)
    
    return _session_loggers[session_id]


def close_analysis_logger(session_id: str):
    """关闭会话日志"""
    global _session_loggers
    
    if session_id in _session_loggers:
        _session_loggers[session_id].close()
        del _session_loggers[session_id]

