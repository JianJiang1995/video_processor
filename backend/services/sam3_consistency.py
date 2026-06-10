"""
SAM3 一致性检查器
用于检测何时需要重新初始化 SAM3 流式会话

参考设计文档: docs/SAM3_STREAMING_DESIGN.md
"""
import logging
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SAM3State(Enum):
    """SAM3 流式状态"""
    IDLE = "idle"           # 没有检测到器械
    TRACKING = "tracking"   # 正常跟踪中
    REINIT = "reinit"       # 需要重新初始化


@dataclass
class TrackedInstrument:
    """跟踪的器械信息"""
    obj_id: int
    label: str
    last_bbox: Dict[str, int]  # {x1, y1, x2, y2}
    last_area: int = 0
    last_centroid: Tuple[float, float] = (0, 0)
    frames_tracked: int = 0
    last_update_time: float = 0


@dataclass
class ConsistencyConfig:
    """一致性检查配置"""
    # Mask 质心与 bbox 中心的最大偏离（相对于 bbox 对角线）
    centroid_offset_threshold: float = 0.3
    
    # Mask 面积变化的最大比例
    area_change_threshold: float = 0.5
    
    # 强制刷新间隔（秒）
    forced_refresh_interval: float = 10.0
    
    # 重新初始化后的冷却时间（秒）
    reinit_cooldown: float = 0.5
    
    # IoU 匹配阈值
    iou_threshold: float = 0.3
    
    # 最大传播帧数
    max_propagate_frames: int = 30


@dataclass
class ReinitDecision:
    """重新初始化决策"""
    need_reinit: bool
    reason: Optional[str] = None
    reinit_type: str = "none"  # "none", "full", "partial"
    affected_objects: List[int] = field(default_factory=list)


