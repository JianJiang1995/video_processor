import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from backend.services import ui_translation as translation


class UiTranslationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        translation._cache.clear()
        translation._lock = asyncio.Lock()
        self.client = SimpleNamespace(api_url='http://127.0.0.1:8010/v1', model_name='local',
            chat=AsyncMock(return_value={'success': True, 'text': 'No active bleeding is seen.'}))
        self.patch = patch.object(translation, 'get_glm_client', return_value=self.client)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    async def test_cached_text_only_translation(self):
        result = await translation.translate_ui_text('未见活动性出血')
        self.assertEqual(result['text'], 'No active bleeding is seen.')
        self.assertEqual(result['provider'], 'local')
        self.assertTrue((await translation.translate_ui_text('未见活动性出血'))['cached'])
        self.assertEqual(self.client.chat.await_count, 1)
        arguments = self.client.chat.call_args.kwargs
        self.assertTrue(arguments['disable_thinking'])
        self.assertIn('negation', arguments['system_prompt'])
        self.assertIn('cystic artery', arguments['system_prompt'])

    async def test_concurrent_identical_requests_are_deduplicated(self):
        await asyncio.gather(*(translation.translate_ui_text('未见活动性出血') for _ in range(4)))
        self.assertEqual(self.client.chat.await_count, 1)

    async def test_failures_never_generate_clinical_fallback(self):
        for result in [{'success': False, 'text': ''}, {'success': True, 'text': '仍然中文'},
                       {'success': True, 'text': 'The cystic', 'finish_reason': 'length'}]:
            self.client.chat.return_value = result
            with self.assertRaises(ValueError):
                await translation.translate_ui_text('未见活动性出血')
            self.assertFalse(translation._cache)

    async def test_external_endpoint_rejected(self):
        self.client.api_url = 'https://external.example/v1'
        with self.assertRaises(ValueError):
            await translation.translate_ui_text('未见活动性出血')
        self.client.chat.assert_not_called()

    async def test_english_passthrough_and_chinese_translation(self):
        self.assertEqual((await translation.translate_ui_text('No bleeding.'))['text'], 'No bleeding.')
        self.client.chat.assert_not_called()
        self.client.chat.return_value = {'success': True, 'text': '未见出血。'}
        self.assertEqual((await translation.translate_ui_text('No bleeding.', 'zh'))['text'], '未见出血。')

    async def test_reject_invalid_language_and_oversized_input(self):
        with self.assertRaises(ValueError):
            await translation.translate_ui_text('出血', 'xx')
        with self.assertRaises(ValueError):
            await translation.translate_ui_text('出血' * 4000)

    async def test_timestamp_omission_retries_without_inventing_a_review_link(self):
        self.client.chat.side_effect = [
            {'success': True, 'text': 'The grasper retracts the gallbladder.'},
            {'success': True, 'text': '0:40-0:45 The grasper retracts the gallbladder.'},
        ]
        result = await translation.translate_ui_text('0:40-0:45 抓钳牵拉胆囊。')
        self.assertIn('0:40-0:45', result['text'])
        self.assertEqual(self.client.chat.await_count, 2)

    async def test_changed_or_added_timestamps_are_rejected_and_not_cached(self):
        for text in ('1:40-1:45 No active bleeding.', 'No active bleeding. See 1:00.'):
            self.client.chat.return_value = {'success': True, 'text': text}
            with self.assertRaises(ValueError):
                await translation.translate_ui_text('0:40-0:45 未见活动性出血。')
            self.assertFalse(translation._cache)

    async def test_chinese_punctuation_and_adjacent_words_do_not_hide_timestamps(self):
        self.client.chat.return_value = {'success': True, 'text': 'At 0:05 and 0:15, no bleeding is seen.'}
        result = await translation.translate_ui_text('记录：0:05和0:15，未见出血。')
        self.assertEqual(result['text'], self.client.chat.return_value['text'])
        self.client.chat.return_value = {'success': True, 'text': '回看：0:05和0:15。'}
        result = await translation.translate_ui_text('Review 0:05 and 0:15.', 'zh')
        self.assertEqual(result['text'], '回看：0:05和0:15。')

    async def test_markdown_range_heading_and_time_followed_by_colon(self):
        self.client.chat.return_value = {'success': True, 'text': '### 0:00-4:00 Preparation\n- **Transition**: Dissection after 4:00.\n- 4:00-18:08: CVS not confirmed.'}
        result = await translation.translate_ui_text('### 0:00-4:00 准备\n- **衔接**：4:00后进行分离。\n- 4:00-18:08：CVS未确认。')
        self.assertTrue(result['success'])
        self.assertEqual(self.client.chat.await_count, 1)
