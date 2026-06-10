"""Evaluate offline pipeline outputs against Cholec80 ground truth.

Usage:
  python -m offline_pipeline.scripts.eval_cholec80 \
    --session offline_pipeline/jobs/b4009181 \
    --video-id 1
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

CHOLEC80_ROOT = "/data3/tos_copy/cholec80/cholec80"
SRC_FPS = 25  # Cholec80 native FPS

# YOLO class label → Cholec80 tool column name
YOLO_TO_GT = {
    "grasper": "Grasper",
    "bipolar": "Bipolar",
    "hook": "Hook",
    "scissors": "Scissors",
    "clipper": "Clipper",
    "irrigator": "Irrigator",
    "specimen_bag": "SpecimenBag",
    # "snare" is in our YOLO model but not in Cholec80 — ignored in eval
}
GT_TOOL_COLS = ["Grasper", "Bipolar", "Hook", "Scissors", "Clipper", "Irrigator", "SpecimenBag"]


def _norm(p: str) -> str:
    return p.lower().replace("_", "").replace(" ", "")


def load_phase_gt(video_id: int) -> List[str]:
    """Returns per-source-frame phase labels (length = n_frames)."""
    p = Path(CHOLEC80_ROOT) / "phase_annotations" / f"video{video_id:02d}-phase.txt"
    labels: List[str] = []
    with p.open() as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            labels.append(parts[1])
    return labels


def load_tool_gt(video_id: int) -> Tuple[List[int], np.ndarray]:
    """Returns (frame_indices_at_1fps, binary_matrix [N, 7])."""
    p = Path(CHOLEC80_ROOT) / "tool_annotations" / f"video{video_id:02d}-tool.txt"
    indices: List[int] = []
    rows: List[List[int]] = []
    with p.open() as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            indices.append(int(parts[0]))
            rows.append([int(x) for x in parts[1:8]])
    return indices, np.array(rows, dtype=np.int8)


def evaluate(session_dir: str, video_id: int) -> Dict:
    fr = json.loads((Path(session_dir) / "frames_results.json").read_text())
    n = len(fr)

    # ----- Phase eval -----
    gt_phases = load_phase_gt(video_id)
    pred_norm: List[str] = []
    gt_norm: List[str] = []
    per_class_correct: Dict[str, int] = defaultdict(int)
    per_class_total: Dict[str, int] = defaultdict(int)
    confusion: Dict[Tuple[str, str], int] = defaultdict(int)
    matched = 0

    for i, r in enumerate(fr):
        src_idx = i * SRC_FPS
        if src_idx >= len(gt_phases):
            break
        g = _norm(gt_phases[src_idx])
        p = _norm(r["phase_label"]) if r.get("phase_label") else ""
        if not p:
            continue
        gt_norm.append(g); pred_norm.append(p)
        per_class_total[g] += 1
        if p == g:
            per_class_correct[g] += 1
            matched += 1
        confusion[(g, p)] += 1

    phase_acc = matched / max(len(pred_norm), 1)
    phase_per_class = {
        g: {"acc": per_class_correct[g] / per_class_total[g],
            "n": per_class_total[g]}
        for g in sorted(per_class_total)
    }

    # ----- Tool eval -----
    gt_idx, gt_tools = load_tool_gt(video_id)
    # Build dict src_frame_idx → row
    gt_map = {int(idx): gt_tools[i] for i, idx in enumerate(gt_idx)}
    tool_per_class: Dict[str, Dict] = {}
    # Per-class TP/FP/FN
    counters: Dict[str, Dict[str, int]] = {col: {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
                                            for col in GT_TOOL_COLS}
    n_eval = 0
    for i, r in enumerate(fr):
        src_idx = i * SRC_FPS
        if src_idx not in gt_map:
            continue
        gt_row = gt_map[src_idx]  # array of 7 (Grasper, Bipolar, Hook, Scissors, Clipper, Irrigator, SpecimenBag)
        pred_present = {col: False for col in GT_TOOL_COLS}
        for det in r.get("tools", []):
            col = YOLO_TO_GT.get(det["label"])
            if col:
                pred_present[col] = True
        for col_i, col in enumerate(GT_TOOL_COLS):
            g = bool(gt_row[col_i])
            p = pred_present[col]
            if g and p: counters[col]["tp"] += 1
            elif p and not g: counters[col]["fp"] += 1
            elif g and not p: counters[col]["fn"] += 1
            else: counters[col]["tn"] += 1
        n_eval += 1

    tool_metrics: Dict[str, Dict] = {}
    macro = {"precision": [], "recall": [], "f1": [], "acc": []}
    for col in GT_TOOL_COLS:
        c = counters[col]
        prec = c["tp"] / max(c["tp"] + c["fp"], 1)
        rec = c["tp"] / max(c["tp"] + c["fn"], 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-9)
        acc = (c["tp"] + c["tn"]) / max(sum(c.values()), 1)
        tool_metrics[col] = {"precision": prec, "recall": rec, "f1": f1,
                             "acc": acc, "support_pos": c["tp"] + c["fn"], **c}
        macro["precision"].append(prec); macro["recall"].append(rec)
        macro["f1"].append(f1); macro["acc"].append(acc)
    macro_metrics = {k: float(np.mean(v)) for k, v in macro.items()}

    return {
        "session_dir": session_dir,
        "video_id": video_id,
        "n_pred_frames": n,
        "phase": {
            "n_eval": len(pred_norm),
            "frame_accuracy": phase_acc,
            "per_class": phase_per_class,
            "confusion_top": sorted(
                ((f"{g}→{p}", c) for (g, p), c in confusion.items() if g != p),
                key=lambda x: -x[1])[:10],
        },
        "tool": {
            "n_eval": n_eval,
            "macro": macro_metrics,
            "per_class": tool_metrics,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--video-id", type=int, required=True)
    ap.add_argument("--out", default=None, help="Optional path to write JSON report")
    args = ap.parse_args()

    rep = evaluate(args.session, args.video_id)

    print("=" * 60)
    print(f"video{args.video_id:02d} — {args.session}")
    print("=" * 60)
    print(f"\n[Phase]  frame accuracy = {rep['phase']['frame_accuracy']*100:.2f}%  "
          f"(n={rep['phase']['n_eval']})")
    print(f"{'class':30s} {'acc':>8s} {'n':>6s}")
    for cls, m in rep["phase"]["per_class"].items():
        print(f"  {cls:28s} {m['acc']*100:7.2f}% {m['n']:6d}")
    print(f"\n  Top confusions (gt→pred, count):")
    for k, v in rep["phase"]["confusion_top"]:
        print(f"    {k:60s} {v}")

    print(f"\n[Tool]   n_eval = {rep['tool']['n_eval']}")
    m = rep["tool"]["macro"]
    print(f"  macro: precision={m['precision']*100:.2f}%  "
          f"recall={m['recall']*100:.2f}%  f1={m['f1']*100:.2f}%  "
          f"acc={m['acc']*100:.2f}%")
    print(f"  {'tool':14s} {'prec':>7s} {'rec':>7s} {'f1':>7s} {'acc':>7s} {'supp':>6s}  "
          f"{'TP':>5s} {'FP':>5s} {'FN':>5s}")
    for col, met in rep["tool"]["per_class"].items():
        print(f"  {col:14s} {met['precision']*100:6.2f}% {met['recall']*100:6.2f}% "
              f"{met['f1']*100:6.2f}% {met['acc']*100:6.2f}% {met['support_pos']:6d}  "
              f"{met['tp']:5d} {met['fp']:5d} {met['fn']:5d}")

    if args.out:
        Path(args.out).write_text(json.dumps(rep, indent=2, ensure_ascii=False))
        print(f"\nReport saved to {args.out}")


if __name__ == "__main__":
    main()