class SAM3ConsistencyChecker:
    """
    SAM3 一致性检查器
    
    检测何时需要重新初始化 SAM3 会话，包括：
    1. 器械数量变化
    2. 新器械类型出现
    3. Mask 与 BBox 偏离过大
    4. Mask 面积异常变化
    5. 定时强制刷新
    """
    
    def __init__(self, config: Optional[ConsistencyConfig] = None):
        self.config = config or ConsistencyConfig()
        self.tracked_instruments: Dict[int, TrackedInstrument] = {}
        self.last_reinit_time: float = 0
        self.frames_since_reinit: int = 0
        self.state = SAM3State.IDLE
        self._next_obj_id = 1
    
    def get_next_obj_id(self) -> int:
        """获取下一个物体 ID"""
        obj_id = self._next_obj_id
        self._next_obj_id += 1
        return obj_id
    
    def reset(self):
        """重置检查器状态"""
        self.tracked_instruments.clear()
        self.last_reinit_time = 0
        self.frames_since_reinit = 0
        self.state = SAM3State.IDLE
    
    def check(
        self,
        current_time: float,
        surgr1_bboxes: List[Dict[str, Any]],
        sam3_masks: Optional[Dict[int, np.ndarray]] = None
    ) -> ReinitDecision:
        """
        检查是否需要重新初始化 SAM3
        
        Args:
            current_time: 当前时间戳（秒）
            surgr1_bboxes: SurgR1 检测到的 bboxes [{"x1", "y1", "x2", "y2", "label"}, ...]
            sam3_masks: SAM3 当前的 masks {obj_id: mask_array, ...}
            
        Returns:
            ReinitDecision 包含是否需要重新初始化及原因
        """
        sam3_masks = sam3_masks or {}
        
        # 检查冷却时间
        if current_time - self.last_reinit_time < self.config.reinit_cooldown:
            return ReinitDecision(need_reinit=False)
        
        # 1. 如果没有器械，切换到 IDLE
        if not surgr1_bboxes:
            if self.state != SAM3State.IDLE:
                self.state = SAM3State.IDLE
                self.tracked_instruments.clear()
                return ReinitDecision(
                    need_reinit=True,
                    reason="所有器械消失",
                    reinit_type="full"
                )
            return ReinitDecision(need_reinit=False)
        
        # 2. 如果当前是 IDLE 状态但检测到器械，需要初始化
        if self.state == SAM3State.IDLE:
            return ReinitDecision(
                need_reinit=True,
                reason="检测到新器械（从 IDLE 状态）",
                reinit_type="full"
            )
        
        # 3. 器械数量变化
        current_count = len(surgr1_bboxes)
        tracked_count = len(self.tracked_instruments)
        
        if current_count != tracked_count:
            return ReinitDecision(
                need_reinit=True,
                reason=f"器械数量变化: {tracked_count} -> {current_count}",
                reinit_type="full"
            )
        
        # 4. 器械类型变化
        current_labels = set(b.get('label', 'unknown') for b in surgr1_bboxes)
        tracked_labels = set(t.label for t in self.tracked_instruments.values())
        
        new_labels = current_labels - tracked_labels
        if new_labels:
            return ReinitDecision(
                need_reinit=True,
                reason=f"新器械类型: {new_labels}",
                reinit_type="full"
            )
        
        # 5. 定时强制刷新
        if current_time - self.last_reinit_time > self.config.forced_refresh_interval:
            return ReinitDecision(
                need_reinit=True,
                reason=f"定时刷新（{self.config.forced_refresh_interval}s）",
                reinit_type="full"
            )
        
        # 6. 帧数限制
        if self.frames_since_reinit > self.config.max_propagate_frames:
            return ReinitDecision(
                need_reinit=True,
                reason=f"超过最大传播帧数: {self.config.max_propagate_frames}",
                reinit_type="full"
            )
        
        # 7. 逐个检查一致性（如果有 mask 数据）
        if sam3_masks:
            affected_objects = []
            
            for obj_id, mask in sam3_masks.items():
                if obj_id not in self.tracked_instruments:
                    continue
                
                tracked = self.tracked_instruments[obj_id]
                
                # 找到匹配的 bbox
                matching_bbox = self._find_matching_bbox(tracked, surgr1_bboxes)
                if matching_bbox is None:
                    affected_objects.append(obj_id)
                    continue
                
                # 质心偏离检查
                if self._check_centroid_offset(mask, matching_bbox):
                    affected_objects.append(obj_id)
                    continue
                
                # 面积突变检查
                current_area = int(np.sum(mask > 0.5))
                if self._check_area_change(current_area, tracked.last_area):
                    affected_objects.append(obj_id)
                    continue
            
            if affected_objects:
                return ReinitDecision(
                    need_reinit=True,
                    reason=f"一致性检查失败: 物体 {affected_objects}",
                    reinit_type="partial" if len(affected_objects) < len(self.tracked_instruments) else "full",
                    affected_objects=affected_objects
                )
        
        return ReinitDecision(need_reinit=False)
    
    def _find_matching_bbox(
        self,
        tracked: TrackedInstrument,
        bboxes: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        找到与跟踪物体匹配的 bbox
        使用 IoU 和标签匹配
        """
        best_match = None
        best_score = 0
        
        for bbox in bboxes:
            # 计算 IoU
            iou = self._compute_iou(tracked.last_bbox, bbox)
            
            # 标签匹配奖励
            label_match = 1.0 if bbox.get('label') == tracked.label else 0.5
            
            score = iou * label_match
            
            if score > best_score and score > self.config.iou_threshold:
                best_score = score
                best_match = bbox
        
        return best_match
    
    def _compute_iou(self, bbox1: Dict, bbox2: Dict) -> float:
        """计算两个 bbox 的 IoU"""
        x1 = max(bbox1['x1'], bbox2['x1'])
        y1 = max(bbox1['y1'], bbox2['y1'])
        x2 = min(bbox1['x2'], bbox2['x2'])
        y2 = min(bbox1['y2'], bbox2['y2'])
        
        if x2 <= x1 or y2 <= y1:
            return 0.0
        
        intersection = (x2 - x1) * (y2 - y1)
        
        area1 = (bbox1['x2'] - bbox1['x1']) * (bbox1['y2'] - bbox1['y1'])
        area2 = (bbox2['x2'] - bbox2['x1']) * (bbox2['y2'] - bbox2['y1'])
        
        union = area1 + area2 - intersection
        
        return intersection / union if union > 0 else 0.0
    
    def _check_centroid_offset(self, mask: np.ndarray, bbox: Dict) -> bool:
        """
        检查 mask 质心是否偏离 bbox 中心过多
        """
        # 计算 mask 质心
        mask_binary = mask > 0.5
        if not np.any(mask_binary):
            return True  # 空 mask 视为不一致
        
        ys, xs = np.where(mask_binary)
        centroid_x = np.mean(xs)
        centroid_y = np.mean(ys)
        
        # bbox 中心
        bbox_cx = (bbox['x1'] + bbox['x2']) / 2
        bbox_cy = (bbox['y1'] + bbox['y2']) / 2
        
        # bbox 对角线长度
        diagonal = np.sqrt(
            (bbox['x2'] - bbox['x1'])**2 + 
            (bbox['y2'] - bbox['y1'])**2
        )
        
        if diagonal == 0:
            return True
        
        # 计算相对偏离
        distance = np.sqrt((centroid_x - bbox_cx)**2 + (centroid_y - bbox_cy)**2)
        relative_offset = distance / diagonal
        
        return relative_offset > self.config.centroid_offset_threshold
    
    def _check_area_change(self, current_area: int, prev_area: int) -> bool:
        """
        检查 mask 面积是否突然变化过大
        """
        if prev_area == 0:
            return False  # 第一帧不检查
        
        change_ratio = abs(current_area - prev_area) / prev_area
        
        return change_ratio > self.config.area_change_threshold
    
    def update_after_reinit(
        self,
        current_time: float,
        bboxes: List[Dict[str, Any]],
        sam3_result: Dict[str, Any]
    ):
        """
        重新初始化后更新跟踪状态
        """
        self.last_reinit_time = current_time
        self.frames_since_reinit = 0
        self.state = SAM3State.TRACKING
        
        # 重建跟踪列表
        self.tracked_instruments.clear()
        
        tracked_objects = sam3_result.get('tracked_objects', [])
        
        for i, bbox in enumerate(bboxes):
            obj_id = self.get_next_obj_id()
            
            # 尝试从 SAM3 结果获取对应信息
            sam3_obj = tracked_objects[i] if i < len(tracked_objects) else {}
            
            self.tracked_instruments[obj_id] = TrackedInstrument(
                obj_id=obj_id,
                label=bbox.get('label', 'unknown'),
                last_bbox=bbox,
                last_area=0,
                last_centroid=(
                    (bbox['x1'] + bbox['x2']) / 2,
                    (bbox['y1'] + bbox['y2']) / 2
                ),
                frames_tracked=0,
                last_update_time=current_time
            )
        
        logger.info(f"SAM3 重新初始化完成，跟踪 {len(self.tracked_instruments)} 个器械")
    
    def update_after_propagate(
        self,
        current_time: float,
        sam3_masks: Optional[Dict[int, np.ndarray]] = None
    ):
        """
        传播后更新跟踪状态
        """
        self.frames_since_reinit += 1
        
        if sam3_masks:
            for obj_id, mask in sam3_masks.items():
                if obj_id in self.tracked_instruments:
                    tracked = self.tracked_instruments[obj_id]
                    
                    # 更新面积
                    tracked.last_area = int(np.sum(mask > 0.5))
                    
                    # 更新质心
                    mask_binary = mask > 0.5
                    if np.any(mask_binary):
                        ys, xs = np.where(mask_binary)
                        tracked.last_centroid = (np.mean(xs), np.mean(ys))
                    
                    tracked.frames_tracked += 1
                    tracked.last_update_time = current_time
    
    def get_status(self) -> Dict[str, Any]:
        """获取当前状态"""
        return {
            "state": self.state.value,
            "tracked_count": len(self.tracked_instruments),
            "tracked_labels": [t.label for t in self.tracked_instruments.values()],
            "frames_since_reinit": self.frames_since_reinit,
            "last_reinit_time": self.last_reinit_time
        }


# 工具函数
def parse_bboxes_from_surgr1(tool_localization: str) -> List[Dict[str, Any]]:
    """
    从 SurgR1 的 tool_localization 输出解析 bboxes
    
    支持多种格式:
    - SurgR1 格式: "<think>...</think><answer>grasper(328,0),(466,112) hook(516,0),(853,287)</answer>"
    - 简单格式: "grasper(328,0),(466,112) hook(516,0),(853,287)"
    - 旧格式: "label: (x1, y1), (x2, y2)"
    - bbox格式: "bbox (x1,y1), (x2,y2)" 或 "tool bbox (x1,y1), (x2,y2)"
    - 通用格式: "(x1,y1), (x2,y2)" 或 "(x1, y1) (x2, y2)"
    """
    import re
    
    if not tool_localization:
        return []
    
    bboxes = []
    
    # 提取 <answer> 标签中的内容（如果存在）
    answer_match = re.search(r'<answer>(.*?)</answer>', tool_localization, re.DOTALL)
    if answer_match:
        text = answer_match.group(1).strip()
    else:
        text = tool_localization
    
    # 调试日志
    logger.debug(f"[parse_bboxes] Input: {text[:200]}...")
    
    # 格式1: SurgR1 格式 "label(x1,y1),(x2,y2)"
    # 例如: grasper(328,0),(466,112)
    pattern1 = r"(\w+)\((\d+),\s*(\d+)\),?\s*\((\d+),\s*(\d+)\)"
    matches = re.findall(pattern1, text)
    
    if matches:
        for match in matches:
            label, x1, y1, x2, y2 = match
            bboxes.append({
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "label": label
            })
        logger.debug(f"[parse_bboxes] Format1 matched: {len(bboxes)} bboxes")
        return bboxes
    
    # 格式2: "label: (x1, y1), (x2, y2)"
    pattern2 = r"(\w+):\s*\((\d+),\s*(\d+)\),?\s*\((\d+),\s*(\d+)\)"
    matches = re.findall(pattern2, text)
    
    if matches:
        for match in matches:
            label, x1, y1, x2, y2 = match
            bboxes.append({
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "label": label
            })
        logger.debug(f"[parse_bboxes] Format2 matched: {len(bboxes)} bboxes")
        return bboxes
    
    # 格式3: "label bbox (x1,y1), (x2,y2)" 或 "label: bbox (x1,y1), (x2,y2)"
    pattern3 = r"(\w+)[:\s]+bbox\s*\((\d+),\s*(\d+)\),?\s*\((\d+),\s*(\d+)\)"
    matches = re.findall(pattern3, text, re.IGNORECASE)
    
    if matches:
        for match in matches:
            label, x1, y1, x2, y2 = match
            bboxes.append({
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "label": label
            })
        logger.debug(f"[parse_bboxes] Format3 matched: {len(bboxes)} bboxes")
        return bboxes
    
    # 格式4: 无标签的 bbox "(x1,y1), (x2,y2)" 或 "(x1,y1) (x2,y2)"
    # 这种格式常见于简单的 bbox 输出
    pattern4 = r"\((\d+),\s*(\d+)\)[,\s]*\((\d+),\s*(\d+)\)"
    matches = re.findall(pattern4, text)
    
    if matches:
        for i, match in enumerate(matches):
            x1, y1, x2, y2 = match
            bboxes.append({
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "label": f"tool_{i}"  # 没有标签时使用通用名称
            })
        logger.debug(f"[parse_bboxes] Format4 matched: {len(bboxes)} bboxes")
        return bboxes
    
    # 格式5: 尝试匹配任何包含四个数字的模式
    # 例如: "100,50,300,200" 或 "x1=100, y1=50, x2=300, y2=200"
    pattern5 = r"(\d+)[,\s]+(\d+)[,\s]+(\d+)[,\s]+(\d+)"
    matches = re.findall(pattern5, text)
    
    if matches:
        for i, match in enumerate(matches):
            x1, y1, x2, y2 = match
            # 验证这是合理的 bbox（x2>x1, y2>y1）
            if int(x2) > int(x1) and int(y2) > int(y1):
                bboxes.append({
                    "x1": int(x1),
                    "y1": int(y1),
                    "x2": int(x2),
                    "y2": int(y2),
                    "label": f"tool_{i}"
                })
        if bboxes:
            logger.debug(f"[parse_bboxes] Format5 matched: {len(bboxes)} bboxes")
            return bboxes
    
    logger.warning(f"[parse_bboxes] No bboxes found in: {text[:100]}...")
    return bboxes

