"""
Temporal Analysis for SurgR1 Results
Provides image-level (intra-frame) and adjacent (inter-frame) consistency analysis.
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from collections import Counter
import logging

logger = logging.getLogger(__name__)


@dataclass
class FrameAnalysis:
    """Single frame analysis result from SurgR1"""
    frame_idx: int = 0
    timestamp: float = 0.0
    phase: str = ""
    action: str = ""
    tools: str = ""
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FrameAnalysis":
        return cls(
            frame_idx=d.get("frame_idx", 0),
            timestamp=d.get("timestamp", 0.0),
            phase=d.get("phase", d.get("surgical_phase", "")),
            action=d.get("action", d.get("surgical_action", "")),
            tools=d.get("tools", d.get("tool_localization", ""))
        )


@dataclass
class ClipData:
    """A clip (window) containing multiple frame analyses"""
    window_id: int = 0
    start_time: float = 0.0
    end_time: float = 0.0
    frames: List[FrameAnalysis] = field(default_factory=list)
    
    @classmethod
    def from_frame_analyses(
        cls, 
        frame_analyses: List[Dict[str, Any]], 
        window_id: int,
        window_duration: float = 5.0
    ) -> "ClipData":
        """Create ClipData from a list of frame analysis dicts"""
        frames = [FrameAnalysis.from_dict(f) for f in frame_analyses]
        start_time = window_id * window_duration
        end_time = start_time + window_duration
        return cls(
            window_id=window_id,
            start_time=start_time,
            end_time=end_time,
            frames=frames
        )


def clean_phase(phase: str) -> str:
    """Clean phase string by removing <think> tags and extracting actual phase"""
    if not phase:
        return "Unknown"
    
    # Remove <think> content
    if "<think>" in phase:
        # Try to extract text after </think> or before <think>
        if "</think>" in phase:
            parts = phase.split("</think>")
            if len(parts) > 1:
                phase = parts[-1].strip()
        else:
            # If no closing tag, try to extract any phase name
            phase = phase.split("<think>")[0].strip()
    
    # Clean up common phase names
    phase = phase.strip()
    if not phase or phase.startswith("<"):
        return "Unknown"
    
    # Map to standard phase names
    phase_mapping = {
        "preparation": "Preparation",
        "calottriangled": "CalotTriangleDissection",
        "calottriangledissection": "CalotTriangleDissection",
        "clippingcutting": "ClippingCutting",
        "gallbladderd": "GallbladderDissection",
        "gallbladderdissection": "GallbladderDissection",
        "gallbladderr": "GallbladderRetraction",
        "gallbladderretraction": "GallbladderRetraction",
        "cleaningc": "CleaningCoagulation",
        "cleaningcoagulation": "CleaningCoagulation",
        "gallbladderp": "GallbladderPackaging",
        "gallbladderpackaging": "GallbladderPackaging",
    }
    
    phase_lower = phase.lower().replace(" ", "").replace("_", "")
    for key, value in phase_mapping.items():
        if key in phase_lower:
            return value
    
    return phase if len(phase) < 50 else phase[:50]


def clean_action(action: str) -> str:
    """Clean action string"""
    if not action:
        return "Unknown"
    
    # Remove <think> content
    if "<think>" in action:
        if "</think>" in action:
            parts = action.split("</think>")
            if len(parts) > 1:
                action = parts[-1].strip()
        else:
            action = action.split("<think>")[0].strip()
    
    action = action.strip()
    if not action or action.startswith("<"):
        return "Unknown"
    
    return action if len(action) < 200 else action[:200]


def analyze_clip_consistency(clip: ClipData) -> Dict[str, Any]:
    """
    Analyze consistency within a clip (image-level and adjacent).
    
    Returns:
        Dict containing:
        - image_level_consistency: Intra-frame statistics
        - adjacent_consistency: Inter-frame transition statistics
        - temporal_summary: Aggregated temporal information
    """
    if not clip.frames:
        return {
            "image_level_consistency": {},
            "adjacent_consistency": {},
            "temporal_summary": {},
            "cleaned_data": {}
        }
    
    # Extract and clean data from frames
    phases = [clean_phase(f.phase) for f in clip.frames]
    actions = [clean_action(f.action) for f in clip.frames]
    tools_list = [f.tools for f in clip.frames]
    
    # ========== Image-level consistency (Intra-frame) ==========
    unique_phases = set(phases)
    unique_actions = set(actions)
    
    # Count phase distribution
    phase_counter = Counter(phases)
    dominant_phase = phase_counter.most_common(1)[0][0] if phase_counter else "Unknown"
    phase_confidence = phase_counter[dominant_phase] / len(phases) if phases else 0
    
    # Count action distribution
    action_counter = Counter(actions)
    dominant_action = action_counter.most_common(1)[0][0] if action_counter else "Unknown"
    
    image_level_consistency = {
        "unique_phases": len(unique_phases),
        "dominant_phase": dominant_phase,
        "phase_confidence": round(phase_confidence, 2),
        "phase_distribution": dict(phase_counter),
        "unique_actions": len(unique_actions),
        "dominant_action": dominant_action,
        "action_diversity": round(len(unique_actions) / len(actions), 2) if actions else 0
    }
    
    # ========== Adjacent consistency (Inter-frame) ==========
    phase_transitions = sum(1 for i in range(len(phases) - 1) if phases[i] != phases[i + 1])
    action_transitions = sum(1 for i in range(len(actions) - 1) if actions[i] != actions[i + 1])
    
    phase_stability = 1 - (phase_transitions / (len(phases) - 1)) if len(phases) > 1 else 1.0
    action_stability = 1 - (action_transitions / (len(actions) - 1)) if len(actions) > 1 else 1.0
    
    adjacent_consistency = {
        "phase_transitions": phase_transitions,
        "phase_stability": round(phase_stability, 2),
        "action_transitions": action_transitions,
        "action_stability": round(action_stability, 2)
    }
    
    # ========== Temporal summary ==========
    # Collect all tools mentioned
    all_tools = []
    for t in tools_list:
        if t:
            # Extract tool names from bounding box descriptions or tool lists
            tools_str = str(t).lower()
            if "grasper" in tools_str:
                all_tools.append("抓钳")
            if "hook" in tools_str:
                all_tools.append("电钩")
            if "scissor" in tools_str:
                all_tools.append("剪刀")
            if "clipper" in tools_str or "clip" in tools_str:
                all_tools.append("施夹器")
            if "irrigator" in tools_str:
                all_tools.append("冲洗器")
            if "bipolar" in tools_str:
                all_tools.append("双极电凝")
    
    unique_tools = list(set(all_tools))
    
    temporal_summary = {
        "window_id": clip.window_id,
        "start_time": clip.start_time,
        "end_time": clip.end_time,
        "frame_count": len(clip.frames),
        "dominant_phase": dominant_phase,
        "phase_confidence": round(phase_confidence, 2),
        "is_stable": phase_stability > 0.7,
        "tools_detected": unique_tools
    }
    
    # Cleaned data for GLM
    cleaned_data = {
        "phase": dominant_phase,
        "phase_confidence": round(phase_confidence, 2),
        "actions": list(set([a for a in actions if a != "Unknown"]))[:3],  # Top 3 unique actions
        "tools": unique_tools,
        "stability": {
            "phase": round(phase_stability, 2),
            "action": round(action_stability, 2)
        }
    }
    
    return {
        "image_level_consistency": image_level_consistency,
        "adjacent_consistency": adjacent_consistency,
        "temporal_summary": temporal_summary,
        "cleaned_data": cleaned_data
    }


def build_glm_context(consistency_result: Dict[str, Any]) -> str:
    """
    Build a structured context string for GLM based on consistency analysis.
    
    Args:
        consistency_result: Result from analyze_clip_consistency()
        
    Returns:
        Formatted context string in Chinese for GLM
    """
    cleaned = consistency_result.get("cleaned_data", {})
    temporal = consistency_result.get("temporal_summary", {})
    adj = consistency_result.get("adjacent_consistency", {})
    
    phase = cleaned.get("phase", "Unknown")
    confidence = cleaned.get("phase_confidence", 0)
    tools = cleaned.get("tools", [])
    actions = cleaned.get("actions", [])
    stability = cleaned.get("stability", {})
    
    # Map phase to Chinese
    phase_cn = {
        "Preparation": "准备阶段",
        "CalotTriangleDissection": "Calot三角分离",
        "ClippingCutting": "夹闭切断",
        "GallbladderDissection": "胆囊分离",
        "GallbladderRetraction": "胆囊牵拉",
        "CleaningCoagulation": "清洁止血",
        "GallbladderPackaging": "胆囊取出",
        "Unknown": "未知阶段"
    }.get(phase, phase)
    
    context_lines = [
        f"## 视频片段分析 (窗口 {temporal.get('window_id', 0)})",
        f"时间范围: {temporal.get('start_time', 0):.1f}s - {temporal.get('end_time', 0):.1f}s",
        f"分析帧数: {temporal.get('frame_count', 0)}帧",
        "",
        f"### 手术阶段",
        f"- 主导阶段: {phase_cn}",
        f"- 置信度: {confidence * 100:.0f}%",
        f"- 阶段稳定性: {stability.get('phase', 0) * 100:.0f}%",
        "",
        f"### 检测到的器械",
        f"- {', '.join(tools) if tools else '无明确检测'}",
        "",
        f"### 主要动作",
    ]
    
    for i, action in enumerate(actions[:3], 1):
        if action and action != "Unknown":
            context_lines.append(f"- {action[:100]}")
    
    if not actions or all(a == "Unknown" for a in actions):
        context_lines.append("- 正在进行手术操作")
    
    context_lines.extend([
        "",
        f"### 时序一致性",
        f"- 阶段转换次数: {adj.get('phase_transitions', 0)}",
        f"- 动作转换次数: {adj.get('action_transitions', 0)}",
    ])
    
    return "\n".join(context_lines)


def process_window_for_glm(
    frame_analyses: List[Dict[str, Any]],
    window_id: int,
    window_duration: float = 5.0
) -> Dict[str, Any]:
    """
    Process a window of frame analyses and prepare context for GLM.
    
    Args:
        frame_analyses: List of frame analysis dicts from SurgR1
        window_id: The window ID
        window_duration: Duration of each window in seconds
        
    Returns:
        Dict containing:
        - consistency: Full consistency analysis
        - context: Formatted context string for GLM
        - summary_hint: Suggested summary based on analysis
    """
    # Create clip data
    clip = ClipData.from_frame_analyses(frame_analyses, window_id, window_duration)
    
    # Analyze consistency
    consistency = analyze_clip_consistency(clip)
    
    # Build context for GLM
    context = build_glm_context(consistency)
    
    # Generate summary hint based on analysis
    cleaned = consistency.get("cleaned_data", {})
    phase = cleaned.get("phase", "Unknown")
    tools = cleaned.get("tools", [])
    
    phase_cn = {
        "Preparation": "准备阶段",
        "CalotTriangleDissection": "Calot三角分离",
        "ClippingCutting": "夹闭切断",
        "GallbladderDissection": "胆囊分离",
        "GallbladderRetraction": "胆囊牵拉",
        "CleaningCoagulation": "清洁止血",
        "GallbladderPackaging": "胆囊取出",
        "Unknown": "手术操作"
    }.get(phase, phase)
    
    tools_str = "、".join(tools) if tools else "手术器械"
    summary_hint = f"当前处于{phase_cn}，使用{tools_str}进行操作。"
    
    return {
        "consistency": consistency,
        "context": context,
        "summary_hint": summary_hint
    }




