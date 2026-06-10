"""Shared dataclasses for offline pipeline.

Mirrors handoff doc §4.3.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass
class FrameExpertResult:
    """Per-frame expert outputs after fusion."""
    frame_idx: int
    timestamp: float
    frame_path: str
    # YOLO
    tools: List[Dict[str, Any]] = field(default_factory=list)  # {label, confidence, x1,y1,x2,y2}
    # Phase Expert (raw probs; smoothing done at sequence level)
    phase_probs: Optional[np.ndarray] = None  # shape [7]
    phase_label: Optional[str] = None          # post-smoothing
    # Triplet Expert (carried from the clip this frame belongs to)
    triplets: List[Dict[str, Any]] = field(default_factory=list)  # top-K [{ivt_label, conf}]
    ivt_probs: Optional[np.ndarray] = None  # full 100-dim sigmoid probs (for mAP)


@dataclass
class WindowContext:
    """15-second aggregation for Gemini prompt."""
    window_id: int
    session_id: str
    start_time: float
    end_time: float
    dominant_phase: str
    phase_transitions: List[Dict[str, Any]]        # [{t, from, to}]
    tool_frequencies: Dict[str, int]
    triplet_summary: List[Dict[str, Any]]          # top-K [{ivt_label, count, mean_conf}]
    representative_frames: List[str]               # absolute frame paths
    phase_probs_mean: Optional[List[float]] = None

    def to_prompt_context(self) -> str:
        """Render expert evidence section for Gemini prompt."""
        lines = [
            f"【窗口 {self.window_id}】{self.start_time:.1f}s - {self.end_time:.1f}s",
            f"识别阶段: {self.dominant_phase}",
        ]
        if self.tool_frequencies:
            tools = ", ".join(f"{k} ({v} 帧)" for k, v in
                              sorted(self.tool_frequencies.items(), key=lambda x: -x[1]))
            lines.append(f"出现工具: {tools}")
        if self.triplet_summary:
            tri = ", ".join(f"{t['ivt_label']} ({t['count']}x)" for t in self.triplet_summary[:5])
            lines.append(f"主要动作三元组: {tri}")
        if self.phase_transitions:
            trans = "; ".join(f"{t['t']:.1f}s {t['from']}→{t['to']}" for t in self.phase_transitions)
            lines.append(f"阶段切换: {trans}")
        return "\n".join(lines)


@dataclass
class JobState:
    job_id: str
    session_id: str
    video_path: str
    status: str = "queued"            # queued|extracting|experts|batch_pending|embedding|done|failed
    frames_extracted: int = 0
    frames_total: int = 0
    experts_done: float = 0.0
    batch_state: Optional[str] = None
    windows_done: int = 0
    windows_total: int = 0
    error: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "session_id": self.session_id,
            "status": self.status,
            "progress": {
                "frames_extracted": self.frames_extracted,
                "frames_total": self.frames_total,
                "experts_done": self.experts_done,
                "batch_state": self.batch_state,
                "windows_done": self.windows_done,
                "windows_total": self.windows_total,
            },
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
