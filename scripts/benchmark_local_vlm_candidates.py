#!/usr/bin/env python3
"""Benchmark local OpenAI-compatible VLMs on surgical video windows.

This script does not download or start models. Start one candidate model with an
OpenAI-compatible server, then run this client against selected windows.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


PROMPT = """你将看到一个约5秒的腹腔镜胆囊切除术视频窗口抽帧。
只根据图像判断，不要根据窗口号或外部先验猜测。

请重点判断：
1. 是否可见 Hem-o-lok 聚合物夹、金属钛夹、钛夹钳活动。
2. 是否可见剪刀；如果可见，是否在胆囊管或胆囊动脉附近操作或剪断。
3. CVS 是否已达成、评估中或不适用。
4. 是否有大量活动性出血、出血已控制、镜头起雾/模糊、镜头移出体外、标本袋装袋取出。
5. 钛夹是小型银灰金属夹体；Hem-o-lok 是白色/乳白/淡紫塑料锁扣夹。不要把电凝钩尖端、剪刀刃、钛夹钳钳口或高光当作夹体。

输出严格 JSON，不要 markdown，不要解释：
{
  "summary": "一句中文事实摘要",
  "hemolok": {"present": false, "confidence": 0.0, "evidence": ""},
  "titanium_clip": {"present": false, "confidence": 0.0, "evidence": ""},
  "clip_applier": {"active": false, "confidence": 0.0, "target": "cystic_duct|cystic_artery|unknown"},
  "scissors": {"visible": false, "cutting": false, "confidence": 0.0, "target": "cystic_duct|cystic_artery|unknown"},
  "cvs": {"status": "not_applicable|assessing|partial|achieved", "confidence": 0.0},
  "bleeding": {"active": false, "severity": "none|minor|moderate|severe", "controlled": false, "confidence": 0.0},
  "visibility": {"status": "clear|foggy|blurred|blocked|out_of_body", "confidence": 0.0},
  "specimen_bagging": {"present": false, "confidence": 0.0}
}
"""


def load_profile(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_windows(spec: str) -> List[int]:
    out: List[int] = []
    for chunk in re.split(r"[,，\s]+", spec.strip()):
        if not chunk:
            continue
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            start, end = int(a), int(b)
            step = 1 if end >= start else -1
            out.extend(range(start, end + step, step))
        else:
            out.append(int(chunk))
    return sorted(set(out))


def image_to_data_url(frame_bgr: Any, max_side: int = 768, quality: int = 72) -> str:
    import cv2

    h, w = frame_bgr.shape[:2]
    scale = min(1.0, float(max_side) / float(max(h, w)))
    if scale < 1.0:
        frame_bgr = cv2.resize(frame_bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("failed to encode frame")
    payload = base64.b64encode(encoded.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{payload}"


def sample_window_frames(video_path: Path, window_id: int, window_duration: float, max_frames: int) -> Tuple[float, float, List[str]]:
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open video: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_count / fps if fps > 0 else 0.0

    start = float(window_id) * float(window_duration)
    end = min(start + float(window_duration), duration) if duration else start + float(window_duration)
    if end <= start:
        end = start + float(window_duration)

    if max_frames <= 1:
        timestamps = [(start + end) / 2.0]
    else:
        # Avoid exact boundaries; they often duplicate adjacent windows.
        timestamps = [
            start + (end - start) * (i + 0.5) / max_frames
            for i in range(max_frames)
        ]

    frames: List[str] = []
    for ts in timestamps:
        if fps > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(math.floor(ts * fps))))
        else:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0, int(ts * 1000)))
        ok, frame = cap.read()
        if ok and frame is not None:
            frames.append(image_to_data_url(frame))
    cap.release()
    return start, end, frames


def extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {"_raw": cleaned}
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(cleaned[start:end + 1])
                return parsed if isinstance(parsed, dict) else {"_raw": cleaned}
            except Exception:
                pass
    return {"_raw": cleaned, "_json_error": True}


def call_openai_compatible(candidate: Dict[str, Any], frames: List[str], prompt: str, timeout: float) -> Dict[str, Any]:
    base_url = str(candidate["openai_base_url"]).rstrip("/")
    model = candidate.get("served_model_name") or candidate.get("name")
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for url in frames:
        content.append({"type": "image_url", "image_url": {"url": url}})
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": float(candidate.get("temperature", 0.0)),
        "max_tokens": int(candidate.get("max_tokens", 700)),
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        latency = time.time() - started
        text = payload["choices"][0]["message"]["content"]
        return {
            "success": True,
            "latency_seconds": round(latency, 3),
            "model": payload.get("model") or model,
            "text": text,
            "parsed": extract_json_object(text),
        }
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")[:2000]
        return {"success": False, "latency_seconds": round(time.time() - started, 3), "error": body_text}
    except Exception as exc:
        return {"success": False, "latency_seconds": round(time.time() - started, 3), "error": str(exc)}


def iter_candidates(profile: Dict[str, Any], selected: Iterable[str]) -> List[Dict[str, Any]]:
    candidates = profile.get("candidates") or []
    wanted = {name.strip() for name in selected if name.strip()}
    if not wanted:
        return candidates
    return [c for c in candidates if c.get("name") in wanted or c.get("served_model_name") in wanted]


def write_summary(output_dir: Path, results: List[Dict[str, Any]]) -> None:
    lines = ["# Local VLM Benchmark Summary", ""]
    by_model: Dict[str, List[Dict[str, Any]]] = {}
    for row in results:
        by_model.setdefault(row["candidate"], []).append(row)
    for name, rows in by_model.items():
        successes = [r for r in rows if r.get("success")]
        json_ok = [r for r in successes if not (r.get("parsed") or {}).get("_json_error")]
        avg_latency = sum(float(r.get("latency_seconds") or 0) for r in rows) / max(1, len(rows))
        lines.append(f"## {name}")
        lines.append(f"- windows: {len(rows)}")
        lines.append(f"- success: {len(successes)}/{len(rows)}")
        lines.append(f"- strict_json: {len(json_ok)}/{len(rows)}")
        lines.append(f"- avg_latency_seconds: {avg_latency:.2f}")
        lines.append("")
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local VLM candidates on selected surgical video windows.")
    parser.add_argument("--video", required=True, help="Path to a local video file.")
    parser.add_argument("--windows", default="185-195", help="Window ids, e.g. '76,185-195'. Uses zero-based internal ids.")
    parser.add_argument("--window-duration", type=float, default=5.0)
    parser.add_argument("--max-frames", type=int, default=4)
    parser.add_argument("--candidate", action="append", default=[], help="Candidate name from profile. Repeatable. Defaults to all.")
    parser.add_argument("--profile", default="config_profiles/local_vlm_4090_candidates_20260707.json")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    profile_path = Path(args.profile)
    if not profile_path.is_absolute():
        profile_path = repo_root / profile_path
    profile = load_profile(profile_path)
    candidates = iter_candidates(profile, args.candidate)
    if not candidates:
        raise SystemExit("No matching VLM candidates in profile")

    video_path = Path(args.video).expanduser().resolve()
    windows = parse_windows(args.windows)
    output_dir = Path(args.output_dir) if args.output_dir else repo_root / "runs" / "local_vlm_benchmark" / datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []
    for window_id in windows:
        start, end, frames = sample_window_frames(video_path, window_id, args.window_duration, args.max_frames)
        if not frames:
            results.append({
                "candidate": "frame_extraction",
                "window_id": window_id,
                "start_time": start,
                "end_time": end,
                "success": False,
                "error": "no frames extracted",
            })
            continue
        for candidate in candidates:
            response = call_openai_compatible(candidate, frames, PROMPT, args.timeout)
            row = {
                "candidate": candidate.get("name"),
                "served_model_name": candidate.get("served_model_name"),
                "modelscope_model_id": candidate.get("modelscope_model_id"),
                "window_id": window_id,
                "start_time": start,
                "end_time": end,
                "frame_count": len(frames),
                **response,
            }
            results.append(row)
            with (output_dir / "results.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps({
                "candidate": row["candidate"],
                "window_id": window_id,
                "success": row.get("success"),
                "latency_seconds": row.get("latency_seconds"),
                "summary": (row.get("parsed") or {}).get("summary"),
                "error": row.get("error"),
            }, ensure_ascii=False))

    write_summary(output_dir, results)
    print(f"wrote benchmark results to {output_dir}")


if __name__ == "__main__":
    main()
