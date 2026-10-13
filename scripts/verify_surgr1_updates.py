#!/usr/bin/env python3
"""Exercise actual paced analysis in Electron and record draft-to-refinement updates."""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import time

import httpx

STATE = "(document.querySelector('#app').__vue_app__._instance || document.querySelector('#app').__vue_app__._container._vnode.component).setupState"
RECORDING_STATE = "document.querySelector('.screen-recording-control').__vueParentComponent.setupState"

PLAYBACK_PROBE = r'''() => {
    const video = document.querySelector('.primary-video-player video');
    if (!video?.requestVideoFrameCallback) throw Error('Frame callbacks unavailable');
    const state = window.__recordingPlaybackProbe = {
        samples: [], waiting: [], longTasks: [], first: null, last: null,
        baselineQuality: video.getVideoPlaybackQuality(), width: video.videoWidth, height: video.videoHeight,
    };
    const eligible = () => !video.paused && !video.seeking && video.currentTime >= 3 &&
        video.currentTime < video.duration - .5;
    let previous = null;
    const callback = (now, meta) => {
        if (eligible()) {
            const sample = {wall: now, media: meta.mediaTime, presented: meta.presentedFrames};
            state.first ||= sample;
            state.last = sample;
            if (previous) state.samples.push({gap: now - previous.wall,
                mediaGap: (meta.mediaTime - previous.media) * 1000,
                frames: meta.presentedFrames - previous.presented});
            previous = sample;
        } else previous = null;
        if (!state.stopped) video.requestVideoFrameCallback(callback);
    };
    video.requestVideoFrameCallback(callback);
    for (const event of ['waiting', 'stalled']) video.addEventListener(event, () => {
        if (eligible()) state.waiting.push({event, time: video.currentTime});
    });
    const observer = new PerformanceObserver(list => {
        if (eligible()) state.longTasks.push(...list.getEntries().map(e => e.duration));
    });
    observer.observe({entryTypes: ['longtask']});
    state.finish = () => {
        state.stopped = true;
        observer.disconnect();
        const quality = video.getVideoPlaybackQuality();
        const gaps = state.samples.map(s => s.gap).sort((a,b) => a-b);
        const elapsed = (state.last?.wall - state.first?.wall) / 1000;
        return {width:state.width, height:state.height, warmup_excluded_seconds:3,
            measured_seconds:elapsed,
            presented_fps:elapsed > 0 ? (state.last.presented-state.first.presented)/elapsed : null,
            callback_fps:elapsed > 0 ? state.samples.length/elapsed : null,
            p95_gap_ms:gaps.length ? gaps[Math.floor((gaps.length-1)*.95)] : null,
            max_gap_ms:gaps.at(-1), gaps_over_200_ms:gaps.filter(g=>g>200).length,
            dropped_frames:quality.droppedVideoFrames-state.baselineQuality.droppedVideoFrames,
            total_video_frames:quality.totalVideoFrames-state.baselineQuality.totalVideoFrames,
            waiting:state.waiting, long_tasks:state.longTasks,
            long_task_max_ms:Math.max(0,...state.longTasks)};
    };
}'''


async def settled_snapshot(api, sid):
    state = (await api.get(f'/api/analysis/pending-refinements/{sid}')).json()
    # The final patch can commit between a rows poll and the pending check.
    rows = (await api.get(f'/api/analysis/summaries/{sid}')).json() if state.get('settled') else None
    return state, rows


def inspect_recording(path, source_duration):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
            'format=duration,size:stream=width,height,r_frame_rate', '-of', 'json', str(path)],
            check=True, capture_output=True, text=True, timeout=30)
        probe = json.loads(result.stdout)
        duration = float(probe['format']['duration'])
        return {'complete': duration >= source_duration, 'duration': duration, 'probe': probe,
                'error': None if duration >= source_duration else 'recording_shorter_than_source'}
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
        return {'complete': False, 'error': str(error)}


