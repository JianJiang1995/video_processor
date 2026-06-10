"""CLI entry for offline pipeline.

Example:
  python -m offline_pipeline.scripts.run_offline \
    --video stream_simulator/media/sample.mp4 \
    --gpu 5 --sample-fps 0.5 --window 30
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from offline_pipeline.pipelines.offline_pipeline import run_pipeline


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="Path to video file (mp4/mov/avi)")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "config.json"))
    ap.add_argument("--sample-fps", type=float, default=None)
    ap.add_argument("--window", type=float, default=None, help="Window duration in seconds")
    ap.add_argument("--frames-per-window", type=int, default=None,
                    help="Representative frames sent to Gemini per window")
    ap.add_argument("--max-duration", type=float, default=None,
                    help="Analyze only the first N seconds after --start-time")
    ap.add_argument("--start-time", type=float, default=None,
                    help="Start analysis at this timestamp in seconds")
    ap.add_argument("--max-windows", type=int, default=None,
                    help="Analyze only first N windows after sampling")
    ap.add_argument("--resize-max-width", type=int, default=None,
                    help="Resize extracted frames to this max width before experts")
    ap.add_argument("--gpu", type=int, default=None,
                    help="Physical GPU index to expose as cuda:0")
    ap.add_argument("--skip-batch", action="store_true",
                    help="Skip Gemini Batch API (experts only, for dev)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.gpu is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu)

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
    if args.frames_per_window:
        cfg["sampling"]["frames_per_window_for_gemini"] = args.frames_per_window
    if args.max_windows:
        cfg["sampling"]["max_windows"] = args.max_windows
    if args.max_duration:
        cfg.setdefault("extraction", {})["max_duration_sec"] = args.max_duration
    if args.start_time is not None:
        cfg.setdefault("extraction", {})["start_time_sec"] = args.start_time
    if args.resize_max_width:
        cfg.setdefault("extraction", {})["resize_max_width"] = args.resize_max_width
    if args.skip_batch:
        cfg.setdefault("gemini", {})["skip_batch"] = True
    if args.gpu is not None:
        for expert in cfg.get("experts", {}).values():
            if isinstance(expert, dict) and "device" in expert:
                expert["device"] = "cuda:0"

    result = run_pipeline(args.video, cfg)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
