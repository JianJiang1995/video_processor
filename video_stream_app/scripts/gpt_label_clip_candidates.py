#!/usr/bin/env python3
"""Use a vision LLM to classify clip-detector candidate boxes.

Input is the bootstrap v1 metadata with candidate boxes. For each frame, this
script renders numbered candidate boxes on the full frame and asks Gemini/GPT to
classify each candidate into a small ontology. The result is exported as a
multi-class YOLO dataset for training the v2 clip expert.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


CLASS_NAMES = [
    "hemolok_clip",
    "titanium_clip",
    "clip_applier",
    "gauze",
    "specular_highlight",
    "other_instrument",
]

LABEL_TO_ID = {name: idx for idx, name in enumerate(CLASS_NAMES)}
NEGATIVE_LABELS = {"not_clip", "uncertain", "ignore", "none", ""}


@dataclass
class CandidateBox:
    index: int
    x1: float
    y1: float
    x2: float
    y2: float
    source: str = ""

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    def to_yolo(self, class_id: int, w: int, h: int) -> str:
        cx = (self.x1 + self.x2) / 2.0 / w
        cy = (self.y1 + self.y2) / 2.0 / h
        bw = self.width / w
        bh = self.height / h
        return f"{class_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    for path in (Path(".env"), Path("../.env"), Path("../../.env")):
        if path.exists():
            load_dotenv(path)


def _load_rows(metadata: Path, max_images: int = 0, require_boxes: bool = True) -> List[Dict[str, Any]]:
    rows = []
    with metadata.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if require_boxes and not row.get("boxes"):
                continue
            rows.append(row)
            if max_images and len(rows) >= max_images:
                break
    return rows


def _to_candidates(row: Dict[str, Any], max_boxes: int = 12) -> List[CandidateBox]:
    out: List[CandidateBox] = []
    for idx, box in enumerate(row.get("boxes") or [], start=1):
        try:
            x1, y1, x2, y2 = float(box["x1"]), float(box["y1"]), float(box["x2"]), float(box["y2"])
        except Exception:
            continue
        out.append(CandidateBox(idx, min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), str(box.get("source", ""))))
        if len(out) >= max_boxes:
            break
    return out


def _draw_numbered_boxes(image_path: Path, boxes: Sequence[CandidateBox], out_path: Path) -> Path:
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    colors = [
        (255, 80, 40),
        (40, 220, 255),
        (255, 220, 40),
        (120, 255, 120),
        (255, 80, 220),
        (200, 160, 255),
    ]
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    for box in boxes:
        color = colors[(box.index - 1) % len(colors)]
        xy = [box.x1, box.y1, box.x2, box.y2]
        draw.rectangle(xy, outline=color, width=4)
        label = str(box.index)
        tx, ty = int(box.x1), max(0, int(box.y1) - 22)
        draw.rectangle([tx, ty, tx + 26, ty + 22], fill=color)
        draw.text((tx + 6, ty + 1), label, fill=(0, 0, 0), font=font)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=92)
    return out_path


def _image_bytes(path: Path, max_width: int = 1280, quality: int = 86) -> bytes:
    img = Image.open(path).convert("RGB")
    w, h = img.size
    if w > max_width:
        ratio = max_width / float(w)
        img = img.resize((max_width, int(h * ratio)), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _image_data_url(path: Path, max_width: int = 1280, quality: int = 86) -> str:
    encoded = base64.b64encode(_image_bytes(path, max_width=max_width, quality=quality)).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def _prompt(row: Dict[str, Any], boxes: Sequence[CandidateBox]) -> str:
    candidate_lines = "\n".join(
        f"{box.index}. box=[{box.x1:.1f},{box.y1:.1f},{box.x2:.1f},{box.y2:.1f}] source={box.source}"
        for box in boxes
    )
    return f"""You are labeling laparoscopic cholecystectomy images for training a high-precision object detector.

The image has numbered candidate boxes. Classify EACH numbered candidate box using exactly one label:
- hemolok_clip: deployed Hem-o-lok / polymer locking clip body clamped on tissue. It is a distinct opaque plastic clip body with a locking/bar shape; usually thick, rounded, white/ivory/green/blue/purple. Do NOT use this for dark shadows, blue-looking tissue, blood, or a vague dark triangular area.
- titanium_clip: deployed metal titanium ligating clip body clamped on tissue. It is a distinct small thin silver/gray metallic clip, usually V/U/parallel short metal pieces with clear edges. Do NOT use this for wet glare, highlights, tissue streaks, or long instrument shafts.
- clip_applier: clip applier / clipper instrument jaw or shaft, including titanium clip applier, but NOT the released clip body itself.
- gauze: surgical gauze, sponge, bag-like fabric, or mesh.
- specular_highlight: glare, shiny tissue reflection, wet highlight, isolated bright spot, or metal glare without a true clip body.
- other_instrument: scissors, hook, grasper, suction/irrigator, forceps, or other instrument not covered above.
- not_clip: tissue, duct, vessel, fat, background, smoke, or anything that should not be trained as an object class.
- uncertain: genuinely unclear after inspecting the image.

