#!/usr/bin/env python3
"""Finish events, reports, review MP4s, and validation for saved sessions."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from batch_record_analysis import finalize_analysis, render_review
from validate_batch_review import validate_label


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--encoder", choices=("auto", "nvenc", "x264"), default="nvenc")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    results = json.loads(Path(args.manifest).read_text(encoding="utf-8"))

    finalized = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = {pool.submit(finalize_analysis, result, out_dir): result["label"] for result in results}
        for future in as_completed(futures):
            finalized.append(future.result())

    rendered = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        futures = {
            pool.submit(render_review, result, out_dir, args.encoder): result["label"]
            for result in finalized
        }
        for future in as_completed(futures):
            rendered.append(future.result())

    quality = [validate_label(out_dir, result) for result in rendered]
    (out_dir / "recovered_results.json").write_text(
        json.dumps(rendered, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "recovered_quality.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"results": rendered, "quality": quality}, ensure_ascii=False, indent=2))
    if any(item.get("status") == "fail" for item in quality):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
