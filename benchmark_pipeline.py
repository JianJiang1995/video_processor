#!/usr/bin/env python3
"""
Pipeline Latency Benchmark
Measures actual SurgR1 and Gemini API latency for different batch sizes.
"""
import asyncio
import cv2
import json
import os
import sys
import time
import tempfile
import httpx
from pathlib import Path
from PIL import Image
from io import BytesIO
from typing import List, Dict, Any

# Add parent paths
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "backend"))

# Load .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env")

VIDEO_PATH = "/data2/jj/proj/video_processor/test_data/2024-12-24_225315_VID002.mp4"
SURGR1_URL = "http://localhost:9003"

# ============================================================================
# Frame Extraction
# ============================================================================

def extract_frames(video_path: str, count: int, start_sec: float = 10.0, interval_sec: float = 1.0) -> List[Dict]:
    """Extract frames from video, save as temp JPEG files."""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    frames = []
    
    for i in range(count):
        ts = start_sec + i * interval_sec
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(ts * fps))
        ret, bgr = cap.read()
        if not ret:
            break
        
        # Save to temp file for SurgR1 API
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, dir="/tmp")
        cv2.imwrite(tmp.name, bgr)
        
        # Also keep PIL image for Gemini
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        
        frames.append({
            "path": tmp.name,
            "pil": pil_img,
            "timestamp": ts,
            "frame_idx": i
        })
    
    cap.release()
    return frames


def cleanup_frames(frames: List[Dict]):
    for f in frames:
        try:
            os.unlink(f["path"])
        except:
            pass


# ============================================================================
# SurgR1 Benchmark
# ============================================================================

async def benchmark_surgr1_batch(frames: List[Dict], label: str = "") -> Dict:
    """Benchmark SurgR1 batch analysis."""
    image_paths = [f["path"] for f in frames]
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        payload = {"image_paths": image_paths}
        
        t0 = time.time()
        resp = await client.post(f"{SURGR1_URL}/analyze", json=payload)
        elapsed = time.time() - t0
        
        if resp.status_code != 200:
            return {"label": label, "count": len(frames), "elapsed": elapsed, "error": resp.text, "fps": 0}
        
        data = resp.json()
        results = data.get("results", [])
        total_q = data.get("total_questions", 0)
        
        # Extract sample result
        sample = None
        if results:
            r = results[0]
            sample = {k: v[:80] for k, v in r.get("responses", {}).items()}
        
        return {
            "label": label,
            "count": len(frames),
            "elapsed": round(elapsed, 2),
            "fps": round(len(frames) / elapsed, 2) if elapsed > 0 else 0,
            "per_frame": round(elapsed / len(frames), 2) if frames else 0,
            "total_questions": total_q,
            "sample": sample
        }


async def benchmark_surgr1_all():
    """Run SurgR1 benchmarks for various batch sizes."""
    print("\n" + "=" * 70)
    print("  SurgR1 Batch Latency Benchmark")
    print("=" * 70)
    
    # Check health first
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{SURGR1_URL}/health")
            health = resp.json()
            print(f"  Status: {health.get('status')}, Model: {health.get('model_loaded')}")
        except Exception as e:
            print(f"  ERROR: SurgR1 not reachable: {e}")
            return []
    
    # Extract enough frames for largest batch
    max_frames = 15
    print(f"\n  Extracting {max_frames} frames from video...")
    all_frames = extract_frames(VIDEO_PATH, max_frames, start_sec=30.0, interval_sec=1.0)
    print(f"  Extracted {len(all_frames)} frames")
    
    batch_sizes = [1, 3, 5, 8, 10, 15]
    results = []
    
    for bs in batch_sizes:
        if bs > len(all_frames):
            break
        frames = all_frames[:bs]
        label = f"batch_{bs}"
        print(f"\n  Testing batch_size={bs}...", end=" ", flush=True)
        
        result = await benchmark_surgr1_batch(frames, label)
        results.append(result)
        
        if result.get("error"):
            print(f"ERROR: {result['error'][:60]}")
        else:
            print(f"{result['elapsed']}s ({result['fps']} fps, {result['per_frame']}s/frame)")
    
    cleanup_frames(all_frames)
    
    # Summary table
    print(f"\n  {'Batch':>6} | {'Time(s)':>8} | {'FPS':>6} | {'Per Frame':>10} | {'Questions':>10}")
    print(f"  {'-'*6}-+-{'-'*8}-+-{'-'*6}-+-{'-'*10}-+-{'-'*10}")
    for r in results:
        if not r.get("error"):
            print(f"  {r['count']:>6} | {r['elapsed']:>8} | {r['fps']:>6} | {r['per_frame']:>10} | {r['total_questions']:>10}")
    
    return results


