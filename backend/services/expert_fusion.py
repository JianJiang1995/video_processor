"""Expert Fusion — run YOLO + Phase + Triplet on a window's frames and
produce a compact text summary suitable for Stage-1 (text-only) Gemini integration.

Call pattern (in analysis.py window loop):

    from .expert_fusion import run_experts_on_window

    expert_ctx = run_experts_on_window(window_frames_bgr)
    # expert_ctx["text"]  → paste into Gemini prompt
    # expert_ctx["yolo"]  → keep for frontend overlay
    # expert_ctx["phase"] → dominant phase
    # expert_ctx["triplet"]

This module is optional — if any expert service is disabled or fails, that
section is skipped gracefully so the pipeline degrades instead of erroring.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Set

import numpy as np

logger = logging.getLogger(__name__)

# Phase Expert 的输出类名（snake_case）与 WindowHistoryManager 里一套约束规则
# 用的 canonical 名字不一致，在这里映射一次。
PHASE_EXPERT_TO_CANONICAL: Dict[str, str] = {
    "preparation": "Preparation",
    "calot_triangle_dissection": "CalotTriangleDissection",
    "clipping_cutting": "ClippingCutting",
    "gallbladder_dissection": "GallbladderDissection",
    "gallbladder_retraction": "GallbladderRetraction",
    "cleaning_coagulation": "CleaningCoagulation",
    "gallbladder_packaging": "GallbladderPackaging",
}

CANONICAL_PHASE_ORDER: Dict[str, int] = {
    "Preparation": 0,
    "CalotTriangleDissection": 1,
    "ClippingCutting": 2,
    "GallbladderDissection": 3,
    "GallbladderRetraction": 4,
    "CleaningCoagulation": 5,
    "GallbladderPackaging": 6,
}


def _aggregate_yolo(per_frame: List[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Aggregate per-frame YOLO detections → unique tool labels + count."""
    label_counts: Counter = Counter()
    total = 0
    for dets in per_frame:
        for d in dets:
            label_counts[d["label"]] += 1
            total += 1
    return {
        "tools": [{"label": lbl, "frames_seen": c} for lbl, c in label_counts.most_common()],
        "total_detections": total,
        "frames_analyzed": len(per_frame),
    }


