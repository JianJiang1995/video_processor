#!/usr/bin/env python3
"""Normalize saved realtime telemetry and build one batch manifest/report."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


def video_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def latency_stats(values: list[float]) -> dict:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"count": 0, "mean": None, "p50": None, "p95": None, "max": None}

    def percentile(ratio: float) -> float:
        position = (len(ordered) - 1) * ratio
        lower = int(position)
        upper = min(len(ordered) - 1, lower + 1)
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    return {
        "count": len(ordered),
        "mean": round(sum(ordered) / len(ordered), 3),
        "p50": round(percentile(0.50), 3),
        "p95": round(percentile(0.95), 3),
        "max": round(ordered[-1], 3),
    }


def video_path_for_label(label: str, video_dir: Path) -> Path:
    match = re.search(r"video(\d+)", label, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"cannot derive video filename from label: {label}")
    path = video_dir / f"video{match.group(1)}.mp4"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.resolve()


def normalize_one(out_dir: Path, label: str, video_dir: Path) -> dict:
    summaries_path = out_dir / f"{label}.summaries.json"
    telemetry_path = out_dir / f"{label}.realtime_telemetry.json"
    summaries = json.loads(summaries_path.read_text(encoding="utf-8"))
    telemetry = json.loads(telemetry_path.read_text(encoding="utf-8"))
    rows = sorted(summaries, key=lambda row: int(row.get("window_id") or 0))
    window_rows = sorted(
        telemetry.get("windows") or [],
        key=lambda row: int(row.get("window_id") or 0),
    )
    source = video_path_for_label(label, video_dir)
    duration = video_duration(source)
    trimmed_tiny_tail_ids = []
    while len(rows) > 1:
        tail_span = float(rows[-1].get("end_time") or 0) - float(rows[-1].get("start_time") or 0)
        previous_end = float(rows[-2].get("end_time") or 0)
        if tail_span > 0.25 or duration - previous_end > 0.25:
            break
        trimmed_tiny_tail_ids.append(int(rows[-1].get("window_id") or 0))
        rows.pop()
    if trimmed_tiny_tail_ids:
        summaries_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        window_rows = [
            row
            for row in window_rows
            if int(row.get("window_id") or 0) not in set(trimmed_tiny_tail_ids)
        ]
        telemetry["trimmed_tiny_tail_window_ids"] = sorted(trimmed_tiny_tail_ids)
    by_id = {int(row.get("window_id") or 0): row for row in window_rows}
    observed_ids = [int(row.get("window_id") or 0) for row in rows]
    expected_ids = list(range(observed_ids[0], observed_ids[-1] + 1))
    missing = sorted(set(expected_ids) - set(observed_ids))
    final_id = observed_ids[-1]
    final_window = by_id.get(final_id) or {}
    target_coverage = max(float(row.get("end_time") or 0) for row in rows)
    coverage_elapsed = float(
        final_window.get("stage1_seen_elapsed")
        or telemetry.get("coverage_reached_elapsed_seconds")
        or 0
    )
    settled_elapsed = float(
        telemetry.get("analysis_settled_elapsed_seconds") or coverage_elapsed
    )
    vlm_completed = sum(
        1
        for window_id in expected_ids
        if by_id.get(window_id, {}).get("vlm_seen_elapsed") is not None
    )
    telemetry["stage1_latency_seconds"] = latency_stats([
        row["stage1_latency_seconds"]
        for row in window_rows
        if row.get("stage1_latency_seconds") is not None
    ])
    telemetry["vlm_latency_seconds"] = latency_stats([
        row["vlm_latency_seconds"]
        for row in window_rows
        if row.get("vlm_latency_seconds") is not None
    ])
    telemetry["special_review_windows"] = {
        key: sum(
            1
            for row in window_rows
            if (row.get("special_reviews") or {}).get(key)
        )
        for key in ("clip", "scissors", "visibility")
    }
    stage1_p95 = (telemetry.get("stage1_latency_seconds") or {}).get("p95")
    vlm_p95 = (telemetry.get("vlm_latency_seconds") or {}).get("p95")
    factor = coverage_elapsed / target_coverage if target_coverage else 0.0
    refinements_settled = bool(telemetry.get("refinements_settled"))
    target = {
        "max_stage1_p95_seconds": 5.0,
        "max_vlm_refinement_p95_seconds": 15.0,
        "max_coverage_lag_seconds": 5.0,
        "max_realtime_factor": 1.08,
        "stage1_met": bool(stage1_p95 is not None and float(stage1_p95) <= 5.0),
        "vlm_refinement_met": bool(vlm_p95 is not None and float(vlm_p95) <= 15.0),
    }
    target["met"] = bool(
        refinements_settled
        and not missing
        and vlm_completed == len(expected_ids)
        and target["stage1_met"]
        and target["vlm_refinement_met"]
        and coverage_elapsed - target_coverage <= target["max_coverage_lag_seconds"]
        and factor <= target["max_realtime_factor"]
    )
    telemetry.update({
        "target_coverage_seconds": round(target_coverage, 3),
        "coverage_reached_elapsed_seconds": round(coverage_elapsed, 3),
        "realtime_factor_to_coverage": round(factor, 4),
        "coverage_lag_seconds": round(coverage_elapsed - target_coverage, 3),
        "refinement_drain_seconds": round(max(0.0, settled_elapsed - coverage_elapsed), 3),
        "expected_windows": len(expected_ids),
        "observed_windows": len(observed_ids),
        "missing_window_ids": missing,
        "vlm_completed_windows": vlm_completed,
        "vlm_completion_rate": round(vlm_completed / len(expected_ids), 4),
        "realtime_target": target,
    })
    telemetry_path.write_text(
        json.dumps(telemetry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report = out_dir / "clinical_reports" / f"{label}_doctor_summary.md"
    return {
        "label": label,
        "session_id": str(telemetry.get("session_id") or ""),
        "segment": str(source),
        "segment_duration": round(duration, 3),
        "covered": round(target_coverage, 3),
        "windows": len(rows),
        "analysis_wall_seconds": round(settled_elapsed, 3),
        "realtime_factor": round(factor, 4),
        "telemetry": str(telemetry_path.resolve()),
        "telemetry_summary": {
            key: value for key, value in telemetry.items() if key != "windows"
        },
        "events": 0,
        "review": None,
        "report": str(report.resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--video-dir", type=Path, required=True)
    parser.add_argument("--labels", nargs="+", required=True)
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    results = [
        normalize_one(out_dir, label, args.video_dir.resolve())
        for label in args.labels
    ]
    (out_dir / "batch_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    metrics = []
    for result in results:
        telemetry = result["telemetry_summary"]
        metrics.append({
            "label": result["label"],
            "duration_seconds": result["segment_duration"],
            "windows": result["windows"],
            "stage1_p95_seconds": telemetry["stage1_latency_seconds"]["p95"],
            "stage1_max_seconds": telemetry["stage1_latency_seconds"]["max"],
            "vlm_p95_seconds": telemetry["vlm_latency_seconds"]["p95"],
            "vlm_max_seconds": telemetry["vlm_latency_seconds"]["max"],
            "coverage_lag_seconds": telemetry["coverage_lag_seconds"],
            "realtime_factor": telemetry["realtime_factor_to_coverage"],
            "target_met": telemetry["realtime_target"]["met"],
        })
    report_payload = {
        "videos": metrics,
        "all_targets_met": all(item["target_met"] for item in metrics),
        "targets": {
            "stage1_p95_seconds": 5.0,
            "vlm_p95_seconds": 15.0,
            "coverage_lag_seconds": 5.0,
            "realtime_factor": 1.08,
        },
    }
    (out_dir / "realtime_stability_report.json").write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# 四视频实时性与稳定性测试",
        "",
        "| 视频 | 时长 | 窗口 | Stage 1 p95 | VLM p95 | 覆盖延迟 | 实时倍率 | 通过 |",
        "|---|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for item in metrics:
        lines.append(
            f"| {item['label']} | {item['duration_seconds']:.2f}s | {item['windows']} | "
            f"{item['stage1_p95_seconds']:.3f}s | {item['vlm_p95_seconds']:.3f}s | "
            f"{item['coverage_lag_seconds']:.3f}s | {item['realtime_factor']:.4f}x | "
            f"{'是' if item['target_met'] else '否'} |"
        )
    lines += [
        "",
        "判定阈值：Stage 1 p95 <= 5 秒，VLM p95 <= 15 秒，最终覆盖延迟 <= 5 秒，完整覆盖倍率 <= 1.08x，且无缺失窗口。",
    ]
    (out_dir / "REALTIME_STABILITY_REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report_payload, ensure_ascii=False, indent=2))
    return 0 if report_payload["all_targets_met"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