# ============================================================================
# Gemini Benchmark
# ============================================================================

async def benchmark_gemini(frames: List[Dict], r1_results: List[Dict] = None, label: str = "") -> Dict:
    """Benchmark Gemini integrate_analysis_results."""
    from backend.services.gemini_client import get_gemini_client
    
    client = get_gemini_client()
    
    # Build fake R1 results if not provided
    if r1_results is None:
        r1_results = []
        for f in frames:
            r1_results.append({
                "frame_idx": f["frame_idx"],
                "timestamp": f["timestamp"],
                "phase": "GallbladderDissection",
                "action": "Electrocautery hook dissecting gallbladder from liver bed",
                "tools": "grasper at (0.1,0.2),(0.3,0.5); electrocautery hook at (0.4,0.3),(0.7,0.8)"
            })
    
    images = [f["pil"] for f in frames]
    
    t0 = time.time()
    result = await client.integrate_analysis_results(
        frame_analyses=r1_results,
        images=images,
        include_background=True,
        history_context=None
    )
    elapsed = time.time() - t0
    
    text = result.get("text", "")[:200] if result.get("success") else result.get("error", "")[:200]
    
    return {
        "label": label,
        "image_count": len(frames),
        "elapsed": round(elapsed, 2),
        "success": result.get("success", False),
        "text_preview": text,
        "duration_ms": result.get("duration_ms", 0)
    }


async def benchmark_gemini_all():
    """Run Gemini benchmarks for various image counts."""
    print("\n" + "=" * 70)
    print("  Gemini Summarization Latency Benchmark")
    print("=" * 70)
    
    # Check Gemini health
    from backend.services.gemini_client import get_gemini_client
    client = get_gemini_client()
    is_healthy = await client.check_health()
    print(f"  Gemini healthy: {is_healthy}")
    
    if not is_healthy:
        print("  ERROR: Gemini not available")
        return []
    
    max_frames = 8
    print(f"\n  Extracting {max_frames} frames...")
    all_frames = extract_frames(VIDEO_PATH, max_frames, start_sec=30.0, interval_sec=2.0)
    
    image_counts = [2, 3, 4, 5, 6, 8]
    results = []
    
    for ic in image_counts:
        if ic > len(all_frames):
            break
        frames = all_frames[:ic]
        label = f"images_{ic}"
        print(f"\n  Testing image_count={ic}...", end=" ", flush=True)
        
        result = await benchmark_gemini(frames, label=label)
        results.append(result)
        
        if result["success"]:
            print(f"{result['elapsed']}s")
            print(f"    Preview: {result['text_preview'][:100]}...")
        else:
            print(f"ERROR: {result['text_preview'][:80]}")
    
    cleanup_frames(all_frames)
    
    # Summary
    print(f"\n  {'Images':>7} | {'Time(s)':>8} | {'Success':>8}")
    print(f"  {'-'*7}-+-{'-'*8}-+-{'-'*8}")
    for r in results:
        print(f"  {r['image_count']:>7} | {r['elapsed']:>8} | {'OK' if r['success'] else 'FAIL':>8}")
    
    return results


# ============================================================================
# Gemini Quality Comparison: different frame counts
# ============================================================================

