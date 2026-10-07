#!/usr/bin/env python3
"""Short paired tests of the running Electron window recorder and live preview.

Temporarily select this recorder's frame rate without saving the preference;
keep its codec, bitrate, resolution, file writer and preview unchanged.
No network tracing, screenshots, additional capture source or inference pause.
"""
import argparse
import asyncio
import json
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path

import websockets


INSTALL = """(() => {
  const app = document.querySelector('#app').__vue_app__._container._vnode.component.setupState;
  const ui = document.querySelector('.screen-recording-control').__vueParentComponent.setupState;
  if (!app.isPlaying || !app.isProcessing || !app.previewMetrics?.frames) throw Error('Live analysis required');
  if (ui.recording || ui.busy || ui.recorder.job) throw Error('A recording is already active');
  if (window.__recordingFpsProbe) throw Error('Probe already installed');
  const recorder = ui.recorder, frameRate = ui.frameRate, automatic = ui.autoRecord;
  const probe = { app, ui, recorder, frameRate, automatic, fps: frameRate, sessionId: app.currentSession.session_id };
  probe.restore = async () => {
    clearTimeout(probe.watchdog);
    try { if (recorder.job) await recorder.stop('fps_benchmark'); }
    finally { ui.frameRate = frameRate; ui.autoRecord = automatic; delete window.__recordingFpsProbe; }
  };
  probe.watchdog = setTimeout(() => void probe.restore(), 600000);
  ui.autoRecord = false;
  window.__recordingFpsProbe = probe;
  return { sessionId: probe.sessionId, automaticPreference: automatic };
})()"""

STATE = """(() => {
  const p = window.__recordingFpsProbe;
  if (!p || p.app.currentSession?.session_id !== p.sessionId) throw Error('Session changed');
  return { sampledAt: Date.now(), sessionId: p.sessionId, analyzing: p.app.isProcessing,
    playing: p.app.isPlaying, metrics: p.app.previewMetrics, recording: p.ui.status,
    requestedFps: p.fps, track: p.recorder.stream?.getVideoTracks()[0]?.getSettings(),
    pendingBytes: p.recorder.pending, recorderState: p.recorder.recorder?.state };
})()"""


def summarize(rows):
    metrics = [row['metrics'] for row in rows]
    if not metrics:
        raise RuntimeError('No fresh preview samples')
    return {
        'samples': len(metrics),
        'mean_draw_fps': statistics.mean(m['fps'] for m in metrics),
        'mean_received_fps': statistics.mean(m['receivedFps'] for m in metrics),
        'min_interval_fps': min(m['fps'] for m in metrics),
        'max_gap_ms': max(m['maxGapMs'] for m in metrics),
        'intervals_over_200ms': sum(m['maxGapMs'] > 200 for m in metrics),
        'max_interval_p95_ms': max(m['p95PipelineMs'] for m in metrics),
        'absolute_pass': all(m['fps'] >= 45 and m['maxGapMs'] <= 100 and
                             m['p95PipelineMs'] <= 100 for m in metrics),
    }


