"""
Local video source helpers for capture-card simulation.

The simulator should behave like a realtime capture card to the application,
but it should not force the local process chain through an HTTP MJPEG
decode/re-encode path. These helpers resolve simulator:// sources to their
backing local video file and pace reads at the source FPS.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import cv2

logger = logging.getLogger(__name__)

SIMULATOR_URI = "simulator://capture-card/0"
SIMULATED_CAPTURE_NAME = "手术室采集卡模拟源"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass
class ResolvedVideoSource:
    original: str
    source: str
    is_simulator: bool = False
    fps: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    total_frames: Optional[int] = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _candidate_simulator_files() -> list[Path]:
    env_path = os.getenv("SURGR1_SIMULATOR_VIDEO") or os.getenv("SURGR1_SIMULATOR_VIDEO_PATH")
    candidates = []
    if env_path:
        candidates.append(Path(env_path))

    root = _repo_root()
    candidates.extend([
        root.parent / "stream_simulator" / "media" / "stable_capture_30fps.mp4",
        root.parent / "stream_simulator" / "media" / "sample_long_cfr30.mp4",
        root.parent / "stream_simulator" / "media" / "sample_long.mp4",
        root.parent / "stream_simulator" / "media" / "sample.mp4",
        root.parent / "test_data" / "cholec02_0028.mp4",
    ])
    return candidates


def _probe_video(path: str, fallback_fps: float = 30.0) -> Optional[ResolvedVideoSource]:
    if not path or not os.path.exists(path):
        return None

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        cap.release()
        return None

    fps = float(cap.get(cv2.CAP_PROP_FPS) or fallback_fps or 30.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = (total_frames / fps) if fps > 0 and total_frames > 0 else 0.0
    cap.release()
    return ResolvedVideoSource(
        original=path,
        source=path,
        is_simulator=True,
        fps=fps,
        width=width,
        height=height,
        duration=duration,
        total_frames=total_frames,
    )


def _simulator_info_from_http(stream_url: str, timeout: float = 0.5) -> Optional[dict]:
    parsed = urlparse(stream_url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in LOCAL_HOSTS or not parsed.netloc:
        return None

    try:
        info_url = f"{parsed.scheme}://{parsed.netloc}/info"
        with urllib.request.urlopen(info_url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        logger.debug("[SimulatorSource] HTTP /info unavailable: %s", exc)
        return None


def get_simulator_source(stream_url: Optional[str] = None) -> Optional[ResolvedVideoSource]:
    """Return metadata for the local capture-card simulator backing file."""
    if stream_url:
        info = _simulator_info_from_http(stream_url)
        if info:
            probed = _probe_video(str(info.get("video_path") or ""), float(info.get("fps") or 30.0))
            if probed:
                probed.fps = float(info.get("fps") or probed.fps or 30.0)
                probed.width = int(info.get("width") or probed.width or 0)
                probed.height = int(info.get("height") or probed.height or 0)
                return probed

    for candidate in _candidate_simulator_files():
        probed = _probe_video(str(candidate))
        if probed:
            return probed

    return None


def resolve_video_source(video_source: str) -> ResolvedVideoSource:
    """Resolve simulator sources to local files; leave all other sources intact."""
    if video_source.startswith("simulator://"):
        resolved = get_simulator_source()
        if resolved:
            resolved.original = video_source
            return resolved
        return ResolvedVideoSource(original=video_source, source=video_source, is_simulator=True)

    parsed = urlparse(video_source)
    if parsed.scheme in ("http", "https") and parsed.hostname in LOCAL_HOSTS:
        resolved = get_simulator_source(video_source)
        if resolved:
            resolved.original = video_source
            return resolved

    return ResolvedVideoSource(original=video_source, source=video_source)


class PacedVideoCapture:
    """Small cv2.VideoCapture wrapper that reads a file at realtime FPS."""

    def __init__(self, source: str, fps: Optional[float] = None, loop: bool = True):
        self.source = source
        self.cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
        cap_fps = self.cap.get(cv2.CAP_PROP_FPS) if self.cap.isOpened() else 0
        self.fps = max(1.0, float(fps or cap_fps or 30.0))
        self.interval = 1.0 / self.fps
        self.loop = loop
        self._next_read: Optional[float] = None
        self._last_frame_index: int = -1
        self._last_timestamp: float = 0.0

    def isOpened(self):
        return self.cap.isOpened()

    def read(self):
        now = time.perf_counter()
        if self._next_read is None:
            self._next_read = now
        elif now < self._next_read:
            time.sleep(self._next_read - now)

        ret, frame = self.cap.read()
        if not ret and self.loop:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self._next_read = time.perf_counter() + self.interval
            ret, frame = self.cap.read()
        if ret:
            # OpenCV reports the next frame index after read(); convert it back
            # to the frame that was actually returned.
            pos = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)
            self._last_frame_index = max(0, pos - 1)
            msec = float(self.cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
            self._last_timestamp = (msec / 1000.0) if msec > 0 else (self._last_frame_index / self.fps)

        base = self._next_read if self._next_read is not None else time.perf_counter()
        self._next_read = max(base + self.interval, time.perf_counter())
        return ret, frame

    def grab(self):
        return self.cap.grab()

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return self.fps
        return self.cap.get(prop)

    def set(self, prop, value):
        if prop == cv2.CAP_PROP_POS_FRAMES:
            self._next_read = None
            self._last_frame_index = max(0, int(value) - 1)
            self._last_timestamp = max(0.0, self._last_frame_index / self.fps)
        return self.cap.set(prop, value)

    def last_timestamp(self) -> float:
        return self._last_timestamp

    def last_frame_index(self) -> int:
        return self._last_frame_index

    def release(self):
        return self.cap.release()
