import json
import unittest

import httpx

from backend.services.chat_context import EXCERPT_NOTICE, select_context_excerpts
from backend.services.glm_client import GLMClient


class ChatContextTests(unittest.TestCase):
    def test_short_context_is_unchanged(self):
        assert select_context_excerpts("entire record", "query", 100) == "entire record"


    def test_excerpt_preserves_relevant_early_record_and_time_order(self):
        records = [f"### Window {i} ({i}:00)\n" + ("bleeding" if i == 2 else "routine") * 30 for i in range(20)]
        context = "\n\n".join(records)
        result = select_context_excerpts(context, "bleeding", 1100)
        assert len(result) <= 1100
        assert records[2] in result
        assert records[19] in result
        assert result.index(records[2]) < result.index(records[19])
        assert result.startswith(EXCERPT_NOTICE)


    def test_chinese_query_retrieves_matching_record(self):
        records = [f"### 窗口 {i}\n" + ("镜头起雾" if i == 3 else "常规操作") * 40 for i in range(20)]
        result = select_context_excerpts("\n\n".join(records), "什么时候起雾", 800)
        assert records[3] in result


    def test_oversized_record_is_not_cut_into_false_evidence(self):
        result = select_context_excerpts("### Window 1\n" + "evidence " * 500, "query", 500)
        assert result == EXCERPT_NOTICE.rstrip()


class ChatRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_context_overflow_retries_without_losing_question_or_system_prompt(self):
        payloads = []
        def respond(request):
            payloads.append(json.loads(request.content))
            if len(payloads) == 1:
                return httpx.Response(400, json={"error": {"message": "maximum context length is 12288 tokens"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": "Ready"}}]})
        client = GLMClient()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        context = "\n\n".join(f"### Window {i}\n" + "record " * 50 for i in range(30))
        try:
            result = await client.chat_with_context("my question", context, system_prompt="preserve instructions")
            assert result["success"] and result["context_excerpted"]
            assert len(payloads) == 2
            assert payloads[0]["messages"][0] == payloads[1]["messages"][0]
            assert payloads[1]["messages"][-1]["content"].endswith("my question")
            assert EXCERPT_NOTICE in payloads[1]["messages"][-1]["content"]
        finally:
            await client.close()


    async def test_other_http_errors_do_not_shrink_or_retry_context(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(400, json={"error": {"message": "unknown model"}})
        client = GLMClient()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            result = await client.chat_with_context("question", "context", include_background=False)
            assert not result["success"]
            assert not result["context_excerpted"]
            assert len(calls) == 1
        finally:
            await client.close()
