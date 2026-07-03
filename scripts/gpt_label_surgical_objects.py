#!/usr/bin/env python3
"""Ask a vision LLM to annotate surgical objects directly on sampled frames."""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import shutil
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import cv2
from PIL import Image


CLASS_NAMES = [
    "hemolok_clip",
    "titanium_clip",
    "clip_applier",
    "gauze",
    "active_bleeding",
    "specular_highlight",
    "other_instrument",
]
LABEL_TO_ID = {name: idx for idx, name in enumerate(CLASS_NAMES)}


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    for path in (Path(".env"), Path("../.env"), Path("../../.env")):
        if path.exists():
            load_dotenv(path)


def _extract_json(text: str) -> Dict[str, Any]:
    original = text or ""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model response")
    if "```" in text:
        for part in text.split("```"):
            cand = part.strip()
            if cand.startswith("json"):
                cand = cand[4:].strip()
            if cand.startswith("{"):
                text = cand
                break
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        text = match.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = original.replace("\n", " ")[:500]
        raise ValueError(f"invalid JSON response: {exc}; response={snippet!r}") from exc


def _pil_to_data_url(image: Image.Image, max_width: int = 1280, quality: int = 86) -> str:
    image = image.convert("RGB")
    w, h = image.size
    if w > max_width:
        ratio = max_width / float(w)
        image = image.resize((max_width, int(h * ratio)), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def _prompt() -> str:
    return """You are creating object-detection training labels for laparoscopic cholecystectomy frames.

Find visible objects from this fixed ontology:
- hemolok_clip: deployed plastic Hem-o-lok locking clip body, usually opaque white/ivory/green/blue/purple, thick and rounded with a lock/bar shape.
- titanium_clip: deployed metal titanium ligating clip body, small short silver/gray metallic V/U/parallel clip pieces.
- clip_applier: clip applier instrument jaw or shaft, including a titanium clip applier before releasing the clip.
- gauze: surgical gauze, sponge, fabric, mesh, or bag-like material.
- active_bleeding: active bleeding region, visible fresh flowing/pooling blood that is clinically meaningful. Do not label old stains or minor dots.
- specular_highlight: isolated glare/highlight that might be confused with a clip.
- other_instrument: scissors, hook, grasper, forceps, suction, or other visible tool not covered above.

High-precision rules:
1. Return no object for tissue, fat, ducts, vessels, smoke, dark shadows, vague shapes, and old blood stains.
2. Label hemolok_clip or titanium_clip only when the deployed clip BODY is visible. Do not confuse long jaws/shafts with a deployed clip.
3. White/ivory thick plastic clip body is hemolok_clip. Thin silver/gray short metallic clip body is titanium_clip.
4. If a frame contains no clear ontology object, return an empty objects list.
5. Use tight boxes around only the object body. Coordinates must be normalized integers in the original image coordinate system from 0 to 1000: [x1,y1,x2,y2].
6. Each confidence must be between 0 and 1. Be conservative; use confidence below 0.55 for uncertain objects.

Return ONLY valid JSON:
{
  "objects": [
    {
      "label": "hemolok_clip|titanium_clip|clip_applier|gauze|active_bleeding|specular_highlight|other_instrument",
      "box_1000": [0, 0, 1000, 1000],
      "confidence": 0.0,
      "reason": "brief visual reason"
    }
  ]
}
"""


class OpenAIFrameAnnotator:
    def __init__(self, model_name: str, api_key_env: str, temperature: float, max_tokens: int, base_url: str = ""):
        _load_dotenv()
        try:
            from openai import OpenAI
        except Exception as exc:
            raise RuntimeError("openai package is required for OpenAI annotation") from exc
        api_key = os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(f"Missing API key in {api_key_env}/OPENAI_API_KEY")
        kwargs: Dict[str, Any] = {"api_key": api_key}
        resolved_base_url = base_url or os.environ.get("OPENAI_BASE_URL", "")
        if resolved_base_url:
            kwargs["base_url"] = resolved_base_url.rstrip("/")
        self.client = OpenAI(**kwargs)
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens

    def annotate(self, image: Image.Image) -> Dict[str, Any]:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "system",
                    "content": "You are a conservative surgical object annotator. Return only one valid JSON object.",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _prompt()},
                        {"type": "image_url", "image_url": {"url": _pil_to_data_url(image), "detail": "high"}},
                    ],
                },
            ],
            response_format={"type": "json_object"},
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return _extract_json(response.choices[0].message.content or "")


