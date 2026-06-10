"""
DeckLink capture adapter backed by one shared GStreamer appsink per URI.

OpenCV in this deployment is built without GStreamer support, while Blackmagic
DeckLink capture is exposed through the GStreamer decklink plugin. This module
implements the small cv2.VideoCapture surface used by the backend and shares a
single DeckLink pipeline across display, frame storage, and analysis readers.
"""

import logging
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

logger = logging.getLogger(__name__)


_GST_READY = False
Gst = None
_STATES = {}
_STATES_LOCK = threading.Lock()


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


def parse_decklink_uri(uri: str):
    parsed = urlparse(uri)
    device_spec = parsed.netloc or parsed.path.lstrip("/") or "0"
    try:
        device_number = int(device_spec)
    except ValueError:
        device_number = 0

    query = parse_qs(parsed.query)
    mode = (query.get("mode") or ["1080p30"])[0]
    return device_number, mode


def _fps_from_mode(mode: str) -> float:
    if mode.endswith(("p60", "i60")):
        return 60.0
    if mode.endswith(("p5994", "i5994")):
        return 60000.0 / 1001.0
    if mode.endswith(("p50", "i50")):
        return 50.0
    if mode.endswith(("p30", "i30")):
        return 30.0
    if mode.endswith(("p2997", "i2997")):
        return 30000.0 / 1001.0
    if mode.endswith("p25"):
        return 25.0
    if mode.endswith("p24"):
        return 24.0
    return 30.0


class _DeckLinkSharedState:
    def __init__(self, uri: str):
        _ensure_gst()
        self.uri = uri
        self.device_number, self.mode = parse_decklink_uri(uri)
        self.fps = _fps_from_mode(self.mode)
        self.width = 0
        self.height = 0
        self.latest_frame = None
        self.sequence = 0
        self.running = False
        self.opened = False
        self.ref_count = 0
        self.pipeline = None
        self.sink = None
        self.thread = None
        self.condition = threading.Condition()

    def start(self):
        with self.condition:
            self.ref_count += 1
            if self.running:
                return
            self.running = True
            self.thread = threading.Thread(
                target=self._run,
                name=f"decklink-{self.device_number}-{self.mode}",
                daemon=True,
            )
            self.thread.start()

        deadline = time.time() + 3.0
        while time.time() < deadline:
            if self.opened:
                return
            time.sleep(0.02)

    def release(self):
        with self.condition:
            self.ref_count = max(0, self.ref_count - 1)
            if self.ref_count == 0:
                self.running = False
                self.condition.notify_all()

    def read_after(self, last_sequence: int, timeout: float = 2.0):
        end_time = time.time() + timeout
        with self.condition:
            while self.running and self.sequence == last_sequence:
                remaining = end_time - time.time()
                if remaining <= 0:
                    break
                self.condition.wait(timeout=remaining)

            if self.latest_frame is None:
                return False, None, last_sequence

            return True, self.latest_frame.copy(), self.sequence

    def _run(self):
        pipeline_desc = (
            f"decklinkvideosrc device-number={self.device_number} mode={self.mode} "
            "! queue leaky=downstream max-size-buffers=1 "
            "! videoconvert n-threads=2 "
            "! video/x-raw,format=BGR "
            "! appsink name=sink emit-signals=false sync=false max-buffers=1 drop=true"
        )

        try:
            self.pipeline = Gst.parse_launch(pipeline_desc)
            self.sink = self.pipeline.get_by_name("sink")
            result = self.pipeline.set_state(Gst.State.PLAYING)
            if result == Gst.StateChangeReturn.FAILURE:
                logger.error("[DeckLink] Failed to start pipeline: %s", pipeline_desc)
                return

            self.opened = True
            logger.info("[DeckLink] Started shared pipeline device=%s mode=%s", self.device_number, self.mode)

            while True:
                with self.condition:
                    if not self.running:
                        break

                sample = self.sink.emit("try-pull-sample", 2 * Gst.SECOND)
                if sample is None:
                    continue

                caps = sample.get_caps()
                structure = caps.get_structure(0)
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

                with self.condition:
                    self.width = width
                    self.height = height
                    self.latest_frame = frame
                    self.sequence += 1
                    self.condition.notify_all()
        except Exception as e:
            logger.error("[DeckLink] Pipeline error for %s: %s", self.uri, e)
        finally:
            if self.pipeline is not None:
                self.pipeline.set_state(Gst.State.NULL)
            with self.condition:
                self.opened = False
                self.running = False
                self.condition.notify_all()
            logger.info("[DeckLink] Stopped shared pipeline device=%s mode=%s", self.device_number, self.mode)


class DeckLinkCapture:
    def __init__(self, uri: str):
        self.uri = uri
        self._last_sequence = 0
        with _STATES_LOCK:
            state = _STATES.get(uri)
            if state is None or not state.running:
                state = _DeckLinkSharedState(uri)
                _STATES[uri] = state
            self._state = state
        self._state.start()

    def isOpened(self):
        return self._state.opened

    def read(self):
        ret, frame, sequence = self._state.read_after(self._last_sequence)
        if ret:
            self._last_sequence = sequence
        return ret, frame

    def get(self, prop_id):
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
