#!/usr/bin/env python3
"""Re-run chosen saved windows locally, retaining before/after evidence without DB edits."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.mysql_service import get_mysql_service
from backend.services.window_reanalysis import load_review_frames, analyze_review
from backend.services.triplet_service import get_triplet_service
from backend.services.glm_client import get_glm_client
from backend.services.surgr1_client import get_surgr1_client


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    store = get_mysql_service()
    expert = get_triplet_service()
    if expert is None:
        raise RuntimeError('Triplet unavailable')
    report = ['# Window Anatomy Review', '',
              'Local model outputs, not clinician-verified ground truth. Window numbers below are one-based.', '']
    results = []
    try:
        for case in args.case:
            sid, wid = case.split(':')
            wid = int(wid)
            session = store.get_video_session(sid)
            row = next(r for r in store.get_all_window_summaries(sid) if r['window_id'] == wid)
            folder = args.output / f'{sid}_window_{wid + 1}'
            folder.mkdir(exist_ok=True)
            images, times = load_review_frames(session, row['window_start'], row['window_end'])
            for i, image in enumerate(images):
                image.save(folder / f'frame_{i + 1}_{times[i]:.3f}.jpg', quality=95)
            pack = dict((row.get('others') or {}).get('experts') or {})
            before_triplet = pack.get('triplet')
            pack['triplet'] = await asyncio.to_thread(expert.recognize_clip, images)
            started = time.monotonic()
            result = await analyze_review(images, times, pack, row.get('surgical_phase'))
            record = {'session': sid, 'window_id': wid, 'timestamps': times,
                      'before_summary': row.get('glm_summary'), 'before_triplet': before_triplet,
                      'after': result, 'elapsed_seconds': round(time.monotonic() - started, 3)}
            (folder / 'result.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
            results.append(record)
            r1 = result['experts']['surgr1']
            report.extend([f'## {session["video_name"]} / Window {wid + 1}', '',
                           f'Before: {record["before_summary"]}', '', f'After: {result["summary"]}', '',
                           f'SurgR1: {r1["status"]}, {r1.get("latency_ms")} ms; total {record["elapsed_seconds"]} s.', '',
                           *[f'- {item.get("timestamp")}: {item["action"]}' for item in r1.get('observations', [])], '',
                           f'Evidence: [{folder.name}/result.json]({folder.name}/result.json)', ''])
            (args.output / 'review.md').write_text('\n'.join(report))
            print(json.dumps({'case': case, 'summary': result['summary'], 'r1': r1.get('observations'),
                              'seconds': record['elapsed_seconds']}, ensure_ascii=False), flush=True)
    finally:
        await get_glm_client().close()
        await get_surgr1_client().close()
    (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append', required=True, help='session_id:zero_based_window_id')
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