def _video_duration(video_path: Path) -> Tuple[float, float, int]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    duration = frames / fps if fps > 0 else 0.0
    return fps, duration, frames


def _sample_times(video_path: Path, interval_sec: float, max_frames: int, start_sec: float, end_sec: float) -> List[float]:
    _, duration, _ = _video_duration(video_path)
    start = max(0.0, start_sec)
    end = min(duration, end_sec if end_sec > 0 else duration)
    if end <= start:
        return []
    times = []
    t = start
    while t <= end:
        times.append(t)
        t += max(0.1, interval_sec)
    if max_frames and len(times) > max_frames:
        if max_frames == 1:
            return [times[len(times) // 2]]
        step = (len(times) - 1) / float(max_frames - 1)
        return [times[round(i * step)] for i in range(max_frames)]
    return times


def _read_frame(video_path: Path, sec: float) -> Image.Image:
    cap = cv2.VideoCapture(str(video_path))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_idx = int(round(sec * fps)) if fps > 0 else 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_idx))
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise RuntimeError(f"Cannot read frame {sec:.2f}s from {video_path}")
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def _normalize_label(label: Any) -> str:
    label = str(label or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "hem_o_lok_clip": "hemolok_clip",
        "hemolock_clip": "hemolok_clip",
        "hemlok_clip": "hemolok_clip",
        "polymer_clip": "hemolok_clip",
        "metal_clip": "titanium_clip",
        "titanium": "titanium_clip",
        "applier": "clip_applier",
        "clipper": "clip_applier",
        "bleeding": "active_bleeding",
        "blood": "active_bleeding",
        "highlight": "specular_highlight",
        "glare": "specular_highlight",
        "instrument": "other_instrument",
    }
    return aliases.get(label, label)


def _validate_objects(data: Dict[str, Any], min_conf: float) -> List[Dict[str, Any]]:
    objects = []
    for obj in data.get("objects") or []:
        label = _normalize_label(obj.get("label"))
        if label not in LABEL_TO_ID:
            continue
        try:
            conf = float(obj.get("confidence", 0.0))
        except Exception:
            conf = 0.0
        if conf < min_conf:
            continue
        box = obj.get("box_1000") or obj.get("bbox") or obj.get("box")
        if not isinstance(box, list) or len(box) != 4:
            continue
        try:
            x1, y1, x2, y2 = [float(v) for v in box]
        except Exception:
            continue
        x1, x2 = sorted([max(0.0, min(1000.0, x1)), max(0.0, min(1000.0, x2))])
        y1, y2 = sorted([max(0.0, min(1000.0, y1)), max(0.0, min(1000.0, y2))])
        if (x2 - x1) < 4 or (y2 - y1) < 4:
            continue
        objects.append(
            {
                "label": label,
                "confidence": max(0.0, min(1.0, conf)),
                "box_1000": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                "reason": str(obj.get("reason", ""))[:300],
            }
        )
    return objects


def _to_yolo_line(obj: Dict[str, Any]) -> str:
    x1, y1, x2, y2 = obj["box_1000"]
    cx = ((x1 + x2) / 2.0) / 1000.0
    cy = ((y1 + y2) / 2.0) / 1000.0
    bw = (x2 - x1) / 1000.0
    bh = (y2 - y1) / 1000.0
    return f"{LABEL_TO_ID[obj['label']]} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def _safe_stem(video_path: Path, sec: float) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", video_path.with_suffix("").as_posix().strip("/"))
    return f"{stem}_{sec:08.2f}s".replace(".", "p")


