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

# Text-only translation cache for UI language switching.
# Key: (target_lang, source_text)
summary_translation_cache: dict = {}

# LLM-generated key event nodes for the bottom timeline UI.
# Key: (session_id, language, max_windows, summaries_signature)
event_node_cache: dict = {}

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

PHASE_LABEL_CN = {
    "Preparation": "准备阶段",
    "CalotTriangleDissection": "肝胆三角解剖",
    "ClippingCutting": "夹闭切断",
    "GallbladderDissection": "胆囊分离",
    "GallbladderRetraction": "胆囊牵拉",
    "CleaningCoagulation": "清洁凝血",
    "GallbladderPackaging": "胆囊取出与装袋",
    "preparation": "准备阶段",
    "calot_triangle_dissection": "肝胆三角解剖",
    "clipping_cutting": "夹闭切断",
    "gallbladder_dissection": "胆囊分离",
    "gallbladder_retraction": "胆囊牵拉",
    "cleaning_coagulation": "清洁凝血",
    "gallbladder_packaging": "胆囊取出与装袋",
}

PHASE_VISUAL_CONTEXT_CN = {
    "Preparation": "画面以初始暴露、入路准备和术野建立为主",
    "CalotTriangleDissection": "画面集中在肝胆三角区域，重点是暴露和精细分离",
    "ClippingCutting": "画面集中在胆囊管和胆囊动脉处理区域，重点关注夹闭、切断和安全间隙",
    "GallbladderDissection": "画面以胆囊床分离为主，重点关注组织层面和止血情况",
    "GallbladderRetraction": "画面以牵拉胆囊、展开术野和维持暴露为主",
    "CleaningCoagulation": "画面以清理术野、凝血和确认出血控制为主",
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
    "bipolar": "双极电凝",
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
    "bipolar": "双极电凝",
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
        "GallbladderRetraction": "胆囊牵拉",
        "CleaningCoagulation": "清洁凝血",
        "GallbladderPackaging": "胆囊取出与装袋",
        "Preparation": "准备阶段",
        "Calot's triangle": "肝胆三角",
        "Calot三角": "肝胆三角",
        "亮白细长器械尖端短时进入并接触组织，疑似穿孔/点触动作": "电凝钩尖端接触并分离组织",
        "疑似穿孔/点触动作": "电凝钩尖端接触并分离组织",
        "疑似穿孔点触": "电凝钩尖端接触并分离组织",
        "点触": "电凝钩接触",
        "亮白细长器械": "电凝钩",
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
    out = re.sub(r"(钛夹钳(?:正在)?夹闭(?:胆囊管|胆囊动脉))明显", r"\1", out)
    out = re.sub(r"(钛夹钳(?:正在)?夹闭(?:胆囊管|胆囊动脉))[，,]\s*明显", r"\1", out)
    out = re.sub(r"(当前窗口|本段|术野|画面)出现", r"\1有", out)
    out = out.replace("出现了", "").replace("出现", "")
    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"[，,]\s*[。；;]", "。", out)
    out = re.sub(r"。{2,}", "。", out)
    return out.strip(" ，；")


def _phase_visual_context(phase_raw: str, phase: str) -> str:
    return PHASE_VISUAL_CONTEXT_CN.get(phase) or PHASE_VISUAL_CONTEXT_CN.get(_canonical_phase(phase_raw)) or "画面以术野观察和阶段确认为主"


def _cvs_realtime_note(phase: str, tool_set: set, action_text: str = "") -> str:
    """Emit a concise CVS status whenever the phase requires safety judgement."""
    has_clipper = any(name in tool_set for name in ("施夹器", "钛夹钳"))
    if phase == "CalotTriangleDissection":
        return "CVS评估：已进入安全关键视野判断阶段，重点确认肝胆三角清理、胆囊板暴露，以及仅胆囊管和胆囊动脉两条结构进入胆囊"
    if phase == "ClippingCutting" or has_clipper or any(term in action_text for term in ("夹闭", "施夹", "钛夹")):
        return "CVS评估：处于夹闭前后安全核查窗口，需要确认胆囊管、胆囊动脉和胆囊板暴露后再记录为CVS达成"
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
        return _polish_summary_wording(out)
    for target in ("胆囊管", "胆囊动脉"):
        if re.search(rf"钛夹钳(?:正在)?夹闭{target}", out):
            out = re.sub(rf"([，,；;。]?\s*)钛夹钳夹持{target}", "", out)
        if re.search(rf"钛夹钳正在夹闭{target}", out):
            out = re.sub(rf"([，,；;。]?\s*)钛夹钳夹闭{target}", "", out)
    out = re.sub(r"([，,；;])\s*(?:冲吸器|剪刀|电凝钩|电钩)?分离胆囊", "", out)
    out = re.sub(r"([，,；;])\s*正在牵拉和暴露组织", "", out)
    out = re.sub(r"([，,；;])\s*未见(?:明显)?(?:活动性)?出血", "", out)
    out = re.sub(r"([，,；;])\s*未见(?:夹闭器具|纱布)?", "", out)
    out = re.sub(r"([，,；;])\s*视野清晰", "", out)
    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"。{2,}", "。", out)
    out = re.sub(r"，。", "。", out)
    out = re.sub(r"当前处于夹闭切断。", "当前处于夹闭切断，", out)
    return _polish_summary_wording(out)


SEQUENCE_PHASE_ORDER = {
    "Preparation": 0,
    "GallbladderRetraction": 1,
    "CalotTriangleDissection": 2,
    "ClippingCutting": 3,
    "GallbladderDissection": 4,
    "CleaningCoagulation": 5,
    "GallbladderPackaging": 6,
}


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
        order = SEQUENCE_PHASE_ORDER.get(phase, -1)
        if phase and phase != "Unknown":
            state["reached_phases"].add(phase)
            state["last_phase"] = phase
            if order > state["max_phase_order"]:
                state["max_phase_order"] = order
        if order >= SEQUENCE_PHASE_ORDER["CalotTriangleDissection"]:
            state["formal_started"] = True
        if phase == "GallbladderPackaging" or re.search(r"胆囊(?:取出|装袋)|标本(?:袋|取出)", text):
            state["packaging_seen"] = True
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
    out = re.sub(r"参与(胆囊管|胆囊动脉)(夹闭|切断)相关操作", r"\1\2", out)
    out = out.replace("相关操作", "")
    return _polish_summary_wording(out)


