#!/usr/bin/env python3
"""Build temporal review sheets for surgical clip training candidates."""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import cv2
from PIL import Image, ImageDraw, ImageFont


CLIP_LABELS = {"hemolok_clip", "titanium_clip"}


@dataclass
class Candidate:
    source: str
    video: Path
    time_sec: float
    image: Path | None
    label: str
    confidence: float
    box_1000: Tuple[float, float, float, float]
    reason: str

    @property
    def key(self) -> Tuple[str, str, int]:
        return (str(self.video), self.label, int(round(self.time_sec * 10)))


def _norm_label(value: Any) -> str:
    label = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "hem_o_lok_clip": "hemolok_clip",
        "hemolock_clip": "hemolok_clip",
        "hemlok_clip": "hemolok_clip",
        "metal_clip": "titanium_clip",
        "titanium": "titanium_clip",
    }
    return aliases.get(label, label)


def _safe_stem(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")[:180]


def _read_json(path: Path) -> Dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _as_box_1000(box: Sequence[Any]) -> Tuple[float, float, float, float] | None:
    if not isinstance(box, Sequence) or len(box) != 4:
        return None
    try:
        x1, y1, x2, y2 = [float(v) for v in box]
    except Exception:
        return None
    x1, x2 = sorted((max(0.0, min(1000.0, x1)), max(0.0, min(1000.0, x2))))
    y1, y2 = sorted((max(0.0, min(1000.0, y1)), max(0.0, min(1000.0, y2))))
    if x2 - x1 < 3 or y2 - y1 < 3:
        return None
    return (x1, y1, x2, y2)


def _candidate_from_fullframe(source: Path, ann: Dict[str, Any], obj: Dict[str, Any], min_conf: float) -> Candidate | None:
    label = _norm_label(obj.get("label"))
    if label not in CLIP_LABELS:
        return None
    try:
        conf = float(obj.get("confidence", 0.0))
    except Exception:
        conf = 0.0
    if conf < min_conf:
        return None
    box = _as_box_1000(obj.get("box_1000") or [])
    if box is None:
        return None
    video = Path(str(ann.get("video") or ""))
    if not video.exists():
        return None
    image = None
    split = ann.get("split") or "train"
    image_guess = source / "images" / str(split) / f"{Path(str(ann.get('video', ''))).stem}.jpg"
    if image_guess.exists():
        image = image_guess
    try:
        sec = float(ann.get("time_sec", 0.0))
    except Exception:
        return None
    return Candidate(
        source=str(source),
        video=video,
        time_sec=sec,
        image=image,
        label=label,
        confidence=conf,
        box_1000=box,
        reason=str(obj.get("reason", "")),
    )


def _load_fullframe_sources(sources: Iterable[Path], min_conf: float) -> List[Candidate]:
    out: List[Candidate] = []
    for source in sources:
        ann_dir = source / "annotations"
        if not ann_dir.exists():
            continue
        for ann_path in sorted(ann_dir.glob("*.json")):
            ann = _read_json(ann_path)
            if not ann:
                continue
            for obj in ann.get("objects") or []:
                cand = _candidate_from_fullframe(source, ann, obj, min_conf)
                if cand is not None:
                    out.append(cand)
    return out


def _metadata_by_image(metadata_path: Path) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    with metadata_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            image = Path(str(row.get("image", "")))
            rows[str(image)] = row
            rows[str(image.resolve())] = row
    return rows


def _load_classified_candidates(jsonl_paths: Iterable[Path], metadata_path: Path, min_conf: float) -> List[Candidate]:
    meta = _metadata_by_image(metadata_path)
    out: List[Candidate] = []
    for jsonl_path in jsonl_paths:
        if not jsonl_path.exists():
            continue
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                source_image = Path(str(row.get("source_image") or row.get("image") or ""))
                meta_row = meta.get(str(source_image)) or meta.get(str(source_image.resolve()))
                if not meta_row:
                    continue
                boxes = meta_row.get("boxes") or []
                width = float(meta_row.get("width") or 0.0)
                height = float(meta_row.get("height") or 0.0)
                if width <= 0 or height <= 0:
                    continue
                for obj in row.get("objects") or []:
                    label = _norm_label(obj.get("label"))
                    if label not in CLIP_LABELS:
                        continue
                    try:
                        conf = float(obj.get("confidence", 0.0))
                        index = int(obj.get("index", 0))
                    except Exception:
                        continue
                    if conf < min_conf or index < 1 or index > len(boxes):
                        continue
                    box = boxes[index - 1]
                    try:
                        x1, y1, x2, y2 = float(box["x1"]), float(box["y1"]), float(box["x2"]), float(box["y2"])
                    except Exception:
                        continue
                    box_1000 = _as_box_1000([x1 / width * 1000.0, y1 / height * 1000.0, x2 / width * 1000.0, y2 / height * 1000.0])
                    if box_1000 is None:
                        continue
                    video = Path(str(meta_row.get("video") or ""))
                    if not video.exists():
                        continue
                    out.append(
                        Candidate(
                            source=str(jsonl_path.parent),
                            video=video,
                            time_sec=float(meta_row.get("timestamp", meta_row.get("time_sec", 0.0))),
                            image=source_image if source_image.exists() else None,
                            label=label,
                            confidence=conf,
                            box_1000=box_1000,
                            reason=str(obj.get("reason", "")),
                        )
                    )
    return out


def _dedupe(candidates: List[Candidate], gap_sec: float) -> List[Candidate]:
    if gap_sec <= 0:
        return candidates
    candidates = sorted(candidates, key=lambda c: (str(c.video), c.label, c.time_sec, -c.confidence))
    out: List[Candidate] = []
    last_time: Dict[Tuple[str, str], float] = {}
    for cand in candidates:
        key = (str(cand.video), cand.label)
        prev = last_time.get(key)
        if prev is not None and abs(cand.time_sec - prev) < gap_sec:
            continue
        out.append(cand)
        last_time[key] = cand.time_sec
    return out


def _read_frame(cap: cv2.VideoCapture, fps: float, sec: float) -> Image.Image | None:
    frame_idx = int(round(max(0.0, sec) * fps)) if fps > 0 else 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_idx))
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _draw_box(image: Image.Image, cand: Candidate, color: Tuple[int, int, int]) -> Image.Image:
    img = image.copy()
    draw = ImageDraw.Draw(img)
    w, h = img.size
    x1, y1, x2, y2 = cand.box_1000
    xy = [x1 / 1000.0 * w, y1 / 1000.0 * h, x2 / 1000.0 * w, y2 / 1000.0 * h]
    draw.rectangle(xy, outline=color, width=4)
    label = f"{cand.label} {cand.confidence:.2f}"
    font = _font(18)
    bbox = draw.textbbox((int(xy[0]), max(0, int(xy[1]) - 24)), label, font=font)
    draw.rectangle(bbox, fill=color)
    draw.text((bbox[0], bbox[1]), label, fill=(0, 0, 0), font=font)
    return img


