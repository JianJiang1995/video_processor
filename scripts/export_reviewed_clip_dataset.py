#!/usr/bin/env python3
"""Export reviewed temporal clip candidates into a YOLO seed dataset."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import cv2
from PIL import Image, ImageDraw, ImageFont


CLASS_NAMES = ["hemolok_clip", "titanium_clip"]
LABEL_TO_ID = {name: idx for idx, name in enumerate(CLASS_NAMES)}


def _safe_stem(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")[:180]


def _read_review_rows(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                cid = row.get("candidate_id")
                if cid in seen:
                    continue
                seen.add(cid)
                rows.append(row)
    return rows


def _read_frame(video_path: Path, sec: float) -> Image.Image | None:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_idx = int(round(max(0.0, sec) * fps)) if fps > 0 else 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_idx))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return None
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def _to_yolo(box_1000: List[float], label: str) -> str:
    x1, y1, x2, y2 = [float(v) for v in box_1000]
    cx = ((x1 + x2) / 2.0) / 1000.0
    cy = ((y1 + y2) / 2.0) / 1000.0
    bw = (x2 - x1) / 1000.0
    bh = (y2 - y1) / 1000.0
    return f"{LABEL_TO_ID[label]} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def _draw_overlay(image: Image.Image, box_1000: List[float], label: str, output_path: Path) -> None:
    img = image.copy()
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    color = (255, 90, 90) if label == "hemolok_clip" else (40, 210, 255)
    w, h = img.size
    x1, y1, x2, y2 = [float(v) for v in box_1000]
    xy = [x1 / 1000.0 * w, y1 / 1000.0 * h, x2 / 1000.0 * w, y2 / 1000.0 * h]
    draw.rectangle(xy, outline=color, width=4)
    bbox = draw.textbbox((int(xy[0]), max(0, int(xy[1]) - 24)), label, font=font)
    draw.rectangle(bbox, fill=color)
    draw.text((bbox[0], bbox[1]), label, fill=(0, 0, 0), font=font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=92)


def export(args: argparse.Namespace) -> None:
    output = Path(args.output)
    if output.exists() and args.clean:
        shutil.rmtree(output)
    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)
    (output / "annotations").mkdir(parents=True, exist_ok=True)
    (output / "audit").mkdir(parents=True, exist_ok=True)
    (output / "data.yaml").write_text(
        "\n".join(
            [
                f"path: {output.resolve()}",
                "train: images/train",
                "val: images/val",
                "names:",
                *[f"  {idx}: {name}" for idx, name in enumerate(CLASS_NAMES)],
                "",
            ]
        ),
        encoding="utf-8",
    )

    rows = _read_review_rows([Path(p) for p in args.review_jsonl])
    positives = []
    hard_negatives = []
    for row in rows:
        review = row.get("review") or {}
        cand = row.get("candidate") or {}
        label = str(review.get("training_label") or "")
        if label in LABEL_TO_ID and bool(review.get("use_for_training")) and float(review.get("confidence", 0.0)) >= args.min_review_conf:
            positives.append(row)
        elif label == "reject" and args.include_hard_negatives:
            category = str(review.get("visual_category") or "")
            if category in {"specular_highlight", "clip_applier_or_instrument", "tissue_or_blood_or_fat", "uncertain"}:
                hard_negatives.append(row)

    hard_negatives = hard_negatives[: int(len(positives) * args.negative_ratio)]
    export_rows = positives + hard_negatives
    counts: Counter[str] = Counter()
    negative_count = 0
    for idx, row in enumerate(export_rows, start=1):
        review = row["review"]
        cand = row["candidate"]
        label = str(review.get("training_label"))
        video = Path(str(cand.get("video")))
        sec = float(cand.get("time_sec", 0.0))
        image = _read_frame(video, sec)
        if image is None:
            continue
        split = "val" if idx % args.val_every == 0 else "train"
        stem = _safe_stem(f"{label}_{video.stem}_{sec:.2f}_{idx:04d}")
        image_path = output / "images" / split / f"{stem}.jpg"
        label_path = output / "labels" / split / f"{stem}.txt"
        image.save(image_path, quality=92)
        objects = []
        if label in LABEL_TO_ID:
            box = cand["box_1000"]
            label_path.write_text(_to_yolo(box, label) + "\n", encoding="utf-8")
            _draw_overlay(image, box, label, output / "audit" / f"{stem}.jpg")
            counts[label] += 1
            objects.append({"label": label, "box_1000": box, "review": review})
        else:
            label_path.write_text("", encoding="utf-8")
            negative_count += 1
        ann = {
            "candidate_id": row.get("candidate_id"),
            "source_video": str(video),
            "time_sec": sec,
            "split": split,
            "objects": objects,
            "review": review,
        }
        (output / "annotations" / f"{stem}.json").write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "review_jsonl": args.review_jsonl,
        "images": sum(counts.values()) + negative_count,
        "positive_images": sum(counts.values()),
        "negative_images": negative_count,
        "objects": dict(counts),
        "min_review_conf": args.min_review_conf,
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-jsonl", nargs="+", required=True)
    parser.add_argument("--output", default="datasets/clip_detector_reviewed_seed_v1")
    parser.add_argument("--min-review-conf", type=float, default=0.72)
    parser.add_argument("--include-hard-negatives", action="store_true")
    parser.add_argument("--negative-ratio", type=float, default=2.0)
    parser.add_argument("--val-every", type=int, default=5)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    export(args)


if __name__ == "__main__":
    main()
