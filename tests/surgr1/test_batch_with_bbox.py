#!/usr/bin/env python3
"""
Test SurgR1 API with 100 samples from the JSONL dataset.
Draw bounding boxes on images for tool_localization results.
"""
import os
import re
import json
import time
import requests
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# For drawing bboxes
from PIL import Image, ImageDraw, ImageFont

# Configuration
SURGR1_API_URL = "http://localhost:9003"
JSONL_PATH = "/data/jj/proj/Laparo/data_json/renji/first_1000_quest_merged.jsonl"
OUTPUT_DIR = Path("/data2/jj/proj/video_processor/tests/surgr1/outputs")
NUM_SAMPLES = 100  # Number of unique images to test
BATCH_SIZE = 10    # Images per API call


def parse_bbox_from_response(response: str) -> List[Tuple[int, int, int, int]]:
    """
    Parse bounding boxes from model response.
    Expected format: bbox (x1,y1), (x2,y2) or similar variations.
    Returns list of (x1, y1, x2, y2) tuples.
    """
    bboxes = []
    
    # Pattern to match bbox coordinates like (123,456), (789,012)
    # Also handles formats like (123, 456), (789, 012) with spaces
    pattern = r'\((\d+)\s*,\s*(\d+)\)\s*,?\s*\((\d+)\s*,\s*(\d+)\)'
    
    matches = re.findall(pattern, response)
    for match in matches:
        try:
            x1, y1, x2, y2 = map(int, match)
            # Validate coordinates
            if x1 < x2 and y1 < y2:
                bboxes.append((x1, y1, x2, y2))
        except ValueError:
            continue
    
    return bboxes


def draw_bboxes_on_image(
    image_path: str,
    bboxes: List[Tuple[int, int, int, int]],
    output_path: str,
    tool_names: Optional[List[str]] = None
) -> bool:
    """
    Draw bounding boxes on an image and save it.
    """
    try:
        img = Image.open(image_path).convert("RGB")
        draw = ImageDraw.Draw(img)
        
        # Colors for different tools
        colors = ["#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#FF00FF", "#00FFFF"]
        
        # Try to load a font, fall back to default
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        except:
            font = ImageFont.load_default()
        
        for i, bbox in enumerate(bboxes):
            x1, y1, x2, y2 = bbox
            color = colors[i % len(colors)]
            
            # Draw rectangle with thick border
            for offset in range(3):
                draw.rectangle(
                    [x1 - offset, y1 - offset, x2 + offset, y2 + offset],
                    outline=color
                )
            
            # Draw label
            label = tool_names[i] if tool_names and i < len(tool_names) else f"Tool {i+1}"
            # Background for text
            text_bbox = draw.textbbox((x1, y1 - 20), label, font=font)
            draw.rectangle(text_bbox, fill=color)
            draw.text((x1, y1 - 20), label, fill="white", font=font)
        
        img.save(output_path)
        return True
    except Exception as e:
        print(f"Error drawing bboxes: {e}")
        return False


def load_unique_images(jsonl_path: str, num_images: int) -> List[str]:
    """Load unique image paths from JSONL file."""
    images = set()
    with open(jsonl_path, 'r') as f:
        for line in f:
            if len(images) >= num_images:
                break
            data = json.loads(line)
            for img in data.get('images', []):
                if os.path.exists(img):
                    images.add(img)
                    if len(images) >= num_images:
                        break
    return list(images)[:num_images]


def call_surgr1_api(image_paths: List[str]) -> Dict:
    """Call SurgR1 API with a batch of images."""
    try:
        response = requests.post(
            f"{SURGR1_API_URL}/analyze",
            json={"image_paths": image_paths},
            timeout=120
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"API error: {e}")
        return {"results": [], "error": str(e)}


