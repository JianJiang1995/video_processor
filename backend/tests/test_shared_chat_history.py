"""Exercise history persistence and cursor isolation without the production database."""
import unittest
from datetime import datetime

from sqlalchemy import create_engine

from backend.services.mysql_service import ChatHistory, MySQLService


class SharedChatHistoryTests(unittest.TestCase):
    def setUp(self):
        self.db = object.__new__(MySQLService)
        self.db._engine = create_engine("sqlite:///:memory:")
        self.db._SessionLocal = None
        ChatHistory.__table__.create(self.db._engine)
        self.addCleanup(self.db._engine.dispose)

    def test_all_pages_are_chronological_and_never_include_another_video(self):
        expected = []
        for index in range(5):
            expected.append(self.db.save_chat("video-a", "user", f"Question {index}"))
            self.db.save_chat("video-b", "user", "Other video")
        # A common timestamp must not cause repeated or skipped messages.
        with self.db.get_session() as session:
            session.query(ChatHistory).update({ChatHistory.created_at: datetime(2026, 10, 7)})
        cursor = None
        restored = []
        while True:
            page = self.db.get_chat_history("video-a", limit=2, before_id=cursor)
            if not page:
                break
            restored = page + restored
            cursor = page[0]["id"]
        self.assertEqual([message["id"] for message in restored], expected)
        self.assertEqual([message["content"] for message in restored], [f"Question {i}" for i in range(5)])
        self.assertEqual(self.db.get_chat_history("missing-video"), [])

    def test_existing_default_history_shape_and_order_are_preserved(self):
        user_id = self.db.save_chat("video-a", "user", "Question")
        answer_id = self.db.save_chat("video-a", "assistant", "Answer at 0:05")
        history = self.db.get_chat_history("video-a")
        self.assertEqual([message["id"] for message in history], [user_id, answer_id])
        self.assertEqual(set(history[0]), {"id", "role", "content", "audio_duration", "created_at"})
        self.db.clear_chat("video-a")
        self.assertEqual(self.db.get_chat_history("video-a"), [])
