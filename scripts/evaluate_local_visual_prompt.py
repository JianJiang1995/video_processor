"""Offline, full-right-view prompt probes. Timestamps are test inputs only."""
import argparse
import base64
import json
from pathlib import Path
import sys
import time

import cv2
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.services.local_visual_prompt import LOCAL_VISUAL_PROMPT, VISIBILITY_REVIEW_PROMPT
from backend.services.clip_review import CLIP_REVIEW_PROMPT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('--times', nargs='+', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--kind', choices=['primary', 'visibility', 'clip'], default='primary')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with httpx.Client(timeout=90, trust_env=False) as client:
        capture = cv2.VideoCapture(args.source)
        try:
            for start in args.times:
                prompt = {'primary': LOCAL_VISUAL_PROMPT, 'visibility': VISIBILITY_REVIEW_PROMPT,
                          'clip': CLIP_REVIEW_PROMPT}[args.kind]
                content = [{'type': 'text', 'text': prompt}]
                for offset in (.3, 2.5, 4.5):
                    capture.set(cv2.CAP_PROP_POS_MSEC, (start + offset) * 1000)
                    ok, frame = capture.read()
                    if not ok:
                        raise RuntimeError(f'Cannot read source at {start + offset}')
                    frame = frame[:, frame.shape[1] // 4:]
                    frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_AREA)
                    cv2.imwrite(str(args.output / f'{start:g}_{offset:g}.jpg'), frame)
                    ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    content.append({'type': 'image_url', 'image_url': {
                        'url': 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode()}})
                began = time.monotonic()
                response = client.post('http://127.0.0.1:8012/v1/chat/completions', json={
                    'model': 'Qwen3-VL-8B-Instruct', 'temperature': 0, 'max_tokens': 1100,
                    'chat_template_kwargs': {'enable_thinking': False},
                    'messages': [{'role': 'system', 'content': '只输出JSON。'},
                                 {'role': 'user', 'content': content}],
                })
                response.raise_for_status()
                result = {'start': start, 'elapsed': time.monotonic() - began,
                          'response': response.json()}
                results.append(result)
                (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
                print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            capture.release()


if __name__ == '__main__':
    main()
