"""Synthesize an mp4 from a CholecT50 frame folder.

CholecT50 frames are already 1-FPS sampled, so we encode at 1 FPS.
That way the offline pipeline (sample_fps=1) reads every frame 1:1.
"""
import argparse
import logging
from pathlib import Path

import cv2

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("build_cholect50_video")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vid-dir", required=True,
                    help="e.g. /data3/tos_copy/CholecT50/CholecT50/videos/VID01")
    ap.add_argument("--out", required=True, help="output mp4 path")
    ap.add_argument("--fps", type=int, default=1,
                    help="encoded fps (CholecT50 is 1-fps sampled)")
    args = ap.parse_args()

    vid_dir = Path(args.vid_dir)
    frames = sorted(vid_dir.glob("*.png"))
    if not frames:
        raise RuntimeError(f"no .png frames in {vid_dir}")
    first = cv2.imread(str(frames[0]))
    h, w = first.shape[:2]
    logger.info("encoding %d frames @ %d fps → %s (%dx%d)",
                len(frames), args.fps, args.out, w, h)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(args.out, fourcc, args.fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError(f"cannot open writer for {args.out}")
    for i, fp in enumerate(frames):
        img = cv2.imread(str(fp))
        if img is None:
            logger.warning("skipping unreadable %s", fp)
            continue
        writer.write(img)
        if (i + 1) % 200 == 0:
            logger.info("  encoded %d/%d", i + 1, len(frames))
    writer.release()
    logger.info("done: %s", args.out)


if __name__ == "__main__":
    main()
