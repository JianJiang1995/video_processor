#!/usr/bin/env python3
"""Apply final evidence gates to saved batch summaries and rebuild SRT files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from backend.routers.analysis import (
    _apply_surgical_sequence_rules,
    _build_surgical_sequence_state,
    _canonical_phase,
    _expand_vague_operation_language,
    _replace_current_phase_text,
    _resolve_bipolar_hook_conflict,
    _sequence_state_meta,
    _strip_focused_scissors_instrument_conflicts,
    _strip_focused_visibility_conflicts,
    _strip_nonprogress_idle_applier_claim,
    _strip_unverified_target_specific_clip_claims,
    _strip_visual_rejected_scissors_claims,
)
from batch_record_analysis import event_subtitle_entries, wrap_subtitle, write_srt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--labels", nargs="*", default=[])
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    results = json.loads((out_dir / args.results).read_text(encoding="utf-8"))
    corrections = []
    selected_labels = {str(label) for label in args.labels if str(label).strip()}
    for result in results:
        label = result["label"]
        if selected_labels and label not in selected_labels:
            continue
        path = out_dir / f"{label}.summaries.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        weak_target_windows = set()
        for row_index, row in enumerate(rows):
            others = row.get("others") or {}
            experts = others.get("experts") or {}
            visual = others.get("visual_gpt") or (
                (experts.get("open_vlm") or {}).get("visual") or {}
            )
            original_text = str(row.get("summary_text") or row.get("summary") or "")
            old = original_text
            original_phase = str(row.get("surgical_phase") or row.get("phase") or "")
            expert_phase = _canonical_phase(
                str((experts.get("phase") or {}).get("label") or original_phase)
            )
            prior_state = _build_surgical_sequence_state(
                rows,
                before_window_id=int(row.get("window_id") or 0),
            )
            strict_clip_review = visual.get("clip_action_secondary_review") or {}
            focused_clip_review = visual.get("clip_secondary_review") or {}
            focused_clip_rejected = bool(
                focused_clip_review.get("success")
                and float(focused_clip_review.get("confidence") or 0.0) >= 0.80
                and str(focused_clip_review.get("classification") or "").lower()
                in {"no_clip", "instrument", "glare", "glare_or_instrument"}
            )
            strict_clip_confirmed = bool(
                strict_clip_review.get("success")
                and float(strict_clip_review.get("confidence") or 0.0) >= 0.75
                and not focused_clip_rejected
                and (
                    strict_clip_review.get("active_clip_action")
                    or strict_clip_review.get("deployed_clip_visible")
                )
            )
            clipper_frames = max(
                [
                    int(tool.get("frames_seen") or 0)
                    for tool in ((experts.get("yolo") or {}).get("tools") or [])
                    if isinstance(tool, dict)
                    and str(tool.get("label") or "").lower() == "clipper"
                ]
                or [0]
            )
            transition_supported = bool(strict_clip_confirmed or clipper_frames >= 2)
            if (
                int(prior_state.get("max_phase_order", -1)) < 2
                and not transition_supported
            ):
                raw_clip_review = dict(visual.get("clip_secondary_review") or {})
                raw_class = str(raw_clip_review.get("classification") or "").lower()
                if raw_clip_review.get("success") and raw_class in {"clip", "clip_applier"}:
                    visual["clip_secondary_review"] = {
                        "success": True,
                        "classification": "instrument",
                        "instrument": "other",
                        "confidence": max(
                            0.85,
                            float(raw_clip_review.get("confidence") or 0.0),
                        ),
                        "applier_active": False,
                        "clamped_on_tissue": False,
                        "reason": "阶段转换缺少严格施夹证据，且逐帧检测未检出clipper。",
                        "source": "phase-transition-evidence-gate",
                        "superseded_clip_review": raw_clip_review,
                    }
                    applier = dict(visual.get("clip_applier") or {})
                    applier.update({"visible": False, "active": False})
                    visual["clip_applier"] = applier
                    generic = dict(visual.get("generic_clip") or {})
                    generic.update({"visible": False, "placed": False})
                    visual["generic_clip"] = generic
                    open_visual = (experts.get("open_vlm") or {}).get("visual")
                    if isinstance(open_visual, dict) and open_visual is not visual:
                        open_visual.update({
                            "clip_secondary_review": dict(visual["clip_secondary_review"]),
                            "clip_applier": dict(applier),
                            "generic_clip": dict(generic),
                        })
            phase = "ClippingCutting" if strict_clip_confirmed else expert_phase
            if (
                phase == "ClippingCutting"
                and int(prior_state.get("max_phase_order", -1))
                < 2
                and not strict_clip_confirmed
                and clipper_frames < 2
            ):
                phase = "CalotTriangleDissection"
            recent_bag_confidence = 0.0
            recent_bag_scene_confirmed = False
            for evidence_row in rows[max(0, row_index - 2):row_index + 1]:
                evidence_others = evidence_row.get("others") or {}
                evidence_experts = evidence_others.get("experts") or {}
                evidence_visual = evidence_others.get("visual_gpt") or (
                    (evidence_experts.get("open_vlm") or {}).get("visual") or {}
                )
                evidence_visibility_review = (
                    evidence_visual.get("visibility_secondary_review") or {}
                )
                evidence_scene = str(
                    evidence_visibility_review.get("classification") or ""
                ).lower()
                evidence_scene_confident = bool(
                    evidence_visibility_review.get("success")
                    and float(evidence_visibility_review.get("confidence") or 0.0)
                    >= 0.75
                )
                if evidence_scene_confident and evidence_scene == "specimen_bag_inside":
                    recent_bag_scene_confirmed = True
                if evidence_scene_confident and evidence_scene != "specimen_bag_inside":
                    continue
                recent_bag_confidence = max(
                    recent_bag_confidence,
                    max(
                        [
                            float(item.get("confidence") or 0.0)
                            for item in ((evidence_experts.get("triplet") or {}).get("target") or [])
                            if isinstance(item, dict)
                            and str(item.get("label") or "").lower() == "specimen_bag"
                        ]
                        or [0.0]
                    ),
                )
            visibility_review = visual.get("visibility_secondary_review") or {}
            bag_scene_confirmed = bool(
                recent_bag_scene_confirmed
            )
            bag_scene_rejected = bool(
                visibility_review.get("success")
                and float(visibility_review.get("confidence") or 0.0) >= 0.75
                and str(visibility_review.get("classification") or "").lower()
                not in {"", "specimen_bag_inside"}
            )
            if (
                phase == "GallbladderPackaging"
                and not prior_state.get("packaging_seen")
                and (
                    bag_scene_rejected
                    or (
                        recent_bag_confidence < 0.65
                        and not bag_scene_confirmed
                    )
                )
            ):
                phase = (
                    "GallbladderDissection"
                    if "GallbladderDissection" in (prior_state.get("reached_phases") or set())
                    else prior_state.get("last_phase") or "GallbladderDissection"
                )
            elif (
                prior_state.get("packaging_seen")
                and not prior_state.get("post_retrieval_review")
                and phase in {
                    "CalotTriangleDissection",
                    "ClippingCutting",
                    "GallbladderDissection",
                }
            ):
                phase = "GallbladderPackaging"
            if phase == "GallbladderDissection" and re.search(
                r"胆囊取出与装袋|装入标本袋|胆囊装袋|准备取出",
                old,
            ):
                stage1_candidate = str(
                    row.get("stage1_summary") or others.get("stage1_summary") or ""
                )
                if stage1_candidate and not re.search(
                    r"胆囊取出与装袋|装入标本袋|胆囊装袋|准备取出",
                    stage1_candidate,
                ):
                    old = stage1_candidate
                else:
                    old = "当前处于胆囊分离，正在观察胆囊床剥离面并处理残余粘连组织。"
            target_guarded = _strip_unverified_target_specific_clip_claims(
                old, visual, experts
            )
            if phase and phase != "Unknown" and "当前处于" in target_guarded:
                target_guarded = _replace_current_phase_text(target_guarded, phase)
                if phase == "ClippingCutting":
                    target_guarded = target_guarded.replace(
                        "CVS安全视野确认中",
                        "CVS处于夹闭前后安全核查中",
                    )
                elif phase == "CalotTriangleDissection":
                    target_guarded = target_guarded.replace(
                        "CVS处于夹闭前后安全核查中",
                        "CVS安全视野确认中",
                    )
            new = _strip_visual_rejected_scissors_claims(
                target_guarded,
                visual,
                experts,
                phase,
            )
            new = _strip_focused_scissors_instrument_conflicts(new, visual)
            new = _strip_nonprogress_idle_applier_claim(
                new,
                phase,
            )
            new = _strip_focused_visibility_conflicts(new, visual)
            new = _resolve_bipolar_hook_conflict(new, experts)
            new = _expand_vague_operation_language(
                new,
                phase,
            )
            new, corrected_phase, applied_rules = _apply_surgical_sequence_rules(
                new,
                phase,
                prior_state,
                visual=visual,
            )
            new = _strip_visual_rejected_scissors_claims(
                new,
                visual,
                experts,
                corrected_phase,
            )
            new = _strip_unverified_target_specific_clip_claims(new, visual, experts)
            new = _strip_focused_visibility_conflicts(new, visual)
            if corrected_phase == "CalotTriangleDissection":
                new = re.sub(
                    r"(双极电凝钳|电凝钩)(?:正在)?分离(?:胆囊管|胆囊动脉)",
                    r"\1分离肝胆三角纤维组织",
                    new,
                )
            if corrected_phase == "GallbladderDissection" and re.search(
                r"胆囊取出与装袋|装入标本袋|胆囊装袋|准备取出|装袋取出后",
                new,
            ):
                fog_suffix = (
                    "镜头起雾，手术视野受遮挡。"
                    if re.search(r"镜头起雾|视野受遮挡", new)
                    else ""
                )
                new = (
                    "当前处于胆囊分离，正在观察胆囊床剥离面并处理残余粘连组织。"
                    + fog_suffix
                )
            new = re.sub(
                r"[，,；;。]?\s*(?:当前处于)?肝胆三角解剖阶段夹子放置、剪切或",
                "",
                new,
            )
            new = re.sub(r"[，,；;。]?\s*[^。；;]{0,30}夹闭或剪切动作无雾", "", new)
            new = re.sub(r"[，,；;。]?\s*(双极电凝钳|电凝钩|抓钳)\s*[。；;]", "。", new)
            new = re.sub(r"。{2,}", "。", new)
            if corrected_phase and corrected_phase != original_phase:
                row["surgical_phase"] = corrected_phase
                corrections.append({
                    "label": label,
                    "window_id": row.get("window_id"),
                    "field": "surgical_phase",
                    "old": original_phase,
                    "new": corrected_phase,
                })
            others["sequence_rules"] = _sequence_state_meta(prior_state, applied_rules)
            row["others"] = others

            stage1_old = str(row.get("stage1_summary") or others.get("stage1_summary") or "")
            stage1_new = _resolve_bipolar_hook_conflict(stage1_old, experts)
            stage1_new = _expand_vague_operation_language(
                stage1_new,
                str(row.get("surgical_phase") or row.get("phase") or ""),
            )
            if (
                corrected_phase
                and corrected_phase != "Unknown"
                and "当前处于" in stage1_new
            ):
                stage1_new = _replace_current_phase_text(
                    stage1_new,
                    corrected_phase,
                )
            stage1_new = _strip_unverified_target_specific_clip_claims(
                stage1_new,
                visual,
                experts,
            )
            stage1_new = _strip_visual_rejected_scissors_claims(
                stage1_new,
                visual,
                experts,
                corrected_phase,
            )
            stage1_new, _, _ = _apply_surgical_sequence_rules(
                stage1_new,
                corrected_phase,
                prior_state,
                visual=visual,
            )
            stage1_new = _strip_unverified_target_specific_clip_claims(
                stage1_new,
                visual,
                experts,
            )
            stage1_new = _strip_focused_visibility_conflicts(stage1_new, visual)
            if (
                corrected_phase
                and corrected_phase != "Unknown"
                and "当前处于" not in stage1_new
                and not re.search(r"镜头移出体外|画面切换至套管口|腹壁外场景", stage1_new)
            ):
                stage1_new = _replace_current_phase_text(
                    stage1_new,
                    corrected_phase,
                )
            if stage1_new != stage1_old:
                row["stage1_summary"] = stage1_new
                others["stage1_summary"] = stage1_new
                row["others"] = others
                corrections.append({
                    "label": label,
                    "window_id": row.get("window_id"),
                    "field": "stage1_summary",
                    "old": stage1_old,
                    "new": stage1_new,
                })
            if new == original_text:
                continue
            corrections.append({
                "label": label,
                "window_id": row.get("window_id"),
                "old": original_text,
                "new": new,
            })
            if target_guarded != original_text:
                weak_target_windows.add(int(row.get("window_id") or 0))
            if "summary_text" in row:
                row["summary_text"] = new
            else:
                row["summary"] = new

        events_path = out_dir / f"{label}.events.json"
        event_payload = json.loads(events_path.read_text(encoding="utf-8"))
        events = (
            event_payload.get("events", [])
            if isinstance(event_payload, dict)
            else event_payload
        )
        for event in events:
            event_windows = {
                int(window_id) for window_id in (event.get("window_ids") or [])
            }
            text = f"{event.get('title') or ''} {event.get('summary') or ''}"
            if (
                event_windows
                and event_windows.issubset(weak_target_windows)
                and any(token in text for token in ("胆囊管夹闭", "胆囊动脉夹闭"))
            ):
                old_event = {
                    "title": event.get("title"),
                    "summary": event.get("summary"),
                }
                event["title"] = "夹子放置"
                event["summary"] = "夹子已夹闭目标组织，具体目标需回看原片确认。"
                event["source"] = "evidence-guardrail"
                corrections.append({
                    "label": label,
                    "event_id": event.get("id"),
                    "old": old_event,
                    "new": {
                        "title": event["title"],
                        "summary": event["summary"],
                    },
                })

        generic_clip_events = [
            event
            for event in events
            if event.get("type") == "action" and event.get("title") == "夹子放置"
        ]
        if len(generic_clip_events) > 1:
            merged = generic_clip_events[0]
            merged["window_ids"] = sorted({
                int(window_id)
                for event in generic_clip_events
                for window_id in (event.get("window_ids") or [])
            })
            merged["representative_window_id"] = max(
                int(event.get("representative_window_id") or 0)
                for event in generic_clip_events
            )
            merged["start_time"] = min(
                float(event.get("start_time") or 0) for event in generic_clip_events
            )
            merged["end_time"] = max(
                float(event.get("end_time") or 0) for event in generic_clip_events
            )
            merged["source"] = "evidence-guardrail"
            duplicate_ids = {id(event) for event in generic_clip_events[1:]}
            events[:] = [event for event in events if id(event) not in duplicate_ids]
            corrections.append({
                "label": label,
                "event_merge": [event.get("id") for event in generic_clip_events],
                "new": {
                    "id": merged.get("id"),
                    "title": merged.get("title"),
                    "window_ids": merged.get("window_ids"),
                },
            })

        if args.dry_run:
            continue
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        events_path.write_text(
            json.dumps(event_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        write_srt(
            [
                (
                    float(row.get("start_time") or 0),
                    float(row.get("end_time") or 0),
                    wrap_subtitle(
                        f"W{row.get('window_id')} "
                        f"{row.get('summary_text') or row.get('summary') or ''}"
                    ),
                )
                for row in rows
            ],
            out_dir / f"{label}.windows.srt",
        )
        write_srt(
            event_subtitle_entries(events, rows),
            out_dir / f"{label}.events.srt",
        )

    print(json.dumps(corrections, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
