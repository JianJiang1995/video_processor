"""On-demand window review, independent of live capture and analysis cursors."""
import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Path

from ..services.mysql_service import get_mysql_service
from ..services.window_reanalysis import load_review_frames, analyze_review

router = APIRouter(prefix="/api/analysis", tags=["analysis"])
logger = logging.getLogger(__name__)
_active_reviews = set()


@router.post("/reanalyze-window/{session_id}/{window_id}")
async def reanalyze_window(session_id: str, window_id: int = Path(ge=0)):
    store = get_mysql_service()
    # The live-session cache is created before frame storage and may omit its
    # path. Persisted session metadata is authoritative for historical images.
    session = await asyncio.to_thread(store.get_video_session, session_id)
    if not session:
        raise HTTPException(404, "review_session_missing")
    rows = await asyncio.to_thread(store.get_all_window_summaries, session_id)
    row = next((r for r in rows if r["window_id"] == window_id), None)
    if not row:
        raise HTTPException(404, "review_window_missing")
    # One bounded manual request at a time, leaving capacity for live inference.
    if _active_reviews:
        raise HTTPException(409, "review_busy")
    key = (session_id, window_id)
    _active_reviews.add(key)
    try:
        start, end = row["window_start"], row["window_end"]
        if start is None or end is None or not 0 < end - start <= 30:
            raise HTTPException(422, "review_invalid_range")
        images, timestamps = await asyncio.to_thread(load_review_frames, session, start, end)
        result = await analyze_review(images, timestamps,
                                      experts=(row.get("others") or {}).get("experts"),
                                      phase=row.get("surgical_phase"))
        metadata = {
            "revision": uuid4().hex,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "model": result["model"], "frame_count": len(images),
            "timestamps": timestamps,
            "experts": result.get("experts", {}),
        }
        updated = await asyncio.to_thread(
            store.save_window_review, session_id, window_id,
            result["summary"], result["visual"], metadata,
        )
        if not updated:
            raise HTTPException(404, "review_window_missing")
        return {"session_id": session_id, "window_id": window_id,
                "start_time": start, "end_time": end, "summary": result["summary"],
                "stage": 3, "summary_en": "", "others": updated,
                "surgical_phase": row.get("surgical_phase") or "Unknown"}
    except FileNotFoundError:
        raise HTTPException(410, "review_frames_unavailable")
    except PermissionError:
        raise HTTPException(503, "review_local_only")
    except asyncio.TimeoutError:
        raise HTTPException(504, "review_timeout")
    except (ValueError, TypeError):
        raise HTTPException(502, "review_invalid_result")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Window review failed for %s/%s", session_id, window_id)
        raise HTTPException(503, "review_model_unavailable")
    finally:
        _active_reviews.discard(key)
