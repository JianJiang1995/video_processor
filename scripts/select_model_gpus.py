#!/usr/bin/env python3
"""Place the sustained VLM workload away from an active display when possible."""
import argparse
import csv
import os
import subprocess


def parse_gpus(text):
    rows = []
    for row in csv.reader(text.splitlines()):
        index, free, display = [value.strip() for value in row]
        rows.append({'index': str(int(index)), 'free': int(free),
                     'display': {'Enabled': True, 'Disabled': False}.get(display)})
    return rows


def select_gpus(rows, preferred_r1, *, r1_override=None, vlm_override=None, r1_enabled=True):
    available = {row['index']: row for row in rows}
    if not available:
        raise ValueError('No NVIDIA GPU available')
    for value in (r1_override, vlm_override):
        if value is not None and value not in available:
            raise ValueError(f'Configured GPU {value} is not available')
    preferred_r1 = str(preferred_r1)
    candidates = [row for row in rows if not r1_enabled or row['index'] != r1_override]
    if not candidates:
        raise ValueError('SurgR1 and VLM require separate GPUs; disable SurgR1 on a single-GPU machine')
    non_display = [row for row in candidates if row['display'] is False]
    # With unknown/multiple active displays, retain the established R1 assignment.
    choices = non_display or [row for row in candidates
                             if not r1_enabled or row['index'] != preferred_r1] or candidates
    vlm = vlm_override or max(choices, key=lambda row: row['free'])['index']
    r1_candidates = [row for row in rows if row['index'] != vlm]
    if r1_enabled and not r1_candidates and not r1_override:
        raise ValueError('SurgR1 and VLM require separate GPUs')
    r1 = r1_override or (preferred_r1 if preferred_r1 in available and preferred_r1 != vlm
                         else max(r1_candidates, key=lambda row: row['free'])['index'] if r1_candidates else vlm)
    if r1_enabled and r1 == vlm:
        raise ValueError('SurgR1 and VLM were assigned to the same GPU')
    return vlm, r1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preferred-r1', required=True)
    parser.add_argument('--r1-enabled', choices=['0', '1'], default='1')
    args = parser.parse_args()
    result = subprocess.run(['nvidia-smi', '--query-gpu=index,memory.free,display_active',
                             '--format=csv,noheader,nounits'], check=True, capture_output=True, text=True, timeout=10)
    print(*select_gpus(parse_gpus(result.stdout), args.preferred_r1,
                      r1_override=os.environ.get('SURGR1_GPU') or None,
                      vlm_override=os.environ.get('VLM_GPU') or None,
                      r1_enabled=args.r1_enabled == '1'))
