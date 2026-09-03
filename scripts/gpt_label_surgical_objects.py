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
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
from PIL import Image, ImageDraw, ImageFont


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


def _prompt(prompt_profile: str = "default") -> str:
    if prompt_profile == "dual-clip-strict":
        return """You are creating high-precision object-detection training labels for DEPLOYED clip bodies in laparoscopic cholecystectomy frames.

Primary targets:
- hemolok_clip: a deployed white/ivory opaque plastic Hem-o-lok locking clip BODY. It is thick, non-metallic, usually C/U/V-shaped or bar-like, with a locking hinge/notch, attached across a cystic duct or vessel.
- titanium_clip: a deployed metal titanium ligating clip BODY. It is small, short, silver/gray metallic, often V/U/staple-like or two short parallel prongs on a duct/vessel. It is much shorter than instrument jaws.

Allowed distractors:
- specular_highlight: wet glare, saturated white reflection, or shiny tissue/instrument highlight that might be confused with a clip.
- clip_applier: clip applier jaws or shaft, including jaws holding a clip before release. This is NOT the deployed clip body.
- other_instrument: scissors, hook, grasper, forceps, suction, trocar, or any non-clip object.

Strict labeling rules:
1. Label only released/deployed clip BODY objects. Do not label clip applier jaws, grasper teeth, scissors tips, electrocautery hooks, shafts, sutures, ducts, vessels, tissue folds, fat strands, smoke, or blood.
2. Hem-o-lok must be white/ivory opaque plastic and visibly thick/locking. Do not label blue, green, purple, transparent, or uncertain polymer clips as hemolok_clip.
3. Titanium clip must be a short silver/gray metallic clip body with rigid clip morphology. Do not label a long bright line, tool edge, jaw tip, or wet highlight as titanium_clip.
4. A bright spot is titanium_clip only if the short rigid V/U/parallel-prong clip shape is visible; otherwise label it specular_highlight or omit it.
5. Boxes must be tight around only the clip body. Do not include surrounding tissue, duct/vessel, instrument jaws, or large background.
6. If the clip type is uncertain between hemolok_clip and titanium_clip, omit the object or use confidence below 0.55.
7. Prefer high precision over recall. It is better to return no clip than to label a false clip.

Coordinates must be normalized integers in the original image coordinate system from 0 to 1000: [x1,y1,x2,y2].

Return ONLY valid JSON:
{
  "objects": [
    {
      "label": "hemolok_clip|titanium_clip|specular_highlight|clip_applier|other_instrument",
      "box_1000": [0, 0, 1000, 1000],
      "confidence": 0.0,
      "reason": "brief visual reason"
    }
  ]
}
"""
    if prompt_profile == "titanium-focused":
        return """You are creating high-precision object-detection training labels for deployed METAL titanium ligating clips in laparoscopic cholecystectomy frames.

Primary target:
- titanium_clip: a deployed metal titanium ligating clip BODY. It is a small, short, silver/gray metallic clip on a duct/vessel, often V/U/parallel-pronged or staple-like. It is much shorter than instrument jaws.

Allowed distractor labels:
- specular_highlight: wet glare or isolated highlight that might be confused with a titanium clip.
- clip_applier: clip applier jaws or shaft. These are long/large instrument parts and are NOT the deployed clip body.
- other_instrument: scissors, hook, grasper, forceps, suction, or any non-applier tool.
- hemolok_clip: only if a deployed white/ivory opaque plastic locking Hem-o-lok clip body is unambiguous. Do not label blue/green/purple polymer clips.

Titanium-specific rules:
1. Label titanium_clip only when the released/deployed metal clip body itself is visible.
2. The titanium clip box must tightly surround only the short metal clip body, not the duct/vessel/tissue/instrument.
3. Do not label long applier jaws, grasper teeth, scissor tips, shafts, or electrocautery tips as titanium_clip.
4. Do not label wet white glare or saturated highlights as titanium_clip unless a rigid short V/U/parallel metal clip shape is clearly visible.
5. Do not label sutures, vessel edges, fat strands, tissue folds, or smoke as titanium_clip.
6. If a possible titanium clip is mostly hidden or ambiguous, use confidence below 0.55 or omit it.
7. If the frame has no clear deployed titanium clip body, return an empty objects list or label only clear distractors.

Coordinates must be normalized integers in the original image coordinate system from 0 to 1000: [x1,y1,x2,y2].

Return ONLY valid JSON:
{
  "objects": [
    {
      "label": "titanium_clip|specular_highlight|clip_applier|other_instrument|hemolok_clip",
      "box_1000": [0, 0, 1000, 1000],
      "confidence": 0.0,
      "reason": "brief visual reason"
    }
  ]
}
"""
    if prompt_profile == "clip-focused":
        return """You are creating high-precision object-detection training labels for deployed surgical clips in laparoscopic cholecystectomy frames.

Find only visible deployed clip BODIES from this ontology:
- hemolok_clip: deployed white/ivory opaque Hem-o-lok locking clip body. It is a thick plastic locking clip with a rigid bar/lock shape, often C/V/U shaped or a short white locking segment on a duct or vessel.
- titanium_clip: deployed metal titanium ligating clip body. It is a small short silver/gray metallic clip, often V/U shaped, parallel-pronged, or a rigid short bright metal piece on a duct or vessel.
- specular_highlight: isolated glare/highlight that might be confused with a clip.

Do not label ordinary instruments, tissue, fat, ducts, vessels, smoke, blood stains, gauze, shadows, or long applier jaws/shafts.

Clip-specific rules:
1. Label a clip only when the released/deployed clip body itself is visible.
2. White/ivory thick plastic locking clip body -> hemolok_clip.
3. Thin silver/gray short metallic deployed clip body -> titanium_clip.
4. Do not label blue, purple, green, or unknown-color polymer clips as hemolok_clip in this dataset.
5. Do not label clip applier jaws or instrument tips as titanium_clip.
6. Do not label bright wet reflections as titanium_clip unless the rigid short metallic clip shape is clearly visible.
7. Use tight boxes around only the deployed clip body, not the duct, vessel, surrounding tissue, or instrument jaw.
8. If a possible clip is too occluded or ambiguous, use confidence below 0.55 or omit it.
9. If the frame has no clear deployed clip body, return an empty objects list.

Coordinates must be normalized integers in the original image coordinate system from 0 to 1000: [x1,y1,x2,y2].

Return ONLY valid JSON:
{
  "objects": [
    {
      "label": "hemolok_clip|titanium_clip|specular_highlight",
      "box_1000": [0, 0, 1000, 1000],
      "confidence": 0.0,
      "reason": "brief visual reason"
    }
  ]
}
"""
    return """You are creating high-precision object-detection training labels for laparoscopic cholecystectomy frames.

Find visible objects from this fixed ontology:
- hemolok_clip: deployed white/ivory opaque Hem-o-lok locking clip body, thick and rounded with a lock/bar shape.
- titanium_clip: deployed metal titanium ligating clip body, small short silver/gray metallic V/U/parallel clip pieces.
- clip_applier: clip applier instrument jaw or shaft, including a titanium clip applier before releasing the clip.
- gauze: surgical gauze, sponge, fabric, mesh, or bag-like material.
- active_bleeding: active bleeding region, visible fresh flowing/pooling blood that is clinically meaningful. Do not label old stains or minor dots.
- specular_highlight: isolated glare/highlight that might be confused with a clip.
- other_instrument: scissors, hook, grasper, forceps, suction, or other visible tool not covered above.

High-precision rules:
1. Return no object for tissue, fat, ducts, vessels, smoke, dark shadows, vague shapes, and old blood stains.
2. Label hemolok_clip or titanium_clip only when the deployed clip BODY is visible. Do not confuse long jaws/shafts with a deployed clip.
3. White/ivory thick plastic locking clip body is hemolok_clip. Thin silver/gray short metallic clip body is titanium_clip.
4. If a frame contains no clear ontology object, return an empty objects list.
5. Use tight boxes around only the object body. Coordinates must be normalized integers in the original image coordinate system from 0 to 1000: [x1,y1,x2,y2].
6. Each confidence must be between 0 and 1. Be conservative; use confidence below 0.55 for uncertain objects.
7. For training data quality, false positives are worse than missing a hard case. If unsure, omit the object.
8. Do not label a clip applier as a titanium clip. Clip applier jaws/shafts are much larger and longer than the released clip body.
9. Do not label a bright wet reflection as titanium_clip unless a rigid short metallic clip shape is clearly visible.
10. Do not merge colored polymer clips into hemolok_clip for this dataset. Blue, purple, green, or unknown-color polymer clips should be omitted unless the task explicitly adds a separate polymer_clip class.

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


def _annotation_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "objects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "label": {"type": "string", "enum": CLASS_NAMES},
                        "box_1000": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 4,
                            "maxItems": 4,
                        },
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["label", "box_1000", "confidence", "reason"],
                },
            }
        },
        "required": ["objects"],
    }


def _verification_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "objects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "source_index": {"type": "integer"},
                        "decision": {"type": "string", "enum": ["keep", "relabel", "reject"]},
                        "label": {"type": "string", "enum": CLASS_NAMES},
                        "box_1000": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 4,
                            "maxItems": 4,
                        },
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["source_index", "decision", "label", "box_1000", "confidence", "reason"],
                },
            }
        },
        "required": ["objects"],
    }


class OpenAIFrameAnnotator:
    def __init__(
        self,
        model_name: str,
        api_key_env: str,
        temperature: float,
        max_tokens: int,
        base_url: str = "",
        prompt_profile: str = "default",
    ):
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
        self.prompt_profile = prompt_profile

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
                        {"type": "text", "text": _prompt(self.prompt_profile)},
                        {"type": "image_url", "image_url": {"url": _pil_to_data_url(image), "detail": "high"}},
                    ],
                },
            ],
            response_format={"type": "json_object"},
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return _extract_json(response.choices[0].message.content or "")


class OpenAIResponsesFrameAnnotator:
    """Responses API annotator for GPT-5.x vision models."""

    def __init__(
        self,
        model_name: str,
        api_key_env: str,
        temperature: Optional[float],
        max_tokens: int,
        base_url: str = "",
        reasoning_effort: str = "none",
        image_detail: str = "high",
        verifier_model_name: str = "",
        prompt_profile: str = "default",
    ):
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
        self.verifier_model_name = verifier_model_name or model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.image_detail = image_detail
        self.prompt_profile = prompt_profile

    def _create(self, *, model: str, prompt: str, image: Image.Image, schema_name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        request: Dict[str, Any] = {
            "model": model,
            "instructions": "You are a conservative surgical image labeling assistant. Return only valid JSON.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": _pil_to_data_url(image),
                            "detail": self.image_detail,
                        },
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                    "strict": True,
                }
            },
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": self.max_tokens,
            "store": False,
        }
        if self.temperature is not None:
            request["temperature"] = self.temperature
        response = self.client.responses.create(**request)
        return _extract_json(getattr(response, "output_text", "") or "")

    def annotate(self, image: Image.Image) -> Dict[str, Any]:
        return self._create(
            model=self.model_name,
            prompt=_prompt(self.prompt_profile),
            image=image,
            schema_name="surgical_object_annotations",
            schema=_annotation_schema(),
        )

    def verify(self, image: Image.Image, objects: List[Dict[str, Any]]) -> Dict[str, Any]:
        overlay = _draw_objects_overlay(image, objects)
        proposed = []
        for idx, obj in enumerate(objects, start=1):
            proposed.append(
                {
                    "source_index": idx,
                    "label": obj.get("label"),
                    "box_1000": obj.get("box_1000"),
                    "confidence": obj.get("confidence"),
                    "reason": obj.get("reason", ""),
                }
            )
        if self.prompt_profile in {"clip-focused", "titanium-focused", "dual-clip-strict"}:
            rules = """1. Keep only deployed clip BODY boxes that are good enough for detector training.