def _crop(image: Image.Image, cand: Candidate, pad: float = 1.8) -> Image.Image:
    w, h = image.size
    x1, y1, x2, y2 = cand.box_1000
    px1, py1 = x1 / 1000.0 * w, y1 / 1000.0 * h
    px2, py2 = x2 / 1000.0 * w, y2 / 1000.0 * h
    cx, cy = (px1 + px2) / 2.0, (py1 + py2) / 2.0
    bw, bh = max(24.0, (px2 - px1) * pad), max(24.0, (py2 - py1) * pad)
    size = max(bw, bh, 96.0)
    left = max(0, int(cx - size / 2.0))
    top = max(0, int(cy - size / 2.0))
    right = min(w, int(cx + size / 2.0))
    bottom = min(h, int(cy + size / 2.0))
    return image.crop((left, top, right, bottom))


def _make_sequence(cand: Candidate, frames: List[Tuple[float, Image.Image]], thumb_width: int) -> Image.Image:
    color = (255, 90, 90) if cand.label == "hemolok_clip" else (40, 210, 255)
    tiles: List[Image.Image] = []
    font = _font(18)
    for offset, frame in frames:
        img = _draw_box(frame, cand, color) if abs(offset) < 1e-6 else frame.copy()
        ratio = thumb_width / float(img.width)
        img = img.resize((thumb_width, max(1, int(img.height * ratio))), Image.Resampling.LANCZOS)
        caption = f"{offset:+.1f}s"
        caption_h = 30
        tile = Image.new("RGB", (img.width, img.height + caption_h), (12, 12, 12))
        tile.paste(img, (0, 0))
        draw = ImageDraw.Draw(tile)
        draw.text((6, img.height + 4), caption, fill=(255, 255, 255), font=font)
        tiles.append(tile)
    header_h = 42
    tile_w = max(t.width for t in tiles)
    tile_h = max(t.height for t in tiles)
    sheet = Image.new("RGB", (tile_w * len(tiles), tile_h + header_h), (8, 8, 8))
    draw = ImageDraw.Draw(sheet)
    header = f"{cand.label} conf={cand.confidence:.2f}  {cand.video.name}@{cand.time_sec:.1f}s"
    draw.text((8, 9), header, fill=(255, 255, 255), font=_font(20))
    for idx, tile in enumerate(tiles):
        sheet.paste(tile, (idx * tile_w, header_h))
    return sheet


