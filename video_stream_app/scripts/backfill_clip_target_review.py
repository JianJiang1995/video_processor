#!/usr/bin/env python3
"""Backfill anatomy evidence for target-specific clipping summaries."""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.routers.analysis import (
    _sanitize_target_language,
    _strip_visual_rejected_clip_claims,
)


TARGET_CLIP_RE = re.compile(
    r"(?:钛夹钳|施夹器|施夹钳|夹子)(?:正在)?(?:夹闭|闭合)(胆囊管|胆囊动脉)|"
    r"(胆囊管|胆囊动脉)残端已由夹子(?:闭合|夹闭)"
)

PROMPT = (
    "这些是同一个腹腔镜胆囊切除术5秒窗口的5张连续完整画面。"
    "不要假设画面中存在施夹；必须先否定或确认真实施夹动作，再判断目标。"
    "不要根据手术阶段、模型标签或常见顺序猜测，只使用图像关系。\n"
    "active_clip_action=true 仅限：可见施夹器较宽、近乎平行的夹臂包绕管状组织，"
    "并在连续帧中闭合或释放夹子。只有器械杆进入视野、夹臂悬空或调整位置不算。\n"
    "deployed_clip_visible=true 仅限：可见独立于器械的短小夹体横跨并固定在组织上。"
    "高光、白色电凝钩尖端和器械边缘不算夹子。\n"
    "常见反例：白色陶瓷头加细弯钩是电凝钩；蓝色或金属双爪反复夹持组织常为双极电凝钳；"
    "夹爪中间有明显长方形开窗、椭圆开窗、锯齿或钝头的是抓钳，绝不是施夹器；"
    "V形锐利双刃是剪刀。画面同时出现电凝钩和开窗抓钳时，也不能把其中任何一个当施夹器。\n"
    "cystic_duct（胆囊管）：通常较粗、苍白或淡黄、管腔样，连续连接胆囊颈/胆囊漏斗。\n"
    "cystic_artery（胆囊动脉）：通常更细、红色或粉红血管样，作为较小血管分支进入胆囊。\n"
    "只有 active_clip_action 或 deployed_clip_visible 为 true 时才能填写解剖目标；"
    "否则 target 必须是 unknown。如果夹臂、目标或连接关系不清楚，也必须输出unknown。"
    "只输出JSON："
    '{"instrument":"clip_applier|electrocautery_hook|bipolar_forceps|grasper|scissors|other",'
    '"active_clip_action":false,"deployed_clip_visible":false,'
    '"target":"cystic_duct|cystic_artery|unknown",'
    '"decision_confidence":0.0,"target_confidence":0.0,"visible_evidence":"具体可见证据"}'
    "decision_confidence 是你对 active_clip_action 真或假的整体判定把握；"
    "清楚看到电凝钩、双极钳或开窗抓钳并确认不是施夹时也应给0.90以上，不能因为结果为false就写0。"
)


def replace_clipping_target(text: str, target: str) -> str:
    target_cn = "胆囊管" if target == "cystic_duct" else "胆囊动脉"
    out = re.sub(
        r"((?:钛夹钳|施夹器|施夹钳|夹子)(?:正在)?(?:夹闭|闭合))(?:胆囊管|胆囊动脉)",
        rf"\1{target_cn}",
        text,
    )
    out = re.sub(
        r"(?:胆囊管|胆囊动脉)(残端已由夹子(?:闭合|夹闭))",
        rf"{target_cn}\1",
        out,
    )
    return _sanitize_target_language(out, target)


