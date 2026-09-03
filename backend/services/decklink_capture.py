"""
Low-latency DeckLink capture backed by one shared GStreamer pipeline per URI.

Display, frame storage, and analysis readers all consume the latest frame from
the same pipeline. This prevents several processes from opening one DeckLink
input and prevents slow consumers from building a realtime backlog.
"""

import logging
import math
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse

import cv2
import numpy as np

logger = logging.getLogger(__name__)


_GST_READY = False
Gst = None
_STATES = {}
_STATES_LOCK = threading.Lock()

DECKLINK_MODES = frozenset({
    "auto",
    "ntsc",
    "ntsc2398",
    "pal",
    "ntsc-p",
    "pal-p",
    "1080p2398",
    "1080p24",
    "1080p25",
    "1080p2997",
    "1080p30",
    "1080i50",
    "1080i5994",
    "1080i60",
    "1080p50",
    "1080p5994",
    "1080p60",
    "720p50",
    "720p5994",
    "720p60",
    "1556p2398",
    "1556p24",
    "1556p25",
    "2kdcip2398",
    "2kdcip24",
    "2kdcip25",
    "2kdcip2997",
    "2kdcip30",
    "2kdcip50",
    "2kdcip5994",
    "2kdcip60",
    "2160p2398",
    "2160p24",
    "2160p25",
    "2160p2997",
    "2160p30",
    "2160p50",
    "2160p5994",
    "2160p60",
    "ntsc-widescreen",
    "ntsc2398-widescreen",
    "pal-widescreen",
    "ntsc-p-widescreen",
    "pal-p-widescreen",
})
DECKLINK_CONNECTIONS = frozenset({"auto", "hdmi", "sdi", "optical-sdi"})


def _ensure_gst():
    global _GST_READY, Gst
    if _GST_READY:
        return

    # The project venv does not expose Debian's PyGObject package by default.
    if "/usr/lib/python3/dist-packages" not in sys.path:
        sys.path.append("/usr/lib/python3/dist-packages")

    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst as _Gst

    _Gst.init(None)
    Gst = _Gst
    _GST_READY = True


def _validated_choice(value: str, allowed, fallback: str, field: str) -> str:
    normalized = str(value or fallback).strip().lower()
    if normalized not in allowed:
        raise ValueError(f"Unsupported DeckLink {field}: {normalized}")
    return normalized


def parse_decklink_uri(uri: str):
    parsed = urlparse(uri)
    if parsed.scheme and parsed.scheme != "decklink":
        raise ValueError(f"Unsupported capture URI scheme: {parsed.scheme}")

    device_spec = parsed.netloc or parsed.path.lstrip("/") or "0"
    try:
        device_number = max(0, int(device_spec))
    except ValueError as exc:
        raise ValueError(f"Invalid DeckLink device number: {device_spec}") from exc

    query = parse_qs(parsed.query)
    mode = _validated_choice((query.get("mode") or ["auto"])[0], DECKLINK_MODES, "auto", "mode")
    connection = _validated_choice(
        (query.get("connection") or ["auto"])[0],
        DECKLINK_CONNECTIONS,
        "auto",
        "connection",
    )
    return device_number, mode, connection


def build_decklink_uri(device_number: int = 0, mode: str = "auto", connection: str = "auto") -> str:
    safe_mode = _validated_choice(mode, DECKLINK_MODES, "auto", "mode")
    safe_connection = _validated_choice(connection, DECKLINK_CONNECTIONS, "auto", "connection")
    return f"decklink://{max(0, int(device_number))}?{urlencode({'mode': safe_mode, 'connection': safe_connection})}"


