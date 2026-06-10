"""Triplet Expert Service (LAM-Lite)

Per-clip (10 frames) surgical action triplet recognition:
  (Instrument, Verb, Target) plus combined IVT index.
Reference: /data4/jj/proj/surg_agent/docs/SurgAgent_Expert_Results.md §3
"""
import json
import logging
import os as _os
import sys
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional, Union

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# LAM-Lite class definition lives in the surg_agent project; we import it
# instead of duplicating 150+ lines. This sys.path insertion is contained.
_SURG_AGENT_ROOT = Path(_os.environ.get("SURG_AGENT_ROOT", "/data4/jj/proj/surg_agent"))


class TripletService:
    """LAM-Lite 10-frame clip triplet recognizer."""

    NUM_FRAMES = 10
    HIDDEN_DIM = 512

    def __init__(self, model_path: str, mappings_path: str, device: str = "cuda:0"):
        import torch
        from torchvision import transforms as T

        # Make surg_agent code importable (for LAMLite definition)
        triplet_pkg = _SURG_AGENT_ROOT / "triplet_expert"
        if str(triplet_pkg) not in sys.path:
            sys.path.insert(0, str(triplet_pkg))

        from eval_lam_on_our_val import LAMLite  # noqa: E402

        logger.info(f"[Triplet] Loading LAM-Lite from {model_path} on {device}")
        ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
        self.device = torch.device(device)
        self.model = LAMLite(hidden_dim=self.HIDDEN_DIM, num_frames=self.NUM_FRAMES)
        self.model.load_state_dict(ckpt["state_dict"], strict=False)
        self.model.eval().to(self.device)

        self.transform = T.Compose([
            T.Resize((224, 224)),
            T.ToTensor(),
            T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])

        # Decode index → human-readable
        with open(mappings_path) as f:
            mapping = json.load(f)
        self.instruments: Dict[int, str] = {int(k): v for k, v in mapping["instruments"].items()}
        self.verbs: Dict[int, str] = {int(k): v for k, v in mapping["verbs"].items()}
        self.targets: Dict[int, str] = {int(k): v for k, v in mapping["targets"].items()}
        self.triplets: Dict[int, str] = {int(k): v for k, v in mapping["triplets"].items()}

        # Sliding window of past frames for clip buffering (real-time streams)
        self._buffer: Deque = deque(maxlen=self.NUM_FRAMES)
        self._ready = True
        logger.info(
            f"[Triplet] Ready. I={len(self.instruments)} V={len(self.verbs)} "
            f"T={len(self.targets)} IVT={len(self.triplets)}"
        )

    @property
    def is_ready(self) -> bool:
        return self._ready

    def _to_tensor(self, image: Union[np.ndarray, Image.Image]):
        if isinstance(image, np.ndarray):
            if image.ndim == 3 and image.shape[2] == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(image)
        image = image.convert("RGB")
        return self.transform(image)

    def recognize_clip(self, frames: List[Union[np.ndarray, Image.Image]]) -> Dict:
        """Recognize triplets on a 10-frame clip.

        If fewer than 10 frames provided, the first frame is repeated to pad.
        Returns {"instrument", "verb", "target", "triplet", top-N of each}.
        """
        import torch

        if not frames:
            return {}
        if len(frames) < self.NUM_FRAMES:
            frames = [frames[0]] * (self.NUM_FRAMES - len(frames)) + list(frames)
        elif len(frames) > self.NUM_FRAMES:
            # Uniform sample 10
            idxs = np.linspace(0, len(frames) - 1, self.NUM_FRAMES).astype(int)
            frames = [frames[i] for i in idxs]

        clip = torch.stack([self._to_tensor(f) for f in frames], dim=0)  # [10, 3, 224, 224]
        clip = clip.unsqueeze(0).to(self.device)  # [1, 10, 3, 224, 224]
        with torch.no_grad():
            out = self.model(clip)
        # Apply sigmoid (multi-label) and take top-1 per head for headline,
        # top-3 for secondary detail.
        def topk(logits, k, mapping):
            probs = torch.sigmoid(logits[0]).cpu().numpy()
            order = np.argsort(-probs)
            picks = []
            for idx in order[:k]:
                picks.append({"label": mapping.get(int(idx), f"class_{idx}"),
                              "confidence": round(float(probs[idx]), 3)})
            return picks

        return {
            "instrument": topk(out["i"], 3, self.instruments),
            "verb": topk(out["v"], 3, self.verbs),
            "target": topk(out["t"], 3, self.targets),
            "triplet": topk(out["ivt"], 5, self.triplets),
        }

    def push_frame(self, frame: Union[np.ndarray, Image.Image]) -> Optional[Dict]:
        """Streaming helper: feed one frame; returns recognition when buffer full.

        Useful when running on a live RTSP/MJPEG source where we want a sliding
        10-frame window inference without rebuffering.
        """
        self._buffer.append(frame)
        if len(self._buffer) < self.NUM_FRAMES:
            return None
        return self.recognize_clip(list(self._buffer))

    def reset_buffer(self):
        self._buffer.clear()


_triplet_service: Optional[TripletService] = None


def get_triplet_service() -> Optional[TripletService]:
    """Get or lazily create the singleton."""
    global _triplet_service
    if _triplet_service is not None:
        return _triplet_service

    try:
        config_path = Path(__file__).parent.parent.parent / "config.json"
        with open(config_path) as f:
            cfg = json.load(f)
        tcfg = cfg.get("services", {}).get("triplet", {})
        if not tcfg.get("enabled", False):
            logger.info("[Triplet] Service disabled in config")
            return None
        model_path = tcfg.get("model_path", "")
        mappings_path = tcfg.get("mappings_path", "")
        if not model_path or not Path(model_path).exists():
            logger.error(f"[Triplet] Checkpoint not found: {model_path}")
            return None
        if not mappings_path or not Path(mappings_path).exists():
            logger.error(f"[Triplet] Mappings file not found: {mappings_path}")
            return None
        device = _os.environ.get("TRIPLET_DEVICE") or tcfg.get("device", "cuda:0")
        _triplet_service = TripletService(model_path, mappings_path, device)
        return _triplet_service
    except Exception as e:
        logger.error(f"[Triplet] Initialization failed: {e}")
        return None
