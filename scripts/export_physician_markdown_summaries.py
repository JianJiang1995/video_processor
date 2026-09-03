#!/usr/bin/env python3
"""Export one concise, timestamped physician-review Markdown per video."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.routers.analysis import (  # noqa: E402
    _build_deterministic_clinical_report,
    _normalize_summary_for_event_nodes,
)


def load_events(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("events", [])
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def export_summaries(batch_results: Path, output_dir: Path, language: str) -> list[Path]:
    artifact_dir = batch_results.parent.resolve()
    results = json.loads(batch_results.read_text(encoding="utf-8"))
    if not isinstance(results, list) or not results:
        raise ValueError(f"batch results must be a non-empty list: {batch_results}")

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for result in results:
        label = str(result["label"])
        summary_path = artifact_dir / f"{label}.summaries.json"
        event_path = artifact_dir / f"{label}.events.json"
        rows = json.loads(summary_path.read_text(encoding="utf-8"))
        records = [
            record
            for record in (_normalize_summary_for_event_nodes(row) for row in rows)
            if record and record.get("summary")
        ]
        events = load_events(event_path)
        video_title = Path(str(result.get("segment") or label)).name
        markdown = _build_deterministic_clinical_report(
            video_title=video_title,
            records=records,
            events=events,
            language=language,
        )
        target = output_dir / f"{label}_doctor_summary.md"
        target.write_text(markdown, encoding="utf-8")
        written.append(target.resolve())
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--language", default="zh")
    args = parser.parse_args()

    for path in export_summaries(
        args.batch_results.resolve(),
        args.output_dir.resolve(),
        args.language,
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