2. Reject tissue, fat, ducts, vessels, old blood stains, shadows, smoke, glare, ordinary instruments, and long applier jaws/shafts.
3. Hem-o-lok must be a visible deployed white/ivory plastic locking clip body. Reject blue/green/purple polymer clips for this dataset.
4. Titanium clip must be a small short silver/gray metallic deployed clip body.
5. If the proposed box is a reflection, use specular_highlight only when the box clearly marks glare; otherwise reject.
6. If the box is too broad, tighten it around only the clip body if possible; otherwise reject it.
7. If unsure, reject."""
        else:
            rules = """1. Keep only boxes that are good enough for detector training.
2. Reject tissue, fat, ducts, vessels, old blood stains, shadows, smoke, glare, and ordinary instruments mislabeled as clips.
3. Hem-o-lok must be a visible deployed white/ivory plastic locking clip body. Reject blue/green/purple polymer clips for this dataset.
4. Titanium clip must be a small short silver/gray metallic deployed clip body. Long instrument jaws/shafts are not titanium clips.
5. Clip applier must be the applier jaw or shaft, not the released clip body.
6. For active_bleeding, keep only meaningful fresh flowing/pooling/spraying blood.
7. If the label is wrong but the box contains another ontology object, use decision relabel and correct the label.
8. If the box is too broad, tighten it if possible; otherwise reject it.
9. If unsure, reject."""
        prompt = f"""Review proposed surgical object labels for training data.

