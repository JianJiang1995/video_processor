import unittest
from unittest.mock import patch

import cv2
import numpy as np

from backend.services.local_video_source import (
    INPUT_LAYOUT_FULL,
    INPUT_LAYOUT_QUAD_RIGHT,
    ResolvedVideoSource,
    file_simulator_uri,
    apply_input_layout,
    layout_output_dimensions,
    resolve_video_source,
    split_input_layout,
    with_input_layout,
    wrap_capture_for_layout,
)
from backend.services.decklink_capture import parse_decklink_uri


class _FakeCapture:
    def __init__(self, frame):
        self.frame = frame

    def read(self):
        return True, self.frame.copy()

    def get(self, prop):
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return self.frame.shape[1]
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return self.frame.shape[0]
        return 0

    def isOpened(self):
        return True


class InputLayoutTests(unittest.TestCase):
    def test_bound_simulator_keeps_its_file_and_layout(self):
        path = '/tmp/operation #1 ? take.mp4'
        uri = with_input_layout(file_simulator_uri(path), INPUT_LAYOUT_QUAD_RIGHT)
        with patch('backend.services.local_video_source._probe_video') as probe:
            probe.return_value = ResolvedVideoSource(original=path, source=path,
                                                     is_simulator=True, duration=12)
            with patch('backend.services.local_video_source.get_simulator_source') as preset:
                result = resolve_video_source(uri)
                preset.assert_not_called()
            probe.assert_called_once_with(path)
        self.assertEqual(result.source, path)
        self.assertEqual(result.input_layout, INPUT_LAYOUT_QUAD_RIGHT)
        self.assertTrue(result.is_simulator)
        self.assertEqual(result.duration, 12)

    def test_missing_bound_video_never_loads_default(self):
        with patch('backend.services.local_video_source._probe_video', return_value=None):
            with patch('backend.services.local_video_source.get_simulator_source') as preset:
                result = resolve_video_source(file_simulator_uri('/tmp/missing.mp4'))
                preset.assert_not_called()
        self.assertEqual(result.source, '/tmp/missing.mp4')
        self.assertIsNone(result.duration)

    def test_layout_query_preserves_existing_capture_parameters(self):
        source = "decklink://0?mode=1080p30&connection=hdmi"
        decorated = with_input_layout(source, INPUT_LAYOUT_QUAD_RIGHT)

        clean, layout = split_input_layout(decorated)

        self.assertEqual(clean, source)
        self.assertEqual(layout, INPUT_LAYOUT_QUAD_RIGHT)
        self.assertEqual(parse_decklink_uri(decorated), (0, "1080p30", "hdmi"))

    def test_unknown_layout_falls_back_to_full_frame(self):
        decorated = with_input_layout("simulator://capture-card/0", "unknown")
        clean, layout = split_input_layout(decorated)

        self.assertEqual(clean, "simulator://capture-card/0")
        self.assertEqual(layout, INPUT_LAYOUT_FULL)

    def test_quad_layout_keeps_only_right_three_quarters(self):
        frame = np.zeros((8, 16, 3), dtype=np.uint8)
        frame[:, :4] = (1, 2, 3)
        frame[:, 4:] = (10, 20, 30)

        cropped = apply_input_layout(frame, INPUT_LAYOUT_QUAD_RIGHT)

        self.assertEqual(cropped.shape, (8, 12, 3))
        self.assertTrue(np.all(cropped == (10, 20, 30)))
        self.assertTrue(np.shares_memory(cropped, frame))
        self.assertEqual(layout_output_dimensions(3840, 2160, INPUT_LAYOUT_QUAD_RIGHT), (2880, 2160))

    def test_capture_wrapper_reports_and_returns_cropped_width(self):
        frame = np.zeros((108, 192, 3), dtype=np.uint8)
        capture = wrap_capture_for_layout(_FakeCapture(frame), INPUT_LAYOUT_QUAD_RIGHT)

        ok, cropped = capture.read()

        self.assertTrue(ok)
        self.assertEqual(cropped.shape[:2], (108, 144))
        self.assertEqual(capture.get(cv2.CAP_PROP_FRAME_WIDTH), 144)
        self.assertEqual(capture.get(cv2.CAP_PROP_FRAME_HEIGHT), 108)

    def test_resolver_keeps_layout_metadata_out_of_backing_source(self):
        resolved = resolve_video_source("device://2?input_layout=quad_right")

        self.assertEqual(resolved.source, "device://2")
        self.assertEqual(resolved.input_layout, INPUT_LAYOUT_QUAD_RIGHT)


if __name__ == "__main__":
    unittest.main()
