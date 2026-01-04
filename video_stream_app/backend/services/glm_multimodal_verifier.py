"""
GLM Multimodal Verifier - 多模态验证服务

核心功能：
1. 将帧图像与R1的分析结果一起发送给GLM进行验证
2. GLM会根据实际图像内容验证R1的分析是否准确
3. 如果R1分析与实际图像不符，以实际图像为准进行修正
4. 支持动态批处理（Dynamic Batching）以提高吞吐量

动态批处理策略：
- 累积请求直到达到batch_size或timeout
- 批量发送给GLM处理
- 支持优先级队列

使用示例：
```python
verifier = GLMMultimodalVerifier()
await verifier.start()

# 提交验证任务
task_id = await verifier.submit_verification(
    session_id="session_123",
    frame_data={
        "image": pil_image,
        "frame_idx": 10,
        "timestamp": 5.0,
        "r1_analysis": {
            "phase": "CalotTriangleDissection",
            "action": "Dissecting tissue",
            "tools": "Grasper, Hook"
        }
    }
)

# 等待结果
result = await verifier.wait_for_result(task_id)
```
"""
import asyncio
import logging
import time
import uuid
import base64
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union
from collections import defaultdict
from io import BytesIO
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class VerificationStatus(Enum):
    PENDING = "pending"
    BATCHED = "batched"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class VerificationTask:
    """单个验证任务"""
    id: str
    session_id: str
    frame_idx: int
    timestamp: float
    image: Image.Image
    r1_analysis: Dict[str, Any]
    status: VerificationStatus = VerificationStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    
    def __lt__(self, other):
        """按时间戳排序，用于时序分析"""
        return self.timestamp < other.timestamp


@dataclass
class BatchConfig:
    """动态批处理配置"""
    max_batch_size: int = 8          # 最大批次大小
    batch_timeout: float = 0.5        # 批次超时（秒）
    max_images_per_request: int = 6   # 每个GLM请求最多图片数
    max_concurrent_batches: int = 4   # 最大并发批次数