The image contains numbered boxes corresponding to the proposed objects below.

Proposed objects:
{json.dumps(proposed, ensure_ascii=False, indent=2)}

Verification rules:
{rules}

Return one verification object for every proposed source_index. Do not add new boxes in this review pass."""
        return self._create(
            model=self.verifier_model_name,
            prompt=prompt,
            image=overlay,
            schema_name="surgical_object_verification",
            schema=_verification_schema(),
        )


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


def _validate_verified_objects(data: Dict[str, Any], source_objects: List[Dict[str, Any]], min_conf: float) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    by_source = {idx: obj for idx, obj in enumerate(source_objects, start=1)}
    kept: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    seen = set()
    for obj in data.get("objects") or []:
        try:
            source_index = int(obj.get("source_index"))
        except Exception:
            continue
        src = by_source.get(source_index)
        if src is None:
            continue
        seen.add(source_index)
        decision = str(obj.get("decision") or "reject").strip().lower()
        label = _normalize_label(obj.get("label") or src.get("label"))
        try:
            conf = float(obj.get("confidence", 0.0))
        except Exception:
            conf = 0.0
        candidate = {
            "label": label if label in LABEL_TO_ID else src.get("label"),
            "confidence": max(0.0, min(1.0, conf)),
            "box_1000": obj.get("box_1000") or src.get("box_1000"),
            "reason": str(obj.get("reason", ""))[:300],
            "source_index": source_index,
            "verification_decision": decision,
            "initial": src,
        }
        valid = _validate_objects({"objects": [candidate]}, min_conf)
        if decision in {"keep", "relabel"} and valid:
            kept.append(valid[0])
        else:
            rejected.append(candidate)
    for source_index, src in by_source.items():
        if source_index not in seen:
            rejected.append(
                {
                    "source_index": source_index,
                    "verification_decision": "reject",
                    "reason": "missing from verifier output",
                    "initial": src,
                }
            )
    return kept, rejected


def _auto_quality_filter(objects: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Remove boxes that are structurally unsafe for detector training."""
    kept: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for obj in objects:
        x1, y1, x2, y2 = [float(v) for v in obj["box_1000"]]
        bw = max(0.0, x2 - x1)
        bh = max(0.0, y2 - y1)
        area = (bw * bh) / 1_000_000.0
        label = obj["label"]
        reason = ""
        if bw < 4 or bh < 4:
            reason = "box too small"
        elif label in {"hemolok_clip", "titanium_clip"} and area > 0.045:
            reason = "clip body box too large"
        elif label == "titanium_clip" and max(bw / max(hb := bh, 1.0), bh / max(bw, 1.0)) > 12:
            reason = "titanium box too elongated"
        elif label == "hemolok_clip" and max(bw / max(hb := bh, 1.0), bh / max(bw, 1.0)) > 8:
            reason = "Hem-o-lok box too elongated"
        elif label == "active_bleeding" and area < 0.0008:
            reason = "bleeding region too small"
        if reason:
            bad = dict(obj)
            bad["auto_reject_reason"] = reason
            rejected.append(bad)
        else:
            kept.append(obj)
    return kept, rejected


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


