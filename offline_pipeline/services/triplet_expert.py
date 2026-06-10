"""Triplet Expert — LAM-Lite 10-frame clip (instrument-verb-target).

Handoff doc §2.3. Sliding 10-frame window (default stride 10, i.e.
non-overlapping). Per-frame result is broadcast from its owning clip.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _load_label_mapping(path: str) -> List[str]:
    """Accepts either the CholecT50 class_mappings.json (preferred, has
    [tool]-[verb]-[target] strings) or a csv/txt list. Falls back to
    ivt_000..ivt_099 if nothing usable."""
    import json
    p = Path(path)
    if not p.exists():
        logger.warning("[triplet-expert] label mapping not found: %s", path)
        return [f"ivt_{i:03d}" for i in range(100)]
    if p.suffix.lower() == ".json":
        with p.open() as f:
            data = json.load(f)
        triplets = data.get("triplets", {})
        labels = [triplets.get(str(i), f"ivt_{i:03d}") for i in range(100)]
        return labels
    labels: List[str] = []
    with p.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "," in line:
                _, lab = line.split(",", 1)
            elif "\t" in line:
                _, lab = line.split("\t", 1)
            elif " " in line:
                parts = line.split(None, 1)
                lab = parts[1] if len(parts) == 2 else parts[0]
            else:
                lab = line
            labels.append(lab.strip())
    if len(labels) < 100:
        labels += [f"ivt_{i:03d}" for i in range(len(labels), 100)]
    return labels[:100]


class TripletExpert:
    def __init__(self, model_path: str, source_dir: str, label_mapping_path: str,
                 device: str = "cuda:0", clip_len: int = 10, clip_stride: int = 10,
                 batch_size: int = 8):
        import torch
        from torchvision import transforms

        if source_dir not in sys.path:
            sys.path.insert(0, source_dir)
        try:
            from eval_lam_on_our_val import LAMLite  # type: ignore
        except Exception as e:
            raise RuntimeError(
                f"Cannot import LAMLite from {source_dir}. "
                f"Check handoff doc §2.3. Underlying error: {e}"
            )

        self.device = device
        self.clip_len = clip_len
        self.clip_stride = clip_stride
        self.batch_size = batch_size
        self.ivt_labels = _load_label_mapping(label_mapping_path)

        logger.info("[triplet-expert] loading %s", model_path)
        ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
        model = LAMLite(hidden_dim=512, num_frames=clip_len)
        model.load_state_dict(ckpt.get("state_dict", ckpt), strict=False)
        self.model = model.eval().to(device)

        self.transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406],
                                 [0.229, 0.224, 0.225]),
        ])
        self._torch = torch

    def _load_clip(self, frame_paths: List[str]):
        from PIL import Image
        torch = self._torch
        assert len(frame_paths) == self.clip_len
        tensors = [self.transform(Image.open(p).convert("RGB")) for p in frame_paths]
        return torch.stack(tensors)  # [T, 3, 224, 224]

    def infer_paths(self, frame_paths: List[str],
                    topk: int = 3):
        """Run over a flat list of frame paths with sliding clips.

        Returns (per_frame_topk, per_frame_probs):
          - per_frame_topk: List[List[Dict]], each [{ivt_label, confidence}, ...]
          - per_frame_probs: np.ndarray [N, 100] full sigmoid probs

        Frames inside the same clip receive identical predictions; frames
        beyond the last full clip are filled with the previous clip's result.
        """
        import torch
        n = len(frame_paths)
        per_frame: List[List[Dict]] = [[] for _ in range(n)]
        full_probs = np.zeros((n, 100), dtype=np.float32)

        starts = list(range(0, max(n - self.clip_len + 1, 1), self.clip_stride))
        if n < self.clip_len:
            return per_frame, full_probs

        last_pred: List[Dict] = []
        last_probs: Optional[np.ndarray] = None
        for bstart in range(0, len(starts), self.batch_size):
            batch_starts = starts[bstart:bstart + self.batch_size]
            clips = torch.stack([
                self._load_clip(frame_paths[s:s + self.clip_len]) for s in batch_starts
            ]).to(self.device, non_blocking=True)

            with torch.no_grad():
                out = self.model(clips)
            ivt_logits = out["ivt"] if isinstance(out, dict) else out
            # LAM-Lite is a multi-label head (eval uses sigmoid, see
            # triplet_expert/eval_lam_on_our_val.py line 307).
            ivt_probs = torch.sigmoid(ivt_logits).cpu().numpy()  # [B, 100]

            for i, s in enumerate(batch_starts):
                probs = ivt_probs[i]
                top_idx = probs.argsort()[::-1][:topk]
                preds = [
                    {"ivt_label": self.ivt_labels[j], "confidence": float(probs[j])}
                    for j in top_idx
                ]
                last_pred = preds
                last_probs = probs
                end = min(s + self.clip_stride, n)
                for fi in range(s, end):
                    per_frame[fi] = preds
                    full_probs[fi] = probs

        if last_pred:
            for fi in range(n):
                if not per_frame[fi]:
                    per_frame[fi] = last_pred
                    if last_probs is not None:
                        full_probs[fi] = last_probs
        return per_frame, full_probs


def load_from_config(cfg: Dict) -> Optional[TripletExpert]:
    tc = cfg.get("experts", {}).get("triplet", {})
    if not tc.get("enabled"):
        return None
    path = tc.get("model_path", "")
    if not path or not Path(path).exists():
        logger.error("[triplet-expert] weights not found: %s", path)
        return None
    return TripletExpert(
        model_path=path,
        source_dir=tc["source_dir"],
        label_mapping_path=tc.get("label_mapping", ""),
        device=tc.get("device", "cuda:0"),
        clip_len=tc.get("clip_len", 10),
        clip_stride=tc.get("clip_stride", 10),
    )
