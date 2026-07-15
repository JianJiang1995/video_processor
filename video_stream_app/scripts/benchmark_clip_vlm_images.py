#!/usr/bin/env python3
"""Benchmark OpenAI-compatible VLMs on labeled Hem-o-lok/titanium clip images."""
from __future__ import annotations

import argparse
import base64
import json
import random
import re
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


PROMPT = """你是一名腹腔镜胆囊切除术图像审核助手。
只根据图像判断是否可见已经释放并夹在组织上的夹体，不要根据文件名、时间或先验猜测。

关键区分：
- Hem-o-lok：塑料/聚合物锁扣夹，较厚，非金属，有锁扣/短棒/C形/U形/V形结构；常见颜色包括白色、乳白、淡紫、蓝色、绿色或紫色。只要是厚的塑料锁扣夹体，即使不是白色，也归 hemolok_clip。
- titanium_clip：小型银灰色金属钛夹，短、细、金属感，常呈V形/U形/平行短夹臂。
- clip_applier：施夹器/钛夹钳/钳口或长金属器械，不是已经释放的夹体。
- glare_or_instrument：高光、剪刀刃、电凝钩、钳口、组织反光或其他非夹体。

不要把蓝色/紫色/绿色塑料锁扣夹误判为 titanium_clip；只有明确银灰色金属短夹才是 titanium_clip。

如果看不到明确已经释放的夹体，请输出 no_clip。
输出严格 JSON，不要 markdown，不要解释：
{
  "label": "hemolok_clip|titanium_clip|clip_applier|glare_or_instrument|no_clip",
  "confidence": 0.0,
  "evidence": "一句中文证据"
}
"""


@dataclass
class Sample:
    image_path: Path
    label_path: Path
    expected: str
    split: str
    bbox_xywhn: tuple[float, float, float, float]


def parse_names(data_yaml: Path) -> dict[int, str]:
    text = data_yaml.read_text(encoding="utf-8")
    names: dict[int, str] = {}
    in_names = False
    for line in text.splitlines():
        if line.strip() == "names:":
            in_names = True
            continue
        if in_names:
            if not line.startswith(" ") and not line.startswith("\t"):
                break
            m = re.match(r"\s*(\d+)\s*:\s*(.+?)\s*$", line)
            if m:
                names[int(m.group(1))] = m.group(2).strip().strip("\"'")
    return names


def dataset_root(data_yaml: Path) -> Path:
    for line in data_yaml.read_text(encoding="utf-8").splitlines():
        if line.startswith("path:"):
            value = line.split(":", 1)[1].strip()
            root = Path(value)
            return root if root.is_absolute() else (data_yaml.parent / root).resolve()
    return data_yaml.parent.resolve()


def collect_samples(data_yaml: Path, labels: set[str], per_label: int, seed: int) -> list[Sample]:
    root = dataset_root(data_yaml)
    names = parse_names(data_yaml)
    by_label: dict[str, list[Sample]] = defaultdict(list)
    for split in ("val", "train"):
        label_dir = root / "labels" / split
        image_dir = root / "images" / split
        if not label_dir.exists():
            continue
        for label_path in sorted(label_dir.glob("*.txt")):
            image_path = None
            for suffix in (".jpg", ".jpeg", ".png"):
                candidate = image_dir / f"{label_path.stem}{suffix}"
                if candidate.exists():
                    image_path = candidate
                    break
            if image_path is None:
                continue
            for raw in label_path.read_text(encoding="utf-8").splitlines():
                parts = raw.strip().split()
                if len(parts) < 5:
                    continue
                cls = int(float(parts[0]))
                name = names.get(cls, f"class_{cls}")
                if name not in labels:
                    continue
                x, y, w, h = [float(v) for v in parts[1:5]]
                by_label[name].append(Sample(image_path, label_path, name, split, (x, y, w, h)))

    rng = random.Random(seed)
    out: list[Sample] = []
    for label in sorted(labels):
        rows = by_label.get(label, [])
        rng.shuffle(rows)
        out.extend(rows[:per_label] if per_label > 0 else rows)
    return out


def crop_from_yolo(image: Image.Image, bbox: tuple[float, float, float, float], pad: float) -> Image.Image:
    w, h = image.size
    x, y, bw, bh = bbox
    cx, cy = x * w, y * h
    box_w, box_h = bw * w, bh * h
    side_pad = max(box_w, box_h) * pad
    x1 = max(0, int(cx - box_w / 2 - side_pad))
    y1 = max(0, int(cy - box_h / 2 - side_pad))
    x2 = min(w, int(cx + box_w / 2 + side_pad))
    y2 = min(h, int(cy + box_h / 2 + side_pad))
    crop = image.crop((x1, y1, x2, y2))
    if min(crop.size) < 224:
        scale = 224 / max(1, min(crop.size))
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.Resampling.BICUBIC)
    return crop


def annotated_image(image: Image.Image, bbox: tuple[float, float, float, float]) -> Image.Image:
    out = image.copy()
    w, h = out.size
    x, y, bw, bh = bbox
    x1 = int((x - bw / 2) * w)
    y1 = int((y - bh / 2) * h)
    x2 = int((x + bw / 2) * w)
    y2 = int((y + bh / 2) * h)
    draw = ImageDraw.Draw(out)
    for offset in range(4):
        draw.rectangle((x1 - offset, y1 - offset, x2 + offset, y2 + offset), outline=(255, 40, 40))
    return out


