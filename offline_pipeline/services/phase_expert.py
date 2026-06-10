"""Phase Expert — ResNet-18 (timm) + non-causal Gaussian smoothing.

Offline wins ~1.4pp over the causal real-time version by using a symmetric
Gaussian kernel (σ=3). Handoff doc §2.2.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class PhaseExpert:
    def __init__(self, model_path: str, device: str = "cuda:0",
                 sigma: float = 3.0, batch_size: int = 64):
        import torch
        import timm
        from torchvision import transforms

        self.device = device
        self.sigma = sigma
        self.batch_size = batch_size

        logger.info("[phase-expert] loading %s", model_path)
        ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
        self.classes: List[str] = ckpt["classes"]
        self.num_classes = ckpt["num_classes"]

        model = timm.create_model(
            ckpt["model_name"], pretrained=False, num_classes=self.num_classes
        )
        model.load_state_dict(ckpt["model_state_dict"])
        self.model = model.eval().to(device)

        self.transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406],
                                 [0.229, 0.224, 0.225]),
        ])
        self._torch = torch

    def infer_paths(self, frame_paths: List[str]) -> np.ndarray:
        """Returns phase probabilities array shape [N, num_classes]."""
        from PIL import Image
        torch = self._torch

        probs = np.zeros((len(frame_paths), self.num_classes), dtype=np.float32)
        for start in range(0, len(frame_paths), self.batch_size):
            batch_paths = frame_paths[start:start + self.batch_size]
            tensors = []
            for p in batch_paths:
                img = Image.open(p).convert("RGB")
                tensors.append(self.transform(img))
            batch = torch.stack(tensors).to(self.device, non_blocking=True)
            with torch.no_grad():
                logits = self.model(batch)
                p = torch.softmax(logits, dim=1).cpu().numpy()
            probs[start:start + len(batch_paths)] = p
        return probs

    def smooth_and_decode(self, probs: np.ndarray) -> Tuple[np.ndarray, List[str]]:
        """Non-causal Gaussian σ smoothing + argmax. Returns (smoothed_probs, labels)."""
        from scipy.ndimage import gaussian_filter1d
        smoothed = gaussian_filter1d(probs, sigma=self.sigma, axis=0, mode="reflect")
        idx = smoothed.argmax(1)
        labels = [self.classes[i] for i in idx]
        return smoothed, labels


def load_from_config(cfg: Dict) -> Optional[PhaseExpert]:
    pc = cfg.get("experts", {}).get("phase", {})
    if not pc.get("enabled"):
        return None
    path = pc.get("model_path", "")
    if not path or not Path(path).exists():
        logger.error("[phase-expert] weights not found: %s", path)
        return None
    return PhaseExpert(path, pc.get("device", "cuda:0"),
                      pc.get("gaussian_sigma", 3.0))
