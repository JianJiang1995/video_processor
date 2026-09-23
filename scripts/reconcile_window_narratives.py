#!/usr/bin/env python3
"""Apply current generic sequence rules to saved local VLM facts, without re-inference."""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.routers.analysis import (
    _apply_surgical_sequence_rules, _build_surgical_sequence_state, _stable_expert_phase,
    _remove_negative_visual_checklist, _suppress_unsupported_tool_actions,
    _strip_unverified_target_specific_clip_claims, _retain_reviewed_clip_fact,
    _visibility_flags_from_visual,
    _stable_prior_phase,
)
from backend.services.mysql_service import get_mysql_service
from backend.services.local_visual_prompt import reviewed_scene_summary


def reconcile(args):
    args.output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(trust_env=False, timeout=30) as api:
        pending = api.get(f'http://127.0.0.1:8001/api/analysis/pending-refinements/{args.session}').json()
    if not pending.get('settled'):
        raise RuntimeError('Analysis must be settled')
    store = get_mysql_service()
    rows = store.get_all_window_summaries(args.session)
    (args.output / 'before_narrative_review.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    rebuilt, changes = [], []
    for original in rows:
        row = copy.deepcopy(original)
        others = row.get('others') or {}
        experts = others.get('experts') or {}
        model = experts.get('open_vlm') or {}
        current_visual = others.get('visual_gpt') or model.get('visual') or {}
        if others.get('manual_review') and _visibility_flags_from_visual(current_visual)['out_of_body']:
            phase = _stable_prior_phase(_build_surgical_sequence_state(rebuilt))
            text = reviewed_scene_summary(current_visual) or '镜头处于体外视野。'
            if phase != row['surgical_phase'] or text != row['glm_summary']:
                metadata = {**others['manual_review'], 'revision': uuid4().hex,
                            'completed_at': datetime.now(timezone.utc).isoformat(),
                            'kind': 'offline_narrative_reconciliation', 'new_model_call': False}
                updated = store.save_window_review(args.session, row['window_id'], text,
                                                   current_visual, metadata, phase=phase)
                changes.append({'window_id': row['window_id'], 'phase_before': row['surgical_phase'],
                                'phase_after': phase, 'before': row['glm_summary'], 'after': text,
                                'rules': ['scope_exit_preserves_surgical_phase', 'reviewed_scene_facts_only']})
                row.update(glm_summary=text, surgical_phase=phase, others=updated)
        try:
            raw = json.loads(model.get('raw') or '')
        except (ValueError, TypeError):
            raw = {}
        if others.get('manual_review') or not raw.get('summary') or not isinstance((raw.get('visual') or {}).get('observations'), list):
            rebuilt.append(row)
            continue
        visual = copy.deepcopy(others.get('visual_gpt') or model.get('visual') or {})
        visual['observations'] = raw['visual']['observations']
        text = _remove_negative_visual_checklist(raw['summary'])
        text = _suppress_unsupported_tool_actions(text, visual, experts)
        text = _strip_unverified_target_specific_clip_claims(text, visual, experts)
        text = _retain_reviewed_clip_fact(text, visual)
        if _visibility_flags_from_visual(visual)['out_of_body']:
            text = reviewed_scene_summary(visual) or '镜头处于体外视野。'
        if not text.strip():
            rebuilt.append(row)
            continue
        prior = _build_surgical_sequence_state(rebuilt)
        candidate = _stable_expert_phase(experts.get('phase') or {}, rebuilt, row['window_id'])
        text, phase, rules = _apply_surgical_sequence_rules(text, candidate, prior, visual)
        if text and (text != row['glm_summary'] or phase != row['surgical_phase']):
            change = {'window_id': row['window_id'], 'start_time': row['window_start'],
                      'before': row['glm_summary'], 'after': text,
                      'phase_before': row['surgical_phase'], 'phase_after': phase, 'rules': rules}
            metadata = {'revision': uuid4().hex, 'completed_at': datetime.now(timezone.utc).isoformat(),
                        'model': model.get('model') or 'local', 'kind': 'offline_narrative_reconciliation',
                        'new_model_call': False, 'rules': rules}
            updated = store.save_window_review(args.session, row['window_id'], text, visual, metadata, phase=phase)
            row.update(glm_summary=text, surgical_phase=phase, others=updated)
            changes.append(change)
        rebuilt.append(row)
    (args.output / 'narrative_reviews.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2))
    print(json.dumps({'session': args.session, 'updated': len(changes), 'windows': len(rows)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True)
    parser.add_argument('--output', type=Path, required=True)
    reconcile(parser.parse_args())