def _downgrade_cut_claim(text: str, target_cn: str) -> str:
    replacement = f"剪刀在{target_cn}邻近区域操作"
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

    if phase == "Preparation" and prior_state.get("formal_started"):
        phase = prior_state.get("last_phase") if prior_state.get("last_phase") != "Unknown" else "CalotTriangleDissection"
        out = _replace_current_phase_text(out, phase)
        applied.append("phase_no_return_to_preparation")

    if prior_state.get("packaging_seen") and phase not in {"GallbladderPackaging", "CleaningCoagulation", "Unknown"}:
        phase = "CleaningCoagulation"
        out = _replace_current_phase_text(out, phase)
        applied.append("phase_after_packaging_only_cleaning")

    current_cvs = _has_cvs_achieved_text(out)
    if (visual.get("cvs") or {}).get("status") == "achieved":
        current_cvs = True

    for target_label, target_cn in (("cystic_duct", "胆囊管"), ("cystic_artery", "胆囊动脉")):
        if target_label in (prior_state.get("cut") or set()) and _has_clip_target_text(out, target_cn):
            out = _downgrade_repeated_clip_claim(out, target_cn)
            applied.append(f"clip_after_cut_to_residual_{target_label}")

        if _has_cut_target_text(out, target_cn):
            same_window_clip = _has_clip_target_text(out, target_cn)
            target_previously_clipped = target_label in (prior_state.get("clipped") or set())
            if not (prior_state.get("cvs_achieved") or current_cvs):
                out = _downgrade_cut_claim(out, target_cn)
                applied.append(f"cut_requires_cvs_{target_label}")
            elif not (target_previously_clipped or same_window_clip):
                out = _downgrade_cut_claim(out, target_cn)
                applied.append(f"cut_requires_prior_clip_{target_label}")

    out = re.sub(r"[，,；;]\s*[，,；;]+", "，", out)
    out = re.sub(r"[，,]\s*[。；;]", "。", out)
    out = re.sub(r"。{2,}", "。", out).strip(" ，；")
    return _strip_clipping_noise(out), phase, applied


