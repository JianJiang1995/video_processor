"""
SAM3 流式视频分割模型 - 支持实时视频流处理和mask传播
参考: https://github.com/matteo-tafuro/sam3-realtime

核心功能:
1. 维护视频流会话
2. 在关键帧接收 SurgR1 的 bbox 作为 prompt
3. 自动将 mask 传播到中间帧
"""
import os
import base64
import torch
import numpy as np
import cv2
import threading
import time
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from collections import OrderedDict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Patch torch.autocast for float32
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
    logger.error("Could not import sam3. Make sure the sam3 package exists in src/ directory.")

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
    """跟踪的物体"""
    obj_id: int
    label: str
    color: Tuple[int, int, int]
    first_frame_idx: int
    last_mask: Optional[np.ndarray] = None
    bbox: Optional[Dict] = None


@dataclass  
class StreamingSession:
    """流式视频会话"""
    session_id: str
    stream_id: str
    predictor_session_id: Optional[str] = None
    tracked_objects: Dict[int, TrackedObject] = field(default_factory=dict)
    frame_count: int = 0
    last_frame_time: float = 0
    is_active: bool = True
    width: int = 0
    height: int = 0
    last_masks: Dict[int, np.ndarray] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


class SAM3StreamingModel:
    """
    SAM3 流式视频分割模型
    
    支持:
    1. 创建视频流会话
    2. 在任意帧添加 prompt (bbox from SurgR1)
    3. 推理并传播 mask 到后续帧
    """
    
    def __init__(
        self, 
        checkpoint_path: str = "/data/ckpt/sam3/sam3.pt",
        device: str = "cuda",
        max_sessions: int = 5
    ):
        self.device = device
        self.model_path = checkpoint_path
        self.max_sessions = max_sessions
        self.predictor = None
        self.sessions: OrderedDict[str, StreamingSession] = OrderedDict()
        self.sessions_lock = threading.Lock()
        self._next_obj_id = 1
        
        logger.info(f"[SAM3Streaming] Initializing with device={device}...")
        
        if not os.path.exists(checkpoint_path):
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")
        
        try:
            self.predictor = build_sam3_video_predictor(checkpoint_path=checkpoint_path)
            logger.info("[SAM3Streaming] Model loaded successfully.")
        except Exception as e:
            logger.error(f"[SAM3Streaming] Failed to load model: {e}")
            raise RuntimeError(f"Failed to initialize SAM3: {e}")
    
    def _get_next_obj_id(self) -> int:
        """获取下一个物体ID"""
        obj_id = self._next_obj_id
        self._next_obj_id += 1
        return obj_id
    
    def create_session(self, stream_id: str) -> str:
        """
        创建新的流式会话
        
        Args:
            stream_id: 视频流标识符
            
        Returns:
            session_id: 会话ID
        """
        session_id = f"stream_{stream_id}_{int(time.time())}"
        
        with self.sessions_lock:
            # 清理过期会话
            if len(self.sessions) >= self.max_sessions:
                oldest_key = next(iter(self.sessions))
                self._close_session_internal(oldest_key)
            
            session = StreamingSession(
                session_id=session_id,
                stream_id=stream_id
            )
            self.sessions[session_id] = session
        
        logger.info(f"[SAM3Streaming] Created session: {session_id}")
        return session_id
    
    def _close_session_internal(self, session_id: str):
        """内部关闭会话"""
        if session_id in self.sessions:
            session = self.sessions[session_id]
            if session.predictor_session_id and self.predictor:
                try:
                    self.predictor.handle_request(
                        request=dict(
                            type="close_session",
                            session_id=session.predictor_session_id
                        )
                    )
                except Exception as e:
                    logger.warning(f"Error closing predictor session: {e}")
            del self.sessions[session_id]
            logger.info(f"[SAM3Streaming] Closed session: {session_id}")
    
    def close_session(self, session_id: str):
        """关闭会话"""
        with self.sessions_lock:
            self._close_session_internal(session_id)
    
    def add_prompt(
        self,
        session_id: str,
        frame: np.ndarray,
        frame_idx: int,
        bboxes: List[Dict],
        timestamp: float = 0.0
    ) -> Dict:
        """
        在指定帧添加 prompt (bbox)
        
        这是当 SurgR1 分析到新的帧时调用的方法
        
        Args:
            session_id: 会话ID
            frame: BGR格式的帧图像
            frame_idx: 帧索引
            bboxes: bbox列表 [{"x1", "y1", "x2", "y2", "label"}, ...]
            timestamp: 时间戳
            
        Returns:
            Dict with masks and visualization
        """
        if session_id not in self.sessions:
            raise ValueError(f"Session not found: {session_id}")
        
        session = self.sessions[session_id]
        
        with session.lock:
            height, width = frame.shape[:2]
            session.width = width
            session.height = height
            session.last_frame_time = timestamp
            
            # 如果没有 predictor session，创建一个
            # 注意：SAM3 需要图片路径，所以我们先保存临时图片
            import tempfile
            temp_path = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name
            cv2.imwrite(temp_path, frame)
            
            try:
                # 每次新的 bbox 都创建新的 session
                if session.predictor_session_id:
                    try:
                        self.predictor.handle_request(
                            request=dict(
                                type="close_session",
                                session_id=session.predictor_session_id
                            )
                        )
                    except:
                        pass
                
                response = self.predictor.handle_request(
                    request=dict(
                        type="start_session",
                        resource_path=temp_path
                    )
                )
                session.predictor_session_id = response["session_id"]
                
                # 处理每个 bbox
                new_masks = {}
                failed_bboxes = []
                
                logger.info(f"[SAM3Streaming] Processing {len(bboxes)} bboxes for frame {frame_idx}")
                
                for bbox_idx, bbox in enumerate(bboxes):
                    obj_id = self._get_next_obj_id()
                    label = bbox.get("label", "object")
                    
                    # 转换 bbox 格式
                    x1, y1, x2, y2 = bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"]
                    
                    # 验证 bbox 有效性
                    if x2 <= x1 or y2 <= y1:
                        logger.warning(f"[SAM3Streaming] Invalid bbox {bbox_idx} ({label}): ({x1},{y1})-({x2},{y2})")
                        continue
                    
                    # 归一化 bbox 为 xywh 格式
                    box_xywh = [x1/width, y1/height, (x2-x1)/width, (y2-y1)/height]
                    
                    logger.debug(f"[SAM3Streaming] bbox {bbox_idx} ({label}): ({x1},{y1})-({x2},{y2}) -> xywh={box_xywh}")
                    
                    try:
                        # 首先尝试使用 bbox + text prompt
                        prompt_response = self.predictor.handle_request(
                            request=dict(
                                type="add_prompt",
                                session_id=session.predictor_session_id,
                                frame_index=0,
                                text=label,
                                bounding_boxes=[box_xywh],
                                bounding_box_labels=[1]
                            )
                        )
                        
                        outputs = prompt_response.get("outputs")
                        mask_found = False
                        
                        if outputs and "out_binary_masks" in outputs:
                            out_masks = outputs["out_binary_masks"]
                            logger.debug(f"[SAM3Streaming] Got {len(out_masks)} masks from SAM3")
                            
                            if len(out_masks) > 0:
                                mask = out_masks[0]
                                if isinstance(mask, torch.Tensor):
                                    mask = mask.squeeze().cpu().numpy()
                                
                                # 放宽验证：只要 mask 非空就接受
                                mask_binary = mask > 0.5
                                mask_area = np.sum(mask_binary)
                                
                                if mask_area > 0:
                                    # 计算 mask 的质心
                                    ys, xs = np.where(mask_binary)
                                    centroid_x = np.mean(xs)
                                    centroid_y = np.mean(ys)
                                    
                                    # 计算与 bbox 的交集比例（仅用于日志）
                                    bbox_mask = np.zeros_like(mask_binary)
                                    bbox_mask[max(0,y1):min(height,y2), max(0,x1):min(width,x2)] = True
                                    intersection = np.sum(mask_binary & bbox_mask)
                                    iou = intersection / (mask_area + 1e-6)
                                    
                                    logger.info(f"[SAM3Streaming] bbox {bbox_idx} ({label}): mask accepted "
                                              f"(area={mask_area}, iou={iou:.2f}, centroid=({centroid_x:.0f},{centroid_y:.0f})) ✓")
                                    
                                    color = get_tool_color(label, obj_id)
                                    tracked_obj = TrackedObject(
                                        obj_id=obj_id,
                                        label=label,
                                        color=color,
                                        first_frame_idx=frame_idx,
                                        last_mask=mask,
                                        bbox=bbox
                                    )
                                    session.tracked_objects[obj_id] = tracked_obj
                                    new_masks[obj_id] = mask
                                    session.last_masks[obj_id] = mask
                                    mask_found = True
                                else:
                                    logger.warning(f"[SAM3Streaming] bbox {bbox_idx} ({label}): empty mask ✗")
                        else:
                            logger.warning(f"[SAM3Streaming] bbox {bbox_idx} ({label}): no outputs from SAM3")
                        
                        if not mask_found:
                            failed_bboxes.append({"bbox": bbox, "reason": "no valid mask"})
                                    
                    except Exception as e:
                        logger.error(f"[SAM3Streaming] Error processing bbox {bbox_idx} ({label}): {e}")
                        import traceback
                        traceback.print_exc()
                        failed_bboxes.append({"bbox": bbox, "reason": str(e)})
                
                if failed_bboxes:
                    logger.warning(f"[SAM3Streaming] {len(failed_bboxes)}/{len(bboxes)} bboxes failed")
                
                # 生成可视化结果
                result_frame = self._visualize_masks(frame, new_masks, session)
                
                session.frame_count = frame_idx + 1
                
                return {
                    "success": True,
                    "frame_idx": frame_idx,
                    "num_objects": len(new_masks),
                    "tracked_objects": [
                        {"obj_id": oid, "label": session.tracked_objects[oid].label}
                        for oid in new_masks.keys()
                    ],
                    "visualization": result_frame
                }
                
            finally:
                # 清理临时文件
                try:
                    os.unlink(temp_path)
                except:
                    pass
    
    def propagate_frame(
        self,
        session_id: str,
        frame: np.ndarray,
        frame_idx: int,
        timestamp: float = 0.0
    ) -> Dict:
        """
        传播 mask 到新帧
        
        对于 SurgR1 没有分析的中间帧，使用上一帧的 mask 并尝试用 SAM3 重新分割
        
        实现策略：
        1. 如果有上一帧的 mask，使用 mask 的 bbox 作为 prompt 重新分割当前帧
        2. 这比简单复用 mask 更准确，因为器械可能移动了
        3. 如果重新分割失败，回退到复用上一帧的 mask
        
        Args:
            session_id: 会话ID
            frame: BGR格式的帧图像
            frame_idx: 帧索引
            timestamp: 时间戳
            
        Returns:
            Dict with propagated masks and visualization
        """
        if session_id not in self.sessions:
            raise ValueError(f"Session not found: {session_id}")
        
        session = self.sessions[session_id]
        
        with session.lock:
            if not session.last_masks:
                # 没有已跟踪的物体，返回原始帧
                return {
                    "success": True,
                    "frame_idx": frame_idx,
                    "num_objects": 0,
                    "propagated": False,
                    "visualization": frame
                }
            
            height, width = frame.shape[:2]
            
            # 尝试使用上一帧的 mask 中心点作为 point prompt 进行重新分割
            # 这比直接复用 mask 更准确，可以跟踪移动的器械
            use_point_propagation = True
            propagated_masks = {}
            
            if use_point_propagation and self.predictor:
                try:
                    # 保存当前帧到临时文件
                    import tempfile
                    temp_path = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name
                    cv2.imwrite(temp_path, frame)
                    
                    try:
                        # 为当前帧创建新的 predictor session
                        if session.predictor_session_id:
                            try:
                                self.predictor.handle_request(
                                    request=dict(
                                        type="close_session",
                                        session_id=session.predictor_session_id
                                    )
                                )
                            except:
                                pass
                        
                        response = self.predictor.handle_request(
                            request=dict(
                                type="start_session",
                                resource_path=temp_path
                            )
                        )
                        session.predictor_session_id = response["session_id"]
                        
                        # 对每个跟踪的物体，使用其 mask 中心作为 point prompt
                        for obj_id, mask in session.last_masks.items():
                            if obj_id not in session.tracked_objects:
                                continue
                            
                            tracked_obj = session.tracked_objects[obj_id]
                            
                            # 计算 mask 的中心点
                            mask_binary = mask > 0.5 if mask.max() <= 1 else mask > 128
                            if not np.any(mask_binary):
                                continue
                            
                            ys, xs = np.where(mask_binary)
                            center_x = np.mean(xs) / width  # 归一化
                            center_y = np.mean(ys) / height
                            
                            # 也可以使用 bbox 的中心
                            if tracked_obj.bbox:
                                bbox = tracked_obj.bbox
                                bbox_cx = (bbox["x1"] + bbox["x2"]) / 2 / width
                                bbox_cy = (bbox["y1"] + bbox["y2"]) / 2 / height
                                # 取 mask 中心和 bbox 中心的平均
                                center_x = (center_x + bbox_cx) / 2
                                center_y = (center_y + bbox_cy) / 2
                            
                            try:
                                # 使用点 prompt 进行分割
                                prompt_response = self.predictor.handle_request(
                                    request=dict(
                                        type="add_prompt",
                                        session_id=session.predictor_session_id,
                                        frame_index=0,
                                        text=tracked_obj.label,
                                        points=[[center_x, center_y]],
                                        point_labels=[1],  # 1 = foreground
                                    )
                                )
                                
                                outputs = prompt_response.get("outputs")
                                if outputs and "out_binary_masks" in outputs:
                                    out_masks = outputs["out_binary_masks"]
                                    if len(out_masks) > 0:
                                        new_mask = out_masks[0]
                                        if isinstance(new_mask, torch.Tensor):
                                            new_mask = new_mask.squeeze().cpu().numpy()
                                        
                                        # 验证新 mask 有效
                                        if np.any(new_mask > 0.5):
                                            propagated_masks[obj_id] = new_mask
                                            logger.debug(f"Propagated mask for {tracked_obj.label} using point prompt")
                                
                            except Exception as e:
                                logger.warning(f"Point prompt failed for {tracked_obj.label}: {e}")
                        
                    finally:
                        # 清理临时文件
                        try:
                            os.unlink(temp_path)
                        except:
                            pass
                            
                except Exception as e:
                    logger.warning(f"Point-based propagation failed: {e}")
            
            # 如果 point propagation 成功，使用新的 mask；否则回退到旧 mask
            if propagated_masks:
                # 更新 session 中的 mask
                for obj_id, new_mask in propagated_masks.items():
                    session.last_masks[obj_id] = new_mask
                    if obj_id in session.tracked_objects:
                        session.tracked_objects[obj_id].last_mask = new_mask
                
                result_frame = self._visualize_masks(frame, session.last_masks, session)
                logger.debug(f"Frame {frame_idx}: propagated {len(propagated_masks)} masks using SAM3")
            else:
                # 回退：直接复用上一帧的 mask（简单但可能不准确）
                result_frame = self._visualize_masks(frame, session.last_masks, session)
                logger.debug(f"Frame {frame_idx}: reused {len(session.last_masks)} masks (fallback)")
            
            session.frame_count = frame_idx + 1
            session.last_frame_time = timestamp
            
            return {
                "success": True,
                "frame_idx": frame_idx,
                "num_objects": len(session.last_masks),
                "propagated": True,
                "propagated_with_sam3": len(propagated_masks) > 0,
                "tracked_objects": [
                    {"obj_id": oid, "label": session.tracked_objects[oid].label}
                    for oid in session.last_masks.keys()
                    if oid in session.tracked_objects
                ],
                "visualization": result_frame
            }
    
    def process_frame(
        self,
        session_id: str,
        frame: np.ndarray,
        frame_idx: int,
        timestamp: float = 0.0,
        bboxes: Optional[List[Dict]] = None
    ) -> Dict:
        """
        处理单帧 - 统一入口
        
        如果提供了 bboxes，会添加新的 prompt
        否则，使用传播的 mask
        
        Args:
            session_id: 会话ID
            frame: BGR格式的帧图像
            frame_idx: 帧索引
            timestamp: 时间戳
            bboxes: 可选的 bbox 列表 (来自 SurgR1)
            
        Returns:
            Dict with masks and visualization
        """
        if bboxes and len(bboxes) > 0:
            return self.add_prompt(session_id, frame, frame_idx, bboxes, timestamp)
        else:
            return self.propagate_frame(session_id, frame, frame_idx, timestamp)
    
    def _visualize_masks(
        self,
        frame: np.ndarray,
        masks: Dict[int, np.ndarray],
        session: StreamingSession,
        alpha: float = 0.4,
        contour_thickness: int = 2
    ) -> np.ndarray:
        """可视化 masks"""
        viz_config = get_visualization_config()
        alpha = viz_config.get("alpha", alpha)
        contour_thickness = viz_config.get("contour_thickness", contour_thickness)
        
        result = frame.copy()
        
        for obj_id, mask in masks.items():
            if obj_id not in session.tracked_objects:
                continue
            
            tracked_obj = session.tracked_objects[obj_id]
            color = tracked_obj.color
            
            # 确保 mask 是 2D
            if mask.ndim > 2:
                mask = mask.squeeze()
            
            # 调整大小以匹配帧
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
            
            # 添加标签
            if contours:
                M = cv2.moments(contours[0])
                if M["m00"] > 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    cv2.putText(result, tracked_obj.label, (cx-30, cy), 
                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        
        return result
    
    def get_session_status(self, session_id: str) -> Dict:
        """获取会话状态"""
        if session_id not in self.sessions:
            return {"exists": False}
        
        session = self.sessions[session_id]
        return {
            "exists": True,
            "session_id": session_id,
            "stream_id": session.stream_id,
            "frame_count": session.frame_count,
            "tracked_objects": len(session.tracked_objects),
            "is_active": session.is_active
        }
    
    def frame_to_base64(self, frame: np.ndarray) -> str:
        """将帧转换为 base64"""
        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return base64.b64encode(buffer).decode('utf-8')


# 全局实例
_streaming_model: Optional[SAM3StreamingModel] = None


def get_streaming_model() -> SAM3StreamingModel:
    """获取全局流式模型实例"""
    global _streaming_model
    if _streaming_model is None:
        checkpoint = os.environ.get("SAM3_CHECKPOINT", "/data/ckpt/sam3/sam3.pt")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _streaming_model = SAM3StreamingModel(checkpoint_path=checkpoint, device=device)
    return _streaming_model

