import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import voice
from backend.services.conversation_service import ConversationService
from backend.services.glm_client import GLMClient


REVIEW = {"start_time": 70.25, "end_time": 85.5, "window_id": 3}


def window(window_id, start, end, summary):
    return {
        "window_id": window_id,
        "window_start": start,
        "window_end": end,
        "glm_summary": summary,
    }


class ClipChatRequestTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(voice.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.service = Mock()
        self.service.handle_user_input = AsyncMock(return_value={
            "success": True, "response_text": "Recorded answer", "audio_pending": False,
        })
        getter = patch.object(voice, "get_conversation_service", return_value=self.service)
        self.get_service = getter.start()
        self.addCleanup(getter.stop)

    def send(self, **fields):
        return self.client.post("/api/voice/chat/session-a/send", json={
            "role": "user", "content": "What happened here?", **fields,
        })

    def test_valid_context_reaches_service_without_changing_response_shape(self):
        response = self.send(review_context=REVIEW)
        self.assertEqual(response.status_code, 200)
        self.get_service.assert_called_once_with("session-a")
        self.service.handle_user_input.assert_awaited_once_with(
            "What happened here?", review_context=REVIEW,
        )
        data = response.json()
        self.assertEqual(set(data), {
            "success", "user_message", "response", "audio_pending", "glm_id",
        })
        self.assertTrue(data["success"])
        self.assertEqual(data["response"]["content"], "Recorded answer")
        for name in ("user_message", "response"):
            self.assertEqual(set(data[name]), {"role", "content", "timestamp", "audio_base64"})

    def test_context_is_optional_and_zero_based_window_id_is_valid(self):
        for context in (None, {"start_time": 0, "end_time": 1},
                        {"start_time": 0, "end_time": 1, "window_id": 0},
                        {"start_time": 0, "end_time": 1, "window_id": None}):
            with self.subTest(context=context):
                self.service.handle_user_input.reset_mock()
                response = self.send(review_context=context)
                self.assertEqual(response.status_code, 200)
                expected = None if context is None else {
                    key: value for key, value in context.items() if value is not None
                }
                self.service.handle_user_input.assert_awaited_once_with(
                    "What happened here?", review_context=expected,
                )
        self.assertTrue(self.send().json()["success"])
        self.assertIsNone(self.service.handle_user_input.await_args.kwargs["review_context"])

    def test_invalid_ranges_and_window_ids_are_rejected_before_service_lookup(self):
        invalid = [
            {}, {"start_time": 0}, {"end_time": 1},
            {"start_time": -1, "end_time": 1},
            {"start_time": 0, "end_time": -1},
            {"start_time": 1, "end_time": 1},
            {"start_time": 2, "end_time": 1},
            {"start_time": None, "end_time": 1},
            {"start_time": False, "end_time": 1},
            {"start_time": 0, "end_time": True},
            {"start_time": "NaN", "end_time": 1},
            {"start_time": 0, "end_time": "Infinity"},
            {**REVIEW, "summary": "CLIENT INVENTED EVIDENCE"},
        ]
        invalid.extend({**REVIEW, "window_id": value}
                       for value in (-1, 1.5, 1.0, True, "3", [], {}))
        for context in invalid:
            with self.subTest(context=context):
                self.assertEqual(self.send(review_context=context).status_code, 422)
        self.get_service.assert_not_called()
        self.service.handle_user_input.assert_not_awaited()

    def test_nonfinite_numbers_return_422_instead_of_unserializable_errors(self):
        for field in ("start_time", "end_time", "window_id"):
            for literal in ("NaN", "Infinity", "-Infinity", "1e999"):
                with self.subTest(field=field, literal=literal):
                    context = {**REVIEW, field: "NONFINITE"}
                    body = json.dumps({
                        "role": "user", "content": "Explain the record", "review_context": context,
                    }).replace('"NONFINITE"', literal)
                    response = self.client.post(
                        "/api/voice/chat/session-a/send", content=body,
                        headers={"Content-Type": "application/json"},
                    )
                    self.assertEqual(response.status_code, 422)
        self.get_service.assert_not_called()

    def test_top_level_client_summary_is_not_forwarded(self):
        self.assertTrue(self.send(review_context=REVIEW, summary="UNTRUSTED").json()["success"])
        self.service.handle_user_input.assert_awaited_once_with(
            "What happened here?", review_context=REVIEW,
        )

    def test_error_response_shape_is_unchanged(self):
        self.service.handle_user_input.return_value = {"success": False, "error": "unavailable"}
        result = self.send(review_context=REVIEW).json()
        self.assertEqual(set(result), {"success", "user_message", "error"})
        self.assertFalse(result["success"])
        self.assertNotIn("review_context", result["user_message"])

    def test_history_uses_existing_mysql_method_and_preserves_session(self):
        db = Mock()
        db.get_chat_history.return_value = [{"id": 1, "role": "user", "content": "Earlier question"}]
        with patch.object(voice, "get_mysql_service", return_value=db):
            result = self.client.get("/api/voice/chat/session-a/history?limit=7").json()
        db.get_chat_history.assert_called_once_with("session-a", 7)
        self.assertEqual(result["messages"], db.get_chat_history.return_value)
        self.assertEqual(result["total"], 1)
        self.assertNotIn("error", result)

    def test_clear_history_uses_existing_mysql_method(self):
        db = Mock()
        with patch.object(voice, "get_mysql_service", return_value=db):
            result = self.client.delete("/api/voice/chat/session-a").json()
        self.assertTrue(result["success"])
        db.clear_chat.assert_called_once_with("session-a")

    def test_history_cursor_retains_session_scope_and_reports_more_pages(self):
        db = Mock()
        db.get_chat_history.return_value = [{"id": 2, "role": "user", "content": "Earlier question"}]
        with patch.object(voice, "get_mysql_service", return_value=db):
            result = self.client.get("/api/voice/chat/session-a/history?limit=1&before_id=5").json()
        db.get_chat_history.assert_called_once_with("session-a", 1, before_id=5)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["session_id"], "session-a")

    def test_history_rejects_invalid_limits_and_cursors(self):
        for query in ("limit=0", "limit=501", "before_id=0", "before_id=-1"):
            self.assertEqual(self.client.get(f"/api/voice/chat/session-a/history?{query}").status_code, 422)


class ClipChatContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch("backend.services.conversation_service.load_config",
                   return_value={"chat_assistant": {"provider": "glm"}}):
            self.service = ConversationService("session-a")
        self.db = Mock()
        self.db.get_all_window_summaries.return_value = [
            window(0, 0, 50, "OUTSIDE EARLY"),
            window(1, 50, 70.25, "TOUCHES START ONLY"),
            window(4, 80, 90, "RIGHT OVERLAP"),
            window(3, 75, 80, "INSIDE RECORD"),
            window(2, 65, 75, "LEFT OVERLAP"),
            window(5, 85.5, 100, "TOUCHES END ONLY"),
            window(6, 100, 120, "OUTSIDE LATE"),
        ]
        self.service._mysql_service = self.db
        self.db.get_chat_history.return_value = []
        self.db.save_chat.return_value = 99
        self.model = Mock()
        self.model.chat_with_context = AsyncMock(return_value={"success": True, "text": "Scoped answer"})
        self.service._vlm_client = self.model
        self.service._tts_client = Mock(check_health=AsyncMock(return_value=False))
        self.service._summary_compressor = Mock()
        self.service._summary_compressor.get_context_for_chat.return_value = "WHOLE VIDEO CONTEXT"

    def forbid_global_summary(self):
        guard = patch.object(self.service, "_local_summary_answer",
                             side_effect=AssertionError("Whole-video fallback must not run"))
        mock = guard.start()
        self.addCleanup(guard.stop)
        return mock

    async def test_main_chat_followup_uses_questions_from_the_shared_session(self):
        self.db.get_chat_history.return_value = [
            {"id": 1, "role": "user", "content": "PREVIOUS REVIEW QUESTION"},
            {"id": 2, "role": "assistant", "content": "OLD UNVERIFIED ANSWER"},
            {"id": 99, "role": "user", "content": "CURRENT QUESTION"},
        ]
        with patch.object(self.service, "_local_summary_answer", return_value=None):
            await self.service.handle_user_input("Can you explain further?")
        context = self.model.chat_with_context.await_args.kwargs["surgical_context"]
        self.assertIn("WHOLE VIDEO CONTEXT", context)
        self.assertIn("PREVIOUS REVIEW QUESTION", context)
        self.assertIn("not video evidence", context)
        self.assertNotIn("OLD UNVERIFIED ANSWER", context)
        self.assertNotIn("CURRENT QUESTION", context)
        self.db.get_chat_history.assert_called_once_with("session-a", limit=7)

    async def test_model_payload_contains_full_timeline_and_selected_focus(self):
        fallback = self.forbid_global_summary()
        payloads = []

        def respond(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "Scoped answer"}}]})

        client = GLMClient()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        self.service._vlm_client = client
        try:
            result = await self.service.handle_user_input("Summarize what happened", REVIEW)
        finally:
            await client.close()
        self.assertTrue(result["success"])
        self.assertEqual(result["response_text"], "Scoped answer")
        self.assertEqual(len(payloads), 1)
        messages = payloads[0]["messages"]
        prompt = messages[0]["content"]
        context = messages[-1]["content"]
        self.assertEqual(messages[0]["role"], "system")
        for fragment in ("1:10-1:25", "70.25-85.5s", "window_id: 3",
                         "whole-session evidence", "not newly viewed frames",
                         "Cite the record timestamps", "partially overlap",
                         "Do not infer", "insufficient evidence", "search ALL",
                         "First recorded", "not unseen future frames"):
            self.assertIn(fragment, prompt)
        for fragment in ("LEFT OVERLAP", "INSIDE RECORD", "RIGHT OVERLAP",
                         "1:05-1:15", "65-75s", "75-80s", "80-90s",
                         "70.25-85.5s", "Summarize what happened"):
            self.assertIn(fragment, context)
        for fragment in ("OUTSIDE EARLY", "OUTSIDE LATE", "TOUCHES START ONLY", "TOUCHES END ONLY"):
            self.assertIn(fragment, context)
        self.assertNotIn("WHOLE VIDEO CONTEXT", context)
        self.assertLess(context.index("LEFT OVERLAP"), context.index("INSIDE RECORD"))
        self.assertLess(context.index("INSIDE RECORD"), context.index("RIGHT OVERLAP"))
        self.db.get_all_window_summaries.assert_called_once_with("session-a")
        self.service._summary_compressor.get_context_for_chat.assert_not_called()
        fallback.assert_not_called()
        self.assertEqual(self.db.save_chat.call_count, 2)
        self.assertEqual(self.service.mode, "listening")

    async def test_window_id_does_not_override_focus_or_filter_global_evidence(self):
        await self.service.handle_user_input("Explain the record", {**REVIEW, "window_id": 6})
        options = self.model.chat_with_context.await_args.kwargs
        self.assertIn("OUTSIDE LATE", options["surgical_context"])
        focus = options["surgical_context"].split("### Selected review focus")[1].split("\n\n### ")[0]
        self.assertIn("INSIDE RECORD", focus)
        self.assertNotIn("OUTSIDE", focus)
        self.assertNotIn("TOUCHES", focus)
        self.assertFalse(options["include_background"])

    async def test_empty_focus_keeps_other_windows_available_to_model(self):
        fallback = self.forbid_global_summary()
        result = await self.service.handle_user_input(
            "When did an instrument first appear?", {"start_time": 130, "end_time": 150},
        )
        self.assertTrue(result["success"])
        context = self.model.chat_with_context.await_args.kwargs["surgical_context"]
        self.assertIn("No saved analysis overlaps this clip", context)
        self.assertIn("OUTSIDE EARLY", context)
        self.assertIn("OUTSIDE LATE", context)
        fallback.assert_not_called()

    async def test_empty_session_returns_honest_answer_without_model_or_global_fallback(self):
        fallback = self.forbid_global_summary()
        self.db.get_all_window_summaries.return_value = []
        result = await self.service.handle_user_input(
            "How many clips were placed?", {"start_time": 130, "end_time": 150},
        )
        self.assertTrue(result["success"])
        self.assertIn("2:10-2:30", result["response_text"])
        self.assertIn("130-150s", result["response_text"])
        self.assertIn("没有可用的窗口分析记录", result["response_text"])
        self.assertIn("并未重新查看视频帧", result["response_text"])
        self.assertIn("不能据此推断某事件未发生", result["response_text"])
        self.model.chat_with_context.assert_not_awaited()
        self.service._summary_compressor.get_context_for_chat.assert_not_called()
        fallback.assert_not_called()
        self.assertEqual(self.service.mode, "listening")

    async def test_missing_summaries_or_invalid_record_times_are_not_evidence(self):
        self.db.get_all_window_summaries.return_value = [
            window(0, None, 80, "MISSING START"),
            window(1, 75, None, "MISSING END"),
            window(2, "bad", 80, "INVALID START"),
            window(3, float("nan"), 80, "NAN"),
            window(4, 75, float("inf"), "INFINITE"),
            window(5, -1, 80, "NEGATIVE"),
            window(6, 80, 80, "ZERO DURATION"),
            window(7, 80, 75, "REVERSED"),
            window(8, 75, 80, ""),
            window(9, 75, 80, "  \n"),
        ]
        result = await self.service.handle_user_input("What happened?", REVIEW)
        self.assertTrue(result["success"])
        self.assertIn("没有可用的窗口分析记录", result["response_text"])
        self.model.chat_with_context.assert_not_awaited()

    async def test_scoped_model_failures_do_not_use_deterministic_fallback(self):
        fallback = self.forbid_global_summary()
        for raises in (False, True):
            with self.subTest(raises=raises):
                self.model.chat_with_context.return_value = {"success": False, "error": "unavailable"}
                self.model.chat_with_context.side_effect = RuntimeError("unavailable") if raises else None
                result = await self.service.handle_user_input("Summarize what happened", REVIEW)
                self.assertFalse(result["success"])
                self.assertEqual(result["error"], "unavailable")
                self.assertEqual(self.service.mode, "listening")
        fallback.assert_not_called()
        self.service._summary_compressor.get_context_for_chat.assert_not_called()

    async def test_follow_up_includes_bounded_server_dialogue_not_client_evidence(self):
        self.db.get_chat_history.return_value = [
            {"id": 1, "role": "user", "content": "What is happening in this period?"},
            {"id": 2, "role": "assistant", "content": "Old clip-limited answer"},
            {"id": 99, "role": "user", "content": "When did it first appear?"},
        ]
        await self.service.handle_user_input("When did it first appear?", REVIEW)
        options = self.model.chat_with_context.await_args.kwargs
        self.assertIn("Recent user questions (not video evidence)", options["surgical_context"])
        self.assertIn("What is happening in this period?", options["surgical_context"])
        self.assertNotIn("Old clip-limited answer", options["surgical_context"])
        self.assertNotIn("When did it first appear?", options["surgical_context"])
        self.assertIn("never as video evidence", options["system_prompt"])
        self.db.get_chat_history.assert_called_once_with("session-a", limit=7)

    async def test_updated_records_are_loaded_for_each_question_without_other_session_context(self):
        await self.service.handle_user_input("Explain this period", REVIEW)
        self.db.get_all_window_summaries.return_value = [window(9, 0, 5, "UPDATED RECORD")]
        await self.service.handle_user_input("When did an instrument first appear?", REVIEW)
        context = self.model.chat_with_context.await_args.kwargs["surgical_context"]
        self.assertIn("UPDATED RECORD", context)
        self.assertNotIn("INSIDE RECORD", context)
        for call in self.db.get_all_window_summaries.call_args_list:
            self.assertEqual(call.args, ("session-a",))

    async def test_full_timeline_is_ordered_by_time_not_window_id(self):
        self.db.get_all_window_summaries.return_value = [
            window(0, 100, 105, "LATER"), window(1, 0, 5, "EARLIER"),
        ]
        await self.service.handle_user_input("What happened first?", REVIEW)
        context = self.model.chat_with_context.await_args.kwargs["surgical_context"]
        self.assertLess(context.index("EARLIER"), context.index("LATER"))

    async def test_scope_is_request_local_and_whole_video_model_chat_is_unchanged(self):
        await self.service.handle_user_input("Explain the record", REVIEW)
        self.model.chat_with_context.reset_mock()
        result = await self.service.handle_user_input("Explain the record")
        self.assertTrue(result["success"])
        self.model.chat_with_context.assert_awaited_once_with(
            user_query="Explain the record", surgical_context="WHOLE VIDEO CONTEXT", disable_thinking=True,
        )
        self.service._summary_compressor.get_context_for_chat.assert_called_once_with()

    async def test_whole_video_deterministic_summary_is_unchanged(self):
        result = await self.service.handle_user_input("Give a summary")
        self.assertTrue(result["success"])
        self.assertEqual(result["provider"], "local_summary")
        self.assertIn("OUTSIDE EARLY", result["response_text"])
        self.assertIn("OUTSIDE LATE", result["response_text"])
        self.model.chat_with_context.assert_not_awaited()

    async def test_whole_video_error_fallback_is_unchanged(self):
        for raises in (False, True):
            with self.subTest(raises=raises), patch.object(
                self.service, "_local_summary_answer", side_effect=[None, "Whole-video fallback"],
            ) as fallback:
                self.model.chat_with_context.return_value = {"success": False, "error": "unavailable"}
                self.model.chat_with_context.side_effect = RuntimeError("unavailable") if raises else None
                result = await self.service.handle_user_input("Explain the record")
                self.assertTrue(result["success"])
                self.assertEqual(result["response_text"], "Whole-video fallback")
                self.assertEqual(result["provider"], "local_summary_fallback")
                self.assertEqual(fallback.call_count, 2)


if __name__ == "__main__":
    unittest.main()
