"""Real Electron/local-model regression for grid playback, Q&A and reanalysis.

Run after connecting the capture-card simulator in Electron (CDP 9222).
No playback or model responses are mocked in the successful workflow.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import httpx
from playwright.async_api import async_playwright


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    recorder = None
    result = {}
    async with async_playwright() as p, httpx.AsyncClient(
        base_url="http://127.0.0.1:8001", timeout=100, trust_env=False,
    ) as api:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        page.set_default_timeout(30000)
        errors, mutations = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def mutation(request):
            if request.method == "POST" and "/api/video/control/" in request.url:
                body = request.post_data_json or {}
                if body.get("action") != "position":
                    mutations.append(body)

        page.on("request", mutation)
        await page.wait_for_function("""count => {
          const a=document.querySelector('#app').__vue_app__;
          return (a._instance || a._container._vnode.component).setupState.summaries.length >= count;
        }""", arg=max(3, args.window_index + 1), timeout=120000)
        sid = await page.evaluate("""() => {
          const a=document.querySelector('#app').__vue_app__;
          return (a._instance || a._container._vnode.component).setupState.currentSession.session_id;
        }""")
        result["session_id"] = sid
        result["session"] = (await api.get(f"/api/video/session/{sid}")).json()
        await page.bring_to_front()
        window = subprocess.check_output([
            "xdotool", "search", "--onlyvisible", "--name", "Surg-R1 手术助手",
        ], text=True).splitlines()[0]
        log = (args.output / "recorder.log").open("w")
        recorder = subprocess.Popen([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
            "-f", "x11grab", "-draw_mouse", "0", "-framerate", "20",
            "-window_id", window, "-i", os.environ.get("DISPLAY", ":1"), "-an",
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "h264_nvenc",
            "-gpu", "0", "-preset", "p4", "-cq", "25", "-b:v", "6M", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(args.output / "grid_review_demo.mp4"),
        ], stdout=log, stderr=log)
        try:
            main_video = page.locator(".video-section video")
            before = await main_video.evaluate("v=>({time:v.currentTime,paused:v.paused,src:v.currentSrc})")
            started = time.monotonic()
            if not await page.locator(".window-card").count():
                await page.locator(".bcs-overview-btn").click()
            await page.locator(".window-card").nth(args.window_index).click()
            modal = page.locator(".review-dialog")
            await modal.wait_for()
            await page.wait_for_function("document.querySelector('.review-dialog video')?.readyState >= 2")
            original = await modal.locator(".review-analysis-record p").inner_text()
            await page.screenshot(path=str(args.output / "grid_review_before.png"))
            loop_samples = []
            for _ in range(20):
                loop_samples.append(await modal.locator("video").evaluate("v=>({time:v.currentTime,paused:v.paused})"))
                await asyncio.sleep(.5)
            assert any(b["time"] < a["time"] - 1 for a, b in zip(loop_samples, loop_samples[1:])), loop_samples
            await modal.get_by_role("button", name="暂停片段", exact=True).click()
            t1 = await modal.locator("video").evaluate("v=>v.currentTime")
            await asyncio.sleep(1)
            t2 = await modal.locator("video").evaluate("v=>v.currentTime")
            assert abs(t1 - t2) < .1, (t1, t2)
            await modal.get_by_role("button", name="播放片段", exact=True).click()

            infer_start = time.monotonic()
            async with page.expect_response(lambda r: "/reanalyze-window/" in r.url, timeout=105000) as pending:
                await modal.get_by_role("button", name="重新分析", exact=True).click()
                await modal.get_by_role("button", name="正在分析", exact=True).wait_for()
                await page.screenshot(path=str(args.output / "grid_review_pending.png"))
            response = await pending.value
            assert response.status == 200, await response.text()
            updated = await response.json()
            result["reanalysis_seconds"] = round(time.monotonic() - infer_start, 3)
            await modal.get_by_text("已通过本地模型重新分析", exact=True).wait_for()
            result["original_summary"] = original
            result["reanalysis"] = updated
            assert len(updated["others"]["manual_review"]["timestamps"]) > 0
            rows = (await api.get(f"/api/analysis/summaries/{sid}")).json()
            saved = next(row for row in rows if row["window_id"] == updated["window_id"])
            assert saved["summary"] == updated["summary"]

            question = "这个片段中看到了什么？请只依据所选片段记录回答，并附上对应时间戳。"
            await modal.locator(".chat-input").fill(question)
            async with page.expect_response(lambda r: "/api/voice/chat/" in r.url and r.url.endswith("/send"), timeout=100000) as pending:
                await modal.locator(".chat-send-btn").click()
            chat_response = await pending.value
            answer = await chat_response.json()
            assert answer.get("success"), answer
            scope = chat_response.request.post_data_json["review_context"]
            assert scope["start_time"] == updated["start_time"]
            assert scope["end_time"] == updated["end_time"]
            result["chat"] = answer
            result["chat_context"] = scope
            await modal.locator(".chat-msg.assistant").last.wait_for()
            await page.screenshot(path=str(args.output / "grid_review_after.png"))
            await asyncio.sleep(3)
            citation = modal.locator(".chat-msg.assistant a.chat-timestamp").first
            if await citation.count():
                await citation.click()
                assert await page.locator(".review-dialog").count() == 1
                result["chat_timestamp_link"] = True

            # Exercise a failed request separately; the successful calls above
            # used the actual local model. A failed review must preserve text.
            before_failure = await modal.locator(".review-analysis-record p").inner_text()
            async def failed_request(route):
                await route.fulfill(status=503, content_type="application/json",
                                    body='{"detail":"review_model_unavailable"}')
            await page.route("**/api/analysis/reanalyze-window/**", failed_request)
            await modal.get_by_role("button", name="重新分析", exact=True).click()
            await modal.locator(".review-result.error").wait_for()
            assert await modal.locator(".review-analysis-record p").inner_text() == before_failure
            await page.unroute("**/api/analysis/reanalyze-window/**", failed_request)
            result["failure_preserves_summary"] = True

            # Start a genuine retry, then close/reopen while inference runs.
            async with page.expect_response(lambda r: "/reanalyze-window/" in r.url, timeout=105000) as pending:
                await modal.get_by_role("button", name="重新分析", exact=True).click()
                await modal.get_by_role("button", name="正在分析", exact=True).wait_for()
                await modal.get_by_role("button", name="关闭回看", exact=True).click()
                await page.locator(".window-card").nth(args.window_index).click()
            assert (await pending.value).status == 200
            await modal.get_by_text("已通过本地模型重新分析", exact=True).wait_for()
            result["close_during_review"] = True

            original_viewport = page.viewport_size
            await page.set_viewport_size({"width": 390, "height": 844})
            await asyncio.sleep(.5)
            await page.screenshot(path=str(args.output / "mobile_review.png"))
            result["mobile"] = await modal.evaluate("""el => {
              const r=el.getBoundingClientRect(), i=el.querySelector('.chat-input').getBoundingClientRect();
              return {overflow:el.scrollWidth>el.clientWidth+1, inputVisible:i.x>=r.x && i.right<=r.right && i.bottom<=r.bottom};
            }""")
            await page.set_viewport_size(original_viewport or {"width": 2048, "height": 1125})
            assert not result["mobile"]["overflow"] and result["mobile"]["inputVisible"]
            await modal.get_by_role("button", name="关闭回看", exact=True).click()
            await page.locator(".overview-layer .back-btn").click()
            await asyncio.sleep(2)
            after = await main_video.evaluate("v=>({time:v.currentTime,paused:v.paused,src:v.currentSrc})")
            elapsed = time.monotonic() - started
            assert before["src"] == after["src"]
            assert not after["paused"]
            assert abs((after["time"] - before["time"]) - elapsed) < 2, (before, after, elapsed)
            assert not mutations, mutations
            assert not errors, errors
            result.update(main_before=before, main_after=after, elapsed=elapsed,
                          loop_samples=loop_samples, playback_mutations=mutations, page_errors=errors)
            await page.screenshot(path=str(args.output / "main_after_review.png"))
            print(json.dumps({"session_id": sid, "reanalysis_seconds": result["reanalysis_seconds"],
                              "main_advanced": after["time"] - before["time"], "elapsed": elapsed,
                              "result": "PASS"}, ensure_ascii=False), flush=True)
        finally:
            result["page_errors"] = errors
            (args.output / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
            if recorder and recorder.poll() is None:
                recorder.send_signal(signal.SIGINT)
                await asyncio.to_thread(recorder.wait, 30)
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--window-index", type=int, default=1)
    asyncio.run(main(parser.parse_args()))
