import asyncio
from contextlib import ExitStack
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from backend.routers import analysis


class SummaryStreamCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def check_final_update(self, task_kind):
        task = asyncio.get_running_loop().create_future()
        reads = 0

        def snapshots(*args):
            nonlocal reads
            reads += 1
            if reads == 1:
                # The producer finishes after this snapshot was read. The SSE
                # must sweep again instead of closing on the session status.
                task.set_result(None)
            return [{'window_id': 0, 'window_start': 0, 'window_end': 5,
                     'glm_summary': 'initial' if reads == 1 else 'reviewed',
                     'others': {'stage': 1}}]

        with ExitStack() as stack:
            values = {
                'get_video_session': lambda *args: {'session_id': 'test'},
                'get_summaries_by_session': snapshots,
                'active_glm_tasks': {'test': task} if task_kind == 'analysis' else {},
                'active_open_vlm_tasks': {'test': {task}} if task_kind == 'review' else {},
                'analysis_cancellation_flags': {},
            }
            for name, value in values.items():
                stack.enter_context(patch.object(analysis, name, value))
            stack.enter_context(patch('backend.database.get_session_record',
                                      return_value={'status': 'completed'}))
            stack.enter_context(patch.object(analysis.asyncio, 'sleep', new=AsyncMock()))
            request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
            response = await analysis.stream_summaries('test', request, db=None)
            messages = [json.loads(part.removeprefix('data: ').strip())
                        async for part in response.body_iterator]
        self.assertEqual([m.get('summary') for m in messages[:-1]], ['initial', 'reviewed'])
        self.assertEqual(messages[-1], {'status': 'completed'})

    async def test_review_can_finish_after_last_snapshot(self):
        await self.check_final_update('review')

    async def test_analysis_can_finish_after_last_snapshot(self):
        await self.check_final_update('analysis')

    async def test_manual_review_metadata_reaches_other_clients(self):
        others = {'stage': 3, 'manual_review': {'revision': 'review-1'}, 'visual_gpt': {}}
        with ExitStack() as stack:
            for name, value in {
                'get_video_session': lambda *args: {'session_id': 'test'},
                'get_summaries_by_session': lambda *args: [{
                    'window_id': 0, 'window_start': 0, 'window_end': 5,
                    'glm_summary': 'reviewed', 'others': others,
                }],
                'active_glm_tasks': {}, 'active_open_vlm_tasks': {},
                'analysis_cancellation_flags': {},
            }.items():
                stack.enter_context(patch.object(analysis, name, value))
            stack.enter_context(patch('backend.database.get_session_record',
                                      return_value={'status': 'completed'}))
            request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
            response = await analysis.stream_summaries('test', request, db=None)
            first = json.loads((await anext(response.body_iterator)).removeprefix('data: ').strip())
            await response.body_iterator.aclose()
        self.assertEqual(first['others'], others)
