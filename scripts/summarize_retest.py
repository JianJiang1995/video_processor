"""Summarize recording telemetry, without interpreting clinical correctness."""
import argparse
import collections
import json
import math
from pathlib import Path
import statistics


def percentile(values, fraction):
    if not values:
        return None
    return round(sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)], 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory
    metadata = json.loads((root / 'run.json').read_text())
    snapshots = [json.loads(line) for line in (root / 'telemetry.jsonl').read_text().splitlines()]
    rows_path = root / 'summaries_settled.json'
    if not rows_path.exists():
        rows_path = root / 'summaries.json'
    rows = json.loads(rows_path.read_text())
    first_stage, first_refined = {}, {}
    offsets = [s['elapsed'] - s['ui']['time'] for s in snapshots[:10]
               if s['ui'].get('time') is not None and not s['ui'].get('loop')]
    origin_offset = statistics.median(offsets)
    ends = {}
    for snapshot in snapshots:
        for window in snapshot['telemetry']['windows']:
            wid = window['window_id']
            ends[wid] = window['end_time']
            first_stage.setdefault(wid, snapshot['elapsed'])
            if window['vlm_complete']:
                first_refined.setdefault(wid, snapshot['elapsed'])
    stage_lags = [max(0, elapsed - origin_offset - ends[wid]) for wid, elapsed in first_stage.items()]
    refined_lags = [max(0, elapsed - origin_offset - ends[wid]) for wid, elapsed in first_refined.items()]
    calls, providers = collections.Counter(), collections.Counter()
    failures = []
    for row in rows:
        others = row.get('others') or {}
        if isinstance(others, str):
            others = json.loads(others)
        hint = ((others.get('experts') or {}).get('open_vlm') or {})
        providers[str(hint.get('provider'))] += 1
        if not hint.get('success'):
            failures.append(row['window_id'])
        for name, call in (others.get('model_calls') or {}).items():
            calls[name] += int(call.get('count') or 0)
    duration = metadata['duration']
    expected = math.ceil(duration / 5)
    observed = {r['window_id'] for r in rows}
    result = {
        'session_id': metadata['session_id'], 'source_duration': duration,
        'window_count': len(rows), 'expected_windows': expected,
        'missing_window_ids': sorted(set(range(expected)) - observed),
        'beyond_duration': [r['window_id'] for r in rows if r['end_time'] > duration + .05],
        'last_end_time': max((r['end_time'] for r in rows), default=0),
        'stage1_first_observed_lag_p50': percentile(stage_lags, .5),
        'stage1_first_observed_lag_p95': percentile(stage_lags, .95),
        'refinement_first_observed_lag_p50': percentile(refined_lags, .5),
        'refinement_first_observed_lag_p95': percentile(refined_lags, .95),
        'refinements_observed': len(first_refined), 'refinements_failed_or_pending': failures,
        'model_calls': dict(calls), 'vision_providers': dict(providers),
        'peak_pending': max((s.get('pending', {}).get('pending', 0) for s in snapshots), default=0),
        'last_ui': snapshots[-1]['ui'],
        'timing_note': 'First observation includes up to one polling interval; not direct inference timing.',
    }
    (root / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
