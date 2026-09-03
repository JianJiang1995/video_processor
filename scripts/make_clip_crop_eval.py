#!/usr/bin/env python3
"""Build a crop-mode eval set: run the production clip YOLO on eval images and
save padded crops around its detections. Mirrors the production candidate flow
(detector box -> crop -> VLM confirm), so VLM benchmark results on these crops
predict pipeline behavior with a crop-based clip_vlm_review.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image
from ultralytics import YOLO


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="models/clip_detector/yolo_clip_reviewed_seed_plus_gptimage2_imagebg_100_v1/weights/best.pt")
    ap.add_argument("--data", default="datasets/clip_detector_reviewed_seed_v1")
    ap.add_argument("--hard-dir", default="datasets/clip_binary_hard_v1/images")
    ap.add_argument("--out-dir", default="datasets/clip_binary_hard_v1/crops")
    ap.add_argument("--conf", type=float, default=0.05)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--pad", type=float, default=2.5)
    ap.add_argument("--top", type=int, default=1)
    ap.add_argument("--device", default="cuda:0")
    args = ap.parse_args()

    images = []
    data = Path(args.data)
    for split in ("train", "val"):
        for img in sorted((data / "images" / split).glob("*.jpg")):
            if img.name.startswith(("hemolok_clip_", "titanium_clip_")):
                images.append((img, "pos"))
            elif img.name.startswith("reject_"):
                images.append((img, "neg"))
    for img in sorted(Path(args.hard_dir).glob("*.jpg")):
        images.append((img, "pos" if img.name.startswith("pos_") else "neg"))

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    model = YOLO(args.weights)

    n_crops = 0
    fired = {"pos": 0, "neg": 0}
    for img_path, kind in images:
        res = model.predict(str(img_path), conf=args.conf, imgsz=args.imgsz,
                            device=args.device, verbose=False)[0]
        if res.boxes is None or len(res.boxes) == 0:
            continue
        fired[kind] += 1
        boxes = sorted(res.boxes, key=lambda b: -float(b.conf))[: args.top]
        im = Image.open(img_path).convert("RGB")
        W, H = im.size
        for bi, b in enumerate(boxes):
            x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            w, h = (x2 - x1) * args.pad, (y2 - y1) * args.pad
            side = max(w, h, 96)
            l = max(0, int(cx - side / 2)); t = max(0, int(cy - side / 2))
            r = min(W, int(cx + side / 2)); btm = min(H, int(cy + side / 2))
            crop = im.crop((l, t, r, btm))
            crop.save(out / f"{kind}_{img_path.stem}__c{bi}_conf{float(b.conf):.2f}.jpg", quality=92)
            n_crops += 1
    print(f"images={len(images)} detector_fired pos={fired['pos']} neg={fired['neg']} crops={n_crops} -> {out}")


if __name__ == "__main__":
    main()
