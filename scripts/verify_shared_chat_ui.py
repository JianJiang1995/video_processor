#!/usr/bin/env python3
"""Exercise shared Q&A in an isolated Electron profile, with deterministic API replies."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import quote

from playwright.async_api import async_playwright


async def verify(args):
    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=True)
    results = {"mock_chat_api": True, "real_replay_media": True, "language": args.language, "page_errors": [], "checks": []}
    history = [
        {"id": 1, "role": "user", "content": "UI regression test: review the referenced clips."},
        {"id": 2, "role": "assistant", "content": "UI regression test references: 0:05 and 0:15."},
    ]
    sent = []
    gate = asyncio.Event()
    gate.set()
    spec = str(args.spec.resolve())
    url = args.url + "/?replaySpec=" + quote(spec, safe="")
    with tempfile.TemporaryDirectory(prefix="surgr1-shared-chat-") as profile:
        env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":1"),
               "XAUTHORITY": os.environ.get("XAUTHORITY", "/run/user/1000/gdm/Xauthority"),
               "ELECTRON_USER_DATA_DIR": profile, "ELECTRON_REMOTE_DEBUGGING_PORT": str(args.port),
               "VITE_DEV_SERVER_URL": args.url, "ELECTRON_RECORDING_MODE": "1"}
        with (args.output / "electron.log").open("w") as log:
            process = await asyncio.create_subprocess_exec(
                str(root / "frontend/node_modules/electron/dist/electron"), ".", "--dev", "--no-sandbox",
                cwd=root / "frontend", env=env, stdout=log, stderr=log,
            )
            try:
                async with async_playwright() as playwright:
                    browser = None
                    for _ in range(60):
                        try:
                            browser = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
                            break
                        except Exception:
                            await asyncio.sleep(.5)
                    assert browser, "Electron diagnostics did not become ready"
                    context = browser.contexts[0]
                    page = context.pages[0]
                    page.set_default_timeout(20000)
                    await page.add_init_script(f"localStorage.setItem('surg_ui_language', {json.dumps(args.language)})")
                    page.on("pageerror", lambda error: results["page_errors"].append(str(error)))

                    async def chat_history(route):
                        await route.fulfill(json={"messages": history, "has_more": False})

                    async def chat_send(route):
                        payload = route.request.post_data_json
                        sent.append(payload)
                        history.append({"id": len(history) + 1, "role": "user", "content": payload["content"]})
                        await gate.wait()
                        answer = {"id": len(history) + 1, "role": "assistant", "content": f"UI regression reply {len(sent)}: 0:05 and 0:15."}
                        history.append(answer)
                        await route.fulfill(json={"success": True, "response": answer})

                    await page.route("**/api/voice/chat/*/history?*", chat_history)
                    await page.route("**/api/voice/chat/*/send", chat_send)
                    await page.goto(url)
                    await page.locator(".right-panel").wait_for()
                    await page.locator(".rp-tab").nth(1).click()
                    main = page.locator(".right-panel .chat-panel")
                    modal = page.locator(".review-chat .chat-panel")

                    async def count(panel, expected):
                        await page.wait_for_function(
                            "([selector, count]) => document.querySelectorAll(selector + ' .chat-msg-content:not(.typing)').length === count",
                            arg=[".review-chat .chat-panel" if panel == modal else ".right-panel .chat-panel", expected],
                        )

                    await count(main, 2)
                    await main.locator(".chat-input").fill("Shared draft")
                    await main.get_by_role("link", name="0:05", exact=True).first.click()
                    await count(modal, 2)
                    assert await modal.locator(".chat-input").input_value() == "Shared draft"
                    await modal.get_by_role("link", name="0:15", exact=True).first.click()
                    assert "0:15" in await page.locator(".review-range").inner_text()
                    await count(modal, 2)
                    results["checks"].append("timestamp changes retain messages and draft")

                    await modal.locator(".chat-input").fill("UI regression question from review")
                    await modal.locator(".chat-send-btn").click()
                    await count(modal, 4)
                    assert sent[-1]["review_context"]["start_time"] == 15
                    await page.wait_for_function("""() => {
                      const video = document.querySelector('.review-player video');
                      return video && video.readyState >= 2 && video.videoWidth > 0;
                    }""")
                    results["checks"].append("review video decodes after timestamp navigation")
                    await page.wait_for_timeout(500)
                    await page.screenshot(path=str(args.output / "review_shared_chat.png"))
                    await page.locator(".review-header .review-icon").click()
                    await count(main, 4)
                    results["checks"].append("review exchange is visible on main screen")

                    await main.locator(".chat-input").fill("UI regression follow-up from main")
                    await main.locator(".chat-send-btn").click()
                    await count(main, 6)
                    assert "review_context" not in sent[-1]
                    await main.get_by_role("link", name="0:05", exact=True).first.click()
                    await count(modal, 6)
                    results["checks"].append("main exchange is visible in reopened review")

                    gate.clear()
                    await modal.locator(".chat-input").fill("UI regression pending question")
                    await modal.locator(".chat-send-btn").click()
                    await count(modal, 7)
                    await modal.get_by_role("link", name="0:15", exact=True).first.click()
                    await count(modal, 7)
                    await page.locator(".review-header .review-icon").click()
                    assert await main.locator(".chat-input").is_disabled()
                    gate.set()
                    await count(main, 8)
                    assert sent[-1]["review_context"]["start_time"] == 5
                    await page.screenshot(path=str(args.output / "main_shared_chat.png"))
                    results["checks"].append("pending answer survives clip change and closing modal")

                    await page.reload()
                    await page.locator(".rp-tab").nth(1).click()
                    await count(main, 8)
                    results["checks"].append("reload restores persisted conversation without duplicates")
                    await page.get_by_role("button", name="中文", exact=True).first.click()
                    await main.get_by_role("link", name="0:05", exact=True).first.click()
                    await count(modal, 8)
                    await page.locator(".review-header").get_by_role("button", name="English", exact=True).click()
                    await count(modal, 8)
                    assert await modal.get_by_role("link", name="0:05", exact=True).first.is_visible()
                    results["checks"].append("language switches retain shared messages and working timestamp links")
                    assert not results["page_errors"], results["page_errors"]
                    await page.evaluate("window.close()")
            finally:
                gate.set()
                if process.returncode is None:
                    process.terminate()
                await asyncio.wait_for(process.wait(), 15)
    (args.output / "verification.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5133")
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--language", choices=("zh", "en"), default="zh")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("recordings/shared_chat_verification"))
    asyncio.run(verify(parser.parse_args()))
