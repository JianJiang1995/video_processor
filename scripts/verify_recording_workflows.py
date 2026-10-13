#!/usr/bin/env python3
"""Real simulator, inference, MP4 recording and UI interactions; no mocked AI."""
import argparse
import asyncio
import json
import math
import re
import shutil
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

APP = "document.querySelector('#app').__vue_app__._container._vnode.component.setupState"
REC = "document.querySelector('.screen-recording-control').__vueParentComponent.setupState"
VIDEO = '.primary-video-player video'


async def verify(args):
    args.output.mkdir(parents=True, exist_ok=True)
    result = {'passed': False, 'mocked_ai': False, 'checks': [], 'page_errors': []}
    async with async_playwright() as p, httpx.AsyncClient(base_url='http://127.0.0.1:8001', timeout=120, trust_env=False) as api:
        browser = await p.chromium.connect_over_cdp(args.cdp)
        page = browser.contexts[0].pages[0]
        page.set_default_timeout(30000)
        assert not await page.evaluate(f'{APP}.isProcessing'), 'Do not interrupt active analysis'
        previous = await page.evaluate("window.electronAPI.getConfig('autoRecordAnalysis')")
        await page.evaluate("window.electronAPI.setConfig('autoRecordAnalysis', false)")
        await page.goto('http://127.0.0.1:5133/')
        await page.wait_for_function(f'{APP}.handleStreamConnect !== undefined')
        await page.evaluate(f"{APP}.setLanguage('zh')")
        page.on('pageerror', lambda e: result['page_errors'].append(str(e)))
        response = await api.post('/api/video/connect-capture', json={
            'backend': 'simulator', 'simulator_path': str(args.source.resolve()),
            'input_layout': 'quad_right', 'auto_analyze': True})
        response.raise_for_status()
        sid = response.json()['session_id']
        session = (await api.get(f'/api/video/session/{sid}')).json()
        expected_windows = max(1, math.ceil((session['duration'] - .5 / max(1, session['fps'])) / 5))
        result['session_id'] = sid
        try:
            await page.evaluate(f"session => {{ const a={APP}; a.mode='stream'; a.handleStreamConnect({{session,autoAnalyze:true}}); }}", session)
            await page.wait_for_function(f"document.querySelector('{VIDEO}')?.currentTime > 10")
            await page.get_by_test_id('screen-recording-toggle').click()
            await page.wait_for_function(f"{REC}.status.state === 'recording'")
            await page.evaluate(f"window.__workflowRecordingTrack = {REC}.recorder.stream.getVideoTracks()[0]")

            async def video_time():
                return await page.locator(VIDEO).first.evaluate('v => v.currentTime')

            await page.locator('.controls-bar .control-btn.primary').click()
            await page.wait_for_function(f"document.querySelector('{VIDEO}').paused")
            paused = await video_time()
            await asyncio.sleep(2)
            assert abs(await video_time() - paused) < .15, 'Pause did not freeze video'
            await page.locator('.controls-bar .control-btn.primary').click()
            await asyncio.sleep(2)
            assert await video_time() > paused + 1, 'Resume reset or stalled video'
            result['checks'].append('pause freezes video; resume preserves time')

            await page.wait_for_function(f'{APP}.summaries.length >= 2')
            before = await video_time()
            await page.evaluate(f'{APP}.openReviewWindow(0)')
            await page.wait_for_function("document.querySelector('.review-player video')?.readyState >= 2")
            times = []
            for _ in range(28):
                times.append(await page.locator('.review-player video').evaluate('v => v.currentTime'))
                await asyncio.sleep(.25)
            assert any(b < a - 1 for a, b in zip(times, times[1:])), 'Review did not loop'
            assert await video_time() > before + 5, 'Review interrupted main stream'
            result['review_times'] = times
            await page.screenshot(path=str(args.output / 'review.png'))
            await page.locator('.review-header .review-icon').click()
            result['checks'].append('review loops independently while main stream advances')

            before = await video_time()
            await page.evaluate(f'{APP}.enterOverview()')
            await page.locator('.window-card').first.click()
            await page.wait_for_function("document.querySelector('.review-player video')?.readyState >= 2")
            await asyncio.sleep(2)
            await page.locator('.review-header .review-icon').click()
            await page.locator('.overview-header .back-btn').click()
            await asyncio.sleep(2)
            assert await video_time() > before + 2, 'Grid return reset or stalled main video'
            result['checks'].append('grid review and return preserve live video clock')

            await page.locator('.rp-tab').nth(1).click()
            main = page.locator('.right-panel .chat-panel')
            question = '请概括目前已分析的手术进展，并列出关键操作的时间戳；没有证据的内容不要推断。'
            await main.locator('.chat-input').fill(question)
            await main.locator('.chat-send-btn').click()
            await page.wait_for_function("!document.querySelector('.right-panel .chat-panel .chat-input').disabled", timeout=180000)
            answer = await main.locator('.chat-msg.assistant .chat-msg-content:not(.typing)').last.inner_text()
            assert answer.strip() and not answer.startswith(('错误', 'Error')), answer
            result['qa'] = {'question': question, 'answer': answer}
            count = await main.locator('.chat-msg').count()
            await page.evaluate(f'{APP}.openReviewWindow(1)')
            modal = page.locator('.review-chat .chat-panel')
            assert await modal.locator('.chat-msg').count() == count
            assert answer in await modal.inner_text()
            await page.evaluate(f'{APP}.openReviewWindow(0)')
            assert await modal.locator('.chat-msg').count() == count
            await page.locator('.review-header .review-icon').click()
            assert await main.locator('.chat-msg').count() == count
            await page.screenshot(path=str(args.output / 'chat.png'))
            result['checks'].append('real local VLM Q&A; messages shared across main/review/range changes')

            for _ in range(150):
                state = (await api.get(f'/api/analysis/pending-refinements/{sid}')).json()
                at_eof = await page.evaluate(f"{APP}.streamEnded || document.querySelector('{VIDEO}')?.ended")
                if state.get('settled') and at_eof:
                    break
                await asyncio.sleep(1)
            assert state.get('settled') and at_eof, 'Analysis/video did not reach EOF'
            result['eof_video'] = await page.locator(VIDEO).first.evaluate('v => ({time:v.currentTime,duration:v.duration,paused:v.paused,ended:v.ended})')
            assert result['eof_video']['paused'] and abs(result['eof_video']['duration'] - result['eof_video']['time']) < .5, 'EOF stopped the video before its final frames'
            assert await page.evaluate("document.querySelector('.controls-bar').__vueParentComponent.setupState.currentWindowId") == expected_windows - 1, 'EOF displays a phantom analysis window'
            rows = (await api.get(f'/api/analysis/summaries/{sid}')).json()
            assert len(rows) == expected_windows and all(r['others']['stage'] == 2 for r in rows)
            result['window_count'] = len(rows)
            await asyncio.sleep(3)
            assert len((await api.get(f'/api/analysis/summaries/{sid}')).json()) == len(rows)
            result['checks'].append('EOF stops analysis; all windows receive final updates')

            count = await main.locator('.chat-msg-content:not(.typing)').count()
            await main.locator('.chat-input').fill('Shared grid draft')
            await page.evaluate(f'{APP}.enterOverview()')
            await page.locator('.chat-toggle-btn').click()
            grid = page.locator('.overview-container .chat-panel')
            assert await grid.locator('.chat-msg-text:not(.typing-indicator)').count() == count
            assert await grid.locator('.chat-input').input_value() == 'Shared grid draft'
            grid_question = '最后两个分析窗口分别描述了什么操作？请给出对应时间戳，不要推断未记录的操作。'
            await grid.locator('.chat-input').fill(grid_question)
            await grid.locator('.chat-send-btn').click()
            await page.locator('.overview-header .back-btn').click()
            await page.wait_for_function("!document.querySelector('.right-panel .chat-panel .chat-input').disabled", timeout=180000)
            assert await main.locator('.chat-msg-content:not(.typing)').count() == count + 2
            grid_answer = await main.locator('.chat-msg.assistant .chat-msg-content:not(.typing)').last.inner_text()
            assert grid_answer.strip() and not grid_answer.startswith(('错误', 'Error')), grid_answer
            result['grid_qa'] = {'question': grid_question, 'answer': grid_answer}
            normalized = grid_answer.replace('：', ':')
            for row in sorted(rows, key=lambda row: row['start_time'])[-2:]:
                start = f"{int(row['start_time']) // 60}:{int(row['start_time']) % 60:02d}"
                end = f"{int(row['end_time']) // 60}:{int(row['end_time']) % 60:02d}"
                assert re.search(rf'{re.escape(start)}\s*[-–—至到]\s*{re.escape(end)}', normalized), 'Answer shifted a saved timestamp'
                assert re.search(rf'(?:窗口|Window)\s*#?\s*{row["window_id"] + 1}\b', normalized, re.I), 'Answer used a different window number than UI'
            result['checks'].append('Q&A cites the actual final two windows and their stored timestamps')
            await page.evaluate(f'{APP}.enterOverview()')
            await page.locator('.chat-toggle-btn').click()
            assert await grid.locator('.chat-msg-text:not(.typing-indicator)').count() == count + 2
            await page.screenshot(path=str(args.output / 'grid_chat.png'))
            await grid.locator('.chat-msg.assistant .chat-msg-text').last.get_by_role('link').first.click()
            await page.wait_for_function("document.querySelector('.review-player video')?.readyState >= 2")
            assert await modal.locator('.chat-msg-content:not(.typing)').count() == count + 2
            await page.screenshot(path=str(args.output / 'grid_timestamp_review.png'))
            await page.locator('.review-header .review-icon').click()
            await page.locator('.overview-header .back-btn').click()
            result['checks'].append('real grid Q&A survives return; timestamp opens playback with shared messages')
            assert not result['page_errors'], result['page_errors']
            result['passed'] = True
        except Exception as error:
            result['error'] = f'{type(error).__name__}: {error}'
            await page.screenshot(path=str(args.output / 'failure.png'))
            raise
        finally:
            await page.evaluate(f"{REC}.recorder.stop('workflow_verification')")
            result['recording'] = await page.evaluate(f'{REC}.status')
            result['capture_track_released'] = await page.evaluate("window.__workflowRecordingTrack?.readyState === 'ended'")
            if result['recording'].get('path'):
                source = Path(result['recording']['path'])
                target = args.output / ('workflow' + source.suffix)
                shutil.copy2(source, target)
                if Path(str(source) + '.json').exists():
                    shutil.copy2(str(source) + '.json', str(target) + '.json')
            await page.evaluate("value => window.electronAPI.setConfig('autoRecordAnalysis', value)", previous is True)
            await page.evaluate(f"value => {{ {REC}.autoRecord = value }}", previous is True)
            (args.output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cdp', default='http://127.0.0.1:9222')
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(verify(parser.parse_args()))