class GLMMultimodalVerifier:
    """
    GLM多模态验证器
    
    使用GLM-4.6V-Flash对SurgR1的分析结果进行验证和修正。
    支持动态批处理以提高处理效率。
    """
    
    # 验证系统提示词
    VERIFICATION_SYSTEM_PROMPT = """你是腹腔镜手术分析专家。你的任务是验证AI模型（R1）对手术图像的分析是否准确。

你将收到：
1. 按时间顺序排列的手术帧图像（带时间戳）
2. R1模型对每帧的分析结果

你需要：
1. 仔细观察每帧图像的实际内容
2. 对比R1的分析是否与图像内容一致
3. 如果R1分析错误，根据实际图像进行修正

输出格式（JSON）：
{
    "frames": [
        {
            "frame_idx": 帧索引,
            "timestamp": 时间戳,
            "r1_correct": true/false,
            "verified_phase": "修正后的阶段（如R1正确则保持原值）",
            "verified_action": "修正后的动作描述",
            "verified_tools": "修正后的工具列表",
            "correction_notes": "修正说明（如有）"
        }
    ],
    "temporal_summary": "时序分析摘要，描述这段时间内手术进展",
    "consistency_check": "时序一致性检查结果"
}

重要规则：
- 以实际图像内容为准，不要被R1的错误分析误导
- 注意时序连续性，相邻帧的阶段变化应合理
- 工具识别要准确，注意区分：抓钳(Grasper)、电钩(Hook)、剪刀(Scissors)、钛夹钳(Clipper)
- 手术阶段应遵循正常顺序：准备→肝胆三角解剖→夹闭切断→胆囊分离→胆囊牵拉→清洁凝血→胆囊取出"""

    def __init__(
        self,
        batch_config: Optional[BatchConfig] = None,
        glm_client = None
    ):
        self.batch_config = batch_config or BatchConfig()
        self._glm_client = glm_client
        
        # 任务队列（按session分组）
        self._pending_tasks: Dict[str, List[VerificationTask]] = defaultdict(list)
        self._all_tasks: Dict[str, VerificationTask] = {}
        self._task_events: Dict[str, asyncio.Event] = {}
        
        # 批处理控制
        self._batch_semaphore = asyncio.Semaphore(self.batch_config.max_concurrent_batches)
        self._session_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._batch_timers: Dict[str, asyncio.Task] = {}
        
        # 运行状态
        self._running = False
        self._processor_task: Optional[asyncio.Task] = None
        
        # 统计
        self._stats = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "batches_processed": 0,
            "total_frames_verified": 0
        }
        
        logger.info(f"[GLMVerifier] Initialized with batch_size={self.batch_config.max_batch_size}, timeout={self.batch_config.batch_timeout}s")
    
    async def _get_glm_client(self):
        """获取GLM客户端"""
        if self._glm_client is None:
            from .glm_client import ensure_glm_available
            self._glm_client = await ensure_glm_available()
        return self._glm_client
    
    async def start(self):
        """启动验证器"""
        if self._running:
            return
        
        self._running = True
        self._processor_task = asyncio.create_task(self._batch_processor())
        logger.info("[GLMVerifier] Started batch processor")
    
    async def stop(self):
        """停止验证器"""
        self._running = False
        
        # 取消所有batch timer
        for timer in self._batch_timers.values():
            timer.cancel()
        self._batch_timers.clear()
        
        # 取消processor
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        
        logger.info("[GLMVerifier] Stopped")
    
    async def _batch_processor(self):
        """后台批处理器 - 监控并处理累积的任务"""
        while self._running:
            try:
                await asyncio.sleep(0.1)  # 检查间隔
                
                # 检查每个session的pending任务
                for session_id in list(self._pending_tasks.keys()):
                    tasks = self._pending_tasks.get(session_id, [])
                    if not tasks:
                        continue
                    
                    # 检查是否达到batch条件
                    should_process = False
                    
                    # 条件1: 达到最大批次大小
                    if len(tasks) >= self.batch_config.max_batch_size:
                        should_process = True
                        logger.debug(f"[GLMVerifier] Session {session_id}: batch size reached ({len(tasks)})")
                    
                    # 条件2: 最早任务超时
                    if tasks and (time.time() - tasks[0].created_at) > self.batch_config.batch_timeout:
                        should_process = True
                        logger.debug(f"[GLMVerifier] Session {session_id}: batch timeout reached")
                    
                    if should_process:
                        # 异步处理这个batch
                        asyncio.create_task(self._process_session_batch(session_id))
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[GLMVerifier] Batch processor error: {e}")
                await asyncio.sleep(1)
    
    async def _process_session_batch(self, session_id: str):
        """处理单个session的批次"""
        async with self._session_locks[session_id]:
            tasks = self._pending_tasks.get(session_id, [])
            if not tasks:
                return
            
            # 取出batch
            batch_size = min(len(tasks), self.batch_config.max_batch_size)
            batch_tasks = tasks[:batch_size]
            self._pending_tasks[session_id] = tasks[batch_size:]
            
            # 清理空列表
            if not self._pending_tasks[session_id]:
                del self._pending_tasks[session_id]
        
        # 更新状态
        for task in batch_tasks:
            task.status = VerificationStatus.BATCHED
        
        # 处理batch
        async with self._batch_semaphore:
            await self._verify_batch(batch_tasks)
    
    async def _verify_batch(self, tasks: List[VerificationTask]):
        """验证一批任务"""
        if not tasks:
            return
        
        logger.info(f"[GLMVerifier] Processing batch of {len(tasks)} frames for session {tasks[0].session_id}")
        
        # 按时间戳排序
        tasks.sort(key=lambda t: t.timestamp)
        
        # 更新状态
        for task in tasks:
            task.status = VerificationStatus.PROCESSING
        
        try:
            glm_client = await self._get_glm_client()
            
            # 构建多模态请求
            # 由于GLM对图片数量有限制，可能需要分批
            max_images = self.batch_config.max_images_per_request
            
            if len(tasks) <= max_images:
                # 单批次处理
                result = await self._send_verification_request(glm_client, tasks)
                self._apply_results(tasks, result)
            else:
                # 分多个子批次处理
                for i in range(0, len(tasks), max_images):
                    sub_tasks = tasks[i:i + max_images]
                    result = await self._send_verification_request(glm_client, sub_tasks)
                    self._apply_results(sub_tasks, result)
            
            self._stats["batches_processed"] += 1
            self._stats["total_frames_verified"] += len(tasks)
            
        except Exception as e:
            logger.error(f"[GLMVerifier] Batch verification failed: {e}")
            for task in tasks:
                task.status = VerificationStatus.FAILED
                task.error = str(e)
                task.completed_at = time.time()
                self._stats["failed"] += 1
                
                # 触发完成事件
                if task.id in self._task_events:
                    self._task_events[task.id].set()
    
    async def _send_verification_request(
        self,
        glm_client,
        tasks: List[VerificationTask]
    ) -> Dict[str, Any]:
        """发送验证请求到GLM"""
        
        # 构建消息内容
        content = []
        
        # 添加图像（按时序）
        for task in tasks:
            # 转换图像为base64
            image_url = self._image_to_base64_url(task.image)
            content.append({
                "type": "image_url",
                "image_url": {"url": image_url, "detail": "low"}
            })
        
        # 构建R1分析文本
        r1_text = "## R1模型分析结果（按时序）\n\n"
        for task in tasks:
            r1_text += f"### 帧{task.frame_idx}（时间戳: {task.timestamp:.2f}s）\n"
            r1_text += f"- 阶段: {task.r1_analysis.get('phase', '未知')}\n"
            r1_text += f"- 动作: {task.r1_analysis.get('action', '未知')}\n"
            r1_text += f"- 工具: {task.r1_analysis.get('tools', '未知')}\n\n"
        
        r1_text += "\n请验证以上R1分析是否与图像实际内容一致，并进行时序分析。"
        
        content.append({"type": "text", "text": r1_text})
        
        # 构建完整消息
        messages = [
            {"role": "system", "content": self.VERIFICATION_SYSTEM_PROMPT},
            {"role": "user", "content": content}
        ]
        
        # 发送请求
        try:
            response = await glm_client.client.post(
                f"{glm_client.api_url}/chat/completions",
                json={
                    "model": glm_client.model_name,
                    "messages": messages,
                    "temperature": 0.3,  # 低温度保证一致性
                    "max_tokens": 2000
                }
            )
            response.raise_for_status()
            
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            
            # 尝试解析JSON响应
            return self._parse_verification_response(text, tasks)
            
        except Exception as e:
            logger.error(f"[GLMVerifier] GLM request failed: {e}")
            raise
    
    def _parse_verification_response(
        self,
        response_text: str,
        tasks: List[VerificationTask]
    ) -> Dict[str, Any]:
        """解析GLM验证响应"""
        import json
        import re
        
        # 尝试提取JSON
        json_match = re.search(r'\{[\s\S]*\}', response_text)
        
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        # 如果无法解析JSON，构建基于文本的响应
        logger.warning("[GLMVerifier] Could not parse JSON response, using text fallback")
        
        return {
            "frames": [
                {
                    "frame_idx": task.frame_idx,
                    "timestamp": task.timestamp,
                    "r1_correct": True,  # 默认假设正确
                    "verified_phase": task.r1_analysis.get("phase", ""),
                    "verified_action": task.r1_analysis.get("action", ""),
                    "verified_tools": task.r1_analysis.get("tools", ""),
                    "correction_notes": ""
                }
                for task in tasks
            ],
            "temporal_summary": response_text[:500],
            "consistency_check": "基于文本响应"
        }
    
    def _apply_results(
        self,
        tasks: List[VerificationTask],
        result: Dict[str, Any]
    ):
        """将验证结果应用到任务"""
        frames_result = result.get("frames", [])
        
        # 创建frame_idx到result的映射
        result_map = {fr["frame_idx"]: fr for fr in frames_result if "frame_idx" in fr}
        
        for task in tasks:
            frame_result = result_map.get(task.frame_idx, {})
            
            task.result = {
                "r1_correct": frame_result.get("r1_correct", True),
                "verified_phase": frame_result.get("verified_phase", task.r1_analysis.get("phase", "")),
                "verified_action": frame_result.get("verified_action", task.r1_analysis.get("action", "")),
                "verified_tools": frame_result.get("verified_tools", task.r1_analysis.get("tools", "")),
                "correction_notes": frame_result.get("correction_notes", ""),
                "original_r1_analysis": task.r1_analysis,
                "temporal_summary": result.get("temporal_summary", ""),
                "consistency_check": result.get("consistency_check", "")
            }
            
            task.status = VerificationStatus.COMPLETED
            task.completed_at = time.time()
            self._stats["completed"] += 1
            
            # 触发完成事件
            if task.id in self._task_events:
                self._task_events[task.id].set()
            
            logger.debug(f"[GLMVerifier] Task {task.id} completed: r1_correct={task.result['r1_correct']}")
    
    def _image_to_base64_url(self, image: Image.Image) -> str:
        """将PIL图像转换为base64 URL"""
        if image.mode == 'RGBA':
            image = image.convert('RGB')
        
        # 缩小图像以减少token消耗
        max_size = 512
        if max(image.size) > max_size:
            ratio = max_size / max(image.size)
            new_size = (int(image.width * ratio), int(image.height * ratio))
            image = image.resize(new_size, Image.LANCZOS)
        
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=75)
        b64 = base64.b64encode(buffer.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    
    async def submit_verification(
        self,
        session_id: str,
        frame_data: Dict[str, Any]
    ) -> str:
        """
        提交验证任务
        
        Args:
            session_id: 会话ID
            frame_data: 帧数据，包含:
                - image: PIL Image
                - frame_idx: 帧索引
                - timestamp: 时间戳
                - r1_analysis: R1分析结果 dict
        
        Returns:
            任务ID
        """
        task_id = f"verify_{session_id}_{frame_data['frame_idx']}_{uuid.uuid4().hex[:6]}"
        
        task = VerificationTask(
            id=task_id,
            session_id=session_id,
            frame_idx=frame_data["frame_idx"],
            timestamp=frame_data["timestamp"],
            image=frame_data["image"],
            r1_analysis=frame_data.get("r1_analysis", {})
        )
        
        self._all_tasks[task_id] = task
        self._task_events[task_id] = asyncio.Event()
        
        async with self._session_locks[session_id]:
            self._pending_tasks[session_id].append(task)
        
        self._stats["submitted"] += 1
        
        logger.debug(f"[GLMVerifier] Task {task_id} submitted for session {session_id}")
        return task_id
    
    async def submit_batch(
        self,
        session_id: str,
        frames_data: List[Dict[str, Any]]
    ) -> List[str]:
        """
        批量提交验证任务
        
        Args:
            session_id: 会话ID
            frames_data: 帧数据列表
        
        Returns:
            任务ID列表
        """
        task_ids = []
        for frame_data in frames_data:
            task_id = await self.submit_verification(session_id, frame_data)
            task_ids.append(task_id)
        return task_ids
    
    async def wait_for_result(
        self,
        task_id: str,
        timeout: float = 60.0
    ) -> Dict[str, Any]:
        """
        等待验证结果
        
        Args:
            task_id: 任务ID
            timeout: 超时时间（秒）
        
        Returns:
            验证结果
        
        Raises:
            TimeoutError: 超时
            KeyError: 任务不存在
            Exception: 任务失败
        """
        if task_id not in self._task_events:
            raise KeyError(f"Task {task_id} not found")
        
        event = self._task_events[task_id]
        
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Task {task_id} timed out after {timeout}s")
        
        task = self._all_tasks[task_id]
        
        if task.status == VerificationStatus.FAILED:
            raise Exception(task.error or "Unknown error")
        
        return task.result
    
    async def wait_for_batch(
        self,
        task_ids: List[str],
        timeout: float = 120.0
    ) -> List[Dict[str, Any]]:
        """等待批量任务完成"""
        results = []
        
        for task_id in task_ids:
            try:
                result = await self.wait_for_result(task_id, timeout)
                results.append(result)
            except Exception as e:
                results.append({"error": str(e)})
        
        return results
    
    def get_task_status(self, task_id: str) -> Optional[VerificationTask]:
        """获取任务状态"""
        return self._all_tasks.get(task_id)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            **self._stats,
            "pending_sessions": len(self._pending_tasks),
            "total_pending_tasks": sum(len(tasks) for tasks in self._pending_tasks.values()),
            "batch_config": {
                "max_batch_size": self.batch_config.max_batch_size,
                "batch_timeout": self.batch_config.batch_timeout,
                "max_images_per_request": self.batch_config.max_images_per_request
            }
        }
    
    def clear_completed_tasks(self, max_age: float = 300):
        """清理已完成的任务（防止内存泄漏）"""
        now = time.time()
        to_remove = []
        
        for task_id, task in self._all_tasks.items():
            if task.status in (VerificationStatus.COMPLETED, VerificationStatus.FAILED):
                if task.completed_at and (now - task.completed_at) > max_age:
                    to_remove.append(task_id)
        
        for task_id in to_remove:
            del self._all_tasks[task_id]
            if task_id in self._task_events:
                del self._task_events[task_id]
        
        if to_remove:
            logger.info(f"[GLMVerifier] Cleared {len(to_remove)} old tasks")


