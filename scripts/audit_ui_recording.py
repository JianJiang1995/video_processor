#!/usr/bin/env python3
"""Summarize measured UI coverage/lag and retain frames from the actual recording."""
import argparse
import json
from pathlib import Path
import subprocess

import cv2
import numpy as np
from PIL import Image, ImageDraw


def audit(folder):
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
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                            'format=duration,size:stream=width,height,r_frame_rate',
                            '-of', 'json', str(recording)], check=True, capture_output=True, text=True)
    result['recording'] = json.loads(probe.stdout)
    cap = cv2.VideoCapture(str(recording), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 2])
    sheet = Image.new('RGB', (1280, 3 * 384), '#ffffff')
    draw = ImageDraw.Draw(sheet)
    try:
        duration = float(result['recording']['format']['duration'])
        for i, fraction in enumerate([.01, .2, .4, .6, .8, .98]):
            timestamp = duration * fraction
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f'Recording cannot decode at {timestamp}')
            picture = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            picture.thumbnail((640, 360))
            x, y = (i % 2) * 640, (i // 2) * 384
            sheet.paste(picture, (x, y + 24))
            draw.text((x + 5, y + 5), f'Recording {timestamp:.1f}s', fill='black')
    finally:
        cap.release()
    sheet.save(folder / 'recording_contact_sheet.jpg', quality=92)
    (folder / 'quality_checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    audit(parser.parse_args().folder)
