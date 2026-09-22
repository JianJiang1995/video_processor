import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.routers import review
from backend.services import window_reanalysis as service
from backend.services.mysql_service import MySQLService, AnalysisResult


class ReviewEndpointTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(review.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.db = Mock()
        self.db.get_video_session.return_value = {
            "video_path": "simulator://capture-card/0", "storage_path": "/saved/session",
        }
        self.row = {"window_id": 0, "window_start": 0, "window_end": 5, "glm_summary": "original"}
        self.db.get_all_window_summaries.return_value = [self.row]
        self.db.save_window_review.return_value = {"stage": 3}
        for target, value in (("get_mysql_service", self.db),
                              ("load_review_frames", ([Image.new("RGB", (40, 20))], [1.25]))):
            patcher = patch.object(review, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.infer = AsyncMock(return_value={"summary": "new facts", "visual": {"scissors": {"visible": False}}, "model": "local"})
        patcher = patch.object(review, "analyze_review", self.infer)
        patcher.start()
        self.addCleanup(patcher.stop)
        review._active_reviews.clear()
        self.addCleanup(review._active_reviews.clear)

    def send(self, window="0"):
        return self.client.post(f"/api/analysis/reanalyze-window/demo/{window}")

    def test_existing_window_zero_saved_with_original_range(self):
        result = self.send()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["summary"], "new facts")
        self.assertEqual(result.json()["start_time"], 0)
        self.assertEqual(result.json()["end_time"], 5)
        metadata = self.db.save_window_review.call_args.args[-1]
        self.assertEqual(metadata["timestamps"], [1.25])
        self.assertEqual(metadata["frame_count"], 1)
        self.assertFalse(review._active_reviews)
        review.load_review_frames.assert_called_once_with(
            self.db.get_video_session.return_value, 0, 5,
        )

    def test_missing_persisted_session_is_rejected(self):
        self.db.get_video_session.return_value = None
        self.assertEqual(self.send().status_code, 404)
        self.infer.assert_not_awaited()

    def test_missing_and_invalid_windows_never_infer(self):
        self.assertEqual(self.send("99").status_code, 404)
        self.assertEqual(self.send("-1").status_code, 422)
        self.row["window_end"] = 100
        self.assertEqual(self.send().status_code, 422)
        self.infer.assert_not_awaited()
        self.db.save_window_review.assert_not_called()

    def test_failures_leave_original_and_release_busy_state(self):
        for error, code in ((FileNotFoundError(), 410), (PermissionError(), 503),
                            (ValueError(), 502), (asyncio.TimeoutError(), 504),
                            (RuntimeError(), 503)):
            with self.subTest(error=type(error)):
                self.infer.side_effect = error
                self.assertEqual(self.send().status_code, code)
                self.assertFalse(review._active_reviews)
        self.db.save_window_review.assert_not_called()

    def test_busy_does_not_launch_another_job(self):
        review._active_reviews.add(("other", 1))
        self.assertEqual(self.send().status_code, 409)
        self.infer.assert_not_awaited()


class ReviewStorageTests(unittest.TestCase):
    def setUp(self):
        self.db = object.__new__(MySQLService)
        self.db._engine = create_engine("sqlite://")
        self.db._SessionLocal = sessionmaker(bind=self.db._engine)
        AnalysisResult.__table__.create(self.db._engine)
        self.addCleanup(self.db._engine.dispose)
        self.db.save_analysis("demo", window_id=0, window_start=0, window_end=5,
                              analysis_type="window", glm_summary="old", others_data={"stage": 1})

    def test_review_history_and_late_automatic_overwrite_protection(self):
        metadata = {"completed_at": "2026-09-22", "model": "local", "revision": "r1"}
        result = self.db.save_window_review("demo", 0, "new", {"evidence": "test"}, metadata)
        self.assertEqual(result["review_history"][0]["summary"], "old")
        self.db.save_analysis("demo", window_id=0, analysis_type="window", glm_summary="late", others_data={"stage": 2})
        self.assertEqual(self.db.get_all_window_summaries("demo")[0]["glm_summary"], "new")
        result = self.db.save_window_review("demo", 0, "newer", {}, {**metadata, "revision": "r2"})
        self.assertEqual(len(result["review_history"]), 2)
        self.assertEqual(result["review_history"][1]["summary"], "new")
        self.assertIsNone(self.db.save_window_review("demo", 99, "missing", {}, metadata))
        self.assertEqual(len(self.db.get_all_window_summaries("demo")), 1)

    def test_unreviewed_window_still_accepts_automatic_updates(self):
        self.db.save_analysis("demo", window_id=0, analysis_type="window", glm_summary="stage2")
        self.assertEqual(self.db.get_all_window_summaries("demo")[0]["glm_summary"], "stage2")


class ReviewFrameTests(unittest.TestCase):
    def test_saved_frames_stay_in_range_are_bounded_and_not_cropped_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            Image.new("RGB", (1200, 600)).save(Path(folder) / "frame.jpg")
            storage = Mock()
            storage.list_frames_in_range.return_value = [
                {"timestamp": t, "path": "frame.jpg"} for t in (4.9, 5, 6, 7, 8, 9, 10)
            ]
            with patch.object(service, "get_frame_storage_service", return_value=storage), \
                 patch.object(service, "resolve_video_source") as resolve:
                images, times = service.load_review_frames({"storage_path": folder}, 5, 10)
            self.assertEqual(times, [5, 6, 8, 9])
            self.assertTrue(all(image.size == (640, 320) for image in images))
            resolve.assert_not_called()

    def test_missing_capture_frames_does_not_reopen_capture_device(self):
        source = Mock(source="decklink://0", input_layout="full")
        with patch.object(service, "resolve_video_source", return_value=source), \
             patch.object(service.cv2, "VideoCapture") as capture:
            with self.assertRaises(FileNotFoundError):
                service.load_review_frames({"video_path": "decklink://0"}, 0, 5)
            capture.assert_not_called()

    def test_old_simulator_record_never_reads_current_simulator_file(self):
        with patch.object(service, "resolve_video_source") as resolve:
            with self.assertRaises(FileNotFoundError):
                service.load_review_frames({"video_path": "simulator://capture-card/0"}, 0, 5)
            resolve.assert_not_called()

    def test_invalid_model_output_rejected(self):
        for text in ('', '[]', '{}', '{"summary":"", "visual":{}}', '{"summary":"ok","visual":"wrong"}'):
            with self.assertRaises(ValueError):
                service.parse_review(text)
        expected = {"summary": "facts", "visual": {"scissors": {"visible": False}}}
        self.assertEqual(service.parse_review('```json\n' + json.dumps(expected) + '\n```'), expected)


class LocalOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_model_is_rejected_before_sending_images(self):
        client = Mock(api_url="https://external.example/v1")
        client._analyze_images_with_timestamps = AsyncMock()
        with patch.object(service, "get_glm_client", return_value=client):
            with self.assertRaises(PermissionError):
                await service.analyze_review([Image.new("RGB", (1, 1))], [0])
        client._analyze_images_with_timestamps.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