async def run(args):
    from playwright.async_api import async_playwright

    args.output.mkdir(parents=True, exist_ok=True)
    record = {'source': str(args.source.resolve()), 'passed': False,
              'checks': [], 'revisions': [], 'page_errors': []}
    recorder = None
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8001', timeout=120, trust_env=False) as api:
        response = await api.post('/api/video/connect-capture', json={
            'backend': 'simulator', 'simulator_path': str(args.source.resolve()),
            'input_layout': args.layout, 'auto_analyze': True})
        response.raise_for_status()
        sid = response.json()['session_id']
        session = (await api.get(f'/api/video/session/{sid}')).json()
        assert session['video_path'].startswith('simulator://file/'), 'Paced source lost during persistence'
        # Container duration can extend a fraction of a frame beyond the last
        # window boundary. Do not invent an extra window from that rounding.
        expected_windows = max(1, math.ceil((session['duration'] - .5 / max(1, session['fps'])) / 5))
        record['session_id'] = sid
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp('http://127.0.0.1:9222')
            page = browser.contexts[0].pages[0]
            previous_auto = None
            if args.app_recording or args.no_recording:
                previous_auto = await page.evaluate("window.electronAPI.getConfig('autoRecordAnalysis')")
                await page.evaluate("value => window.electronAPI.setConfig('autoRecordAnalysis', value)", args.app_recording)
            page.on('pageerror', lambda error: record['page_errors'].append(str(error)))
            await page.add_init_script('''(() => {
                if (window.__latencyInstrumented) return;
                window.__latencyInstrumented = true;
                window.__analysisReceipts = [];
                const Native = window.EventSource;
                window.EventSource = class extends Native {
                    constructor(...args) {
                        super(...args);
                        this.addEventListener('message', e => {
                            try {
                                const d = JSON.parse(e.data);
                                if (d.window_id == null) return;
                                window.__analysisReceipts.push({window_id:d.window_id,
                                    start:d.start_time, end:d.end_time, stage:d.stage,
                                    received_wall:Date.now()/1000, emitted_wall:d.emitted_at_wall,
                                    timing:d.timing, r1_status:d.experts?.surgr1?.status,
                                    video_time:document.querySelector('.primary-video-player video')?.currentTime});
                            } catch (_) {}
                        });
                    }
                };
            })();''')
            await page.goto('http://127.0.0.1:5133/')
            await page.wait_for_function(f'{STATE}.handleStreamConnect !== undefined')
            await page.bring_to_front()
            wid = None
            if not (args.app_recording or args.no_recording):
                wid = subprocess.check_output(['xdotool', 'search', '--onlyvisible', '--name', 'Surg-R1 手术助手'], text=True).splitlines()[-1]
            with (args.output / 'recorder.log').open('w') as log:
                recorder = None if (args.app_recording or args.no_recording) else subprocess.Popen([
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
                    if args.app_recording:
                        await page.wait_for_function(f"{RECORDING_STATE}.status.state === 'recording'", timeout=15000)
                        record['app_recording_started'] = await page.evaluate(f"{RECORDING_STATE}.status")
                    if args.measure_playback:
                        await page.evaluate(PLAYBACK_PROBE)
                    previous = {}
                    state = {}
                    rows = []
                    next_snapshot = 30
                    while time.monotonic() - started < args.timeout:
                        if recorder and recorder.poll() is not None and 'recording_error' not in record:
                            record['recording_error'] = 'recorder_exited_before_analysis_finished'
                            print(record['recording_error'], flush=True)
                        rows = (await api.get(f'/api/analysis/summaries/{sid}')).json()
                        for row in rows:
                            key = row['window_id']
                            others = row.get('others') or {}
                            r1 = (others.get('experts') or {}).get('surgr1') or {}
                            signature = (row.get('summary'), row.get('surgical_phase'), r1.get('status'), others.get('stage'))
                            if previous.get(key) != signature:
                                previous[key] = signature
                                record['revisions'].append({'elapsed': round(time.monotonic() - started, 3),
                                    'window_id': key, 'stage': others.get('stage'), 'r1_status': r1.get('status'),
                                    'summary': row.get('summary'), 'phase': row.get('surgical_phase')})
                                if key < 4 or key % 12 == 0 or args.timeout <= 180:
                                    print(record['revisions'][-1], flush=True)
                        if time.monotonic() - started >= next_snapshot:
                            receipts = await page.evaluate('window.__analysisReceipts')
                            (args.output / 'progress.json').write_text(json.dumps({
                                'session_id': sid, 'elapsed': time.monotonic() - started,
                                'rows': len(rows), 'receipts': receipts}, ensure_ascii=False))
                            if next_snapshot % 120 == 30:
                                await page.screenshot(path=str(args.output / f'at_{next_snapshot:04d}.png'))
                            next_snapshot += 30
                        if len(rows) == expected_windows:
                            state, final_rows = await settled_snapshot(api, sid)
                            if state.get('settled'):
                                rows = final_rows
                                break
                        await asyncio.sleep(3 if args.timeout > 180 else 1)
                    await page.wait_for_function(f'''expected => {{const rows = {STATE}.summaries;
                        return rows.length === expected.count && rows.every(r =>
                            r.stage >= expected.stage && String(r.summary || '').trim());}}''',
                        arg={'count': expected_windows, 'stage': 1 if args.r1_disabled else 2}, timeout=10000)
                    await page.screenshot(path=str(args.output / 'complete.png'))
                    ui = await page.evaluate(f'''() => {{const s = {STATE}; return {{time:s.currentTime,
                        summaries:s.summaries.map(r => ({{id:r.window_id, summary:r.summary, stage:r.stage}}))}} }}''')
                    record['ui'] = ui
                    record['rows'] = rows
                    record['receipts'] = await page.evaluate('window.__analysisReceipts')
                    drafts = {r['window_id'] for r in record['revisions'] if not r['r1_status']}
                    refined = {r['window_id'] for r in record['receipts'] if r.get('r1_status') == 'completed'}
                    assert len(rows) == expected_windows, 'Missing or extra windows'
                    assert state.get('settled'), 'Analysis did not finish at EOF'
                    assert all(str(row.get('summary') or '').strip() for row in rows), 'Empty final window summary'
                    for row in rows:
                        for observation in row['others']['experts'].get('surgr1', {}).get('observations', []):
                            assert row['start_time'] <= observation['timestamp'] < row['end_time'], 'R1 frame is from a different window'
                    if not args.r1_disabled:
                        assert drafts & refined, 'No real draft-to-SurgR1 update observed'
                        assert len(refined) == len(rows), 'Some SurgR1 reviews did not complete'
                        assert all((row['others'].get('experts', {}).get('open_vlm') or {}).get('success')
                                   and row['others'].get('stage') == 2 for row in rows), 'Incomplete visual refinement'
                    else:
                        assert not refined, 'R1 was not disabled for comparison'
                    assert len(ui['summaries']) == len(rows), 'UI did not receive all windows'
                    assert all(str(row.get('summary') or '').strip() for row in ui['summaries']), 'Empty UI window summary'
                    assert not record['page_errors'], record['page_errors']
                    record['checks'] = ['paced_real_inference', 'ui_received_results', 'finite_eof']
                    if not args.r1_disabled:
                        record['checks'] += ['draft_before_r1', 'all_r1_updates_saved', 'r1_frame_timestamps_match_window']
                    record['passed'] = True
                except Exception as error:
                    record['error'] = f'{type(error).__name__}: {error}'
                    raise
                finally:
                    record['wall_seconds'] = round(time.monotonic() - started, 3)
                    if args.measure_playback:
                        record['playback'] = await page.evaluate('window.__recordingPlaybackProbe?.finish()')
                    recording_path = args.output / 'window_updates.mp4'
                    if recorder:
                        if recorder.poll() is None:
                            recorder.send_signal(signal.SIGINT)
                        await asyncio.to_thread(recorder.wait, 20)
                    elif args.app_recording:
                        await page.evaluate(f"{RECORDING_STATE}.recorder.stop('verification_complete')")
                        result = await page.evaluate(f"{RECORDING_STATE}.status")
                        record['app_recording_finished'] = result
                        recording_path = args.output / ('window_updates' + Path(result.get('path') or 'recording.mp4').suffix)
                        if result.get('path'):
                            shutil.copy2(result['path'], recording_path)
                            sidecar = Path(result['path'] + '.json')
                            if sidecar.exists():
                                shutil.copy2(sidecar, str(recording_path) + '.json')
                        await page.evaluate("value => window.electronAPI.setConfig('autoRecordAnalysis', value)", previous_auto is True)
                        await page.evaluate(f"value => {{ {RECORDING_STATE}.autoRecord = value }}", previous_auto is True)
                    elif args.no_recording:
                        record['recording_disabled_state'] = await page.evaluate(f"{RECORDING_STATE}.status")
                        await page.evaluate("value => window.electronAPI.setConfig('autoRecordAnalysis', value)", previous_auto is True)
                        await page.evaluate(f"value => {{ {RECORDING_STATE}.autoRecord = value }}", previous_auto is True)
                    record['analysis_passed'] = record['passed']
                    if args.no_recording:
                        record['recording'] = {'enabled': False}
                        record['passed'] = record['analysis_passed'] and record['recording_disabled_state'].get('state') != 'recording'
                    else:
                        record['recording'] = inspect_recording(recording_path, session['duration'])
                        record['passed'] = record['analysis_passed'] and record['recording']['complete']
                    (args.output / 'verification.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'session': sid, 'output': str(args.output), 'passed': record.get('passed')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--layout', choices=['full', 'quad_right'], default='full')
    parser.add_argument('--r1-disabled', action='store_true')
    parser.add_argument('--measure-playback', action='store_true', help='Measure actual video presentation gaps during inference')
    recording = parser.add_mutually_exclusive_group()
    recording.add_argument('--app-recording', action='store_true')
    recording.add_argument('--no-recording', action='store_true', help='Run real analysis and screenshots without video recording')
    asyncio.run(run(parser.parse_args()))
