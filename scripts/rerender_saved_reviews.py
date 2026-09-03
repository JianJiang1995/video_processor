#!/usr/bin/env python3
"""Rebuild event subtitles and review MP4s from saved batch artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from batch_record_analysis import event_subtitle_entries, render_review, write_srt
from validate_batch_review import validate_label


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--results", default="recovered_results.json")
    parser.add_argument("--quality", default="recovered_quality.json")
    parser.add_argument("--encoder", choices=("auto", "nvenc", "x264"), default="nvenc")
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    results = json.loads((out_dir / args.results).read_text(encoding="utf-8"))
    rendered = []
    for result in results:
        label = result["label"]
        event_payload = json.loads(
            (out_dir / f"{label}.events.json").read_text(encoding="utf-8")
        )
        events = (
            event_payload.get("events", [])
            if isinstance(event_payload, dict)
            else event_payload
        )
        summaries = json.loads((out_dir / f"{label}.summaries.json").read_text(encoding="utf-8"))
        write_srt(
            event_subtitle_entries(events, summaries),
            out_dir / f"{label}.events.srt",
        )
        rendered.append(render_review(result, out_dir, args.encoder))

    quality = [validate_label(out_dir, result) for result in rendered]
    quality_by_label = {item["label"]: item for item in quality}
    for result in rendered:
        item_quality = quality_by_label.get(result.get("label"), {})
        result["quality"] = item_quality
        if "events" in item_quality:
            result["events"] = item_quality["events"]
    (out_dir / args.results).write_text(
        json.dumps(rendered, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / args.quality).write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(quality, ensure_ascii=False, indent=2))
    if any(item.get("status") == "fail" for item in quality):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
