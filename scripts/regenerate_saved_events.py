#!/usr/bin/env python3
"""Rebuild evidence-grounded event nodes from saved window summaries."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from backend.routers.analysis import (
    _ensure_required_event_nodes,
    _event_nodes_signature,
    _fallback_event_nodes,
    _merge_visibility_status_events,
    _normalize_summary_for_event_nodes,
    _select_key_event_nodes,
)
from batch_record_analysis import event_subtitle_entries, write_srt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--results", default="batch_results.json")
    parser.add_argument("--language", default="zh")
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    results = json.loads((out_dir / args.results).read_text(encoding="utf-8"))
    rebuilt = []
    for result in results:
        label = str(result["label"])
        rows = json.loads(
            (out_dir / f"{label}.summaries.json").read_text(encoding="utf-8")
        )
        records = [
            record
            for record in (_normalize_summary_for_event_nodes(row) for row in rows)
            if record and record.get("summary")
        ]
        events = _fallback_event_nodes(
            records,
            args.language,
            reason="offline evidence-grounded regeneration",
        )
        events = _ensure_required_event_nodes(events, records, args.language)
        events = _merge_visibility_status_events(events, records, args.language)
        events = _select_key_event_nodes(events, 10)
        payload = {
            "success": True,
            "session_id": str(result.get("session_id") or ""),
            "language": args.language,
            "source": "offline-evidence-rules",
            "cached": False,
            "provider": "local",
            "model": None,
            "window_count": len(records),
            "prompt_window_count": 0,
            "signature": _event_nodes_signature(records),
            "events": events,
        }
        events_path = out_dir / f"{label}.events.json"
        events_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_srt(
            event_subtitle_entries(events, rows),
            out_dir / f"{label}.events.srt",
        )
        result["events"] = len(events)
        rebuilt.append({
            "label": label,
            "events": len(events),
            "titles": [event.get("title") for event in events],
        })

    (out_dir / args.results).write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(rebuilt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
