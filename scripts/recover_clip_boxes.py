#!/usr/bin/env python3
"""Recover bounding boxes for mislabeled reject_* images that actually contain clips.

For each image in label_corrections_20260709.json relabel_to_clip:
  1. run the current clip YOLO at very low confidence to propose boxes
  2. crop each proposal (padded) and ask the local VLM whether it shows a
     deployed clip, and whether it is polymer (hemolok-style) or metal (titanium)
  3. keep VLM-confirmed boxes and write YOLO label files
  4. save annotated previews for visual audit

Outputs:
  <out>/labels/<stem>.txt         YOLO labels (0=hemolok_clip, 1=titanium_clip)
  <out>/preview/<stem>.jpg        image with confirmed boxes drawn
  <out>/recovery_report.json
"""

from __future__ import annotations

import argparse
import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw

PROMPT = (
    "This is a close-up crop from a laparoscopic cholecystectomy frame. "
    "Decide whether a DEPLOYED clip body (already released, resting on tissue) is visible in this crop. "
    "Polymer locking clips (Hem-o-lok style) can be white, ivory, purple, blue or green plastic. "
    "Titanium clips are small silver/gray metallic V or U shapes. "
    "A long instrument shaft, clip applier jaw, hook tip, trocar or glare is NOT a deployed clip. "
    "Return only JSON: {\"is_clip\": true, \"material\": \"polymer|metal|unknown\", \"confidence\": 0.0}"
)


def data_url(im: Image.Image) -> str:
    im = im.convert("RGB")
    im.thumbnail((448, 448), Image.Resampling.LANCZOS)
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def ask_vlm(base_url: str, model: str, crop: Image.Image, timeout: float = 30.0) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return one compact JSON object only."},
            {"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": data_url(crop), "detail": "low"}},
            ]},
        ],
        "temperature": 0.0,
        "max_tokens": 80,
    }
    try:
        r = requests.post(f"{base_url}/chat/completions", json=payload, timeout=timeout)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"] or ""
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}
    except Exception as exc:
        return {"error": str(exc)[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corrections", default="datasets/clip_detector_reviewed_seed_v1/label_corrections_20260709.json")
    ap.add_argument("--seed-data", default="datasets/clip_detector_reviewed_seed_v1")
    ap.add_argument("--weights", default="models/clip_detector/yolo_clip_reviewed_seed_plus_gptimage2_imagebg_100_v1/weights/best.pt")
    ap.add_argument("--out", default="datasets/clip_box_recovery_v1")
    ap.add_argument("--conf", type=float, default=0.005)
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--pad", type=float, default=2.2)
    ap.add_argument("--min-vlm-conf", type=float, default=0.5)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--base-url", default="http://127.0.0.1:8010/v1")
    ap.add_argument("--model", default="Qwen3-VL-8B-Instruct")
    args = ap.parse_args()

    from ultralytics import YOLO

    corr = json.loads(Path(args.corrections).read_text())
    targets = []
    for name in corr["relabel_to_clip"]:
        hits = list(Path(args.seed_data, "images").glob(f"*/{name}"))
        if hits:
            targets.append(hits[0])
    print(f"targets: {len(targets)}")

    out = Path(args.out)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    (out / "preview").mkdir(parents=True, exist_ok=True)

    yolo = YOLO(args.weights)
    report = []
    for img_path in targets:
        res = yolo.predict(str(img_path), conf=args.conf, imgsz=1280, iou=0.4,
                           device=args.device, verbose=False)[0]
        boxes = sorted(res.boxes, key=lambda b: -float(b.conf))[: args.top] if res.boxes is not None else []
        im = Image.open(img_path).convert("RGB")
        W, H = im.size
        crops, metas = [], []
        for b in boxes:
            x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            side = max((x2 - x1) * args.pad, (y2 - y1) * args.pad, 80)
            l, t = max(0, int(cx - side / 2)), max(0, int(cy - side / 2))
            r_, btm = min(W, int(cx + side / 2)), min(H, int(cy + side / 2))
            crops.append(im.crop((l, t, r_, btm)))
            metas.append((x1, y1, x2, y2, float(b.conf)))
        with ThreadPoolExecutor(4) as ex:
            answers = list(ex.map(lambda c: ask_vlm(args.base_url, args.model, c), crops))

        confirmed = []
        for (x1, y1, x2, y2, det_conf), ans in zip(metas, answers):
            if ans.get("is_clip") and float(ans.get("confidence") or 0) >= args.min_vlm_conf:
                # drop near-duplicate boxes (IoU-ish center distance test)
                dup = False
                for cx2 in confirmed:
                    if abs((x1 + x2) / 2 - (cx2[0] + cx2[2]) / 2) < (x2 - x1) and \
                       abs((y1 + y2) / 2 - (cx2[1] + cx2[3]) / 2) < (y2 - y1):
                        dup = True
                        break
                if not dup:
                    cls = 0 if ans.get("material") == "polymer" else 1
                    confirmed.append((x1, y1, x2, y2, cls, det_conf, ans))

        stem = img_path.stem
        if confirmed:
            lines = []
            draw_im = im.copy()
            d = ImageDraw.Draw(draw_im)
            for x1, y1, x2, y2, cls, det_conf, ans in confirmed:
                cxn, cyn = (x1 + x2) / 2 / W, (y1 + y2) / 2 / H
                wn, hn = (x2 - x1) / W, (y2 - y1) / H
                lines.append(f"{cls} {cxn:.6f} {cyn:.6f} {wn:.6f} {hn:.6f}")
                color = "cyan" if cls == 0 else "yellow"
                d.rectangle([x1, y1, x2, y2], outline=color, width=4)
                d.text((x1 + 2, max(0, y1 - 16)), f"{'hem' if cls==0 else 'ti'} {det_conf:.2f}", fill=color)
            (out / "labels" / f"{stem}.txt").write_text("\n".join(lines) + "\n")
            draw_im.save(out / "preview" / f"{stem}.jpg", quality=88)
        report.append({"image": img_path.name, "proposals": len(boxes),
                       "confirmed": len(confirmed),
                       "answers": [a for a in answers]})
        print(f"{stem}: proposals={len(boxes)} confirmed={len(confirmed)}")

    (out / "recovery_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    got = sum(1 for r in report if r["confirmed"])
    print(f"recovered boxes for {got}/{len(targets)} images -> {out}")


if __name__ == "__main__":
    main()
