#!/usr/bin/env python3
"""Binary deployed-clip benchmark against an OpenAI-compatible VLM server.

Uses the EXACT production prompt from clip_vlm_review (backend/routers/analysis.py
_run_clip_vlm_review) and the production image shape (whole frame, 640px max side,
JPEG q64), so results transfer directly to the realtime pipeline.

Eval set = reviewed seed dataset (positives: hemolok/titanium boxes; negatives:
reject_*) + hard frames dir (pos_*/neg_* filename prefixes).

Usage:
  python scripts/benchmark_clip_binary_v2.py --base-url http://127.0.0.1:8011/v1 \
      --model InternVL3_5-8B --candidate internvl3_5-8b
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import statistics
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

PROMPT = (
    "You are checking laparoscopic cholecystectomy frames for deployed clips. "
    "Only decide whether a deployed clip body is visible. Do not distinguish Hem-o-lok "
    "from titanium clips; white/ivory/purple/blue/green polymer locking clips and "
    "small metallic silver/gray clips should all be classified as clip. Do not classify "
    "a long white instrument tip, ceramic hook tip, suction tip, trocar, or clip applier "
    "jaw as a deployed clip. "
    "Return only JSON with fields: "
    "{\"classification\":\"clip|clip_applier|no_clip|glare_or_instrument\","
    "\"confidence\":0.0,\"count\":0,\"reason\":\"short reason\"}."
)

CLIP_PRED = {"clip", "titanium_clip", "hemolok_clip", "hem-o-lok", "hemolok"}


def image_data_url(path: Path, max_side: int = 640, quality: int = 64) -> str:
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def parse_json_obj(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def collect_seed(data_yaml: Path) -> list[tuple[Path, str]]:
    root = data_yaml.parent
    samples = []
    for split in ("train", "val"):
        for img in sorted((root / "images" / split).glob("*.jpg")):
            name = img.name
            if name.startswith(("hemolok_clip_", "titanium_clip_")):
                samples.append((img, "clip"))
            elif name.startswith("reject_"):
                samples.append((img, "no_clip"))
    return samples


def collect_hard(hard_dir: Path) -> list[tuple[Path, str]]:
    samples = []
    for img in sorted(hard_dir.glob("*.jpg")):
        if img.name.startswith("pos_"):
            samples.append((img, "clip"))
        elif img.name.startswith("neg_"):
            samples.append((img, "no_clip"))
    return samples


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--data", default="datasets/clip_detector_reviewed_seed_v1/data.yaml")
    ap.add_argument("--hard-dir", default="datasets/clip_binary_hard_v1/images")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--max-tokens", type=int, default=180)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    samples = ([] if args.data == "none" else collect_seed(Path(args.data))) + collect_hard(Path(args.hard_dir))
    if args.limit:
        samples = samples[: args.limit]

    out_dir = Path("runs/clip_vlm_binary_benchmark_v2") / f"{args.candidate}_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for idx, (img, expected) in enumerate(samples, 1):
        payload = {
            "model": args.model,
            "messages": [
                {"role": "system", "content": "Return one compact JSON object only."},
                {"role": "user", "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": image_data_url(img), "detail": "low"}},
                ]},
            ],
            "temperature": 0.0,
            "max_tokens": args.max_tokens,
        }
        t0 = time.perf_counter()
        try:
            resp = requests.post(f"{args.base_url}/chat/completions", json=payload, timeout=args.timeout)
            latency = time.perf_counter() - t0
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"] or ""
            parsed = parse_json_obj(text)
            pred_raw = str(parsed.get("classification") or parsed.get("label") or "").strip().lower()
            predicted = "clip" if pred_raw in CLIP_PRED else ("error" if not pred_raw else "no_clip")
            ok = True
        except Exception as exc:
            latency = time.perf_counter() - t0
            text, parsed, pred_raw, predicted, ok = str(exc)[:300], {}, "", "error", False
        rows.append({
            "image": str(img), "expected": expected, "predicted": predicted,
            "pred_raw": pred_raw, "hard": "clip_binary_hard" in str(img),
            "correct": predicted == expected, "success": ok,
            "latency_seconds": round(latency, 3),
            "confidence": parsed.get("confidence"), "text": text[:400],
        })
        if idx % 25 == 0:
            print(f"{idx}/{len(samples)} done")

    with open(out_dir / "results.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def stats(subset):
        n = len(subset)
        if not n:
            return {}
        acc = sum(r["correct"] for r in subset) / n
        pos = [r for r in subset if r["expected"] == "clip"]
        neg = [r for r in subset if r["expected"] == "no_clip"]
        tpr = sum(r["correct"] for r in pos) / len(pos) if pos else None
        tnr = sum(r["correct"] for r in neg) / len(neg) if neg else None
        return {"n": n, "accuracy": round(acc, 3),
                "clip_recall": round(tpr, 3) if tpr is not None else None,
                "no_clip_specificity": round(tnr, 3) if tnr is not None else None}

    lat = [r["latency_seconds"] for r in rows if r["success"]]
    summary = {
        "candidate": args.candidate, "model": args.model,
        "overall": stats(rows),
        "seed": stats([r for r in rows if not r["hard"]]),
        "hard": stats([r for r in rows if r["hard"]]),
        "hard_detail": [
            {"image": Path(r["image"]).name, "expected": r["expected"], "predicted": r["predicted"]}
            for r in rows if r["hard"]
        ],
        "errors": sum(1 for r in rows if not r["success"]),
        "avg_latency": round(statistics.mean(lat), 3) if lat else None,
        "p50_latency": round(statistics.median(lat), 3) if lat else None,
        "p95_latency": round(sorted(lat)[int(len(lat) * 0.95) - 1], 3) if len(lat) >= 2 else None,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("output:", out_dir)


if __name__ == "__main__":
    main()
