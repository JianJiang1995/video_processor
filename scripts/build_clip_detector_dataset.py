#!/usr/bin/env python3
"""Build a YOLO dataset for deployed surgical clip detection.

This bootstraps labels from LocateAnything-3B. The resulting dataset is a
one-class detector (`surgical_clip`) intended to find deployed clips clamped on
tissue. It deliberately does not hard-code timestamps and does not use file
names as labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import cv2
import numpy as np
import torch
from PIL import Image


DEFAULT_MODEL_DIR = Path("/home/user/models/LocateAnything-3B")
DEFAULT_DEPS_DIR = Path("/home/user/models/locateanything_pydeps")
DEFAULT_VIDEO_GLOB = "/home/user/proj/video_processor/test_data/夹子视频/*/*.mp4"

PROMPTS: Sequence[Tuple[str, str]] = (
    ("deployed_clip", "Locate all small deployed surgical clips clamped on tissue."),
    ("metal_clip", "Locate all small thin metal ligating clips clamped on tissue."),
    ("polymer_clip", "Locate all small thick polymer locking clips clamped on tissue."),
)


@dataclass
class Candidate:
    x1: float
    y1: float
    x2: float
    y2: float
    source: str

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)

    def to_yolo(self, w: int, h: int) -> str:
        cx = (self.x1 + self.x2) / 2.0 / w
        cy = (self.y1 + self.y2) / 2.0 / h
        bw = (self.x2 - self.x1) / w
        bh = (self.y2 - self.y1) / h
        return f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"

    def as_dict(self) -> Dict[str, float | str]:
        return {
            "x1": round(self.x1, 1),
            "y1": round(self.y1, 1),
            "x2": round(self.x2, 1),
            "y2": round(self.y2, 1),
            "source": self.source,
        }


def _insert_deps(deps_dir: Path) -> None:
    if deps_dir.exists():
        sys.path.insert(0, str(deps_dir))


def _load_locateanything(model_dir: Path, deps_dir: Path):
    _insert_deps(deps_dir)
    from transformers import AutoModel, AutoProcessor, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        str(model_dir), trust_remote_code=True, fix_mistral_regex=True
    )
    processor = AutoProcessor.from_pretrained(
        str(model_dir), trust_remote_code=True, fix_mistral_regex=True
    )
    model = AutoModel.from_pretrained(
        str(model_dir),
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
        attn_implementation="sdpa",
    ).to("cuda").eval()
    return tokenizer, processor, model


def _parse_boxes(answer: str, width: int, height: int, source: str) -> List[Candidate]:
    candidates: List[Candidate] = []
    for match in re.finditer(r"<box><(\d+)><(\d+)><(\d+)><(\d+)></box>", answer or ""):
        vals = [int(group) for group in match.groups()]
        x1, y1, x2, y2 = vals
        px1, px2 = sorted((x1 / 1000.0 * width, x2 / 1000.0 * width))
        py1, py2 = sorted((y1 / 1000.0 * height, y2 / 1000.0 * height))
        candidates.append(Candidate(px1, py1, px2, py2, source))
    return candidates


def _filter_candidate(c: Candidate, width: int, height: int) -> bool:
    bw = c.x2 - c.x1
    bh = c.y2 - c.y1
    if bw < 6 or bh < 5:
        return False
    area_ratio = c.area / float(width * height)
    if area_ratio < 0.00005 or area_ratio > 0.10:
        return False
    if bw / width > 0.48 or bh / height > 0.45:
        return False

    # LocateAnything often confuses a white vertical instrument entering from
    # the top border with a white polymer clip. Keep this as a pseudo-label
    # filter only; it is not used in runtime inference.
    if c.source == "polymer_clip" and c.y1 <= height * 0.02 and bh > height * 0.18:
        return False
    if c.y1 <= 1 and bh > height * 0.28:
        return False
    return True


def _iou(a: Candidate, b: Candidate) -> float:
    ix1 = max(a.x1, b.x1)
    iy1 = max(a.y1, b.y1)
    ix2 = min(a.x2, b.x2)
    iy2 = min(a.y2, b.y2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def _merge_candidates(candidates: List[Candidate], width: int, height: int) -> List[Candidate]:
    filtered = [c for c in candidates if _filter_candidate(c, width, height)]
    filtered.sort(key=lambda c: c.area, reverse=True)
    merged: List[Candidate] = []
    for cand in filtered:
        placed = False
        for idx, existing in enumerate(merged):
            if _iou(cand, existing) >= 0.35:
                merged[idx] = Candidate(
                    min(existing.x1, cand.x1),
                    min(existing.y1, cand.y1),
                    max(existing.x2, cand.x2),
                    max(existing.y2, cand.y2),
                    existing.source if existing.source == cand.source else f"{existing.source}+{cand.source}",
                )
                placed = True
                break
        if not placed:
            merged.append(cand)
    return sorted(merged, key=lambda c: (c.y1, c.x1))


def _infer_frame(model, processor, tokenizer, image: Image.Image, prompt: str) -> str:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    text = processor.py_apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    images, videos = processor.process_vision_info(messages)
    inputs = processor(text=[text], images=images, videos=videos, return_tensors="pt").to("cuda")
    with torch.inference_mode():
        response = model.generate(
            pixel_values=inputs["pixel_values"].to(torch.bfloat16),
            input_ids=inputs["input_ids"],
            attention_mask=inputs["attention_mask"],
            image_grid_hws=inputs.get("image_grid_hws"),
            tokenizer=tokenizer,
            max_new_tokens=256,
            use_cache=True,
            generation_mode="hybrid",
            temperature=0.1,
            do_sample=False,
            top_p=0.9,
            repetition_penalty=1.1,
            verbose=False,
        )
    if isinstance(response, tuple):
        response = response[0]
    if isinstance(response, list):
        response = response[0]
    return str(response)


def _sample_timestamps(video_path: Path, sample_seconds: float, max_frames: int) -> List[float]:
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
    cap.release()
    duration = total / fps if fps > 0 else 0.0
    if duration <= 0:
        return []
    ts = list(np.arange(0, duration, sample_seconds, dtype=float))
    if max_frames > 0 and len(ts) > max_frames:
        idx = np.linspace(0, len(ts) - 1, max_frames).round().astype(int)
        ts = [ts[i] for i in sorted(set(idx.tolist()))]
    return [float(t) for t in ts]


def _read_frame_at(video_path: Path, timestamp: float) -> np.ndarray | None:
    cap = cv2.VideoCapture(str(video_path))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, timestamp * 1000.0))
    ok, frame = cap.read()
    cap.release()
    return frame if ok else None


def _split_for(video_path: Path, timestamp: float, val_ratio: float) -> str:
    key = f"{video_path.as_posix()}:{timestamp:.3f}".encode("utf-8")
    value = int(hashlib.sha1(key).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "val" if value < val_ratio else "train"


def _write_data_yaml(output: Path) -> None:
    data = "\n".join(
        [
            f"path: {output.resolve()}",
            "train: images/train",
            "val: images/val",
            "names:",
            "  0: surgical_clip",
            "",
        ]
    )
    (output / "data.yaml").write_text(data, encoding="utf-8")


def _draw_debug(frame_bgr: np.ndarray, boxes: Sequence[Candidate], path: Path) -> None:
    vis = frame_bgr.copy()
    for box in boxes:
        cv2.rectangle(
            vis,
            (int(box.x1), int(box.y1)),
            (int(box.x2), int(box.y2)),
            (0, 220, 255),
            2,
        )
        cv2.putText(
            vis,
            "clip",
            (int(box.x1), max(0, int(box.y1) - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 220, 255),
            1,
            cv2.LINE_AA,
        )
    cv2.imwrite(str(path), vis)


def _iter_videos(args: argparse.Namespace) -> List[Path]:
    videos: List[Path] = []
    for item in args.video or []:
        videos.append(Path(item))
    for pattern in args.video_glob or []:
        videos.extend(Path(p) for p in sorted(Path().glob(pattern) if not pattern.startswith("/") else []))
    if args.video_glob:
        import glob
        for pattern in args.video_glob:
            videos.extend(Path(p) for p in glob.glob(pattern))
    if not videos:
        import glob
        videos = [Path(p) for p in glob.glob(DEFAULT_VIDEO_GLOB)]
    unique = sorted({p.resolve() for p in videos if p.exists()})
    if not unique:
        raise SystemExit("No videos found.")
    return unique


def build_dataset(args: argparse.Namespace) -> None:
    output = Path(args.output)
    if output.exists() and args.clean:
        shutil.rmtree(output)
    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)
    (output / "debug").mkdir(parents=True, exist_ok=True)
    _write_data_yaml(output)

    if args.gpu is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu)

    tokenizer, processor, model = _load_locateanything(Path(args.model_dir), Path(args.deps_dir))
    videos = _iter_videos(args)

    metadata_path = output / "metadata.jsonl"
    total_images = 0
    total_boxes = 0
    t0 = time.time()
    with metadata_path.open("w", encoding="utf-8") as meta:
        for video_path in videos:
            timestamps = _sample_timestamps(video_path, args.sample_seconds, args.max_frames_per_video)
            print(f"[dataset] {video_path} -> {len(timestamps)} samples", flush=True)
            for timestamp in timestamps:
                frame = _read_frame_at(video_path, timestamp)
                if frame is None:
                    continue
                h, w = frame.shape[:2]
                image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                raw_by_prompt: Dict[str, str] = {}
                candidates: List[Candidate] = []
                for source, prompt in PROMPTS:
                    raw = _infer_frame(model, processor, tokenizer, image, prompt)
                    raw_by_prompt[source] = raw
                    candidates.extend(_parse_boxes(raw, w, h, source))
                boxes = _merge_candidates(candidates, w, h)

                stem = f"{video_path.parent.name}_{video_path.stem}_{timestamp:07.2f}s".replace(".", "p")
                split = _split_for(video_path, timestamp, args.val_ratio)
                image_path = output / "images" / split / f"{stem}.jpg"
                label_path = output / "labels" / split / f"{stem}.txt"
                cv2.imwrite(str(image_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(args.jpeg_quality)])
                label_path.write_text("\n".join(b.to_yolo(w, h) for b in boxes) + ("\n" if boxes else ""), encoding="utf-8")
                if boxes and total_images % max(1, args.debug_every) == 0:
                    _draw_debug(frame, boxes, output / "debug" / f"{stem}.jpg")

                row = {
                    "video": str(video_path),
                    "timestamp": round(float(timestamp), 3),
                    "split": split,
                    "image": str(image_path),
                    "label": str(label_path),
                    "width": w,
                    "height": h,
                    "boxes": [b.as_dict() for b in boxes],
                    "raw": raw_by_prompt,
                }
                meta.write(json.dumps(row, ensure_ascii=False) + "\n")
                total_images += 1
                total_boxes += len(boxes)
                print(
                    f"[dataset] {stem} split={split} boxes={len(boxes)} elapsed={time.time() - t0:.1f}s",
                    flush=True,
                )

    print(f"[dataset] wrote {total_images} images, {total_boxes} boxes -> {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", action="append", help="Video path; may be repeated")
    parser.add_argument("--video-glob", action="append", help="Glob for input videos")
    parser.add_argument("--output", default="datasets/clip_detector_v1")
    parser.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    parser.add_argument("--deps-dir", default=str(DEFAULT_DEPS_DIR))
    parser.add_argument("--gpu", default="1", help="CUDA_VISIBLE_DEVICES value for LocateAnything")
    parser.add_argument("--sample-seconds", type=float, default=3.0)
    parser.add_argument("--max-frames-per-video", type=int, default=70)
    parser.add_argument("--val-ratio", type=float, default=0.18)
    parser.add_argument("--jpeg-quality", type=int, default=92)
    parser.add_argument("--debug-every", type=int, default=8)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    build_dataset(args)


if __name__ == "__main__":
    main()