def annotate(args: argparse.Namespace) -> None:
    output = Path(args.output)
    if output.exists() and args.clean:
        shutil.rmtree(output)
    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)
    (output / "annotations").mkdir(parents=True, exist_ok=True)
    (output / "data.yaml").write_text(
        "\n".join(
            [
                f"path: {output.resolve()}",
                "train: images/train",
                "val: images/val",
                "names:",
                *[f"  {idx}: {name}" for idx, name in enumerate(CLASS_NAMES)],
                "",
            ]
        ),
        encoding="utf-8",
    )

    videos = [Path(v) for v in args.videos]
    samples: List[Tuple[Path, float]] = []
    for video in videos:
        times = _sample_times(video, args.interval_sec, args.max_frames_per_video, args.start_sec, args.end_sec)
        samples.extend((video, sec) for sec in times)
    if args.shuffle:
        random.Random(args.seed).shuffle(samples)
    if args.max_total_frames and len(samples) > args.max_total_frames:
        samples = samples[: args.max_total_frames]

    annotator = OpenAIFrameAnnotator(
        model_name=args.model,
        api_key_env=args.api_key_env,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        base_url=args.base_url,
    )
    counts = {name: 0 for name in CLASS_NAMES}
    empty = 0
    results_path = output / "annotations.jsonl"
    with results_path.open("a", encoding="utf-8") as out_jsonl:
        for idx, (video, sec) in enumerate(samples, start=1):
            stem = _safe_stem(video, sec)
            split = "val" if idx % args.val_every == 0 else "train"
            ann_path = output / "annotations" / f"{stem}.json"
            image_path = output / "images" / split / f"{stem}.jpg"
            label_path = output / "labels" / split / f"{stem}.txt"
            if args.resume and ann_path.exists():
                ann = json.loads(ann_path.read_text(encoding="utf-8"))
                objects = ann.get("objects") or []
                if not image_path.exists():
                    image = _read_frame(video, sec)
                    image.save(image_path, quality=92)
            else:
                image = _read_frame(video, sec)
                last_error: Exception | None = None
                objects: List[Dict[str, Any]] = []
                for attempt in range(args.retries + 1):
                    try:
                        objects = _validate_objects(annotator.annotate(image), args.min_confidence)
                        break
                    except Exception as exc:
                        last_error = exc
                        time.sleep(min(8.0, 1.5 * (attempt + 1)))
                if last_error and not objects:
                    print(f"[gpt-fullframe] warning empty after error {video.name}@{sec:.1f}s: {last_error}", flush=True)
                image.save(image_path, quality=92)
                ann = {"video": str(video), "time_sec": sec, "split": split, "objects": objects}
                ann_path.write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")

            lines = []
            for obj in objects:
                counts[obj["label"]] += 1
                lines.append(_to_yolo_line(obj))
            if not lines:
                empty += 1
            label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            out_jsonl.write(
                json.dumps(
                    {
                        "video": str(video),
                        "time_sec": sec,
                        "image": str(image_path),
                        "label": str(label_path),
                        "split": split,
                        "objects": objects,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            out_jsonl.flush()
            print(f"[gpt-fullframe] {idx}/{len(samples)} {video.name}@{sec:.1f}s objects={len(objects)}", flush=True)

    print("[gpt-fullframe] counts", json.dumps(counts, ensure_ascii=False, indent=2))
    print(f"[gpt-fullframe] empty_images={empty}/{len(samples)}")
    print(f"[gpt-fullframe] wrote {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--videos", nargs="+", required=True)
    parser.add_argument("--output", default="datasets/surgical_objects_gpt_v3")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=1000)
    parser.add_argument("--interval-sec", type=float, default=12.0)
    parser.add_argument("--max-frames-per-video", type=int, default=30)
    parser.add_argument("--max-total-frames", type=int, default=0)
    parser.add_argument("--start-sec", type=float, default=0.0)
    parser.add_argument("--end-sec", type=float, default=0.0)
    parser.add_argument("--min-confidence", type=float, default=0.62)
    parser.add_argument("--val-every", type=int, default=5)
    parser.add_argument("--shuffle", action="store_true")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    annotate(args)


if __name__ == "__main__":
    main()
