#!/usr/bin/env python3
"""Review temporal clip candidates with a vision LLM."""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image


TRAINING_LABELS = ["hemolok_clip", "titanium_clip", "reject"]


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    for path in (Path(".env"), Path("../.env"), Path("../../.env")):
        if path.exists():
            load_dotenv(path)


def _data_url(path: Path, max_width: int = 1280, quality: int = 86) -> str:
    img = Image.open(path).convert("RGB")
    if img.width > max_width:
        ratio = max_width / float(img.width)
        img = img.resize((max_width, max(1, int(img.height * ratio))), Image.Resampling.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")


def _schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "training_label": {"type": "string", "enum": TRAINING_LABELS},
            "visual_category": {
                "type": "string",
                "enum": [
                    "white_or_ivory_polymer_locking_clip",
                    "colored_polymer_locking_clip",
                    "silver_titanium_clip",
                    "clip_applier_or_instrument",
                    "specular_highlight",
                    "tissue_or_blood_or_fat",
                    "uncertain",
                ],
            },
            "confidence": {"type": "number"},
            "box_quality": {"type": "string", "enum": ["good", "loose", "bad"]},
            "use_for_training": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["training_label", "visual_category", "confidence", "box_quality", "use_for_training", "reason"],
    }


def _prompt(row: Dict[str, Any]) -> str:
    return f"""You are reviewing surgical clip detector training candidates.

You will see a temporal sequence image with three panels: -1.0s, +0.0s, +1.0s. The CENTER panel contains one colored candidate box. A crop of the same boxed area is also provided.

Classify ONLY the object inside the center-panel box. Use the neighboring panels only to check temporal persistence and reject one-frame glare.

Training labels:
- hemolok_clip: a deployed opaque polymer locking clip BODY, including white/ivory/blue/purple/green Hem-o-lok-style clips. It is thick, rigid, plastic-looking, often C/U/V/bar-shaped with a locking notch or paired jaws. It is already released/clamped on tissue.
- titanium_clip: a deployed short silver/gray metallic titanium ligating clip BODY, often V/U/staple-like or two short parallel prongs on a duct/vessel. It is much shorter than applier jaws.
- reject: not a deployed clip body good enough for training.

Strict rules:
1. Relabel blue/purple/green polymer locking clips as hemolok_clip, not titanium_clip.
2. Label titanium_clip only for short silver/gray metal clip bodies. Do not use titanium_clip for glare, a long tool edge, applier jaw, scissors, hook, or grasper tip.
3. Reject if the box mainly covers tissue, blood, fat, smoke, shadows, wet reflection, instrument jaws/shaft, or if the clip body is too uncertain.
4. If the boxed object is a clip but the box is too loose for detector training, set box_quality=loose and use_for_training=false unless the clip body is still clearly localized.
5. Prefer precision over recall. False positives are worse than missing a hard sample.

Original candidate metadata:
label={row.get('label')} confidence={row.get('confidence')} video={Path(str(row.get('video',''))).name} time_sec={row.get('time_sec')}
reason={row.get('reason','')[:500]}

Return one JSON object only."""


class Reviewer:
    def __init__(self, args: argparse.Namespace):
        _load_dotenv()
        try:
            from openai import OpenAI
        except Exception as exc:
            raise RuntimeError("openai package is required") from exc
        api_key = os.environ.get(args.api_key_env) or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(f"Missing API key in {args.api_key_env}/OPENAI_API_KEY")
        kwargs: Dict[str, Any] = {"api_key": api_key}
        base_url = args.base_url or os.environ.get("OPENAI_BASE_URL", "")
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
        self.client = OpenAI(**kwargs)
        self.args = args

    def review(self, row: Dict[str, Any], sequence_path: Path, crop_path: Path) -> Dict[str, Any]:
        request: Dict[str, Any] = {
            "model": self.args.model,
            "instructions": "You are a conservative surgical data curation assistant. Return only valid JSON.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": _prompt(row)},
                        {"type": "input_image", "image_url": _data_url(sequence_path), "detail": self.args.image_detail},
                        {"type": "input_image", "image_url": _data_url(crop_path, max_width=512, quality=92), "detail": self.args.image_detail},
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "clip_temporal_review",
                    "schema": _schema(),
                    "strict": True,
                }
            },
            "reasoning": {"effort": self.args.reasoning_effort},
            "max_output_tokens": self.args.max_tokens,
            "store": False,
        }
        if self.args.temperature >= 0:
            request["temperature"] = self.args.temperature
        response = self.client.responses.create(**request)
        return json.loads(getattr(response, "output_text", "") or "{}")


def _load_rows(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def run(args: argparse.Namespace) -> None:
    input_dir = Path(args.input)
    output = Path(args.output)
    if output.exists() and args.clean:
        shutil.rmtree(output)
    (output / "reviews").mkdir(parents=True, exist_ok=True)
    rows = _load_rows(input_dir / "candidates.jsonl")
    if args.label:
        rows = [row for row in rows if str(row.get("label")) == args.label]
    if args.max_candidates and len(rows) > args.max_candidates:
        rows = rows[: args.max_candidates]

    reviewer = Reviewer(args)
    out_jsonl = output / "reviews.jsonl"
    counts = {label: 0 for label in TRAINING_LABELS}
    existing = set()
    if args.resume and out_jsonl.exists():
        for line in out_jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing.add(json.loads(line).get("candidate_id"))
    with out_jsonl.open("a", encoding="utf-8") as out:
        for idx, row in enumerate(rows, start=1):
            candidate_id = f"{Path(str(row.get('sequence_image'))).stem}"
            if candidate_id in existing:
                continue
            sequence_path = input_dir / str(row["sequence_image"])
            crop_path = input_dir / str(row["crop_image"])
            if not sequence_path.exists() or not crop_path.exists():
                continue
            last_error: Optional[Exception] = None
            review: Dict[str, Any] = {}
            for attempt in range(args.retries + 1):
                try:
                    review = reviewer.review(row, sequence_path, crop_path)
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(min(8.0, 1.5 * (attempt + 1)))
            if not review:
                review = {
                    "training_label": "reject",
                    "visual_category": "uncertain",
                    "confidence": 0.0,
                    "box_quality": "bad",
                    "use_for_training": False,
                    "reason": f"review failed: {last_error}",
                }
            label = str(review.get("training_label", "reject"))
            if label not in counts:
                label = "reject"
            counts[label] += 1
            record = {"candidate_id": candidate_id, "candidate": row, "review": review}
            (output / "reviews" / f"{candidate_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            print(f"[clip-review] {idx}/{len(rows)} {candidate_id} -> {label} conf={review.get('confidence')}", flush=True)

    summary = {
        "input": str(input_dir),
        "model": args.model,
        "reviewed": sum(1 for _ in out_jsonl.open("r", encoding="utf-8")) if out_jsonl.exists() else 0,
        "counts_this_run": counts,
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="datasets/clip_temporal_review_candidates_v1")
    parser.add_argument("--output", default="datasets/clip_temporal_review_candidates_gpt55_review_v1")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--reasoning-effort", default="none", choices=["none", "minimal", "low", "medium", "high", "xhigh"])
    parser.add_argument("--image-detail", default="high", choices=["low", "high", "auto", "original"])
    parser.add_argument("--temperature", type=float, default=-1.0)
    parser.add_argument("--max-tokens", type=int, default=500)
    parser.add_argument("--max-candidates", type=int, default=0)
    parser.add_argument("--label", default="")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
