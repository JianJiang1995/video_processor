"""
Video Analysis API Routes
Handles GPT summarization, SAM2 masks, and TTS
"""
import asyncio
import time
from typing import Optional, List, Dict, Any, Tuple
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
import json
import hashlib
import re
import os
import tempfile
from PIL import Image
from io import BytesIO
import base64
import httpx
from urllib.parse import urlparse
from pathlib import Path

from ..database import (
    get_db, get_video_session, get_video_session_by_id,
    create_frame_analysis, get_frames_by_session,
    create_window_summary, get_summaries_by_session, get_summary_for_timestamp
)
from ..services.video_processor import VideoProcessor, build_frame_context
from ..services.gpt_summarizer import GPTSummarizer
from ..services.sam2_service import SAM2Service
from ..services.tts_service import TTSService
from ..services.model_service import get_model_service, ensure_model_loaded
from ..services.surgr1_client import get_surgr1_client, ensure_surgr1_available
from ..services.sam3_client import get_sam3_client, ensure_sam3_available
from ..services.glm_client import get_glm_client, ensure_glm_available
from ..services.vlm_factory import get_vlm_client, ensure_vlm_available, check_vlm_health, get_summarization_provider, load_config, cleanup_session_resources
from ..services.gemini_client import get_gemini_client
from ..services.embedding_service import get_embedding_service
from ..services.tts_cosyvoice_client import get_tts_client, ensure_tts_available
from ..services.mysql_service import get_mysql_service
from ..services.frame_storage_service import get_frame_storage_service
from ..services.frame_capture_service import get_frame_capture_service
from ..services.decklink_capture import DeckLinkCapture
from ..services.local_video_source import PacedVideoCapture, resolve_video_source
from ..services.video_export_service import get_video_export_service, export_tasks
from ..config import settings, ANALYSIS_SYSTEM_PROMPT

import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def get_frame_attr(frame, attr: str, default=None):
    """Helper to get attribute from frame - handles both dict and object."""
    if isinstance(frame, dict):
        return frame.get(attr, default)
    return getattr(frame, attr, default)

# Global service instances
gpt_summarizer: Optional[GPTSummarizer] = None
sam2_service: Optional[SAM2Service] = None
tts_service: Optional[TTSService] = None

# Global cancellation flags for analysis tasks
# Key: session_id, Value: bool (True = should cancel)
analysis_cancellation_flags: dict = {}

# Global flags for continuous SurgR1 processing
# Key: session_id, Value: bool (True = running)
surgr1_continuous_flags: dict = {}

# Active SurgR1 task references for cancellation
# Key: session_id, Value: list of tasks
active_surgr1_tasks: dict = {}

# In-flight local VLM refinements started after the fast expert summary.
# Key: session_id, Value: set of asyncio tasks
active_open_vlm_tasks: dict = {}
active_glm_tasks: dict = {}


def _track_open_vlm_task(session_id: str, task: asyncio.Task) -> None:
    tasks = active_open_vlm_tasks.setdefault(session_id, set())
    tasks.add(task)

    def discard(completed: asyncio.Task) -> None:
        current = active_open_vlm_tasks.get(session_id)
        if current is not None:
            current.discard(completed)
            if not current:
                active_open_vlm_tasks.pop(session_id, None)
        try:
            completed.exception()
        except (asyncio.CancelledError, Exception):
            pass

    task.add_done_callback(discard)


def _track_glm_task(session_id: str, task: asyncio.Task) -> None:
    active_glm_tasks[session_id] = task

    def discard(completed: asyncio.Task) -> None:
        if active_glm_tasks.get(session_id) is completed:
            active_glm_tasks.pop(session_id, None)
        try:
            completed.exception()
        except (asyncio.CancelledError, Exception):
            pass

    task.add_done_callback(discard)

# Text-only translation cache for UI language switching.
# Key: (target_lang, source_text)
summary_translation_cache: dict = {}

# LLM-generated key event nodes for the bottom timeline UI.
# Key: (session_id, language, max_windows, summaries_signature)
event_node_cache: dict = {}

# LLM-generated per-session clinical reports.
# Key: (session_id, language, max_windows, summaries_signature)
clinical_summary_cache: dict = {}

# Global stream start times for time synchronization
# Key: session_id, Value: float (unix timestamp when processing started)
stream_start_times: dict = {}

# R1 /health can be delayed while the model is busy. Keep a short last-good
# cache so the UI does not mark a running local service as unavailable.
surgr1_status_cache = {
    "available": False,
    "last_success": 0.0,
    "last_checked": 0.0,
}


def _legacy_surgr1_enabled() -> bool:
    """Whether the deprecated localhost SurgR1 API should receive frame batches."""
    try:
        return bool((load_config().get("services", {}).get("surgr1", {}) or {}).get("enabled", False))
    except Exception:
        return False

PHASE_LABEL_CN = {
    "Preparation": "准备阶段",
    "CalotTriangleDissection": "肝胆三角解剖",
    "ClippingCutting": "夹闭切断",
    "GallbladderDissection": "胆囊分离",
    "GallbladderRetraction": "标本袋牵拉取出",
    "CleaningCoagulation": "清洁凝血",
    "GallbladderPackaging": "胆囊取出与装袋",
    "preparation": "准备阶段",
    "calot_triangle_dissection": "肝胆三角解剖",
    "clipping_cutting": "夹闭切断",
    "gallbladder_dissection": "胆囊分离",
    "gallbladder_retraction": "标本袋牵拉取出",
    "cleaning_coagulation": "清洁凝血",
    "gallbladder_packaging": "胆囊取出与装袋",
}

PHASE_VISUAL_CONTEXT_CN = {
    "Preparation": "画面以初始暴露、入路准备和术野建立为主",
    "CalotTriangleDissection": "画面集中在肝胆三角区域，重点是暴露和精细分离",
    "ClippingCutting": "画面集中在胆囊管和胆囊动脉处理区域，重点关注夹闭、切断和安全间隙",
    "GallbladderDissection": "画面以胆囊床分离为主，重点关注组织层面和止血情况",
    "GallbladderRetraction": "画面以牵拉装有胆囊的标本袋经切口取出为主",
    "CleaningCoagulation": "画面以胆囊取出后的腹腔视野复查为主",
    "GallbladderPackaging": "画面以标本装袋、取出和收尾检查为主",
}

PHASE_EXPERT_CANONICAL = {
    "preparation": "Preparation",
    "calot_triangle_dissection": "CalotTriangleDissection",
    "clipping_cutting": "ClippingCutting",
    "gallbladder_dissection": "GallbladderDissection",
    "gallbladder_retraction": "GallbladderRetraction",
    "cleaning_coagulation": "CleaningCoagulation",
    "gallbladder_packaging": "GallbladderPackaging",
}

TOOL_LABEL_CN = {
    "bipolar": "双极电凝钳",
    "clipper": "钛夹钳",
    "grasper": "抓钳",
    "hook": "电凝钩",
    "irrigator": "冲洗器",
    "scissors": "剪刀",
    "snare": "圈套器",
    "specimen_bag": "标本袋",
}

CVS_RELEVANT_PHASES = {"CalotTriangleDissection", "ClippingCutting"}

TRIPLET_INSTRUMENT_CN = {
    "bipolar": "双极电凝钳",
    "clipper": "钛夹钳",
    "grasper": "抓钳",
    "hook": "电凝钩",
    "irrigator": "冲吸器",
    "scissors": "剪刀",
    "snare": "圈套器",
    "specimen_bag": "标本袋",
}

TRIPLET_VERB_CN = {
    "clip": "夹闭",
    "cut": "切断",
    "coagulate": "凝血处理",
    "dissect": "分离",
    "grasp": "夹持",
    "retract": "牵拉",
    "irrigate": "冲洗",
    "aspirate": "吸引",
    "pack": "装袋",
}

TRIPLET_TARGET_CN = {
    "cystic_duct": "胆囊管",
    "cystic_artery": "胆囊动脉",
    "blood_vessel": "胆囊动脉",
    "cystic_pedicle": "胆囊管",
    "gallbladder": "胆囊",
    "gallbladder_bed": "胆囊床",
    "cystic_plate": "胆囊板",
    "liver": "肝脏",
    "peritoneum": "腹膜",
    "adhesion": "粘连组织",
    "fluid": "液体",
    "specimen_bag": "标本袋",
    "hepatic_fossa": "肝窝",
    "calot_triangle": "肝胆三角",
}

CLIP_CUT_VERBS = {"clip", "cut", "coagulate"}
FORCED_TARGET_LABELS = {"cystic_duct", "cystic_artery"}


def _canonical_phase(label: str) -> str:
    return PHASE_EXPERT_CANONICAL.get(label or "", label or "Unknown")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _parse_triplet_label(label: Any) -> tuple:
    text = str(label or "").strip()
    bracket_parts = re.findall(r"\[([^\]]+)\]", text)
    if len(bracket_parts) >= 3:
        return tuple(part.strip() for part in bracket_parts[:3])
    cleaned = text.replace("[", "").replace("]", "")
    parts = [part.strip() for part in cleaned.split("-") if part.strip()]
    while len(parts) < 3:
        parts.append("")
    return tuple(parts[:3])


def _target_label_from_raw(raw_label: Any) -> str:
    label = str(raw_label or "").strip().lower()
    if label in FORCED_TARGET_LABELS:
        return label
    if label == "blood_vessel":
        return "cystic_artery"
    if label == "cystic_pedicle":
        return "cystic_duct"
    return ""


def _target_cn(label: Any) -> str:
    return "胆囊动脉" if str(label or "").strip().lower() == "cystic_artery" else "胆囊管"


def _target_hint_from_triplet(triplet: Dict[str, Any]) -> Dict[str, Any]:
    """Choose a concrete cystic target from Triplet output.

    The UI should not surface an ambiguous cystic duct/artery phrase. Triplet
    target probabilities are used as the deterministic prior, then the visual
    GPT pass can overwrite confidence and evidence while keeping a concrete
    label.
    """
    candidates: List[Dict[str, Any]] = []

    for item in (triplet or {}).get("triplet") or []:
        inst, verb, target = _parse_triplet_label(item.get("label"))
        label = _target_label_from_raw(target)
        if not label:
            continue
        conf = max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.0)))
        score = conf
        if verb in CLIP_CUT_VERBS:
            score += 0.08
        if target == "blood_vessel":
            score = conf * 0.85 + (0.06 if verb in CLIP_CUT_VERBS else 0.0)
        elif target == "cystic_pedicle":
            score = conf * 0.55 + (0.05 if verb in CLIP_CUT_VERBS else 0.0)
        candidates.append({
            "label": label,
            "confidence": conf,
            "score": score,
            "source": f"triplet:{inst}-{verb}-{target}",
        })

    for item in (triplet or {}).get("target") or []:
        raw = str(item.get("label") or "").strip().lower()
        label = _target_label_from_raw(raw)
        if not label:
            continue
        conf = max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.0)))
        weight = 0.75
        if raw == "blood_vessel":
            weight = 0.65
        elif raw == "cystic_pedicle":
            weight = 0.40
        candidates.append({
            "label": label,
            "confidence": conf,
            "score": conf * weight,
            "source": f"target:{raw}",
        })

    if not candidates:
        return {
            "label": "cystic_duct",
            "confidence": 0.05,
            "score": 0.0,
            "source": "default:cystic_duct",
        }
    best = max(candidates, key=lambda x: (x["score"], x["confidence"]))
    best["confidence"] = round(float(best.get("confidence") or 0.0), 3)
    best["score"] = round(float(best.get("score") or 0.0), 3)
    return best


def _triplet_operation_phrases(triplet: Dict[str, Any], max_items: int = 4, phase: str = "") -> List[str]:
    phrases: List[str] = []
    seen = set()
    target_hint = _target_hint_from_triplet(triplet)
    core_targets = {"cystic_duct", "cystic_artery", "blood_vessel", "cystic_pedicle"}
    safe_progress_targets = {
        "cystic_duct", "cystic_artery", "blood_vessel", "cystic_pedicle",
        "gallbladder", "gallbladder_bed", "cystic_plate", "calot_triangle",
    }
    for item in (triplet or {}).get("triplet") or []:
        conf = _safe_float(item.get("confidence"), 0.0)
        if conf < 0.12:
            continue
        inst, verb, target = _parse_triplet_label(item.get("label"))
        if not verb or verb.startswith("null"):
            continue
        inst_cn = TRIPLET_INSTRUMENT_CN.get(inst, "")
        verb_cn = TRIPLET_VERB_CN.get(verb, "")
        if not verb_cn:
            continue
        if verb == "coagulate":
            if target in {"cystic_artery", "blood_vessel"} and conf >= 0.18:
                target_cn = "胆囊动脉"
            else:
                continue
        elif verb in {"clip", "cut"}:
            if target not in core_targets:
                continue
            target_cn = TRIPLET_TARGET_CN.get(target, _target_cn(target_hint.get("label")))
        elif target in {"", "null_target", "null"}:
            target_cn = ""
        else:
            if target not in safe_progress_targets or conf < 0.18:
                continue
            target_cn = TRIPLET_TARGET_CN.get(target, "")
            if verb == "retract" and target in core_targets:
                target_cn = "胆囊颈和胆囊体"
            elif verb == "grasp" and target in core_targets:
                target_cn = "胆囊颈附近组织"
        if not target_cn:
            continue
        if inst_cn and inst_cn != target_cn:
            phrase = f"{inst_cn}{verb_cn}{target_cn}"
        else:
            phrase = f"{verb_cn}{target_cn}"
        if phrase in seen:
            continue
        seen.add(phrase)
        phrases.append(phrase)
        if len(phrases) >= max_items:
            break
    return phrases


def _bipolar_forceps_evidence(expert_pack: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Resolve the recurring blue bipolar-forceps vs single-hook conflict."""
    pack = expert_pack or {}
    triplet = pack.get("triplet") or {}
    instrument_scores = {
        str(item.get("label") or "").strip().lower(): _safe_float(item.get("confidence"), 0.0)
        for item in (triplet.get("instrument") or [])
        if isinstance(item, dict)
    }
    bipolar_action = 0.0
    for item in triplet.get("triplet") or []:
        inst, verb, _ = _parse_triplet_label(item.get("label"))
        if inst == "bipolar" and verb in {"coagulate", "dissect", "grasp", "retract"}:
            bipolar_action = max(bipolar_action, _safe_float(item.get("confidence"), 0.0))

    yolo_counts = {
        str(item.get("label") or "").strip().lower(): int(item.get("frames_seen") or 0)
        for item in ((pack.get("yolo") or {}).get("tools") or [])
        if isinstance(item, dict)
    }
    bipolar_conf = instrument_scores.get("bipolar", 0.0)
    hook_conf = instrument_scores.get("hook", 0.0)
    bipolar_frames = yolo_counts.get("bipolar", 0)
    hook_frames = yolo_counts.get("hook", 0)
    clipper_frames = yolo_counts.get("clipper", 0)
    blue_cue = pack.get("blue_bipolar_forceps") or {}
    blue_cue_confidence = _safe_float(blue_cue.get("confidence"), 0.0)
    blue_cue_detected = bool(blue_cue.get("detected") and blue_cue_confidence >= 0.55)
    phase = _canonical_phase((pack.get("phase") or {}).get("label", "") or "")

    # Triplet contributes temporal action evidence while YOLO contributes local
    # morphology. A strong hook prediction blocks the Triplet-only route so a
    # real L-shaped cautery hook is not renamed merely because bipolar is second.
    temporal_candidate = bipolar_conf >= 0.85 and bipolar_action >= 0.85 and hook_conf <= 0.10
    visual_and_model_backed = (
        blue_cue_detected
        and (bipolar_action >= 0.50 or bipolar_frames >= 2)
        and not (phase == "ClippingCutting" and clipper_frames >= 4 and bipolar_frames < 2)
    )
    return {
        "resolved": bool(visual_and_model_backed),
        "temporal_candidate": bool(temporal_candidate),
        "bipolar_confidence": round(bipolar_conf, 3),
        "hook_confidence": round(hook_conf, 3),
        "bipolar_action_confidence": round(bipolar_action, 3),
        "bipolar_frames": bipolar_frames,
        "hook_frames": hook_frames,
        "blue_cue_detected": blue_cue_detected,
        "blue_cue_confidence": round(blue_cue_confidence, 3),
    }


def _resolve_bipolar_hook_conflict(text: Any, expert_pack: Optional[Dict[str, Any]]) -> str:
    """Use fused evidence to correct a hook description to bipolar forceps."""
    out = str(text or "").strip()
    if not out or "电凝钩" not in out or not _bipolar_forceps_evidence(expert_pack).get("resolved"):
        return out
    grasper_frames = max(
        (
            int(item.get("frames_seen") or 0)
            for item in (((expert_pack or {}).get("yolo") or {}).get("tools") or [])
            if isinstance(item, dict) and str(item.get("label") or "").lower() == "grasper"
        ),
        default=0,
    )
    calot_action = "双极电凝钳反复开合夹持并分离肝胆三角内纤维脂肪组织"
    if grasper_frames >= 3:
        calot_action += "，抓钳配合牵拉以扩大关键结构暴露"
    else:
        calot_action += "，以扩大关键结构暴露"
    out = out.replace("电凝钩尖端接触并分离组织", "双极电凝钳夹持并分离局部纤维组织")
    out = re.sub(r"电凝钩(?:正在)?分离肝胆三角(?:区域)?(?:纤维脂肪)?组织", calot_action, out)
    out = re.sub(
        r"电凝钩(?:正在)?分离(?:胆囊床组织|胆囊与胆囊床粘连组织)",
        "双极电凝钳夹持并分离胆囊床粘连组织，逐步扩大胆囊与肝床间隙",
        out,
    )
    out = re.sub(r"电凝钩(?:正在)?分离", "双极电凝钳夹持并分离", out)
    out = out.replace("电凝钩", "双极电凝钳")
    return _compact_local_summary_text(out)


def _expand_vague_operation_language(text: Any, phase: Any = "") -> str:
    """Make common operation phrases specific without inventing anatomy."""
    out = str(text or "").strip()
    if not out:
        return out
    canonical_phase = _canonical_phase(str(phase or ""))
    irrigation_summary = "冲吸器清理术野内液体和组织碎屑，以恢复局部观察"
    out = out.replace(
        "使用冲吸器清除操作区域内液体和碎屑，以恢复局部观察内液体，以恢复局部观察",
        irrigation_summary,
    )
    out = out.replace(
        "使用冲吸器清理术野内液体，以恢复局部观察",
        irrigation_summary,
    )
    out = out.replace(
        "画面以初始暴露、入路准备和术野建立为主",
        "完成腹腔镜初始视野建立并调整胆囊暴露，为后续肝胆三角解剖做准备",
    )
    out = out.replace(
        "画面以胆囊床分离为主，重点关注组织层面和止血情况",
        "正在沿胆囊壁与肝床间隙继续分离粘连组织，并核查剥离面是否存在活动性出血",
    )
    out = out.replace(
        "画面以胆囊床术野观察为主",
        "正在观察胆囊床剥离面，核查残余粘连组织及活动性出血",
    )
    out = out.replace(
        "抓钳牵拉胆囊颈和胆囊体以暴露操作区域",
        "抓钳牵拉胆囊颈部并抬起胆囊体，以扩大肝胆三角及待处理结构暴露",
    )
    out = out.replace(
        "抓钳牵拉胆囊颈和胆囊体以暴露肝胆三角",
        "抓钳牵拉胆囊颈部并抬起胆囊体，以扩大肝胆三角暴露",
    )
    out = out.replace(
        "观察胆囊管和胆囊动脉处理区域",
        "观察胆囊管和胆囊动脉走行区域，核查夹闭前后的结构边界",
    )
    out = re.sub(r"冲吸器清理术野(?!内液体)", irrigation_summary, out)
    out = re.sub(
        r"电凝钩(?:正在)?分离肝胆三角(?:区域)?组织",
        "电凝钩沿肝胆三角解剖层次分离纤维脂肪组织，以逐步扩大关键结构暴露",
        out,
    )
    out = re.sub(
        r"电凝钩(?:正在)?分离(?:胆囊床组织|胆囊与胆囊床粘连组织)",
        "电凝钩沿胆囊壁与肝床间隙分离粘连组织，逐步扩大剥离范围",
        out,
    )
    out = out.replace(
        "电凝钩沿胆囊壁与肝床间隙分离粘连组织，逐步扩大剥离范围与胆囊床粘连组织",
        "电凝钩沿胆囊壁与肝床间隙分离粘连组织，逐步扩大胆囊床剥离范围",
    )
    local_hook = "电凝钩分离局部纤维组织"
    if local_hook in out:
        if canonical_phase in {"CalotTriangleDissection", "ClippingCutting"}:
            replacement = "电凝钩分离肝胆三角内纤维脂肪组织，以维持关键结构暴露"
        elif canonical_phase == "GallbladderDissection":
            replacement = "电凝钩沿胆囊壁与肝床间隙分离局部粘连组织"
        elif canonical_phase in {
            "Preparation",
            "GallbladderPackaging",
            "CleaningCoagulation",
            "PostRetrievalReview",
            "GallbladderRetraction",
        }:
            replacement = ""
        else:
            replacement = "电凝钩分离局部纤维组织并扩大操作间隙"
        out = out.replace(local_hook, replacement)
    if canonical_phase in {
        "Preparation",
        "GallbladderPackaging",
        "CleaningCoagulation",
        "PostRetrievalReview",
        "GallbladderRetraction",
    }:
        out = out.replace("电凝钩分离局部纤维组织并扩大操作间隙", "")
        out = re.sub(r"(?:^|(?<=[。；;]))\s*并扩大操作间隙[。.]?", "", out)
    out = out.replace(
        "冲洗并清理术野",
        irrigation_summary,
    )
    out = out.replace(
        "使用冲洗或吸引操作清理术野内液体，以恢复局部观察",
        irrigation_summary,
    )
    if re.fullmatch(r"当前处于夹闭切断[。.]?", out):
        out = "观察胆囊管和胆囊动脉走行区域，核查夹体位置及后续切断条件。"
    return _compact_local_summary_text(out)


def _sanitize_target_language(text: Any, target_label: Any = "cystic_duct") -> str:
    target = _target_cn(target_label)
    out = str(text or "")
    if not out:
        return out
    replacements = {
        "施夹器/钛夹钳": "钛夹钳",
        "钛夹钳/施夹器": "钛夹钳",
        "Hem-o-lok/钛夹": "Hem-o-lok夹和金属钛夹",
        "Hemolok/钛夹": "Hem-o-lok夹和金属钛夹",
        "纱布/棉片": "纱布和棉片",
        "电钩/尖端器械": "尖端器械",
        "胆囊管/胆囊动脉候选结构（尚不能区分）": target,
        "胆囊管/胆囊动脉候选结构（未确认）": target,
        "胆囊管/胆囊动脉候选结构，尚不能区分": target,
        "胆囊管/胆囊动脉候选结构": target,
        "胆囊管/胆囊动脉目标结构": target,
        "胆囊管/胆囊动脉处理区域": "胆囊管和胆囊动脉处理区域",
        "非胆囊管/胆囊动脉组织": "非目标组织",
        "胆囊管或者胆囊动脉": target,
        "胆囊管或胆囊动脉": target,
        "cystic_duct_or_artery_uncertain": target,
    }
    for src, dst in replacements.items():
        out = out.replace(src, dst)
    out = re.sub(r"胆囊管\s*/\s*胆囊动脉(?:候选结构|目标结构|候选残端|残端)?(?:（[^）]*）)?", target, out)
    out = re.sub(r"胆囊管(?:或者|或)胆囊动脉(?:候选结构|目标结构|候选残端|残端)?", target, out)
    out = re.sub(r"(?:关键)?管状结构(残端)?", f"{target}\\1", out)
    out = out.replace("尚不能区分", f"模型倾向{target}")
    out = _polish_summary_wording(out)
    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    return out


def _polish_summary_wording(text: Any) -> str:
    out = str(text or "")
    if not out:
        return out
    typo_replacements = {
        "CalotTriangleDissection": "肝胆三角解剖",
        "ClippingCutting": "夹闭切断",
        "GallbladderDissection": "胆囊分离",
        "GallbladderRetraction": "标本袋牵拉取出",
        "CleaningCoagulation": "清洁凝血",
        "GallbladderPackaging": "胆囊取出与装袋",
        "Preparation": "准备阶段",
        "Calot's triangle": "肝胆三角",
        "Calot三角": "肝胆三角",
        "清理胆囊床并核查活动性出血": "清理胆囊床并复查术野",
        "亮白细长器械尖端短时进入并接触组织，疑似穿孔/点触动作": "电凝钩尖端接触并分离组织",
        "疑似穿孔/点触动作": "电凝钩尖端接触并分离组织",
        "疑似穿孔点触": "电凝钩尖端接触并分离组织",
        "点触": "电凝钩接触",
        "亮白细长器械": "电凝钩",
        "电泥沟": "电凝钩",
        "电泥钩": "电凝钩",
        "电凝沟": "电凝钩",
        "电钩": "电凝钩",
        "太夹前": "钛夹钳",
        "钛夹前": "钛夹钳",
        "太夹钳": "钛夹钳",
        "胎夹钳": "钛夹钳",
        "动胆囊动脉": "胆囊动脉",
        "动胆囊管": "胆囊管",
    }
    for src, dst in typo_replacements.items():
        out = out.replace(src, dst)
    out = re.sub(r"【工具】[^\n。；;]*(?:[\n。；;]|$)", "", out)
    out = re.sub(r"【阶段】\s*([^\n。；;]+)", r"当前处于\1", out)
    out = re.sub(r"【操作】\s*", "", out)
    out = re.sub(r"【CVS】\s*", "", out)
    out = re.sub(r"电凝钩\s*(?:/|、|和|与|及|或)?\s*(?:剪刀|电剪)", "电凝钩", out)
    out = re.sub(r"(?:剪刀|电剪)\s*(?:/|、|和|与|及|或)?\s*电凝钩", "电凝钩", out)
    out = re.sub(r"电凝钩电凝钩", "电凝钩", out)
    out = re.sub(r"(胆囊管|胆囊动脉|胆囊板|胆囊床)残端残端", r"\1残端", out)
    out = out.replace("残端残端", "残端")
    out = out.replace("正在进行凝血或止血处理", "正在进行凝血处理")
    out = out.replace("凝血或止血处理", "凝血处理")
    out = out.replace("已已夹闭", "已夹闭")
    out = out.replace("已已闭合", "已闭合")
    out = out.replace("当前处于肝胆三角解剖板", "当前处于肝胆三角解剖")
    out = out.replace("肝胆三角解剖板", "肝胆三角解剖")
    out = re.sub(r"(Hem-o-lok夹已)已(夹闭|闭合)", r"\1\2", out)
    out = re.sub(r"(金属钛夹已)已(夹闭|闭合)", r"\1\2", out)
    out = re.sub(
        r"可见(\d+枚)?金属钛夹已可见已夹闭的?(胆囊管|胆囊动脉)残端",
        r"可见\1金属钛夹已夹闭\2",
        out,
    )
    out = re.sub(r"可见(?:白色)?Hem-o-lok夹已夹闭(胆囊管|胆囊动脉)残端", r"\1残端已由Hem-o-lok夹闭合", out)
    out = re.sub(r"可见(?:白色)?Hem-o-lok夹已夹闭(胆囊管|胆囊动脉)", r"使用Hem-o-lok夹闭合\1", out)
    out = re.sub(r"可见(?:\d+枚)?金属钛夹已夹闭(胆囊管|胆囊动脉)残端", r"\1残端已由金属钛夹闭合", out)
    out = re.sub(r"可见(?:\d+枚)?金属钛夹已夹闭(胆囊管|胆囊动脉)", r"使用金属钛夹闭合\1", out)
    out = re.sub(r"可见已夹闭的?(胆囊管|胆囊动脉)残端", r"已夹闭\1残端", out)
    out = re.sub(
        r"(可见(?:\d+枚)?金属钛夹已夹闭(胆囊管|胆囊动脉))[；;，,。]\s*已夹闭\2残端",
        r"\1",
        out,
    )
    out = re.sub(r"(已夹闭(?:胆囊管|胆囊动脉)残端)[；;，,。]\s*\1", r"\1", out)
    out = re.sub(r"(钛夹钳(?:正在)?夹闭(?:胆囊管|胆囊动脉))明显", r"\1", out)
    out = re.sub(r"(钛夹钳(?:正在)?夹闭(?:胆囊管|胆囊动脉))[，,]\s*明显", r"\1", out)
    out = re.sub(r"(Hem-o-lok夹|金属钛夹|钛夹钳|施夹器)(?:正在)?夹闭(胆囊管|胆囊动脉)明显", r"\1夹闭\2", out)
    out = re.sub(r"(?:钛夹钳夹持(胆囊管|胆囊动脉)[；;，,。]\s*)钛夹钳(?:正在)?夹闭\1", r"钛夹钳夹闭\1", out)
    out = re.sub(r"钛夹钳(?:正在)?夹闭(胆囊管|胆囊动脉)[；;，,。]\s*钛夹钳夹持\1", r"钛夹钳夹闭\1", out)
    out = re.sub(r"(Hem-o-lok夹|金属钛夹)已夹闭(胆囊管|胆囊动脉)残端", r"\2残端已由\1闭合", out)
    out = re.sub(r"已夹闭(胆囊管|胆囊动脉)残端", r"\1残端已夹闭", out)
    out = re.sub(r"(胆囊管|胆囊动脉)残端已由(Hem-o-lok夹|金属钛夹)闭合[；;，,。]\s*\1残端已夹闭", r"\1残端已由\2闭合", out)
    out = re.sub(r"(胆囊管|胆囊动脉)残端已夹闭[；;，,。]\s*\1残端已夹闭", r"\1残端已夹闭", out)
    out = out.replace("冲吸器分离胆囊", "冲吸器清理术野")
    out = out.replace("冲吸器分离局部组织", "冲吸器清理术野")
    out = out.replace("电凝钩分离局部组织", "电凝钩分离局部纤维组织")
    out = out.replace("电凝钩尖端接触并分离组织", "电凝钩分离局部纤维组织")
    out = out.replace("可见双极器械接触组织，尚未确认止血操作", "双极电凝接触局部组织")
    out = re.sub(r"正在冲洗或清理术野", "冲洗并清理术野", out)
    out = re.sub(r"正在牵拉和暴露组织", "抓钳牵拉胆囊颈和胆囊体以暴露操作区域", out)
    out = out.replace(
        "画面集中在胆囊管和胆囊动脉处理区域，重点关注夹闭、切断和安全间隙",
        "观察胆囊管和胆囊动脉处理区域",
    )
    out = re.sub(r"参与(胆囊管|胆囊动脉)(夹闭|切断)", r"\1\2", out)
    out = out.replace("相关操作", "")
    out = re.sub(r"(当前窗口|本段|术野|画面)出现", r"\1有", out)
    out = out.replace("当前窗口有", "").replace("本段有", "")
    out = out.replace("出现了", "").replace("出现", "")
    out = re.sub(r"(夹闭|切断|分离|牵拉|清理|凝血处理)(?:明显)(?!出血)", r"\1", out)
    out = re.sub(r"Hem[-\s]?o[-\s]?lok夹和金属钛夹(?:样)?(?:亮白)?夹体", "夹子", out, flags=re.IGNORECASE)
    out = re.sub(r"(?:白色|乳白色)?Hem[-\s]?o[-\s]?lok夹", "夹子", out, flags=re.IGNORECASE)
    out = re.sub(r"金属钛夹|钛夹(?!钳)", "夹子", out)
    out = re.sub(r"夹子样(?:亮白)?夹体", "夹子", out)
    out = re.sub(r"可见\s*\d+\s*枚夹子", "可见夹子", out)
    out = re.sub(r"夹子和夹子", "夹子", out)
    out = re.sub(r"使用夹子夹闭合", "使用夹子闭合", out)
    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"[，,]\s*[。；;]", "。", out)
    out = re.sub(r"([。；;])\s*\1+", r"\1", out)
    out = re.sub(r"。{2,}", "。", out)
    return out.strip(" ，；")


def _compact_local_summary_text(text: Any) -> str:
    """Final display cleanup for local expert/local VLM summaries.

    The local route can produce repeated detector evidence. Keep the surgical
    action, bleeding and CVS information while removing duplicated fragments.
    """
    out = _polish_summary_wording(text)
    if not out:
        return out

    parts = [p.strip(" ，。；;") for p in re.split(r"[。；;]+", out) if p.strip(" ，。；;")]
    kept: List[str] = []
    seen_keys: set = set()
    for part in parts:
        part = _polish_summary_wording(part)
        if not part:
            continue
        key = re.sub(r"\d+[:：]\d+(?:\s*[-–]\s*\d+[:：]\d+)?|窗口\s*\d+", "", part)
        key = re.sub(r"\s+", "", key)
        key = re.sub(r"(当前处于[^，,]+)[，,]?", "", key)
        key = key.replace("肝胆三角区域组织", "肝胆三角组织")
        key = key.replace("胆囊床区域组织", "胆囊床组织")
        if key in seen_keys:
            continue
        seen_keys.add(key)
        kept.append(part)

    if not kept:
        return ""

    # Prefer concise CVS wording in per-window summaries; the detailed CVS
    # state is shown in the key-event node.
    compacted: List[str] = []
    for part in kept:
        if re.fullmatch(r"(胆囊管|胆囊动脉)残端", part) and any(
            part in existing and ("夹闭" in existing or "闭合" in existing) for existing in compacted
        ):
            continue
        if re.search(r"CVS(?:仍在评估中|安全视野确认中|评估中|尚未完全确认)", part) and any(
            re.search(r"CVS.*(?:评估|确认|核查|达成)", existing) for existing in compacted
        ):
            continue
        if part.startswith("CVS评估："):
            if "已达成" in part or "CVS已达成" in part:
                part = "CVS已达成"
            elif "夹闭前后" in part:
                part = "CVS处于夹闭前后安全核查中"
            else:
                part = "CVS安全视野确认中"
        compacted.append(part)

    # Stage 1 and the local VLM can describe the same hook action at two
    # levels of specificity. Keep the anatomical description only.
    has_specific_hook_action = any(
        re.search(
            r"电凝钩分离(?:肝胆三角组织|胆囊床组织|胆囊与胆囊床粘连组织)",
            part,
        )
        for part in compacted
    )
    if has_specific_hook_action:
        compacted = [
            part for part in compacted
            if not re.fullmatch(r"电凝钩分离局部纤维组织", part)
        ]

    has_detailed_hook_action = any(
        re.search(
            r"电凝钩沿(?:肝胆三角解剖层次|胆囊壁与肝床间隙)分离",
            part,
        )
        for part in compacted
    )
    if has_detailed_hook_action:
        compacted = [
            part
            for part in compacted
            if not re.fullmatch(
                r"电凝钩(?:正在)?分离(?:肝胆三角(?:内纤维脂肪|区域的?)组织|"
                r"胆囊床(?:粘连)?组织|局部纤维组织)(?:，[^。]*)?",
                part,
            )
        ]

    has_specific_bipolar_calot_action = any(
        re.search(r"双极电凝钳反复开合夹持并分离肝胆三角内纤维脂肪组织", part)
        for part in compacted
    )
    if has_specific_bipolar_calot_action:
        compacted = [
            part
            for part in compacted
            if not re.fullmatch(
                r"双极电凝钳夹持并分离(?:局部纤维组织|肝胆三角区域的?组织)",
                part,
            )
        ]

    if any(part == "双极电凝钳夹持并分离局部组织" for part in compacted):
        compacted = [
            part
            for part in compacted
            if part != "双极电凝钳夹持并分离局部纤维组织"
        ]

    has_target_clipping_action = any(
        re.search(
            r"(?:钛夹钳|施夹器|施夹钳)(?:正在)?(?:夹闭|闭合|施夹)(?:胆囊管|胆囊动脉)",
            part,
        )
        for part in compacted
    )
    if has_target_clipping_action:
        concise_clipping_parts: List[str] = []
        for part in compacted:
            if part == "可见已释放夹子":
                continue
            part = re.sub(r"[，,]?\s*电凝钩分离局部纤维组织", "", part).strip(" ，,")
            if part:
                concise_clipping_parts.append(part)
        compacted = concise_clipping_parts

    has_specific_scissors_warning = any(
        re.search(r"剪刀在(?:胆囊管|胆囊动脉)邻近区域操作", part)
        for part in compacted
    )
    if has_specific_scissors_warning:
        compacted = [part for part in compacted if part != "剪刀在操作区域内活动"]

    if "当前处于胆囊分离" in out:
        compacted = [part for part in compacted if part != "可见已释放夹子"]

    # A concrete scissors-before-CVS warning already carries the current CVS
    # state. Do not append the routine assessment sentence again in the same
    # five-second window.
    if any(re.search(r"剪刀.*CVS尚未达成|CVS尚未达成.*剪刀", part) for part in compacted):
        compacted = [
            part for part in compacted
            if not re.fullmatch(r"CVS(?:处于)?夹闭前后安全核查中|CVS安全视野确认中", part)
        ]

    out = "。".join(compacted)
    out = re.sub(r"。{2,}", "。", out).strip(" 。；")
    return _polish_summary_wording(out + ("。" if out else ""))


def _normalize_packaging_summary(text: Any, visual: Optional[Dict[str, Any]] = None) -> str:
    """Suppress stale cystic-duct dissection labels once specimen bagging starts."""
    out = _polish_summary_wording(text)
    visual = visual or {}
    visibility = _visibility_flags_from_visual(visual)
    has_out_of_body = visibility["out_of_body"] or bool(OUT_OF_BODY_RE.search(out))
    has_bag_context = bool(re.search(r"胆囊取出与装袋|标本袋|胆囊袋|装袋|取出", out))
    has_gauze = bool(re.search(r"纱布|棉片", out))
    has_fog = bool(
        visibility["fog_active"]
        or re.search(r"镜头起雾|雾气|烟雾|视野受遮挡|视野不清", out)
    )
    fog_resolved = bool(
        visibility["fog_resolved"]
        or re.search(r"雾已去除|雾气消散|视野恢复", out)
    )
    bleeding_visual = visual.get("bleeding") if isinstance(visual.get("bleeding"), dict) else None
    if bleeding_visual is not None:
        severity = str(bleeding_visual.get("severity") or "none").lower()
        has_bleeding = bool(
            bleeding_visual.get("controlled")
            or (bleeding_visual.get("active") and severity in {"moderate", "severe"})
        )
    else:
        # Local expert summaries can still surface an explicit coagulation
        # action before the structured visual refinement arrives.
        has_bleeding = bool(re.search(r"凝血处理|双极电凝处理出血点", out))

    stale_patterns = (
        r"(?:胆囊管|胆囊动脉)残端已(?:由夹子)?(?:闭合|夹闭)",
        r"(?:胆囊管|胆囊动脉)残端已夹闭",
        r"(?:可见)?已夹闭(?:胆囊管|胆囊动脉)残端",
        r"(?:使用)?夹子(?:夹闭|闭合)(?:胆囊管|胆囊动脉)",
        r"(?:双极电凝钳?|电凝钩|剪刀|钳子)?(?:正在)?分离胆囊管",
        r"(?:双极电凝钳?|电凝钩|剪刀|钳子)?(?:正在)?分离胆囊板",
        r"电凝钩尖端接触并分离组织",
        r"画面以[^。；;]*牵拉胆囊[^。；;]*为主",
        r"抓钳牵拉胆囊颈和胆囊体以暴露操作区域",
        r"可见双极器械接触组织，尚未确认止血操作",
        r"尚未确认止血操作",
        r"钛夹钳(?:正在)?夹闭(?:胆囊管|胆囊动脉)",
    )
    for pattern in stale_patterns:
        out = re.sub(rf"[，,；;。]?\s*{pattern}", "", out)

    parts: List[str] = []
    if has_out_of_body:
        return "镜头移出体外，画面切换至套管口或腹壁外场景。"

    if has_bag_context or "胆囊取出与装袋" in out or not parts:
        parts.append("当前处于胆囊取出与装袋，将胆囊装入标本袋并准备取出")

    if has_gauze and not any("纱布" in part for part in parts):
        parts.append("可见纱布和棉片用于局部压迫或清理")
    if has_bleeding and not any("凝血" in part or "出血" in part for part in parts):
        parts.append("正在进行凝血处理")
    if has_fog and not has_out_of_body:
        parts.append("镜头起雾，手术视野受遮挡")
    elif fog_resolved and not has_out_of_body:
        parts.append("雾已去除，腹腔视野恢复")

    normalized = "。".join(dict.fromkeys(part.strip(" 。；") for part in parts if part.strip(" 。；")))
    return _polish_summary_wording(normalized + ("。" if normalized else ""))


def _normalize_post_packaging_cleaning_summary(
    text: Any,
    *,
    retrieval_confirmed: bool = False,
    review_already_seen: bool = False,
) -> str:
    out = _polish_summary_wording(text)
    if OUT_OF_BODY_RE.search(out):
        return "镜头移出体外，画面切换至套管口或腹壁外场景。"
    reentry_pattern = (
        r"(?:胆囊装袋取出后[，,]?\s*)?"
        r"镜头重新进入腹腔[，,]?\s*(?:进行)?术野复查"
    )
    has_reentry_evidence = bool(re.search(reentry_pattern, out))
    if not retrieval_confirmed:
        out = re.sub(r"胆囊装袋取出后[，,]?\s*", "", out)
    if review_already_seen:
        out = re.sub(rf"[，,；;。]?\s*{reentry_pattern}", "", out)
    stale_patterns = (
        r"(?:胆囊管|胆囊动脉)残端已(?:由夹子)?(?:闭合|夹闭)",
        r"(?:胆囊管|胆囊动脉)残端已夹闭",
        r"(?:可见)?已夹闭(?:胆囊管|胆囊动脉)残端",
        r"(?:使用)?夹子(?:夹闭|闭合)(?:胆囊管|胆囊动脉)",
        r"钛夹钳(?:正在)?夹闭(?:胆囊管|胆囊动脉)",
        r"(?:双极电凝钳?|电凝钩|剪刀|钳子)?(?:正在)?分离胆囊管",
        r"(?:双极电凝钳?|电凝钩|剪刀|钳子)?(?:正在)?分离胆囊板",
        r"电凝钩尖端接触并分离组织",
        r"画面以[^。；;]*牵拉胆囊[^。；;]*为主",
        r"抓钳牵拉胆囊颈和胆囊体以暴露操作区域",
        r"可见双极器械接触组织，尚未确认止血操作",
        r"尚未确认止血操作",
        r"(?:抓钳)?(?:正在)?装袋胆囊",
        r"将胆囊装入标本袋并准备取出",
        r"进入标本装袋或取出相关步骤",
        # Post-retrieval tail: the duct/artery work is long finished, so both
        # the downgraded scissors warning and any CVS assessment note are stale.
        r"剪刀在(?:胆囊管|胆囊动脉)邻近区域操作[^。；;]*",
        r"CVS尚未达成[^。；;]*",
        r"需核查后再剪断",
        r"CVS(?:处于|评估：?)?(?:夹闭前后)?安全核查中",
        r"CVS评估[^。；;]*",
    )
    for pattern in stale_patterns:
        out = re.sub(rf"[，,；;。]?\s*{pattern}", "", out)
    generic_review = "当前处于清洁凝血，清理胆囊床并复查术野。"
    if has_reentry_evidence and not review_already_seen:
        default_review = (
            "胆囊装袋取出后，镜头重新进入腹腔，进行术野复查。"
            if retrieval_confirmed
            else "镜头重新进入腹腔，进行术野复查。"
        )
    else:
        default_review = generic_review
    generic_cleaning = bool(
        re.fullmatch(r"当前处于清洁凝血。?", out.strip())
        or re.search(r"清理术野并确认出血控制|画面以清理术野、凝血和确认出血控制为主", out)
    )
    specific_cleaning_evidence = bool(re.search(
        r"正在进行凝血处理|双极电凝|冲洗|纱布|棉片|活动性出血|出血点|渗血|止血处理|吸引|"
        r"镜头起雾|视野受遮挡|雾已去除",
        out,
    ))
    if generic_cleaning or not specific_cleaning_evidence:
        out = default_review
    return _polish_summary_wording(out.rstrip(" 。；") + "。")


def _normalize_prepackaging_cleaning_summary(text: Any) -> str:
    """Keep intraoperative cleaning free of stale Calot and clipping claims."""
    out = _polish_summary_wording(text)
    stale_patterns = (
        r"抓钳牵拉胆囊颈部并抬起胆囊体[^。；;]*",
        r"抓钳牵拉胆囊颈和胆囊体[^。；;]*",
        r"(?:钛夹钳|施夹器|施夹钳)(?:正在)?(?:夹闭|闭合|施夹)[^。；;]*",
        r"目标组织正在接受夹闭处理[^。；;]*",
        r"夹子已夹闭目标组织[^。；;]*",
        r"(?:双极电凝钳?|电凝钩|剪刀|抓钳|钳子)(?:正在)?分离(?:胆囊管|胆囊动脉)",
        r"CVS(?:安全视野确认中|处于夹闭前后安全核查中|评估中)",
    )
    for pattern in stale_patterns:
        out = re.sub(rf"[，,；;。]?\s*{pattern}", "", out)
    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"[，,]\s*。", "。", out)
    out = re.sub(r"。{2,}", "。", out).strip(" ，；。")
    if not out or re.fullmatch(r"当前处于清洁凝血", out):
        out = "当前处于清洁凝血，清理胆囊床并复查术野"
    return _polish_summary_wording(out + "。")


def _normalize_retraction_summary(text: Any, visual: Optional[Dict[str, Any]] = None) -> str:
    """Keep the terminal retrieval phase free of stale instrument actions."""
    out = _polish_summary_wording(text)
    flags = _visibility_flags_from_visual(visual or {})
    if flags["out_of_body"] or OUT_OF_BODY_RE.search(out):
        return "镜头移出体外，画面切换至套管口或腹壁外场景。"
    parts = ["当前处于标本袋牵拉取出，牵拉装有胆囊的标本袋经切口取出"]
    if flags["fog_active"] or re.search(r"镜头起雾|雾气|烟雾|视野受遮挡", out):
        parts.append("镜头起雾，手术视野受遮挡")
    elif flags["fog_resolved"] or re.search(r"雾已去除|雾气消散|视野恢复", out):
        parts.append("雾已去除，腹腔视野恢复")
    bleeding = (visual or {}).get("bleeding") or {}
    if bleeding.get("active") and str(bleeding.get("severity") or "").lower() == "severe":
        parts.append("检测到大量活动性出血")
    return "。".join(parts) + "。"


def _phase_visual_context(phase_raw: str, phase: str) -> str:
    return PHASE_VISUAL_CONTEXT_CN.get(phase) or PHASE_VISUAL_CONTEXT_CN.get(_canonical_phase(phase_raw)) or "画面以术野观察和阶段确认为主"


def _cvs_realtime_note(phase: str, tool_set: set, action_text: str = "") -> str:
    """Emit a concise CVS status whenever the phase requires safety judgement."""
    if phase == "CalotTriangleDissection":
        return "CVS评估：安全视野确认中"
    if phase == "ClippingCutting":
        return "CVS评估：夹闭前后安全核查中"
    return ""


def _strip_clipping_noise(text: Any) -> str:
    out = _polish_summary_wording(text)
    if not out:
        return out
    for phrase in ("正在进行局部分离和尖端接触", "对组织进行尖端接触和分离"):
        out = re.sub(rf"[，,；;。]\s*{phrase}[，,；;。]?", "。", out)
        out = re.sub(rf"^{phrase}[，,；;。]?", "", out)
    if not re.search(r"(钛夹钳|施夹器|Hem-o-lok|金属钛夹).{0,16}(夹闭|夹持|闭合)|夹闭(?:胆囊管|胆囊动脉)", out):
        out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
        out = re.sub(r"。{2,}", "。", out)
        return _compact_local_summary_text(out)
    for target in ("胆囊管", "胆囊动脉"):
        if re.search(rf"钛夹钳(?:正在)?夹闭{target}", out):
            out = re.sub(rf"([，,；;。]?\s*)钛夹钳夹持{target}", "", out)
        if re.search(rf"钛夹钳正在夹闭{target}", out):
            out = re.sub(rf"([，,；;。]?\s*)钛夹钳夹闭{target}", "", out)
            out = re.sub(rf"([，,；;。]?\s*)可见(?:\d+枚)?金属钛夹已夹闭{target}(?:残端)?", "", out)
            out = re.sub(rf"([，,；;。]?\s*)已夹闭{target}残端", "", out)
    out = re.sub(r"([，,；;])\s*(?:冲吸器|剪刀|电凝钩|电钩)?分离胆囊", "", out)
    out = re.sub(r"([，,；;])\s*正在牵拉和暴露组织", "", out)
    out = re.sub(r"([，,；;])\s*未见(?:明显)?(?:活动性)?出血", "", out)
    out = re.sub(r"([，,；;])\s*未见(?:夹闭器具|纱布)?", "", out)
    out = re.sub(r"([，,；;])\s*视野清晰", "", out)
    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"。{2,}", "。", out)
    out = re.sub(r"，。", "。", out)
    out = re.sub(r"当前处于夹闭切断。", "当前处于夹闭切断，", out)
    return _compact_local_summary_text(out)


def _clip_review_rejects_deployed_clip(visual: Optional[Dict[str, Any]]) -> bool:
    """Return true when the dedicated reviewer explicitly rejects a placed clip."""
    review = (visual or {}).get("clip_secondary_review") or {}
    if not review.get("success"):
        return False
    confidence = _safe_float(review.get("confidence"), 0.0)
    prediction = str(review.get("classification") or "").strip().lower()
    return confidence >= 0.80 and prediction in {
        "no_clip",
        "clip_applier",
        "glare_or_instrument",
        "instrument",
        "glare",
    }


def _focused_clip_action_confirmed(visual: Optional[Dict[str, Any]]) -> bool:
    """Trust a focused morphology review when clipping starts before phase transition."""
    review = (visual or {}).get("clip_secondary_review") or {}
    if not (
        review.get("success")
        and _safe_float(review.get("confidence"), 0.0) >= 0.85
    ):
        return False
    classification = str(review.get("classification") or "").strip().lower()
    if classification == "clip_applier":
        return bool(review.get("applier_active") or review.get("clamped_on_tissue"))
    if classification == "clip":
        return bool(
            review.get("independent_from_instrument")
            or review.get("clamped_on_tissue")
        )
    return False


def _strip_visual_rejected_clip_claims(
    text: Any,
    visual: Optional[Dict[str, Any]],
    phase: str = "",
) -> str:
    """Let a high-confidence visual veto remove stale Stage-1 clip claims."""
    out = _polish_summary_wording(text)
    if not out or not _clip_review_rejects_deployed_clip(visual):
        return out

    deployed_clip_patterns = (
        r"(?:可见)?\d*枚?夹子已(?:夹闭|闭合)(?:胆囊管|胆囊动脉)(?:残端)?",
        r"(?:胆囊管|胆囊动脉)残端已由夹子(?:闭合|夹闭)",
        r"(?:胆囊管|胆囊动脉)残端已夹闭",
        r"(?:使用)?夹子(?:夹闭|闭合)(?:胆囊管|胆囊动脉)",
        r"可见已夹闭的?(?:胆囊管|胆囊动脉)残端",
    )
    for pattern in deployed_clip_patterns:
        out = re.sub(rf"[，,；;。]?\s*{pattern}", "", out)

    secondary = visual.get("clip_secondary_review") or {}
    clip_applier = visual.get("clip_applier") or {}
    secondary_prediction = str(secondary.get("classification") or "").strip().lower()
    secondary_confidence = _safe_float(secondary.get("confidence"), 0.0)
    active_application_rejected = bool(
        secondary.get("success")
        and secondary_confidence >= 0.80
        and (
            secondary_prediction in {"no_clip", "glare_or_instrument", "instrument", "glare"}
            or (
                secondary_prediction == "clip_applier"
                and not secondary.get("clamped_on_tissue")
                and not secondary.get("applier_active")
                and clip_applier.get("active") is False
            )
        )
    )
    if active_application_rejected:
        active_clip_patterns = (
            r"(?:钛夹钳|施夹器|施夹钳)(?:正在)?(?:夹闭|闭合|施夹)(?:胆囊管|胆囊动脉)",
            r"(?:正在)?(?:夹闭|闭合|施夹)(?:胆囊管|胆囊动脉)",
        )
        for pattern in active_clip_patterns:
            out = re.sub(rf"[，,；;。]?\s*{pattern}", "", out)

    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"[，,]\s*。", "。", out)
    out = re.sub(r"。{2,}", "。", out).strip(" ，；。")

    canonical_phase = _canonical_phase(phase or "Unknown")
    phase_only = bool(re.fullmatch(r"当前处于[^，,。；;]+", out))
    if not out or phase_only:
        fallback = {
            "GallbladderDissection": "当前处于胆囊分离，画面以胆囊床术野观察为主。",
            "ClippingCutting": "当前处于夹闭切断阶段，观察胆囊管和胆囊动脉处理区域。",
            "CalotTriangleDissection": "当前处于肝胆三角解剖，继续进行安全视野评估。",
        }.get(canonical_phase, "当前画面以术野观察和阶段确认为主。")
        return fallback
    return _compact_local_summary_text(out + "。")


def _strip_unverified_target_specific_clip_claims(
    text: Any,
    visual: Optional[Dict[str, Any]],
    expert_pack: Optional[Dict[str, Any]] = None,
) -> str:
    """Keep a confirmed clip while withholding an unsupported anatomy label."""
    out = _polish_summary_wording(text)
    if not out or not re.search(
        r"(?:钛夹钳|施夹器|夹子|残端).{0,18}(?:夹闭|闭合)|"
        r"(?:夹闭|闭合).{0,10}(?:胆囊管|胆囊动脉)",
        out,
    ):
        return out

    visual = visual or {}
    target = visual.get("target_structure") or {}
    visual_target_supported = bool(
        _target_label_from_raw(target.get("label"))
        and _safe_float(target.get("confidence"), 0.0) >= 0.55
    )
    triplet_hint = _target_hint_from_triplet((expert_pack or {}).get("triplet") or {})
    triplet_target_supported = bool(
        _target_label_from_raw(triplet_hint.get("label"))
        and str(triplet_hint.get("source") or "").startswith(("triplet:", "target:"))
        and _safe_float(triplet_hint.get("confidence"), 0.0) >= 0.45
    )
    if visual_target_supported or triplet_target_supported:
        return out

    review = visual.get("clip_secondary_review") or {}
    generic = visual.get("generic_clip") or {}
    applier = visual.get("clip_applier") or {}
    deployed_clip_confirmed = bool(
        review.get("success")
        and str(review.get("classification") or "").lower() == "clip"
        and _safe_float(review.get("confidence"), 0.0) >= 0.80
        and review.get("independent_from_instrument")
        and review.get("clamped_on_tissue")
    ) or bool(
        generic.get("placed")
        and _safe_float(generic.get("confidence"), 0.0) >= 0.80
    )
    applier_active = bool(
        applier.get("active")
        and _safe_float(applier.get("confidence"), 0.0) >= 0.75
    )
    if deployed_clip_confirmed:
        replacement = "夹子已夹闭目标组织，具体目标需回看确认"
    elif applier_active:
        replacement = "钛夹钳正在夹闭目标组织，具体目标需回看确认"
    else:
        replacement = "目标组织正在接受夹闭处理，具体目标需回看确认"

    patterns = (
        r"(?:钛夹钳|施夹器|施夹钳)(?:正在)?(?:夹闭|闭合|施夹)(?:胆囊管|胆囊动脉)",
        r"(?:使用)?夹子(?:夹闭|闭合)(?:胆囊管|胆囊动脉)(?:残端)?",
        r"(?:可见)?夹子已(?:夹闭|闭合)(?:胆囊管|胆囊动脉)(?:残端)?",
        r"(?:胆囊管|胆囊动脉)残端已由夹子(?:闭合|夹闭)",
        r"(?:胆囊管|胆囊动脉)残端已夹闭",
    )
    replaced = False
    for pattern in patterns:
        out, count = re.subn(pattern, replacement if not replaced else "", out)
        replaced = replaced or count > 0
    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"[，,]\s*。", "。", out)
    out = re.sub(r"。{2,}", "。", out)
    return _compact_local_summary_text(out)


def _strip_nonprogress_idle_applier_claim(text: Any, phase: str = "") -> str:
    """Do not carry an idle clip-applier observation outside clipping work."""
    out = _polish_summary_wording(text)
    if not out or _canonical_phase(phase or "Unknown") == "ClippingCutting":
        return out
    out = re.sub(r"[，,；;。]?\s*钛夹钳在操作区域内调整", "", out)
    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"[，,]\s*。", "。", out)
    out = re.sub(r"。{2,}", "。", out).strip(" ，；。")
    return _compact_local_summary_text(out + ("。" if out else ""))


def _strip_focused_visibility_conflicts(
    text: Any,
    visual: Optional[Dict[str, Any]],
) -> str:
    """Make the focused scene classifier the final visibility text authority."""
    out = _polish_summary_wording(text)
    review = (visual or {}).get("visibility_secondary_review") or {}
    if not review.get("success") or _safe_float(review.get("confidence"), 0.0) < 0.75:
        return out
    classification = str(review.get("classification") or "").lower()
    if classification in {"external_body", "trocar_transition"}:
        return "镜头移出体外，画面切换至套管口或腹壁外场景。"

    def remove_visibility_sentences(value: str) -> str:
        for pattern in (
            r"[，,；;。]?\s*镜头起雾，手术视野受遮挡",
            r"[，,；;。]?\s*雾已去除，腹腔视野恢复",
            r"[，,；;。]?\s*镜头移出体外，画面切换至套管口或腹壁外场景",
        ):
            value = re.sub(pattern, "", value)
        value = re.sub(r"[，,；;]\s*[，,；;]+", "，", value)
        value = re.sub(r"[，,]\s*。", "。", value)
        return re.sub(r"。{2,}", "。", value).strip(" ，；。")

    base = remove_visibility_sentences(out)
    if classification == "foggy_inside":
        return (base + "。" if base else "") + "镜头起雾，手术视野受遮挡。"
    if classification in {"specimen_bag_inside", "intra_abdominal"}:
        return base + ("。" if base else "")
    return out


def _strip_unsupported_stapler_wording(
    text: Any,
    visual: Optional[Dict[str, Any]],
) -> str:
    """Remove stapler/suturing prose when the focused review sees an idle applier."""
    out = _polish_summary_wording(text)
    review = (visual or {}).get("clip_secondary_review") or {}
    applier = (visual or {}).get("clip_applier") or {}
    prediction = str(review.get("classification") or "").strip().lower()
    review_rejects_stapling = bool(
        review.get("success")
        and _safe_float(review.get("confidence"), 0.0) >= 0.80
        and prediction in {"clip_applier", "no_clip", "glare_or_instrument", "instrument", "glare"}
        and not review.get("clamped_on_tissue")
        and applier.get("active") is False
    )
    if not review_rejects_stapling:
        return out
    out = re.sub(
        r"[，,；;。]?\s*[^。；;]*(?:自动缝合器|吻合器|缝合操作|Autosuture)[^。；;]*",
        "",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"[，,]\s*。", "。", out)
    out = re.sub(r"。{2,}", "。", out).strip(" ，；。")
    return _compact_local_summary_text(out + ("。" if out else ""))


def _strip_visual_rejected_scissors_claims(
    text: Any,
    visual: Optional[Dict[str, Any]],
    expert_pack: Optional[Dict[str, Any]] = None,
    phase: str = "",
) -> str:
    """Remove stale scissors prose when the structured visual review rejects it."""
    out = _polish_summary_wording(text)
    if _visibility_flags_from_visual(visual or {})["out_of_body"] or OUT_OF_BODY_RE.search(out):
        return "镜头移出体外，画面切换至套管口或腹壁外场景。"
    scissors = (visual or {}).get("scissors") or {}
    has_structured_review = isinstance(scissors, dict) and (
        "visible" in scissors or "cutting" in scissors
    )
    if (
        not out
        or not has_structured_review
        or scissors.get("visible")
        or scissors.get("cutting")
    ):
        return out

    out = re.sub(
        r"[，,；;。]?\s*剪刀在(?:胆囊管|胆囊动脉)邻近区域操作[^。；;]*",
        "",
        out,
    )
    out = re.sub(
        r"[，,；;。]?\s*剪刀(?:正在|已)?(?:剪断|切断|离断|分离|解剖|夹持|操作|接触)[^。；;]*",
        "",
        out,
    )
    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"[，,]\s*。", "。", out)
    out = re.sub(r"。{2,}", "。", out).strip(" ，；。")

    canonical_phase = _canonical_phase(phase or "Unknown")
    if canonical_phase == "ClippingCutting" and "CVS" not in out:
        out = f"{out.rstrip('。')}。CVS处于夹闭前后安全核查中"
    elif canonical_phase == "CalotTriangleDissection" and "CVS" not in out:
        out = f"{out.rstrip('。')}。CVS安全视野确认中"
    return _compact_local_summary_text(out + ("。" if out else ""))


def _strip_focused_scissors_instrument_conflicts(
    text: Any,
    visual: Optional[Dict[str, Any]],
) -> str:
    """Let the focused morphology review remove contradictory tool wording."""
    out = _polish_summary_wording(text)
    if not out:
        return out

    scissors_review = (visual or {}).get("scissors_secondary_review") or {}
    morphology_review = (visual or {}).get("clip_secondary_review") or {}
    focused_class = ""
    if (
        scissors_review.get("success")
        and scissors_review.get("instrument") == "scissors"
        and scissors_review.get("scissors_visible")
        and _safe_float(scissors_review.get("confidence"), 0.0) >= 0.85
    ):
        focused_class = "scissors"
    elif (
        morphology_review.get("success")
        and _safe_float(morphology_review.get("confidence"), 0.0) >= 0.85
    ):
        candidate = str(morphology_review.get("classification") or "").lower()
        if candidate == "scissors":
            focused_class = candidate
        elif candidate == "clip_applier" and (
            morphology_review.get("applier_active")
            or morphology_review.get("clamped_on_tissue")
        ):
            focused_class = candidate

    common_conflicts = (
        r"[，,；;。]?\s*电凝钩(?:正在)?(?:分离|接触|操作)[^，。；;]*",
        r"[，,；;。]?\s*双极电凝钳[^，。；;]*",
    )
    if focused_class == "scissors":
        conflicts = common_conflicts + (
            r"[，,；;。]?\s*钛夹钳(?:正在)?(?:夹闭|夹持|操作)[^，。；;]*",
            r"[，,；;。]?\s*施夹器(?:正在)?(?:夹闭|夹持|操作)[^，。；;]*",
        )
    elif focused_class == "clip_applier":
        conflicts = common_conflicts + (
            r"[，,；;。]?\s*剪刀(?:正在|已)?(?:活动|操作|接触|分离|剪切|剪断|切断)[^，。；;]*",
        )
    else:
        return out

    for pattern in conflicts:
        out = re.sub(pattern, "", out)
    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"[，,]\s*。", "。", out)
    out = re.sub(r"。{2,}", "。", out).strip(" ，；。")
    return _compact_local_summary_text(out + ("。" if out else ""))


def _resolve_clip_applier_scissors_conflict(
    visual: Optional[Dict[str, Any]],
    expert_pack: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve the common clip-applier/scissors morphology disagreement."""
    resolved = visual if isinstance(visual, dict) else {}
    clip_review = resolved.get("clip_secondary_review") or {}
    scissors_review = resolved.get("scissors_secondary_review") or {}
    if not (
        clip_review.get("success")
        and str(clip_review.get("classification") or "").lower() == "clip_applier"
        and _safe_float(clip_review.get("confidence"), 0.0) >= 0.85
        and scissors_review.get("success")
        and scissors_review.get("scissors_visible")
        and _safe_float(scissors_review.get("confidence"), 0.0) >= 0.75
    ):
        return resolved

    tool_counts: Dict[str, int] = {}
    for tool in (((expert_pack or {}).get("yolo") or {}).get("tools") or []):
        label = str((tool or {}).get("label") or "").strip().lower()
        if label:
            tool_counts[label] = tool_counts.get(label, 0) + int((tool or {}).get("frames_seen") or 0)
    clipper_frames = tool_counts.get("clipper", 0)
    scissors_frames = tool_counts.get("scissors", 0)
    clip_confidence = _safe_float(clip_review.get("confidence"), 0.0)
    scissors_confidence = _safe_float(scissors_review.get("confidence"), 0.0)

    # Keep a detector-supported scissors judgement. With no such evidence, a
    # higher-confidence dedicated clip review wins because thick parallel
    # applier jaws are the recurring source of the false scissors label.
    if scissors_frames > clipper_frames and scissors_frames > 0:
        return resolved
    if clipper_frames <= scissors_frames and clip_confidence < scissors_confidence + 0.05:
        return resolved

    scissors = dict(resolved.get("scissors") or {})
    scissors.update({
        "visible": False,
        "cutting": False,
        "target": "unknown",
        "confidence": clip_confidence,
        "rejected_by_clip_applier_review": True,
    })
    resolved["scissors"] = scissors

    applier = dict(resolved.get("clip_applier") or {})
    applier.update({
        "visible": True,
        "active": bool(
            clip_review.get("applier_active")
            or clip_review.get("clamped_on_tissue")
        ),
        "confidence": max(_safe_float(applier.get("confidence"), 0.0), clip_confidence),
        "secondary_review": True,
        "conflict_resolved": True,
    })
    applier.pop("rejected_by_scissors_review", None)
    resolved["clip_applier"] = applier

    scissors_review["conflict_resolved_as"] = "clip_applier"
    scissors_review["scissors_visible"] = False
    scissors_review["scissors_cutting"] = False
    resolved["scissors_secondary_review"] = scissors_review
    return resolved


def _remove_unsupported_calot_clip_claims(
    text: str,
    phase: str,
    prior_state: Dict[str, Any],
    visual: Optional[Dict[str, Any]] = None,
) -> str:
    """Drop clip/closure claims during Calot dissection unless clipping already happened."""
    if phase != "CalotTriangleDissection":
        return text
    if prior_state.get("clipped"):
        return text
    if _focused_clip_action_confirmed(visual):
        return text
    if not re.search(r"(夹子|夹闭|闭合|施夹|钛夹|Hem-o-lok|clip)", text, re.IGNORECASE):
        return text

    sentences = re.split(r"(?<=[。；;])", text)
    kept = [
        sentence
        for sentence in sentences
        if sentence.strip()
        and not re.search(r"(夹子|夹闭|闭合|施夹|钛夹|Hem-o-lok|clip)", sentence, re.IGNORECASE)
    ]
    out = "".join(kept).strip()
    if out:
        return out
    return "当前处于肝胆三角解剖，电凝钩分离肝胆三角组织。CVS安全视野确认中。"


SEQUENCE_PHASE_ORDER = {
    "Preparation": 0,
    "CalotTriangleDissection": 1,
    "ClippingCutting": 2,
    "GallbladderDissection": 3,
    # Cholec80 cases can alternate these late phases. They are all terminal
    # workflow phases and must not be treated as regressions between each other.
    "CleaningCoagulation": 4,
    "GallbladderPackaging": 4,
    "GallbladderRetraction": 4,
}

LATE_WORKFLOW_PHASES = {
    "CleaningCoagulation",
    "GallbladderPackaging",
    "GallbladderRetraction",
}


def _late_workflow_prerequisites_met(state: Dict[str, Any]) -> bool:
    reached = state.get("reached_phases") or set()
    return "ClippingCutting" in reached and "GallbladderDissection" in reached


def _stable_prior_phase(state: Dict[str, Any], fallback: str = "Unknown") -> str:
    phase = _canonical_phase(state.get("last_phase") or fallback)
    return phase if phase in SEQUENCE_PHASE_ORDER else _canonical_phase(fallback)


def _bounded_window_end(start_time: float, window_duration: float, media_duration: Any) -> float:
    nominal_end = float(start_time) + float(window_duration)
    duration = _safe_float(media_duration, 0.0)
    if duration > float(start_time):
        return min(nominal_end, duration)
    return nominal_end


def _summary_row_window_id(summary: Any) -> Optional[int]:
    value = summary.get("window_id") if isinstance(summary, dict) else getattr(summary, "window_id", None)
    try:
        return int(value)
    except Exception:
        return None


def _summary_row_text(summary: Any) -> str:
    if isinstance(summary, dict):
        return str(summary.get("glm_summary") or summary.get("summary") or summary.get("summary_text") or "")
    return str(getattr(summary, "summary_text", getattr(summary, "glm_summary", "")) or "")


def _summary_row_phase(summary: Any) -> str:
    if isinstance(summary, dict):
        return _canonical_phase(summary.get("surgical_phase") or summary.get("phase") or "Unknown")
    return _canonical_phase(getattr(summary, "surgical_phase", getattr(summary, "phase", "Unknown")) or "Unknown")


def _summary_row_visual(summary: Any) -> Dict[str, Any]:
    if isinstance(summary, dict):
        others = summary.get("others") or {}
    else:
        others = (
            getattr(summary, "others_data", None)
            or getattr(summary, "others", None)
            or {}
        )
    if isinstance(others, str):
        try:
            others = json.loads(others) if others else {}
        except Exception:
            others = {}
    if not isinstance(others, dict):
        return {}
    visual = others.get("visual_gpt") or (
        ((others.get("experts") or {}).get("open_vlm") or {}).get("visual")
    )
    return visual if isinstance(visual, dict) else {}


def _has_cvs_achieved_text(text: Any) -> bool:
    src = str(text or "")
    if not src:
        return False
    negative = r"(?:未|尚未|不能|无法|不足|评估中|部分|需要确认|不要硬判).{0,12}(?:CVS|安全关键视野|三要素)|(?:CVS|安全关键视野|三要素).{0,12}(?:未|尚未|不能|无法|不足|评估中|部分|需要确认)"
    if re.search(negative, src, re.IGNORECASE):
        return False
    positive = (
        r"CVS(?:三要素)?(?:已经|已)?(?:基本)?(?:达成|确认|满足)|"
        r"(?:安全关键视野|关键安全视野)(?:已经|已)?(?:基本)?(?:达成|确认)|"
        r"三要素(?:均|已经|已)?(?:基本)?(?:满足|达成|确认)|"
        r"two and only two structures"
    )
    return bool(re.search(positive, src, re.IGNORECASE))


def _has_clip_target_text(text: Any, target_cn: str) -> bool:
    src = str(text or "")
    if not src or target_cn not in src:
        return False
    cleaned = re.sub(r"夹闭切断(?:阶段)?|夹闭前后|夹闭\/剪断", "", src)
    patterns = (
        rf"(?:Hem-o-lok夹|金属钛夹|钛夹|钛夹钳|施夹器|施夹钳|夹体).{{0,16}}(?:已|正在)?(?:夹闭|闭合|施夹).{{0,10}}{target_cn}",
        rf"(?:已|正在)?(?:夹闭|闭合|施夹).{{0,10}}{target_cn}",
        rf"{target_cn}.{{0,10}}(?:已|被|正在)?(?:夹闭|闭合|施夹)",
        rf"{target_cn}.{{0,10}}(?:已)?由(?:Hem-o-lok夹|金属钛夹|钛夹).{{0,8}}(?:闭合|夹闭)",
    )
    return any(re.search(pattern, cleaned) for pattern in patterns)


def _has_cut_target_text(text: Any, target_cn: str) -> bool:
    src = str(text or "")
    if not src or target_cn not in src:
        return False
    cleaned = re.sub(r"夹闭切断(?:阶段)?|夹闭前后|切断前|剪断前", "", src)
    patterns = (
        rf"(?:剪刀|剪切|剪断|切断|离断|夹断).{{0,14}}{target_cn}",
        rf"{target_cn}.{{0,14}}(?:已|被|正在)?(?:剪断|切断|离断|夹断)",
    )
    return any(re.search(pattern, cleaned) for pattern in patterns)


def _has_scissors_activity_text(text: Any) -> bool:
    src = str(text or "")
    if not src:
        return False
    negative = r"未见剪刀|无剪刀|没有剪刀|scissors not|no scissors"
    if re.search(negative, src, re.IGNORECASE):
        return False
    return bool(re.search(
        r"(?:剪刀|电剪|scissors).{0,20}(?:出现|可见|进入|操作|接触|靠近|分离|剪切|剪断|切断)|"
        r"(?:剪切|剪断|切断).{0,16}(?:胆囊管|胆囊动脉|cystic duct|cystic artery)",
        src,
        re.IGNORECASE,
    ))


def _build_surgical_sequence_state(
    summaries: List[Any],
    before_window_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Build irreversible LC workflow state from already saved windows."""
    state: Dict[str, Any] = {
        "cvs_achieved": False,
        "clipped": set(),
        "cut": set(),
        "reached_phases": set(),
        "last_phase": "Unknown",
        "max_phase_order": -1,
        "packaging_seen": False,
        "packaging_windows": 0,
        "post_packaging_reentry": False,
        "post_retrieval_review": False,
        "formal_started": False,
    }
    rows = []
    for summary in summaries or []:
        wid = _summary_row_window_id(summary)
        if wid is None:
            continue
        if before_window_id is not None and wid >= before_window_id:
            continue
        rows.append((wid, summary))
    rows.sort(key=lambda item: item[0])

    for _, summary in rows:
        text = _summary_row_text(summary)
        phase = _summary_row_phase(summary)
        visual = _summary_row_visual(summary)
        visibility_review = visual.get("visibility_secondary_review") or {}
        focused_bag_inside = bool(
            visibility_review.get("success")
            and _safe_float(visibility_review.get("confidence"), 0.0) >= 0.75
            and str(visibility_review.get("classification") or "").lower()
            == "specimen_bag_inside"
        )
        # Scope removal is a visibility event, not an irreversible workflow
        # transition. Likewise, reject isolated late-phase predictions until
        # the required clipping and gallbladder-dissection phases were seen.
        if OUT_OF_BODY_RE.search(text):
            phase = _stable_prior_phase(state)
        if phase == "GallbladderDissection" and "ClippingCutting" not in state["reached_phases"]:
            phase = _stable_prior_phase(state)
        if phase in LATE_WORKFLOW_PHASES and not _late_workflow_prerequisites_met(state):
            phase = _stable_prior_phase(state)
        order = SEQUENCE_PHASE_ORDER.get(phase, -1)
        if 0 <= order < int(state.get("max_phase_order", -1)):
            phase = _stable_prior_phase(state, phase)
            order = SEQUENCE_PHASE_ORDER.get(phase, -1)
        if phase and phase != "Unknown":
            state["reached_phases"].add(phase)
            state["last_phase"] = phase
            if order > state["max_phase_order"]:
                state["max_phase_order"] = order
        if order >= SEQUENCE_PHASE_ORDER["CalotTriangleDissection"]:
            state["formal_started"] = True
        if (
            _late_workflow_prerequisites_met(state)
            and (phase == "GallbladderPackaging" or focused_bag_inside)
        ):
            state["packaging_seen"] = True
            state["packaging_windows"] += 1
        reentry_evidence = bool(re.search(
            r"镜头重新进入腹腔|术野复查|腹腔视野复查",
            text,
        ))
        if state["packaging_seen"] and reentry_evidence:
            state["post_packaging_reentry"] = True
            # Bag placement can be followed by another intra-abdominal pass
            # before final extraction. Do not call that a post-retrieval review
            # until an actual retraction phase has occurred.
            if "GallbladderRetraction" in state["reached_phases"]:
                state["post_retrieval_review"] = True
        if _has_cvs_achieved_text(text):
            state["cvs_achieved"] = True
        for target_label, target_cn in (("cystic_duct", "胆囊管"), ("cystic_artery", "胆囊动脉")):
            if _has_clip_target_text(text, target_cn):
                state["clipped"].add(target_label)
            if _has_cut_target_text(text, target_cn):
                state["cut"].add(target_label)
    return state


def _sequence_state_meta(state: Dict[str, Any], applied_rules: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "cvs_achieved": bool(state.get("cvs_achieved")),
        "clipped": sorted(state.get("clipped") or []),
        "cut": sorted(state.get("cut") or []),
        "reached_phases": sorted(state.get("reached_phases") or []),
        "last_phase": state.get("last_phase") or "Unknown",
        "max_phase_order": int(state.get("max_phase_order", -1)),
        "packaging_seen": bool(state.get("packaging_seen")),
        "post_packaging_reentry": bool(state.get("post_packaging_reentry")),
        "post_retrieval_review": bool(state.get("post_retrieval_review")),
        "applied_rules": applied_rules or [],
    }


def _replace_current_phase_text(text: str, phase: str) -> str:
    phase_cn = PHASE_LABEL_CN.get(phase) or PHASE_LABEL_CN.get(_canonical_phase(phase)) or phase
    if re.search(r"当前处于[^，。；;]+", text):
        return re.sub(r"当前处于[^，。；;]+", f"当前处于{phase_cn}", text, count=1)
    return f"当前处于{phase_cn}，{text}" if text else f"当前处于{phase_cn}。"


def _normalize_svo_wording(text: Any, phase: str = "") -> str:
    out = _polish_summary_wording(text)
    if not out:
        return out
    if phase == "CalotTriangleDissection":
        exposure_object = "肝胆三角"
    elif phase == "GallbladderDissection":
        exposure_object = "胆囊床"
    else:
        exposure_object = "操作区域"
    out = re.sub(r"(?:抓钳)?(?:正在)?牵拉胆囊管", "抓钳牵拉胆囊颈以暴露胆囊管", out)
    out = re.sub(r"(?:抓钳)?(?:正在)?牵拉胆囊动脉", "抓钳牵拉胆囊颈附近组织以暴露胆囊动脉", out)
    out = re.sub(r"正在牵拉和暴露组织", f"抓钳牵拉胆囊颈和胆囊体以暴露{exposure_object}", out)
    out = re.sub(r"(?:抓钳)?(?:正在)?牵拉组织", f"抓钳牵拉胆囊颈和胆囊体以暴露{exposure_object}", out)
    out = re.sub(r"抓钳牵拉胆囊颈和胆囊体以暴露操作区域", f"抓钳牵拉胆囊颈和胆囊体以暴露{exposure_object}", out)
    non_scissors_object = {
        "CalotTriangleDissection": "肝胆三角纤维组织",
        "GallbladderDissection": "胆囊床粘连组织",
    }.get(phase, "局部纤维组织")
    hook_dissection_object = (
        "肝胆三角组织"
        if phase in {"CalotTriangleDissection", "ClippingCutting"}
        else "胆囊床组织"
        if phase == "GallbladderDissection"
        else "局部纤维组织"
    )
    out = re.sub(
        r"电凝钩(?:正在)?分离(?:胆囊周围组织|胆囊(?!管|动脉|床|周围))",
        f"电凝钩分离{hook_dissection_object}",
        out,
    )
    out = re.sub(
        r"(电凝钩|双极电凝钳?)(?:正在|已)?(?:剪断|切断|离断)(?:胆囊管|胆囊动脉)",
        rf"\1分离{non_scissors_object}",
        out,
    )
    out = re.sub(r"参与(胆囊管|胆囊动脉)(夹闭|切断)相关操作", r"\1\2", out)
    out = out.replace("相关操作", "")
    return _polish_summary_wording(out)


def _downgrade_cut_claim(text: str, target_cn: str, reason: str = "cvs") -> str:
    if reason == "cvs":
        replacement = f"剪刀在{target_cn}邻近区域操作，CVS尚未达成，需核查后再剪断"
    else:
        replacement = f"剪刀在{target_cn}邻近区域操作，尚未确认该目标已夹闭，需核查后再剪断"
    patterns = (
        rf"剪刀(?:正在|已)?(?:剪断|切断|离断|夹断){target_cn}",
        rf"(?:剪切|剪断|切断|离断|夹断){target_cn}",
        rf"{target_cn}(?:已经|已|被|正在)?(?:剪断|切断|离断|夹断)",
    )
    out = text
    for pattern in patterns:
        out = re.sub(pattern, replacement, out)
    return out


def _downgrade_repeated_clip_claim(text: str, target_cn: str) -> str:
    residual = f"可见已夹闭的{target_cn}残端"
    patterns = (
        rf"钛夹钳(?:正在)?夹闭{target_cn}",
        rf"施夹器(?:正在)?夹闭{target_cn}",
        rf"(?:正在)?(?:夹闭|闭合|施夹){target_cn}",
    )
    out = text
    if re.search(rf"{target_cn}.{0,8}残端", out):
        return out
    for pattern in patterns:
        out = re.sub(pattern, residual, out)
    return out


def _apply_surgical_sequence_rules(
    summary_text: Any,
    dominant_phase: str,
    prior_state: Dict[str, Any],
    visual: Optional[Dict[str, Any]] = None,
) -> tuple:
    """Apply irreversible LC workflow and SVO wording rules before display/storage."""
    phase = _canonical_phase(dominant_phase or "Unknown")
    out = _normalize_svo_wording(summary_text, phase)
    applied: List[str] = []
    visual = visual or {}
    visibility_flags = _visibility_flags_from_visual(visual)
    focused_visibility = visual.get("visibility_secondary_review") or {}
    focused_bag_inside = bool(
        focused_visibility.get("success")
        and _safe_float(focused_visibility.get("confidence"), 0.0) >= 0.75
        and str(focused_visibility.get("classification") or "").lower()
        == "specimen_bag_inside"
    )

    if visibility_flags["out_of_body"]:
        prior_phase = _stable_prior_phase(prior_state, phase)
        if prior_phase != "Unknown" and phase != prior_phase:
            phase = prior_phase
            applied.append("scope_exit_preserves_surgical_phase")

    if phase == "CalotTriangleDissection" and _focused_clip_action_confirmed(visual):
        phase = "ClippingCutting"
        out = _replace_current_phase_text(out, phase)
        out = out.replace("CVS安全视野确认中", "CVS处于夹闭前后安全核查中")
        applied.append("focused_clip_starts_clipping_phase")

    current_order = SEQUENCE_PHASE_ORDER.get(phase, -1)
    prior_max_order = int(prior_state.get("max_phase_order", -1))
    if 0 <= current_order < prior_max_order:
        stable_phase = _stable_prior_phase(prior_state, phase)
        if SEQUENCE_PHASE_ORDER.get(stable_phase, -1) >= prior_max_order:
            phase = stable_phase
            out = _replace_current_phase_text(out, phase)
            if phase == "ClippingCutting":
                out = out.replace("CVS安全视野确认中", "CVS处于夹闭前后安全核查中")
            applied.append("phase_no_backward_regression")

    if phase == "GallbladderDissection" and "ClippingCutting" not in (
        prior_state.get("reached_phases") or set()
    ):
        phase = _stable_prior_phase(prior_state, "CalotTriangleDissection")
        if not visibility_flags["out_of_body"]:
            out = _replace_current_phase_text(out, phase)
        applied.append("dissection_requires_clipping_phase")

    if phase in LATE_WORKFLOW_PHASES and not _late_workflow_prerequisites_met(prior_state):
        phase = _stable_prior_phase(prior_state, "CalotTriangleDissection")
        if not visibility_flags["out_of_body"]:
            for pattern in (
                r"将胆囊装入标本袋并准备取出",
                r"胆囊装袋取出后[^。；;]*",
                r"牵拉装有胆囊的标本袋经切口取出",
            ):
                out = re.sub(rf"[，,；;。]?\s*{pattern}", "", out)
            out = _replace_current_phase_text(out, phase)
            if re.fullmatch(r"当前处于[^，,。；;]+[，,。]?", out.strip()):
                out = f"{out.rstrip('，。')}，{_phase_visual_context(phase, phase)}。"
        applied.append("late_phase_requires_clipping_and_dissection")

    if phase == "Preparation" and prior_state.get("formal_started"):
        phase = prior_state.get("last_phase") if prior_state.get("last_phase") != "Unknown" else "CalotTriangleDissection"
        out = _replace_current_phase_text(out, phase)
        applied.append("phase_no_return_to_preparation")

    if phase == "Preparation":
        preparation_text = re.sub(
            r"(双极电凝钳?|电凝钩|剪刀|抓钳|钳子)(?:正在)?分离(?:胆囊管|胆囊动脉)",
            r"\1分离局部粘连组织",
            out,
        )
        for pattern in (
            r"(?:钛夹钳|施夹器|施夹钳)(?:正在)?(?:夹闭|闭合|施夹)(?:胆囊管|胆囊动脉)",
            r"剪刀(?:正在|已)?(?:剪断|切断|离断)(?:胆囊管|胆囊动脉)",
        ):
            preparation_text = re.sub(rf"[，,；;。]?\s*{pattern}", "", preparation_text)
        if preparation_text != out:
            out = preparation_text
            applied.append("preparation_suppresses_target_specific_action")

    premature_retrieval = bool(re.search(
        r"胆囊(?:装袋)?取出后|胆囊取出与装袋|将胆囊装入标本袋|"
        r"标本袋(?:牵拉|取出)|标本装袋",
        out,
    ))
    if (
        premature_retrieval
        and phase not in LATE_WORKFLOW_PHASES
        and not prior_state.get("packaging_seen")
        and not visibility_flags["out_of_body"]
    ):
        out = f"当前处于{PHASE_LABEL_CN.get(phase, phase)}，{_phase_visual_context(phase, phase)}。"
        if phase == "CalotTriangleDissection":
            out += "CVS安全视野确认中。"
        elif phase == "ClippingCutting":
            out += "CVS处于夹闭前后安全核查中。"
        applied.append("suppress_premature_retrieval_language")
    elif (
        premature_retrieval
        and phase == "CleaningCoagulation"
        and not prior_state.get("packaging_seen")
        and not visibility_flags["out_of_body"]
    ):
        out = "当前处于清洁凝血，清理胆囊床并复查术野。"
        if visibility_flags["fog_active"]:
            out += "镜头起雾，手术视野受遮挡。"
        elif visibility_flags["fog_resolved"]:
            out += "雾已去除，腹腔视野恢复。"
        applied.append("pre_packaging_cleaning_suppresses_retrieval_language")

    if (
        focused_bag_inside
        and _late_workflow_prerequisites_met(prior_state)
        and not prior_state.get("post_retrieval_review")
        and phase in {
            "GallbladderDissection",
            "CleaningCoagulation",
            "GallbladderPackaging",
            "GallbladderRetraction",
        }
        and not (
            phase == "GallbladderRetraction"
            and prior_state.get("packaging_seen")
        )
    ):
        if phase != "GallbladderPackaging":
            phase = "GallbladderPackaging"
            out = _replace_current_phase_text(out, phase)
            applied.append("focused_bag_starts_packaging_phase")

    if phase == "GallbladderRetraction" and not prior_state.get("packaging_seen"):
        phase = _stable_prior_phase(prior_state, "GallbladderDissection")
        if not visibility_flags["out_of_body"]:
            out = _replace_current_phase_text(out, phase)
        applied.append("retraction_requires_prior_packaging")

    if prior_state.get("packaging_seen") and phase not in {
        "GallbladderPackaging",
        "CleaningCoagulation",
        "GallbladderRetraction",
        "Unknown",
    }:
        phase = "CleaningCoagulation"
        out = _replace_current_phase_text(out, phase)
        applied.append("phase_after_packaging_only_cleaning")

    if phase == "GallbladderPackaging" and prior_state.get("post_retrieval_review"):
        # After retrieval/out-of-body, the phase expert often jitters back to
        # GallbladderPackaging on the re-entry review frames. Do not re-assert
        # active bagging; treat it as the post-retrieval review tail instead.
        phase = "CleaningCoagulation"
        out = _replace_current_phase_text(out, phase)
        applied.append("packaging_after_retrieval_to_review")

    cleaned_clip_out = _remove_unsupported_calot_clip_claims(out, phase, prior_state, visual)
    if cleaned_clip_out != out:
        out = cleaned_clip_out
        applied.append("drop_unsupported_calot_clip_claim")

    visibility = visual.get("visibility") or {}
    focused_scope_exit = bool(
        visibility_flags["out_of_body"]
        and _safe_float(visibility.get("confidence"), 0.0) >= 0.85
        and visibility.get("evidence_source") == "visibility_secondary_review"
    )
    if (
        phase == "GallbladderPackaging"
        and OUT_OF_BODY_RE.search(out)
        and not prior_state.get("packaging_seen")
        and not focused_scope_exit
    ):
        # The first packaging window often contains a large white specimen bag
        # or scope rim. Do not turn that first bagging view into a scope-exit
        # event; true scope exit is only allowed after bagging has already been
        # observed in earlier windows.
        out = "当前处于胆囊取出与装袋，将胆囊装入标本袋并准备取出。"
        applied.append("first_packaging_suppresses_out_of_body")

    current_cvs = _has_cvs_achieved_text(out)
    if (visual.get("cvs") or {}).get("status") == "achieved":
        current_cvs = True

    for target_label, target_cn in (("cystic_duct", "胆囊管"), ("cystic_artery", "胆囊动脉")):
        if (
            phase == "GallbladderDissection"
            and target_label in (prior_state.get("clipped") or set())
            and _has_clip_target_text(out, target_cn)
        ):
            repeated_patterns = (
                rf"(?:钛夹钳|施夹器|施夹钳)?(?:正在)?(?:夹闭|闭合|施夹){target_cn}",
                rf"(?:可见)?(?:已)?夹闭的?{target_cn}残端",
                rf"{target_cn}残端已(?:由夹子)?(?:夹闭|闭合)",
            )
            for pattern in repeated_patterns:
                out = re.sub(rf"[，,；;。]?\s*{pattern}", "", out)
            applied.append(f"drop_repeated_target_clip_after_dissection_{target_label}")

        if target_label in (prior_state.get("cut") or set()) and _has_clip_target_text(out, target_cn):
            out = _downgrade_repeated_clip_claim(out, target_cn)
            applied.append(f"clip_after_cut_to_residual_{target_label}")

        if _has_cut_target_text(out, target_cn):
            same_window_clip = _has_clip_target_text(out, target_cn)
            target_previously_clipped = target_label in (prior_state.get("clipped") or set())
            if not (prior_state.get("cvs_achieved") or current_cvs):
                out = _downgrade_cut_claim(out, target_cn, "cvs")
                applied.append(f"cut_requires_cvs_{target_label}")
            elif not (target_previously_clipped or same_window_clip):
                out = _downgrade_cut_claim(out, target_cn, "clip")
                applied.append(f"cut_requires_prior_clip_{target_label}")

    if phase == "GallbladderDissection":
        post_clipping_text = re.sub(
            r"(双极电凝钳?|电凝钩|剪刀|抓钳|钳子)(?:正在)?分离(?:胆囊管|胆囊动脉)",
            r"\1分离胆囊与胆囊床粘连组织",
            out,
        )
        if post_clipping_text != out:
            out = post_clipping_text
            applied.append("post_clipping_target_to_gallbladder_bed")
        post_active_clip_text = out
        for pattern in (
            r"(?:钛夹钳|施夹器|施夹钳)(?:正在)?(?:夹闭|闭合|施夹)(?:胆囊管|胆囊动脉)",
            r"目标组织正在接受夹闭处理[^。；;]*",
        ):
            post_active_clip_text = re.sub(
                rf"[，,；;。]?\s*{pattern}",
                "",
                post_active_clip_text,
            )
        if post_active_clip_text != out:
            out = post_active_clip_text
            applied.append("gallbladder_dissection_drops_active_clipping")
        post_clipping_context = re.sub(
            r"电凝钩沿肝胆三角解剖层次分离纤维脂肪组织，?以逐步扩大关键结构暴露",
            "电凝钩沿胆囊壁与肝床间隙分离粘连组织，逐步扩大剥离范围",
            out,
        )
        post_clipping_context = re.sub(
            r"[，,；;。]?\s*CVS(?:安全视野确认中|处于夹闭前后安全核查中|评估中)",
            "",
            post_clipping_context,
        )
        if post_clipping_context != out:
            out = post_clipping_context
            applied.append("gallbladder_dissection_suppresses_calot_context")
        if re.fullmatch(r"当前处于胆囊分离[，,。]?", out.strip()):
            out = "当前处于胆囊分离，画面以胆囊床术野观察为主。"

    if phase == "GallbladderPackaging":
        normalized_packaging = _normalize_packaging_summary(out, visual)
        if normalized_packaging and normalized_packaging != out:
            out = normalized_packaging
            applied.append("packaging_suppresses_stale_dissection")
    elif phase == "CleaningCoagulation" and prior_state.get("packaging_seen"):
        retrieval_confirmed = "GallbladderRetraction" in (
            prior_state.get("reached_phases") or set()
        )
        normalized_cleaning = _normalize_post_packaging_cleaning_summary(
            out,
            retrieval_confirmed=retrieval_confirmed,
            review_already_seen=(
                prior_state.get("post_retrieval_review")
                if retrieval_confirmed
                else prior_state.get("post_packaging_reentry")
            ),
        )
        if normalized_cleaning and normalized_cleaning != out:
            out = normalized_cleaning
            applied.append("post_packaging_suppresses_stale_dissection")
    elif phase == "CleaningCoagulation":
        normalized_cleaning = _normalize_prepackaging_cleaning_summary(out)
        if normalized_cleaning and normalized_cleaning != out:
            out = normalized_cleaning
            applied.append("pre_packaging_suppresses_stale_calot_and_clip")
    elif phase == "GallbladderRetraction":
        normalized_retraction = _normalize_retraction_summary(out, visual)
        if normalized_retraction != out:
            out = normalized_retraction
            applied.append("retraction_suppresses_stale_instrument_action")

    out = _strip_unsupported_stapler_wording(out, visual)
    out = _strip_visual_rejected_clip_claims(out, visual, phase)
    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"[，,]\s*[。；;]", "。", out)
    out = re.sub(r"。{2,}", "。", out).strip(" ，；")
    return _strip_clipping_noise(out), phase, applied


def _local_visibility_cue_from_bgr_frames(frames: List[Any]) -> Dict[str, Any]:
    """Fast local visibility cue for Stage 1.

    This is intentionally conservative. It only uses image evidence:
    - fog/smoke: low edge detail plus low saturation/high brightness
    - outside-body/trocar transition candidate: large low-saturation bright
      trocar/wall region in the laparoscope field with reduced tissue view
    OpenVLM can later refine or remove the claim.
    """
    if not frames:
        return {"fog": False, "confidence": 0.0}
    try:
        import cv2
        import numpy as np
    except Exception:
        return {"fog": False, "confidence": 0.0}

    metrics = []
    for frame in frames:
        if frame is None:
            continue
        try:
            small = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            gray_std = float(gray.std())
            sat_mean = float(hsv[:, :, 1].mean())
            low_sat_bright = float(np.mean((hsv[:, :, 1] < 45) & (hsv[:, :, 2] > 115)))
            h, w = gray.shape[:2]
            yy, xx = np.ogrid[:h, :w]
            cx = (w - 1) / 2.0
            cy = (h - 1) / 2.0
            radius = np.sqrt(((xx - cx) / max(cx, 1.0)) ** 2 + ((yy - cy) / max(cy, 1.0)) ** 2)
            inner = radius < 0.58
            annulus = (radius >= 0.58) & (radius < 0.98)
            nonblack = hsv[:, :, 2] > 30
            bright_low_sat = (hsv[:, :, 1] < 55) & (hsv[:, :, 2] > 145)
            tissue_like = (
                (hsv[:, :, 1] > 45)
                & (hsv[:, :, 2] > 55)
                & (
                    ((hsv[:, :, 0] >= 0) & (hsv[:, :, 0] <= 30))
                    | ((hsv[:, :, 0] >= 150) & (hsv[:, :, 0] <= 179))
                )
            )
            annulus_bright = float(np.mean(bright_low_sat[annulus & nonblack])) if np.any(annulus & nonblack) else 0.0
            inner_tissue = float(np.mean(tissue_like[inner & nonblack])) if np.any(inner & nonblack) else 0.0
            overall_bright = float(np.mean(bright_low_sat[nonblack])) if np.any(nonblack) else 0.0
            trocar_like = bool(
                (annulus_bright > 0.24 and overall_bright > 0.16 and inner_tissue < 0.62)
                or (overall_bright > 0.38 and inner_tissue < 0.36)
            )
            foggy = bool(
                (lap_var < 1500 and sat_mean < 65 and low_sat_bright > 0.25)
                or (lap_var < 700 and sat_mean < 85 and gray_std < 60 and low_sat_bright > 0.20)
            )
            metrics.append({
                "foggy": foggy,
                "trocar_like": trocar_like,
                "lap_var": lap_var,
                "gray_std": gray_std,
                "sat_mean": sat_mean,
                "low_sat_bright": low_sat_bright,
                "annulus_bright": annulus_bright,
                "inner_tissue": inner_tissue,
                "overall_bright": overall_bright,
            })
        except Exception:
            continue

    if not metrics:
        return {"fog": False, "confidence": 0.0}
    fog_count = sum(1 for item in metrics if item["foggy"])
    fog_ratio = fog_count / max(1, len(metrics))
    out_of_body_count = sum(1 for item in metrics if item.get("trocar_like"))
    out_of_body_ratio = out_of_body_count / max(1, len(metrics))
    confidence = min(0.92, max(0.0, 0.35 + fog_ratio * 0.55))
    out_confidence = min(0.94, max(0.0, 0.35 + out_of_body_ratio * 0.58))
    representative = max(
        metrics,
        key=lambda item: (
            item.get("trocar_like", False),
            item["foggy"],
            item.get("annulus_bright", 0.0),
            item["low_sat_bright"],
            -item["lap_var"],
        ),
    )
    strong_transient = bool(
        representative.get("annulus_bright", 0.0) >= 0.28
        and representative.get("overall_bright", 0.0) >= 0.18
        and representative.get("inner_tissue", 1.0) < 0.68
    )
    strong_external_view = bool(
        representative.get("inner_tissue", 1.0) <= 0.06
        and out_of_body_ratio >= 0.50
        and representative.get("annulus_bright", 0.0) >= 0.24
    )
    centered_external_view = bool(
        representative.get("inner_tissue", 1.0) <= 0.03
        and out_of_body_ratio >= 0.25
        and representative.get("annulus_bright", 0.0) >= 0.25
        and representative.get("overall_bright", 0.0) >= 0.16
    )
    out_candidate = out_of_body_ratio >= 0.20 or strong_transient
    out_confirmed = bool(
        (
            out_of_body_ratio >= 0.66
            and representative.get("annulus_bright", 0.0) >= 0.28
            and representative.get("inner_tissue", 1.0) <= 0.08
        )
        or strong_external_view
        or centered_external_view
    )
    return {
        "fog": fog_ratio >= 0.34,
        "confidence": round(confidence if fog_ratio >= 0.34 else fog_ratio * 0.3, 3),
        "fog_ratio": round(fog_ratio, 3),
        "out_of_body_candidate": out_candidate,
        "out_of_body": out_confirmed,
        "out_of_body_confidence": round(out_confidence if out_candidate else out_of_body_ratio * 0.3, 3),
        "out_of_body_ratio": round(out_of_body_ratio, 3),
        "lap_var": round(representative["lap_var"], 1),
        "gray_std": round(representative["gray_std"], 1),
        "sat_mean": round(representative["sat_mean"], 1),
        "low_sat_bright": round(representative["low_sat_bright"], 3),
        "annulus_bright": round(representative.get("annulus_bright", 0.0), 3),
        "inner_tissue": round(representative.get("inner_tissue", 0.0), 3),
        "overall_bright": round(representative.get("overall_bright", 0.0), 3),
        "summary": "镜头起雾，手术视野受遮挡。" if fog_ratio >= 0.34 else "",
        "out_of_body_summary": "镜头移出体外，画面切换至套管口或腹壁外场景。" if out_confirmed else "",
        "prompt_hint": (
            "本地视觉特征提示可能出现套管口/体外过渡：画面中低饱和高亮区域占比高，"
            "腹腔内组织视野减少。请根据图像本身复核是否为镜头移出体外、套管口或腹壁外场景。"
        ) if out_candidate else "",
    }


def _fuse_packaging_scope_exit_evidence(
    visual: Dict[str, Any],
    local_cue: Dict[str, Any],
    phase: str,
) -> Dict[str, Any]:
    """Promote a strong extra-abdominal transition missed by the local VLM.

    The fusion is based on phase and image metrics only; it never uses a video
    name, timestamp, or frame index. A confident VLM clear/fog decision remains
    authoritative. This handles the model's zero-confidence `clear` response
    on bright abdominal-skin or port views during specimen retrieval.
    """
    result = dict(visual or {})
    visibility = dict(result.get("visibility") or {})
    if _visibility_flags_from_visual(result)["out_of_body"]:
        return result
    if _canonical_phase(phase) not in {"GallbladderPackaging", "GallbladderRetraction"}:
        return result

    status = str(visibility.get("status") or "clear").lower()
    vlm_confidence = _safe_float(visibility.get("confidence"), 0.0)
    if vlm_confidence >= 0.75 and status in {"clear", "foggy", "blurred", "blocked"}:
        return result

    strong_external_surface = bool(
        local_cue.get("out_of_body_candidate")
        and _safe_float(local_cue.get("inner_tissue"), 1.0) <= 0.03
        and _safe_float(local_cue.get("annulus_bright"), 0.0) >= 0.52
        and _safe_float(local_cue.get("overall_bright"), 0.0) >= 0.45
        and _safe_float(local_cue.get("low_sat_bright"), 0.0) >= 0.45
        and _safe_float(local_cue.get("out_of_body_confidence"), 0.0) >= 0.40
    )
    if not strong_external_surface:
        return result

    visibility.update({
        "status": "out_of_body",
        "out_of_body": True,
        "fog": False,
        "fog_cleared": False,
        "confidence": max(0.78, _safe_float(local_cue.get("out_of_body_confidence"), 0.0)),
        "evidence": "bright low-saturation external surface with no intra-abdominal tissue",
        "evidence_source": "local_visual_fusion",
    })
    result["visibility"] = visibility
    return result


def _expert_snapshot_summary(expert_pack: Dict[str, Any], start_time: float, end_time: float, frame_count: int) -> str:
    """Build a deterministic live summary from fast local experts.

    This is the realtime path: no Gemini/R1 call is allowed here. It keeps the
    surgery progress panel close to the actual video clock, while slower R1/VLM
    passes can overwrite the same window later as Stage 2.
    """
    phase_raw = (expert_pack.get("phase") or {}).get("label", "")
    phase = _canonical_phase(phase_raw)
    phase_cn = PHASE_LABEL_CN.get(phase_raw) or PHASE_LABEL_CN.get(phase) or "当前阶段"
    target_hint = _target_hint_from_triplet((expert_pack.get("triplet") or {}))
    target_label = target_hint.get("label") or "cystic_duct"
    target_cn = _target_cn(target_label)

    tools = (expert_pack.get("yolo") or {}).get("tools", [])[:5]
    short_action = expert_pack.get("short_action") or {}
    tool_names = []
    tool_frame_counts: Dict[str, int] = {}
    for t in tools:
        label = t.get("label", "")
        frames_seen = int(t.get("frames_seen") or 0)
        # A single-frame detector hit in the preparation phase is often a
        # glare/tool-edge false positive. Do not promote it to a surgical action.
        if frames_seen < 2 and label in {"hook", "bipolar", "scissors", "specimen_bag", "clipper"}:
            continue
        if phase == "Preparation" and label in {"bipolar", "specimen_bag"}:
            continue
        if label == "clipper":
            tool_names.append("钛夹钳")
            tool_frame_counts["钛夹钳"] = max(tool_frame_counts.get("钛夹钳", 0), frames_seen)
        else:
            name = TOOL_LABEL_CN.get(label, label)
            if name:
                tool_names.append(name)
                tool_frame_counts[name] = max(tool_frame_counts.get(name, 0), frames_seen)
    if short_action.get("detected") and not any(name in tool_names for name in ("电凝钩", "剪刀", "钛夹钳")):
        tool_names.append("尖端器械")

    bipolar_evidence = _bipolar_forceps_evidence(expert_pack)
    if bipolar_evidence.get("resolved"):
        tool_names = [name for name in tool_names if name not in {"电凝钩", "双极电凝"}]
        if "双极电凝钳" not in tool_names:
            tool_names.append("双极电凝钳")

    tool_set = set(tool_names)
    action_text = ""
    if short_action.get("detected"):
        action_text = short_action.get("description", "短时器械接触动作")
        action_text = action_text.replace("可见", "").strip(" ，。")

    clipper_frames = int(tool_frame_counts.get("钛夹钳") or tool_frame_counts.get("施夹器") or 0)
    scissors_frames = int(tool_frame_counts.get("剪刀") or 0)
    action_parts = _triplet_operation_phrases(expert_pack.get("triplet") or {}, max_items=4, phase=phase)
    if scissors_frames < 3:
        # Triplet may map hook dissection near the cystic plate to a scissors
        # triplet. Do not display scissors actions unless YOLO also saw scissors
        # across multiple sampled frames.
        action_parts = [
            part for part in action_parts
            if not re.match(r"剪刀", part)
        ]
    hemlok_clip = expert_pack.get("hemlok_clip") or {}
    clip_detector = expert_pack.get("clip_detector") or {}
    clipper_context = clipper_frames >= 4 and phase == "ClippingCutting"
    if clipper_context:
        # In the clipping/cutting phase, broad Triplet progress labels such as
        # "irrigator dissect gallbladder" often describe the same close-up field
        # but read as a different operation. Keep explicit core clip/cut/coag
        # actions and drop low-value dissection phrases.
        action_parts = [
            part for part in action_parts
            if not re.search(r"(分离胆囊|分离胆囊板|分离肝脏|分离腹膜|分离肝胆三角)", part)
        ]
    if phase == "GallbladderPackaging":
        action_parts = [
            part for part in action_parts
            if re.search(r"装袋|取出|标本袋|胆囊袋|凝血|出血|纱布|棉片", part)
        ]
        if not any(re.search(r"装袋|取出|标本袋|胆囊袋", part) for part in action_parts):
            action_parts.append("将胆囊装入标本袋并准备取出")
    has_clip_action = any(
        term in " ".join(action_parts)
        for term in ("夹闭", "闭合", "施夹", "夹持", "夹子", "Hem-o-lok", "钛夹")
    )
    if (
        clipper_context and not has_clip_action
    ):
        # The Triplet model can miss the exact I/V/T label while YOLO sees the
        # clip applier across most sampled frames. Surface the observed applier
        # action immediately; OpenVision can later refine it to placed clip vs
        # active clipping.
        action_parts.append(f"钛夹钳夹闭{target_cn}")
        has_clip_action = True
    has_cut_action = any("切断" in part for part in action_parts)
    if scissors_frames >= 3 and not has_cut_action and phase in {"ClippingCutting", "GallbladderDissection", "CleaningCoagulation"}:
        action_parts.append(f"剪刀正在切断{target_cn}")
        has_cut_action = True
    if "电凝钩" in tool_set and not has_clip_action and not has_cut_action:
        if phase == "Preparation":
            action_parts.append("电凝钩进入或调整术野")
        elif phase == "CalotTriangleDissection":
            action_parts.append("电凝钩沿肝胆三角解剖层次分离纤维脂肪组织，以逐步扩大关键结构暴露")
        elif phase == "GallbladderDissection":
            action_parts.append("电凝钩沿胆囊壁与肝床间隙分离粘连组织，逐步扩大剥离范围")
        else:
            action_parts.append("电凝钩分离局部组织")
    if "冲洗器" in tool_set and not has_clip_action:
        action_parts.append("正在冲洗或清理术野")
    if "双极电凝钳" in tool_set and not has_clip_action and not has_cut_action and not any(
        "双极电凝钳" in part for part in action_parts
    ):
        if "出血" in action_text:
            action_parts.append("双极电凝钳处理出血点")
        elif phase == "CalotTriangleDissection":
            bipolar_action = "双极电凝钳反复开合夹持并分离肝胆三角内纤维脂肪组织"
            if "抓钳" in tool_set:
                bipolar_action += "，抓钳配合牵拉以扩大关键结构暴露"
            else:
                bipolar_action += "，以扩大关键结构暴露"
            action_parts.append(bipolar_action)
        elif phase == "GallbladderDissection":
            action_parts.append("双极电凝钳夹持并分离胆囊床粘连组织，逐步扩大胆囊与肝床间隙")
        else:
            action_parts.append("双极电凝钳夹持并分离局部组织")
    elif "双极电凝" in tool_set and "出血" in action_text:
        action_parts.append("双极电凝处理出血点")
    if "标本袋" in tool_set and phase == "GallbladderPackaging":
        action_parts.append("进入标本装袋或取出相关步骤")
    if not action_parts and "抓钳" in tool_set:
        if phase == "CalotTriangleDissection":
            action_parts.append("抓钳牵拉胆囊颈和胆囊体以暴露肝胆三角")
        elif phase == "GallbladderDissection":
            action_parts.append("抓钳牵拉胆囊体以暴露胆囊床")
        else:
            action_parts.append("抓钳牵拉胆囊颈和胆囊体以暴露操作区域")

    if phase == "GallbladderRetraction":
        action_parts = ["牵拉装有胆囊的标本袋经切口取出"]

    visibility_cue = expert_pack.get("local_visibility") or {}
    if visibility_cue.get("out_of_body"):
        # Color/geometry cues only nominate a scope-exit candidate. Fog and a
        # white specimen bag can look identical to a trocar lumen, so the live
        # summary waits for the structured VLM before asserting out-of-body.
        if phase == "GallbladderRetraction":
            return "当前处于标本袋牵拉取出，牵拉装有胆囊的标本袋经切口取出。"

    parts = [] if phase_cn == "当前阶段" else [f"当前处于{phase_cn}"]
    if action_parts:
        prefix = "，" if parts else ""
        parts.append(prefix + "；".join(dict.fromkeys(action_parts)))
    elif phase_cn and phase_cn != "当前阶段":
        parts.append("，" + _phase_visual_context(phase_raw, phase))
    else:
        parts.append("画面以术野观察和状态确认为主")
    if action_text:
        parts.append(f"。{action_text}")
    cvs_note = _cvs_realtime_note(phase, tool_set, action_text)
    if cvs_note:
        parts.append(f"。{cvs_note}")
    if visibility_cue.get("fog"):
        parts.append("。镜头起雾，手术视野受遮挡")
    summary = _strip_clipping_noise(_sanitize_target_language("".join(parts).rstrip("。") + "。", target_label))
    summary = _resolve_bipolar_hook_conflict(summary, expert_pack)
    return _expand_vague_operation_language(summary, phase)


def _frame_analysis_has_visual_signal(frame_analyses: List[Dict[str, Any]]) -> bool:
    """Return True only when SurgR1 rows contain usable, non-error evidence."""
    if not frame_analyses:
        return False

    error_terms = (
        "[Error:",
        "All connection attempts failed",
        "Connection refused",
        "service unavailable",
        "SurgR1",
        "Traceback",
    )
    signal_fields = (
        "phase",
        "action",
        "tools",
        "tool_localization",
        "surgical_phase",
        "surgical_action",
    )
    for item in frame_analyses:
        if not isinstance(item, dict):
            continue
        for field in signal_fields:
            value = item.get(field)
            if value is None:
                continue
            text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            text = text.strip()
            if not text or text.lower() in {"unknown", "none", "null", "[]", "{}"}:
                continue
            if any(term in text for term in error_terms):
                continue
            return True
    return False


def _expert_pack_has_key_event_candidate(expert_pack: Dict[str, Any]) -> bool:
    """Gate expensive local VLM refinement to windows with plausible key events."""
    expert_pack = expert_pack or {}
    phase = _canonical_phase((expert_pack.get("phase") or {}).get("label", "") or "")
    yolo_labels = {
        str(tool.get("label") or "").strip().lower()
        for tool in ((expert_pack.get("yolo") or {}).get("tools") or [])
        if isinstance(tool, dict)
    }
    clip_detector = expert_pack.get("clip_detector") or {}
    clip_conf = _safe_float(clip_detector.get("max_confidence"), 0.0)
    clip_frames = int(clip_detector.get("frames_seen") or 0)
    clip_candidate = bool(
        "clipper" in yolo_labels
        or ((expert_pack.get("hemlok_clip") or {}).get("detected"))
        or (
            int(clip_detector.get("detections_total") or 0) > 0
            and clip_frames >= 3
            and clip_conf >= 0.20
            and phase in {"CalotTriangleDissection", "ClippingCutting", "GallbladderDissection"}
        )
        # Persistent low-confidence detections: the clip YOLO scores real
        # polymer (blue/white Hem-o-lok style) clips around 0.07-0.13, so a
        # confidence gate alone would drop true positives. Persistence across
        # many frames is what separates them from one-off glare hits; route
        # them to local VLM review instead of asserting them directly.
        or (
            int(clip_detector.get("detections_total") or 0) >= 8
            and clip_frames >= 5
            and clip_conf >= 0.07
            and phase in {"CalotTriangleDissection", "ClippingCutting", "GallbladderDissection"}
        )
    )
    scissors_candidate = bool(
        "scissors" in yolo_labels
        and phase in {"ClippingCutting", "GallbladderDissection", "CleaningCoagulation"}
    )
    bag_candidate = bool(
        "specimen_bag" in yolo_labels
        or phase in {"GallbladderPackaging", "GallbladderRetraction"}
    )
    visibility = expert_pack.get("local_visibility") or {}
    visibility_candidate = bool(
        visibility.get("fog")
        or visibility.get("out_of_body")
        or visibility.get("out_of_body_candidate")
    )
    bleeding_candidate = bool(
        "bipolar" in yolo_labels
        and phase in {"GallbladderDissection", "CleaningCoagulation", "GallbladderPackaging"}
    )
    instrument_conflict_candidate = "bipolar" in yolo_labels and "hook" in yolo_labels
    return bool(
        clip_candidate
        or scissors_candidate
        or bag_candidate
        or visibility_candidate
        or bleeding_candidate
        or instrument_conflict_candidate
    )


def _merge_realtime_evidence(
    summary_text: str,
    stage1_summary: str = "",
    open_vlm_summary: str = "",
    short_action: Optional[Dict[str, Any]] = None,
) -> str:
    """Preserve high-value realtime evidence when slower refinement overwrites a window."""
    text = (summary_text or "").strip()
    short_action = short_action or {}
    evidence_terms = (
        "穿刺", "穿孔", "穿透", "钻孔", "器械进入", "进入视野", "吸引器", "抓钳",
        "电凝钩", "电钩", "双极电凝", "双极电凝钳", "剪刀", "施夹器", "钛夹钳", "Hem-o-lok", "Hemolok", "hemlock", "钛夹", "施夹", "夹闭",
        "CVS", "安全视野", "关键安全视野", "胆囊管", "胆囊动脉", "胆囊板", "两条结构",
        "纱布", "棉片", "棉球", "止血纱", "出血", "活动性出血", "渗血", "涌血", "止血",
        "起雾", "雾气", "烟雾", "模糊", "视野受遮挡", "视野不清",
        "镜头移出体外", "移出体外", "退出体外", "离开腹腔", "腹腔外", "套管口", "手术室场景",
        "gauze", "sponge", "bleeding", "hemostasis", "active bleeding",
        "outside the body", "out_of_body", "trocar", "operating room scene",
        "尖端", "接触", "牵拉", "暴露",
    )
    negative_terms = ("无明确新增动作", "未见明确器械", "未见手术器械", "没有器械")

    def _sentences(s: str) -> List[str]:
        import re as _re
        return [p.strip(" \n。；;") for p in _re.split(r"[。；;\n]+", s or "") if p.strip()]

    out_of_body_terms = (
        "镜头移出体外", "移出体外", "退出体外", "离开腹腔", "腹腔外",
        "腹壁外", "手术室场景", "outside the body", "out_of_body", "trocar outside", "extra-abdominal",
    )
    if open_vlm_summary and any(term in open_vlm_summary for term in out_of_body_terms):
        # Once the post-update visual reviewer determines that the scope has
        # left the cavity, stale intra-abdominal stage summaries should not be
        # carried forward into the live window text.
        text = ""

    def _append(sentence: str):
        nonlocal text
        sentence = sentence.strip(" \n。；;")
        if not sentence:
            return
        if any(term in sentence for term in negative_terms):
            return
        if not any(term in sentence for term in evidence_terms):
            return
        if sentence in text:
            return
        text = f"{text.rstrip('。')}。{sentence}。" if text else f"{sentence}。"

    sources = (open_vlm_summary,) if open_vlm_summary and any(term in open_vlm_summary for term in out_of_body_terms) else (stage1_summary, open_vlm_summary)
    for source in sources:
        for sentence in _sentences(source):
            _append(sentence)

    if short_action.get("detected"):
        _append(short_action.get("description", "短时器械接触动作"))

    return _strip_clipping_noise(text)


def _queue_embedding(session_id: str, window_id: int, summary_text: str,
                     start_time: float = 0, end_time: float = 0):
    """Queue embedding generation for a window summary (non-blocking, failure-tolerant)."""
    try:
        _embedding_svc = get_embedding_service()
        if _embedding_svc and summary_text and not summary_text.startswith("["):
            asyncio.create_task(_embedding_svc.add_window_embedding(
                session_id=session_id,
                window_id=window_id,
                summary_text=summary_text,
                metadata={
                    "start_time": start_time,
                    "end_time": end_time,
                }
            ))
    except Exception as e:
        logger.warning(f"[Embedding] Failed to queue embedding for window {window_id}: {e}")


def open_video_source(video_path: str):
    """
    Open a video source, handling different URL schemes:
    - http://, https://, rtsp:// - Network streams
    - device://N - Local capture device by index
    - device://name - Local capture device by name (Windows DirectShow)
    - file path - Local video file
    
    Returns:
        cv2.VideoCapture object or None if failed
    """
    import cv2
    import platform

    resolved = resolve_video_source(video_path)
    if resolved.is_simulator and resolved.source != video_path:
        cap = PacedVideoCapture(resolved.source, fps=resolved.fps, loop=False)
        logger.info(
            "[SurgR1] Using paced simulator source: %s @ %.1ffps",
            resolved.source,
            cap.get(cv2.CAP_PROP_FPS),
        )
        return cap
    
    if video_path.startswith("decklink://"):
        return DeckLinkCapture(video_path)

    if video_path.startswith("device://"):
        # Local capture device
        device_spec = video_path.replace("device://", "")
        
        try:
            device_id = int(device_spec)
            # Open by device index
            if platform.system() == "Linux":
                cap = cv2.VideoCapture(f"/dev/video{device_id}", cv2.CAP_V4L2)
            else:
                cap = cv2.VideoCapture(device_id)
        except ValueError:
            # Device name (Windows DirectShow)
            if platform.system() == "Windows":
                cap = cv2.VideoCapture(f"video={device_spec}", cv2.CAP_DSHOW)
            else:
                logger.warning(f"Device name specification only supported on Windows: {device_spec}")
                cap = cv2.VideoCapture(0)
        
        return cap
    else:
        # Network stream or local file
        cap = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
        return cap


# Global SAM3 streaming sessions
# Key: session_id, Value: dict with sam3_session_id, last_frame, consistency_checker, etc.
sam3_streaming_sessions: dict = {}

# Store latest SAM3 segmented frames for quick access
# Key: session_id, Value: dict with timestamp, image_base64, etc.
sam3_latest_frames: dict = {}

# Import consistency checker
from ..services.sam3_consistency import (
    SAM3ConsistencyChecker, 
    SAM3State, 
    ConsistencyConfig,
    parse_bboxes_from_surgr1
)

# Frame capture flags for playback
# Key: session_id, Value: bool (True = running)
frame_capture_flags: dict = {}


async def check_stream_ended(video_path: str) -> bool:
    """
    Check if a stream has ended by querying the stream server's /info endpoint.
    
    Args:
        video_path: The stream URL (e.g., http://localhost:9001/stream)
    
    Returns:
        True if the stream has ended, False otherwise
    """
    import aiohttp
    from urllib.parse import urlparse
    
    try:
        # Extract base URL from stream path
        parsed = urlparse(video_path)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        info_url = f"{base_url}/info"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(info_url, timeout=aiohttp.ClientTimeout(total=2)) as response:
                if response.status == 200:
                    info = await response.json()
                    video_ended = info.get("video_ended", False)
                    if video_ended:
                        logger.info(f"[StreamCheck] Stream ended detected from {info_url}")
                    return video_ended
    except Exception as e:
        logger.debug(f"[StreamCheck] Could not check stream status: {e}")
    
    return False


async def frame_capture_for_playback(
    session_id: str,
    video_source: str,
    is_realtime_stream: bool,
    stream_start_time: float
):
    """
    Independent frame capture task that runs at 10 FPS for smooth loop playback.
    This runs in parallel with SurgR1 analysis which is slower (~1 fps).
    """
    import cv2
    import time as time_module
    
    FRAME_SAVE_INTERVAL = 0.1  # 10 FPS for smooth loop playback
    loop = asyncio.get_running_loop()
    
    # Mark as running
    frame_capture_flags[session_id] = True
    
    # Open a separate video capture (supports device://, rtsp://, http://, files)
    # [perf] open_video_source 阻塞，放 executor
    cap = await loop.run_in_executor(None, open_video_source, video_source)
    if not cap or not cap.isOpened():
        logger.warning(f"[FrameCapture] Could not open video source for session {session_id}")
        return
    resolved_video_source = resolve_video_source(video_source)
    is_finite_simulator = bool(resolved_video_source.is_simulator and resolved_video_source.source != video_source)
    
    logger.info(f"[FrameCapture] Started frame capture task for session {session_id} at 10 FPS")
    
    saved_frame_idx = 0
    last_save_time = -FRAME_SAVE_INTERVAL
    
    try:
        # Get or create storage path once
        mysql_service = get_mysql_service()
        video_session = mysql_service.get_video_session(session_id)
        storage_path = video_session.get("storage_path") if video_session else None
        
        if not storage_path:
            frame_storage = get_frame_storage_service()
            video_name = video_session.get("video_name", "stream") if video_session else "stream"
            storage_path = frame_storage.create_session_folder(session_id, video_name)
            mysql_service.update_video_session(session_id, storage_path=storage_path)
            logger.info(f"[FrameCapture] Created storage folder: {storage_path}")
        
        frame_storage = get_frame_storage_service()
        
        while frame_capture_flags.get(session_id, False) and surgr1_continuous_flags.get(session_id, False):
            # [perf] cap.read() 放 executor 避免阻塞 asyncio loop
            ret, bgr_frame = await loop.run_in_executor(None, cap.read)
            
            if not ret:
                if is_finite_simulator:
                    logger.info(f"[FrameCapture] Simulator source ended for session {session_id}")
                    break
                if is_realtime_stream:
                    await asyncio.sleep(0.05)
                    continue
                else:
                    # End of file - restart
                    await loop.run_in_executor(None, cap.set, cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
            
            # Calculate current time
            if is_finite_simulator and hasattr(cap, "last_timestamp"):
                current_time = float(cap.last_timestamp())
            elif is_realtime_stream:
                current_time = time_module.time() - stream_start_time
            else:
                fps = cap.get(cv2.CAP_PROP_FPS) or 30
                frame_pos = cap.get(cv2.CAP_PROP_POS_FRAMES)
                current_time = frame_pos / fps
            
            # Check if we should save this frame
            if current_time - last_save_time >= FRAME_SAVE_INTERVAL:
                last_save_time = current_time
                
                try:
                    frame_storage.save_frame(
                        storage_path=storage_path,
                        timestamp=current_time,
                        frame_data=bgr_frame,
                        frame_idx=saved_frame_idx,
                        subfolder="frames"
                    )
                    saved_frame_idx += 1
                    
                    if saved_frame_idx % 25 == 0:  # Log every 5 seconds
                        logger.info(f"[FrameCapture] Saved {saved_frame_idx} frames for session {session_id}")
                except Exception as e:
                    logger.warning(f"[FrameCapture] Failed to save frame: {e}")
            
            # Small sleep to not overwhelm CPU
            await asyncio.sleep(0.02)  # ~50 fps read rate, save at 10 fps
    
    except asyncio.CancelledError:
        logger.info(f"[FrameCapture] Task cancelled for session {session_id}")
    except Exception as e:
        logger.error(f"[FrameCapture] Error: {e}")
    finally:
        frame_capture_flags[session_id] = False
        if cap is not None:
            try:
                await loop.run_in_executor(None, cap.release)
            except Exception:
                pass
        logger.info(f"[FrameCapture] Stopped for session {session_id}, saved {saved_frame_idx} frames")


def get_gpt_summarizer() -> GPTSummarizer:
    """Get or create GPT summarizer"""
    global gpt_summarizer
    if gpt_summarizer is None:
        gpt_summarizer = GPTSummarizer(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            model=settings.GPT_MODEL
        )
    return gpt_summarizer


def get_sam2_service() -> SAM2Service:
    """Get or create SAM2 service"""
    global sam2_service
    if sam2_service is None:
        sam2_service = SAM2Service(
            model_path=settings.SAM2_MODEL_PATH
        )
    return sam2_service


def get_tts_service() -> TTSService:
    """Get or create TTS service"""
    global tts_service
    if tts_service is None:
        tts_service = TTSService(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            voice=settings.TTS_VOICE,
            output_dir=settings.OUTPUT_DIR / "tts"
        )
    return tts_service


class AnalyzeWindowRequest(BaseModel):
    session_id: str
    start_time: float
    use_chinese: bool = False


class SummarizeRequest(BaseModel):
    text: str
    use_chinese: bool = False


class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None


class SAM2Request(BaseModel):
    session_id: str
    timestamp: float
    auto_detect: bool = True


class VLMAnalyzeRequest(BaseModel):
    session_id: str
    start_time: float
    use_vlm: bool = True  # Use local VLM instead of GPT


class ImageAnalysisRequest(BaseModel):
    """Request for image analysis API"""
    session_id: str
    start_time: float
    analysis_type: str = "all"  # "all", "phase", "action", "tools"


class IntegrateAnalysisRequest(BaseModel):
    """Request for integrating analysis results"""
    session_id: str
    start_time: float
    use_glm: bool = True  # Use GLM-4.6V-Flash instead of GPT


class ProcessVideoSurgR1GLMRequest(BaseModel):
    """Request for processing video with SurgR1 + GLM"""
    session_id: str
    use_chinese: bool = False
    use_glm_multimodal: bool = False  # Use GLM with images


class FrameData(BaseModel):
    """Frame data for batch analysis"""
    frame_idx: int
    timestamp: float
    image_base64: Optional[str] = None  # Base64 encoded image


class AnalyzeFramesBatchRequest(BaseModel):
    """Request for batch frame analysis from frontend queue"""
    session_id: str
    frames: List[FrameData]
    enable_glm_verification: bool = False  # 启用GLM验证R1分析结果
    glm_verification_async: bool = True    # GLM验证是否异步执行（不阻塞返回）


class SemanticSearchRequest(BaseModel):
    session_id: str
    query: str
    top_k: int = 5


class TextSearchRequest(BaseModel):
    session_id: str
    query: str


class TranslateSummaryRequest(BaseModel):
    text: str
    target_lang: str = "en"


class EventNodesRequest(BaseModel):
    language: str = "zh"
    force: bool = False
    max_windows: Optional[int] = None
    # Offline/batch callers can allow the local VLM more time than the
    # realtime UI default in services.event_nodes.timeout.
    timeout: Optional[float] = None


class ClinicalSummaryRequest(BaseModel):
    language: str = "zh"
    force: bool = False
    max_windows: Optional[int] = None
    max_events: Optional[int] = None
    video_title: Optional[str] = None
    output_dir: Optional[str] = None


@router.post("/analyze-frames-batch")
async def analyze_frames_batch(
    request: AnalyzeFramesBatchRequest,
    db: Session = Depends(get_db)
):
    """
    Batch analyze frames sent from frontend queue.
    
    This endpoint receives frames from the frontend AnalysisQueue
    and processes them in batch for efficiency.
    
    For real-time stream mode, frames include base64 images.
    For video file mode, frames are extracted from the video.
    """
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    if not request.frames:
        return {"success": True, "results": [], "message": "No frames to analyze"}
    
    try:
        surgr1_client = await ensure_surgr1_available()
        
        # Prepare frames for batch processing
        batch_frames = []
        
        for frame_data in request.frames:
            if frame_data.image_base64:
                # Decode base64 image
                import base64
                from io import BytesIO
                image_bytes = base64.b64decode(frame_data.image_base64)
                image = Image.open(BytesIO(image_bytes))
                batch_frames.append({
                    "image": image,
                    "frame_idx": frame_data.frame_idx,
                    "timestamp": frame_data.timestamp
                })
            else:
                # Extract frame from video file
                processor = VideoProcessor(
                    video_path=session["video_path"],
                    window_duration=settings.WINDOW_DURATION,
                    sample_interval=settings.SAMPLE_INTERVAL
                )
                frame = processor.extract_frame(frame_data.timestamp)
                if frame:
                    batch_frames.append({
                        "image": frame.image,
                        "frame_idx": frame_data.frame_idx,
                        "timestamp": frame_data.timestamp
                    })
        
        if not batch_frames:
            return {"success": True, "results": [], "message": "No valid frames"}
        
        # Batch analyze all frames
        results = await surgr1_client.analyze_frames_batch(
            frames=batch_frames,
            analysis_type="all",
            session_id=request.session_id,
            save_to_mysql=True
        )
        
        # Save to SQLite database
        for result in results:
            create_frame_analysis(
                db=db,
                session_id=session["session_id"],
                frame_idx=result.get("frame_idx"),
                timestamp=result.get("timestamp"),
                tool_localization=result.get("tools", ""),
                surgical_action=result.get("action", ""),
                surgical_phase=result.get("phase", "")
            )
        
        logger.info(f"Batch analyzed {len(results)} frames for session {request.session_id}")
        
        # 准备返回结果
        response_results = [
            {
                "frame_idx": r.get("frame_idx"),
                "timestamp": r.get("timestamp"),
                "tool_localization": r.get("tools", ""),
                "surgical_action": r.get("action", ""),
                "surgical_phase": r.get("phase", ""),
                "window_id": int(r.get("timestamp", 0) / settings.WINDOW_DURATION)
            }
            for r in results
        ]
        
        # 如果启用GLM验证，提交验证任务
        glm_verification_task_ids = None
        if request.enable_glm_verification and batch_frames:
            try:
                from ..services.glm_multimodal_verifier import get_glm_verifier
                verifier = await get_glm_verifier()
                
                # 准备验证数据（将R1结果与图像配对）
                frames_for_verification = []
                for i, bf in enumerate(batch_frames):
                    r1_result = results[i] if i < len(results) else {}
                    frames_for_verification.append({
                        "image": bf["image"],
                        "frame_idx": bf["frame_idx"],
                        "timestamp": bf["timestamp"],
                        "r1_analysis": {
                            "phase": r1_result.get("phase", ""),
                            "action": r1_result.get("action", ""),
                            "tools": r1_result.get("tools", "")
                        }
                    })
                
                # 提交GLM验证任务
                task_ids = await verifier.submit_batch(
                    session_id=request.session_id,
                    frames_data=frames_for_verification
                )
                
                if request.glm_verification_async:
                    # 异步模式：立即返回，验证在后台进行
                    glm_verification_task_ids = task_ids
                    logger.info(f"GLM verification submitted async: {len(task_ids)} tasks")
                else:
                    # 同步模式：等待验证结果
                    verification_results = await verifier.wait_for_batch(task_ids)
                    
                    # 用验证结果更新返回数据
                    for i, vr in enumerate(verification_results):
                        if i < len(response_results) and isinstance(vr, dict) and not vr.get("error"):
                            response_results[i]["glm_verified"] = True
                            response_results[i]["glm_verification"] = vr
                            
                            # 如果GLM修正了R1的结果，使用修正后的值
                            if not vr.get("r1_correct", True):
                                response_results[i]["surgical_phase"] = vr.get("verified_phase", response_results[i]["surgical_phase"])
                                response_results[i]["surgical_action"] = vr.get("verified_action", response_results[i]["surgical_action"])
                                response_results[i]["tool_localization"] = vr.get("verified_tools", response_results[i]["tool_localization"])
                                response_results[i]["glm_corrected"] = True
                    
                    logger.info(f"GLM verification completed: {len(verification_results)} frames verified")
                    
            except Exception as glm_err:
                logger.warning(f"GLM verification failed (R1 results still valid): {glm_err}")
        
        response = {
            "success": True,
            "results": response_results,
            "batch_size": len(results)
        }
        
        if glm_verification_task_ids:
            response["glm_verification_pending"] = True
            response["glm_verification_task_ids"] = glm_verification_task_ids
        
        return response
        
    except Exception as e:
        logger.error(f"Batch frame analysis failed: {e}")
        raise HTTPException(500, f"Batch analysis failed: {str(e)}")


# ==================== GLM Multimodal Verification ====================

class FrameWithR1Analysis(BaseModel):
    """Frame data with R1 analysis for verification"""
    frame_idx: int
    timestamp: float
    image_base64: str  # Base64 encoded image
    r1_phase: str = ""
    r1_action: str = ""
    r1_tools: str = ""


class GLMVerifyRequest(BaseModel):
    """Request for GLM multimodal verification"""
    session_id: str
    frames: List[FrameWithR1Analysis]
    wait_for_results: bool = True  # If False, returns task IDs immediately


class GLMVerifyBatchConfig(BaseModel):
    """Configuration for GLM verification batch processing"""
    max_batch_size: int = 8
    batch_timeout: float = 0.5
    max_images_per_request: int = 6


@router.post("/glm-verify")
async def glm_verify_frames(
    request: GLMVerifyRequest,
    db: Session = Depends(get_db)
):
    """
    使用GLM验证R1的分析结果
    
    GLM会将图像和R1的分析结果一起按时序分析：
    - 检查R1的阶段/动作/工具识别是否与图像实际内容一致
    - 如果R1分析错误，GLM会根据实际图像进行修正
    - 支持动态批处理以提高效率
    
    Args:
        request: 包含session_id、帧数据（带R1分析）的请求
    
    Returns:
        验证结果列表，每帧包含：
        - r1_correct: R1分析是否正确
        - verified_phase/action/tools: 验证后的结果
        - correction_notes: 修正说明（如有）
    """
    from ..services.glm_multimodal_verifier import get_glm_verifier, verify_frames_with_r1
    
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    if not request.frames:
        return {"success": True, "results": [], "message": "No frames to verify"}
    
    try:
        # 准备帧数据
        frames_data = []
        for frame in request.frames:
            # 解码base64图像
            image_bytes = base64.b64decode(frame.image_base64)
            image = Image.open(BytesIO(image_bytes))
            
            frames_data.append({
                "image": image,
                "frame_idx": frame.frame_idx,
                "timestamp": frame.timestamp,
                "r1_analysis": {
                    "phase": frame.r1_phase,
                    "action": frame.r1_action,
                    "tools": frame.r1_tools
                }
            })
        
        # 执行验证
        if request.wait_for_results:
            results = await verify_frames_with_r1(
                session_id=request.session_id,
                frames=frames_data,
                wait_for_results=True
            )
            
            return {
                "success": True,
                "results": results,
                "frame_count": len(results),
                "message": f"Verified {len(results)} frames with GLM"
            }
        else:
            # 返回任务ID，客户端可稍后查询
            task_ids = await verify_frames_with_r1(
                session_id=request.session_id,
                frames=frames_data,
                wait_for_results=False
            )
            
            return {
                "success": True,
                "task_ids": task_ids,
                "message": f"Submitted {len(task_ids)} frames for verification"
            }
        
    except Exception as e:
        logger.error(f"GLM verification failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(500, f"GLM verification failed: {str(e)}")


@router.get("/glm-verify/stats")
async def get_glm_verify_stats():
    """获取GLM验证器统计信息"""
    from ..services.glm_multimodal_verifier import get_glm_verifier
    
    try:
        verifier = await get_glm_verifier()
        return {
            "success": True,
            "stats": verifier.get_stats()
        }
    except Exception as e:
        logger.error(f"Failed to get GLM verifier stats: {e}")
        raise HTTPException(500, str(e))


@router.post("/glm-verify/configure")
async def configure_glm_verifier(config: GLMVerifyBatchConfig):
    """配置GLM验证器的批处理参数"""
    from ..services.glm_multimodal_verifier import get_glm_verifier, BatchConfig
    
    try:
        verifier = await get_glm_verifier()
        
        # 更新配置
        verifier.batch_config.max_batch_size = config.max_batch_size
        verifier.batch_config.batch_timeout = config.batch_timeout
        verifier.batch_config.max_images_per_request = config.max_images_per_request
        
        logger.info(f"GLM verifier config updated: batch_size={config.max_batch_size}, timeout={config.batch_timeout}")
        
        return {
            "success": True,
            "config": {
                "max_batch_size": config.max_batch_size,
                "batch_timeout": config.batch_timeout,
                "max_images_per_request": config.max_images_per_request
            }
        }
    except Exception as e:
        logger.error(f"Failed to configure GLM verifier: {e}")
        raise HTTPException(500, str(e))


class GLMVerifyResultsRequest(BaseModel):
    """Request for querying verification results by task IDs"""
    task_ids: List[str]
    timeout: float = 30.0


@router.post("/glm-verify/results")
async def get_glm_verify_results(request: GLMVerifyResultsRequest):
    """
    查询GLM验证结果
    
    用于异步验证模式：先提交任务，稍后查询结果
    """
    from ..services.glm_multimodal_verifier import get_glm_verifier, VerificationStatus
    
    try:
        verifier = await get_glm_verifier()
        
        results = []
        pending_count = 0
        
        for task_id in request.task_ids:
            task = verifier.get_task_status(task_id)
            
            if task is None:
                results.append({
                    "task_id": task_id,
                    "status": "not_found",
                    "error": "Task not found"
                })
            elif task.status == VerificationStatus.COMPLETED:
                results.append({
                    "task_id": task_id,
                    "status": "completed",
                    "frame_idx": task.frame_idx,
                    "timestamp": task.timestamp,
                    "result": task.result
                })
            elif task.status == VerificationStatus.FAILED:
                results.append({
                    "task_id": task_id,
                    "status": "failed",
                    "frame_idx": task.frame_idx,
                    "timestamp": task.timestamp,
                    "error": task.error
                })
            else:
                # Still processing
                pending_count += 1
                results.append({
                    "task_id": task_id,
                    "status": task.status.value,
                    "frame_idx": task.frame_idx,
                    "timestamp": task.timestamp
                })
        
        return {
            "success": True,
            "results": results,
            "total": len(request.task_ids),
            "completed": len([r for r in results if r.get("status") == "completed"]),
            "pending": pending_count,
            "failed": len([r for r in results if r.get("status") == "failed"])
        }
        
    except Exception as e:
        logger.error(f"Failed to get GLM verify results: {e}")
        raise HTTPException(500, str(e))


@router.post("/glm-verify/wait")
async def wait_glm_verify_results(request: GLMVerifyResultsRequest):
    """
    等待并获取GLM验证结果
    
    会阻塞直到所有任务完成或超时
    """
    from ..services.glm_multimodal_verifier import get_glm_verifier
    
    try:
        verifier = await get_glm_verifier()
        
        results = await verifier.wait_for_batch(
            task_ids=request.task_ids,
            timeout=request.timeout
        )
        
        return {
            "success": True,
            "results": results,
            "total": len(results)
        }
        
    except Exception as e:
        logger.error(f"Failed to wait for GLM verify results: {e}")
        raise HTTPException(500, str(e))


@router.post("/analyze-window")
async def analyze_window(
    request: AnalyzeWindowRequest,
    db: Session = Depends(get_db)
):
    """Analyze a 5-second window and generate summary using GPT"""
    
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Create video processor
    processor = VideoProcessor(
        video_path=session["video_path"],
        window_duration=settings.WINDOW_DURATION,
        sample_interval=settings.SAMPLE_INTERVAL
    )
    
    # Extract window
    window = processor.extract_window(request.start_time)
    
    if window.frame_count == 0:
        raise HTTPException(400, "No frames extracted for this window")
    
    # Get GPT summarizer
    summarizer = get_gpt_summarizer()
    if request.use_chinese:
        summarizer.use_chinese = True
    
    # Build context and generate summary
    context = build_frame_context(window)
    
    result = await summarizer.summarize_window(
        images=window.get_images(),
        context=context,
        system_prompt=ANALYSIS_SYSTEM_PROMPT
    )
    
    if result["success"]:
        # Save to database
        summary = create_window_summary(
            db=db,
            session_id=session["session_id"],
            window_id=window.window_id,
            start_time=window.start_time,
            end_time=window.end_time,
            summary_text=result["summary"],
            summary_chinese=result["summary"] if request.use_chinese else None
        )

        # Generate embedding for semantic search
        _queue_embedding(session["session_id"], window.window_id, result["summary"],
                         window.start_time, window.end_time)

        return {
            "window_id": window.window_id,
            "start_time": window.start_time,
            "end_time": window.end_time,
            "frame_count": window.frame_count,
            "summary": result["summary"],
            "summary_id": summary.id
        }
    else:
        raise HTTPException(500, f"Summarization failed: {result.get('error', 'Unknown error')}")


@router.post("/analyze-window-vlm")
async def analyze_window_with_vlm(
    request: VLMAnalyzeRequest,
    db: Session = Depends(get_db)
):
    """
    Analyze a 5-second window using local VLM model (vLLM)
    
    Uses the local Qwen2.5-VL model for frame-by-frame analysis,
    then generates a summary.
    """
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Create video processor
    processor = VideoProcessor(
        video_path=session["video_path"],
        window_duration=settings.WINDOW_DURATION,
        sample_interval=settings.SAMPLE_INTERVAL
    )
    
    # Extract window
    window = processor.extract_window(request.start_time)
    
    if window.frame_count == 0:
        raise HTTPException(400, "No frames extracted for this window")
    
    # Get VLM model service
    vlm_service = await ensure_model_loaded()
    
    # Analyze each frame
    frame_analyses = []
    for frame in window.frames:
        analysis = await vlm_service.analyze_frame(frame.image, analysis_type="all")
        frame_analyses.append({
            "frame_idx": frame.frame_idx,
            "timestamp": frame.timestamp,
            **analysis
        })
        
        # Save frame analysis to database
        create_frame_analysis(
            db=db,
            session_id=session["session_id"],
            frame_idx=frame.frame_idx,
            timestamp=frame.timestamp,
            tool_localization=analysis.get("tools", ""),
            surgical_action=analysis.get("action", ""),
            surgical_phase=analysis.get("phase", "")
        )
    
    # Generate summary using GLM-4.6V-Flash (fallback to GPT if GLM unavailable)
    summary_text = "Analysis completed."
    model_used = "VLM"
    
    try:
        # Try VLM multimodal analysis (Gemini or GLM based on config)
        vlm_client = get_vlm_client()
        is_healthy = await vlm_client.check_health()
        
        if is_healthy:
            # 提取窗口帧图片用于VLM多模态验证
            window_images = [frame.image for frame in window.frames if frame.image is not None]
            
            # Integrate using VLM (多模态分析)
            result = await vlm_client.integrate_analysis_results(
                frame_analyses=frame_analyses,
                images=window_images  # 传入图片用于多模态验证
            )
            
            if result["success"]:
                summary_text = result["summary"]
                provider = get_summarization_provider()
                model_used = f"VLM + {provider.upper()}"
            else:
                raise Exception(f"VLM整合失败: {result.get('error')}")
        else:
            raise Exception("VLM服务不可用")
            
    except Exception as e:
        # Fallback to GPT
        logger.warning(f"GLM integration failed, falling back to GPT: {e}")
        context = build_frame_context(window, frame_analyses)
        summarizer = get_gpt_summarizer()
        result = await summarizer.summarize_window(
            images=window.get_images(),
            context=context,
            system_prompt=ANALYSIS_SYSTEM_PROMPT
        )
        
        if result["success"]:
            summary_text = result["summary"]
            model_used = "VLM + GPT"
        else:
            summary_text = result.get("summary", "Analysis completed.")
            model_used = "VLM"
    
    # Save summary
    summary = create_window_summary(
        db=db,
        session_id=session["session_id"],
        window_id=window.window_id,
        start_time=window.start_time,
        end_time=window.end_time,
        summary_text=summary_text,
        tools_detected=[f.get("tools", "") for f in frame_analyses],
        key_actions=[f.get("action", "") for f in frame_analyses]
    )

    # Generate embedding for semantic search
    _queue_embedding(session["session_id"], window.window_id, summary_text,
                     window.start_time, window.end_time)

    return {
        "window_id": window.window_id,
        "start_time": window.start_time,
        "end_time": window.end_time,
        "frame_count": window.frame_count,
        "frame_analyses": frame_analyses,
        "summary": summary_text,
        "summary_id": summary.id,
        "model": model_used
    }


@router.post("/process-video")
async def process_full_video(
    session_id: str,
    background_tasks: BackgroundTasks,
    use_chinese: bool = False,
    db: Session = Depends(get_db)
):
    """Start processing entire video in background"""
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Add background task
    background_tasks.add_task(
        process_video_task,
        session_id=session_id,
        video_path=session["video_path"],
        db_session_id=session["session_id"],
        use_chinese=use_chinese
    )
    
    return {
        "message": "Processing started",
        "session_id": session_id,
        "estimated_windows": int(session["duration"] / settings.WINDOW_DURATION) + 1
    }


@router.post("/process-video-surgr1-glm")
async def process_video_with_surgr1_glm(
    request: ProcessVideoSurgR1GLMRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Start processing video with SurgR1 (frame analysis) + GLM (summary)
    
    Processing flow:
    1. For each 5-second window, extract frames
    2. Use SurgR1 to analyze each frame (tool_localization, surgical_action, surgical_phase)
    3. After SurgR1 completes for all frames in window, use GLM to summarize
    4. Stream results via SSE
    """
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Clear any previous cancellation flag
    analysis_cancellation_flags[request.session_id] = False
    
    # Add background task
    background_tasks.add_task(
        process_video_surgr1_glm_task,
        session_id=request.session_id,
        video_path=session["video_path"],
        db_session_id=session["session_id"],
        use_chinese=request.use_chinese,
        use_glm_multimodal=request.use_glm_multimodal
    )
    
    return {
        "message": "SurgR1+GLM processing started",
        "session_id": request.session_id,
        "estimated_windows": int(session["duration"] / settings.WINDOW_DURATION) + 1,
        "processing_mode": "surgr1_glm"
    }


@router.post("/stop-analysis/{session_id}")
async def stop_analysis(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Stop an ongoing video analysis task
    
    Sets a cancellation flag that the background task will check.
    The task will stop after completing the current window.
    """
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Set cancellation flag
    analysis_cancellation_flags[session_id] = True
    
    # Update session status
    from ..database import update_session_status
    update_session_status(db, session_id, "cancelled")
    
    logger.info(f"Analysis cancellation requested for session {session_id}")
    
    return {
        "message": "Analysis stop requested",
        "session_id": session_id,
        "status": "cancelling"
    }


# ==============================================================================
# Continuous SurgR1 Processing (runs in background when stream starts)
# ==============================================================================

@router.post("/start-surgr1-continuous/{session_id}")
async def start_surgr1_continuous(
    session_id: str,
    background_tasks: BackgroundTasks,
    enable_sam3: bool = True,
    db: Session = Depends(get_db)
):
    """
    Start continuous SurgR1 frame analysis for a video session.
    
    This runs in the background and continuously analyzes frames with SurgR1.
    Results are stored in the database and can be used by GLM summarization later.
    
    If enable_sam3 is True, also creates a SAM3 streaming session for
    real-time segmentation with mask propagation.
    
    Called automatically when entering stream mode.
    """
    try:
        session = get_video_session(db, session_id)
        if not session:
            return {
                "success": False,
                "message": "Session not found",
                "session_id": session_id,
                "status": "error"
            }
        
        # Check if already running
        if surgr1_continuous_flags.get(session_id, False):
            return {
                "success": True,
                "message": "SurgR1 continuous processing already running",
                "session_id": session_id,
                "status": "running",
                "sam3_enabled": session_id in sam3_streaming_sessions
            }
        
        # Initialize SAM3 streaming session if enabled
        sam3_session_id = None
        consistency_checker = None
        
        if enable_sam3:
            try:
                sam3_client = await ensure_sam3_available()
                is_healthy = await sam3_client.check_health()
                
                if is_healthy:
                    result = await sam3_client.create_stream_session(session_id)
                    if result.get("success"):
                        sam3_session_id = result.get("session_id")
                        consistency_checker = SAM3ConsistencyChecker(ConsistencyConfig(
                            forced_refresh_interval=10.0,
                            max_propagate_frames=30,
                            centroid_offset_threshold=0.3,
                            area_change_threshold=0.5
                        ))
                        sam3_streaming_sessions[session_id] = {
                            "sam3_session_id": sam3_session_id,
                            "frame_count": 0,
                            "last_update": 0,
                            "consistency_checker": consistency_checker,
                            "state": "idle"
                        }
                        logger.info(f"SAM3 streaming session created: {sam3_session_id}")
            except Exception as e:
                logger.warning(f"Failed to create SAM3 streaming session: {e}")
        
        # Mark as running
        surgr1_continuous_flags[session_id] = True
        
        import time as time_module
        stream_start_time = time_module.time()
        
        # Determine if this is a real-time stream (HTTP/RTSP) or capture device
        video_path = session["video_path"]
        is_realtime_stream = video_path.startswith(("http://", "https://", "rtsp://", "device://", "decklink://", "simulator://"))
        
        # Create background task using asyncio.create_task so it can be cancelled
        # (FastAPI background_tasks cannot be cancelled)
        task = asyncio.create_task(
            surgr1_continuous_task(
                session_id=session_id,
                video_path=video_path,
                db_session_id=session["session_id"],
                sam3_session_id=sam3_session_id
            )
        )
        
        # 【解耦】启动独立的帧捕获服务（25fps固定存储，与分析完全解耦）
        # 帧捕获服务独立于分析流程运行，确保帧存储完整性
        mysql_service = get_mysql_service()
        video_session = mysql_service.get_video_session(session_id)
        storage_path = video_session.get("storage_path") if video_session else None
        
        if not storage_path:
            frame_storage = get_frame_storage_service()
            video_name = video_session.get("video_name", "stream") if video_session else "stream"
            storage_path = frame_storage.create_session_folder(session_id, video_name)
            mysql_service.update_video_session(session_id, storage_path=storage_path)
            logger.info(f"[FrameCapture] Created storage folder: {storage_path}")
        
        frame_capture_service = get_frame_capture_service()
        await frame_capture_service.start_capture(
            session_id=session_id,
            video_source=video_path,
            storage_path=storage_path,
            is_realtime_stream=is_realtime_stream,
            stream_start_time=stream_start_time
        )
        
        # Store task references for cancellation (only the analysis task now)
        active_surgr1_tasks[session_id] = [task]
        
        logger.info(f"Started SurgR1 continuous processing for session {session_id}")
        logger.info(f"Started independent frame capture service at 25 FPS for session {session_id}")
        
        return {
            "success": True,
            "message": "SurgR1 continuous processing started",
            "session_id": session_id,
            "status": "started",
            "sam3_enabled": sam3_session_id is not None,
            "sam3_session_id": sam3_session_id,
            # Server timestamp for time synchronization with frontend
            "server_time": time_module.time()
        }
        
    except Exception as e:
        logger.error(f"Error starting SurgR1 continuous: {e}")
        return {
            "success": False,
            "message": f"Failed to start: {e}",
            "session_id": session_id,
            "status": "error"
        }


@router.post("/stop-surgr1-continuous/{session_id}")
async def stop_surgr1_continuous(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Stop continuous SurgR1 frame analysis.
    
    Called when leaving stream mode or stopping the session.
    Also cleans up any active SAM3 streaming session.
    
    Note: This endpoint is lenient - returns success even if session
    doesn't exist (for page close cleanup via sendBeacon).
    """
    # Mark as stopped - do this first even if session doesn't exist
    was_running = surgr1_continuous_flags.get(session_id, False)
    surgr1_continuous_flags[session_id] = False
    
    # Stop frame capture flag (legacy)
    frame_capture_flags[session_id] = False
    
    # 【解耦】停止独立的帧捕获服务
    frame_capture_service = get_frame_capture_service()
    await frame_capture_service.stop_capture(session_id)
    
    # ========== Cancel active asyncio tasks ==========
    # This is crucial - just setting flags doesn't stop running tasks
    tasks_cancelled = 0
    if session_id in active_surgr1_tasks:
        tasks = active_surgr1_tasks.pop(session_id, [])
        for task in tasks:
            if task and not task.done():
                task.cancel()
                tasks_cancelled += 1
                try:
                    # Give task a moment to handle CancelledError
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                except Exception as e:
                    logger.warning(f"Error cancelling task: {e}")
        logger.info(f"Cancelled {tasks_cancelled} active tasks for session {session_id}")
    
    # Clean up stream start time
    if session_id in stream_start_times:
        del stream_start_times[session_id]
    
    # Also try to close SAM3 session if still active
    sam3_info = sam3_streaming_sessions.get(session_id)
    if sam3_info:
        try:
            sam3_client = get_sam3_client()
            sam3_session_id = sam3_info.get("sam3_session_id")
            if sam3_session_id:
                await sam3_client.close_stream_session(sam3_session_id)
        except Exception as e:
            logger.warning(f"Error closing SAM3 session during stop: {e}")
        finally:
            sam3_streaming_sessions.pop(session_id, None)
            sam3_latest_frames.pop(session_id, None)
    
    # Also cancel pending requests when the legacy API is explicitly enabled.
    if _legacy_surgr1_enabled():
        try:
            surgr1_client = get_surgr1_client()
            await surgr1_client.cancel_session(session_id)
        except Exception as e:
            logger.debug(f"SurgR1 client cancel (optional): {e}")
    
    logger.info(f"Stopped SurgR1 continuous processing for session {session_id} (was_running={was_running}, tasks_cancelled={tasks_cancelled})")
    
    return {
        "message": "SurgR1 continuous processing stopped",
        "session_id": session_id,
        "status": "stopped",
        "tasks_cancelled": tasks_cancelled
    }


@router.get("/surgr1-continuous-status/{session_id}")
async def get_surgr1_continuous_status(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get the status of continuous SurgR1 processing"""
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    is_running = surgr1_continuous_flags.get(session_id, False)
    
    # Get count of analyzed frames
    frames = get_frames_by_session(db, session["session_id"])
    
    # Get SAM3 streaming status
    sam3_info = sam3_streaming_sessions.get(session_id, {})
    
    return {
        "session_id": session_id,
        "is_running": is_running,
        "frames_analyzed": len(frames) if frames else 0,
        "sam3_enabled": session_id in sam3_streaming_sessions,
        "sam3_frames_processed": sam3_info.get("frame_count", 0)
    }


@router.get("/sam3/stream-frame/{session_id}")
async def get_sam3_stream_frame(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Get the latest SAM3 streamed frame for a session.
    
    This returns the most recently processed frame with SAM3 segmentation
    from the streaming pipeline. Much faster than re-processing each frame.
    
    The streaming task continuously updates sam3_latest_frames with
    segmented frames, so this endpoint just returns the cached result.
    
    Response includes:
    - image_base64: The segmented frame
    - propagated: True if mask was propagated (vs. newly generated)
    - state: "idle", "tracking", or "reinit"
    - reinit_reason: Why reinit was triggered (if applicable)
    """
    try:
        session = get_video_session(db, session_id)
        if not session:
            return {
                "success": False,
                "message": "Session not found",
                "streaming_active": False
            }
        
        latest = sam3_latest_frames.get(session_id)
        streaming_info = sam3_streaming_sessions.get(session_id, {})
        
        if not latest:
            # No SAM3 frame available yet
            return {
                "success": False,
                "message": "No SAM3 streamed frame available yet",
                "streaming_active": session_id in sam3_streaming_sessions,
                "state": streaming_info.get("state", "idle")
            }
        
        return {
            "success": True,
            "timestamp": latest.get("timestamp", 0),
            "frame_idx": latest.get("frame_idx", 0),
            "image_base64": latest.get("image_base64"),
            "num_objects": latest.get("num_objects", 0),
            "propagated": latest.get("propagated", False),
            "state": latest.get("state", "unknown"),
            "reinit_reason": latest.get("reinit_reason"),
            "age_seconds": time.time() - latest.get("updated_at", time.time())
        }
    except Exception as e:
        logger.error(f"Error in get_sam3_stream_frame: {e}")
        return {
            "success": False,
            "message": f"Server error: {str(e)}",
            "streaming_active": False
        }


@router.get("/sam3/stream-status/{session_id}")
async def get_sam3_stream_status(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get the status of SAM3 streaming for a session"""
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    sam3_info = sam3_streaming_sessions.get(session_id, {})
    latest = sam3_latest_frames.get(session_id)
    
    # Get consistency checker status if available
    consistency_status = {}
    checker = sam3_info.get("consistency_checker")
    if checker:
        consistency_status = checker.get_status()
    
    return {
        "session_id": session_id,
        "streaming_active": session_id in sam3_streaming_sessions,
        "sam3_session_id": sam3_info.get("sam3_session_id"),
        "frames_processed": sam3_info.get("frame_count", 0),
        "last_update": sam3_info.get("last_update", 0),
        "state": sam3_info.get("state", "unknown"),
        "latest_frame_timestamp": latest.get("timestamp") if latest else None,
        "latest_frame_objects": latest.get("num_objects") if latest else None,
        "consistency": consistency_status
    }


@router.post("/sam3/force-reinit/{session_id}")
async def force_sam3_reinit(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Force SAM3 streaming session to reinitialize.
    
    This manually triggers a reinit on the next key frame.
    Useful when the user notices tracking issues.
    """
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    sam3_info = sam3_streaming_sessions.get(session_id)
    if not sam3_info:
        raise HTTPException(400, "SAM3 streaming not active for this session")
    
    # Reset the consistency checker to force reinit on next check
    checker = sam3_info.get("consistency_checker")
    if checker:
        checker.reset()
        checker.state = SAM3State.REINIT
        logger.info(f"Forced SAM3 reinit for session {session_id}")
        return {
            "success": True,
            "message": "SAM3 will reinitialize on next key frame"
        }
    else:
        return {
            "success": False,
            "message": "No consistency checker available"
        }


@router.get("/sam3/consistency/{session_id}")
async def get_sam3_consistency_status(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Get detailed consistency checker status for debugging.
    
    Returns information about tracked instruments, reinit history, etc.
    """
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    sam3_info = sam3_streaming_sessions.get(session_id, {})
    checker = sam3_info.get("consistency_checker")
    
    if not checker:
        return {
            "available": False,
            "message": "No consistency checker for this session"
        }
    
    status = checker.get_status()
    
    # Add tracked instrument details
    tracked_details = []
    for obj_id, instrument in checker.tracked_instruments.items():
        tracked_details.append({
            "obj_id": obj_id,
            "label": instrument.label,
            "frames_tracked": instrument.frames_tracked,
            "last_area": instrument.last_area,
            "last_centroid": instrument.last_centroid,
            "last_bbox": instrument.last_bbox
        })
    
    return {
        "available": True,
        **status,
        "tracked_instruments": tracked_details,
        "config": {
            "centroid_offset_threshold": checker.config.centroid_offset_threshold,
            "area_change_threshold": checker.config.area_change_threshold,
            "forced_refresh_interval": checker.config.forced_refresh_interval,
            "max_propagate_frames": checker.config.max_propagate_frames
        }
    }


async def surgr1_continuous_task(
    session_id: str,
    video_path: str,
    db_session_id: int,
    sam3_session_id: Optional[str] = None
):
    """
    Background task for continuous SurgR1 frame analysis with SAM3 streaming.
    
    Continuously reads frames from video/stream and:
    1. Analyzes key frames with SurgR1 (every 1 second)
    2. Uses SAM3 to generate segmentation masks
    3. Propagates masks to intermediate frames with SAM3
    4. Uses consistency checker to detect when reinit is needed
    
    This implements the real-time streaming approach from:
    https://github.com/matteo-tafuro/sam3-realtime
    """
    import cv2
    import time as time_module
    from ..services.yolo_service import get_yolo_service

    db = next(get_db())
    sam3_client = None
    consistency_checker = None
    cap = None  # Initialize cap outside try block for proper cleanup
    frame_capture_task = None  # Initialize frame capture task for proper cleanup
    pending_r1_tasks = []
    loop = asyncio.get_running_loop()

    # --- Blocking helpers, run in executor to avoid blocking the event loop ---
    # [perf] 关键优化：cap.read / cvtColor / PIL 构造 / yolo.detect 都是同步 CPU/IO
    # 操作，过去直接在协程里同步调用会堵住整个 FastAPI 事件循环（含 MJPEG 代理、
    # SSE、其它 HTTP），导致前端视频卡顿。现在统一丢到默认 ThreadPoolExecutor。
    def _blocking_read(c):
        return c.read()

    def _bgr_to_pil(bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)

    try:
        if not _legacy_surgr1_enabled():
            # FrameCaptureService owns the one paced decoder in local-only mode.
            # Keep the legacy running flag synchronized without opening a second
            # decoder or issuing failed localhost:9003 requests.
            capture_service = get_frame_capture_service()
            capture_seen = False
            capture_started = False
            wait_deadline = time_module.time() + 5.0
            while surgr1_continuous_flags.get(session_id, False):
                state = capture_service.get_capture_state(session_id)
                if state is not None:
                    capture_seen = True
                    if state.is_running:
                        capture_started = True
                    elif capture_started:
                        break
                if not capture_started and time_module.time() >= wait_deadline:
                    logger.warning("[LocalExperts] Frame capture did not start for session %s", session_id)
                    break
                await asyncio.sleep(0.1)
            logger.info(
                "[LocalExperts] Capture monitor stopped for %s (capture_seen=%s)",
                session_id,
                capture_seen,
            )
            return

        surgr1_client = await ensure_surgr1_available()
        
        # Clear any previous cancellation flag for this session
        surgr1_client.clear_cancellation(session_id)
        
        # Get SAM3 client and consistency checker if session exists
        if sam3_session_id and session_id in sam3_streaming_sessions:
            try:
                sam3_client = await ensure_sam3_available()
                consistency_checker = sam3_streaming_sessions[session_id].get("consistency_checker")
            except Exception as e:
                logger.warning(f"SAM3 client not available: {e}")
                sam3_client = None
        
        # Open video/stream (supports device://, rtsp://, http://, files)
        # [perf] open_video_source 在 HTTP/RTSP 上可能阻塞数秒，放 executor
        cap = await loop.run_in_executor(None, open_video_source, video_path)
        if not cap or not cap.isOpened():
            logger.error(f"Cannot open video: {video_path}")
            surgr1_continuous_flags[session_id] = False
            return
        
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        is_realtime_stream = video_path.startswith(("http://", "https://", "rtsp://", "device://", "decklink://", "simulator://"))
        resolved_video_source = resolve_video_source(video_path)
        is_finite_simulator = bool(resolved_video_source.is_simulator and resolved_video_source.source != video_path)
        
        # For realtime streams, use wall clock time instead of frame-based time
        # This ensures timestamps match the frontend's elapsed time
        import time as time_module
        stream_start_time = time_module.time() if is_realtime_stream else None
        
        # Store the start time for synchronization with frontend
        if is_realtime_stream:
            stream_start_times[session_id] = stream_start_time
            logger.info(f"Stream start time recorded for {session_id}: {stream_start_time}")
        
        surgr1_interval = settings.SAMPLE_INTERVAL  # SurgR1 采样间隔（从config.json读取，默认3秒）
        sam3_interval = 0.1  # SAM3 propagates masks at 10 FPS (propagation is very fast)
        # 【解耦】帧保存已移至独立的 frame_capture_service，此处不再保存帧
        last_surgr1_time = -surgr1_interval  # Ensure first frame is analyzed
        last_sam3_time = 0
        frame_idx = 0
        
        # ========== 批量处理配置（动态 batch size）==========
        # 根据积累的未处理帧数量动态调整 batch size
        # vLLM 并行处理时，大 batch 吞吐量更高（但延迟也增加）
        # [优化] 基于 benchmark 测试结果调整：
        #   batch_5: 4.19s (0.84s/frame) — 最佳效率
        #   减少 batch 等待时间以降低端到端延迟
        SURGR1_MIN_BATCH_SIZE = 2    # 最小批量大小（从3降到2，减少等待）
        SURGR1_MAX_BATCH_SIZE = 15   # 最大批量大小
        SURGR1_TARGET_BATCH_SIZE = 5  # 目标批量大小（从8降到5，匹配15s/3s=5帧/窗口）
        surgr1_batch_buffer = []  # 帧缓冲区: [(pil_image, frame_idx, timestamp), ...]
        last_batch_time = None  # 上次批量处理的时间（None表示尚未开始）
        batch_timeout = 3.0  # 超时时间（秒），从6s降到3s以减少延迟
        
        # ========== 【优化】并行异步 R1 处理任务 ==========
        # 支持多个并行 R1 任务，充分利用 GPU 和 vLLM 的并发能力
        MAX_PARALLEL_R1_TASKS = 1  # 实时预览优先：避免后台 R1 批任务抢占 CPU/解码
        pending_r1_tasks = []  # 正在执行的 R1 批处理任务列表
        r1_processing_buffer = []  # 正在被 R1 处理的帧（用于追踪）
        
        def get_dynamic_batch_size(buffer_size: int, video_elapsed_time: float) -> int:
            """根据积压帧数动态计算 batch size
            
            策略：优先使用大 batch 提高吞吐量
            - 积压少（<5帧）：等待积累更多（除非超时）
            - 积压中（5-10帧）：使用目标大小10
            - 积压多（>10帧）：使用最大值15
            """
            if buffer_size < SURGR1_MIN_BATCH_SIZE:
                return SURGR1_MIN_BATCH_SIZE  # 等待更多帧
            elif buffer_size < SURGR1_TARGET_BATCH_SIZE:
                return SURGR1_TARGET_BATCH_SIZE  # 等待达到目标
            elif buffer_size <= SURGR1_MAX_BATCH_SIZE:
                return buffer_size  # 处理全部积压
            else:
                return SURGR1_MAX_BATCH_SIZE
        
        # 【解耦】帧捕获已移至独立的 frame_capture_service
        # 分析服务只负责读取帧并分析，不再保存帧
        # 帧存储在 start_surgr1_continuous 中通过 frame_capture_service 启动
        
        # Get storage path for reading frames (created by frame_capture_service)
        mysql_service = get_mysql_service()
        video_session = mysql_service.get_video_session(session_id)
        storage_path = video_session.get("storage_path") if video_session else None
        
        # Store last known bboxes for SAM3 propagation
        last_bboxes = []
        last_tool_localization = ""
        
        # Track SAM3 initialization state - only reinit when instruments change
        sam3_initialized = False
        sam3_tracked_instruments = set()  # Set of instrument labels being tracked
        
        logger.info(f"SurgR1 continuous task started for {session_id} (SAM3: {sam3_session_id is not None}, realtime_stream: {is_realtime_stream})")
        
        # Track consecutive read failures for stream end detection
        consecutive_read_failures = 0
        MAX_READ_FAILURES = 50  # 50 * 0.1s = 5 seconds of no frames
        last_stream_check_time = 0
        STREAM_CHECK_INTERVAL = 2.0  # Check stream status every 2 seconds during failures
        
        while surgr1_continuous_flags.get(session_id, False):
            # [perf] cap.read() 对 HTTP MJPEG / RTSP 会阻塞到下一帧到达，
            # 过去直接同步调用会堵住 asyncio loop → MJPEG 代理/SSE 全部卡住
            ret, bgr_frame = await loop.run_in_executor(None, _blocking_read, cap)
            
            if not ret:
                if is_finite_simulator:
                    logger.info(f"[SurgR1] Simulator source ended for session {session_id}, stopping continuous processing")
                    break
                # For streams, wait and retry; for files, loop
                if is_realtime_stream:
                    consecutive_read_failures += 1
                    
                    # Check if stream has ended after consecutive failures
                    if consecutive_read_failures >= MAX_READ_FAILURES:
                        current_check_time = time_module.time()
                        
                        # Only check stream status periodically to avoid flooding
                        if current_check_time - last_stream_check_time >= STREAM_CHECK_INTERVAL:
                            last_stream_check_time = current_check_time
                            
                            # Check if the stream server indicates video has ended
                            stream_ended = await check_stream_ended(video_path)
                            if stream_ended:
                                logger.info(f"[SurgR1] Stream ended for session {session_id}, stopping continuous processing")
                                break
                            else:
                                logger.debug(f"[SurgR1] {consecutive_read_failures} consecutive read failures, stream not ended yet")
                    
                    await asyncio.sleep(0.1)
                    continue
                else:
                    # End of file - restart from beginning for continuous processing
                    await loop.run_in_executor(None, cap.set, cv2.CAP_PROP_POS_FRAMES, 0)
                    frame_idx = 0
                    last_surgr1_time = -surgr1_interval
                    if consistency_checker:
                        consistency_checker.reset()
                    continue
            
            # Reset failure counter on successful read
            consecutive_read_failures = 0
            
            # For realtime streams, use actual elapsed time (wall clock)
            # For local videos, use frame-based time calculation
            if is_finite_simulator and hasattr(cap, "last_timestamp"):
                current_time = float(cap.last_timestamp())
            elif is_realtime_stream:
                current_time = time_module.time() - stream_start_time
            else:
                current_time = frame_idx / fps
            
            # 【解耦】帧保存已移至独立的 frame_capture_service（25fps固定存储）
            # 分析服务只负责处理帧，不再保存帧
            
            # Determine if this is a SurgR1 key frame (采样间隔1秒)
            is_surgr1_frame = (current_time - last_surgr1_time >= surgr1_interval)
            sam3_due = bool(
                sam3_client
                and sam3_session_id
                and (current_time - last_sam3_time >= sam3_interval)
            )
            pil_image = None
            if is_surgr1_frame or sam3_due:
                # colorspace + PIL 构造是 CPU 密集，只有真正需要分析/传播时才做
                pil_image = await loop.run_in_executor(None, _bgr_to_pil, bgr_frame)
            
            if is_surgr1_frame:
                last_surgr1_time = current_time
                
                # 将帧加入批量缓冲区
                surgr1_batch_buffer.append({
                    "image": pil_image.copy(),  # 复制图像避免被覆盖
                    "frame_idx": frame_idx,
                    "timestamp": current_time
                })
                logger.debug(f"[SurgR1 Batch] Added frame {frame_idx} to buffer, size={len(surgr1_batch_buffer)}")
            
            # ========== 批量处理触发条件（动态 batch size）==========
            # 1. 缓冲区达到动态计算的 batch_size
            # 2. 超时（距首帧入队超过 batch_timeout 秒且缓冲区非空）
            # 首帧入队时记录时间
            if len(surgr1_batch_buffer) == 1 and last_batch_time is None:
                last_batch_time = current_time  # 记录首帧入队时间
            
            # ========== 【优化】非阻塞检查并行 R1 任务是否完成 ==========
            # 检查所有已完成的任务并处理结果
            completed_tasks = []
            for task in pending_r1_tasks:
                if task.done():
                    completed_tasks.append(task)
                    try:
                        batch_results, batch_to_process_done = task.result()
                        
                        # 处理批量结果
                        for i, result in enumerate(batch_results):
                            if i >= len(batch_to_process_done):
                                break
                            
                            batch_frame = batch_to_process_done[i]
                            f_idx = batch_frame["frame_idx"]
                            f_ts = batch_frame["timestamp"]
                            
                            image_saved = 1 if storage_path else 0
                            image_path = None
                            
                            # Save analysis to MySQL database
                            mysql_service.save_analysis(
                                session_id=session_id,
                                frame_idx=f_idx,
                                timestamp=f_ts,
                                analysis_type="frame",
                                tool_localization=result.get("tools", ""),
                                surgical_action=result.get("action", ""),
                                surgical_phase=result.get("phase", ""),
                                image_path=image_path,
                                image_saved=image_saved
                            )
                            
                            # Also save to in-memory database (backward compat)
                            create_frame_analysis(
                                db=db,
                                session_id=db_session_id,
                                frame_idx=f_idx,
                                timestamp=f_ts,
                                tool_localization=result.get("tools", ""),
                                surgical_action=result.get("action", ""),
                                surgical_phase=result.get("phase", "")
                            )
                            
                            logger.info(f"[SurgR1] Frame {f_idx} at {f_ts:.1f}s analyzed")
                        
                        # 使用最后一个结果更新 SAM3 的 bbox
                        if batch_results:
                            last_result = batch_results[-1]
                            # Use YOLO for tool detection instead of SurgR1
                            yolo_svc = get_yolo_service()
                            last_batch_frame = batch_to_process_done[-1] if batch_to_process_done else None
                            if yolo_svc and last_batch_frame and "image" in last_batch_frame:
                                # [perf] yolo_svc.detect 是同步 GPU 推理（~几十 ms），
                                # 放到 executor 避免阻塞 asyncio loop
                                yolo_detections = await loop.run_in_executor(
                                    None, yolo_svc.detect, last_batch_frame["image"]
                                )
                                last_bboxes = yolo_svc.detections_to_sam3_format(yolo_detections)
                                last_tool_localization = json.dumps(yolo_detections)
                                logger.info(f"[YOLO] Detected {len(yolo_detections)} tools: {[d['label'] for d in yolo_detections]}")
                            else:
                                # Fallback to SurgR1 tool localization if YOLO unavailable
                                last_tool_localization = last_result.get("tools", "")
                                last_bboxes = parse_bboxes_from_surgr1(last_tool_localization)
                            logger.info(f"[SurgR1 Batch] Completed {len(batch_results)} frames, last has {len(last_bboxes)} bboxes")
                    
                    except Exception as e:
                        logger.warning(f"SurgR1 batch analysis failed: {e}")
            
            # 移除已完成的任务
            for task in completed_tasks:
                pending_r1_tasks.remove(task)
            # 未完成的任务继续运行，不阻塞帧采集
            
            # 动态计算当前应该使用的 batch size
            current_batch_size = get_dynamic_batch_size(len(surgr1_batch_buffer), current_time)
            
            # 检查是否需要启动新的处理（允许最多 MAX_PARALLEL_R1_TASKS 个并行任务）
            batch_full = len(surgr1_batch_buffer) >= current_batch_size
            batch_timeout_reached = (
                len(surgr1_batch_buffer) > 0 and 
                last_batch_time is not None and 
                current_time - last_batch_time >= batch_timeout
            )
            can_start_new_task = len(pending_r1_tasks) < MAX_PARALLEL_R1_TASKS
            should_process_batch = (batch_full or batch_timeout_reached) and can_start_new_task
            
            if should_process_batch and surgr1_batch_buffer:
                # 取出要处理的帧（动态数量）
                actual_batch_size = min(len(surgr1_batch_buffer), SURGR1_MAX_BATCH_SIZE)
                batch_to_process = surgr1_batch_buffer[:actual_batch_size]
                surgr1_batch_buffer = surgr1_batch_buffer[actual_batch_size:]
                
                # 如果缓冲区清空了，重置时间
                if not surgr1_batch_buffer:
                    last_batch_time = None
                
                batch_timestamps = [f"{f['timestamp']:.1f}s" for f in batch_to_process]
                logger.info(f"[SurgR1 Batch] Starting async processing of {len(batch_to_process)} frames (buffer remaining: {len(surgr1_batch_buffer)}, timestamps: {batch_timestamps})")
                
                # ========== 【关键改进】非阻塞启动 R1 处理任务 ==========
                # 帧采集继续进行，R1 处理在后台运行
                async def _process_batch(frames_to_process):
                    try:
                        results = await surgr1_client.analyze_frames_batch(
                            frames=frames_to_process,
                            analysis_type="all",
                            session_id=session_id,
                            save_to_mysql=False
                        )
                        return results, frames_to_process
                    except Exception as e:
                        logger.warning(f"SurgR1 batch analysis failed: {e}")
                        return [], frames_to_process
                
                r1_processing_buffer = batch_to_process
                new_task = asyncio.create_task(_process_batch(batch_to_process))
                pending_r1_tasks.append(new_task)
                logger.info(f"[SurgR1] Started task #{len(pending_r1_tasks)} (max: {MAX_PARALLEL_R1_TASKS})")
            
            # SAM3 streaming: process frame with masks
            # Key insight: Only reinit SAM3 when instruments change, otherwise just propagate
            if sam3_due:
                last_sam3_time = current_time
                if pil_image is None:
                    pil_image = await loop.run_in_executor(None, _bgr_to_pil, bgr_frame)
                
                try:
                    need_reinit = False
                    reinit_reason = None
                    sam3_result = None
                    
                    # Extract current instrument labels from bboxes
                    current_instruments = set()
                    for bbox in last_bboxes:
                        label = bbox.get("label", "unknown")
                        current_instruments.add(label)
                    
                    # 调试：输出 SAM3 处理状态
                    logger.info(f"[SAM3] Processing: initialized={sam3_initialized}, bboxes={len(last_bboxes)}, instruments={current_instruments}")
                    
                    # Check if we need to reinitialize SAM3
                    if not sam3_initialized and last_bboxes:
                        # First time seeing instruments - initialize
                        need_reinit = True
                        reinit_reason = "first_detection"
                    elif is_surgr1_frame and last_bboxes:
                        # Check if instruments changed (new instruments appeared)
                        new_instruments = current_instruments - sam3_tracked_instruments
                        if new_instruments:
                            need_reinit = True
                            reinit_reason = f"new_instruments: {new_instruments}"
                        # Check if instrument count changed significantly
                        elif len(current_instruments) != len(sam3_tracked_instruments):
                            need_reinit = True
                            reinit_reason = f"count_changed: {len(sam3_tracked_instruments)} -> {len(current_instruments)}"
                        # Also check consistency checker if available
                        elif consistency_checker:
                            decision = consistency_checker.check(
                                current_time=current_time,
                                surgr1_bboxes=last_bboxes,
                                sam3_masks=None
                            )
                            if decision.need_reinit:
                                need_reinit = True
                                reinit_reason = decision.reason
                    
                    # Determine what to send to SAM3
                    if need_reinit and last_bboxes:
                        # Need to reinitialize - close old session and create new
                        logger.info(f"SAM3 reinit triggered: {reinit_reason}")
                        
                        # Close old session if exists
                        if sam3_initialized:
                            try:
                                await sam3_client.close_stream_session(sam3_session_id)
                            except:
                                pass
                        
                        # Create new session
                        new_session_result = await sam3_client.create_stream_session(session_id)
                        if new_session_result.get("success"):
                            sam3_session_id = new_session_result.get("session_id")
                            sam3_streaming_sessions[session_id]["sam3_session_id"] = sam3_session_id
                            
                            # Process frame with bboxes (initialization)
                            # 调试：记录发送给 SAM3 的 bboxes
                            logger.info(f"[SAM3] Initializing with {len(last_bboxes)} bboxes: {last_bboxes}")
                            
                            sam3_result = await sam3_client.process_stream_frame(
                                session_id=sam3_session_id,
                                frame=pil_image,
                                frame_idx=frame_idx,
                                timestamp=current_time,
                                bboxes=last_bboxes
                            )
                            
                            # 调试：记录 SAM3 返回结果
                            logger.info(f"[SAM3] Init result: success={sam3_result.get('success')}, "
                                       f"num_objects={sam3_result.get('num_objects', 0)}, "
                                       f"has_image={bool(sam3_result.get('image_base64'))}")
                            
                            if sam3_result.get("success"):
                                sam3_initialized = True
                                sam3_tracked_instruments = current_instruments.copy()
                                logger.info(f"[SAM3] Initialized successfully, tracking: {sam3_tracked_instruments}")
                            else:
                                logger.warning(f"[SAM3] Initialization failed: {sam3_result.get('error', 'unknown')}")
                            
                            # Update consistency checker
                            if consistency_checker:
                                consistency_checker.update_after_reinit(
                                    current_time=current_time,
                                    bboxes=last_bboxes,
                                    sam3_result=sam3_result
                                )
                        else:
                            logger.error("Failed to create new SAM3 session")
                            continue
                    
                    elif sam3_initialized:
                        # Already initialized - just propagate masks (FAST!)
                        sam3_result = await sam3_client.process_stream_frame(
                            session_id=sam3_session_id,
                            frame=pil_image,
                            frame_idx=frame_idx,
                            timestamp=current_time,
                            bboxes=None  # Propagate only
                        )
                        
                        # Update consistency checker for propagation
                        if consistency_checker:
                            consistency_checker.update_after_propagate(
                                current_time=current_time,
                                sam3_masks=None
                            )
                    
                    # Store result if successful
                    if sam3_result:
                        success = sam3_result.get("success", False)
                        has_image = bool(sam3_result.get("image_base64"))
                        num_objects = sam3_result.get("num_objects", 0)
                        propagated = sam3_result.get("propagated", False)
                        
                        # 调试日志：详细记录 SAM3 结果
                        if success and has_image:
                            logger.debug(f"[SAM3] Frame {frame_idx}: success, {num_objects} objects, propagated={propagated}")
                            
                            # Store latest SAM3 frame for frontend access
                            sam3_latest_frames[session_id] = {
                                "timestamp": current_time,
                                "frame_idx": frame_idx,
                                "image_base64": sam3_result["image_base64"],
                                "num_objects": num_objects,
                                "propagated": propagated,
                                "reinit_reason": reinit_reason,
                                "state": consistency_checker.state.value if consistency_checker else "unknown",
                                "updated_at": time_module.time()
                            }
                            
                            # Update streaming session info
                            if session_id in sam3_streaming_sessions:
                                sam3_streaming_sessions[session_id]["frame_count"] += 1
                                sam3_streaming_sessions[session_id]["last_update"] = time_module.time()
                                sam3_streaming_sessions[session_id]["state"] = \
                                    consistency_checker.state.value if consistency_checker else "unknown"
                        else:
                            # 调试：记录失败原因
                            error_msg = sam3_result.get("error", "unknown")
                            logger.warning(f"[SAM3] Frame {frame_idx}: failed - success={success}, has_image={has_image}, num_objects={num_objects}, error={error_msg}")
                    else:
                        logger.warning(f"[SAM3] Frame {frame_idx}: sam3_result is None")
                            
                except Exception as e:
                    logger.error(f"[SAM3] Frame {frame_idx} exception: {e}")
                    import traceback
                    traceback.print_exc()
            
            frame_idx += 1
            
            # [perf] 实时流下 cap.read() 已经 await 等帧，不需要再 sleep；
            # 本地文件或设备拉流会以 cv2 最大速度读，需要节流到 fps 避免 100% CPU
            if is_realtime_stream:
                # 让出事件循环一跳，避免饿死其它协程；不做固定延迟
                await asyncio.sleep(0)
            else:
                # 本地文件/设备：按名义 fps 节流（最多读到 2x 正常速度）
                target_interval = max(1.0 / (fps * 2.0), 0.005)
                await asyncio.sleep(target_interval)
        
        # [perf] cap.release() 对某些 backend（FFMPEG/RTSP）会阻塞数秒，放 executor
        await loop.run_in_executor(None, cap.release)
        logger.info(f"SurgR1 continuous task stopped for {session_id}")
    
    except asyncio.CancelledError:
        logger.info(f"SurgR1 continuous task cancelled for {session_id}")
        # Let the finally block handle cleanup
        
    except Exception as e:
        logger.error(f"SurgR1 continuous task error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        surgr1_continuous_flags[session_id] = False
        
        # 【优化】等待所有并行 R1 任务完成，确保所有帧都被处理
        if pending_r1_tasks:
            logger.info(f"[SurgR1] Waiting for {len(pending_r1_tasks)} pending R1 tasks to complete...")
            for task in pending_r1_tasks:
                if not task.done():
                    try:
                        batch_results, batch_to_process_done = await asyncio.wait_for(task, timeout=30.0)
                        # 保存结果
                        for i, result in enumerate(batch_results):
                            if i >= len(batch_to_process_done):
                                break
                            batch_frame = batch_to_process_done[i]
                            mysql_service.save_analysis(
                                session_id=session_id,
                                frame_idx=batch_frame["frame_idx"],
                                timestamp=batch_frame["timestamp"],
                                analysis_type="frame",
                                tool_localization=result.get("tools", ""),
                                surgical_action=result.get("action", ""),
                                surgical_phase=result.get("phase", ""),
                                image_saved=1 if storage_path else 0
                            )
                        logger.info(f"[SurgR1] Final batch processed: {len(batch_results)} frames")
                    except asyncio.TimeoutError:
                        logger.warning(f"[SurgR1] Timeout waiting for final R1 task")
                    except Exception as e:
                        logger.warning(f"[SurgR1] Error in final R1 task: {e}")
            pending_r1_tasks.clear()
        
        # Cancel frame capture task
        if frame_capture_task and not frame_capture_task.done():
            frame_capture_task.cancel()
            try:
                await frame_capture_task
            except asyncio.CancelledError:
                logger.info(f"Frame capture task cancelled for session {session_id}")
        
        # Clean up task reference
        active_surgr1_tasks.pop(session_id, None)
        
        # CRITICAL: Release video capture to free stream connection
        # [perf] release 对 FFMPEG/RTSP 可能阻塞，放 executor
        if cap is not None:
            try:
                await loop.run_in_executor(None, cap.release)
                logger.info(f"Released video capture for session {session_id}")
            except Exception as e:
                logger.warning(f"Error releasing video capture: {e}")
        
        # Clean up SAM3 streaming session
        if sam3_client and sam3_session_id:
            try:
                await sam3_client.close_stream_session(sam3_session_id)
            except Exception as e:
                logger.warning(f"Error closing SAM3 session: {e}")
        
        # Clean up stored data
        sam3_streaming_sessions.pop(session_id, None)
        sam3_latest_frames.pop(session_id, None)
        
        db.close()


# ==============================================================================
# GLM-only Summarization (uses existing SurgR1 results)
# ==============================================================================

class GLMSummarizeRequest(BaseModel):
    """Request for GLM-only summarization"""
    session_id: str
    use_chinese: bool = True
    use_glm_multimodal: bool = False
    is_live: bool = False  # True=在线实时流(速度优先), False=离线视频(准确率优先)


@router.post("/start-glm-summarization")
async def start_glm_summarization(
    request: GLMSummarizeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Start GLM summarization using existing SurgR1 frame analysis results.
    
    This is called when user clicks "开始分析". It uses the SurgR1 results
    that have been continuously collected in the background.
    """
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Check VLM availability, but do not block the realtime pipeline. Live
    # sessions can still emit local expert Stage 1 summaries and later refine
    # them when VLM becomes available.
    vlm_available = False
    try:
        vlm_client = await ensure_vlm_available()
        is_healthy = await vlm_client.check_health()
        if not is_healthy:
            raise RuntimeError("VLM service not available")
        vlm_available = True
    except Exception as e:
        logger.warning(
            "[GLM] VLM unavailable for session %s; starting expert-only Stage 1: %s",
            request.session_id,
            e,
        )
    
    # Update session status to processing
    from ..database import update_session_status
    update_session_status(db, request.session_id, "processing")
    
    # The current deployment uses local phase/triplet/YOLO experts. Retain the
    # legacy health gate only when that API is explicitly enabled.
    if _legacy_surgr1_enabled():
        try:
            from ..services.surgr1_client import SurgR1Client
            surgr1_client = SurgR1Client()
            r1_healthy = await surgr1_client.check_health()
            if not r1_healthy:
                existing_frames = get_frames_by_session(db, session["session_id"]) or []
                has_frame_rows = len(existing_frames) > 0
                if not surgr1_continuous_flags.get(request.session_id, False) and not has_frame_rows:
                    update_session_status(db, request.session_id, "error")
                    raise HTTPException(503, "SurgR1 服务不可用，请先启动 R1 服务（bash SurgR1_api/run.sh）")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"[GLM] SurgR1 health check error: {e}, proceeding anyway")
    
    # Clear any previous cancellation flag
    analysis_cancellation_flags[request.session_id] = False
    
    # Get current frame count
    frames = get_frames_by_session(db, session["session_id"])
    frame_count = len(frames) if frames else 0
    
    # Start background task using asyncio.create_task for proper async execution
    # This ensures the task runs in the background and continues processing
    glm_task = asyncio.create_task(
        glm_summarization_task(
            session_id=request.session_id,
            video_path=session["video_path"],
            db_session_id=session["session_id"],
            use_chinese=request.use_chinese,
            use_glm_multimodal=request.use_glm_multimodal,
            is_live=request.is_live
        )
    )
    _track_glm_task(session["session_id"], glm_task)
    
    logger.info(f"[GLM] Started background task for session {request.session_id}")
    
    return {
        "message": "GLM summarization started",
        "session_id": request.session_id,
        "processing_mode": "glm_only",
        "frames_available": frame_count,
        "surgr1_running": surgr1_continuous_flags.get(request.session_id, False),
        "vlm_available": vlm_available,
        "stage1_fallback": not vlm_available,
    }


async def glm_summarization_task(
    session_id: str,
    video_path: str,
    db_session_id: str,  # session_id string, not int
    use_chinese: bool = True,
    use_glm_multimodal: bool = False,
    is_live: bool = False
):
    """
    Background task for GLM summarization using existing SurgR1 results.
    
    is_live=True (在线模式): 每个完整5秒窗口立即输出本地专家结果，并异步做本地VLM复核
    is_live=False (离线模式): 准确率优先，等满帧再处理
    """
    from ..database import update_session_status
    db = next(get_db())
    
    try:
        vlm_client = None
        try:
            vlm_client = await ensure_vlm_available()
            if not await vlm_client.check_health():
                logger.warning("[GLM Task] VLM health check failed; using expert-only Stage 1")
                vlm_client = None
        except Exception as e:
            logger.warning(f"[GLM Task] VLM unavailable; using expert-only Stage 1: {e}")
        processor_source = resolve_video_source(video_path).source
        
        processor = VideoProcessor(
            video_path=processor_source,
            window_duration=settings.WINDOW_DURATION,
            sample_interval=settings.SAMPLE_INTERVAL
        )
        
        # Wait for SurgR1 results if none exist yet (up to 30 seconds).
        # In live mode we do not gate Stage 1 on R1: frame_capture_service already
        # saves stream frames independently, so the fast expert+Gemini draft can
        # start from saved frames and Stage 2 can refine it later when R1 lands.
        all_frames = None
        wait_count = 0
        max_wait = 30  # seconds
        mysql_service = get_mysql_service()
        video_session = mysql_service.get_video_session(session_id)
        storage_path = video_session.get("storage_path") if video_session else None
        frame_storage = get_frame_storage_service()
        
        while wait_count < max_wait:
            # Check cancellation
            if analysis_cancellation_flags.get(session_id, False):
                logger.info(f"GLM summarization cancelled while waiting for SurgR1")
                update_session_status(db, session_id, "cancelled")
                return
                
            all_frames = get_frames_by_session(db, db_session_id)
            if all_frames and len(all_frames) > 0:
                break

            if is_live and storage_path:
                capture_stats = get_frame_capture_service().get_capture_stats(session_id) or {}
                if (capture_stats.get("last_capture_time") or 0) > 0:
                    logger.info("[GLM Task] Live saved frames available; starting Stage 1 before R1")
                    break
            
            logger.info(f"Waiting for SurgR1 results... ({wait_count}s)")
            await asyncio.sleep(1)
            wait_count += 1
            db.expire_all()  # Refresh database cache
        
        if (not all_frames or len(all_frames) == 0) and not is_live:
            logger.warning(f"No SurgR1 results available for session {session_id} after waiting")
            # Create a placeholder summary to inform user
            create_window_summary(
                db=db,
                session_id=db_session_id,
                window_id=0,
                start_time=0,
                end_time=5,
                summary_text="⚠️ 等待 SurgR1 分析帧... 请确保 SurgR1 服务正在运行。",
                tools_detected=[],
                key_actions=[]
            )
            update_session_status(db, session_id, "completed")
            return
        
        # Group frames by window
        window_frames = {}
        for frame in all_frames or []:
            # Handle both dict and object access patterns, ensure ts is not None
            ts = frame.get("timestamp") if isinstance(frame, dict) else getattr(frame, "timestamp", None)
            ts = ts if ts is not None else 0  # Ensure ts is never None
            window_id = int(ts / settings.WINDOW_DURATION)
            if window_id not in window_frames:
                window_frames[window_id] = []
            window_frames[window_id].append(frame)
        
        logger.info(f"Processing {len(window_frames)} windows with GLM for session {session_id}")
        
        # Chinese system prompt for surgical video analysis
        CHINESE_SYSTEM_PROMPT = """你是一位专业的腹腔镜胆囊切除术视频分析专家。你将收到一个5秒视频窗口的逐帧分析结果。请将这些分析整合成一个简洁的中文叙述摘要。

## 手术阶段
- Preparation(准备)
- CalotTriangleDissection(肝胆三角解剖)
- ClippingCutting(夹闭切断)
- GallbladderDissection(胆囊分离)
- GallbladderRetraction(标本袋牵拉取出)
- CleaningCoagulation(清洁止血)
- GallbladderPackaging(胆囊取出)

## 关键解剖结构
胆囊管、胆囊动脉、胆囊、肝胆三角、胆囊板

## 手术器械
抓钳、电凝钩、剪刀、钛夹钳、Hem-o-lok夹、金属钛夹、冲洗器、双极电凝钳

## 你的任务
根据多帧分析结果，用2-4句中文描述：
1. 当前手术阶段和主要操作
2. 使用的器械及操作方式
3. 重要观察发现

关键要求：
- 如果出现施夹器、钛夹钳、Hem-o-lok或金属钛夹，必须明确写出“钛夹、施夹、夹闭”的动作或结果，并尽量判断夹闭目标是胆囊管还是胆囊动脉。
- 禁止单独使用“管状结构”作为最终对象；夹闭、施夹、钛夹释放、剪切发生时，必须在胆囊管和胆囊动脉中选一个具体对象。
- 不要写“或者”和斜线合并对象；如果证据不足，参考 Triplet 目标倾向做二选一。
- 胆囊管和胆囊动脉的剪断是不可逆动作；必须先看到CVS达成，并且同一目标已被Hem-o-lok夹、金属钛夹或钛夹钳明确夹闭，才能写“剪刀剪断/切断胆囊管或胆囊动脉”。
- 如果没有夹闭证据，只能写剪刀在目标附近操作或分离组织；已经剪断后的同一目标，后续不要再写正在夹闭，只能写已夹闭残端。
- 所有动作必须有主谓宾：例如“抓钳牵拉胆囊颈和胆囊体以暴露肝胆三角”“电凝钩分离肝胆三角纤维脂肪组织”“钛夹钳夹闭胆囊管”。不要写“牵拉胆囊管”“牵拉组织”“相关操作”。
- 摘要不能只写“处理区域”“分离组织”或“术野观察”。必须说明器械、可见动作、明确到当前证据支持的解剖区域，以及操作目的或造成的进展；看不清具体胆囊管/胆囊动脉时写“肝胆三角关键结构”，不要臆测二选一。
- 双极电凝钳有两片可开合钳口，常有蓝色绝缘包覆；电凝钩是单杆细小L形钩头。不要把蓝色双钳口写成电凝钩。
- 如果阶段为肝胆三角解剖、夹闭切断，或画面出现胆囊管、胆囊动脉夹闭，请同时给出CVS评估状态。
- CVS三要素包括：肝胆三角充分清理、胆囊下1/3从肝床/胆囊板分离、只有胆囊管和胆囊动脉两条结构进入胆囊。
- 证据不足时写“CVS评估中/尚未完全确认”；三要素均明确时才写“CVS达成”。

请务必使用中文回答！"""
        
        # Track processed windows to continue from where we left off
        processed_windows = set()
        # Stage 1 is allowed to run before R1 in live mode. A window is added to
        # processed_windows only after Stage 2/final processing, so keep a
        # separate set to avoid repeatedly emitting the same draft.
        stage1_processed_windows = set()
        last_window_id = -1
        # 放宽到 10 分钟：真实流处理中 SurgR1 可能短暂积压；cancellation flag 仍负责"用户点停止"。
        max_wait_for_new_frames = 600
        no_new_frames_count = 0
        loop_count = 0
        
        logger.info(f"[GLM Task] Starting continuous summarization for session {session_id}, mode={'LIVE' if is_live else 'OFFLINE'}")
        
        while True:
            loop_count += 1
            
            # Check cancellation flag
            if analysis_cancellation_flags.get(session_id, False):
                logger.info(f"[GLM Task] Cancelled for session {session_id}")
                update_session_status(db, session_id, "cancelled")
                return
            
            # Refresh frames from database
            try:
                all_frames = get_frames_by_session(db, db_session_id)
            except Exception as e:
                logger.error(f"[GLM Task] Failed to get frames: {e}")
                await asyncio.sleep(2)
                continue
            
            # Rebuild window_frames with new data
            # ========== 按 timestamp 去重：同一时间戳只保留最新的帧 ==========
            # 避免因 R1 批量重试导致的重复帧
            unique_frames = {}
            for frame in all_frames:
                ts = frame.get("timestamp") if isinstance(frame, dict) else getattr(frame, "timestamp", None)
                ts = ts if ts is not None else 0
                # 四舍五入到 0.1 秒精度，避免浮点数比较问题
                ts_key = round(ts, 1)
                # 保留最新的（按 id 或覆盖）
                if ts_key not in unique_frames:
                    unique_frames[ts_key] = frame
                else:
                    # 如果有 id，保留 id 更大的（更新的）
                    old_id = unique_frames[ts_key].get("id", 0) if isinstance(unique_frames[ts_key], dict) else getattr(unique_frames[ts_key], "id", 0)
                    new_id = frame.get("id", 0) if isinstance(frame, dict) else getattr(frame, "id", 0)
                    if new_id > old_id:
                        unique_frames[ts_key] = frame
            
            # 按窗口分组
            window_frames = {}
            for ts_key, frame in unique_frames.items():
                window_id = int(ts_key / settings.WINDOW_DURATION)
                if window_id not in window_frames:
                    window_frames[window_id] = []
                window_frames[window_id].append(frame)
            
            # ========== 窗口就绪判断逻辑 ==========
            # 理论帧数 = 窗口时长 / 采样间隔 = WINDOW_DURATION / SAMPLE_INTERVAL
            # 例如：15秒窗口 / 1秒采样 = 15帧
            EXPECTED_FRAMES_PER_WINDOW = int(settings.WINDOW_DURATION / settings.SAMPLE_INTERVAL)
            
            # 从配置读取最小帧数比例（默认 30%，降低以适应实时流处理延迟）
            # 实时流下 SurgR1 处理速度可能跟不上视频播放，导致帧被跳过
            from ..services.vlm_factory import load_config as load_vlm_config
            vlm_config = load_vlm_config()
            min_frames_ratio = vlm_config.get("window_analysis", {}).get("min_frames_ratio", 0.3)
            live_stage2_enabled = bool(
                (vlm_config.get("window_analysis") or {}).get(
                    "live_stage2_enabled",
                    False,
                )
            )
            
            # 最小帧数要求：至少需要理论帧数的 min_frames_ratio（默认 30%）
            # 对于15秒窗口（期望15帧），只需要约5帧即可触发总结
            MIN_FRAMES_PER_WINDOW = max(3, int(EXPECTED_FRAMES_PER_WINDOW * min_frames_ratio))
            
            # 获取所有窗口ID并排序
            r1_window_ids = sorted(window_frames.keys())
            stage1_ready_windows = []
            playback_limited_elapsed = None

            # Live fast path: use independently saved stream frames to emit
            # Stage 1 without waiting for SurgR1. This is the intended v4
            # two-stage contract: experts+Gemini first, R1+Gemini refinement
            # later. We still require saved frames in the target window so
            # expert_fusion has real images to inspect.
            if is_live and storage_path:
                try:
                    capture_stats = get_frame_capture_service().get_capture_stats(session_id) or {}
                    live_elapsed = float(capture_stats.get("last_capture_time") or 0)
                    try:
                        video_session_for_clock = get_video_session(db, session_id) or {}
                        playback_position = float(video_session_for_clock.get("current_position") or 0)
                        if playback_position > 0:
                            live_elapsed = min(live_elapsed, playback_position + 1.0)
                        playback_limited_elapsed = live_elapsed
                    except Exception as e:
                        logger.debug(f"[GLM Task] Could not clamp live clock to playback position: {e}")
                    if live_elapsed > 0:
                        max_completed = int(live_elapsed / settings.WINDOW_DURATION)
                        candidate_ids = set(range(0, max_completed))

                        for wid in sorted(candidate_ids):
                            if wid in stage1_processed_windows:
                                continue
                            start_time = wid * settings.WINDOW_DURATION
                            end_time = start_time + settings.WINDOW_DURATION
                            if live_elapsed + 0.2 < end_time:
                                continue
                            saved = frame_storage.list_frames_in_range(
                                storage_path=storage_path,
                                start_time=start_time,
                                end_time=end_time,
                                subfolder="frames"
                            )
                            if len(saved) >= 2:
                                stage1_ready_windows.append(wid)
                except Exception as e:
                    logger.warning(f"[GLM Task] Live Stage 1 readiness check failed: {e}")

            all_window_ids = sorted(set(r1_window_ids) | set(stage1_ready_windows))
            max_window_id = max(all_window_ids) if all_window_ids else -1
            
            # 窗口就绪条件（放宽条件以适应实际处理延迟）：
            # 1. 帧数达到理论帧数 → 立即处理
            # 2. 帧数 >= 最小帧数 且 下一个窗口已开始 → 可以处理
            # 3. 帧数 >= 最小帧数 且 是最后一个窗口（没有更多帧进来）→ 可以处理
            # 4. 【新增】帧数不足但已被跳过（下一个窗口已开始+2）→ 强制处理，避免永久跳过
            new_windows = []
            waiting_windows = []  # 等待更多帧的窗口

            # Add live Stage 1 windows first. They are not final, so they are
            # not marked processed here; Stage 2 can still run later when R1
            # frame analyses arrive.
            new_windows.extend(stage1_ready_windows)
            
            # ========== 在线模式：第一个窗口特殊化，尽早出结果 ==========
            # 离线模式：正常等满帧再处理，保证准确率
            first_window_fast = bool(
                is_live
                and live_stage2_enabled
                and len(processed_windows) == 0
            )
            
            for wid in r1_window_ids:
                if wid in processed_windows:
                    continue
                    
                frame_count = len(window_frames[wid])
                has_next_window = (wid + 1) in window_frames  # 下一个窗口是否已开始
                has_skip_gap = (wid + 2) in window_frames  # 是否已被跳过（下下个窗口已开始）
                is_latest_window = (wid == max_window_id)
                
                # 【在线模式特殊】第一个窗口快速触发：有 2 帧就立即处理
                if first_window_fast and frame_count >= 2:
                    logger.info(f"[GLM Task] 🚀 Live mode fast-track: window {wid} with {frame_count} frames")
                    new_windows.append(wid)
                    first_window_fast = False
                    continue
                
                # 条件1：帧数已满（所有图像都被R1处理）→ 立即可以处理
                if frame_count >= EXPECTED_FRAMES_PER_WINDOW:
                    new_windows.append(wid)
                # 条件2：帧数 >= 最小帧数 且 下一个窗口已开始 → 可以处理
                elif frame_count >= MIN_FRAMES_PER_WINDOW and has_next_window:
                    new_windows.append(wid)
                # 条件3：帧数 >= 最小帧数 且 是最新窗口 且 有2个以上窗口在等待 → 可以处理（避免无限等待）
                elif frame_count >= MIN_FRAMES_PER_WINDOW and is_latest_window and len(all_window_ids) >= 2:
                    # 检查是否有足够多的窗口在等待，说明视频已经播放了一段时间
                    unprocessed_count = len([w for w in all_window_ids if w not in processed_windows])
                    if unprocessed_count >= 2:
                        new_windows.append(wid)
                    else:
                        waiting_windows.append((wid, frame_count, f"等待下一窗口开始"))
                # 【新增】条件4：帧数不足但已被跳过（下下个窗口已有帧）→ 强制处理
                # 这确保即使帧数不足（甚至只有1帧），只要被跳过了就会被处理，避免永久丢失
                elif frame_count >= 1 and has_skip_gap:
                    logger.info(f"[GLM Task] Force processing window {wid} with only {frame_count} frame(s) (skipped)")
                    new_windows.append(wid)
                # 【新增】条件5：帧数为0但已被跳过 → 标记为已处理（无法分析）
                elif frame_count == 0 and has_skip_gap:
                    logger.warning(f"[GLM Task] Window {wid} has 0 frames, marking as processed (skipped)")
                    processed_windows.add(wid)  # 直接标记为已处理，避免永久等待
                else:
                    # 帧数不足，继续等待 R1 处理更多帧
                    waiting_windows.append((wid, frame_count, f"需要{MIN_FRAMES_PER_WINDOW}帧(当前{frame_count})"))

            if is_live and playback_limited_elapsed is not None:
                max_allowed_window = int(playback_limited_elapsed / settings.WINDOW_DURATION)
                min_candidate_window = min(new_windows) if new_windows else None
                # After backend/frontend restarts, playback position can reset
                # to zero while stored frame timestamps still start at a later
                # window. In that case the two clocks are not comparable; do
                # not filter everything out.
                if min_candidate_window is None or max_allowed_window >= min_candidate_window:
                    new_windows = [wid for wid in new_windows if wid <= max_allowed_window]
                else:
                    logger.info(
                        "[GLM Task] Skipping playback clamp: playback window %s < first candidate %s",
                        max_allowed_window,
                        min_candidate_window,
                    )

            new_windows = sorted(set(new_windows))

            if is_live and new_windows:
                draft_ids = sorted(
                    [wid for wid in new_windows if wid not in stage1_processed_windows],
                    reverse=True,
                )
                refinement_ids = sorted(
                    [wid for wid in new_windows if wid in stage1_processed_windows]
                )
                selected_ids = draft_ids[:2]
                if len(draft_ids) > 2:
                    selected_ids.append(draft_ids[-1])
                if not selected_ids and refinement_ids:
                    selected_ids = refinement_ids[:1]
                new_windows = list(dict.fromkeys(selected_ids))
            
            # Log window frame counts for debugging
            window_frame_counts = {wid: len(window_frames[wid]) for wid in window_frames.keys()}
            
            # Log status every 10 loops or when we have new windows
            if loop_count % 10 == 1 or new_windows:
                logger.info(f"[GLM Task] Loop {loop_count}: {len(all_frames)} frames, expected={EXPECTED_FRAMES_PER_WINDOW}/window, frame_counts={window_frame_counts}, processed={processed_windows}, ready={new_windows}")
            
            # Log waiting windows
            if waiting_windows and loop_count % 5 == 1:
                logger.info(f"[GLM Task] Waiting for R1: {waiting_windows}")
            
            if not new_windows:
                capture_stats = get_frame_capture_service().get_capture_stats(session_id) if is_live else None
                capture_stopped = bool(capture_stats and not capture_stats.get("is_running"))
                if capture_stopped:
                    remaining_windows = {
                        wid for wid in all_window_ids
                        if wid not in processed_windows and len(window_frames.get(wid, [])) >= 1
                    }
                    # The independent frame writer can finish before the final
                    # SurgR1 batch reaches the database. Recover those tail
                    # windows directly from persisted capture frames.
                    if storage_path:
                        last_capture_time = float(capture_stats.get("last_capture_time") or 0)
                        last_storage_window = int(last_capture_time / settings.WINDOW_DURATION)
                        for wid in range(max(0, last_storage_window) + 1):
                            if wid in processed_windows:
                                continue
                            start_time = wid * settings.WINDOW_DURATION
                            end_time = start_time + settings.WINDOW_DURATION
                            stored = frame_storage.list_frames_in_range(
                                storage_path=storage_path,
                                start_time=start_time,
                                end_time=end_time,
                                subfolder="frames",
                            )
                            if stored:
                                remaining_windows.add(wid)
                    remaining_windows = sorted(remaining_windows)
                    if remaining_windows:
                        logger.info(f"[GLM Task] Capture ended; processing remaining windows before exit: {remaining_windows}")
                        new_windows = remaining_windows
                    else:
                        logger.info(f"[GLM Task] Capture ended and no remaining windows; stopping for session {session_id}")
                        update_session_status(db, session_id, "completed")
                        break
                else:
                    no_new_frames_count += 1
                    if no_new_frames_count >= max_wait_for_new_frames:
                        logger.info(f"[GLM Task] No new frames for {max_wait_for_new_frames}s, checking for remaining windows...")
                        
                        # ========== 【新增】流结束时强制处理所有剩余窗口 ==========
                        # 确保即使帧数不足的窗口也能有总结，避免历史记录出现空洞
                        remaining_windows = [wid for wid in all_window_ids if wid not in processed_windows and len(window_frames.get(wid, [])) >= 1]
                        if remaining_windows:
                            logger.info(f"[GLM Task] Force processing {len(remaining_windows)} remaining windows before exit: {remaining_windows}")
                            new_windows = remaining_windows
                            no_new_frames_count = 0  # 重置计数器，允许处理完成
                            # 继续下面的处理逻辑，不 continue
                        else:
                            logger.info(f"[GLM Task] No remaining windows with frames, stopping for session {session_id}")
                            break
                    else:
                        await asyncio.sleep(1)
                        continue
            
            no_new_frames_count = 0  # Reset counter when we have new windows
            
            # ========== 动态批处理：根据积压窗口数调整并发数 ==========
            # 积压多 → 并发数大；积压少 → 并发数小
            glm_max_concurrent = min(len(new_windows), settings.GLM_MAX_CONCURRENT if hasattr(settings, 'GLM_MAX_CONCURRENT') else 16)
            glm_max_concurrent = max(1, glm_max_concurrent)  # 至少 1
            
            logger.info(f"[GLM Task] Processing {len(new_windows)} new windows with {glm_max_concurrent} concurrent: {new_windows}")
            
            # ========== 1. 准备所有窗口数据 ==========
            windows_to_process = []
            window_metadata = {}
            
            from ..services.temporal_analyze import process_window_for_glm
            from ..services.analysis_logger import get_analysis_logger, close_analysis_logger
            from PIL import Image
            from pathlib import Path
            
            # 获取会话的存储路径（用于加载帧图片）
            mysql_service = get_mysql_service()
            video_session = mysql_service.get_video_session(session_id)
            storage_path = video_session.get("storage_path") if video_session else None
            frame_storage = get_frame_storage_service()
            
            # 获取分析日志记录器
            analysis_log = get_analysis_logger(session_id)
            
            for window_id in new_windows:
                if analysis_cancellation_flags.get(session_id, False):
                    break
                    
                frames = window_frames.get(window_id, [])
                start_time = window_id * settings.WINDOW_DURATION
                end_time = _bounded_window_end(
                    start_time,
                    settings.WINDOW_DURATION,
                    (video_session or {}).get("duration"),
                )
                
                # Build frame analyses for GLM
                frame_analyses = []
                for f in frames:
                    if isinstance(f, dict):
                        frame_analyses.append({
                            "frame_idx": f.get("frame_idx") or 0,
                            "timestamp": f.get("timestamp") or 0,
                            "phase": f.get("surgical_phase", "") or "",
                            "action": f.get("surgical_action", "") or "",
                            "tools": f.get("tool_localization", "") or ""
                        })
                    else:
                        frame_analyses.append({
                            "frame_idx": getattr(f, "frame_idx", None) or 0,
                            "timestamp": getattr(f, "timestamp", None) or 0,
                            "phase": getattr(f, "surgical_phase", "") or "",
                            "action": getattr(f, "surgical_action", "") or "",
                            "tools": getattr(f, "tool_localization", "") or ""
                        })
                
                # Temporal Analysis. Live Stage 1 can run before R1 has saved
                # any frame analyses, so allow an empty frame_analyses list.
                if frame_analyses:
                    temporal_result = process_window_for_glm(
                        frame_analyses=frame_analyses,
                        window_id=window_id,
                        window_duration=settings.WINDOW_DURATION
                    )
                else:
                    temporal_result = {"consistency": {"cleaned_data": {}}}
                consistency = temporal_result.get("consistency", {})
                
                logger.info(f"[Temporal] Window {window_id}: {consistency.get('cleaned_data', {})}")
                
                # ========== 加载窗口帧图片用于GLM多模态验证 ==========
                window_images = None
                expert_images = None
                if storage_path:
                    try:
                        # 获取该时间范围内的帧文件列表
                        frame_files = frame_storage.list_frames_in_range(
                            storage_path=storage_path,
                            start_time=start_time,
                            end_time=end_time,
                            subfolder="frames"
                        )
                        
                        if frame_files:
                            def _sample_frame_infos(files, max_count):
                                if len(files) <= max_count:
                                    return files
                                import numpy as _np_sample
                                idxs = _np_sample.linspace(0, len(files) - 1, max_count).round().astype(int)
                                sampled = []
                                seen = set()
                                for idx in idxs:
                                    idx = int(idx)
                                    if idx in seen:
                                        continue
                                    seen.add(idx)
                                    sampled.append(files[idx])
                                return sampled

                            def _load_images(sampled_files):
                                loaded = []
                                for frame_info in sampled_files:
                                    frame_path = Path(storage_path) / frame_info["path"]
                                    if frame_path.exists():
                                        try:
                                            img = Image.open(frame_path)
                                            loaded.append(img)
                                        except Exception as e:
                                            logger.warning(f"[GLM Task] Failed to load frame {frame_path}: {e}")
                                return loaded
                            
                            # Expert pass gets denser full-frame samples so short
                            # tool-tip actions are not missed. Gemini still gets a
                            # small image set below.
                            expert_images = _load_images(_sample_frame_infos(frame_files, 20))
                            window_images = _load_images(_sample_frame_infos(frame_files, 6))
                            
                            if window_images:
                                logger.info(
                                    f"[GLM Task] Loaded {len(window_images)} Gemini images and "
                                    f"{len(expert_images or [])} expert images for window {window_id}"
                                )
                            else:
                                window_images = None
                    except Exception as e:
                        logger.warning(f"[GLM Task] Failed to load frames for window {window_id}: {e}")
                        window_images = None
                
                # 记录窗口内所有帧的 R1 分析结果到日志
                analysis_log.log_window_frames(window_id, frame_analyses)
                
                # 添加到并发处理列表（现在包含图片！）
                windows_to_process.append({
                    "window_id": window_id,
                    "frame_analyses": frame_analyses,
                    "images": window_images,  # GLM多模态验证：图片 + R1分析结果
                    "expert_images": expert_images,
                    "stage1_only": window_id in stage1_ready_windows and not frame_analyses
                })
                
                # 存储元数据
                window_metadata[window_id] = {
                    "start_time": start_time,
                    "end_time": end_time,
                    "frame_analyses": frame_analyses,
                    "consistency": consistency,
                    "images_loaded": len(window_images) if window_images else 0
                }
            
            # ========== 2. 逐个处理窗口（每完成一个立即保存到 DB，前端可实时看到） ==========
            if windows_to_process:
                try:
                    import time as time_module
                    batch_start = time_module.time()
                    
                    logger.info(f"[GLM Task] Processing {len(windows_to_process)} windows sequentially (save-as-you-go)")
                    logger.info(f"[GLM Task] Window IDs: {[w['window_id'] for w in windows_to_process]}")
                    
                    # 获取或创建历史上下文管理器
                    from ..services.vlm_factory import get_history_manager
                    from ..services.glm_client import WindowSummary
                    history_manager = get_history_manager(session_id)
                    
                    if is_live:
                        draft_windows = sorted(
                            [
                                w for w in windows_to_process
                                if w.get("window_id") not in stage1_processed_windows
                            ],
                            key=lambda w: w.get("window_id", 0),
                            reverse=True,
                        )
                        refinement_windows = sorted(
                            [
                                w for w in windows_to_process
                                if w.get("window_id") in stage1_processed_windows
                            ],
                            key=lambda w: w.get("window_id", 0),
                        )
                        # Keep live UI close to playback: never spend a loop
                        # backfilling a long draft backlog before showing the
                        # newest windows. Older gaps are filled by later loops.
                        max_live_drafts = 2
                        max_live_refinements = 0 if draft_windows else 1
                        sorted_windows = draft_windows[:max_live_drafts] + refinement_windows[:max_live_refinements]
                    else:
                        sorted_windows = sorted(windows_to_process, key=lambda w: w.get("window_id", 0))
                    
                    # Pipeline v4: 两阶段流程
                    # Stage 1 = live mode: local experts only, no VLM/R1 wait;
                    # offline mode: experts → Gemini text-only.
                    # Stage 2 = 专家 + SurgR1 CoT + 1 张图 → Gemini 多模态（精修）
                    import numpy as _np
                    from ..services.expert_fusion import run_experts_on_window

                    phase_map = {
                        "准备阶段": "Preparation", "准备": "Preparation", "准备期": "Preparation",
                        "肝胆三角解剖": "CalotTriangleDissection", "Calot三角": "CalotTriangleDissection",
                        "夹闭切断": "ClippingCutting",
                        "胆囊分离": "GallbladderDissection",
                        "胆囊取出": "GallbladderPackaging",
                        "清洁凝血": "CleaningCoagulation",
                        "标本袋牵拉取出": "GallbladderRetraction",
                        "胆囊牵拉": "GallbladderRetraction",
                    }
                    analysis_runtime_config = load_config()
                    live_stage2_enabled = bool(
                        (analysis_runtime_config.get("window_analysis") or {}).get(
                            "live_stage2_enabled",
                            False,
                        )
                    )

                    def _pil_list_to_bgr(pil_list):
                        out = []
                        for im in pil_list or []:
                            try:
                                arr = _np.array(im.convert("RGB"))
                                out.append(arr[:, :, ::-1].copy())  # RGB→BGR
                            except Exception:
                                pass
                        return out

                    def _uniform_sample(items, max_count: int = 12):
                        if not items:
                            return []
                        if len(items) <= max_count:
                            return items
                        idxs = _np.linspace(0, len(items) - 1, max_count).round().astype(int)
                        sampled = []
                        seen = set()
                        for idx in idxs:
                            idx = int(idx)
                            if idx in seen:
                                continue
                            seen.add(idx)
                            sampled.append(items[idx])
                        return sampled

                    async def _open_vlm_realtime_hint(
                        vlm_client,
                        images,
                        start_time: float,
                        end_time: float,
                        expert_text: str,
                        window_id: int,
                        expert_pack: Optional[Dict[str, Any]] = None,
                    ) -> Dict[str, Any]:
                        """One external visual call for live window facts.

                        Hem-o-lok, titanium clips, gauze, bleeding and CVS are
                        deliberately grouped into one structured call so the UI
                        does not stitch together scattered model conclusions.
                        """
                        config = load_config()
                        open_cfg = config.get("services", {}).get("realtime_open_vision", {})
                        if os.environ.get("DISABLE_REALTIME_OPEN_VISION") == "1":
                            return {}
                        if not open_cfg.get("enabled", True):
                            return {}
                        if not images:
                            return {}

                        provider = str(open_cfg.get("provider", "openai")).lower()
                        model_name = open_cfg.get("model_name")
                        thinking_level = open_cfg.get("thinking_level", "none")
                        max_images = int(open_cfg.get("max_images", 4) or 4)
                        max_image_edge = max(320, int(open_cfg.get("max_image_edge", 640) or 640))
                        max_tokens = int(open_cfg.get("max_tokens", 180) or 180)
                        timeout_s = float(open_cfg.get("timeout", 8.0) or 8.0)
                        temperature = float(open_cfg.get("temperature", 0.1) or 0.1)
                        sampled = _uniform_sample(images, max_images)
                        if not sampled:
                            return {}
                        if len(sampled) == 1:
                            timestamps = [(start_time + end_time) / 2]
                        else:
                            timestamps = [
                                start_time + (end_time - start_time) * i / (len(sampled) - 1)
                                for i in range(len(sampled))
                            ]
                        target_hint = _target_hint_from_triplet((expert_pack or {}).get("triplet") or {})
                        raw_phase_context = str(
                            ((expert_pack or {}).get("phase") or {}).get("label", "") or ""
                        ).strip()
                        phase_context = _canonical_phase(raw_phase_context)
                        cvs_phase_context = bool(
                            phase_context in {"CalotTriangleDissection", "ClippingCutting"}
                            or raw_phase_context.lower() in {
                                "calot_triangle_dissection",
                                "clipping_cutting",
                            }
                        )
                        is_preparation_context = phase_context == "Preparation"
                        local_tool_labels = {
                            str(tool.get("label") or "").strip().lower()
                            for tool in ((expert_pack or {}).get("yolo") or {}).get("tools", [])
                            if isinstance(tool, dict)
                        }
                        local_clipper_seen = "clipper" in local_tool_labels
                        target_hint_cn = _target_cn(target_hint.get("label"))
                        target_hint_line = (
                            f"Triplet目标倾向：{target_hint_cn}，"
                            f"置信度{float(target_hint.get('confidence') or 0):.2f}，来源{target_hint.get('source')}"
                        )
                        local_visibility_cue = dict((expert_pack or {}).get("local_visibility") or {})
                        clip_detector_pack = (expert_pack or {}).get("clip_detector") or {}
                        local_clip_conf = _safe_float(clip_detector_pack.get("max_confidence"), 0.0)
                        local_clip_frames = int(clip_detector_pack.get("frames_seen") or 0)
                        local_clip_candidate = bool(
                            ((expert_pack or {}).get("hemlok_clip") or {}).get("detected")
                            or local_clipper_seen
                            or (
                                int(clip_detector_pack.get("detections_total") or 0) > 0
                                and local_clip_frames >= 3
                                and local_clip_conf >= 0.20
                                and phase_context in {
                                    "CalotTriangleDissection",
                                    "ClippingCutting",
                                    "GallbladderDissection",
                                }
                            )
                            # Keep in sync with _expert_pack_has_key_event_candidate:
                            # persistent low-confidence clip detections still deserve
                            # VLM review because real polymer clips score ~0.07-0.13.
                            or (
                                int(clip_detector_pack.get("detections_total") or 0) >= 8
                                and local_clip_frames >= 5
                                and local_clip_conf >= 0.07
                                and phase_context in {
                                    "CalotTriangleDissection",
                                    "ClippingCutting",
                                    "GallbladderDissection",
                                }
                            )
                        )
                        local_scissors_candidate = bool(
                            "scissors" in local_tool_labels
                            and phase_context in {"ClippingCutting", "GallbladderDissection", "CleaningCoagulation"}
                        )
                        local_bag_candidate = bool(
                            "specimen_bag" in local_tool_labels
                            or phase_context in {"GallbladderPackaging", "GallbladderRetraction"}
                        )
                        local_visibility_candidate = bool(
                            local_visibility_cue.get("fog")
                            or local_visibility_cue.get("out_of_body")
                            or local_visibility_cue.get("out_of_body_candidate")
                        )
                        local_bleeding_candidate = bool(
                            "bipolar" in local_tool_labels
                            and phase_context in {"GallbladderDissection", "CleaningCoagulation", "GallbladderPackaging"}
                        )
                        cvs_review_interval = max(
                            1,
                            int(open_cfg.get("cvs_review_interval_windows", 2) or 2),
                        )
                        local_cvs_candidate = bool(
                            cvs_phase_context and window_id % cvs_review_interval == 0
                        )
                        if bool(open_cfg.get("candidate_only", True)) and not any((
                            local_clip_candidate,
                            local_scissors_candidate,
                            local_bag_candidate,
                            local_visibility_candidate,
                            local_bleeding_candidate,
                            local_cvs_candidate,
                        )):
                            logger.debug(
                                "[OpenVLM] Skip window %s: no local key-event candidate "
                                "(phase=%s tools=%s)",
                                window_id,
                                phase_context or "Unknown",
                                sorted(local_tool_labels),
                            )
                            return {}
                        out_of_body_focus_line = ""
                        if local_visibility_cue.get("out_of_body_candidate"):
                            out_of_body_focus_line = (
                                "\n【重点复核：镜头移出体外候选】"
                                f"{local_visibility_cue.get('prompt_hint') or ''}"
                                "本地提示只是弱候选，不能直接当作结论。若腹腔内肝胆组织仍可见，"
                                "只是画面边缘或前景出现套管/镜鞘/入路器械，visibility必须保持clear，"
                                "不要写镜头移出体外，也不要设置visibility.out_of_body=true。"
                                "只有在腹腔内组织已经不可见，"
                                "且画面变成套管内壁、腹壁外场景、整帧红色贴近组织或手术室体外结构，"
                                "才输出 visibility.status=\"out_of_body\"、visibility.out_of_body=true、"
                                "visibility.confidence>=0.75，并在summary写“镜头移出体外，画面切换至套管口或腹壁外场景”。"
                                "浅黄或肤色的平坦腹壁皮肤、体毛、皮肤切口/血痕、白色套管阀门、"
                                "金属器械盘或蓝绿色手术巾都是体外证据，不要把这些平坦表面误认为肝脏或雾气。"
                            )
                        cvs_focus_line = ""
                        if local_cvs_candidate:
                            cvs_focus_line = (
                                "\n【重点复核：CVS关键安全视野】当前阶段正在处理肝胆三角。"
                                "此时cvs.status不能写not_applicable：三要素尚未完整可见时写assessing或partial；"
                                "只有肝胆三角已清理、胆囊下三分之一与胆囊板暴露、并且仅胆囊管和胆囊动脉"
                                "两条结构进入胆囊均在图像中清楚成立时，才写achieved且confidence>=0.85。"
                            )

                        question = (
                            "你将看到一个约5秒的腹腔镜胆囊切除术视频窗口的连续抽帧。"
                            "请只根据图像判断关键视觉事实；本地模型提示只能作为弱参考，可能误检。\n"
                            "必须把以下视觉信息合并到一次判断里：已释放夹子、钛夹钳、"
                            "剪刀切断胆囊管或胆囊动脉、标本袋/胆囊袋与胆囊装袋取出、纱布和棉片、"
                            "活动性出血、止血状态、CVS关键安全视野、"
                            "镜头起雾/烟雾/模糊、雾气解除、镜头移出体外或画面切到套管口/腹壁外/手术室场景。\n"
                            "判别规则：\n"
                            "- 不需要区分Hem-o-lok和金属钛夹，运行时统一称为“夹子”。"
                            "白色、乳白色、淡紫色、蓝色、绿色塑料锁扣夹，以及小型银灰金属夹，只要是已释放并留在组织上的夹体，都标为generic_clip。"
                            "白色长杆、电凝钩绝缘陶瓷头、吸引器尖端、套管针尖端不是夹体；"
                            "如果亮白物体连接在长杆器械末端且随器械移动，只能算器械尖端，不能标为夹子。"
                            "只有看到独立留在组织上的C形/U形/锁扣状夹体、释放中的夹体，或明确已夹闭残端，才可认为placed=true。"
                            "- 只看到钛夹钳时不要直接推断已经放置夹子；必须看到夹体、释放动作、已夹闭残端。"
                            "- 如果当前处于preparation/准备/入路建立阶段，禁止判断为施夹、夹闭、夹子或钛夹钳活动；"
                            "此阶段白色细长器械更可能是穿刺器械、套管针、镜头或入路器械。"
                            "- 如果钛夹钳夹臂张开后夹住细管状目标、夹臂围住目标结构、或正在释放夹体，"
                            "clip_applier.active必须为true，summary必须写出钛夹钳正在夹闭胆囊管或胆囊动脉。"
                            "- 如果看到剪刀夹住、剪断或正在剪断被夹闭后的细管状结构，scissors.cutting必须为true，"
                            "并判断target是cystic_duct还是cystic_artery；summary必须写出剪刀正在剪断胆囊管或胆囊动脉。"
                            "但剪断胆囊管或胆囊动脉是不可逆动作：只有在同一窗口或历史证据已看到CVS达成，"
                            "且同一目标已经被夹子或钛夹钳明确夹闭后，才能把剪刀动作写成剪断该目标；"
                            "如果只看到剪刀接触但没有已夹闭证据，只能写剪刀在目标附近操作或分离组织，不能写切断。"
                            "已经切断后的同一目标，后续不要再写“正在夹闭”，只能写已夹闭残端。"
                            "- 纱布和棉片是白色片状或团状材料，不要把强反光、水泡、烟雾当作纱布。"
                            "- 双极电凝钳通常有两片可开合钳口，常见蓝色绝缘包覆和金属夹持面；"
                            "电凝钩是单根杆，末端为细小L形钩头。看到蓝色双钳口开合、夹持或分离组织时，"
                            "应写双极电凝钳，不要写电凝钩。"
                            "- 如果看到白色或半透明标本袋/胆囊袋张开、套住胆囊、钳子把胆囊推入袋内，"
                            "summary必须写“将胆囊装入标本袋并准备取出”。"
                            "这类画面不要写成分离胆囊管、分离胆囊板或夹闭胆囊管；Triplet若提示胆囊管分离，"
                            "在装袋画面中应视为弱参考而不是结论。"
                            "- GallbladderRetraction阶段是末期将装有胆囊的标本袋经切口牵拉取出，"
                            "不是早期牵拉胆囊暴露术野；此阶段summary应写标本袋牵拉取出。"
                            "- 出血只关注大量活动性出血、持续涌出、明确出血源或影响视野的持续渗血；"
                            "少量血染、红色组织、陈旧血液、夹闭区局部血迹不算需要上报的活动性出血。"
                            "- 视野状态需要独立判断：如果镜头起雾、烟雾、模糊或水汽遮挡使组织边界不清，"
                            "visibility.status写foggy或blurred，summary写“镜头起雾，手术视野受遮挡”。"
                            "如果同一窗口中雾气从有到无、视野恢复清晰，visibility.fog_cleared=true，"
                            "summary写“雾已去除，腹腔视野恢复”。"
                            "如果画面离开腹腔、出现套管口、腹壁外场景、体外器械台或手术室环境，"
                            "visibility.out_of_body=true，summary写“镜头移出体外，画面切换至套管口或腹壁外场景”。"
                            "注意：套管/镜鞘边缘进入画面但腹腔内肝胆组织仍可见时，不属于移出体外。"
                            "准备或入路建立阶段不要仅凭套管边缘、镜鞘边缘或穿刺器械判断out_of_body。"
                            "- 夹闭、施夹、钛夹释放、剪切发生时，target_structure.label必须在cystic_duct和cystic_artery中二选一。"
                            "视觉证据不足时参考Triplet目标倾向，不要写“管状结构”“或者”和斜线合并对象。"
                            "target_structure.confidence必须反映图像证据，不要复制Triplet置信度；"
                            "如果主要依赖Triplet辅助二选一，confidence不要超过0.45，并在evidence中说明Triplet辅助。"
                            "胆囊管通常较粗、苍白或管腔样并连接胆囊颈；胆囊动脉通常更细、红色血管样或位于血管残端。"
                            "- CVS只在肝胆三角和夹闭切断相关画面判断；三要素为肝胆三角清理、胆囊下三分之一和胆囊板暴露、"
                            "仅胆囊管和胆囊动脉两条结构进入胆囊。证据不足时不要硬判达成。\n"
                            "- 所有操作摘要必须使用主谓宾结构：例如“抓钳牵拉胆囊颈和胆囊体以暴露肝胆三角”、"
                            "“电凝钩分离肝胆三角纤维脂肪组织”、“钛夹钳夹闭胆囊管”。"
                            "禁止写“牵拉胆囊管”“牵拉组织”“相关操作”等笼统或对象错误的表达。\n"
                            "输出要求：只输出一个JSON对象，不要markdown，不要解释。字段如下：\n"
                            "{\n"
                            '  "summary": "1至3句中文事实摘要，依次写清阶段、器械-动作-解剖区域-操作进展，以及出血/CVS/视野等关键状态；禁止只写处理区域或分离组织；没有新增事实才为空字符串",\n'
                            '  "visual": {\n'
                            '    "generic_clip": {"visible": false, "placed": false, "count": 0, "confidence": 0.0},\n'
                            '    "hemolok": {"visible": false, "placed": false, "count": 0, "confidence": 0.0},\n'
                            '    "titanium_clip": {"visible": false, "placed": false, "count": 0, "confidence": 0.0},\n'
                            '    "clip_applier": {"visible": false, "active": false, "confidence": 0.0},\n'
                            '    "scissors": {"visible": false, "cutting": false, "target": "unknown", "confidence": 0.0},\n'
                            f'    "target_structure": {{"label": "{target_hint.get("label")}", "confidence": 0.0, "evidence": ""}},\n'
                            '    "gauze": {"visible": false, "manipulated": false, "confidence": 0.0},\n'
                            '    "bleeding": {"active": false, "severity": "none", "controlled": false, "confidence": 0.0},\n'
                            '    "cvs": {"status": "not_applicable", "confidence": 0.0},\n'
                            '    "visibility": {"status": "clear", "fog": false, "fog_cleared": false, "out_of_body": false, "confidence": 0.0}\n'
                            "  }\n"
                            "}\n"
                            "severity只能是 none、minor、moderate、severe；cvs.status只能是 "
                            "not_applicable、assessing、partial、achieved；visibility.status只能是 "
                            "clear、foggy、blurred、blocked、out_of_body；target_structure.label优先使用 "
                            "cystic_duct或cystic_artery，只有完全没有夹闭和剪切动作时才允许other或unknown。\n\n"
                            f"当前本地阶段：{phase_context or 'Unknown'}\n"
                            f"{out_of_body_focus_line}\n"
                            f"{cvs_focus_line}\n"
                            f"{target_hint_line}\n"
                            f"本地模型弱提示：\n{expert_text[:1000]}"
                        )

                        def _openai_image_url(image: Image.Image) -> str:
                            im = image.convert("RGB")
                            im.thumbnail((max_image_edge, max_image_edge), Image.Resampling.LANCZOS)
                            buffer = BytesIO()
                            im.save(buffer, format="JPEG", quality=64, optimize=True)
                            encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
                            return f"data:image/jpeg;base64,{encoded}"

                        async def _call_openai_vision() -> Dict[str, Any]:
                            api_key_env = open_cfg.get("api_key_env", "OPENAI_API_KEY")
                            api_key = open_cfg.get("api_key") or os.environ.get(api_key_env) or settings.OPENAI_API_KEY
                            if not api_key:
                                return {"success": False, "error": f"{api_key_env} is not configured"}
                            base_url = (
                                open_cfg.get("base_url")
                                or os.environ.get("OPENAI_BASE_URL")
                                or settings.OPENAI_BASE_URL
                                or "https://api.openai.com/v1"
                            ).rstrip("/")
                            openai_model = (
                                open_cfg.get("openai_model_name")
                                or (model_name if provider in {"openai", "gpt", "openai_compatible"} else None)
                                or "gpt-4o-mini"
                            )
                            content = [{"type": "text", "text": question}]
                            for image in sampled:
                                content.append({
                                    "type": "image_url",
                                    "image_url": {"url": _openai_image_url(image), "detail": "low"},
                                })
                            payload = {
                                "model": openai_model,
                                "messages": [
                                    {
                                        "role": "system",
                                        "content": "你是腹腔镜手术视觉审阅专家。必须只输出一个JSON对象，不要markdown。",
                                    },
                                    {"role": "user", "content": content},
                                ],
                                "temperature": temperature,
                                "max_tokens": max_tokens,
                            }
                            if bool(open_cfg.get("response_format_json", True)):
                                payload["response_format"] = {"type": "json_object"}
                            logger.info(
                                "[ModelCall] session=%s window=%s type=open_visual_gpt provider=openai model=%s images=%s",
                                session_id,
                                window_id,
                                openai_model,
                                len(sampled),
                            )
                            transport = str(open_cfg.get("transport", "curl")).lower()
                            if transport == "curl":
                                def quote_curl_config(value: str) -> str:
                                    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'

                                payload_path = ""
                                try:
                                    with tempfile.NamedTemporaryFile(
                                        "w",
                                        encoding="utf-8",
                                        delete=False,
                                        prefix="openvlm_payload_",
                                        suffix=".json",
                                    ) as tmp:
                                        payload_path = tmp.name
                                        os.chmod(payload_path, 0o600)
                                        tmp.write(json.dumps(payload, ensure_ascii=False))
                                    curl_config = "\n".join([
                                        f"url = {quote_curl_config(f'{base_url}/chat/completions')}",
                                        'request = "POST"',
                                        f"max-time = {int(max(1, timeout_s))}",
                                        f"header = {quote_curl_config(f'Authorization: Bearer {api_key}')}",
                                        'header = "Content-Type: application/json"',
                                        f"data-binary = @{payload_path}",
                                    ]) + "\n"
                                    proc = await asyncio.create_subprocess_exec(
                                        "curl",
                                        "-sS",
                                        "-K",
                                        "-",
                                        stdin=asyncio.subprocess.PIPE,
                                        stdout=asyncio.subprocess.PIPE,
                                        stderr=asyncio.subprocess.PIPE,
                                        env=os.environ.copy(),
                                    )
                                    stdout, stderr = await proc.communicate(curl_config.encode("utf-8"))
                                finally:
                                    if payload_path:
                                        try:
                                            os.unlink(payload_path)
                                        except OSError:
                                            pass
                                if proc.returncode != 0:
                                    return {
                                        "success": False,
                                        "error": stderr.decode("utf-8", "ignore")[:1000],
                                        "_external_call_count": 1,
                                        "model": openai_model,
                                    }
                                try:
                                    data = json.loads(stdout.decode("utf-8"))
                                except Exception as exc:
                                    return {
                                        "success": False,
                                        "error": f"OpenAI response is not JSON: {exc}",
                                        "_external_call_count": 1,
                                        "model": openai_model,
                                    }
                            else:
                                trust_env = bool(open_cfg.get("trust_env", True))
                                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                                async with httpx.AsyncClient(timeout=timeout_s, trust_env=trust_env) as client:
                                    response = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
                                    if response.status_code >= 400:
                                        return {
                                            "success": False,
                                            "error": response.text[:1000],
                                            "_external_call_count": 1,
                                            "model": openai_model,
                                        }
                                    data = response.json()
                            if data.get("error"):
                                return {
                                    "success": False,
                                    "error": str(data.get("error"))[:1000],
                                    "_external_call_count": 1,
                                    "model": openai_model,
                                }
                            try:
                                text = data["choices"][0]["message"]["content"]
                            except Exception as exc:
                                return {
                                    "success": False,
                                    "error": f"OpenAI response missing content: {exc}",
                                    "_external_call_count": 1,
                                    "model": openai_model,
                                }
                            return {
                                "success": True,
                                "text": (text or "").strip(),
                                "model": data.get("model") or openai_model,
                                "_external_call_count": 1,
                            }

                        def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
                            cleaned = (text or "").strip()
                            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
                            cleaned = re.sub(r"\s*```$", "", cleaned)

                            def parse(value: str) -> Optional[Dict[str, Any]]:
                                try:
                                    decoded = json.loads(value)
                                    return decoded if isinstance(decoded, dict) else None
                                except Exception:
                                    return None

                            def close_truncated_prefix(value: str) -> str:
                                stack: List[str] = []
                                in_string = False
                                escaped = False
                                for char in value:
                                    if in_string:
                                        if escaped:
                                            escaped = False
                                        elif char == "\\":
                                            escaped = True
                                        elif char == '"':
                                            in_string = False
                                        continue
                                    if char == '"':
                                        in_string = True
                                    elif char in "{[":
                                        stack.append(char)
                                    elif char in "}]":
                                        expected = "{" if char == "}" else "["
                                        if not stack or stack[-1] != expected:
                                            return value
                                        stack.pop()
                                if in_string or not stack:
                                    return value
                                repaired = re.sub(r",\s*$", "", value.rstrip())
                                repaired += "".join("}" if opener == "{" else "]" for opener in reversed(stack))
                                return repaired

                            parsed = parse(cleaned)
                            if parsed is not None:
                                return parsed
                            try:
                                start = cleaned.find("{")
                                end = cleaned.rfind("}")
                                if start >= 0 and end > start:
                                    parsed = parse(cleaned[start:end + 1])
                                    if parsed is not None:
                                        return parsed
                                if start >= 0:
                                    return parse(close_truncated_prefix(cleaned[start:]))
                            except Exception:
                                return None
                            return None

                        def _ensure_visual_defaults(visual: Any) -> Dict[str, Any]:
                            base = {
                                "generic_clip": {"visible": False, "placed": False, "count": 0, "confidence": 0.0},
                                "hemolok": {"visible": False, "placed": False, "count": 0, "confidence": 0.0},
                                "titanium_clip": {"visible": False, "placed": False, "count": 0, "confidence": 0.0},
                                "clip_applier": {"visible": False, "active": False, "confidence": 0.0},
                                "scissors": {"visible": False, "cutting": False, "target": "unknown", "confidence": 0.0},
                                "target_structure": {
                                    "label": target_hint.get("label") or "cystic_duct",
                                    "confidence": float(target_hint.get("confidence") or 0.05),
                                    "evidence": target_hint_line,
                                },
                                "gauze": {"visible": False, "manipulated": False, "confidence": 0.0},
                                "bleeding": {"active": False, "severity": "none", "controlled": False, "confidence": 0.0},
                                "cvs": {"status": "not_applicable", "confidence": 0.0},
                                "visibility": {"status": "clear", "fog": False, "fog_cleared": False, "out_of_body": False, "confidence": 0.0},
                            }
                            if not isinstance(visual, dict):
                                return base
                            aliases = {
                                "hem_o_lok": "hemolok",
                                "hemlok": "hemolok",
                                "hemolock": "hemolok",
                                "clip": "generic_clip",
                                "surgical_clip": "generic_clip",
                                "generic": "generic_clip",
                                "titanium": "titanium_clip",
                                "clip_target": "target_structure",
                                "target": "target_structure",
                                "anatomy": "target_structure",
                                "scissor": "scissors",
                                "cutting": "scissors",
                                "sponge": "gauze",
                                "cotton": "gauze",
                                "view": "visibility",
                                "field": "visibility",
                                "smoke": "visibility",
                                "fog": "visibility",
                                "blur": "visibility",
                                "lens": "visibility",
                                "outside_body": "visibility",
                            }
                            for key, value in list(visual.items()):
                                canonical = aliases.get(key, key)
                                if canonical in base and isinstance(value, dict):
                                    base[canonical].update(value)
                            if is_preparation_context:
                                base["generic_clip"] = {"visible": False, "placed": False, "count": 0, "confidence": 0.0}
                                base["hemolok"] = {"visible": False, "placed": False, "count": 0, "confidence": 0.0}
                                base["titanium_clip"] = {"visible": False, "placed": False, "count": 0, "confidence": 0.0}
                                base["clip_applier"] = {"visible": False, "active": False, "confidence": 0.0}
                                base["scissors"] = {"visible": False, "cutting": False, "target": "unknown", "confidence": 0.0}
                            target = base.get("target_structure") or {}
                            raw_label = str(target.get("label") or "").strip().lower()
                            forced_label = _target_label_from_raw(raw_label)
                            has_clip_context = any(
                                bool((base.get(k) or {}).get(flag))
                                for k, flag in (
                                    ("hemolok", "visible"),
                                    ("hemolok", "placed"),
                                    ("titanium_clip", "visible"),
                                    ("titanium_clip", "placed"),
                                    ("generic_clip", "visible"),
                                    ("generic_clip", "placed"),
                                    ("clip_applier", "visible"),
                                    ("clip_applier", "active"),
                                )
                            )
                            scissors = base.get("scissors") or {}
                            scissors_target = _target_label_from_raw(scissors.get("target"))
                            has_cut_context = bool(
                                (scissors.get("cutting") or scissors.get("visible"))
                                and _safe_float(scissors.get("confidence"), 0.0) >= 0.35
                            )
                            if scissors_target:
                                target["label"] = scissors_target
                            elif forced_label:
                                target["label"] = forced_label
                            elif raw_label in {"other", "unknown", "", "cystic_duct_or_artery_uncertain"} or has_clip_context or has_cut_context:
                                target["label"] = target_hint.get("label") or "cystic_duct"
                                target["confidence"] = max(
                                    _safe_float(target.get("confidence"), 0.0),
                                    float(target_hint.get("confidence") or 0.05),
                                )
                                evidence = str(target.get("evidence") or "").strip()
                                target["evidence"] = evidence or target_hint_line
                            target_evidence = str(target.get("evidence") or "")
                            if (
                                _safe_float(target.get("confidence"), 0.0) > 0.45
                                and re.search(r"Triplet|本地模型弱提示|weak hint", target_evidence, re.IGNORECASE)
                                and not re.search(
                                    r"粗|苍白|管腔|胆囊颈|细小|红色|血管样|走行|morpholog|visual",
                                    target_evidence,
                                    re.IGNORECASE,
                                )
                            ):
                                target["confidence"] = 0.45
                            base["target_structure"] = target
                            cvs = base.get("cvs") or {}
                            cvs_status = str(cvs.get("status") or "not_applicable").strip().lower()
                            cvs_confidence = _safe_float(cvs.get("confidence"), 0.0)
                            if cvs_phase_context:
                                if cvs_status == "not_applicable":
                                    cvs.update({"status": "assessing", "confidence": max(cvs_confidence, 0.45)})
                                elif cvs_status == "achieved" and cvs_confidence < 0.85:
                                    cvs.update({"status": "partial", "confidence": cvs_confidence})
                            elif cvs_status not in {"not_applicable", "assessing", "partial", "achieved"}:
                                cvs.update({"status": "not_applicable", "confidence": 0.0})
                            base["cvs"] = cvs
                            visibility = base.get("visibility") or {}
                            raw_status = str(visibility.get("status") or "clear").strip().lower()
                            status_aliases = {
                                "normal": "clear",
                                "ok": "clear",
                                "cleared": "clear",
                                "fog": "foggy",
                                "smoke": "foggy",
                                "smoky": "foggy",
                                "mist": "foggy",
                                "misty": "foggy",
                                "hazy": "blurred",
                                "outside": "out_of_body",
                                "extracorporeal": "out_of_body",
                                "operating_room": "out_of_body",
                                "or_scene": "out_of_body",
                            }
                            visibility["status"] = status_aliases.get(raw_status, raw_status)
                            if visibility["status"] not in {"clear", "foggy", "blurred", "blocked", "out_of_body"}:
                                visibility["status"] = "clear"
                            if visibility["status"] in {"foggy", "blurred", "blocked"}:
                                visibility["fog"] = True
                            if visibility["status"] == "out_of_body":
                                visibility["out_of_body"] = True
                            if visibility.get("out_of_body"):
                                local_confirmed = bool(local_visibility_cue.get("out_of_body"))
                                evidence_text = " ".join(
                                    str(visibility.get(key) or "")
                                    for key in ("evidence", "reason", "description")
                                )
                                external_terms = re.search(
                                    r"(手术室|器械台|体外器械|皮肤|腹壁外皮肤|operating room|or scene|"
                                    r"outside[- ]the[- ]body|extra[- ]abdominal|extracorporeal)",
                                    evidence_text,
                                    re.IGNORECASE,
                                )
                                packaging_context = phase_context == "GallbladderPackaging"
                                weak_preparation_transition = (
                                    is_preparation_context
                                    and not local_confirmed
                                    and not external_terms
                                    and not packaging_context
                                )
                                weak_packaging_transition = (
                                    packaging_context
                                    and not local_confirmed
                                    and not external_terms
                                    and (
                                        _safe_float(local_visibility_cue.get("inner_tissue"), 1.0) > 0.08
                                        or _safe_float(visibility.get("confidence"), 0.0) < 0.9
                                    )
                                )
                                if weak_preparation_transition or weak_packaging_transition:
                                    visibility.update({
                                        "status": "clear",
                                        "out_of_body": False,
                                        "fog": False,
                                        "confidence": min(_safe_float(visibility.get("confidence"), 0.0), 0.3),
                                        "downgraded_reason": (
                                            "weak_packaging_transition_with_intra_abdominal_view"
                                            if weak_packaging_transition
                                            else "trocar_or_scope_edge_visible_during_preparation_with_intra_abdominal_view"
                                        ),
                                    })
                            base["visibility"] = visibility
                            return base

                        def _target_structure_text(
                            visual: Dict[str, Any],
                            require_visual_confidence: bool = False,
                        ) -> str:
                            target = visual.get("target_structure") or {}
                            label = str(target.get("label") or "unknown").strip().lower()
                            if require_visual_confidence and _safe_float(target.get("confidence"), 0.0) < 0.55:
                                return ""
                            forced_label = _target_label_from_raw(label) or target_hint.get("label") or "cystic_duct"
                            return _target_cn(forced_label)

                        def _apply_target_structure_to_summary(summary: str, visual: Dict[str, Any]) -> str:
                            text = str(summary or "")
                            if not text:
                                return text
                            target = visual.get("target_structure") or {}
                            label = _target_label_from_raw(target.get("label")) or target_hint.get("label") or "cystic_duct"
                            return _sanitize_target_language(text, label)

                        def _visual_fallback_summary(visual: Dict[str, Any]) -> str:
                            parts: List[str] = []
                            clip_flags = _clip_display_flags(visual)
                            hemolok = visual.get("hemolok") or {}
                            titanium = visual.get("titanium_clip") or {}
                            applier = visual.get("clip_applier") or {}
                            scissors = visual.get("scissors") or {}
                            gauze = visual.get("gauze") or {}
                            bleeding = visual.get("bleeding") or {}
                            cvs = visual.get("cvs") or {}
                            visibility = _visibility_flags_from_visual(visual)
                            hem_count = clip_flags["hem_count"]
                            titanium_count = clip_flags["titanium_count"]
                            generic_count = clip_flags.get("generic_count", 0)
                            if visibility["out_of_body"]:
                                return "镜头移出体外，画面切换至套管口或腹壁外场景。"
                            if visibility["fog_active"]:
                                parts.append("镜头起雾，手术视野受遮挡")
                            elif visibility["fog_resolved"]:
                                parts.append("雾已去除，腹腔视野恢复")
                            if clip_flags["clip_visible"]:
                                clip_target = _target_structure_text(visual, require_visual_confidence=True)
                                parts.append(
                                    f"可见夹子已夹闭{clip_target}"
                                    if clip_target else
                                    "可见已释放夹子"
                                )
                            if clip_flags["applier_active"]:
                                parts.append(f"钛夹钳正在夹闭{_target_structure_text(visual)}")
                            elif (
                                applier.get("visible")
                                and _safe_float(applier.get("confidence"), 0.0) >= 0.80
                            ):
                                parts.append("钛夹钳在操作区域内调整")
                            if (
                                scissors.get("cutting")
                                and _safe_float(scissors.get("confidence"), 0.0) >= 0.45
                                and not is_preparation_context
                            ):
                                scissors_target = _target_label_from_raw(scissors.get("target"))
                                target_text = _target_cn(scissors_target) if scissors_target else _target_structure_text(visual)
                                parts.append(f"剪刀正在剪断{target_text}")
                            elif (
                                scissors.get("visible")
                                and _safe_float(scissors.get("confidence"), 0.0) >= 0.75
                                and not is_preparation_context
                            ):
                                parts.append("剪刀在操作区域内活动")
                            if gauze.get("visible") or gauze.get("manipulated"):
                                parts.append("可见纱布和棉片用于局部压迫或清理")
                            severity = str(bleeding.get("severity") or "minor").lower()
                            significant_bleeding = bool(bleeding.get("active")) and severity == "severe"
                            if significant_bleeding:
                                severity = str(bleeding.get("severity") or "minor")
                                sev_cn = {"minor": "少量", "moderate": "中等量", "severe": "大量"}.get(severity, "")
                                parts.append(f"可见{sev_cn}活动性出血")
                            elif bleeding.get("controlled") and severity == "severe":
                                parts.append("出血已控制")
                            cvs_status = str(cvs.get("status") or "not_applicable")
                            cvs_confidence = _safe_float(cvs.get("confidence"), 0.0)
                            if cvs_status == "achieved" and cvs_confidence >= 0.85:
                                parts.append("CVS三要素已基本达成")
                            elif cvs_status in {"assessing", "partial", "achieved"}:
                                parts.append("CVS仍在评估中")
                            return "；".join(parts).strip("；") + ("。" if parts else "")

                        def _clip_display_flags(visual: Dict[str, Any]) -> Dict[str, Any]:
                            hemolok = visual.get("hemolok") or {}
                            titanium = visual.get("titanium_clip") or {}
                            generic = visual.get("generic_clip") or {}
                            applier = visual.get("clip_applier") or {}
                            target = visual.get("target_structure") or {}
                            hem_count = int(hemolok.get("count") or 0)
                            titanium_count = int(titanium.get("count") or 0)
                            generic_count = int(generic.get("count") or 0)
                            hem_conf = _safe_float(hemolok.get("confidence"), 0.0)
                            titanium_conf = _safe_float(titanium.get("confidence"), 0.0)
                            generic_conf = _safe_float(generic.get("confidence"), 0.0)
                            applier_conf = _safe_float(applier.get("confidence"), 0.0)
                            focused_clip_action = _focused_clip_action_confirmed(visual)
                            target_evidence = str(target.get("evidence") or "")
                            target_conf = _safe_float(target.get("confidence"), 0.0)
                            target_label = _target_label_from_raw(target.get("label")) or target_hint.get("label") or "cystic_duct"
                            evidence_active = bool(
                                re.search(r"(?:钛夹钳|施夹器).{0,10}(?:夹住|夹闭|释放|闭合)", target_evidence)
                            )
                            hem_visible = bool(
                                hemolok.get("placed")
                                and (hem_conf >= 0.35 or hem_count > 0)
                            )
                            titanium_visible = bool(
                                titanium.get("placed")
                                and (titanium_conf >= 0.35 or titanium_count > 0)
                            )
                            generic_visible = bool(
                                generic.get("placed")
                                and (generic_conf >= 0.35 or generic_count > 0)
                            )
                            if hem_visible and titanium_visible:
                                same_or_unknown_count = (
                                    hem_count == titanium_count
                                    or hem_count == 0
                                    or titanium_count == 0
                                )
                                if same_or_unknown_count and hem_conf >= titanium_conf - 0.08:
                                    titanium_visible = False
                            if is_preparation_context:
                                hem_visible = False
                                titanium_visible = False
                                generic_visible = False
                            hem_seen_high_conf = bool(
                                hemolok.get("visible")
                                and hem_conf >= 0.80
                                and hem_count > 0
                            )
                            clip_seen_mid_conf = bool(
                                hemolok.get("visible")
                                and hem_conf >= 0.60
                                and hem_count > 0
                            )
                            strong_artery_context = bool(
                                target_label != "cystic_artery"
                                or applier_conf >= 0.85
                                or hem_visible
                                or titanium_visible
                                or generic_visible
                                or local_clipper_seen
                                or phase_context == "ClippingCutting"
                            )
                            evidence_active_allowed = bool(evidence_active and strong_artery_context)
                            applier_context = bool(
                                phase_context == "ClippingCutting"
                                or local_clipper_seen
                                or hem_visible
                                or titanium_visible
                                or generic_visible
                                or (hem_seen_high_conf and applier_conf >= 0.75)
                                or (clip_seen_mid_conf and applier_conf >= 0.90)
                                or evidence_active_allowed
                                or focused_clip_action
                            )
                            applier_active = bool(
                                applier.get("active")
                                and (applier_conf >= 0.75 or (evidence_active_allowed and applier_conf >= 0.55))
                                and applier_context
                                and (
                                    target_conf >= 0.35
                                    or hem_visible
                                    or titanium_visible
                                    or generic_visible
                                    or local_clipper_seen
                                    or phase_context == "ClippingCutting"
                                    or focused_clip_action
                                )
                                and not is_preparation_context
                            )
                            return {
                                "hem_visible": hem_visible,
                                "titanium_visible": titanium_visible,
                                "generic_visible": generic_visible,
                                "clip_visible": bool(hem_visible or titanium_visible or generic_visible),
                                "applier_active": applier_active,
                                "hem_count": hem_count,
                                "titanium_count": titanium_count,
                                "generic_count": generic_count,
                            }

                        def _has_structured_visual_evidence(visual: Dict[str, Any]) -> bool:
                            clip_flags = _clip_display_flags(visual)
                            applier = visual.get("clip_applier") or {}
                            scissors = visual.get("scissors") or {}
                            gauze = visual.get("gauze") or {}
                            bleeding = visual.get("bleeding") or {}
                            cvs = visual.get("cvs") or {}
                            visibility = _visibility_flags_from_visual(visual)
                            return bool(
                                clip_flags["clip_visible"]
                                or clip_flags["applier_active"]
                                or (
                                    applier.get("visible")
                                    and _safe_float(applier.get("confidence"), 0.0) >= 0.80
                                )
                                or (
                                    (
                                        scissors.get("cutting")
                                        and _safe_float(scissors.get("confidence"), 0.0) >= 0.45
                                    )
                                    or (
                                        scissors.get("visible")
                                        and _safe_float(scissors.get("confidence"), 0.0) >= 0.75
                                    )
                                )
                                or gauze.get("visible")
                                or gauze.get("manipulated")
                                or (bleeding.get("active") and str(bleeding.get("severity") or "").lower() == "severe")
                                or cvs.get("status") in {"partial", "achieved"}
                                or visibility["fog_active"]
                                or visibility["fog_resolved"]
                                or visibility["out_of_body"]
                            )

                        def _filter_visual_summary_noise(summary: str, visual: Dict[str, Any]) -> str:
                            text = str(summary or "").strip()
                            if not text:
                                return ""
                            text = re.sub(r"可见\s*\d+\s*枚夹子", "可见夹子", text)
                            # Do not surface negative visual checklists in the live panel.
                            negative_patterns = (
                                r"[，,；;。]?\s*当前处于[A-Za-z]+阶段，?未见明显夹闭或出血",
                                r"[，,；;。]?\s*未见明显夹闭或出血",
                                r"[，,；;。]?\s*纱布未见",
                                r"[，,；;。]?\s*未见纱布",
                                r"[，,；;。]?\s*未见(?:明显)?(?:活动性)?出血",
                                r"[，,；;。]?\s*未见夹闭或施夹动作",
                                r"[，,；;。]?\s*未见夹闭动作",
                                r"[，,；;。]?\s*未见夹闭器具",
                                r"[，,；;。]?\s*未见",
                                r"[，,；;。]?\s*尚未确认止血操作",
                                r"[，,；;。]?\s*可见双极器械接触组织，尚未确认止血操作",
                                r"[，,；;。]?\s*视野清晰",
                            )
                            for pattern in negative_patterns:
                                text = re.sub(pattern, "", text)
                            clip_flags = _clip_display_flags(visual)
                            applier = visual.get("clip_applier") or {}
                            applier_visible = bool(
                                applier.get("visible")
                                and _safe_float(applier.get("confidence"), 0.0) >= 0.80
                            )
                            if is_preparation_context or not (
                                clip_flags["clip_visible"]
                                or clip_flags["applier_active"]
                                or applier_visible
                            ):
                                clipping_patterns = (
                                    r"[，,；;。]?\s*可见\d*枚?夹子已(?:夹闭|闭合)(?:胆囊管|胆囊动脉)(?:残端)?",
                                    r"[，,；;。]?\s*(?:胆囊管|胆囊动脉)残端已由夹子(?:闭合|夹闭)",
                                    r"[，,；;。]?\s*(?:胆囊管|胆囊动脉)残端已夹闭",
                                    r"[，,；;。]?\s*(?:使用)?夹子(?:夹闭|闭合)(?:胆囊管|胆囊动脉)",
                                    r"[，,；;。]?\s*可见\d*枚?Hem-o-lok夹夹闭(?:胆囊管|胆囊动脉)",
                                    r"[，,；;。]?\s*可见\d*枚?金属钛夹夹闭(?:胆囊管|胆囊动脉)",
                                    r"[，,；;。]?\s*使用Hem-o-lok夹闭合(?:胆囊管|胆囊动脉)",
                                    r"[，,；;。]?\s*使用金属钛夹闭合(?:胆囊管|胆囊动脉)",
                                    r"[，,；;。]?\s*(?:胆囊管|胆囊动脉)残端已由(?:Hem-o-lok夹|金属钛夹)闭合",
                                    r"[，,；;。]?\s*钛夹钳(?:正在)?夹闭(?:胆囊管|胆囊动脉)",
                                    r"[，,；;。]?\s*看到钛夹钳活动，?正在夹闭(?:胆囊管|胆囊动脉)",
                                    r"[，,；;。]?\s*看到.{0,8}钛夹钳.{0,8}夹闭(?:胆囊管|胆囊动脉)",
                                    r"[，,；;。]?\s*(?:观察到|可见)?(?:疑似)?夹闭(?:胆囊管|胆囊动脉)的?活动?",
                                    r"[，,；;。]?\s*(?:观察到|可见)?(?:疑似)?夹体，?(?:可能)?夹闭(?:胆囊管|胆囊动脉)",
                                    r"[，,；;。]?\s*(?:可见|看到)(?:疑似)?Hem-o-lok夹和金属钛夹(?:样)?(?:亮白)?夹体，?提示夹闭处理",
                                    r"[，,；;。]?\s*(?:可见|看到)(?:疑似)?Hem-o-lok夹和金属钛夹，?提示夹闭处理",
                                    r"[，,；;。]?\s*可见疑似夹闭处理，?钛夹钳活动，?目标为(?:胆囊管|胆囊动脉)",
                                    r"[，,；;。]?\s*钛夹钳活动，?目标为(?:胆囊管|胆囊动脉)",
                                    r"[，,；;。]?\s*当前处于[A-Za-z]+阶段明显夹闭或出血",
                                    r"[，,；;。]?\s*明显夹闭或出血",
                                )
                                for pattern in clipping_patterns:
                                    text = re.sub(pattern, "", text)
                                text = re.sub(
                                    r"[^。；;\n]*?(?:Hem-o-lok|金属钛夹|钛夹钳|施夹|夹闭处理|夹体)[^。；;\n]*(?:[。；;]|$)",
                                    "",
                                    text,
                                    flags=re.IGNORECASE,
                                )

                            bleeding = visual.get("bleeding") or {}
                            severity = str(bleeding.get("severity") or "none").lower()
                            if not bleeding.get("active") and not bleeding.get("controlled"):
                                for pattern in (
                                    r"[，,；;。]?\s*(?:正在)?进行凝血处理",
                                    r"[，,；;。]?\s*双极电凝(?:正在)?(?:凝血|止血)(?:处理)?(?:局部|出血点|组织)?",
                                    r"[，,；;。]?\s*(?:正在)?进行止血处理",
                                    r"[，,；;。]?\s*出血已控制",
                                ):
                                    text = re.sub(pattern, "", text)
                            # The UI alert should focus on clinically important
                            # active bleeding. Minor oozing/old blood should not
                            # produce a red event or dominate the window summary.
                            if severity != "severe":
                                minor_patterns = (
                                    r"[，,；;。]?\s*(?:观察到)?活动性出血(?:状态|存在)?",
                                    r"[，,；;。]?\s*(?:观察到)?中等(?:量)?活动性出血",
                                    r"[，,；;。]?\s*活动性出血被控制",
                                    r"[，,；;。]?\s*可见少量活动性出血",
                                    r"[，,；;。]?\s*少量活动性出血",
                                )
                                for pattern in minor_patterns:
                                    text = re.sub(pattern, "", text)
                                if not bleeding.get("controlled"):
                                    # The live panel intentionally reports only
                                    # clinically important active bleeding. Drop
                                    # model prose that turns minor blood staining
                                    # into an invented hemostasis action/product.
                                    text = re.sub(
                                        r"[^。；;\n]*(?:少量出血|轻微出血|出血的|准备止血|止血夹)[^。；;\n]*(?:[。；;]|$)",
                                        "",
                                        text,
                                    )
                            text = re.sub(r"[，,；;]\s*[，,；;]+", "，", text)
                            text = re.sub(r"^[，,；;。]+|[，,；;。]+$", "", text)
                            text = re.sub(r"^(?:看到|可见)?[，,]?\s*且有(?:明显)?$", "", text)
                            if text in {"看到明显", "看到", "且有", "可见明显", "可见"}:
                                return ""
                            return text.strip()

                        def _has_clip_review_context(visual: Dict[str, Any]) -> bool:
                            if phase_context not in {
                                "CalotTriangleDissection",
                                "ClippingCutting",
                                "GallbladderDissection",
                            }:
                                return False
                            clip_detector = (expert_pack or {}).get("clip_detector") or {}
                            if int(clip_detector.get("detections_total") or 0) > 0:
                                return True
                            if local_clipper_seen:
                                return True
                            applier = visual.get("clip_applier") or {}
                            titanium = visual.get("titanium_clip") or {}
                            hemolok = visual.get("hemolok") or {}
                            generic_clip = visual.get("generic_clip") or {}
                            return bool(
                                applier.get("visible")
                                or applier.get("active")
                                or titanium.get("visible")
                                or titanium.get("placed")
                                or hemolok.get("visible")
                                or hemolok.get("placed")
                                or generic_clip.get("visible")
                                or generic_clip.get("placed")
                            )

                        def _should_run_clip_vlm_review(visual: Dict[str, Any]) -> bool:
                            review_cfg = config.get("services", {}).get("clip_vlm_review", {})
                            if not review_cfg.get("enabled", False):
                                return False
                            if review_cfg.get("trigger_on_clip_context_only", True) and not _has_clip_review_context(visual):
                                return False
                            hemolok = visual.get("hemolok") or {}
                            generic_clip = visual.get("generic_clip") or {}
                            hem_conf = _safe_float(hemolok.get("confidence"), 0.0)
                            hem_count = int(hemolok.get("count") or 0)
                            generic_conf = _safe_float(generic_clip.get("confidence"), 0.0)
                            generic_count = int(generic_clip.get("count") or 0)
                            # Always review a claimed deployed clip. The primary
                            # VLM can confuse an applier jaw or glare with a clip,
                            # so its confidence must not bypass the dedicated veto.
                            return True

                        async def _run_clip_vlm_review(visual: Dict[str, Any]) -> Dict[str, Any]:
                            review_cfg = config.get("services", {}).get("clip_vlm_review", {})
                            base_url = str(review_cfg.get("base_url") or "").rstrip("/")
                            model = str(review_cfg.get("model_name") or "InternVL3.5-2B")
                            if not base_url:
                                return {"success": False, "error": "clip_vlm_review.base_url is empty"}
                            max_review_images = int(review_cfg.get("max_images", 2) or 2)
                            source_images = list(images or sampled or [])
                            detector_frames = list(
                                (((expert_pack or {}).get("clip_detector") or {}).get("per_frame") or [])
                            )
                            detector_sampled_images = (
                                _uniform_sample(source_images, len(detector_frames))
                                if detector_frames else []
                            )
                            detector_hit_images = []
                            for frame_index, detections in enumerate(detector_frames):
                                if frame_index >= len(detector_sampled_images):
                                    break
                                if any(isinstance(det, dict) for det in (detections or [])):
                                    detector_hit_images.append(detector_sampled_images[frame_index])
                            if detector_hit_images:
                                # The deployed clip can be visible for only a
                                # few frames. Review those exact detector hits;
                                # the VLM still vetoes glare/applier-jaw false
                                # positives using the morphology prompt below.
                                review_images = _uniform_sample(detector_hit_images, max_review_images)
                                image_selection = "clip_detector_frames"
                            else:
                                review_images = _uniform_sample(sampled, max_review_images)
                                image_selection = "uniform_window_frames"
                            if not review_images:
                                return {"success": False, "error": "no review images"}
                            prompt = (
                                "These chronological full laparoscopic cholecystectomy frames are from one "
                                "five-second interval. Classify the candidate by visible morphology only; ignore "
                                "the phase and all other model labels. A deployed clip is a small independent "
                                "C/U/locking body clamped on tissue. White, ivory, purple, blue or green polymer "
                                "clips and small silver/gray metallic clips are all classification=clip. A clip "
                                "applier has a thick shaft and two broad, nearly parallel jaws; applier_active is "
                                "true only when the jaws visibly surround or clamp a tubular structure, or release "
                                "a clip. Scissors have two thin sharp crossing blades that open or close in a V. "
                                "An electrocautery hook has one shaft ending in one small L-shaped hook, often with "
                                "white ceramic insulation. A grasper has blunt, toothed or fenestrated jaws. Do not "
                                "classify a long white instrument tip, suction tip, trocar, applier jaw or glare as "
                                "a deployed clip. When active clipping is visible, also classify the target: the "
                                "cystic duct is usually the thicker pale duct continuous with the gallbladder neck; "
                                "the cystic artery is a thinner red/pink vascular branch. Use unknown when the target "
                                "cannot be distinguished. Inspect every frame and return only JSON: "
                                "{\"classification\":\"clip|clip_applier|scissors|electrocautery_hook|grasper|"
                                "no_clip|glare_or_instrument|other\",\"confidence\":0.0,\"count\":0,"
                                "\"independent_from_instrument\":false,\"clamped_on_tissue\":false,"
                                "\"applier_active\":false,\"scissors_cutting\":false,"
                                "\"target\":\"cystic_duct|cystic_artery|unknown\",\"target_confidence\":0.0,"
                                "\"reason\":\"short morphology evidence\"}."
                            )
                            content = [{"type": "text", "text": prompt}]
                            for image in review_images:
                                content.append({
                                    "type": "image_url",
                                    "image_url": {"url": _openai_image_url(image), "detail": "low"},
                                })
                            payload = {
                                "model": model,
                                "messages": [
                                    {"role": "system", "content": "Return one compact JSON object only."},
                                    {"role": "user", "content": content},
                                ],
                                "temperature": float(review_cfg.get("temperature", 0.0) or 0.0),
                                "max_tokens": int(review_cfg.get("max_tokens", 180) or 180),
                            }
                            try:
                                async with httpx.AsyncClient(timeout=float(review_cfg.get("timeout", 5.0) or 5.0), trust_env=False) as client:
                                    response = await client.post(f"{base_url}/chat/completions", json=payload)
                                if response.status_code >= 400:
                                    return {
                                        "success": False,
                                        "error": f"HTTP {response.status_code}: {response.text[:500]}",
                                        "model": model,
                                    }
                                data = response.json()
                                text = str(data["choices"][0]["message"]["content"] or "").strip()
                                parsed = _parse_json_object(text) or {}
                                classification = str(parsed.get("classification") or parsed.get("label") or "").strip().lower()
                                return {
                                    "success": True,
                                    "classification": classification,
                                    "confidence": _safe_float(parsed.get("confidence"), 0.0),
                                    "count": int(parsed.get("count") or 0),
                                    "independent_from_instrument": bool(parsed.get("independent_from_instrument", False)),
                                    "clamped_on_tissue": bool(parsed.get("clamped_on_tissue", False)),
                                    "applier_active": bool(parsed.get("applier_active", False)),
                                    "scissors_cutting": bool(parsed.get("scissors_cutting", False)),
                                    "target": str(parsed.get("target") or "unknown").strip().lower(),
                                    "target_confidence": _safe_float(parsed.get("target_confidence"), 0.0),
                                    "reason": str(parsed.get("reason") or "")[:240],
                                    "raw": text[:800],
                                    "model": data.get("model") or model,
                                    "images": len(review_images),
                                    "image_selection": image_selection,
                                }
                            except Exception as exc:
                                return {"success": False, "error": f"{type(exc).__name__}: {exc}"[:500], "model": model}

                        async def _apply_clip_vlm_review(visual: Dict[str, Any]) -> Dict[str, Any]:
                            if not _should_run_clip_vlm_review(visual):
                                return visual
                            review = await _run_clip_vlm_review(visual)
                            visual["clip_secondary_review"] = review
                            if not review.get("success"):
                                logger.warning(f"[ClipVLMReview] Window {window_id} failed: {review.get('error')}")
                                return visual
                            min_conf = _safe_float(
                                config.get("services", {}).get("clip_vlm_review", {}).get("min_confidence"),
                                0.55,
                            )
                            pred = str(review.get("classification") or "").lower()
                            conf = _safe_float(review.get("confidence"), 0.0)
                            count = int(review.get("count") or 0)
                            position_confirmed = bool(
                                review.get("independent_from_instrument")
                                or review.get("clamped_on_tissue")
                            )
                            if (
                                pred in {"clip", "titanium_clip", "hemolok_clip"}
                                and conf >= min_conf
                                and count >= 1
                                and position_confirmed
                            ):
                                generic_clip = visual.get("generic_clip") or {}
                                generic_clip.update({
                                    "visible": True,
                                    "placed": True,
                                    "count": count,
                                    "confidence": max(_safe_float(generic_clip.get("confidence"), 0.0), conf),
                                    "secondary_review": True,
                                })
                                visual["generic_clip"] = generic_clip
                            elif pred == "clip_applier" and conf >= 0.80:
                                applier_state = visual.get("clip_applier") or {}
                                applier_state.update({
                                    "visible": True,
                                    "active": bool(
                                        review.get("applier_active")
                                        or review.get("clamped_on_tissue")
                                    ),
                                    "confidence": max(
                                        _safe_float(applier_state.get("confidence"), 0.0),
                                        conf,
                                    ),
                                    "secondary_review": True,
                                })
                                visual["clip_applier"] = applier_state
                                for key in ("generic_clip", "hemolok", "titanium_clip"):
                                    clip_state = visual.get(key) or {}
                                    clip_state.update({
                                        "visible": False,
                                        "placed": False,
                                        "count": 0,
                                        "confidence": 0.0,
                                        "rejected_by_secondary_review": True,
                                    })
                                    visual[key] = clip_state
                            elif pred == "scissors" and conf >= 0.80:
                                scissors_state = visual.get("scissors") or {}
                                scissors_state.update({
                                    "visible": True,
                                    "cutting": bool(review.get("scissors_cutting")),
                                    "confidence": max(
                                        _safe_float(scissors_state.get("confidence"), 0.0),
                                        conf,
                                    ),
                                    "secondary_review": True,
                                })
                                visual["scissors"] = scissors_state
                                applier_state = visual.get("clip_applier") or {}
                                applier_state.update({
                                    "visible": False,
                                    "active": False,
                                    "rejected_by_secondary_review": True,
                                })
                                visual["clip_applier"] = applier_state
                            elif conf >= 0.80 and pred in {
                                "no_clip",
                                "glare_or_instrument",
                                "instrument",
                                "glare",
                                "electrocautery_hook",
                                "grasper",
                                "other",
                            }:
                                for key in ("generic_clip", "hemolok", "titanium_clip"):
                                    clip_state = visual.get(key) or {}
                                    clip_state.update({
                                        "visible": False,
                                        "placed": False,
                                        "count": 0,
                                        "confidence": 0.0,
                                        "rejected_by_secondary_review": True,
                                    })
                                    visual[key] = clip_state
                                applier_state = visual.get("clip_applier") or {}
                                applier_state.update({
                                    "visible": False,
                                    "active": False,
                                    "rejected_by_secondary_review": True,
                                })
                                visual["clip_applier"] = applier_state

                            review_target = _target_label_from_raw(review.get("target"))
                            review_target_confidence = _safe_float(
                                review.get("target_confidence"),
                                0.0,
                            )
                            if (
                                review_target
                                and review_target_confidence >= 0.65
                                and pred in {"clip", "titanium_clip", "hemolok_clip", "clip_applier"}
                                and (
                                    pred != "clip_applier"
                                    or review.get("applier_active")
                                    or review.get("clamped_on_tissue")
                                )
                            ):
                                target_state = visual.get("target_structure") or {}
                                target_state.update({
                                    "label": review_target,
                                    "confidence": review_target_confidence,
                                    "evidence": review.get("reason", ""),
                                    "evidence_source": "clip_secondary_review",
                                })
                                visual["target_structure"] = target_state
                            return visual

                        def _should_run_scissors_vlm_review(visual: Dict[str, Any]) -> bool:
                            review_cfg = config.get("services", {}).get("scissors_vlm_review", {})
                            if not review_cfg.get("enabled", False) or is_preparation_context:
                                return False
                            morphology_review = visual.get("clip_secondary_review") or {}
                            if (
                                morphology_review.get("success")
                                and _safe_float(morphology_review.get("confidence"), 0.0) >= 0.80
                                and str(morphology_review.get("classification") or "").lower() in {
                                    "clip_applier",
                                    "scissors",
                                    "electrocautery_hook",
                                    "grasper",
                                }
                            ):
                                return False
                            scissors = visual.get("scissors") or {}
                            # This is a verifier, not a scissors discovery call.
                            # Running it on every clipping window made applier
                            # jaws look like scissors and added one VLM request
                            # even when neither primary source proposed scissors.
                            return bool(
                                local_scissors_candidate
                                or scissors.get("visible")
                                or scissors.get("cutting")
                            )

                        async def _run_scissors_vlm_review() -> Dict[str, Any]:
                            review_cfg = config.get("services", {}).get("scissors_vlm_review", {})
                            base_url = str(review_cfg.get("base_url") or "").rstrip("/")
                            model = str(review_cfg.get("model_name") or "Qwen3-VL-8B-Instruct")
                            if not base_url:
                                return {"success": False, "error": "scissors_vlm_review.base_url is empty"}
                            max_review_images = int(review_cfg.get("max_images", 3) or 3)
                            source_images = list(images or sampled or [])
                            yolo_per_frame = list(
                                (((expert_pack or {}).get("yolo") or {}).get("per_frame") or [])
                            )
                            yolo_sampled_images = (
                                _uniform_sample(source_images, len(yolo_per_frame))
                                if yolo_per_frame else []
                            )
                            yolo_scissors_images = []
                            for frame_index, detections in enumerate(yolo_per_frame):
                                if frame_index >= len(yolo_sampled_images):
                                    break
                                if any(
                                    str(det.get("label") or det.get("raw_label") or "").lower() == "scissors"
                                    or str(det.get("raw_label") or "").lower() == "scissors"
                                    for det in (detections or [])
                                    if isinstance(det, dict)
                                ):
                                    yolo_scissors_images.append(yolo_sampled_images[frame_index])

                            # When scissors are brief, uniformly sampling the
                            # whole five-second window often sends two frames
                            # after the tool has left and makes the VLM vote by
                            # majority. Keep full frames, but prioritize the
                            # exact frames where the local detector saw the
                            # candidate so morphology can be reviewed.
                            if yolo_scissors_images:
                                review_images = _uniform_sample(
                                    yolo_scissors_images,
                                    max_review_images,
                                )
                                image_selection = "yolo_scissors_frames"
                            elif len(source_images) <= max_review_images:
                                review_images = source_images
                                image_selection = "all_frames"
                            elif max_review_images == 3:
                                # Avoid the exact window boundaries, where a
                                # previous or next instrument often dominates.
                                review_images = [
                                    source_images[round((len(source_images) - 1) * fraction)]
                                    for fraction in (0.20, 0.60, 0.90)
                                ]
                                image_selection = "window_fractions"
                            else:
                                review_images = _uniform_sample(source_images, max_review_images)
                                image_selection = "uniform"
                            if not review_images:
                                return {"success": False, "error": "no review images"}
                            prompt = (
                                "你是腹腔镜手术器械复核器。下面多张全图来自同一个5秒窗口。"
                                "只判断主要操作器械，不参考手术阶段和其他模型。\n"
                                "剪刀：有两片细长、尖锐、相互对合的金属刀刃，铰链后张开形成V形，"
                                "可见开合或夹剪组织。\n"
                                "电凝钩：单根杆，末端常有白色陶瓷绝缘头和一个细小弯钩，"
                                "不存在两片对合刀刃。\n"
                                "抓钳：两片较钝、有齿或开窗的夹爪，用于抓持牵拉，不是锐利刀刃。\n"
                                "施夹器：较宽厚、近乎平行且通常不交叉的对合夹臂，用于释放夹子；"
                                "即使看起来像两片金属臂，也不是剪刀。\n"
                                "必须逐张检查、以形态为准，不要按多张图的多数投票。"
                                "只有任意一张图清晰显示细长刀刃、交叉铰链或实际剪切闭合，"
                                "才能确认scissors_visible=true；仅有两片平行对合夹臂不够。"
                                "形态不确定时必须否决剪刀。只输出JSON："
                                '{"instrument":"scissors|electrocautery_hook|grasper|clip_applier|other",'
                                '"scissors_visible":false,"scissors_cutting":false,'
                                '"confidence":0.0,"reason":"一句话形态证据"}'
                            )
                            content = [{"type": "text", "text": prompt}]
                            for image in review_images:
                                content.append({
                                    "type": "image_url",
                                    "image_url": {"url": _openai_image_url(image), "detail": "low"},
                                })
                            payload = {
                                "model": model,
                                "messages": [
                                    {"role": "system", "content": "只输出一个紧凑JSON对象。"},
                                    {"role": "user", "content": content},
                                ],
                                "temperature": float(review_cfg.get("temperature", 0.0) or 0.0),
                                "max_tokens": int(review_cfg.get("max_tokens", 180) or 180),
                                "chat_template_kwargs": {"enable_thinking": False},
                            }
                            try:
                                async with httpx.AsyncClient(
                                    timeout=float(review_cfg.get("timeout", 30.0) or 30.0),
                                    trust_env=False,
                                ) as client:
                                    response = await client.post(f"{base_url}/chat/completions", json=payload)
                                if response.status_code >= 400:
                                    return {
                                        "success": False,
                                        "error": f"HTTP {response.status_code}: {response.text[:500]}",
                                        "model": model,
                                    }
                                data = response.json()
                                text = str(data["choices"][0]["message"]["content"] or "").strip()
                                parsed = _parse_json_object(text) or {}
                                instrument = str(parsed.get("instrument") or "other").strip().lower()
                                return {
                                    "success": True,
                                    "instrument": instrument,
                                    "scissors_visible": bool(parsed.get("scissors_visible")) or instrument == "scissors",
                                    "scissors_cutting": bool(parsed.get("scissors_cutting")) and instrument == "scissors",
                                    "confidence": _safe_float(parsed.get("confidence"), 0.0),
                                    "reason": str(parsed.get("reason") or "")[:240],
                                    "raw": text[:800],
                                    "model": data.get("model") or model,
                                    "images": len(review_images),
                                    "image_selection": image_selection,
                                }
                            except Exception as exc:
                                return {
                                    "success": False,
                                    "error": f"{type(exc).__name__}: {exc}"[:500],
                                    "model": model,
                                }

                        async def _apply_scissors_vlm_review(visual: Dict[str, Any]) -> Dict[str, Any]:
                            if not _should_run_scissors_vlm_review(visual):
                                return visual
                            review = await _run_scissors_vlm_review()
                            visual["scissors_secondary_review"] = review
                            if not review.get("success"):
                                logger.warning(
                                    "[ScissorsVLMReview] Window %s failed: %s",
                                    window_id,
                                    review.get("error"),
                                )
                                return visual
                            min_conf = _safe_float(
                                config.get("services", {}).get("scissors_vlm_review", {}).get("min_confidence"),
                                0.75,
                            )
                            confidence = _safe_float(review.get("confidence"), 0.0)
                            if confidence < min_conf:
                                return visual
                            scissors = visual.get("scissors") or {}
                            if review.get("scissors_visible"):
                                scissors.update({
                                    "visible": True,
                                    "cutting": bool(review.get("scissors_cutting")),
                                    "confidence": confidence,
                                    "secondary_review": True,
                                })
                                applier = visual.get("clip_applier") or {}
                                applier.update({
                                    "visible": False,
                                    "active": False,
                                    "rejected_by_scissors_review": True,
                                })
                                visual["clip_applier"] = applier
                            elif str(review.get("instrument") or "") in {
                                "electrocautery_hook",
                                "grasper",
                                "clip_applier",
                                "other",
                            }:
                                scissors.update({
                                    "visible": False,
                                    "cutting": False,
                                    "target": "unknown",
                                    "confidence": confidence,
                                    "rejected_by_secondary_review": True,
                                })
                            visual["scissors"] = scissors
                            return visual

                        def _should_run_visibility_vlm_review(visual: Dict[str, Any]) -> bool:
                            review_cfg = config.get("services", {}).get("visibility_vlm_review", {})
                            if not review_cfg.get("enabled", False):
                                return False
                            return _should_review_visibility_candidate(
                                visual,
                                local_visibility_cue,
                            )

                        async def _run_visibility_vlm_review() -> Dict[str, Any]:
                            review_cfg = config.get("services", {}).get("visibility_vlm_review", {})
                            base_url = str(review_cfg.get("base_url") or "").rstrip("/")
                            model = str(review_cfg.get("model_name") or "Qwen3-VL-8B-Instruct")
                            if not base_url:
                                return {"success": False, "error": "visibility_vlm_review.base_url is empty"}
                            max_review_images = int(review_cfg.get("max_images", 3) or 3)
                            source_images = list(images or sampled or [])
                            if len(source_images) <= max_review_images:
                                review_images = source_images
                            elif max_review_images == 3:
                                review_images = [
                                    source_images[round((len(source_images) - 1) * fraction)]
                                    for fraction in (0.20, 0.60, 0.90)
                                ]
                            else:
                                review_images = _uniform_sample(source_images, max_review_images)
                            if not review_images:
                                return {"success": False, "error": "no review images"}
                            prompt = (
                                "这些是同一个5秒胆囊切除术窗口的连续帧。只判断场景，输出一个JSON。\n"
                                "external_body：出现患者腹壁外侧皮肤（浅黄/肤色平坦表面、体毛、皮肤切口或血痕）、"
                                "白色套管阀门、体外金属器械盘、手术巾或手术室，且不再看到腹腔内红色肝胆组织。\n"
                                "specimen_bag_inside：白色/半透明、柔软折叠的塑料标本袋被抓钳夹持，"
                                "周围仍有红色肝脏或腹腔组织；这不是体外。\n"
                                "trocar_transition：主要看到套管圆形内壁或镜鞘，已看不到腹腔组织。\n"
                                "foggy_inside：仍在腹腔内，但雾气、水汽或烟雾遮挡组织。\n"
                                "intra_abdominal：清楚看到红褐色肝胆组织。\n"
                                "场景证据优先于清晰度：任意一帧出现直线形手术室顶灯、矩形柜体或监护设备、"
                                "蓝色无菌巾、体外器械台或医护手套，即使镜头模糊或有水汽，也必须判为external_body。"
                                "只有所有帧仍可辨认腹腔内肝胆组织、且没有手术室几何结构时，才可判foggy_inside。"
                                "浅黄平坦皮肤不要误认为肝脏；白色柔软折叠袋不要误认为手术室器械。"
                                "只输出："
                                '{"classification":"external_body|specimen_bag_inside|trocar_transition|foggy_inside|intra_abdominal",'
                                '"confidence":0.0,"evidence":"具体视觉证据"}'
                            )
                            content = [{"type": "text", "text": prompt}]
                            for image in review_images:
                                content.append({
                                    "type": "image_url",
                                    "image_url": {"url": _openai_image_url(image), "detail": "low"},
                                })
                            payload = {
                                "model": model,
                                "messages": [
                                    {"role": "system", "content": "只输出一个紧凑JSON对象。"},
                                    {"role": "user", "content": content},
                                ],
                                "temperature": float(review_cfg.get("temperature", 0.0) or 0.0),
                                "max_tokens": int(review_cfg.get("max_tokens", 180) or 180),
                                "chat_template_kwargs": {"enable_thinking": False},
                            }
                            try:
                                async with httpx.AsyncClient(
                                    timeout=float(review_cfg.get("timeout", 30.0) or 30.0),
                                    trust_env=False,
                                ) as client:
                                    response = await client.post(f"{base_url}/chat/completions", json=payload)
                                if response.status_code >= 400:
                                    return {
                                        "success": False,
                                        "error": f"HTTP {response.status_code}: {response.text[:500]}",
                                        "model": model,
                                    }
                                data = response.json()
                                text = str(data["choices"][0]["message"]["content"] or "").strip()
                                parsed = _parse_json_object(text) or {}
                                return {
                                    "success": True,
                                    "classification": str(parsed.get("classification") or "").strip().lower(),
                                    "confidence": _safe_float(parsed.get("confidence"), 0.0),
                                    "evidence": str(parsed.get("evidence") or "")[:240],
                                    "raw": text[:800],
                                    "model": data.get("model") or model,
                                    "images": len(review_images),
                                }
                            except Exception as exc:
                                return {
                                    "success": False,
                                    "error": f"{type(exc).__name__}: {exc}"[:500],
                                    "model": model,
                                }

                        async def _apply_visibility_vlm_review(visual: Dict[str, Any]) -> Dict[str, Any]:
                            if not _should_run_visibility_vlm_review(visual):
                                return visual
                            review = await _run_visibility_vlm_review()
                            visual["visibility_secondary_review"] = review
                            if not review.get("success"):
                                logger.warning(
                                    "[VisibilityVLMReview] Window %s failed: %s",
                                    window_id,
                                    review.get("error"),
                                )
                                return visual
                            min_conf = _safe_float(
                                config.get("services", {}).get("visibility_vlm_review", {}).get("min_confidence"),
                                0.75,
                            )
                            confidence = _safe_float(review.get("confidence"), 0.0)
                            if confidence < min_conf:
                                return visual
                            classification = str(review.get("classification") or "").lower()
                            visibility = dict(visual.get("visibility") or {})
                            if classification in {"external_body", "trocar_transition"}:
                                visibility.update({
                                    "status": "out_of_body",
                                    "out_of_body": True,
                                    "fog": False,
                                    "fog_cleared": False,
                                    "confidence": confidence,
                                    "evidence": review.get("evidence", ""),
                                    "evidence_source": "visibility_secondary_review",
                                })
                            elif classification == "foggy_inside":
                                visibility.update({
                                    "status": "foggy",
                                    "out_of_body": False,
                                    "fog": True,
                                    "confidence": confidence,
                                    "evidence": review.get("evidence", ""),
                                    "evidence_source": "visibility_secondary_review",
                                })
                            elif classification in {"specimen_bag_inside", "intra_abdominal"}:
                                visibility.update({
                                    "status": "clear",
                                    "out_of_body": False,
                                    "fog": False,
                                    "confidence": confidence,
                                    "evidence": review.get("evidence", ""),
                                    "evidence_source": "visibility_secondary_review",
                                })
                            visual["visibility"] = visibility
                            return visual

                        async def _normalize_open_vision_result(result: Dict[str, Any], source_provider: str) -> Dict[str, Any]:
                            text = (result.get("text") or result.get("summary") or "").strip()
                            if not result.get("success") or not text:
                                return {
                                    "success": False,
                                    "error": result.get("error"),
                                    "model": result.get("model"),
                                    "provider": source_provider,
                                    "external_call_count": int(result.get("_external_call_count") or 0),
                                }
                            parsed = _parse_json_object(text)
                            visual = _ensure_visual_defaults((parsed or {}).get("visual") if parsed else None)
                            if phase_context not in {
                                "CalotTriangleDissection",
                                "ClippingCutting",
                                "GallbladderDissection",
                            }:
                                # Clipping commonly starts while the phase
                                # expert still reports Calot dissection. Keep
                                # that transition eligible, while rejecting
                                # clip-like highlights in unrelated phases.
                                for key in ("generic_clip", "hemolok", "titanium_clip"):
                                    clip_state = dict(visual.get(key) or {})
                                    clip_state.update({
                                        "visible": False,
                                        "placed": False,
                                        "count": 0,
                                        "confidence": 0.0,
                                        "rejected_by_phase": True,
                                    })
                                    visual[key] = clip_state
                                applier_state = dict(visual.get("clip_applier") or {})
                                applier_state.update({
                                    "visible": False,
                                    "active": False,
                                    "rejected_by_phase": True,
                                })
                                visual["clip_applier"] = applier_state
                            visual = await _apply_clip_vlm_review(visual)
                            visual = await _apply_scissors_vlm_review(visual)
                            visual = _resolve_clip_applier_scissors_conflict(visual, expert_pack)
                            visual = await _apply_visibility_vlm_review(visual)
                            summary = ""
                            if parsed:
                                summary = str(parsed.get("summary") or parsed.get("analysis") or "").strip()
                            if not summary and parsed:
                                summary = _visual_fallback_summary(visual)
                            if not parsed:
                                summary = "" if re.search(r'^\s*\{|"visual"\s*:', text or "") else text
                            if any(token in summary for token in ("无明确新增动作", "未见明确", "没有明确", "无明显")):
                                summary = ""
                            if not summary and parsed:
                                summary = _visual_fallback_summary(visual)
                            structured_summary = _visual_fallback_summary(visual)
                            if structured_summary and _has_structured_visual_evidence(visual):
                                summary = structured_summary
                            summary = _apply_target_structure_to_summary(summary, visual)
                            summary = _filter_visual_summary_noise(summary, visual)
                            summary = summary.replace("\n", " ").strip(" 。")
                            return {
                                "success": True,
                                "summary": (summary + "。") if summary else "",
                                "visual": visual,
                                "raw": text,
                                "model": result.get("model"),
                                "provider": source_provider,
                                "thinking_level": result.get("_thinking_level_used", thinking_level),
                                "requested_thinking_level": thinking_level,
                                "duration_ms": result.get("duration_ms"),
                                "external_call_count": (
                                    int(result.get("_external_call_count") or 0)
                                    + int("clip_secondary_review" in visual)
                                    + int("scissors_secondary_review" in visual)
                                    + int("visibility_secondary_review" in visual)
                                ),
                            }

                        if provider in {"openai", "gpt", "openai_compatible"}:
                            result = await _call_openai_vision()
                            return await _normalize_open_vision_result(result, "openai")

                        vision_client = vlm_client
                        if provider == "gemini":
                            try:
                                from ..services.gemini_client import GeminiClient
                                cache_key = (provider, model_name or "", thinking_level)
                                cached = getattr(_open_vlm_realtime_hint, "_client_cache", {})
                                if cache_key not in cached:
                                    cached[cache_key] = GeminiClient(
                                        model_name=model_name,
                                        thinking_level=thinking_level,
                                        max_tokens=max_tokens,
                                        max_concurrent=1,
                                    )
                                    setattr(_open_vlm_realtime_hint, "_client_cache", cached)
                                vision_client = cached[cache_key]
                            except Exception as e:
                                logger.warning(f"[OpenVLM] Could not create realtime Gemini client: {e}")

                        if not hasattr(vision_client, "_analyze_images_with_timestamps"):
                            return {}

                        async def _call_vision(client, level_label: str):
                            logger.info(
                                "[ModelCall] session=%s window=%s type=open_visual_gpt provider=%s model=%s images=%s",
                                session_id,
                                window_id,
                                provider,
                                model_name or getattr(client, "model_name", ""),
                                len(sampled),
                            )
                            result = await asyncio.wait_for(
                                client._analyze_images_with_timestamps(
                                    images=sampled,
                                    timestamps=timestamps,
                                    question=question,
                                    system_prompt="你是腹腔镜手术视觉审阅专家。必须只输出一个JSON对象，不要markdown。",
                                    temperature=temperature,
                                    max_tokens=max_tokens,
                                ),
                                timeout=timeout_s,
                            )
                            result["_thinking_level_used"] = level_label
                            result["_external_call_count"] = 1
                            return result

                        try:
                            result = await _call_vision(vision_client, thinking_level)
                        except Exception as e:
                            error_text = str(e)
                            local_retry_count = int(open_cfg.get("retry_count", 0) or 0)
                            if provider in {"glm", "local"} and local_retry_count > 0:
                                try:
                                    logger.warning(
                                        "[OpenVLM] Local call failed for window %s (%s); retrying once",
                                        window_id,
                                        type(e).__name__,
                                    )
                                    await asyncio.sleep(0.5)
                                    result = await _call_vision(vision_client, thinking_level)
                                    result["_external_call_count"] = 2
                                except Exception as retry_e:
                                    logger.warning(
                                        "[OpenVLM] Local retry failed for window %s: %s: %s",
                                        window_id,
                                        type(retry_e).__name__,
                                        retry_e,
                                    )
                                    return {
                                        "success": False,
                                        "error": f"{type(retry_e).__name__}: {retry_e}",
                                        "initial_error": f"{type(e).__name__}: {e}",
                                        "external_call_count": 2,
                                    }
                            elif provider == "gemini" and "Budget 0 is invalid" in error_text and thinking_level == "none":
                                try:
                                    from ..services.gemini_client import GeminiClient
                                    fallback_key = (provider, model_name or "", "low")
                                    cached = getattr(_open_vlm_realtime_hint, "_client_cache", {})
                                    if fallback_key not in cached:
                                        cached[fallback_key] = GeminiClient(
                                            model_name=model_name,
                                            thinking_level="low",
                                            max_tokens=max_tokens,
                                            max_concurrent=1,
                                        )
                                        setattr(_open_vlm_realtime_hint, "_client_cache", cached)
                                    logger.warning("[OpenVLM] Model rejected thinking=none; retrying with thinking=low")
                                    result = await _call_vision(cached[fallback_key], "low")
                                except Exception as retry_e:
                                    logger.warning(f"[OpenVLM] Realtime hint failed after fallback: {retry_e}")
                                    return {"success": False, "error": str(retry_e), "initial_error": error_text}
                            else:
                                logger.warning(f"[OpenVLM] Realtime hint failed: {type(e).__name__}: {e}")
                                if open_cfg.get("openai_fallback", True):
                                    fallback = await _call_openai_vision()
                                    normalized = await _normalize_open_vision_result(fallback, "openai")
                                    if normalized.get("success"):
                                        return normalized
                                return {"success": False, "error": error_text}

                        # GLMClient converts transient HTTP failures into a
                        # success=false payload instead of raising. Retry that
                        # path too; otherwise a settled task can still leave an
                        # unreviewed five-second window in the final export.
                        local_retry_count = int(open_cfg.get("retry_count", 0) or 0)
                        if (
                            provider in {"glm", "local"}
                            and not result.get("success")
                            and local_retry_count > 0
                            and int(result.get("_external_call_count") or 1) <= 1
                        ):
                            first_error = str(result.get("error") or "local VLM returned success=false")
                            try:
                                logger.warning(
                                    "[OpenVLM] Local call returned failure for window %s; retrying once",
                                    window_id,
                                )
                                await asyncio.sleep(0.5)
                                result = await _call_vision(vision_client, thinking_level)
                                result["_external_call_count"] = 2
                                if not result.get("success"):
                                    result["initial_error"] = first_error
                            except Exception as retry_e:
                                logger.warning(
                                    "[OpenVLM] Local returned-error retry failed for window %s: %s: %s",
                                    window_id,
                                    type(retry_e).__name__,
                                    retry_e,
                                )
                                return {
                                    "success": False,
                                    "error": f"{type(retry_e).__name__}: {retry_e}",
                                    "initial_error": first_error,
                                    "external_call_count": 2,
                                }

                        if (
                            provider == "gemini"
                            and not result.get("success")
                            and "Budget 0 is invalid" in str(result.get("error", ""))
                            and thinking_level == "none"
                        ):
                            try:
                                from ..services.gemini_client import GeminiClient
                                fallback_key = (provider, model_name or "", "low")
                                cached = getattr(_open_vlm_realtime_hint, "_client_cache", {})
                                if fallback_key not in cached:
                                    cached[fallback_key] = GeminiClient(
                                        model_name=model_name,
                                        thinking_level="low",
                                        max_tokens=max_tokens,
                                        max_concurrent=1,
                                    )
                                    setattr(_open_vlm_realtime_hint, "_client_cache", cached)
                                logger.warning("[OpenVLM] Model returned thinking=none error; retrying with thinking=low")
                                result = await _call_vision(cached[fallback_key], "low")
                            except Exception as retry_e:
                                logger.warning(f"[OpenVLM] Realtime hint failed after returned-error fallback: {retry_e}")
                                return {
                                    "success": False,
                                    "error": str(retry_e),
                                    "initial_error": result.get("error"),
                                }

                        return await _normalize_open_vision_result(result, provider)

                    async def _patch_open_vlm_window(
                        window_id: int,
                        meta: Dict[str, Any],
                        pil_images,
                        expert_text: str,
                        expert_pack_base: Dict[str, Any],
                        stage1_summary_text: str,
                        stage1_phase: str,
                    ):
                        try:
                            open_vlm_hint = await _open_vlm_realtime_hint(
                                vlm_client=vlm_client,
                                images=pil_images,
                                start_time=meta.get("start_time", 0),
                                end_time=meta.get("end_time", 0),
                                expert_text=expert_text,
                                window_id=window_id,
                                expert_pack=expert_pack_base,
                            )
                            if not open_vlm_hint:
                                return

                            current_summary = stage1_summary_text
                            current_phase = stage1_phase
                            current_stage = 1
                            others_data: Dict[str, Any] = {}
                            try:
                                existing_summary = next(
                                    (
                                        s for s in get_summaries_by_session(db, db_session_id)
                                        if (s.get("window_id") if isinstance(s, dict) else getattr(s, "window_id", None)) == window_id
                                    ),
                                    None,
                                )
                                if existing_summary:
                                    current_summary = (
                                        existing_summary.get("glm_summary", "")
                                        if isinstance(existing_summary, dict)
                                        else getattr(existing_summary, "summary_text", "")
                                    ) or current_summary
                                    current_phase = (
                                        existing_summary.get("surgical_phase")
                                        if isinstance(existing_summary, dict)
                                        else getattr(existing_summary, "surgical_phase", None)
                                    ) or current_phase
                                    others_data = (
                                        existing_summary.get("others")
                                        if isinstance(existing_summary, dict)
                                        else getattr(existing_summary, "others_data", None)
                                    ) or {}
                                    if isinstance(others_data, str):
                                        import json as _json_patch
                                        others_data = _json_patch.loads(others_data) if others_data else {}
                                    current_stage = int(others_data.get("stage", current_stage))
                            except Exception as e:
                                logger.debug(f"[OpenVLM] Could not load existing window {window_id} before patch: {e}")

                            hint_summary = open_vlm_hint.get("summary", "")
                            hint_visual = open_vlm_hint.get("visual") or {}
                            hint_visual = _fuse_packaging_scope_exit_evidence(
                                hint_visual,
                                expert_pack_base.get("local_visibility") or {},
                                current_phase,
                            )
                            open_vlm_hint["visual"] = hint_visual
                            current_summary = _strip_visual_rejected_clip_claims(
                                current_summary,
                                hint_visual,
                                current_phase,
                            )
                            current_summary = _strip_visual_rejected_scissors_claims(
                                current_summary,
                                hint_visual,
                                expert_pack_base,
                                current_phase,
                            )
                            visibility_flags = _visibility_flags_from_visual(hint_visual)
                            if visibility_flags["out_of_body"]:
                                current_summary = ""
                                hint_summary = ""
                            if hint_summary:
                                normalized_hint = hint_summary.rstrip("。")
                                if normalized_hint and normalized_hint not in current_summary:
                                    current_summary = (
                                        f"{current_summary.rstrip('。')}。{normalized_hint}。"
                                        if current_summary.strip(" 。")
                                        else f"{normalized_hint}。"
                                    )
                            current_summary = _strip_focused_scissors_instrument_conflicts(
                                current_summary,
                                hint_visual,
                            )
                            current_summary = _strip_unverified_target_specific_clip_claims(
                                current_summary,
                                hint_visual,
                                expert_pack_base,
                            )
                            current_summary = _strip_nonprogress_idle_applier_claim(
                                current_summary,
                                current_phase,
                            )
                            current_summary = _strip_focused_visibility_conflicts(
                                current_summary,
                                hint_visual,
                            )
                            visibility_summary = _visibility_summary_for_window(
                                hint_visual,
                                get_summaries_by_session(db, db_session_id),
                                before_window_id=window_id,
                                language="zh",
                            )
                            if visibility_summary and visibility_summary.rstrip("。") not in current_summary:
                                current_summary = (
                                    f"{current_summary.rstrip('。')}。{visibility_summary.rstrip('。')}。"
                                    if current_summary.strip(" 。")
                                    else f"{visibility_summary.rstrip('。')}。"
                                )
                            patch_target_hint = _target_hint_from_triplet(expert_pack_base.get("triplet") or {})
                            patch_target_label = patch_target_hint.get("label") or "cystic_duct"
                            visual_target = ((open_vlm_hint.get("visual") or {}).get("target_structure") or {})
                            visual_target_label = _target_label_from_raw(visual_target.get("label"))
                            current_summary = _sanitize_target_language(
                                current_summary,
                                visual_target_label or patch_target_label,
                            )
                            current_summary = _strip_clipping_noise(current_summary)
                            sequence_state = {}
                            patch_sequence_rules: List[str] = []
                            try:
                                sequence_state = _build_surgical_sequence_state(
                                    get_summaries_by_session(db, db_session_id),
                                    before_window_id=window_id,
                                )
                                current_summary, current_phase, patch_sequence_rules = _apply_surgical_sequence_rules(
                                    current_summary,
                                    current_phase,
                                    sequence_state,
                                    visual=hint_visual,
                                )
                            except Exception as rule_e:
                                logger.warning(f"[SequenceRules] OpenVLM rule pass failed for window {window_id}: {rule_e}")
                            current_summary = _strip_visual_rejected_scissors_claims(
                                current_summary,
                                hint_visual,
                                expert_pack_base,
                                current_phase,
                            )
                            current_summary = _strip_focused_scissors_instrument_conflicts(
                                current_summary,
                                hint_visual,
                            )

                            experts = dict((others_data or {}).get("experts") or {})
                            experts.update({
                                "phase": expert_pack_base.get("phase", experts.get("phase", {})),
                                "triplet": expert_pack_base.get("triplet", experts.get("triplet", {})),
                                "yolo": {
                                    "tools": expert_pack_base.get("yolo", {}).get("tools", []),
                                    "total_detections": expert_pack_base.get("yolo", {}).get("total_detections", 0),
                                },
                                "clip_detector": expert_pack_base.get("clip_detector", experts.get("clip_detector", {})),
                                "short_action": expert_pack_base.get("short_action", experts.get("short_action", {})),
                                "hemlok_clip": expert_pack_base.get("hemlok_clip", experts.get("hemlok_clip", {})),
                                "blue_bipolar_forceps": expert_pack_base.get("blue_bipolar_forceps", experts.get("blue_bipolar_forceps", {})),
                                "local_visibility": expert_pack_base.get("local_visibility", experts.get("local_visibility", {})),
                                "open_vlm": open_vlm_hint,
                                "available": expert_pack_base.get("available", experts.get("available", [])),
                            })
                            others_data = dict(others_data or {})
                            others_data["stage"] = current_stage
                            others_data.setdefault("stage1_summary", stage1_summary_text)
                            others_data["experts"] = experts
                            if open_vlm_hint.get("visual"):
                                others_data["visual_gpt"] = open_vlm_hint["visual"]
                            model_calls = dict(others_data.get("model_calls") or {})
                            model_calls.setdefault("stage1_local", {
                                "count": 0,
                                "provider": "local",
                                "model": "yolo+clip_detector+phase+triplet+opencv_cues",
                            })
                            model_calls["open_visual_gpt"] = {
                                "count": int(open_vlm_hint.get("external_call_count") or 0),
                                "provider": open_vlm_hint.get("provider"),
                                "model": open_vlm_hint.get("model"),
                                "covers": ["Hem-o-lok", "titanium_clip", "clip_applier", "scissors_cutting", "target_structure", "gauze", "bleeding", "CVS", "fog", "visibility", "out_of_body"],
                            }
                            others_data["model_calls"] = model_calls
                            others_data["sequence_rules"] = _sequence_state_meta(sequence_state, patch_sequence_rules)

                            create_window_summary(
                                db=db,
                                session_id=db_session_id,
                                window_id=window_id,
                                start_time=meta.get("start_time", 0),
                                end_time=meta.get("end_time", 0),
                                summary_text=current_summary,
                                dominant_phase=current_phase,
                                tools_detected=[t["label"] for t in expert_pack_base.get("yolo", {}).get("tools", [])],
                                key_actions=[],
                                others_data=others_data,
                            )
                            logger.info(
                                f"[OpenVLM] Window {window_id} patched asynchronously: "
                                f"{(hint_summary or open_vlm_hint.get('raw') or '')[:80]}"
                            )
                        except Exception as e:
                            logger.warning(f"[OpenVLM] Async patch failed for window {window_id}: {e}")

                    for window_data in sorted_windows:
                        # Check cancellation
                        if analysis_cancellation_flags.get(session_id, False):
                            logger.info(f"[GLM Task] Cancelled during window processing")
                            break

                        window_id = window_data["window_id"]
                        meta = window_metadata.get(window_id, {})
                        frame_analyses = meta.get("frame_analyses", [])

                        # ===================== Stage 1: 专家 → Gemini 文本 =====================
                        stage1_summary_text = ""
                        stage1_phase = "Unknown"
                        expert_pack: Dict[str, Any] = {}
                        stage1_text_vlm_count = 0
                        should_emit_stage1 = window_id not in stage1_processed_windows
                        try:
                            pil_images = window_data.get("expert_images") or window_data.get("images") or []
                            bgr_frames = _pil_list_to_bgr(_uniform_sample(pil_images, 12))
                            # 传入会话内已经走过的阶段集合，让 Phase Expert 不再闪回回退阶段
                            reached = set(getattr(history_manager, "_reached_phases", set()) or set())
                            if bgr_frames:
                                loop = asyncio.get_event_loop()
                                expert_pack = await loop.run_in_executor(
                                    None,
                                    lambda: run_experts_on_window(bgr_frames, reached_phases=reached),
                                )
                                local_visibility = _local_visibility_cue_from_bgr_frames(bgr_frames)
                                expert_pack["local_visibility"] = local_visibility
                            expert_text = expert_pack.get("text", "【专家模型判断】(未启用)")

                            if should_emit_stage1:
                                if is_live:
                                    # Real-time means local expert output must
                                    # reach the UI without waiting on Gemini/R1.
                                    stage1_summary_text = _expert_snapshot_summary(
                                        expert_pack=expert_pack,
                                        start_time=meta.get("start_time", 0),
                                        end_time=meta.get("end_time", 0),
                                        frame_count=len(bgr_frames),
                                    )
                                    stage1_phase = _canonical_phase(
                                        expert_pack.get("phase", {}).get("label", "Unknown") or "Unknown"
                                    )
                                    expert_pack["open_vlm"] = {"pending": True}
                                    refinement_task = asyncio.create_task(
                                        _patch_open_vlm_window(
                                            window_id=window_id,
                                            meta=meta,
                                            pil_images=pil_images,
                                            expert_text=expert_text,
                                            expert_pack_base=dict(expert_pack),
                                            stage1_summary_text=stage1_summary_text,
                                            stage1_phase=stage1_phase,
                                        )
                                    )
                                    _track_open_vlm_task(db_session_id, refinement_task)
                                else:
                                    stage1_summary_text = _expert_snapshot_summary(
                                        expert_pack=expert_pack,
                                        start_time=meta.get("start_time", 0),
                                        end_time=meta.get("end_time", 0),
                                        frame_count=len(bgr_frames),
                                    )
                                    stage1_phase = _canonical_phase(
                                        expert_pack.get("phase", {}).get("label", "Unknown") or "Unknown"
                                    )
                        except Exception as s1e:
                            logger.warning(f"[GLM Task] Stage 1 failed for window {window_id}: {s1e}")

                        sequence_state = {}
                        stage1_sequence_rules: List[str] = []
                        if stage1_summary_text:
                            try:
                                sequence_state = _build_surgical_sequence_state(
                                    get_summaries_by_session(db, db_session_id),
                                    before_window_id=window_id,
                                )
                                stage1_summary_text, stage1_phase, stage1_sequence_rules = _apply_surgical_sequence_rules(
                                    stage1_summary_text,
                                    stage1_phase,
                                    sequence_state,
                                    visual=(expert_pack.get("open_vlm") or {}).get("visual") if isinstance(expert_pack.get("open_vlm"), dict) else None,
                                )
                            except Exception as rule_e:
                                logger.warning(f"[SequenceRules] Stage 1 rule pass failed for window {window_id}: {rule_e}")

                        # 立即保存 Stage 1（前端 SSE 很快就看到）
                        if stage1_summary_text:
                            create_window_summary(
                                db=db,
                                session_id=db_session_id,
                                window_id=window_id,
                                start_time=meta.get("start_time", 0),
                                end_time=meta.get("end_time", 0),
                                summary_text=stage1_summary_text,
                                dominant_phase=stage1_phase,
                                tools_detected=[t["label"] for t in expert_pack.get("yolo", {}).get("tools", [])],
                                key_actions=[],
                                others_data={
                                    "stage": 1,
                                    "realtime": bool(is_live),
                                    "experts": {
                                        "phase": expert_pack.get("phase", {}),
                                        "triplet": expert_pack.get("triplet", {}),
                                        "yolo": {
                                            "tools": expert_pack.get("yolo", {}).get("tools", []),
                                            "total_detections": expert_pack.get("yolo", {}).get("total_detections", 0),
                                        },
                                        "clip_detector": expert_pack.get("clip_detector", {}),
                                        "short_action": expert_pack.get("short_action", {}),
                                        "hemlok_clip": expert_pack.get("hemlok_clip", {}),
                                        "blue_bipolar_forceps": expert_pack.get("blue_bipolar_forceps", {}),
                                        "local_visibility": expert_pack.get("local_visibility", {}),
                                        "open_vlm": expert_pack.get("open_vlm", {}),
                                        "available": expert_pack.get("available", []),
                                    },
                                    "model_calls": {
                                        "stage1_local": {
                                            "count": 0,
                                            "provider": "local",
                                            "model": "yolo+clip_detector+phase+triplet+opencv_cues",
                                        },
                                        "stage1_text_vlm": {
                                            "count": stage1_text_vlm_count,
                                            "provider": get_summarization_provider() if stage1_text_vlm_count else None,
                                            "model": None,
                                        },
                                        "open_visual_gpt": {
                                            "count": 0,
                                            "provider": None,
                                            "model": None,
                                            "status": "pending" if is_live else "not_scheduled",
                                            "covers": ["Hem-o-lok", "titanium_clip", "clip_applier", "scissors_cutting", "target_structure", "gauze", "bleeding", "CVS", "fog", "visibility", "out_of_body"],
                                        },
                                        "stage2_summary_vlm": {
                                            "count": 0,
                                            "provider": None,
                                            "model": None,
                                            "status": "waiting_r1",
                                        },
                                    },
                                    "sequence_rules": _sequence_state_meta(sequence_state, stage1_sequence_rules),
                                },
                            )
                            stage1_processed_windows.add(window_id)
                            if is_live and not live_stage2_enabled:
                                # The structured local VLM patch is the only
                                # refinement in local deployment. Mark this
                                # complete window final now so a later R1 loop
                                # cannot race with and overwrite that patch.
                                processed_windows.add(window_id)

                            if is_live and should_emit_stage1:
                                logger.info(f"[GLM Task] Window {window_id} Stage 1 saved; deferring Stage 2")
                                continue

                        # If this live window was triggered only by saved
                        # playback frames, R1 has not produced frame analyses
                        # yet. Stop after Stage 1 and let a later loop run
                        # Stage 2 once the R1 rows are available.
                        if not frame_analyses:
                            if stage1_summary_text:
                                logger.info(f"[GLM Task] Window {window_id} Stage 1 saved; waiting for R1 Stage 2")
                            continue

                        existing_model_calls: Dict[str, Any] = {}
                        existing_visual_gpt: Dict[str, Any] = {}
                        existing_experts: Dict[str, Any] = {}
                        existing_window_summary_text = ""
                        stage2_sequence_state: Dict[str, Any] = {}
                        stage2_sequence_rules: List[str] = []

                        if not should_emit_stage1:
                            try:
                                existing_summary = next(
                                    (
                                        s for s in get_summaries_by_session(db, db_session_id)
                                        if (s.get("window_id") if isinstance(s, dict) else getattr(s, "window_id", None)) == window_id
                                    ),
                                    None,
                                )
                                if existing_summary:
                                    existing_window_summary_text = (
                                        existing_summary.get("glm_summary", "")
                                        if isinstance(existing_summary, dict)
                                        else getattr(existing_summary, "summary_text", "")
                                    ) or ""
                                    stage1_phase = (
                                        existing_summary.get("surgical_phase")
                                        if isinstance(existing_summary, dict)
                                        else getattr(existing_summary, "surgical_phase", None)
                                    ) or stage1_phase
                                    existing_others_for_stage2 = (
                                        existing_summary.get("others")
                                        if isinstance(existing_summary, dict)
                                        else getattr(existing_summary, "others_data", None)
                                    ) or {}
                                    if isinstance(existing_others_for_stage2, str):
                                        existing_others_for_stage2 = json.loads(existing_others_for_stage2) if existing_others_for_stage2 else {}
                                    stage1_summary_text = (
                                        existing_others_for_stage2.get("stage1_summary")
                                        or existing_window_summary_text
                                        or stage1_summary_text
                                    )
                                    existing_model_calls = existing_others_for_stage2.get("model_calls") or {}
                                    existing_visual_gpt = existing_others_for_stage2.get("visual_gpt") or {}
                                    existing_experts = existing_others_for_stage2.get("experts") or {}
                                    if (
                                        not (expert_pack.get("open_vlm") or {}).get("success")
                                        and existing_experts.get("open_vlm")
                                    ):
                                        expert_pack["open_vlm"] = existing_experts["open_vlm"]
                            except Exception as e:
                                logger.debug(f"[GLM Task] Could not load previous Stage 1 for window {window_id}: {e}")

                        # ===================== Stage 2: 专家 + SurgR1 + 图像 =====================
                        stage2_summary_vlm_count = 0
                        stage2_summary_provider = None
                        stage2_summary_model = None
                        stage2_has_frame_signal = _frame_analysis_has_visual_signal(frame_analyses)
                        stage2_input_images = window_data.get("images") or window_data.get("expert_images") or []
                        stage2_key_candidate = _expert_pack_has_key_event_candidate(expert_pack)
                        if (
                            vlm_client is None
                            or not stage2_input_images
                            or (is_live and not live_stage2_enabled)
                            or (is_live and (not stage2_key_candidate or not stage2_has_frame_signal))
                        ):
                            summary_text = existing_window_summary_text or stage1_summary_text or _expert_snapshot_summary(
                                expert_pack=expert_pack,
                                start_time=meta.get("start_time", 0),
                                end_time=meta.get("end_time", 0),
                                frame_count=len(frame_analyses),
                            )
                            others_data_raw = None
                            dominant_phase = stage1_phase
                            surgr1_reasoning = ""
                            if vlm_client is not None and not stage2_input_images:
                                logger.info(
                                    "[ModelCall] session=%s window=%s type=stage2_summary_vlm skipped images=0 r1_signal=%s",
                                    session_id,
                                    window_id,
                                    stage2_has_frame_signal,
                                )
                            elif vlm_client is not None and is_live and not live_stage2_enabled:
                                logger.debug(
                                    "[ModelCall] session=%s window=%s type=stage2_summary_vlm skipped live_stage2_disabled",
                                    session_id,
                                    window_id,
                                )
                            elif vlm_client is not None and is_live and not stage2_key_candidate:
                                logger.debug(
                                    "[ModelCall] session=%s window=%s type=stage2_summary_vlm skipped no_key_candidate",
                                    session_id,
                                    window_id,
                                )
                            elif vlm_client is not None and is_live and not stage2_has_frame_signal:
                                logger.debug(
                                    "[ModelCall] session=%s window=%s type=stage2_summary_vlm skipped no_valid_r1_signal",
                                    session_id,
                                    window_id,
                                )
                        else:
                            try:
                                # 10s 窗口 / 1fps = ~10 帧；Stage 2 取 3 张（首/中/尾）给 Gemini 多模态
                                window_images = stage2_input_images
                                if window_images and len(window_images) > 3:
                                    n = len(window_images)
                                    window_images = [
                                        window_images[0],
                                        window_images[n // 2],
                                        window_images[-1],
                                    ]

                                conflict_context = None
                                if expert_pack.get("text"):
                                    conflict_context = expert_pack["text"]

                                history_context = await history_manager.build_history_context()
                                stage2_summary_vlm_count = 1
                                stage2_summary_provider = get_summarization_provider()
                                logger.info(
                                    "[ModelCall] session=%s window=%s type=stage2_summary_vlm provider=%s images=%s",
                                    session_id,
                                    window_id,
                                    stage2_summary_provider,
                                    len(window_images or []),
                                )
                                result = await vlm_client.integrate_analysis_results(
                                    frame_analyses=window_data.get("frame_analyses", []),
                                    images=window_images,
                                    history_context=history_context,
                                    conflict_context=conflict_context,
                                    temperature=0.7 if is_live else 0.9,
                                    max_tokens=1500,
                                )

                                if result.get("success"):
                                    summary_text = result.get("summary", "")
                                    previous_stage1_summary = stage1_summary_text
                                    try:
                                        existing_summary = next(
                                            (
                                                s for s in get_summaries_by_session(db, db_session_id)
                                                if (s.get("window_id") if isinstance(s, dict) else getattr(s, "window_id", None)) == window_id
                                            ),
                                            None,
                                        )
                                        if existing_summary:
                                            existing_others = (
                                                existing_summary.get("others")
                                                if isinstance(existing_summary, dict)
                                                else getattr(existing_summary, "others_data", None)
                                            ) or {}
                                            if isinstance(existing_others, str):
                                                existing_others = json.loads(existing_others) if existing_others else {}
                                            previous_stage1_summary = (
                                                existing_others.get("stage1_summary")
                                                or previous_stage1_summary
                                                or (
                                                    existing_summary.get("glm_summary", "")
                                                    if isinstance(existing_summary, dict)
                                                    else getattr(existing_summary, "summary_text", "")
                                                )
                                            )
                                            existing_experts = existing_others.get("experts") or {}
                                            existing_model_calls = existing_others.get("model_calls") or existing_model_calls
                                            existing_visual_gpt = existing_others.get("visual_gpt") or existing_visual_gpt
                                    except Exception as e:
                                        logger.debug(f"[GLM Task] Could not merge previous realtime evidence for window {window_id}: {e}")

                                    short_action = expert_pack.get("short_action") or {}
                                    existing_short_action = existing_experts.get("short_action") or {}
                                    if not short_action.get("detected") and existing_short_action.get("detected"):
                                        short_action = existing_short_action

                                    hemlok_clip = expert_pack.get("hemlok_clip") or {}
                                    existing_hemlok_clip = existing_experts.get("hemlok_clip") or {}
                                    if not hemlok_clip.get("detected") and existing_hemlok_clip.get("detected"):
                                        hemlok_clip = existing_hemlok_clip

                                    open_vlm = expert_pack.get("open_vlm") or {}
                                    existing_open_vlm = existing_experts.get("open_vlm") or {}
                                    if (
                                        not open_vlm.get("summary")
                                        and (
                                            existing_open_vlm.get("summary")
                                            or existing_open_vlm.get("visual")
                                            or existing_open_vlm.get("success") is True
                                        )
                                    ):
                                        open_vlm = existing_open_vlm
                                        expert_pack["open_vlm"] = open_vlm
                                    if not existing_visual_gpt and isinstance(open_vlm, dict) and open_vlm.get("visual"):
                                        existing_visual_gpt = open_vlm.get("visual") or {}
                                    expert_pack["short_action"] = short_action
                                    expert_pack["hemlok_clip"] = hemlok_clip

                                    open_vlm_summary = (open_vlm or {}).get("summary", "")
                                    summary_text = _merge_realtime_evidence(
                                        summary_text=summary_text,
                                        stage1_summary=previous_stage1_summary,
                                        open_vlm_summary=open_vlm_summary,
                                        short_action=short_action,
                                    )
                                    summary_text = _resolve_bipolar_hook_conflict(summary_text, expert_pack)
                                    summary_text = _strip_visual_rejected_clip_claims(
                                        summary_text,
                                        existing_visual_gpt,
                                        stage1_phase,
                                    )
                                    stage1_summary_text = previous_stage1_summary or stage1_summary_text
                                    others_data_raw = result.get("others")
                                    stage2_summary_model = (
                                        (others_data_raw or {}).get("model")
                                        if isinstance(others_data_raw, dict)
                                        else None
                                    )
                                    gemini_phase = next((en for cn, en in phase_map.items() if cn in summary_text), "")
                                    r1_phase = result.get("consistency_analysis", {}).get("图像级一致性", {}).get("主导阶段", "Unknown")
                                    dominant_phase = gemini_phase or r1_phase or stage1_phase or "Unknown"
                                    try:
                                        stage2_sequence_state = _build_surgical_sequence_state(
                                            get_summaries_by_session(db, db_session_id),
                                            before_window_id=window_id,
                                        )
                                        summary_text, dominant_phase, stage2_sequence_rules = _apply_surgical_sequence_rules(
                                            summary_text,
                                            dominant_phase,
                                            stage2_sequence_state,
                                            visual=existing_visual_gpt,
                                        )
                                    except Exception as rule_e:
                                        logger.warning(f"[SequenceRules] Stage 2 rule pass failed for window {window_id}: {rule_e}")
                                    # SurgR1 CoT: 原始响应是 <think>...</think><answer>...</answer>
                                    # 拆成可读段落，每帧一块（至多 4 帧）
                                    import re as _re
                                    def _extract_cot(raw):
                                        if not raw:
                                            return None
                                        tm = _re.search(r"<think>(.*?)</think>", raw, _re.DOTALL)
                                        am = _re.search(r"<answer>(.*?)</answer>", raw, _re.DOTALL)
                                        think = tm.group(1).strip() if tm else ""
                                        answer = am.group(1).strip() if am else raw.strip()
                                        return think, answer
                                    cot_blocks = []
                                    for f in frame_analyses[:4]:
                                        raw_phase = f.get("phase", "")
                                        if not raw_phase:
                                            continue
                                        think, answer = _extract_cot(raw_phase)
                                        ts = f.get("timestamp", 0)
                                        if think:
                                            cot_blocks.append(f"[t={ts:.1f}s]\n推理：{think}\n判断：{answer}")
                                        else:
                                            cot_blocks.append(f"[t={ts:.1f}s] {answer}")
                                    surgr1_reasoning = "\n\n".join(cot_blocks)

                                    await history_manager.add_summary(WindowSummary(
                                        window_id=window_id,
                                        start_time=meta.get("start_time", 0),
                                        end_time=meta.get("end_time", 0),
                                        summary=summary_text[:200],
                                        dominant_phase=dominant_phase,
                                        tools=[],
                                        cvs_status=""
                                    ))
                                else:
                                    # Stage 2 失败也不吞 Stage 1 的结果
                                    summary_text = stage1_summary_text or f"[分析出错: {result.get('error', '未知错误')}]"
                                    others_data_raw = None
                                    dominant_phase = stage1_phase
                                    surgr1_reasoning = ""
                            except Exception as inner_e:
                                logger.error(f"[GLM Task] Stage 2 failed for window {window_id}: {inner_e}")
                                summary_text = stage1_summary_text or f"[分析出错: {str(inner_e)}]"
                                others_data_raw = None
                                dominant_phase = stage1_phase
                                surgr1_reasoning = ""

                        model_calls = dict(existing_model_calls or {})
                        model_calls.setdefault("stage1_local", {
                            "count": 0,
                            "provider": "local",
                            "model": "yolo+clip_detector+phase+triplet+opencv_cues",
                        })
                        model_calls.setdefault("open_visual_gpt", {
                            "count": 0,
                            "provider": None,
                            "model": None,
                            "status": "not_available",
                            "covers": ["Hem-o-lok", "titanium_clip", "clip_applier", "scissors_cutting", "target_structure", "gauze", "bleeding", "CVS", "fog", "visibility", "out_of_body"],
                        })
                        model_calls["stage2_summary_vlm"] = {
                            "count": stage2_summary_vlm_count,
                            "provider": stage2_summary_provider,
                            "model": stage2_summary_model,
                        }
                        summary_target_hint = _target_hint_from_triplet(expert_pack.get("triplet") or {})
                        visual_target = (existing_visual_gpt or {}).get("target_structure") or {}
                        visual_target_label = _target_label_from_raw(visual_target.get("label"))
                        summary_text = _sanitize_target_language(
                            summary_text,
                            visual_target_label or summary_target_hint.get("label") or "cystic_duct",
                        )
                        stage1_summary_text = _sanitize_target_language(
                            stage1_summary_text,
                            visual_target_label or summary_target_hint.get("label") or "cystic_duct",
                        )
                        summary_text = _strip_clipping_noise(summary_text)
                        stage1_summary_text = _strip_clipping_noise(stage1_summary_text)
                        summary_text = _strip_visual_rejected_scissors_claims(
                            summary_text,
                            existing_visual_gpt,
                            expert_pack,
                            dominant_phase or stage1_phase,
                        )
                        summary_text = _strip_focused_scissors_instrument_conflicts(
                            summary_text,
                            existing_visual_gpt,
                        )
                        summary_text = _resolve_bipolar_hook_conflict(summary_text, expert_pack)
                        stage1_summary_text = _resolve_bipolar_hook_conflict(stage1_summary_text, expert_pack)
                        stage1_summary_text = _strip_visual_rejected_scissors_claims(
                            stage1_summary_text,
                            existing_visual_gpt,
                            expert_pack,
                            stage1_phase,
                        )
                        try:
                            if not stage2_sequence_state:
                                stage2_sequence_state = _build_surgical_sequence_state(
                                    get_summaries_by_session(db, db_session_id),
                                    before_window_id=window_id,
                                )
                            summary_text, dominant_phase, final_sequence_rules = _apply_surgical_sequence_rules(
                                summary_text,
                                dominant_phase,
                                stage2_sequence_state,
                                visual=existing_visual_gpt,
                            )
                            if stage1_summary_text:
                                stage1_summary_text, _, stage1_final_rules = _apply_surgical_sequence_rules(
                                    stage1_summary_text,
                                    stage1_phase,
                                    stage2_sequence_state,
                                    visual=existing_visual_gpt,
                                )
                            else:
                                stage1_final_rules = []
                            stage2_sequence_rules = list(dict.fromkeys(
                                stage2_sequence_rules + final_sequence_rules + [f"stage1:{r}" for r in stage1_final_rules]
                            ))
                        except Exception as rule_e:
                            logger.warning(f"[SequenceRules] Final rule pass failed for window {window_id}: {rule_e}")

                        # Sequence rules can rewrite a cut claim into a safety
                        # warning. Re-apply the visual veto as the final text
                        # gate so rejected scissors evidence cannot survive in
                        # the UI, event nodes, or clinical report.
                        summary_text = _strip_visual_rejected_scissors_claims(
                            summary_text,
                            existing_visual_gpt,
                            expert_pack,
                            dominant_phase or stage1_phase,
                        )
                        stage1_summary_text = _strip_visual_rejected_scissors_claims(
                            stage1_summary_text,
                            existing_visual_gpt,
                            expert_pack,
                            stage1_phase,
                        )
                        summary_text = _strip_unverified_target_specific_clip_claims(
                            summary_text,
                            existing_visual_gpt,
                            expert_pack,
                        )
                        stage1_summary_text = _strip_unverified_target_specific_clip_claims(
                            stage1_summary_text,
                            existing_visual_gpt,
                            expert_pack,
                        )
                        summary_text = _strip_nonprogress_idle_applier_claim(
                            summary_text,
                            dominant_phase or stage1_phase,
                        )
                        stage1_summary_text = _strip_nonprogress_idle_applier_claim(
                            stage1_summary_text,
                            stage1_phase,
                        )
                        summary_text = _strip_focused_visibility_conflicts(
                            summary_text,
                            existing_visual_gpt,
                        )
                        stage1_summary_text = _strip_focused_visibility_conflicts(
                            stage1_summary_text,
                            existing_visual_gpt,
                        )
                        summary_text = _expand_vague_operation_language(
                            summary_text,
                            dominant_phase or stage1_phase,
                        )
                        stage1_summary_text = _expand_vague_operation_language(
                            stage1_summary_text,
                            stage1_phase,
                        )

                        final_visual_visibility = _visibility_flags_from_visual(existing_visual_gpt)
                        final_phase = _canonical_phase(dominant_phase or stage1_phase or "Unknown")
                        if is_live and final_visual_visibility["out_of_body"]:
                            focused_visibility = (
                                (existing_visual_gpt or {}).get("visibility_secondary_review") or {}
                            )
                            focused_external = bool(
                                focused_visibility.get("success")
                                and _safe_float(focused_visibility.get("confidence"), 0.0) >= 0.75
                                and str(focused_visibility.get("classification") or "").lower()
                                in {"external_body", "trocar_transition"}
                            )
                            summary_text = (
                                "镜头移出体外，画面切换至套管口或腹壁外场景。"
                                if focused_external or final_phase != "GallbladderRetraction" else
                                "当前处于标本袋牵拉取出，牵拉装有胆囊的标本袋经切口取出。"
                            )
                        # Stage 2 others_data：保留 stage1 的 experts 数据并附加 CoT + raw others
                        others_data = {
                            "stage": 2,
                            "realtime": bool(is_live),
                            "stage1_summary": stage1_summary_text,
                            "experts": {
                                "phase": expert_pack.get("phase", {}),
                                "triplet": expert_pack.get("triplet", {}),
                                "yolo": {
                                    "tools": expert_pack.get("yolo", {}).get("tools", []),
                                    "total_detections": expert_pack.get("yolo", {}).get("total_detections", 0),
                                },
                                "clip_detector": expert_pack.get("clip_detector", {}),
                                "short_action": expert_pack.get("short_action", {}),
                                "hemlok_clip": expert_pack.get("hemlok_clip", {}),
                                "blue_bipolar_forceps": expert_pack.get("blue_bipolar_forceps", {}),
                                "local_visibility": expert_pack.get("local_visibility", {}),
                                "open_vlm": expert_pack.get("open_vlm", {}),
                                "available": expert_pack.get("available", []),
                            },
                            "visual_gpt": existing_visual_gpt,
                            "model_calls": model_calls,
                            "sequence_rules": _sequence_state_meta(stage2_sequence_state, stage2_sequence_rules),
                            "surgr1_reasoning": surgr1_reasoning,
                            "gemini_others": others_data_raw,
                        }

                        # 覆盖同一窗口记录（mysql_service 已支持 window_id 级别 upsert）
                        create_window_summary(
                            db=db,
                            session_id=db_session_id,
                            window_id=window_id,
                            start_time=meta.get("start_time", 0),
                            end_time=meta.get("end_time", 0),
                            summary_text=summary_text,
                            dominant_phase=dominant_phase,
                            tools_detected=meta.get("consistency", {}).get("cleaned_data", {}).get("tools", []),
                            key_actions=[f.get("action", "")[:200] for f in frame_analyses[:3]],
                            others_data=others_data
                        )
                        
                        # Generate embedding for semantic search
                        _queue_embedding(db_session_id, window_id, summary_text,
                                         meta.get("start_time", 0), meta.get("end_time", 0))

                        # 记录日志
                        analysis_log.log_glm_window(
                            window_id=window_id,
                            start_time=meta.get("start_time", 0),
                            end_time=meta.get("end_time", 0),
                            summary=summary_text,
                            images_loaded=meta.get("images_loaded", 0),
                            frame_count=len(frame_analyses)
                        )

                        processed_windows.add(window_id)
                        logger.info(f"[GLM Task] Window {window_id} saved to DB: {summary_text[:60]}...")
                    
                    batch_elapsed = time_module.time() - batch_start
                    logger.info(
                        f"[GLM Task] Batch completed: {len(sorted_windows)} windows in {batch_elapsed:.2f}s "
                        f"({len(sorted_windows)/max(batch_elapsed, 0.01):.1f} windows/s)"
                    )
                    
                except Exception as e:
                    logger.error(f"GLM concurrent summarization failed: {e}")
                    # 回退到串行处理
                    for window_data in windows_to_process:
                        window_id = window_data["window_id"]
                        meta = window_metadata.get(window_id, {})
                        
                        try:
                            # 尝试获取窗口图片用于多模态验证
                            window_images = window_data.get("images", None)
                            result = await vlm_client.integrate_analysis_results(
                                frame_analyses=window_data["frame_analyses"],
                                images=window_images  # 传入图片（如果有）
                            )
                            summary_text = result.get("summary", "") if result.get("success") else "[分析出错]"
                        except Exception as inner_e:
                            summary_text = f"[分析出错: {str(inner_e)}]"
                        
                        cleaned_data = meta.get("consistency", {}).get("cleaned_data", {})
                        create_window_summary(
                            db=db,
                            session_id=db_session_id,
                            window_id=window_id,
                            start_time=meta.get("start_time", 0),
                            end_time=meta.get("end_time", 0),
                            summary_text=summary_text,
                            dominant_phase=cleaned_data.get("phase", "Unknown"),
                            tools_detected=cleaned_data.get("tools", []),
                            key_actions=[]
                        )
                        processed_windows.add(window_id)
            
            # Small delay before checking for more frames
            await asyncio.sleep(2)
        
        update_session_status(db, session_id, "completed")
        logger.info(f"GLM summarization completed for session {session_id}")
        
    except Exception as e:
        import traceback
        logger.error(f"GLM summarization task error: {e}")
        logger.error(f"GLM task traceback: {traceback.format_exc()}")
        update_session_status(db, session_id, "error")
    finally:
        # Clean up cancellation flag
        analysis_cancellation_flags.pop(session_id, None)
        # Clean up session history and conflict resolver resources
        cleanup_session_resources(session_id)
        # 关闭分析日志
        try:
            from ..services.analysis_logger import close_analysis_logger
            close_analysis_logger(session_id)
        except:
            pass
        db.close()


async def process_video_surgr1_glm_task(
    session_id: str,
    video_path: str,
    db_session_id: int,
    use_chinese: bool = False,
    use_glm_multimodal: bool = False
):
    """Background task to process video with SurgR1 + GLM"""
    
    from ..database import update_session_status
    db = next(get_db())
    
    try:
        processor = VideoProcessor(
            video_path=video_path,
            window_duration=settings.WINDOW_DURATION,
            sample_interval=settings.SAMPLE_INTERVAL
        )
        
        # Get SurgR1 and VLM clients
        surgr1_client = await ensure_surgr1_available()
        vlm_client = await ensure_vlm_available()
        
        # Pipeline overlap: track previous window's Gemini task
        _prev_gemini_task = None  # asyncio.Task for previous window's Gemini call
        _prev_window_meta = None  # metadata needed to save previous window's results
        
        _PHASE_CN_TO_EN = {
            "准备阶段": "Preparation", "准备期": "Preparation",
            "肝胆三角解剖": "CalotTriangleDissection", "Calot三角": "CalotTriangleDissection",
            "夹闭切断": "ClippingCutting", "胆囊分离": "GallbladderDissection",
            "胆囊取出": "GallbladderPackaging", "清洁凝血": "CleaningCoagulation",
            "标本袋牵拉取出": "GallbladderRetraction",
            "胆囊牵拉": "GallbladderRetraction",
        }
        
        def _extract_phase_from_summary(summary: str) -> str:
            for cn, en in _PHASE_CN_TO_EN.items():
                if cn in summary:
                    return en
            return ""
        
        async def _save_gemini_result(task, meta):
            """Await Gemini task and save results to DB + history"""
            try:
                result = await task
                summary_text = ""
                others_data = None
                
                if result.get("success"):
                    summary_text = result.get("summary", "")
                    others_data = result.get("others")
                    
                    gemini_phase = _extract_phase_from_summary(summary_text)
                    r1_phase = result.get("consistency_analysis", {}).get("图像级一致性", {}).get("主导阶段", "Unknown")
                    dominant_phase = gemini_phase if gemini_phase else r1_phase
                    
                    tools_list = [f.get("tools", "")[:50] for f in meta["frame_analyses"][:3] if f.get("tools")]
                    
                    await meta["history_manager"].add_summary(WindowSummary(
                        window_id=meta["window_id"],
                        start_time=meta["start_time"],
                        end_time=meta["end_time"],
                        summary=summary_text[:200],
                        dominant_phase=dominant_phase,
                        tools=tools_list,
                        cvs_status="未评估"
                    ))
                else:
                    summary_text = f"[分析出错: {result.get('error', '未知错误')}]"
                    dominant_phase = None
                
                create_window_summary(
                    db=db,
                    session_id=db_session_id,
                    window_id=meta["window_id"],
                    start_time=meta["start_time"],
                    end_time=meta["end_time"],
                    summary_text=summary_text,
                    tools_detected=[f.get("tools", "")[:200] for f in meta["frame_analyses"]],
                    key_actions=[f.get("action", "")[:200] for f in meta["frame_analyses"]],
                    dominant_phase=dominant_phase,
                    others_data=others_data
                )
                # Generate embedding for semantic search
                _queue_embedding(db_session_id, meta["window_id"], summary_text,
                                 meta["start_time"], meta["end_time"])

                logger.info(f"Completed window {meta['window_id']} with SurgR1+GLM (pipeline)")
            except Exception as e:
                logger.error(f"Failed to save Gemini result for window {meta['window_id']}: {e}")
                create_window_summary(
                    db=db,
                    session_id=db_session_id,
                    window_id=meta["window_id"],
                    start_time=meta["start_time"],
                    end_time=meta["end_time"],
                    summary_text=f"[VLM Error: {str(e)}]",
                    tools_detected=[f.get("tools", "")[:200] for f in meta["frame_analyses"]],
                    key_actions=[f.get("action", "")[:200] for f in meta["frame_analyses"]],
                )
        
        async for window in processor.process_stream():
            # Check cancellation flag at the start of each window
            if analysis_cancellation_flags.get(session_id, False):
                # Wait for any pending Gemini task before cancelling
                if _prev_gemini_task and not _prev_gemini_task.done():
                    await _save_gemini_result(_prev_gemini_task, _prev_window_meta)
                logger.info(f"Analysis cancelled for session {session_id} at window {window.window_id}")
                update_session_status(db, session_id, "cancelled")
                return
            # ==================================================================
            # Step 1: SurgR1 - Batch analyze all frames in window
            # (runs in parallel with previous window's Gemini call)
            # ==================================================================
            # Prepare batch request - collect all frames
            batch_frames = [
                {
                    "image": frame.image,
                    "frame_idx": frame.frame_idx,
                    "timestamp": frame.timestamp
                }
                for frame in window.frames
            ]
            
            try:
                # Single batch API call for all frames in window
                frame_analyses = await surgr1_client.analyze_frames_batch(
                    frames=batch_frames,
                    analysis_type="all",
                    session_id=session_id,
                    save_to_mysql=True
                )
                
                # Save to SQLite database
                for result in frame_analyses:
                    create_frame_analysis(
                        db=db,
                        session_id=db_session_id,
                        frame_idx=result.get("frame_idx"),
                        timestamp=result.get("timestamp"),
                        tool_localization=result.get("tools", ""),
                        surgical_action=result.get("action", ""),
                        surgical_phase=result.get("phase", "")
                    )
                    
                logger.info(f"Batch analyzed {len(frame_analyses)} frames in window {window.window_id}")
                
            except Exception as e:
                logger.warning(f"SurgR1 batch analysis failed for window {window.window_id}: {e}")
                # Fallback: create empty analyses
                frame_analyses = [
                    {
                        "frame_idx": frame.frame_idx,
                        "timestamp": frame.timestamp,
                        "phase": "",
                        "action": "",
                        "tools": ""
                    }
                    for frame in window.frames
                ]

            # ==================================================================
            # Step 1.5: Await previous window's Gemini result (if any)
            # Must complete before building history_context for current window
            # ==================================================================
            if _prev_gemini_task and not _prev_gemini_task.done():
                await _save_gemini_result(_prev_gemini_task, _prev_window_meta)
                _prev_gemini_task = None
                _prev_window_meta = None

            # ==================================================================
            # Step 2: VLM - Fire off Gemini as background task (pipeline overlap)
            # ==================================================================
            try:
                # 获取上一窗口的摘要作为历史上下文，保持阶段连续性
                from ..services.vlm_factory import get_history_manager
                from ..services.glm_client import WindowSummary
                history_manager = get_history_manager(session_id)
                history_context = await history_manager.build_history_context()
                
                # 提取窗口帧图片用于VLM多模态验证
                window_images = [frame.image for frame in window.frames if frame.image is not None]
                
                # 创建 Gemini 调用的协程（不立即 await）
                async def _run_gemini(fa, wi, hc, vlm):
                    return await vlm.integrate_analysis_results(
                        frame_analyses=fa,
                        images=wi,
                        system_prompt=None,
                        temperature=0.9,
                        max_tokens=1500,
                        history_context=hc
                    )
                
                # 保存当前窗口的元数据
                _prev_window_meta = {
                    "window_id": window.window_id,
                    "start_time": window.start_time,
                    "end_time": window.end_time,
                    "frame_analyses": frame_analyses,
                    "history_manager": history_manager,
                }
                
                # 启动 Gemini task（不阻塞，下一个窗口的 R1 可以立即开始）
                _prev_gemini_task = asyncio.create_task(
                    _run_gemini(frame_analyses, window_images, history_context, vlm_client)
                )
                logger.info(f"Launched Gemini task for window {window.window_id} (pipeline overlap)")
                
            except Exception as e:
                logger.error(f"VLM task creation failed for window {window.window_id}: {e}")
                # 同步保存错误结果
                create_window_summary(
                    db=db,
                    session_id=db_session_id,
                    window_id=window.window_id,
                    start_time=window.start_time,
                    end_time=window.end_time,
                    summary_text=f"[VLM Error: {str(e)}]",
                    tools_detected=[f.get("tools", "")[:200] for f in frame_analyses],
                    key_actions=[f.get("action", "")[:200] for f in frame_analyses],
                )
                _prev_gemini_task = None
                _prev_window_meta = None
        
        # ==================================================================
        # After loop: await the last window's Gemini task
        # ==================================================================
        if _prev_gemini_task and not _prev_gemini_task.done():
            await _save_gemini_result(_prev_gemini_task, _prev_window_meta)
        
        # Update session status
        update_session_status(db, session_id, "completed")
        
    except Exception as e:
        logger.error(f"SurgR1+GLM processing failed: {e}")
        update_session_status(db, session_id, "error")
        raise
    finally:
        # Clean up cancellation flag
        analysis_cancellation_flags.pop(session_id, None)
        db.close()


@router.get("/frame-analysis/{session_id}")
async def get_frame_analysis(
    session_id: str,
    timestamp: float = Query(..., ge=0, description="Frame timestamp"),
    db: Session = Depends(get_db)
):
    """
    Get SurgR1 analysis for a specific frame (nearest to timestamp)
    
    This is used when user drags the progress bar to show single-frame analysis.
    """
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Get frame analyses near the target timestamp (within ±2 seconds to avoid limit=100 issue)
    frames = get_frames_by_session(
        db, 
        session["session_id"],
        start_time=max(0, timestamp - 2.0),
        end_time=timestamp + 2.0
    )
    
    if not frames:
        return {
            "found": False,
            "message": "No frame analyses available yet",
            "timestamp": timestamp
        }
    
    # Find nearest frame to the requested timestamp (frames is a list of dicts)
    nearest_frame = min(frames, key=lambda f: abs(f["timestamp"] - timestamp))
    
    # Only return if within 1 second of requested time
    if abs(nearest_frame["timestamp"] - timestamp) > 1.0:
        return {
            "found": False,
            "message": "No frame analysis near this timestamp",
            "timestamp": timestamp,
            "nearest_timestamp": nearest_frame["timestamp"]
        }
    
    return {
        "found": True,
        "frame_idx": nearest_frame["frame_idx"],
        "timestamp": nearest_frame["timestamp"],
        "tool_localization": nearest_frame.get("tool_localization") or "",
        "surgical_action": nearest_frame.get("surgical_action") or "",
        "surgical_phase": nearest_frame.get("surgical_phase") or "",
        "window_id": int(nearest_frame["timestamp"] / settings.WINDOW_DURATION)
    }


@router.post("/analyze-single-frame")
async def analyze_single_frame(
    session_id: str,
    timestamp: float = Query(..., ge=0, description="Frame timestamp"),
    db: Session = Depends(get_db)
):
    """
    Analyze a single frame with SurgR1 on-demand
    
    This is used when user clicks on a specific frame and wants fresh analysis.
    """
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Create video processor
    processor = VideoProcessor(
        video_path=session["video_path"],
        window_duration=settings.WINDOW_DURATION,
        sample_interval=settings.SAMPLE_INTERVAL
    )
    
    # Extract single frame
    frame = processor.extract_frame(timestamp)
    
    if frame is None:
        raise HTTPException(400, f"Could not extract frame at timestamp {timestamp}")
    
    # Get SurgR1 client and analyze
    try:
        surgr1_client = await ensure_surgr1_available()
        
        result = await surgr1_client.analyze_frame(
            image=frame.image,
            analysis_type="all",
            session_id=session_id,
            frame_idx=frame.frame_idx,
            timestamp=frame.timestamp,
            save_to_mysql=True
        )
        
        # Save to SQLite database
        create_frame_analysis(
            db=db,
            session_id=session["session_id"],
            frame_idx=frame.frame_idx,
            timestamp=frame.timestamp,
            tool_localization=result.get("tools", ""),
            surgical_action=result.get("action", ""),
            surgical_phase=result.get("phase", "")
        )
        
        return {
            "success": True,
            "frame_idx": frame.frame_idx,
            "timestamp": frame.timestamp,
            "tool_localization": result.get("tools", ""),
            "surgical_action": result.get("action", ""),
            "surgical_phase": result.get("phase", ""),
            "window_id": int(timestamp / settings.WINDOW_DURATION)
        }
        
    except Exception as e:
        logger.error(f"Single frame analysis failed: {e}")
        raise HTTPException(500, f"Analysis failed: {str(e)}")


async def process_video_task(
    session_id: str,
    video_path: str,
    db_session_id: int,
    use_chinese: bool = False
):
    """Background task to process entire video"""
    
    db = next(get_db())
    
    try:
        processor = VideoProcessor(
            video_path=video_path,
            window_duration=settings.WINDOW_DURATION,
            sample_interval=settings.SAMPLE_INTERVAL
        )
        
        summarizer = get_gpt_summarizer()
        summarizer.use_chinese = use_chinese
        
        async for window in processor.process_stream():
            # Build context
            context = build_frame_context(window)
            
            # Generate summary
            result = await summarizer.summarize_window(
                images=window.get_images(),
                context=context,
                system_prompt=ANALYSIS_SYSTEM_PROMPT
            )
            
            if result["success"]:
                # Save frame analyses
                for frame in window.frames:
                    create_frame_analysis(
                        db=db,
                        session_id=db_session_id,
                        frame_idx=frame.frame_idx,
                        timestamp=frame.timestamp
                    )
                
                # Save summary
                create_window_summary(
                    db=db,
                    session_id=db_session_id,
                    window_id=window.window_id,
                    start_time=window.start_time,
                    end_time=window.end_time,
                    summary_text=result["summary"]
                )

                # Generate embedding for semantic search
                _queue_embedding(db_session_id, window.window_id, result["summary"],
                                 window.start_time, window.end_time)

        # Update session status
        from ..database import update_session_status
        update_session_status(db, session_id, "completed")
        
    except Exception as e:
        from ..database import update_session_status
        update_session_status(db, session_id, "error")
        raise
    finally:
        db.close()


@router.get("/summaries/{session_id}")
async def get_session_summaries(
    session_id: str,
    db: Session = Depends(get_db)
):
    """Get all summaries for a session"""
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    summaries = get_summaries_by_session(db, session["session_id"])
    
    out = []
    for s in summaries:
        others = s.get("others") or {}
        payload = {
            "window_id": s.get("window_id"),
            "start_time": s.get("window_start"),
            "end_time": s.get("window_end"),
            "summary": s.get("glm_summary", ""),
            "surgical_phase": s.get("surgical_phase") or "Unknown",
            "tts_audio_path": s.get("image_path")  # Use image_path as fallback, no tts_audio_path in new schema
        }
        if isinstance(others, dict):
            payload["stage"] = int(others.get("stage", 2) or 2)
            if others.get("stage1_summary"):
                payload["stage1_summary"] = others.get("stage1_summary")
            payload["others"] = others
        out.append(payload)
    return out


@router.get("/summary-telemetry/{session_id}")
async def get_summary_telemetry(
    session_id: str,
    db: Session = Depends(get_db),
):
    """Return lightweight per-window readiness data for realtime benchmarks."""
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    rows = []
    for summary in get_summaries_by_session(db, session["session_id"]):
        others = summary.get("others") or {}
        experts = (others.get("experts") or {}) if isinstance(others, dict) else {}
        open_vlm = (experts.get("open_vlm") or {}) if isinstance(experts, dict) else {}
        model_calls = (others.get("model_calls") or {}) if isinstance(others, dict) else {}
        visual_calls = (model_calls.get("open_visual_gpt") or {}) if isinstance(model_calls, dict) else {}
        visual = (open_vlm.get("visual") or {}) if isinstance(open_vlm, dict) else {}
        rows.append({
            "window_id": int(summary.get("window_id") or 0),
            "start_time": _safe_float(summary.get("window_start"), 0.0),
            "end_time": _safe_float(summary.get("window_end"), 0.0),
            "stage": int(others.get("stage", 1) or 1) if isinstance(others, dict) else 1,
            "phase": summary.get("surgical_phase") or "Unknown",
            "vlm_complete": bool(isinstance(open_vlm, dict) and open_vlm.get("success")),
            "vlm_call_count": int(visual_calls.get("count") or 0) if isinstance(visual_calls, dict) else 0,
            "special_reviews": {
                "clip": bool(visual.get("clip_secondary_review")) if isinstance(visual, dict) else False,
                "scissors": bool(
                    visual.get("scissors_secondary_review")
                    or (
                        str((visual.get("clip_secondary_review") or {}).get("classification") or "").lower()
                        == "scissors"
                    )
                ) if isinstance(visual, dict) else False,
                "visibility": bool(visual.get("visibility_secondary_review")) if isinstance(visual, dict) else False,
            },
        })
    return {
        "session_id": session_id,
        "count": len(rows),
        "windows": sorted(rows, key=lambda row: row["window_id"]),
    }


@router.get("/pending-refinements/{session_id}")
async def get_pending_refinements(
    session_id: str,
    db: Session = Depends(get_db),
):
    """Return the number of local VLM window refinements still in flight."""
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    tasks = active_open_vlm_tasks.get(session["session_id"], set())
    pending = sum(1 for task in tasks if task and not task.done())
    glm_task = active_glm_tasks.get(session["session_id"])
    analysis_running = bool(glm_task and not glm_task.done())
    return {
        "session_id": session_id,
        "pending": pending,
        "analysis_running": analysis_running,
        "settled": pending == 0 and not analysis_running,
    }


@router.post("/translate-summary")
async def translate_summary(request: TranslateSummaryRequest):
    """Translate one window summary for UI language switching.

    This is intentionally text-only and cached in memory. It does not alter the
    stored clinical analysis; it only provides a localized display string.
    """
    source = (request.text or "").strip()
    target = (request.target_lang or "en").lower()
    if not source:
        return {"success": True, "text": "", "target_lang": target, "cached": False}
    if target.startswith("zh"):
        return {"success": True, "text": source, "target_lang": target, "cached": True}

    key = (target, source)
    if key in summary_translation_cache:
        return {
            "success": True,
            "text": summary_translation_cache[key],
            "target_lang": target,
            "cached": True,
        }

    def local_translate_summary_en(text: str) -> str:
        """Fast local fallback for short laparoscopic cholecystectomy UI summaries.

        This is deliberately conservative: it compresses known surgical findings
        into professional English and preserves fixed terms instead of trying to
        be a general-purpose translator.
        """
        src = re.sub(r"\s+", " ", str(text or "")).strip()
        src = re.sub(r"^【[^】]*】\s*", "", src)
        src = re.sub(r"当前处于[^，。；;]+[，,。；;]?\s*", "", src)
        src = re.sub(r"(?:可见|视野中可见)(?:抓钳|施夹器|施夹钳|钛夹钳|剪刀|电凝钩|电钩|冲吸器|冲洗器|吸引器|器械)(?:、(?:抓钳|施夹器|施夹钳|钛夹钳|剪刀|电凝钩|电钩|冲吸器|冲洗器|吸引器|器械))*[，,。；;]?\s*", "", src)

        sentences: List[str] = []
        lower = src.lower()

        def add(sentence: str) -> None:
            if sentence and sentence not in sentences:
                sentences.append(sentence)

        if re.search(r"hem[-\s]?o[-\s]?lok|hemolok|hemlock", lower, re.IGNORECASE):
            add("A Hem-o-lok clip is placed on the cystic duct.")

        if re.search(r"(肝胆三角|胆囊三角|calot)", src, re.IGNORECASE) and re.search(r"(游离|分离|电凝|剥离|点触)", src):
            add("Dissection is performed in the hepatocystic triangle, separating adhesions and connective tissue around the cystic duct.")

        if re.search(r"(胆囊管|胆囊动脉|管状结构)", src) and re.search(r"(夹闭|施夹|闭合)", src):
            if "胆囊动脉" in src:
                add("The isolated cystic artery is clipped.")
            else:
                add("The isolated cystic duct is clipped.")

        if re.search(r"(剪刀|剪切|切断|夹断)", src) and re.search(r"(胆囊管|胆囊动脉|管状结构|组织)", src):
            if "胆囊动脉" in src:
                add("The clipped cystic artery is divided.")
            else:
                add("The clipped cystic duct is divided.")

        if re.search(r"(胆囊分离|胆囊与肝床|胆囊床|肝床)", src) and re.search(r"(游离|分离|剪切|剥离|牵拉)", src):
            add("The gallbladder is dissected from the liver bed under traction.")

        if re.search(r"(冲吸器|冲洗|吸引|清理)", src):
            add("Suction and irrigation are used to clear the operative field.")

        if re.search(r"(穿刺|穿入|穿孔|钻孔)", src):
            add("A puncture or entry maneuver is noted.")

        if re.search(
            r"(大量(?:活动性)?出血|明显(?:活动性)?出血|持续(?:活动性)?出血|"
            r"喷涌出血|喷射性出血|涌血|明确出血源|影响视野的持续渗血)",
            src,
        ):
            add("Active bleeding is noted and requires attention.")
        elif re.search(r"(少量出血|少量渗血|渗血|局部.*出血|出血)", src):
            add("Minor local bleeding or oozing is noted.")

        if re.search(r"(止血|凝血|出血.*控制|无活动性出血|未见活动性出血)", src):
            add("Hemostasis is achieved or no active bleeding is seen.")

        if re.search(r"(牵拉|夹持)", src) and re.search(r"(胆囊|组织)", src):
            add("Traction is maintained to expose the operative field.")

        if sentences:
            return " ".join(sentences[:3])

        replacements = [
            ("Hem-o-lok", "Hem-o-lok clip"),
            ("Hemolok", "Hem-o-lok clip"),
            ("胆囊管", "cystic duct"),
            ("胆囊动脉", "cystic artery"),
            ("肝胆三角", "hepatocystic triangle"),
            ("Calot三角", "Calot's triangle"),
            ("胆囊床", "gallbladder bed"),
            ("肝床", "liver bed"),
            ("钛夹", "titanium clip"),
            ("施夹器", "clip applier"),
            ("施夹钳", "clip applier"),
            ("钛夹钳", "titanium clip applier"),
            ("抓钳", "grasper"),
            ("电凝钩", "electrocautery hook"),
            ("电钩", "electrocautery hook"),
            ("剪刀", "scissors"),
            ("出血", "bleeding"),
            ("止血", "hemostasis"),
            ("夹闭切断", "clipping and division"),
            ("胆囊分离", "gallbladder dissection"),
        ]
        out = src
        for cn, en in replacements:
            out = out.replace(cn, en)
        return out[:260]

    config = load_config()
    translation_cfg = config.get("services", {}).get("translation", {})
    provider = translation_cfg.get("provider", "gemini")
    model_name = translation_cfg.get("model_name")
    max_tokens = int(translation_cfg.get("max_tokens", 220))
    temperature = float(translation_cfg.get("temperature", 0.0))

    system_prompt = (
        "You are a surgical UI localization assistant. Translate the Chinese "
        "laparoscopic cholecystectomy window summary into concise professional "
        "English. Preserve instruments, anatomy, actions, bleeding status, and "
        "puncture/contact uncertainty. Do not add new findings. Output only the "
        "translated sentence(s), no markdown."
    )
    prompt = f"Translate to English:\n{source}"

    try:
        if provider == "glm":
            client = get_glm_client()
            result = await client.chat(
                message=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                disable_thinking=True,
            )
        else:
            from ..services.gemini_client import GeminiClient
            client = GeminiClient(
                model_name=model_name,
                thinking_level=translation_cfg.get("thinking_level", "none"),
                max_tokens=max_tokens,
            )
            result = await client.chat(
                message=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        text = (result.get("text") or "").strip()
        if not result.get("success") or not text:
            raise RuntimeError(result.get("error") or "empty translation")
        summary_translation_cache[key] = text
        return {
            "success": True,
            "text": text,
            "target_lang": target,
            "cached": False,
            "provider": provider,
            "model": model_name or result.get("model"),
        }
    except Exception as exc:
        logger.warning(f"[Translate] Failed to translate summary: {exc}")
        fallback_text = local_translate_summary_en(source)
        summary_translation_cache[key] = fallback_text
        return {
            "success": True,
            "text": fallback_text,
            "target_lang": target,
            "cached": False,
            "provider": "local_rule_fallback",
            "error": str(exc),
        }


FOG_ACTIVE_RE = re.compile(
    r"(镜头)?(?:起雾|雾气|烟雾|烟雾弥漫|水汽|模糊|视野不清|视野受遮挡|视野受限|"
    r"fogging|foggy|fog (?:obscures|obscured|limits|blocks)|smoke|smoky|haze|hazy|blur(?:red|ry)?|obscur(?:ed|ing))",
    re.IGNORECASE,
)
FOG_RESOLVED_RE = re.compile(
    r"(雾(?:已|已经)?(?:去除|清除|消散|解除)|烟雾(?:已|已经)?(?:清除|消散)|"
    r"视野(?:恢复|转为|变得)(?:清晰|可辨)|镜头(?:恢复|转为|变得)清晰|"
    r"fog (?:cleared|resolved)|smoke (?:cleared|resolved)|view (?:restored|clear))",
    re.IGNORECASE,
)
OUT_OF_BODY_RE = re.compile(
    r"(镜头|腹腔镜|视野|画面).{0,12}(?:移出体外|退出体外|离开腹腔|腹腔外|腹壁外|切换至手术室|手术室场景)|"
    r"(?:体外|腹腔外|腹壁外|手术室场景|器械台|trocar outside|outside the body|outside-body|extracorporeal|operating room scene|extra-abdominal)",
    re.IGNORECASE,
)


def _visibility_flags_from_visual(visual: Any) -> Dict[str, Any]:
    data = visual if isinstance(visual, dict) else {}
    visibility = data.get("visibility") if isinstance(data.get("visibility"), dict) else {}
    status = str(visibility.get("status") or "").strip().lower()
    confidence = _safe_float(visibility.get("confidence"), 0.0)
    fog_active = bool(
        visibility.get("fog")
        or status in {"foggy", "blurred", "blocked", "smoke", "smoky", "hazy"}
    )
    fog_resolved = bool(
        visibility.get("fog_cleared")
        or visibility.get("cleared")
        or visibility.get("resolved")
        or status in {"clear_after_fog", "fog_cleared"}
    )
    out_of_body = bool(
        visibility.get("out_of_body")
        or visibility.get("outside_body")
        or status == "out_of_body"
    )
    if confidence and confidence < 0.35:
        fog_active = False
        fog_resolved = False
        out_of_body = False
    return {
        "fog_active": fog_active,
        "fog_resolved": fog_resolved,
        "out_of_body": out_of_body,
        "confidence": confidence,
        "status": status or "clear",
    }


def _should_review_visibility_candidate(
    visual: Optional[Dict[str, Any]],
    local_cue: Optional[Dict[str, Any]],
) -> bool:
    """Review plausible scope exits in every surgical phase, not only retrieval."""
    flags = _visibility_flags_from_visual(visual or {})
    cue = local_cue or {}
    visual_exit_claim = flags["out_of_body"]
    candidate = bool(
        visual_exit_claim
        or cue.get("out_of_body_candidate")
        or cue.get("out_of_body")
    )
    strong_geometry = bool(
        _safe_float(cue.get("inner_tissue"), 1.0) <= 0.24
        and _safe_float(cue.get("annulus_bright"), 0.0) >= 0.28
        and _safe_float(cue.get("overall_bright"), 0.0) >= 0.22
    )
    return candidate and bool(
        visual_exit_claim
        or strong_geometry
        or flags["fog_active"]
        or cue.get("out_of_body")
        or _safe_float(cue.get("out_of_body_confidence"), 0.0) >= 0.55
    )


def _scissors_tokens_from_visual(visual: Any) -> str:
    data = visual if isinstance(visual, dict) else {}
    scissors = data.get("scissors") if isinstance(data.get("scissors"), dict) else {}
    if not scissors:
        return ""
    confidence = _safe_float(scissors.get("confidence"), 0.0)
    if confidence and confidence < 0.35:
        return ""
    tokens = []
    if scissors.get("visible"):
        tokens.append("scissors_visible")
    if scissors.get("cutting") and (not confidence or confidence >= 0.45):
        tokens.append("scissors_cutting")
    target = _target_label_from_raw(scissors.get("target"))
    if target:
        tokens.append(f"target_{target}")
    return ",".join(tokens)


def _summary_text_and_visual(summary: Any) -> Tuple[int, str, Dict[str, Any], float, float]:
    if isinstance(summary, dict):
        window_id = int(summary.get("window_id") or 0)
        text = str(summary.get("glm_summary") or summary.get("summary") or summary.get("summary_text") or "")
        start = _safe_float(summary.get("window_start", summary.get("start_time", 0)), 0.0)
        end = _safe_float(summary.get("window_end", summary.get("end_time", start)), start)
        others = summary.get("others") or {}
    else:
        window_id = int(getattr(summary, "window_id", 0) or 0)
        text = str(getattr(summary, "summary_text", getattr(summary, "glm_summary", "")) or "")
        start = _safe_float(getattr(summary, "start_time", getattr(summary, "window_start", 0)), 0.0)
        end = _safe_float(getattr(summary, "end_time", getattr(summary, "window_end", start)), start)
        others = getattr(summary, "others_data", None) or getattr(summary, "others", None) or {}
    if isinstance(others, str):
        try:
            others = json.loads(others) if others else {}
        except Exception:
            others = {}
    visual = {}
    if isinstance(others, dict):
        visual = others.get("visual_gpt") or ((others.get("experts") or {}).get("open_vlm") or {}).get("visual") or {}
    return window_id, text, visual if isinstance(visual, dict) else {}, start, end


def _visibility_state_from_summaries(summaries: List[Any], before_window_id: Optional[int] = None) -> Dict[str, Any]:
    fog_active = False
    first_active = None
    latest_active = None
    latest_resolved = None
    latest_out_of_body = None
    for item in sorted(summaries or [], key=lambda s: _summary_text_and_visual(s)[0]):
        window_id, text, visual, start, end = _summary_text_and_visual(item)
        if before_window_id is not None and window_id >= before_window_id:
            continue
        flags = _visibility_flags_from_visual(visual)
        active = flags["fog_active"] or bool(FOG_ACTIVE_RE.search(text))
        resolved = flags["fog_resolved"] or bool(FOG_RESOLVED_RE.search(text))
        out_of_body = flags["out_of_body"] or bool(OUT_OF_BODY_RE.search(text))
        record = {
            "window_id": window_id,
            "summary": text,
            "start_time": start,
            "end_time": end,
            "confidence": flags.get("confidence") or 0.62,
        }
        if active:
            if not fog_active:
                first_active = record
            fog_active = True
            latest_active = record
        if resolved:
            latest_resolved = record
            fog_active = False
        if out_of_body:
            latest_out_of_body = record
    return {
        "fog_active": fog_active,
        "first_active": first_active,
        "latest_active": latest_active,
        "latest_resolved": latest_resolved,
        "latest_out_of_body": latest_out_of_body,
    }


def _visibility_summary_for_window(
    visual: Any,
    previous_summaries: List[Any],
    before_window_id: Optional[int] = None,
    language: str = "zh",
) -> str:
    zh = not str(language or "zh").lower().startswith("en")
    flags = _visibility_flags_from_visual(visual)
    previous = _visibility_state_from_summaries(previous_summaries, before_window_id=before_window_id)
    visibility_payload = visual.get("visibility") if isinstance(visual, dict) else None
    explicit_clear = bool(
        isinstance(visibility_payload, dict)
        and flags["status"] == "clear"
        and not flags["fog_active"]
        and not flags["out_of_body"]
    )
    previous_explicit_clear = False
    prior_rows = []
    for item in previous_summaries or []:
        row = _summary_text_and_visual(item)
        if before_window_id is None or row[0] < before_window_id:
            prior_rows.append(row)
    if prior_rows:
        _, prior_text, prior_visual, _, _ = max(prior_rows, key=lambda row: row[0])
        prior_flags = _visibility_flags_from_visual(prior_visual)
        prior_payload = prior_visual.get("visibility") if isinstance(prior_visual, dict) else None
        previous_explicit_clear = bool(
            isinstance(prior_payload, dict)
            and prior_flags["status"] == "clear"
            and not prior_flags["fog_active"]
            and not prior_flags["out_of_body"]
            and not FOG_ACTIVE_RE.search(prior_text)
        )
    if flags["out_of_body"]:
        return "镜头移出体外，画面切换至套管口或腹壁外场景。" if zh else "The scope is moved outside the body and the view switches to the trocar or extra-abdominal scene."
    if flags["fog_active"]:
        return "镜头起雾，手术视野受遮挡。" if zh else "Lens fogging obscures the surgical field."
    if flags["fog_resolved"] or (
        previous.get("fog_active")
        and explicit_clear
        and previous_explicit_clear
    ):
        return "雾已去除，腹腔视野恢复。" if zh else "The fog has cleared and the laparoscopic view is restored."
    return ""


def _normalize_summary_for_event_nodes(summary: Any) -> Optional[Dict[str, Any]]:
    """Convert stored summary rows into compact records for event-node LLM input."""
    if isinstance(summary, dict):
        others = summary.get("others") or {}
        if isinstance(others, str):
            try:
                others = json.loads(others)
            except Exception:
                others = {}
        window_id = summary.get("window_id")
        start_time = summary.get("window_start", summary.get("start_time", 0))
        end_time = summary.get("window_end", summary.get("end_time", 0))
        text = summary.get("glm_summary") or summary.get("summary") or summary.get("summary_text") or ""
        phase = (
            summary.get("surgical_phase")
            or summary.get("phase")
            or (others.get("phase") if isinstance(others, dict) else None)
            or "Unknown"
        )
    else:
        others = getattr(summary, "others_data", None) or getattr(summary, "others", None) or {}
        if isinstance(others, str):
            try:
                others = json.loads(others)
            except Exception:
                others = {}
        window_id = getattr(summary, "window_id", None)
        start_time = getattr(summary, "start_time", getattr(summary, "window_start", 0))
        end_time = getattr(summary, "end_time", getattr(summary, "window_end", 0))
        text = getattr(summary, "summary_text", getattr(summary, "glm_summary", ""))
        phase = getattr(summary, "surgical_phase", None) or (others.get("phase") if isinstance(others, dict) else None) or "Unknown"

    if window_id is None:
        return None
    try:
        window_id = int(window_id)
    except Exception:
        return None

    try:
        start_time = float(start_time or 0)
        end_time = float(end_time or start_time)
    except Exception:
        start_time, end_time = 0.0, 0.0

    stage = 2
    if isinstance(others, dict):
        try:
            stage = int(others.get("stage", 2) or 2)
        except Exception:
            stage = 2
    visual = {}
    if isinstance(others, dict):
        visual = others.get("visual_gpt") or ((others.get("experts") or {}).get("open_vlm") or {}).get("visual") or {}
    visual = visual if isinstance(visual, dict) else {}
    visibility_flags = _visibility_flags_from_visual(visual)
    visibility_tokens = []
    if visibility_flags["fog_active"]:
        visibility_tokens.append("fog_active")
    if visibility_flags["fog_resolved"]:
        visibility_tokens.append("fog_resolved")
    if visibility_flags["out_of_body"]:
        visibility_tokens.append("out_of_body")
    scissors_tokens = _scissors_tokens_from_visual(visual)
    scissors_review = "unavailable"
    if isinstance(others, dict):
        experts = others.get("experts") or {}
        yolo_tools = ((experts.get("yolo") or {}).get("tools") or [])
        open_vlm = experts.get("open_vlm") or {}
        reviewed_scissors = ((open_vlm.get("visual") or {}).get("scissors") or {})
        visual_rejected_scissors = bool(
            open_vlm.get("success")
            and isinstance(reviewed_scissors, dict)
            and not reviewed_scissors.get("visible")
            and not reviewed_scissors.get("cutting")
        )
        if visual_rejected_scissors:
            scissors_review = "rejected"
        elif open_vlm.get("success") and reviewed_scissors:
            scissors_review = "positive" if (
                reviewed_scissors.get("visible") or reviewed_scissors.get("cutting")
            ) else "unavailable"
        robust_yolo_scissors = any(
            isinstance(tool, dict)
            and str(tool.get("label") or "").lower() == "scissors"
            and int(tool.get("frames_seen") or 0) >= 3
            for tool in yolo_tools
        )
        if robust_yolo_scissors and not visual_rejected_scissors:
            scissors_tokens = ",".join(filter(None, (scissors_tokens, "yolo_scissors")))

    return {
        "window_id": window_id,
        "display_window": window_id + 1,
        "start_time": start_time,
        "end_time": end_time,
        "summary": str(text or "").strip(),
        "phase": str(phase or "Unknown"),
        "stage": stage,
        "visibility": ",".join(visibility_tokens) or "clear",
        "scissors": scissors_tokens,
        "scissors_review": scissors_review,
    }


def _event_nodes_signature(records: List[Dict[str, Any]]) -> str:
    payload = [
        {
            "w": r.get("window_id"),
            "p": r.get("phase"),
            "s": r.get("stage"),
            "v": r.get("visibility"),
            "sc": r.get("scissors"),
            "scr": r.get("scissors_review"),
            "t": r.get("summary", ""),
        }
        for r in records
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _strip_json_fences(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _json_loads_with_llm_repairs(text: str) -> Any:
    candidates = [text]
    repaired = text
    repaired = repaired.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(r"}\s*(?=\{)", "},", repaired)
    repaired = re.sub(r'([}\]])\s*\n\s*"', r'\1,\n"', repaired)
    repaired = re.sub(r'("(?:[^"\\]|\\.)*")\s*\n\s*"', r'\1,\n"', repaired)
    repaired = re.sub(r"\b(true|false|null)\s*\n\s*\"", r'\1,\n"', repaired)
    repaired = re.sub(r"(-?\d+(?:\.\d+)?)\s*\n\s*\"", r'\1,\n"', repaired)
    key_ahead = r'"(?=[A-Za-z_][A-Za-z0-9_]*"\s*:)'
    repaired = re.sub(rf'([}}\]])\s+({key_ahead})', r'\1, \2', repaired)
    repaired = re.sub(rf'("(?:[^"\\]|\\.)*")\s+({key_ahead})', r'\1, \2', repaired)
    repaired = re.sub(rf"\b(true|false|null)\s+({key_ahead})", r'\1, \2', repaired)
    repaired = re.sub(rf"(-?\d+(?:\.\d+)?)\s+({key_ahead})", r'\1, \2', repaired)
    if repaired != text:
        candidates.append(repaired)

    last_exc: Optional[Exception] = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception as exc:
            last_exc = exc
    raise last_exc or ValueError("invalid JSON")


def _extract_balanced_json_objects(text: str) -> List[str]:
    objects: List[str] = []
    start: Optional[int] = None
    depth = 0
    in_string = False
    escape = False
    for idx, ch in enumerate(text or ""):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start:idx + 1])
                start = None
    return objects


def _extract_events_array_text(text: str) -> str:
    match = re.search(r'"events"\s*:', text or "", flags=re.IGNORECASE)
    if not match:
        return ""
    start = (text or "").find("[", match.end())
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start + 1:idx]
    return text[start + 1:]


def _parse_event_nodes_from_objects(text: str) -> List[Dict[str, Any]]:
    array_text = _extract_events_array_text(text)
    if not array_text:
        return []
    events: List[Dict[str, Any]] = []
    for obj_text in _extract_balanced_json_objects(array_text):
        try:
            item = _json_loads_with_llm_repairs(obj_text)
        except Exception:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def _parse_event_nodes_json(text: str) -> Dict[str, Any]:
    cleaned = _strip_json_fences(text)
    try:
        parsed = _json_loads_with_llm_repairs(cleaned)
    except Exception:
        events = _parse_event_nodes_from_objects(cleaned)
        if events:
            return {"events": events}
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        matched = match.group(0)
        try:
            parsed = _json_loads_with_llm_repairs(matched)
        except Exception:
            events = _parse_event_nodes_from_objects(matched)
            if events:
                return {"events": events}
            raise
    if isinstance(parsed, list):
        return {"events": parsed}
    if not isinstance(parsed, dict):
        raise ValueError("event node response must be a JSON object")
    return parsed


def _coerce_event_window_ids(raw_ids: Any, valid_ids: set) -> List[int]:
    if raw_ids is None:
        return []
    if isinstance(raw_ids, (int, float)):
        candidates = [int(raw_ids)]
    elif isinstance(raw_ids, str):
        candidates = [int(x) for x in re.findall(r"-?\d+", raw_ids)]
    elif isinstance(raw_ids, list):
        candidates = []
        for item in raw_ids:
            try:
                candidates.append(int(item))
            except Exception:
                if isinstance(item, str):
                    candidates.extend(int(x) for x in re.findall(r"-?\d+", item))
    else:
        candidates = []

    seen = set()
    out = []
    for wid in candidates:
        if wid in valid_ids and wid not in seen:
            out.append(wid)
            seen.add(wid)
    return sorted(out)


def _has_explicit_clip_evidence(text: Any) -> bool:
    """Clip event evidence must be an action, not just the ClippingCutting phase name."""
    src = str(text or "")
    if not src:
        return False
    explicit_patterns = (
        r"hem[-\s]?o[-\s]?lok|hemolok|hemlock|金属钛夹|钛夹(?!钳)|施夹",
        r"已释放夹子|夹子已(?:放置|释放)|可见夹子",
        r"已夹闭|已闭合|夹闭残端|闭合残端",
        r"(?:钛夹钳|施夹器|施夹钳).{0,10}(?:夹闭|闭合)",
        r"(?:夹闭|闭合).{0,10}(?:胆囊管|胆囊动脉|残端)",
        r"(?:胆囊管|胆囊动脉|残端).{0,10}(?:夹闭|闭合)",
        r"\bclip(?:ped|ping|s)?\b",
    )
    if not any(re.search(pattern, src, re.IGNORECASE) for pattern in explicit_patterns):
        return False
    # Generic phase/safety phrasing is not enough to create a clip-placement event.
    generic_only = re.sub(
        r"(夹闭切断|夹闭、切断|夹闭和切断|夹闭前后|夹闭\/剪断|clipping and division|clipping/cutting)",
        "",
        src,
        flags=re.IGNORECASE,
    )
    return any(re.search(pattern, generic_only, re.IGNORECASE) for pattern in explicit_patterns)


def _looks_like_clip_event(text: Any) -> bool:
    src = str(text or "")
    return bool(re.search(
        r"(胆囊管夹闭|胆囊动脉夹闭|夹子放置|已释放夹子|夹闭|闭合|钛夹|施夹|hem[-\s]?o[-\s]?lok|clip)",
        src,
        re.IGNORECASE,
    ))


def _record_has_scissors_activity(record: Dict[str, Any]) -> bool:
    summary = str(record.get("summary", "") or "")
    phase = _canonical_phase(str(record.get("phase") or "Unknown"))
    if phase not in CVS_RELEVANT_PHASES:
        return False
    if re.search(
        r"胆囊取出与装袋|标本袋|装袋|取出后|术野复查|清洁凝血|移出体外|"
        r"specimen bag|post-removal|field review|outside body",
        summary,
        re.IGNORECASE,
    ):
        return False
    if str(record.get("scissors_review") or "") == "rejected":
        return False
    scissors_tokens = {token.strip() for token in str(record.get("scissors", "") or "").split(",") if token.strip()}
    if scissors_tokens.intersection({"scissors_visible", "scissors_cutting", "yolo_scissors"}):
        return True
    return bool(re.search(r"剪刀|scissors", summary, re.IGNORECASE) and _has_scissors_activity_text(summary))


def _is_high_value_action_event(text: Any) -> bool:
    src = str(text or "")
    if not src:
        return False
    return bool(
        _has_explicit_clip_evidence(src)
        or re.search(r"(标本袋|胆囊袋|装袋|取出|specimen bag|bagging|removal)", src, re.IGNORECASE)
        or re.search(r"(穿刺|穿孔|穿入|穿透|钻孔|trocar|puncture|perforat|drill)", src, re.IGNORECASE)
        or re.search(r"(剪刀|scissors).{0,20}(?:CVS|安全视野|危险|核查)", src, re.IGNORECASE)
    )


def _normalize_event_nodes(
    raw_events: List[Dict[str, Any]],
    records: List[Dict[str, Any]],
    language: str,
    source: str,
) -> List[Dict[str, Any]]:
    valid_ids = {r["window_id"] for r in records}
    by_window = {r["window_id"]: r for r in records}
    allowed_types = {"phase", "cvs", "action", "risk", "resolution", "visibility", "other"}
    allowed_severities = {"normal", "important", "safety", "critical", "resolved"}
    events = []

    for idx, item in enumerate(raw_events or [], start=1):
        if not isinstance(item, dict):
            continue
        window_ids = _coerce_event_window_ids(item.get("window_ids"), valid_ids)
        if not window_ids:
            rep_id = item.get("representative_window_id") or item.get("window_id")
            window_ids = _coerce_event_window_ids(rep_id, valid_ids)
        if not window_ids:
            continue

        windows = [by_window[wid] for wid in window_ids if wid in by_window]
        if not windows:
            continue
        start_time = min(float(w.get("start_time", 0) or 0) for w in windows)
        end_time = max(float(w.get("end_time", start_time) or start_time) for w in windows)

        try:
            confidence = float(item.get("confidence", 0.65))
        except Exception:
            confidence = 0.65
        if confidence <= 0:
            confidence = 0.65
        confidence = max(0.0, min(1.0, confidence))

        event_type = str(item.get("type") or "other").lower()
        if event_type not in allowed_types:
            event_type = "other"
        severity = str(item.get("severity") or "normal").lower()
        if severity not in allowed_severities:
            severity = "normal"

        title = str(item.get("title") or "").strip()
        summary_text = str(item.get("summary") or "").strip()
        if not title:
            title = "关键事件" if language.startswith("zh") else "Key event"
        title = re.sub(
            r"(胆囊管|胆囊动脉)(?:Hem[-\s]?o[-\s]?lok夹|金属钛夹|钛夹|夹子)夹闭",
            r"\1夹闭",
            title,
            flags=re.IGNORECASE,
        )
        if not summary_text:
            summary_text = windows[-1].get("summary") or title
        summary_text = re.sub(r"[，,；;。]?\s*阶段从\d+降至\d+", "", summary_text).strip(" ，；。")
        summary_text = re.sub(r"可见\s*\d+\s*枚夹子", "可见夹子", summary_text)
        summary_text = re.sub(
            r"[，,；;。]?\s*(?:视野清晰)?(?:无|未见)(?:明显)?(?:活动性)?出血",
            "",
            summary_text,
        ).strip(" ，；。")
        if event_type == "phase":
            summary_text = re.sub(
                r"[，,；;。]?\s*(?:可见)?(?:已释放夹子|夹子已释放)",
                "",
                summary_text,
            )
            summary_text = re.sub(r"[，,；;。]?\s*期间或视野障碍", "", summary_text)
            summary_text = re.sub(r"[，,；;。]{2,}", "，", summary_text).strip(" ，；。")

        if event_type == "risk" and re.search(
            r"(?:CVS|安全视野).{0,24}(?:剪刀|scissors)|(?:剪刀|scissors).{0,24}(?:CVS|安全视野)",
            f"{title} {summary_text}",
            re.IGNORECASE,
        ):
            supported_windows = [window for window in windows if _record_has_scissors_activity(window)]
            if not supported_windows:
                continue
            windows = supported_windows
            window_ids = [int(window["window_id"]) for window in windows]
            summary_text = (
                "检测到剪刀操作，但CVS尚未达成，剪断目标结构前需继续核查。"
                if language.startswith("zh") else
                "Scissors activity is detected before CVS confirmation; verify safety before division."
            )

        # Constrain model-generated phase ranges to records carrying that
        # phase. This prevents a broad "bagging and removal" event from
        # swallowing the later retrieval-bag retraction phase.
        if event_type == "phase" and not re.search(
            r"取出后.{0,18}复查|重新进入腹腔|术野复查|post-removal.{0,24}review",
            f"{title} {summary_text}",
            re.IGNORECASE,
        ):
            phase_text = f"{title} {summary_text}"
            phase_hint = ""
            for candidate, pattern in (
                ("CalotTriangleDissection", r"肝胆三角|卡洛三角|Calot"),
                ("ClippingCutting", r"夹闭切断|clipping"),
                ("GallbladderDissection", r"胆囊分离|gallbladder dissection"),
                ("GallbladderPackaging", r"胆囊装袋|取出与装袋|装袋与取出|packaging|bagging"),
                ("GallbladderRetraction", r"标本袋牵拉取出|经切口取出|bag retraction"),
                ("CleaningCoagulation", r"清洁凝血|术野清洁|cleaning|coagulation"),
                ("Preparation", r"准备阶段|术前准备|preparation"),
            ):
                if re.search(pattern, phase_text, re.IGNORECASE):
                    phase_hint = candidate
                    break
            if phase_hint:
                supported_windows = [
                    window for window in windows
                    if _canonical_phase(str(window.get("phase") or "Unknown")) == phase_hint
                ]
                if supported_windows:
                    windows = supported_windows
                    window_ids = [int(window["window_id"]) for window in windows]

        combined_event_text = f"{title} {summary_text}"
        if event_type == "action" and re.search(
            r"标本袋|胆囊袋|装袋|取出|specimen bag|bagging|removal",
            combined_event_text,
            re.IGNORECASE,
        ):
            supported_windows = [
                window for window in windows
                if re.search(
                    r"胆囊装袋|装入标本袋|准备取出|specimen bagging|prepared for removal",
                    str(window.get("summary") or ""),
                    re.IGNORECASE,
                )
                and _canonical_phase(str(window.get("phase") or "Unknown")) != "GallbladderRetraction"
                and not re.search(r"取出后.{0,18}复查|重新进入腹腔", str(window.get("summary") or ""))
            ]
            if not supported_windows:
                continue
            windows = supported_windows
            window_ids = [int(window["window_id"]) for window in windows]

        if event_type == "phase" and windows and all(
            re.search(r"取出后.{0,18}复查|重新进入腹腔|术野复查", str(window.get("summary") or ""))
            for window in windows
        ):
            title = "术野复查" if language.startswith("zh") else "Post-removal field review"
            summary_text = (
                "胆囊取出后，镜头重新进入腹腔进行术野复查。"
                if language.startswith("zh") else
                "After specimen removal, the scope returns to the abdomen for field review."
            )

        start_time = min(float(w.get("start_time", 0) or 0) for w in windows)
        end_time = max(float(w.get("end_time", start_time) or start_time) for w in windows)

        if _looks_like_clip_event(f"{title} {summary_text}") and not any(
            _has_explicit_clip_evidence(w.get("summary", "")) for w in windows
        ):
            continue

        combined_event_text = f"{title} {summary_text}"
        if event_type == "action" and not _is_high_value_action_event(combined_event_text):
            continue
        if (
            event_type == "risk"
            and re.search(r"(出血|bleeding)", combined_event_text, re.IGNORECASE)
            and not re.search(
                r"(大量|明显|持续|喷涌|涌出|出血源|影响视野|"
                r"heavy|massive|profuse|significant|source)",
                combined_event_text,
                re.IGNORECASE,
            )
        ):
            continue
        if (
            event_type == "visibility"
            and re.search(r"(移出体外|腹腔外|腹壁外|outside|extra-abdominal|extracorporeal)", combined_event_text, re.IGNORECASE)
            and not any(
                OUT_OF_BODY_RE.search(w.get("summary", ""))
                or "out_of_body" in str(w.get("visibility", "") or "")
                for w in windows
            )
        ):
            continue

        events.append({
            "id": str(item.get("id") or f"event_{idx:03d}"),
            "type": event_type,
            "severity": severity,
            "title": title[:80],
            "summary": summary_text[:260],
            "window_ids": window_ids,
            "representative_window_id": window_ids[-1],
            "start_time": start_time,
            "end_time": end_time,
            "confidence": confidence,
            "source": source,
        })

    # Chronological order; frontend chooses latest-first layout.
    events.sort(key=lambda e: (e["start_time"], e["representative_window_id"]))
    return events[:10]


def _merge_or_append_fallback_event(events: List[Dict[str, Any]], event: Dict[str, Any]) -> None:
    """Merge adjacent fallback events of the same type/severity to reduce clutter."""
    if events:
        last = events[-1]
        close = event["window_ids"][0] - last["window_ids"][-1] <= 2
        if close and last["type"] == event["type"] and last["severity"] == event["severity"] and last["title"] == event["title"]:
            merged_ids = sorted(set(last["window_ids"] + event["window_ids"]))
            last["window_ids"] = merged_ids
            last["representative_window_id"] = merged_ids[-1]
            last["end_time"] = max(last["end_time"], event["end_time"])
            if event.get("summary") and event["summary"] not in last.get("summary", ""):
                last["summary"] = f"{last['summary'].rstrip('。.')}; {event['summary']}"
            return
    events.append(event)


def _fallback_event_nodes(records: List[Dict[str, Any]], language: str, reason: str = "") -> List[Dict[str, Any]]:
    zh = language.startswith("zh")
    events: List[Dict[str, Any]] = []
    prev_phase = None
    cvs_achieved = False

    def make_event(record: Dict[str, Any], event_type: str, severity: str, title: str, summary: str, confidence: float) -> Dict[str, Any]:
        return {
            "id": f"fallback_{len(events) + 1:03d}",
            "type": event_type,
            "severity": severity,
            "title": title,
            "summary": summary[:260],
            "window_ids": [record["window_id"]],
            "representative_window_id": record["window_id"],
            "start_time": record["start_time"],
            "end_time": record["end_time"],
            "confidence": confidence,
            "source": "fallback",
            "fallback_reason": reason,
        }

    for record in records:
        text = f"{record.get('summary', '')} {record.get('phase', '')}".lower()
        phase = record.get("phase") or "Unknown"
        current_cvs_achieved = _has_cvs_achieved_text(text)
        if phase != prev_phase and phase not in ("Unknown", "未知", ""):
            title = f"进入{PHASE_LABEL_CN.get(phase, phase)}" if zh else f"Phase shift: {phase}"
            summary = record.get("summary") or title
            _merge_or_append_fallback_event(events, make_event(record, "phase", "important", title, summary, 0.55))
            prev_phase = phase

        if re.search(r"(cvs|critical view|安全视野|关键安全视野|两条结构|胆囊板|三要素)", text, re.IGNORECASE):
            title = "CVS安全评估节点" if zh else "CVS safety checkpoint"
            _merge_or_append_fallback_event(events, make_event(record, "cvs", "safety", title, record.get("summary") or title, 0.58))

        if _record_has_scissors_activity(record) and not (cvs_achieved or current_cvs_achieved):
            title = "CVS未达成时出现剪刀操作" if zh else "Scissors before CVS confirmation"
            summary = (
                "检测到剪刀操作，但CVS尚未达成，剪断目标结构前需继续核查。"
                if zh else
                "Scissors activity is detected before CVS confirmation; verify safety before dividing the cystic duct or artery."
            )
            _merge_or_append_fallback_event(events, make_event(record, "risk", "critical", title, summary, 0.72))

        if _has_explicit_clip_evidence(text):
            if re.search(r"胆囊管|cystic duct", text, re.IGNORECASE):
                title = "胆囊管夹闭" if zh else "Cystic duct clipping"
            elif re.search(r"胆囊动脉|cystic artery", text, re.IGNORECASE):
                title = "胆囊动脉夹闭" if zh else "Cystic artery clipping"
            else:
                title = "夹子放置" if zh else "Clip placement"
            _merge_or_append_fallback_event(events, make_event(record, "action", "important", title, record.get("summary") or title, 0.62))

        if re.search(
            r"(大量(?:活动性)?出血|明显(?:活动性)?出血|"
            r"持续(?:活动性)?出血|喷涌出血|涌出|出血源|"
            r"heavy bleeding|massive bleeding|profuse bleeding|significant bleeding)",
            text,
            re.IGNORECASE,
        ):
            title = "活动性出血" if zh else "Active bleeding"
            _merge_or_append_fallback_event(events, make_event(record, "risk", "critical", title, record.get("summary") or title, 0.68))

        if re.search(r"(出血(?:已经|已)?(?:停止|控制|解决)|止血(?:完成|成功|有效)|无活动性出血|bleeding (?:stopped|controlled|resolved)|hemostasis achieved)", text, re.IGNORECASE):
            title = "出血已控制" if zh else "Bleeding controlled"
            _merge_or_append_fallback_event(events, make_event(record, "resolution", "resolved", title, record.get("summary") or title, 0.62))

        summary_text_for_visibility = str(record.get("summary", "") or "")
        visibility_tokens = {token.strip() for token in str(record.get("visibility", "") or "").split(",")}
        visibility_text = f"{summary_text_for_visibility} {record.get('visibility', '')}"
        if FOG_ACTIVE_RE.search(summary_text_for_visibility) or "fog_active" in visibility_tokens:
            title = "视野起雾" if zh else "Fog obscures view"
            summary = "镜头起雾，手术视野受遮挡。" if zh else "Lens fogging obscures the surgical field."
            _merge_or_append_fallback_event(events, make_event(record, "visibility", "critical", title, summary, 0.68))

        if FOG_RESOLVED_RE.search(summary_text_for_visibility) or "fog_resolved" in visibility_tokens:
            title = "雾已去除" if zh else "Fog cleared"
            summary = "雾已去除，腹腔视野恢复。" if zh else "The fog has cleared and the laparoscopic view is restored."
            _merge_or_append_fallback_event(events, make_event(record, "visibility", "resolved", title, summary, 0.66))

        if OUT_OF_BODY_RE.search(summary_text_for_visibility) or "out_of_body" in visibility_tokens:
            title = "镜头移出体外" if zh else "Scope moved outside body"
            summary = "镜头移出体外，画面切换至套管口或腹壁外场景。" if zh else "The scope is moved outside the body and the view switches to the trocar or extra-abdominal scene."
            _merge_or_append_fallback_event(events, make_event(record, "visibility", "important", title, summary, 0.68))

        if re.search(r"(穿刺|穿孔|穿入|穿透|钻孔|trocar|puncture|perforat|drill)", text, re.IGNORECASE):
            title = "穿刺进入动作" if zh else "Puncture entry action"
            _merge_or_append_fallback_event(events, make_event(record, "action", "important", title, record.get("summary") or title, 0.64))

        if current_cvs_achieved:
            cvs_achieved = True

    if not events and records:
        latest = records[-1]
        title = "最新手术进展" if zh else "Latest surgical progress"
        events.append(make_event(latest, "other", "normal", title, latest.get("summary") or title, 0.45))

    return events[-10:]


def _select_key_event_nodes(events: List[Dict[str, Any]], max_events: int = 10) -> List[Dict[str, Any]]:
    """Apply one safety-first cap after all deterministic events are merged."""
    ordered = sorted(
        events or [],
        key=lambda event: (
            float(event.get("start_time", 0) or 0),
            int(event.get("representative_window_id", 0) or 0),
        ),
    )
    if len(ordered) <= max_events:
        return ordered

    def priority(event: Dict[str, Any]) -> int:
        event_id = str(event.get("id") or "")
        event_type = str(event.get("type") or "")
        severity = str(event.get("severity") or "")
        text = f"{event.get('title', '')} {event.get('summary', '')}"
        if event_type == "risk" or severity == "critical" or event_id == "event_required_scissors_before_cvs":
            return 0
        if (
            event_type == "cvs"
            or event_id.startswith("event_required_clip_")
            or event_id in {
                "event_required_specimen_bagging",
                "event_required_retrieval_bag_retraction",
            }
            or FOG_ACTIVE_RE.search(text)
            or FOG_RESOLVED_RE.search(text)
        ):
            return 1
        if event_type == "visibility":
            return 2
        if event_type == "phase":
            return 3
        return 4

    selected = sorted(
        ordered,
        key=lambda event: (
            priority(event),
            float(event.get("start_time", 0) or 0),
            int(event.get("representative_window_id", 0) or 0),
        ),
    )[:max_events]
    return sorted(
        selected,
        key=lambda event: (
            float(event.get("start_time", 0) or 0),
            int(event.get("representative_window_id", 0) or 0),
        ),
    )


def _ensure_required_event_nodes(events: List[Dict[str, Any]], records: List[Dict[str, Any]], language: str) -> List[Dict[str, Any]]:
    """Add record-supported high-value events that the event-node LLM omitted."""
    zh = language.startswith("zh")
    review_pattern = r"取出后.{0,18}复查|重新进入腹腔|术野复查|post-removal.{0,24}review"
    # Rebuild review milestones from records so alternating 5-second cleaning
    # windows do not create a row of near-identical event cards.
    merged = [
        event for event in (events or [])
        if not re.search(
            review_pattern,
            f"{event.get('title', '')} {event.get('summary', '')}",
            re.IGNORECASE,
        )
    ]
    # Rebuild one persistent CVS node from records so model grouping cannot
    # truncate it at the first phase boundary.
    merged = [event for event in merged if str(event.get("type") or "") != "cvs"]
    cvs_records = [
        record for record in records or []
        if re.search(
            r"CVS.{0,12}(?:评估|核查|安全视野|确认|达成)|安全关键视野|关键安全视野|"
            r"critical view of safety|两条结构.{0,12}进入胆囊|胆囊板",
            str(record.get("summary") or ""),
            re.IGNORECASE,
        )
    ]
    if cvs_records:
        achieved_records = [
            record for record in cvs_records
            if _has_cvs_achieved_text(record.get("summary", ""))
        ]
        latest = achieved_records[-1] if achieved_records else cvs_records[-1]
        window_ids = sorted({int(record["window_id"]) for record in cvs_records})
        merged.append({
            "id": "event_required_cvs_status",
            "type": "cvs",
            "severity": "resolved" if achieved_records else "safety",
            "title": "CVS已达成" if zh and achieved_records else (
                "CVS安全评估" if zh else (
                    "CVS achieved" if achieved_records else "CVS safety assessment"
                )
            ),
            "summary": (
                "系统记录到CVS达成证据，仍需医生回看确认三要素。"
                if zh and achieved_records else
                "系统持续进行CVS安全视野评估，尚未形成达成结论，需医生回看确认三要素。"
                if zh else
                "The system recorded evidence of CVS achievement; surgeon review is still required."
                if achieved_records else
                "The system is assessing CVS and has not formed an achievement conclusion; surgeon review is required."
            ),
            "window_ids": window_ids,
            "representative_window_id": int(latest["window_id"]),
            "start_time": min(float(record.get("start_time", 0) or 0) for record in cvs_records),
            "end_time": max(float(record.get("end_time", 0) or 0) for record in cvs_records),
            "confidence": 0.78 if achieved_records else 0.66,
            "source": "record-derived",
        })

    merged = [
        event for event in merged
        if not (
            event.get("type") == "action"
            and re.search(
                r"胆囊装袋|装入标本袋|准备取出|specimen bagging|prepared for removal",
                f"{event.get('title', '')} {event.get('summary', '')}",
                re.IGNORECASE,
            )
        )
    ]
    pack_records = [
        r for r in records or []
        if (
            _canonical_phase(str(r.get("phase") or "Unknown")) == "GallbladderPackaging"
            or re.search(
                r"(胆囊装袋|装入标本袋|准备取出|specimen bagging|prepared for removal)",
                r.get("summary", ""),
                re.IGNORECASE,
            )
        )
        and not re.search(
            r"标本袋牵拉取出|经切口取出|取出后.{0,18}复查|重新进入腹腔|术野复查|post-removal.{0,24}review",
            r.get("summary", ""),
            re.IGNORECASE,
        )
        and not OUT_OF_BODY_RE.search(str(r.get("summary") or ""))
    ]
    if pack_records:
        # The packaging milestone already conveys the phase transition. Keep a
        # single high-value card instead of showing an identical phase/action
        # pair over the same time range.
        merged = [
            event for event in merged
            if not (
                event.get("type") == "phase"
                and re.search(
                    r"胆囊装袋|装袋与取出|取出与装袋|specimen bagging|bagging and removal",
                    f"{event.get('title', '')} {event.get('summary', '')}",
                    re.IGNORECASE,
                )
            )
        ]
        window_ids = sorted({int(r["window_id"]) for r in pack_records})
        start_time = min(float(r.get("start_time", 0) or 0) for r in pack_records)
        end_time = max(float(r.get("end_time", 0) or 0) for r in pack_records)
        merged.append({
            "id": "event_required_specimen_bagging",
            "type": "action",
            "severity": "important",
            "title": "胆囊装袋取出" if zh else "Specimen bagging and removal",
            "summary": "将胆囊装入标本袋并准备取出。" if zh else "The gallbladder is placed into a specimen bag and prepared for removal.",
            "window_ids": window_ids,
            "representative_window_id": window_ids[-1],
            "start_time": start_time,
            "end_time": end_time,
            "confidence": 0.72,
            "source": "record-derived",
        })

    # Rebuild clip milestones from record evidence. LLM output often splits one
    # placement into a target-specific event followed by a generic "clip
    # visible" event when the released clip remains in later frames.
    merged = [
        event for event in merged
        if not (
            event.get("type") == "action"
            and _looks_like_clip_event(f"{event.get('title', '')} {event.get('summary', '')}")
        )
    ]
    clip_records = [
        record for record in records or []
        if _has_explicit_clip_evidence(record.get("summary", ""))
    ]
    target_groups = []
    for target_key, target_pattern, title in (
        ("artery", r"胆囊动脉|cystic artery", "胆囊动脉夹闭" if zh else "Cystic artery clipping"),
        ("duct", r"胆囊管|cystic duct", "胆囊管夹闭" if zh else "Cystic duct clipping"),
    ):
        target_records = [
            record for record in clip_records
            if re.search(target_pattern, str(record.get("summary") or ""), re.IGNORECASE)
        ]
        if target_records:
            target_groups.append((target_key, title, target_records))

    # Once a target-specific placement is present, later generic sightings are
    # residual evidence for that milestone, not a second surgical action.
    if not target_groups and clip_records:
        target_groups.append(("generic", "夹子放置" if zh else "Clip placement", clip_records))

    for target_key, title, target_records in target_groups:
        window_ids = sorted({int(record["window_id"]) for record in target_records})
        merged.append({
            "id": f"event_required_clip_{target_key}",
            "type": "action",
            "severity": "important",
            "title": title,
            "summary": (
                (
                    "可见已释放夹子，目标结构需回看原片确认。"
                    if target_key == "generic" else
                    f"检测到{title}证据，需回看原片确认目标与夹体状态。"
                )
                if zh else
                f"Window analysis records {title.lower()}; source-video confirmation is required."
            ),
            "window_ids": window_ids,
            "representative_window_id": window_ids[-1],
            "start_time": min(float(record.get("start_time", 0) or 0) for record in target_records),
            "end_time": max(float(record.get("end_time", 0) or 0) for record in target_records),
            "confidence": 0.72,
            "source": "record-derived",
        })

    merged = [
        event for event in merged
        if not (
            event.get("type") == "action"
            and re.search(
                r"标本袋牵拉取出|retrieval bag removal",
                f"{event.get('title', '')} {event.get('summary', '')}",
                re.IGNORECASE,
            )
        )
    ]
    retraction_records = [
        record for record in records or []
        if _canonical_phase(str(record.get("phase") or "Unknown")) == "GallbladderRetraction"
    ]
    if retraction_records:
        merged = [
            event for event in merged
            if not (
                event.get("type") == "phase"
                and re.search(
                    r"标本袋牵拉取出|经切口取出|retrieval bag removal|bag retraction",
                    f"{event.get('title', '')} {event.get('summary', '')}",
                    re.IGNORECASE,
                )
            )
        ]
        window_ids = sorted({int(record["window_id"]) for record in retraction_records})
        merged.append({
            "id": "event_required_retrieval_bag_retraction",
            "type": "action",
            "severity": "important",
            "title": "标本袋牵拉取出" if zh else "Retrieval bag removal",
            "summary": (
                "牵拉装有胆囊的标本袋经切口取出。"
                if zh else
                "The retrieval bag containing the gallbladder is drawn out through the incision."
            ),
            "window_ids": window_ids,
            "representative_window_id": window_ids[-1],
            "start_time": min(float(record.get("start_time", 0) or 0) for record in retraction_records),
            "end_time": max(float(record.get("end_time", 0) or 0) for record in retraction_records),
            "confidence": 0.78,
            "source": "record-derived",
        })

    review_records = [
        record for record in sorted(
            records or [],
            key=lambda r: (int(r.get("window_id", 0) or 0), float(r.get("start_time", 0) or 0)),
        )
        if re.search(
            review_pattern,
            str(record.get("summary") or ""),
            re.IGNORECASE,
        )
    ]
    review_groups: List[List[Dict[str, Any]]] = []
    for record in review_records:
        if review_groups:
            previous = review_groups[-1][-1]
            nearby = (
                float(record.get("start_time", 0) or 0)
                <= float(previous.get("end_time", 0) or 0) + 45.0
            )
            if nearby:
                review_groups[-1].append(record)
                continue
        review_groups.append([record])

    for group_index, group in enumerate(review_groups, start=1):
        window_ids = [int(record["window_id"]) for record in group]
        merged.append({
            "id": f"event_required_post_retrieval_review_{group_index}",
            "type": "phase",
            "severity": "normal",
            "title": "术野复查" if zh else "Post-removal field review",
            "summary": (
                "胆囊取出后，镜头重新进入腹腔进行术野复查。"
                if zh else
                "After specimen removal, the scope returns to the abdomen for field review."
            ),
            "window_ids": window_ids,
            "representative_window_id": window_ids[-1],
            "start_time": min(float(record.get("start_time", 0) or 0) for record in group),
            "end_time": max(float(record.get("end_time", 0) or 0) for record in group),
            "confidence": 0.74,
            "source": "record-derived",
        })

    # Always rebuild the CVS/scissors risk from every reviewed window. The LLM
    # commonly selects only one of several non-contiguous scissors intervals.
    merged = [
        event for event in merged
        if not (
            str(event.get("type") or "") == "risk"
            and re.search(
                r"CVS|安全视野|critical view",
                f"{event.get('title', '')} {event.get('summary', '')}",
                re.IGNORECASE,
            )
            and re.search(
                r"剪刀|scissors",
                f"{event.get('title', '')} {event.get('summary', '')}",
                re.IGNORECASE,
            )
        )
    ]
    danger_records: List[Dict[str, Any]] = []
    cvs_achieved = False
    for record in sorted(records or [], key=lambda r: (int(r.get("window_id", 0) or 0), float(r.get("start_time", 0) or 0))):
        text = record.get("summary", "")
        current_cvs_achieved = _has_cvs_achieved_text(text)
        if _record_has_scissors_activity(record) and not (cvs_achieved or current_cvs_achieved):
            danger_records.append(record)
        if current_cvs_achieved:
            cvs_achieved = True
    if danger_records:
        window_ids = sorted({int(r["window_id"]) for r in danger_records})
        start_time = min(float(r.get("start_time", 0) or 0) for r in danger_records)
        end_time = max(float(r.get("end_time", 0) or 0) for r in danger_records)
        repeated = any(current > previous + 1 for previous, current in zip(window_ids, window_ids[1:]))
        merged.append({
            "id": "event_required_scissors_before_cvs",
            "type": "risk",
            "severity": "critical",
            "title": "CVS未达成时出现剪刀操作" if zh else "Scissors before CVS confirmation",
            "summary": (
                "多个窗口检测到剪刀操作，但CVS尚未达成，剪断目标结构前需继续核查。"
                if zh and repeated else
                "检测到剪刀操作，但CVS尚未达成，剪断目标结构前需继续核查。"
                if zh else
                "Scissors activity is detected in multiple windows before CVS confirmation; verify safety before division."
                if repeated else
                "Scissors activity is detected before CVS confirmation; verify safety before division."
            ),
            "window_ids": window_ids,
            "representative_window_id": window_ids[-1],
            "start_time": start_time,
            "end_time": end_time,
            "confidence": 0.76,
            "source": "record-derived",
        })

    return _select_key_event_nodes(merged, 10)


def _visibility_status_events(records: List[Dict[str, Any]], language: str) -> List[Dict[str, Any]]:
    zh = language.startswith("zh")
    fog_records: List[Dict[str, Any]] = []
    resolved_records: List[Dict[str, Any]] = []
    out_of_body_groups: List[List[Dict[str, Any]]] = []
    ordered_records = sorted(records or [], key=lambda r: (r.get("window_id", 0), r.get("start_time", 0)))
    records_by_id = {int(record["window_id"]): record for record in ordered_records}

    for record in ordered_records:
        summary_text = str(record.get("summary", "") or "")
        tokens = {token.strip() for token in str(record.get("visibility", "") or "").split(",")}
        active = FOG_ACTIVE_RE.search(summary_text) or "fog_active" in tokens
        resolved = FOG_RESOLVED_RE.search(summary_text) or "fog_resolved" in tokens
        out_of_body = OUT_OF_BODY_RE.search(summary_text) or "out_of_body" in tokens

        if active:
            fog_records.append(record)
        if resolved:
            resolved_records.append(record)
        if out_of_body:
            if not out_of_body_groups:
                out_of_body_groups.append([record])
            else:
                previous = out_of_body_groups[-1][-1]
                previous_id = int(previous["window_id"])
                current_id = int(record["window_id"])
                consecutive = (
                    current_id == previous_id + 1
                    and float(record.get("start_time", 0) or 0)
                    <= float(previous.get("end_time", 0) or 0) + 1.0
                )
                bridge_records = [
                    records_by_id[wid]
                    for wid in range(previous_id + 1, current_id)
                    if wid in records_by_id
                ]
                fog_bridge = bool(
                    current_id == previous_id + 2
                    and bridge_records
                    and all(
                        FOG_ACTIVE_RE.search(str(item.get("summary", "") or ""))
                        or "fog_active" in str(item.get("visibility", "") or "")
                        for item in bridge_records
                    )
                )
                nearby_return = bool(
                    float(record.get("start_time", 0) or 0)
                    - float(previous.get("end_time", 0) or 0)
                    <= 35.0
                )
                if consecutive or fog_bridge or nearby_return:
                    out_of_body_groups[-1].append(record)
                else:
                    out_of_body_groups.append([record])

    events: List[Dict[str, Any]] = []
    if fog_records:
        first_active = fog_records[0]
        latest_active = fog_records[-1]
        fog_ids = sorted({int(record["window_id"]) for record in fog_records})
        repeated_fog = any(current > previous + 1 for previous, current in zip(fog_ids, fog_ids[1:]))
        events.append({
            "id": "event_visibility_fog_status",
            "type": "visibility",
            "severity": "critical",
            "title": (
                "视野反复起雾" if zh and repeated_fog else
                "视野起雾" if zh else
                "Repeated lens fogging" if repeated_fog else
                "Fog obscures view"
            ),
            "summary": (
                "期间多次出现镜头起雾，手术视野间歇受遮挡。" if zh and repeated_fog else
                "镜头起雾，手术视野受遮挡。" if zh else
                "Lens fogging repeatedly obscures the surgical field." if repeated_fog else
                "Lens fogging obscures the surgical field."
            ),
            "window_ids": fog_ids,
            "representative_window_id": int(first_active["window_id"]),
            "start_time": float(first_active.get("start_time", 0) or 0),
            "end_time": float(first_active.get("end_time", first_active.get("start_time", 0)) or 0),
            "confidence": 0.78,
            "source": "visibility-status",
        })

        # A green resolution node is meaningful only when the latest explicit
        # clear state follows the most recent fog-positive window.
        latest_resolved = resolved_records[-1] if resolved_records else None
        if latest_resolved and int(latest_resolved["window_id"]) > int(latest_active["window_id"]):
            events.append({
                "id": "event_visibility_fog_resolved",
                "type": "visibility",
                "severity": "resolved",
                "title": "雾已去除" if zh else "Fog cleared",
                "summary": "雾已去除，腹腔视野恢复。" if zh else "The fog has cleared and the laparoscopic view is restored.",
                "window_ids": [int(latest_resolved["window_id"])],
                "representative_window_id": int(latest_resolved["window_id"]),
                "start_time": float(latest_resolved.get("start_time", 0) or 0),
                "end_time": float(latest_resolved.get("end_time", latest_resolved.get("start_time", 0)) or 0),
                "confidence": 0.74,
                "source": "visibility-status",
            })

    for group_index, group in enumerate(out_of_body_groups, start=1):
        start_record = group[0]
        latest_out_of_body = group[-1]
        window_ids = [int(record["window_id"]) for record in group]
        events.append({
            "id": f"event_visibility_out_of_body_{group_index}",
            "type": "visibility",
            "severity": "important",
            "title": "镜头移出体外" if zh else "Scope moved outside body",
            "summary": "镜头移出体外，画面切换至套管口或腹壁外场景。" if zh else "The scope is moved outside the body and the view switches to the trocar or extra-abdominal scene.",
            "window_ids": window_ids,
            "representative_window_id": int(latest_out_of_body["window_id"]),
            "start_time": float(start_record.get("start_time", latest_out_of_body.get("start_time", 0)) or 0),
            "end_time": float(latest_out_of_body.get("end_time", latest_out_of_body.get("start_time", 0)) or 0),
            "confidence": 0.76,
            "source": "visibility-status",
        })

    return events


def _merge_visibility_status_events(events: List[Dict[str, Any]], records: List[Dict[str, Any]], language: str) -> List[Dict[str, Any]]:
    status_events = _visibility_status_events(records, language)
    if not status_events:
        return events

    def is_visibility_duplicate(event: Dict[str, Any], status_event: Dict[str, Any]) -> bool:
        if event.get("type") != "visibility":
            return False
        text = f"{event.get('title', '')} {event.get('summary', '')}"
        status_text = f"{status_event.get('title', '')} {status_event.get('summary', '')}"
        if "体外" in status_text or "outside" in status_text.lower():
            return bool(OUT_OF_BODY_RE.search(text))
        return bool(FOG_ACTIVE_RE.search(text) or FOG_RESOLVED_RE.search(text) or "雾" in text)

    has_out_of_body_status = any(
        "体外" in f"{event.get('title', '')} {event.get('summary', '')}"
        or "outside" in f"{event.get('title', '')} {event.get('summary', '')}".lower()
        for event in status_events
    )
    has_fog_status = any(
        not (
            "体外" in f"{event.get('title', '')} {event.get('summary', '')}"
            or "outside" in f"{event.get('title', '')} {event.get('summary', '')}".lower()
        )
        for event in status_events
    )
    merged = []
    for event in events or []:
        text = f"{event.get('title', '')} {event.get('summary', '')}"
        is_out_of_body = bool(OUT_OF_BODY_RE.search(text))
        is_fog = bool(FOG_ACTIVE_RE.search(text) or FOG_RESOLVED_RE.search(text) or "雾" in text)
        if has_out_of_body_status and is_out_of_body:
            continue
        if has_fog_status and is_fog:
            continue
        merged.append(event)
    merged.extend(status_events)

    return _select_key_event_nodes(merged, 10)


def _build_event_nodes_prompt(records: List[Dict[str, Any]], language: str) -> str:
    output_language = "Chinese" if language.startswith("zh") else "English"
    window_lines = []
    for record in records:
        summary = re.sub(r"\s+", " ", record.get("summary", "")).strip()
        # Keep the 180-window offline review inside the local model's 12k
        # context while preserving every salient window selected above.
        if len(summary) > 280:
            summary = summary[:277] + "..."
        window_lines.append(
            f"- window_id={record['window_id']} display_window={record['display_window']} "
            f"time={record['start_time']:.1f}-{record['end_time']:.1f}s "
            f"phase={record.get('phase') or 'Unknown'} stage={record.get('stage', 2)} "
            f"visibility={record.get('visibility') or 'clear'} "
            f"scissors={record.get('scissors') or 'none'} "
            f"summary={summary}"
        )

    return f"""
You will receive chronological laparoscopic cholecystectomy window summaries.
Identify key event nodes for a clinical review timeline. Output language: {output_language}.

Selection rules:
1. Use only the provided window summaries. Do not invent findings.
2. Merge adjacent similar windows into one event node.
3. Prefer phase transitions, CVS safety milestones, critical actions, risks, and resolved risks.
4. CVS milestones include: hepatocystic triangle exposure/clearing, lower gallbladder separation,
   only two structures entering the gallbladder, clipping/cutting safety confirmation, or CVS uncertainty.
5. Critical actions include trocar/puncture/entry, drilling/perforation-like contact, clipping, cutting,
   specimen bagging, and gallbladder removal. Do not create event nodes for routine traction, exposure,
   electrocautery, irrigation, or ordinary dissection unless they mark a phase transition or a safety risk.
   Do not create a cystic duct or cystic artery clipping event from the phase name "ClippingCutting" alone.
   A clipping event requires explicit evidence such as Hem-o-lok, titanium clip, clip applier release,
   clipped stump, or a sentence directly saying the cystic duct/artery was clipped.
   A cystic duct/artery cutting event requires explicit prior or same-window clipping evidence for the same
   target and CVS achieved/confirmed evidence. If that ordering evidence is missing, keep it as scissors
   manipulation near the target rather than a division event.
   If scissors activity appears while CVS is not achieved or still under assessment, create a critical risk
   event titled "CVS未达成时出现剪刀操作" in Chinese or "Scissors before CVS confirmation" in English.
6. Write event titles and summaries as surgical progress, action, safety, bleeding, or resolution.
   Do not list visible instruments unless the instrument name is necessary to explain the action.
7. Bleeding status is highest priority: create a critical risk event for heavy active bleeding or a clear bleeding source;
   create a resolved event when a later window says bleeding is controlled, stopped, or hemostasis is achieved.
   Minor oozing should only be mentioned when it affects the field or requires hemostasis.
8. Visibility is a key safety event:
   create a critical visibility event when summaries or visibility flags show lens fogging, smoke, blur, water vapor,
   or obscured operative view; create a resolved visibility event when a later window says the fog/smoke cleared or
   the view was restored. Use title "视野起雾"/"雾已去除" in Chinese or "Fog obscures view"/"Fog cleared" in English.
9. If the scope/view leaves the abdomen and shows the outside-body or operating-room scene, create one important
   visibility event titled "镜头移出体外" in Chinese or "Scope moved outside body" in English.
10. Keep 3-10 event nodes when possible. Include the latest meaningful event.
11. window_ids must use the exact internal window_id values from the input, not display_window.

Return strict JSON only, no markdown:
{{
  "events": [
    {{
      "id": "event_001",
      "type": "phase|cvs|action|risk|resolution|visibility|other",
      "severity": "normal|important|safety|critical|resolved",
      "title": "short title",
      "summary": "one concise clinical sentence",
      "window_ids": [0, 1],
      "representative_window_id": 1,
      "start_time": 0.0,
      "end_time": 10.0,
      "confidence": 0.65
    }}
  ]
}}

Window summaries:
{chr(10).join(window_lines)}
""".strip()


def _compact_clinical_summary_records(records: List[Dict[str, Any]], max_windows: int) -> List[Dict[str, Any]]:
    """Keep broad coverage without sampling away short safety-critical events."""
    rows = sorted(records or [], key=lambda r: (float(r.get("start_time", 0) or 0), int(r.get("window_id", 0) or 0)))
    if len(rows) <= max_windows:
        return rows

    if max_windows <= 1:
        return [rows[-1]]

    salient_re = re.compile(
        r"CVS(?:已|基本)?达成|CVS尚未达成.{0,24}剪刀|剪刀.{0,24}CVS尚未达成|"
        r"大量活动性出血|出血(?:已)?(?:控制|停止|解决)|"
        r"镜头起雾|雾已去除|移出体外|"
        r"(?:夹闭|剪断|切断)(?:胆囊管|胆囊动脉)|"
        r"胆囊装入标本袋|标本袋牵拉取出",
        re.IGNORECASE,
    )
    mandatory = {0, len(rows) - 1}
    previous_phase = ""
    previous_visibility = ""
    for index, row in enumerate(rows):
        phase = _canonical_phase(str(row.get("phase") or "Unknown"))
        visibility = str(row.get("visibility") or "clear")
        summary = str(row.get("summary") or "")
        scissors = str(row.get("scissors") or "")
        if previous_phase and phase != previous_phase:
            mandatory.update({max(0, index - 1), index})
        if previous_visibility and visibility != previous_visibility:
            mandatory.update({max(0, index - 1), index})
        if visibility != "clear" or salient_re.search(summary):
            mandatory.add(index)
        if scissors and row.get("scissors_review") != "rejected":
            mandatory.add(index)
        previous_phase = phase
        previous_visibility = visibility

    # If an unusually event-dense case exceeds the prompt budget, retain an
    # even temporal sample of the mandatory windows before adding routine ones.
    if len(mandatory) > max_windows:
        ordered_mandatory = sorted(mandatory)
        selected_positions = {
            min(len(ordered_mandatory) - 1, round(i * (len(ordered_mandatory) - 1) / (max_windows - 1)))
            for i in range(max_windows)
        }
        return [rows[ordered_mandatory[position]] for position in sorted(selected_positions)]

    selected = set(mandatory)
    last = len(rows) - 1
    broad_indices = [
        min(last, round(i * last / (max_windows - 1)))
        for i in range(max_windows)
    ]
    for index in broad_indices:
        if len(selected) >= max_windows:
            break
        selected.add(index)
    if len(selected) < max_windows:
        for index in range(len(rows)):
            selected.add(index)
            if len(selected) >= max_windows:
                break
    return [rows[index] for index in sorted(selected)]


def _format_clinical_summary_records(records: List[Dict[str, Any]]) -> str:
    lines = []
    for record in records:
        summary = re.sub(r"\s+", " ", str(record.get("summary", "") or "")).strip()
        if len(summary) > 360:
            summary = summary[:357] + "..."
        lines.append(
            f"- id={record.get('window_id')} "
            f"time={float(record.get('start_time', 0) or 0):.0f}-{float(record.get('end_time', 0) or 0):.0f}s "
            f"phase={record.get('phase') or 'Unknown'} "
            f"visibility={record.get('visibility') or 'clear'} "
            f"summary={summary}"
        )
    return "\n".join(lines)


def _format_clinical_summary_events(events: List[Dict[str, Any]], max_events: int) -> str:
    rows = sorted(events or [], key=lambda e: (float(e.get("start_time", 0) or 0), int(e.get("representative_window_id", 0) or 0)))
    lines = []
    for event in rows[:max_events]:
        title = re.sub(r"\s+", " ", str(event.get("title", "") or "")).strip()
        summary = re.sub(r"\s+", " ", str(event.get("summary", "") or "")).strip()
        lines.append(
            f"- time={float(event.get('start_time', 0) or 0):.0f}-{float(event.get('end_time', 0) or 0):.0f}s "
            f"type={event.get('type') or 'other'} severity={event.get('severity') or 'normal'} "
            f"{title}: {summary}"
        )
    return "\n".join(lines) if lines else "（无独立事件节点输入）"


def _format_report_time(seconds: Any) -> str:
    value = max(0, int(round(_safe_float(seconds, 0.0))))
    minutes, secs = divmod(value, 60)
    return f"{minutes}:{secs:02d}"


def _event_time_range(event: Dict[str, Any]) -> str:
    return f"{_format_report_time(event.get('start_time'))}-{_format_report_time(event.get('end_time'))}"


def _phase_event_label(phase: str) -> str:
    if phase == "PostRetrievalReview":
        return "取出后术野复查与清洁"
    return PHASE_LABEL_CN.get(_canonical_phase(phase), phase or "当前阶段")


def _build_deterministic_clinical_report(
    video_title: str,
    records: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    language: str,
    reason: str = "",
) -> str:
    """Create a concise event-based report when the local LLM/VLM is unavailable."""
    zh = not language.startswith("en")
    ordered_records = sorted(records or [], key=lambda r: (float(r.get("start_time", 0) or 0), int(r.get("window_id", 0) or 0)))
    ordered_events = sorted(events or [], key=lambda e: (float(e.get("start_time", 0) or 0), int(e.get("representative_window_id", 0) or 0)))
    duration = max([_safe_float(r.get("end_time"), 0.0) for r in ordered_records] or [0.0])

    def infer_record_phase(record: Dict[str, Any]) -> str:
        text = str(record.get("summary") or "")
        if re.search(r"取出后.{0,18}复查|重新进入腹腔|术野复查", text, re.IGNORECASE):
            return "PostRetrievalReview"
        phase = _canonical_phase(str(record.get("phase") or "Unknown"))
        if phase == "GallbladderRetraction":
            return phase
        visibility_tokens = {
            token.strip()
            for token in str(record.get("visibility", "") or "").split(",")
            if token.strip()
        }
        if phase not in {"Unknown", "未知", ""}:
            return phase
        if OUT_OF_BODY_RE.search(text) or "out_of_body" in visibility_tokens:
            return ""
        inferred = (
            ("GallbladderPackaging", r"胆囊取出与装袋|标本袋|胆囊袋|装袋|取出"),
            ("CleaningCoagulation", r"清洁凝血|凝血处理|出血控制|止血"),
            ("ClippingCutting", r"夹闭切断|夹闭前后|剪刀正在切断|剪断胆囊|切断胆囊"),
            ("GallbladderDissection", r"胆囊分离|胆囊床"),
            ("CalotTriangleDissection", r"肝胆三角|卡洛三角|CVS评估|安全关键视野"),
            ("Preparation", r"准备阶段|初始暴露|入路准备|术野建立"),
        )
        for canonical, pattern in inferred:
            if re.search(pattern, text, re.IGNORECASE):
                return canonical
        return phase

    def group_record_action(
        pattern: str,
        title: str,
        summary: str,
        confidence: float = 0.68,
        max_gap: float = 30.0,
        event_type: str = "action",
        severity: str = "important",
        exclude_pattern: str = "",
        phases: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        def searchable_text(record: Dict[str, Any]) -> str:
            return re.sub(
                r"当前处于夹闭切断(?:阶段)?",
                "当前阶段",
                str(record.get("summary") or ""),
            )

        matches = [
            record for record in ordered_records
            if re.search(pattern, searchable_text(record), re.IGNORECASE)
            and (not phases or infer_record_phase(record) in phases)
            and not (
                exclude_pattern
                and re.search(exclude_pattern, searchable_text(record), re.IGNORECASE)
            )
        ]
        if not matches:
            return []
        groups: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        last_end = 0.0
        for record in matches:
            start = _safe_float(record.get("start_time"), 0.0)
            if current and start - last_end > max_gap:
                groups.append(current)
                current = []
            current.append(record)
            last_end = _safe_float(record.get("end_time"), start)
        if current:
            groups.append(current)

        action_rows = []
        for group in groups:
            window_ids = sorted({int(r["window_id"]) for r in group})
            phase_counts: Dict[str, int] = {}
            for record in group:
                record_phase = infer_record_phase(record)
                if record_phase:
                    phase_counts[record_phase] = phase_counts.get(record_phase, 0) + 1
            dominant_phase = max(
                phase_counts,
                key=lambda item: (phase_counts[item], item),
                default="",
            )
            action_rows.append({
                "id": f"record_action_{re.sub(r'[^A-Za-z0-9]+', '_', title)[:24]}_{window_ids[0]}",
                "type": event_type,
                "severity": severity,
                "title": title,
                "summary": summary,
                "window_ids": window_ids,
                "representative_window_id": window_ids[-1],
                "start_time": min(_safe_float(r.get("start_time"), 0.0) for r in group),
                "end_time": max(_safe_float(r.get("end_time"), 0.0) for r in group),
                "confidence": confidence,
                "source": "record-derived-report",
                "surgical_phase": dominant_phase,
            })
        return action_rows

    def concise_event_summary(event: Dict[str, Any]) -> str:
        text = re.sub(r"\s+", " ", str(event.get("summary") or "")).strip()
        title = str(event.get("title") or "")
        if event.get("type") == "cvs":
            if _has_cvs_achieved_text(f"{title} {text}"):
                return "系统记录到 CVS 达成证据，仍需医生回看确认肝胆三角清理、胆囊板暴露及仅两条结构进入胆囊三要素。"
            return "系统持续进行 CVS 安全视野评估，尚未形成达成结论；需医生回看确认三要素。"
        if "CVS未达成" in title or "Scissors before CVS" in title:
            return (
                "多个窗口检测到剪刀相关操作，但系统尚未记录 CVS 达成；剪断目标结构前需继续核查。"
                if re.search(r"多个窗口|multiple windows", text, re.IGNORECASE) else
                "检测到剪刀相关操作，但系统尚未记录 CVS 达成；剪断目标结构前需继续核查。"
            )
        if "出血已控制" in title or "Bleeding controlled" in title:
            return "窗口摘要提示清理术野并确认出血控制。"
        if len(text) > 110:
            text = text[:107].rstrip("，。；; ") + "..."
        return text or title

    def merge_report_events(
        rows: List[Dict[str, Any]],
        limit: int = 5,
        max_gap: float = 90.0,
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        for event in sorted(rows or [], key=lambda e: (_safe_float(e.get("start_time"), 0.0), int(e.get("representative_window_id", 0) or 0))):
            title = str(event.get("title") or "")
            if merged:
                last = merged[-1]
                if (
                    str(last.get("title") or "") == title
                    and _safe_float(event.get("start_time"), 0.0) - _safe_float(last.get("end_time"), 0.0) <= max_gap
                ):
                    last["end_time"] = max(_safe_float(last.get("end_time"), 0.0), _safe_float(event.get("end_time"), 0.0))
                    last_ids = list(last.get("window_ids") or [])
                    last["window_ids"] = sorted(set(last_ids + list(event.get("window_ids") or [])))
                    last["representative_window_id"] = max(int(last.get("representative_window_id") or 0), int(event.get("representative_window_id") or 0))
                    continue
            merged.append(dict(event))
        return merged[:limit]

    phase_ranges: List[Dict[str, Any]] = []
    current_phase = ""
    current_start = 0.0
    current_end = 0.0
    post_retrieval_seen = False
    for record in ordered_records:
        phase = infer_record_phase(record)
        if phase == "PostRetrievalReview":
            post_retrieval_seen = True
        elif post_retrieval_seen and phase == "CleaningCoagulation":
            phase = "PostRetrievalReview"
        if phase == "" and current_phase:
            # A transient scope-exit/visibility window does not end the
            # surrounding surgical phase.
            current_end = max(current_end, _safe_float(record.get("end_time"), current_end))
            continue
        if phase in {"Unknown", "未知", ""}:
            if current_phase:
                phase_ranges.append({"phase": current_phase, "start": current_start, "end": current_end})
                current_phase = ""
                current_start = 0.0
                current_end = 0.0
            continue
        start = _safe_float(record.get("start_time"), 0.0)
        end = _safe_float(record.get("end_time"), start)
        if not current_phase:
            current_phase, current_start, current_end = phase, start, end
            continue
        if phase == current_phase and start - current_end <= 10:
            current_end = max(current_end, end)
        else:
            phase_ranges.append({"phase": current_phase, "start": current_start, "end": current_end})
            current_phase, current_start, current_end = phase, start, end
    if current_phase:
        phase_ranges.append({"phase": current_phase, "start": current_start, "end": current_end})

    def pick_events(*types: str, severity: Optional[str] = None, pattern: str = "", limit: int = 4) -> List[Dict[str, Any]]:
        out = []
        for event in ordered_events:
            text = f"{event.get('title', '')} {event.get('summary', '')}"
            if types and str(event.get("type") or "") not in types:
                continue
            if severity and str(event.get("severity") or "") != severity:
                continue
            if pattern and not re.search(pattern, text, re.IGNORECASE):
                continue
            out.append(event)
            if len(out) >= limit:
                break
        return out

    critical_events = [
        event for event in ordered_events
        if str(event.get("severity") or "") == "critical"
        and str(event.get("type") or "") in {"risk", "visibility"}
    ][:4]
    action_events = pick_events(
        "action",
        pattern=r"夹闭|闭合|夹子|释放|剪刀|剪断|切断|装袋|取出|Hem-o-lok|钛夹|clip|bag|removal",
        limit=8,
    )[:5]
    record_action_events: List[Dict[str, Any]] = []
    if not any(
        _has_explicit_clip_evidence(f"{event.get('title', '')} {event.get('summary', '')}")
        for event in action_events
    ):
        record_action_events += group_record_action(
            r"(?:Hem[-\s]?o[-\s]?lok|hemolok|hemlock|金属钛夹|钛夹(?!钳)|施夹器|施夹钳).{0,24}(?:夹闭|闭合|释放)|(?:已夹闭|已闭合|夹闭残端|闭合残端)|(?:夹闭|闭合).{0,12}(?:胆囊管|胆囊动脉)|已释放夹子|可见夹子",
            "夹闭操作核查",
            "窗口摘要提示存在夹子放置或夹闭操作；目标结构需结合原片确认。",
        )
    record_action_events += group_record_action(
        r"(?:剪刀.{0,8})?(?:剪切|剪断|切断|离断).{0,18}胆囊动脉|胆囊动脉.{0,18}(?:剪断|切断|离断)",
        "胆囊动脉剪断核查",
        "窗口摘要提示剪刀处理胆囊动脉；是否完成不可逆剪断需医生回看确认。",
        exclude_pattern=r"CVS尚未|尚未确认|需核查|再剪断",
    )
    record_action_events += group_record_action(
        r"(?:剪刀.{0,8})?(?:剪切|剪断|切断|离断).{0,18}胆囊管|胆囊管.{0,18}(?:剪断|切断|离断)",
        "胆囊管剪断核查",
        "窗口摘要提示剪刀处理胆囊管；是否完成不可逆剪断需医生回看确认。",
        exclude_pattern=r"CVS尚未|尚未确认|需核查|再剪断",
    )
    record_action_events += group_record_action(
        r"穿刺|穿孔|穿入|穿透|钻孔|trocar|puncture|perforat|drill",
        "穿刺进入动作",
        "窗口摘要提示存在穿刺或进入相关动作，需结合原片确认具体器械和部位。",
    )
    action_events = merge_report_events([*action_events, *record_action_events], limit=5)
    cvs_events = pick_events("cvs", limit=3)
    if not cvs_events:
        cvs_events = group_record_action(
            r"CVS.{0,12}(?:评估|核查|安全视野|确认|达成)|安全关键视野|关键安全视野|两条结构|胆囊板",
            "CVS安全评估节点",
            "系统进入 CVS 安全核查阶段，需医生回看确认肝胆三角清理、胆囊板暴露及仅两条结构进入胆囊是否充分满足。",
            confidence=0.58,
            max_gap=45.0,
            event_type="cvs",
            severity="safety",
        )
    visibility_events = pick_events("visibility", pattern=r"雾|体外|腹腔外|outside|fog|smoke|blur|trocar", limit=4)
    record_visibility_events: List[Dict[str, Any]] = []
    if not visibility_events:
        record_visibility_events += group_record_action(
            r"镜头移出体外|腹壁外|腹腔外|套管口|outside body|extra-abdominal|trocar",
            "镜头移出体外",
            "镜头移出体外，画面切换至套管口或腹壁外场景。",
            confidence=0.76,
            max_gap=20.0,
            event_type="visibility",
            severity="important",
        )
        record_visibility_events += group_record_action(
            r"起雾|雾气|烟雾|水汽|视野受遮挡|fog|smoke|blur",
            "视野起雾",
            "镜头起雾或烟雾遮挡，手术视野受影响。",
            confidence=0.72,
            max_gap=20.0,
            event_type="visibility",
            severity="critical",
        )
    visibility_events = merge_report_events([*visibility_events, *record_visibility_events], limit=4, max_gap=20.0)
    bleeding_events = pick_events("risk", "resolution", pattern=r"出血|止血|bleeding|hemostasis", limit=4)
    bleeding_events = merge_report_events(bleeding_events, limit=3, max_gap=30.0)
    visibility_events = merge_report_events(visibility_events, limit=3, max_gap=20.0)

    technique_events: List[Dict[str, Any]] = []
    technique_events += group_record_action(
        r"双极电凝钳.{0,90}(?:夹持|分离|凝血|处理)",
        "双极电凝钳解剖",
        "双极电凝钳反复开合夹持并分离纤维脂肪组织，逐步扩大关键结构暴露。",
        confidence=0.78,
        max_gap=15.0,
        phases={"CalotTriangleDissection", "GallbladderDissection", "CleaningCoagulation"},
    )
    technique_events += group_record_action(
        r"电凝钩.{0,70}(?:分离|凝血|处理)",
        "电凝钩解剖",
        "电凝钩沿解剖层次分离肝胆三角或胆囊床组织；具体组织边界需结合原片确认。",
        confidence=0.70,
        max_gap=25.0,
        phases={"CalotTriangleDissection", "ClippingCutting", "GallbladderDissection", "CleaningCoagulation"},
    )
    technique_events += group_record_action(
        r"抓钳.{0,70}(?:牵拉|夹持).{0,50}(?:暴露|肝胆三角|胆囊床)",
        "抓钳牵拉暴露",
        "抓钳牵拉胆囊颈或胆囊体以维持肝胆三角、胆囊床等操作区域暴露。",
        confidence=0.68,
        max_gap=25.0,
        phases={"CalotTriangleDissection", "ClippingCutting", "GallbladderDissection"},
    )
    technique_events += group_record_action(
        r"(?:冲洗|冲吸|吸引).{0,45}(?:术野|液体|清理)|(?:冲洗器|冲吸器).{0,45}(?:冲洗|吸引|清理)",
        "冲洗与术野清理",
        "使用冲洗或吸引操作清理液体和术野，为后续观察与复查提供视野。",
        confidence=0.66,
        max_gap=20.0,
    )
    merged_technique_events = merge_report_events(technique_events, limit=40, max_gap=20.0)
    for event in merged_technique_events:
        title = str(event.get("title") or "")
        event_phase = str(event.get("surgical_phase") or "")
        if title == "电凝钩解剖":
            if event_phase == "GallbladderDissection":
                event["summary"] = "电凝钩沿胆囊壁与肝床间隙逐层分离粘连组织，扩大胆囊床剥离范围。"
            else:
                event["summary"] = "电凝钩沿肝胆三角解剖层次分离纤维脂肪组织，逐步扩大关键结构暴露。"
        elif title == "抓钳牵拉暴露":
            if event_phase == "GallbladderDissection":
                event["summary"] = "抓钳抬起并调整胆囊体位置，以维持胆囊壁与肝床间隙暴露。"
            else:
                event["summary"] = "抓钳牵拉胆囊颈部并抬起胆囊体，以维持肝胆三角操作区域暴露。"
        elif title == "冲洗与术野清理":
            event["summary"] = "冲吸器清理术野内液体和组织碎屑，为后续观察与安全核查恢复视野。"
    detailed_technique_events = [dict(event) for event in merged_technique_events]
    title_counts: Dict[str, int] = {}
    technique_events = []
    for event in merged_technique_events:
        title = str(event.get("title") or "")
        if title_counts.get(title, 0) >= 2:
            continue
        title_counts[title] = title_counts.get(title, 0) + 1
        technique_events.append(event)
    technique_events.sort(key=lambda event: _safe_float(event.get("start_time"), 0.0))

    def compact_event_overview(rows: List[Dict[str, Any]], limit: int = 4) -> str:
        grouped: Dict[str, List[str]] = {}
        ordered_titles: List[str] = []
        for event in rows:
            title = re.sub(r"\s+", " ", str(event.get("title") or "")).strip()
            if not title:
                continue
            if title not in grouped:
                grouped[title] = []
                ordered_titles.append(title)
            time_range = _event_time_range(event)
            if time_range not in grouped[title]:
                grouped[title].append(time_range)
        return "；".join(
            f"{title}（{'、'.join(grouped[title][:3])}）"
            for title in ordered_titles[:limit]
        )

    phase_sequence: List[str] = []
    for row in phase_ranges:
        label = _phase_event_label(str(row.get("phase") or ""))
        if label and (not phase_sequence or phase_sequence[-1] != label):
            phase_sequence.append(label)
    technique_overview = compact_event_overview(detailed_technique_events, limit=4)
    action_overview = compact_event_overview(action_events, limit=4)
    visibility_overview = compact_event_overview([*bleeding_events, *visibility_events], limit=4)
    cvs_achieved = any(
        _has_cvs_achieved_text(f"{event.get('title', '')} {event.get('summary', '')}")
        for event in cvs_events
    )

    def phase_progress_detail(phase_row: Dict[str, Any], max_clauses: int = 2) -> str:
        phase = str(phase_row.get("phase") or "")
        if phase == "Preparation":
            return "完成初始暴露、入路准备与术野建立"
        start = _safe_float(phase_row.get("start"), 0.0)
        end = _safe_float(phase_row.get("end"), start)
        category_rows: Dict[str, Dict[str, Any]] = {}
        category_priority = {
            "clip": 3.0,
            "cut": 2.8,
            "bipolar": 2.5,
            "hook": 2.0,
            "bag": 2.0,
            "irrigation": 1.5,
            "grasper": 1.2,
            "other": 0.0,
        }

        def category_for(clause: str) -> str:
            if re.search(r"夹闭|施夹|钛夹|Hem-o-lok|夹子", clause, re.IGNORECASE):
                return "clip"
            if re.search(r"剪刀|剪断|切断|离断", clause):
                return "cut"
            if "双极电凝钳" in clause:
                return "bipolar"
            if "电凝钩" in clause:
                return "hook"
            if re.search(r"标本袋|胆囊袋|装袋|取出", clause):
                return "bag"
            if re.search(r"冲洗|冲吸|吸引|清理术野", clause):
                return "irrigation"
            if re.search(r"抓钳|牵拉", clause):
                return "grasper"
            return "other"

        for record in ordered_records:
            record_start = _safe_float(record.get("start_time"), 0.0)
            record_end = _safe_float(record.get("end_time"), record_start)
            if record_end < start or record_start > end or infer_record_phase(record) != phase:
                continue
            text = re.sub(r"^当前处于[^，。；;]{1,24}[，,]?", "", str(record.get("summary") or "")).strip()
            for clause in re.split(r"[。；;]+", text):
                clause = re.sub(r"\s+", " ", clause).strip(" ，,。；;")
                if not clause or re.search(r"^CVS|安全视野确认中|夹闭前后安全核查中", clause):
                    continue
                if re.fullmatch(r"画面以.{0,40}为主|术野观察|当前阶段", clause):
                    continue
                category = category_for(clause)
                entry = category_rows.setdefault(category, {"count": 0, "clause": clause})
                entry["count"] += 1
                if len(clause) > len(str(entry.get("clause") or "")):
                    entry["clause"] = clause

        ranked = sorted(
            category_rows.items(),
            key=lambda item: (
                category_priority.get(item[0], 0.0) + min(2.0, float(item[1]["count"]) / 8.0),
                len(str(item[1].get("clause") or "")),
            ),
            reverse=True,
        )
        selected = [
            str(item[1]["clause"])
            for item in ranked[:max(1, max_clauses)]
            if item[1].get("clause")
        ]
        detail = "；".join(dict.fromkeys(selected))
        return detail[:360].rstrip("，。；; ")

    def phase_review_focus(phase: str) -> str:
        focuses = {
            "Preparation": "建立腹腔镜术野并调整胆囊牵拉方向，为肝胆三角解剖提供稳定暴露。",
            "CalotTriangleDissection": "清理肝胆三角纤维脂肪组织，逐步显露进入胆囊的关键结构，并持续核查 CVS 三要素。",
            "ClippingCutting": "核查胆囊管、胆囊动脉走行及夹体位置；任何不可逆剪断均需建立在目标结构和 CVS 已确认的基础上。",
            "GallbladderDissection": "沿胆囊壁与肝床间隙继续剥离，维持正确组织层面并观察剥离面活动性出血。",
            "GallbladderPackaging": "将游离胆囊完整纳入标本袋，确认袋口和标本位置后准备取出。",
            "CleaningCoagulation": "清除术野内液体和组织碎屑，复查剥离面及凝血状态。",
            "PostRetrievalReview": "镜头重新进入腹腔后复查胆囊床、残端区域和术野清洁情况。",
            "GallbladderRetraction": "持续牵拉标本袋经切口取出，并记录镜头进入体外场景后的视野变化。",
        }
        return focuses.get(phase, "复核本阶段的主要器械操作、解剖进展和安全状态。")

    def phase_technique_details(phase_row: Dict[str, Any]) -> List[str]:
        phase = str(phase_row.get("phase") or "")
        phase_start = _safe_float(phase_row.get("start"), 0.0)
        phase_end = _safe_float(phase_row.get("end"), phase_start)
        grouped: Dict[str, Dict[str, Any]] = {}
        for event in detailed_technique_events:
            event_start = _safe_float(event.get("start_time"), 0.0)
            event_end = _safe_float(event.get("end_time"), event_start)
            if event_end <= phase_start or event_start >= phase_end:
                continue
            event_phase = str(event.get("surgical_phase") or "")
            if event_phase and event_phase != phase:
                continue
            title = str(event.get("title") or "器械操作")
            clipped_start = max(phase_start, event_start)
            clipped_end = min(phase_end, event_end)
            if clipped_end <= clipped_start:
                continue
            entry = grouped.setdefault(
                title,
                {
                    "summary": concise_event_summary(event),
                    "ranges": [],
                    "duration": 0.0,
                    "first": clipped_start,
                },
            )
            entry["ranges"].append(
                f"{_format_report_time(clipped_start)}-{_format_report_time(clipped_end)}"
            )
            entry["duration"] += clipped_end - clipped_start
            entry["first"] = min(_safe_float(entry.get("first"), clipped_start), clipped_start)

        lines_out: List[str] = []
        for title, entry in sorted(grouped.items(), key=lambda item: _safe_float(item[1].get("first"), 0.0)):
            ranges = list(dict.fromkeys(entry.get("ranges") or []))
            shown_ranges = "、".join(ranges[:6])
            if len(ranges) > 6:
                shown_ranges += f"，另 {len(ranges) - 6} 段"
            duration_text = _format_report_time(entry.get("duration"))
            lines_out.append(
                f"- **{title}**：{shown_ranges}（累计约 {duration_text}）。{entry.get('summary')}"
            )
        return lines_out

    def record_context_text(record: Optional[Dict[str, Any]]) -> str:
        if not record:
            return ""
        text = _expand_vague_operation_language(
            str(record.get("summary") or ""),
            infer_record_phase(record),
        )
        text = re.sub(r"^当前处于[^，。；;]{1,30}[，,。.]?", "", text).strip()
        clauses: List[str] = []
        for clause in re.split(r"[。；;]+", text):
            clause = re.sub(r"\s+", " ", clause).strip(" ，,。；; ")
            if not clause:
                continue
            if re.search(r"^(?:CVS|安全视野确认中|夹闭前后安全核查中)", clause, re.IGNORECASE):
                continue
            if clause in {"当前阶段", "术野观察"}:
                continue
            clauses.append(clause)
            if len(clauses) >= 2:
                break
        return "；".join(clauses)[:220].rstrip("，。；; ")

    def action_context(event: Dict[str, Any], before: bool) -> str:
        event_start = _safe_float(event.get("start_time"), 0.0)
        event_end = _safe_float(event.get("end_time"), event_start)
        candidates: List[Tuple[float, Dict[str, Any]]] = []
        for record in ordered_records:
            record_start = _safe_float(record.get("start_time"), 0.0)
            record_end = _safe_float(record.get("end_time"), record_start)
            if before:
                distance = event_start - record_end
                if distance < 0 or distance > 35:
                    continue
            else:
                distance = record_start - event_end
                if distance < 0 or distance > 35:
                    continue
            context = record_context_text(record)
            if not context:
                continue
            information = sum(
                1
                for token in (
                    "双极电凝钳", "电凝钩", "抓钳", "冲吸器", "钛夹钳",
                    "夹子", "剪刀", "胆囊管", "胆囊动脉", "胆囊床",
                    "标本袋", "起雾", "移出体外", "术野复查",
                )
                if token in context
            )
            score = information * 30 + min(len(context), 180) - distance * 1.5
            candidates.append((score, record))
        if not candidates:
            return ""
        candidates.sort(key=lambda item: item[0], reverse=True)
        return record_context_text(candidates[0][1])

    def phase_related_nodes(phase_row: Dict[str, Any]) -> str:
        phase_start = _safe_float(phase_row.get("start"), 0.0)
        phase_end = _safe_float(phase_row.get("end"), phase_start)
        nodes: List[str] = []
        seen = set()
        for event in [*action_events, *bleeding_events, *visibility_events, *critical_events]:
            event_start = _safe_float(event.get("start_time"), 0.0)
            event_end = _safe_float(event.get("end_time"), event_start)
            if event_end <= phase_start or event_start >= phase_end:
                continue
            title = str(event.get("title") or "关键节点")
            key = (title, _event_time_range(event))
            if key in seen:
                continue
            seen.add(key)
            nodes.append(f"{title}（{_event_time_range(event)}）")
        return "；".join(nodes[:5])

    if zh:
        lines = [
            f"# {video_title} 关键事件复盘报告",
            "",
            "## 整体判断",
        ]
        if phase_sequence:
            lines.append(f"- **流程覆盖**：{' → '.join(phase_sequence)}。")
        if technique_overview:
            lines.append(f"- **主要解剖操作**：{technique_overview}。")
        if action_overview:
            lines.append(f"- **关键里程碑**：{action_overview}。")
        if cvs_events:
            lines.append(
                "- **CVS核查**：系统记录到 CVS 达成证据，仍需医生回看确认三要素。"
                if cvs_achieved else
                "- **CVS核查**：系统持续评估 CVS，但未形成达成结论；夹闭或剪断前需医生回看确认三要素。"
            )
        if visibility_overview:
            lines.append(f"- **出血与视野**：{visibility_overview}。")
        elif ordered_records:
            lines.append("- **出血与视野**：窗口与事件节点未形成大量活动性出血、持续起雾或体外场景记录。")
        lines += [
            "",
            "## 报告范围",
            f"本报告基于 {len(ordered_records)} 个已完成窗口摘要和关键事件节点生成，覆盖约 {_format_report_time(duration)}。时间轴按阶段、主要器械操作和安全事件归并，不逐窗口罗列；夹闭目标、不可逆剪断及 CVS 结论等不确定内容仍需医生回看原片确认。",
        ]
        # Runtime/provider diagnostics stay in the API metadata and logs. The
        # doctor-facing document should contain only review content.

        lines += ["", "## 关键手术阶段"]
        if phase_ranges:
            for row in phase_ranges[:8]:
                detail = phase_progress_detail(row)
                line = f"- {_format_report_time(row['start'])}-{_format_report_time(row['end'])}：{_phase_event_label(row['phase'])}。"
                if detail:
                    line += f" 主要进展：{detail}。"
                lines.append(line)
        else:
            lines.append("- 已读取窗口摘要，但阶段信息不足，需结合原片复核。")

        lines += ["", "## 分阶段详细复盘"]
        if phase_ranges:
            for index, row in enumerate(phase_ranges[:8]):
                phase = str(row.get("phase") or "")
                lines += [
                    "",
                    f"### {_format_report_time(row['start'])}-{_format_report_time(row['end'])} {_phase_event_label(phase)}",
                    f"- **复盘重点**：{phase_review_focus(phase)}",
                ]
                detailed_progress = phase_progress_detail(row, max_clauses=3)
                if detailed_progress:
                    lines.append(f"- **观察到的进展**：{detailed_progress}。")
                technique_lines = phase_technique_details(row)
                if technique_lines:
                    lines.append("- **器械与操作区间**：")
                    lines.extend(f"  {line}" for line in technique_lines)
                else:
                    lines.append("- **器械与操作区间**：窗口记录未形成可稳定合并的独立器械操作区间。")
                related_nodes = phase_related_nodes(row)
                if related_nodes:
                    lines.append(f"- **阶段内关键节点**：{related_nodes}。")
                if phase in CVS_RELEVANT_PHASES:
                    lines.append(
                        "- **阶段安全状态**：系统记录到 CVS 达成证据，仍需医生逐项确认三要素。"
                        if cvs_achieved else
                        "- **阶段安全状态**：CVS 持续评估中，但尚未形成达成结论；夹闭和不可逆剪断前需继续核查。"
                    )
                if index + 1 < len(phase_ranges[:8]):
                    next_row = phase_ranges[index + 1]
                    lines.append(
                        f"- **阶段衔接**：{_format_report_time(row['end'])} 后进入{_phase_event_label(str(next_row.get('phase') or ''))}。"
                    )
        else:
            lines.append("- 阶段信息不足，无法形成逐阶段详细复盘；需结合原片核查。")

        lines += ["", "## 关键操作与上下文"]
        if action_events:
            for event in action_events:
                lines += [
                    "",
                    f"### {_event_time_range(event)} {event.get('title')}",
                ]
                before_context = action_context(event, before=True)
                if before_context:
                    lines.append(f"- **操作前**：{before_context}。")
                lines.append(f"- **节点记录**：{concise_event_summary(event)}")
                after_context = action_context(event, before=False)
                if after_context:
                    lines.append(f"- **后续状态**：{after_context}。")
        else:
            lines.append("- 未形成稳定的夹闭、剪断、装袋取出等关键操作节点；需回看原片确认核心操作。")

        lines += ["", "## CVS/安全核查"]
        if cvs_events:
            for event in merge_report_events(cvs_events, limit=2):
                lines.append(f"- {_event_time_range(event)}：{concise_event_summary(event)}")
        else:
            has_cvs_relevant_phase = any(
                infer_record_phase(record) in CVS_RELEVANT_PHASES
                for record in ordered_records
            )
            lines.append(
                "- 未形成明确 CVS 事件节点；若涉及夹闭或剪断，需回看原片确认 CVS 三要素是否充分满足。"
                if has_cvs_relevant_phase else
                "- 本片段未覆盖肝胆三角解剖或夹闭切断阶段，不单独形成 CVS 结论。"
            )
        for event in critical_events:
            if re.search(r"CVS|剪刀|scissors", f"{event.get('title', '')} {event.get('summary', '')}", re.IGNORECASE):
                lines.append(f"- **风险提示** {_event_time_range(event)}：{concise_event_summary(event)}")

        lines += ["", "## 出血与视野事件"]
        combined_visibility = []
        seen_ids = set()
        for event in [*bleeding_events, *visibility_events]:
            eid = event.get("id") or id(event)
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            combined_visibility.append(event)
        if combined_visibility:
            for event in combined_visibility[:6]:
                lines.append(f"- {_event_time_range(event)}：{event.get('title')}。{concise_event_summary(event)}")
        else:
            lines.append("- 未记录大量活动性出血、起雾或镜头移出体外等独立视野事件。")

        lines += ["", "## 需要医生回看的不确定点"]
        has_clip_context = any(
            re.search(r"夹闭|闭合|夹子|钛夹|施夹", str(record.get("summary") or ""))
            for record in ordered_records
        )
        has_scissors_context = any(
            re.search(r"剪刀|剪断|切断|离断", re.sub(r"夹闭切断", "", str(record.get("summary") or "")))
            for record in ordered_records
        )
        if has_clip_context:
            lines.append("- 夹闭目标与夹体状态仍需回看原片确认。")
        if has_scissors_context:
            lines.append("- 剪刀是否真正完成不可逆切断及其目标结构，仍需回看原片确认。")
        lines.append("- 本报告是辅助复盘材料，不作为最终临床结论。")
        return "\n".join(lines).rstrip() + "\n"

    lines = [
        f"# {video_title} Key Event Review Report",
        "",
        "## Overall Impression",
        f"This report is generated from completed window summaries and key event nodes, covering about {_format_report_time(duration)}. It merges repeated windows into clinical events and should be verified against the source video.",
    ]
    if reason:
        lines.append(f"> The local report model was unavailable; deterministic key-event fallback was used: {reason}")
    lines += ["", "## Key Phases"]
    if phase_ranges:
        for row in phase_ranges[:8]:
            lines.append(f"- {_format_report_time(row['start'])}-{_format_report_time(row['end'])}: {_phase_event_label(row['phase'])}.")
    else:
        lines.append("- Phase evidence is insufficient; review the source video.")
    lines += ["", "## Key Actions"]
    for event in action_events[:5]:
        lines.append(f"- {_event_time_range(event)}: {event.get('title')}. {concise_event_summary(event)}")
    if not action_events:
        lines.append("- No stable clipping, division, bagging, or removal event was formed.")
    lines += ["", "## CVS / Safety Checks"]
    for event in merge_report_events(cvs_events, limit=2):
        lines.append(f"- {_event_time_range(event)}: {concise_event_summary(event)}")
    if not cvs_events:
        lines.append("- No clear CVS event node was formed; verify CVS before clipping or division.")
    for event in critical_events:
        if re.search(r"CVS|scissors|剪刀", f"{event.get('title', '')} {event.get('summary', '')}", re.IGNORECASE):
            lines.append(f"- **Risk** {_event_time_range(event)}: {concise_event_summary(event)}")
    lines += ["", "## Bleeding And Visibility Events"]
    for event in [*bleeding_events, *visibility_events][:6]:
        lines.append(f"- {_event_time_range(event)}: {event.get('title')}. {concise_event_summary(event)}")
    if not bleeding_events and not visibility_events and not critical_events:
        lines.append("- No major bleeding or visibility event was recorded.")
    lines += ["", "## Points Requiring Surgeon Review"]
    lines.append("- Clip type, target anatomy, and irreversible division events should be confirmed on the source video.")
    return "\n".join(lines).rstrip() + "\n"


def _unsupported_clinical_report_claims(markdown: Any, language: str) -> List[str]:
    """Find certainty/quality claims that sampled window evidence cannot prove."""
    text = str(markdown or "")
    if not text:
        return ["empty_report"]
    patterns = (
        r"手术流程(?:完整|顺利)",
        r"(?:操作|处理)(?:规范|标准)",
        r"未见(?:明显)?技术失误",
        r"(?:无|未见)[^。\n]{0,18}(?:漏出|外溢|撕裂|松脱|滑脱|残留)",
        r"(?:全程)?(?:无|未见)[^。\n]{0,12}(?:活动性)?出血",
        r"视野(?:始终|持续)清晰",
        r"(?:无|未见)[^。\n]{0,12}(?:起雾|烟雾|视野异常)",
        r"(?:凝血|止血)(?:处理)?(?:充分|及时|完整|彻底)",
        r"术野复查(?:充分|完整)",
        r"(?:完整|成功)(?:装入|包裹|夹闭)",
        r"顺利取出",
        r"符合(?:操作规范|术后常规操作)",
    )
    if language.startswith("en"):
        patterns += (
            r"no (?:active )?bleeding",
            r"without (?:leak|tear|complication)",
            r"procedure was (?:complete|uneventful|standard)",
            r"adequate hemostasis",
            r"successfully (?:clipped|removed|bagged)",
        )
    hits: List[str] = []
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            hits.append(match.group(0)[:80])
    return hits


def _build_clinical_video_summary_prompt(
    video_title: str,
    records: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    language: str,
    max_events: int,
) -> str:
    if language.startswith("en"):
        return f"""
You are generating a doctor-facing Markdown review summary for one laparoscopic cholecystectomy video.

Video: {video_title}

Rules:
1. Summarize this single video only. Do not merge with any other video.
2. Do not write a window-by-window log. Extract clinical meaning from repeated windows.
3. Required sections: Overall impression, Key phases, Key actions, CVS / safety checks, Bleeding and visibility events, Points requiring surgeon review.
4. Focus on cystic duct/artery clipping and division, deployed clips, specimen bagging/removal, scope outside body, fog/smoke, active bleeding, and hemostasis.
5. Preserve uncertainty. If the data does not prove an event, say it requires source-video review.
6. Do not output window ids. Use at most 6 concrete time ranges in the full report.
7. Output concise English Markdown only.
8. For CVS, do not state that CVS was absent or unclear if the input contains CVS assessment/safety events.
   Phrase it as a system-supported CVS assessment milestone that still requires surgeon confirmation.
9. Key event nodes are higher priority than repeated window rows. Merge repeated fog, bleeding, cleaning,
   and visibility records into one clinical event range; do not list many short repeated time spans.
10. Never infer procedural quality or absence from missing detections. Forbidden claims include: complete/uneventful
   procedure, standard technique, no bleeding, no leak/tear, clear throughout, adequate hemostasis, or successful
   clipping/removal. State only recorded positive events and explicitly preserve uncertainty.

Key event nodes:
{_format_clinical_summary_events(events, max_events)}

Window summary data:
{_format_clinical_summary_records(records)}
""".strip()

    return f"""
请基于下面的腹腔镜胆囊切除术窗口摘要，为单个视频生成医生复盘用的 Markdown 精要总结。

视频：{video_title}

要求：
1. 只总结这一个视频，不能和其他视频合并。
2. 不要写成流水账，不要逐窗口复述；相邻重复内容要归并成阶段或临床事件。
3. 必须包含这些小节：整体判断、关键手术阶段、关键操作、CVS/安全核查、出血与视野事件、需要医生回看的不确定点。
4. 重点关注胆囊管/胆囊动脉夹闭和切断、夹子放置、标本袋装袋/取出、镜头移出体外、起雾/烟雾、活动性出血和凝血控制。
5. 保留不确定性；数据不能证明的内容必须写“需回看原片确认”，不能把模型结果写成临床定论。
6. 不要输出窗口编号或 id；全文最多出现 6 个具体时间段。
7. 关键操作只写临床重要动作，不要重复写“可见器械”。
8. CVS部分：如果输入中已有CVS评估/安全核查/关键安全视野事件，不要写“CVS未明确”或“未达成”这类绝对否定；
   应写成“系统提示已形成CVS评估/安全核查节点，仍需医生回看确认三要素是否充分满足”。
9. 关键事件节点优先于逐窗口摘要；重复出现的起雾、凝血、清理、体外视野等窗口必须合并成一个临床事件时间段，
   不要罗列多个零碎时间点。
10. 输出中文 Markdown，控制在 700-1000 个中文字符左右。
11. 禁止从“没有检测到”推导临床阴性结论，也禁止评价操作质量。不要写“手术流程完整/操作规范/未见技术失误/无活动性出血/无漏出或撕裂/视野始终清晰/凝血充分/顺利取出”等句子。
12. 只能写输入中明确记录到的阳性事件；没有形成事件时写“系统未形成独立事件记录”，不能写“未发生”。夹子数量、目标结构、CVS达成、不可逆剪断均需保留原片复核提示。

关键事件节点：
{_format_clinical_summary_events(events, max_events)}

窗口摘要数据：
{_format_clinical_summary_records(records)}
""".strip()


async def _call_clinical_summary_llm(prompt: str, language: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    provider = cfg.get("provider") or "gemini"
    if os.environ.get("DISABLE_EXTERNAL_AI") == "1" and provider != "glm":
        return {
            "success": False,
            "error": "external clinical summary LLM disabled by runtime environment",
        }

    model_name = cfg.get("model_name") or "gemini-2.5-flash"
    max_tokens = int(cfg.get("max_tokens") or 2200)
    temperature = float(cfg.get("temperature", 0.0))
    system_prompt = (
        "你是资深腹腔镜胆囊切除术视频复盘助手，只输出专业、审慎的Markdown。"
        if language.startswith("zh")
        else "You are a senior laparoscopic cholecystectomy video review assistant. Output professional Markdown only."
    )

    if provider == "glm":
        client = get_glm_client()
        return await client.chat(
            message=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            disable_thinking=True,
        )

    if provider in {"openai", "gpt", "openai_compatible"}:
        return await _call_openai_compatible_event_nodes(
            prompt=prompt,
            system_prompt=system_prompt,
            event_cfg=cfg,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    from ..services.gemini_client import GeminiClient

    client = GeminiClient(
        model_name=model_name,
        thinking_level=cfg.get("thinking_level") or "none",
        max_tokens=max_tokens,
    )
    return await client.chat(
        message=prompt,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )


async def _call_openai_compatible_event_nodes(
    prompt: str,
    system_prompt: str,
    event_cfg: Dict[str, Any],
    max_tokens: int,
    temperature: float,
) -> Dict[str, Any]:
    """Call OpenAI-compatible chat completions without requiring the openai package."""
    api_key_env = event_cfg.get("api_key_env", "OPENAI_API_KEY")
    api_key = event_cfg.get("api_key") or os.environ.get(api_key_env) or settings.OPENAI_API_KEY
    if not api_key:
        raise RuntimeError(f"{api_key_env} is not configured")

    base_url = (
        event_cfg.get("base_url")
        or os.environ.get("OPENAI_BASE_URL")
        or settings.OPENAI_BASE_URL
        or "https://api.openai.com/v1"
    ).rstrip("/")
    model_name = event_cfg.get("model_name") or "gpt-4o-mini"
    timeout = float(event_cfg.get("timeout", 30.0))
    trust_env = bool(event_cfg.get("trust_env", False))
    transport = str(event_cfg.get("transport", "curl")).lower()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    if transport == "curl":
        def quote_curl_config(value: str) -> str:
            return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'

        curl_payload = json.dumps(payload, ensure_ascii=False)
        curl_config = "\n".join([
            f"url = {quote_curl_config(f'{base_url}/chat/completions')}",
            'request = "POST"',
            f"max-time = {int(max(1, timeout))}",
            f"header = {quote_curl_config(f'Authorization: Bearer {api_key}')}",
            'header = "Content-Type: application/json"',
            f"data = {quote_curl_config(curl_payload)}",
        ]) + "\n"
        proc = await asyncio.create_subprocess_exec(
            "curl",
            "-sS",
            "-K",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout, stderr = await proc.communicate(curl_config.encode("utf-8"))
        if proc.returncode != 0:
            raise RuntimeError(f"curl OpenAI-compatible event-node call failed: {stderr.decode('utf-8', 'ignore')[:1000]}")
        try:
            data = json.loads(stdout.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"curl OpenAI-compatible response is not JSON: {stdout[:1000]!r}") from exc
        if data.get("error"):
            raise RuntimeError(f"OpenAI-compatible event-node call failed: {data.get('error')}")
        try:
            text = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise RuntimeError(f"OpenAI-compatible response missing text: {data}") from exc
        return {
            "success": True,
            "text": text,
            "model": data.get("model") or model_name,
        }

    async with httpx.AsyncClient(timeout=timeout, trust_env=trust_env) as client:
        response = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
        if response.status_code >= 400:
            body = response.text[:1200]
            retry_payload = dict(payload)
            should_retry = False
            if "max_tokens" in body and "max_completion_tokens" in body:
                retry_payload.pop("max_tokens", None)
                retry_payload["max_completion_tokens"] = max_tokens
                should_retry = True
            if "temperature" in body and ("unsupported" in body.lower() or "not support" in body.lower()):
                retry_payload.pop("temperature", None)
                should_retry = True
            if should_retry and retry_payload != payload:
                response = await client.post(f"{base_url}/chat/completions", headers=headers, json=retry_payload)
                body = response.text[:1200]
            if response.status_code >= 400:
                raise RuntimeError(f"OpenAI-compatible event-node call failed: HTTP {response.status_code}: {body}")

    data = response.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise RuntimeError(f"OpenAI-compatible response missing text: {data}") from exc
    return {
        "success": True,
        "text": text,
        "model": data.get("model") or model_name,
    }


@router.post("/event-nodes/{session_id}")
async def get_event_nodes(
    session_id: str,
    request: EventNodesRequest,
    db: Session = Depends(get_db)
):
    """Generate key event nodes from stored window summaries via text-only LLM."""
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    config = load_config()
    event_cfg = config.get("services", {}).get("event_nodes", {})
    translation_cfg = config.get("services", {}).get("translation", {})
    language = (request.language or "zh").lower()
    if not language.startswith("en"):
        language = "zh"

    configured_max = int(event_cfg.get("max_windows", 120) or 120)
    max_windows = int(request.max_windows or configured_max)
    max_windows = max(6, min(max_windows, 180))

    raw_summaries = get_summaries_by_session(db, session["session_id"])
    records = [
        item for item in (_normalize_summary_for_event_nodes(s) for s in raw_summaries)
        if item and item.get("summary")
    ]
    records.sort(key=lambda r: (r["window_id"], r["start_time"]))

    if not records:
        return {
            "success": True,
            "session_id": session_id,
            "language": language,
            "source": "empty",
            "cached": False,
            "window_count": 0,
            "events": [],
        }

    # The LLM only needs a representative, safety-biased sample. Required
    # events and visibility state are rebuilt from every stored window below,
    # so prompt compaction can never discard a risk interval from the result.
    prompt_records = records
    if len(prompt_records) > max_windows:
        prompt_records = _compact_clinical_summary_records(prompt_records, max_windows)
        prompt_records.sort(key=lambda r: (r["window_id"], r["start_time"]))

    signature = _event_nodes_signature(records)
    cache_key = (session_id, language, max_windows, signature)
    if not request.force and cache_key in event_node_cache:
        cached = event_node_cache[cache_key]
        return {
            **cached,
            "cached": True,
        }

    provider = event_cfg.get("provider") or translation_cfg.get("provider", "gemini")
    model_name = event_cfg.get("model_name") or translation_cfg.get("model_name")
    max_tokens = int(event_cfg.get("max_tokens") or 1200)
    temperature = float(event_cfg.get("temperature", 0.0))
    llm_timeout = max(3.0, float(request.timeout or event_cfg.get("timeout") or 30.0))

    system_prompt = (
        "You are a surgical event-node analyst for laparoscopic cholecystectomy. "
        "You convert existing time-window summaries into a concise, structured key-event timeline. "
        "You must preserve uncertainty, never add unobserved findings, and output valid JSON only."
    )
    prompt = _build_event_nodes_prompt(prompt_records, language)

    try:
        if os.environ.get("DISABLE_EXTERNAL_AI") == "1" and provider != "glm":
            raise RuntimeError("external event node LLM disabled by runtime environment")

        if not event_cfg.get("enabled", True):
            raise RuntimeError("event node LLM disabled by config")

        if provider == "glm":
            client = get_glm_client()
            result = await asyncio.wait_for(
                client.chat(
                    message=prompt,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    disable_thinking=True,
                ),
                timeout=llm_timeout,
            )
        elif provider in {"openai", "gpt", "openai_compatible"}:
            result = await asyncio.wait_for(
                _call_openai_compatible_event_nodes(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    event_cfg=event_cfg,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ),
                timeout=llm_timeout,
            )
        else:
            from ..services.gemini_client import GeminiClient
            fallback_models = event_cfg.get("fallback_models") or [
                "gemini-2.5-flash",
                "gemini-2.0-flash",
                "gemini-1.5-flash",
            ]
            candidate_models = []
            for candidate in [model_name, *fallback_models]:
                if candidate and candidate not in candidate_models:
                    candidate_models.append(candidate)

            result = None
            last_error = ""
            for candidate_model in candidate_models:
                client = GeminiClient(
                    model_name=candidate_model,
                    thinking_level=event_cfg.get("thinking_level", translation_cfg.get("thinking_level", "none")),
                    max_tokens=max_tokens,
                )
                result = await asyncio.wait_for(
                    client.chat(
                        message=prompt,
                        system_prompt=system_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    ),
                    timeout=llm_timeout,
                )
                if result.get("success") and (result.get("text") or "").strip():
                    model_name = candidate_model
                    break
                last_error = result.get("error") or f"{candidate_model} returned empty response"
                logger.warning(f"[EventNodes] Gemini model {candidate_model} failed: {last_error}")
            else:
                raise RuntimeError(last_error or "all Gemini event-node models failed")

        text = (result.get("text") or "").strip()
        if not result.get("success") or not text:
            raise RuntimeError(result.get("error") or "empty event-node response")

        parsed = _parse_event_nodes_json(text)
        events = _normalize_event_nodes(parsed.get("events", []), records, language, "llm")
        if not events:
            raise RuntimeError("LLM returned no usable event nodes")
        events = _ensure_required_event_nodes(events, records, language)
        events = _merge_visibility_status_events(events, records, language)

        response = {
            "success": True,
            "session_id": session_id,
            "language": language,
            "source": "llm",
            "cached": False,
            "provider": provider,
            "model": model_name or result.get("model"),
            "window_count": len(records),
            "prompt_window_count": len(prompt_records),
            "signature": signature,
            "events": events,
        }
    except Exception as exc:
        logger.warning(f"[EventNodes] Falling back for session {session_id}: {exc}")
        response = {
            "success": False,
            "session_id": session_id,
            "language": language,
            "source": "fallback",
            "cached": False,
            "window_count": len(records),
            "prompt_window_count": len(prompt_records),
            "signature": signature,
            "error": f"{type(exc).__name__}: {exc}",
            "events": _merge_visibility_status_events(
                _ensure_required_event_nodes(_fallback_event_nodes(records, language, str(exc)), records, language),
                records,
                language,
            ),
        }

    if response.get("source") == "llm":
        if len(event_node_cache) > 128:
            event_node_cache.pop(next(iter(event_node_cache)), None)
        event_node_cache[cache_key] = response
    return response


@router.post("/clinical-summary/{session_id}")
async def generate_clinical_video_summary(
    session_id: str,
    request: ClinicalSummaryRequest,
    db: Session = Depends(get_db)
):
    """Generate one doctor-facing Markdown report for one analyzed video session."""
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    config = load_config()
    summary_cfg = dict(config.get("services", {}).get("clinical_summary", {}) or {})
    translation_cfg = config.get("services", {}).get("translation", {})
    language = (request.language or "zh").lower()
    if not language.startswith("en"):
        language = "zh"

    configured_max = int(summary_cfg.get("max_windows", 180) or 180)
    max_windows = int(request.max_windows or configured_max)
    max_windows = max(20, min(max_windows, 260))
    max_events = int(request.max_events or summary_cfg.get("max_events", 40) or 40)
    max_events = max(4, min(max_events, 80))

    raw_summaries = get_summaries_by_session(db, session["session_id"])
    records = [
        item for item in (_normalize_summary_for_event_nodes(s) for s in raw_summaries)
        if item and item.get("summary")
    ]
    records.sort(key=lambda r: (float(r.get("start_time", 0) or 0), int(r.get("window_id", 0) or 0)))
    if not records:
        raise HTTPException(400, "No window summaries are available for this session")

    compact_records = _compact_clinical_summary_records(records, max_windows)
    signature = _event_nodes_signature(compact_records)
    cache_key = (session_id, language, max_windows, signature)
    if not request.force and cache_key in clinical_summary_cache:
        return {
            **clinical_summary_cache[cache_key],
            "cached": True,
        }

    # Event nodes are generated from the same session records; no video-name,
    # window-number, or timestamp-specific rules are used here.
    try:
        event_response = await get_event_nodes(
            session_id,
            # Reuse the normalized event set produced for this summary signature.
            # Regenerating it here can yield a different LLM grouping for the same video.
            EventNodesRequest(language=language, force=False, max_windows=180),
            db,
        )
        events = list(event_response.get("events") or [])
    except Exception as exc:
        logger.warning(f"[ClinicalSummary] Event-node generation failed for session {session_id}: {exc}")
        events = _merge_visibility_status_events(_fallback_event_nodes(compact_records, language, str(exc)), compact_records, language)

    video_title = (
        request.video_title
        or session.get("video_name")
        or session.get("video_path")
        or session_id
    )
    prompt = _build_clinical_video_summary_prompt(
        video_title=str(video_title),
        records=compact_records,
        events=events,
        language=language,
        max_events=max_events,
    )

    provider = summary_cfg.get("provider") or translation_cfg.get("provider", "gemini")
    if "provider" not in summary_cfg:
        summary_cfg["provider"] = provider
    if "model_name" not in summary_cfg and translation_cfg.get("model_name"):
        summary_cfg["model_name"] = translation_cfg.get("model_name")
    if "thinking_level" not in summary_cfg and translation_cfg.get("thinking_level"):
        summary_cfg["thinking_level"] = translation_cfg.get("thinking_level")

    source = "llm"
    llm_error = ""
    llm_result: Dict[str, Any] = {}
    try:
        llm_result = await _call_clinical_summary_llm(prompt, language, summary_cfg)
        markdown = str(llm_result.get("text") or "").strip()
        if not llm_result.get("success") or not markdown:
            raise RuntimeError(llm_result.get("error") or "empty clinical summary response")
    except Exception as exc:
        llm_error = str(exc)
        source = "deterministic_fallback"
        logger.warning(f"[ClinicalSummary] LLM unavailable for session {session_id}, using key-event fallback: {exc}")
        markdown = _build_deterministic_clinical_report(
            video_title=str(video_title),
            records=records,
            events=events,
            language=language,
            reason=llm_error,
        )

    if source == "llm":
        unsupported_claims = _unsupported_clinical_report_claims(markdown, language)
        if unsupported_claims:
            source = "deterministic_guardrail"
            llm_error = "unsupported certainty claims: " + ", ".join(unsupported_claims[:6])
            logger.warning(
                "[ClinicalSummary] Rejected overconfident report for session %s: %s",
                session_id,
                llm_error,
            )
            markdown = _build_deterministic_clinical_report(
                video_title=str(video_title),
                records=records,
                events=events,
                language=language,
                reason=llm_error,
            )

    if summary_cfg.get("evidence_grounded_output", True):
        source = "evidence_grounded"
        markdown = _build_deterministic_clinical_report(
            video_title=str(video_title),
            records=records,
            events=events,
            language=language,
        )

    if not markdown.lstrip().startswith("#"):
        heading = f"# {video_title} 临床精要总结" if language.startswith("zh") else f"# {video_title} Clinical Review Summary"
        markdown = f"{heading}\n\n{markdown}"

    safe_title = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", str(video_title)).strip("_")[:80] or "video"
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_dir = Path(request.output_dir) if request.output_dir else Path("docs/clinical_summaries")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{safe_title}_{session_id}_{timestamp}_{language}.md"
    output_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")

    response = {
        "success": True,
        "session_id": session_id,
        "language": language,
        "cached": False,
        "source": source,
        "provider": provider,
        "model": summary_cfg.get("model_name") or llm_result.get("model"),
        "llm_error": llm_error,
        "window_count": len(records),
        "used_window_count": len(compact_records),
        "event_count": len(events),
        "events": events,
        "signature": signature,
        "output_path": str(output_path.resolve()),
        "markdown": markdown,
    }
    if len(clinical_summary_cache) > 64:
        clinical_summary_cache.pop(next(iter(clinical_summary_cache)), None)
    clinical_summary_cache[cache_key] = response
    return response


@router.get("/summary-at/{session_id}")
async def get_summary_at_time(
    session_id: str,
    timestamp: float = Query(..., ge=0),
    db: Session = Depends(get_db)
):
    """Get summary for a specific timestamp"""
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    summary = get_summary_for_timestamp(db, session["session_id"], timestamp)
    
    if summary:
        return {
            "window_id": summary.window_id,
            "start_time": summary.start_time,
            "end_time": summary.end_time,
            "summary": summary.summary_text,
            "tts_audio_path": summary.tts_audio_path
        }
    else:
        return {
            "window_id": None,
            "start_time": None,
            "end_time": None,
            "summary": None,
            "message": "No summary available for this timestamp"
        }


@router.get("/stream-summaries/{session_id}")
async def stream_summaries(
    session_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """Stream summaries as they are generated (SSE)"""
    from ..database import get_session_record

    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    async def event_generator():
        # Pipeline v4: 同一个 window 可能先来 stage=1，后被 OpenVision
        # patch，再被 stage=2 覆盖。内容变化也要推给前端，而不只是 stage 增长。
        last_signature_by_window: Dict[int, str] = {}
        # 4 小时，足够长视频跑完；真实停止靠 session status / cancellation_flag 驱动。
        max_iterations = 14400
        iteration = 0

        try:
            # 客户端断连检测：浏览器关闭 tab / 刷新 / 切换 session 都会让 SSE 连接中断，
            # 这种情况下我们主动给 session 打上 cancellation 标记，让背景 GLM 任务能在
            # 下一轮 loop 里自己停掉，不会继续消耗事件循环。
            pass
        except Exception:
            pass

        while iteration < max_iterations:
            # 每轮先看客户端是不是走了
            if await request.is_disconnected():
                logger.info(f"[SSE] Client disconnected for {session_id}, cancelling background task")
                analysis_cancellation_flags[session_id] = True
                break
            iteration += 1

            try:
                summaries = get_summaries_by_session(db, session["session_id"])

                # 按 window_id 升序推送，保证前端收到顺序稳定
                ordered = sorted(
                    [s for s in summaries if (s.get("window_id") if isinstance(s, dict) else getattr(s, "window_id", None)) is not None],
                    key=lambda s: (s.get("window_id", 0) if isinstance(s, dict) else getattr(s, "window_id", 0))
                )

                for s in ordered:
                    s_window_id = s.get("window_id", 0) if isinstance(s, dict) else getattr(s, "window_id", 0)
                    s_start = s.get("window_start", 0) if isinstance(s, dict) else getattr(s, "start_time", 0)
                    s_end = s.get("window_end", 0) if isinstance(s, dict) else getattr(s, "end_time", 0)
                    s_summary = s.get("glm_summary", "") if isinstance(s, dict) else getattr(s, "summary_text", "")
                    s_phase = s.get("surgical_phase") if isinstance(s, dict) else getattr(s, "surgical_phase", None)
                    s_others = (s.get("others") if isinstance(s, dict) else getattr(s, "others_data", None)) or {}
                    # 兼容旧行（无 stage 字段，视为 stage=2 已完成）
                    stage = int(s_others.get("stage", 2)) if isinstance(s_others, dict) else 2
                    signature = json.dumps({
                        "stage": stage,
                        "summary": s_summary,
                        "phase": s_phase,
                        "others": s_others,
                    }, ensure_ascii=False, sort_keys=True, default=str)
                    prev_signature = last_signature_by_window.get(s_window_id)

                    if signature != prev_signature:
                        payload = {
                            "window_id": s_window_id,
                            "start_time": s_start,
                            "end_time": s_end,
                            "summary": s_summary,
                            "phase": s_phase,
                            "stage": stage,
                        }
                        # 附带 experts / 推理链（前端渲染深度面板）
                        if isinstance(s_others, dict):
                            if "experts" in s_others:
                                payload["experts"] = s_others["experts"]
                            if "surgr1_reasoning" in s_others and s_others["surgr1_reasoning"]:
                                payload["surgr1_reasoning"] = s_others["surgr1_reasoning"]
                            if "stage1_summary" in s_others and s_others["stage1_summary"]:
                                payload["stage1_summary"] = s_others["stage1_summary"]
                        yield f"data: {json.dumps(payload)}\n\n"
                        last_signature_by_window[s_window_id] = signature
                
                # Check if processing is complete or cancelled - reload session from cache/DB
                current_session = get_session_record(session_id)
                if current_session:
                    status = current_session.get("status", "")
                    if status == "completed":
                        yield f"data: {json.dumps({'status': 'completed'})}\n\n"
                        break
                    elif status == "cancelled":
                        yield f"data: {json.dumps({'status': 'cancelled'})}\n\n"
                        break
                
                # Check cancellation flag
                if analysis_cancellation_flags.get(session_id, False):
                    yield f"data: {json.dumps({'status': 'cancelled'})}\n\n"
                    break
                    
            except Exception as e:
                logger.warning(f"[SSE] Error in event_generator: {e}")

            # 2s 节拍：10s 窗口下 2s 检查一次足够及时；减半 DB 扫描频率，
            # 让事件循环在 Gemini/embedding 高耗时任务之间更从容。
            await asyncio.sleep(2)
        
        # Send final completed message if we hit max iterations
        if iteration >= max_iterations:
            yield f"data: {json.dumps({'status': 'completed', 'message': 'timeout'})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )


@router.post("/sam2/segment")
async def segment_frame(
    request: SAM2Request,
    db: Session = Depends(get_db)
):
    """Segment surgical instruments in a frame using SAM2"""
    
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Get frame
    processor = VideoProcessor(video_path=session["video_path"])
    frame = processor.extract_frame(request.timestamp)
    
    if frame is None:
        raise HTTPException(400, "Cannot extract frame")
    
    # Get SAM2 service
    sam2 = get_sam2_service()
    
    if request.auto_detect:
        result = sam2.auto_segment_instruments(frame.image)
    else:
        result = sam2.segment_image(frame.image)
    
    return {
        "timestamp": request.timestamp,
        "frame_idx": frame.frame_idx,
        **result
    }


@router.get("/sam2/status")
async def sam2_status():
    """Check SAM2 availability"""
    sam2 = get_sam2_service()
    return {
        "available": sam2.is_available,
        "loaded": sam2._is_loaded,
        "model_path": sam2.model_path
    }


@router.post("/tts/synthesize")
async def synthesize_speech(request: TTSRequest):
    """Convert text to speech"""
    
    tts = get_tts_service()
    
    result = await tts.synthesize(
        text=request.text,
        voice=request.voice,
        save_to_file=True
    )
    
    return result


@router.post("/tts/summary/{session_id}/{window_id}")
async def synthesize_summary(
    session_id: str,
    window_id: int,
    db: Session = Depends(get_db)
):
    """Generate TTS audio for a window summary"""
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    summaries = get_summaries_by_session(db, session["session_id"])
    summary = next((s for s in summaries if s.window_id == window_id), None)
    
    if not summary:
        raise HTTPException(404, "Summary not found")
    
    tts = get_tts_service()
    
    result = await tts.synthesize_summary(
        summary=summary.summary_text,
        window_id=window_id,
        session_id=session_id,
        save_to_file=True
    )
    
    if result["success"] and result.get("file_path"):
        # Update summary with TTS path
        summary.tts_audio_path = result["file_path"]
        db.commit()
    
    return result


@router.get("/tts/voices")
async def get_tts_voices():
    """Get available TTS voices"""
    tts = get_tts_service()
    return tts.get_available_voices()


@router.post("/analyze-images")
async def analyze_images(
    request: ImageAnalysisRequest,
    db: Session = Depends(get_db)
):
    """
    Analyze images from a video window (independent API)
    
    This endpoint extracts frames from a video window and analyzes them
    using the local VLM model. Returns frame-by-frame analysis results.
    """
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Create video processor
    processor = VideoProcessor(
        video_path=session["video_path"],
        window_duration=settings.WINDOW_DURATION,
        sample_interval=settings.SAMPLE_INTERVAL
    )
    
    # Extract window
    window = processor.extract_window(request.start_time)
    
    if window.frame_count == 0:
        raise HTTPException(400, "No frames extracted for this window")
    
    # Get VLM model service
    vlm_service = await ensure_model_loaded()
    
    # Analyze each frame
    frame_analyses = []
    for frame in window.frames:
        analysis = await vlm_service.analyze_frame(
            frame.image,
            analysis_type=request.analysis_type
        )
        
        frame_analysis = {
            "frame_idx": frame.frame_idx,
            "timestamp": frame.timestamp,
            **analysis
        }
        frame_analyses.append(frame_analysis)
        
        # Save frame analysis to database
        create_frame_analysis(
            db=db,
            session_id=session["session_id"],
            frame_idx=frame.frame_idx,
            timestamp=frame.timestamp,
            tool_localization=analysis.get("tools", ""),
            surgical_action=analysis.get("action", ""),
            surgical_phase=analysis.get("phase", "")
        )
    
    return {
        "window_id": window.window_id,
        "start_time": window.start_time,
        "end_time": window.end_time,
        "frame_count": window.frame_count,
        "frame_analyses": frame_analyses,
        "analysis_type": request.analysis_type
    }


@router.post("/integrate-analysis")
async def integrate_analysis_results(
    request: IntegrateAnalysisRequest,
    db: Session = Depends(get_db)
):
    """
    Integrate frame analysis results into a coherent summary
    
    This endpoint takes frame-by-frame analysis results and integrates them
    into a single narrative summary using GLM-4.6V-Flash (or GPT as fallback).
    """
    session = get_video_session(db, request.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Create video processor
    processor = VideoProcessor(
        video_path=session["video_path"],
        window_duration=settings.WINDOW_DURATION,
        sample_interval=settings.SAMPLE_INTERVAL
    )
    
    # Extract window
    window = processor.extract_window(request.start_time)
    
    if window.frame_count == 0:
        raise HTTPException(400, "No frames extracted for this window")
    
    # Get frame analyses from database or analyze on-the-fly
    frame_analyses = []
    # Pass window time range to database query to avoid limit=100 issue
    db_frames = get_frames_by_session(
        db, 
        session["session_id"],
        start_time=window.start_time,
        end_time=window.end_time
    )
    
    # db_frames already filtered by time range in database query
    window_frames = db_frames
    
    if window_frames:
        # Use existing analyses from database
        for db_frame in window_frames:
            frame_analyses.append({
                "frame_idx": db_frame["frame_idx"],
                "timestamp": db_frame["timestamp"],
                "phase": db_frame.get("surgical_phase") or "",
                "action": db_frame.get("surgical_action") or "",
                "tools": db_frame.get("tool_localization") or ""
            })
    else:
        # Analyze frames if not in database
        vlm_service = await ensure_model_loaded()
        for frame in window.frames:
            analysis = await vlm_service.analyze_frame(frame.image, analysis_type="all")
            frame_analyses.append({
                "frame_idx": frame.frame_idx,
                "timestamp": frame.timestamp,
                **analysis
            })
    
    if not frame_analyses:
        raise HTTPException(400, "No analysis results available for this window")
    
    # Integrate using VLM (Gemini or GLM based on config) or GPT
    if request.use_glm:
        try:
            vlm_client = get_vlm_client()
            
            # Check VLM service health
            is_healthy = await vlm_client.check_health()
            if not is_healthy:
                raise HTTPException(503, "VLM服务不可用")
            
            # 提取窗口帧图片用于VLM多模态验证
            window_images = [frame.image for frame in window.frames if frame.image is not None]
            
            # Integrate using VLM (多模态：图片 + R1分析结果)
            result = await vlm_client.integrate_analysis_results(
                frame_analyses=frame_analyses,
                images=window_images  # 传入图片用于多模态验证
            )
            
            if not result["success"]:
                raise HTTPException(500, f"VLM整合失败: {result.get('error', '未知错误')}")
            
            summary_text = result["summary"]
            provider = get_summarization_provider()
            model_used = f"{provider.upper()} (多模态)"
            
        except HTTPException:
            raise
        except Exception as e:
            # Fallback to GPT if VLM fails
            logger.warning(f"VLM integration failed, falling back to GPT: {e}")
            request.use_glm = False
    
    if not request.use_glm:
        # Use GPT as fallback
        summarizer = get_gpt_summarizer()
        context = build_frame_context(window, frame_analyses)
        
        result = await summarizer.summarize_window(
            images=window.get_images(),
            context=context,
            system_prompt=ANALYSIS_SYSTEM_PROMPT
        )
        
        if not result["success"]:
            raise HTTPException(500, f"Summarization failed: {result.get('error', 'Unknown error')}")
        
        summary_text = result["summary"]
        model_used = "GPT"
    
    # Save summary to database
    summary = create_window_summary(
        db=db,
        session_id=session["session_id"],
        window_id=window.window_id,
        start_time=window.start_time,
        end_time=window.end_time,
        summary_text=summary_text,
        tools_detected=[f.get("tools", "") for f in frame_analyses],
        key_actions=[f.get("action", "") for f in frame_analyses]
    )

    # Generate embedding for semantic search
    _queue_embedding(session["session_id"], window.window_id, summary_text,
                     window.start_time, window.end_time)

    return {
        "window_id": window.window_id,
        "start_time": window.start_time,
        "end_time": window.end_time,
        "frame_count": window.frame_count,
        "frame_analyses": frame_analyses,
        "summary": summary_text,
        "summary_id": summary.id,
        "model": model_used
    }


@router.get("/surgr1/status")
async def surgr1_status():
    """Check SurgR1 service status"""
    now = time.time()
    if not _legacy_surgr1_enabled():
        return {
            "available": True,
            "api_url": None,
            "cached": False,
            "mode": "local_experts",
            "message": "本地 phase/triplet/YOLO/VLM 分析链路已启用",
        }
    try:
        surgr1_client = get_surgr1_client()

        # Use a dedicated short-timeout client for status checks. The normal
        # SurgR1 client has a long inference timeout; reusing it for UI health
        # makes the frontend show false "unavailable" states when the model is
        # busy but still alive.
        async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
            response = await client.get(f"{surgr1_client.api_url}/health")
        is_healthy = response.status_code == 200
        if is_healthy:
            surgr1_status_cache.update({
                "available": True,
                "last_success": now,
                "last_checked": now,
            })
        return {
            "available": is_healthy,
            "api_url": surgr1_client.api_url,
            "cached": False,
        }
    except Exception as e:
        surgr1_client = get_surgr1_client()
        last_success = surgr1_status_cache.get("last_success", 0.0)
        if last_success and now - last_success < 300:
            surgr1_status_cache["last_checked"] = now
            return {
                "available": True,
                "api_url": surgr1_client.api_url,
                "cached": True,
                "warning": f"health check delayed; using last success {int(now - last_success)}s ago",
            }
        try:
            parsed = urlparse(surgr1_client.api_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=1.0,
            )
            writer.close()
            await writer.wait_closed()
            surgr1_status_cache.update({
                "available": True,
                "last_success": now,
                "last_checked": now,
            })
            return {
                "available": True,
                "api_url": surgr1_client.api_url,
                "cached": True,
                "warning": "health endpoint delayed; API port is reachable",
            }
        except Exception:
            pass
        return {
            "available": False,
            "api_url": surgr1_client.api_url,
            "error": str(e),
            "cached": False,
        }


@router.get("/glm/status")
async def glm_status():
    """Check VLM service status (supports both GLM and Gemini providers)
    
    根据 config.json 中的 window_analysis.provider 配置检查当前活跃的 VLM 服务。
    - 如果 provider 是 "gemini"，检查 Gemini 服务状态
    - 如果 provider 是其他值，检查 GLM 服务状态
    """
    try:
        config = load_config()
        provider = config.get("window_analysis", {}).get("provider", "glm")
        
        if provider == "gemini":
            # 使用 Gemini 作为 VLM provider
            gemini_client = get_gemini_client()
            if gemini_client and gemini_client.client:
                is_healthy = await gemini_client.check_health()
                return {
                    "available": is_healthy,
                    "api_url": "gemini-api",
                    "model_name": gemini_client.model_name,
                    "provider": "gemini"
                }
            else:
                return {
                    "available": False,
                    "error": "Gemini client not initialized",
                    "provider": "gemini"
                }
        else:
            # 使用 GLM 作为 VLM provider
            glm_client = get_glm_client()
            is_healthy = await glm_client.check_health()
            return {
                "available": is_healthy,
                "api_url": glm_client.api_url,
                "model_name": glm_client.model_name,
                "provider": "glm"
            }
    except Exception as e:
        return {
            "available": False,
            "error": str(e)
        }


@router.get("/vlm/status")
async def vlm_status():
    """Check VLM service status (current provider: Gemini or GLM)
    
    根据 config.json 中的 summarization_provider 配置检查当前活跃的 VLM 服务。
    """
    return await check_vlm_health()


@router.get("/sam3/status")
async def sam3_status():
    """Check SAM3 service status"""
    try:
        sam3_client = get_sam3_client()
        is_healthy = await sam3_client.check_health()
        return {
            "available": is_healthy,
            "api_url": sam3_client.api_url
        }
    except Exception as e:
        return {
            "available": False,
            "error": str(e)
        }


@router.get("/sam3/segmented-frame/{session_id}")
async def get_sam3_segmented_frame(
    session_id: str,
    timestamp: float = Query(..., ge=0, description="Frame timestamp"),
    alpha: float = Query(0.4, ge=0.0, le=1.0, description="Mask transparency"),
    db: Session = Depends(get_db)
):
    """
    Get a single frame with SAM3 segmentation overlay.
    
    Uses SurgR1 tool_localization to get bounding boxes,
    then SAM3 to generate segmentation masks.
    
    Returns base64 encoded image with segmentation overlay.
    """
    try:
        session = get_video_session(db, session_id)
        if not session:
            return {
                "success": False,
                "message": "Session not found",
                "timestamp": timestamp,
                "has_segmentation": False
            }
        
        # Check if SAM3 is available
        try:
            sam3_client = await ensure_sam3_available()
            is_healthy = await sam3_client.check_health()
            if not is_healthy:
                return {
                    "success": False,
                    "message": "SAM3 service not available",
                    "timestamp": timestamp,
                    "has_segmentation": False
                }
        except Exception as e:
            logger.warning(f"SAM3 service check failed: {e}")
            return {
                "success": False,
                "message": f"SAM3 service error: {e}",
                "timestamp": timestamp,
                "has_segmentation": False
            }
        
        # Create video processor and extract frame
        processor = VideoProcessor(
            video_path=session["video_path"],
            window_duration=settings.WINDOW_DURATION,
            sample_interval=settings.SAMPLE_INTERVAL
        )
        
        frame = processor.extract_frame(timestamp)
        if frame is None:
            return {
                "success": False,
                "timestamp": timestamp,
                "message": f"Could not extract frame at timestamp {timestamp}",
                "has_segmentation": False
            }
        
        # Step 1: Get SurgR1 tool_localization result
        # First try to get from database (frames is a list of dicts)
        # Use time range to avoid limit=100 issue
        frames = get_frames_by_session(
            db, 
            session["session_id"],
            start_time=max(0, timestamp - 2.0),
            end_time=timestamp + 2.0
        )
        nearest_frame = None
        if frames:
            nearest_frame = min(frames, key=lambda f: abs(f["timestamp"] - timestamp))
            if abs(nearest_frame["timestamp"] - timestamp) > 1.0:
                nearest_frame = None
        
        tool_localization = ""
        if nearest_frame and nearest_frame.get("tool_localization"):
            tool_localization = nearest_frame["tool_localization"]
        else:
            # Analyze with SurgR1 on-the-fly
            try:
                surgr1_client = await ensure_surgr1_available()
                result = await surgr1_client.analyze_frame(
                    image=frame.image,
                    analysis_type="tools",
                    session_id=session_id,
                    frame_idx=frame.frame_idx,
                    timestamp=frame.timestamp,
                    save_to_mysql=False
                )
                tool_localization = result.get("tools", "")
            except Exception as e:
                logger.warning(f"SurgR1 analysis failed: {e}")
                # Return original frame if SurgR1 fails
                return {
                    "success": False,
                    "timestamp": timestamp,
                    "frame_idx": frame.frame_idx,
                    "message": f"SurgR1 analysis failed: {e}",
                    "image_base64": frame.to_base64(),
                    "has_segmentation": False
                }
        
        if not tool_localization:
            # No tools detected, return original frame
            return {
                "success": True,
                "timestamp": timestamp,
                "frame_idx": frame.frame_idx,
                "message": "No tools detected in frame",
                "image_base64": frame.to_base64(),
                "has_segmentation": False
            }
        
        # Step 2: Parse bboxes and call SAM3
        try:
            result = await sam3_client.segment_from_surgr1(
                image=frame.image,
                surgr1_bbox_output=tool_localization,
                alpha=alpha,
                return_base64=True
            )
            
            if result.get("success") and result.get("image_base64"):
                return {
                    "success": True,
                    "timestamp": timestamp,
                    "frame_idx": frame.frame_idx,
                    "image_base64": result["image_base64"],
                    "has_segmentation": True,
                    "num_objects": result.get("num_objects", 0),
                    "parsed_bboxes": result.get("parsed_bboxes", [])
                }
            else:
                # SAM3 failed, return original frame
                return {
                    "success": False,
                    "timestamp": timestamp,
                    "frame_idx": frame.frame_idx,
                    "message": result.get("error", "SAM3 segmentation failed"),
                    "image_base64": frame.to_base64(),
                    "has_segmentation": False
                }
                
        except Exception as e:
            logger.error(f"SAM3 segmentation failed: {e}")
            try:
                return {
                    "success": False,
                    "timestamp": timestamp,
                    "frame_idx": frame.frame_idx,
                    "message": f"SAM3 error: {e}",
                    "image_base64": frame.to_base64(),
                    "has_segmentation": False
                }
            except:
                return {
                    "success": False,
                    "timestamp": timestamp,
                    "message": f"SAM3 error: {e}",
                    "has_segmentation": False
                }
                
    except Exception as outer_e:
        # Catch any other unhandled exceptions
        logger.error(f"Unexpected error in get_sam3_segmented_frame: {outer_e}")
        return {
            "success": False,
            "timestamp": timestamp,
            "message": f"Server error: {outer_e}",
            "has_segmentation": False
        }


@router.get("/sam3/stream/{session_id}")
async def stream_sam3_segmented_video(
    session_id: str,
    alpha: float = Query(0.4, ge=0.0, le=1.0, description="Mask transparency"),
    fps: float = Query(5.0, ge=1.0, le=30.0, description="Stream FPS"),
    db: Session = Depends(get_db)
):
    """
    Stream video with SAM3 segmentation overlay as MJPEG.
    
    This endpoint provides a continuous MJPEG stream where each frame
    has been processed with SurgR1 (bbox) + SAM3 (segmentation).
    
    Note: This is computationally intensive. Consider caching results.
    """
    import time
    import tempfile
    from pathlib import Path
    
    session = get_video_session(db, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    
    # Check services availability
    try:
        sam3_client = await ensure_sam3_available()
        surgr1_client = await ensure_surgr1_available()
    except Exception as e:
        raise HTTPException(503, f"Required services not available: {e}")
    
    # Open video
    import cv2
    loop = asyncio.get_running_loop()
    # [perf] VideoCapture 构造 + FPS 探测同步阻塞，放 executor
    cap = await loop.run_in_executor(None, cv2.VideoCapture, session["video_path"])
    if not cap.isOpened():
        raise HTTPException(400, "Cannot open video file")
    
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_interval = 1.0 / fps  # Target interval between frames
    
    async def generate_frames():
        """Generator that yields MJPEG frames"""
        frame_idx = 0
        last_frame_time = 0
        
        def _bgr_to_pil(bgr):
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            return Image.fromarray(rgb)
        
        try:
            while True:
                # [perf] cap.read / cvtColor 同步阻塞，放 executor
                ret, bgr_frame = await loop.run_in_executor(None, cap.read)
                if not ret:
                    break
                
                current_time = frame_idx / video_fps
                
                # Rate limiting: skip frames to match target FPS
                if current_time - last_frame_time < frame_interval:
                    frame_idx += 1
                    continue
                
                last_frame_time = current_time
                
                pil_image = await loop.run_in_executor(None, _bgr_to_pil, bgr_frame)
                
                # Get SurgR1 analysis
                try:
                    surgr1_result = await surgr1_client.analyze_frame(
                        image=pil_image,
                        analysis_type="tools",
                        save_to_mysql=False
                    )
                    tool_localization = surgr1_result.get("tools", "")
                except Exception as e:
                    logger.warning(f"SurgR1 failed for frame {frame_idx}: {e}")
                    tool_localization = ""
                
                output_image = pil_image
                
                # Apply SAM3 segmentation if tools detected
                if tool_localization:
                    try:
                        sam3_result = await sam3_client.segment_from_surgr1(
                            image=pil_image,
                            surgr1_bbox_output=tool_localization,
                            alpha=alpha,
                            return_base64=True
                        )
                        
                        if sam3_result.get("success") and sam3_result.get("image_base64"):
                            # Decode base64 to image
                            import base64
                            from io import BytesIO
                            img_data = base64.b64decode(sam3_result["image_base64"])
                            output_image = Image.open(BytesIO(img_data))
                    except Exception as e:
                        logger.warning(f"SAM3 failed for frame {frame_idx}: {e}")
                
                # Convert to JPEG bytes
                buffer = BytesIO()
                output_image.save(buffer, format="JPEG", quality=80)
                jpeg_bytes = buffer.getvalue()
                
                # Yield as MJPEG frame
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg_bytes)).encode() + b"\r\n"
                    b"\r\n" + jpeg_bytes + b"\r\n"
                )
                
                frame_idx += 1
                
                # Small delay to prevent CPU overload
                await asyncio.sleep(0.01)
                
        finally:
            try:
                await loop.run_in_executor(None, cap.release)
            except Exception:
                pass
    
    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


# ==============================================================================
# Frame and Summary Retrieval APIs (for seek/drag operations)
# ==============================================================================

@router.get("/frame-at-timestamp/{session_id}")
async def get_frame_at_timestamp(
    session_id: str,
    timestamp: float = Query(..., description="Target timestamp in seconds"),
    tolerance: float = Query(1.0, description="Time tolerance for finding frame"),
    db: Session = Depends(get_db)
):
    """
    Get the saved frame closest to the specified timestamp.
    
    Used when seeking/dragging in the video player to show the analyzed frame.
    Returns frame image (base64) and analysis results.
    """
    mysql_service = get_mysql_service()
    
    # Get video session info
    video_session = mysql_service.get_video_session(session_id)
    if not video_session:
        raise HTTPException(404, "Session not found")
    
    storage_path = video_session.get("storage_path")
    
    # PRIORITY 1: Try to find frame from storage folder (saved at 10fps for smooth playback)
    if storage_path:
        frame_storage = get_frame_storage_service()
        nearest = frame_storage.find_nearest_frame(storage_path, timestamp)
        if nearest and nearest["timestamp_diff"] <= tolerance:
            image_base64 = frame_storage.get_frame(storage_path, nearest["path"])
            if image_base64:
                # Also try to get analysis data if available
                frame_data = mysql_service.get_frame_at_timestamp(session_id, timestamp, tolerance)
                analysis = None
                if frame_data:
                    analysis = {
                        "tool_localization": frame_data.get("tool_localization"),
                        "surgical_action": frame_data.get("surgical_action"),
                        "surgical_phase": frame_data.get("surgical_phase")
                    }
                return {
                    "success": True,
                    "has_saved_frame": True,
                    "timestamp": timestamp,
                    "actual_timestamp": timestamp - nearest["timestamp_diff"],
                    "image_base64": image_base64,
                    "analysis": analysis
                }
    
    # PRIORITY 2: Try to get from database (for backward compatibility)
    frame_data = mysql_service.get_frame_at_timestamp(session_id, timestamp, tolerance)
    
    if not frame_data:
        # PRIORITY 3: Fallback to live video stream
        video_path = video_session.get("video_path")
        if video_path and (video_path.startswith("http://") or video_path.startswith("https://")):
            try:
                import cv2
                import base64
                # [perf] cv2 连接 HTTP 源 + 读 3 帧 + JPEG 编码都是同步阻塞，
                # 全部塞到一个闭包里交给 executor，避免阻塞 asyncio loop
                def _grab_live_frame(vp: str):
                    c = cv2.VideoCapture(vp)
                    try:
                        if not c.isOpened():
                            return None
                        frame = None
                        for _ in range(3):  # Read 3 frames, use the last one
                            ret, frame = c.read()
                            if not ret:
                                frame = None
                                break
                        if frame is None:
                            return None
                        ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        if not ok:
                            return None
                        return base64.b64encode(buf).decode('utf-8')
                    finally:
                        c.release()

                loop = asyncio.get_running_loop()
                image_base64 = await loop.run_in_executor(None, _grab_live_frame, video_path)
                if image_base64:
                    return {
                        "success": True,
                        "has_saved_frame": False,
                        "is_live_frame": True,
                        "timestamp": timestamp,
                        "actual_timestamp": timestamp,
                        "image_base64": image_base64,
                        "analysis": None,
                        "message": "Live frame from stream (no saved frame available)"
                    }
            except Exception as e:
                logger.warning(f"Failed to get live frame from stream: {e}")
        
        return {
            "success": False,
            "has_saved_frame": False,
            "timestamp": timestamp,
            "message": "No saved frame found for this timestamp"
        }
    
    # Get image from storage
    image_base64 = None
    if storage_path and frame_data.get("image_path"):
        frame_storage = get_frame_storage_service()
        image_base64 = frame_storage.get_frame(storage_path, frame_data["image_path"])
    
    return {
        "success": True,
        "has_saved_frame": bool(image_base64),
        "timestamp": timestamp,
        "actual_timestamp": frame_data.get("timestamp"),
        "frame_idx": frame_data.get("frame_idx"),
        "image_base64": image_base64,
        "analysis": {
            "tool_localization": frame_data.get("tool_localization"),
            "surgical_action": frame_data.get("surgical_action"),
            "surgical_phase": frame_data.get("surgical_phase")
        }
    }


@router.get("/window-summary-at-timestamp/{session_id}")
async def get_window_summary_at_timestamp(
    session_id: str,
    timestamp: float = Query(..., description="Target timestamp in seconds"),
    db: Session = Depends(get_db)
):
    """
    Get the GLM window summary that covers the specified timestamp.
    
    Used when seeking/dragging to show the corresponding analysis summary.
    """
    mysql_service = get_mysql_service()
    
    # Get window summary
    summary = mysql_service.get_window_summary_at_timestamp(session_id, timestamp)
    
    if not summary:
        return {
            "success": False,
            "timestamp": timestamp,
            "window_id": None,
            "summary": None,
            "message": "No window summary found for this timestamp"
        }
    
    return {
        "success": True,
        "timestamp": timestamp,
        "window_id": summary.get("window_id"),
        "window_start": summary.get("window_start"),
        "window_end": summary.get("window_end"),
        "summary": summary.get("glm_summary"),
        "surgical_phase": summary.get("surgical_phase")
    }


@router.get("/all-window-summaries/{session_id}")
async def get_all_window_summaries(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Get all GLM window summaries for a session.
    
    Used to populate the summary list in the UI.
    """
    mysql_service = get_mysql_service()
    
    summaries = mysql_service.get_all_window_summaries(session_id)
    
    return {
        "success": True,
        "session_id": session_id,
        "count": len(summaries),
        "summaries": summaries
    }


@router.get("/frames-in-range/{session_id}")
async def get_frames_in_range(
    session_id: str,
    start: float = Query(..., description="Start timestamp in seconds"),
    end: float = Query(..., description="End timestamp in seconds"),
    db: Session = Depends(get_db)
):
    """
    Get list of saved frames within a time range.
    
    Used for loop playback feature to fetch available frames in a window.
    Returns frame metadata (timestamp, frame_idx) without full image data.
    """
    mysql_service = get_mysql_service()
    
    # Get video session info
    video_session = mysql_service.get_video_session(session_id)
    if not video_session:
        raise HTTPException(404, "Session not found")
    
    storage_path = video_session.get("storage_path")
    
    # Get frames from storage folder (these are saved at higher FPS for smooth playback)
    frames_in_range = []
    if storage_path:
        frame_storage = get_frame_storage_service()
        storage_frames = frame_storage.list_frames_in_range(storage_path, start, end, "frames")
        frames_in_range = [
            {
                "frame_idx": f.get("frame_idx", -1),
                "timestamp": f.get("timestamp"),
                "has_image": True,
                "path": f.get("path")
            }
            for f in storage_frames
        ]
    
    # If no frames in storage, fall back to database
    if not frames_in_range:
        frames = mysql_service.get_analyses(session_id, limit=10000)
        frames_in_range = [
            {
                "frame_idx": f.get("frame_idx"),
                "timestamp": f.get("timestamp"),
                "has_image": f.get("image_saved") == 1,
            }
            for f in frames
            if f.get("analysis_type") == "frame" 
            and f.get("timestamp") is not None
            and start <= f.get("timestamp") <= end
        ]
    
    return {
        "success": True,
        "session_id": session_id,
        "start": start,
        "end": end,
        "count": len(frames_in_range),
        "frames": sorted(frames_in_range, key=lambda x: x.get("timestamp", 0))
    }


@router.get("/frames-batch/{session_id}")
async def get_frames_batch(
    session_id: str,
    start: float = Query(..., description="Start timestamp in seconds"),
    end: float = Query(..., description="End timestamp in seconds"),
    max_frames: int = Query(300, description="Maximum number of frames to return (up to 300 for 15fps * 20s)"),
    use_url: bool = Query(True, description="Return URLs instead of base64 (faster)"),
    use_preview: bool = Query(True, description="Use low-quality preview frames for faster loading (default true)"),
    db: Session = Depends(get_db)
):
    """
    Get multiple frames for loop playback.
    
    By default returns URLs for direct image access (faster).
    Set use_url=false to get base64 data instead.
    
    Preview mode (use_preview=true, default):
    - Returns low-quality preview frames (~40KB each vs ~600KB)
    - Much faster loading for loop playback
    - Falls back to full frames if preview not available
    
    Args:
        session_id: Video session ID
        start: Start timestamp in seconds
        end: End timestamp in seconds
        max_frames: Maximum number of frames to return (default 200)
        use_url: If true, return URLs; if false, return base64 data
        use_preview: If true, use low-quality preview frames for faster loading
    
    Returns:
        JSON with frames array containing timestamp and url/image_base64
    """
    mysql_service = get_mysql_service()
    
    # Get video session info
    video_session = mysql_service.get_video_session(session_id)
    if not video_session:
        raise HTTPException(404, "Session not found")
    
    storage_path = video_session.get("storage_path")
    if not storage_path:
        return {
            "success": False,
            "message": "No storage path for session",
            "frames": []
        }
    
    frame_storage = get_frame_storage_service()
    
    # Determine which subfolder to use
    # Try preview first if requested, fall back to frames
    subfolder = "frames"
    if use_preview:
        # Prefer preview frames only if coverage is good enough.
        # In some deployments, preview generation may be partial (e.g., only first few seconds),
        # which would cause loop playback to "move" briefly then freeze on the last preview frame.
        preview_frames = frame_storage.list_frames_in_range(storage_path, start, end, "preview")
        full_frames = frame_storage.list_frames_in_range(storage_path, start, end, "frames")

        # Heuristic: require preview coverage to be at least 80% of full frames in range
        # (and at least a small minimum) before using preview.
        if preview_frames and full_frames:
            coverage = len(preview_frames) / max(1, len(full_frames))
            if coverage >= 0.8 and len(preview_frames) >= 10:
                subfolder = "preview"
                storage_frames = preview_frames
            else:
                subfolder = "frames"
                storage_frames = full_frames
                logger.info(
                    f"[FramesBatch] Preview coverage too low ({len(preview_frames)}/{len(full_frames)}={coverage:.2f}); "
                    f"falling back to full frames for session {session_id} ({start:.1f}s-{end:.1f}s)"
                )
        elif preview_frames and not full_frames:
            # No full frames found (unexpected), use preview.
            subfolder = "preview"
            storage_frames = preview_frames
        else:
            # Fall back to full frames
            storage_frames = full_frames
    else:
        storage_frames = frame_storage.list_frames_in_range(storage_path, start, end, "frames")
    
    if not storage_frames:
        return {
            "success": False,
            "message": "No frames found in range",
            "frames": [],
            # 【解耦增强】返回覆盖率信息
            "coverage": {
                "requested_start": start,
                "requested_end": end,
                "requested_duration": end - start,
                "actual_start": None,
                "actual_end": None,
                "actual_duration": 0,
                "frame_count": 0,
                "expected_frames": int((end - start) * 25),  # 25fps
                "coverage_ratio": 0.0,
                "is_complete": False
            }
        }
    
    # Sort by timestamp and limit
    storage_frames = sorted(storage_frames, key=lambda x: x.get("timestamp", 0))[:max_frames]
    
    # 【解耦增强】计算帧覆盖率信息
    timestamps = [f.get("timestamp", 0) for f in storage_frames]
    actual_start = min(timestamps) if timestamps else start
    actual_end = max(timestamps) if timestamps else end
    actual_duration = actual_end - actual_start
    requested_duration = end - start
    
    # 计算期望帧数（基于配置的25fps）和覆盖率
    expected_frames = int(requested_duration * 25)  # 25fps from config
    coverage_ratio = len(storage_frames) / max(1, expected_frames)
    
    # 判断是否完整覆盖（覆盖率>=80%且时间范围接近）
    time_coverage = actual_duration / max(0.1, requested_duration)
    is_complete = coverage_ratio >= 0.8 and time_coverage >= 0.9
    
    # Extract folder name from storage path for URL construction
    # storage_path is like: /data2/.../sessions/20260107_123456_abc123_stream
    from pathlib import Path
    folder_name = Path(storage_path).name
    
    frames_list = []
    for frame_info in storage_frames:
        # Get filename from frame_info
        filename = frame_info.get("filename", "")
        if not filename:
            continue
            
        frame_data = {
            "timestamp": frame_info.get("timestamp"),
            "frame_idx": frame_info.get("frame_idx", -1),
        }
        
        if use_url:
            # Return URL for direct static file access
            frame_data["url"] = f"/sessions/{folder_name}/{subfolder}/{filename}"
        else:
            # Return base64 data
            frame_path = f"{subfolder}/{filename}"
            image_base64 = frame_storage.get_frame(storage_path, frame_path)
            if image_base64:
                frame_data["image_base64"] = image_base64
            else:
                continue  # Skip if can't load
        
        frames_list.append(frame_data)
    
    logger.info(f"[FramesBatch] Returning {len(frames_list)} {subfolder} frames for session {session_id} ({start:.1f}s - {end:.1f}s), coverage={coverage_ratio:.2%}, use_url={use_url}, use_preview={use_preview}")
    
    return {
        "success": True,
        "session_id": session_id,
        "start": start,
        "end": end,
        "count": len(frames_list),
        "use_url": use_url,
        "use_preview": use_preview,
        "subfolder": subfolder,
        "frames": frames_list,
        # 【解耦增强】返回帧覆盖率和实际时间范围信息
        "coverage": {
            "requested_start": start,
            "requested_end": end,
            "requested_duration": requested_duration,
            "actual_start": actual_start,
            "actual_end": actual_end,
            "actual_duration": actual_duration,
            "frame_count": len(frames_list),
            "expected_frames": expected_frames,
            "coverage_ratio": round(coverage_ratio, 3),
            "is_complete": is_complete
        }
    }


@router.get("/session-frames/{session_id}")
async def list_session_frames(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    List all saved frames for a session.
    
    Returns metadata about saved frames for timeline display.
    """
    mysql_service = get_mysql_service()
    
    # Get video session info
    video_session = mysql_service.get_video_session(session_id)
    if not video_session:
        raise HTTPException(404, "Session not found")
    
    storage_path = video_session.get("storage_path")
    
    # Get all frame analyses with saved images
    frames = mysql_service.get_analyses(session_id, limit=10000)
    saved_frames = [
        {
            "frame_idx": f.get("frame_idx"),
            "timestamp": f.get("timestamp"),
            "has_image": f.get("image_saved") == 1,
            "surgical_phase": f.get("surgical_phase")
        }
        for f in frames
        if f.get("analysis_type") == "frame"
    ]
    
    return {
        "success": True,
        "session_id": session_id,
        "storage_path": storage_path,
        "count": len(saved_frames),
        "frames": sorted(saved_frames, key=lambda x: x.get("timestamp", 0))
    }


# ============================================================================
# Video Export Endpoints
# ============================================================================

class ExportClipsRequest(BaseModel):
    """Request body for export clips endpoint."""
    window_ids: List[int]


@router.post("/export-clips/{session_id}")
async def export_clips(
    session_id: str,
    request: ExportClipsRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Start batch export of video clips with analysis text.
    
    Creates video clips for selected windows, with the analysis text
    embedded on the right side of each clip.
    
    Args:
        session_id: Video session ID
        request: Request body containing window_ids list
    
    Returns:
        task_id for tracking progress via /export-status/{task_id}
    """
    mysql_service = get_mysql_service()
    export_service = get_video_export_service()
    
    # Get video session info
    video_session = mysql_service.get_video_session(session_id)
    if not video_session:
        raise HTTPException(404, f"Session not found: {session_id}")
    
    # Get all window summaries
    all_summaries = mysql_service.get_all_window_summaries(session_id)
    if not all_summaries:
        raise HTTPException(400, "No analysis results found for this session")
    
    # Filter to requested window IDs
    window_ids_set = set(request.window_ids)
    selected_summaries = [
        s for s in all_summaries
        if s.get("window_id") in window_ids_set
    ]
    
    if not selected_summaries:
        raise HTTPException(400, "No matching windows found for the requested IDs")
    
    # Sort by window_id
    selected_summaries.sort(key=lambda x: x.get("window_id", 0))
    
    # Create export task
    task_id = export_service.create_export_task(session_id, request.window_ids)
    
    logger.info(f"[Export] Starting export task {task_id} for session {session_id}, "
               f"{len(selected_summaries)} windows")
    
    # Run export in background using asyncio.create_task
    async def run_export():
        try:
            await export_service.export_clips(
                task_id=task_id,
                session_id=session_id,
                window_summaries=selected_summaries,
                video_session=video_session
            )
        except Exception as e:
            logger.error(f"[Export] Task {task_id} failed: {e}")
            import traceback
            traceback.print_exc()
            if task_id in export_tasks:
                export_tasks[task_id]["status"] = "failed"
                export_tasks[task_id]["error"] = str(e)
    
    # Schedule background task - use asyncio.create_task directly
    asyncio.create_task(run_export())
    
    return {
        "success": True,
        "task_id": task_id,
        "session_id": session_id,
        "window_count": len(selected_summaries),
        "message": f"Export started. Track progress via /api/analysis/export-status/{task_id}"
    }


@router.get("/export-status/{task_id}")
async def get_export_status(task_id: str):
    """
    Get export task status and progress.
    
    Args:
        task_id: Export task ID from /export-clips response
    
    Returns:
        Task status including progress percentage and download links when complete
    """
    export_service = get_video_export_service()
    
    status = export_service.get_task_status(task_id)
    if not status:
        raise HTTPException(404, f"Export task not found: {task_id}")
    
    return status


@router.get("/download-clip/{session_id}/{filename}")
async def download_clip(session_id: str, filename: str):
    """
    Download an exported video clip.
    
    Args:
        session_id: Video session ID
        filename: Name of the exported file
    
    Returns:
        Video file stream for download
    """
    from fastapi.responses import FileResponse
    
    export_service = get_video_export_service()
    
    file_path = export_service.get_export_file_path(session_id, filename)
    if not file_path:
        raise HTTPException(404, f"Export file not found: {filename}")
    
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="video/mp4"
    )


@router.get("/exports/{session_id}")
async def list_exports(session_id: str):
    """
    List all exported clips for a session.
    
    Args:
        session_id: Video session ID
    
    Returns:
        List of exported files with download URLs
    """
    export_service = get_video_export_service()
    
    exports = export_service.list_exports(session_id)
    
    return {
        "success": True,
        "session_id": session_id,
        "count": len(exports),
        "exports": exports
    }


@router.get("/exportable-windows/{session_id}")
async def get_exportable_windows(session_id: str, db: Session = Depends(get_db)):
    """
    Get list of windows that can be exported for a session.
    
    Returns all analyzed windows with their summaries for the export selection UI.
    
    Args:
        session_id: Video session ID
    
    Returns:
        List of windows with window_id, time range, and summary preview
    """
    mysql_service = get_mysql_service()
    
    # Get video session info
    video_session = mysql_service.get_video_session(session_id)
    if not video_session:
        raise HTTPException(404, f"Session not found: {session_id}")
    
    # Get all window summaries
    summaries = mysql_service.get_all_window_summaries(session_id)
    
    # Format for UI
    windows = []
    for s in summaries:
        summary_text = s.get("glm_summary", "")
        windows.append({
            "window_id": s.get("window_id"),
            "start_time": s.get("window_start"),
            "end_time": s.get("window_end"),
            "summary_preview": summary_text[:100] + "..." if len(summary_text) > 100 else summary_text,
            "surgical_phase": s.get("surgical_phase")
        })
    
    return {
        "success": True,
        "session_id": session_id,
        "video_name": video_session.get("video_name"),
        "video_type": video_session.get("video_type"),
        "count": len(windows),
        "windows": windows
    }


# ==============================================================================
# Embedding-based semantic search endpoints
# ==============================================================================

@router.post("/search/semantic")
async def semantic_search(request: SemanticSearchRequest):
    """Semantic search across window summaries using Gemini embeddings."""
    embedding_service = get_embedding_service()
    if not embedding_service:
        raise HTTPException(status_code=503, detail="Embedding service not available")
    results = await embedding_service.search_similar(
        session_id=request.session_id,
        query_text=request.query,
        top_k=request.top_k
    )
    return {"results": results, "query": request.query}


@router.get("/search/similar-window/{session_id}/{window_id}")
async def find_similar_windows(session_id: str, window_id: int, top_k: int = 5):
    """Find windows similar to a given window."""
    embedding_service = get_embedding_service()
    if not embedding_service:
        raise HTTPException(status_code=503, detail="Embedding service not available")
    results = await embedding_service.find_similar_windows(session_id, window_id, top_k)
    return {"results": results, "source_window": window_id}


@router.post("/search/text")
async def text_search(request: TextSearchRequest):
    """Exact text search across window summaries."""
    embedding_service = get_embedding_service()
    if not embedding_service:
        raise HTTPException(status_code=503, detail="Embedding service not available")
    results = embedding_service.text_search(request.session_id, request.query)
    return {"results": results, "query": request.query}


@router.get("/search/embedding-stats/{session_id}")
async def embedding_stats(session_id: str):
    """Get embedding statistics for a session."""
    embedding_service = get_embedding_service()
    if not embedding_service:
        raise HTTPException(status_code=503, detail="Embedding service not available")
    return embedding_service.get_session_stats(session_id)
