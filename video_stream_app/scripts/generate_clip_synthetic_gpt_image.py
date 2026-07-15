#!/usr/bin/env python3
"""Generate synthetic Hem-o-lok/titanium clip YOLO data with GPT Image edits.

The generator uses real laparoscopic frames as backgrounds and asks GPT Image
to insert a single deployed clip inside a transparent mask region. Labels are
bootstrapped from the masked edit region and refined by image-difference
between the generated image and the original background.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import random
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


CLASS_NAMES = ["hemolok_clip", "titanium_clip"]
LABEL_TO_ID = {name: idx for idx, name in enumerate(CLASS_NAMES)}
DEFAULT_DOTENV = Path("/home/user/proj/video_processor/.env")
DEFAULT_VIDEO = Path("/data/cholec80/cholec80/videos/video12.mp4")
CANVAS_SIZE = (1536, 1024)


@dataclass(frozen=True)
class PlannedSample:
    label: str
    video: Optional[Path]
    timestamp: float
    source_image: Optional[Path]
    mask_box: Tuple[int, int, int, int]
    split: str
    seed: int


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        if text.startswith("export "):
            text = text[len("export "):].strip()
            if "=" not in text:
                continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _split_for(key: str, val_ratio: float) -> str:
    value = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "val" if value < val_ratio else "train"


def _safe_stem(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)[:180].strip("_")


def _video_duration(path: Path) -> float:
    cap = cv2.VideoCapture(str(path))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    cap.release()
    return frames / fps if fps > 0 else 0.0


def _read_frame(path: Path, timestamp: float) -> Image.Image:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_idx = int(round(max(0.0, timestamp) * fps)) if fps > 0 else 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_idx))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise RuntimeError(f"Cannot read frame: {path} @ {timestamp:.2f}s")
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def _read_background(plan: PlannedSample) -> Image.Image:
    if plan.source_image is not None:
        return Image.open(plan.source_image).convert("RGB")
    if plan.video is None:
        raise RuntimeError("planned sample has no video or source image")
    return _read_frame(plan.video, plan.timestamp)


def _letterbox(image: Image.Image, size: Tuple[int, int] = CANVAS_SIZE) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    target_w, target_h = size
    src_w, src_h = image.size
    scale = min(target_w / src_w, target_h / src_h)
    new_w = int(round(src_w * scale))
    new_h = int(round(src_h * scale))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, (0, 0, 0))
    x0 = (target_w - new_w) // 2
    y0 = (target_h - new_h) // 2
    canvas.paste(resized, (x0, y0))
    return canvas, (x0, y0, x0 + new_w, y0 + new_h)


def _make_mask(size: Tuple[int, int], box: Tuple[int, int, int, int]) -> Image.Image:
    # Image API edit masks use transparent regions as the editable area.
    mask = Image.new("RGBA", size, (0, 0, 0, 255))
    draw = ImageDraw.Draw(mask)
    draw.rectangle(box, fill=(0, 0, 0, 0))
    return mask


def _candidate_boxes(content_box: Tuple[int, int, int, int], rng: random.Random) -> List[Tuple[int, int, int, int]]:
    x0, y0, x1, y1 = content_box
    w = x1 - x0
    h = y1 - y0
    centers = [
        (0.48, 0.48),
        (0.54, 0.52),
        (0.43, 0.57),
        (0.61, 0.45),
        (0.50, 0.63),
        (0.58, 0.58),
    ]
    rng.shuffle(centers)
    boxes: List[Tuple[int, int, int, int]] = []
    for cx_norm, cy_norm in centers:
        bw = int(w * rng.uniform(0.085, 0.120))
        bh = int(h * rng.uniform(0.050, 0.078))
        cx = int(x0 + w * (cx_norm + rng.uniform(-0.035, 0.035)))
        cy = int(y0 + h * (cy_norm + rng.uniform(-0.035, 0.035)))
        bx0 = max(x0 + 12, cx - bw // 2)
        by0 = max(y0 + 12, cy - bh // 2)
        bx1 = min(x1 - 12, cx + bw // 2)
        by1 = min(y1 - 12, cy + bh // 2)
        boxes.append((bx0, by0, bx1, by1))
    return boxes


def _plan_samples(args: argparse.Namespace) -> List[PlannedSample]:
    rng = random.Random(args.seed)
    videos = [Path(v).resolve() for v in args.video]
    for video in videos:
        if not video.exists():
            raise SystemExit(f"Missing video: {video}")
    source_images = _iter_source_images(args)
    per_label = {
        "hemolok_clip": args.hemolok_count,
        "titanium_clip": args.titanium_count,
    }
    plans: List[PlannedSample] = []
    for label, count in per_label.items():
        for idx in range(count):
            source_image: Optional[Path] = None
            video: Optional[Path] = None
            timestamp = 0.0
            if source_images:
                source_image = source_images[(idx + (0 if label == "hemolok_clip" else len(source_images) // 3)) % len(source_images)]
                frame = Image.open(source_image).convert("RGB")
            else:
                video = videos[(idx + (0 if label == "hemolok_clip" else 1)) % len(videos)]
                duration = _video_duration(video)
                start = min(max(0.0, args.start_sec), max(0.0, duration - 1.0))
                end = min(max(start + 1.0, args.end_sec), max(start + 1.0, duration - 1.0))
                if args.timestamps:
                    timestamp = float(args.timestamps[idx % len(args.timestamps)])
                else:
                    frac = (idx + 0.5) / max(1, count)
                    timestamp = start + (end - start) * frac + rng.uniform(-args.time_jitter, args.time_jitter)
                    timestamp = max(0.0, min(duration - 1.0, timestamp))
                frame = _read_frame(video, timestamp)
            _, content_box = _letterbox(frame)
            boxes = _candidate_boxes(content_box, rng)
            box = boxes[idx % len(boxes)]
            seed = rng.randint(1, 2_000_000_000)
            source_key = str(source_image or video)
            key = f"{source_key}:{timestamp:.3f}:{label}:{seed}"
            split = _split_for(key, args.val_ratio)
            plans.append(
                PlannedSample(
                    label=label,
                    video=video,
                    timestamp=timestamp,
                    source_image=source_image,
                    mask_box=box,
                    split=split,
                    seed=seed,
                )
            )
    return plans


def _iter_source_images(args: argparse.Namespace) -> List[Path]:
    import glob

    images: List[Path] = []
    for item in args.source_image or []:
        path = Path(item)
        if path.exists():
            images.append(path.resolve())
    for pattern in args.source_image_glob or []:
        images.extend(Path(p).resolve() for p in glob.glob(pattern))
    valid = sorted(
        {
            path
            for path in images
            if path.exists() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        }
    )
    rng = random.Random(args.seed + 17)
    rng.shuffle(valid)
    if args.max_source_images > 0:
        valid = valid[: args.max_source_images]
    return valid


def _prompt_for(label: str, mask_box: Tuple[int, int, int, int]) -> str:
    x0, y0, x1, y1 = mask_box
    common = (
        "Edit this laparoscopic cholecystectomy frame for computer-vision training. "
        "Only modify the transparent mask rectangle and preserve the rest of the frame exactly. "
        "Do not add labels, arrows, text, rulers, boxes, UI elements, extra instruments, or non-surgical objects. "
        "Do not add any clip applier, forceps, grasper, metal shaft, instrument jaw, trocar, or tool tip. "
        "The inserted object must be a deployed surgical ligating clip clamped on a small tubular tissue structure "
        "near the Calot triangle, matching laparoscopic lighting, blur, specular highlights, scale, and perspective. "
        f"The object must stay inside pixel rectangle ({x0}, {y0}) to ({x1}, {y1}). "
    )
    if label == "hemolok_clip":
        return (
            common
            + "Insert exactly one Hem-o-lok polymer locking clip: off-white or ivory plastic, thicker than a metal clip, "
            "slightly curved C/U profile with a visible locking notch/hinge, matte surface with mild highlights. "
            "Only the clip body should be visible; it should not look metallic silver."
        )
    return (
        common
        + "Insert exactly one titanium surgical ligating clip: small thin metallic silver-gray U/V-shaped clip, "
        "reflective metal highlights, narrow jaws clamped across the tubular tissue. "
        "Only the clip body should be visible; it should not look like a white polymer Hem-o-lok clip."
    )


def _decode_image_response(response: object) -> Image.Image:
    data = getattr(response, "data", None)
    if not data:
        raise RuntimeError("OpenAI image response missing data")
    item = data[0]
    b64 = getattr(item, "b64_json", None)
    if not b64:
        raise RuntimeError("OpenAI image response missing b64_json")
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def _call_image_edit(client: object, args: argparse.Namespace, base_path: Path, mask_path: Path, prompt: str) -> Image.Image:
    with base_path.open("rb") as image_file, mask_path.open("rb") as mask_file:
        response = client.images.edit(
            model=args.model,
            image=image_file,
            mask=mask_file,
            prompt=prompt,
            size=args.size,
            quality=args.quality,
            n=1,
            output_format="png",
            timeout=args.timeout,
        )
    return _decode_image_response(response)


def _estimate_box(
    base: Image.Image,
    generated: Image.Image,
    mask_box: Tuple[int, int, int, int],
    min_area: int = 300,
) -> Tuple[Tuple[int, int, int, int], Dict[str, object]]:
    base_arr = np.asarray(base.convert("RGB"))
    gen_arr = np.asarray(generated.convert("RGB").resize(base.size, Image.Resampling.LANCZOS))
    diff = cv2.absdiff(base_arr, gen_arr)
    gray = cv2.cvtColor(diff, cv2.COLOR_RGB2GRAY)
    x0, y0, x1, y1 = mask_box
    roi = gray[y0:y1, x0:x1]
    _, binary = cv2.threshold(roi, 18, 255, cv2.THRESH_BINARY)
    kernel = np.ones((5, 5), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)
    binary = cv2.dilate(binary, kernel, iterations=1)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return mask_box, {"source": "mask_fallback", "changed_area": 0}

    contour = max(contours, key=cv2.contourArea)
    cx, cy, cw, ch = cv2.boundingRect(contour)
    area = int(cv2.contourArea(contour))
    if area < min_area:
        return mask_box, {"source": "mask_fallback_small_diff", "changed_area": area}

    margin = 8
    bx0 = max(x0, x0 + cx - margin)
    by0 = max(y0, y0 + cy - margin)
    bx1 = min(x1, x0 + cx + cw + margin)
    by1 = min(y1, y0 + cy + ch + margin)
    mask_area = max(1, (x1 - x0) * (y1 - y0))
    box_area = max(1, (bx1 - bx0) * (by1 - by0))
    if box_area > mask_area * 0.98 or box_area < min_area:
        return mask_box, {"source": "mask_fallback_bad_box", "changed_area": area}
    return (bx0, by0, bx1, by1), {"source": "image_diff", "changed_area": area}


def _to_yolo(label: str, box: Tuple[int, int, int, int], size: Tuple[int, int]) -> str:
    w, h = size
    x0, y0, x1, y1 = box
    cx = ((x0 + x1) / 2.0) / w
    cy = ((y0 + y1) / 2.0) / h
    bw = (x1 - x0) / w
    bh = (y1 - y0) / h
    return f"{LABEL_TO_ID[label]} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def _draw_audit(image: Image.Image, label: str, box: Tuple[int, int, int, int], mask_box: Tuple[int, int, int, int], path: Path) -> None:
    out = image.copy()
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
    color = (255, 80, 80) if label == "hemolok_clip" else (40, 220, 255)
    draw.rectangle(mask_box, outline=(160, 160, 160), width=2)
    draw.rectangle(box, outline=color, width=5)
    text = label
    tx, ty = box[0], max(0, box[1] - 30)
    tb = draw.textbbox((tx, ty), text, font=font)
    draw.rectangle(tb, fill=color)
    draw.text((tx, ty), text, fill=(0, 0, 0), font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path, quality=92)


def _make_contact_sheet(paths: Sequence[Path], output: Path, thumb_w: int = 384) -> None:
    if not paths:
        return
    images = [Image.open(p).convert("RGB") for p in paths if p.exists()]
    if not images:
        return
    thumbs: List[Image.Image] = []
    for img in images:
        thumb_h = int(round(img.height * (thumb_w / img.width)))
        thumbs.append(img.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS))
    cols = min(3, len(thumbs))
    rows = math.ceil(len(thumbs) / cols)
    gap = 12
    cell_h = max(t.height for t in thumbs)
    sheet = Image.new("RGB", (cols * thumb_w + (cols + 1) * gap, rows * cell_h + (rows + 1) * gap), (24, 24, 24))
    for idx, thumb in enumerate(thumbs):
        x = gap + (idx % cols) * (thumb_w + gap)
        y = gap + (idx // cols) * (cell_h + gap)
        sheet.paste(thumb, (x, y))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=92)


def _write_data_yaml(output: Path) -> None:
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


def _prepare_output(output: Path, clean: bool) -> None:
    if output.exists() and clean:
        shutil.rmtree(output)
    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)
    for subdir in ("base", "masks", "audit", "annotations"):
        (output / subdir).mkdir(parents=True, exist_ok=True)
    _write_data_yaml(output)


def generate(args: argparse.Namespace) -> None:
    _load_dotenv(Path(args.dotenv))
    api_key = os.environ.get(args.api_key_env) or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit(f"Missing API key in {args.api_key_env}/OPENAI_API_KEY; checked {args.dotenv}")

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    output = Path(args.output)
    _prepare_output(output, args.clean)
    plans = _plan_samples(args)
    metadata_path = output / "metadata.jsonl"
    audit_paths: List[Path] = []
    counts: Dict[str, int] = {name: 0 for name in CLASS_NAMES}
    failures: List[Dict[str, object]] = []
    t0 = time.time()

    mode = "w" if args.clean or not metadata_path.exists() else "a"
    with metadata_path.open(mode, encoding="utf-8") as meta:
        for idx, plan in enumerate(plans, start=1):
            source_stem = plan.source_image.stem if plan.source_image is not None else (plan.video.stem if plan.video is not None else "source")
            stem = _safe_stem(f"gptimage2_{plan.label}_{source_stem}_{plan.timestamp:07.2f}s_{idx:04d}")
            base_path = output / "base" / f"{stem}.png"
            mask_path = output / "masks" / f"{stem}.png"
            try:
                frame = _read_background(plan)
                base, content_box = _letterbox(frame)
                mask = _make_mask(base.size, plan.mask_box)
                base.save(base_path)
                mask.save(mask_path)
                prompt = _prompt_for(plan.label, plan.mask_box)

                source_name = plan.source_image.name if plan.source_image is not None else (plan.video.name if plan.video is not None else "source")
                print(f"[gpt-image] {idx}/{len(plans)} {plan.label} {source_name}@{plan.timestamp:.2f}s split={plan.split}", flush=True)
                generated = _call_image_edit(client, args, base_path, mask_path, prompt)
                if generated.size != base.size:
                    generated = generated.resize(base.size, Image.Resampling.LANCZOS)
                box, box_meta = _estimate_box(base, generated, plan.mask_box)

                image_path = output / "images" / plan.split / f"{stem}.png"
                label_path = output / "labels" / plan.split / f"{stem}.txt"
                generated.save(image_path)
                label_path.write_text(_to_yolo(plan.label, box, generated.size) + "\n", encoding="utf-8")
                audit_path = output / "audit" / f"{stem}.jpg"
                _draw_audit(generated, plan.label, box, plan.mask_box, audit_path)
                audit_paths.append(audit_path)
                counts[plan.label] += 1

                row = {
                    "image": str(image_path),
                    "label_file": str(label_path),
                    "audit": str(audit_path),
                    "class": plan.label,
                    "source_video": str(plan.video) if plan.video is not None else None,
                    "source_image": str(plan.source_image) if plan.source_image is not None else None,
                    "source_timestamp": round(plan.timestamp, 3),
                    "split": plan.split,
                    "size": {"width": generated.width, "height": generated.height},
                    "mask_box_xyxy": list(plan.mask_box),
                    "box_xyxy": list(box),
                    "box_meta": box_meta,
                    "prompt": prompt,
                    "model": args.model,
                    "quality": args.quality,
                    "seed": plan.seed,
                    "content_box_xyxy": list(content_box),
                }
                (output / "annotations" / f"{stem}.json").write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
                meta.write(json.dumps(row, ensure_ascii=False) + "\n")
                meta.flush()
            except Exception as exc:
                failure = {
                    "class": plan.label,
                    "video": str(plan.video) if plan.video is not None else None,
                    "source_image": str(plan.source_image) if plan.source_image is not None else None,
                    "timestamp": plan.timestamp,
                    "error": repr(exc),
                }
                failures.append(failure)
                print(f"[gpt-image] failed: {failure}", flush=True)
                if not args.continue_on_error:
                    raise

    _make_contact_sheet(audit_paths, output / "audit" / "contact_sheet.jpg")
    summary = {
        "output": str(output.resolve()),
        "model": args.model,
        "size": args.size,
        "quality": args.quality,
        "requested": {"hemolok_clip": args.hemolok_count, "titanium_clip": args.titanium_count},
        "generated": counts,
        "failures": failures,
        "elapsed_sec": round(time.time() - t0, 2),
        "contact_sheet": str((output / "audit" / "contact_sheet.jpg").resolve()),
        "data_yaml": str((output / "data.yaml").resolve()),
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", action="append", default=[], help="Input laparoscopic video; repeatable")
    parser.add_argument("--source-image", action="append", default=[], help="Input background image; repeatable")
    parser.add_argument("--source-image-glob", action="append", default=[], help="Glob of background images")
    parser.add_argument("--max-source-images", type=int, default=0)
    parser.add_argument("--output", default="datasets/surgical_clips_gptimage2_synthetic_pilot_v1")
    parser.add_argument("--dotenv", default=str(DEFAULT_DOTENV))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--size", default="1536x1024")
    parser.add_argument("--quality", default="medium", choices=["low", "medium", "high", "auto"])
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--hemolok-count", type=int, default=4)
    parser.add_argument("--titanium-count", type=int, default=4)
    parser.add_argument("--start-sec", type=float, default=240.0)
    parser.add_argument("--end-sec", type=float, default=620.0)
    parser.add_argument("--time-jitter", type=float, default=12.0)
    parser.add_argument("--timestamps", type=float, nargs="*", default=None)
    parser.add_argument("--val-ratio", type=float, default=0.20)
    parser.add_argument("--seed", type=int, default=20260705)
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()
    if not args.video:
        args.video = [str(DEFAULT_VIDEO)]
    generate(args)


if __name__ == "__main__":
    main()