def _draw_objects_overlay(image: Image.Image, objects: Sequence[Dict[str, Any]]) -> Image.Image:
    overlay = image.convert("RGB").copy()
    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    w, h = overlay.size
    colors = {
        "hemolok_clip": (255, 80, 80),
        "titanium_clip": (40, 210, 255),
        "clip_applier": (255, 210, 40),
        "gauze": (80, 255, 120),
        "active_bleeding": (255, 0, 0),
        "specular_highlight": (180, 100, 255),
        "other_instrument": (170, 170, 255),
    }
    for idx, obj in enumerate(objects, start=1):
        label = _normalize_label(obj.get("label"))
        x1, y1, x2, y2 = obj.get("box_1000") or [0, 0, 0, 0]
        px1, py1 = int(float(x1) / 1000.0 * w), int(float(y1) / 1000.0 * h)
        px2, py2 = int(float(x2) / 1000.0 * w), int(float(y2) / 1000.0 * h)
        color = colors.get(label, (255, 255, 255))
        draw.rectangle([px1, py1, px2, py2], outline=color, width=4)
        text = f"{idx}:{label}"
        bbox = draw.textbbox((px1, max(0, py1 - 24)), text, font=font)
        draw.rectangle(bbox, fill=color)
        draw.text((px1, max(0, py1 - 24)), text, fill=(0, 0, 0), font=font)
    return overlay


