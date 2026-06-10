"""Evaluate offline pipeline outputs against CholecT50 ground truth.

CholecT50 is 1-FPS sampled (one PNG per second of original 25-FPS video).
We synthesize a 1-FPS mp4 from the PNG folder and run the offline pipeline
with --sample-fps 1, so every PNG → exactly one predicted frame, GT row
index = pred frame index.

Computes:
  - Phase: frame-level accuracy + per-class
  - Triplet (IVT 100-class multi-label):
      mAP-IVT (sklearn average_precision_score)
      F1 @ threshold
      Top-K precision / recall
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

import numpy as np

CHOLECT50_ROOT = "/data3/tos_copy/CholecT50/CholecT50"

# CholecT50 phase id → our model's label name (lowercase_with_underscores)
PHASE_ID_TO_NAME = {
    0: "preparation",
    1: "calot_triangle_dissection",
    2: "clipping_cutting",
    3: "gallbladder_dissection",
    4: "gallbladder_packaging",
    5: "cleaning_coagulation",
    6: "gallbladder_retraction",
}


def load_gt(video_id: str) -> Dict[int, Dict]:
    """video_id like 'VID01'. Returns dict frame_idx → {phase: int|None, ivt: set[int]}."""
    p = Path(CHOLECT50_ROOT) / "labels" / f"{video_id}.json"
    raw = json.loads(p.read_text())
    ann = raw["annotations"]
    out: Dict[int, Dict] = {}
    for fid, rows in ann.items():
        i = int(fid)
        ivt_set = set()
        phase = None
        for row in rows:
            if len(row) >= 15:
                if phase is None:
                    phase = int(row[14])
                if int(row[0]) >= 0:
                    ivt_set.add(int(row[0]))
        out[i] = {"phase": phase, "ivt": ivt_set}
    return out


def evaluate(session_dir: str, video_id: str,
             ivt_threshold: float = 0.5, topk: int = 5) -> Dict:
    fr = json.loads((Path(session_dir) / "frames_results.json").read_text())
    gt = load_gt(video_id)
    n = len(fr)

    # --- Phase ---
    correct = 0
    total = 0
    per_cls_total = Counter()
    per_cls_correct = Counter()
    confusion = defaultdict(int)
    for i, r in enumerate(fr):
        if i not in gt or gt[i]["phase"] is None:
            continue
        gt_name = PHASE_ID_TO_NAME[gt[i]["phase"]]
        pred = r.get("phase_label") or ""
        per_cls_total[gt_name] += 1
        total += 1
        if pred == gt_name:
            correct += 1
            per_cls_correct[gt_name] += 1
        else:
            confusion[(gt_name, pred)] += 1
    phase_acc = correct / max(total, 1)

    # --- Triplet (IVT multi-label, 100 classes) ---
    # Build [N, 100] pred probs and gt binary
    n_eff = min(n, len(gt))
    pred_probs = np.zeros((n_eff, 100), dtype=np.float32)
    gt_bin = np.zeros((n_eff, 100), dtype=np.int8)
    for i in range(n_eff):
        if i not in gt:
            continue
        for c in gt[i]["ivt"]:
            if 0 <= c < 100:
                gt_bin[i, c] = 1
        probs = fr[i].get("ivt_probs")
        if probs is not None:
            arr = np.array(probs, dtype=np.float32)
            if arr.shape == (100,):
                pred_probs[i] = arr

    # per-class AP (sklearn)
    from sklearn.metrics import average_precision_score
    aps = []
    per_class_ap = {}
    for c in range(100):
        if gt_bin[:, c].sum() == 0:
            continue  # skip classes never appearing in GT
        ap = average_precision_score(gt_bin[:, c], pred_probs[:, c])
        aps.append(ap)
        per_class_ap[c] = ap
    mAP = float(np.mean(aps)) if aps else 0.0

    # F1 @ threshold (per-frame multi-label)
    pred_bin = (pred_probs >= ivt_threshold).astype(np.int8)
    tp = int(((pred_bin == 1) & (gt_bin == 1)).sum())
    fp = int(((pred_bin == 1) & (gt_bin == 0)).sum())
    fn = int(((pred_bin == 0) & (gt_bin == 1)).sum())
    prec = tp / max(tp + fp, 1)
    rec = tp / max(tp + fn, 1)
    f1 = 2 * prec * rec / max(prec + rec, 1e-9)

    # Top-K (per-frame): take top-K predicted classes, compare to GT set
    topk_p_sum = 0.0
    topk_r_sum = 0.0
    topk_frames = 0
    for i in range(n_eff):
        gt_set = set(np.where(gt_bin[i])[0].tolist())
        if not gt_set:
            continue
        topk_set = set(np.argsort(-pred_probs[i])[:topk].tolist())
        inter = len(gt_set & topk_set)
        topk_p_sum += inter / topk
        topk_r_sum += inter / len(gt_set)
        topk_frames += 1
    topk_p = topk_p_sum / max(topk_frames, 1)
    topk_r = topk_r_sum / max(topk_frames, 1)

    # GT-class coverage stats
    n_gt_classes = int((gt_bin.sum(axis=0) > 0).sum())
    most_freq = sorted(((c, int(gt_bin[:, c].sum())) for c in range(100)),
                       key=lambda x: -x[1])[:10]

    return {
        "session_dir": session_dir,
        "video_id": video_id,
        "n_frames": n_eff,
        "phase": {
            "frame_accuracy": phase_acc,
            "n_eval": total,
            "per_class": {
                k: {"acc": per_cls_correct[k] / per_cls_total[k],
                    "n": per_cls_total[k]}
                for k in sorted(per_cls_total)
            },
            "top_confusions": sorted(
                ((f"{a}→{b}", c) for (a, b), c in confusion.items()),
                key=lambda x: -x[1])[:5],
        },
        "triplet": {
            "n_eval_frames": n_eff,
            "n_classes_in_gt": n_gt_classes,
            "mAP_IVT": mAP,
            f"F1@{ivt_threshold}": f1,
            f"P@{ivt_threshold}": prec,
            f"R@{ivt_threshold}": rec,
            f"top{topk}_precision": topk_p,
            f"top{topk}_recall": topk_r,
            "tp": tp, "fp": fp, "fn": fn,
            "top10_per_class_ap": [
                {"ivt_id": c, "ap": per_class_ap.get(c, 0.0),
                 "support": int(gt_bin[:, c].sum())}
                for c, _ in most_freq
            ],
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--video-id", default="VID01")
    ap.add_argument("--ivt-threshold", type=float, default=0.5)
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rep = evaluate(args.session, args.video_id, args.ivt_threshold, args.topk)

    print("=" * 64)
    print(f"CholecT50 {args.video_id} — {args.session}")
    print("=" * 64)
    print(f"\n[Phase]  frame accuracy = {rep['phase']['frame_accuracy']*100:.2f}%  "
          f"(n={rep['phase']['n_eval']})")
    for cls, m in rep["phase"]["per_class"].items():
        print(f"  {cls:30s} {m['acc']*100:7.2f}% ({m['n']:5d})")
    if rep["phase"]["top_confusions"]:
        print("  top confusions:")
        for k, v in rep["phase"]["top_confusions"]:
            print(f"    {k:60s} {v}")

    t = rep["triplet"]
    print(f"\n[Triplet IVT]  n_frames={t['n_eval_frames']}  "
          f"classes_seen_in_gt={t['n_classes_in_gt']}/100")
    print(f"  mAP-IVT (sklearn AP, GT-present classes only)  = {t['mAP_IVT']*100:.2f}%")
    print(f"  Multi-label @ thr={args.ivt_threshold}: "
          f"P={t[f'P@{args.ivt_threshold}']*100:.2f}%  "
          f"R={t[f'R@{args.ivt_threshold}']*100:.2f}%  "
          f"F1={t[f'F1@{args.ivt_threshold}']*100:.2f}%  "
          f"(TP={t['tp']} FP={t['fp']} FN={t['fn']})")
    print(f"  Top-{args.topk} per frame: P={t[f'top{args.topk}_precision']*100:.2f}%  "
          f"R={t[f'top{args.topk}_recall']*100:.2f}%")
    print(f"\n  AP for top-10 most-frequent IVT classes in GT:")
    for r in t["top10_per_class_ap"]:
        print(f"    ivt_id={r['ivt_id']:3d}  AP={r['ap']*100:6.2f}%  support={r['support']}")

    if args.out:
        Path(args.out).write_text(json.dumps(rep, indent=2, ensure_ascii=False))
        print(f"\nReport saved to {args.out}")


if __name__ == "__main__":
    main()
