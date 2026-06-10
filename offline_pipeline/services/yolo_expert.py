"""YOLO26s Tool Expert wrapper (offline batch-friendly).

Thin wrapper around ultralytics.YOLO that accepts a list of frame paths and
returns detections per frame. Mirrors the live-stream
`video_stream_app/backend/services/yolo_service.py` API surface where
sensible, but without BGR-draw / SAM3 plumbing (offline doesn't need overlay).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

CLASSES = ["bipolar", "clipper", "grasper", "hook",
           "irrigator", "scissors", "snare", "specimen_bag"]


class YoloExpert:
    def __init__(self, model_path: str, device: str = "cuda:0",
                 conf_threshold: float = 0.25, batch_size: int = 32):
        from ultralytics import YOLO
        logger.info("[yolo-expert] loading %s on %s", model_path, device)
        self.model = YOLO(model_path)
        self.device = device
        self.conf = conf_threshold
        self.batch_size = batch_size

    def infer_paths(self, frame_paths: List[str]) -> List[List[Dict]]:
        """Run detection on a list of frame paths.

        Returns a parallel list; each entry is the detections for that frame.
        """
        out: List[List[Dict]] = [[] for _ in frame_paths]
        for start in range(0, len(frame_paths), self.batch_size):
            batch = frame_paths[start:start + self.batch_size]
            results = self.model(batch, verbose=False, conf=self.conf, device=self.device)
            for local_i, res in enumerate(results):
                dets: List[Dict] = []
                if res.boxes is None:
                    out[start + local_i] = dets
                    continue
                for box in res.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    cls_id = int(box.cls[0])
                    label = self.model.names.get(cls_id, f"class_{cls_id}")
                    dets.append({
                        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                        "label": label,
                        "confidence": round(float(box.conf[0]), 3),
                        "class_id": cls_id,
                    })
                out[start + local_i] = dets
        return out


def load_from_config(cfg: Dict) -> Optional[YoloExpert]:
    yc = cfg.get("experts", {}).get("yolo", {})
    if not yc.get("enabled"):
        return None
    path = yc.get("model_path", "")
    if not path or not Path(path).exists():
        logger.error("[yolo-expert] weights not found: %s", path)
        return None
    return YoloExpert(path, yc.get("device", "cuda:0"),
                      yc.get("confidence_threshold", 0.25))