def _write_audit_sheets(output: Path, max_per_class: int = 40, thumb_width: int = 360) -> None:
    audit_dir = output / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    per_class: Dict[str, List[Tuple[Path, Dict[str, Any]]]] = {name: [] for name in CLASS_NAMES}
    for ann_path in sorted((output / "annotations").glob("*.json")):
        try:
            ann = json.loads(ann_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        split = ann.get("split") or "train"
        image_path = output / "images" / split / f"{ann_path.stem}.jpg"
        if not image_path.exists():
            continue
        for obj in ann.get("objects") or []:
            label = _normalize_label(obj.get("label"))
            if label in per_class and len(per_class[label]) < max_per_class:
                per_class[label].append((image_path, obj))

    for label, items in per_class.items():
        if not items:
            continue
        thumbs: List[Image.Image] = []
        for image_path, obj in items:
            img = Image.open(image_path).convert("RGB")
            img = _draw_objects_overlay(img, [obj])
            ratio = thumb_width / float(img.size[0])
            img = img.resize((thumb_width, max(1, int(img.size[1] * ratio))), Image.Resampling.LANCZOS)
            caption_h = 34
            tile = Image.new("RGB", (thumb_width, img.size[1] + caption_h), (18, 18, 18))
            tile.paste(img, (0, 0))
            draw = ImageDraw.Draw(tile)
            draw.text((6, img.size[1] + 7), f"{label} conf={float(obj.get('confidence', 0.0)):.2f}", fill=(255, 255, 255))
            thumbs.append(tile)
        cols = 3
        rows = (len(thumbs) + cols - 1) // cols
        tile_w = thumb_width
        tile_h = max(t.size[1] for t in thumbs)
        sheet = Image.new("RGB", (cols * tile_w, rows * tile_h), (8, 8, 8))
        for idx, tile in enumerate(thumbs):
            x = (idx % cols) * tile_w
            y = (idx // cols) * tile_h
            sheet.paste(tile, (x, y))
        sheet.save(audit_dir / f"{label}.jpg", quality=90)


def _load_frame_samples(frames_jsonl: str) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    path = Path(frames_jsonl)
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            image_path = Path(str(row.get("image", "")))
            if not image_path.exists():
                continue
            video = Path(str(row.get("video", image_path)))
            sec = float(row.get("time_sec", 0.0))
            samples.append({"video": video, "time_sec": sec, "image": image_path})
    return samples


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

    samples: List[Dict[str, Any]] = []
    if args.frames_jsonl:
        samples = _load_frame_samples(args.frames_jsonl)
    else:
        videos = [Path(v) for v in args.videos]
        for video in videos:
            times = _sample_times(video, args.interval_sec, args.max_frames_per_video, args.start_sec, args.end_sec)
            samples.extend({"video": video, "time_sec": sec} for sec in times)
    if args.shuffle:
        random.Random(args.seed).shuffle(samples)
    if args.max_total_frames and len(samples) > args.max_total_frames:
        samples = samples[: args.max_total_frames]

    temperature: Optional[float] = None if args.temperature < 0 else args.temperature
    if args.api_mode == "responses":
        annotator = OpenAIResponsesFrameAnnotator(
            model_name=args.model,
            api_key_env=args.api_key_env,
            temperature=temperature,
            max_tokens=args.max_tokens,
            base_url=args.base_url,
            reasoning_effort=args.reasoning_effort,
            image_detail=args.image_detail,
            verifier_model_name=args.verifier_model,
            prompt_profile=args.prompt_profile,
        )
    else:
        annotator = OpenAIFrameAnnotator(
            model_name=args.model,
            api_key_env=args.api_key_env,
            temperature=temperature if temperature is not None else 0.0,
            max_tokens=args.max_tokens,
            base_url=args.base_url,
            prompt_profile=args.prompt_profile,
        )
    counts = {name: 0 for name in CLASS_NAMES}
    rejected_counts = {name: 0 for name in CLASS_NAMES + ["unknown"]}
    empty = 0
    results_path = output / "annotations.jsonl"
    with results_path.open("a", encoding="utf-8") as out_jsonl:
        for idx, sample in enumerate(samples, start=1):
            video = Path(sample["video"])
            sec = float(sample["time_sec"])
            stem = _safe_stem(video, sec)
            split = "val" if idx % args.val_every == 0 else "train"
            ann_path = output / "annotations" / f"{stem}.json"
            image_path = output / "images" / split / f"{stem}.jpg"
            label_path = output / "labels" / split / f"{stem}.txt"
            if args.resume and ann_path.exists():
                ann = json.loads(ann_path.read_text(encoding="utf-8"))
                objects = ann.get("objects") or []
                if not image_path.exists():
                    if sample.get("image"):
                        image = Image.open(sample["image"]).convert("RGB")
                    else:
                        image = _read_frame(video, sec)
                    image.save(image_path, quality=92)
            else:
                if sample.get("image"):
                    image = Image.open(sample["image"]).convert("RGB")
                else:
                    image = _read_frame(video, sec)
                last_error: Exception | None = None
                objects: List[Dict[str, Any]] = []
                rejected: List[Dict[str, Any]] = []
                for attempt in range(args.retries + 1):
                    try:
                        objects = _validate_objects(annotator.annotate(image), args.min_confidence)
                        if args.verify and objects and hasattr(annotator, "verify"):
                            verified, verifier_rejected = _validate_verified_objects(
                                annotator.verify(image, objects),
                                objects,
                                args.min_verified_confidence,
                            )
                            objects = verified
                            rejected.extend(verifier_rejected)
                        objects, auto_rejected = _auto_quality_filter(objects)
                        rejected.extend(auto_rejected)
                        break
                    except Exception as exc:
                        last_error = exc
                        time.sleep(min(8.0, 1.5 * (attempt + 1)))
                if last_error and not objects:
                    print(f"[gpt-fullframe] warning empty after error {video.name}@{sec:.1f}s: {last_error}", flush=True)
                image.save(image_path, quality=92)
                ann = {
                    "video": str(video),
                    "time_sec": sec,
                    "split": split,
                    "model": args.model,
                    "api_mode": args.api_mode,
                    "verified": bool(args.verify and hasattr(annotator, "verify")),
                    "objects": objects,
                    "rejected_objects": rejected,
                }
                ann_path.write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")

            lines = []
            for obj in objects:
                counts[obj["label"]] += 1
                lines.append(_to_yolo_line(obj))
            for obj in ann.get("rejected_objects") or []:
                label = _normalize_label(obj.get("label") or (obj.get("initial") or {}).get("label"))
                rejected_counts[label if label in rejected_counts else "unknown"] += 1
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
                        "rejected_objects": ann.get("rejected_objects") or [],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            out_jsonl.flush()
            print(
                f"[gpt-fullframe] {idx}/{len(samples)} {video.name}@{sec:.1f}s "
                f"objects={len(objects)} rejected={len(ann.get('rejected_objects') or [])}",
                flush=True,
            )

    print("[gpt-fullframe] counts", json.dumps(counts, ensure_ascii=False, indent=2))
    print("[gpt-fullframe] rejected_counts", json.dumps(rejected_counts, ensure_ascii=False, indent=2))
    print(f"[gpt-fullframe] empty_images={empty}/{len(samples)}")
    if args.write_audit:
        _write_audit_sheets(output, max_per_class=args.audit_max_per_class)
        print(f"[gpt-fullframe] audit={output / 'audit'}")
    print(f"[gpt-fullframe] wrote {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--videos", nargs="+", default=[])
    parser.add_argument("--frames-jsonl", default="")
    parser.add_argument("--output", default="datasets/surgical_objects_gpt_v3")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--verifier-model", default="")
    parser.add_argument("--api-mode", choices=["responses", "chat"], default="responses")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--temperature", type=float, default=-1.0, help="Set <0 to omit temperature for GPT-5.x defaults")
    parser.add_argument("--max-tokens", type=int, default=1000)
    parser.add_argument("--reasoning-effort", default="none", choices=["none", "minimal", "low", "medium", "high", "xhigh"])
    parser.add_argument("--image-detail", default="high", choices=["low", "high", "auto", "original"])
    parser.add_argument("--prompt-profile", default="default", choices=["default", "clip-focused", "titanium-focused", "dual-clip-strict"])
    parser.add_argument("--interval-sec", type=float, default=12.0)
    parser.add_argument("--max-frames-per-video", type=int, default=30)
    parser.add_argument("--max-total-frames", type=int, default=0)
    parser.add_argument("--start-sec", type=float, default=0.0)
    parser.add_argument("--end-sec", type=float, default=0.0)
    parser.add_argument("--min-confidence", type=float, default=0.62)
    parser.add_argument("--min-verified-confidence", type=float, default=0.70)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--val-every", type=int, default=5)
    parser.add_argument("--shuffle", action="store_true")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--write-audit", action="store_true")
    parser.add_argument("--audit-max-per-class", type=int, default=40)
    args = parser.parse_args()
    if not args.frames_jsonl and not args.videos:
        parser.error("either --videos or --frames-jsonl is required")
    annotate(args)


if __name__ == "__main__":
    main()
