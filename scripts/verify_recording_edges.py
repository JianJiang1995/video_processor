#!/usr/bin/env python3
"""Exercise real Electron recording recovery; inject only an empty encoder output."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess

from playwright.async_api import async_playwright

APP = "document.querySelector('#app').__vue_app__._container._vnode.component.setupState"
CONTROL = "document.querySelector('.screen-recording-control').__vueParentComponent.setupState"


async def verify(args):
    args.output.mkdir(parents=True, exist_ok=True)
    result = {'passed': False, 'empty_encoder_injected': True, 'inference': False,
              'checks': [], 'recordings': [], 'page_errors': []}
    env = {**os.environ, 'DISPLAY': args.display}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp)
        page = browser.contexts[0].pages[0]
        page.set_default_timeout(30000)
        await page.wait_for_function(f'{APP}.openReplayBundle !== undefined')
        assert not await page.evaluate(f'{APP}.isProcessing'), 'Only test an idle app'
        page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
        await page.evaluate(f'''async path => {{
            const bundle = await window.electronAPI.loadReplayBundle(path);
            if (!bundle.success) throw Error(bundle.error);
            bundle.spec.auto_play = false;
            await {APP}.openReplayBundle(bundle, 0);
        }}''', str(args.spec.resolve()))
        await page.wait_for_function("document.querySelector('.primary-video-player video')?.readyState >= 2")
        await page.evaluate(f"{APP}.setLanguage('zh')")
        previous_fps = await page.evaluate(f'{CONTROL}.frameRate')
        window_id = None
        try:
            await page.evaluate(f'''() => {{
                const recorder = {CONTROL}.recorder;
                window.__originalRecorder = recorder.Recorder;
                recorder.Recorder = class extends EventTarget {{
                    static isTypeSupported() {{ return true; }}
                    start() {{ this.state = 'recording'; }}
                    stop() {{ this.state = 'inactive'; queueMicrotask(() => this.dispatchEvent(new Event('stop'))); }}
                }};
            }}''')
            await page.get_by_test_id('screen-recording-toggle').click()
            await page.wait_for_function(f"{CONTROL}.status.state === 'recording'")
            await page.get_by_test_id('screen-recording-toggle').click()
            await page.wait_for_function(f"{CONTROL}.status.state === 'error'")
            result['empty_capture'] = await page.evaluate(f'{CONTROL}.status')
            assert result['empty_capture']['warning'] == 'recording_empty'
            assert '未录到有效画面' in await page.locator('.record-message').inner_text()
            result['checks'].append('empty encoder output reports an error, not successful recording')
            await page.evaluate(f'() => {{ {CONTROL}.recorder.Recorder = window.__originalRecorder }}')
            await page.evaluate(f'{APP}.handlePlay()')

            for fps in (25, 40, 60):
                await page.evaluate(f'{CONTROL}.settingsOpen = true')
                await page.get_by_test_id('screen-recording-fps').fill(str(fps))
                await page.get_by_test_id('screen-recording-fps').press('Tab')
                await page.wait_for_function(f'!{CONTROL}.frameRateSaving')
                await page.get_by_test_id('screen-recording-toggle').click()
                await page.wait_for_function(f"{CONTROL}.status.state === 'recording'")
                status = await page.evaluate(f'{CONTROL}.status')
                window_id = status['sourceId'].split(':')[1]
                await page.evaluate(f"window.__edgeTrack = {CONTROL}.recorder.stream.getVideoTracks()[0]")
                constraints = await page.evaluate('window.__edgeTrack.getConstraints()')
                assert status['fps'] == fps, status
                await asyncio.sleep(3)
                if fps == 60:
                    subprocess.run(['xdotool', 'windowminimize', window_id], env=env, check=True)
                else:
                    await page.get_by_test_id('screen-recording-toggle').click()
                await page.wait_for_function(f"{CONTROL}.status.state === 'saved'")
                status = await page.evaluate(f'{CONTROL}.status')
                assert not status['warning'] and status['media']['codec'] == 'h264', status
                assert await page.evaluate("window.__edgeTrack.readyState === 'ended'")
                assert await page.evaluate(f'{CONTROL}.recorder.stream === null')
                if fps == 60:
                    assert status['reason'] == 'window_minimized', status
                    subprocess.run(['xdotool', 'windowmap', window_id], env=env, check=True)
                    subprocess.run(['xdotool', 'windowactivate', '--sync', window_id], env=env, check=True)
                subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', status['path'],
                                '-vsync', '0', '-enc_time_base', '-1', '-f', 'null', '-'],
                               check=True, capture_output=True, timeout=60)
                result['recordings'].append({'result': status, 'constraints': constraints})
            result['checks'].append('normal recording recovers after failure; 25/40/60fps settings and MP4 decode pass')
            result['checks'].append('manual stop and minimize release capture tracks; minimize saves a playable file')
            assert not result['page_errors'], result['page_errors']
            await page.screenshot(path=str(args.output / 'recording_recovered.png'))
            result['passed'] = True
        except Exception as error:
            result['error'] = f'{type(error).__name__}: {error}'
            raise
        finally:
            await page.evaluate(f'() => {{ {CONTROL}.recorder.Recorder = window.__originalRecorder || MediaRecorder }}')
            await page.evaluate(f"{CONTROL}.recorder.stop('edge_test_cleanup')")
            await page.evaluate(f'''async fps => {{ const c={CONTROL}; c.frameRate=fps;
                await c.saveFrameRate(); c.settingsOpen=false; }}''', previous_fps)
            await page.evaluate(f'{APP}.handlePause()')
            if window_id:
                subprocess.run(['xdotool', 'windowmap', window_id], env=env, check=False)
                subprocess.run(['xdotool', 'windowactivate', '--sync', window_id], env=env, check=False)
            (args.output / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cdp', default='http://127.0.0.1:9222')
    parser.add_argument('--display', default=':1')
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(verify(parser.parse_args()))
