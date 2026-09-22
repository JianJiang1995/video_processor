"""Compare saved simulator frames with nearby frames of the original file."""
import argparse
import json
from pathlib import Path

import cv2
import httpx
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('session')
    parser.add_argument('--times', nargs='+', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cap = cv2.VideoCapture(args.source)
    fps = cap.get(cv2.CAP_PROP_FPS)
    results = []
    try:
        with httpx.Client(base_url='http://127.0.0.1:8001', trust_env=False) as api:
            for target in args.times:
                response = api.get(f'/api/analysis/frames-batch/{args.session}', params={
                    'start': target, 'end': target + 1, 'max_frames': 1,
                    'use_url': 'true', 'use_preview': 'false'})
                response.raise_for_status()
                frame = response.json()['frames'][0]
                saved_response = api.get(frame['url'])
                saved_response.raise_for_status()
                saved = cv2.imdecode(np.frombuffer(saved_response.content, np.uint8), cv2.IMREAD_COLOR)
                saved = cv2.resize(saved, (640, 480), interpolation=cv2.INTER_AREA)
                candidates = []
                base_index = round(frame['timestamp'] * fps)
                first_index = max(0, base_index - round(fps / 2))
                cap.set(cv2.CAP_PROP_POS_FRAMES, first_index)
                for index in range(first_index, base_index + round(fps / 2) + 1):
                    ok, original = cap.read()
                    if not ok:
                        break
                    original = original[:, original.shape[1] // 4:]
                    original = cv2.resize(original, (640, 480), interpolation=cv2.INTER_AREA)
                    mae = float(np.abs(original.astype(np.float32) - saved).mean())
                    candidates.append((mae, cap.get(cv2.CAP_PROP_POS_MSEC) / 1000))
                best_error, best_time = min(candidates)
                results.append({'requested': target, 'saved_time': frame['timestamp'],
                                'best_source_time': best_time, 'offset_seconds': best_time - frame['timestamp'],
                                'mean_absolute_pixel_error': best_error, 'url': frame['url']})
                print(results[-1], flush=True)
    finally:
        cap.release()
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
