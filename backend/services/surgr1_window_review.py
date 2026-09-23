"""Bounded SurgR1 image review, independent of capture and playback clocks."""
import asyncio
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from .surgr1_client import get_surgr1_client, load_config

QUESTION = "Given the laparoscopic cholecystectomy image <image>, describe the complete surgical action in terms of tool, action, and tissue."
_gate = asyncio.Semaphore(1)


def answer_only(raw):
    text = str(raw or "").strip()
    answer = re.search(r"<answer>(.*?)</answer>", text, re.DOTALL | re.IGNORECASE)
    if answer:
        return answer.group(1).strip()[:1200]
    # A truncated reasoning trace must never be presented as an observation.
    if "<think>" in text or "<answer>" in text or text.startswith("[Error"):
        return ""
    return text[:1200]


async def review_surgr1_window(images, timestamps):
    cfg = load_config().get("services", {}).get("surgr1", {})
    if not cfg.get("enabled") or cfg.get("mode") != "window_refinement" or not images:
        return {"success": False, "status": "disabled", "call_count": 0}
    client = get_surgr1_client()
    if urlparse(client.api_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        return {"success": False, "status": "local_only", "call_count": 0}
    try:
        await asyncio.wait_for(_gate.acquire(), timeout=3)
    except asyncio.TimeoutError:
        return {"success": False, "status": "busy", "call_count": 0}
    started = time.monotonic()
    count = min(len(images), max(1, min(3, int(cfg.get("max_images", 2)))))
    indices = [round(i * (len(images) - 1) / (count - 1)) for i in range(count)] if count > 1 else [len(images) // 2]
    try:
        with tempfile.TemporaryDirectory(prefix="surgr1-window-") as directory:
            paths = []
            for index in indices:
                image = images[index].convert("RGB")
                image.thumbnail((840, 840))
                path = str(Path(directory) / f"frame-{index}.jpg")
                await asyncio.to_thread(image.save, path, format="JPEG", quality=90)
                paths.append(path)
            result = await asyncio.wait_for(client.analyze_batch(
                image_paths=paths, questions=[QUESTION], temperature=0,
                max_tokens=int(cfg.get("max_tokens", 512)),
            ), timeout=float(cfg.get("timeout", 30)))
        observations = []
        raw_results = result.get("results") or []
        for index, row in zip(indices, raw_results):
            responses = row.get("responses") or {}
            answer = answer_only(responses.get("q0") or responses.get("surgical_action"))
            if answer:
                observations.append({"frame_index": index,
                                     "timestamp": timestamps[index] if index < len(timestamps) else None,
                                     "action": answer})
        return {"success": bool(observations), "status": "completed" if observations else "no_valid_answer",
                "model": "SurgR1", "call_count": 1, "frame_count": count,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "observations": observations, "raw_results": raw_results}
    except Exception as error:
        return {"success": False, "status": "failed", "error": str(error) or type(error).__name__,
                "call_count": 1, "latency_ms": round((time.monotonic() - started) * 1000)}
    finally:
        _gate.release()