# ==================== 全局实例管理 ====================

_glm_verifier: Optional[GLMMultimodalVerifier] = None
_verifier_lock = asyncio.Lock()


async def get_glm_verifier() -> GLMMultimodalVerifier:
    """获取全局GLM验证器实例"""
    global _glm_verifier
    
    if _glm_verifier is None:
        async with _verifier_lock:
            if _glm_verifier is None:
                _glm_verifier = GLMMultimodalVerifier()
                await _glm_verifier.start()
    
    return _glm_verifier


async def shutdown_verifier():
    """关闭验证器"""
    global _glm_verifier
    
    if _glm_verifier:
        await _glm_verifier.stop()
        _glm_verifier = None


# ==================== 便捷函数 ====================

async def verify_frames_with_r1(
    session_id: str,
    frames: List[Dict[str, Any]],
    wait_for_results: bool = True,
    timeout: float = 120.0
) -> Union[List[str], List[Dict[str, Any]]]:
    """
    便捷函数：验证帧与R1分析的一致性
    
    Args:
        session_id: 会话ID
        frames: 帧列表，每个包含:
            - image: PIL Image
            - frame_idx: 帧索引
            - timestamp: 时间戳
            - r1_analysis: R1分析结果 (phase, action, tools)
        wait_for_results: 是否等待结果
        timeout: 超时时间
    
    Returns:
        如果wait_for_results=True，返回验证结果列表
        如果wait_for_results=False，返回任务ID列表
    """
    verifier = await get_glm_verifier()
    
    task_ids = await verifier.submit_batch(session_id, frames)
    
    if wait_for_results:
        return await verifier.wait_for_batch(task_ids, timeout)
    else:
        return task_ids

