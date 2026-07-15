#!/usr/bin/env python3
"""
Batch Bleeding Detection & YOLO Annotation Generator

Uses Gemini Pro to detect bleeding areas in surgical video frames,
then outputs annotations in YOLO format for training a bleeding detection model.

Usage:
    python -m video_stream_app.backend.scripts.bleeding_annotation \
        --session-dir /path/to/sessions/{session_folder} \
        --output-dir /path/to/bleeding_dataset \
        --sample-interval 5 \
        --visualize

Or run directly:
    python backend/scripts/bleeding_annotation.py \
        --session-dir sessions/20260408_abc123_surgery/ \
        --output-dir /data4/jj/proj/surg_agent/detection_expert/datasets/bleeding \
        --sample-interval 5
"""
import os
import sys
import json
import asyncio
import argparse
import shutil
import logging
from pathlib import Path
from typing import List, Dict

import cv2
import numpy as np
from PIL import Image

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Load env
from dotenv import load_dotenv
load_dotenv(project_root.parent / ".env")


class BleedingAnnotator:
    """Generates YOLO-format bleeding annotations using Gemini."""

    BLEEDING_CLASS_ID = 0  # Single class: "bleeding"

    def __init__(self, model: str = "gemini-2.5-flash"):
        from google import genai
        from google.genai import types
        self.genai = genai
        self.types = types
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set")
        self.client = genai.Client(api_key=api_key)
        self.model = model
        logger.info(f"BleedingAnnotator initialized with model: {model}")

    def detect_bleeding(self, image_path: str) -> List[Dict]:
        """Detect bleeding areas in a single image. Returns normalized bboxes.

        Uses Gemini native box_2d format [y_min, x_min, y_max, x_max] in 0-1000 range,
        which is more reliable than custom field names.
        Output: list of dicts with x_center, y_center, width, height (normalized 0-1)
        """
        prompt = """Detect ANY visible blood or bleeding in this laparoscopic surgical image.

Include these categories:
- Active bleeding (flowing/oozing blood)
- Pooled blood (collected liquid blood)
- Fresh blood smears on tissue or instruments
- Blood-soaked gauze

Do NOT mark: healthy pink/red tissue, normal vasculature, liver color, clean organs, cauterized black tissue, bile fluid.

Return a JSON array (may be empty []) where each item has:
  "box_2d": [y_min, x_min, y_max, x_max]   (integers 0-1000, relative to image)
  "label": short description (e.g. "blood smear", "pooled blood", "active bleeding")

Example:
[{"box_2d": [120, 480, 380, 740], "label": "blood smear"}]"""

        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()

            img_part = self.types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

            response = self.client.models.generate_content(
                model=self.model,
                contents=[prompt, img_part],
                config=self.types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=1024,
                )
            )

            text = response.text.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            bboxes = json.loads(text)
            if not isinstance(bboxes, list):
                return []

            valid = []
            for bb in bboxes:
                # Parse Gemini native box_2d format [y_min, x_min, y_max, x_max] (0-1000)
                if "box_2d" in bb and isinstance(bb["box_2d"], list) and len(bb["box_2d"]) == 4:
                    y_min, x_min, y_max, x_max = bb["box_2d"]
                    # Convert to 0-1 normalized
                    x_min_n = max(0, min(1, x_min / 1000))
                    y_min_n = max(0, min(1, y_min / 1000))
                    x_max_n = max(0, min(1, x_max / 1000))
                    y_max_n = max(0, min(1, y_max / 1000))
                    bw = x_max_n - x_min_n
                    bh = y_max_n - y_min_n
                    if bw > 0.01 and bh > 0.01:
                        valid.append({
                            "x_center": round((x_min_n + x_max_n) / 2, 6),
                            "y_center": round((y_min_n + y_max_n) / 2, 6),
                            "width": round(bw, 6),
                            "height": round(bh, 6),
                            "label": bb.get("label", "bleeding"),
                        })
                # Fallback: legacy x_center/y_center/width/height format (0-1)
                elif all(k in bb for k in ("x_center", "y_center", "width", "height")):
                    vals = [bb["x_center"], bb["y_center"], bb["width"], bb["height"]]
                    if all(0 <= v <= 1 for v in vals) and bb["width"] > 0.01 and bb["height"] > 0.01:
                        valid.append({
                            "x_center": round(bb["x_center"], 6),
                            "y_center": round(bb["y_center"], 6),
                            "width": round(bb["width"], 6),
                            "height": round(bb["height"], 6),
                            "label": bb.get("label", bb.get("type", "bleeding")),
                        })
            return valid
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error for {image_path}: {e}")
            return []
        except Exception as e:
            logger.error(f"Bleeding detection failed for {image_path}: {e}")
            return []

    def save_yolo_label(self, detections: List[Dict], output_path: str):
        """Save detections as YOLO format label file."""
        with open(output_path, "w") as f:
            for det in detections:
                f.write(f"{self.BLEEDING_CLASS_ID} {det['x_center']} {det['y_center']} {det['width']} {det['height']}\n")

    def visualize(self, image_path: str, detections: List[Dict], output_path: str):
        """Draw bboxes on image for visual QA."""
        img = cv2.imread(image_path)
        h, w = img.shape[:2]
        for det in detections:
            cx, cy, bw, bh = det["x_center"], det["y_center"], det["width"], det["height"]
            x1 = int((cx - bw / 2) * w)
            y1 = int((cy - bh / 2) * h)
            x2 = int((cx + bw / 2) * w)
            y2 = int((cy + bh / 2) * h)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.putText(img, "bleeding", (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
        cv2.imwrite(output_path, img)

    def process_session(
        self, session_dir: str, output_dir: str,
        sample_interval: int = 5, visualize: bool = False,
        max_frames: int = 0
    ):
        """Process frames from a session directory."""
        session_path = Path(session_dir)
        frames_dir = session_path / "frames"
        if not frames_dir.exists():
            logger.error(f"Frames directory not found: {frames_dir}")
            return

        output_path = Path(output_dir)
        images_dir = output_path / "images" / "train"
        labels_dir = output_path / "labels" / "train"
        images_dir.mkdir(parents=True, exist_ok=True)
        labels_dir.mkdir(parents=True, exist_ok=True)

        if visualize:
            vis_dir = output_path / "visualizations"
            vis_dir.mkdir(parents=True, exist_ok=True)

        # Get sorted frame files
        frame_files = sorted(frames_dir.glob("*.jpg"))
        if not frame_files:
            logger.error(f"No JPEG frames found in {frames_dir}")
            return

        # Sample frames
        sampled = frame_files[::sample_interval]
        if max_frames > 0:
            sampled = sampled[:max_frames]

        logger.info(f"Processing {len(sampled)} frames (sampled every {sample_interval} from {len(frame_files)} total)")

        stats = {"total": 0, "with_bleeding": 0, "total_bboxes": 0}

        for i, frame_path in enumerate(sampled):
            stats["total"] += 1
            frame_name = frame_path.stem

            logger.info(f"[{i+1}/{len(sampled)}] Processing {frame_name}...")

            detections = self.detect_bleeding(str(frame_path))

            if detections:
                stats["with_bleeding"] += 1
                stats["total_bboxes"] += len(detections)
                logger.info(f"  -> Found {len(detections)} bleeding area(s)")

                # Copy image
                dst_img = images_dir / f"{frame_name}.jpg"
                shutil.copy2(frame_path, dst_img)

                # Save label
                dst_label = labels_dir / f"{frame_name}.txt"
                self.save_yolo_label(detections, str(dst_label))

                # Visualize
                if visualize:
                    vis_path = vis_dir / f"{frame_name}_vis.jpg"
                    self.visualize(str(frame_path), detections, str(vis_path))
            else:
                # Save negative sample (image with empty label)
                dst_img = images_dir / f"{frame_name}.jpg"
                shutil.copy2(frame_path, dst_img)
                dst_label = labels_dir / f"{frame_name}.txt"
                dst_label.touch()  # Empty label = no objects
                logger.info(f"  -> No bleeding detected (negative sample)")

        # Create data.yaml
        data_yaml = output_path / "data.yaml"
        yaml_content = f"""# Bleeding Detection Dataset
# Generated by bleeding_annotation.py from session: {session_path.name}

path: {output_path}
train: images/train
val: images/train  # TODO: split into val set

nc: 1
names:
  0: bleeding
"""
        data_yaml.write_text(yaml_content)

        # Save stats
        stats_file = output_path / "annotation_stats.json"
        with open(stats_file, "w") as f:
            json.dump(stats, f, indent=2)

        logger.info(f"\n{'='*50}")
        logger.info(f"Annotation complete!")
        logger.info(f"  Total frames processed: {stats['total']}")
        logger.info(f"  Frames with bleeding: {stats['with_bleeding']}")
        logger.info(f"  Total bleeding bboxes: {stats['total_bboxes']}")
        logger.info(f"  Output directory: {output_path}")
        logger.info(f"  data.yaml: {data_yaml}")
        logger.info(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(description="Generate bleeding detection annotations using Gemini")
    parser.add_argument("--session-dir", required=True, help="Path to session directory (with frames/ subfolder)")
    parser.add_argument("--output-dir", required=True, help="Output directory for YOLO dataset")
    parser.add_argument("--sample-interval", type=int, default=5, help="Sample every Nth frame (default: 5)")
    parser.add_argument("--max-frames", type=int, default=0, help="Max frames to process (0=all)")
    parser.add_argument("--visualize", action="store_true", help="Generate visualization images")
    parser.add_argument("--model", default="gemini-2.5-flash", help="Gemini model name")

    args = parser.parse_args()

    annotator = BleedingAnnotator(model=args.model)
    annotator.process_session(
        session_dir=args.session_dir,
        output_dir=args.output_dir,
        sample_interval=args.sample_interval,
        max_frames=args.max_frames,
        visualize=args.visualize,
    )


if __name__ == "__main__":
    main()
