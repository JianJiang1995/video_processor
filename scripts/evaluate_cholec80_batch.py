#!/usr/bin/env python3
"""Compare full-video batch output with Cholec80 phase annotations."""

from __future__ import annotations

import argparse
import bisect
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


PHASES = (
    "Preparation",
    "CalotTriangleDissection",
    "ClippingCutting",
    "GallbladderDissection",
    "GallbladderPackaging",
    "CleaningCoagulation",
    "GallbladderRetraction",
)
PHASE_ALIASES = {
    "preparation": "Preparation",
    "calottriangledissection": "CalotTriangleDissection",
    "calot_triangle_dissection": "CalotTriangleDissection",
    "clippingcutting": "ClippingCutting",
    "clipping_cutting": "ClippingCutting",
    "gallbladderdissection": "GallbladderDissection",
    "gallbladder_dissection": "GallbladderDissection",
    "gallbladderpackaging": "GallbladderPackaging",
    "gallbladder_packaging": "GallbladderPackaging",
    "cleaningcoagulation": "CleaningCoagulation",
    "cleaning_coagulation": "CleaningCoagulation",
    "gallbladderretraction": "GallbladderRetraction",
    "gallbladder_retraction": "GallbladderRetraction",
}
SUMMARY_PHASE_PATTERNS = (
    (re.compile(r"标本袋牵拉取出|牵拉装有胆囊的标本袋"), "GallbladderRetraction"),
    (re.compile(r"当前处于胆囊取出与装袋|将胆囊装入标本袋"), "GallbladderPackaging"),
    (re.compile(r"胆囊装袋取出后|当前处于清洁凝血"), "CleaningCoagulation"),
    (re.compile(r"当前处于胆囊分离"), "GallbladderDissection"),
    (re.compile(r"当前处于夹闭切断"), "ClippingCutting"),
    (re.compile(r"当前处于肝胆三角解剖"), "CalotTriangleDissection"),
    (re.compile(r"当前处于准备阶段"), "Preparation"),
)


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _seconds(timestamp: str) -> float:
    hours, minutes, seconds = timestamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _annotation_transitions(path: Path) -> tuple[list[float], list[str]]:
    times: list[float] = []
    phases: list[str] = []
    last = ""
    with path.open("r", encoding="utf-8") as handle:
        next(handle, None)
        for line in handle:
            fields = line.strip().split("\t")
            if len(fields) != 2:
                continue
            phase = fields[1].strip()
            if phase == last:
                continue
            times.append(_seconds(fields[0]))
            phases.append(phase)
            last = phase
    return times, phases


def _phase_at(timestamp: float, times: list[float], phases: list[str]) -> str:
    index = bisect.bisect_right(times, timestamp) - 1
    return phases[max(0, index)] if phases else "Unknown"


def _canonical(value: Any) -> str:
    text = str(value or "").strip()
    if text in PHASES:
        return text
    key = re.sub(r"[\s_-]", "", text).lower()
    return PHASE_ALIASES.get(text.lower()) or PHASE_ALIASES.get(key, "Unknown")


def _others(row: dict) -> dict:
    value = row.get("others") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _raw_phase(row: dict) -> str:
    experts = _others(row).get("experts") or {}
    return _canonical((experts.get("phase") or {}).get("label"))


def _display_phase(row: dict, previous: str) -> str:
    persisted = _canonical(row.get("surgical_phase"))
    if persisted != "Unknown":
        return persisted
    text = str(row.get("summary") or row.get("summary_text") or row.get("glm_summary") or "")
    for pattern, phase in SUMMARY_PHASE_PATTERNS:
        if pattern.search(text):
            return phase
    return previous if previous != "Unknown" else _raw_phase(row)


def _stable_first_occurrences(rows: list[tuple[float, str]]) -> dict[str, float]:
    occurrences: dict[str, float] = {}
    for index, (start, phase) in enumerate(rows):
        if phase in occurrences:
            continue
        following = rows[index:index + 2]
        if len(following) == 2 and all(candidate == phase for _, candidate in following):
            occurrences[phase] = start
    return occurrences


def _mismatch_runs(windows: list[dict]) -> list[dict]:
    runs: list[dict] = []
    for row in windows:
        if row["expected"] == row["effective"]:
            continue
        if (
            runs
            and runs[-1]["expected"] == row["expected"]
            and runs[-1]["predicted"] == row["effective"]
            and abs(runs[-1]["end"] - row["start"]) < 0.35
        ):
            runs[-1]["end"] = row["end"]
            runs[-1]["windows"] += 1
        else:
            runs.append({
                "start": row["start"],
                "end": row["end"],
                "expected": row["expected"],
                "predicted": row["effective"],
                "windows": 1,
            })
    return runs


