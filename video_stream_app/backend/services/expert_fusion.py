"""Expert Fusion — run YOLO + ClipDetector + Phase + Triplet on a window's frames and
produce a compact text summary suitable for Stage-1 (text-only) Gemini integration.

Call pattern (in analysis.py window loop):

    from .expert_fusion import run_experts_on_window

    expert_ctx = run_experts_on_window(window_frames_bgr)
    # expert_ctx["text"]  → paste into Gemini prompt
    # expert_ctx["yolo"]  → keep for frontend overlay
    # expert_ctx["clip_detector"] → deployed surgical clip candidates
    # expert_ctx["phase"] → dominant phase
    # expert_ctx["triplet"]

This module is optional — if any expert service is disabled or fails, that
section is skipped gracefully so the pipeline degrades instead of erroring.
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Set

import cv2
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
    "CleaningCoagulation": 4,
    "GallbladderPackaging": 4,
}

TRIPLET_INSTRUMENT_CN = {
    "bipolar": "双极电凝钳",
    "clipper": "钛夹钳",
    "grasper": "抓钳",
    "hook": "电凝钩",
    "irrigator": "冲吸器",
    "scissors": "剪刀",
    "snare": "圈套器",
    "specimen_bag": "标本袋",
}

TRIPLET_VERB_CN = {
    "clip": "夹闭",
    "cut": "切断",
    "coagulate": "凝血处理",
    "dissect": "分离",
    "grasp": "夹持",
    "retract": "牵拉",
    "irrigate": "冲洗",
    "aspirate": "吸引",
    "pack": "装袋",
}

TRIPLET_TARGET_CN = {
    "cystic_duct": "胆囊管",
    "cystic_artery": "胆囊动脉",
    "blood_vessel": "胆囊动脉",
    "cystic_pedicle": "胆囊管",
    "gallbladder": "胆囊",
    "gallbladder_bed": "胆囊床",
    "cystic_plate": "胆囊板",
    "liver": "肝脏",
    "peritoneum": "腹膜",
    "adhesion": "粘连组织",
    "fluid": "液体",
    "hepatic_fossa": "肝窝",
    "calot_triangle": "肝胆三角",
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _parse_triplet_label(label: Any) -> List[str]:
    parts = re.findall(r"\[([^\]]+)\]", str(label or ""))
    if len(parts) >= 3:
        return [p.strip() for p in parts[:3]]
    cleaned = str(label or "").replace("[", "").replace("]", "")
    parsed = [p.strip() for p in cleaned.split("-") if p.strip()]
    while len(parsed) < 3:
        parsed.append("")
    return parsed[:3]


def _triplet_target_hint(triplet: Dict[str, Any]) -> Dict[str, Any]:
    scores = {"cystic_duct": 0.0, "cystic_artery": 0.0}
    for item in (triplet or {}).get("triplet") or []:
        _, verb, target = _parse_triplet_label(item.get("label"))
        conf = _safe_float(item.get("confidence"), 0.0)
        bonus = 0.08 if verb in {"clip", "cut", "coagulate"} else 0.0
        if target == "cystic_duct":
            scores["cystic_duct"] = max(scores["cystic_duct"], conf + bonus)
        elif target in {"cystic_artery", "blood_vessel"}:
            scores["cystic_artery"] = max(scores["cystic_artery"], conf + bonus)
        elif target == "cystic_pedicle":
            scores["cystic_duct"] = max(scores["cystic_duct"], conf * 0.55)
    for item in (triplet or {}).get("target") or []:
        label = str(item.get("label") or "").lower()
        conf = _safe_float(item.get("confidence"), 0.0)
        if label == "cystic_duct":
            scores["cystic_duct"] = max(scores["cystic_duct"], conf * 0.75)
        elif label in {"cystic_artery", "blood_vessel"}:
            scores["cystic_artery"] = max(scores["cystic_artery"], conf * 0.75)
        elif label == "cystic_pedicle":
            scores["cystic_duct"] = max(scores["cystic_duct"], conf * 0.40)
    label = "cystic_artery" if scores["cystic_artery"] > scores["cystic_duct"] else "cystic_duct"
    return {"label": label, "confidence": round(scores[label], 3)}


def _triplet_operation_phrases(triplet: Dict[str, Any], phase_label: str = "", max_items: int = 5) -> List[str]:
    target_hint = _triplet_target_hint(triplet)
    target_hint_cn = "胆囊动脉" if target_hint["label"] == "cystic_artery" else "胆囊管"
    phrases: List[str] = []
    seen = set()
    core_targets = {"cystic_duct", "cystic_artery", "blood_vessel", "cystic_pedicle"}
    safe_progress_targets = {
        "cystic_duct", "cystic_artery", "blood_vessel", "cystic_pedicle",
        "gallbladder", "gallbladder_bed", "cystic_plate", "calot_triangle",
    }
    for item in (triplet or {}).get("triplet") or []:
        conf = _safe_float(item.get("confidence"), 0.0)
        if conf < 0.12:
            continue
        inst, verb, target = _parse_triplet_label(item.get("label"))
        verb_cn = TRIPLET_VERB_CN.get(verb, "")
        if not verb_cn:
            continue
        inst_cn = TRIPLET_INSTRUMENT_CN.get(inst, "")
        if verb == "coagulate":
            if target in {"cystic_artery", "blood_vessel"} and conf >= 0.18:
                target_cn = "胆囊动脉"
            else:
                continue
        elif verb in {"clip", "cut"}:
            if target not in core_targets:
                continue
            target_cn = TRIPLET_TARGET_CN.get(target, target_hint_cn)
        else:
            if target not in safe_progress_targets or conf < 0.18:
                continue
            target_cn = TRIPLET_TARGET_CN.get(target, "")
            if verb == "retract" and target in core_targets:
                target_cn = "胆囊颈和胆囊体"
            elif verb == "grasp" and target in core_targets:
                target_cn = "胆囊颈附近组织"
        if not target_cn:
            continue
        phrase = f"{inst_cn}{verb_cn}{target_cn}" if inst_cn else f"{verb_cn}{target_cn}"
        if phrase in seen:
            continue
        seen.add(phrase)
        phrases.append(f"{phrase}({conf:.2f})")
        if len(phrases) >= max_items:
            break
    return phrases


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


def _aggregate_clip_detector(per_frame: List[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Aggregate per-frame deployed clip detections."""
    total = 0
    frames_seen = 0
    max_conf = 0.0
    top: List[Dict[str, Any]] = []
    for frame_index, dets in enumerate(per_frame):
        if dets:
            frames_seen += 1
        for det in dets:
            total += 1
            conf = _safe_float(det.get("confidence"), 0.0)
            max_conf = max(max_conf, conf)
            item = dict(det)
            item["frame_index"] = frame_index
            top.append(item)
    top.sort(key=lambda d: _safe_float(d.get("confidence"), 0.0), reverse=True)
    return {
        "detections_total": total,
        "frames_seen": frames_seen,
        "frames_analyzed": len(per_frame),
        "max_confidence": round(max_conf, 3),
        "top_detections": top[:8],
    }


