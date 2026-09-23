#!/usr/bin/env python3
"""Exercise a settled replay in Electron, including optional real UI reanalysis."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
from urllib.parse import urlencode, urlsplit

from playwright.async_api import async_playwright


async def inspect(args):
    args.output.mkdir(parents=True, exist_ok=True)
    spec = json.loads(args.spec.read_text())
    rows = json.loads((args.spec.parent / spec['summaries_path']).read_text())
    candidates = json.loads(args.candidates.read_text()) if args.candidates else []
    ids = args.window_ids or [r['window_id'] for r in candidates] or [rows[len(rows) // 2]['window_id']]
    result = {'session_id': spec['session']['session_id'], 'checks': [], 'page_errors': []}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://127.0.0.1:9222')
        page = browser.contexts[0].pages[0]
        page.set_default_timeout(30000)
        page.on('pageerror', lambda err: result['page_errors'].append(str(err)))
        origin = urlsplit(page.url)
        url = f'{origin.scheme}://{origin.netloc}/?' + urlencode({
            'replaySpec': str(args.spec.resolve()), 'replayStartAt': spec['session']['duration']})
        await page.goto(url)
        await page.locator('.summary-ready-btn').wait_for()
        await page.wait_for_function("document.querySelector('.video-section video')?.readyState >= 2")
        await page.wait_for_function("""() => {
            const cards = [...document.querySelectorAll('.event-node-scroll .event-node-card')];
            return !cards.length || cards.every(card => card.querySelector('img')?.naturalWidth > 0);
        }""")
        await page.bring_to_front()
        env = {**os.environ, 'DISPLAY': ':1'}
        wid = subprocess.check_output(['xdotool', 'search', '--onlyvisible', '--name',
                                       'Surg-R1 手术助手'], env=env, text=True).splitlines()[0]
        with (args.output / 'recorder.log').open('w') as log:
            recorder = subprocess.Popen([
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'warning',
                '-f', 'x11grab', '-draw_mouse', '0', '-framerate', '25',
                '-window_id', wid, '-i', ':1', '-an',
                '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-c:v', 'h264_nvenc',
                '-gpu', '0', '-preset', 'p4', '-cq', '23', '-b:v', '0',
                '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                str(args.output / 'replay_review.mp4')], stdout=log, stderr=log, env=env)
            try:
                await page.screenshot(path=str(args.output / 'loaded.png'))
                await page.locator('.summary-ready-btn').click()
                await page.locator('.report-markdown').wait_for()
                result['report_chars'] = len(await page.locator('.report-markdown').inner_text())
                assert result['report_chars'] > 100
                await page.screenshot(path=str(args.output / 'report.png'))
                await asyncio.sleep(2)
                await page.locator('.report-back').click()
                main = page.locator('.video-section video')
                before = await main.evaluate('v=>({time:v.currentTime,src:v.currentSrc})')
                await page.locator('.bcs-overview-btn').click()
                await page.locator('.window-card').first.wait_for()
                assert await page.locator('.window-card').count() == len(rows)
                for window_id in ids:
                    # Match the overlay exactly so #8 cannot select #80.
                    card = page.locator('.window-card').filter(has=page.get_by_text(f'#{window_id + 1}', exact=True))
                    await card.click()
                    modal = page.locator('.review-dialog')
                    await modal.wait_for()
                    await page.wait_for_function("document.querySelector('.review-dialog video')?.readyState >= 2")
                    samples = []
                    for _ in range(24):
                        samples.append(await modal.locator('video').evaluate('v=>v.currentTime'))
                        await asyncio.sleep(.3)
                    check = {'window_id': window_id, 'playback_times': samples,
                             'original_summary': await modal.locator('.review-analysis-record p').inner_text()}
                    assert max(samples) - min(samples) > .2, 'Replay is frozen'
                    check['loop_wrapped'] = any(b < a - .2 for a, b in zip(samples, samples[1:]))
                    assert check['loop_wrapped'], 'Replay did not wrap'
                    if args.candidates:
                        async with page.expect_response(lambda r: '/reanalyze-window/' in r.url, timeout=125000) as pending:
                            await modal.get_by_role('button', name='重新分析', exact=True).click()
                        response = await pending.value
                        assert response.status == 200, await response.text()
                        check['reanalysis'] = await response.json()
                        await modal.get_by_text('已通过本地模型重新分析', exact=True).wait_for()
                    await page.screenshot(path=str(args.output / f'window_{window_id + 1}.png'))
                    result['checks'].append(check)
                    await modal.get_by_role('button', name='关闭回看', exact=True).click()
                await page.locator('.overview-layer .back-btn').click()
                after = await main.evaluate('v=>({time:v.currentTime,src:v.currentSrc})')
                result['main_before'], result['main_after'] = before, after
                assert before['src'] == after['src'] and abs(before['time'] - after['time']) < .15
                assert not result['page_errors'], result['page_errors']
                await page.screenshot(path=str(args.output / 'returned.png'))
                result['passed'] = True
            finally:
                (args.output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
                if recorder.poll() is None:
                    recorder.send_signal(signal.SIGINT)
                await asyncio.to_thread(recorder.wait, 30)
    print(json.dumps({'output': str(args.output), 'passed': result.get('passed')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--candidates', type=Path)
    parser.add_argument('--window-ids', type=int, nargs='+')
    asyncio.run(inspect(parser.parse_args()))
