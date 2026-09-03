#!/usr/bin/env python3
"""Backfill focused scope-exit/fog reviews for saved full-video artifacts."""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

import cv2
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from backend.routers.analysis import (
    _ensure_required_event_nodes,
    _merge_visibility_status_events,
    _normalize_summary_for_event_nodes,
    _should_review_visibility_candidate,
)
from batch_record_analysis import event_subtitle_entries, wrap_subtitle, write_srt


PROMPT = (
    "这些是同一个5秒胆囊切除术窗口的连续帧。只判断场景，输出一个JSON。\n"
    "external_body：出现患者腹壁外侧皮肤、白色套管阀门、体外金属器械盘、"
    "手术巾或手术室，且不再看到腹腔内红色肝胆组织。\n"
    "specimen_bag_inside：白色或半透明、柔软折叠的塑料标本袋被抓钳夹持，"
    "周围仍有红色肝脏或腹腔组织；这不是体外。透明标本袋可能覆盖大部分画面，"
    "但会出现清楚的弧形袋口、塑料薄膜边缘、膜面褶皱、线状高光或双层透明轮廓。"
    "只要任意一帧存在这些有形塑料结构，就优先判specimen_bag_inside，不要把透明塑料膜误判成雾气。\n"
    "trocar_transition：主要看到套管或镜鞘的圆形内壁、规则管腔和中央出口，"
    "已看不到腹腔组织。规则圆环或管道结构不是雾气。\n"
    "foggy_inside：仍在腹腔内，但均匀雾气、水汽或烟雾遮挡组织；"
    "它没有可追踪的塑料袋边缘、袋口、薄膜褶皱或规则管腔。\n"
    "intra_abdominal：清楚看到红褐色肝胆组织。\n"
    "场景证据优先于清晰度：任意一帧出现直线形手术室顶灯、矩形柜体或监护设备、"
    "蓝色无菌巾、体外器械台或医护手套，即使镜头模糊或有水汽，也必须判为external_body。"
    "只有所有帧仍可辨认腹腔内肝胆组织，且没有手术室几何结构、没有标本袋的有形边缘、"
    "也没有规则套管管腔时，才可判foggy_inside。按优先级判断：external_body > "
    "specimen_bag_inside > trocar_transition > foggy_inside > intra_abdominal。\n"
    "只输出JSON："
    '{"classification":"external_body|specimen_bag_inside|trocar_transition|foggy_inside|intra_abdominal",'
    '"confidence":0.0,"evidence":"具体视觉证据"}'
)


def parse_object(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        value = json.loads(match.group(0)) if match else {}
    return value if isinstance(value, dict) else {}


def read_images(cap: cv2.VideoCapture, start: float, end: float) -> tuple[list[str], list[float]]:
    span = max(0.1, end - start)
    timestamps = [start + span * fraction for fraction in (0.20, 0.60, 0.90)]
    urls = []
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
    return urls, timestamps


def run_review(
    cap: cv2.VideoCapture,
    row: dict,
    base_url: str,
    model: str,
) -> dict:
    start = float(row.get("start_time") or 0.0)
    end = float(row.get("end_time") or start + 5.0)
    urls, timestamps = read_images(cap, start, end)
    content = [{"type": "text", "text": PROMPT}]
    for url in urls:
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
    parsed = parse_object(raw)
    return {
        "success": True,
        "classification": str(parsed.get("classification") or "").strip().lower(),
        "confidence": float(parsed.get("confidence") or 0.0),
        "evidence": str(parsed.get("evidence") or "")[:240],
        "raw": raw[:800],
        "model": data.get("model") or model,
        "images": len(urls),
        "timestamps": [round(value, 3) for value in timestamps],
        "source": "focused-visibility-backfill",
    }


def remove_visibility_prose(text: str) -> str:
    out = str(text or "")
    for pattern in (
        r"[，,；;。]?\s*镜头起雾，手术视野受遮挡",
        r"[，,；;。]?\s*雾已去除，腹腔视野恢复",
        r"[，,；;。]?\s*镜头移出体外，画面切换至套管口或腹壁外场景",
    ):
        out = re.sub(pattern, "", out)
    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"[，,]\s*。", "。", out)
    return re.sub(r"。{2,}", "。", out).strip(" ，；。")