def evaluate_label(out_dir: Path, result: dict) -> dict:
    label = str(result.get("label") or "unknown")
    segment = Path(str(result.get("segment") or ""))
    annotation = segment.with_name(f"{segment.stem}-timestamp.txt")
    summaries = _load(out_dir / f"{label}.summaries.json", [])
    if not annotation.is_file() or not summaries:
        return {
            "label": label,
            "status": "skip",
            "reason": "full-video Cholec80 annotation or summaries unavailable",
        }

    gt_times, gt_phases = _annotation_transitions(annotation)
    previous = "Unknown"
    windows: list[dict] = []
    raw_correct = 0
    effective_correct = 0
    confusion: Counter[tuple[str, str]] = Counter()
    effective_timeline: list[tuple[float, str]] = []
    for row in sorted(summaries, key=lambda item: int(item.get("window_id") or 0)):
        start = float(row.get("start_time") or 0)
        end = float(row.get("end_time") or start)
        expected = _phase_at((start + end) / 2, gt_times, gt_phases)
        raw = _raw_phase(row)
        effective = _display_phase(row, previous)
        previous = effective
        raw_correct += int(raw == expected)
        effective_correct += int(effective == expected)
        confusion[(expected, effective)] += 1
        effective_timeline.append((start, effective))
        windows.append({
            "window_id": int(row.get("window_id") or 0),
            "start": start,
            "end": end,
            "expected": expected,
            "raw": raw,
            "effective": effective,
        })

    total = max(1, len(windows))
    predicted_transitions = _stable_first_occurrences(effective_timeline)
    gt_transition_map = dict(zip(gt_phases, gt_times))
    transition_errors = {
        phase: round(predicted_transitions[phase] - gt_transition_map[phase], 2)
        for phase in PHASES
        if phase in predicted_transitions and phase in gt_transition_map
    }
    missing_phases = [phase for phase in gt_phases if phase not in predicted_transitions]
    runs = _mismatch_runs(windows)
    longest_mismatch = max((run["end"] - run["start"] for run in runs), default=0.0)
    effective_accuracy = effective_correct / total
    status = "pass"
    failures: list[str] = []
    if effective_accuracy < 0.90:
        failures.append(f"effective phase accuracy {effective_accuracy:.3f} < 0.90")
    if longest_mismatch > 30.0:
        failures.append(f"longest phase mismatch {longest_mismatch:.1f}s > 30s")
    if missing_phases:
        failures.append(f"missing phases: {', '.join(missing_phases)}")
    if any(abs(error) > 15.0 for error in transition_errors.values()):
        failures.append("one or more stable phase transitions differ by over 15s")
    if failures:
        status = "fail"

    return {
        "label": label,
        "status": status,
        "windows": len(windows),
        "raw_phase_accuracy": round(raw_correct / total, 4),
        "effective_phase_accuracy": round(effective_accuracy, 4),
        "longest_mismatch_seconds": round(longest_mismatch, 2),
        "transition_errors_seconds": transition_errors,
        "missing_phases": missing_phases,
        "failures": failures,
        "mismatch_runs": sorted(
            runs,
            key=lambda run: run["end"] - run["start"],
            reverse=True,
        )[:12],
        "confusion": [
            {"expected": expected, "predicted": predicted, "windows": count}
            for (expected, predicted), count in sorted(confusion.items())
            if expected != predicted
        ],
    }


def _write_markdown(reports: list[dict], path: Path) -> None:
    lines = ["# Cholec80 长视频阶段验收", ""]
    for report in reports:
        lines.extend([f"## {report['label']}", ""])
        if report["status"] == "skip":
            lines.extend([f"- 状态：跳过（{report['reason']}）", ""])
            continue
        lines.extend([
            f"- 状态：{report['status']}",
            f"- 原始阶段专家准确率：{report['raw_phase_accuracy']:.2%}",
            f"- 最终展示阶段准确率：{report['effective_phase_accuracy']:.2%}",
            f"- 最长连续错配：{report['longest_mismatch_seconds']:.1f} 秒",
            f"- 转场偏差（秒）：`{json.dumps(report['transition_errors_seconds'], ensure_ascii=False)}`",
        ])
        if report["failures"]:
            lines.append(f"- 失败原因：{'；'.join(report['failures'])}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args()
    out_dir = args.out_dir.resolve()
    results = _load(out_dir / "batch_results.json", [])
    reports = [evaluate_label(out_dir, result) for result in results]
    (out_dir / "phase_evaluation.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_markdown(reports, out_dir / "phase_evaluation.md")
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 1 if any(report["status"] == "fail" for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
