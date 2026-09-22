#!/usr/bin/env python3
"""Record the visible Electron simulator run and retain evolving evidence."""
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
    args.output.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, 'DISPLAY': args.display}
    recorder = None
    async with httpx.AsyncClient(base_url=args.backend, timeout=30, trust_env=False) as api:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(args.cdp)
            page = browser.contexts[0].pages[0]
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.reload()
            await page.get_by_text('手术室采集卡模拟源', exact=True).click()
            await page.get_by_text('是，左三右一', exact=True).click()
            await page.bring_to_front()
            window = subprocess.check_output(
                ['xdotool', 'search', '--onlyvisible', '--name', 'Surg-R1 手术助手'],
                env=env, text=True).splitlines()[0]
            with (args.output / 'recorder.log').open('w') as log:
                recorder = subprocess.Popen([
                    'ffmpeg', '-y', '-hide_banner', '-loglevel', 'warning',
                    '-f', 'x11grab', '-draw_mouse', '0', '-framerate', '25',
                    '-window_id', window, '-i', args.display, '-an',
                    '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-c:v', 'h264_nvenc',
                    '-gpu', '0', '-preset', 'p4', '-cq', '23', '-b:v', '5M',
                    '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                    str(args.output / 'first_video_complete.mp4')],
                    stdout=log, stderr=log, env=env)
                start = time.monotonic()
                try:
                    await page.get_by_role('button', name='📹 连接采集卡', exact=True).click()
                    await page.wait_for_function('document.querySelector("video")?.currentTime > 0', timeout=60000)
                    sid = (await page.locator('.header-session-id').inner_text()).strip().split()[-1]
                    session = (await api.get(f'/api/video/session/{sid}'))
                    if session.status_code != 200:
                        sessions = (await api.get('/api/video/sessions')).json()
                        session_data = next(s for s in sessions if s['session_id'] == sid)
                    else:
                        session_data = session.json()
                    duration = float(session_data['duration'])
                    metadata = {'session_id': sid, 'duration': duration,
                                'record_started_at': time.time(), 'source': session_data}
                    (args.output / 'run.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
                    print(json.dumps(metadata, ensure_ascii=False), flush=True)
                    revisions = {}
                    last_snapshot = -60
                    while time.monotonic() - start < duration + args.tail + 60:
                        elapsed = time.monotonic() - start
                        if recorder.poll() is not None:
                            raise RuntimeError(f'recorder stopped early: {recorder.returncode}')
                        ui = await page.evaluate('''() => {
                          const v=document.querySelector('video');
                          return {time:v?.currentTime,ended:v?.ended,paused:v?.paused,
                            topTime:document.querySelector('.time-overlay')?.innerText,
                            loop:!!document.querySelector('.loop-indicator-bar'),
                            cards:document.querySelectorAll('.event-node-card').length};
                        }''')
                        response = await api.get(f'/api/analysis/summary-telemetry/{sid}')
                        response.raise_for_status()
                        telemetry = response.json()
                        pending = (await api.get(f'/api/analysis/pending-refinements/{sid}')).json()
                        session_now = (await api.get(f'/api/video/session/{sid}')).json()
                        entry = {'elapsed': round(elapsed, 2), 'ui': ui, 'telemetry': telemetry,
                                 'pending': pending, 'backend_position': session_now.get('current_position')}
                        with (args.output / 'telemetry.jsonl').open('a') as f:
                            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
                        if elapsed - last_snapshot >= 30:
                            rows = (await api.get(f'/api/analysis/summaries/{sid}')).json()
                            (args.output / 'summaries.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                            for row in rows:
                                signature = json.dumps(row, ensure_ascii=False, sort_keys=True)
                                if signature != revisions.get(row['window_id']):
                                    revisions[row['window_id']] = signature
                                    with (args.output / 'revisions.jsonl').open('a') as f:
                                        f.write(json.dumps({'elapsed':elapsed, 'row':row}, ensure_ascii=False)+'\n')
                            await page.screenshot(path=str(args.output / f'ui_{int(elapsed):04d}.png'))
                            last_snapshot = elapsed
                            print(json.dumps({'elapsed':round(elapsed), 'video':ui['time'],
                                  'windows':len(rows), 'refined':sum(w['vlm_complete'] for w in telemetry['windows'])}), flush=True)
                        if ui.get('ended') and elapsed >= duration + args.tail and pending.get('settled'):
                            break
                        await asyncio.sleep(5)
                    rows = (await api.get(f'/api/analysis/summaries/{sid}')).json()
                    (args.output / 'summaries.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                    (args.output / 'browser_errors.json').write_text(json.dumps(errors, ensure_ascii=False, indent=2))
                    await page.screenshot(path=str(args.output / 'final.png'))
                    print(f'Finished session {sid}, {len(rows)} windows', flush=True)
                finally:
                    if recorder.poll() is None:
                        recorder.send_signal(signal.SIGINT)
                    await asyncio.to_thread(recorder.wait, 30)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cdp', default='http://127.0.0.1:9222')
    parser.add_argument('--backend', default='http://127.0.0.1:8001')
    parser.add_argument('--display', default=':1')
    parser.add_argument('--tail', type=float, default=45)
    asyncio.run(main(parser.parse_args()))
