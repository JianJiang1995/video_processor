#!/usr/bin/env python3
"""Local image re-review of unsettled scene evidence after the entire run finishes."""
import argparse
import asyncio
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.services.local_visual_prompt import scene_observations_need_review
from backend.services.mysql_service import get_mysql_service
from backend.services.window_reanalysis import load_review_frames, review_scene


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(trust_env=False, timeout=30) as api:
        pending = api.get(f'http://127.0.0.1:8001/api/analysis/pending-refinements/{args.session}').json()
    if not pending.get('settled'):
        raise RuntimeError('Wait for live analysis and refinements to finish')
    store = get_mysql_service()
    session = store.get_video_session(args.session)
    if not session:
        raise RuntimeError('Unknown session')
    rows = store.get_all_window_summaries(args.session)
    (args.output / 'before_scene_review.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    changes = []
    for row in rows:
        others = row.get('others') or {}
        model = (others.get('experts') or {}).get('open_vlm') or {}
        visual = copy.deepcopy(others.get('visual_gpt') or model.get('visual') or {})
        try:
            raw = json.loads(model.get('raw', ''))
        except (ValueError, TypeError):
            raw = {}
        raw_visual = raw.get('visual') or visual
        review = visual.get('visibility_secondary_review') or {}
        manual = others.get('manual_review') or {}
        confirmed_scene = review.get('success') and review.get('classification') in {
            'external_body', 'trocar_transition', 'specimen_bag_inside'}
        user_review = bool(manual) and manual.get('kind') not in {
            'offline_scene_review', 'offline_narrative_reconciliation'}
        if not scene_observations_need_review(raw_visual) or confirmed_scene or user_review:
            continue
        images, timestamps = load_review_frames(session, row['window_start'], row['window_end'])
        for index, image in enumerate(images):
            image.save(args.output / f"window_{row['window_id'] + 1}_{index}.jpg")
        # Use the original structured observations, not a suggested scene label.
        result = await review_scene(images, timestamps, {
            'summary': row['glm_summary'], 'visual': raw_visual, 'model': model.get('model') or 'local'})
        scene = result['visual'].get('visibility_secondary_review') or {}
        save = (scene.get('classification') in {'external_body', 'trocar_transition'}
                and float(scene.get('confidence') or 0) >= .75
                and result['summary'] != row['glm_summary'])
        item = {'window_id': row['window_id'], 'start_time': row['window_start'],
                'before': row['glm_summary'], 'after': result['summary'], 'scene': scene, 'saved': save}
        if save:
            store.save_window_review(args.session, row['window_id'], result['summary'], result['visual'], {
                'revision': uuid4().hex, 'completed_at': datetime.now(timezone.utc).isoformat(),
                'model': result['model'], 'frame_count': len(images), 'timestamps': timestamps,
                'kind': 'offline_scene_review',
            })
        changes.append(item)
        (args.output / 'scene_reviews.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2))
        print(json.dumps(item, ensure_ascii=False), flush=True)
    print(json.dumps({'reviewed': len(changes), 'updated': sum(x['saved'] for x in changes)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
