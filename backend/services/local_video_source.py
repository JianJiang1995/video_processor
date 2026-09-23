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
import math
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlsplit, urlunsplit

import cv2

logger = logging.getLogger(__name__)

SIMULATOR_URI = "simulator://capture-card/0"
# filesim://<abs_path> treats an arbitrary local file as a finite, realtime-paced
# capture-card style source (PacedVideoCapture, stop at EOF). Used for headless
# batch validation runs so multiple sessions can play different files in parallel.
FILESIM_PREFIX = "filesim://"
SIMULATED_CAPTURE_NAME = "手术室采集卡模拟源"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_SIMULATOR_STREAM_URL = os.getenv("SURGR1_SIMULATOR_STREAM_URL", "http://127.0.0.1:9001/stream")
INPUT_LAYOUT_FULL = "full"
INPUT_LAYOUT_QUAD_RIGHT = "quad_right"
INPUT_LAYOUT_QUERY_KEY = "input_layout"
VALID_INPUT_LAYOUTS = frozenset({INPUT_LAYOUT_FULL, INPUT_LAYOUT_QUAD_RIGHT})


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
    input_layout: str = INPUT_LAYOUT_FULL


def normalize_input_layout(layout: Optional[str]) -> str:
    normalized = str(layout or INPUT_LAYOUT_FULL).strip().lower()
    return normalized if normalized in VALID_INPUT_LAYOUTS else INPUT_LAYOUT_FULL


def file_simulator_uri(path: str) -> str:
    """Bind a simulator session to one file, independent of the global preset."""
    return "simulator://file" + quote(str(Path(path).expanduser().resolve()), safe="/")


def split_input_layout(video_source: str) -> tuple[str, str]:
    """Return a source without our layout query and its normalized layout."""
    raw = str(video_source or "")
    parts = urlsplit(raw)
    query = parse_qsl(parts.query, keep_blank_values=True)
    layout = INPUT_LAYOUT_FULL
    clean_query = []
    for key, value in query:
        if key == INPUT_LAYOUT_QUERY_KEY:
            layout = normalize_input_layout(value)
        else:
            clean_query.append((key, value))
    clean = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(clean_query), parts.fragment))
    return clean, layout


