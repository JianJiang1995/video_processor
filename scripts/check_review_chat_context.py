"""Repeat saved user questions in the open Electron review, using the real local model."""

import argparse
import asyncio
import json
from pathlib import Path
import time

import httpx
from playwright.async_api import async_playwright


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p, httpx.AsyncClient(
        base_url="http://127.0.0.1:8001", timeout=120, trust_env=False,
    ) as api:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        modal = page.locator(".review-dialog")
        await modal.wait_for()
        history = (await api.get(f"/api/voice/chat/{args.session}/history?limit=100")).json()
        assert "error" not in history, history
        questions = [message["content"] for message in history["messages"]
                     if message["role"] == "user"][:2]
        assert len(questions) == 2, "Two saved user questions are required"
        result = {"session_id": args.session, "original_history": history["messages"], "checks": []}
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        for index, question in enumerate(questions):
            count = await modal.locator(".chat-msg.assistant").count()
            await modal.locator(".chat-input").fill(question)
            started = time.monotonic()
            async with page.expect_response(
                lambda response: f"/api/voice/chat/{args.session}/send" in response.url,
                timeout=120000,
            ) as pending:
                await modal.locator(".chat-send-btn").click()
            response = await pending.value
            answer = await response.json()
            assert response.status == 200 and answer.get("success"), answer
            await page.wait_for_function(
                "count => document.querySelectorAll('.review-dialog .chat-msg.assistant').length > count",
                arg=count,
            )
            request = response.request.post_data_json
            latest = modal.locator(".chat-msg.assistant").last
            links = await latest.locator("a.chat-timestamp").evaluate_all(
                "links => links.map(a => ({text: a.textContent, href: a.getAttribute('href')}))"
            )
            check = {"question": question, "request": request, "answer": answer,
                     "elapsed_seconds": round(time.monotonic() - started, 3), "links": links}
            result["checks"].append(check)
            (args.output / "verification.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            await page.screenshot(path=str(args.output / f"question_{index + 1}.png"))
            print(json.dumps(check, ensure_ascii=False), flush=True)

        links = modal.locator(".chat-msg.assistant").last.locator("a.chat-timestamp")
        if await links.count():
            initial_range = await modal.locator(".review-range").inner_text()
            await links.first.click()
            await page.wait_for_function(
                "before => document.querySelector('.review-range')?.textContent !== before",
                arg=initial_range,
            )
            await page.wait_for_function("document.querySelector('.review-dialog video')?.readyState >= 2")
            result["linked_range"] = await modal.locator(".review-range").inner_text()
            result["linked_analysis"] = await modal.locator(".review-analysis").inner_text()
            before = await modal.locator("video").evaluate("v=>v.currentTime")
            await asyncio.sleep(1)
            after = await modal.locator("video").evaluate("v=>v.currentTime")
            assert abs(after - before) > .2, (before, after)
            assert await page.locator(".review-dialog").count() == 1
            result["linked_playback_moves"] = True
            await page.screenshot(path=str(args.output / "timestamp_review.png"))

        after_history = (await api.get(f"/api/voice/chat/{args.session}/history?limit=100")).json()
        ids = {message["id"] for message in after_history["messages"]}
        assert all(message["id"] in ids for message in history["messages"])
        result["original_history_preserved"] = True
        result["page_errors"] = errors
        (args.output / "verification.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        assert not errors, errors
        print(json.dumps({key: value for key, value in result.items()
                          if key not in {"original_history", "checks"}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
