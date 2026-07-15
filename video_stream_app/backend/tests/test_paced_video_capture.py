import unittest
from unittest.mock import patch

import cv2

from backend.services.local_video_source import PacedVideoCapture


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