def _fps_from_mode(mode: str) -> float:
    if mode == "auto":
        return 30.0
    if mode.endswith(("p5994", "i5994")):
        return 60000.0 / 1001.0
    if mode.endswith(("p2997", "i2997")):
        return 30000.0 / 1001.0
    if mode.endswith(("p2398", "i2398")) or mode in {"ntsc2398", "ntsc2398-widescreen"}:
        return 24000.0 / 1001.0
    if mode.endswith(("p60", "i60")) or mode in {"ntsc", "ntsc-widescreen", "ntsc-p", "ntsc-p-widescreen"}:
        return 60.0
    if mode.endswith(("p50", "i50")) or mode in {"pal", "pal-widescreen", "pal-p", "pal-p-widescreen"}:
        return 50.0
    if mode.endswith(("p30", "i30")):
        return 30.0
    if mode.endswith("p25"):
        return 25.0
    if mode.endswith("p24"):
        return 24.0
    return 30.0


def _fraction_to_float(value) -> float:
    numerator = getattr(value, "num", getattr(value, "numerator", 0))
    denominator = getattr(value, "denom", getattr(value, "denominator", 0))
    if numerator and denominator:
        return float(numerator) / float(denominator)
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def build_pipeline_description(device_number: int, mode: str, connection: str) -> str:
    safe_mode = _validated_choice(mode, DECKLINK_MODES, "auto", "mode")
    safe_connection = _validated_choice(connection, DECKLINK_CONNECTIONS, "auto", "connection")
    return (
        "decklinkvideosrc name=source "
        f"device-number={max(0, int(device_number))} connection={safe_connection} mode={safe_mode} "
        "buffer-size=2 drop-no-signal-frames=true "
        "! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 "
        "! deinterlace mode=auto method=linear fields=all "
        "! videoconvert n-threads=4 "
        "! video/x-raw,format=BGR "
        "! appsink name=sink emit-signals=false sync=false max-buffers=1 drop=true"
    )


