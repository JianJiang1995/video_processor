#!/usr/bin/env python3
"""Compare old vs new clip YOLO weights on verified hard frames.

Positives/negatives come from datasets/clip_binary_hard_v1/images (manually
verified) plus optional extra negative frames. Reports per-image max
confidence and simple operating-point stats.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ultralytics import YOLO


def evaluate(weights: str, images: list[tuple[Path, str]], device: str, conf: float, imgsz: int):
    model = YOLO(weights)
    rows = []
    for img, expected in images:
        r = model.predict(str(img), conf=conf, imgsz=imgsz, device=device, verbose=False)[0]
        boxes = r.boxes
        maxconf = max((float(b.conf) for b in boxes), default=0.0) if boxes is not None else 0.0
        rows.append({"image": img.name, "expected": expected,
                     "max_conf": round(maxconf, 3),
                     "detections": 0 if boxes is None else len(boxes)})
    return rows


def summarize(rows, thr):
    pos = [r for r in rows if r["expected"] == "clip"]
    neg = [r for r in rows if r["expected"] == "no_clip"]
    tp = sum(1 for r in pos if r["max_conf"] >= thr)
    fp = sum(1 for r in neg if r["max_conf"] >= thr)
    return {"thr": thr, "pos_hit": f"{tp}/{len(pos)}", "neg_fire": f"{fp}/{len(neg)}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default="models/clip_detector/yolo_clip_reviewed_seed_plus_gptimage2_imagebg_100_v1/weights/best.pt")
    ap.add_argument("--new", default="models/clip_detector/yolo_clip_corrected_v2/weights/best.pt")
    ap.add_argument("--hard-dir", default="datasets/clip_binary_hard_v1/images")
    ap.add_argument("--extra-neg-dir", default="")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--conf", type=float, default=0.01)
    ap.add_argument("--imgsz", type=int, default=1280)
    args = ap.parse_args()

    images = []
    for img in sorted(Path(args.hard_dir).glob("*.jpg")):
        images.append((img, "clip" if img.name.startswith("pos_") else "no_clip"))
    if args.extra_neg_dir:
        for img in sorted(Path(args.extra_neg_dir).glob("*.jpg")):
            images.append((img, "no_clip"))

    out = {}
    for tag, weights in (("old", args.old), ("new", args.new)):
        rows = evaluate(weights, images, args.device, args.conf, args.imgsz)
        out[tag] = rows
        print(f"== {tag}: {weights}")
        for r in rows:
            print(f"  {r['image']:34s} {r['expected']:8s} maxconf={r['max_conf']:.3f} n={r['detections']}")
        for thr in (0.05, 0.10, 0.20, 0.30):
            print(" ", summarize(rows, thr))

    Path("runs/clip_yolo_binary_benchmark").mkdir(parents=True, exist_ok=True)
    Path("runs/clip_yolo_binary_benchmark/compare_old_new.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
