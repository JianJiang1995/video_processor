#!/usr/bin/env python3
"""Validate batch-analysis artifacts for contradictions and coverage gaps."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any


SCISSORS_RE = re.compile(r"剪刀|scissors", re.IGNORECASE)
OUT_OF_BODY_RE = re.compile(r"移出体外|体外场景|套管口|outside (?:the )?body", re.IGNORECASE)
RELEASED_CLIP_RE = re.compile(r"已释放夹子|夹子已(?:夹闭|闭合)|可见夹子", re.IGNORECASE)
ACTIVE_TARGET_CLIP_RE = re.compile(
    r"(?:钛夹钳|施夹器|施夹钳)?(?:正在)?(?:夹闭|闭合|施夹)(?:胆囊管|胆囊动脉)",
    re.IGNORECASE,
)
FOG_ACTIVE_RE = re.compile(r"视野(?:反复)?起雾|镜头起雾|fog(?:ging)? obscures|repeated lens fogging", re.IGNORECASE)
FOG_RESOLVED_RE = re.compile(r"雾已去除|fog cleared", re.IGNORECASE)
FORBIDDEN_SUMMARY_PATTERNS = {
    "ambiguous_target": re.compile(r"胆囊管(?:或者|或|/)胆囊动脉"),
    "ambiguous_hemostasis": re.compile(r"凝血或止血"),
    "merged_instrument": re.compile(r"电凝钩(?:剪刀|/剪刀)|剪刀(?:电凝钩|/电凝钩)"),
    "placeholder_wording": re.compile(r"尖端接触|管状结构"),
    "non_scissors_target_division": re.compile(
        r"(?:电凝钩|双极电凝).{0,12}(?:剪断|切断|离断)(?:胆囊管|胆囊动脉)"
    ),
    "non_scissors_cut_warning": re.compile(r"(?:电凝钩|双极电凝).{0,32}需核查后再剪断"),
    "unsupported_stapler_wording": re.compile(r"自动缝合器|吻合器|缝合操作|Autosuture", re.IGNORECASE),
    "vague_hook_gallbladder_action": re.compile(r"电凝钩(?:正在)?分离胆囊(?:[，,。；;]|$)"),
    "vague_hook_gallbladder_surrounding_action": re.compile(r"电凝钩(?:正在)?分离胆囊周围组织"),
}


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _summary_text(row: dict) -> str:
    return str(row.get("summary_text") or row.get("glm_summary") or row.get("summary") or "")


def _others(row: dict) -> dict:
    value = row.get("others") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    return value if isinstance(value, dict) else {}


def _visual_review(row: dict) -> tuple[dict, bool]:
    others = _others(row)
    experts = others.get("experts") or {}
    open_vlm = experts.get("open_vlm") or {}
    visual = others.get("visual_gpt") or open_vlm.get("visual") or {}
    return (visual if isinstance(visual, dict) else {}), bool(open_vlm.get("success"))


def _visual_rejects_scissors(row: dict) -> bool:
    visual, success = _visual_review(row)
    scissors = visual.get("scissors") or {}
    return bool(
        success
        and isinstance(scissors, dict)
        and ("visible" in scissors or "cutting" in scissors)
        and not scissors.get("visible")
        and not scissors.get("cutting")
    )


def _visual_accepts_scissors(row: dict) -> bool:
    visual, _ = _visual_review(row)
    scissors = visual.get("scissors") or {}
    return bool(scissors.get("visible") or scissors.get("cutting"))


def _visual_rejects_out_of_body(row: dict) -> bool:
    visual, success = _visual_review(row)
    visibility = visual.get("visibility") or {}
    if not success or not isinstance(visibility, dict):
        return False
    status = str(visibility.get("status") or "").lower()
    return not visibility.get("out_of_body") and status not in {"out_of_body", "outside_body"}


def _clip_is_visually_supported(row: dict) -> bool:
    visual, _ = _visual_review(row)
    secondary = visual.get("clip_secondary_review") or {}
    secondary_class = str(secondary.get("classification") or "").lower()
    return bool(
        (visual.get("generic_clip") or {}).get("visible")
        or (visual.get("generic_clip") or {}).get("placed")
        or (visual.get("hemolok") or {}).get("visible")
        or (visual.get("titanium_clip") or {}).get("visible")
        or secondary_class in {"clip", "deployed_clip"}
    )


def _visual_rejects_active_clip_application(row: dict) -> bool:
    visual, success = _visual_review(row)
    secondary = visual.get("clip_secondary_review") or {}
    applier = visual.get("clip_applier") or {}
    prediction = str(secondary.get("classification") or "").lower()
    confidence = float(secondary.get("confidence") or 0)
    return bool(
        success
        and secondary.get("success")
        and confidence >= 0.80
        and (
            prediction in {"no_clip", "glare_or_instrument", "instrument", "glare"}
            or (
                prediction == "clip_applier"
                and not secondary.get("clamped_on_tissue")
                and applier.get("active") is False
            )
        )
    )


def _duration(path: Path) -> float:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(probe.stdout.strip())
    except ValueError:
        return 0.0


def validate_label(out_dir: Path, result: dict) -> dict:
    label = str(result.get("label") or "unknown")
    errors: list[str] = []
    warnings: list[str] = []
    if result.get("error"):
        return {"label": label, "status": "fail", "errors": [str(result["error"])], "warnings": []}

    summaries_path = out_dir / f"{label}.summaries.json"
    summaries = _load(summaries_path, [])
    if not isinstance(summaries, list) or not summaries:
        errors.append("missing or empty summaries")
        summaries = []

    summaries.sort(key=lambda row: int(row.get("window_id") or 0))
    ids = [int(row.get("window_id") or 0) for row in summaries]
    if ids and ids != list(range(ids[0], ids[0] + len(ids))):
        errors.append(f"non-contiguous window ids: {ids}")

    segment_duration = float(result.get("segment_duration") or 0)
    expected_coverage = math.floor((segment_duration + 0.25) / 5.0) * 5.0
    covered = max((float(row.get("end_time") or 0) for row in summaries), default=0.0)
    if expected_coverage and covered < expected_coverage - 0.25:
        errors.append(f"incomplete coverage: {covered:.1f}/{expected_coverage:.1f}s")

    invalid_sequence_state_reported = False
    for index, row in enumerate(summaries):
        wid = int(row.get("window_id") or 0)
        text = _summary_text(row)
        start = float(row.get("start_time") or 0)
        end = float(row.get("end_time") or 0)
        if end <= start:
            errors.append(f"W{wid}: invalid time range {start:.1f}-{end:.1f}")
        if segment_duration and end > segment_duration + 0.25:
            errors.append(f"W{wid}: window end exceeds source duration: {end:.1f}/{segment_duration:.1f}s")
        if index and abs(start - float(summaries[index - 1].get("end_time") or 0)) > 0.35:
            errors.append(f"W{wid}: time gap/overlap before {start:.1f}s")

        for name, pattern in FORBIDDEN_SUMMARY_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"W{wid}: {name}: {text}")

        if _visual_rejects_scissors(row) and SCISSORS_RE.search(text):
            errors.append(f"W{wid}: scissors prose contradicts structured VLM rejection")
        if _visual_rejects_out_of_body(row) and OUT_OF_BODY_RE.search(text):
            errors.append(f"W{wid}: out-of-body prose contradicts structured VLM rejection")
        if RELEASED_CLIP_RE.search(text) and not _clip_is_visually_supported(row):
            errors.append(f"W{wid}: released-clip prose lacks visual confirmation")
        if ACTIVE_TARGET_CLIP_RE.search(text) and _visual_rejects_active_clip_application(row):
            errors.append(f"W{wid}: active target-clip prose contradicts focused clip review")
        if ACTIVE_TARGET_CLIP_RE.search(text) and "电凝钩分离局部纤维组织" in text:
            errors.append(f"W{wid}: target clipping is diluted by a generic hook fragment")
        if ACTIVE_TARGET_CLIP_RE.search(text) and "可见已释放夹子" in text:
            errors.append(f"W{wid}: target clipping is duplicated by generic released-clip prose")
        if (
            re.search(r"剪刀在(?:胆囊管|胆囊动脉)邻近区域操作", text)
            and "剪刀在操作区域内活动" in text
        ):
            errors.append(f"W{wid}: specific scissors warning is duplicated by generic activity")
        if (
            re.search(r"电凝钩分离(?:肝胆三角组织|胆囊床组织|胆囊与胆囊床粘连组织)", text)
            and "电凝钩分离局部纤维组织" in text
        ):
            errors.append(f"W{wid}: specific and generic hook actions are duplicated")
        if (
            "电凝钩分离肝胆三角组织" in text
            and "电凝钩分离肝胆三角区域组织" in text
        ):
            errors.append(f"W{wid}: synonymous Calot hook actions are duplicated")
        if text.count("CVS尚未达成") > 1 or text.count("CVS安全视野") > 1:
            errors.append(f"W{wid}: duplicated CVS wording")

        calls = _others(row).get("model_calls") or {}
        open_vlm = ((_others(row).get("experts") or {}).get("open_vlm") or {})
        if open_vlm.get("pending"):
            errors.append(f"W{wid}: local VLM refinement remained pending")
        elif not open_vlm.get("success"):
            errors.append(f"W{wid}: local VLM refinement missing or failed")
        experts = _others(row).get("experts") or {}
        phase_label = str((experts.get("phase") or {}).get("label") or "").lower()
        persisted_phase = str(row.get("surgical_phase") or "")
        if persisted_phase in {"Preparation", "CalotTriangleDissection"} and re.search(
            r"将胆囊装入标本袋|胆囊装袋取出后|标本袋牵拉取出",
            text,
        ):
            errors.append(f"W{wid}: late-workflow prose survived in persisted phase {persisted_phase}")
        sequence_state = _others(row).get("sequence_rules") or {}
        reached = set(sequence_state.get("reached_phases") or [])
        terminal_reached = reached.intersection({
            "CleaningCoagulation",
            "GallbladderPackaging",
            "GallbladderRetraction",
        })
        if (
            terminal_reached
            and not {"ClippingCutting", "GallbladderDissection"}.issubset(reached)
            and not invalid_sequence_state_reported
        ):
            errors.append(
                f"W{wid}: terminal workflow state was accepted before clipping and gallbladder dissection"
            )
            invalid_sequence_state_reported = True
        visual, _ = _visual_review(row)
        if (
            phase_label == "gallbladder_dissection"
            and "钛夹钳在操作区域内调整" in text
        ):
            errors.append(f"W{wid}: idle clip-applier wording survived after clipping phase")
        target_specific_clip = re.search(
            r"(?:钛夹钳|施夹器|夹子)(?:正在)?(?:夹闭|闭合)(胆囊管|胆囊动脉)",
            text,
        )
        if target_specific_clip:
            visual_target = visual.get("target_structure") or {}
            visual_target_confidence = float(visual_target.get("confidence") or 0.0)
            triplet_target_confidence = max(
                [
                    float(item.get("confidence") or 0.0)
                    for item in ((experts.get("triplet") or {}).get("target") or [])
                    if str(item.get("label") or "").lower()
                    in {"cystic_duct", "cystic_artery", "blood_vessel", "cystic_pedicle"}
                ]
                or [0.0]
            )
            if visual_target_confidence < 0.55 and triplet_target_confidence < 0.45:
                errors.append(
                    f"W{wid}: target-specific clipping lacks anatomy confidence"
                )
        scissors = visual.get("scissors") or {}
        visibility_review = visual.get("visibility_secondary_review") or {}
        if visibility_review.get("success"):
            visibility_class = str(visibility_review.get("classification") or "").lower()
            if visibility_class in {"specimen_bag_inside", "intra_abdominal"} and OUT_OF_BODY_RE.search(text):
                errors.append(f"W{wid}: out-of-body prose contradicts focused visibility review")
            if visibility_class in {"specimen_bag_inside", "intra_abdominal"} and FOG_ACTIVE_RE.search(text):
                errors.append(f"W{wid}: fog prose contradicts focused visibility review")
            if visibility_class in {"external_body", "trocar_transition"} and not OUT_OF_BODY_RE.search(text):
                errors.append(f"W{wid}: focused visibility review confirmed scope exit but prose omitted it")
        focused_review = visual.get("scissors_secondary_review") or {}
        morphology_review = visual.get("clip_secondary_review") or {}
        scissors_morphology_checked = bool(
            morphology_review.get("success")
            and str(morphology_review.get("classification") or "").lower() == "scissors"
            and float(morphology_review.get("confidence") or 0.0) >= 0.80
        )
        yolo_tools = {
            str(tool.get("label") or "").lower()
            for tool in ((experts.get("yolo") or {}).get("tools") or [])
            if isinstance(tool, dict)
        }
        scissors_claimed = bool(re.search(r"剪刀|scissors", text, re.IGNORECASE))
        if (
            ("scissors" in yolo_tools or scissors_claimed)
            and not focused_review.get("success")
            and not scissors_morphology_checked
        ):
            errors.append(f"W{wid}: scissors evidence was not checked by the focused review")
        if scissors_claimed and not (scissors.get("visible") or scissors.get("cutting")):
            errors.append(f"W{wid}: scissors prose contradicts the focused visual state")
        if phase_label == "gallbladder_dissection" and re.search(
            r"(?:分离|解剖)(?:胆囊管|胆囊动脉)",
            text,
        ):
            errors.append(f"W{wid}: post-clipping anatomy regressed to duct/artery dissection")
        if phase_label == "gallbladder_dissection" and ACTIVE_TARGET_CLIP_RE.search(text):
            errors.append(f"W{wid}: target-specific clipping was reasserted during gallbladder dissection")
        if phase_label == "gallbladder_dissection" and "可见已释放夹子" in text:
            errors.append(f"W{wid}: stale released-clip observation remained during gallbladder dissection")
        if int((calls.get("stage2_summary_vlm") or {}).get("count") or 0) != 0:
            errors.append(f"W{wid}: live Stage 2 VLM call is enabled")
        provider = str((calls.get("open_visual_gpt") or {}).get("provider") or "")
        if provider and provider not in {"glm", "local"}:
            errors.append(f"W{wid}: unexpected visual provider {provider}")

    events_payload = _load(out_dir / f"{label}.events.json", {})
    events = events_payload.get("events", []) if isinstance(events_payload, dict) else []
    if len(events) > 10:
        errors.append(f"too many key events: {len(events)}")
    seen_events: set[tuple[str, str, float, float]] = set()
    by_id = {int(row.get("window_id") or 0): row for row in summaries}
    for event in events:
        key = (
            str(event.get("type") or ""),
            str(event.get("title") or ""),
            float(event.get("start_time") or 0),
            float(event.get("end_time") or 0),
        )
        if key in seen_events:
            errors.append(f"duplicate event: {key[1]}")
        seen_events.add(key)
        event_text = f"{event.get('title', '')} {event.get('summary', '')}"
        if str(event.get("type") or "") == "risk" and SCISSORS_RE.search(event_text):
            rows = [by_id[wid] for wid in event.get("window_ids", []) if wid in by_id]
            if not rows or not any(_visual_accepts_scissors(row) for row in rows):
                errors.append(f"unsupported scissors risk event: {event.get('title')}")

    expected_scissors_risk_ids: set[int] = set()
    cvs_achieved = False
    for row in summaries:
        text = _summary_text(row)
        current_achieved = bool(re.search(r"CVS.{0,8}(?:已达成|达成证据)|critical view.{0,8}achieved", text, re.IGNORECASE))
        phase_label = str((((_others(row).get("experts") or {}).get("phase") or {}).get("label") or "")).lower()
        if phase_label in {"calot_triangle_dissection", "clipping_cutting"} and _visual_accepts_scissors(row) and not (
            cvs_achieved or current_achieved
        ):
            expected_scissors_risk_ids.add(int(row.get("window_id") or 0))
        cvs_achieved = cvs_achieved or current_achieved
    actual_scissors_risk_ids = {
        int(window_id)
        for event in events
        if str(event.get("type") or "") == "risk"
        and SCISSORS_RE.search(f"{event.get('title', '')} {event.get('summary', '')}")
        for window_id in (event.get("window_ids") or [])
    }
    missing_risk_ids = sorted(expected_scissors_risk_ids - actual_scissors_risk_ids)
    if missing_risk_ids:
        errors.append(f"scissors risk event omitted reviewed windows: {missing_risk_ids}")

    fog_active_events = [
        event for event in events
        if FOG_ACTIVE_RE.search(f"{event.get('title', '')} {event.get('summary', '')}")
    ]
    fog_resolved_events = [
        event for event in events
        if FOG_RESOLVED_RE.search(f"{event.get('title', '')} {event.get('summary', '')}")
    ]
    for resolved in fog_resolved_events:
        if not any(
            float(active.get("start_time") or 0) < float(resolved.get("start_time") or 0)
            for active in fog_active_events
        ):
            errors.append("fog-resolution event has no preceding fog event")

    event_texts = [f"{event.get('title', '')} {event.get('summary', '')}" for event in events]
    has_target_clip = any(re.search(r"胆囊管夹闭|胆囊动脉夹闭|cystic (?:duct|artery) clipping", text, re.IGNORECASE) for text in event_texts)
    has_generic_clip = any(re.search(r"夹子放置|clip placement", text, re.IGNORECASE) for text in event_texts)
    if has_target_clip and has_generic_clip:
        errors.append("target-specific clip event duplicated by generic clip event")

    for category in (
        re.compile(r"胆囊装袋|装入标本袋|specimen bagging", re.IGNORECASE),
        re.compile(r"标本袋牵拉取出|retrieval bag removal|bag retraction", re.IGNORECASE),
    ):
        matching = [
            event for event, text in zip(events, event_texts)
            if category.search(text)
        ]
        if any(event.get("type") == "phase" for event in matching) and any(
            event.get("type") == "action" for event in matching
        ):
            errors.append("same packaging/retrieval milestone emitted as both phase and action")

    report_path = Path(str(result.get("report") or ""))
    if not report_path.is_file():
        warnings.append("clinical report missing")
    else:
        report_text = report_path.read_text(encoding="utf-8")
        if re.search(r"Traceback|\[分析出错|provider=|Qwen|Gemini", report_text, re.IGNORECASE):
            errors.append("clinical report contains runtime/model diagnostics")
        if any(error.startswith("unsupported scissors risk") for error in errors) and SCISSORS_RE.search(report_text):
            errors.append("clinical report repeats unsupported scissors risk")
        if FOG_RESOLVED_RE.search(report_text) and not FOG_ACTIVE_RE.search(report_text):
            errors.append("clinical report says fog cleared without recording fog onset")

    review_path = Path(str(result.get("review") or out_dir / f"{label}_review.mp4"))
    if result.get("review") is not None:
        if not review_path.is_file():
            errors.append("review video missing")
        else:
            review_duration = _duration(review_path)
            if segment_duration and abs(review_duration - segment_duration) > 0.6:
                errors.append(f"review duration mismatch: {review_duration:.1f}/{segment_duration:.1f}s")

    return {
        "label": label,
        "status": "pass" if not errors else "fail",
        "windows": len(summaries),
        "coverage": covered,
        "events": len(events),
        "errors": errors,
        "warnings": warnings,
    }


def validate_directory(out_dir: Path) -> list[dict]:
    results = _load(out_dir / "batch_results.json", [])
    if not isinstance(results, list):
        results = []
    if not results:
        results = [
            {"label": path.name.removesuffix(".summaries.json")}
            for path in sorted(out_dir.glob("*.summaries.json"))
        ]
    return [validate_label(out_dir, result) for result in results]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = validate_directory(args.out_dir.resolve())
    output = args.output or args.out_dir.resolve() / "quality_report.json"
    output.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 1 if any(report["status"] == "fail" for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