def apply_review(row: dict, review: dict) -> tuple[str, str]:
    others = row.setdefault("others", {})
    experts = others.setdefault("experts", {})
    open_vlm = experts.setdefault("open_vlm", {})
    visual = open_vlm.setdefault("visual", {})
    old = str(row.get("summary_text") or row.get("summary") or "")
    classification = review.get("classification")
    confidence = float(review.get("confidence") or 0.0)
    visibility = dict(visual.get("visibility") or {})
    if confidence >= 0.75 and classification in {"external_body", "trocar_transition"}:
        visibility.update({
            "status": "out_of_body",
            "out_of_body": True,
            "fog": False,
            "fog_cleared": False,
            "confidence": confidence,
            "evidence": review.get("evidence", ""),
            "evidence_source": "visibility_secondary_review",
        })
        new = "镜头移出体外，画面切换至套管口或腹壁外场景。"
    elif confidence >= 0.75 and classification == "foggy_inside":
        visibility.update({
            "status": "foggy",
            "out_of_body": False,
            "fog": True,
            "fog_cleared": False,
            "confidence": confidence,
            "evidence": review.get("evidence", ""),
            "evidence_source": "visibility_secondary_review",
        })
        base = remove_visibility_prose(old)
        new = (base + "。" if base else "") + "镜头起雾，手术视野受遮挡。"
    elif confidence >= 0.75 and classification in {"intra_abdominal", "specimen_bag_inside"}:
        visibility.update({
            "status": "clear",
            "out_of_body": False,
            "fog": False,
            "fog_cleared": False,
            "confidence": confidence,
            "evidence": review.get("evidence", ""),
            "evidence_source": "visibility_secondary_review",
        })
        base = remove_visibility_prose(old)
        if classification == "specimen_bag_inside" and not base:
            new = "当前处于胆囊取出与装袋，将胆囊装入标本袋并准备取出。"
        else:
            new = base + ("。" if base else "")
    else:
        new = old
    visual["visibility"] = visibility
    visual["visibility_secondary_review"] = review
    open_vlm["visual"] = visual
    if isinstance(others.get("visual_gpt"), dict):
        others["visual_gpt"].update(visual)
    if "summary_text" in row:
        row["summary_text"] = new
    else:
        row["summary"] = new
    return old, new


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--summaries", type=Path, required=True)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--windows", type=int, nargs="*")
    parser.add_argument("--base-url", default="http://127.0.0.1:8012/v1")
    parser.add_argument("--model", default="Qwen3-VL-8B-Instruct")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    rows = json.loads(args.summaries.read_text(encoding="utf-8"))
    selected = set(args.windows or [])
    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {args.video}")
    corrections = []
    try:
        for row in rows:
            window_id = int(row.get("window_id") or 0)
            others = row.get("others") or {}
            experts = others.get("experts") or {}
            open_vlm = experts.get("open_vlm") or {}
            visual = others.get("visual_gpt") or open_vlm.get("visual") or {}
            local_cue = experts.get("local_visibility") or {}
            existing = visual.get("visibility_secondary_review") or {}
            if selected:
                should_review = window_id in selected
            else:
                should_review = _should_review_visibility_candidate(visual, local_cue)
            if not should_review or (existing.get("success") and not args.force):
                continue
            review = run_review(cap, row, args.base_url, args.model)
            old, new = apply_review(row, review)
            corrections.append({
                "window_id": window_id,
                "start_time": row.get("start_time"),
                "classification": review.get("classification"),
                "confidence": review.get("confidence"),
                "old": old,
                "new": new,
            })
    finally:
        cap.release()

    args.summaries.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    write_srt(
        [
            (
                float(row.get("start_time") or 0),
                float(row.get("end_time") or 0),
                wrap_subtitle(f"W{row.get('window_id')} {row.get('summary_text') or row.get('summary') or ''}"),
            )
            for row in rows
        ],
        args.summaries.with_name(args.summaries.name.replace(".summaries.json", ".windows.srt")),
    )

    if args.events and args.events.exists():
        payload = json.loads(args.events.read_text(encoding="utf-8"))
        events = payload.get("events", []) if isinstance(payload, dict) else payload
        records = [
            record
            for record in (_normalize_summary_for_event_nodes(row) for row in rows)
            if record and record.get("summary")
        ]
        events = _ensure_required_event_nodes(events, records, "zh")
        events = _merge_visibility_status_events(events, records, "zh")
        if isinstance(payload, dict):
            payload["events"] = events
        else:
            payload = events
        args.events.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        write_srt(
            event_subtitle_entries(events, rows),
            args.events.with_name(args.events.name.replace(".events.json", ".events.srt")),
        )
    print(json.dumps(corrections, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