def probe_file(path):
    result = subprocess.run([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_packets',
        '-show_streams', '-show_entries',
        'stream=width,height,codec_name:packet=pts_time', '-of', 'json', path,
    ], check=True, capture_output=True, text=True, timeout=30)
    data = json.loads(result.stdout)
    times = [float(p['pts_time']) for p in data['packets'] if 'pts_time' in p]
    gaps = [b - a for a, b in zip(times, times[1:])]
    return {
        **data['streams'][0], 'frames': len(times),
        'encoded_mean_fps': (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 else 0,
        'encoded_max_gap_ms': max(gaps, default=0) * 1000,
    }


async def main(args):
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'scope': 'Paired short screening; draw submissions, not panel/optical latency',
              'criteria': 'All sampled intervals: draw >=45fps, max gap <=100ms, P95 <=100ms',
              'default_fps_changed': False, 'levels': [], 'errors': []}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(args.cdp + '/json/list', timeout=5) as response:
        target = next(t for t in json.load(response) if t['type'] == 'page' and '5133' in t['url'])
    async with websockets.connect(target['webSocketDebuggerUrl']) as socket:
        command_id = 0

        async def evaluate(expression):
            nonlocal command_id
            command_id += 1
            await socket.send(json.dumps({'id': command_id, 'method': 'Runtime.evaluate', 'params': {
                'expression': '(async () => JSON.stringify(await (' + expression + ')))()',
                'returnByValue': True, 'awaitPromise': True,
            }}))
            while True:
                message = json.loads(await asyncio.wait_for(socket.recv(), 45))
                if message.get('id') != command_id:
                    continue
                if 'error' in message or message.get('result', {}).get('exceptionDetails'):
                    raise RuntimeError(json.dumps(message))
                return json.loads(message['result']['result']['value'])

        async def sample(seconds, recording=False):
            # Discard the partial metric interval spanning the transition.
            cutoff = time.time() * 1000 + 2500
            deadline = time.monotonic() + seconds + 3
            seen, rows = set(), []
            while time.monotonic() < deadline:
                state = await evaluate(STATE)
                metrics = state.get('metrics') or {}
                if not state['playing'] or not state['analyzing']:
                    raise RuntimeError('Live analysis stopped during benchmark')
                if recording and state['recording']['state'] != 'recording':
                    raise RuntimeError('Recorder stopped unexpectedly')
                measured = metrics.get('measuredAt', 0)
                if measured >= cutoff and measured not in seen:
                    seen.add(measured)
                    rows.append(state)
                    if recording and metrics.get('maxGapMs', 0) > 1200:
                        raise RuntimeError('Safety stop: preview gap exceeded 1.2 seconds')
                await asyncio.sleep(.5)
            return rows

        installed = False
        try:
            report['initial'] = await evaluate(INSTALL)
            installed = True
            for fps in args.fps:
                print(f'BASELINE before {fps}fps', flush=True)
                baseline = await sample(args.baseline)
                await evaluate(f"""(async () => {{
                  const p = window.__recordingFpsProbe; p.fps = p.ui.frameRate = {fps};
                  await p.ui.start(false);
                  if (p.ui.status.state !== 'recording') throw Error(JSON.stringify(p.ui.status));
                  return p.ui.status;
                }})()""")
                print(f'RECORD {fps}fps', flush=True)
                samples = await sample(args.seconds, recording=True)
                stopped = await evaluate("""(async () => {
                  const p = window.__recordingFpsProbe;
                  await p.ui.toggle(); return p.ui.status;
                })()""")
                level = {'requested_fps': fps, 'path': stopped['path'],
                         'baseline': summarize(baseline), 'recorded': summarize(samples),
                         'track': samples[-1].get('track'), 'samples': samples,
                         'baseline_samples': baseline}
                level['relative_draw_change_percent'] = (
                    level['recorded']['mean_draw_fps'] / level['baseline']['mean_draw_fps'] - 1) * 100
                report['levels'].append(level)
                (args.output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
                print(json.dumps({k: v for k, v in level.items() if k not in {'samples', 'baseline_samples'}},
                                 ensure_ascii=False), flush=True)
        except Exception as error:
            report['errors'].append(str(error))
        finally:
            if installed:
                try:
                    report['restored'] = await evaluate("""(async () => {
                      const p = window.__recordingFpsProbe;
                      if (!p) return { watchdogAlreadyRestored: true };
                      const ui = p.ui, frameRate = p.frameRate;
                      await p.restore();
                      return { originalFrameRateRestored: ui.frameRate === frameRate,
                        autoRecord: ui.autoRecord, recording: ui.recording };
                    })()""")
                except Exception as error:
                    report['errors'].append('Restore failed: ' + str(error))
            (args.output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))

    # Demux after timed tests, not during them. Annotate only the benchmark
    # artifacts with measured cadence, distinct from the selected frame rate.
    for level in report['levels']:
        level['file_probe'] = await asyncio.to_thread(probe_file, level['path'])
        metadata = Path(level['path'] + '.json')
        data = json.loads(metadata.read_text())
        data.update(fps=level['requested_fps'], benchmark=True,
                    measuredEncodedFps=level['file_probe']['encoded_mean_fps'])
        metadata.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    (args.output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'restored': report.get('restored'), 'errors': report['errors'],
                      'encoded': [{k: l[k] for k in ('requested_fps', 'file_probe')} for l in report['levels']]},
                     ensure_ascii=False), flush=True)
    if report['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cdp', default='http://127.0.0.1:9222')
    parser.add_argument('--fps', type=int, nargs='+', default=[15, 20, 25, 30, 40, 50])
    parser.add_argument('--seconds', type=int, default=20)
    parser.add_argument('--baseline', type=int, default=10)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if any(fps < 1 or fps > 60 for fps in args.fps):
        parser.error('Frame rates must be between 1 and 60')
    asyncio.run(main(args))
