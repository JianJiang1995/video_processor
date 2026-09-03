import unittest
import threading

import numpy as np

from backend.services.decklink_capture import (
    _DeckLinkSharedState,
    _fps_from_mode,
    build_decklink_uri,
    build_pipeline_description,
    parse_decklink_uri,
)


class DeckLinkCaptureConfigurationTests(unittest.TestCase):
    def test_uri_defaults_to_auto_detection(self):
        self.assertEqual(parse_decklink_uri("decklink://0"), (0, "auto", "auto"))

    def test_uri_round_trip(self):
        uri = build_decklink_uri(2, mode="1080i5994", connection="hdmi")
        self.assertEqual(parse_decklink_uri(uri), (2, "1080i5994", "hdmi"))

    def test_uri_rejects_pipeline_injection(self):
        with self.assertRaises(ValueError):
            build_decklink_uri(0, mode="auto ! fakesink", connection="hdmi")

    def test_pipeline_is_low_latency_and_deinterlaces_interlaced_input(self):
        pipeline = build_pipeline_description(0, "auto", "hdmi")
        self.assertIn("connection=hdmi mode=auto", pipeline)
        self.assertIn("buffer-size=2", pipeline)
        self.assertIn("drop-no-signal-frames=true", pipeline)
        self.assertIn("leaky=downstream max-size-buffers=1", pipeline)
        self.assertIn("deinterlace mode=auto method=linear fields=all", pipeline)
        self.assertIn("max-buffers=1 drop=true", pipeline)

    def test_mode_fps_defaults(self):
        self.assertAlmostEqual(_fps_from_mode("1080p5994"), 60000 / 1001)
        self.assertAlmostEqual(_fps_from_mode("1080i50"), 50.0)
        self.assertAlmostEqual(_fps_from_mode("1080p25"), 25.0)

    def test_reader_does_not_repeat_a_stale_frame_after_timeout(self):
        state = _DeckLinkSharedState.__new__(_DeckLinkSharedState)
        state.condition = threading.Condition()
        state.running = True
        state.sequence = 7
        state.latest_frame = np.zeros((2, 2, 3), dtype=np.uint8)

        fresh, frame, sequence = state.read_after(6, timeout=0)
        self.assertTrue(fresh)
        self.assertEqual(sequence, 7)
        self.assertIsNotNone(frame)

        fresh, frame, sequence = state.read_after(7, timeout=0)
        self.assertFalse(fresh)
        self.assertIsNone(frame)
        self.assertEqual(sequence, 7)


if __name__ == "__main__":
    unittest.main()