async def benchmark_gemini_quality():
    """Compare Gemini summary quality with different frame counts.
    Uses REAL SurgR1 results for accurate quality assessment."""
    print("\n" + "=" * 70)
    print("  Gemini Summary Quality Comparison (with real R1 results)")
    print("=" * 70)
    
    from backend.services.gemini_client import get_gemini_client
    client = get_gemini_client()
    
    # Extract frames for a 15s window (simulating different sample_intervals)
    configs = [
        {"name": "interval_1s (15 frames)", "interval": 1.0, "count": 15},
        {"name": "interval_2s (8 frames)", "interval": 2.0, "count": 8},
        {"name": "interval_3s (5 frames)", "interval": 3.0, "count": 5},
        {"name": "interval_5s (3 frames)", "interval": 5.0, "count": 3},
    ]
    
    for cfg in configs:
        print(f"\n  --- {cfg['name']} ---")
        frames = extract_frames(VIDEO_PATH, cfg["count"], start_sec=60.0, interval_sec=cfg["interval"])
        
        # Get real SurgR1 results
        print(f"  Getting SurgR1 results for {len(frames)} frames...", end=" ", flush=True)
        r1_results = []
        async with httpx.AsyncClient(timeout=120.0) as http_client:
            try:
                paths = [f["path"] for f in frames]
                resp = await http_client.post(f"{SURGR1_URL}/analyze", json={"image_paths": paths})
                if resp.status_code == 200:
                    data = resp.json()
                    for i, r in enumerate(data.get("results", [])):
                        responses = r.get("responses", {})
                        r1_results.append({
                            "frame_idx": frames[i]["frame_idx"],
                            "timestamp": frames[i]["timestamp"],
                            "phase": responses.get("surgical_phase", ""),
                            "action": responses.get("surgical_action", ""),
                            "tools": responses.get("tool_localization", "")
                        })
                    print(f"OK ({len(r1_results)} results)")
                else:
                    print(f"FAILED ({resp.status_code})")
                    cleanup_frames(frames)
                    continue
            except Exception as e:
                print(f"ERROR: {e}")
                cleanup_frames(frames)
                continue
        
        # Run Gemini summarization
        print(f"  Running Gemini summarization...", end=" ", flush=True)
        result = await benchmark_gemini(frames, r1_results=r1_results, label=cfg["name"])
        
        if result["success"]:
            print(f"{result['elapsed']}s")
            print(f"\n  Summary output:")
            print(f"  {'-'*60}")
            # Print full text
            full_text = ""
            gemini_result = await client.integrate_analysis_results(
                frame_analyses=r1_results,
                images=[f["pil"] for f in frames],
                include_background=True
            )
            if gemini_result.get("success"):
                full_text = gemini_result.get("text", "")
            print(f"  {full_text}")
            print(f"  {'-'*60}")
        else:
            print(f"FAILED: {result['text_preview'][:80]}")
        
        cleanup_frames(frames)


# ============================================================================
# Pipeline Simulation
# ============================================================================

