#!/usr/bin/env python3
"""Measure the actual Electron preview while the analysis UI keeps running.

Requires an opt-in loopback Electron CDP port. Does not open another capture
source or disable inference. Draw-submission metrics are not glass-to-glass delay.
"""
import argparse
import asyncio
import base64
import json
import time
import urllib.request
from pathlib import Path

import websockets


STATE = """() => {
  const app = document.querySelector('#app')?.__vue_app__?._container?._vnode?.component?.setupState;
  const recorder = document.querySelector('.screen-recording-control')?.__vueParentComponent?.setupState;
  return { sessionId: app?.currentSession?.session_id, videoPath: app?.currentSession?.video_path,
    playing: app?.isPlaying, analyzing: app?.isProcessing, time: app?.currentTime,
    metrics: app?.previewMetrics, recording: recorder?.status,
    canvas: [...document.querySelectorAll('canvas.live-capture-preview')].map(c => ({width: c.width, height: c.height})) };
}"""


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    errors = []
    # Playwright enables Network on worker targets and copies every raw frame
    # into CDP. Use Runtime-only CDP so the measurement does not add that load.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(args.cdp + '/json/list') as response:
        targets = json.load(response)
    target = next(target for target in targets if target['type'] == 'page' and '5133' in target['url'])
    async with websockets.connect(target['webSocketDebuggerUrl'], max_size=32 * 1024 * 1024) as connection:
        command_id = 0
        async def command(method, params=None):
            nonlocal command_id
            command_id += 1
            await connection.send(json.dumps({'id': command_id, 'method': method, 'params': params or {}}))
            while True:
                message = json.loads(await asyncio.wait_for(connection.recv(), 15))
                if message.get('id') == command_id:
                    if 'error' in message:
                        raise RuntimeError(message['error'])
                    return message.get('result', {})
                if message.get('method') == 'Runtime.exceptionThrown':
                    errors.append(message['params'])
        await command('Runtime.enable')
        await command('Page.bringToFront')
        started = time.monotonic()
        last_measurement = None
        while time.monotonic() - started < args.seconds:
            response = await command('Runtime.evaluate', {'expression': 'JSON.stringify((' + STATE + ')())', 'returnByValue': True})
            row = json.loads(response['result']['value'])
            metrics = row.get('metrics') or {}
            if metrics.get('measuredAt') != last_measurement:
                last_measurement = metrics.get('measuredAt')
                row['elapsed'] = round(time.monotonic() - started, 3)
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
            await asyncio.sleep(.5)
        # Capture after the timed measurement so screenshot readback is excluded.
        screenshot = await command('Page.captureScreenshot', {'format': 'png'})
        (args.output / 'electron_preview.png').write_bytes(base64.b64decode(screenshot['data']))
    samples = [row['metrics'] for row in rows if row.get('metrics', {}).get('frames')]
    result = {
        'scope': 'Actual Electron; source-read to draw submission, not sensor-to-monitor latency',
        'duration_seconds': args.seconds, 'samples': rows, 'browser_errors': errors,
        'summary': {
            'mean_fps': sum(s['fps'] for s in samples) / len(samples) if samples else None,
            'min_interval_fps': min((s['fps'] for s in samples), default=None),
            'max_interval_p95_pipeline_ms': max((s['p95PipelineMs'] for s in samples), default=None),
            'max_gap_ms': max((s['maxGapMs'] for s in samples), default=None),
        },
    }
    (args.output / 'preview_metrics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result['summary'], indent=2))
    if not samples:
        raise SystemExit('No live preview metrics were received')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cdp', default='http://127.0.0.1:9222')
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
