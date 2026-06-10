"""Phase Expert Service (ResNet-18 + timm)

Replaces SurgR1 surgical_phase for real-time per-frame phase classification.
Reference: /data4/jj/proj/surg_agent/docs/SurgAgent_Expert_Results.md §2
"""
import logging
import os as _os
from pathlib import Path
from typing import Dict, List, Optional, Union

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


class PhaseService:
    """Singleton per-frame phase classifier."""

    def __init__(self, model_path: str, device: str = "cuda:0"):
        import timm
        import torch
        from torchvision import transforms as T

        logger.info(f"[Phase] Loading checkpoint from {model_path} on {device}")
        ckpt = torch.load(model_path, map_location="cpu", weights_only=False)

        self.device = torch.device(device)
        # checkpoint 里的 classes 可能是 dict({idx: name}) 或 list；统一成按 idx 排序的 list
        raw_classes = ckpt["classes"]
        if isinstance(raw_classes, dict):
            self.classes: List[str] = [raw_classes[i] for i in sorted(raw_classes.keys())]
        else:
            self.classes = list(raw_classes)
        self.model = timm.create_model(
            ckpt["model_name"],
            pretrained=False,
            num_classes=ckpt["num_classes"],
        )
        self.model.load_state_dict(ckpt["model_state_dict"])
        self.model.eval().to(self.device)

        self.transform = T.Compose([
            T.Resize((224, 224)),
            T.ToTensor(),
            T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])
        self._ready = True
        logger.info(f"[Phase] Ready. {ckpt['num_classes']} classes: {self.classes}")

    @property
    def is_ready(self) -> bool:
        return self._ready

    def classify(self, image: Union[np.ndarray, Image.Image]) -> Dict:
        """Run phase classification on a single frame.

        Returns {"label": str, "confidence": float, "probs": {class: prob}}.
        """
        import torch

        if isinstance(image, np.ndarray):
            if image.ndim == 3 and image.shape[2] == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(image)
        image = image.convert("RGB")
        x = self.transform(image).unsqueeze(0).to(self.device)
        with torch.no_grad():
            logits = self.model(x)
            probs = torch.softmax(logits, dim=1)[0]
        top_idx = int(probs.argmax().item())
        top_conf = float(probs[top_idx].item())
        return {
            "label": self.classes[top_idx],
            "confidence": round(top_conf, 3),
            "probs": {c: round(float(p), 3) for c, p in zip(self.classes, probs.tolist())},
        }

    def classify_batch(self, images: List[Union[np.ndarray, Image.Image]]) -> List[Dict]:
        """Majority-vote-friendly: classify multiple frames, return list."""
        return [self.classify(im) for im in images]

    def smooth_window(self, results: List[Dict]) -> Dict:
        """Simple majority vote across a window's classifications.

        Returns dominant label + its mean confidence over the window.
        """
        if not results:
            return {"label": "", "confidence": 0.0, "frame_count": 0}
        from collections import Counter
        labels = [r["label"] for r in results]
        winner, _ = Counter(labels).most_common(1)[0]
        conf = float(np.mean([r["confidence"] for r in results if r["label"] == winner]))
        return {
            "label": winner,
            "confidence": round(conf, 3),
            "frame_count": len(results),
            "per_frame": labels,
        }


_phase_service: Optional[PhaseService] = None


def get_phase_service() -> Optional[PhaseService]:
    """Get or lazily create the singleton. Returns None if disabled/missing."""
    global _phase_service
    if _phase_service is not None:
        return _phase_service

    try:
        import json as _json
        config_path = Path(__file__).parent.parent.parent / "config.json"
        with open(config_path) as f:
            cfg = _json.load(f)
        phase_cfg = cfg.get("services", {}).get("phase", {})
        if not phase_cfg.get("enabled", False):
            logger.info("[Phase] Service disabled in config")
            return None
        model_path = phase_cfg.get("model_path", "")
        if not model_path or not Path(model_path).exists():
            logger.error(f"[Phase] Checkpoint not found: {model_path}")
            return None
        device = _os.environ.get("PHASE_DEVICE") or phase_cfg.get("device", "cuda:0")
        _phase_service = PhaseService(model_path, device)
        return _phase_service
    except Exception as e:
        logger.error(f"[Phase] Initialization failed: {e}")
        return None
