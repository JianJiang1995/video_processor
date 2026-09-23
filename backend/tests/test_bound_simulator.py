import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import video
from backend.services.local_video_source import ResolvedVideoSource


class BoundSimulatorTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(video.router)
        app.dependency_overrides[video.get_db] = lambda: None
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_paced_load_preserves_finite_source_in_both_stores(self):
        cap = Mock()
        cap.get.side_effect = lambda key: {cv2.CAP_PROP_FPS: 25,
            cv2.CAP_PROP_FRAME_COUNT: 750, cv2.CAP_PROP_FRAME_WIDTH: 1440,
            cv2.CAP_PROP_FRAME_HEIGHT: 1080}[key]
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'clip.mp4'
            source.touch()
            with patch.object(video.cv2, 'VideoCapture', return_value=cap), \
                    patch.object(video, 'create_video_session', return_value={'session_id': 'paced'}) as create, \
                    patch.object(video, 'get_mysql_service') as mysql, \
                    patch.object(video, 'get_frame_storage_service'):
                result = self.client.post('/api/video/load', params={'video_path': str(source), 'paced': True})
        self.assertEqual(result.status_code, 200, result.text)
        stored = create.call_args.kwargs['video_path']
        self.assertTrue(stored.startswith('simulator://file/'))
        self.assertEqual(mysql.return_value.create_video_session.call_args.kwargs['video_path'], stored)
        self.assertEqual(mysql.return_value.create_video_session.call_args.kwargs['video_type'], 'stream')
        self.assertEqual(result.json()['duration'], 30)

    def test_custom_file_and_layout_persist_identically_in_both_stores(self):
        source = ResolvedVideoSource(original='/tmp/one.mp4', source='/tmp/one.mp4',
                                     is_simulator=True, fps=50, width=3840, height=2160,
                                     duration=12.5, total_frames=625)
        capture = Mock()
        capture.get.side_effect = lambda key: {cv2.CAP_PROP_FPS: 50,
            cv2.CAP_PROP_FRAME_WIDTH: 3840, cv2.CAP_PROP_FRAME_HEIGHT: 2160}[key]
        store = Mock()
        frames = Mock()
        frames.create_session_folder.return_value = '/saved/new'
        with patch.object(video, 'resolve_video_source', return_value=source), \
                patch.object(video, '_open_simulator_capture', return_value=(capture, None)) as opener, \
                patch.object(video, 'get_simulator_source') as preset, \
                patch.object(video, 'create_video_session', return_value={'session_id': 'new'}) as create, \
                patch.object(video, 'get_mysql_service', return_value=store), \
                patch.object(video, 'get_frame_storage_service', return_value=frames), \
                patch.object(video, 'update_session_status'):
            response = self.client.post('/api/video/connect-capture', json={
                'backend': 'simulator', 'simulator_path': '/tmp/one.mp4', 'input_layout': 'quad_right',
            })
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['duration'], 12.5)
        self.assertEqual(data['width'], 2880)
        self.assertEqual(data['video_path'], 'simulator://file/tmp/one.mp4?input_layout=quad_right')
        self.assertEqual(create.call_args.kwargs['video_path'], data['video_path'])
        self.assertEqual(store.create_video_session.call_args.kwargs['video_path'], data['video_path'])
        preset.assert_not_called()
        opener.assert_called_once_with('/tmp/one.mp4')
        capture.release.assert_called_once()

    def test_invalid_explicit_file_never_falls_back(self):
        missing = ResolvedVideoSource(original='missing', source='/tmp/missing.mp4', is_simulator=True)
        with patch.object(video, 'resolve_video_source', return_value=missing), \
                patch.object(video, 'get_simulator_source') as preset, \
                patch.object(video, 'create_video_session') as create:
            result = self.client.post('/api/video/connect-capture', json={
                'backend': 'simulator', 'simulator_path': '/tmp/missing.mp4',
            })
        self.assertEqual(result.status_code, 400)
        create.assert_not_called()
        preset.assert_not_called()


if __name__ == '__main__':
    unittest.main()
