#!/usr/bin/env python3
"""Audit a completed Electron verification run without changing saved analysis."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path


def distribution(values):
    values = sorted(float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    if not values:
        return {"count": 0}

    def percentile(fraction):
        position = (len(values) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        return round(values[lower] + (values[upper] - values[lower]) * (position - lower), 3)

    return {"count": len(values), "p50": percentile(.5), "p95": percentile(.95),
            "max": round(values[-1], 3)}


def audit(record):
    rows = sorted(record.get("rows") or [], key=lambda row: row["window_id"])
    if not rows:
        raise ValueError("Verification contains no windows")
    gaps, r1_time_errors, transitions, revisits = [], [], [], []
    seen_phases = set()
    crop, sizes, available, statuses = Counter(), Counter(), Counter(), Counter()
    r1_queue, r1_latency = [], []
    previous_end = 0
    for expected_id, row in enumerate(rows):
        wid = row["window_id"]
        start, end = row["start_time"], row["end_time"]
        if wid != expected_id or abs(start - previous_end) > .001 or end <= start:
            gaps.append({"window_id": wid, "start": start, "end": end, "prior_end": previous_end})
        previous_end = end
        others = row.get("others") or {}
        experts, timing = others.get("experts") or {}, others.get("timing") or {}
        crop[str(timing.get("analysis_crop_right_fraction"))] += 1
        sizes.update({"x".join(map(str, size)) for size in timing.get("expert_input_sizes", [])})
        available.update(experts.get("available") or [])
        r1 = experts.get("surgr1") or {}
        statuses[str(r1.get("status", "missing"))] += 1
        r1_queue.append(r1.get("queue_ms"))
        r1_latency.append(r1.get("latency_ms"))
        for observation in r1.get("observations") or []:
            timestamp = observation.get("timestamp")
            if not isinstance(timestamp, (int, float)) or not start <= timestamp < end:
                r1_time_errors.append({"window_id": wid, "timestamp": timestamp})
        phase = row.get("surgical_phase")
        if not transitions or transitions[-1]["phase"] != phase:
            change = {"ui_window": wid + 1, "start": start, "phase": phase}
            transitions.append(change)
            if phase in seen_phases:
                revisits.append(change)
            seen_phases.add(phase)

    # Use capture wall time for the primary latency statistic, including EOF.
    # Playback-time lag is separate: its frozen EOF clock would undercount.
    wall_lags, playback_lags = {1: [], 2: []}, {1: [], 2: []}
    draft_parts = {key: [] for key in ('window_poll_ms', 'image_load_ms', 'expert_ms',
                                      'save_and_push_ms', 'phase_ms', 'triplet_ms', 'yolo_ms',
                                      'clip_detector_ms', 'cpu_cues_ms')}
    draft_startup, draft_steady = [], []
    draft_over_five = []
    seen = set()
    for receipt in sorted(record.get("receipts") or [], key=lambda row: row["received_wall"]):
        stage = receipt.get("stage")
        key = receipt.get("window_id"), stage
        if stage not in wall_lags or key in seen:
            continue
        seen.add(key)
        source_wall = (receipt.get("timing") or {}).get("source_window_end_wall")
        if isinstance(source_wall, (int, float)):
            lag = receipt["received_wall"] - source_wall
            wall_lags[stage].append(lag)
            if stage == 1:
                (draft_startup if receipt['window_id'] < 5 else draft_steady).append(lag)
                if lag > 5:
                    draft_over_five.append({'ui_window': receipt['window_id'] + 1,
                                            'lag_seconds': round(lag, 3)})
        if stage == 1:
            timing = receipt.get('timing') or {}
            for name in ('image_load_ms', 'expert_ms'):
                draft_parts[name].append(timing.get(name))
            for name, duration in (timing.get('expert_components_ms') or {}).items():
                if name in draft_parts:
                    draft_parts[name].append(duration)
            ready, saved = timing.get('window_ready_wall'), timing.get('stage1_ready_to_save_wall')
            if isinstance(ready, (int, float)) and isinstance(source_wall, (int, float)):
                draft_parts['window_poll_ms'].append((ready - source_wall) * 1000)
            if isinstance(saved, (int, float)):
                draft_parts['save_and_push_ms'].append((receipt['received_wall'] - saved) * 1000)
        position = receipt.get("video_time")
        if isinstance(position, (int, float)) and position < previous_end - .15:
            playback_lags[stage].append(position - receipt["end"])
    return {
        "session_id": record["session_id"], "source": record.get("source"),
        "scope": "Software and data-flow checks, not clinical accuracy validation",
        "verification_passed": record.get("passed"), "wall_seconds": record.get("wall_seconds"),
        "window_count": len(rows), "last_window_end": previous_end,
        "ui_time": (record.get("ui") or {}).get("time"),
        "ui_window_count": len((record.get("ui") or {}).get("summaries") or []),
        "window_gaps": gaps, "r1_timestamp_errors": r1_time_errors,
        "page_errors": record.get("page_errors"), "recording": record.get("recording"),
        "crop_window_counts": dict(crop), "input_size_window_counts": dict(sizes),
        "expert_window_counts": dict(available), "r1_status_counts": dict(statuses),
        "r1_queue_ms": distribution(r1_queue), "r1_latency_ms": distribution(r1_latency),
        "phase_transitions": transitions, "phase_revisits_for_review": revisits,
        "capture_window_end_to_ui_seconds": {str(s): distribution(v) for s, v in wall_lags.items()},
        "draft_first_five_windows_seconds": distribution(draft_startup),
        "draft_remaining_windows_seconds": distribution(draft_steady),
        "draft_over_five_seconds": draft_over_five,
        "draft_component_milliseconds": {name: distribution(values) for name, values in draft_parts.items()},
        "moving_playback_clock_lag_seconds": {str(s): distribution(v) for s, v in playback_lags.items()},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("verification", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(json.loads(args.verification.read_text(encoding="utf-8")))
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    output = args.output or args.verification.with_name("quality_audit.json")
    output.write_text(text, encoding="utf-8")
    print(text, end="")
