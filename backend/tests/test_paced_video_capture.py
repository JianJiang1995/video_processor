import unittest
from unittest.mock import Mock, patch

import cv2

from backend.services.local_video_source import PacedVideoCapture
from backend.services.frame_capture_service import frame_retention_seconds
from backend.services import frame_capture_service as capture_service
from backend.services.local_video_source import ResolvedVideoSource


class _FakeCapture:
    def __init__(self, fps=25.0, total=500):
        self.fps = fps
        self.total = total
        self.pos = 0

    def isOpened(self):
        return True

    def read(self):
        if self.pos >= self.total:
            return False, None
        frame = self.pos
        self.pos += 1
        return True, frame

    def grab(self):
        if self.pos >= self.total:
            return False
        self.pos += 1
        return True

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return self.fps
        if prop == cv2.CAP_PROP_POS_FRAMES:
            return self.pos
        if prop == cv2.CAP_PROP_POS_MSEC:
            return max(0, self.pos - 1) / self.fps * 1000
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return self.total
        return 0

    def set(self, prop, value):
        if prop == cv2.CAP_PROP_POS_FRAMES:
            self.pos = int(value)
            return True
        return False

    def release(self):
        return None


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def perf_counter(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, seconds)


class PacedVideoCaptureTest(unittest.TestCase):
    def test_local_file_eof_does_not_restart_capture(self):
        cap = Mock()
        cap.get.return_value = 25
        cap.read.side_effect = [(False, None), RuntimeError('Read beyond EOF')]
        states = {}
        with patch.object(capture_service, 'resolve_video_source', return_value=ResolvedVideoSource(
                original='/tmp/file.mp4', source='/tmp/file.mp4', is_simulator=False)), \
                patch.object(capture_service, 'open_video_source', return_value=cap), \
                patch.object(capture_service, '_capture_states', states), \
                patch.object(capture_service, 'get_frame_storage_service'), \
                patch('backend.services.frame_storage_service._frame_index_cache'):
            capture_service.frame_capture_thread_func('eof', '/tmp/file.mp4', '/unused', False, 0)
        cap.read.assert_called_once()
        cap.set.assert_not_called()
        cap.release.assert_called_once()
        self.assertFalse(states['eof'].is_running)

    def test_finite_simulator_keeps_history_but_live_capture_remains_bounded(self):
        config = {'live_retention_seconds': 300}
        self.assertEqual(frame_retention_seconds(config, True, True), 0)
        self.assertEqual(frame_retention_seconds(config, False, False), 0)
        self.assertEqual(frame_retention_seconds(config, True, False), 300)

    def test_50fps_analysis_sampling_preserves_source_time_and_eof(self):
        fake_capture = _FakeCapture(fps=50, total=21)
        clock = _FakeClock()
        with (
            patch("backend.services.local_video_source.cv2.VideoCapture", return_value=fake_capture),
            patch("backend.services.local_video_source.time.perf_counter", side_effect=clock.perf_counter),
            patch("backend.services.local_video_source.time.sleep", side_effect=clock.sleep),
        ):
            capture = PacedVideoCapture("fake.mp4", loop=False, sample_fps=5)
            for index in (0, 10, 20):
                self.assertEqual(capture.read(), (True, index))
                self.assertAlmostEqual(capture.last_timestamp(), index / 50)
                self.assertAlmostEqual(clock.now, index / 50)
            self.assertEqual(capture.read(), (False, None))
            capture.set(cv2.CAP_PROP_POS_FRAMES, 10)
            self.assertEqual(capture.read(), (True, 10))

    def test_late_consumer_skips_stale_frames_without_shifting_clock(self):
        fake_capture = _FakeCapture()
        clock = _FakeClock()
        with (
            patch("backend.services.local_video_source.cv2.VideoCapture", return_value=fake_capture),
            patch("backend.services.local_video_source.time.perf_counter", side_effect=clock.perf_counter),
            patch("backend.services.local_video_source.time.sleep", side_effect=clock.sleep),
        ):
            capture = PacedVideoCapture("fake.mp4", loop=False)
            ok, frame = capture.read()
            self.assertTrue(ok)
            self.assertEqual(frame, 0)

            clock.now = 0.20
            ok, frame = capture.read()
            self.assertTrue(ok)
            self.assertEqual(frame, 5)
            self.assertEqual(capture.last_frame_index(), 5)
            self.assertEqual(capture.dropped_frames(), 4)

            ok, frame = capture.read()
            self.assertTrue(ok)
            self.assertEqual(frame, 6)
            self.assertAlmostEqual(clock.now, 0.24, places=6)
            self.assertEqual(capture.dropped_frames(), 4)

    def test_seek_resets_wall_clock_anchor(self):
        fake_capture = _FakeCapture()
        clock = _FakeClock()
        with (
            patch("backend.services.local_video_source.cv2.VideoCapture", return_value=fake_capture),
            patch("backend.services.local_video_source.time.perf_counter", side_effect=clock.perf_counter),
            patch("backend.services.local_video_source.time.sleep", side_effect=clock.sleep),
        ):
            capture = PacedVideoCapture("fake.mp4", loop=False)
            capture.read()
            clock.now = 1.0
            capture.set(cv2.CAP_PROP_POS_FRAMES, 100)
            ok, frame = capture.read()
            self.assertTrue(ok)
            self.assertEqual(frame, 100)
            self.assertEqual(capture.last_frame_index(), 100)


if __name__ == "__main__":
    unittest.main()
