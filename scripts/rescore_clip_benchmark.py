#!/usr/bin/env python3
"""Re-score all clip binary benchmark runs with audited label corrections."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

CORR = json.loads(Path("datasets/clip_detector_reviewed_seed_v1/label_corrections_20260709.json").read_text())
RELABEL = set(CORR["relabel_to_clip"])
EXCLUDE = set(CORR["exclude"])


def corrected(row):
    name = Path(row["image"]).name
    stem_hits = [c for c in RELABEL if Path(c).stem in name]
    if any(Path(c).stem in name for c in EXCLUDE):
        return None
    expected = "clip" if stem_hits else row["expected"]
    return expected


def main():
    latest = {}
    for summary in sorted(Path("runs/clip_vlm_binary_benchmark_v2").glob("*/results.jsonl")):
        cand = summary.parent.name.rsplit("_", 2)[0]
        latest[cand] = summary  # sorted() keeps the latest run per candidate

    print(f"{'candidate':30s} {'n':>4s} {'acc':>6s} {'recall':>7s} {'spec':>6s} {'hardR':>6s} {'hardS':>6s} {'lat':>6s} {'p95':>6s}")
    results = {}
    for cand, path in sorted(latest.items()):
        rows = [json.loads(l) for l in open(path)]
        scored = []
        for r in rows:
            exp = corrected(r)
            if exp is None:
                continue
            scored.append({**r, "expected": exp, "correct": r["predicted"] == exp})
        n = len(scored)
        pos = [r for r in scored if r["expected"] == "clip"]
        neg = [r for r in scored if r["expected"] == "no_clip"]
        hpos = [r for r in pos if r["hard"]]
        hneg = [r for r in neg if r["hard"]]
        lat = [r["latency_seconds"] for r in scored if r["success"]]
        def rate(xs):
            return round(sum(x["correct"] for x in xs) / len(xs), 3) if xs else float("nan")
        acc = rate(scored); rec = rate(pos); spec = rate(neg)
        hr = rate(hpos); hs = rate(hneg)
        avg = round(statistics.mean(lat), 2) if lat else float("nan")
        p95 = round(sorted(lat)[max(0, int(len(lat)*0.95)-1)], 2) if len(lat) > 1 else float("nan")
        print(f"{cand:30s} {n:4d} {acc:6.3f} {rec:7.3f} {spec:6.3f} {hr:6.3f} {hs:6.3f} {avg:6.2f} {p95:6.2f}")
        results[cand] = {"n": n, "acc": acc, "recall": rec, "spec": spec,
                         "hard_recall": hr, "hard_spec": hs, "avg_latency": avg, "p95": p95}
    Path("runs/clip_vlm_binary_benchmark_v2/rescored_summary.json").write_text(
        json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