def _aggregate_phase(
    per_frame: List[Dict[str, Any]],
    reached_phases: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Majority vote across frames, with phase-order protection.

    如果 `reached_phases`（会话中已确认走过的阶段）给出了，且多数票落在一个回退
    阶段（PHASE_ORDER 小于已到达的最高阶段），会按得票顺序选择第一个"不回退"
    的阶段；若所有候选都回退则保持已到达的最高阶段，避免 Phase Expert 闪回。
    """
    if not per_frame:
        return {"label": "", "confidence": 0.0, "frame_count": 0}

    labels = [r.get("label", "") for r in per_frame]
    counter = Counter(labels)
    ranked = counter.most_common()

    def _is_regressive(raw_label: str) -> bool:
        if not reached_phases:
            return False
        canonical = PHASE_EXPERT_TO_CANONICAL.get(raw_label, raw_label)
        order = CANONICAL_PHASE_ORDER.get(canonical, -1)
        max_reached = max(
            (CANONICAL_PHASE_ORDER.get(p, -1) for p in reached_phases), default=-1
        )
        return order >= 0 and max_reached >= 0 and order < max_reached

    winner = ranked[0][0]
    demoted_from = None
    if reached_phases and _is_regressive(winner):
        demoted_from = winner
        # 挑得票最高且"不回退"的候选
        chosen = None
        for lbl, _ in ranked:
            if not _is_regressive(lbl):
                chosen = lbl
                break
        if chosen is not None:
            winner = chosen
        else:
            # 所有候选都回退：保持已到达最高阶段（转换成 phase_expert 格式）
            max_p = max(reached_phases, key=lambda p: CANONICAL_PHASE_ORDER.get(p, -1))
            inverse = {v: k for k, v in PHASE_EXPERT_TO_CANONICAL.items()}
            winner = inverse.get(max_p, winner)
        logger.info(
            f"[ExpertFusion] Phase demotion: raw top-1 was {demoted_from!r} (regressive), "
            f"replaced with {winner!r} (reached={sorted(reached_phases)})"
        )

    conf_values = [r["confidence"] for r in per_frame if r.get("label") == winner]
    conf = float(np.mean(conf_values)) if conf_values else 0.0
    result = {
        "label": winner,
        "confidence": round(conf, 3),
        "frame_count": len(per_frame),
        "vote_counts": dict(counter),
    }
    if demoted_from is not None:
        result["demoted_from"] = demoted_from
    return result


def _format_text(yolo: Dict[str, Any], phase: Dict[str, Any], triplet: Dict[str, Any]) -> str:
    """Build a compact text block to paste into Gemini prompts.

    Formatted for clarity — one section per expert with top-K findings.
    """
    lines = ["【专家模型判断】"]
    if phase:
        lines.append(f"[Phase Expert] {phase.get('label', '?')} (conf {phase.get('confidence', 0):.2f}, "
                     f"{phase.get('frame_count', 0)} 帧投票)")
    if yolo.get("tools"):
        tool_str = ", ".join(f"{t['label']}×{t['frames_seen']}" for t in yolo["tools"][:6])
        lines.append(f"[YOLO Expert] 检出工具 (帧出现次数): {tool_str}")
    else:
        lines.append("[YOLO Expert] 未检出工具")
    if triplet and triplet.get("triplet"):
        top3 = ", ".join(f"{t['label']} ({t['confidence']:.2f})" for t in triplet["triplet"][:3])
        lines.append(f"[Triplet Expert] top-3 动作三元组: {top3}")
    return "\n".join(lines)


def run_experts_on_window(
    frames_bgr: List[np.ndarray],
    use_yolo: bool = True,
    use_phase: bool = True,
    use_triplet: bool = True,
    reached_phases: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Run available expert services on a window of BGR frames.

    Gracefully skips any expert that's disabled or not initialized.
    Returns a dict with:
        - "text":    formatted text for Gemini prompt injection
        - "yolo":    aggregated YOLO result (frontend overlay friendly)
        - "phase":   aggregated Phase result (dominant label + conf)
        - "triplet": raw Triplet recognition (top-K I/V/T/IVT)
        - "available": list of experts that actually produced output
    """
    out: Dict[str, Any] = {"yolo": {}, "phase": {}, "triplet": {}, "available": []}
    if not frames_bgr:
        out["text"] = "【专家模型判断】(窗口无采样帧)"
        return out

    # Load/use torch-native experts before YOLO. Ultralytics can set
    # CUDA_VISIBLE_DEVICES to the selected YOLO GPU during initialization; if
    # YOLO lazy-loads first, later cuda:2/cuda:7 expert devices become invalid
    # within the same process.
    # Phase
    if use_phase:
        try:
            from .phase_service import get_phase_service
            phase = get_phase_service()
            if phase is not None and phase.is_ready:
                per_frame = phase.classify_batch(frames_bgr)
                out["phase"] = _aggregate_phase(per_frame, reached_phases=reached_phases)
                out["phase"]["per_frame"] = per_frame
                out["available"].append("phase")
        except Exception as e:
            logger.warning(f"[ExpertFusion] Phase skipped: {e}")

    # Triplet (one clip per window, uniform-sampled 10 frames)
    if use_triplet:
        try:
            from .triplet_service import get_triplet_service
            triplet = get_triplet_service()
            if triplet is not None and triplet.is_ready:
                out["triplet"] = triplet.recognize_clip(frames_bgr)
                out["available"].append("triplet")
        except Exception as e:
            logger.warning(f"[ExpertFusion] Triplet skipped: {e}")

    # YOLO
    if use_yolo:
        try:
            from .yolo_service import get_yolo_service
            yolo = get_yolo_service()
            if yolo is not None and yolo.is_ready:
                per_frame_dets = [yolo.detect(f) for f in frames_bgr]
                out["yolo"] = _aggregate_yolo(per_frame_dets)
                out["yolo"]["per_frame"] = per_frame_dets  # keep raw for overlay
                out["available"].append("yolo")
        except Exception as e:
            logger.warning(f"[ExpertFusion] YOLO skipped: {e}")

    out["text"] = _format_text(out["yolo"], out["phase"], out["triplet"])
    logger.info(f"[ExpertFusion] available={out['available']}")
    return out
