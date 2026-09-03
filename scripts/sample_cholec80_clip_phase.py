#!/usr/bin/env python3
"""Sample Cholec80 frames around the ClippingCutting phase for clip labeling."""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import cv2
from PIL import Image, ImageDraw, ImageFont


def _parse_time(value: str) -> float:
    value = value.strip()
    hh, mm, ss = value.split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(ss)


def _phase_ranges(timestamp_path: Path, target_phase: str, margin_sec: float) -> List[Tuple[float, float]]:
    ranges: List[Tuple[float, float]] = []
    current_start = None
    prev_t = None
    prev_phase = None
    with timestamp_path.open("r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            try:
                t = _parse_time(row["Frame"])
            except Exception:
                continue
            phase = str(row.get("Phase", "")).strip()
            if phase == target_phase and current_start is None:
                current_start = t
            if prev_phase == target_phase and phase != target_phase and current_start is not None:
                ranges.append((max(0.0, current_start - margin_sec), (prev_t or t) + margin_sec))
                current_start = None
            prev_t = t
            prev_phase = phase
    if current_start is not None and prev_t is not None:
        ranges.append((max(0.0, current_start - margin_sec), prev_t + margin_sec))
    return ranges


def _sample_range(start: float, end: float, interval_sec: float, max_per_range: int) -> List[float]:
    if end <= start:
        return []
    out = []
    t = start
    while t <= end:
        out.append(round(t, 2))
        t += interval_sec
    if max_per_range and len(out) > max_per_range:
        if max_per_range == 1:
            return [out[len(out) // 2]]
        step = (len(out) - 1) / float(max_per_range - 1)
        return [out[round(i * step)] for i in range(max_per_range)]
    return out


def _read_frame(video_path: Path, sec: float) -> Image.Image | None:
    cap = cv2.VideoCapture(str(video_path))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_idx = int(round(sec * fps)) if fps > 0 else 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_idx))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return None
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def _read_grouped_frames(rows: List[Dict]) -> Iterable[Tuple[int, Dict, Image.Image]]:
    """Read selected rows while opening each source video only once."""
    by_video: Dict[str, List[Tuple[int, Dict]]] = defaultdict(list)
    for idx, row in enumerate(rows, start=1):
        by_video[str(row["video"])].append((idx, row))

    for video_name, items in sorted(by_video.items()):
        video_path = Path(video_name)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            continue
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        for original_idx, row in sorted(items, key=lambda item: float(item[1]["time_sec"])):
            sec = float(row["time_sec"])
            frame_idx = int(round(sec * fps)) if fps > 0 else 0
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_idx))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            yield original_idx, row, image
        cap.release()


def _safe_stem(video_path: Path, sec: float) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", video_path.with_suffix("").as_posix().strip("/"))
    return f"{stem}_{sec:08.2f}s".replace(".", "p")


def _write_contact_sheet(output: Path, rows: List[Dict], max_images: int, thumb_width: int) -> None:
    if max_images <= 0:
        return
    chosen = rows[:max_images]
    thumbs = []
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    for row in chosen:
        image_path = Path(str(row.get("image", "")))
        if not image_path.exists():
            continue
        image = Image.open(image_path).convert("RGB")
        ratio = thumb_width / float(image.width)
        thumb = image.resize((thumb_width, max(1, int(image.height * ratio))), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(thumb)
        label = f"{Path(str(row.get('video', ''))).stem} {float(row.get('time_sec', 0.0)):.1f}s"
        bbox = draw.textbbox((4, 4), label, font=font)
        draw.rectangle(bbox, fill=(0, 0, 0))
        draw.text((4, 4), label, fill=(255, 255, 255), font=font)
        thumbs.append(thumb)
    if not thumbs:
        return
    cols = max(1, min(5, len(thumbs)))
    rows_count = (len(thumbs) + cols - 1) // cols
    cell_w = max(t.width for t in thumbs)
    cell_h = max(t.height for t in thumbs)
    sheet = Image.new("RGB", (cols * cell_w, rows_count * cell_h), (20, 20, 20))
    for idx, thumb in enumerate(thumbs):
        x = (idx % cols) * cell_w
        y = (idx // cols) * cell_h
        sheet.paste(thumb, (x, y))
    sheet.save(output / "contact_sheet.jpg", quality=90)


def sample(args: argparse.Namespace) -> None:
    video_dir = Path(args.video_dir)
    output = Path(args.output)
    if output.exists() and args.clean:
        shutil.rmtree(output)
    (output / "images").mkdir(parents=True, exist_ok=True)
    videos = sorted(video_dir.glob("video*.mp4"))
    if args.max_videos:
        videos = videos[: args.max_videos]

    rng = random.Random(args.seed)
    rows: List[Dict] = []
    phase_counts = Counter()
    for video in videos:
        timestamp = video.with_name(f"{video.stem}-timestamp.txt")
        if not timestamp.exists():
            continue
        ranges = _phase_ranges(timestamp, args.phase, args.margin_sec)
        phase_counts[video.name] = len(ranges)
        for range_idx, (start, end) in enumerate(ranges):
            for sec in _sample_range(start, end, args.interval_sec, args.max_per_range):
                rows.append({"video": str(video), "time_sec": sec, "range_index": range_idx, "phase": args.phase})

    rng.shuffle(rows)
    if args.max_total and len(rows) > args.max_total:
        rows = rows[: args.max_total]

    metadata_path = output / "frames.jsonl"
    written_rows: List[Dict] = []
    with metadata_path.open("w", encoding="utf-8") as f:
        for idx, row, image in _read_grouped_frames(rows):
            video = Path(row["video"])
            sec = float(row["time_sec"])
            stem = _safe_stem(video, sec)
            image_path = output / "images" / f"{stem}.jpg"
            image.save(image_path, quality=92)
            row = dict(row)
            row["image"] = str(image_path)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            written_rows.append(row)
            print(f"[cholec80-sample] {idx}/{len(rows)} {video.name}@{sec:.1f}s", flush=True)

    summary = {
        "video_dir": str(video_dir),
        "phase": args.phase,
        "videos_seen": len(videos),
        "videos_with_phase": sum(1 for n in phase_counts.values() if n > 0),
        "frames_written": sum(1 for _ in metadata_path.open("r", encoding="utf-8")) if metadata_path.exists() else 0,
        "phase_ranges_by_video": dict(phase_counts),
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_contact_sheet(output, written_rows, args.contact_sheet_max, args.contact_sheet_thumb_width)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-dir", default="/data/cholec80/cholec80/videos")
    parser.add_argument("--output", default="datasets/cholec80_clipping_samples_v1")
    parser.add_argument("--phase", default="ClippingCutting")
    parser.add_argument("--margin-sec", type=float, default=30.0)
    parser.add_argument("--interval-sec", type=float, default=4.0)
    parser.add_argument("--max-per-range", type=int, default=30)
    parser.add_argument("--max-total", type=int, default=240)
    parser.add_argument("--max-videos", type=int, default=0)
    parser.add_argument("--seed", type=int, default=43)
    parser.add_argument("--contact-sheet-max", type=int, default=60)
    parser.add_argument("--contact-sheet-thumb-width", type=int, default=320)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    sample(args)


if __name__ == "__main__":
    main()
