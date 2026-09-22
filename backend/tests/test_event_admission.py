import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from backend.routers import analysis
from scripts.local_vlm_balancer import build_app


class EventAdmissionTests(unittest.IsolatedAsyncioTestCase):
    def environment(self, pending=0, fail=False):
        stack = ExitStack()
        self.addCleanup(stack.close)
        records = [{'window_id': i, 'display_window': i + 1, 'start_time': i * 5, 'end_time': (i + 1) * 5,
                    'phase': 'CalotTriangleDissection', 'summary': '电凝钩分离组织'} for i in range(120)]
        client = SimpleNamespace(chat=AsyncMock(
            side_effect=TimeoutError() if fail else None,
            return_value={'success': True, 'text': '{"events": []}'}))
        task = SimpleNamespace(done=lambda: False)
        patches = {
            'get_video_session': lambda *a: {'session_id': 'test'},
            'get_summaries_by_session': lambda *a: records,
            '_normalize_summary_for_event_nodes': lambda r: r,
            'load_config': lambda: {'services': {'event_nodes': {'provider': 'glm', 'enabled': True}}},
            'get_glm_client': lambda: client,
            'active_glm_tasks': {'test': task},
            'active_open_vlm_tasks': {'test': [task] * pending},
            'event_node_latest': {}, 'event_node_cache': {},
            'event_node_last_attempt': {}, 'event_node_inflight': set(),
        }
        for key, value in patches.items():
            stack.enter_context(patch.object(analysis, key, value))
        return client, records

    async def test_visual_backlog_does_not_submit_another_text_job(self):
        client, _ = self.environment(pending=4)
        result = await analysis.get_event_nodes('test', analysis.EventNodesRequest(), db=None)
        self.assertEqual(result['deferred_reason'], 'visual_backlog')
        client.chat.assert_not_awaited()

    async def test_timeout_also_enters_cooldown_even_when_records_change(self):
        client, records = self.environment(fail=True)
        first = await analysis.get_event_nodes('test', analysis.EventNodesRequest(), db=None)
        records[-1]['summary'] = '电凝钩接触组织'
        second = await analysis.get_event_nodes('test', analysis.EventNodesRequest(), db=None)
        self.assertEqual(first['source'], 'fallback')
        self.assertEqual(second['deferred_reason'], 'cooldown')
        self.assertEqual(client.chat.await_count, 1)
        self.assertEqual(analysis.event_node_inflight, set())

    async def test_live_request_is_compacted_and_token_bounded(self):
        client, _ = self.environment()
        result = await analysis.get_event_nodes('test', analysis.EventNodesRequest(max_windows=120), db=None)
        self.assertLessEqual(result['prompt_window_count'], 16)
        self.assertEqual(client.chat.call_args.kwargs['max_tokens'], 600)

    async def test_duplicate_inflight_request_cannot_bypass_guard_with_force(self):
        client, _ = self.environment()
        analysis.event_node_inflight.add(('test', 'zh'))
        result = await analysis.get_event_nodes('test', analysis.EventNodesRequest(force=True), db=None)
        self.assertEqual(result['deferred_reason'], 'inflight')
        client.chat.assert_not_awaited()


class ProxyCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_caller_cancels_upstream_generation(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def upstream(request):
            entered.set()
            try:
                await asyncio.sleep(60)
                return web.json_response({})
            except asyncio.CancelledError:
                cancelled.set()
                raise

        app = web.Application()
        app.router.add_post('/v1/chat/completions', upstream)
        async with TestServer(app, handler_cancellation=True) as server:
            proxy = build_app([str(server.make_url('/v1'))], 60)
            async with TestServer(proxy, handler_cancellation=True) as proxy_server:
                async with ClientSession() as client:
                    task = asyncio.create_task(client.post(proxy_server.make_url('/v1/chat/completions'),
                                                           json={'messages': []}))
                    await asyncio.wait_for(entered.wait(), 3)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    await asyncio.wait_for(cancelled.wait(), 3)


if __name__ == '__main__':
    unittest.main()
