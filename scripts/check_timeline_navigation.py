#!/usr/bin/env python3
"""Electron regression: import, independent review clocks and analysis follow.

Uses real local replay media. Analysis-progress/live control inputs are controlled
fixtures; this does not claim to test an attached capture card or AI accuracy.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlencode, urlsplit

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / 'recordings/actual_videos_20260923/09-40-30/analysis.replay.json'
OUTPUT = ROOT / 'recordings/replay_navigation_20260923'
STATE = "(document.querySelector('#app').__vue_app__._instance || document.querySelector('#app').__vue_app__._container._vnode.component).setupState"


async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    result = {'checks': [], 'page_errors': [], 'controls': [], 'fixture_note': __doc__}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://127.0.0.1:9222')
        page = browser.contexts[0].pages[0]
        page.set_default_timeout(25000)
        page.on('pageerror', lambda e: result['page_errors'].append(str(e)))
        origin = urlsplit(page.url)
        base = f'{origin.scheme}://{origin.netloc}/'

        async def state(code):
            return await page.evaluate(f'async () => {{ const s = {STATE}; {code} }}')

        async def seek(time):
            slider = page.locator('.timeline-slider')
            await slider.fill(str(time))
            await page.wait_for_function('''target => {
              const v = document.querySelector('.video-section .primary-video-player video');
              return v && !v.seeking && Math.abs(v.currentTime - target) < .2;
            }''', arg=time)

        async def controls(route):
            result['controls'].append(route.request.post_data_json)
            await route.fulfill(json={'success': True})

        try:
            # Opening query replays must survive the desktop's automatic stream
            # navigation timer, even if the backend's configuration is delayed.
            async def slow_config(route):
                await asyncio.sleep(2)
                await route.fulfill(json={'window_duration': 5})
            await page.route('**/api/config', slow_config)
            await page.goto(base + '?' + urlencode({'replaySpec': str(SPEC), 'replayStartAt': 0}))
            await page.locator('.summary-ready-btn').wait_for()
            await page.wait_for_function("document.querySelector('.primary-video-player video')?.readyState >= 2")
            await asyncio.sleep(2.5)
            assert await state("return s.replayMode && s.currentView === 'main'")
            result['checks'].append('query_import_survives_delayed_config_and_autostart')
            await page.unroute('**/api/config', slow_config)
            expected = await state('return s.summaries.length')
            assert expected == 218
            await seek(40)
            await seek(12)
            assert await state('return !s.loopWindow && !s.isPlaying && s.summaries.length === 218 && s.summaryReady')
            # A real pointer drag also stays a seek, never an implicit loop.
            slider = page.locator('.timeline-slider')
            box = await slider.bounding_box()
            await page.mouse.move(box['x'] + box['width'] * .2, box['y'] + box['height'] / 2)
            await page.mouse.down()
            await page.mouse.move(box['x'] + box['width'] * .4, box['y'] + box['height'] / 2, steps=12)
            await page.mouse.up()
            await asyncio.sleep(.5)
            assert await state('return !s.loopWindow && s.currentTime > 400 && !s.isPlaying')
            await page.get_by_role('button', name='定位最新分析', exact=True).click()
            await page.wait_for_function(f'{STATE}.currentTime >= 1085')
            await page.screenshot(path=str(OUTPUT / 'offline_latest.png'))
            result['checks'].append('offline_click_drag_and_latest_keep_all_results_and_report')
            await page.locator('.summary-ready-btn').click()
            await page.locator('.report-markdown').wait_for()
            assert len(await page.locator('.report-markdown').inner_text()) > 100
            await page.locator('.report-back').click()

            # Exercise the native Electron import button, not only query loading.
            await state('s.goHome()')
            await page.bring_to_front()
            await page.get_by_role('button', name='导入分析回放', exact=True).click()
            await asyncio.sleep(.8)
            env = {**os.environ, 'DISPLAY': ':1'}
            dialog = subprocess.check_output(['xdotool', 'search', '--onlyvisible', '--name', '选择分析回放文件'], env=env, text=True).splitlines()[-1]
            subprocess.run(['xdotool', 'windowfocus', '--sync', dialog], env=env, check=True)
            for args in (['key', 'ctrl+l'], ['type', '--clearmodifiers', str(SPEC)], ['key', 'Return']):
                subprocess.run(['xdotool', *args], env=env, check=True)
                await asyncio.sleep(.3)
            await page.locator('.summary-ready-btn').wait_for()
            assert await state("return s.replayMode && s.currentView === 'main'")
            result['checks'].append('native_file_dialog_import_opens_in_place')

            # No control commands are allowed to reach an existing medical session
            # from the following deterministic progress and simulator fixtures.
            await page.route('**/api/video/control/**', controls)
            await page.route('**/api/analysis/stop-surgr1-continuous/**', lambda r: r.fulfill(json={}))
            await state('s.replayMode = false; s.isProcessing = true; s.analysisPosition = 45; s.summaries = s.summaries.slice(0, 8)')
            await seek(10)
            await page.get_by_role('button', name='返回分析进度', exact=True).click()
            await page.wait_for_function(f'{STATE}.currentTime === 45')
            await state('s.analysisPosition = 48')
            await page.wait_for_function("Math.abs(document.querySelector('.primary-video-player video').currentTime - 48) < .15")
            await seek(20)
            await state('s.analysisPosition = 52')
            await asyncio.sleep(.4)
            assert await state('return s.currentTime === 20 && !s.followingAnalysis')
            assert not result['controls'], result['controls']
            await page.screenshot(path=str(OUTPUT / 'offline_return_analysis.png'))
            result['checks'].append('offline_follow_tracks_analysis_and_manual_seek_detaches_without_backend_controls')

            await state("""
              s.isProcessing = false; s.mode = 'stream';
              s.currentSession = {...s.currentSession, session_id: 'timeline-regression', video_path: 'simulator://timeline-test'};
              s.currentTime = 60; s.livePlaybackTime = 60; s.isPlaying = false;
            """)
            await page.wait_for_function("document.querySelector('.primary-video-player video')?.readyState >= 2")
            await page.locator('.controls-row .primary').click()
            await asyncio.sleep(2)
            live_before = await page.locator('.primary-video-player video').evaluate('v=>v.currentTime')
            await slider.fill('15')
            await page.locator('.timeshift-player video').wait_for()
            await page.wait_for_function("document.querySelector('.timeshift-player video')?.readyState >= 2")
            await asyncio.sleep(1)
            await page.locator('.controls-row .primary').click()
            paused = await page.locator('.timeshift-player video').evaluate('v=>v.currentTime')
            await asyncio.sleep(2)
            now = await page.locator('.timeshift-player video').evaluate('v=>v.currentTime')
            live_after = await page.locator('.primary-video-player video').evaluate('v=>v.currentTime')
            assert abs(now - paused) < .1, (now, paused)
            assert live_after > live_before + 2, (live_before, live_after)
            assert not any(c.get('action') in ('seek', 'pause') for c in result['controls'])
            await page.screenshot(path=str(OUTPUT / 'live_history.png'))
            await page.get_by_role('button', name='返回实时直播', exact=True).click()
            assert await page.locator('.timeshift-player').count() == 0
            restored = await page.locator('.primary-video-player video').evaluate('v=>v.currentTime')
            assert restored >= live_after
            result['checks'].append('live_review_pause_does_not_pause_seek_or_remount_primary_stream')
            result['live_positions'] = [live_before, live_after, restored]
            await page.screenshot(path=str(OUTPUT / 'returned_live.png'))

            # Real saved frame URLs exercise the hardware-history player without
            # opening any physical input or pretending this is a capture test.
            await page.goto(base + '?' + urlencode({'replaySpec': str(SPEC), 'replayStartAt': 40}))
            await page.locator('.summary-ready-btn').wait_for()
            response = await page.request.get('http://127.0.0.1:8001/api/analysis/history-range/479d3aec')
            bounds = await response.json()
            assert bounds['available'] and bounds['end'] > 1000
            await page.evaluate('''async () => {
              const {createApp, reactive, h} = await import('/node_modules/.vite/deps/vue.js');
              const {default: Player} = await import('/src/components/CachedHistoryPlayer.vue');
              const p = reactive({sessionId:'479d3aec', currentTime:30, playing:false, seekNonce:0, start:0, end:1087});
              const host = document.createElement('div');
              host.style.cssText='position:fixed;inset:100px;z-index:10000;display:flex;background:#111';
              document.body.append(host);
              const app=createApp({render:()=>h(Player,{...p,onTimeupdate:t=>p.currentTime=t,onPause:()=>p.playing=false,onToggle:()=>p.playing=!p.playing})});
              app.mount(host); window.historyTest={p,cleanup:()=>{app.unmount();host.remove()}};
            }''')
            await page.wait_for_function("document.querySelector('.cached-history-player img')?.naturalWidth > 0")
            first_image = await page.locator('.cached-history-player img').get_attribute('src')
            await page.locator('.cached-history-player').click()
            await asyncio.sleep(2)
            assert await page.evaluate('window.historyTest.p.currentTime > 30.5')
            await page.locator('.cached-history-player').click()
            await page.evaluate('window.historyTest.p.currentTime=40;window.historyTest.p.seekNonce++')
            await page.wait_for_function("document.querySelector('.cached-history-player img')?.naturalWidth > 0")
            assert first_image != await page.locator('.cached-history-player img').get_attribute('src')
            await page.screenshot(path=str(OUTPUT / 'cached_frame_review.png'))
            await page.route('**/api/analysis/frames-batch/479d3aec?**', lambda r: r.fulfill(json={'frames': []}))
            await page.evaluate('window.historyTest.p.currentTime=50;window.historyTest.p.seekNonce++')
            await page.get_by_text('此位置暂无缓存画面', exact=True).wait_for()
            assert await page.locator('.cached-history-player img').count() == 0
            await page.evaluate('window.historyTest.cleanup()')
            result['checks'].append('cached_history_plays_seeks_and_reports_missing_intervals_using_real_saved_frames')
            assert not result['page_errors'], result['page_errors']
            result['passed'] = True
        finally:
            # Restore the user's real local replay, with no fixture running.
            await page.goto(base + '?' + urlencode({'replaySpec': str(SPEC), 'replayStartAt': 40}))
            await page.locator('.summary-ready-btn').wait_for()
            await page.unroute_all(behavior='wait')
            (OUTPUT / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
