import tempfile
import unittest
from pathlib import Path
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.services.frame_storage_service import FrameStorageService
from backend.services.mysql_service import AnalysisResult, MySQLService


class HistoryRangeTests(unittest.TestCase):
    def setUp(self):
        self.storage = FrameStorageService()

    def test_no_history_is_not_seekable(self):
        self.assertEqual(self.storage.available_range(None),
                         dict(available=False, start=0, end=0, frame_count=0))

    def test_bounds_follow_retention_and_include_preview(self):
        with tempfile.TemporaryDirectory() as path:
            Path(path, 'frames').mkdir()
            Path(path, 'preview').mkdir()
            for ts in (2.5, 6.0, 12.0):
                self.storage._cache.add_frame(path, 'frames', ts, f'frame_{ts}.jpg')
            self.storage._cache.add_frame(path, 'preview', 14.0, 'preview.jpg')
            self.assertEqual(self.storage.available_range(path),
                             dict(available=True, start=2.5, end=14.0, frame_count=3))
            self.storage._cache.prune_older_than(path, 'frames', 5.0)
            self.assertEqual(self.storage.available_range(path)['start'], 6.0)
            self.storage._cache.prune_older_than(path, 'frames', 20.0)
            self.storage._cache.prune_older_than(path, 'preview', 20.0)
            self.assertFalse(self.storage.available_range(path)['available'])


class AnalysisProgressTests(unittest.TestCase):
    def test_cursor_includes_window_only_experts_and_all_frames(self):
        engine = create_engine('sqlite://')
        AnalysisResult.__table__.create(engine)
        service = object.__new__(MySQLService)

        @contextmanager
        def session_scope():
            with Session(engine) as session:
                yield session

        service.get_session = session_scope
        with Session(engine) as session:
            session.add_all([AnalysisResult(session_id='a', timestamp=i, analysis_type='frame')
                             for i in range(120)])
            session.add(AnalysisResult(session_id='a', analysis_type='window', window_start=125, window_end=130))
            session.add(AnalysisResult(session_id='b', analysis_type='window', window_start=700))
            session.commit()
        self.assertEqual(service.get_analysis_progress('a'),
                         {'frames_analyzed': 120, 'latest_timestamp': 125})
        self.assertEqual(service.get_analysis_progress('b')['latest_timestamp'], 700)
        self.assertEqual(service.get_analysis_progress('missing')['latest_timestamp'], 0)
        engine.dispose()