def with_input_layout(video_source: str, layout: Optional[str]) -> str:
    """Attach layout metadata to a source URI without changing the source itself."""
    clean_source, _ = split_input_layout(video_source)
    normalized = normalize_input_layout(layout)
    if normalized == INPUT_LAYOUT_FULL:
        return clean_source

    parts = urlsplit(clean_source)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.append((INPUT_LAYOUT_QUERY_KEY, normalized))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def layout_output_dimensions(width: int, height: int, layout: Optional[str]) -> tuple[int, int]:
    width = max(0, int(width or 0))
    height = max(0, int(height or 0))
    if normalize_input_layout(layout) == INPUT_LAYOUT_QUAD_RIGHT and width > 1:
        return width - (width // 4), height
    return width, height


def apply_input_layout(frame, layout: Optional[str]):
    """Crop a decoded frame according to the selected capture layout."""
    if frame is None or normalize_input_layout(layout) != INPUT_LAYOUT_QUAD_RIGHT:
        return frame
    height, width = frame.shape[:2]
    if width < 4 or height < 1:
        return frame
    # NumPy slicing is a zero-copy ROI. Downstream JPEG encoding/resizing already
    # materializes its own output, so copying the 4K crop here would only add
    # memory bandwidth and latency to every captured frame.
    return frame[:, width // 4:]


def apply_input_layout_to_image(image, layout: Optional[str]):
    """Apply the same layout crop to a PIL image used by frame endpoints."""
    if image is None or normalize_input_layout(layout) != INPUT_LAYOUT_QUAD_RIGHT:
        return image
    width, height = image.size
    if width < 4 or height < 1:
        return image
    return image.crop((width // 4, 0, width, height))


class InputLayoutCapture:
    """Transparent VideoCapture wrapper that applies one layout crop per read."""

    def __init__(self, capture, layout: Optional[str]):
        self.capture = capture
        self.input_layout = normalize_input_layout(layout)

    def read(self, *args, **kwargs):
        ret, frame = self.capture.read(*args, **kwargs)
        if ret:
            frame = apply_input_layout(frame, self.input_layout)
        return ret, frame

    def retrieve(self, *args, **kwargs):
        ret, frame = self.capture.retrieve(*args, **kwargs)
        if ret:
            frame = apply_input_layout(frame, self.input_layout)
        return ret, frame

    def get(self, prop):
        value = self.capture.get(prop)
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            width, _ = layout_output_dimensions(value, 0, self.input_layout)
            return width
        return value

    def __getattr__(self, name):
        return getattr(self.capture, name)


def wrap_capture_for_layout(capture, layout: Optional[str]):
    normalized = normalize_input_layout(layout)
    if capture is None or normalized == INPUT_LAYOUT_FULL:
        return capture
    return InputLayoutCapture(capture, normalized)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _candidate_simulator_files() -> list[Path]:
    env_path = os.getenv("SURGR1_SIMULATOR_VIDEO") or os.getenv("SURGR1_SIMULATOR_VIDEO_PATH")
    candidates = []
    if env_path:
        candidates.append(Path(env_path))

    root = _repo_root()
    candidates.extend([
        root.parent / "stream_simulator" / "media" / "vid001_capture_30fps.mp4",
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

    cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 4])
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
    original_source = str(video_source or "")
    clean_source, input_layout = split_input_layout(original_source)

    parsed_source = urlsplit(clean_source)
    if parsed_source.scheme == "simulator" and parsed_source.netloc == "file":
        backing_path = unquote(parsed_source.path)
        resolved = _probe_video(backing_path)
        if resolved:
            resolved.original = original_source
            resolved.input_layout = input_layout
            return resolved
        # Missing bound files must not silently switch to a different video.
        return ResolvedVideoSource(original=original_source, source=backing_path,
                                   is_simulator=True, input_layout=input_layout)

    if clean_source.startswith(FILESIM_PREFIX):
        backing_path = clean_source[len(FILESIM_PREFIX):]
        probed = _probe_video(backing_path)
        if probed:
            probed.original = original_source
            probed.input_layout = input_layout
            return probed
        logger.warning("[SimulatorSource] filesim backing file unavailable: %s", backing_path)
        return ResolvedVideoSource(
            original=original_source,
            source=backing_path,
            is_simulator=True,
            input_layout=input_layout,
        )

    if clean_source.startswith("simulator://"):
        # simulator:// is the capture-card abstraction used by the app. Resolve
        # it to the currently running simulator server's backing file first so
        # preview, frame capture, and analysis all use the same source.
        resolved = get_simulator_source(DEFAULT_SIMULATOR_STREAM_URL) or get_simulator_source()
        if resolved:
            resolved.original = original_source
            resolved.input_layout = input_layout
            return resolved
        return ResolvedVideoSource(
            original=original_source,
            source=clean_source,
            is_simulator=True,
            input_layout=input_layout,
        )

    parsed = urlparse(clean_source)
    if parsed.scheme in ("http", "https") and parsed.hostname in LOCAL_HOSTS:
        resolved = get_simulator_source(clean_source)
        if resolved:
            resolved.original = original_source
            resolved.input_layout = input_layout
            return resolved

    return ResolvedVideoSource(
        original=original_source,
        source=clean_source,
        input_layout=input_layout,
    )


class PacedVideoCapture:
    """Read a file on a wall-clock timeline, dropping stale frames when late."""

    def __init__(self, source: str, fps: Optional[float] = None, loop: bool = True,
                 sample_fps: Optional[float] = None):
        self.source = source
        self.cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 4])
        cap_fps = self.cap.get(cv2.CAP_PROP_FPS) if self.cap.isOpened() else 0
        self.fps = max(1.0, float(fps or cap_fps or 30.0))
        self.interval = 1.0 / self.fps
        self.sample_stride = max(1.0, self.fps / sample_fps) if sample_fps and sample_fps > 0 else 1.0
        self.loop = loop
        self._clock_start: Optional[float] = None
        self._source_start_frame: int = 0
        self._last_frame_index: int = -1
        self._last_timestamp: float = 0.0
        self._dropped_frames: int = 0

    def isOpened(self):
        return self.cap.isOpened()

    def read(self):
        now = time.perf_counter()
        next_source_frame = max(
            math.ceil(self._last_frame_index + self.sample_stride)
            if self._clock_start is not None and self._last_frame_index >= 0 else 0,
            int(self.cap.get(cv2.CAP_PROP_POS_FRAMES) or 0),
        )
        if self._clock_start is None:
            self._clock_start = now
            self._source_start_frame = next_source_frame
        else:
            due = self._clock_start + (
                (next_source_frame - self._source_start_frame) / self.fps
            )
            if now < due:
                time.sleep(due - now)
                now = time.perf_counter()

            # Saving a full-resolution frame can occasionally take longer than
            # one source interval. A capture card would keep advancing while
            # the consumer is busy, so skip source frames that are already in
            # the past instead of shifting the whole media clock backwards.
            target_frame = self._source_start_frame + int(
                max(0.0, now - self._clock_start) * self.fps
            )
            target_frame = max(next_source_frame, target_frame)
            # grab() decodes reference frames without converting every discarded
            # 4K frame to BGR. Source PTS and wall-clock speed stay unchanged.
            frames_to_skip = target_frame - int(self.cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)
            if frames_to_skip > 0:
                if frames_to_skip <= max(8, int(self.fps * 2)):
                    skipped = 0
                    while skipped < frames_to_skip and self.cap.grab():
                        skipped += 1
                else:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                    skipped = frames_to_skip
                self._dropped_frames += skipped

        ret, frame = self.cap.read()
        if not ret and self.loop:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self._clock_start = time.perf_counter()
            self._source_start_frame = 0
            self._last_frame_index = -1
            self._last_timestamp = 0.0
            ret, frame = self.cap.read()
        if ret:
            # OpenCV reports the next frame index after read(); convert it back
            # to the frame that was actually returned.
            pos = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)
            self._last_frame_index = max(0, pos - 1)
            msec = float(self.cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
            self._last_timestamp = (msec / 1000.0) if msec > 0 else (self._last_frame_index / self.fps)

        return ret, frame

    def grab(self):
        return self.cap.grab()

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return self.fps
        return self.cap.get(prop)

    def set(self, prop, value):
        if prop == cv2.CAP_PROP_POS_FRAMES:
            self._clock_start = None
            self._source_start_frame = max(0, int(value))
            self._last_frame_index = max(0, int(value) - 1)
            self._last_timestamp = max(0.0, self._last_frame_index / self.fps)
        return self.cap.set(prop, value)

    def last_timestamp(self) -> float:
        return self._last_timestamp

    def last_frame_index(self) -> int:
        return self._last_frame_index

    def dropped_frames(self) -> int:
        return self._dropped_frames

    def release(self):
        return self.cap.release()
