#!/usr/bin/env python3
"""Exercise actual paced analysis in Electron and record draft-to-refinement updates."""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time

import httpx
from playwright.async_api import async_playwright

STATE = "(document.querySelector('#app').__vue_app__._instance || document.querySelector('#app').__vue_app__._container._vnode.component).setupState"


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    record = {'source': str(args.source.resolve()), 'passed': False,
              'checks': [], 'revisions': [], 'page_errors': []}
    recorder = None
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8001', timeout=120, trust_env=False) as api:
        response = await api.post('/api/video/load', params={'video_path': str(args.source.resolve()), 'paced': True})
        response.raise_for_status()
        sid = response.json()['session_id']
        session = (await api.get(f'/api/video/session/{sid}')).json()
        assert session['video_path'].startswith('simulator://file/'), 'Paced source lost during persistence'
        expected_windows = math.ceil(session['duration'] / 5)
        record['session_id'] = sid
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp('http://127.0.0.1:9222')
            page = browser.contexts[0].pages[0]
            page.on('pageerror', lambda error: record['page_errors'].append(str(error)))
            await page.goto('http://127.0.0.1:5133/')
            await page.wait_for_function(f'{STATE}.handleStreamConnect !== undefined')
            await page.bring_to_front()
            wid = subprocess.check_output(['xdotool', 'search', '--onlyvisible', '--name', 'Surg-R1 手术助手'], text=True).splitlines()[-1]
            with (args.output / 'recorder.log').open('w') as log:
                recorder = subprocess.Popen([
                    'ffmpeg', '-nostdin', '-y', '-hide_banner', '-loglevel', 'warning',
                    '-f', 'x11grab', '-draw_mouse', '0', '-framerate', '15', '-window_id', wid,
                    '-i', os.environ.get('DISPLAY', ':1'), '-an', '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
                    '-c:v', 'h264_nvenc', '-gpu', '0', '-preset', 'p4', '-cq', '23', '-b:v', '0',
                    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(args.output / 'window_updates.mp4')],
                    stdout=log, stderr=log)
                started = time.monotonic()
                try:
                    await page.evaluate(f'''session => {{const s = {STATE}; s.mode = 'stream';
                        s.handleStreamConnect({{session, autoAnalyze:true}});}}''', session)
                    await page.wait_for_function("document.querySelector('.primary-video-player video')?.readyState >= 2")
                    previous = {}
                    state = {}
                    while time.monotonic() - started < args.timeout:
                        rows = (await api.get(f'/api/analysis/summaries/{sid}')).json()
                        for row in rows:
                            key = row['window_id']
                            others = row.get('others') or {}
                            r1 = (others.get('experts') or {}).get('surgr1') or {}
                            signature = (row.get('summary'), r1.get('status'), others.get('stage'))
                            if previous.get(key) != signature:
                                previous[key] = signature
                                record['revisions'].append({'elapsed': round(time.monotonic() - started, 3),
                                    'window_id': key, 'stage': others.get('stage'), 'r1_status': r1.get('status'),
                                    'summary': row.get('summary')})
                                print(record['revisions'][-1], flush=True)
                        if len(rows) == expected_windows:
                            state = (await api.get(f'/api/analysis/pending-refinements/{sid}')).json()
                            if len(rows) >= expected_windows and state.get('settled'):
                                break
                        await asyncio.sleep(1)
                    await page.screenshot(path=str(args.output / 'complete.png'))
                    ui = await page.evaluate(f'''() => {{const s = {STATE}; return {{time:s.currentTime,
                        summaries:s.summaries.map(r => ({{id:r.window_id, summary:r.summary, stage:r.stage}}))}} }}''')
                    record['ui'] = ui
                    record['rows'] = rows
                    drafts = {r['window_id'] for r in record['revisions'] if not r['r1_status']}
                    refined = {r['window_id'] for r in record['revisions'] if r['r1_status'] == 'completed'}
                    assert len(rows) == expected_windows, 'Missing or extra windows'
                    assert state.get('settled'), 'Analysis did not finish at EOF'
                    for row in rows:
                        for observation in row['others']['experts']['surgr1'].get('observations', []):
                            assert row['start_time'] <= observation['timestamp'] < row['end_time'], 'R1 frame is from a different window'
                    assert drafts & refined, 'No real draft-to-SurgR1 update observed'
                    assert len(refined) == len(rows), 'Some SurgR1 reviews did not complete'
                    assert len(ui['summaries']) == len(rows), 'UI did not receive all windows'
                    assert not record['page_errors'], record['page_errors']
                    record['checks'] = ['paced_real_inference', 'draft_before_r1', 'all_r1_updates_saved',
                                        'ui_received_results', 'finite_eof', 'r1_frame_timestamps_match_window']
                    record['passed'] = True
                except Exception as error:
                    record['error'] = f'{type(error).__name__}: {error}'
                    raise
                finally:
                    record['wall_seconds'] = round(time.monotonic() - started, 3)
                    (args.output / 'verification.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
                    if recorder.poll() is None:
                        recorder.send_signal(signal.SIGINT)
                    await asyncio.to_thread(recorder.wait, 20)
    print(json.dumps({'session': sid, 'output': str(args.output), 'passed': record.get('passed')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=120)
    asyncio.run(run(parser.parse_args()))
