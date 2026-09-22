"""Reusable local preview files; analysis always opens the original source."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import threading

logger = logging.getLogger(__name__)
CACHE_DIR = Path(__file__).resolve().parents[2] / ".runtime" / "playback"
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_encoder_slot = threading.Semaphore(1)


def probe(path: str) -> dict:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height,r_frame_rate,duration:format=duration", "-of", "json", path,
    ], check=True, capture_output=True, text=True, timeout=20)
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    numerator, denominator = stream["r_frame_rate"].split("/")
    return {"width": int(stream["width"]), "height": int(stream["height"]),
            "fps": float(numerator) / max(1.0, float(denominator)),
            "duration": float(stream.get("duration") or data["format"]["duration"])}


def preview_format(info: dict) -> dict:
    scale = min(1.0, 1920 / info["width"], 1080 / info["height"])
    return {"width": max(2, int(info["width"] * scale) // 2 * 2),
            "height": max(2, int(info["height"] * scale) // 2 * 2),
            "fps": info["fps"] / max(1, math.ceil(info["fps"] / 30))}


def _build(key: str, source: str, target: Path):
    job = _jobs[key]
    partial = target.with_suffix(".partial.mp4")
    try:
        info = probe(source)
        fmt = preview_format(info)
        job.update(source=info, preview=fmt)
        if all(abs(info[k] - fmt[k]) < 0.01 for k in fmt):
            job.update(status="original", progress=1.0)
            return
        if target.exists():
            cached = probe(str(target))
            if abs(cached["duration"] - info["duration"]) < 0.15:
                job.update(status="ready", progress=1.0)
                return
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with _encoder_slot:
            fps_filter = f"fps={fmt['fps']:.8f}"
            size = f"{fmt['width']}:{fmt['height']}"
            gpu = os.environ.get("PLAYBACK_GPU", "0")
            for hardware in (True, False):
                command = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error"]
                if hardware:
                    command += ["-hwaccel", "cuda", "-hwaccel_device", gpu,
                                "-hwaccel_output_format", "cuda"]
                else:
                    command += ["-threads", "4"]
                command += ["-i", source, "-map", "0:v:0", "-an", "-sn", "-dn"]
                if hardware:
                    command += ["-vf", f"{fps_filter},scale_cuda={size}",
                                "-c:v", "h264_nvenc", "-gpu", gpu, "-preset", "p4",
                                "-rc", "vbr", "-cq", "23", "-b:v", "5M",
                                "-maxrate", "8M", "-bufsize", "16M"]
                else:
                    command += ["-vf", f"{fps_filter},scale={size}", "-c:v", "libx264",
                                "-threads", "4", "-filter_threads", "2", "-preset", "veryfast", "-crf", "22"]
                command += ["-g", str(max(1, round(fmt["fps"] * 2))), "-movflags", "+faststart",
                            "-progress", "pipe:1", str(partial)]
                with target.with_suffix(".log").open("w") as log:
                    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, text=True)
                    job["process"] = process
                    for line in process.stdout:
                        if line.startswith("out_time_us="):
                            try:
                                job["progress"] = min(0.99, int(line.split("=", 1)[1]) / 1e6 / info["duration"])
                            except ValueError:
                                pass
                    code = process.wait()
                    job.pop("process", None)
                if code == 0:
                    break
                logger.warning("Preview conversion failed (hardware=%s): %s", hardware, source)
            if code != 0:
                raise RuntimeError("preview conversion failed; see " + str(target.with_suffix(".log")))
            output = probe(str(partial))
            if abs(output["duration"] - info["duration"]) > 0.15:
                raise RuntimeError("preview duration does not match source")
            partial.replace(target)
            job.update(status="ready", progress=1.0, encoder="nvenc" if hardware else "libx264")
    except Exception as exc:
        logger.warning("Preview unavailable for %s: %s", source, exc)
        job.update(status="error", error=str(exc))
    finally:
        partial.unlink(missing_ok=True)


def request_preview(source: str) -> dict:
    path = Path(source).resolve()
    stat = path.stat()
    identity = f"v1:{path}:{stat.st_size}:{stat.st_mtime_ns}:1920:1080:30"
    key = hashlib.sha256(identity.encode()).hexdigest()[:24]
    target = CACHE_DIR / f"{key}.mp4"
    with _lock:
        if key not in _jobs:
            _jobs[key] = {"status": "preparing", "progress": 0.0, "path": str(target)}
            threading.Thread(target=_build, args=(key, str(path), target),
                             name=f"preview-{key[:6]}", daemon=True).start()
        return {k: v for k, v in _jobs[key].items() if k != "process"}
