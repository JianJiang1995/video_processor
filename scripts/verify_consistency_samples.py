#!/usr/bin/env python3
"""Review selected stored windows locally without changing their database records."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.services.mysql_service import get_mysql_service
from backend.services.window_reanalysis import load_review_frames, analyze_review
from backend.services.glm_client import get_glm_client
from backend.services.surgr1_client import get_surgr1_client
from backend.routers.analysis import _strip_focused_visibility_conflicts, _expert_snapshot_summary


async def run(args):
    store = get_mysql_service()
    session = store.get_video_session(args.session)
    rows = store.get_all_window_summaries(args.session)
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    try:
        for number in args.windows:
            row = next(row for row in rows if row['window_id'] == number - 1)
            images, times = load_review_frames(session, row['window_start'], row['window_end'])
            folder = args.output / f'window_{number}'
            folder.mkdir(exist_ok=True)
            for index, image in enumerate(images):
                image.save(folder / f'frame_{index}_{times[index]:.3f}.jpg')
            experts = (row.get('others') or {}).get('experts') or {}
            result = await analyze_review(images, times, experts, row.get('surgical_phase'))
            result['summary'] = _strip_focused_visibility_conflicts(result['summary'], result['visual'])
            record = {'session_id': args.session, 'ui_window': number, 'timestamps': times,
                      'before': row.get('glm_summary'), 'after': result,
                      'new_draft': _expert_snapshot_summary(experts, row['window_start'], row['window_end'], len(images))}
            (folder / 'result.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
            results.append(record)
            print(json.dumps({'window': number, 'before': record['before'], 'after': result['summary'],
                              'visibility': result['visual'].get('visibility')}, ensure_ascii=False), flush=True)
    finally:
        await get_glm_client().close()
        await get_surgr1_client().close()
    (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True)
    parser.add_argument('--windows', nargs='+', type=int, required=True, help='One-based UI window numbers')
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
