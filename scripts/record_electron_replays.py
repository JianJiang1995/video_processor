#!/usr/bin/env python3
"""Record complete Electron UI replays on isolated local X displays."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import signal
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
ELECTRON = FRONTEND / "node_modules" / ".bin" / "electron"


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )


def wait_for_display(env: dict[str, str], timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            run(["xdpyinfo"], env=env, timeout=2)
            return
        except (subprocess.SubprocessError, OSError):
            time.sleep(0.2)
    raise TimeoutError(f"display did not become ready: {env['DISPLAY']}")


def wait_for_window(env: dict[str, str], timeout: float = 45) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", "Surg-R1 手术助手"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if ids:
            return ids[0]
        time.sleep(0.25)
    raise TimeoutError(f"Electron window did not become ready: {env['DISPLAY']}")


def terminate_group(process: subprocess.Popen | None) -> None:
    if not process or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=8)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def make_recording_spec(source: Path, target: Path) -> dict:
    spec = json.loads(source.read_text(encoding="utf-8"))
    spec["auto_play"] = False
    spec["auto_start_delay_ms"] = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return spec


def record_one(
    source_spec: Path,
    output_dir: Path,
    display_number: int,
    width: int,
    height: int,
    fps: int,
    encoder_gpu: int,
    frontend_url: str,
    start_at: float,
    completed_tail: float,
    output_suffix: str,
) -> dict:
    label = source_spec.name.removesuffix(".replay.json")
    display = f":{display_number}"
    output = output_dir / f"{label}{output_suffix}"
    log_path = output_dir / "logs" / f"{label}.log"
    runtime_spec = output_dir / "replay_specs" / source_spec.name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    spec = make_recording_spec(source_spec, runtime_spec)
    duration = float(spec.get("session", {}).get("duration") or spec.get("duration") or 0)
    if duration <= 0:
        raise ValueError(f"invalid duration in {source_spec}")
    replay_start = min(duration, max(0.0, start_at))
    capture_duration = duration - replay_start + max(2.0, completed_tail)

    replay_url = f"{frontend_url}/?replaySpec={urllib.parse.quote(str(runtime_spec), safe='/')}"
    if replay_start > 0:
        replay_url += f"&replayStartAt={replay_start:.3f}"

    env = os.environ.copy()
    env.update({
        "DISPLAY": display,
        "VITE_DEV_SERVER_URL": replay_url,
        "ELECTRON_OPEN_DEVTOOLS": "0",
        "ELECTRON_RECORDING_MODE": "1",
        "ELECTRON_USER_DATA_DIR": str(output_dir / "profiles" / label),
        "ELECTRON_DISABLE_SANDBOX": "1",
    })

    xvfb: subprocess.Popen | None = None
    electron: subprocess.Popen | None = None
    ffmpeg: subprocess.Popen | None = None
    started = time.time()
    try:
        xvfb = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", f"{width}x{height}x24", "-nolisten", "tcp", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        wait_for_display(env)

        log_file = log_path.open("w", encoding="utf-8")
        electron = subprocess.Popen(
            [str(ELECTRON), ".", "--dev", "--no-sandbox"],
            cwd=FRONTEND,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
        )
        window_id = wait_for_window(env)

        # Let the replay bundle, native video metadata, and first thumbnail settle.
        time.sleep(3.0)
        geometry = run(["xdotool", "getwindowgeometry", "--shell", window_id], env=env).stdout
        if f"WIDTH={width}" not in geometry or f"HEIGHT={height}" not in geometry:
            run(["xdotool", "windowsize", window_id, str(width), str(height)], env=env)
            run(["xdotool", "windowmove", window_id, "0", "0"], env=env)

        ffmpeg_command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
            "-f", "x11grab", "-draw_mouse", "0", "-framerate", str(fps),
            "-video_size", f"{width}x{height}", "-i", f"{display}.0+0,0",
            # Keep a completed-state tail so the generated Summary button and
            # final event nodes remain visible despite variable startup latency.
            "-t", f"{capture_duration:.3f}",
            "-an", "-c:v", "h264_nvenc", "-gpu", str(encoder_gpu),
            "-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "0",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
        ]
        with log_path.open("a", encoding="utf-8") as ffmpeg_log:
            ffmpeg = subprocess.Popen(
                ffmpeg_command,
                env=env,
                stdout=ffmpeg_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
            )

        time.sleep(0.15)
        # Clicking the center of the video starts the replay at source time zero.
        run(["xdotool", "mousemove", "--window", window_id, str(width // 3), str(height // 3), "click", "1"], env=env)

        ffmpeg.wait(timeout=capture_duration + 90)
        if ffmpeg.returncode != 0:
            raise RuntimeError(f"ffmpeg exited with status {ffmpeg.returncode}; see {log_path}")
        if not output.is_file() or output.stat().st_size < 1_000_000:
            raise RuntimeError(f"recording is missing or too small: {output}")

        probe = json.loads(run([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=width,height,r_frame_rate,nb_frames",
            "-of", "json", str(output),
        ], timeout=30).stdout)
        return {
            "label": label,
            "source_spec": str(source_spec),
            "output": str(output.resolve()),
            "source_duration": duration,
            "source_start": replay_start,
            "recording": probe,
            "display": display,
            "wall_seconds": round(time.time() - started, 2),
            "status": "completed",
        }
    finally:
        terminate_group(ffmpeg)
        terminate_group(electron)
        terminate_group(xvfb)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specs", type=Path, nargs="+")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--frontend-url", default="http://127.0.0.1:5133")
    parser.add_argument("--display-start", type=int, default=91)
    parser.add_argument("--width", type=int, default=2560)
    parser.add_argument("--height", type=int, default=1440)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--encoder-gpu", type=int, default=0)
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--start-at", type=float, default=0.0)
    parser.add_argument("--completed-tail", type=float, default=6.0)
    parser.add_argument("--output-suffix", default="_electron_complete.mp4")
    args = parser.parse_args()

    if not ELECTRON.is_file():
        raise FileNotFoundError(ELECTRON)
    if not shutil.which("Xvfb") or not shutil.which("ffmpeg") or not shutil.which("xdotool"):
        raise RuntimeError("Xvfb, ffmpeg, and xdotool are required")
    urllib.request.urlopen(args.frontend_url, timeout=5).read(64)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    specs = [item.resolve() for item in args.specs]
    results: list[dict] = []
    errors: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        futures = {
            pool.submit(
                record_one,
                spec,
                output_dir,
                args.display_start + index,
                args.width,
                args.height,
                args.fps,
                args.encoder_gpu,
                args.frontend_url.rstrip("/"),
                args.start_at,
                args.completed_tail,
                args.output_suffix,
            ): spec
            for index, spec in enumerate(specs)
        }
        for future in concurrent.futures.as_completed(futures):
            spec = futures[future]
            try:
                result = future.result()
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
            except Exception as exc:
                error = {"spec": str(spec), "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                errors.append(error)
                print(json.dumps(error, ensure_ascii=False), flush=True)

    manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "results": sorted(results, key=lambda item: item["label"]),
        "errors": errors,
    }
    (output_dir / "recording_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
