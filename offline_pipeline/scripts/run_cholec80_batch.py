"""Run the optimized offline pipeline on Cholec80 videos.

Typical flow:
  1. Trial on one short video, experts only:
     python -m offline_pipeline.scripts.run_cholec80_batch --trial --skip-batch --gpu 5

  2. Run ten videos after tuning:
     python -m offline_pipeline.scripts.run_cholec80_batch --limit 10 --gpu 5
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from offline_pipeline.pipelines.offline_pipeline import get_video_metadata, run_pipeline


DEFAULT_VIDEO_DIR = "/data3/tos_copy/cholec80/cholec80/videos"


def _video_number(path: Path) -> int:
    m = re.search(r"video(\d+)\.mp4$", path.name)
    return int(m.group(1)) if m else 10_000


def list_videos(video_dir: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in sorted(Path(video_dir).glob("video*.mp4"), key=_video_number):
        meta = get_video_metadata(str(path))
        items.append({
            "video_id": path.stem,
            "path": str(path),
            "duration_sec": meta["duration_sec"],
            "frames": meta["frame_count"],
            "fps": meta["fps"],
            "size_bytes": path.stat().st_size,
        })
    return items


def choose_videos(items: List[Dict[str, Any]], args) -> List[Dict[str, Any]]:
    if args.video_ids:
        wanted = {v.strip() if v.strip().startswith("video") else f"video{int(v):02d}"
                  for v in args.video_ids.split(",") if v.strip()}
        selected = [item for item in items if item["video_id"] in wanted]
        missing = sorted(wanted - {item["video_id"] for item in selected})
        if missing:
            raise RuntimeError(f"requested videos not found: {missing}")
        return selected

    ordered = list(items)
    if args.selection == "shortest":
        ordered.sort(key=lambda x: (x["duration_sec"], x["video_id"]))
    elif args.selection == "first":
        ordered.sort(key=lambda x: _video_number(Path(x["path"])))
    else:
        raise ValueError(f"unsupported selection: {args.selection}")

    limit = 1 if args.trial else args.limit
    return ordered[:limit]


def load_config(path: str, args) -> Dict[str, Any]:
    with open(path) as f:
        cfg = json.load(f)

    cfg.setdefault("sampling", {})
    cfg.setdefault("extraction", {})
    cfg.setdefault("gemini", {})

    if args.sample_fps is not None:
        cfg["sampling"]["target_fps"] = args.sample_fps
    if args.window is not None:
        cfg["sampling"]["window_duration_sec"] = args.window
    if args.frames_per_window is not None:
        cfg["sampling"]["frames_per_window_for_gemini"] = args.frames_per_window
    if args.max_windows is not None:
        cfg["sampling"]["max_windows"] = args.max_windows
    if args.max_duration is not None:
        cfg["extraction"]["max_duration_sec"] = args.max_duration
    if args.start_time is not None:
        cfg["extraction"]["start_time_sec"] = args.start_time
    if args.resize_max_width is not None:
        cfg["extraction"]["resize_max_width"] = args.resize_max_width
    if args.skip_batch:
        cfg["gemini"]["skip_batch"] = True

    if args.gpu is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu)
        for expert in cfg.get("experts", {}).values():
            if isinstance(expert, dict) and "device" in expert:
                expert["device"] = "cuda:0"
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-dir", default=DEFAULT_VIDEO_DIR)
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "config.json"))
    ap.add_argument("--trial", action="store_true", help="Run only one selected video")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--video-ids", default=None,
                    help="Comma-separated ids, e.g. 01,02,video78")
    ap.add_argument("--selection", choices=["shortest", "first"], default="shortest")
    ap.add_argument("--sample-fps", type=float, default=0.5)
    ap.add_argument("--window", type=float, default=30.0)
    ap.add_argument("--frames-per-window", type=int, default=3)
    ap.add_argument("--max-duration", type=float, default=None)
    ap.add_argument("--start-time", type=float, default=None)
    ap.add_argument("--max-windows", type=int, default=None)
    ap.add_argument("--resize-max-width", type=int, default=960)
    ap.add_argument("--gpu", type=int, default=5)
    ap.add_argument("--skip-batch", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    items = list_videos(args.video_dir)
    selected = choose_videos(items, args)
    cfg = load_config(args.config, args)

    manifest = {
        "created_at": time.time(),
        "video_dir": args.video_dir,
        "selection": args.selection,
        "trial": args.trial,
        "limit": args.limit,
        "config": {
            "sample_fps": cfg["sampling"]["target_fps"],
            "window_sec": cfg["sampling"]["window_duration_sec"],
            "frames_per_window": cfg["sampling"]["frames_per_window_for_gemini"],
            "max_duration_sec": cfg["extraction"].get("max_duration_sec"),
            "max_windows": cfg["sampling"].get("max_windows"),
            "resize_max_width": cfg["extraction"].get("resize_max_width"),
            "gpu": args.gpu,
            "skip_batch": cfg.get("gemini", {}).get("skip_batch", False),
        },
        "selected": selected,
        "results": [],
    }

    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return

    run_root = Path(cfg["storage"]["sessions_root"]) / f"cholec80_{time.strftime('%Y%m%d_%H%M%S')}"
    run_root.mkdir(parents=True, exist_ok=True)
    manifest_path = run_root / "manifest.json"

    for item in selected:
        per_cfg = json.loads(json.dumps(cfg))
        per_cfg["storage"]["sessions_root"] = str(run_root / item["video_id"])
        logging.info("[cholec80] running %s duration=%.1fs", item["video_id"], item["duration_sec"])
        t0 = time.perf_counter()
        try:
            result = run_pipeline(item["path"], per_cfg)
            result["video_id"] = item["video_id"]
            result["elapsed_sec"] = time.perf_counter() - t0
            manifest["results"].append(result)
        except Exception as exc:
            logging.exception("[cholec80] failed %s", item["video_id"])
            manifest["results"].append({
                "video_id": item["video_id"],
                "path": item["path"],
                "error": str(exc),
                "elapsed_sec": time.perf_counter() - t0,
            })
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    print(json.dumps({
        "run_root": str(run_root),
        "manifest": str(manifest_path),
        "videos": len(selected),
        "completed": sum(1 for r in manifest["results"] if "error" not in r),
        "failed": sum(1 for r in manifest["results"] if "error" in r),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