def image_to_data_url(image: Image.Image, max_side: int, quality: int) -> str:
    import io

    image = image.convert("RGB")
    w, h = image.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        image = image.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    payload = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{payload}"


def extract_json(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else {"_raw": cleaned, "_json_error": True}
    except Exception:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(cleaned[start : end + 1])
                return obj if isinstance(obj, dict) else {"_raw": cleaned, "_json_error": True}
            except Exception:
                pass
    return {"_raw": cleaned, "_json_error": True}


def call_vlm(base_url: str, model: str, image: Image.Image, timeout: float, max_side: int) -> dict[str, Any]:
    content = [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": image_to_data_url(image, max_side=max_side, quality=86)}},
    ]
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.0,
        "max_tokens": 260,
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text = payload["choices"][0]["message"]["content"]
        return {
            "success": True,
            "latency_seconds": round(time.time() - started, 3),
            "model": payload.get("model", model),
            "text": text,
            "parsed": extract_json(text),
        }
    except urllib.error.HTTPError as exc:
        return {
            "success": False,
            "latency_seconds": round(time.time() - started, 3),
            "error": exc.read().decode("utf-8", errors="replace")[:2000],
        }
    except Exception as exc:
        return {"success": False, "latency_seconds": round(time.time() - started, 3), "error": str(exc)}


def is_correct(expected: str, predicted: str) -> bool:
    return expected == predicted


def write_summary(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    lines = ["# Clip VLM Image Benchmark", ""]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["candidate"], row["mode"])].append(row)

    for (candidate, mode), group in sorted(grouped.items()):
        ok = [r for r in group if r.get("success")]
        json_ok = [r for r in ok if not (r.get("parsed") or {}).get("_json_error")]
        correct = [r for r in ok if r.get("correct")]
        lines.append(f"## {candidate} / {mode}")
        lines.append(f"- samples: {len(group)}")
        lines.append(f"- success: {len(ok)}/{len(group)}")
        lines.append(f"- strict_json: {len(json_ok)}/{len(group)}")
        lines.append(f"- accuracy: {len(correct)}/{len(group)} ({len(correct) / max(1, len(group)):.1%})")
        lat = sum(float(r.get("latency_seconds") or 0) for r in group) / max(1, len(group))
        lines.append(f"- avg_latency_seconds: {lat:.2f}")
        by_expected = Counter((r.get("expected"), r.get("predicted")) for r in group)
        lines.append("- confusion:")
        for (expected, predicted), count in sorted(by_expected.items()):
            lines.append(f"  - {expected} -> {predicted}: {count}")
        lines.append("")

    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="datasets/clip_detector_reviewed_seed_v1/data.yaml")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--candidate", default="")
    parser.add_argument("--labels", default="hemolok_clip,titanium_clip")
    parser.add_argument("--per-label", type=int, default=10)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--modes", default="whole,crop,annotated")
    parser.add_argument("--crop-pad", type=float, default=3.0)
    parser.add_argument("--max-side", type=int, default=1024)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    data_yaml = Path(args.data)
    if not data_yaml.is_absolute():
        data_yaml = repo_root / data_yaml
    labels = {v.strip() for v in args.labels.split(",") if v.strip()}
    modes = [v.strip() for v in args.modes.split(",") if v.strip()]
    candidate = args.candidate or args.model

    out_dir = Path(args.output_dir) if args.output_dir else repo_root / "runs" / "clip_vlm_image_benchmark" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    samples = collect_samples(data_yaml, labels, args.per_label, args.seed)
    rows: list[dict[str, Any]] = []
    for idx, sample in enumerate(samples, start=1):
        image = Image.open(sample.image_path)
        mode_images = {
            "whole": image,
            "crop": crop_from_yolo(image, sample.bbox_xywhn, args.crop_pad),
            "annotated": annotated_image(image, sample.bbox_xywhn),
        }
        for mode in modes:
            response = call_vlm(args.base_url, args.model, mode_images[mode], args.timeout, args.max_side)
            parsed = response.get("parsed") or {}
            predicted = str(parsed.get("label") or "error")
            row = {
                "candidate": candidate,
                "mode": mode,
                "index": idx,
                "image": str(sample.image_path),
                "label_file": str(sample.label_path),
                "split": sample.split,
                "expected": sample.expected,
                "predicted": predicted,
                "correct": is_correct(sample.expected, predicted),
                **response,
            }
            rows.append(row)
            with (out_dir / "results.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps({
                "candidate": candidate,
                "mode": mode,
                "idx": idx,
                "expected": sample.expected,
                "predicted": predicted,
                "correct": row["correct"],
                "success": row.get("success"),
                "latency_seconds": row.get("latency_seconds"),
                "error": row.get("error"),
            }, ensure_ascii=False), flush=True)
    write_summary(out_dir, rows)
    print(f"wrote benchmark results to {out_dir}")


if __name__ == "__main__":
    main()
