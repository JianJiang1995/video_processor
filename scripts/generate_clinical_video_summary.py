#!/usr/bin/env python3
"""Generate a per-video clinical Markdown summary from window summaries.

This is an offline reporting step: it consumes already-produced window
summaries and asks the configured LLM to produce a concise doctor-facing
summary. It does not alter live analysis records.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
import urllib.request
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.gemini_client import GeminiClient  # noqa: E402
from backend.services.vlm_factory import load_config  # noqa: E402


def _summary_text(item: Dict[str, Any]) -> str:
    return str(
        item.get("summary")
        or item.get("glm_summary")
        or item.get("summary_text")
        or ""
    ).strip()


def _window_id(item: Dict[str, Any]) -> int:
    try:
        return int(item.get("window_id") or 0)
    except Exception:
        return 0


def _time_value(item: Dict[str, Any], *keys: str) -> float:
    for key in keys:
        try:
            value = item.get(key)
            if value is not None:
                return float(value)
        except Exception:
            pass
    return 0.0


def _load_json(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("summaries", "items", "windows", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError(f"{path} does not contain a summary list")
    return [item for item in data if isinstance(item, dict)]


def _fetch_session(base_url: str, session_id: str) -> List[Dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/analysis/summaries/{session_id}"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response for session {session_id}")
    return [item for item in data if isinstance(item, dict)]


def _compact_records(items: List[Dict[str, Any]], max_records: int = 180) -> List[Dict[str, Any]]:
    rows = []
    for item in items:
        text = _summary_text(item)
        if not text:
            continue
        rows.append({
            "window_id": _window_id(item),
            "start": _time_value(item, "start_time", "window_start"),
            "end": _time_value(item, "end_time", "window_end"),
            "phase": str(item.get("dominant_phase") or item.get("surgical_phase") or item.get("phase") or ""),
            "summary": text,
        })
    rows.sort(key=lambda row: (row["start"], row["window_id"]))
    if len(rows) <= max_records:
        return rows

    # Keep temporal coverage without sending every repeated window.
    step = max(1, len(rows) // max_records)
    sampled = rows[::step]
    if sampled[-1] != rows[-1]:
        sampled.append(rows[-1])
    return sampled[:max_records]


def _load_events(path: Path | None) -> List[Dict[str, Any]]:
    if not path:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("events", "items", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _unique_values(items: List[Dict[str, Any]], keys: tuple[str, ...]) -> Dict[str, set[str]]:
    values: Dict[str, set[str]] = {key: set() for key in keys}
    for item in items:
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                values[key].add(str(value))
    return values


def _assert_single_video_input(items: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> None:
    """Fail fast when a JSON input clearly mixes multiple sessions/videos."""
    summary_values = _unique_values(items, ("session_id", "video_session_id", "video_name", "video_path"))
    event_values = _unique_values(events, ("session_id", "video_session_id", "video_name", "video_path"))

    for label, values in (("summary", summary_values), ("event", event_values)):
        for key, seen in values.items():
            if len(seen) > 1:
                raise ValueError(
                    f"{label} input contains multiple {key} values; "
                    "generate one clinical summary per video/session"
                )

    for key in ("session_id", "video_session_id", "video_name", "video_path"):
        if summary_values.get(key) and event_values.get(key) and summary_values[key] != event_values[key]:
            raise ValueError(f"summary and event inputs refer to different {key} values")


def _compact_events(items: List[Dict[str, Any]], max_events: int = 40) -> List[Dict[str, Any]]:
    rows = []
    for item in items or []:
        title = str(item.get("title") or item.get("event_title") or item.get("type") or "").strip()
        summary = str(item.get("summary") or item.get("description") or "").strip()
        if not title and not summary:
            continue
        rows.append({
            "start": _time_value(item, "start_time", "start"),
            "end": _time_value(item, "end_time", "end"),
            "type": str(item.get("type") or item.get("event_type") or ""),
            "severity": str(item.get("severity") or ""),
            "title": title,
            "summary": summary,
        })
    rows.sort(key=lambda row: (row["start"], row["end"]))
    return rows[:max_events]


def _build_prompt(video_title: str, records: List[Dict[str, Any]], events: List[Dict[str, Any]] | None = None) -> str:
    record_lines = []
    for row in records:
        record_lines.append(
            f"- 窗口{row['window_id']} [{row['start']:.0f}-{row['end']:.0f}s]"
            f" phase={row['phase'] or 'unknown'}: {row['summary']}"
        )
    timeline = "\n".join(record_lines)
    event_lines = []
    for event in events or []:
        event_lines.append(
            f"- [{event['start']:.0f}-{event['end']:.0f}s]"
            f" type={event['type'] or 'unknown'} severity={event['severity'] or 'normal'} "
            f"{event['title']}: {event['summary']}"
        )
    event_timeline = "\n".join(event_lines) if event_lines else "（无独立事件节点输入）"
    return textwrap.dedent(f"""
    请基于下面的腹腔镜胆囊切除术窗口摘要，为单个视频生成医生后续复盘用的 Markdown 精要总结。

    视频：{video_title}

    要求：
    1. 不是流水账，不逐个窗口复述，只提炼临床相关重点，全文控制在 700-1000 个中文字符左右。
    2. 必须包含这些小节：整体判断、关键手术阶段、关键操作、CVS/安全核查、出血与视野事件、需要医生回看的不确定点。
    3. 描述要审慎，模型不能确认的地方要明确写“需回看原片确认”，不要把模型摘要当成临床定论。
    4. 关注胆囊管/胆囊动脉夹闭切断、夹子放置、标本袋装袋/取出、镜头移出体外、起雾、活动性出血或凝血控制。
    5. 不要输出“窗口1/窗口31/window_id”等窗口编号，也不要罗列大量时间点；全篇最多出现 6 个具体时间段。
    6. 关键手术阶段只写连续的大阶段或主趋势，不能把同一阶段的每次重复出现都列出来。
    7. 关键操作只写临床重要动作，不要重复写“可见器械”或同一夹闭残端反复出现。
    8. 如果多个相邻摘要表达相似，要合并为一个阶段或一个临床事件。
    9. 输出中文 Markdown，只输出该视频一个总结，不要包含其他视频。

    关键事件节点（若与窗口摘要冲突，以视觉事实更具体、时间更近的信息为主）：
    {event_timeline}

    窗口摘要数据：
    {timeline}
    """).strip()


async def _call_llm(prompt: str, max_tokens: int) -> str:
    config = load_config()
    report_cfg = config.get("services", {}).get("clinical_summary", {})
    translation_cfg = config.get("services", {}).get("translation", {})
    provider = report_cfg.get("provider") or translation_cfg.get("provider") or "gemini"
    if provider != "gemini":
        raise RuntimeError(f"clinical summary provider {provider!r} is not implemented in this CLI")

    client = GeminiClient(
        model_name=report_cfg.get("model_name") or translation_cfg.get("model_name") or "gemini-2.5-flash",
        thinking_level=report_cfg.get("thinking_level") or translation_cfg.get("thinking_level") or "none",
        max_tokens=max_tokens,
    )
    result = await client.chat(
        message=prompt,
        system_prompt="你是资深腹腔镜胆囊切除术视频复盘助手，只输出中文Markdown。",
        temperature=float(report_cfg.get("temperature", 0.0)),
        max_tokens=max_tokens,
    )
    if not result.get("success"):
        raise RuntimeError(result.get("error") or result.get("text") or "LLM call failed")
    text = str(result.get("text") or "").strip()
    if not text:
        raise RuntimeError("LLM returned empty summary")
    return text


def _fallback_summary(video_title: str, records: List[Dict[str, Any]], error: str) -> str:
    return textwrap.dedent(f"""
    # {video_title} 临床精要总结

    > LLM 生成失败：{error}

    ## 整体判断

    本次离线总结没有生成正式医生复盘文本。已读取 {len(records)} 条窗口摘要，但为避免把窗口记录误写成流水账式正式报告，本文件不输出逐窗口摘录。

    ## 处理建议

    请确认临床总结模型配置和网络连接后重新运行该脚本，或调用后端 `POST /api/analysis/clinical-summary/{{session_id}}` 生成单视频 Markdown。
    """).strip() + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-json", type=Path, help="Window summary JSON file")
    parser.add_argument("--events-json", type=Path, help="Optional key-event JSON file")
    parser.add_argument("--session-id", help="Fetch summaries from backend by session id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--video-title", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-records", type=int, default=180)
    parser.add_argument("--max-tokens", type=int, default=2200)
    parser.add_argument("--allow-mixed-input", action="store_true", help="Skip single-session/video input validation")
    args = parser.parse_args()

    if not args.input_json and not args.session_id:
        parser.error("provide --input-json or --session-id")

    items = _load_json(args.input_json) if args.input_json else _fetch_session(args.base_url, args.session_id)
    raw_events = _load_events(args.events_json) if args.events_json else []
    if not args.allow_mixed_input:
        _assert_single_video_input(items, raw_events)
    records = _compact_records(items, max_records=args.max_records)
    events = _compact_events(raw_events)
    prompt = _build_prompt(args.video_title, records, events)

    try:
        markdown = await _call_llm(prompt, max_tokens=args.max_tokens)
    except Exception as exc:
        markdown = _fallback_summary(args.video_title, records, str(exc))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
