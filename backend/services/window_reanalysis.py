"""Isolated, image-first review of a persisted window using only the local VLM."""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

import cv2
from PIL import Image

from .frame_storage_service import get_frame_storage_service
from .glm_client import get_glm_client
from .local_video_source import resolve_video_source, apply_input_layout, split_input_layout
from .local_visual_prompt import LOCAL_VISUAL_PROMPT


def sample_indices(length, maximum=4):
    if length <= maximum:
        return list(range(length))
    return [round(i * (length - 1) / (maximum - 1)) for i in range(maximum)]


def fit_image(image):
    result = image.convert("RGB")
    result.thumbnail((640, 640), Image.Resampling.LANCZOS)
    return result


def load_review_frames(session, start, end):
    """Read saved, already-cropped frames or a private file decoder, never live capture."""
    storage = session.get("storage_path")
    if storage:
        frames = get_frame_storage_service().list_frames_in_range(storage, start, end)
        frames = sorted((f for f in frames if start <= f["timestamp"] < end),
                        key=lambda f: f["timestamp"])
        images, times = [], []
        for index in sample_indices(len(frames)):
            frame = frames[index]
            try:
                with Image.open(Path(storage) / frame["path"]) as image:
                    images.append(fit_image(image))
                times.append(frame["timestamp"])
            except (OSError, ValueError):
                continue
        if images:
            return images, times

    video_path = str(session["video_path"])
    clean_path, _ = split_input_layout(video_path)
    # simulator:// is a mutable device name, not the historical backing file.
    # Missing stored frames must not silently analyze a different simulator run.
    if not (clean_path.startswith("filesim://") or Path(clean_path).is_file()):
        raise FileNotFoundError("review_frames_unavailable")
    source = resolve_video_source(video_path)
    if not Path(source.source).is_file():
        raise FileNotFoundError("review_frames_unavailable")
    capture = cv2.VideoCapture(source.source, cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 4])
    images, times = [], []
    try:
        for index in range(4):
            timestamp = start + (end - start) * (index + .5) / 4
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            actual = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if not start <= actual < end:
                continue
            frame = apply_input_layout(frame, source.input_layout)
            images.append(fit_image(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))))
            times.append(actual)
    finally:
        capture.release()
    if not images:
        raise FileNotFoundError("review_frames_unavailable")
    return images, times


def parse_review(text):
    raw = str(text or "").strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("review_invalid_result")
    summary = result.get("summary")
    visual = result.get("visual")
    if not isinstance(summary, str) or not summary.strip() or not isinstance(visual, dict) or not visual:
        raise ValueError("review_invalid_result")
    return {"summary": summary.strip(), "visual": visual}


async def analyze_review(images, timestamps):
    client = get_glm_client()
    # This endpoint must never inherit an external-provider fallback.
    address = urlparse(client.api_url)
    if address.scheme not in {"http", "https"} or address.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise PermissionError("review_local_only")
    response = await asyncio.wait_for(client._analyze_images_with_timestamps(
        images=images, timestamps=timestamps, question=LOCAL_VISUAL_PROMPT,
        system_prompt="仅依据本次图像重新核实，不沿用旧摘要。单帧不能确认动态动作。只输出要求的JSON。",
        temperature=0.01, max_tokens=1200,
    ), timeout=90)
    if not response.get("success"):
        raise RuntimeError("review_model_unavailable")
    return {**parse_review(response.get("text")), "model": response.get("model", client.model_name)}
