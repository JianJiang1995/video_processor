import unittest

from backend.routers.analysis import _analysis_source_elapsed


class LiveAnalysisClockTests(unittest.TestCase):
    def test_physical_capture_ignores_stale_review_positions(self):
        for source in ("decklink://0?mode=1080p50&connection=sdi", "device://0", "v4l2:///dev/video0"):
            for position in (0, 10, 256.5, 900):
                for paused in (False, True):
                    with self.subTest(source=source, position=position, paused=paused):
                        session = dict(video_path=source, current_position=position,
                                       duration=0, is_paused=paused)
                        self.assertEqual(_analysis_source_elapsed(475, session, True), 475)
                        self.assertEqual(_analysis_source_elapsed(480, session, True), 480)

    def test_capture_does_not_use_accumulated_ui_duration_as_eof(self):
        session = dict(video_path="decklink://0", current_position=30, duration=200)
        self.assertEqual(_analysis_source_elapsed(480, session, True), 480)

    def test_network_live_stream_does_not_depend_on_a_browser(self):
        for source in ("rtsp://camera/live", "rtmp://camera/live", "http://camera/mjpeg"):
            session = dict(video_path=source, current_position=15, duration=0)
            self.assertEqual(_analysis_source_elapsed(480, session, True), 480)

    def test_signal_loss_cannot_create_wall_clock_windows(self):
        session = dict(video_path="decklink://0", current_position=900, duration=0)
        for capture in (0, 255, 255):
            self.assertEqual(_analysis_source_elapsed(capture, session, True), capture)

    def test_finite_simulator_remains_playback_limited(self):
        for source in ("simulator://file/tmp/sample.mp4?input_layout=quad_right",
                       "filesim:///tmp/sample.mp4", "http://127.0.0.1:9001/stream"):
            session = dict(video_path=source, duration=600, current_position=256.5)
            self.assertEqual(_analysis_source_elapsed(475, session, True), 257.5)
            session["current_position"] = 490
            self.assertEqual(_analysis_source_elapsed(475, session, True), 475)

    def test_offline_media_does_not_wait_for_preview(self):
        session = dict(video_path="/tmp/sample.mp4", duration=600, current_position=30)
        self.assertEqual(_analysis_source_elapsed(475, session, False), 475)

    def test_finite_media_cannot_analyze_beyond_eof(self):
        session = dict(video_path="simulator://file/tmp/sample.mp4", duration=600,
                       current_position=900)
        for live in (False, True):
            self.assertEqual(_analysis_source_elapsed(900, session, live), 600)


if __name__ == "__main__":
    unittest.main()
