#!/usr/bin/env python3
"""Sample frames around existing clip-positive annotations for more GPT labeling."""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import cv2
from PIL import Image, ImageDraw, ImageFont


def _safe_stem(video_path: Path, sec: float) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", video_path.with_suffix("").as_posix().strip("/"))
    return f"{stem}_{sec:08.2f}s".replace(".", "p")


def _read_frame(video_path: Path, sec: float) -> Image.Image | None:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_count / fps if fps > 0 else 0.0
    sec = max(0.0, min(sec, duration))
    frame_idx = int(round(sec * fps)) if fps > 0 else 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_idx))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return None
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def _video_duration(video_path: Path) -> float:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return 0.0
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    return frames / fps if fps > 0 else 0.0


def _iter_positive_times(sources: Iterable[Path], labels: set[str], min_conf: float) -> Iterable[Tuple[str, float, str, float]]:
    for source in sources:
        ann_dir = source / "annotations"
        if not ann_dir.exists():
            continue
        for ann_path in sorted(ann_dir.glob("*.json")):
            try:
                ann = json.loads(ann_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            video = str(ann.get("video") or ann.get("source_video") or "")
            if not video:
                continue
            try:
                sec = float(ann.get("time_sec", 0.0))
            except Exception:
                sec = 0.0
            for obj in ann.get("objects") or []:
                label = str(obj.get("label") or "")
                if label not in labels:
                    continue
                try:
                    conf = float(obj.get("confidence", (obj.get("review") or {}).get("confidence", 0.0)))
                except Exception:
                    conf = 0.0
                if conf >= min_conf:
                    yield video, sec, label, conf


def _merge_times(times: List[float], merge_gap_sec: float) -> List[float]:
    if not times:
        return []
    times = sorted(times)
    clusters: List[List[float]] = [[times[0]]]
    for sec in times[1:]:
        if sec - clusters[-1][-1] <= merge_gap_sec:
            clusters[-1].append(sec)
        else:
            clusters.append([sec])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _sample_window(center: float, duration: float, radius_sec: float, interval_sec: float) -> List[float]:
    start = max(0.0, center - radius_sec)
    end = min(duration, center + radius_sec)
    out = []
    t = start
    while t <= end + 1e-6:
        out.append(round(t, 2))
        t += interval_sec
    return out


def _write_contact_sheet(output: Path, rows: List[Dict[str, Any]], max_images: int, thumb_width: int) -> None:
    if max_images <= 0:
        return
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    thumbs = []
    for row in rows[:max_images]:
        image_path = Path(str(row.get("image", "")))
        if not image_path.exists():
            continue
        image = Image.open(image_path).convert("RGB")
        ratio = thumb_width / float(image.width)
        thumb = image.resize((thumb_width, max(1, int(image.height * ratio))), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(thumb)
        label = f"{Path(str(row.get('video', ''))).name} {row.get('label_hint')} {float(row.get('time_sec', 0.0)):.1f}s"
        bbox = draw.textbbox((4, 4), label, font=font)
        draw.rectangle(bbox, fill=(0, 0, 0))
        draw.text((4, 4), label, fill=(255, 255, 255), font=font)
        thumbs.append(thumb)
    if not thumbs:
        return
    cols = min(5, len(thumbs))
    rows_count = math.ceil(len(thumbs) / cols)
    cell_w = max(t.width for t in thumbs)
    cell_h = max(t.height for t in thumbs)
    sheet = Image.new("RGB", (cols * cell_w, rows_count * cell_h), (20, 20, 20))
    for idx, thumb in enumerate(thumbs):
        sheet.paste(thumb, ((idx % cols) * cell_w, (idx // cols) * cell_h))
    sheet.save(output / "contact_sheet.jpg", quality=90)


def sample(args: argparse.Namespace) -> None:
    output = Path(args.output)
    if output.exists() and args.clean:
        shutil.rmtree(output)
    (output / "images").mkdir(parents=True, exist_ok=True)

    labels = {label.strip() for label in args.labels.split(",") if label.strip()}
    by_video_label: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    conf_counts: Counter[str] = Counter()
    for video, sec, label, _conf in _iter_positive_times([Path(p) for p in args.sources], labels, args.min_conf):
        by_video_label[(video, label)].append(sec)
        conf_counts[label] += 1

    candidates: List[Dict[str, Any]] = []
    for (video, label), times in sorted(by_video_label.items()):
        video_path = Path(video)
        if not video_path.exists():
            continue
        duration = _video_duration(video_path)
        if duration <= 0:
            continue
        centers = _merge_times(times, args.merge_gap_sec)
        for center in centers:
            for sec in _sample_window(center, duration, args.radius_sec, args.interval_sec):
                candidates.append({"video": str(video_path), "time_sec": sec, "label_hint": label, "center_sec": round(center, 2)})

    # Deduplicate exact frames while preserving sorted order by video/time/label.
    seen = set()
    deduped = []
    for row in sorted(candidates, key=lambda r: (r["video"], r["time_sec"], r["label_hint"])):
        key = (row["video"], row["time_sec"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    if args.max_total and len(deduped) > args.max_total:
        deduped = deduped[: args.max_total]

    written: List[Dict[str, Any]] = []
    frames_path = output / "frames.jsonl"
    with frames_path.open("w", encoding="utf-8") as f:
        for idx, row in enumerate(deduped, start=1):
            video_path = Path(row["video"])
            sec = float(row["time_sec"])
            image = _read_frame(video_path, sec)
            if image is None:
                continue
            stem = _safe_stem(video_path, sec)
            image_path = output / "images" / f"{stem}.jpg"
            image.save(image_path, quality=92)
            out_row = dict(row)
            out_row["image"] = str(image_path)
            f.write(json.dumps(out_row, ensure_ascii=False) + "\n")
            written.append(out_row)
            print(f"[clip-window-sample] {idx}/{len(deduped)} {video_path.name}@{sec:.1f}s {row['label_hint']}", flush=True)

    summary = {
        "sources": args.sources,
        "labels": sorted(labels),
        "positive_objects_seen": dict(conf_counts),
        "video_label_groups": len(by_video_label),
        "candidate_frames": len(deduped),
        "frames_written": len(written),
        "radius_sec": args.radius_sec,
        "interval_sec": args.interval_sec,
        "merge_gap_sec": args.merge_gap_sec,
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_contact_sheet(output, written, args.contact_sheet_max, args.contact_sheet_thumb_width)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", nargs="+", required=True)
    parser.add_argument("--output", default="datasets/clip_positive_window_samples_v1")
    parser.add_argument("--labels", default="hemolok_clip,titanium_clip")
    parser.add_argument("--min-conf", type=float, default=0.55)
    parser.add_argument("--radius-sec", type=float, default=8.0)
    parser.add_argument("--interval-sec", type=float, default=1.0)
    parser.add_argument("--merge-gap-sec", type=float, default=6.0)
    parser.add_argument("--max-total", type=int, default=800)
    parser.add_argument("--contact-sheet-max", type=int, default=80)
    parser.add_argument("--contact-sheet-thumb-width", type=int, default=300)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    sample(args)


if __name__ == "__main__":
    main()
