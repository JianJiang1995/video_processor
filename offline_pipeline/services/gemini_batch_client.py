"""Gemini Batch API client.

Builds JSONL request files, submits a batch job, polls, and downloads results.
50% cheaper than realtime; 24h SLA, usually 5–30 min. Handoff doc §3.2.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class BatchWindowRequest:
    window_id: str            # unique id used for result matching
    prompt_text: str
    image_paths: List[str]    # small representative frames
    generation_config: Optional[Dict[str, Any]] = None


def _b64_jpeg(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


class GeminiBatchClient:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        api_key = os.environ.get(cfg.get("api_key_env", "GEMINI_API_KEY"))
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set (configure gemini.api_key_env)"
            )
        from google import genai
        self._genai = genai
        self.client = genai.Client(api_key=api_key)
        self.model = cfg.get("model", "gemini-3.1-pro-preview")
        self.poll_interval = cfg.get("poll_interval_sec", 30)
        self.max_jsonl_bytes = cfg.get("max_batch_jsonl_bytes", 20_000_000)
        self.default_gen_cfg = {
            "temperature": cfg.get("temperature", 0.1),
            "maxOutputTokens": cfg.get("max_output_tokens", 2048),
            "thinkingConfig": {"thinkingBudget": cfg.get("thinking_budget", 512)},
        }

    # ---------- JSONL building ----------
    def build_jsonl(self, requests: Iterable[BatchWindowRequest],
                    out_path: str) -> List[str]:
        """Write one or more JSONL shards (split when exceeding max_jsonl_bytes).

        Returns the list of shard paths produced.
        """
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        shards: List[str] = []
        shard_idx = 0
        current = out.with_suffix(f".{shard_idx}.jsonl")
        shards.append(str(current))
        fh = open(current, "w")
        size = 0
        try:
            for req in requests:
                parts: List[Dict[str, Any]] = [{"text": req.prompt_text}]
                for img in req.image_paths:
                    parts.append({
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": _b64_jpeg(img),
                        }
                    })
                body = {
                    "request": {
                        "contents": [{"parts": parts}],
                        "generationConfig": req.generation_config or self.default_gen_cfg,
                    },
                    "metadata": {"window_id": req.window_id},
                }
                line = json.dumps(body, ensure_ascii=False) + "\n"
                encoded = line.encode("utf-8")
                if size + len(encoded) > self.max_jsonl_bytes and size > 0:
                    fh.close()
                    shard_idx += 1
                    current = out.with_suffix(f".{shard_idx}.jsonl")
                    shards.append(str(current))
                    fh = open(current, "w")
                    size = 0
                fh.write(line)
                size += len(encoded)
        finally:
            fh.close()
        logger.info("[gemini-batch] wrote %d shard(s): %s", len(shards), shards)
        return shards

    # ---------- Submission / polling ----------
    def submit_shard(self, jsonl_path: str, display_name: str) -> Any:
        logger.info("[gemini-batch] uploading %s", jsonl_path)
        uploaded = self.client.files.upload(
            file=jsonl_path,
            config={"display_name": display_name},
        )
        job = self.client.batches.create(
            model=self.model,
            src=uploaded.name,
            config={"display_name": display_name},
        )
        logger.info("[gemini-batch] submitted job=%s state=%s",
                    job.name, job.state.name)
        return job

    def poll_until_done(self, job: Any,
                        on_state: Optional[Callable[[str], None]] = None) -> Any:
        terminal = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED",
                    "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}
        while job.state.name not in terminal:
            time.sleep(self.poll_interval)
            job = self.client.batches.get(name=job.name)
            if on_state:
                on_state(job.state.name)
        return job

    def download_results(self, job: Any) -> List[Dict[str, Any]]:
        if job.state.name != "JOB_STATE_SUCCEEDED":
            raise RuntimeError(f"batch job {job.name} ended in state {job.state.name}")
        dest_name = job.dest.file_name
        raw = self.client.files.download(file=dest_name)
        if isinstance(raw, (bytes, bytearray)):
            text = raw.decode("utf-8")
        else:
            text = raw
        results: List[Dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                results.append(json.loads(line))
        return results

    # ---------- Convenience: all-in-one ----------
    def run(self, requests: List[BatchWindowRequest],
            work_dir: str, display_name: str,
            on_state: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
        shards = self.build_jsonl(requests, str(Path(work_dir) / "batch_requests"))
        all_results: List[Dict[str, Any]] = []
        for i, shard in enumerate(shards):
            job = self.submit_shard(shard, f"{display_name}_shard{i}")
            job = self.poll_until_done(job, on_state=on_state)
            all_results.extend(self.download_results(job))
        return all_results
