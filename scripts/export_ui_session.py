#!/usr/bin/env python3
"""Export a settled UI session, its Markdown report, and an Electron replay spec."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.services.local_video_source import resolve_video_source
from backend.services.playback_proxy import request_preview, probe


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def export(args):
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.backend, timeout=600, trust_env=False) as api:
        def get(path):
            response = api.get(path)
            response.raise_for_status()
            return response.json()

        session = get(f'/api/video/session/{args.session}')
        pending = get(f'/api/analysis/pending-refinements/{args.session}')
        if not pending.get('settled'):
            raise RuntimeError(f'Analysis not settled: {pending}')
        summaries = get(f'/api/analysis/summaries/{args.session}')
        if not summaries:
            raise RuntimeError('No analysis results')
        save(out / 'summaries.json', summaries)
        save(out / 'telemetry_final.json', get(f'/api/analysis/summary-telemetry/{args.session}'))
        events = api.post(f'/api/analysis/event-nodes/{args.session}', json={
            'language': 'zh', 'force': True, 'max_windows': 180, 'timeout': 120,
        })
        events.raise_for_status()
        save(out / 'events.json', events.json())
        report = api.post(f'/api/analysis/clinical-summary/{args.session}', json={
            'language': 'zh', 'force': True, 'output_dir': str(out),
        })
        report.raise_for_status()
        report = report.json()
        save(out / 'report_generation.json', report)
        if report.get('output_path'):
            shutil.copyfile(report['output_path'], out / 'summary.md')

    source = resolve_video_source(session['video_path'])
    deadline = time.monotonic() + 600
    preview = request_preview(source.source)
    while preview['status'] == 'preparing' and time.monotonic() < deadline:
        time.sleep(1)
        preview = request_preview(source.source)
    if preview['status'] not in {'ready', 'original'}:
        raise RuntimeError(f'Preview unavailable: {preview}')
    media = preview['path'] if preview['status'] == 'ready' else source.source
    replay_media = Path(media)
    if source.input_layout == 'quad_right':
        replay_media = out / 'review_source.mp4'
        identity = {'preview': str(Path(media).resolve()),
                    'bytes': Path(media).stat().st_size, 'mtime_ns': Path(media).stat().st_mtime_ns}
        identity_path = out / 'replay_media_source.json'
        cached = replay_media.exists() and identity_path.exists() and json.loads(identity_path.read_text()) == identity
        command = ['ffmpeg', '-y', '-nostdin', '-hide_banner', '-loglevel', 'error',
                   '-threads', '4', '-i', media, '-map', '0:v:0', '-an',
                   '-vf', 'crop=iw*3/4:ih:iw/4:0', '-c:v', 'h264_nvenc', '-gpu', '0',
                   '-preset', 'p4', '-cq', '23', '-b:v', '0', '-pix_fmt', 'yuv420p',
                   '-movflags', '+faststart', str(replay_media)]
        if not cached:
            subprocess.run(command, check=True, timeout=600)
            save(identity_path, identity)
    info = probe(str(replay_media))
    if abs(info['duration'] - float(session['duration'])) > .15:
        raise RuntimeError('Replay duration is not aligned with the source')
    spec = {
        'version': 1, 'title': session['video_name'], 'language': 'zh',
        'video_path': str(replay_media.resolve()), 'summaries_path': 'summaries.json',
        'events_path': 'events.json', 'report_path': report.get('output_path'),
        'auto_play': False, 'final_update_delay': 0,
        'session': {'session_id': args.session, 'video_name': session['video_name'],
                    'duration': session['duration'], 'fps': info['fps'],
                    'width': info['width'], 'height': info['height']},
    }
    save(out / 'analysis.replay.json', spec)
    save(out / 'export_manifest.json', {'session': session, 'source_file': source.source,
        'input_layout': source.input_layout, 'replay_media': str(replay_media.resolve()),
        'replay_spec': str(out / 'analysis.replay.json'), 'report': report.get('output_path'),
        'report_source': report.get('source'), 'window_count': len(summaries),
        'post_reviewed_windows': sum(bool((row.get('others') or {}).get('manual_review')) for row in summaries),
        'live_recording_includes_post_review': False,
        'mode': 'settled_ui_analysis_replay_export'})
    print(json.dumps({'output': str(out), 'windows': len(summaries),
                      'report': report.get('output_path')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backend', default='http://127.0.0.1:8001')
    export(parser.parse_args())
