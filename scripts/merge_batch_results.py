#!/usr/bin/env python3
"""Merge recovered and newly completed batch results, then validate all items."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validate_batch_review import validate_label


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--output", default="batch_results.json")
    parser.add_argument("--quality", default="quality_report.json")
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    by_label = {}
    order = []
    for filename in args.input:
        path = out_dir / filename
        if not path.exists():
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        for row in rows:
            label = row.get("label")
            if not label:
                continue
            if label not in by_label:
                order.append(label)
            by_label[label] = row

    results = [by_label[label] for label in order]
    quality = [validate_label(out_dir, result) for result in results]
    quality_by_label = {item["label"]: item for item in quality}
    for result in results:
        item_quality = quality_by_label.get(result.get("label"), {})
        result["quality"] = item_quality
        if "events" in item_quality:
            result["events"] = item_quality["events"]
    (out_dir / args.output).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / args.quality).write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"results": results, "quality": quality}, ensure_ascii=False, indent=2))
    if any(item.get("status") == "fail" for item in quality):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
