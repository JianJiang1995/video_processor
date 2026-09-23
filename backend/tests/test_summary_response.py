import asyncio
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import analysis


class SummaryResponseTests(unittest.TestCase):
    def test_full_history_serialization_preserves_contract_off_event_loop(self):
        app = FastAPI()
        app.include_router(analysis.router)
        app.dependency_overrides[analysis.get_db] = lambda: None
        others = {'stage': 2, 'stage1_summary': 'draft', 'experts': {'surgr1': {'status': 'completed'}}}

        def rows(*args):
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            return [{'window_id': 0, 'window_start': 0, 'window_end': 5,
                     'glm_summary': 'reviewed', 'surgical_phase': 'Preparation', 'others': others}]

        with patch.object(analysis, 'get_video_session', return_value={'session_id': 'one'}), \
             patch.object(analysis, 'get_summaries_by_session', side_effect=rows):
            response = TestClient(app).get('/api/analysis/summaries/one')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{'window_id': 0, 'start_time': 0, 'end_time': 5,
            'summary': 'reviewed', 'surgical_phase': 'Preparation', 'tts_audio_path': None,
            'stage': 2, 'stage1_summary': 'draft', 'others': others}])
