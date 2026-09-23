import unittest
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