class _DeckLinkSharedState:
    _PULL_TIMEOUT_NS = 250_000_000
    _NO_FRAME_RESTART_SECONDS = 8.0
    _IDLE_GRACE_SECONDS = 5.0

    def __init__(self, uri: str):
        _ensure_gst()
        self.uri = uri
        self.device_number, self.mode, self.connection = parse_decklink_uri(uri)
        self.fps = _fps_from_mode(self.mode)
        self.width = 0
        self.height = 0
        self.interlace_mode = "unknown"
        self.latest_frame = None
        self.sequence = 0
        self.running = False
        self.opened = False
        self.signal = False
        self.phase = "idle"
        self.last_error = ""
        self.last_frame_monotonic = 0.0
        self.near_black = False
        self.near_black_since = 0.0
        self.restart_count = 0
        self.ref_count = 0
        self.idle_deadline = 0.0
        self.pipeline = None
        self.source = None
        self.sink = None
        self.thread = None
        self.condition = threading.Condition()

    def start(self):
        with self.condition:
            self.ref_count += 1
            self.idle_deadline = 0.0
            if not self.running:
                self.running = True
                self.phase = "starting"
                self.thread = threading.Thread(
                    target=self._run,
                    name=f"decklink-{self.device_number}-{self.connection}-{self.mode}",
                    daemon=True,
                )
                self.thread.start()

        deadline = time.monotonic() + 3.0
        with self.condition:
            while self.running and not self.opened and time.monotonic() < deadline:
                self.condition.wait(timeout=0.05)

    def release(self):
        with self.condition:
            self.ref_count = max(0, self.ref_count - 1)
            if self.ref_count == 0:
                # The connection probe releases immediately before display and
                # analysis readers attach. Keep the hardware pipeline warm so
                # that handoff cannot race a GStreamer teardown/reopen.
                self.idle_deadline = time.monotonic() + self._IDLE_GRACE_SECONDS
                self.condition.notify_all()

    def read_after(self, last_sequence: int, timeout: float = 2.0):
        end_time = time.monotonic() + max(0.0, float(timeout))
        with self.condition:
            while self.running and self.sequence == last_sequence:
                remaining = end_time - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(timeout=remaining)

            if self.latest_frame is None or self.sequence == last_sequence:
                return False, None, last_sequence

            return True, self.latest_frame.copy(), self.sequence

    def status(self):
        with self.condition:
            frame_age = None
            if self.last_frame_monotonic:
                frame_age = max(0.0, time.monotonic() - self.last_frame_monotonic)
            return {
                "uri": self.uri,
                "device_number": self.device_number,
                "mode": self.mode,
                "connection": self.connection,
                "phase": self.phase,
                "opened": self.opened,
                "signal": self.signal,
                "width": self.width,
                "height": self.height,
                "fps": round(float(self.fps), 3),
                "interlace_mode": self.interlace_mode,
                "sequence": self.sequence,
                "last_frame_age": round(frame_age, 3) if frame_age is not None else None,
                "near_black": self.near_black,
                "last_error": self.last_error,
                "restart_count": self.restart_count,
                "readers": self.ref_count,
            }

    def _is_running(self) -> bool:
        with self.condition:
            if (
                self.running
                and self.ref_count == 0
                and self.idle_deadline
                and time.monotonic() >= self.idle_deadline
            ):
                self.running = False
                self.condition.notify_all()
            return self.running

    def wait_for_shutdown(self, timeout: float = 1.0):
        thread = self.thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))

    def _set_status(self, **values):
        with self.condition:
            for key, value in values.items():
                setattr(self, key, value)
            self.condition.notify_all()

    def _read_source_signal(self) -> bool:
        if self.source is None:
            return False
        try:
            return bool(self.source.get_property("signal"))
        except Exception:
            return False

    def _consume_bus_message(self):
        if self.pipeline is None:
            return None
        bus = self.pipeline.get_bus()
        message = bus.timed_pop_filtered(0, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if message is None:
            return None
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            detail = str(error)
            if debug:
                logger.debug("[DeckLink] GStreamer debug: %s", debug)
            return detail
        return "DeckLink pipeline reached end of stream"

    def _update_caps(self, structure):
        width = int(structure.get_value("width"))
        height = int(structure.get_value("height"))
        fps = _fraction_to_float(structure.get_value("framerate")) if structure.has_field("framerate") else 0.0
        interlace_mode = (
            str(structure.get_value("interlace-mode"))
            if structure.has_field("interlace-mode")
            else "progressive"
        )
        with self.condition:
            self.width = width
            self.height = height
            if fps > 0:
                self.fps = fps
            self.interlace_mode = interlace_mode

    def _update_black_level(self, frame, now: float):
        # Sample sparsely; this status is diagnostic and should not add latency.
        if self.sequence % 15 != 0:
            return
        sample = frame[::16, ::16]
        is_near_black = float(sample.mean()) < 3.0 and float(sample.std()) < 3.0
        with self.condition:
            if is_near_black:
                if not self.near_black_since:
                    self.near_black_since = now
                self.near_black = now - self.near_black_since >= 2.0
            else:
                self.near_black = False
                self.near_black_since = 0.0

    def _run_pipeline_once(self):
        pipeline_desc = build_pipeline_description(self.device_number, self.mode, self.connection)
        started_at = time.monotonic()
        last_sample_at = started_at

        try:
            self.pipeline = Gst.parse_launch(pipeline_desc)
            self.source = self.pipeline.get_by_name("source")
            self.sink = self.pipeline.get_by_name("sink")
            result = self.pipeline.set_state(Gst.State.PLAYING)
            if result == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("GStreamer rejected the DeckLink pipeline")

            self._set_status(opened=True, phase="waiting_signal", last_error="")
            logger.info(
                "[DeckLink] Started device=%s connection=%s mode=%s",
                self.device_number,
                self.connection,
                self.mode,
            )

            while self._is_running():
                bus_error = self._consume_bus_message()
                if bus_error:
                    raise RuntimeError(bus_error)

                sample = self.sink.emit("try-pull-sample", self._PULL_TIMEOUT_NS)
                now = time.monotonic()
                signal = self._read_source_signal()
                if sample is None:
                    phase = "waiting_signal" if not signal else "stalled"
                    self._set_status(signal=signal, phase=phase)
                    # A source without a cable is a stable waiting state. Keep
                    # the pipeline open for hot-plug and avoid exposing a brief
                    # reconnect race every few seconds. Rebuild only when the
                    # card reports a valid signal but frames stop arriving.
                    if signal and now - last_sample_at >= self._NO_FRAME_RESTART_SECONDS:
                        return "No frames received; restarting input negotiation"
                    continue

                caps = sample.get_caps()
                structure = caps.get_structure(0)
                self._update_caps(structure)
                width = int(structure.get_value("width"))
                height = int(structure.get_value("height"))
                buffer = sample.get_buffer()
                ok, map_info = buffer.map(Gst.MapFlags.READ)
                if not ok:
                    continue

                try:
                    frame = np.frombuffer(map_info.data, dtype=np.uint8).reshape((height, width, 3)).copy()
                finally:
                    buffer.unmap(map_info)

                last_sample_at = now
                with self.condition:
                    self.signal = True
                    self.phase = "streaming"
                    self.last_error = ""
                    self.last_frame_monotonic = now
                    self.latest_frame = frame
                    self.sequence += 1
                    self.condition.notify_all()
                self._update_black_level(frame, now)
        finally:
            if self.pipeline is not None:
                self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
            self.source = None
            self.sink = None
            self._set_status(opened=False)

        return None

    def _run(self):
        backoff = 0.5
        try:
            while self._is_running():
                try:
                    reason = self._run_pipeline_once()
                    if not self._is_running():
                        break
                    self.restart_count += 1
                    self._set_status(phase="reconnecting", last_error=reason or "Input pipeline stopped")
                except Exception as exc:
                    if not self._is_running():
                        break
                    self.restart_count += 1
                    self._set_status(phase="reconnecting", last_error=str(exc), signal=False)
                    logger.warning("[DeckLink] Pipeline error for %s: %s", self.uri, exc)

                deadline = time.monotonic() + backoff
                with self.condition:
                    while self.running and time.monotonic() < deadline:
                        self.condition.wait(timeout=min(0.1, deadline - time.monotonic()))
                backoff = min(2.0, backoff * 2.0)
        finally:
            with self.condition:
                self.opened = False
                self.running = False
                self.signal = False
                self.phase = "stopped"
                self.condition.notify_all()
            logger.info(
                "[DeckLink] Stopped device=%s connection=%s mode=%s",
                self.device_number,
                self.connection,
                self.mode,
            )


class DeckLinkCapture:
    def __init__(self, uri: str):
        self.uri = uri
        self._last_sequence = 0
        with _STATES_LOCK:
            state = _STATES.get(uri)
            if state is None or not state.running:
                if state is not None:
                    state.wait_for_shutdown(timeout=1.0)
                state = _DeckLinkSharedState(uri)
                _STATES[uri] = state
            self._state = state
            # Starting while holding the registry lock makes state creation and
            # reference acquisition atomic across display/storage/analysis.
            self._state.start()

    def isOpened(self):
        return bool(self._state and self._state.opened)

    def read(self, timeout: float = 2.0):
        if self._state is None:
            return False, None
        ret, frame, sequence = self._state.read_after(self._last_sequence, timeout=timeout)
        if ret:
            self._last_sequence = sequence
        return ret, frame

    def status(self):
        if self._state is None:
            return None
        return self._state.status()

    def get(self, prop_id):
        if self._state is None:
            return 0
        if prop_id == cv2.CAP_PROP_FPS:
            return self._state.fps
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return self._state.width
        if prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return self._state.height
        return 0

    def set(self, _prop_id, _value):
        return False

    def grab(self):
        ret, _ = self.read()
        return ret

    def release(self):
        if self._state is not None:
            self._state.release()
            self._state = None


def get_decklink_status(uri: str):
    with _STATES_LOCK:
        state = _STATES.get(uri)
    return state.status() if state is not None else None
