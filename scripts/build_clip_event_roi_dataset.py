#!/usr/bin/env python3
"""Build a YOLO clip-event ROI dataset from existing clip box datasets.

This intentionally converts tiny clip-body boxes into larger event-region boxes.
For the UI/analysis flow we need reliable "clip event is present here" signals,
not pixel-perfect clip-body localization. The transform is fully automatic and
does not require manual annotation.
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


DEFAULT_NAMES = ["hemolok_clip", "titanium_clip"]


def _read_names(dataset: Path) -> List[str]:
    yaml_path = dataset / "data.yaml"
    if not yaml_path.exists():
        return DEFAULT_NAMES
    names: Dict[int, str] = {}
    for raw in yaml_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key.isdigit():
            names[int(key)] = value
    if not names:
        return DEFAULT_NAMES
    return [names[idx] for idx in sorted(names)]


def _parse_label_line(line: str) -> Tuple[int, float, float, float, float] | None:
    parts = line.split()
    if len(parts) < 5:
        return None
    try:
        cls = int(float(parts[0]))
        cx = float(parts[1])
        cy = float(parts[2])
        bw = float(parts[3])
        bh = float(parts[4])
    except ValueError:
        return None
    return cls, cx, cy, bw, bh


def _clamp_box(cx: float, cy: float, bw: float, bh: float) -> Tuple[float, float, float, float]:
    bw = max(0.001, min(1.0, bw))
    bh = max(0.001, min(1.0, bh))
    x1 = max(0.0, cx - bw / 2.0)
    y1 = max(0.0, cy - bh / 2.0)
    x2 = min(1.0, cx + bw / 2.0)
    y2 = min(1.0, cy + bh / 2.0)
    if x2 <= x1:
        x2 = min(1.0, x1 + 0.001)
    if y2 <= y1:
        y2 = min(1.0, y1 + 0.001)
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0, x2 - x1, y2 - y1


def _expand_box(
    cls: int,
    cx: float,
    cy: float,
    bw: float,
    bh: float,
    min_size_by_class: Dict[int, Tuple[float, float]],
    scale_by_class: Dict[int, float],
) -> Tuple[float, float, float, float]:
    min_w, min_h = min_size_by_class.get(cls, (0.10, 0.08))
    scale = scale_by_class.get(cls, 1.0)
    event_w = max(bw * scale, min_w)
    event_h = max(bh * scale, min_h)
    return _clamp_box(cx, cy, event_w, event_h)


def _format_label(cls: int, cx: float, cy: float, bw: float, bh: float) -> str:
    return f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def _iter_images(image_dir: Path) -> Iterable[Path]:
    if not image_dir.exists():
        return []
    return sorted(
        path
        for path in image_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )


def _parse_class_pair(text: str) -> Tuple[int, float, float]:
    parts = text.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("Expected CLASS:MIN_WIDTH:MIN_HEIGHT")
    try:
        return int(parts[0]), float(parts[1]), float(parts[2])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Invalid class/min-size value") from exc


def _parse_scale_pair(text: str) -> Tuple[int, float]:
    parts = text.split(":")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("Expected CLASS:SCALE")
    try:
        return int(parts[0]), float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Invalid class/scale value") from exc


def _copy_dataset(
    dataset: Path,
    output: Path,
    prefix: str,
    min_size_by_class: Dict[int, Tuple[float, float]],
    scale_by_class: Dict[int, float],
    keep_negative: bool,
) -> Counter:
    counts: Counter[str] = Counter()
    for split in ("train", "val"):
        image_dir = dataset / "images" / split
        label_dir = dataset / "labels" / split
        for image_path in _iter_images(image_dir):
            source_label = label_dir / f"{image_path.stem}.txt"
            raw_lines = source_label.read_text(encoding="utf-8", errors="ignore").splitlines() if source_label.exists() else []
            out_lines: List[str] = []
            for line in raw_lines:
                parsed = _parse_label_line(line)
                if parsed is None:
                    continue
                cls, cx, cy, bw, bh = parsed
                cx, cy, bw, bh = _expand_box(cls, cx, cy, bw, bh, min_size_by_class, scale_by_class)
                out_lines.append(_format_label(cls, cx, cy, bw, bh))
                counts[f"class_{cls}"] += 1
            if not out_lines and not keep_negative:
                counts["skipped_negative_images"] += 1
                continue
            stem = f"{prefix}_{image_path.stem}"
            out_image = output / "images" / split / f"{stem}{image_path.suffix.lower()}"
            out_label = output / "labels" / split / f"{stem}.txt"
            shutil.copy2(image_path, out_image)
            out_label.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
            counts["images"] += 1
            if not out_lines:
                counts["negative_images"] += 1
    return counts


def _write_data_yaml(output: Path, names: List[str]) -> None:
    (output / "data.yaml").write_text(
        "\n".join(
            [
                f"path: {output.resolve()}",
                "train: images/train",
                "val: images/val",
                "names:",
                *[f"  {idx}: {name}" for idx, name in enumerate(names)],
                "",
            ]
        ),
        encoding="utf-8",
    )


def build(args: argparse.Namespace) -> None:
    output = Path(args.output)
    if output.exists() and args.clean:
        shutil.rmtree(output)
    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)

    datasets = [Path(item).resolve() for item in args.dataset]
    for dataset in datasets:
        if not dataset.exists():
            raise SystemExit(f"Missing dataset: {dataset}")
    names = _read_names(datasets[0])
    _write_data_yaml(output, names)

    min_size_by_class = {cls: (w, h) for cls, w, h in args.class_min_size}
    scale_by_class = {cls: scale for cls, scale in args.class_scale}
    total: Counter[str] = Counter()
    summary = {
        "datasets": [],
        "names": names,
        "class_min_size": {str(cls): [w, h] for cls, (w, h) in min_size_by_class.items()},
        "class_scale": {str(cls): scale for cls, scale in scale_by_class.items()},
        "keep_negative": args.keep_negative,
    }
    for idx, dataset in enumerate(datasets, start=1):
        prefix = f"d{idx:02d}_{dataset.name}"
        counts = _copy_dataset(dataset, output, prefix, min_size_by_class, scale_by_class, args.keep_negative)
        total.update(counts)
        summary["datasets"].append({"path": str(dataset), "prefix": prefix, "counts": dict(counts)})
    summary["counts"] = dict(total)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", action="append", required=True, help="YOLO dataset root; repeatable")
    parser.add_argument("--output", required=True)
    parser.add_argument("--class-min-size", action="append", type=_parse_class_pair, default=[])
    parser.add_argument("--class-scale", action="append", type=_parse_scale_pair, default=[])
    parser.add_argument("--keep-negative", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    if not args.class_min_size:
        args.class_min_size = [(0, 0.12, 0.10), (1, 0.12, 0.08)]
    if not args.class_scale:
        args.class_scale = [(0, 1.8), (1, 2.8)]
    build(args)


if __name__ == "__main__":
    main()
