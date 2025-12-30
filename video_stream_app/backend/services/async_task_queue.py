"""
Async Task Queue Service - 高性能异步任务队列
用于 SurgR1 和 GLM 的并发请求处理

特性:
1. 内存队列 + 并发工作者池
2. 支持优先级队列
3. 自动重试机制
4. 并发数限制 (Semaphore)
5. 任务状态跟踪
"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Awaitable
from collections import defaultdict
import traceback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class Task:
    """表示一个异步任务"""
    id: str
    func: Callable[..., Awaitable[Any]]
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.NORMAL
    callback: Optional[Callable[[Any], None]] = None
    error_callback: Optional[Callable[[Exception], None]] = None
    max_retries: int = 2
    retry_count: int = 0
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: Optional[Exception] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    session_id: Optional[str] = None  # 用于分组任务
    
    def __lt__(self, other):
        """用于优先级队列排序"""
        return self.priority.value > other.priority.value


class AsyncTaskQueue:
    """
    高性能异步任务队列
    
    使用方式:
    ```python
    queue = AsyncTaskQueue(max_concurrent=5)
    await queue.start()
    
    # 提交任务
    task_id = await queue.submit(
        surgr1_client.analyze_frame,
        args=(image,),
        kwargs={"analysis_type": "all"},
        priority=TaskPriority.HIGH
    )
    
    # 等待结果
    result = await queue.wait_for_task(task_id)
    
    await queue.stop()
    ```
    """
    
    def __init__(
        self,
        max_concurrent: int = 5,
        name: str = "default"
    ):
        self.max_concurrent = max_concurrent
        self.name = name
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._tasks: Dict[str, Task] = {}
        self._running = False
        self._workers: List[asyncio.Task] = []
        self._task_events: Dict[str, asyncio.Event] = {}
        self._stats = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "retried": 0
        }
        
        logger.info(f"[{self.name}] AsyncTaskQueue initialized with max_concurrent={max_concurrent}")
    
    async def start(self, num_workers: int = None):
        """启动工作者池"""
        if self._running:
            return
        
        num_workers = num_workers or self.max_concurrent
        self._running = True
        
        for i in range(num_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        
        logger.info(f"[{self.name}] Started {num_workers} workers")
    
    async def stop(self):
        """停止队列"""
        self._running = False
        
        # 取消所有工作者
        for worker in self._workers:
            worker.cancel()
        
        # 等待工作者完成
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        
        logger.info(f"[{self.name}] Stopped")
    
    async def _worker(self, worker_id: int):
        """工作者协程 - 从队列获取并执行任务"""
        logger.debug(f"[{self.name}] Worker {worker_id} started")
        
        while self._running:
            try:
                # 等待任务
                try:
                    _, task = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
                
                # 执行任务
                async with self._semaphore:
                    await self._execute_task(task, worker_id)
                
                self._queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.name}] Worker {worker_id} error: {e}")
        
        logger.debug(f"[{self.name}] Worker {worker_id} stopped")
    
    async def _execute_task(self, task: Task, worker_id: int):
        """执行单个任务"""
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        
        try:
            # 执行异步函数
            result = await task.func(*task.args, **task.kwargs)
            
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = time.time()
            self._stats["completed"] += 1
            
            # 调用成功回调
            if task.callback:
                try:
                    if asyncio.iscoroutinefunction(task.callback):
                        await task.callback(result)
                    else:
                        task.callback(result)
                except Exception as e:
                    logger.warning(f"[{self.name}] Callback error for task {task.id}: {e}")
            
            logger.debug(f"[{self.name}] Task {task.id} completed by worker {worker_id}")
            
        except Exception as e:
            task.error = e
            
            # 重试逻辑
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                task.status = TaskStatus.PENDING
                self._stats["retried"] += 1
                
                logger.warning(f"[{self.name}] Task {task.id} failed, retrying ({task.retry_count}/{task.max_retries}): {e}")
                
                # 重新加入队列（使用负值确保优先级正确）
                await self._queue.put((-task.priority.value, task))
                return
            
            # 最终失败
            task.status = TaskStatus.FAILED
            task.completed_at = time.time()
            self._stats["failed"] += 1
            
            # 调用错误回调
            if task.error_callback:
                try:
                    if asyncio.iscoroutinefunction(task.error_callback):
                        await task.error_callback(e)
                    else:
                        task.error_callback(e)
                except Exception as cb_e:
                    logger.warning(f"[{self.name}] Error callback failed: {cb_e}")
            
            logger.error(f"[{self.name}] Task {task.id} failed after {task.retry_count} retries: {e}")
        
        finally:
            # 触发等待事件
            if task.id in self._task_events:
                self._task_events[task.id].set()
    
    async def submit(
        self,
        func: Callable[..., Awaitable[Any]],
        args: tuple = (),
        kwargs: dict = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        callback: Optional[Callable[[Any], None]] = None,
        error_callback: Optional[Callable[[Exception], None]] = None,
        max_retries: int = 2,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None
    ) -> str:
        """
        提交任务到队列
        
        Args:
            func: 异步函数
            args: 位置参数
            kwargs: 关键字参数
            priority: 优先级
            callback: 成功回调
            error_callback: 失败回调
            max_retries: 最大重试次数
            session_id: 会话 ID（用于分组）
            task_id: 自定义任务 ID
            
        Returns:
            任务 ID
        """
        task_id = task_id or str(uuid.uuid4())[:8]
        
        task = Task(
            id=task_id,
            func=func,
            args=args,
            kwargs=kwargs or {},
            priority=priority,
            callback=callback,
            error_callback=error_callback,
            max_retries=max_retries,
            session_id=session_id
        )
        
        self._tasks[task_id] = task
        self._task_events[task_id] = asyncio.Event()
        self._stats["submitted"] += 1
        
        # 使用负值确保高优先级任务先被处理 (PriorityQueue 取最小值)
        # CRITICAL=3 -> -3, HIGH=2 -> -2, NORMAL=1 -> -1, LOW=0 -> 0
        await self._queue.put((-priority.value, task))
        
        logger.debug(f"[{self.name}] Task {task_id} submitted with priority {priority.name}")
        return task_id
    
    async def submit_batch(
        self,
        tasks_data: List[Dict[str, Any]],
        priority: TaskPriority = TaskPriority.NORMAL,
        session_id: Optional[str] = None
    ) -> List[str]:
        """
        批量提交任务
        
        Args:
            tasks_data: 任务数据列表，每个包含 func, args, kwargs
            priority: 优先级
            session_id: 会话 ID
            
        Returns:
            任务 ID 列表
        """
        task_ids = []
        
        for data in tasks_data:
            task_id = await self.submit(
                func=data["func"],
                args=data.get("args", ()),
                kwargs=data.get("kwargs", {}),
                priority=priority,
                callback=data.get("callback"),
                error_callback=data.get("error_callback"),
                session_id=session_id
            )
            task_ids.append(task_id)
        
        return task_ids
    
    async def wait_for_task(self, task_id: str, timeout: float = None) -> Any:
        """
        等待任务完成并返回结果
        
        Args:
            task_id: 任务 ID
            timeout: 超时时间
            
        Returns:
            任务结果
            
        Raises:
            TimeoutError: 超时
            Exception: 任务执行失败
        """
        if task_id not in self._task_events:
            raise KeyError(f"Task {task_id} not found")
        
        event = self._task_events[task_id]
        
        try:
            if timeout:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            else:
                await event.wait()
        except asyncio.TimeoutError:
            raise TimeoutError(f"Task {task_id} timed out after {timeout}s")
        
        task = self._tasks[task_id]
        
        if task.status == TaskStatus.FAILED:
            raise task.error
        
        return task.result
    
    async def wait_for_batch(
        self,
        task_ids: List[str],
        timeout: float = None,
        return_exceptions: bool = True
    ) -> List[Any]:
        """
        等待批量任务完成
        
        Args:
            task_ids: 任务 ID 列表
            timeout: 总超时时间
            return_exceptions: 是否在结果中包含异常
            
        Returns:
            结果列表（与 task_ids 顺序对应）
        """
        async def wait_one(task_id: str):
            try:
                return await self.wait_for_task(task_id, timeout=timeout)
            except Exception as e:
                if return_exceptions:
                    return e
                raise
        
        results = await asyncio.gather(
            *[wait_one(tid) for tid in task_ids],
            return_exceptions=return_exceptions
        )
        
        return results
    
    def get_task_status(self, task_id: str) -> Optional[Task]:
        """获取任务状态"""
        return self._tasks.get(task_id)
    
    def get_session_tasks(self, session_id: str) -> List[Task]:
        """获取会话的所有任务"""
        return [t for t in self._tasks.values() if t.session_id == session_id]
    
    def get_stats(self) -> Dict[str, Any]:
        """获取队列统计信息"""
        return {
            **self._stats,
            "pending": self._queue.qsize(),
            "total_tasks": len(self._tasks),
            "workers": len(self._workers),
            "max_concurrent": self.max_concurrent
        }
    
    def clear_completed_tasks(self, max_age: float = 300):
        """清理已完成的任务（防止内存泄漏）"""
        now = time.time()
        to_remove = []
        
        for task_id, task in self._tasks.items():
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                if task.completed_at and (now - task.completed_at) > max_age:
                    to_remove.append(task_id)
        
        for task_id in to_remove:
            del self._tasks[task_id]
            if task_id in self._task_events:
                del self._task_events[task_id]
        
        if to_remove:
            logger.info(f"[{self.name}] Cleared {len(to_remove)} old tasks")


class SurgR1TaskQueue(AsyncTaskQueue):
    """
    SurgR1 专用任务队列
    
    特性:
    - 自动管理 SurgR1 客户端
    - 并发分析多帧
    - 结果自动保存到数据库
    """
    
    def __init__(self, max_concurrent: int = 3):
        super().__init__(max_concurrent=max_concurrent, name="SurgR1")
        self._surgr1_client = None
    
    async def _get_client(self):
        """获取 SurgR1 客户端"""
        if self._surgr1_client is None:
            from .surgr1_client import ensure_surgr1_available
            self._surgr1_client = await ensure_surgr1_available()
        return self._surgr1_client
    
    async def analyze_frame(
        self,
        image,
        session_id: str,
        frame_idx: int,
        timestamp: float,
        analysis_type: str = "all",
        save_to_mysql: bool = True,
        priority: TaskPriority = TaskPriority.NORMAL,
        callback: Optional[Callable] = None
    ) -> str:
        """
        提交单帧分析任务
        
        Returns:
            任务 ID
        """
        client = await self._get_client()
        
        async def _analyze():
            return await client.analyze_frame(
                image=image,
                analysis_type=analysis_type,
                session_id=session_id,
                frame_idx=frame_idx,
                timestamp=timestamp,
                save_to_mysql=save_to_mysql
            )
        
        return await self.submit(
            func=_analyze,
            priority=priority,
            callback=callback,
            session_id=session_id,
            task_id=f"surgr1_{session_id}_{frame_idx}"
        )
    
    async def analyze_frames_concurrent(
        self,
        frames: List[Dict[str, Any]],
        session_id: str,
        analysis_type: str = "all",
        save_to_mysql: bool = True,
        priority: TaskPriority = TaskPriority.NORMAL
    ) -> List[Dict[str, Any]]:
        """
        并发分析多帧
        
        Args:
            frames: 帧列表，每个包含 image, frame_idx, timestamp
            session_id: 会话 ID
            analysis_type: 分析类型
            save_to_mysql: 是否保存到数据库
            priority: 优先级
            
        Returns:
            分析结果列表
        """
        if not frames:
            return []
        
        client = await self._get_client()
        
        # 创建任务
        task_ids = []
        for frame in frames:
            async def _analyze(f=frame):
                return await client.analyze_frame(
                    image=f["image"],
                    analysis_type=analysis_type,
                    session_id=session_id,
                    frame_idx=f["frame_idx"],
                    timestamp=f["timestamp"],
                    save_to_mysql=save_to_mysql
                )
            
            task_id = await self.submit(
                func=_analyze,
                priority=priority,
                session_id=session_id
            )
            task_ids.append(task_id)
        
        # 等待所有任务完成
        results = await self.wait_for_batch(task_ids, return_exceptions=True)
        
        # 组装结果
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append({
                    "frame_idx": frames[i]["frame_idx"],
                    "timestamp": frames[i]["timestamp"],
                    "error": str(result),
                    "phase": "",
                    "action": "",
                    "tools": ""
                })
            else:
                final_results.append({
                    "frame_idx": frames[i]["frame_idx"],
                    "timestamp": frames[i]["timestamp"],
                    **result
                })
        
        return final_results


class GLMTaskQueue(AsyncTaskQueue):
    """
    GLM 专用任务队列
    
    特性:
    - 自动管理 GLM 客户端
    - 并发处理多个摘要请求
    - 支持文本和多模态
    """
    
    def __init__(self, max_concurrent: int = 3):
        super().__init__(max_concurrent=max_concurrent, name="GLM")
        self._glm_client = None
    
    async def _get_client(self):
        """获取 GLM 客户端"""
        if self._glm_client is None:
            from .glm_client import ensure_glm_available
            self._glm_client = await ensure_glm_available()
        return self._glm_client
    
    async def summarize_window(
        self,
        frame_analyses: List[Dict[str, Any]],
        window_id: int,
        session_id: str,
        images: Optional[List] = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        callback: Optional[Callable] = None
    ) -> str:
        """
        提交窗口摘要任务
        
        Returns:
            任务 ID
        """
        client = await self._get_client()
        
        async def _summarize():
            return await client.integrate_analysis_results(
                frame_analyses=frame_analyses,
                images=images
            )
        
        return await self.submit(
            func=_summarize,
            priority=priority,
            callback=callback,
            session_id=session_id,
            task_id=f"glm_{session_id}_{window_id}"
        )
    
    async def summarize_windows_concurrent(
        self,
        windows: List[Dict[str, Any]],
        session_id: str,
        priority: TaskPriority = TaskPriority.NORMAL
    ) -> List[Dict[str, Any]]:
        """
        并发摘要多个窗口
        
        Args:
            windows: 窗口列表，每个包含 window_id, frame_analyses, images(optional)
            session_id: 会话 ID
            priority: 优先级
            
        Returns:
            摘要结果列表
        """
        if not windows:
            return []
        
        client = await self._get_client()
        
        # 创建任务
        task_ids = []
        for window in windows:
            async def _summarize(w=window):
                return await client.integrate_analysis_results(
                    frame_analyses=w["frame_analyses"],
                    images=w.get("images")
                )
            
            task_id = await self.submit(
                func=_summarize,
                priority=priority,
                session_id=session_id
            )
            task_ids.append(task_id)
        
        # 等待所有任务完成
        results = await self.wait_for_batch(task_ids, return_exceptions=True)
        
        # 组装结果
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append({
                    "window_id": windows[i]["window_id"],
                    "success": False,
                    "error": str(result),
                    "summary": f"[Error: {str(result)}]"
                })
            else:
                final_results.append({
                    "window_id": windows[i]["window_id"],
                    **result
                })
        
        return final_results
    
    async def chat_concurrent(
        self,
        messages: List[Dict[str, Any]],
        priority: TaskPriority = TaskPriority.HIGH
    ) -> List[Dict[str, Any]]:
        """
        并发处理多个聊天请求
        
        Args:
            messages: 消息列表，每个包含 message, system_prompt(optional)
            priority: 优先级
            
        Returns:
            响应列表
        """
        if not messages:
            return []
        
        client = await self._get_client()
        
        # 创建任务
        task_ids = []
        for msg in messages:
            async def _chat(m=msg):
                return await client.chat(
                    message=m["message"],
                    system_prompt=m.get("system_prompt")
                )
            
            task_id = await self.submit(
                func=_chat,
                priority=priority
            )
            task_ids.append(task_id)
        
        # 等待所有任务完成
        results = await self.wait_for_batch(task_ids, return_exceptions=True)
        
        return results


# ==================== 全局实例管理 ====================

_surgr1_queue: Optional[SurgR1TaskQueue] = None
_glm_queue: Optional[GLMTaskQueue] = None
_queues_started = False


async def get_surgr1_queue(max_concurrent: int = 3) -> SurgR1TaskQueue:
    """获取全局 SurgR1 任务队列"""
    global _surgr1_queue, _queues_started
    
    if _surgr1_queue is None:
        _surgr1_queue = SurgR1TaskQueue(max_concurrent=max_concurrent)
    
    if not _queues_started:
        await _surgr1_queue.start()
        _queues_started = True
    
    return _surgr1_queue


async def get_glm_queue(max_concurrent: int = 3) -> GLMTaskQueue:
    """获取全局 GLM 任务队列"""
    global _glm_queue
    
    if _glm_queue is None:
        _glm_queue = GLMTaskQueue(max_concurrent=max_concurrent)
        await _glm_queue.start()
    
    return _glm_queue


async def shutdown_queues():
    """关闭所有队列"""
    global _surgr1_queue, _glm_queue, _queues_started
    
    if _surgr1_queue:
        await _surgr1_queue.stop()
        _surgr1_queue = None
    
    if _glm_queue:
        await _glm_queue.stop()
        _glm_queue = None
    
    _queues_started = False
    logger.info("All task queues shutdown")

