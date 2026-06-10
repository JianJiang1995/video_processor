#!/usr/bin/env python3
"""
Migration script to generate preview frames for existing sessions.

This script scans all existing session folders and generates low-quality
preview frames for any session that doesn't have them yet.

Preview frames are:
- Resized to 640px width (maintaining aspect ratio)  
- Saved with JPEG quality 40
- About 90% smaller than original frames (~40KB vs ~600KB)

Usage:
    python generate_preview_frames.py [--dry-run] [--session SESSION_ID]
    
Options:
    --dry-run       Don't actually generate files, just show what would be done
    --session ID    Only process a specific session folder name
"""

import os
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime

import cv2

# Configuration - must match frame_storage_service.py
PREVIEW_WIDTH = 640
PREVIEW_QUALITY = 40
SESSIONS_BASE = Path("/data2/jj/proj/video_processor/video_stream_app/sessions")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_timestamp_from_filename(filename_stem: str) -> float:
    """Parse timestamp from filename stem"""
    parts = filename_stem.split("_")
    
    if len(parts) >= 3:
        try:
            return float(parts[2])
        except ValueError:
            ts_part = parts[2]
            if ts_part.startswith("ts"):
                ts_part = ts_part[2:]
            
            if len(parts) >= 4:
                try:
                    return float(f"{ts_part}.{parts[3]}")
                except ValueError:
                    pass
            else:
                try:
                    return float(ts_part)
                except ValueError:
                    pass
    
    # Fallback: try frame index
    if len(parts) >= 2:
        try:
            return float(int(parts[1]))
        except ValueError:
            pass
    
    return -1


def generate_preview_for_session(session_path: Path, dry_run: bool = False) -> dict:
    """
    Generate preview frames for a single session.
    
    Returns:
        dict with stats: {frames_found, previews_existing, previews_created, errors}
    """
    frames_folder = session_path / "frames"
    preview_folder = session_path / "preview"
    
    stats = {
        "frames_found": 0,
        "previews_existing": 0,
        "previews_created": 0,
        "previews_skipped": 0,
        "errors": 0
    }
    
    if not frames_folder.exists():
        logger.warning(f"No frames folder found in {session_path}")
        return stats
    
    # Get all frame files
    frame_files = list(frames_folder.glob("*.jpg"))
    stats["frames_found"] = len(frame_files)
    
    if not frame_files:
        logger.info(f"No frames in {session_path}")
        return stats
    
    # Create preview folder if it doesn't exist
    if not dry_run:
        preview_folder.mkdir(exist_ok=True)
    
    # Process each frame
    preview_index = []
    
    for frame_path in frame_files:
        preview_path = preview_folder / frame_path.name
        
        # Skip if preview already exists
        if preview_path.exists():
            stats["previews_existing"] += 1
            # Still add to index
            timestamp = parse_timestamp_from_filename(frame_path.stem)
            if timestamp >= 0:
                preview_index.append({"timestamp": timestamp, "filename": frame_path.name})
            continue
        
        if dry_run:
            stats["previews_created"] += 1
            timestamp = parse_timestamp_from_filename(frame_path.stem)
            if timestamp >= 0:
                preview_index.append({"timestamp": timestamp, "filename": frame_path.name})
            continue
        
        try:
            # Read original frame
            img = cv2.imread(str(frame_path))
            if img is None:
                logger.warning(f"Failed to read {frame_path}")
                stats["errors"] += 1
                continue
            
            # Resize to preview width maintaining aspect ratio
            h, w = img.shape[:2]
            if w > PREVIEW_WIDTH:
                scale = PREVIEW_WIDTH / w
                new_w = PREVIEW_WIDTH
                new_h = int(h * scale)
                preview_img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
            else:
                preview_img = img
            
            # Save with lower quality
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_QUALITY]
            cv2.imwrite(str(preview_path), preview_img, encode_params)
            
            stats["previews_created"] += 1
            
            # Add to index
            timestamp = parse_timestamp_from_filename(frame_path.stem)
            if timestamp >= 0:
                preview_index.append({"timestamp": timestamp, "filename": frame_path.name})
            
        except Exception as e:
            logger.error(f"Error processing {frame_path}: {e}")
            stats["errors"] += 1
    
    # Save preview index file
    if preview_index and not dry_run:
        index_file = session_path / "preview_frames_index.json"
        preview_index.sort(key=lambda x: x["timestamp"])
        with open(index_file, "w") as f:
            json.dump(preview_index, f)
        logger.info(f"Saved preview index with {len(preview_index)} entries")
    
    return stats


def main():
    parser = argparse.ArgumentParser(description="Generate preview frames for existing sessions")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually generate files")
    parser.add_argument("--session", type=str, help="Only process specific session folder name")
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("Preview Frame Generator")
    logger.info("=" * 60)
    logger.info(f"Sessions base: {SESSIONS_BASE}")
    logger.info(f"Preview width: {PREVIEW_WIDTH}px")
    logger.info(f"Preview quality: {PREVIEW_QUALITY}")
    if args.dry_run:
        logger.info("DRY RUN - No files will be created")
    logger.info("=" * 60)
    
    if not SESSIONS_BASE.exists():
        logger.error(f"Sessions folder not found: {SESSIONS_BASE}")
        sys.exit(1)
    
    # Get session folders to process
    if args.session:
        session_folders = [SESSIONS_BASE / args.session]
        if not session_folders[0].exists():
            logger.error(f"Session folder not found: {session_folders[0]}")
            sys.exit(1)
    else:
        session_folders = [f for f in SESSIONS_BASE.iterdir() if f.is_dir()]
    
    logger.info(f"Found {len(session_folders)} session folders")
    
    total_stats = {
        "sessions_processed": 0,
        "frames_found": 0,
        "previews_existing": 0,
        "previews_created": 0,
        "errors": 0
    }
    
    for session_path in sorted(session_folders):
        logger.info(f"\nProcessing: {session_path.name}")
        
        stats = generate_preview_for_session(session_path, args.dry_run)
        
        total_stats["sessions_processed"] += 1
        total_stats["frames_found"] += stats["frames_found"]
        total_stats["previews_existing"] += stats["previews_existing"]
        total_stats["previews_created"] += stats["previews_created"]
        total_stats["errors"] += stats["errors"]
        
        logger.info(f"  Frames: {stats['frames_found']}, "
                   f"Existing previews: {stats['previews_existing']}, "
                   f"Created: {stats['previews_created']}, "
                   f"Errors: {stats['errors']}")
    
    logger.info("\n" + "=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Sessions processed: {total_stats['sessions_processed']}")
    logger.info(f"Total frames found: {total_stats['frames_found']}")
    logger.info(f"Previews already existing: {total_stats['previews_existing']}")
    logger.info(f"Previews created: {total_stats['previews_created']}")
    logger.info(f"Errors: {total_stats['errors']}")
    
    if args.dry_run:
        logger.info("\nThis was a dry run. Run without --dry-run to actually generate files.")


if __name__ == "__main__":
    main()
