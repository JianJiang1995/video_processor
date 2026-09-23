import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from PIL import Image

from backend.services import surgr1_window_review as service


class SurgR1WindowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cfg = {'services': {'surgr1': {'enabled': True, 'mode': 'window_refinement'}}}
        self.client = Mock(api_url='http://127.0.0.1:9003')
        self.client.analyze_batch = AsyncMock(return_value={'success': True, 'results': [
            {'responses': {'q0': '<think>not evidence</think><answer>Grasper retracts gallbladder.</answer>'}},
            {'responses': {'q0': '<think>unfinished'}},
        ]})

    async def test_action_question_and_real_timestamps_no_video_decoder(self):
        with patch.object(service, 'load_config', return_value=self.cfg), \
             patch.object(service, 'get_surgr1_client', return_value=self.client), \
             patch.object(service, '_gate', asyncio.Semaphore(1)):
            result = await service.review_surgr1_window([Image.new('RGB', (20, 10))] * 4, [5.1, 6.2, 7.3, 9.7])
        self.assertTrue(result['success'])
        self.assertEqual(result['frame_count'], 2)
        self.assertEqual(result['observations'], [{'frame_index': 0, 'timestamp': 5.1, 'action': 'Grasper retracts gallbladder.'}])
        self.assertIn('tool, action, and tissue', self.client.analyze_batch.call_args.kwargs['questions'][0])

    async def test_disabled_and_remote_never_send_images(self):
        for enabled, url in [(False, 'http://127.0.0.1:9003'), (True, 'https://external.example')]:
            self.cfg['services']['surgr1']['enabled'] = enabled
            self.client.api_url = url
            with patch.object(service, 'load_config', return_value=self.cfg), \
                 patch.object(service, 'get_surgr1_client', return_value=self.client):
                result = await service.review_surgr1_window([Image.new('RGB', (2, 2))], [0])
            self.assertFalse(result['success'])
        self.client.analyze_batch.assert_not_awaited()

    async def test_model_failure_cleans_images_and_releases_capacity(self):
        self.client.analyze_batch.side_effect = RuntimeError('service unavailable')
        gate = asyncio.Semaphore(1)
        with patch.object(service, 'load_config', return_value=self.cfg), \
                patch.object(service, 'get_surgr1_client', return_value=self.client), \
                patch.object(service, '_gate', gate):
            result = await service.review_surgr1_window([Image.new('RGB', (2, 2))], [0])
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(gate.locked())
        self.assertEqual(result['call_count'], 1)
        for path in self.client.analyze_batch.call_args.kwargs['image_paths']:
            self.assertFalse(Path(path).exists())

    def test_incomplete_reasoning_not_used_as_facts(self):
        for raw in ('<think>guess tissue', '<think>guess</think><answer>partial', '[Error: unavailable]'):
            self.assertEqual(service.answer_only(raw), '')
        self.assertEqual(service.answer_only('<answer>complete</answer>'), 'complete')
