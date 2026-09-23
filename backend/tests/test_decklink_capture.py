import unittest
import threading

import numpy as np

from backend.services.decklink_capture import (
    _DeckLinkSharedState,
    _ensure_gst,
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


class DeckLinkCapsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            _ensure_gst()
        except (ImportError, ValueError) as exc:
            raise unittest.SkipTest(f"GStreamer bindings unavailable: {exc}")
        from gi.repository import Gst

        cls.Gst = Gst

    def test_reads_real_gstreamer_frame_rates(self):
        for fraction, expected in (("50/1", 50.0), ("60000/1001", 60000 / 1001)):
            with self.subTest(fraction=fraction):
                state = _DeckLinkSharedState.__new__(_DeckLinkSharedState)
                state.condition = threading.Condition()
                state.fps = 30.0
                caps = self.Gst.Caps.from_string(
                    "video/x-raw,width=1920,height=1080,"
                    f"framerate={fraction},interlace-mode=progressive"
                )

                state._update_caps(caps.get_structure(0))

                self.assertEqual((state.width, state.height), (1920, 1080))
                self.assertAlmostEqual(state.fps, expected)
                self.assertEqual(state.interlace_mode, "progressive")

    def test_unknown_frame_rate_preserves_previous_value(self):
        for rate_field in ("", ",framerate=0/1"):
            with self.subTest(rate_field=rate_field):
                state = _DeckLinkSharedState.__new__(_DeckLinkSharedState)
                state.condition = threading.Condition()
                state.fps = 50.0
                caps = self.Gst.Caps.from_string(
                    f"video/x-raw,width=1920,height=1080{rate_field}"
                )

                state._update_caps(caps.get_structure(0))

                self.assertEqual(state.fps, 50.0)


if __name__ == "__main__":
    unittest.main()