def _write_html(output: Path, rows: List[Dict[str, Any]]) -> None:
    items = []
    for row in rows:
        seq = html.escape(row["sequence_image"])
        crop = html.escape(row["crop_image"])
        reason = html.escape(row.get("reason", ""))
        label = html.escape(row["label"])
        items.append(
            f"<article><h3>{label} {row['confidence']:.2f}</h3>"
            f"<p>{html.escape(Path(row['video']).name)} @ {row['time_sec']:.1f}s<br>{reason}</p>"
            f"<img src='{seq}'><img src='{crop}' class='crop'></article>"
        )
    page = """<!doctype html>
<meta charset="utf-8">
<title>Clip Temporal Review Set</title>
<style>
body{font-family:Arial,sans-serif;background:#111;color:#eee;margin:18px}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(520px,1fr));gap:16px}
article{background:#1b1b1b;border:1px solid #333;padding:10px;border-radius:6px}
h3{margin:0 0 6px;font-size:18px}
p{font-size:13px;line-height:1.35;color:#ccc}
img{max-width:100%;display:block;margin:8px 0}
.crop{max-width:180px;border:1px solid #555}
</style>
<main>
""" + "\n".join(items) + "\n</main>\n"
    (output / "index.html").write_text(page, encoding="utf-8")


def build(args: argparse.Namespace) -> None:
    output = Path(args.output)
    if output.exists() and args.clean:
        shutil.rmtree(output)
    (output / "sequences").mkdir(parents=True, exist_ok=True)
    (output / "crops").mkdir(parents=True, exist_ok=True)

    candidates: List[Candidate] = []
    candidates.extend(_load_fullframe_sources([Path(p) for p in args.annotation_sources], args.min_confidence))
    candidates.extend(_load_classified_candidates([Path(p) for p in args.classified_candidate_jsonl], Path(args.candidate_metadata), args.min_confidence))
    candidates = _dedupe(candidates, args.min_gap_sec)
    candidates = sorted(candidates, key=lambda c: (-c.confidence, c.label, str(c.video), c.time_sec))
    if args.max_candidates and len(candidates) > args.max_candidates:
        candidates = candidates[: args.max_candidates]

    offsets = [float(v) for v in str(args.offsets).split(",") if v.strip()]
    rows: List[Dict[str, Any]] = []
    counts: Counter[str] = Counter()
    by_video: Dict[Path, List[Candidate]] = defaultdict(list)
    for cand in candidates:
        by_video[cand.video].append(cand)

    for video, video_candidates in by_video.items():
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            continue
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        for cand in video_candidates:
            frames: List[Tuple[float, Image.Image]] = []
            for offset in offsets:
                frame = _read_frame(cap, fps, cand.time_sec + offset)
                if frame is not None:
                    frames.append((offset, frame))
            center = next((frame for offset, frame in frames if abs(offset) < 1e-6), None)
            if center is None and frames:
                center = frames[len(frames) // 2][1]
            if center is None:
                continue
            stem = _safe_stem(f"{cand.label}_{video.stem}_{cand.time_sec:.2f}_{len(rows):04d}")
            seq_rel = Path("sequences") / cand.label / f"{stem}.jpg"
            crop_rel = Path("crops") / cand.label / f"{stem}.jpg"
            (output / seq_rel).parent.mkdir(parents=True, exist_ok=True)
            (output / crop_rel).parent.mkdir(parents=True, exist_ok=True)
            _make_sequence(cand, frames, args.thumb_width).save(output / seq_rel, quality=92)
            _crop(_draw_box(center, cand, (255, 90, 90) if cand.label == "hemolok_clip" else (40, 210, 255)), cand).save(output / crop_rel, quality=92)
            row = {
                "source": cand.source,
                "video": str(cand.video),
                "time_sec": cand.time_sec,
                "label": cand.label,
                "confidence": cand.confidence,
                "box_1000": list(cand.box_1000),
                "reason": cand.reason,
                "sequence_image": str(seq_rel),
                "crop_image": str(crop_rel),
            }
            rows.append(row)
            counts[cand.label] += 1
        cap.release()

    with (output / "candidates.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "annotation_sources": args.annotation_sources,
        "classified_candidate_jsonl": args.classified_candidate_jsonl,
        "candidate_metadata": args.candidate_metadata,
        "offsets": offsets,
        "candidates_written": len(rows),
        "objects": dict(counts),
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_html(output, rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotation-sources", nargs="*", default=[])
    parser.add_argument("--classified-candidate-jsonl", nargs="*", default=[])
    parser.add_argument("--candidate-metadata", default="datasets/clip_detector_v1/metadata.jsonl")
    parser.add_argument("--output", default="datasets/clip_temporal_review_candidates_v1")
    parser.add_argument("--offsets", default="-1,0,1")
    parser.add_argument("--min-confidence", type=float, default=0.72)
    parser.add_argument("--min-gap-sec", type=float, default=2.0)
    parser.add_argument("--max-candidates", type=int, default=0)
    parser.add_argument("--thumb-width", type=int, default=360)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    build(args)


if __name__ == "__main__":
    main()