def _detect_short_action(frames_bgr: List[np.ndarray]) -> Dict[str, Any]:
    """Detect brief shiny tool-tip entry/contact events with a cheap heuristic."""
    hits: List[Dict[str, Any]] = []
    for idx, frame in enumerate(frames_bgr):
        if frame is None or frame.size == 0:
            continue
        h, w = frame.shape[:2]
        scale = 640.0 / max(w, 1)
        small = cv2.resize(frame, (640, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        sh, sw = small.shape[:2]
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        mask = ((hsv[:, :, 1] < 75) & (hsv[:, :, 2] > 178)).astype(np.uint8) * 255
        mask[: int(sh * 0.04), :] = 0
        mask[int(sh * 0.82) :, :] = 0
        mask[:, : int(sw * 0.03)] = 0
        mask[:, int(sw * 0.97) :] = 0
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

        n, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        for comp_id in range(1, n):
            x, y, bw, bh, area = stats[comp_id]
            if area < 18 or area > 900:
                continue
            aspect = bh / max(1, bw)
            if bw > 90 or bh < 24 or aspect < 1.8:
                continue
            cx, cy = centroids[comp_id]
            hits.append({
                "frame_index": idx,
                "bbox": [
                    int(x / scale), int(y / scale),
                    int((x + bw) / scale), int((y + bh) / scale),
                ],
                "centroid": [round(float(cx / scale), 1), round(float(cy / scale), 1)],
                "area": int(area),
                "aspect": round(float(aspect), 2),
            })
            break

    needle_hits = []
    for hit in hits:
        x1, _, x2, _ = hit["bbox"]
        bw = x2 - x1
        cx = hit["centroid"][0]
        # Original frame is 1920 wide in our capture-card simulator; using the
        # hit bbox avoids carrying frame dimensions through every result.
        if hit["aspect"] >= 4.0 and bw <= 55 and 420 <= cx <= 1500:
            needle_hits.append(hit)

    if len(hits) < 2 or not needle_hits:
        return {"detected": False, "frames_seen": len(hits), "hits": hits}

    ys = [hit["centroid"][1] for hit in hits]
    return {
        "detected": True,
        "label": "puncture_like_contact",
        "tool_hint": "hook_or_metal_tip",
        "frames_seen": len(hits),
        "vertical_motion": round(float(max(ys) - min(ys)), 1) if ys else 0,
        "description": "电凝钩尖端接触并分离组织",
        "needle_hits": needle_hits[:4],
        "hits": hits[:8],
    }


def _detect_blue_bipolar_forceps(frames_bgr: List[np.ndarray]) -> Dict[str, Any]:
    """Detect persistent blue-insulated bipolar jaws without a model call."""
    hits: List[Dict[str, Any]] = []
    analyzed = 0
    max_blue_ratio = 0.0
    for frame_index, frame in enumerate(frames_bgr):
        if frame is None or frame.size == 0:
            continue
        analyzed += 1
        h, w = frame.shape[:2]
        if w > 960:
            scale = 960.0 / w
            frame = cv2.resize(frame, (960, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
            h, w = frame.shape[:2]

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        blue = cv2.inRange(hsv, (90, 45, 45), (150, 255, 255))
        blue = cv2.morphologyEx(blue, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        component_count, _, stats, _ = cv2.connectedComponentsWithStats(blue, 8)
        component_ratios = sorted(
            (
                float(stats[index, cv2.CC_STAT_AREA]) / max(1, h * w)
                for index in range(1, component_count)
            ),
            reverse=True,
        )
        total_ratio = float(np.count_nonzero(blue)) / max(1, h * w)
        max_blue_ratio = max(max_blue_ratio, total_ratio)
        largest = component_ratios[0] if component_ratios else 0.0
        second = component_ratios[1] if len(component_ratios) > 1 else 0.0
        paired_or_large = second >= 0.00008 or largest >= 0.00075
        if total_ratio >= 0.00055 and largest >= 0.00022 and paired_or_large:
            hits.append({
                "frame_index": frame_index,
                "blue_ratio": round(total_ratio, 5),
                "largest_component_ratio": round(largest, 5),
                "second_component_ratio": round(second, 5),
            })

    required_hits = max(2, int(math.ceil(analyzed * 0.30))) if analyzed else 2
    detected = len(hits) >= required_hits
    hit_ratio = len(hits) / max(1, analyzed)
    confidence = min(0.97, 0.45 + hit_ratio * 0.48) if detected else min(0.45, hit_ratio)
    return {
        "detected": bool(detected),
        "label": "blue_bipolar_forceps" if detected else "",
        "description": "可见蓝色绝缘双钳口，符合双极电凝钳形态",
        "frames_seen": len(hits),
        "frames_analyzed": analyzed,
        "required_frames": required_hits,
        "hit_ratio": round(hit_ratio, 3),
        "max_blue_ratio": round(max_blue_ratio, 5),
        "confidence": round(float(confidence), 3),
        "hits": hits[:8],
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


def _detect_hemlok_clip_action(frames_bgr: List[np.ndarray]) -> Dict[str, Any]:
    """Cheap Hem-o-lok / pale clip cue for cases where YOLO misses clipper.

    This is intentionally a visual cue, not a trained Hem-o-lok expert model or
    a standalone diagnosis. Surgical tissue, irrigation tips and glare can
    produce similar bright regions, so downstream summary code still combines
    this score with phase, tool context and the external VLM reviewer before
    surfacing it to users.
    """
    frame_hits: List[Dict[str, Any]] = []
    for idx, frame in enumerate(frames_bgr):
        if frame is None or frame.size == 0:
            continue
        h, w = frame.shape[:2]
        scale = 960.0 / max(w, 1)
        small = cv2.resize(frame, (960, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        sh, sw = small.shape[:2]

        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        # Hem-o-lok-style clips are often pale pink/white plastic. Keep
        # moderately saturated red-pink/orange bright regions and drop border
        # glare from the circular endoscopic mask.
        mask = (
            (((hue <= 42) | (hue >= 174)) & (sat >= 35) & (sat <= 125) & (val >= 175))
        ).astype(np.uint8) * 255
        mask[: int(sh * 0.04), :] = 0
        mask[int(sh * 0.75) :, :] = 0
        mask[:, : int(sw * 0.06)] = 0
        mask[:, int(sw * 0.92) :] = 0
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

        n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        candidates: List[Dict[str, Any]] = []
        for comp_id in range(1, n):
            x, y, bw, bh, area = stats[comp_id]
            if bw <= 0 or bh <= 0:
                continue
            fill = area / float(bw * bh)
            aspect = max(bw, bh) / max(1, min(bw, bh))
            cx, cy = centroids[comp_id]

            if not (45 <= area <= 1350):
                continue
            if not (10 <= bw <= 90 and 6 <= bh <= 70):
                continue
            if not (1.05 <= aspect <= 7.0):
                continue
            if not (0.20 <= fill <= 0.85):
                continue
            if not (100 <= cx <= 850 and 30 <= cy <= 330):
                continue

            comp_pixels = small[labels == comp_id]
            mean_bgr = comp_pixels.mean(axis=0)
            # Reject darker purple vessels and dim tissue edges.
            if mean_bgr[2] < 190 or mean_bgr[1] < 145 or mean_bgr[0] < 95:
                continue

            roi = (labels[y : y + bh, x : x + bw] == comp_id).astype(np.uint8) * 255
            _, hierarchy = cv2.findContours(roi, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
            holes = 0
            if hierarchy is not None:
                holes = sum(1 for item in hierarchy[0] if item[3] >= 0)

            candidates.append({
                "centroid": [round(float(cx / scale), 1), round(float(cy / scale), 1)],
                "bbox": [
                    int(x / scale), int(y / scale),
                    int((x + bw) / scale), int((y + bh) / scale),
                ],
                "area": int(area),
                "aspect": round(float(aspect), 2),
                "fill": round(float(fill), 2),
                "holes": int(holes),
            })

        best_cluster: List[Dict[str, Any]] = []
        for cand in candidates:
            cx, cy = cand["centroid"]
            group = [
                other for other in candidates
                if abs(other["centroid"][0] - cx) <= int(230 / scale)
                and abs(other["centroid"][1] - cy) <= int(170 / scale)
            ]
            if len(group) > len(best_cluster):
                best_cluster = group

        elongated = sum(1 for item in best_cluster if item.get("aspect", 0) >= 1.5)
        holes = sum(int(item.get("holes") or 0) for item in best_cluster)
        score = len(best_cluster) + 0.6 * elongated + 2.0 * min(holes, 3)
        if best_cluster:
            frame_hits.append({
                "frame_index": idx,
                "score": round(float(score), 2),
                "cluster_count": len(best_cluster),
                "elongated_count": elongated,
                "hole_count": holes,
                "candidates": best_cluster[:6],
            })

    strong_hits = [
        hit for hit in frame_hits
        if hit.get("cluster_count", 0) >= 8 and hit.get("score", 0) >= 14.0
    ]
    detected = len(strong_hits) >= 2 or (
        len(strong_hits) >= 1 and max((hit.get("score", 0) for hit in strong_hits), default=0) >= 22.0
    )
    max_score = max((hit.get("score", 0) for hit in frame_hits), default=0)
    mean_score = float(np.mean([hit.get("score", 0) for hit in frame_hits])) if frame_hits else 0.0
    confidence = min(0.95, max(0.0, (max_score - 10.0) / 24.0))
    return {
        "detected": bool(detected),
        "label": "hemlok_clip_candidate" if detected else "",
        "description": "可见疑似已释放夹子，提示夹闭处理",
        "frames_seen": len(strong_hits),
        "max_score": round(float(max_score), 2),
        "mean_score": round(float(mean_score), 2),
        "confidence": round(float(confidence), 2),
        "hits": strong_hits[:6],
    }


def _format_text(
    yolo: Dict[str, Any],
    clip_detector: Dict[str, Any],
    phase: Dict[str, Any],
    triplet: Dict[str, Any],
    short_action: Optional[Dict[str, Any]] = None,
    hemlok_clip: Optional[Dict[str, Any]] = None,
    blue_bipolar_forceps: Optional[Dict[str, Any]] = None,
) -> str:
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
    if clip_detector and clip_detector.get("detections_total", 0) > 0:
        lines.append(
            "[Clip Detector] 检出已部署夹子候选 "
            f"{clip_detector.get('frames_seen', 0)}/{clip_detector.get('frames_analyzed', 0)} 帧，"
            f"总数 {clip_detector.get('detections_total', 0)}，"
            f"最高置信度 {clip_detector.get('max_confidence', 0):.2f}"
        )
    if triplet and triplet.get("triplet"):
        target_hint = _triplet_target_hint(triplet)
        target_cn = "胆囊动脉" if target_hint["label"] == "cystic_artery" else "胆囊管"
        lines.append(f"[Triplet Target Hint] 夹闭剪切目标倾向: {target_cn} (conf {target_hint.get('confidence', 0):.2f})")
        op_phrases = _triplet_operation_phrases(triplet, phase_label=str(phase.get("label") or ""))
        yolo_labels = {
            str(tool.get("label") or "").strip().lower()
            for tool in yolo.get("tools", [])
            if isinstance(tool, dict)
        }
        if "scissors" not in yolo_labels:
            op_phrases = [phrase for phrase in op_phrases if not phrase.startswith("剪刀")]
        if op_phrases:
            lines.append(f"[Triplet Core Operations] {', '.join(op_phrases)}")
        else:
            lines.append("[Triplet Core Operations] 未形成高置信核心操作")
    if short_action and short_action.get("detected"):
        lines.append(f"[Short Action Expert] {short_action.get('description')}")
    if hemlok_clip and hemlok_clip.get("detected"):
        lines.append(
            f"[Local Visual Cue] {hemlok_clip.get('description')} "
            f"(conf {hemlok_clip.get('confidence', 0):.2f})"
        )
    if blue_bipolar_forceps and blue_bipolar_forceps.get("detected"):
        lines.append(
            f"[Blue Jaw Cue] {blue_bipolar_forceps.get('description')} "
            f"({blue_bipolar_forceps.get('frames_seen', 0)}/"
            f"{blue_bipolar_forceps.get('frames_analyzed', 0)} frames, "
            f"conf {blue_bipolar_forceps.get('confidence', 0):.2f})"
        )
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
        - "clip_detector": deployed surgical clip candidates
        - "phase":   aggregated Phase result (dominant label + conf)
        - "triplet": raw Triplet recognition (top-K I/V/T/IVT)
        - "available": list of experts that actually produced output
    """
    out: Dict[str, Any] = {
        "yolo": {},
        "clip_detector": {},
        "phase": {},
        "triplet": {},
        "short_action": {},
        "hemlok_clip": {},
        "blue_bipolar_forceps": {},
        "available": [],
    }
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

    # Dedicated deployed clip detector. This is intentionally separate from
    # the tool YOLO model: it detects clip bodies, not clipper instruments.
    try:
        from .clip_detector_service import get_clip_detector_service
        clip_detector = get_clip_detector_service()
        if clip_detector is not None and clip_detector.is_ready:
            per_frame_clip_dets = [clip_detector.detect(f) for f in frames_bgr]
            out["clip_detector"] = _aggregate_clip_detector(per_frame_clip_dets)
            out["clip_detector"]["per_frame"] = per_frame_clip_dets
            if out["clip_detector"].get("detections_total", 0) > 0:
                out["available"].append("clip_detector")
    except Exception as e:
        logger.warning(f"[ExpertFusion] Clip detector skipped: {e}")

    try:
        blue_bipolar_forceps = _detect_blue_bipolar_forceps(frames_bgr)
        out["blue_bipolar_forceps"] = blue_bipolar_forceps
        if blue_bipolar_forceps.get("detected"):
            out["available"].append("blue_bipolar_forceps")
    except Exception as e:
        logger.warning(f"[ExpertFusion] Blue bipolar cue skipped: {e}")

    try:
        short_action = _detect_short_action(frames_bgr)
        out["short_action"] = short_action
        if short_action.get("detected"):
            out["available"].append("short_action")
    except Exception as e:
        logger.warning(f"[ExpertFusion] Short action skipped: {e}")

    try:
        hemlok_clip = _detect_hemlok_clip_action(frames_bgr)
        out["hemlok_clip"] = hemlok_clip
        if hemlok_clip.get("detected"):
            out["available"].append("hemlok_clip")
    except Exception as e:
        logger.warning(f"[ExpertFusion] Hem-o-lok cue skipped: {e}")

    out["text"] = _format_text(
        out["yolo"], out["clip_detector"], out["phase"], out["triplet"], out["short_action"],
        out["hemlok_clip"], out["blue_bipolar_forceps"]
    )
    logger.info(f"[ExpertFusion] available={out['available']}")
    return out
