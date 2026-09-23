#!/usr/bin/env python3
"""Compare a generic candidate prompt on retained real frames, without DB writes."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.services import local_visual_prompt as prompts
from backend.services.glm_client import get_glm_client
from backend.services.window_reanalysis import parse_review, review_scene


async def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    if args.prompt:
        prompts.LOCAL_VISUAL_PROMPT = args.prompt.read_text()
    client = get_glm_client()
    records = []
    try:
        for path in sorted(args.cases.glob('*/result.json')):
            old = json.loads(path.read_text())
            pack = old['after']['experts']
            images = [Image.open(p).convert('RGB') for p in sorted(path.parent.glob('frame_*.jpg'))]
            started = time.monotonic()
            response = await client._analyze_images_with_timestamps(
                images, old['timestamps'], prompts.build_local_visual_prompt(pack, frame_count=len(images)),
                system_prompt='只输出JSON。', temperature=0, max_tokens=1600, include_timestamps=False)
            result = parse_review(response.get('text')) if response.get('success') else response
            if 'visual' in result:
                result = prompts.normalize_local_review(result, len(images))
                result = await review_scene(images, old['timestamps'], result, client)
            record = {'case': path.parent.name, 'before': old['after']['summary'], 'result': result,
                      'seconds': round(time.monotonic() - started, 3)}
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            (args.output / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
    finally:
        await client.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--prompt', type=Path)
    p.add_argument('--output', type=Path, required=True)
    asyncio.run(main(p.parse_args()))
