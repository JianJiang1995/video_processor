"""
Frame Buffer Service - 帧缓冲区服务
用于管理待处理的视频帧，支持动态批处理

核心功能:
1. 帧积累缓冲区 - 收集待处理的帧
2. 动态 Batch Size - 根据积压数量自动调整批处理大小
3. 批处理触发 - 达到条件时触发批量处理
4. 延迟监控 - 跟踪处理延迟，避免积压过大

使用方式:
```python
buffer = get_frame_buffer("session_123")
buffer.add_frame(image, frame_idx, timestamp)

# 获取批次进行处理
batch = await buffer.get_batch()
if batch:
    results = await surgr1_client.analyze_frames_batch(batch)
    buffer.mark_processed(batch)
```
"""
import asyncio
import logging
import time
import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable, Awaitable
from collections import deque
from pathlib import Path
from enum import Enum
import threading

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
class BufferConfig:
    """帧缓冲区配置"""
    min_batch_size: int = 1       # 最小批处理大小
    max_batch_size: int = 10      # 最大批处理大小
    batch_timeout: float = 2.0    # 批处理超时（秒），超时后即使未满也处理
    target_latency: float = 5.0   # 目标延迟（秒），超过则增大 batch size
    max_buffer_size: int = 100    # 最大缓冲区大小，防止内存溢出
    
    @classmethod
    def from_config(cls) -> "BufferConfig":
        """从配置文件加载"""
        config = load_config()
        surgr1_config = config.get("services", {}).get("surgr1", {})
        return cls(
            min_batch_size=surgr1_config.get("min_batch_size", 1),
            max_batch_size=surgr1_config.get("max_batch_size", 10),
            batch_timeout=surgr1_config.get("batch_timeout", 2.0),
            target_latency=surgr1_config.get("target_latency", 5.0),
            max_buffer_size=surgr1_config.get("max_buffer_size", 100)
        )


@dataclass
class FrameData:
    """帧数据"""
    image: Any  # PIL Image 或 bytes
    frame_idx: int
    timestamp: float
    added_at: float = field(default_factory=time.time)
    
    @property
    def age(self) -> float:
        """帧在缓冲区中的时间（秒）"""
        return time.time() - self.added_at


class BufferState(Enum):
    """缓冲区状态"""
    IDLE = "idle"           # 空闲
    COLLECTING = "collecting"  # 收集帧中
    PROCESSING = "processing"  # 正在处理
    OVERLOADED = "overloaded"  # 过载（积压过多）


@dataclass
class BufferStats:
    """缓冲区统计"""
    pending_count: int = 0           # 待处理帧数
    processed_count: int = 0         # 已处理帧数
    total_added: int = 0             # 总添加帧数
    dropped_count: int = 0           # 丢弃帧数
    current_batch_size: int = 1      # 当前批处理大小
    avg_processing_time: float = 0.0 # 平均处理时间
    current_latency: float = 0.0     # 当前延迟（最旧帧的年龄）
    state: BufferState = BufferState.IDLE


