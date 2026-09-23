#!/usr/bin/env python3
"""Summarize instrumented Electron receipts; never infer latency from polling."""
import argparse
import json
import math
from pathlib import Path
import statistics


def stats(values):
    values = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    if not values:
        return {'count': 0}
    return {'count': len(values), 'mean': round(statistics.mean(values), 3),
            'p50': round(statistics.median(values), 3),
            'p95': round(values[math.ceil(.95 * len(values)) - 1], 3),
            'max': round(values[-1], 3), 'min': round(values[0], 3)}


def summarize(data):
    drafts, reviews = {}, {}
    for receipt in data.get('receipts') or []:
        target = drafts if receipt.get('stage') == 1 else reviews
        target.setdefault(receipt['window_id'], receipt)

    def lag(row):
        timing = row.get('timing') or {}
        end = timing.get('source_window_end_wall')
        return row['received_wall'] - end if end is not None else None

    return {'session_id': data.get('session_id'), 'passed': data.get('passed'),
            'draft_lag_seconds': stats([lag(r) for r in drafts.values()]),
            'draft_after_first_30s_seconds': stats([lag(r) for r in drafts.values() if r['start'] >= 30]),
            'review_lag_seconds': stats([lag(r) for r in reviews.values()]),
            'expert_ms': stats([(r.get('timing') or {}).get('expert_ms') for r in drafts.values()]),
            'draft_over_5s_windows': [key for key, r in drafts.items() if lag(r) is not None and lag(r) > 5],
            'server_emit_to_browser_seconds': stats([r['received_wall'] - r['emitted_wall']
                for r in drafts.values() if r.get('emitted_wall') is not None]),
            'ready_to_save_to_browser_seconds': stats([r['received_wall'] - r['timing']['stage1_ready_to_save_wall']
                for r in drafts.values() if (r.get('timing') or {}).get('stage1_ready_to_save_wall')]),
            'expert_components_ms': {name: stats([(r.get('timing') or {}).get('expert_components_ms', {}).get(name)
                for r in drafts.values()]) for name in ('phase_ms', 'triplet_ms', 'yolo_ms', 'clip_detector_ms', 'cpu_cues_ms')}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = {str(path.resolve()): summarize(json.loads(path.read_text())) for path in args.files}
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    print(text, end='')
