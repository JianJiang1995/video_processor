#!/usr/bin/env python3
"""Regenerate evidence-grounded reports from complete saved timelines."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.routers.analysis import (
    _build_deterministic_clinical_report,
    _normalize_summary_for_event_nodes,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--results", default="recovered_results.json")
    parser.add_argument("--language", default="zh")
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    results_path = out_dir / args.results
    results = json.loads(results_path.read_text(encoding="utf-8"))
    for result in results:
        label = result["label"]
        summary_rows = json.loads(
            (out_dir / f"{label}.summaries.json").read_text(encoding="utf-8")
        )
        records = [
            record
            for record in (
                _normalize_summary_for_event_nodes(row) for row in summary_rows
            )
            if record and record.get("summary")
        ]
        event_payload = json.loads(
            (out_dir / f"{label}.events.json").read_text(encoding="utf-8")
        )
        events = (
            event_payload.get("events", [])
            if isinstance(event_payload, dict)
            else event_payload
        )
        video_title = Path(str(result.get("segment") or label)).name
        markdown = _build_deterministic_clinical_report(
            video_title=video_title,
            records=records,
            events=events,
            language=args.language,
        )
        report_value = result.get("report")
        report_path = (
            Path(report_value)
            if report_value
            else out_dir / "clinical_reports" / f"{video_title}_{result.get('session_id')}_{args.language}.md"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(markdown, encoding="utf-8")
        result["report"] = str(report_path.resolve())
        print(report_path.resolve())

    results_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
