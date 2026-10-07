#!/usr/bin/env python3
"""Summarize measured UI coverage/lag and retain frames from the actual recording."""
import argparse
import io
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image, ImageDraw


def audit(folder):
    verification = folder / 'verification.json'
    if verification.exists():
        from audit_surgr1_updates import audit as audit_verification
        result = audit_verification(json.loads(verification.read_text()))
        recording = folder / 'window_updates.webm'
        if not recording.exists():
            recording = folder / 'window_updates.mp4'
        inspect_recording(folder, result, recording)
        return
    completion = json.loads((folder / 'completion.json').read_text())
    run = json.loads((folder / 'run.json').read_text())
    rows = [json.loads(line) for line in (folder / 'telemetry.jsonl').read_text().splitlines()]
    lag = {'initial': [], 'refined': []}
    regressions = []
    previous = 0
    for row in rows:
        ui = row['ui']
        position = float(ui.get('time') or 0)
        if position + .1 < previous:
            regressions.append({'previous': previous, 'position': position, 'elapsed': row['elapsed']})
        previous = position
        if ui.get('ended') or position < 30:
            continue
        windows = row['telemetry'].get('windows', [])
        for kind in lag:
            eligible = [w for w in windows if kind == 'initial' or w.get('vlm_complete')]
            if eligible:
                lag[kind].append(max(0, position - max(float(w['end_time']) for w in eligible)))
    result = {'session_id': run['session_id'], 'source_duration': run['duration'],
              'windows': completion['windows'], 'expected_windows': completion['expected_windows'],
              'eof': completion['ended'], 'settled': completion['pending'].get('settled'),
              'browser_errors': json.loads((folder / 'browser_errors.json').read_text()),
              'backward_time_jumps': regressions,
              'lag_definition': 'UI playback position minus newest available window end; first 30s and EOF tail excluded',
              'lag_seconds': {kind: {label: round(float(np.percentile(values, p)), 2)
                                    for label, p in [('p50', 50), ('p95', 95), ('max', 100)]}
                              for kind, values in lag.items() if values}}
    recording = folder / 'first_video_complete.mp4'
    inspect_recording(folder, result, recording)


def inspect_recording(folder, result, recording):
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                            'format=duration,size:stream=width,height,r_frame_rate',
                            '-of', 'json', str(recording)], check=True, capture_output=True, text=True)
    result['recording'] = json.loads(probe.stdout)
    decoded_samples = []
    sheet = Image.new('RGB', (1280, 3 * 384), '#ffffff')
    draw = ImageDraw.Draw(sheet)
    duration = float(result['recording']['format']['duration'])
    for i, fraction in enumerate([.01, .2, .4, .6, .8, .98]):
        timestamp = duration * fraction
        # MediaRecorder writes variable timestamps. OpenCV's frame-count seek
        # can drift or report EOF when nominal and effective frame rates differ.
        sample = subprocess.run(['ffmpeg', '-v', 'error', '-threads', '2', '-ss', str(timestamp),
            '-i', str(recording), '-frames:v', '1', '-f', 'image2pipe', '-c:v', 'png',
            '-threads', '1', 'pipe:1'], capture_output=True, check=True, timeout=60)
        if not sample.stdout:
            raise RuntimeError(f'Recording cannot decode at {timestamp}')
        picture = Image.open(io.BytesIO(sample.stdout)).convert('RGB')
        frame = np.asarray(picture)
        height, width = frame.shape[:2]
        roi = frame[int(height * .15):int(height * .65), int(width * .2):int(width * .6)]
        decoded_samples.append({'recording_seconds': round(timestamp, 3),
                                'central_video_std': round(float(roi.std()), 3)})
        picture.save(folder / f'recording_sample_{i + 1}.jpg', quality=92)
        picture.thumbnail((640, 360))
        x, y = (i % 2) * 640, (i // 2) * 384
        sheet.paste(picture, (x, y + 24))
        draw.text((x + 5, y + 5), f'Recording {timestamp:.1f}s', fill='black')
    sheet.save(folder / 'recording_contact_sheet.jpg', quality=92)
    result['decoded_recording_samples'] = decoded_samples
    result['recording_review_scope'] = 'Six decoded samples and metadata; not a frame-by-frame clinical review or proof of smooth playback'
    (folder / 'quality_checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    audit(parser.parse_args().folder)