Important rules:
0. Precision is more important than recall for this training set. If the candidate is not unmistakably one of the positive classes, choose not_clip, specular_highlight, or uncertain.
1. Do not infer from filename or timestamp.
2. Only label a Hem-o-lok or titanium clip if the clip BODY is visible, not merely an applier.
3. If a box covers both an applier and a deployed clip, choose the visible deployed clip only if the clip body is clearly inside the box; otherwise choose clip_applier.
4. White thick plastic-looking locked clip is hemolok_clip, not titanium_clip.
5. Thin silver-gray metal short clip pieces are titanium_clip.
6. If the box is too broad and mainly covers tissue/instrument, use not_clip or the dominant object class.
7. Dark tissue shadows, coagulated blood, blue/black shadows, wet folds, or vague triangular areas are not_clip unless a rigid clip shape is clearly visible.
8. Long gray/metallic shafts or jaws are clip_applier/other_instrument, not titanium_clip, unless a separate deployed short clip body is visible.

Candidate boxes:
{candidate_lines}

Return ONLY valid JSON in this exact schema:
{{
  "objects": [
    {{
      "index": 1,
      "label": "hemolok_clip|titanium_clip|clip_applier|gauze|specular_highlight|other_instrument|not_clip|uncertain",
      "confidence": 0.0,
      "reason": "brief visual reason"
    }}
  ]
}}
"""


def _extract_json(text: str) -> Dict[str, Any]:
    original = text or ""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model response")
    if "```" in text:
        parts = text.split("```")
        for part in parts:
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


def _gemini_response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return str(text)
    chunks: List[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                chunks.append(str(part_text))
    return "\n".join(chunks)


class GeminiAnnotator:
    def __init__(self, model_name: str, api_key_env: str, temperature: float, max_tokens: int):
        _load_dotenv()
        try:
            from google import genai
            from google.genai import types
        except Exception as exc:
            raise RuntimeError("google-genai is required for GPT/Gemini annotation") from exc
        api_key = os.environ.get(api_key_env) or os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(f"Missing API key in {api_key_env}/GEMINI_API_KEY/OPENAI_API_KEY")
        self.genai = genai
        self.types = types
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens

    def annotate(self, image_path: Path, prompt: str) -> Dict[str, Any]:
        img = _image_bytes(image_path)
        config = self.types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
            response_mime_type="application/json",
        )
        try:
            config.thinking_config = self.types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=[
                self.types.Part.from_text(text=prompt),
                self.types.Part.from_bytes(data=img, mime_type="image/jpeg"),
            ],
            config=config,
        )
        return _extract_json(_gemini_response_text(response))


class OpenAIAnnotator:
    def __init__(self, model_name: str, api_key_env: str, temperature: float, max_tokens: int, base_url: str = ""):
        _load_dotenv()
        try:
            from openai import OpenAI
        except Exception as exc:
            raise RuntimeError("openai package is required for OpenAI annotation") from exc
        api_key = os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(f"Missing API key in {api_key_env}/OPENAI_API_KEY")
        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        resolved_base_url = base_url or os.environ.get("OPENAI_BASE_URL", "")
        if resolved_base_url:
            client_kwargs["base_url"] = resolved_base_url.rstrip("/")
        self.client = OpenAI(**client_kwargs)
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens

    def annotate(self, image_path: Path, prompt: str) -> Dict[str, Any]:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "system",
                    "content": "You are a surgical image labeling assistant. Return only one valid JSON object.",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _image_data_url(image_path), "detail": "high"}},
                    ],
                },
            ],
            response_format={"type": "json_object"},
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        text = response.choices[0].message.content or ""
        return _extract_json(text)


def _make_annotator(args: argparse.Namespace) -> Any:
    provider = str(args.provider).lower()
    if provider == "gemini":
        return GeminiAnnotator(
            model_name=args.model,
            api_key_env=args.api_key_env,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
    if provider == "openai":
        return OpenAIAnnotator(
            model_name=args.model,
            api_key_env=args.api_key_env,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            base_url=args.base_url,
        )
    raise ValueError(f"Unsupported provider: {args.provider}")


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
        "highlight": "specular_highlight",
        "glare": "specular_highlight",
        "instrument": "other_instrument",
    }
    return aliases.get(label, label)


def _validate_annotation(data: Dict[str, Any], boxes: Sequence[CandidateBox]) -> List[Dict[str, Any]]:
    by_index = {box.index: box for box in boxes}
    cleaned: List[Dict[str, Any]] = []
    for item in data.get("objects") or []:
        try:
            idx = int(item.get("index"))
        except Exception:
            continue
        if idx not in by_index:
            continue
        label = _normalize_label(item.get("label"))
        if label not in LABEL_TO_ID and label not in NEGATIVE_LABELS:
            label = "uncertain"
        try:
            conf = float(item.get("confidence", 0.0))
        except Exception:
            conf = 0.0
        cleaned.append(
            {
                "index": idx,
                "label": label,
                "confidence": max(0.0, min(1.0, conf)),
                "reason": str(item.get("reason", ""))[:300],
            }
        )
    seen = {item["index"] for item in cleaned}
    for box in boxes:
        if box.index not in seen:
            cleaned.append({"index": box.index, "label": "uncertain", "confidence": 0.0, "reason": "missing from model output"})
    cleaned.sort(key=lambda item: int(item["index"]))
    return cleaned


async def annotate_dataset(args: argparse.Namespace) -> None:
    metadata = Path(args.metadata)
    output = Path(args.output)
    if output.exists() and args.clean:
        shutil.rmtree(output)
    for split in ("train", "val"):
        (output / "images" / split).mkdir(parents=True, exist_ok=True)
        (output / "labels" / split).mkdir(parents=True, exist_ok=True)
    (output / "annotations").mkdir(parents=True, exist_ok=True)
    (output / "debug").mkdir(parents=True, exist_ok=True)
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

    annotator = _make_annotator(args)
    rows = _load_rows(metadata, max_images=args.max_images, require_boxes=True)
    results_path = output / "annotations.jsonl"
    counts: Dict[str, int] = {name: 0 for name in CLASS_NAMES + ["not_clip", "uncertain"]}

    with results_path.open("a", encoding="utf-8") as out_jsonl:
        for row_idx, row in enumerate(rows, start=1):
            image_path = Path(row["image"])
            if not image_path.exists():
                continue
            boxes = _to_candidates(row, max_boxes=args.max_boxes_per_image)
            if not boxes:
                continue
            stem = image_path.stem
            ann_path = output / "annotations" / f"{stem}.json"
            overlay_path = output / "debug" / f"{stem}_prompt.jpg"
            if args.resume and ann_path.exists():
                stored = json.loads(ann_path.read_text(encoding="utf-8"))
                cleaned = stored.get("objects") or []
            else:
                _draw_numbered_boxes(image_path, boxes, overlay_path)
                prompt = _prompt(row, boxes)
                last_error: Optional[Exception] = None
                cleaned = []
                for attempt in range(args.retries + 1):
                    try:
                        data = await asyncio.to_thread(annotator.annotate, overlay_path, prompt)
                        cleaned = _validate_annotation(data, boxes)
                        break
                    except Exception as exc:
                        last_error = exc
                        await asyncio.sleep(min(8.0, 1.5 * (attempt + 1)))
                if not cleaned:
                    raise RuntimeError(f"annotation failed for {image_path}: {last_error}")
                ann_path.write_text(
                    json.dumps(
                        {
                            "image": str(image_path),
                            "split": row.get("split", "train"),
                            "width": row.get("width"),
                            "height": row.get("height"),
                            "boxes": [box.__dict__ for box in boxes],
                            "objects": cleaned,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

            split = str(row.get("split") or "train")
            target_image = output / "images" / split / image_path.name
            target_label = output / "labels" / split / f"{image_path.stem}.txt"
            if not target_image.exists():
                shutil.copy2(image_path, target_image)
            img = Image.open(image_path)
            width, height = img.size
            by_index = {box.index: box for box in boxes}
            label_lines: List[str] = []
            for obj in cleaned:
                label = _normalize_label(obj.get("label"))
                counts[label if label in counts else "uncertain"] = counts.get(label if label in counts else "uncertain", 0) + 1
                if label not in LABEL_TO_ID:
                    continue
                if float(obj.get("confidence") or 0.0) < args.min_positive_confidence:
                    continue
                box = by_index.get(int(obj.get("index")))
                if box is None:
                    continue
                label_lines.append(box.to_yolo(LABEL_TO_ID[label], width, height))
            target_label.write_text("\n".join(label_lines) + ("\n" if label_lines else ""), encoding="utf-8")
            out_jsonl.write(
                json.dumps(
                    {
                        "source_image": str(image_path),
                        "dataset_image": str(target_image),
                        "dataset_label": str(target_label),
                        "split": split,
                        "objects": cleaned,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            out_jsonl.flush()
            print(
                f"[gpt-label] {row_idx}/{len(rows)} {image_path.name} boxes={len(boxes)} positives={len(label_lines)}",
                flush=True,
            )

    print("[gpt-label] counts", json.dumps(counts, ensure_ascii=False, indent=2))
    print(f"[gpt-label] wrote {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", default="datasets/clip_detector_v1/metadata.jsonl")
    parser.add_argument("--output", default="datasets/clip_detector_gpt_v2")
    parser.add_argument("--provider", choices=["openai", "gemini"], default="openai")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--max-images", type=int, default=0)
    parser.add_argument("--max-boxes-per-image", type=int, default=12)
    parser.add_argument("--min-positive-confidence", type=float, default=0.55)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    asyncio.run(annotate_dataset(args))


if __name__ == "__main__":
    main()