def parse_object(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if match:
            try:
                value = json.loads(match.group(0))
            except json.JSONDecodeError:
                value = {}
        else:
            value = {}
        # vLLM can occasionally repeat the last JSON key until max_tokens and
        # omit the closing brace. Recover the first scalar value for the fixed
        # schema instead of discarding an otherwise complete classification.
        if not value:
            value = {}
            for key in ("instrument", "target", "visible_evidence"):
                found = re.search(rf'"{key}"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', cleaned)
                if found:
                    try:
                        value[key] = json.loads(f'"{found.group(1)}"')
                    except json.JSONDecodeError:
                        value[key] = found.group(1)
            for key in ("active_clip_action", "deployed_clip_visible"):
                found = re.search(rf'"{key}"\s*:\s*(true|false)', cleaned, flags=re.I)
                if found:
                    value[key] = found.group(1).lower() == "true"
            for key in ("decision_confidence", "confidence", "target_confidence"):
                found = re.search(rf'"{key}"\s*:\s*([0-9]+(?:\.[0-9]+)?)', cleaned)
                if found:
                    value[key] = float(found.group(1))
    return value if isinstance(value, dict) else {}


def image_urls(cap: cv2.VideoCapture, start: float, end: float) -> tuple[list[str], list[float]]:
    span = max(0.1, end - start)
    timestamps = [start + span * fraction for fraction in (0.10, 0.30, 0.50, 0.70, 0.90)]
    urls = []
    for timestamp in timestamps:
        cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError(f"cannot read frame at {timestamp:.3f}s")
        height, width = frame.shape[:2]
        scale = min(1.0, 768.0 / max(height, width))
        if scale < 1.0:
            frame = cv2.resize(
                frame,
                (round(width * scale), round(height * scale)),
                interpolation=cv2.INTER_AREA,
            )
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise RuntimeError(f"cannot encode frame at {timestamp:.3f}s")
        payload = base64.b64encode(encoded.tobytes()).decode("ascii")
        urls.append(f"data:image/jpeg;base64,{payload}")
    return urls, timestamps


def review_window(
    cap: cv2.VideoCapture,
    row: dict,
    base_url: str,
    model: str,
) -> dict:
    start = float(row.get("start_time") or 0.0)
    end = float(row.get("end_time") or start + 5.0)
    urls, timestamps = image_urls(cap, start, end)
    others = row.get("others") or {}
    experts = others.get("experts") or {}
    tool_counts = {
        str(item.get("label") or "").strip().lower(): int(item.get("frames_seen") or 0)
        for item in ((experts.get("yolo") or {}).get("tools") or [])
        if isinstance(item, dict) and item.get("label")
    }
    detector_hint = (
        "\n独立逐帧器械检测计数（仅作为图像形态复核证据）："
        + (json.dumps(tool_counts, ensure_ascii=False) if tool_counts else "无稳定器械类别")
        + "。若 clipper 为0而 grasper/hook/bipolar/scissors在多帧稳定出现，"
        "必须在图像中看到无歧义的施夹器闭合证据才能推翻检测结果；"
        "开窗抓钳的移动或闭合不能算施夹。"
    )
    content = [{"type": "text", "text": PROMPT + detector_hint}]
    content.extend(
        {"type": "image_url", "image_url": {"url": url, "detail": "low"}}
        for url in urls
    )
    response = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "只输出一个紧凑JSON对象，不要解释。"},
                {"role": "user", "content": content},
            ],
            "temperature": 0.0,
            "max_tokens": 180,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    raw = str(data["choices"][0]["message"]["content"] or "").strip()
    parsed = parse_object(raw)
    instrument = str(parsed.get("instrument") or "other").strip().lower()
    allowed_instruments = {
        "clip_applier",
        "electrocautery_hook",
        "bipolar_forceps",
        "grasper",
        "scissors",
        "other",
    }
    if instrument not in allowed_instruments:
        instrument = "other"
    active_clip_action = bool(parsed.get("active_clip_action"))
    deployed_clip_visible = bool(parsed.get("deployed_clip_visible"))
    target = str(parsed.get("target") or "unknown").strip().lower()
    if target not in {"cystic_duct", "cystic_artery", "unknown"}:
        target = "unknown"
    if not (active_clip_action or deployed_clip_visible):
        target = "unknown"
    alternate_tools = {
        key: count
        for key, count in tool_counts.items()
        if key in {"grasper", "hook", "bipolar", "scissors"}
    }
    strongest_alternate = max(alternate_tools.items(), key=lambda item: item[1], default=("", 0))
    fusion_veto = bool(
        active_clip_action
        and not deployed_clip_visible
        and int(tool_counts.get("clipper") or 0) == 0
        and strongest_alternate[1] >= 8
    )
    raw_active_clip_action = active_clip_action
    raw_target = target
    raw_visible_evidence = str(parsed.get("visible_evidence") or "")[:240]
    if fusion_veto:
        active_clip_action = False
        target = "unknown"
        instrument = {
            "hook": "electrocautery_hook",
            "bipolar": "bipolar_forceps",
            "grasper": "grasper",
            "scissors": "scissors",
        }.get(strongest_alternate[0], "other")
        decision_confidence = min(0.97, 0.80 + strongest_alternate[1] * 0.01)
        visible_evidence = (
            f"逐帧器械检测连续{strongest_alternate[1]}帧识别为"
            f"{strongest_alternate[0]}，未检出clipper；否决活动施夹。"
        )
        target_confidence = 0.0
    else:
        decision_confidence = float(
            parsed.get("decision_confidence")
            if parsed.get("decision_confidence") is not None
            else parsed.get("confidence") or 0.0
        )
        visible_evidence = raw_visible_evidence
        target_confidence = float(parsed.get("target_confidence") or 0.0)
    return {
        "success": True,
        "instrument": instrument,
        "active_clip_action": active_clip_action,
        "deployed_clip_visible": deployed_clip_visible,
        "target": target,
        "confidence": decision_confidence,
        "decision_confidence": decision_confidence,
        "target_confidence": target_confidence,
        "visible_evidence": visible_evidence,
        "detector_tool_counts": tool_counts,
        "fusion_veto": fusion_veto,
        "vlm_active_clip_action": raw_active_clip_action,
        "vlm_target": raw_target,
        "vlm_visible_evidence": raw_visible_evidence,
        "raw": raw[:800],
        "model": data.get("model") or model,
        "timestamps": [round(value, 3) for value in timestamps],
        "source": "focused-clip-target-backfill",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }


def is_weak_target_row(row: dict) -> bool:
    text = str(row.get("summary_text") or row.get("summary") or "")
    if not TARGET_CLIP_RE.search(text):
        return False
    others = row.get("others") or {}
    experts = others.get("experts") or {}
    visual = others.get("visual_gpt") or ((experts.get("open_vlm") or {}).get("visual") or {})
    visual_confidence = float((visual.get("target_structure") or {}).get("confidence") or 0.0)
    triplet_confidence = max(
        [
            float(item.get("confidence") or 0.0)
            for item in ((experts.get("triplet") or {}).get("target") or [])
            if str(item.get("label") or "").lower()
            in {"cystic_duct", "cystic_artery", "blood_vessel", "cystic_pedicle"}
        ]
        or [0.0]
    )
    return visual_confidence < 0.55 and triplet_confidence < 0.45


def apply_review(row: dict, review: dict, min_confidence: float) -> tuple[str, str]:
    others = row.setdefault("others", {})
    experts = others.setdefault("experts", {})
    open_vlm = experts.setdefault("open_vlm", {})
    open_visual = open_vlm.setdefault("visual", {})
    visual = others.get("visual_gpt")
    if not isinstance(visual, dict):
        visual = open_visual
        others["visual_gpt"] = visual
    for target_visual in {id(visual): visual, id(open_visual): open_visual}.values():
        target_visual["target_secondary_review"] = dict(review)
        target_visual["clip_action_secondary_review"] = dict(review)

    old = str(row.get("summary_text") or row.get("summary") or "")
    target = review.get("target")
    confidence = float(review.get("confidence") or 0.0)
    target_confidence = float(review.get("target_confidence") or 0.0)
    action_confirmed = bool(
        review.get("active_clip_action") or review.get("deployed_clip_visible")
    )
    new = old
    if review.get("success") and confidence >= min_confidence:
        if review.get("active_clip_action"):
            clip_review = {
                "success": True,
                "classification": "clip_applier",
                "confidence": confidence,
                "applier_active": True,
                "clamped_on_tissue": True,
                "instrument": review.get("instrument"),
                "reason": review.get("visible_evidence", ""),
                "source": "focused-clip-action-backfill",
            }
        elif review.get("deployed_clip_visible"):
            clip_review = {
                "success": True,
                "classification": "clip",
                "confidence": confidence,
                "applier_active": False,
                "clamped_on_tissue": True,
                "independent_from_instrument": True,
                "instrument": review.get("instrument"),
                "reason": review.get("visible_evidence", ""),
                "source": "focused-clip-action-backfill",
            }
        else:
            clip_review = {
                "success": True,
                "classification": "instrument",
                "confidence": confidence,
                "applier_active": False,
                "clamped_on_tissue": False,
                "instrument": review.get("instrument"),
                "reason": review.get("visible_evidence", ""),
                "source": "focused-clip-action-backfill",
            }

        applier = dict(visual.get("clip_applier") or {})
        applier.update({
            "visible": bool(review.get("instrument") == "clip_applier"),
            "active": bool(review.get("active_clip_action")),
            "confidence": confidence,
            "secondary_review": True,
        })
        generic = dict(visual.get("generic_clip") or {})
        generic.update({
            "visible": bool(review.get("deployed_clip_visible")),
            "placed": bool(review.get("deployed_clip_visible")),
            "confidence": confidence,
            "secondary_review": True,
        })
        for target_visual in {id(visual): visual, id(open_visual): open_visual}.values():
            target_visual["clip_secondary_review"] = dict(clip_review)
            target_visual["clip_applier"] = dict(applier)
            target_visual["generic_clip"] = dict(generic)

    if (
        action_confirmed
        and target in {"cystic_duct", "cystic_artery"}
        and confidence >= min_confidence
        and target_confidence >= min_confidence
    ):
        target_state = {
            "label": target,
            "confidence": target_confidence,
            "evidence": review.get("visible_evidence", ""),
            "evidence_source": "target_secondary_review",
        }
        visual["target_structure"] = target_state
        open_visual["target_structure"] = dict(target_state)
        new = replace_clipping_target(old, target)
    elif not action_confirmed and confidence >= min_confidence:
        target_state = {
            "label": "unknown",
            "confidence": 0.0,
            "evidence": review.get("visible_evidence", ""),
            "evidence_source": "clip_action_secondary_review",
        }
        visual["target_structure"] = target_state
        open_visual["target_structure"] = dict(target_state)
        expert_phase = str(((experts.get("phase") or {}).get("label") or "")).lower()
        stage1 = str(row.get("stage1_summary") or others.get("stage1_summary") or "")
        if expert_phase == "calot_triangle_dissection" and stage1:
            new = stage1
            row["surgical_phase"] = "CalotTriangleDissection"
        new = _strip_visual_rejected_clip_claims(
            new,
            visual,
            str(row.get("surgical_phase") or row.get("phase") or ""),
        )
        new = re.sub(
            r"(双极电凝钳|电凝钩)(?:正在)?分离(?:胆囊管|胆囊动脉)",
            r"\1分离肝胆三角纤维组织",
            new,
        )

    if "summary_text" in row:
        row["summary_text"] = new
    else:
        row["summary"] = new
    return old, new


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--summaries", type=Path, required=True)
    parser.add_argument("--windows", type=int, nargs="*")
    parser.add_argument("--base-url", default="http://127.0.0.1:8012/v1")
    parser.add_argument("--model", default="Qwen3-VL-8B-Instruct")
    parser.add_argument("--min-confidence", type=float, default=0.75)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Review every target-specific clipping claim, including previously reviewed rows.",
    )
    args = parser.parse_args()

    rows = json.loads(args.summaries.read_text(encoding="utf-8"))
    selected = set(args.windows or [])
    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {args.video}")
    results = []
    try:
        for row in rows:
            window_id = int(row.get("window_id") or 0)
            if selected:
                should_review = window_id in selected
            elif args.force:
                text = str(row.get("summary_text") or row.get("summary") or "")
                should_review = bool(TARGET_CLIP_RE.search(text))
            else:
                should_review = is_weak_target_row(row)
            if not should_review:
                continue
            review = review_window(cap, row, args.base_url, args.model)
            old, new = apply_review(row, review, args.min_confidence)
            result = {"window_id": window_id, "old": old, "new": new, **review}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        cap.release()

    args.summaries.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"reviewed": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
