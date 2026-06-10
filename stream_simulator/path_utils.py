from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).resolve().parent
MEDIA_DIR = SCRIPT_DIR / "media"
UPLOAD_DIR = SCRIPT_DIR / "uploads"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".m4v"}


def _resolve_explicit_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def _resolve_config_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (SCRIPT_DIR / path).resolve()
    return path


def _first_video_in(directory: Path) -> Optional[Path]:
    if not directory.exists():
        return None

    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            return path.resolve()
    return None


def discover_default_video(config_video: Optional[str] = None) -> Optional[Path]:
    env_video = os.environ.get("STREAM_SIMULATOR_VIDEO")
    if env_video:
        path = _resolve_explicit_path(env_video)
        if path.exists():
            return path

    if config_video:
        path = _resolve_config_path(config_video)
        if path.exists():
            return path

    bundled_sample = MEDIA_DIR / "sample.mp4"
    if bundled_sample.exists():
        return bundled_sample.resolve()

    media_video = _first_video_in(MEDIA_DIR)
    if media_video:
        return media_video

    uploaded_video = _first_video_in(UPLOAD_DIR)
    if uploaded_video:
        return uploaded_video

    return None


def require_video_path(cli_video: Optional[str], config_video: Optional[str] = None) -> Path:
    if cli_video:
        path = _resolve_explicit_path(cli_video)
        if path.exists():
            return path
        raise FileNotFoundError(f"Video not found: {path}")

    discovered = discover_default_video(config_video=config_video)
    if discovered:
        return discovered

    raise FileNotFoundError(
        "No video file found. Pass --video /path/to/video.mp4, "
        "set STREAM_SIMULATOR_VIDEO, or place a sample video at "
        "stream_simulator/media/sample.mp4."
    )
