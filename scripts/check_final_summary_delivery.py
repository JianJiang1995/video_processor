"""Reconnect the actual Electron summary stream and record the final UI update.

This is a recovery/regression check, not another live source-video run.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess

from playwright.async_api import async_playwright


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'DISPLAY': ':1'}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://127.0.0.1:9222')
        page = browser.contexts[0].pages[0]
        session = await page.locator('.header-session-id').inner_text()
        if args.session not in session:
            raise RuntimeError('Electron is displaying another session')
        window = subprocess.check_output(
            ['xdotool', 'search', '--onlyvisible', '--name', 'Surg-R1 手术助手'],
            env=env, text=True).splitlines()[0]
        log = (args.output / 'eof_delivery_recorder.log').open('w')
        recorder = subprocess.Popen([
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'warning',
            '-f', 'x11grab', '-draw_mouse', '0', '-framerate', '25',
            '-window_id', window, '-i', ':1', '-an',
            '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
            '-c:v', 'h264_nvenc', '-gpu', '0', '-preset', 'p4', '-cq', '23',
            '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
            str(args.output / 'eof_delivery_verification.mp4')], env=env, stdout=log, stderr=log)
        try:
            await asyncio.sleep(3)
            result = await page.evaluate('''sid => new Promise((resolve, reject) => {
              const app = document.querySelector('#app').__vue_app__;
              const state = (app._instance || app._container._vnode.component).setupState;
              const source = new EventSource(`http://127.0.0.1:8001/api/analysis/stream-summaries/${sid}`);
              let count = 0, last = null;
              const timeout = setTimeout(() => {source.close(); reject(new Error('SSE did not complete'));}, 20000);
              source.onmessage = event => {
                const data = JSON.parse(event.data);
                if (data.status === 'completed') {
                  clearTimeout(timeout); source.close();
                  const stored = state.summaries.find(row => row.window_id === last.window_id);
                  resolve({received: count, completed: true, last_payload: last,
                           last_ui_summary: stored?.summary,
                           matches: stored?.summary === state.cleanSummaryPayload(last).summary});
                  return;
                }
                if (Number.isFinite(data.window_id)) {state.upsertSummary(data); count++; last = data;}
              };
              source.onerror = () => {clearTimeout(timeout); source.close(); reject(new Error('SSE failed'));};
            })''', args.session)
            (args.output / 'eof_delivery_verification.json').write_text(
                json.dumps(result, ensure_ascii=False, indent=2))
            if not result['matches']:
                raise RuntimeError('UI and final SSE summary differ')
            await asyncio.sleep(8)
            await page.screenshot(path=str(args.output / 'after_eof_delivery_fix.png'))
            print(json.dumps({k: v for k, v in result.items() if k != 'last_payload'}, ensure_ascii=False))
        finally:
            recorder.send_signal(signal.SIGINT)
            await asyncio.to_thread(recorder.wait, 30)
            log.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
