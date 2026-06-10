"""CLI entry for offline pipeline.

Example:
  python -m offline_pipeline.scripts.run_offline \
    --video stream_simulator/media/sample.mp4 \
    --sample-fps 10 --window 15
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from offline_pipeline.pipelines.offline_pipeline import run_pipeline


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="Path to video file (mp4/mov/avi)")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "config.json"))
    ap.add_argument("--sample-fps", type=int, default=None)
    ap.add_argument("--window", type=float, default=None, help="Window duration in seconds")
    ap.add_argument("--skip-batch", action="store_true",
                    help="Skip Gemini Batch API (experts only, for dev)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    with open(args.config) as f:
        cfg = json.load(f)
    if args.sample_fps:
        cfg["sampling"]["target_fps"] = args.sample_fps
    if args.window:
        cfg["sampling"]["window_duration_sec"] = args.window
    if args.skip_batch:
        cfg.setdefault("gemini", {})["skip_batch"] = True

    result = run_pipeline(args.video, cfg)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
