"""
SAM3 视频追踪器 - 使用 SAM3 的 propagate_in_video 功能进行真正的视频追踪
参考: https://github.com/talmolab/sam-track

核心流程:
1. 累积帧到缓冲区目录
2. 当 SurgR1 返回 bbox 时，用帧目录创建 SAM3 session
3. 在 bbox 帧添加 prompt
4. 调用 propagate_in_video 传播到后续帧
5. 缓存每帧的 mask 结果
"""

import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import OrderedDict
import logging

import cv2
import numpy as np
import torch

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Patch torch.autocast
_OriginalAutocast = torch.autocast

class _PatchedAutocast(_OriginalAutocast):
    def __init__(self, device_type, dtype=None, enabled=True, cache_enabled=None):
        if dtype == torch.bfloat16:
            dtype = torch.float32
        super().__init__(device_type, dtype=dtype, enabled=enabled, cache_enabled=cache_enabled)

torch.autocast = _PatchedAutocast

try:
    from sam3.model_builder import build_sam3_video_predictor
except ImportError:
    logger.error("Could not import sam3. Make sure the sam3 package exists.")

try:
    from config_loader import get_tool_color, get_visualization_config
except ImportError:
    TOOL_COLORS = {
        "grasper": (0, 255, 127), "bipolar": (255, 0, 255), "hook": (0, 165, 255),
        "scissors": (255, 255, 0), "clipper": (147, 20, 255), "irrigator": (255, 191, 0),
        "specimenbag": (0, 255, 255), "forceps": (50, 205, 50), "default": (128, 128, 128),
    }
    def get_tool_color(label: str, instance_id: int = 0) -> Tuple[int, int, int]:
        label_lower = label.lower().strip()
        if label_lower in TOOL_COLORS:
            return TOOL_COLORS[label_lower]
        return TOOL_COLORS["default"]
    def get_visualization_config():
        return {"alpha": 0.4, "contour_thickness": 2}


@dataclass
class TrackedObject:
    """追踪的对象"""
    obj_id: int
    label: str
    color: Tuple[int, int, int]
    first_frame_idx: int
    bbox: Optional[Dict] = None


