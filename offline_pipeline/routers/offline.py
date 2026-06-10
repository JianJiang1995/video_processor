"""FastAPI router for offline pipeline.

Endpoints (handoff §4.2):
  POST /api/offline/process-video
  GET  /api/offline/status/{job_id}
  GET  /api/offline/result/{job_id}

Run standalone:
  uvicorn offline_pipeline.routers.offline:app --port 8100
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from ..data_models import JobState
from ..pipelines.offline_pipeline import run_pipeline

logger = logging.getLogger(__name__)

# Load config once
_CFG_PATH = Path(__file__).resolve().parent.parent / "config.json"
with open(_CFG_PATH) as _f:
    CONFIG: Dict[str, Any] = json.load(_f)

# In-memory job registry (persisted to disk per job under jobs/{sid}/job.json)
_JOBS: Dict[str, JobState] = {}
_LOCK = threading.Lock()


class ProcessVideoReq(BaseModel):
    video_path: str
    sample_fps: Optional[int] = None
    window_duration: Optional[float] = None
    enable_bleeding: Optional[bool] = False


router = APIRouter(prefix="/api/offline", tags=["offline"])


def _estimate(video_path: str, sample_fps: int, window: float) -> Dict[str, Any]:
    """Lightweight estimate using ffprobe. Best-effort; falls back to zeros."""
    import subprocess
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, check=True)
        dur = float(r.stdout.strip())
        return {
            "estimated_time_sec": int(dur * 0.2),  # rough: 1h video ~ 12 min
            "expected_windows": int(dur // window) + 1,
            "expected_frames": int(dur * sample_fps),
        }
    except Exception:
        return {"estimated_time_sec": 0, "expected_windows": 0, "expected_frames": 0}


@router.post("/process-video")
def process_video(body: ProcessVideoReq):
    if not Path(body.video_path).exists():
        raise HTTPException(404, f"video not found: {body.video_path}")

    cfg = json.loads(json.dumps(CONFIG))  # deep copy
    if body.sample_fps:
        cfg["sampling"]["target_fps"] = body.sample_fps
    if body.window_duration:
        cfg["sampling"]["window_duration_sec"] = body.window_duration
    if body.enable_bleeding:
        cfg["bleeding"]["enabled"] = True

    sid = uuid.uuid4().hex[:8]
    job_id = f"offline_{time.strftime('%Y%m%d')}_{sid}"
    state = JobState(job_id=job_id, session_id=sid, video_path=body.video_path,
                     created_at=time.time(), updated_at=time.time())
    with _LOCK:
        _JOBS[job_id] = state

    def _worker():
        try:
            run_pipeline(body.video_path, cfg, job_state=state)
        except Exception as e:
            logger.exception("[offline-api] job %s failed: %s", job_id, e)

    threading.Thread(target=_worker, daemon=True, name=job_id).start()

    est = _estimate(body.video_path, cfg["sampling"]["target_fps"],
                    cfg["sampling"]["window_duration_sec"])
    return {"job_id": job_id, "session_id": sid, "status": "queued", **est}


@router.get("/status/{job_id}")
def status(job_id: str):
    with _LOCK:
        st = _JOBS.get(job_id)
    if not st:
        raise HTTPException(404, "job not found")
    return st.to_dict()


@router.get("/result/{job_id}")
def result(job_id: str):
    with _LOCK:
        st = _JOBS.get(job_id)
    if not st:
        raise HTTPException(404, "job not found")
    if st.status != "done":
        raise HTTPException(409, f"job not done (status={st.status})")
    session_dir = Path(CONFIG["storage"]["sessions_root"]) / st.session_id
    payload: Dict[str, Any] = {"session_id": st.session_id,
                                "session_dir": str(session_dir)}
    for name in ("windows.json", "gemini_summaries.json", "embeddings.json"):
        p = session_dir / name
        if p.exists():
            payload[name.replace(".json", "")] = json.loads(p.read_text())
    return payload


app = FastAPI(title="Offline Pipeline", version="0.1.0")
app.include_router(router)


@app.get("/healthz")
def healthz():
    return {"ok": True}
