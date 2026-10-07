#!/usr/bin/env python3
"""Compare real expert inference on the prewarm thread and a fresh worker."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.services.analysis_image_regions import CROP_RIGHT_KEY, crop_right_for_source
from backend.services.expert_image_preparation import prepare_expert_frame, expert_warmup_sizes
from backend.services.expert_fusion import run_experts_on_window
from backend.services.vlm_factory import load_config


def run(args):
    config = load_config()
    with Image.open(args.image) as source:
        source.info[CROP_RIGHT_KEY] = crop_right_for_source('file.mp4?input_layout=' + args.layout, config)
        frame = prepare_expert_frame(source)

    def warm():
        from backend.services.phase_service import get_phase_service
        from backend.services.triplet_service import get_triplet_service
        from backend.services.yolo_service import get_yolo_service
        from backend.services.clip_detector_service import get_clip_detector_service
        phase, triplet = get_phase_service(), get_triplet_service()
        yolo, clips = get_yolo_service(), get_clip_detector_service()
        for width, height in expert_warmup_sizes(config):
            blank = np.zeros((height, width, 3), dtype=np.uint8)
            phase.classify_batch([blank] * 12)
            triplet.recognize_clip([blank] * 12)
            for _ in range(12):
                yolo.detect(blank)
                clips.detect(blank)
        yolo.reset_tracker()
        return threading.get_ident()

    def measure():
        started = time.monotonic()
        result = run_experts_on_window([frame] * 12)
        return {'thread': threading.get_ident(), 'seconds': time.monotonic() - started,
                'components_ms': result.get('timing'), 'available': result['available']}

    result = {'image': str(args.image), 'input_shape': list(frame.shape), 'runs': []}
    with ThreadPoolExecutor(max_workers=1) as warm_worker, ThreadPoolExecutor(max_workers=1) as fresh_worker:
        result['warm_thread'] = warm_worker.submit(warm).result()
        for name, worker in [('warm_thread', warm_worker), ('fresh_thread', fresh_worker),
                             ('fresh_repeat', fresh_worker), ('warm_repeat', warm_worker)]:
            measured = {'name': name, **worker.submit(measure).result()}
            result['runs'].append(measured)
            print(json.dumps(measured), flush=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True, help='Saved main-view frame before inference crop')
    parser.add_argument('--layout', choices=['full', 'quad_right'], default='quad_right')
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args())
