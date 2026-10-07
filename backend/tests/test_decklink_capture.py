import unittest
import threading
from unittest.mock import patch

import numpy as np

from backend.services.decklink_capture import (
    DeckLinkCapture,
    _DeckLinkSharedState,
    _ensure_gst,
    _fps_from_mode,
    build_decklink_uri,
    build_pipeline_description,
    parse_decklink_uri,
)
from backend.services import decklink_capture


class DeckLinkOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.readers = []
        self.registry = {}
        self.registry_patch = patch.object(decklink_capture, "_STATES", self.registry)
        self.gst_patch = patch.object(decklink_capture, "_ensure_gst")

        def run(state):
            self.events.append(("open", state.uri))
            state._set_status(opened=True)
            try:
                while state._is_running():
                    with state.condition:
                        state.condition.wait(timeout=0.01)
            finally:
                self.events.append(("closed", state.uri))
                state._set_status(opened=False, running=False)

        self.run_patch = patch.object(_DeckLinkSharedState, "_run", run)
        self.registry_patch.start()
        self.gst_patch.start()
        self.run_patch.start()

    def tearDown(self):
        for reader in self.readers:
            reader.release()
        for state in self.registry.values():
            state.stop_if_idle()
        self.run_patch.stop()
        self.gst_patch.stop()
        self.registry_patch.stop()

    def capture(self, connection="sdi", mode="auto", device=0):
        reader = DeckLinkCapture(build_decklink_uri(device, mode, connection))
        self.readers.append(reader)
        return reader

    def test_identical_settings_share_pipeline_and_probe_grace(self):
        probe = self.capture()
        state = probe._state
        probe.release()
        preview = self.capture()
        analysis = self.capture()
        self.assertIs(preview._state, state)
        self.assertIs(analysis._state, state)
        self.assertEqual(state.ref_count, 2)
        self.assertEqual(len(self.events), 1)

    def test_equivalent_uris_share_pipeline(self):
        reader = self.capture()
        alias = DeckLinkCapture("decklink://0?connection=sdi&mode=auto")
        self.readers.append(alias)
        self.assertIs(reader._state, alias._state)

    def test_idle_input_released_before_connector_or_mode_switch(self):
        for connection, mode in (("hdmi", "auto"), ("sdi", "auto"), ("sdi", "1080p50")):
            reader = self.capture(connection, mode)
            reader.release()
        self.assertEqual([event for event, _ in self.events], ["open", "closed", "open", "closed", "open"])
        self.assertEqual(len(self.registry), 1)

    def test_active_input_cannot_be_taken_over(self):
        reader = self.capture()
        with self.assertRaisesRegex(RuntimeError, "is in use"):
            self.capture("hdmi")
        self.assertTrue(reader.isOpened())
        self.assertEqual(reader._state.ref_count, 1)
        self.assertEqual(len(self.events), 1)

    def test_different_cards_can_run_together(self):
        first = self.capture()
        second = self.capture("hdmi", device=1)
        self.assertTrue(first.isOpened() and second.isOpened())
        self.assertEqual(len(self.events), 2)

    def test_shutdown_timeout_blocks_new_pipeline(self):
        reader = self.capture("hdmi")
        state = reader._state
        reader.release()
        with patch.object(state, "wait_for_shutdown", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "still releasing"):
                self.capture()
        self.assertEqual(sum(event == "open" for event, _ in self.events), 1)
        self.assertIn(state.uri, self.registry)

    def test_expired_state_is_replaced_not_restarted(self):
        reader = self.capture()
        state = reader._state
        reader.release()
        state.stop_if_idle()
        self.assertFalse(state.start())
        self.assertEqual(state.ref_count, 0)
        replacement = self.capture()
        self.assertIsNot(replacement._state, state)
        self.assertEqual([event for event, _ in self.events], ["open", "closed", "open"])

    def test_simultaneous_readers_open_hardware_only_once(self):
        threads = [threading.Thread(target=self.capture) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(self.readers), 8)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.readers[0]._state.ref_count, 8)


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
