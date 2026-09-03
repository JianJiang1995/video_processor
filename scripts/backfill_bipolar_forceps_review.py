#!/usr/bin/env python3
"""Review saved hook/bipolar conflicts with the local blue-jaw cue."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from backend.routers.analysis import (  # noqa: E402
    _bipolar_forceps_evidence,
    _resolve_bipolar_hook_conflict,
)
from backend.services.expert_fusion import _detect_blue_bipolar_forceps  # noqa: E402
from batch_record_analysis import wrap_subtitle, write_srt  # noqa: E402


def _experts(row: dict) -> tuple[dict, dict]:
    others = row.get("others") or {}
    experts = others.get("experts") or {}
    return others, experts


def _phase(row: dict) -> str:
    return str(row.get("surgical_phase") or row.get("phase") or "")


def _candidate_indices(rows: list[dict]) -> set[int]:
    direct: set[int] = set()
    temporal: set[int] = set()
    for index, row in enumerate(rows):
        text = str(row.get("summary_text") or row.get("summary") or "")
        if "电凝钩" not in text:
            continue
        _, experts = _experts(row)
        yolo_bipolar_frames = max(
            (
                int(item.get("frames_seen") or 0)
                for item in ((experts.get("yolo") or {}).get("tools") or [])
                if isinstance(item, dict) and str(item.get("label") or "").lower() == "bipolar"
            ),
            default=0,
        )
        evidence = _bipolar_forceps_evidence(experts)
        if yolo_bipolar_frames >= 2:
            direct.add(index)
        if evidence.get("temporal_candidate"):
            temporal.add(index)

    expanded = set(direct)
    for index in temporal:
        phase = _phase(rows[index])
        if any(
            neighbor in direct and _phase(rows[neighbor]) == phase
            for neighbor in (index - 1, index + 1)
            if 0 <= neighbor < len(rows)
        ):
            expanded.add(index)
    return expanded


def _sample_candidate_windows(
    video_path: Path,
    rows: list[dict],
    candidates: set[int],
    count: int,
) -> dict[int, list[np.ndarray]]:
    """Decode chronological candidate runs instead of repeatedly seeking."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open source video: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    targets: list[tuple[int, int]] = []
    for index in sorted(candidates):
        row = rows[index]
        start = float(row.get("start_time") or 0)
        end = float(row.get("end_time") or start + 5.0)
        if end <= start:
            end = start + 5.0
        margin = min(0.15, max(0.0, (end - start) * 0.05))
        for timestamp in np.linspace(start + margin, end - margin, max(2, count)):
            targets.append((max(0, int(round(float(timestamp) * fps))), index))
    targets.sort()

    sampled: dict[int, list[np.ndarray]] = {index: [] for index in candidates}
    next_frame = -1
    last_target = -1
    try:
        for target_frame, index in targets:
            if next_frame < 0 or target_frame < next_frame or target_frame - last_target > int(fps * 8):
                cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                next_frame = target_frame
            frame = None
            while next_frame <= target_frame:
                ok, candidate = cap.read()
                if not ok:
                    frame = None
                    break
                frame = candidate
                next_frame += 1
            if frame is not None and frame.size:
                sampled[index].append(frame)
            last_target = target_frame
    finally:
        cap.release()
    return sampled


def _set_summary(row: dict, value: str) -> None:
    if "summary_text" in row:
        row["summary_text"] = value
    else:
        row["summary"] = value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--results", default="batch_results.json")
    parser.add_argument("--sample-frames", type=int, default=12)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    results = json.loads((out_dir / args.results).read_text(encoding="utf-8"))
    corrections: list[dict] = []

    for result in results:
        label = str(result.get("label") or "").strip()
        video_path = Path(str(result.get("segment") or "")).resolve()
        summaries_path = out_dir / f"{label}.summaries.json"
        if not label or not video_path.is_file() or not summaries_path.is_file():
            continue

        rows = json.loads(summaries_path.read_text(encoding="utf-8"))
        candidates = _candidate_indices(rows)
        sampled = _sample_candidate_windows(video_path, rows, candidates, args.sample_frames)
        for index in sorted(candidates):
            row = rows[index]
            frames = sampled.get(index) or []
            cue = _detect_blue_bipolar_forceps(frames)
            others, experts = _experts(row)
            experts["blue_bipolar_forceps"] = cue
            others["experts"] = experts
            row["others"] = others
            if not _bipolar_forceps_evidence(experts).get("resolved"):
                continue

            old = str(row.get("summary_text") or row.get("summary") or "")
            new = _resolve_bipolar_hook_conflict(old, experts)
            stage1_old = str(row.get("stage1_summary") or others.get("stage1_summary") or "")
            stage1_new = _resolve_bipolar_hook_conflict(stage1_old, experts)
            if new != old:
                _set_summary(row, new)
            if stage1_new != stage1_old:
                row["stage1_summary"] = stage1_new
                others["stage1_summary"] = stage1_new
            if new != old or stage1_new != stage1_old:
                others["instrument_review"] = {
                    "classification": "blue_bipolar_forceps",
                    "source": "local_blue_jaw_cue",
                    "confidence": cue.get("confidence", 0.0),
                    "frames_seen": cue.get("frames_seen", 0),
                    "frames_analyzed": cue.get("frames_analyzed", 0),
                }
                corrections.append({
                    "label": label,
                    "window_id": row.get("window_id"),
                    "old": old,
                    "new": new,
                    "cue": others["instrument_review"],
                })

        if args.dry_run:
            continue
        summaries_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        write_srt(
            [
                (
                    float(row.get("start_time") or 0),
                    float(row.get("end_time") or 0),
                    wrap_subtitle(
                        f"W{row.get('window_id')} "
                        f"{row.get('summary_text') or row.get('summary') or ''}"
                    ),
                )
                for row in rows
            ],
            out_dir / f"{label}.windows.srt",
        )

    report_path = out_dir / "bipolar_forceps_backfill.json"
    payload = {
        "source": "local_blue_jaw_cue",
        "dry_run": args.dry_run,
        "corrections": corrections,
    }
    if not args.dry_run:
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