async def simulate_pipeline():
    """Simulate the full pipeline with different parameter configs."""
    print("\n" + "=" * 70)
    print("  Pipeline Simulation: End-to-End Latency")
    print("=" * 70)
    
    configs = [
        {
            "name": "Baseline",
            "sample_interval": 1.0,
            "batch_timeout": 6.0,
            "min_batch": 3,
            "target_batch": 8,
            "window_duration": 15.0,
        },
        {
            "name": "Config A",
            "sample_interval": 2.0,
            "batch_timeout": 3.0,
            "min_batch": 2,
            "target_batch": 4,
            "window_duration": 15.0,
        },
        {
            "name": "Config B",
            "sample_interval": 3.0,
            "batch_timeout": 3.0,
            "min_batch": 2,
            "target_batch": 3,
            "window_duration": 15.0,
        },
        {
            "name": "Config C",
            "sample_interval": 2.0,
            "batch_timeout": 4.0,
            "min_batch": 3,
            "target_batch": 5,
            "window_duration": 15.0,
        },
    ]
    
    for cfg in configs:
        print(f"\n  === {cfg['name']} ===")
        print(f"  sample_interval={cfg['sample_interval']}s, batch_timeout={cfg['batch_timeout']}s, "
              f"min_batch={cfg['min_batch']}, target_batch={cfg['target_batch']}")
        
        wd = cfg["window_duration"]
        si = cfg["sample_interval"]
        frames_per_window = int(wd / si)
        
        # Extract frames matching this config
        frames = extract_frames(VIDEO_PATH, frames_per_window, start_sec=60.0, interval_sec=si)
        print(f"  Frames per window: {len(frames)}")
        
        # Simulate batch accumulation + SurgR1 processing
        total_r1_time = 0
        r1_results = []
        batch_buffer = []
        batch_start_time = None
        
        for i, frame in enumerate(frames):
            simulated_arrival = i * si  # When this frame would arrive
            batch_buffer.append(frame)
            if batch_start_time is None:
                batch_start_time = simulated_arrival
            
            # Check batch trigger conditions
            batch_full = len(batch_buffer) >= cfg["target_batch"]
            batch_timeout = (simulated_arrival - batch_start_time) >= cfg["batch_timeout"] and len(batch_buffer) >= cfg["min_batch"]
            last_frame = (i == len(frames) - 1) and len(batch_buffer) >= cfg["min_batch"]
            
            if batch_full or batch_timeout or last_frame:
                # Process this batch
                wait_time = simulated_arrival - batch_start_time
                print(f"    Batch of {len(batch_buffer)} frames (waited {wait_time:.1f}s)...", end=" ", flush=True)
                
                t0 = time.time()
                async with httpx.AsyncClient(timeout=120.0) as client:
                    paths = [f["path"] for f in batch_buffer]
                    resp = await client.post(f"{SURGR1_URL}/analyze", json={"image_paths": paths})
                    r1_elapsed = time.time() - t0
                    total_r1_time += r1_elapsed
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        for j, r in enumerate(data.get("results", [])):
                            responses = r.get("responses", {})
                            r1_results.append({
                                "frame_idx": batch_buffer[j]["frame_idx"],
                                "timestamp": batch_buffer[j]["timestamp"],
                                "phase": responses.get("surgical_phase", ""),
                                "action": responses.get("surgical_action", ""),
                                "tools": responses.get("tool_localization", "")
                            })
                        print(f"R1: {r1_elapsed:.2f}s")
                    else:
                        print(f"FAILED")
                
                batch_buffer = []
                batch_start_time = None
        
        # Now run Gemini summarization
        print(f"    Running Gemini summarization with {len(r1_results)} R1 results + {len(frames)} images...", end=" ", flush=True)
        
        from backend.services.gemini_client import get_gemini_client
        gemini = get_gemini_client()
        
        t0 = time.time()
        gemini_result = await gemini.integrate_analysis_results(
            frame_analyses=r1_results,
            images=[f["pil"] for f in frames],
            include_background=True
        )
        gemini_elapsed = time.time() - t0
        
        if gemini_result.get("success"):
            print(f"Gemini: {gemini_elapsed:.2f}s")
            summary_text = gemini_result.get("text", "")
            print(f"\n    Summary: {summary_text[:200]}")
        else:
            print(f"FAILED: {gemini_result.get('error', '')[:80]}")
            summary_text = ""
        
        # Calculate total pipeline latency
        # In real pipeline: batch_wait + R1_processing happens during window playback
        # But if R1 is slower than real-time, it adds to latency
        
        # Simulated timeline:
        # t=0: window starts
        # t=window_duration: window ends, need R1 results ready
        # If R1 finishes after window_duration, that's extra latency
        
        # In our simulation, R1 processes sequentially (worst case)
        # In real pipeline, R1 runs in parallel with frame capture
        
        # Best case: R1 finishes within window_duration
        # Worst case: R1 takes total_r1_time
        
        r1_overhead = max(0, total_r1_time - wd)  # Extra time beyond window duration
        total_latency = wd + r1_overhead + gemini_elapsed
        
        print(f"\n    --- Timing Summary ---")
        print(f"    Window duration:     {wd:.1f}s")
        print(f"    Total R1 time:       {total_r1_time:.2f}s (overhead: {r1_overhead:.2f}s)")
        print(f"    Gemini time:         {gemini_elapsed:.2f}s")
        print(f"    Total latency:       {total_latency:.2f}s (= window + R1 overhead + Gemini)")
        print(f"    Latency ratio:       {total_latency/wd:.2f}x window duration")
        print(f"    Summary length:      {len(summary_text)} chars")
        
        cleanup_frames(frames)


# ============================================================================
# Main
# ============================================================================

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Pipeline Latency Benchmark")
    parser.add_argument("--surgr1", action="store_true", help="Run SurgR1 benchmark only")
    parser.add_argument("--gemini", action="store_true", help="Run Gemini benchmark only")
    parser.add_argument("--quality", action="store_true", help="Run Gemini quality comparison")
    parser.add_argument("--simulate", action="store_true", help="Run full pipeline simulation")
    parser.add_argument("--all", action="store_true", help="Run all benchmarks")
    args = parser.parse_args()
    
    if not any([args.surgr1, args.gemini, args.quality, args.simulate, args.all]):
        args.all = True
    
    if args.surgr1 or args.all:
        await benchmark_surgr1_all()
    
    if args.gemini or args.all:
        await benchmark_gemini_all()
    
    if args.quality or args.all:
        await benchmark_gemini_quality()
    
    if args.simulate or args.all:
        await simulate_pipeline()
    
    print("\n" + "=" * 70)
    print("  Benchmark Complete!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
