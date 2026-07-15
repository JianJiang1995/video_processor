#!/usr/bin/env python3
"""Run the focused scissors verifier for saved analysis windows."""

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
    _strip_focused_scissors_instrument_conflicts,
    _strip_visual_rejected_clip_claims,
    _strip_visual_rejected_scissors_claims,
)


PROMPT = (
    "你是腹腔镜手术器械复核器。下面多张全图来自同一个5秒窗口。"
    "只判断主要操作器械，不参考手术阶段和其他模型。\n"
    "剪刀：有两片细长、尖锐、相互对合的金属刀刃，铰链后张开形成V形，"
    "可见开合或夹剪组织。\n"
    "电凝钩：单根杆，末端常有白色陶瓷绝缘头和一个细小弯钩，"
    "不存在两片对合刀刃。\n"
    "抓钳：两片较钝、有齿或开窗的夹爪，用于抓持牵拉，不是锐利刀刃。\n"
    "施夹器：较宽厚、近乎平行且通常不交叉的对合夹臂，用于释放夹子；"
    "即使看起来像两片金属臂，也不是剪刀。\n"
    "必须逐张检查、以形态为准，不要按多张图的多数投票。"
    "只有任意一张图清晰显示细长刀刃、交叉铰链或实际剪切闭合，"
    "才能确认scissors_visible=true；仅有两片平行对合夹臂不够。"
    "形态不确定时必须否决剪刀。只输出JSON："
    '{"instrument":"scissors|electrocautery_hook|grasper|clip_applier|other",'
    '"scissors_visible":false,"scissors_cutting":false,'
    '"confidence":0.0,"reason":"一句话形态证据"}'
)


def parse_json_object(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return {}
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else {}


def image_urls(video: Path, timestamps: list[float]) -> list[str]:
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video}")
    urls = []
    try:
        for timestamp in timestamps:
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f"cannot read frame at {timestamp:.3f}s")
            height, width = frame.shape[:2]
            if width > 1280:
                frame = cv2.resize(frame, (1280, round(height * 1280 / width)))
            ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if not ok:
                raise RuntimeError(f"cannot encode frame at {timestamp:.3f}s")
            payload = base64.b64encode(encoded.tobytes()).decode("ascii")
            urls.append(f"data:image/jpeg;base64,{payload}")
    finally:
        cap.release()
    return urls


def review_window(video: Path, row: dict, base_url: str, model: str) -> dict:
    start = float(row.get("start_time") or 0.0)
    end = float(row.get("end_time") or start + 5.0)
    span = max(0.1, end - start)
    timestamps = [start + span * fraction for fraction in (0.20, 0.60, 0.90)]
    content = [{"type": "text", "text": PROMPT}]
    for url in image_urls(video, timestamps):
        content.append({"type": "image_url", "image_url": {"url": url, "detail": "low"}})
    response = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "只输出一个紧凑JSON对象。"},
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
    parsed = parse_json_object(raw)
    instrument = str(parsed.get("instrument") or "other").strip().lower()
    return {
        "success": True,
        "instrument": instrument,
        "scissors_visible": bool(parsed.get("scissors_visible")) or instrument == "scissors",
        "scissors_cutting": bool(parsed.get("scissors_cutting")) and instrument == "scissors",
        "confidence": float(parsed.get("confidence") or 0.0),
        "reason": str(parsed.get("reason") or "")[:240],
        "raw": raw[:800],
        "model": data.get("model") or model,
        "images": len(timestamps),
        "image_selection": "window_fractions_backfill",
        "timestamps": [round(value, 3) for value in timestamps],
        "backfilled_at": datetime.now(timezone.utc).isoformat(),
    }


def apply_review(row: dict, review: dict, min_confidence: float) -> tuple[str, str]:
    others = row.setdefault("others", {})
    experts = others.setdefault("experts", {})
    open_vlm = experts.setdefault("open_vlm", {})
    open_visual = open_vlm.setdefault("visual", {})
    visual = others.get("visual_gpt")
    if not isinstance(visual, dict):
        visual = open_visual
        others["visual_gpt"] = visual

    for target in {id(open_visual): open_visual, id(visual): visual}.values():
        target["scissors_secondary_review"] = dict(review)

    old = str(row.get("summary_text") or row.get("summary") or "")
    confidence = float(review.get("confidence") or 0.0)
    if review.get("success") and confidence >= min_confidence:
        scissors = dict(visual.get("scissors") or {})
        if review.get("scissors_visible"):
            scissors.update({
                "visible": True,
                "cutting": bool(review.get("scissors_cutting")),
                "confidence": confidence,
                "secondary_review": True,
            })
            applier = dict(visual.get("clip_applier") or {})
            applier.update({
                "visible": False,
                "active": False,
                "rejected_by_scissors_review": True,
            })
            visual["clip_applier"] = applier
        elif str(review.get("instrument") or "") in {
            "electrocautery_hook",
            "grasper",
            "clip_applier",
            "other",
        }:
            scissors.update({
                "visible": False,
                "cutting": False,
                "confidence": confidence,
                "secondary_review": True,
                "rejected_instrument": review.get("instrument"),
            })
        visual["scissors"] = scissors

        focused_instrument = str(review.get("instrument") or "").strip().lower()
        if focused_instrument in {
            "scissors",
            "electrocautery_hook",
            "grasper",
            "other",
        }:
            previous_clip_review = dict(visual.get("clip_secondary_review") or {})
            clip_review = {
                "success": True,
                "classification": "instrument",
                "instrument": focused_instrument,
                "confidence": confidence,
                "applier_active": False,
                "clamped_on_tissue": False,
                "reason": review.get("reason", ""),
                "source": "focused-instrument-backfill",
            }
            if previous_clip_review:
                clip_review["superseded_clip_review"] = previous_clip_review
            visual["clip_secondary_review"] = clip_review
            open_visual["clip_secondary_review"] = dict(clip_review)

        for target in {id(open_visual): open_visual, id(visual): visual}.values():
            target["scissors"] = dict(scissors)
            if "clip_applier" in visual:
                target["clip_applier"] = dict(visual["clip_applier"])

    phase = str(row.get("surgical_phase") or row.get("phase") or "")
    new = _strip_visual_rejected_clip_claims(old, visual, phase)
    new = _strip_visual_rejected_scissors_claims(new, visual, experts, phase)
    new = _strip_focused_scissors_instrument_conflicts(new, visual)
    if "summary_text" in row:
        row["summary_text"] = new
    else:
        row["summary"] = new
    return old, new


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--summaries", type=Path, required=True)
    parser.add_argument("--windows", type=int, nargs="+", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8012/v1")
    parser.add_argument("--model", default="Qwen3-VL-8B-Instruct")
    parser.add_argument("--min-confidence", type=float, default=0.75)
    args = parser.parse_args()

    rows = json.loads(args.summaries.read_text(encoding="utf-8"))
    wanted = set(args.windows)
    found = set()
    for row in rows:
        window_id = int(row.get("window_id") or 0)
        if window_id not in wanted:
            continue
        review = review_window(args.video, row, args.base_url, args.model)
        old, new = apply_review(row, review, args.min_confidence)
        found.add(window_id)
        print(json.dumps({
            "window_id": window_id,
            "old": old,
            "new": new,
            **review,
        }, ensure_ascii=False))

    missing = sorted(wanted - found)
    if missing:
        raise RuntimeError(f"windows missing from summaries: {missing}")
    args.summaries.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
