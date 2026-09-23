"""Compare stored summaries with local image/expert review without changing the DB."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from backend.services.mysql_service import get_mysql_service
from backend.services.window_reanalysis import load_review_frames, analyze_review


async def main(args):
    store = get_mysql_service()
    session = store.get_video_session(args.session)
    if not session:
        raise ValueError("Session not found")
    rows = {row["window_id"]: row for row in store.get_all_window_summaries(args.session)}
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for window_id in args.windows:
        row = rows[window_id]
        images, timestamps = load_review_frames(session, row["window_start"], row["window_end"])
        sheet = Image.new("RGB", (1280, 768), "#181818")
        draw = ImageDraw.Draw(sheet)
        for i, (image, timestamp) in enumerate(zip(images, timestamps)):
            x, y = (i % 2) * 640, (i // 2) * 384
            sheet.paste(image, (x, y + 22))
            draw.text((x + 8, y + 4), f"frame {i + 1} / {timestamp:.2f}s", fill="white")
        sheet.save(args.output / f"window_{window_id + 1}_frames.jpg")
        start = time.monotonic()
        result = await analyze_review(images, timestamps,
                                      experts=(row.get("others") or {}).get("experts"),
                                      phase=row.get("surgical_phase"))
        results.append({"window_id": window_id, "start": row["window_start"], "end": row["window_end"],
                        "phase": row.get("surgical_phase"), "timestamps": timestamps,
                        "previous_summary": row["glm_summary"], "review": result,
                        "elapsed_seconds": round(time.monotonic() - start, 3)})
        (args.output / "comparison.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--windows", nargs="+", type=int, required=True, help="Zero-based stored window IDs")
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
