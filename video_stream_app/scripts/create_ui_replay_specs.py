#!/usr/bin/env python3
"""Create Electron offline-replay specs from batch analysis artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _artifact_path(result: dict, artifact_dir: Path, suffix: str) -> Path:
    label = str(result["label"])
    path = artifact_dir / f"{label}{suffix}"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.resolve()


def _video_path(result: dict) -> Path:
    raw = Path(str(result.get("segment") or result.get("video") or ""))
    candidates = [raw]
    if raw.is_absolute() and str(raw).startswith("/home/user/"):
        candidates.append(Path(str(raw).replace("/home/user/", "/", 1)))
    elif raw.is_absolute() and str(raw).startswith("/data/"):
        candidates.append(Path("/home/user") / str(raw).lstrip("/"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(raw)


def create_specs(batch_results: Path, output_dir: Path) -> list[Path]:
    artifact_dir = batch_results.parent.resolve()
    results = json.loads(batch_results.read_text(encoding="utf-8"))
    if not isinstance(results, list) or not results:
        raise ValueError(f"batch results must be a non-empty list: {batch_results}")

    output_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for result in results:
        label = str(result["label"])
        report_raw = result.get("report")
        report = Path(str(report_raw)).resolve() if report_raw else None
        if report and not report.is_file():
            candidate = artifact_dir / "clinical_reports" / report.name
            report = candidate.resolve() if candidate.is_file() else None

        spec = {
            "version": 1,
            "title": f"{label} 离线分析回放",
            "language": "zh",
            "video_path": str(_video_path(result)),
            "summaries_path": str(_artifact_path(result, artifact_dir, ".summaries.json")),
            "events_path": str(_artifact_path(result, artifact_dir, ".events.json")),
            "report_path": str(report) if report else None,
            "auto_play": True,
            "auto_start_delay_ms": 1200,
            "final_update_delay": 1.25,
            "session": {
                "session_id": str(result["session_id"]),
                "video_name": Path(str(result.get("segment") or label)).name,
                "duration": float(result.get("segment_duration") or result.get("covered") or 0),
                "fps": 25,
                "width": 854,
                "height": 480,
            },
        }
        target = output_dir / f"{label}.replay.json"
        target.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created.append(target.resolve())
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    for path in create_specs(args.batch_results.resolve(), args.output_dir.resolve()):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
