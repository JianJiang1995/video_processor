#!/usr/bin/env python3
"""Check homepage history in an idle Electron app; no inference is started."""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def verify(args):
    args.output.mkdir(parents=True, exist_ok=True)
    result = {'passed': False, 'page_errors': [], 'opened': [], 'analysis_start_requests': []}
    (args.output / 'verification.json').write_text(json.dumps(result))
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp)
        page = browser.contexts[0].pages[0]
        page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
        page.on('request', lambda request: result['analysis_start_requests'].append(request.url)
                if request.method == 'POST' and any(part in request.url for part in
                    ['/api/analysis/start', '/api/video/connect', '/api/analysis/start-surgr1']) else None)
        await page.goto(args.frontend)
        await page.locator('.history-row').first.wait_for()
        initial = await page.evaluate('window.electronAPI.listAnalysisHistory()')
        assert initial['success']
        result['history_count'] = len(initial['entries'])
        targets = args.sessions or [row['sessionId'] for row in initial['entries']
                                   if row['available'] and row['reportAvailable']][:2]
        for session_id in targets:
            await page.get_by_placeholder('搜索视频或会话').fill(session_id)
            row = page.locator(f'.history-row[data-session-id="{session_id}"]')
            await row.get_by_role('button', name='打开分析', exact=True).click()
            await page.wait_for_function("document.querySelector('.video-section video')?.readyState >= 2")
            video = page.locator('.video-section video').first
            state = await video.evaluate('v=>({time:v.currentTime,duration:v.duration,paused:v.paused,width:v.videoWidth})')
            assert state['width'] > 0 and state['paused'], state
            assert await page.locator('.mode-badge').inner_text() == '⏱ 离线分析回放'
            # Exercise media decoding without advancing or restarting analysis.
            await video.evaluate('v=>{v.currentTime=0;return v.play()}')
            await page.wait_for_timeout(1200)
            playing = await video.evaluate('v=>({time:v.currentTime,paused:v.paused})')
            assert playing['time'] > .1 and not playing['paused'], playing
            await video.evaluate('v=>v.pause()')
            await page.wait_for_function("""() => {
                const cards = [...document.querySelectorAll('.event-node-scroll .event-node-card')];
                return !cards.length || cards.every(card => card.querySelector('img')?.naturalWidth > 0);
            }""", timeout=30000)
            await page.screenshot(path=str(args.output / f'{session_id}_analysis.png'))
            await page.locator('.app-header .logo').click()
            await page.locator('.history-row').first.wait_for()
            await page.get_by_placeholder('搜索视频或会话').fill(session_id)
            await page.locator(f'.history-row[data-session-id="{session_id}"]').get_by_role('button', name='查看报告', exact=True).click()
            await page.locator('.report-markdown').wait_for()
            chars = len(await page.locator('.report-markdown').inner_text())
            assert chars > 100
            await page.screenshot(path=str(args.output / f'{session_id}_report.png'))
            result['opened'].append({'session_id': session_id, 'video': state, 'playback': playing, 'report_chars': chars})
            await page.locator('.app-header .logo').click()
            await page.locator('.history-row').first.wait_for()

        await page.reload()
        await page.locator('.history-row').first.wait_for()
        registered = await page.evaluate('window.electronAPI.getConfig("analysisHistory")')
        assert set(targets).issubset({row['sessionId'] for row in registered})
        result['remembered_sessions'] = [row['sessionId'] for row in registered]
        await page.get_by_role('button', name='English', exact=True).click()
        await page.get_by_placeholder('Search video or session').fill('no-such-video-in-history')
        assert await page.get_by_text('No matching analyses', exact=True).is_visible()
        await page.get_by_placeholder('Search video or session').fill('')
        if await page.get_by_role('button', name='Next page', exact=True).count():
            await page.get_by_role('button', name='Next page', exact=True).click()
            assert '2 /' in await page.locator('.history-pagination').inner_text()
            await page.get_by_role('button', name='Previous page', exact=True).click()
        await page.get_by_role('button', name='中文', exact=True).click()
        cdp = await page.context.new_cdp_session(page)
        result['viewports'] = []
        try:
            for width, height in [(1280, 800), (390, 844)]:
                await cdp.send('Emulation.setDeviceMetricsOverride', {
                    'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': False})
                await page.wait_for_timeout(300)
                dimensions = await page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
                assert dimensions['scroll'] <= dimensions['width'] + 1, dimensions
                result['viewports'].append(dimensions)
                await page.screenshot(path=str(args.output / f'home_{width}.png'))
        finally:
            await cdp.send('Emulation.clearDeviceMetricsOverride')
        await page.screenshot(path=str(args.output / 'home_final.png'))
        assert not result['page_errors'], result['page_errors']
        assert not result['analysis_start_requests'], result['analysis_start_requests']
        result['passed'] = True
    (args.output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cdp', default='http://127.0.0.1:9222')
    parser.add_argument('--frontend', default='http://127.0.0.1:5133/')
    parser.add_argument('--sessions', nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(verify(parser.parse_args()))
