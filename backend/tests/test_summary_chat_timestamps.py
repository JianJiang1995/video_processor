import unittest
from unittest.mock import Mock

from backend.services.summary_compressor import SummaryCompressor


class SummaryChatTimestampsTests(unittest.TestCase):
    def setUp(self):
        self.service = SummaryCompressor('test')
        self.db = Mock()
        self.service._mysql_service = self.db
        self.db.get_compressed_summaries_context.return_value = '暂无手术记录总结。'
        self.records = [
            {'window_id': 0, 'window_start': 0, 'window_end': 2, 'glm_summary': 'First record'},
            {'window_id': 9, 'window_start': 17, 'window_end': 23, 'glm_summary': 'Later record'},
        ]
        self.db.get_uncompressed_windows.return_value = self.records

    def test_chat_uses_ui_numbering_and_saved_variable_length_timestamps(self):
        text = self.service.get_context_for_chat()
        self.assertIn('### 窗口 1 (0:00 - 0:02)', text)
        self.assertIn('### 窗口 10 (0:17 - 0:23)', text)
        self.assertNotIn('### 窗口 0 ', text)
        self.assertEqual(self.records[0]['window_id'], 0)
        self.assertEqual(self.records[1]['window_id'], 9)

    def test_compression_uses_the_same_numbering_without_shifting_timestamps(self):
        text = self.service._format_windows_for_compression(self.records)
        self.assertIn('### 窗口 1 (0:00 - 0:02)', text)
        self.assertIn('### 窗口 10 (0:17 - 0:23)', text)

    def test_missing_timestamps_are_not_computed_from_window_number(self):
        self.db.get_uncompressed_windows.return_value = [
            {'window_id': 15, 'window_start': None, 'window_end': None, 'glm_summary': 'No recorded time'},
        ]
        text = self.service.get_context_for_chat()
        self.assertIn('### 窗口 16', text)
        self.assertNotIn('1:15', text)


if __name__ == '__main__':
    unittest.main()
