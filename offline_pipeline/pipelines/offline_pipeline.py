"""Offline pipeline orchestrator.

Six stages (handoff doc §3.1):
  1. Frame extraction (ffmpeg/OpenCV) at target FPS → sessions/{sid}/frames/
  2. Expert inference (YOLO + Phase + Triplet), GPU-batched
  3. Window aggregation (15s)
  4. Gemini Batch API for Chinese summaries
  5. Embedding generation (TODO — wire existing embedding_service)
  6. Bleeding annotation (TODO — P2)

Designed to be usable either as a library (`run_pipeline(...)`) or via the
CLI (`scripts/run_offline.py`).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..data_models import FrameExpertResult, JobState, WindowContext
from ..services import yolo_expert, phase_expert, triplet_expert
from ..services.gemini_batch_client import BatchWindowRequest, GeminiBatchClient

logger = logging.getLogger(__name__)


GEMINI_SYSTEM_PROMPT = """你是腹腔镜手术分析系统。基于专家检测结果与窗口代表帧，\
生成 15 秒窗口的简洁中文总结。

【输出要求】
- 纯中文，300 字以内
- 包含阶段、器械动作、组织状态
- 末尾独立一行以 [others] 开头标注 hem_loc/gauze/bleeding/blur/out_of_body 中存在的项
"""


# ----------------------- Stage 1: frame extraction -----------------------
def get_video_metadata(video_path: str) -> Dict[str, Any]:
    """Return basic video metadata using OpenCV only.

    The deployment machines do not consistently have ffprobe installed, while
    OpenCV is already required by the offline pipeline.
    """
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    return {
        "fps": src_fps,
        "frame_count": frame_count,
        "duration_sec": frame_count / src_fps if src_fps else 0.0,
        "width": width,
        "height": height,
    }


def _resize_for_offline(frame, max_width: int):
    if not max_width or max_width <= 0:
        return frame
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    import cv2

    scale = max_width / float(w)
    return cv2.resize(frame, (max_width, max(1, int(round(h * scale)))),
                      interpolation=cv2.INTER_AREA)


def extract_frames(video_path: str, out_dir: str, target_fps: float,
                   max_duration_sec: Optional[float] = None,
                   start_time_sec: float = 0.0,
                   jpeg_quality: int = 88,
                   resize_max_width: int = 960,
                   method: str = "grab") -> List[str]:
    """Extract frames at `target_fps` using OpenCV (ffmpeg-free).

    Samples by target timestamps and uses grab/retrieve to avoid JPEG encoding
    for skipped frames. Writes JPEGs named frame_00000000.jpg.
    """
    import cv2
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if target_fps <= 0:
        raise ValueError(f"target_fps must be > 0, got {target_fps}")
    start_frame = max(0, int(round(start_time_sec * src_fps)))
    end_frame = n_frames
    if max_duration_sec and max_duration_sec > 0:
        end_frame = min(end_frame, start_frame + int(round(max_duration_sec * src_fps)))
    step = max(1, int(round(src_fps / target_fps)))
    logger.info(
        "[extract] src_fps=%.2f n_frames=%d start=%d end=%d step=%d target=%.3f fps",
        src_fps, n_frames, start_frame, end_frame, step, target_fps,
    )

    paths: List[str] = []
    if start_frame:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    idx_in = start_frame
    idx_out = 0
    next_sample = start_frame
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)]
    sample_indices = list(range(start_frame, end_frame, step))
    if method == "seek":
        for src_idx in sample_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, src_idx)
            ret, frame = cap.read()
            if not ret:
                continue
            frame = _resize_for_offline(frame, resize_max_width)
            p = str(out / f"frame_{idx_out:08d}.jpg")
            cv2.imwrite(p, frame, encode_params)
            paths.append(p)
            idx_out += 1
    else:
        while idx_in < end_frame:
            if idx_in < next_sample:
                if not cap.grab():
                    break
                idx_in += 1
                continue
            ret, frame = cap.read()
            if not ret:
                break
            frame = _resize_for_offline(frame, resize_max_width)
            p = str(out / f"frame_{idx_out:08d}.jpg")
            cv2.imwrite(p, frame, encode_params)
            paths.append(p)
            idx_out += 1
            idx_in += 1
            next_sample += step
    cap.release()
    logger.info("[extract] wrote %d frames to %s", len(paths), out_dir)
    return paths


# ----------------------- Stage 2: experts -----------------------
def run_experts(frame_paths: List[str], cfg: Dict,
                progress_cb=None) -> List[FrameExpertResult]:
    """Run 3 experts concurrently on the same GPU.

    Rationale: 1 FPS sampling + small models (<5 GB combined) leaves GPU
    massively underutilized when run sequentially. Threading lets PyTorch
    overlap CUDA streams across the three models. Weights are loaded in
    parallel too (load dominates for short videos).
    """
    from concurrent.futures import ThreadPoolExecutor
    # Pre-import heavy modules to avoid Python's per-module import lock
    # deadlocking when 3 expert threads start concurrently.
    import torchvision  # noqa: F401
    import torchvision.transforms  # noqa: F401
    import torchvision.models  # noqa: F401
    import timm  # noqa: F401
    import ultralytics  # noqa: F401

    target_fps = cfg["sampling"]["target_fps"]
    results: List[FrameExpertResult] = [
        FrameExpertResult(frame_idx=i, timestamp=i / target_fps, frame_path=p)
        for i, p in enumerate(frame_paths)
    ]

    done = [0]  # mutable shared counter for progress
    def _bump():
        done[0] += 1
        if progress_cb:
            progress_cb(done[0] / 3.0)

    def _run_yolo():
        y = yolo_expert.load_from_config(cfg)
        if y is None:
            logger.info("[experts] YOLO disabled")
            _bump(); return
        logger.info("[experts] YOLO on %d frames", len(frame_paths))
        tools = y.infer_paths(frame_paths)
        for r, t in zip(results, tools):
            r.tools = t
        _bump()

    def _run_phase():
        p = phase_expert.load_from_config(cfg)
        if p is None:
            logger.info("[experts] Phase disabled")
            _bump(); return
        logger.info("[experts] Phase on %d frames", len(frame_paths))
        probs = p.infer_paths(frame_paths)
        smoothed, labels = p.smooth_and_decode(probs)
        for r, sp, lab in zip(results, smoothed, labels):
            r.phase_probs = sp
            r.phase_label = lab
        _bump()

    def _run_triplet():
        t = triplet_expert.load_from_config(cfg)
        if t is None:
            logger.info("[experts] Triplet disabled")
            _bump(); return
        logger.info("[experts] Triplet on %d frames", len(frame_paths))
        tri, probs = t.infer_paths(frame_paths)
        for i, (r, tr) in enumerate(zip(results, tri)):
            r.triplets = tr
            r.ivt_probs = probs[i]
        _bump()

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="expert") as ex:
        futures = [ex.submit(_run_yolo),
                   ex.submit(_run_phase),
                   ex.submit(_run_triplet)]
        for fut in futures:
            fut.result()  # propagate exceptions
    return results


# ----------------------- Stage 3: window aggregation -----------------------
_SHARPNESS_CACHE: Dict[str, float] = {}


def _frame_sharpness(path: str) -> float:
    cached = _SHARPNESS_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        import cv2
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            val = 0.0
        else:
            val = float(cv2.Laplacian(img, cv2.CV_64F).var())
    except Exception:
        val = 0.0
    _SHARPNESS_CACHE[path] = val
    return val


def _confidence_score(frame: FrameExpertResult) -> float:
    tool_conf = max([float(d.get("confidence", 0.0)) for d in frame.tools] or [0.0])
    tri_conf = max([float(d.get("confidence", 0.0)) for d in frame.triplets] or [0.0])
    return max(tool_conf, tri_conf)


def _select_representative_frames(frames: List[FrameExpertResult],
                                  count: int,
                                  strategy: str) -> List[str]:
    if count >= len(frames):
        return [f.frame_path for f in frames]
    if strategy == "even":
        idx = np.linspace(0, len(frames) - 1, count).astype(int)
        return [frames[int(i)].frame_path for i in idx]

    # Divide the window into segments, then pick the clearest/high-evidence
    # frame from each segment. This keeps temporal coverage while avoiding
    # blurry or low-information representative frames for Gemini.
    selected: List[str] = []
    boundaries = np.linspace(0, len(frames), count + 1).astype(int)
    for left, right in zip(boundaries[:-1], boundaries[1:]):
        segment = frames[left:max(left + 1, right)]
        sharp_vals = [_frame_sharpness(f.frame_path) for f in segment]
        sharp_p95 = max(float(np.percentile(sharp_vals, 95)), 1.0) if sharp_vals else 1.0
        best = max(
            segment,
            key=lambda f: min(_frame_sharpness(f.frame_path) / sharp_p95, 1.5)
            + 0.75 * _confidence_score(f),
        )
        selected.append(best.frame_path)
    return selected


def aggregate_windows(results: List[FrameExpertResult], cfg: Dict,
                      session_id: str) -> List[WindowContext]:
    window_dur = cfg["sampling"]["window_duration_sec"]
    reps_per_window = cfg["sampling"]["frames_per_window_for_gemini"]
    if not results:
        return []
    total_dur = results[-1].timestamp + 1e-3
    n_windows = max(1, int(np.ceil(total_dur / window_dur)))
    max_windows = cfg.get("sampling", {}).get("max_windows")
    if max_windows:
        n_windows = min(n_windows, int(max_windows))
    rep_strategy = cfg.get("sampling", {}).get("representative_strategy", "quality")

    windows: List[WindowContext] = []
    for wid in range(n_windows):
        t0 = wid * window_dur
        t1 = (wid + 1) * window_dur
        frames = [r for r in results if t0 <= r.timestamp < t1]
        if not frames:
            continue

        # Dominant phase + transitions
        phase_seq = [f.phase_label for f in frames if f.phase_label]
        dominant = Counter(phase_seq).most_common(1)[0][0] if phase_seq else "unknown"
        transitions: List[Dict[str, Any]] = []
        for a, b in zip(frames, frames[1:]):
            if a.phase_label and b.phase_label and a.phase_label != b.phase_label:
                transitions.append({
                    "t": b.timestamp, "from": a.phase_label, "to": b.phase_label,
                })

        # Tool frequencies (per-frame occurrence)
        tool_counter: Counter = Counter()
        for f in frames:
            seen = {d["label"] for d in f.tools}
            for label in seen:
                tool_counter[label] += 1

        # Triplet summary (aggregate across frames, dedupe by label)
        tri_counter: Counter = Counter()
        tri_conf: Dict[str, float] = {}
        for f in frames:
            for t in f.triplets:
                lab = t["ivt_label"]
                tri_counter[lab] += 1
                tri_conf[lab] = max(tri_conf.get(lab, 0.0), t["confidence"])
        triplet_summary = [
            {"ivt_label": lab, "count": cnt, "mean_conf": tri_conf[lab]}
            for lab, cnt in tri_counter.most_common(10)
        ]

        reps = _select_representative_frames(frames, reps_per_window, rep_strategy)

        phase_mean = None
        if any(f.phase_probs is not None for f in frames):
            stacked = np.stack([f.phase_probs for f in frames
                                if f.phase_probs is not None])
            phase_mean = stacked.mean(axis=0).tolist()

        windows.append(WindowContext(
            window_id=wid,
            session_id=session_id,
            start_time=t0,
            end_time=min(t1, total_dur),
            dominant_phase=dominant,
            phase_transitions=transitions,
            tool_frequencies=dict(tool_counter),
            triplet_summary=triplet_summary,
            representative_frames=reps,
            phase_probs_mean=phase_mean,
        ))
    return windows


# ----------------------- Stage 4: Gemini batch -----------------------
def build_batch_requests(windows: List[WindowContext],
                         session_id: str,
                         history_ctx_size: int = 3) -> List[BatchWindowRequest]:
    """One batch request per window, with rolling history context."""
    reqs: List[BatchWindowRequest] = []
    for i, w in enumerate(windows):
        history = windows[max(0, i - history_ctx_size):i]
        history_text = ""
        if history:
            history_text = "\n".join(
                f"  窗口{h.window_id}: 阶段={h.dominant_phase}, 工具="
                f"{','.join(h.tool_frequencies.keys()) or '无'}"
                for h in history
            )
            history_text = f"\n【历史上下文】\n{history_text}\n"

        prompt = (
            GEMINI_SYSTEM_PROMPT
            + "\n\n【专家检测结果】\n" + w.to_prompt_context()
            + history_text
            + "\n\n请输出中文总结："
        )
        reqs.append(BatchWindowRequest(
            window_id=f"{session_id}:w{w.window_id}",
            prompt_text=prompt,
            image_paths=w.representative_frames,
        ))
    return reqs


def build_expert_window_summaries(windows: List[WindowContext]) -> Dict[int, Dict[str, Any]]:
    """Deterministic local summaries for fast offline iteration.

    These are not a replacement for Gemini; they are a cheap QA artifact that
    lets us tune sampling/windowing before paying batch latency.
    """
    summaries: Dict[int, Dict[str, Any]] = {}
    for w in windows:
        tools = ", ".join(
            f"{name}({count})"
            for name, count in sorted(w.tool_frequencies.items(), key=lambda x: -x[1])[:6]
        ) or "未稳定检测到器械"
        triplets = ", ".join(
            f"{t['ivt_label']}({t['count']}x)"
            for t in w.triplet_summary[:5]
        ) or "未稳定检测到动作三元组"
        transitions = "; ".join(
            f"{t['t']:.1f}s {t['from']}->{t['to']}"
            for t in w.phase_transitions[:5]
        ) or "无明显阶段切换"
        text = (
            f"{w.start_time:.0f}-{w.end_time:.0f}s：阶段以 {w.dominant_phase} 为主；"
            f"器械证据：{tools}；动作证据：{triplets}；阶段变化：{transitions}。"
        )
        summaries[w.window_id] = {
            "text": text,
            "source": "expert_local",
            "representative_frames": w.representative_frames,
        }
    return summaries


def run_gemini_batch(windows: List[WindowContext], cfg: Dict,
                     work_dir: str, session_id: str,
                     state_cb=None) -> Dict[int, Dict[str, Any]]:
    """Returns dict: window_id → gemini response dict (with 'text' parsed)."""
    client = GeminiBatchClient(cfg["gemini"])
    reqs = build_batch_requests(windows, session_id)
    raw = client.run(reqs, work_dir=work_dir,
                     display_name=f"offline_{session_id}",
                     on_state=state_cb)

    # Map back by metadata.window_id
    out: Dict[int, Dict[str, Any]] = {}
    for entry in raw:
        wid_key = entry.get("metadata", {}).get("window_id", "")
        try:
            wid = int(wid_key.split(":w")[-1])
        except Exception:
            continue
        resp = entry.get("response", {})
        text = ""
        try:
            text = resp["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            text = json.dumps(resp)[:500]
        out[wid] = {"text": text, "raw": resp}
    return out


# ----------------------- Orchestrator -----------------------
def run_pipeline(video_path: str, cfg: Dict,
                 job_state: Optional[JobState] = None,
                 persist_json: bool = True) -> Dict[str, Any]:
    timings: Dict[str, float] = {}
    pipeline_t0 = time.perf_counter()
    sid = uuid.uuid4().hex[:8]
    session_dir = Path(cfg["storage"]["sessions_root"]) / sid
    frames_dir = session_dir / "frames"
    session_dir.mkdir(parents=True, exist_ok=True)

    if job_state is None:
        job_state = JobState(job_id=f"offline_{sid}", session_id=sid,
                             video_path=video_path,
                             created_at=time.time(), updated_at=time.time())
    job_state.session_id = sid

    def _touch(**kw):
        for k, v in kw.items():
            setattr(job_state, k, v)
        job_state.updated_at = time.time()
        if persist_json:
            (session_dir / "job.json").write_text(
                json.dumps(job_state.to_dict(), ensure_ascii=False, indent=2))

    try:
        # Stage 1
        _touch(status="extracting")
        metadata = get_video_metadata(video_path)
        (session_dir / "video_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2)
        )
        target_fps = cfg["sampling"]["target_fps"]
        t0 = time.perf_counter()
        extract_cfg = cfg.get("extraction", {})
        frames = extract_frames(
            video_path,
            str(frames_dir),
            target_fps,
            max_duration_sec=extract_cfg.get("max_duration_sec"),
            start_time_sec=float(extract_cfg.get("start_time_sec", 0.0) or 0.0),
            jpeg_quality=int(extract_cfg.get("jpeg_quality", 88)),
            resize_max_width=int(extract_cfg.get("resize_max_width", 960)),
            method=str(extract_cfg.get("method", "grab")),
        )
        timings["extract_sec"] = time.perf_counter() - t0
        _touch(frames_extracted=len(frames), frames_total=len(frames))

        # Stage 2
        _touch(status="experts")
        t0 = time.perf_counter()
        results = run_experts(frames, cfg,
                              progress_cb=lambda p: _touch(experts_done=p))
        timings["experts_sec"] = time.perf_counter() - t0

        # Stage 3
        t0 = time.perf_counter()
        windows = aggregate_windows(results, cfg, sid)
        timings["aggregate_sec"] = time.perf_counter() - t0
        _touch(windows_total=len(windows))

        # persist per-frame + windows locally — useful for P2 / debugging
        (session_dir / "frames_results.json").write_text(json.dumps(
            [{k: (v.tolist() if hasattr(v, "tolist") else v)
              for k, v in asdict(r).items()} for r in results],
            ensure_ascii=False))
        (session_dir / "windows.json").write_text(json.dumps(
            [asdict(w) for w in windows], ensure_ascii=False, indent=2, default=list))
        expert_summaries = build_expert_window_summaries(windows)
        (session_dir / "expert_window_summaries.json").write_text(json.dumps(
            {str(k): v for k, v in expert_summaries.items()}, ensure_ascii=False, indent=2))

        # Stage 4
        if cfg.get("gemini", {}).get("skip_batch"):
            logger.warning("[pipeline] gemini.skip_batch=true — skipping batch stage")
            summaries: Dict[int, Dict[str, Any]] = {}
        else:
            _touch(status="batch_pending")
            t0 = time.perf_counter()
            summaries = run_gemini_batch(
                windows, cfg, work_dir=str(session_dir / "batch"),
                session_id=sid,
                state_cb=lambda s: _touch(batch_state=s),
            )
            timings["gemini_batch_sec"] = time.perf_counter() - t0
            for wid, _ in summaries.items():
                job_state.windows_done += 1
            _touch()

        (session_dir / "gemini_summaries.json").write_text(json.dumps(
            {str(k): v for k, v in summaries.items()}, ensure_ascii=False, indent=2))

        # Stage 5: Embedding — TODO: reuse video_stream_app/backend/services/embedding_service
        _touch(status="embedding")
        # Placeholder: persist a minimal embeddings.json stub to be filled in
        (session_dir / "embeddings.json").write_text(json.dumps({
            "session_id": sid,
            "windows": [{"window_id": w.window_id, "start": w.start_time,
                         "end": w.end_time,
                         "text": summaries.get(w.window_id, {}).get("text", "")}
                        for w in windows],
        }, ensure_ascii=False, indent=2))

        # Stage 6: Bleeding annotation — TODO (P2)

        # MySQL write — TODO: reuse existing analysis_results schema;
        # add columns (triplets, expert_source) per handoff §10(5).

        timings["total_sec"] = time.perf_counter() - pipeline_t0
        (session_dir / "timings.json").write_text(json.dumps(
            timings, ensure_ascii=False, indent=2))

        _touch(status="done")
        return {"session_id": sid, "session_dir": str(session_dir),
                "windows": len(windows), "summaries": len(summaries),
                "expert_summaries": len(expert_summaries),
                "frames": len(frames), "timings": timings,
                "metadata": metadata}
    except Exception as e:
        logger.exception("[pipeline] failed")
        _touch(status="failed", error=str(e))
        raise
