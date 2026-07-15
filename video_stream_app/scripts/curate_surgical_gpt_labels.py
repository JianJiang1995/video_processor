#!/usr/bin/env python3
"""Curate GPT-generated surgical labels into a smaller high-precision YOLO set."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from PIL import Image, ImageDraw, ImageFont


CLASS_NAMES = ["hemolok_clip", "titanium_clip"]
LABEL_TO_ID = {name: idx for idx, name in enumerate(CLASS_NAMES)}


def _norm_label(label: Any) -> str:
    return str(label or "").strip().lower().replace("-", "_").replace(" ", "_")


def _box_stats(obj: Dict[str, Any]) -> Tuple[float, float]:
    x1, y1, x2, y2 = [float(v) for v in obj.get("box_1000", [0, 0, 0, 0])]
    bw = max(0.0, x2 - x1)
    bh = max(0.0, y2 - y1)
    area = (bw * bh) / 1_000_000.0
    elong = max(bw / max(bh, 1.0), bh / max(bw, 1.0))
    return area, elong


def _keep_clip(obj: Dict[str, Any], args: argparse.Namespace) -> bool:
    label = _norm_label(obj.get("label"))
    try:
        conf = float(obj.get("confidence", 0.0))
    except Exception:
        conf = 0.0
    reason = str(obj.get("reason", "")).lower()
    area, elong = _box_stats(obj)
    if label == "hemolok_clip":
        if conf < args.hemolok_conf or area > args.hemolok_max_area or elong > args.hemolok_max_elongation:
            return False
        if args.white_hemolok_only:
            if not re.search(r"\b(white|ivory|opaque|plastic|hem-o-lok|locking)\b", reason):
                return False
            if re.search(r"\b(blue|purple|green)\b", reason):
                return False
        return True
    if label == "titanium_clip":
        if conf < args.titanium_conf or area > args.titanium_max_area or elong > args.titanium_max_elongation:
            return False
        if not re.search(r"\b(silver|gray|grey|metal|metallic|titanium)\b", reason):
            return False
        if re.search(r"\b(shaft|jaw|applier)\b", reason) and not re.search(r"\b(short|small|deployed|clip body)\b", reason):
            return False
        return True
    return False


def _to_yolo(obj: Dict[str, Any]) -> str:
    x1, y1, x2, y2 = [float(v) for v in obj["box_1000"]]
    cx = ((x1 + x2) / 2.0) / 1000.0
    cy = ((y1 + y2) / 2.0) / 1000.0
    bw = (x2 - x1) / 1000.0
    bh = (y2 - y1) / 1000.0
    return f"{LABEL_TO_ID[_norm_label(obj['label'])]} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def _iter_annotations(sources: Iterable[Path]) -> Iterable[Tuple[Path, Dict[str, Any]]]:
    for source in sources:
        for ann_path in sorted((source / "annotations").glob("*.json")):
            try:
                yield source, json.loads(ann_path.read_text(encoding="utf-8"))
            except Exception:
                continue


def _drop_source_label(source: Path, ann: Dict[str, Any], obj: Dict[str, Any], args: argparse.Namespace) -> bool:
    label = _norm_label(obj.get("label"))
    source_text = f"{source} {ann.get('video') or ''}"
    for rule in args.drop_source_label:
        if ":" not in rule:
            continue
        rule_label, pattern = rule.split(":", 1)
        if _norm_label(rule_label) == label and re.search(pattern, source_text):
            return True
    return False


def _source_image(source: Path, ann: Dict[str, Any]) -> Path:
    split = ann.get("split") or "train"
    video = Path(str(ann.get("video", ""))).with_suffix("").as_posix().strip("/")
    safe_video = re.sub(r"[^A-Za-z0-9_.-]+", "_", video)
    stem = f"{safe_video}_{float(ann.get('time_sec', 0.0)):08.2f}s".replace(".", "p")
    return source / "images" / split / f"{stem}.jpg"


def _draw_overlay(image_path: Path, objects: List[Dict[str, Any]], output_path: Path) -> None:
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    w, h = img.size
    colors = {"hemolok_clip": (255, 80, 80), "titanium_clip": (40, 210, 255)}
    for obj in objects:
        x1, y1, x2, y2 = [float(v) for v in obj["box_1000"]]
        px1, py1 = int(x1 / 1000.0 * w), int(y1 / 1000.0 * h)
        px2, py2 = int(x2 / 1000.0 * w), int(y2 / 1000.0 * h)
        color = colors.get(_norm_label(obj.get("label")), (255, 255, 255))
        draw.rectangle([px1, py1, px2, py2], outline=color, width=3)
        text = f"{obj['label']} {float(obj.get('confidence', 0.0)):.2f}"
        bbox = draw.textbbox((px1, max(0, py1 - 22)), text, font=font)
        draw.rectangle(bbox, fill=color)
        draw.text((px1, max(0, py1 - 22)), text, fill=(0, 0, 0), font=font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=90)


def curate(args: argparse.Namespace) -> None:
    sources = [Path(p) for p in args.sources]
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

    positives: List[Tuple[Path, Dict[str, Any], List[Dict[str, Any]]]] = []
    negatives: List[Tuple[Path, Dict[str, Any]]] = []
    last_positive_time: Dict[Tuple[str, str], float] = {}
    for source, ann in _iter_annotations(sources):
        image_path = _source_image(source, ann)
        if not image_path.exists():
            continue
        kept = [
            obj
            for obj in ann.get("objects") or []
            if _keep_clip(obj, args) and not _drop_source_label(source, ann, obj, args)
        ]
        if kept:
            if args.min_positive_gap_sec > 0:
                video_key = str(ann.get("video") or image_path)
                try:
                    sec = float(ann.get("time_sec", 0.0))
                except Exception:
                    sec = 0.0
                labels = sorted({_norm_label(obj.get("label")) for obj in kept})
                if labels and all(
                    abs(sec - last_positive_time.get((video_key, label), -1_000_000.0)) < args.min_positive_gap_sec
                    for label in labels
                ):
                    continue
                for label in labels:
                    last_positive_time[(video_key, label)] = sec
            positives.append((image_path, ann, kept))
        else:
            negatives.append((image_path, ann))

    max_negatives = int(len(positives) * args.negative_ratio)
    negatives = negatives[:max_negatives]
    counts: Counter[str] = Counter()
    image_count = 0

    rows: List[Tuple[Path, Dict[str, Any], List[Dict[str, Any]]]] = positives + [
        (image_path, ann, []) for image_path, ann in negatives
    ]
    for idx, (image_path, ann, objects) in enumerate(rows, start=1):
        split = "val" if idx % args.val_every == 0 else "train"
        stem = f"{image_path.parent.parent.parent.name}_{image_path.stem}_{idx:04d}"
        out_image = output / "images" / split / f"{stem}.jpg"
        out_label = output / "labels" / split / f"{stem}.txt"
        shutil.copy2(image_path, out_image)
        out_label.write_text("\n".join(_to_yolo(obj) for obj in objects) + ("\n" if objects else ""), encoding="utf-8")
        out_ann = {
            "source_image": str(image_path),
            "source_video": ann.get("video"),
            "time_sec": ann.get("time_sec"),
            "split": split,
            "objects": objects,
        }
        (output / "annotations" / f"{stem}.json").write_text(json.dumps(out_ann, ensure_ascii=False, indent=2), encoding="utf-8")
        if objects:
            _draw_overlay(out_image, objects, output / "audit" / f"{stem}.jpg")
        for obj in objects:
            counts[_norm_label(obj.get("label"))] += 1
        image_count += 1

    summary = {
        "sources": [str(s) for s in sources],
        "images": image_count,
        "positive_images": len(positives),
        "negative_images": len(negatives),
        "objects": dict(counts),
        "white_hemolok_only": args.white_hemolok_only,
        "min_positive_gap_sec": args.min_positive_gap_sec,
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", nargs="+", required=True)
    parser.add_argument("--output", default="datasets/surgical_clips_gpt55_high_precision_v1")
    parser.add_argument("--hemolok-conf", type=float, default=0.82)
    parser.add_argument("--titanium-conf", type=float, default=0.74)
    parser.add_argument("--hemolok-max-area", type=float, default=0.05)
    parser.add_argument("--titanium-max-area", type=float, default=0.02)
    parser.add_argument("--hemolok-max-elongation", type=float, default=5.0)
    parser.add_argument("--titanium-max-elongation", type=float, default=5.0)
    parser.add_argument("--negative-ratio", type=float, default=2.0)
    parser.add_argument("--val-every", type=int, default=5)
    parser.add_argument("--min-positive-gap-sec", type=float, default=0.0)
    parser.add_argument(
        "--drop-source-label",
        action="append",
        default=[],
        help="Drop labels from matching sources, format label:regex. Can be repeated.",
    )
    parser.add_argument("--white-hemolok-only", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    curate(args)


if __name__ == "__main__":
    main()
