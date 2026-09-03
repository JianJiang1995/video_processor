"""YOLO26s Surgical Tool Detection Service.
Replaces SurgR1 tool_localization with real-time YOLO detection.

v2: 基于 Ultralytics BoT-SORT 的时序追踪 + per-id 类别多数投票平滑。
    解决 bbox 闪烁和类别抖动（e.g. hook ↔ irrigator）。
"""
import json
import logging
from collections import Counter, deque
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# Color palette for 8 tool classes (BGR for cv2)
TOOL_COLORS = {
    "bipolar": (255, 128, 0),      # Orange
    "clipper": (0, 255, 128),      # Spring green
    "grasper": (255, 255, 0),      # Cyan
    "hook": (0, 128, 255),         # Light blue
    "irrigator": (255, 0, 255),    # Magenta
    "scissors": (0, 255, 255),     # Yellow
    "snare": (128, 0, 255),        # Purple
    "specimen_bag": (255, 0, 128), # Pink
}


class YOLOService:
    """Singleton YOLO26s tool detection service with temporal smoothing."""

    CLASSES = ["bipolar", "clipper", "grasper", "hook", "irrigator", "scissors", "snare", "specimen_bag"]

    # 每个追踪 id 保留最近 N 帧的类别投票（~1.2s at 25fps）
    CLASS_HISTORY_LEN = 30
    # 一个 id 多少帧没出现就从投票表清掉（避免内存累积）
    ID_GC_FRAMES = 60

    def __init__(self, model_path: str, device: str = "cuda:0", conf_threshold: float = 0.25):
        import torch
        from ultralytics import YOLO
        logger.info(f"[YOLO] Loading model from {model_path} on {device} (track mode)")
        self.model = YOLO(model_path)
        # Ultralytics treats a CUDA string as CUDA_VISIBLE_DEVICES and then
        # returns cuda:0. That silently collapses cuda:1 onto GPU0 when torch
        # was initialized earlier by another expert. A torch.device bypasses
        # that remapping and preserves the requested physical index.
        if isinstance(device, str) and device.isdigit():
            device = f"cuda:{device}"
        self.device = torch.device(device) if isinstance(device, str) else device
        self.conf_threshold = conf_threshold

        # 追踪状态
        self._class_votes: Dict[int, Deque[str]] = {}
        self._last_seen: Dict[int, int] = {}
        self._frame_counter = 0

        # Warmup：model.track() 首次调用要加载 bytetrack 配置 + 建 predictor + 首帧推理，
        # 冷启动约 1-3s。不 warmup 时用户第一次打开视频流会卡住（黑屏 3s）。
        try:
            dummy = np.zeros((640, 640, 3), dtype=np.uint8)
            self.model.track(dummy, persist=False, tracker="bytetrack.yaml",
                             verbose=False, conf=0.99, device=self.device)
            logger.info("[YOLO] Tracker warmed up")
        except Exception as e:
            logger.warning(f"[YOLO] Warmup noop (will cold-start on first frame): {e}")

        self._ready = True
        logger.info(f"[YOLO] Model loaded. Classes: {self.model.names}")

    @property
    def is_ready(self) -> bool:
        return self._ready

    def reset_tracker(self):
        """视频切换时清空追踪状态，避免上一个流的 id 残留。"""
        self._class_votes.clear()
        self._last_seen.clear()
        self._frame_counter = 0
        try:
            # Ultralytics 内部 tracker 也重置
            if hasattr(self.model, "predictor") and self.model.predictor is not None:
                for tr in getattr(self.model.predictor, "trackers", []) or []:
                    if hasattr(tr, "reset"):
                        tr.reset()
        except Exception as e:
            logger.debug(f"[YOLO] Tracker reset noop: {e}")

    # ---- 内部工具 -------------------------------------------------

    def _smooth_class(self, track_id: int, raw_label: str) -> str:
        """把 raw_label 写入投票历史，返回多数投票后的平滑 label。"""
        hist = self._class_votes.setdefault(track_id, deque(maxlen=self.CLASS_HISTORY_LEN))
        hist.append(raw_label)
        self._last_seen[track_id] = self._frame_counter
        winner, _ = Counter(hist).most_common(1)[0]
        return winner

    def _gc_old_ids(self):
        stale = [tid for tid, last in self._last_seen.items()
                 if self._frame_counter - last > self.ID_GC_FRAMES]
        for tid in stale:
            self._class_votes.pop(tid, None)
            self._last_seen.pop(tid, None)

    def _to_bgr(self, image: Union[np.ndarray, Image.Image]) -> np.ndarray:
        if isinstance(image, Image.Image):
            arr = np.array(image)
            if arr.ndim == 3 and arr.shape[2] == 4:
                return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
            if arr.ndim == 3 and arr.shape[2] == 3:
                return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            return arr
        return image

    # ---- 公共 API -------------------------------------------------

    def detect(self, image: Union[np.ndarray, Image.Image], conf_threshold: float = None,
               use_tracker: bool = True) -> List[Dict]:
        """Run detection (optionally with tracking) on image.

        Returns list of {x1,y1,x2,y2,label,confidence,class_id,track_id,raw_label}.
        When use_tracker=True, label 是平滑后的结果；raw_label 是本帧 YOLO 的原始预测。
        """
        conf = conf_threshold or self.conf_threshold
        img = self._to_bgr(image)

        if use_tracker:
            self._frame_counter += 1
            # persist=True 保持 id 在连续帧之间稳定
            results = self.model.track(
                img, verbose=False, conf=conf, device=self.device,
                persist=True, tracker="bytetrack.yaml",
            )
        else:
            results = self.model(img, verbose=False, conf=conf, device=self.device)

        detections: List[Dict] = []
        if results and len(results) > 0:
            boxes = results[0].boxes
            ids = getattr(boxes, "id", None)
            for i, box in enumerate(boxes):
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                cls_id = int(box.cls[0])
                conf_val = float(box.conf[0])
                raw_label = self.model.names.get(cls_id, f"class_{cls_id}")

                tid = None
                smoothed = raw_label
                if use_tracker and ids is not None and i < len(ids):
                    tid_raw = ids[i]
                    if tid_raw is not None:
                        tid = int(tid_raw.item() if hasattr(tid_raw, "item") else tid_raw)
                        smoothed = self._smooth_class(tid, raw_label)

                detections.append({
                    "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                    "label": smoothed,
                    "raw_label": raw_label,
                    "confidence": round(conf_val, 3),
                    "class_id": cls_id,
                    "track_id": tid,
                })

        if use_tracker and self._frame_counter % self.ID_GC_FRAMES == 0:
            self._gc_old_ids()

        return detections

    def detect_and_draw(self, frame: np.ndarray, conf_threshold: float = None,
                        use_tracker: bool = True) -> Tuple[np.ndarray, List[Dict]]:
        """Run detection/tracking + draw bboxes on frame (BGR).

        Label 使用时序投票后的平滑类别；右上角小字标注 id。
        """
        detections = self.detect(frame, conf_threshold, use_tracker=use_tracker)
        annotated = frame.copy()
        for det in detections:
            color = TOOL_COLORS.get(det["label"], (255, 255, 255))
            x1, y1, x2, y2 = det["x1"], det["y1"], det["x2"], det["y2"]
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            tid = det.get("track_id")
            id_str = f" #{tid}" if tid is not None else ""
            label_text = f"{det['label']}{id_str} {det['confidence']:.2f}"
            (tw, th), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(annotated, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
            cv2.putText(annotated, label_text, (x1 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
        return annotated, detections

    def detections_to_sam3_format(self, detections: List[Dict]) -> List[Dict]:
        """Convert YOLO detections to SAM3 bbox format."""
        return [{"x1": d["x1"], "y1": d["y1"], "x2": d["x2"], "y2": d["y2"], "label": d["label"]} for d in detections]

    def detections_to_surgr1_format(self, detections: List[Dict]) -> str:
        """Convert YOLO detections to SurgR1-compatible text format for backward compat."""
        parts = []
        for d in detections:
            parts.append(f"{d['label']}: ({d['x1']},{d['y1']}), ({d['x2']},{d['y2']})")
        return "; ".join(parts)


_yolo_service: Optional[YOLOService] = None

def get_yolo_service() -> Optional[YOLOService]:
    """Get or create singleton YOLO service. Returns None if disabled in config."""
    global _yolo_service
    if _yolo_service is not None:
        return _yolo_service

    try:
        import json as _json
        config_path = Path(__file__).parent.parent.parent / "config.json"
        with open(config_path) as f:
            config = _json.load(f)

        yolo_config = config.get("services", {}).get("yolo", {})
        if not yolo_config.get("enabled", False):
            logger.info("[YOLO] Service disabled in config")
            return None

        model_path = yolo_config.get("model_path", "")
        if not model_path or not Path(model_path).exists():
            logger.error(f"[YOLO] Model not found: {model_path}")
            return None

        # Env var overrides config (for runtime GPU assignment without file write)
        import os as _os
        device = _os.environ.get("YOLO_DEVICE") or yolo_config.get("device", "cuda:0")
        conf = yolo_config.get("confidence_threshold", 0.25)
        _yolo_service = YOLOService(model_path, device, conf)
        return _yolo_service
    except Exception as e:
        logger.error(f"[YOLO] Failed to initialize: {e}")
        return None
