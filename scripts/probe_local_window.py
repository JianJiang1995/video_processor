#!/usr/bin/env python3
"""Inspect a local window with the production prompts; never supplies an answer label."""
import argparse
import base64
import io
import json
from pathlib import Path
import sys
import time

import cv2
import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.services.clip_review import CLIP_REVIEW_PROMPT, validate_clip_review
from backend.services.local_video_source import apply_input_layout
from backend.services.local_visual_prompt import build_local_visual_prompt, VISIBILITY_REVIEW_PROMPT


def main(args):
    args.output.mkdir(parents=True, exist_ok=False)
    content = []
    times = []
    cap = cv2.VideoCapture(str(args.video), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 4])
    try:
        for i in range(3):
            timestamp = args.start + args.duration * (i + .5) / 3
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            times.append(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000)
            frame = apply_input_layout(frame, args.layout)
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            image.thumbnail((640, 640), Image.Resampling.LANCZOS)
            image.save(args.output / f'frame_{i}.jpg')
            data = io.BytesIO()
            image.save(data, format='JPEG', quality=90)
            content.append({'type': 'image_url', 'image_url': {
                'url': 'data:image/jpeg;base64,' + base64.b64encode(data.getvalue()).decode()}})
    finally:
        cap.release()
    if not content:
        raise RuntimeError('No frames')
    prompt = {'clip': CLIP_REVIEW_PROMPT, 'visibility': VISIBILITY_REVIEW_PROMPT,
              'primary': build_local_visual_prompt()}[args.kind]
    (args.output / 'prompt.txt').write_text(prompt, encoding='utf-8')
    started = time.monotonic()
    with httpx.Client(timeout=90, trust_env=False) as client:
        response = client.post('http://127.0.0.1:8012/v1/chat/completions', json={
            'model': 'Qwen3-VL-8B-Instruct',
            'messages': [{'role': 'system', 'content': 'Return one JSON object based only on the images.'},
                         {'role': 'user', 'content': [{'type': 'text', 'text': prompt}] + content}],
            'temperature': 0, 'max_tokens': 1500, 'chat_template_kwargs': {'enable_thinking': False},
        })
        response.raise_for_status()
    result = {'video': str(args.video), 'start': args.start, 'timestamps': times,
              'elapsed_seconds': time.monotonic() - started, 'response': response.json()}
    (args.output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, required=True)
    parser.add_argument('--start', type=float, required=True)
    parser.add_argument('--duration', type=float, default=5)
    parser.add_argument('--layout', choices=['full', 'quad_right'], default='full')
    parser.add_argument('--kind', choices=['clip', 'primary', 'visibility'], default='clip')
    parser.add_argument('--output', type=Path, required=True)
    main(parser.parse_args())