@dataclass
class VideoTrackingSession:
    """视频追踪会话"""
    session_id: str
    stream_id: str
    frame_dir: str  # 帧保存目录
    frame_count: int = 0
    tracked_objects: Dict[int, TrackedObject] = field(default_factory=dict)
    
    # SAM3 session
    predictor_session_id: Optional[str] = None
    prompt_frame_idx: int = -1  # 添加 prompt 的帧索引
    
    # 缓存每帧的 mask
    mask_cache: Dict[int, Dict[int, np.ndarray]] = field(default_factory=dict)  # frame_idx -> {obj_id -> mask}
    
    # 帧索引映射
    frame_files: Dict[int, str] = field(default_factory=dict)  # frame_idx -> file_path
    timestamp_to_frame: Dict[float, int] = field(default_factory=dict)
    
    is_active: bool = True
    last_propagation_time: float = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class SAM3VideoTracker:
    """
    SAM3 视频追踪器
    
    使用 SAM3 的 propagate_in_video 功能进行真正的视频追踪
    """
    
    def __init__(
        self,
        checkpoint_path: str = "/data/ckpt/sam3/sam3.pt",
        device: str = "cuda",
        frame_buffer_size: int = 150,  # 缓冲 5 秒 @ 30 FPS
        base_frame_dir: str = "/tmp/sam3_tracking_frames"
    ):
        self.device = device
        self.frame_buffer_size = frame_buffer_size
        self.base_frame_dir = base_frame_dir
        self.sessions: Dict[str, VideoTrackingSession] = {}
        self.predictor = None
        self._obj_id_counter = 0
        self._lock = threading.Lock()
        
        # 初始化 SAM3 predictor
        self._init_predictor(checkpoint_path)
        
        # 创建基础目录
        os.makedirs(base_frame_dir, exist_ok=True)
        
        logger.info(f"[SAM3VideoTracker] Initialized with buffer_size={frame_buffer_size}")
    
    def _init_predictor(self, checkpoint_path: str):
        """初始化 SAM3 predictor"""
        try:
            logger.info(f"[SAM3VideoTracker] Loading SAM3 predictor from {checkpoint_path}")
            self.predictor = build_sam3_video_predictor(checkpoint_path=checkpoint_path)
            logger.info("[SAM3VideoTracker] SAM3 predictor loaded successfully")
        except Exception as e:
            logger.error(f"[SAM3VideoTracker] Failed to load SAM3 predictor: {e}")
            self.predictor = None
    
    def _get_next_obj_id(self) -> int:
        self._obj_id_counter += 1
        return self._obj_id_counter
    
    def create_session(self, stream_id: str) -> str:
        """创建追踪会话"""
        session_id = str(uuid.uuid4())[:8]
        frame_dir = os.path.join(self.base_frame_dir, session_id)
        os.makedirs(frame_dir, exist_ok=True)
        
        session = VideoTrackingSession(
            session_id=session_id,
            stream_id=stream_id,
            frame_dir=frame_dir
        )
        self.sessions[session_id] = session
        
        logger.info(f"[SAM3VideoTracker] Created session {session_id} for stream {stream_id}")
        return session_id
    
    def add_frame(
        self,
        session_id: str,
        frame: np.ndarray,
        frame_idx: int,
        timestamp: float
    ) -> bool:
        """添加帧到缓冲区，并在需要时自动传播 mask"""
        if session_id not in self.sessions:
            logger.warning(f"[SAM3VideoTracker] Session not found: {session_id}")
            return False
        
        session = self.sessions[session_id]
        
        with session.lock:
            # 保存帧为 JPEG
            frame_filename = f"{frame_idx:06d}.jpg"
            frame_path = os.path.join(session.frame_dir, frame_filename)
            cv2.imwrite(frame_path, frame)
            
            session.frame_files[frame_idx] = frame_path
            session.timestamp_to_frame[round(timestamp, 3)] = frame_idx
            session.frame_count = len(session.frame_files)
            
            # 清理过旧的帧
            self._cleanup_old_frames(session)
            
            # 如果追踪已初始化，尝试增量传播到新帧
            should_propagate = (
                session.predictor_session_id and 
                session.tracked_objects and 
                frame_idx not in session.mask_cache and
                session.prompt_frame_idx >= 0
            )
            
            if should_propagate:
                # 异步传播（在单独线程中）
                try:
                    self._propagate_single_frame(session, frame_idx, frame)
                except Exception as e:
                    logger.debug(f"[SAM3VideoTracker] Incremental propagation skipped: {e}")
            
            return True
    
    def _propagate_single_frame(
        self,
        session: VideoTrackingSession,
        frame_idx: int,
        frame: np.ndarray
    ):
        """传播 mask 到单个新帧"""
        if not self.predictor or not session.predictor_session_id:
            return
        
        try:
            height, width = frame.shape[:2]
            
            # 获取所有帧的排序列表
            sorted_frames = sorted(session.frame_files.keys())
            if frame_idx not in sorted_frames:
                return
            
            sam3_frame_idx = sorted_frames.index(frame_idx)
            
            # 使用 propagate_in_video 传播到这一帧
            for result in self.predictor.handle_stream_request(
                request={
                    "type": "propagate_in_video",
                    "session_id": session.predictor_session_id,
                    "propagation_direction": "forward",
                    "start_frame_index": sam3_frame_idx - 1 if sam3_frame_idx > 0 else 0,
                    "max_frame_num_to_track": 2  # 只传播当前帧
                }
            ):
                result_sam3_idx = result.get("frame_index")
                outputs = result.get("outputs", {})
                
                if result_sam3_idx is None:
                    continue
                
                # 转换回我们的帧索引
                if result_sam3_idx < len(sorted_frames):
                    our_frame_idx = sorted_frames[result_sam3_idx]
                else:
                    continue
                
                # 只保存目标帧的 mask
                if our_frame_idx != frame_idx:
                    continue
                
                # 提取 mask
                if "out_binary_masks" in outputs:
                    obj_ids = outputs.get("out_obj_ids", list(session.tracked_objects.keys()))
                    masks = outputs["out_binary_masks"]
                    
                    if our_frame_idx not in session.mask_cache:
                        session.mask_cache[our_frame_idx] = {}
                    
                    for i, mask in enumerate(masks):
                        if i < len(obj_ids):
                            obj_id = obj_ids[i]
                        else:
                            obj_id = i
                        
                        if isinstance(mask, torch.Tensor):
                            mask = mask.squeeze().cpu().numpy()
                        
                        # 调整大小
                        if mask.shape[:2] != (height, width):
                            mask = cv2.resize(mask.astype(np.float32), (width, height))
                        
                        session.mask_cache[our_frame_idx][obj_id] = mask
                    
                    break  # 只需要目标帧
                    
        except Exception as e:
            # 增量传播失败不是致命错误
            logger.debug(f"[SAM3VideoTracker] Single frame propagation failed: {e}")
    
    def _cleanup_old_frames(self, session: VideoTrackingSession):
        """清理过旧的帧"""
        if len(session.frame_files) <= self.frame_buffer_size:
            return
        
        # 找到最小的帧索引，删除最旧的帧
        sorted_indices = sorted(session.frame_files.keys())
        frames_to_remove = len(sorted_indices) - self.frame_buffer_size
        
        for idx in sorted_indices[:frames_to_remove]:
            frame_path = session.frame_files.pop(idx, None)
            if frame_path and os.path.exists(frame_path):
                try:
                    os.unlink(frame_path)
                except:
                    pass
            
            # 同时删除对应的 mask 缓存
            session.mask_cache.pop(idx, None)
    
    def add_prompt_and_propagate(
        self,
        session_id: str,
        frame_idx: int,
        bboxes: List[Dict],
        max_frames_to_propagate: int = 90  # 传播到后续 3 秒 @ 30 FPS
    ) -> Dict:
        """
        在指定帧添加 bbox prompt，然后传播到后续帧
        
        这是核心的追踪初始化方法。当 SurgR1 返回 bbox 时调用。
        
        Args:
            session_id: 会话ID
            frame_idx: 添加 prompt 的帧索引
            bboxes: bbox 列表 [{"x1", "y1", "x2", "y2", "label"}, ...]
            max_frames_to_propagate: 最多传播到多少帧
            
        Returns:
            Dict with success status and propagation info
        """
        if session_id not in self.sessions:
            return {"success": False, "error": "Session not found"}
        
        if not self.predictor:
            return {"success": False, "error": "SAM3 predictor not initialized"}
        
        session = self.sessions[session_id]
        
        with session.lock:
            # 检查帧目录是否有帧（允许单帧初始化，后续帧会自动传播）
            if len(session.frame_files) < 1:
                logger.warning(f"[SAM3VideoTracker] No frames in buffer")
                return {"success": False, "error": "No frames in buffer"}
            
            # 确保 prompt 帧存在
            if frame_idx not in session.frame_files:
                # 使用最接近的帧
                available_frames = sorted(session.frame_files.keys())
                closest = min(available_frames, key=lambda x: abs(x - frame_idx))
                logger.info(f"[SAM3VideoTracker] Frame {frame_idx} not found, using closest: {closest}")
                frame_idx = closest
            
            try:
                # 关闭旧的 predictor session
                if session.predictor_session_id:
                    try:
                        self.predictor.handle_request(
                            request={"type": "close_session", "session_id": session.predictor_session_id}
                        )
                    except:
                        pass
                    session.predictor_session_id = None
                
                # 用帧目录创建新的 SAM3 session
                logger.info(f"[SAM3VideoTracker] Creating SAM3 session with frame_dir: {session.frame_dir}")
                response = self.predictor.handle_request(
                    request={
                        "type": "start_session",
                        "resource_path": session.frame_dir
                    }
                )
                session.predictor_session_id = response["session_id"]
                logger.info(f"[SAM3VideoTracker] SAM3 session created: {session.predictor_session_id}")
                
                # 获取帧尺寸
                frame_path = session.frame_files[frame_idx]
                sample_frame = cv2.imread(frame_path)
                if sample_frame is None:
                    return {"success": False, "error": f"Cannot read frame {frame_path}"}
                
                height, width = sample_frame.shape[:2]
                
                # 计算帧目录中的帧索引（SAM3 使用目录中的顺序）
                sorted_frames = sorted(session.frame_files.keys())
                sam3_frame_idx = sorted_frames.index(frame_idx)
                
                # 清除旧的追踪对象
                session.tracked_objects.clear()
                session.mask_cache.clear()
                
                # 添加每个 bbox 作为 prompt
                for bbox in bboxes:
                    obj_id = self._get_next_obj_id()
                    label = bbox.get("label", "object")
                    
                    x1, y1, x2, y2 = bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"]
                    
                    # 归一化为 xywh 格式
                    box_xywh = [x1/width, y1/height, (x2-x1)/width, (y2-y1)/height]
                    
                    logger.info(f"[SAM3VideoTracker] Adding prompt for {label}: {box_xywh} at frame {sam3_frame_idx}")
                    
                    try:
                        prompt_response = self.predictor.handle_request(
                            request={
                                "type": "add_prompt",
                                "session_id": session.predictor_session_id,
                                "frame_index": sam3_frame_idx,
                                "text": label,
                                "bounding_boxes": [box_xywh],
                                "bounding_box_labels": [1],
                                "obj_id": obj_id
                            }
                        )
                        
                        # 创建追踪对象
                        color = get_tool_color(label, obj_id)
                        session.tracked_objects[obj_id] = TrackedObject(
                            obj_id=obj_id,
                            label=label,
                            color=color,
                            first_frame_idx=frame_idx,
                            bbox=bbox
                        )
                        
                        # 保存初始帧的 mask
                        outputs = prompt_response.get("outputs", {})
                        if "out_binary_masks" in outputs:
                            masks = outputs["out_binary_masks"]
                            if len(masks) > 0:
                                mask = masks[0]
                                if isinstance(mask, torch.Tensor):
                                    mask = mask.squeeze().cpu().numpy()
                                
                                if frame_idx not in session.mask_cache:
                                    session.mask_cache[frame_idx] = {}
                                session.mask_cache[frame_idx][obj_id] = mask
                        
                        logger.info(f"[SAM3VideoTracker] Prompt added successfully for {label} (obj_id={obj_id})")
                        
                    except Exception as e:
                        logger.error(f"[SAM3VideoTracker] Failed to add prompt for {label}: {e}")
                
                session.prompt_frame_idx = frame_idx
                
                # 传播到后续帧
                propagated_count = self._propagate_forward(
                    session, 
                    sam3_frame_idx, 
                    sorted_frames,
                    max_frames_to_propagate,
                    width, 
                    height
                )
                
                session.last_propagation_time = time.time()
                
                return {
                    "success": True,
                    "prompt_frame": frame_idx,
                    "tracked_objects": len(session.tracked_objects),
                    "propagated_frames": propagated_count,
                    "cached_frames": len(session.mask_cache)
                }
                
            except Exception as e:
                logger.error(f"[SAM3VideoTracker] Error in add_prompt_and_propagate: {e}")
                import traceback
                traceback.print_exc()
                return {"success": False, "error": str(e)}
    
    def _propagate_forward(
        self,
        session: VideoTrackingSession,
        start_sam3_frame_idx: int,
        sorted_frames: List[int],
        max_frames: int,
        width: int,
        height: int
    ) -> int:
        """使用 SAM3 propagate_in_video 传播到后续帧"""
        if not self.predictor or not session.predictor_session_id:
            return 0
        
        try:
            logger.info(f"[SAM3VideoTracker] Propagating from SAM3 frame {start_sam3_frame_idx}")
            
            propagated_count = 0
            
            # 调用 SAM3 的 propagate_in_video
            for result in self.predictor.handle_stream_request(
                request={
                    "type": "propagate_in_video",
                    "session_id": session.predictor_session_id,
                    "propagation_direction": "forward",
                    "start_frame_index": start_sam3_frame_idx,
                    "max_frame_num_to_track": max_frames
                }
            ):
                sam3_frame_idx = result.get("frame_index")
                outputs = result.get("outputs", {})
                
                if sam3_frame_idx is None:
                    continue
                
                # 转换回我们的帧索引
                if sam3_frame_idx < len(sorted_frames):
                    our_frame_idx = sorted_frames[sam3_frame_idx]
                else:
                    continue
                
                # 提取 mask
                if "out_binary_masks" in outputs:
                    obj_ids = outputs.get("out_obj_ids", list(session.tracked_objects.keys()))
                    masks = outputs["out_binary_masks"]
                    
                    if our_frame_idx not in session.mask_cache:
                        session.mask_cache[our_frame_idx] = {}
                    
                    for i, mask in enumerate(masks):
                        if i < len(obj_ids):
                            obj_id = obj_ids[i]
                        else:
                            obj_id = i
                        
                        if isinstance(mask, torch.Tensor):
                            mask = mask.squeeze().cpu().numpy()
                        
                        # 调整大小
                        if mask.shape[:2] != (height, width):
                            mask = cv2.resize(mask.astype(np.float32), (width, height))
                        
                        session.mask_cache[our_frame_idx][obj_id] = mask
                    
                    propagated_count += 1
                    
                    if propagated_count % 10 == 0:
                        logger.info(f"[SAM3VideoTracker] Propagated {propagated_count} frames")
            
            logger.info(f"[SAM3VideoTracker] Propagation complete: {propagated_count} frames")
            return propagated_count
            
        except Exception as e:
            logger.error(f"[SAM3VideoTracker] Propagation error: {e}")
            import traceback
            traceback.print_exc()
            return 0
    
    def get_frame_with_mask(
        self,
        session_id: str,
        frame_idx: int,
        alpha: float = 0.4
    ) -> Optional[Dict]:
        """
        获取带 mask 的帧
        
        Args:
            session_id: 会话ID
            frame_idx: 帧索引
            alpha: mask 透明度
            
        Returns:
            Dict with frame, visualization, and metadata
        """
        if session_id not in self.sessions:
            return None
        
        session = self.sessions[session_id]
        
        with session.lock:
            if frame_idx not in session.frame_files:
                return None
            
            # 读取帧
            frame_path = session.frame_files[frame_idx]
            frame = cv2.imread(frame_path)
            if frame is None:
                return None
            
            # 获取缓存的 mask
            masks = session.mask_cache.get(frame_idx, {})
            
            if not masks:
                # 没有 mask，返回原始帧
                return {
                    "success": True,
                    "frame_idx": frame_idx,
                    "has_mask": False,
                    "frame": frame,
                    "visualization": frame,
                    "tracked_objects": []
                }
            
            # 可视化
            visualization = self._visualize_masks(frame, masks, session, alpha)
            
            return {
                "success": True,
                "frame_idx": frame_idx,
                "has_mask": True,
                "frame": frame,
                "visualization": visualization,
                "num_objects": len(masks),
                "tracked_objects": [
                    {"obj_id": oid, "label": session.tracked_objects.get(oid, TrackedObject(oid, "unknown", (128,128,128), 0)).label}
                    for oid in masks.keys()
                ]
            }
    
    def get_mask_for_timestamp(
        self,
        session_id: str,
        timestamp: float,
        tolerance: float = 0.1
    ) -> Optional[Dict]:
        """根据时间戳获取 mask"""
        if session_id not in self.sessions:
            return None
        
        session = self.sessions[session_id]
        
        with session.lock:
            # 查找最近的时间戳
            rounded_ts = round(timestamp, 3)
            if rounded_ts in session.timestamp_to_frame:
                frame_idx = session.timestamp_to_frame[rounded_ts]
            else:
                # 查找最近的
                min_diff = float('inf')
                closest_frame_idx = None
                for ts, fidx in session.timestamp_to_frame.items():
                    diff = abs(ts - timestamp)
                    if diff < min_diff and diff <= tolerance:
                        min_diff = diff
                        closest_frame_idx = fidx
                
                if closest_frame_idx is None:
                    return None
                frame_idx = closest_frame_idx
        
        return self.get_frame_with_mask(session_id, frame_idx)
    
    def _visualize_masks(
        self,
        frame: np.ndarray,
        masks: Dict[int, np.ndarray],
        session: VideoTrackingSession,
        alpha: float = 0.4
    ) -> np.ndarray:
        """可视化 mask"""
        viz_config = get_visualization_config()
        alpha = viz_config.get("alpha", alpha)
        contour_thickness = viz_config.get("contour_thickness", 2)
        
        result = frame.copy()
        
        for obj_id, mask in masks.items():
            tracked_obj = session.tracked_objects.get(obj_id)
            if not tracked_obj:
                color = (128, 128, 128)
            else:
                color = tracked_obj.color
            
            # 确保 mask 是 2D
            if mask.ndim > 2:
                mask = mask.squeeze()
            
            # 调整大小
            if mask.shape[:2] != frame.shape[:2]:
                mask = cv2.resize(mask.astype(np.float32), (frame.shape[1], frame.shape[0]))
            
            # 转换为 uint8
            if mask.max() <= 1:
                mask_uint8 = (mask * 255).astype(np.uint8)
            else:
                mask_uint8 = mask.astype(np.uint8)
            
            # 透明 overlay
            mask_bool = mask_uint8 > 128
            for c in range(3):
                result[:, :, c] = np.where(
                    mask_bool,
                    (1 - alpha) * result[:, :, c] + alpha * color[c],
                    result[:, :, c]
                ).astype(np.uint8)
            
            # 绘制轮廓
            contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(result, contours, -1, color, contour_thickness)
        
        return result
    
    def close_session(self, session_id: str):
        """关闭会话"""
        if session_id not in self.sessions:
            return
        
        session = self.sessions[session_id]
        
        # 关闭 SAM3 session
        if session.predictor_session_id and self.predictor:
            try:
                self.predictor.handle_request(
                    request={"type": "close_session", "session_id": session.predictor_session_id}
                )
            except:
                pass
        
        # 清理帧目录
        try:
            shutil.rmtree(session.frame_dir, ignore_errors=True)
        except:
            pass
        
        del self.sessions[session_id]
        logger.info(f"[SAM3VideoTracker] Closed session {session_id}")
    
    def get_session_status(self, session_id: str) -> Optional[Dict]:
        """获取会话状态"""
        if session_id not in self.sessions:
            return None
        
        session = self.sessions[session_id]
        
        return {
            "session_id": session_id,
            "stream_id": session.stream_id,
            "frame_count": session.frame_count,
            "tracked_objects": len(session.tracked_objects),
            "cached_masks": len(session.mask_cache),
            "is_active": session.is_active,
            "prompt_frame_idx": session.prompt_frame_idx
        }


# 全局实例
_video_tracker: Optional[SAM3VideoTracker] = None


def get_video_tracker() -> SAM3VideoTracker:
    """获取全局视频追踪器实例"""
    global _video_tracker
    if _video_tracker is None:
        _video_tracker = SAM3VideoTracker()
    return _video_tracker

