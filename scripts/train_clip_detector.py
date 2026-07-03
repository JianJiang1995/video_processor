#!/usr/bin/env python3
"""Train the dedicated YOLO surgical clip detector."""
from __future__ import annotations

import argparse
from pathlib import Path


def train(args: argparse.Namespace) -> Path:
    from ultralytics import YOLO

    data_yaml = Path(args.data)
    if not data_yaml.exists():
        raise SystemExit(f"Missing data yaml: {data_yaml}")

    model = YOLO(args.base_model)
    result = model.train(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=str(Path(args.project).resolve()),
        name=args.name,
        workers=args.workers,
        patience=args.patience,
        close_mosaic=max(0, min(10, args.epochs // 4)),
        cache=False,
        single_cls=args.single_cls,
        exist_ok=True,
        verbose=True,
    )
    save_dir = Path(getattr(result, "save_dir", Path(args.project) / args.name))
    best = save_dir / "weights" / "best.pt"
    print(f"[clip-detector] train save_dir={save_dir}")
    print(f"[clip-detector] best={best}")
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="datasets/clip_detector_v1/data.yaml")
    parser.add_argument("--base-model", default="models/clip_detector/pretrained/yolo11n.pt")
    parser.add_argument("--project", default="models/clip_detector")
    parser.add_argument("--name", default="yolo_clip_v1")
    parser.add_argument("--device", default="1")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--patience", type=int, default=12)
    parser.add_argument("--single-cls", action="store_true")
    args = parser.parse_args()
    train(args)


if __name__ == "__main__":
    main()
