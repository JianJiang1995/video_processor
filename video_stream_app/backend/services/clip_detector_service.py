"""Dedicated deployed surgical clip detector.

This service is separate from the existing surgical-tool YOLO model. The tool
model detects instruments such as clipper/scissors; this detector targets clip
bodies that are already clamped on tissue.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

CLIP_COLORS = {
    "surgical_clip": (0, 70, 255),
    "clip": (0, 70, 255),
    "hemolok_clip": (0, 70, 255),
    "titanium_clip": (40, 190, 255),
}

CLIP_BODY_LABELS = {"clip", "surgical_clip", "hemolok_clip", "titanium_clip"}


class ClipDetectorService:
    """YOLO-based one-class detector for deployed surgical clip candidates."""

    def __init__(
        self,
        model_path: str,
        device: str = "cuda:1",
        conf_threshold: float = 0.25,
        max_area_ratio: float = 0.12,
        imgsz: int = 960,
    ):
        import torch
        from ultralytics import YOLO

        logger.info(f"[ClipDetector] Loading model from {model_path} on {device}")
        self.model = YOLO(model_path)
        if isinstance(device, str) and device.isdigit():
            device = f"cuda:{device}"
        self.device = torch.device(device) if isinstance(device, str) else device
        self.conf_threshold = float(conf_threshold)
        self.max_area_ratio = float(max_area_ratio)
        self.imgsz = int(imgsz)
        self._ready = False

        try:
            dummy = np.zeros((640, 640, 3), dtype=np.uint8)
            self.model(dummy, verbose=False, conf=0.99, device=self.device, imgsz=self.imgsz)
            logger.info("[ClipDetector] Warmed up")
        except Exception as exc:
            logger.warning(f"[ClipDetector] Warmup skipped: {exc}")

        self._ready = True
        logger.info(f"[ClipDetector] Model loaded. Classes: {self.model.names}")

    @property
    def is_ready(self) -> bool:
        return self._ready

    def _to_bgr(self, image: Union[np.ndarray, Image.Image]) -> np.ndarray:
        if isinstance(image, Image.Image):
            arr = np.array(image)
            if arr.ndim == 3 and arr.shape[2] == 4:
                return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
            if arr.ndim == 3 and arr.shape[2] == 3:
                return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            return arr
        return image

    def _keep_box(self, x1: int, y1: int, x2: int, y2: int, width: int, height: int) -> bool:
        bw = max(0, x2 - x1)
        bh = max(0, y2 - y1)
        if bw < 5 or bh < 4:
            return False
        area_ratio = (bw * bh) / float(max(1, width * height))
        if area_ratio > self.max_area_ratio:
            return False
        return True

    def _iou(self, a: Dict, b: Dict) -> float:
        ix1 = max(int(a["x1"]), int(b["x1"]))
        iy1 = max(int(a["y1"]), int(b["y1"]))
        ix2 = min(int(a["x2"]), int(b["x2"]))
        iy2 = min(int(a["y2"]), int(b["y2"]))
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        area_a = max(0, int(a["x2"]) - int(a["x1"])) * max(0, int(a["y2"]) - int(a["y1"]))
        area_b = max(0, int(b["x2"]) - int(b["x1"])) * max(0, int(b["y2"]) - int(b["y1"]))
        union = area_a + area_b - inter
        return inter / float(union) if union > 0 else 0.0

    def _dedupe(self, detections: List[Dict]) -> List[Dict]:
        kept: List[Dict] = []
        for det in sorted(detections, key=lambda d: float(d.get("confidence", 0.0)), reverse=True):
            if any(self._iou(det, existing) >= 0.35 for existing in kept):
                continue
            kept.append(det)
        return kept

    def detect(self, image: Union[np.ndarray, Image.Image], conf_threshold: float = None) -> List[Dict]:
        conf = float(conf_threshold if conf_threshold is not None else self.conf_threshold)
        frame = self._to_bgr(image)
        h, w = frame.shape[:2]
        results = self.model(frame, verbose=False, conf=conf, device=self.device, imgsz=self.imgsz)

        detections: List[Dict] = []
        if results and len(results) > 0:
            for box in results[0].boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                if not self._keep_box(x1, y1, x2, y2, w, h):
                    continue
                cls_id = int(box.cls[0])
                conf_val = float(box.conf[0])
                raw_label = str(self.model.names.get(cls_id, f"class_{cls_id}"))
                if raw_label not in CLIP_BODY_LABELS and cls_id not in {0, 1}:
                    continue
                # Downstream UI/summary should not oscillate between Hem-o-lok
                # and titanium wording; subtype is kept only for audit/debug.
                label = "surgical_clip"
                detections.append(
                    {
                        "x1": x1,
                        "y1": y1,
                        "x2": x2,
                        "y2": y2,
                        "label": label,
                        "display_label": "夹子",
                        "raw_label": raw_label,
                        "confidence": round(conf_val, 3),
                        "class_id": cls_id,
                        "area_ratio": round(((x2 - x1) * (y2 - y1)) / float(max(1, w * h)), 5),
                    }
                )
        return self._dedupe(detections)

    def detect_and_draw(
        self, frame: np.ndarray, conf_threshold: float = None
    ) -> Tuple[np.ndarray, List[Dict]]:
        detections = self.detect(frame, conf_threshold=conf_threshold)
        annotated = frame.copy()
        for det in detections:
            color = CLIP_COLORS.get(det["label"], (0, 70, 255))
            x1, y1, x2, y2 = det["x1"], det["y1"], det["x2"], det["y2"]
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            label_text = f"{det['label']} {det['confidence']:.2f}"
            (tw, th), _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(annotated, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
            cv2.putText(
                annotated,
                label_text,
                (x1 + 2, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
        return annotated, detections


_clip_detector_service: Optional[ClipDetectorService] = None


def get_clip_detector_service() -> Optional[ClipDetectorService]:
    """Get or create singleton clip detector. Returns None when disabled/missing."""
    global _clip_detector_service
    if _clip_detector_service is not None:
        return _clip_detector_service

    try:
        config_path = Path(__file__).parent.parent.parent / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        cfg = config.get("services", {}).get("clip_detector", {})
        if not cfg.get("enabled", False):
            logger.info("[ClipDetector] Service disabled in config")
            return None

        model_path = os.environ.get("CLIP_DETECTOR_MODEL") or cfg.get("model_path", "")
        if not model_path or not Path(model_path).exists():
            logger.warning(f"[ClipDetector] Model not found: {model_path}")
            return None

        device = os.environ.get("CLIP_DETECTOR_DEVICE") or cfg.get("device", "cuda:1")
        conf = float(os.environ.get("CLIP_DETECTOR_CONF") or cfg.get("confidence_threshold", 0.25))
        max_area_ratio = float(os.environ.get("CLIP_DETECTOR_MAX_AREA_RATIO") or cfg.get("max_area_ratio", 0.12))
        imgsz = int(os.environ.get("CLIP_DETECTOR_IMGSZ") or cfg.get("imgsz", 960))
        _clip_detector_service = ClipDetectorService(model_path, device, conf, max_area_ratio, imgsz)
        return _clip_detector_service
    except Exception as exc:
        logger.error(f"[ClipDetector] Failed to initialize: {exc}")
        return None