class FrameBuffer:
    """
    帧缓冲区
    
    管理单个会话的帧缓冲，支持动态批处理
    """
    
    def __init__(
        self,
        session_id: str,
        config: BufferConfig = None,
        on_batch_ready: Optional[Callable[[List[Dict]], Awaitable[List[Dict]]]] = None
    ):
        self.session_id = session_id
        self.config = config or BufferConfig.from_config()
        self.on_batch_ready = on_batch_ready
        
        # 帧队列
        self._frames: deque[FrameData] = deque(maxlen=self.config.max_buffer_size)
        self._lock = asyncio.Lock()
        
        # 动态批处理大小
        self._current_batch_size = self.config.min_batch_size
        
        # 统计信息
        self._stats = BufferStats()
        self._processing_times: deque[float] = deque(maxlen=20)  # 最近20次处理时间
        
        # 状态
        self._running = False
        self._last_batch_time = time.time()
        
        logger.info(
            f"[FrameBuffer:{session_id}] Initialized: "
            f"batch_size={self.config.min_batch_size}-{self.config.max_batch_size}, "
            f"timeout={self.config.batch_timeout}s"
        )
    
    @property
    def stats(self) -> BufferStats:
        """获取统计信息"""
        self._stats.pending_count = len(self._frames)
        self._stats.current_batch_size = self._current_batch_size
        
        # 计算当前延迟
        if self._frames:
            self._stats.current_latency = self._frames[0].age
        else:
            self._stats.current_latency = 0.0
        
        # 计算平均处理时间
        if self._processing_times:
            self._stats.avg_processing_time = sum(self._processing_times) / len(self._processing_times)
        
        # 更新状态
        if self._stats.pending_count == 0:
            self._stats.state = BufferState.IDLE
        elif self._stats.pending_count > self.config.max_batch_size * 2:
            self._stats.state = BufferState.OVERLOADED
        else:
            self._stats.state = BufferState.COLLECTING
        
        return self._stats
    
    def add_frame(self, image: Any, frame_idx: int, timestamp: float) -> bool:
        """
        添加帧到缓冲区
        
        Args:
            image: 图片数据
            frame_idx: 帧索引
            timestamp: 视频时间戳
            
        Returns:
            是否成功添加（缓冲区满时可能丢弃）
        """
        if len(self._frames) >= self.config.max_buffer_size:
            # 缓冲区满，丢弃最旧的帧
            self._frames.popleft()
            self._stats.dropped_count += 1
            logger.warning(f"[FrameBuffer:{self.session_id}] Buffer full, dropped oldest frame")
        
        frame = FrameData(
            image=image,
            frame_idx=frame_idx,
            timestamp=timestamp
        )
        self._frames.append(frame)
        self._stats.total_added += 1
        
        # 动态调整 batch size
        self._adjust_batch_size()
        
        return True
    
    def _adjust_batch_size(self):
        """根据积压情况动态调整批处理大小 - 激进版本"""
        pending = len(self._frames)
        current_latency = self._frames[0].age if self._frames else 0
        
        # 策略（激进版）：
        # 1. batch_size 直接根据积压数量设置（取积压的一半或 max）
        # 2. 考虑延迟：延迟越大，batch 越大
        # 3. 最小不低于 min_batch_size
        
        if pending == 0:
            return
        
        # 基于积压数量的 batch size（取积压的一半，至少 min）
        backlog_based = max(pending // 2, self.config.min_batch_size)
        
        # 基于延迟的调整因子
        if current_latency > self.config.target_latency * 2:
            # 延迟严重：使用最大 batch
            latency_factor = self.config.max_batch_size
        elif current_latency > self.config.target_latency:
            # 延迟超标：增大 batch
            latency_factor = int(self.config.max_batch_size * 0.8)
        elif current_latency > self.config.target_latency / 2:
            # 延迟适中：使用中等 batch
            latency_factor = int(self.config.max_batch_size * 0.5)
        else:
            # 延迟正常：使用最小 batch
            latency_factor = self.config.min_batch_size
        
        # 取两者的较大值，但不超过 max
        new_batch_size = min(
            max(backlog_based, latency_factor),
            self.config.max_batch_size,
            pending  # 不超过当前积压数
        )
        
        # 更新
        if new_batch_size != self._current_batch_size:
            old_size = self._current_batch_size
            self._current_batch_size = new_batch_size
            if abs(new_batch_size - old_size) > 1:
                logger.debug(
                    f"[FrameBuffer:{self.session_id}] Batch size: {old_size} -> {new_batch_size} "
                    f"(pending={pending}, latency={current_latency:.1f}s)"
                )
    
    async def get_batch(self, wait: bool = True) -> Optional[List[Dict[str, Any]]]:
        """
        获取一批待处理的帧
        
        Args:
            wait: 是否等待足够的帧
            
        Returns:
            帧数据列表，或 None（如果没有足够的帧）
        """
        async with self._lock:
            pending = len(self._frames)
            
            if pending == 0:
                return None
            
            # 决定批处理大小
            time_since_last = time.time() - self._last_batch_time
            
            # 条件1：达到目标批处理大小
            # 条件2：超时（即使未满也处理）
            # 条件3：积压过多
            should_process = (
                pending >= self._current_batch_size or
                time_since_last >= self.config.batch_timeout or
                pending >= self.config.max_batch_size
            )
            
            if not should_process and wait:
                return None
            
            # 取出一批帧
            batch_size = min(pending, self._current_batch_size)
            batch = []
            
            for _ in range(batch_size):
                if self._frames:
                    frame = self._frames.popleft()
                    batch.append({
                        "image": frame.image,
                        "frame_idx": frame.frame_idx,
                        "timestamp": frame.timestamp
                    })
            
            self._last_batch_time = time.time()
            self._stats.state = BufferState.PROCESSING
            
            logger.debug(
                f"[FrameBuffer:{self.session_id}] Batch ready: "
                f"{len(batch)} frames, remaining: {len(self._frames)}"
            )
            
            return batch
    
    def record_processing_time(self, elapsed: float, batch_size: int):
        """记录处理时间（用于统计和调优）"""
        per_frame_time = elapsed / batch_size if batch_size > 0 else elapsed
        self._processing_times.append(per_frame_time)
        self._stats.processed_count += batch_size
    
    def clear(self):
        """清空缓冲区"""
        self._frames.clear()
        self._stats = BufferStats()
        self._current_batch_size = self.config.min_batch_size


class FrameBufferService:
    """
    帧缓冲区服务
    
    管理多个会话的帧缓冲区，提供全局接口
    """
    
    def __init__(self):
        self._buffers: Dict[str, FrameBuffer] = {}
        self._config = BufferConfig.from_config()
        self._lock = threading.Lock()
        
        logger.info("[FrameBufferService] Initialized")
    
    def get_buffer(self, session_id: str) -> FrameBuffer:
        """获取或创建会话的帧缓冲区"""
        with self._lock:
            if session_id not in self._buffers:
                self._buffers[session_id] = FrameBuffer(
                    session_id=session_id,
                    config=self._config
                )
            return self._buffers[session_id]
    
    def remove_buffer(self, session_id: str):
        """移除会话的帧缓冲区"""
        with self._lock:
            if session_id in self._buffers:
                self._buffers[session_id].clear()
                del self._buffers[session_id]
                logger.info(f"[FrameBufferService] Removed buffer for {session_id}")
    
    def get_all_stats(self) -> Dict[str, Dict]:
        """获取所有缓冲区的统计信息"""
        with self._lock:
            stats = {}
            for session_id, buffer in self._buffers.items():
                s = buffer.stats
                stats[session_id] = {
                    "pending_count": s.pending_count,
                    "processed_count": s.processed_count,
                    "total_added": s.total_added,
                    "dropped_count": s.dropped_count,
                    "current_batch_size": s.current_batch_size,
                    "avg_processing_time": round(s.avg_processing_time, 3),
                    "current_latency": round(s.current_latency, 2),
                    "state": s.state.value
                }
            return stats
    
    def update_config(self, **kwargs):
        """更新配置（影响新创建的缓冲区）"""
        for key, value in kwargs.items():
            if hasattr(self._config, key):
                setattr(self._config, key, value)
        logger.info(f"[FrameBufferService] Config updated: {kwargs}")


# ==================== 全局实例 ====================

_frame_buffer_service: Optional[FrameBufferService] = None


def get_frame_buffer_service() -> FrameBufferService:
    """获取全局帧缓冲区服务"""
    global _frame_buffer_service
    if _frame_buffer_service is None:
        _frame_buffer_service = FrameBufferService()
    return _frame_buffer_service


def get_frame_buffer(session_id: str) -> FrameBuffer:
    """获取指定会话的帧缓冲区"""
    return get_frame_buffer_service().get_buffer(session_id)

