#!/usr/bin/env python3
"""Headless parallel analysis + review-video recorder.

For each requested video (or segment), this script:
  1. optionally cuts the segment with ffmpeg
  2. loads it as a paced (filesim://) session via POST /api/video/load?paced=true
  3. starts continuous expert analysis + window summarization (same endpoints
     the Electron UI uses)
  4. waits until window summaries cover the segment, then stops the session
  5. exports summaries/event nodes/clinical report and burns the window
     summaries + event titles into a review MP4 (the "recording")

Multiple items run in parallel (each paced session plays realtime), so several
long videos can be validated at once without the Electron UI.

Usage:
  python scripts/batch_record_analysis.py --spec spec.json [--parallel 4]

spec.json: [{"video": "/abs/path.mp4", "start": 613, "duration": 600,
             "label": "video01_clipping"}, ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import statistics
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from validate_batch_review import validate_label

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(threadName)s] %(message)s")
logger = logging.getLogger("batch_record")

BACKEND = "http://127.0.0.1:8001"
SUBTITLE_STYLE = (
    "FontName=Noto Sans CJK SC,FontSize=19,PrimaryColour=&H00FFFFFF,"
    "BackColour=&H78000000,OutlineColour=&H78000000,BorderStyle=3,"
    "Outline=1,Shadow=0,MarginL=32,MarginR=32,MarginV=26,Alignment=2"
)
EVENT_STYLE = (
    "FontName=Noto Sans CJK SC,FontSize=15,PrimaryColour=&H0000D7FF,"
    "BackColour=&H70000000,OutlineColour=&H70000000,BorderStyle=3,"
    "Outline=1,Shadow=0,Alignment=6,MarginL=24,MarginR=24,MarginV=20"
)


def _srt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(entries, path: Path):
    lines = []
    for i, (start, end, text) in enumerate(entries, 1):
        lines.append(str(i))
        lines.append(f"{_srt_ts(start)} --> {_srt_ts(end)}")
        lines.append(text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def event_subtitle_entries(events: list[dict], summaries: list[dict]) -> list[tuple[float, float, str]]:
    """Render merged events only over their evidence windows.

    An event can intentionally combine non-contiguous windows, for example
    repeated scissors sightings or intermittent fog. Using the event's broad
    start/end range would display a warning during unaffected windows.
    """
    by_window = {int(row.get("window_id") or 0): row for row in summaries}
    type_labels = {
        "action": "关键操作",
        "cvs": "CVS",
        "phase": "手术阶段",
        "visibility": "视野状态",
        "bleeding": "出血状态",
        "safety": "安全事件",
    }
    severity_labels = {
        "critical": "警告",
        "important": "重要",
        "normal": "常规",
        "resolved": "已解除",
        "safety": "安全",
    }
    entries: list[tuple[float, float, str]] = []
    for event in events:
        event_type = str(event.get("type") or "").strip().lower()
        severity = str(event.get("severity") or "").strip().lower()
        type_label = type_labels.get(event_type, "关键事件")
        severity_label = severity_labels.get(severity, "")
        label = f"{type_label}·{severity_label}" if severity_label else type_label
        text = f"[{label}] {event.get('title') or ''}"
        window_ids = sorted({
            int(window_id) for window_id in (event.get("window_ids") or [])
            if int(window_id) in by_window
        })
        groups: list[list[int]] = []
        for window_id in window_ids:
            if groups and window_id == groups[-1][-1] + 1:
                groups[-1].append(window_id)
            else:
                groups.append([window_id])
        if groups:
            for group in groups:
                start = float(by_window[group[0]].get("start_time") or 0)
                end = float(by_window[group[-1]].get("end_time") or start)
                if end > start:
                    entries.append((start, end, text))
            continue
        start = float(event.get("start_time") or 0)
        end = float(event.get("end_time") or start)
        if end > start:
            entries.append((start, end, text))
    return entries


def wrap_subtitle(text: str, width: int = 32, max_lines: int = 3) -> str:
    """Wrap CJK-heavy summaries so libass does not clip one long glyph run."""
    compact = " ".join(str(text or "").split())
    lines = textwrap.wrap(
        compact,
        width=max(12, width),
        break_long_words=True,
        break_on_hyphens=False,
        replace_whitespace=False,
    )
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip("，。；; ") + "…"
    return "\n".join(lines)


def api(method: str, path: str, **kwargs):
    resp = requests.request(method, f"{BACKEND}{path}", timeout=kwargs.pop("timeout", 60), **kwargs)
    resp.raise_for_status()
    return resp.json()


def cut_segment(video: Path, start: float, duration: float, out: Path) -> Path:
    if start <= 0 and duration <= 0:
        return video
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if start > 0:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(video)]
    if duration > 0:
        cmd += ["-t", str(duration)]
    # Re-encode so the segment starts on a clean keyframe and timestamps are exact.
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(out)]
    subprocess.run(cmd, check=True)
    return out


def fetch_summaries(session_id: str):
    data = api("GET", f"/api/analysis/summaries/{session_id}")
    if isinstance(data, dict):
        data = data.get("summaries", [])
    return sorted(data, key=lambda s: s.get("window_id") or 0)


def fetch_summary_telemetry(session_id: str) -> list[dict]:
    data = api("GET", f"/api/analysis/summary-telemetry/{session_id}")
    return sorted(data.get("windows") or [], key=lambda row: int(row.get("window_id") or 0))


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * min(1.0, max(0.0, quantile))
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def latency_stats(values: list[float]) -> dict:
    clean = [max(0.0, float(value)) for value in values]
    if not clean:
        return {"count": 0, "mean": None, "p50": None, "p95": None, "max": None}
    return {
        "count": len(clean),
        "mean": round(statistics.fmean(clean), 3),
        "p50": round(percentile(clean, 0.50) or 0.0, 3),
        "p95": round(percentile(clean, 0.95) or 0.0, 3),
        "max": round(max(clean), 3),
    }


def wait_for_refinements(
    session_id: str,
    timeout: float = 300.0,
    stable_seconds: float = 2.0,
    poll_callback=None,
):
    """Wait until local VLM patches finish and the exported summary set is stable."""
    deadline = time.time() + timeout
    previous_signature = ""
    stable_since = None
    latest = []
    while time.time() < deadline:
        latest = fetch_summaries(session_id)
        if poll_callback is not None:
            try:
                poll_callback()
            except Exception as exc:
                logger.warning("refinement telemetry callback failed: %s", exc)
        status = api("GET", f"/api/analysis/pending-refinements/{session_id}")
        signature = hashlib.sha1(
            json.dumps(latest, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        if int(status.get("pending") or 0) == 0 and not status.get("analysis_running", False):
            if signature == previous_signature:
                stable_since = stable_since or time.time()
                if time.time() - stable_since >= stable_seconds:
                    return latest, True
            else:
                stable_since = time.time()
        else:
            stable_since = None
        previous_signature = signature
        time.sleep(1)
    return latest, False


def run_item(item: dict, out_dir: Path, keep_session_running_extra: float = 30.0) -> dict:
    label = item["label"]
    threading.current_thread().name = label
    video = Path(item["video"]).resolve()
    start = float(item.get("start") or 0)
    duration = float(item.get("duration") or 0)

    seg_path = video
    if start > 0 or duration > 0:
        seg_path = out_dir / "sources" / f"{label}.mp4"
        logger.info("cutting segment %ss+%ss from %s", start, duration, video.name)
        cut_segment(video, start, duration, seg_path)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(seg_path)],
        capture_output=True, text=True, check=True)
    seg_duration = float(probe.stdout.strip())
    logger.info("segment ready: %s (%.1fs)", seg_path.name, seg_duration)

    loaded = api("POST", "/api/video/load", params={"video_path": str(seg_path), "paced": "true"})
    session_id = loaded["session_id"]
    logger.info("session %s loaded (paced)", session_id)

    analysis_started_monotonic = time.monotonic()
    analysis_started_epoch = time.time()
    api("POST", f"/api/analysis/start-surgr1-continuous/{session_id}", params={"enable_sam3": "false"})
    api("POST", "/api/analysis/start-glm-summarization", json={
        "session_id": session_id,
        "use_chinese": True,
        "use_glm_multimodal": False,
        "is_live": True,
    })
    logger.info("session %s analysis started, waiting ~%.0fs (realtime paced)", session_id, seg_duration)

    window_seconds = 5.0
    full_windows = int(seg_duration // window_seconds)
    tail_seconds = max(0.0, seg_duration - full_windows * window_seconds)
    has_tail_window = tail_seconds > 0.25
    expected_windows = full_windows + int(has_tail_window)
    target_coverage = seg_duration if has_tail_window else full_windows * window_seconds
    deadline = time.time() + seg_duration * 1.3 + 240
    covered = 0.0
    analysis_ended_at = None
    coverage_reached_elapsed = None
    telemetry_by_window: dict[int, dict] = {}
    backlog_samples: list[float] = []
    peak_pending_refinements = 0
    telemetry_poll_failures = 0
    next_status_poll = 0.0
    continuous_status = {"is_running": True}

    def observe_telemetry(rows: list[dict], elapsed: float) -> None:
        nonlocal covered
        for row in rows:
            window_id = int(row.get("window_id") or 0)
            end_time = float(row.get("end_time") or 0)
            covered = max(covered, end_time)
            entry = telemetry_by_window.setdefault(window_id, {
                "window_id": window_id,
                "start_time": float(row.get("start_time") or 0),
                "end_time": end_time,
                "phase": row.get("phase") or "Unknown",
                "stage1_seen_elapsed": None,
                "stage1_latency_seconds": None,
                "vlm_seen_elapsed": None,
                "vlm_latency_seconds": None,
                "vlm_call_count": 0,
                "special_reviews": {"clip": False, "scissors": False, "visibility": False},
            })
            if entry["stage1_seen_elapsed"] is None:
                entry["stage1_seen_elapsed"] = round(elapsed, 3)
                entry["stage1_latency_seconds"] = round(max(0.0, elapsed - end_time), 3)
            entry["phase"] = row.get("phase") or entry["phase"]
            entry["vlm_call_count"] = max(
                int(entry.get("vlm_call_count") or 0),
                int(row.get("vlm_call_count") or 0),
            )
            reviews = row.get("special_reviews") or {}
            for review_type in entry["special_reviews"]:
                entry["special_reviews"][review_type] = bool(
                    entry["special_reviews"][review_type] or reviews.get(review_type)
                )
            if row.get("vlm_complete") and entry["vlm_seen_elapsed"] is None:
                entry["vlm_seen_elapsed"] = round(elapsed, 3)
                entry["vlm_latency_seconds"] = round(max(0.0, elapsed - end_time), 3)

    while time.time() < deadline:
        time.sleep(0.5)
        elapsed = time.monotonic() - analysis_started_monotonic
        try:
            observe_telemetry(fetch_summary_telemetry(session_id), elapsed)
        except Exception as exc:
            telemetry_poll_failures += 1
            logger.warning("telemetry poll failed: %s", exc)
            continue
        expected_ready_coverage = math.floor(max(0.0, elapsed - 0.25) / window_seconds) * window_seconds
        backlog_samples.append(max(0.0, expected_ready_coverage - covered))
        if elapsed >= next_status_poll:
            continuous_status = api("GET", f"/api/analysis/surgr1-continuous-status/{session_id}")
            pending_status = api("GET", f"/api/analysis/pending-refinements/{session_id}")
            peak_pending_refinements = max(
                peak_pending_refinements,
                int(pending_status.get("pending") or 0),
            )
            next_status_poll = elapsed + 1.0
        if covered >= target_coverage - 0.25:
            coverage_reached_elapsed = elapsed
            logger.info("coverage complete: %.0f/%.0fs", covered, target_coverage)
            break
        if not continuous_status.get("is_running"):
            if analysis_ended_at is None:
                analysis_ended_at = time.time()
                logger.info(
                    "continuous analysis ended at coverage %.0f/%.0fs; waiting up to %.0fs for final windows",
                    covered,
                    target_coverage,
                    keep_session_running_extra,
                )
            elif time.time() - analysis_ended_at >= keep_session_running_extra:
                break

    # End capture first. The GLM task then drains any remaining full windows
    # and exits naturally; local VLM patches are allowed to finish as well.
    api("POST", f"/api/analysis/stop-surgr1-continuous/{session_id}")
    _, refinements_settled = wait_for_refinements(
        session_id,
        poll_callback=lambda: observe_telemetry(
            fetch_summary_telemetry(session_id),
            time.monotonic() - analysis_started_monotonic,
        ),
    )
    logger.info("local VLM refinements settled=%s", refinements_settled)

    final_elapsed = time.monotonic() - analysis_started_monotonic
    try:
        observe_telemetry(fetch_summary_telemetry(session_id), final_elapsed)
    except Exception as exc:
        telemetry_poll_failures += 1
        logger.warning("final telemetry poll failed: %s", exc)

    api("POST", f"/api/analysis/stop-analysis/{session_id}")

    summaries = fetch_summaries(session_id)
    covered = max((float(s.get("end_time") or 0) for s in summaries), default=0.0)
    (out_dir / f"{label}.summaries.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")

    expected_ids = set(range(expected_windows))
    observed_ids = set(telemetry_by_window)
    ordered_telemetry = [telemetry_by_window[key] for key in sorted(telemetry_by_window)]
    stage1_latencies = [
        float(row["stage1_latency_seconds"])
        for row in ordered_telemetry
        if row.get("stage1_latency_seconds") is not None
    ]
    vlm_latencies = [
        float(row["vlm_latency_seconds"])
        for row in ordered_telemetry
        if row.get("vlm_latency_seconds") is not None
    ]
    specialist_counts = {
        review_type: sum(
            1 for row in ordered_telemetry
            if (row.get("special_reviews") or {}).get(review_type)
        )
        for review_type in ("clip", "scissors", "visibility")
    }
    stage1_p95 = percentile(stage1_latencies, 0.95)
    vlm_p95 = percentile(vlm_latencies, 0.95)
    telemetry_summary = {
        "label": label,
        "session_id": session_id,
        "analysis_started_epoch": round(analysis_started_epoch, 3),
        "segment_duration_seconds": round(seg_duration, 3),
        "target_coverage_seconds": round(target_coverage, 3),
        "coverage_reached_elapsed_seconds": round(coverage_reached_elapsed, 3) if coverage_reached_elapsed is not None else None,
        "analysis_settled_elapsed_seconds": round(final_elapsed, 3),
        "realtime_factor_to_coverage": round(coverage_reached_elapsed / target_coverage, 4) if coverage_reached_elapsed is not None and target_coverage else None,
        "coverage_lag_seconds": round(max(0.0, coverage_reached_elapsed - target_coverage), 3) if coverage_reached_elapsed is not None else None,
        "refinement_drain_seconds": round(max(0.0, final_elapsed - (coverage_reached_elapsed or final_elapsed)), 3),
        "expected_windows": expected_windows,
        "observed_windows": len(observed_ids),
        "missing_window_ids": sorted(expected_ids - observed_ids),
        "vlm_completed_windows": len(vlm_latencies),
        "vlm_completion_rate": round(len(vlm_latencies) / expected_windows, 4) if expected_windows else 1.0,
        "stage1_latency_seconds": latency_stats(stage1_latencies),
        "vlm_latency_seconds": latency_stats(vlm_latencies),
        "analysis_backlog_seconds": latency_stats(backlog_samples),
        "peak_pending_refinements": peak_pending_refinements,
        "telemetry_poll_failures": telemetry_poll_failures,
        "special_review_windows": specialist_counts,
        "refinements_settled": refinements_settled,
        "realtime_target": {
            "max_stage1_p95_seconds": 5.0,
            "max_vlm_refinement_p95_seconds": 15.0,
            "max_realtime_factor": 1.08,
            "stage1_met": bool(stage1_p95 is not None and stage1_p95 <= 5.0),
            "vlm_refinement_met": bool(vlm_p95 is not None and vlm_p95 <= 15.0),
            "met": bool(
                refinements_settled
                and not (expected_ids - observed_ids)
                and len(vlm_latencies) == expected_windows
                and stage1_p95 is not None
                and stage1_p95 <= 5.0
                and vlm_p95 is not None
                and vlm_p95 <= 15.0
                and coverage_reached_elapsed is not None
                and coverage_reached_elapsed / target_coverage <= 1.08
            ),
        },
        "windows": ordered_telemetry,
    }
    telemetry_path = out_dir / f"{label}.realtime_telemetry.json"
    telemetry_path.write_text(
        json.dumps(telemetry_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not refinements_settled:
        raise RuntimeError(f"local VLM refinements did not settle for {label}")
    if covered < target_coverage - 0.25:
        raise RuntimeError(
            f"incomplete analysis coverage for {label}: {covered:.1f}/{target_coverage:.1f}s"
        )

    # Prepare window subtitles now. Event/report generation and rendering are
    # deferred until every realtime analysis has finished.
    win_srt = out_dir / f"{label}.windows.srt"
    write_srt(
        [(float(s.get("start_time") or 0), float(s.get("end_time") or 0),
          wrap_subtitle(f"W{s.get('window_id')} {s.get('summary_text') or s.get('summary') or ''}"))
         for s in summaries],
        win_srt)
    return {
        "label": label,
        "session_id": session_id,
        "segment": str(seg_path),
        "segment_duration": seg_duration,
        "covered": covered,
        "windows": len(summaries),
        "analysis_wall_seconds": round(final_elapsed, 3),
        "realtime_factor": telemetry_summary["realtime_factor_to_coverage"],
        "telemetry": str(telemetry_path),
        "telemetry_summary": {key: value for key, value in telemetry_summary.items() if key != "windows"},
        "events": 0,
        "review": None,
    }


def finalize_analysis(result: dict, out_dir: Path) -> dict:
    """Generate event nodes and one clinical report after realtime runs end."""
    if result.get("error"):
        return result
    result = dict(result)
    label = result["label"]
    session_id = result["session_id"]
    events = []
    try:
        nodes = api(
            "POST",
            f"/api/analysis/event-nodes/{session_id}",
            json={"language": "zh", "force": True, "max_windows": 100, "timeout": 120},
            timeout=180,
        )
        events = nodes.get("events") or []
        (out_dir / f"{label}.events.json").write_text(
            json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("event nodes failed: %s", exc)

    try:
        report_dir = out_dir / "clinical_reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report = api(
            "POST",
            f"/api/analysis/clinical-summary/{session_id}",
            json={"language": "zh", "force": True, "output_dir": str(report_dir)},
            timeout=600,
        )
        result["report"] = report.get("output_path")
    except Exception as exc:
        logger.warning("clinical summary failed: %s", exc)

    ev_srt = out_dir / f"{label}.events.srt"
    ev_entries = event_subtitle_entries(events, fetch_summaries(session_id))
    if ev_entries:
        write_srt(ev_entries, ev_srt)
    elif ev_srt.exists():
        ev_srt.unlink()
    result["events"] = len(events)
    logger.info("post-processing complete: %s (%d events)", label, len(events))
    return result


def _nvenc_available() -> bool:
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "h264_nvenc" in (probe.stdout or "")


def render_review(result: dict, out_dir: Path, encoder: str) -> dict:
    if result.get("error"):
        return result
    label = result["label"]
    source = Path(result["segment"])
    win_srt = out_dir / f"{label}.windows.srt"
    ev_srt = out_dir / f"{label}.events.srt"
    filters = f"subtitles={win_srt}:force_style='{SUBTITLE_STYLE}'"
    if ev_srt.exists() and ev_srt.stat().st_size > 0:
        filters += f",subtitles={ev_srt}:force_style='{EVENT_STYLE}'"

    review_path = out_dir / f"{label}_review.mp4"
    use_nvenc = encoder == "nvenc" or (encoder == "auto" and _nvenc_available())
    actual_encoder = "h264_nvenc" if use_nvenc else "libx264"
    codec_args = (
        ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23", "-b:v", "0"]
        if use_nvenc
        else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(source),
        "-vf", filters, *codec_args, "-an", str(review_path),
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        if not use_nvenc:
            raise
        logger.warning("NVENC render failed for %s; retrying with libx264", label)
        actual_encoder = "libx264"
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(source),
            "-vf", filters, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-an", str(review_path),
        ], check=True)

    result = dict(result)
    result["review"] = str(review_path)
    result["render_encoder"] = actual_encoder
    logger.info("review video written: %s", review_path)
    return result


def build_realtime_stability_report(results: list[dict], batch_wall_seconds: float) -> dict:
    per_video = []
    all_stage1_latencies: list[float] = []
    all_vlm_latencies: list[float] = []
    total_expected = 0
    total_observed = 0
    total_vlm_completed = 0
    errors = []
    for result in sorted(results, key=lambda item: str(item.get("label") or "")):
        if result.get("error"):
            errors.append({"label": result.get("label"), "error": result.get("error")})
            continue
        telemetry_path = Path(str(result.get("telemetry") or ""))
        if not telemetry_path.is_file():
            errors.append({"label": result.get("label"), "error": "telemetry file missing"})
            continue
        telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))
        windows = telemetry.get("windows") or []
        all_stage1_latencies.extend(
            float(row["stage1_latency_seconds"])
            for row in windows
            if row.get("stage1_latency_seconds") is not None
        )
        all_vlm_latencies.extend(
            float(row["vlm_latency_seconds"])
            for row in windows
            if row.get("vlm_latency_seconds") is not None
        )
        total_expected += int(telemetry.get("expected_windows") or 0)
        total_observed += int(telemetry.get("observed_windows") or 0)
        total_vlm_completed += int(telemetry.get("vlm_completed_windows") or 0)
        per_video.append({
            key: telemetry.get(key)
            for key in (
                "label",
                "session_id",
                "segment_duration_seconds",
                "coverage_reached_elapsed_seconds",
                "analysis_settled_elapsed_seconds",
                "realtime_factor_to_coverage",
                "coverage_lag_seconds",
                "refinement_drain_seconds",
                "expected_windows",
                "observed_windows",
                "missing_window_ids",
                "vlm_completed_windows",
                "vlm_completion_rate",
                "stage1_latency_seconds",
                "vlm_latency_seconds",
                "analysis_backlog_seconds",
                "peak_pending_refinements",
                "telemetry_poll_failures",
                "special_review_windows",
                "refinements_settled",
                "realtime_target",
            )
        })

    aggregate_vlm = latency_stats(all_vlm_latencies)
    aggregate_stage1 = latency_stats(all_stage1_latencies)
    max_factor = max(
        (float(row.get("realtime_factor_to_coverage") or 0) for row in per_video),
        default=0.0,
    )
    all_complete = bool(per_video) and all(
        not row.get("missing_window_ids")
        and row.get("refinements_settled")
        and int(row.get("vlm_completed_windows") or 0) == int(row.get("expected_windows") or 0)
        for row in per_video
    )
    realtime_met = bool(
        all_complete
        and aggregate_stage1.get("p95") is not None
        and float(aggregate_stage1["p95"]) <= 5.0
        and aggregate_vlm.get("p95") is not None
        and float(aggregate_vlm["p95"]) <= 15.0
        and max_factor <= 1.08
        and not errors
    )
    return {
        "status": "pass" if realtime_met else "needs_review",
        "definition": {
            "stage1_latency_seconds": "wall-clock observation time minus source window end",
            "vlm_latency_seconds": "wall-clock time when local VLM patch is first observed minus source window end",
            "realtime_factor": "wall time to full source coverage divided by source duration",
            "target": "Stage 1 p95 <= 5s, VLM refinement p95 <= 15s, realtime factor <= 1.08, no missing windows, all refinements settled",
            "poll_resolution_seconds": 0.5,
        },
        "batch_wall_seconds": round(batch_wall_seconds, 3),
        "videos_completed": len(per_video),
        "videos_failed": len(errors),
        "expected_windows": total_expected,
        "observed_windows": total_observed,
        "vlm_completed_windows": total_vlm_completed,
        "vlm_completion_rate": round(total_vlm_completed / total_expected, 4) if total_expected else 0.0,
        "stage1_latency_seconds": aggregate_stage1,
        "vlm_latency_seconds": aggregate_vlm,
        "max_realtime_factor": round(max_factor, 4),
        "realtime_target_met": realtime_met,
        "errors": errors,
        "per_video": per_video,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, help="JSON spec file")
    parser.add_argument("--out-dir", default="recordings/batch_reviews")
    parser.add_argument("--parallel", type=int, default=2,
                        help="realtime sessions; 2 keeps per-window local-VLM refinement within capacity")
    parser.add_argument("--postprocess-workers", type=int, default=1,
                        help="event/report workers started only after all realtime analyses finish")
    parser.add_argument("--render-workers", type=int, default=1,
                        help="review-video encoders started after analysis completes")
    parser.add_argument("--encoder", choices=("auto", "nvenc", "x264"), default="auto")
    parser.add_argument("--analysis-only", action="store_true",
                        help="save summaries/events/reports/SRT without rendering review MP4 files")
    parser.add_argument("--skip-quality-gate", action="store_true",
                        help="write quality findings without failing the batch")
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text())
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    batch_started_monotonic = time.monotonic()
    results = []
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(run_item, item, out_dir): item["label"] for item in spec}
        for fut in as_completed(futures):
            label = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:
                logger.error("item %s failed: %s", label, exc, exc_info=True)
                results.append({"label": label, "error": str(exc)})

    logger.info("all realtime analyses finished; generating events/reports with %d worker(s)",
                max(1, args.postprocess_workers))
    finalized = []
    with ThreadPoolExecutor(max_workers=max(1, args.postprocess_workers)) as pool:
        futures = {
            pool.submit(finalize_analysis, result, out_dir): result.get("label", "unknown")
            for result in results
        }
        for fut in as_completed(futures):
            label = futures[fut]
            try:
                finalized.append(fut.result())
            except Exception as exc:
                logger.error("post-process %s failed: %s", label, exc, exc_info=True)
                finalized.append({"label": label, "error": f"post-processing failed: {exc}"})
    results = finalized

    if not args.analysis_only:
        logger.info("post-processing finished; rendering %d review videos with %d worker(s)",
                    len(results), max(1, args.render_workers))
        rendered = []
        with ThreadPoolExecutor(max_workers=max(1, args.render_workers)) as pool:
            futures = {
                pool.submit(render_review, result, out_dir, args.encoder): result.get("label", "unknown")
                for result in results
            }
            for fut in as_completed(futures):
                label = futures[fut]
                try:
                    rendered.append(fut.result())
                except Exception as exc:
                    logger.error("render %s failed: %s", label, exc, exc_info=True)
                    rendered.append({"label": label, "error": f"render failed: {exc}"})
        results = rendered

    quality_reports = []
    for result in results:
        quality = validate_label(out_dir, result)
        result["quality"] = quality
        quality_reports.append(quality)
    (out_dir / "quality_report.json").write_text(
        json.dumps(quality_reports, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "batch_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    stability_report = build_realtime_stability_report(
        results,
        time.monotonic() - batch_started_monotonic,
    )
    (out_dir / "realtime_stability_report.json").write_text(
        json.dumps(stability_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if not args.skip_quality_gate and any(report["status"] == "fail" for report in quality_reports):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
