#!/usr/bin/env python3
"""Merge YOLO detection datasets by copying images and labels with prefixes."""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List


DEFAULT_NAMES = ["hemolok_clip", "titanium_clip"]


def _copy_split(dataset: Path, output: Path, prefix: str, split: str, skip_negative: bool) -> Counter:
    counts: Counter[str] = Counter()
    image_dir = dataset / "images" / split
    label_dir = dataset / "labels" / split
    if not image_dir.exists():
        return counts
    for image_path in sorted(image_dir.iterdir()):
        if not image_path.is_file() or image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        stem = f"{prefix}_{image_path.stem}"
        target_image = output / "images" / split / f"{stem}{image_path.suffix.lower()}"
        label_path = label_dir / f"{image_path.stem}.txt"
        target_label = output / "labels" / split / f"{stem}.txt"
        label_text = label_path.read_text(encoding="utf-8", errors="ignore").strip() if label_path.exists() else ""
        if skip_negative and not label_text:
            counts["skipped_negative_images"] += 1
            continue
        shutil.copy2(image_path, target_image)
        if label_path.exists():
            shutil.copy2(label_path, target_label)
            if label_text:
                for line in label_text.splitlines():
                    parts = line.split()
                    if parts:
                        counts[f"class_{parts[0]}"] += 1
            else:
                counts["negative_images"] += 1
        else:
            target_label.write_text("", encoding="utf-8")
            counts["negative_images"] += 1
        counts["images"] += 1
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


def merge(args: argparse.Namespace) -> None:
    output = Path(args.output)
    if output.exists() and args.clean:
        shutil.rmtree(output)
    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)
    names = args.names or DEFAULT_NAMES
    _write_data_yaml(output, names)

    summary: Dict[str, object] = {
        "datasets": [],
        "names": names,
        "counts": {},
    }
    total: Counter[str] = Counter()
    for idx, dataset_arg in enumerate(args.dataset, start=1):
        dataset = Path(dataset_arg).resolve()
        if not dataset.exists():
            raise SystemExit(f"Missing dataset: {dataset}")
        prefix = f"d{idx:02d}_{dataset.name}"
        ds_counts: Counter[str] = Counter()
        for split in ("train", "val"):
            ds_counts.update(_copy_split(dataset, output, prefix, split, args.skip_negative))
        total.update(ds_counts)
        summary["datasets"].append({"path": str(dataset), "prefix": prefix, "counts": dict(ds_counts)})
    summary["counts"] = dict(total)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", action="append", required=True, help="YOLO dataset root; repeatable")
    parser.add_argument("--output", required=True)
    parser.add_argument("--names", nargs="*", default=DEFAULT_NAMES)
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--skip-negative", action="store_true", help="Skip images with missing or empty label files")
    args = parser.parse_args()
    merge(args)


if __name__ == "__main__":
    main()
