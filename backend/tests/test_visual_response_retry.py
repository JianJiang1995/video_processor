import unittest
from unittest.mock import AsyncMock
from backend.services.local_visual_prompt import parse_complete_visual_response, normalize_visual_with_retry
from backend.services.local_visual_prompt import LOCAL_VISUAL_SCHEMA


class VisualResponseTests(unittest.TestCase):
    def test_complete_nested_summary_is_promoted_without_inventing_facts(self):
        result = parse_complete_visual_response('{"visual":{"observations":[],"summary":"腹腔内组织。"}}', 'stop')
        self.assertEqual(result, {'summary': '腹腔内组织。', 'visual': {'observations': []}})

    def test_cutoff_and_missing_summary_cannot_become_success(self):
        good = '{"summary":"观察术野。","visual":{"observations":[]}}'
        self.assertIsNotNone(parse_complete_visual_response(good))
        self.assertIsNotNone(parse_complete_visual_response('```json\n' + good + '\n```'))
        self.assertIsNone(parse_complete_visual_response(good, 'length'))
        self.assertIsNone(parse_complete_visual_response(good[:-1]))
        self.assertIsNone(parse_complete_visual_response('{"visual":{"observations":[]}}'))


class VisualRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_client_passes_schema_and_retains_finish_reason(self):
        import httpx
        from PIL import Image
        from backend.services.glm_client import GLMClient
        client = GLMClient()
        await client.client.aclose()
        response = httpx.Response(200, request=httpx.Request('POST', 'http://localhost/test'), json={
            'choices': [{'finish_reason': 'stop', 'message': {'content': '{"visual":{},"summary":"test"}'}}],
            'usage': {'completion_tokens': 10}})
        client._client = AsyncMock()
        client.client.post.return_value = response
        result = await client._analyze_images_with_timestamps([Image.new('RGB', (4, 4))], [0], 'JSON',
                                                             response_schema=LOCAL_VISUAL_SCHEMA)
        payload = client.client.post.call_args.kwargs['json']
        self.assertEqual(payload['response_format']['json_schema']['schema'], LOCAL_VISUAL_SCHEMA)
        self.assertFalse(payload['chat_template_kwargs']['enable_thinking'])
        self.assertEqual(result['finish_reason'], 'stop')

    async def test_incomplete_response_gets_one_counted_retry(self):
        normalize = AsyncMock(side_effect=[{'success': False, 'error': 'incomplete_local_visual_result'}, {'success': True}])
        retry = AsyncMock(return_value={'success': True})
        result = await normalize_visual_with_retry({'finish_reason': 'length'}, normalize, retry)
        self.assertTrue(result['success'])
        self.assertTrue(result['retried_incomplete_response'])
        self.assertEqual(normalize.call_args.args[0]['_external_call_count'], 2)
        retry.assert_awaited_once()

    async def test_no_retry_storm_for_already_retried_or_other_failures(self):
        for result, error in [({'_external_call_count': 2}, 'incomplete_local_visual_result'), ({}, 'network_error')]:
            retry = AsyncMock()
            normalized = {'success': False, 'error': error}
            self.assertEqual(await normalize_visual_with_retry(result, AsyncMock(return_value=normalized), retry), normalized)
            retry.assert_not_called()