def process_batch(image_paths: List[str], batch_idx: int) -> List[Dict]:
    """Process a batch of images and save results with bbox annotations."""
    print(f"\n{'='*60}")
    print(f"Processing batch {batch_idx + 1}: {len(image_paths)} images")
    print(f"{'='*60}")
    
    start_time = time.time()
    api_result = call_surgr1_api(image_paths)
    elapsed = time.time() - start_time
    
    print(f"API call completed in {elapsed:.2f}s")
    
    results = []
    for result in api_result.get('results', []):
        image_path = result['image_path']
        responses = result['responses']
        
        # Parse bboxes from tool_localization response
        tool_response = responses.get('tool_localization', '')
        bboxes = parse_bbox_from_response(tool_response)
        
        # Create output filename
        image_name = Path(image_path).stem
        output_image_path = OUTPUT_DIR / f"{image_name}_bbox.jpg"
        output_json_path = OUTPUT_DIR / f"{image_name}_result.json"
        
        # Draw bboxes if found
        bbox_drawn = False
        if bboxes:
            bbox_drawn = draw_bboxes_on_image(
                image_path,
                bboxes,
                str(output_image_path)
            )
            if bbox_drawn:
                print(f"  ✓ {image_name}: {len(bboxes)} bbox(es) drawn")
        else:
            # Copy original image if no bboxes found
            try:
                img = Image.open(image_path)
                img.save(str(output_image_path))
                print(f"  ○ {image_name}: No bboxes found")
            except Exception as e:
                print(f"  ✗ {image_name}: Error - {e}")
        
        # Save JSON result
        result_data = {
            "image_path": image_path,
            "output_image": str(output_image_path),
            "responses": responses,
            "parsed_bboxes": bboxes,
            "bbox_count": len(bboxes)
        }
        with open(output_json_path, 'w') as f:
            json.dump(result_data, f, indent=2, ensure_ascii=False)
        
        results.append(result_data)
    
    return results


def main():
    print("="*60)
    print("SurgR1 Batch Test with BBox Visualization")
    print("="*60)
    
    # Check API health
    try:
        health = requests.get(f"{SURGR1_API_URL}/health", timeout=30)
        health_data = health.json()
        print(f"✓ API Status: {health_data.get('status', 'unknown')}")
        print(f"  Model: {health_data.get('model_path', 'unknown')}")
    except Exception as e:
        print(f"✗ API not available: {e}")
        return
    
    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"\nOutput directory: {OUTPUT_DIR}")
    
    # Load images
    print(f"\nLoading {NUM_SAMPLES} unique images from JSONL...")
    image_paths = load_unique_images(JSONL_PATH, NUM_SAMPLES)
    print(f"Loaded {len(image_paths)} images")
    
    if not image_paths:
        print("No valid images found!")
        return
    
    # Process in batches
    all_results = []
    total_start = time.time()
    
    for i in range(0, len(image_paths), BATCH_SIZE):
        batch = image_paths[i:i + BATCH_SIZE]
        batch_results = process_batch(batch, i // BATCH_SIZE)
        all_results.extend(batch_results)
    
    total_elapsed = time.time() - total_start
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    total_bboxes = sum(r['bbox_count'] for r in all_results)
    images_with_bbox = sum(1 for r in all_results if r['bbox_count'] > 0)
    
    print(f"Total images processed: {len(all_results)}")
    print(f"Images with bboxes: {images_with_bbox}")
    print(f"Total bboxes detected: {total_bboxes}")
    print(f"Total time: {total_elapsed:.2f}s")
    print(f"Average time per image: {total_elapsed / len(all_results):.2f}s")
    
    # Save summary
    summary = {
        "total_images": len(all_results),
        "images_with_bbox": images_with_bbox,
        "total_bboxes": total_bboxes,
        "total_time_seconds": total_elapsed,
        "avg_time_per_image": total_elapsed / len(all_results),
        "results": all_results
    }
    
    summary_path = OUTPUT_DIR / "summary.json"
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    print(f"\nResults saved to: {OUTPUT_DIR}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()

