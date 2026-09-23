"""Isolated, image-first review of a persisted window using only the local VLM."""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

import cv2
from PIL import Image

from .frame_storage_service import get_frame_storage_service
from .glm_client import get_glm_client
from .surgr1_window_review import review_surgr1_window
from .triplet_service import get_triplet_service, LABEL_SCHEMA
from .local_video_source import resolve_video_source, apply_input_layout, split_input_layout
from .local_visual_prompt import (
    build_local_visual_prompt, scene_observations_need_review,
    VISIBILITY_REVIEW_PROMPT, validate_visibility_review, reviewed_scene_summary,
)


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
    if not (clean_path.startswith(("filesim://", "simulator://file/")) or Path(clean_path).is_file()):
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


async def analyze_review(images, timestamps, experts=None, phase=None):
    client = get_glm_client()
    # This endpoint must never inherit an external-provider fallback.
    address = urlparse(client.api_url)
    if address.scheme not in {"http", "https"} or address.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise PermissionError("review_local_only")
    experts = dict(experts or {})
    if experts.get("triplet") and experts["triplet"].get("label_schema") != LABEL_SCHEMA:
        def refresh_triplet():
            expert = get_triplet_service()
            return expert.recognize_clip(images) if expert else {}
        experts["triplet"] = await asyncio.to_thread(refresh_triplet)
    experts["surgr1"] = await review_surgr1_window(images, timestamps)
    response = await asyncio.wait_for(client._analyze_images_with_timestamps(
        images=images, timestamps=timestamps, question=build_local_visual_prompt(experts, phase),
        system_prompt="仅依据本次图像重新核实，不沿用旧摘要。单帧不能确认动态动作。只输出要求的JSON。",
        temperature=0.01, max_tokens=1400,
    ), timeout=90)
    if not response.get("success"):
        raise RuntimeError("review_model_unavailable")
    result = {**parse_review(response.get("text")), "model": response.get("model", client.model_name),
              "experts": experts}
    if scene_observations_need_review(result['visual']):
        result = await review_scene(images, timestamps, result, client=client)
    return result


async def review_scene(images, timestamps, result, client=None):
    """Recheck a candidate scene against images, retaining all prior facts on uncertainty."""
    client = client or get_glm_client()
    address = urlparse(client.api_url)
    if address.scheme not in {'http', 'https'} or address.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise PermissionError('review_local_only')
    if scene_observations_need_review(result['visual']):
        # Match the live scene verifier: one instruction followed by images.
        # The timestamped primary-analysis wrapper changes the task framing.
        content = [{'type': 'text', 'text': VISIBILITY_REVIEW_PROMPT}]
        content.extend({'type': 'image_url', 'image_url': {
            'url': client._image_to_base64_url(image), 'detail': 'low'}} for image in images)
        response = await asyncio.wait_for(client.client.post(f'{client.api_url}/chat/completions', json={
            'model': client.model_name,
            'messages': [{'role': 'system', 'content': '只输出一个紧凑JSON对象。'},
                         {'role': 'user', 'content': content}],
            'temperature': 0, 'max_tokens': 300, 'chat_template_kwargs': {'enable_thinking': False},
        }), timeout=30)
        response.raise_for_status()
        raw = str(response.json()['choices'][0]['message']['content'] or '').strip()
        if raw.startswith('```') and raw.endswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        scene = validate_visibility_review(json.loads(raw), len(images), result['visual'])
        result['visual']['visibility_secondary_review'] = {**scene, 'success': True}
        if float(scene.get('confidence') or 0) >= .75:
            classification = scene.get('classification')
            if classification in {'external_body', 'trocar_transition'}:
                result['visual']['visibility'] = {
                    'status': 'out_of_body', 'out_of_body': True, 'fog': False,
                    'fog_cleared': False, 'confidence': scene['confidence'],
                    'evidence': scene.get('evidence', ''), 'evidence_source': 'visibility_secondary_review',
                }
                result['visual']['cvs'] = {'status': 'not_applicable', 'confidence': scene['confidence']}
                result['summary'] = reviewed_scene_summary(result['visual'])
    return result