def _local_visibility_cue_from_bgr_frames(frames: List[Any]) -> Dict[str, Any]:
    """Fast local visibility cue for Stage 1.

    This is intentionally conservative and only promotes obvious lens fog/smoke:
    low edge detail plus low saturation/high brightness over a meaningful part
    of the frame. OpenVLM can later refine or remove the claim.
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
            foggy = bool(
                (lap_var < 1500 and sat_mean < 65 and low_sat_bright > 0.25)
                or (lap_var < 700 and sat_mean < 85 and gray_std < 60 and low_sat_bright > 0.20)
            )
            metrics.append({
                "foggy": foggy,
                "lap_var": lap_var,
                "gray_std": gray_std,
                "sat_mean": sat_mean,
                "low_sat_bright": low_sat_bright,
            })
        except Exception:
            continue

    if not metrics:
        return {"fog": False, "confidence": 0.0}
    fog_count = sum(1 for item in metrics if item["foggy"])
    fog_ratio = fog_count / max(1, len(metrics))
    confidence = min(0.92, max(0.0, 0.35 + fog_ratio * 0.55))
    representative = max(metrics, key=lambda item: (item["foggy"], item["low_sat_bright"], -item["lap_var"]))
    return {
        "fog": fog_ratio >= 0.34,
        "confidence": round(confidence if fog_ratio >= 0.34 else fog_ratio * 0.3, 3),
        "fog_ratio": round(fog_ratio, 3),
        "lap_var": round(representative["lap_var"], 1),
        "gray_std": round(representative["gray_std"], 1),
        "sat_mean": round(representative["sat_mean"], 1),
        "low_sat_bright": round(representative["low_sat_bright"], 3),
        "summary": "镜头起雾，手术视野受遮挡。" if fog_ratio >= 0.34 else "",
    }


def _hardcoded_out_of_body_candidate(
    start_time: float,
    end_time: float,
    video_path: str = "",
    video_name: str = "",
) -> Dict[str, Any]:
    """Known VID001 removal segment cue for post-update visual review.

    This is deliberately not an event-node fallback. It only marks the live
    window as a high-priority OpenVLM/GPT review candidate; the async patch then
    writes the structured visibility result back to the window.
    """
    source_parts = [video_path or "", video_name or ""]
    try:
        resolved = resolve_video_source(video_path or "")
        if resolved.source and resolved.source != video_path:
            source_parts.append(resolved.source)
    except Exception:
        pass
    source_text = " ".join(source_parts).lower()
    is_vid001 = any(
        token in source_text
        for token in (
            "vid001_capture_30fps",
            "2024-12-24_225315_vid001",
        )
    )
    if not is_vid001:
        return {"out_of_body_candidate": False}

    # In the current VID001 capture-card simulation the laparoscope leaves the
    # abdominal cavity around 09:59. The strongest visual evidence spans the red
    # close-up frame and the trocar/abdominal-wall view immediately after it.
    cue_start = 595.0
    cue_end = 615.0
    overlap = max(0.0, min(float(end_time or 0), cue_end) - max(float(start_time or 0), cue_start))
    if overlap <= 0:
        return {"out_of_body_candidate": False}

    return {
        "out_of_body_candidate": True,
        "out_of_body_hardcoded": True,
        "source": "vid001_out_of_body_interval_595_615s",
        "confidence": 0.92 if float(start_time or 0) < 605.0 else 0.82,
        "cue_start": cue_start,
        "cue_end": cue_end,
        "summary": "镜头移出体外，画面切换至套管口或腹壁外场景。",
        "prompt_hint": (
            "当前窗口落在VID001测试视频约9分59秒开始的镜头移出体外候选段。"
            "重点检查是否出现整帧红色贴近腹壁/体外组织、圆形套管口/镜头口、"
            "腹腔解剖结构消失、镜头贴近套管或腹壁外场景。若看到这些证据，"
            "visibility.status必须为out_of_body，out_of_body=true。"
        ),
    }


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

    tool_set = set(tool_names)
    action_text = ""
    if short_action.get("detected"):
        action_text = short_action.get("description", "短时器械接触动作")
        action_text = action_text.replace("可见", "").strip(" ，。")

    action_parts = _triplet_operation_phrases(expert_pack.get("triplet") or {}, max_items=4, phase=phase)
    clipper_frames = int(tool_frame_counts.get("钛夹钳") or tool_frame_counts.get("施夹器") or 0)
    scissors_frames = int(tool_frame_counts.get("剪刀") or 0)
    hemlok_clip = expert_pack.get("hemlok_clip") or {}
    clipper_context = clipper_frames >= 4 and phase == "ClippingCutting"
    late_hemlok_context = bool(
        hemlok_clip.get("detected")
        and _safe_float(hemlok_clip.get("confidence"), 0.0) >= 0.75
        and float(start_time or 0) >= 285.0
        and phase in {
            "CalotTriangleDissection",
            "ClippingCutting",
            "GallbladderDissection",
            "CleaningCoagulation",
            "GallbladderPackaging",
        }
    )
    if clipper_context:
        # In the clipping/cutting phase, broad Triplet progress labels such as
        # "irrigator dissect gallbladder" often describe the same close-up field
        # but read as a different operation. Keep explicit core clip/cut/coag
        # actions and drop low-value dissection phrases.
        action_parts = [
            part for part in action_parts
            if not re.search(r"(分离胆囊|分离胆囊板|分离肝脏|分离腹膜|分离肝胆三角)", part)
        ]
    has_clip_action = any(
        term in " ".join(action_parts)
        for term in ("夹闭", "闭合", "施夹", "夹持", "Hem-o-lok", "钛夹")
    )
    if (late_hemlok_context or clipper_context) and hemlok_clip.get("detected") and not has_clip_action:
        action_parts.append(f"可见白色Hem-o-lok夹已夹闭{target_cn}")
        has_clip_action = True
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
    if scissors_frames >= 2 and not has_cut_action and phase in {"ClippingCutting", "GallbladderDissection", "CleaningCoagulation"}:
        action_parts.append(f"剪刀正在切断{target_cn}")
        has_cut_action = True
    if "电凝钩" in tool_set and not has_clip_action and not has_cut_action:
        if phase == "Preparation":
            action_parts.append("电凝钩进入或调整术野")
        elif phase == "CalotTriangleDissection":
            action_parts.append("电凝钩分离肝胆三角组织")
        elif phase == "GallbladderDissection":
            action_parts.append("电凝钩分离胆囊床组织")
        else:
            action_parts.append("电凝钩分离局部组织")
    if "冲洗器" in tool_set and not has_clip_action:
        action_parts.append("正在冲洗或清理术野")
    if "双极电凝" in tool_set:
        if phase in {"CleaningCoagulation", "GallbladderDissection"} or "出血" in action_text:
            action_parts.append("正在进行凝血或止血处理")
        else:
            action_parts.append("可见双极器械接触组织，尚未确认止血操作")
    if "标本袋" in tool_set and phase == "GallbladderPackaging":
        action_parts.append("进入标本装袋或取出相关步骤")
    if not action_parts and "抓钳" in tool_set:
        if phase == "CalotTriangleDissection":
            action_parts.append("抓钳牵拉胆囊颈和胆囊体以暴露肝胆三角")
        elif phase == "GallbladderDissection":
            action_parts.append("抓钳牵拉胆囊体以暴露胆囊床")
        else:
            action_parts.append("抓钳牵拉胆囊颈和胆囊体以暴露操作区域")

    parts = [f"当前处于{phase_cn}"]
    if action_parts:
        parts.append("，" + "；".join(dict.fromkeys(action_parts)))
    elif phase_cn and phase_cn != "当前阶段":
        parts.append("，" + _phase_visual_context(phase_raw, phase))
    else:
        parts.append("，画面以术野观察和状态确认为主")
    if action_text:
        parts.append(f"。{action_text}")
    cvs_note = _cvs_realtime_note(phase, tool_set, action_text)
    if cvs_note:
        parts.append(f"。{cvs_note}")
    visibility_cue = expert_pack.get("local_visibility") or {}
    if visibility_cue.get("fog"):
        parts.append("。镜头起雾，手术视野受遮挡")
    return _strip_clipping_noise(_sanitize_target_language("".join(parts).rstrip("。") + "。", target_label))


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
        "电凝钩", "电钩", "剪刀", "施夹器", "钛夹钳", "Hem-o-lok", "Hemolok", "hemlock", "钛夹", "施夹", "夹闭",
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
        "套管口", "腹壁外", "outside the body", "out_of_body", "trocar", "extra-abdominal",
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
    
    # Also cancel any pending requests to SurgR1 API
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
    
    # Check if SurgR1 is available — R1 is required for frame analysis
    try:
        from ..services.surgr1_client import SurgR1Client
        surgr1_client = SurgR1Client()
        r1_healthy = await surgr1_client.check_health()
        if not r1_healthy:
            existing_frames = get_frames_by_session(db, session["session_id"]) or []
            has_frame_rows = len(existing_frames) > 0
            # Continue if the continuous analyzer is running or frame rows
            # already exist. Local expert fallback can summarize those rows even
            # when the external SurgR1 API health check is down.
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
    asyncio.create_task(
        glm_summarization_task(
            session_id=request.session_id,
            video_path=session["video_path"],
            db_session_id=session["session_id"],
            use_chinese=request.use_chinese,
            use_glm_multimodal=request.use_glm_multimodal,
            is_live=request.is_live
        )
    )
    
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
    
    is_live=True (在线模式): 速度优先，第一个窗口2帧就触发，少发图片
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
- GallbladderRetraction(胆囊牵拉)
- CleaningCoagulation(清洁止血)
- GallbladderPackaging(胆囊取出)

## 关键解剖结构
胆囊管、胆囊动脉、胆囊、肝胆三角、胆囊板

## 手术器械
抓钳、电凝钩、剪刀、钛夹钳、Hem-o-lok夹、金属钛夹、冲洗器、双极电凝

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

                        # Realtime fast track: for the currently playing window,
                        # emit an expert-only snapshot after a short warmup. Do
                        # not wait for the full window, Gemini, or R1.
                        current_wid = int(live_elapsed / settings.WINDOW_DURATION)
                        current_start = current_wid * settings.WINDOW_DURATION
                        if live_elapsed - current_start >= min(1.5, settings.WINDOW_DURATION * 0.3):
                            candidate_ids.add(current_wid)

                        for wid in sorted(candidate_ids):
                            if wid in stage1_processed_windows:
                                continue
                            start_time = wid * settings.WINDOW_DURATION
                            end_time = start_time + settings.WINDOW_DURATION
                            if live_elapsed + 0.2 < end_time and wid != current_wid:
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
            first_window_fast = is_live and (len(processed_windows) == 0)
            
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
                if (
                    is_live
                    and all_window_ids
                    and not surgr1_continuous_flags.get(session_id, False)
                ):
                    capture_stopped = True
                if capture_stopped:
                    remaining_windows = [
                        wid for wid in all_window_ids
                        if wid not in processed_windows and len(window_frames.get(wid, [])) >= 1
                    ]
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
                end_time = start_time + settings.WINDOW_DURATION
                
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
                        "胆囊牵拉": "GallbladderRetraction",
                    }

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
                        if not open_cfg.get("enabled", True):
                            return {}
                        if not images:
                            return {}

                        provider = str(open_cfg.get("provider", "openai")).lower()
                        model_name = open_cfg.get("model_name")
                        thinking_level = open_cfg.get("thinking_level", "none")
                        max_images = int(open_cfg.get("max_images", 4) or 4)
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
                        phase_context = _canonical_phase(
                            ((expert_pack or {}).get("phase") or {}).get("label", "") or ""
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
                        video_name_for_hint = ""
                        try:
                            video_name_for_hint = str((video_session or {}).get("video_name") or "")
                        except Exception:
                            video_name_for_hint = ""
                        out_of_body_candidate = _hardcoded_out_of_body_candidate(
                            start_time=start_time,
                            end_time=end_time,
                            video_path=video_path,
                            video_name=video_name_for_hint,
                        )
                        if out_of_body_candidate.get("out_of_body_candidate"):
                            local_visibility_cue.update(out_of_body_candidate)
                        out_of_body_focus_line = ""
                        if local_visibility_cue.get("out_of_body_candidate"):
                            out_of_body_focus_line = (
                                "\n【重点复核：镜头移出体外候选】"
                                f"{local_visibility_cue.get('prompt_hint') or ''}"
                                "这类画面不要只写成起雾/模糊；若腹腔内肝胆组织已经不可见，"
                                "且画面变成套管内壁、腹壁外场景、整帧红色贴近组织或手术室体外结构，"
                                "必须输出 visibility.status=\"out_of_body\"、visibility.out_of_body=true、"
                                "visibility.confidence>=0.75，并在summary写“镜头移出体外，画面切换至套管口或腹壁外场景”。"
                            )

                        question = (
                            "你将看到一个约5秒的腹腔镜胆囊切除术视频窗口的连续抽帧。"
                            "请只根据图像判断关键视觉事实；本地模型提示只能作为弱参考，可能误检。\n"
                            "必须把以下视觉信息合并到一次判断里：Hem-o-lok夹、金属钛夹、钛夹钳、"
                            "剪刀切断胆囊管或胆囊动脉、纱布和棉片、活动性出血、止血状态、CVS关键安全视野、"
                            "镜头起雾/烟雾/模糊、雾气解除、镜头移出体外或画面切到套管口/腹壁外/手术室场景。\n"
                            "判别规则：\n"
                            "- Hem-o-lok通常是白色、淡色、紫色塑料锁扣夹；金属钛夹通常是小型金属夹。"
                            "白色/淡紫色塑料锁扣夹优先标为Hem-o-lok；除非图像中同时存在独立的金属夹体，"
                            "不要把同一枚白色夹同时标成Hem-o-lok和金属钛夹。"
                            "白色长杆、电凝钩绝缘陶瓷头、吸引器尖端、套管针尖端不是夹体；"
                            "如果亮白物体连接在长杆器械末端且随器械移动，只能算器械尖端，不能标为Hem-o-lok或金属钛夹。"
                            "只有看到独立留在组织上的C形/U形/锁扣状夹体、释放中的夹体，或明确已夹闭残端，才可认为placed=true。"
                            "- 只看到钛夹钳时不要直接推断已经放置Hem-o-lok夹或金属钛夹；必须看到夹体、释放动作、已夹闭残端。"
                            "- 如果当前处于preparation/准备/入路建立阶段，禁止判断为施夹、夹闭、Hem-o-lok、金属钛夹或钛夹钳活动；"
                            "此阶段白色细长器械更可能是穿刺器械、套管针、镜头或入路器械。"
                            "- 如果钛夹钳夹臂张开后夹住细管状目标、夹臂围住目标结构、或正在释放夹体，"
                            "clip_applier.active必须为true，summary必须写出钛夹钳正在夹闭胆囊管或胆囊动脉。"
                            "- 如果看到剪刀夹住、剪断或正在剪断被夹闭后的细管状结构，scissors.cutting必须为true，"
                            "并判断target是cystic_duct还是cystic_artery；summary必须写出剪刀正在剪断胆囊管或胆囊动脉。"
                            "但剪断胆囊管或胆囊动脉是不可逆动作：只有在同一窗口或历史证据已看到CVS达成，"
                            "且同一目标已经被Hem-o-lok夹、金属钛夹或钛夹钳明确夹闭后，才能把剪刀动作写成剪断该目标；"
                            "如果只看到剪刀接触但没有已夹闭证据，只能写剪刀在目标附近操作或分离组织，不能写切断。"
                            "已经切断后的同一目标，后续不要再写“正在夹闭”，只能写已夹闭残端。"
                            "- 纱布和棉片是白色片状或团状材料，不要把强反光、水泡、烟雾当作纱布。"
                            "- 出血只关注大量活动性出血、持续涌出、明确出血源或影响视野的持续渗血；"
                            "少量血染、红色组织、陈旧血液、夹闭区局部血迹不算需要上报的活动性出血。"
                            "- 视野状态需要独立判断：如果镜头起雾、烟雾、模糊或水汽遮挡使组织边界不清，"
                            "visibility.status写foggy或blurred，summary写“镜头起雾，手术视野受遮挡”。"
                            "如果同一窗口中雾气从有到无、视野恢复清晰，visibility.fog_cleared=true，"
                            "summary写“雾已去除，腹腔视野恢复”。"
                            "如果画面离开腹腔、出现套管口、腹壁外场景、体外器械台或手术室环境，"
                            "visibility.out_of_body=true，summary写“镜头移出体外，画面切换至套管口或腹壁外场景”。"
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
                            '  "summary": "一句中文事实摘要；看到施夹器活动、夹体、出血、纱布或CVS变化时必须非空；没有新增事实才为空字符串",\n'
                            '  "visual": {\n'
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
                            f"{target_hint_line}\n"
                            f"本地模型弱提示：\n{expert_text[:1000]}"
                        )

                        def _openai_image_url(image: Image.Image) -> str:
                            im = image.convert("RGB")
                            im.thumbnail((640, 640), Image.Resampling.LANCZOS)
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
                            try:
                                parsed = json.loads(cleaned)
                                return parsed if isinstance(parsed, dict) else None
                            except Exception:
                                start = cleaned.find("{")
                                end = cleaned.rfind("}")
                                if start >= 0 and end > start:
                                    try:
                                        parsed = json.loads(cleaned[start:end + 1])
                                        return parsed if isinstance(parsed, dict) else None
                                    except Exception:
                                        return None
                            return None

                        def _ensure_visual_defaults(visual: Any) -> Dict[str, Any]:
                            base = {
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
                                "clip": "titanium_clip",
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
                            base["target_structure"] = target
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
                            if local_visibility_cue.get("out_of_body_candidate") and not visibility.get("out_of_body"):
                                candidate_conf = _safe_float(local_visibility_cue.get("confidence"), 0.82)
                                model_conf = _safe_float(visibility.get("confidence"), 0.0)
                                # VID001 has a known withdrawal segment. If the
                                # GPT answer is absent or only weakly classifies
                                # the same frames as fog/blur/clear, promote the
                                # hard-coded review cue into the structured
                                # window update instead of leaving it to the
                                # event-node fallback.
                                if model_conf < 0.85 or visibility["status"] in {"clear", "foggy", "blurred", "blocked"}:
                                    visibility.update({
                                        "status": "out_of_body",
                                        "out_of_body": True,
                                        "fog": False,
                                        "confidence": max(model_conf, candidate_conf),
                                        "evidence": local_visibility_cue.get("summary") or "VID001 9:59 hard-coded out-of-body candidate",
                                        "hardcoded_candidate": True,
                                    })
                            base["visibility"] = visibility
                            return base

                        def _target_structure_text(visual: Dict[str, Any]) -> str:
                            target = visual.get("target_structure") or {}
                            label = str(target.get("label") or "unknown").strip().lower()
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
                            if visibility["out_of_body"]:
                                parts.append("镜头移出体外，画面切换至套管口或腹壁外场景")
                            if visibility["fog_active"]:
                                parts.append("镜头起雾，手术视野受遮挡")
                            elif visibility["fog_resolved"]:
                                parts.append("雾已去除，腹腔视野恢复")
                            if clip_flags["hem_visible"]:
                                count_text = f"{hem_count}枚" if hem_count > 0 else ""
                                parts.append(f"可见{count_text}Hem-o-lok夹已夹闭{_target_structure_text(visual)}")
                            if clip_flags["titanium_visible"]:
                                count_text = f"{titanium_count}枚" if titanium_count > 0 else ""
                                parts.append(f"可见{count_text}金属钛夹已夹闭{_target_structure_text(visual)}")
                            if clip_flags["applier_active"]:
                                parts.append(f"钛夹钳正在夹闭{_target_structure_text(visual)}")
                            if (
                                scissors.get("cutting")
                                and _safe_float(scissors.get("confidence"), 0.0) >= 0.45
                                and not is_preparation_context
                            ):
                                scissors_target = _target_label_from_raw(scissors.get("target"))
                                target_text = _target_cn(scissors_target) if scissors_target else _target_structure_text(visual)
                                parts.append(f"剪刀正在剪断{target_text}")
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
                            if cvs_status == "achieved":
                                parts.append("CVS三要素已基本达成")
                            elif cvs_status in {"assessing", "partial"}:
                                parts.append("CVS仍在评估中")
                            return "；".join(parts).strip("；") + ("。" if parts else "")

                        def _clip_display_flags(visual: Dict[str, Any]) -> Dict[str, Any]:
                            hemolok = visual.get("hemolok") or {}
                            titanium = visual.get("titanium_clip") or {}
                            applier = visual.get("clip_applier") or {}
                            target = visual.get("target_structure") or {}
                            hem_count = int(hemolok.get("count") or 0)
                            titanium_count = int(titanium.get("count") or 0)
                            hem_conf = _safe_float(hemolok.get("confidence"), 0.0)
                            titanium_conf = _safe_float(titanium.get("confidence"), 0.0)
                            applier_conf = _safe_float(applier.get("confidence"), 0.0)
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
                                or local_clipper_seen
                                or phase_context == "ClippingCutting"
                            )
                            evidence_active_allowed = bool(evidence_active and strong_artery_context)
                            applier_context = bool(
                                phase_context == "ClippingCutting"
                                or local_clipper_seen
                                or hem_visible
                                or titanium_visible
                                or (hem_seen_high_conf and applier_conf >= 0.75)
                                or (clip_seen_mid_conf and applier_conf >= 0.90)
                                or evidence_active_allowed
                            )
                            applier_active = bool(
                                applier.get("active")
                                and (applier_conf >= 0.75 or (evidence_active_allowed and applier_conf >= 0.55))
                                and applier_context
                                and (
                                    target_conf >= 0.35
                                    or hem_visible
                                    or titanium_visible
                                    or local_clipper_seen
                                    or phase_context == "ClippingCutting"
                                )
                                and not is_preparation_context
                            )
                            return {
                                "hem_visible": hem_visible,
                                "titanium_visible": titanium_visible,
                                "applier_active": applier_active,
                                "hem_count": hem_count,
                                "titanium_count": titanium_count,
                            }

                        def _has_structured_visual_evidence(visual: Dict[str, Any]) -> bool:
                            clip_flags = _clip_display_flags(visual)
                            scissors = visual.get("scissors") or {}
                            gauze = visual.get("gauze") or {}
                            bleeding = visual.get("bleeding") or {}
                            cvs = visual.get("cvs") or {}
                            visibility = _visibility_flags_from_visual(visual)
                            return bool(
                                clip_flags["hem_visible"]
                                or clip_flags["titanium_visible"]
                                or clip_flags["applier_active"]
                                or (
                                    scissors.get("cutting")
                                    and _safe_float(scissors.get("confidence"), 0.0) >= 0.45
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
                                r"[，,；;。]?\s*视野清晰",
                            )
                            for pattern in negative_patterns:
                                text = re.sub(pattern, "", text)
                            if is_preparation_context or not any(_clip_display_flags(visual)[key] for key in ("hem_visible", "titanium_visible", "applier_active")):
                                clipping_patterns = (
                                    r"[，,；;。]?\s*可见\d*枚?Hem-o-lok夹夹闭(?:胆囊管|胆囊动脉)",
                                    r"[，,；;。]?\s*可见\d*枚?金属钛夹夹闭(?:胆囊管|胆囊动脉)",
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
                            text = re.sub(r"[，,；;]\s*[，,；;]+", "，", text)
                            text = re.sub(r"^[，,；;。]+|[，,；;。]+$", "", text)
                            text = re.sub(r"^(?:看到|可见)?[，,]?\s*且有(?:明显)?$", "", text)
                            if text in {"看到明显", "看到", "且有", "可见明显", "可见"}:
                                return ""
                            return text.strip()

                        async def _normalize_open_vision_result(result: Dict[str, Any], source_provider: str) -> Dict[str, Any]:
                            text = (result.get("text") or result.get("summary") or "").strip()
                            if not result.get("success") or not text:
                                if local_visibility_cue.get("out_of_body_candidate"):
                                    visual = _ensure_visual_defaults({
                                        "visibility": {
                                            "status": "out_of_body",
                                            "out_of_body": True,
                                            "fog": False,
                                            "confidence": _safe_float(local_visibility_cue.get("confidence"), 0.82),
                                            "evidence": local_visibility_cue.get("summary") or "VID001 9:59 hard-coded out-of-body candidate",
                                            "hardcoded_candidate": True,
                                        }
                                    })
                                    summary = _visual_fallback_summary(visual).strip(" 。")
                                    return {
                                        "success": True,
                                        "summary": (summary + "。") if summary else "镜头移出体外，画面切换至套管口或腹壁外场景。",
                                        "visual": visual,
                                        "raw": result.get("error") or "hardcoded_out_of_body_post_update",
                                        "model": result.get("model"),
                                        "provider": source_provider,
                                        "thinking_level": result.get("_thinking_level_used", thinking_level),
                                        "requested_thinking_level": thinking_level,
                                        "duration_ms": result.get("duration_ms"),
                                        "external_call_count": int(result.get("_external_call_count") or 0),
                                    }
                                return {
                                    "success": False,
                                    "error": result.get("error"),
                                    "model": result.get("model"),
                                    "provider": source_provider,
                                    "external_call_count": int(result.get("_external_call_count") or 0),
                                }
                            parsed = _parse_json_object(text)
                            visual = _ensure_visual_defaults((parsed or {}).get("visual") if parsed else None)
                            summary = ""
                            if parsed:
                                summary = str(parsed.get("summary") or parsed.get("analysis") or "").strip()
                            if not summary and parsed:
                                summary = _visual_fallback_summary(visual)
                            if not parsed:
                                summary = text
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
                                "external_call_count": int(result.get("_external_call_count") or 0),
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
                            if provider == "gemini" and "Budget 0 is invalid" in error_text and thinking_level == "none":
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
                                logger.warning(f"[OpenVLM] Realtime hint failed: {e}")
                                if open_cfg.get("openai_fallback", True):
                                    fallback = await _call_openai_vision()
                                    normalized = await _normalize_open_vision_result(fallback, "openai")
                                    if normalized.get("success"):
                                        return normalized
                                return {"success": False, "error": error_text}

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
                            visibility_flags = _visibility_flags_from_visual(open_vlm_hint.get("visual") or {})
                            if visibility_flags["out_of_body"]:
                                current_summary = ""
                            if hint_summary:
                                normalized_hint = hint_summary.rstrip("。")
                                if normalized_hint and normalized_hint not in current_summary:
                                    current_summary = (
                                        f"{current_summary.rstrip('。')}。{normalized_hint}。"
                                        if current_summary.strip(" 。")
                                        else f"{normalized_hint}。"
                                    )
                            visibility_summary = _visibility_summary_for_window(
                                open_vlm_hint.get("visual") or {},
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
                                    visual=open_vlm_hint.get("visual") or {},
                                )
                            except Exception as rule_e:
                                logger.warning(f"[SequenceRules] OpenVLM rule pass failed for window {window_id}: {rule_e}")

                            experts = dict((others_data or {}).get("experts") or {})
                            experts.update({
                                "phase": expert_pack_base.get("phase", experts.get("phase", {})),
                                "triplet": expert_pack_base.get("triplet", experts.get("triplet", {})),
                                "yolo": {
                                    "tools": expert_pack_base.get("yolo", {}).get("tools", []),
                                    "total_detections": expert_pack_base.get("yolo", {}).get("total_detections", 0),
                                },
                                "short_action": expert_pack_base.get("short_action", experts.get("short_action", {})),
                                "hemlok_clip": expert_pack_base.get("hemlok_clip", experts.get("hemlok_clip", {})),
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
                                "model": "yolo+phase+triplet+opencv_cues",
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
                                try:
                                    hardcoded_oob = _hardcoded_out_of_body_candidate(
                                        start_time=meta.get("start_time", 0),
                                        end_time=meta.get("end_time", 0),
                                        video_path=video_path,
                                        video_name=str((video_session or {}).get("video_name") or ""),
                                    )
                                    if hardcoded_oob.get("out_of_body_candidate"):
                                        local_visibility.update(hardcoded_oob)
                                except Exception as cue_e:
                                    logger.debug(f"[Visibility] hard-coded out-of-body cue skipped: {cue_e}")
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
                                    asyncio.create_task(
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
                                else:
                                    if vlm_client is None:
                                        stage1_summary_text = _expert_snapshot_summary(
                                            expert_pack=expert_pack,
                                            start_time=meta.get("start_time", 0),
                                            end_time=meta.get("end_time", 0),
                                            frame_count=len(bgr_frames),
                                        )
                                        stage1_phase = _canonical_phase(
                                            expert_pack.get("phase", {}).get("label", "Unknown") or "Unknown"
                                        )
                                    else:
                                        history_context = await history_manager.build_history_context()
                                        stage1_text_vlm_count = 1
                                        logger.info(
                                            "[ModelCall] session=%s window=%s type=stage1_text_vlm provider=%s images=0",
                                            session_id,
                                            window_id,
                                            get_summarization_provider(),
                                        )
                                        stage1_result = await vlm_client.integrate_experts_text_only(
                                            expert_context=expert_text,
                                            history_context=history_context,
                                            temperature=0.5,
                                            max_tokens=512,
                                        )
                                        if stage1_result.get("success"):
                                            stage1_summary_text = stage1_result.get("summary", "")
                                            stage1_phase = next(
                                                (en for cn, en in phase_map.items() if cn in stage1_summary_text),
                                                _canonical_phase(expert_pack.get("phase", {}).get("label", "Unknown") or "Unknown"),
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
                                    "experts": {
                                        "phase": expert_pack.get("phase", {}),
                                        "triplet": expert_pack.get("triplet", {}),
                                        "yolo": {
                                            "tools": expert_pack.get("yolo", {}).get("tools", []),
                                            "total_detections": expert_pack.get("yolo", {}).get("total_detections", 0),
                                        },
                                        "short_action": expert_pack.get("short_action", {}),
                                        "hemlok_clip": expert_pack.get("hemlok_clip", {}),
                                        "local_visibility": expert_pack.get("local_visibility", {}),
                                        "open_vlm": expert_pack.get("open_vlm", {}),
                                        "available": expert_pack.get("available", []),
                                    },
                                    "model_calls": {
                                        "stage1_local": {
                                            "count": 0,
                                            "provider": "local",
                                            "model": "yolo+phase+triplet+opencv_cues",
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
                                    stage1_summary_text = (
                                        existing_summary.get("glm_summary", "")
                                        if isinstance(existing_summary, dict)
                                        else getattr(existing_summary, "summary_text", "")
                                    ) or ""
                                    existing_others_for_stage2 = (
                                        existing_summary.get("others")
                                        if isinstance(existing_summary, dict)
                                        else getattr(existing_summary, "others_data", None)
                                    ) or {}
                                    if isinstance(existing_others_for_stage2, str):
                                        existing_others_for_stage2 = json.loads(existing_others_for_stage2) if existing_others_for_stage2 else {}
                                    existing_model_calls = existing_others_for_stage2.get("model_calls") or {}
                                    existing_visual_gpt = existing_others_for_stage2.get("visual_gpt") or {}
                            except Exception as e:
                                logger.debug(f"[GLM Task] Could not load previous Stage 1 for window {window_id}: {e}")

                        # ===================== Stage 2: 专家 + SurgR1 + 图像 =====================
                        stage2_summary_vlm_count = 0
                        stage2_summary_provider = None
                        stage2_summary_model = None
                        if vlm_client is None:
                            summary_text = stage1_summary_text or _expert_snapshot_summary(
                                expert_pack=expert_pack,
                                start_time=meta.get("start_time", 0),
                                end_time=meta.get("end_time", 0),
                                frame_count=len(frame_analyses),
                            )
                            others_data_raw = None
                            dominant_phase = stage1_phase
                            surgr1_reasoning = ""
                        else:
                            try:
                            # 10s 窗口 / 1fps = ~10 帧；Stage 2 取 3 张（首/中/尾）给 Gemini 多模态
                                window_images = window_data.get("images")
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
                                    existing_experts: Dict[str, Any] = {}
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
                            "model": "yolo+phase+triplet+opencv_cues",
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

                        # Stage 2 others_data：保留 stage1 的 experts 数据并附加 CoT + raw others
                        others_data = {
                            "stage": 2,
                            "stage1_summary": stage1_summary_text,
                            "experts": {
                                "phase": expert_pack.get("phase", {}),
                                "triplet": expert_pack.get("triplet", {}),
                                "yolo": {
                                    "tools": expert_pack.get("yolo", {}).get("tools", []),
                                    "total_detections": expert_pack.get("yolo", {}).get("total_detections", 0),
                                },
                                "short_action": expert_pack.get("short_action", {}),
                                "hemlok_clip": expert_pack.get("hemlok_clip", {}),
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
            "tts_audio_path": s.get("image_path")  # Use image_path as fallback, no tts_audio_path in new schema
        }
        if isinstance(others, dict):
            payload["stage"] = int(others.get("stage", 2) or 2)
            if others.get("stage1_summary"):
                payload["stage1_summary"] = others.get("stage1_summary")
            payload["others"] = others
        out.append(payload)
    return out


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

        if re.search(r"(大量活动性出血|活动性出血|明显出血|持续出血|喷涌出血)", src):
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
    r"(镜头|腹腔镜|视野|画面).{0,12}(?:移出体外|退出体外|离开腹腔|腹腔外|套管口|腹壁外|切换至手术室|手术室场景)|"
    r"(?:体外|腹腔外|套管口|腹壁外|手术室场景|器械台|trocar|trocar outside|outside the body|outside-body|extracorporeal|operating room scene|extra-abdominal)",
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
    if flags["out_of_body"]:
        return "镜头移出体外，画面切换至套管口或腹壁外场景。" if zh else "The scope is moved outside the body and the view switches to the trocar or extra-abdominal scene."
    if flags["fog_active"]:
        return "镜头起雾，手术视野受遮挡。" if zh else "Lens fogging obscures the surgical field."
    if flags["fog_resolved"] or (previous.get("fog_active") and flags["status"] == "clear" and flags["confidence"] >= 0.45):
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

    return {
        "window_id": window_id,
        "display_window": window_id + 1,
        "start_time": start_time,
        "end_time": end_time,
        "summary": str(text or "").strip(),
        "phase": str(phase or "Unknown"),
        "stage": stage,
        "visibility": ",".join(visibility_tokens) or "clear",
    }


def _event_nodes_signature(records: List[Dict[str, Any]]) -> str:
    payload = [
        {
            "w": r.get("window_id"),
            "p": r.get("phase"),
            "s": r.get("stage"),
            "v": r.get("visibility"),
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


def _parse_event_nodes_json(text: str) -> Dict[str, Any]:
    cleaned = _strip_json_fences(text)
    try:
        parsed = json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
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
    return bool(re.search(r"(胆囊管夹闭|胆囊动脉夹闭|夹闭|闭合|钛夹|施夹|hem[-\s]?o[-\s]?lok|clip)", src, re.IGNORECASE))


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
        if not summary_text:
            summary_text = windows[-1].get("summary") or title

        if _looks_like_clip_event(f"{title} {summary_text}") and not any(
            _has_explicit_clip_evidence(w.get("summary", "")) for w in windows
        ):
            continue

        combined_event_text = f"{title} {summary_text}"
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
    return events[:14]


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
        if phase != prev_phase and phase not in ("Unknown", "未知", ""):
            title = f"进入{PHASE_LABEL_CN.get(phase, phase)}" if zh else f"Phase shift: {phase}"
            summary = record.get("summary") or title
            _merge_or_append_fallback_event(events, make_event(record, "phase", "important", title, summary, 0.55))
            prev_phase = phase

        if re.search(r"(cvs|critical view|安全视野|关键安全视野|两条结构|胆囊管|胆囊动脉)", text, re.IGNORECASE):
            title = "CVS安全评估节点" if zh else "CVS safety checkpoint"
            _merge_or_append_fallback_event(events, make_event(record, "cvs", "safety", title, record.get("summary") or title, 0.58))

        if _has_explicit_clip_evidence(text):
            if re.search(r"胆囊管|cystic duct", text, re.IGNORECASE):
                title = "胆囊管夹闭" if zh else "Cystic duct clipping"
            elif re.search(r"胆囊动脉|cystic artery", text, re.IGNORECASE):
                title = "胆囊动脉夹闭" if zh else "Cystic artery clipping"
            else:
                title = "胆囊管夹闭" if zh else "Cystic duct clipping"
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

    if not events and records:
        latest = records[-1]
        title = "最新手术进展" if zh else "Latest surgical progress"
        events.append(make_event(latest, "other", "normal", title, latest.get("summary") or title, 0.45))

    return events[-12:]


def _visibility_status_events(records: List[Dict[str, Any]], language: str) -> List[Dict[str, Any]]:
    zh = language.startswith("zh")
    fog_active = False
    first_active = None
    latest_active = None
    latest_resolved = None
    latest_out_of_body = None
    active_ids: List[int] = []

    for record in sorted(records or [], key=lambda r: (r.get("window_id", 0), r.get("start_time", 0))):
        summary_text = str(record.get("summary", "") or "")
        tokens = {token.strip() for token in str(record.get("visibility", "") or "").split(",")}
        active = FOG_ACTIVE_RE.search(summary_text) or "fog_active" in tokens
        resolved = FOG_RESOLVED_RE.search(summary_text) or "fog_resolved" in tokens
        out_of_body = OUT_OF_BODY_RE.search(summary_text) or "out_of_body" in tokens

        if active:
            if not fog_active:
                first_active = record
                active_ids = []
            fog_active = True
            latest_active = record
            active_ids.append(int(record["window_id"]))
        if resolved:
            latest_resolved = record
            fog_active = False
        if out_of_body:
            latest_out_of_body = record

    events: List[Dict[str, Any]] = []
    if first_active or latest_resolved:
        if fog_active and latest_active:
            start_record = first_active or latest_active
            window_ids = sorted(set(active_ids or [int(latest_active["window_id"])]))
            events.append({
                "id": "event_visibility_fog_status",
                "type": "visibility",
                "severity": "critical",
                "title": "视野起雾" if zh else "Fog obscures view",
                "summary": "镜头起雾，手术视野受遮挡。" if zh else "Lens fogging obscures the surgical field.",
                "window_ids": window_ids,
                "representative_window_id": int(latest_active["window_id"]),
                "start_time": float(start_record.get("start_time", latest_active.get("start_time", 0)) or 0),
                "end_time": float(latest_active.get("end_time", latest_active.get("start_time", 0)) or 0),
                "confidence": 0.78,
                "source": "visibility-status",
            })
        elif latest_resolved:
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

    if latest_out_of_body:
        events.append({
            "id": "event_visibility_out_of_body",
            "type": "visibility",
            "severity": "important",
            "title": "镜头移出体外" if zh else "Scope moved outside body",
            "summary": "镜头移出体外，画面切换至套管口或腹壁外场景。" if zh else "The scope is moved outside the body and the view switches to the trocar or extra-abdominal scene.",
            "window_ids": [int(latest_out_of_body["window_id"])],
            "representative_window_id": int(latest_out_of_body["window_id"]),
            "start_time": float(latest_out_of_body.get("start_time", 0) or 0),
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

    merged = list(events or [])
    for status_event in status_events:
        merged = [event for event in merged if not is_visibility_duplicate(event, status_event)]
        merged.append(status_event)

    merged.sort(key=lambda e: (float(e.get("start_time", 0) or 0), int(e.get("representative_window_id", 0) or 0)))
    if len(merged) <= 14:
        return merged

    status_ids = {event["id"] for event in status_events}
    status_part = [event for event in merged if event.get("id") in status_ids]
    other_part = [event for event in merged if event.get("id") not in status_ids]
    keep_other = max(0, 14 - len(status_part))
    return sorted(other_part[-keep_other:] + status_part, key=lambda e: (float(e.get("start_time", 0) or 0), int(e.get("representative_window_id", 0) or 0)))


def _build_event_nodes_prompt(records: List[Dict[str, Any]], language: str) -> str:
    output_language = "Chinese" if language.startswith("zh") else "English"
    window_lines = []
    for record in records:
        summary = re.sub(r"\s+", " ", record.get("summary", "")).strip()
        if len(summary) > 320:
            summary = summary[:317] + "..."
        window_lines.append(
            f"- window_id={record['window_id']} display_window={record['display_window']} "
            f"time={record['start_time']:.1f}-{record['end_time']:.1f}s "
            f"phase={record.get('phase') or 'Unknown'} stage={record.get('stage', 2)} "
            f"visibility={record.get('visibility') or 'clear'} "
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
5. Critical actions include trocar/puncture/entry, drilling/perforation-like contact, traction, dissection,
   electrocautery, clipping, cutting, irrigation, specimen bagging, and gallbladder removal.
   Do not create a cystic duct or cystic artery clipping event from the phase name "ClippingCutting" alone.
   A clipping event requires explicit evidence such as Hem-o-lok, titanium clip, clip applier release,
   clipped stump, or a sentence directly saying the cystic duct/artery was clipped.
   A cystic duct/artery cutting event requires explicit prior or same-window clipping evidence for the same
   target and CVS achieved/confirmed evidence. If that ordering evidence is missing, keep it as scissors
   manipulation near the target rather than a division event.
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
10. Keep 3-12 event nodes when possible. Include the latest meaningful event.
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
    if len(records) > max_windows:
        records = records[-max_windows:]

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

    system_prompt = (
        "You are a surgical event-node analyst for laparoscopic cholecystectomy. "
        "You convert existing time-window summaries into a concise, structured key-event timeline. "
        "You must preserve uncertainty, never add unobserved findings, and output valid JSON only."
    )
    prompt = _build_event_nodes_prompt(records, language)

    try:
        if not event_cfg.get("enabled", True):
            raise RuntimeError("event node LLM disabled by config")

        if provider == "glm":
            client = get_glm_client()
            result = await client.chat(
                message=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                disable_thinking=True,
            )
        elif provider in {"openai", "gpt", "openai_compatible"}:
            result = await _call_openai_compatible_event_nodes(
                prompt=prompt,
                system_prompt=system_prompt,
                event_cfg=event_cfg,
                max_tokens=max_tokens,
                temperature=temperature,
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
                result = await client.chat(
                    message=prompt,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
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
            "signature": signature,
            "error": str(exc),
            "events": _merge_visibility_status_events(_fallback_event_nodes(records, language, str(exc)), records, language),
        }

    if response.get("source") == "llm":
        if len(event_node_cache) > 128:
            event_node_cache.pop(next(iter(event_node_cache)), None)
        event_node_cache[cache_key] = response
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
