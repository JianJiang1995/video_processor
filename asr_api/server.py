#!/usr/bin/env python3
"""
Fun-ASR-Nano 中文实时语音识别 API 服务
基于 FunAudioLLM/Fun-ASR-Nano-2512 (0.8B参数)

支持:
1. WebSocket 实时流式识别
2. HTTP REST API 文件识别  
3. 支持 VAD 语音端点检测
4. 支持关键词唤醒（持续监听模式）
5. 后端存储识别历史
6. 统一 JSON 格式输入输出
"""

import asyncio
import base64
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from typing import Optional, List
import numpy as np
import soundfile as sf
from io import BytesIO
from collections import deque
import difflib

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== 配置 ====================
class Config:
    DEVICE = os.getenv("ASR_DEVICE", "cuda:0")
    MODEL_DIR = "FunAudioLLM/Fun-ASR-Nano-2512"
    MODEL_PATH = os.path.expanduser("~/.cache/modelscope/hub/models/FunAudioLLM/Fun-ASR-Nano-2512")
    
    HOST = os.getenv("ASR_HOST", "0.0.0.0")
    PORT = int(os.getenv("ASR_PORT", "8765"))
    SAMPLE_RATE = 16000
    
    # 唤醒词配置 - 添加更多变体
    DEFAULT_KEYWORDS = ["你好小助", "小助小助", "开始识别", "小助你好", "开始录音", "嗨小助"]
    KWS_THRESHOLD = 0.6  # 相似度阈值
    
    MAX_HISTORY = 100


# ==================== 数据模型 ====================
class AudioInput(BaseModel):
    audio_data: str = Field(..., description="Base64编码的音频数据")
    sample_rate: int = Field(default=16000)
    format: str = Field(default="pcm")


class KeywordConfig(BaseModel):
    keywords: List[str] = Field(default_factory=list)
    threshold: float = Field(default=0.6)


# ==================== 历史记录存储 ====================
class TranscriptStore:
    """识别历史存储"""
    def __init__(self, max_size: int = Config.MAX_HISTORY):
        self.history: deque = deque(maxlen=max_size)
        self.current_session: List[dict] = []
        self.session_id: str = ""
        self.full_text: str = ""
    
    def start_session(self) -> str:
        """开始新会话"""
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_session = []
        self.full_text = ""
        return self.session_id
    
    def add_transcript(self, text: str, is_final: bool = True, keyword_detected: dict = None) -> dict:
        """添加识别记录"""
        if not text or not text.strip():
            return None
            
        item = {
            "id": f"{self.session_id}_{len(self.current_session)}_{int(time.time()*1000)}",
            "text": text,
            "timestamp": time.time(),
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "is_final": is_final,
            "keyword_detected": keyword_detected,
            "session_id": self.session_id
        }
        
        self.current_session.append(item)
        self.history.append(item)
        if is_final:
            self.full_text += text
        
        return item
    
    def get_session_transcript(self) -> List[dict]:
        return list(self.current_session)
    
    def get_full_text(self) -> str:
        return self.full_text
    
    def get_history(self, limit: int = 50) -> List[dict]:
        return list(self.history)[-limit:]
    
    def clear_history(self):
        self.history.clear()
        self.current_session = []
        self.full_text = ""


# 全局历史存储
transcript_store = TranscriptStore()


# ==================== 模型加载 ====================
class ASRModels:
    def __init__(self):
        self.asr_model = None  # 使用 AutoModel 集成 VAD
        self.punc_model = None
        self._loaded = False
    
    def load_models(self, device: str = Config.DEVICE):
        if self._loaded:
            return
        
        logger.info(f"正在加载 Fun-ASR-Nano 模型到设备: {device}")
        sys.path.insert(0, Config.MODEL_PATH)
        
        from funasr import AutoModel
        
        # 使用 AutoModel 加载，集成 VAD（按照 README 推荐的方式）
        # 这样 VAD 会在推理时自动过滤无语音段
        logger.info("加载 Fun-ASR-Nano-2512 (集成 VAD)...")
        self.asr_model = AutoModel(
            model=Config.MODEL_DIR,
            trust_remote_code=True,
            vad_model="fsmn-vad",  # 集成 VAD 模型
            vad_kwargs={"max_single_segment_time": 30000},  # 最大单段30秒
            remote_code=os.path.join(Config.MODEL_PATH, "model.py"),
            device=device,
            disable_update=True
        )
        logger.info("Fun-ASR-Nano 模型加载成功（已集成 VAD）!")
        
        logger.info("加载标点恢复模型...")
        self.punc_model = AutoModel(model="ct-punc", device=device, disable_update=True)
        
        self._loaded = True
        logger.info("所有模型加载完成!")
    
    def transcribe(self, audio_input, **kwargs):
        """
        使用集成 VAD 的 ASR 模型进行转写
        VAD 会自动过滤无语音段，减少幻觉输出
        """
        try:
            if isinstance(audio_input, str):
                # 文件路径
                result = self.asr_model.generate(input=[audio_input], cache={}, batch_size=1)
            else:
                # numpy 数组，需要保存为临时文件
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                    sf.write(f.name, audio_input, Config.SAMPLE_RATE)
                    result = self.asr_model.generate(input=[f.name], cache={}, batch_size=1)
                    os.unlink(f.name)
            
            if result and len(result) > 0:
                text = result[0].get('text', '')
                return text if text else ''
        except Exception as e:
            logger.error(f"转写错误: {e}")
        return ''
    
    @property
    def vad_model(self):
        """兼容性属性，VAD 已集成到 asr_model 中"""
        return None


models = ASRModels()

# 全局唤醒词配置
keyword_config = {
    "keywords": Config.DEFAULT_KEYWORDS.copy(),
    "threshold": Config.KWS_THRESHOLD,
    "enabled": True
}


# ==================== 辅助函数 ====================
def decode_audio_base64(audio_base64: str, audio_format: str = "pcm") -> np.ndarray:
    audio_bytes = base64.b64decode(audio_base64)
    if audio_format == "pcm":
        audio_data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    elif audio_format in ["wav", "mp3", "flac"]:
        audio_data, _ = sf.read(BytesIO(audio_bytes), dtype='float32')
        if len(audio_data.shape) > 1:
            audio_data = audio_data.mean(axis=1)
    else:
        audio_data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    return audio_data


def check_audio_energy(audio_data: np.ndarray, threshold: float = 0.02) -> bool:
    """检查音频是否有足够的能量（是否有语音）
    
    Args:
        audio_data: 音频数据（float32，范围 -1 到 1）
        threshold: RMS 能量阈值，默认 0.02（提高以过滤噪音）
    
    Returns:
        是否有足够能量
    """
    if len(audio_data) == 0:
        return False
    # 计算RMS能量
    rms = np.sqrt(np.mean(audio_data ** 2))
    # 同时检查峰值，避免极端情况
    peak = np.max(np.abs(audio_data))
    # 需要同时满足 RMS 阈值和峰值阈值
    return rms > threshold and peak > threshold * 3


def is_repetitive_text(text: str, min_repeat: int = 3) -> bool:
    """检测文本是否有大量重复"""
    if not text or len(text) < 10:
        return False
    
    # 检查连续重复的短语
    for phrase_len in range(2, 8):
        for i in range(len(text) - phrase_len * min_repeat):
            phrase = text[i:i+phrase_len]
            count = 0
            pos = i
            while pos < len(text):
                if text[pos:pos+phrase_len] == phrase:
                    count += 1
                    pos += phrase_len
                else:
                    break
            if count >= min_repeat:
                return True
    
    return False


def filter_repetitive_text(text: str) -> str:
    """过滤掉重复的部分，只保留有意义的内容"""
    if not text:
        return text
    
    # 如果检测到重复，尝试提取有意义的部分
    if is_repetitive_text(text):
        # 找到重复开始的位置
        for phrase_len in range(2, 8):
            for i in range(len(text) - phrase_len * 3):
                phrase = text[i:i+phrase_len]
                if text.count(phrase) >= 5:
                    # 返回重复前的部分
                    if i > 0:
                        return text[:i].strip()
                    return ""
    
    return text


# ASR 模型在无声时常见的幻觉输出
ASR_HALLUCINATION_PATTERNS = [
    "对",
    "对。",
    "嗯",
    "嗯。",
    "啊",
    "啊。",
    "哦",
    "哦。",
    "好",
    "好的",
    "是",
    "是的",
    "好的。",
    "是的。",
    "嗯嗯",
    "嗯嗯。",
    "对对",
    "对对对",
    "好好好",
    "行",
    "行。",
    "谢谢",
    "谢谢。",
]


def is_asr_hallucination(text: str) -> bool:
    """检测文本是否是 ASR 模型在无声时产生的幻觉输出
    
    这些通常是短语、应答词，在没有实际说话时模型可能误识别
    """
    if not text:
        return True
    
    text_clean = text.strip()
    
    # 检查是否为空或太短
    if len(text_clean) <= 1:
        return True
    
    # 检查是否在已知幻觉列表中
    if text_clean in ASR_HALLUCINATION_PATTERNS:
        return True
    
    # 检查是否只有标点符号
    import re
    if re.match(r'^[。，、！？；：""''（）【】…—\s]+$', text_clean):
        return True
    
    # 检查是否只是单个字符重复
    if len(set(text_clean.replace('。', '').replace('，', ''))) <= 1:
        return True
    
    return False


def check_keyword_in_text(text: str, keywords: List[str], threshold: float = 0.6) -> Optional[dict]:
    """检测文本中是否包含关键词（支持模糊匹配）"""
    if not text:
        return None
    
    text_clean = text.replace(" ", "").replace("，", "").replace("。", "").lower()
    
    for keyword in keywords:
        keyword_clean = keyword.replace(" ", "").lower()
        
        # 精确匹配
        if keyword_clean in text_clean:
            logger.info(f"🎯 精确匹配唤醒词: {keyword} in '{text}'")
            return {"detected": True, "keyword": keyword, "confidence": 1.0, "text": text}
        
        # 模糊匹配 - 检查相似度
        for i in range(len(text_clean) - len(keyword_clean) + 1):
            substr = text_clean[i:i+len(keyword_clean)]
            ratio = difflib.SequenceMatcher(None, keyword_clean, substr).ratio()
            if ratio >= threshold:
                logger.info(f"🎯 模糊匹配唤醒词: {keyword} ~ '{substr}' (相似度: {ratio:.2f})")
                return {"detected": True, "keyword": keyword, "confidence": ratio, "text": text}
    
    return None


def make_response(success: bool, code: int, message: str, data: dict = None) -> dict:
    return {
        "success": success,
        "code": code,
        "message": message,
        "data": data or {},
        "timestamp": time.time()
    }


# ==================== FastAPI 应用 ====================
app = FastAPI(
    title="Fun-ASR-Nano 语音识别 API",
    description="基于 Fun-ASR-Nano (0.8B参数) 的中文实时语音识别服务",
    version="2.2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== Web前端页面 ====================
WEB_HTML = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Fun-ASR-Nano 实时语音识别</title>
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary: #6366f1;
            --primary-dark: #4f46e5;
            --secondary: #22d3ee;
            --bg-dark: #0f172a;
            --bg-card: #1e293b;
            --text-primary: #f1f5f9;
            --text-secondary: #94a3b8;
            --success: #22c55e;
            --warning: #f59e0b;
            --error: #ef4444;
            --border: #334155;
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: 'Noto Sans SC', sans-serif;
            background: linear-gradient(135deg, var(--bg-dark) 0%, #1a1a2e 50%, #16213e 100%);
            min-height: 100vh;
            color: var(--text-primary);
        }
        
        .layout {
            display: grid;
            grid-template-columns: 1fr 380px;
            min-height: 100vh;
            gap: 0;
        }
        
        .main-panel {
            padding: 1.5rem;
            overflow-y: auto;
        }
        
        .side-panel {
            background: var(--bg-card);
            border-left: 1px solid var(--border);
            display: flex;
            flex-direction: column;
        }
        
        .side-header {
            padding: 1rem 1.25rem;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .side-header h3 {
            font-size: 0.95rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        
        .transcript-list {
            flex: 1;
            overflow-y: auto;
            padding: 0.75rem;
        }
        
        .transcript-item {
            padding: 0.75rem;
            background: rgba(0,0,0,0.2);
            border-radius: 8px;
            margin-bottom: 0.5rem;
            border-left: 3px solid var(--primary);
            animation: fadeIn 0.3s;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(-5px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .transcript-item.has-keyword {
            border-left-color: var(--warning);
            background: rgba(245, 158, 11, 0.1);
        }
        
        .transcript-item.interim {
            border-left-color: var(--text-secondary);
            opacity: 0.7;
        }
        
        .transcript-time {
            font-size: 0.7rem;
            color: var(--text-secondary);
            margin-bottom: 0.3rem;
        }
        
        .transcript-text {
            font-size: 0.9rem;
            line-height: 1.5;
        }
        
        .keyword-badge {
            display: inline-block;
            background: var(--warning);
            color: #000;
            padding: 0.1rem 0.4rem;
            border-radius: 4px;
            font-size: 0.65rem;
            margin-left: 0.4rem;
        }
        
        .side-footer {
            padding: 0.75rem;
            border-top: 1px solid var(--border);
            background: rgba(0,0,0,0.2);
        }
        
        .full-text-box {
            background: rgba(0,0,0,0.3);
            border-radius: 6px;
            padding: 0.75rem;
            max-height: 120px;
            overflow-y: auto;
            font-size: 0.8rem;
            line-height: 1.5;
        }
        
        .full-text-label {
            font-size: 0.7rem;
            color: var(--text-secondary);
            margin-bottom: 0.4rem;
            display: flex;
            justify-content: space-between;
        }
        
        .char-count {
            color: var(--secondary);
        }
        
        header {
            text-align: center;
            padding: 1rem 0;
            position: relative;
        }
        
        h1 {
            font-size: 1.75rem;
            font-weight: 700;
            background: linear-gradient(135deg, var(--primary) 0%, var(--secondary) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 0.2rem;
        }
        
        .subtitle { color: var(--text-secondary); font-size: 0.85rem; }
        
        .model-badge {
            display: inline-block;
            background: var(--bg-card);
            border: 1px solid var(--border);
            padding: 0.3rem 0.6rem;
            border-radius: 15px;
            font-size: 0.75rem;
            margin-top: 0.5rem;
            color: var(--secondary);
        }
        
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 1.25rem;
            margin-bottom: 1rem;
        }
        
        .mode-switch {
            display: flex;
            gap: 0.4rem;
            margin-bottom: 1rem;
        }
        
        .mode-btn {
            flex: 1;
            padding: 0.6rem;
            border: 1px solid var(--border);
            background: transparent;
            color: var(--text-secondary);
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.3s;
            font-size: 0.85rem;
        }
        
        .mode-btn.active {
            background: var(--primary);
            border-color: var(--primary);
            color: white;
        }
        
        .mode-btn:hover:not(.active) {
            border-color: var(--primary);
            color: var(--text-primary);
        }
        
        .status-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 0.75rem;
            padding: 0.6rem;
            background: rgba(0,0,0,0.2);
            border-radius: 6px;
        }
        
        .status {
            display: flex;
            align-items: center;
            gap: 0.4rem;
        }
        
        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--text-secondary);
            transition: all 0.3s;
        }
        
        .status-dot.connected { background: var(--success); box-shadow: 0 0 8px var(--success); }
        .status-dot.recording { background: var(--error); box-shadow: 0 0 8px var(--error); animation: pulse 1s infinite; }
        .status-dot.listening { background: var(--warning); box-shadow: 0 0 8px var(--warning); animation: pulse 2s infinite; }
        
        @keyframes pulse {
            0%, 100% { transform: scale(1); opacity: 1; }
            50% { transform: scale(1.2); opacity: 0.8; }
        }
        
        .controls {
            display: flex;
            gap: 0.6rem;
            justify-content: center;
            margin-bottom: 0.75rem;
        }
        
        .btn {
            padding: 0.75rem 1.25rem;
            border: none;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.3s;
            display: flex;
            align-items: center;
            gap: 0.4rem;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
            color: white;
        }
        
        .btn-warning {
            background: linear-gradient(135deg, var(--warning) 0%, #d97706 100%);
            color: white;
        }
        
        .btn-danger {
            background: linear-gradient(135deg, var(--error) 0%, #dc2626 100%);
            color: white;
        }
        
        .btn-secondary {
            background: var(--bg-card);
            border: 1px solid var(--border);
            color: var(--text-primary);
        }
        
        .btn:hover:not(:disabled) {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }
        
        .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        
        .visualizer {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 2px;
            height: 45px;
            margin-bottom: 0.75rem;
            padding: 0.4rem;
            background: rgba(0,0,0,0.2);
            border-radius: 6px;
        }
        
        .bar {
            width: 3px;
            background: var(--primary);
            border-radius: 2px;
            transition: height 0.05s;
        }
        
        .subtitle-box {
            background: rgba(0,0,0,0.3);
            border-radius: 8px;
            padding: 1rem;
            min-height: 100px;
            position: relative;
        }
        
        .subtitle-label {
            position: absolute;
            top: -8px;
            left: 12px;
            background: var(--bg-card);
            padding: 0 0.4rem;
            font-size: 0.75rem;
            color: var(--text-secondary);
        }
        
        #subtitle, #wakeSubtitle {
            font-size: 1.1rem;
            line-height: 1.6;
            color: var(--text-primary);
        }
        
        .interim { color: var(--text-secondary) !important; font-style: italic; }
        
        .keyword-section {
            margin-top: 0.75rem;
            padding-top: 0.75rem;
            border-top: 1px solid var(--border);
        }
        
        .keyword-section h4 {
            font-size: 0.8rem;
            color: var(--text-secondary);
            margin-bottom: 0.4rem;
        }
        
        .keyword-tags {
            display: flex;
            flex-wrap: wrap;
            gap: 0.4rem;
        }
        
        .keyword-tag {
            background: rgba(99,102,241,0.2);
            border: 1px solid var(--primary);
            padding: 0.2rem 0.6rem;
            border-radius: 15px;
            font-size: 0.75rem;
        }
        
        .upload-area {
            border: 2px dashed var(--border);
            border-radius: 8px;
            padding: 1.5rem;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .upload-area:hover {
            border-color: var(--primary);
            background: rgba(99,102,241,0.05);
        }
        
        .upload-area input { display: none; }
        
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        
        .alert {
            padding: 0.6rem 0.8rem;
            border-radius: 6px;
            margin-bottom: 0.75rem;
            display: none;
            animation: slideIn 0.3s;
            font-size: 0.85rem;
        }
        
        .alert.success { background: rgba(34,197,94,0.15); border: 1px solid var(--success); color: var(--success); }
        .alert.warning { background: rgba(245,158,11,0.15); border: 1px solid var(--warning); color: var(--warning); }
        .alert.show { display: block; }
        
        @keyframes slideIn {
            from { transform: translateY(-10px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }
        
        .wakeword-mode-info {
            background: rgba(245,158,11,0.1);
            border: 1px solid var(--warning);
            border-radius: 6px;
            padding: 0.75rem;
            margin-bottom: 0.75rem;
            font-size: 0.8rem;
        }
        
        .wakeword-mode-info h4 {
            color: var(--warning);
            margin-bottom: 0.4rem;
            display: flex;
            align-items: center;
            gap: 0.4rem;
            font-size: 0.85rem;
        }
        
        .copy-btn {
            background: transparent;
            border: 1px solid var(--border);
            color: var(--text-secondary);
            padding: 0.2rem 0.4rem;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.7rem;
        }
        
        .copy-btn:hover {
            border-color: var(--primary);
            color: var(--primary);
        }
        
        .clear-btn {
            background: transparent;
            border: 1px solid var(--error);
            color: var(--error);
            padding: 0.2rem 0.4rem;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.7rem;
            margin-left: 0.3rem;
        }
        
        .clear-btn:hover {
            background: var(--error);
            color: white;
        }
        
        @media (max-width: 900px) {
            .layout {
                grid-template-columns: 1fr;
            }
            .side-panel {
                border-left: none;
                border-top: 1px solid var(--border);
                max-height: 300px;
            }
        }
    </style>
</head>
<body>
    <div class="layout">
        <div class="main-panel">
            <header>
                <h1>🎤 Fun-ASR-Nano</h1>
                <p class="subtitle">端到端语音识别大模型 · 实时字幕</p>
                <div class="model-badge">⚡ Fun-ASR-Nano-2512 (0.8B参数)</div>
            </header>
            
            <div class="card">
                <div class="mode-switch">
                    <button class="mode-btn active" data-mode="normal" onclick="setMode('normal')">
                        🎙️ 普通录音
                    </button>
                    <button class="mode-btn" data-mode="wakeword" onclick="setMode('wakeword')">
                        👂 唤醒词模式
                    </button>
                    <button class="mode-btn" data-mode="upload" onclick="setMode('upload')">
                        📁 文件上传
                    </button>
                </div>
                
                <div id="alert" class="alert success"></div>
                
                <!-- 普通录音模式 -->
                <div id="mode-normal" class="tab-content active">
                    <div class="status-bar">
                        <div class="status">
                            <div id="statusDot" class="status-dot"></div>
                            <span id="statusText">未连接</span>
                        </div>
                        <div id="duration">00:00</div>
                    </div>
                    
                    <div class="visualizer" id="visualizer"></div>
                    
                    <div class="controls">
                        <button id="startBtn" class="btn btn-primary" onclick="startRecording()">
                            🎤 开始录音
                        </button>
                        <button id="stopBtn" class="btn btn-danger" onclick="stopRecording()" disabled>
                            ⏹️ 停止录音
                        </button>
                    </div>
                    
                    <div class="subtitle-box">
                        <span class="subtitle-label">实时字幕</span>
                        <div id="subtitle">点击"开始录音"开始语音识别...</div>
                    </div>
                </div>
                
                <!-- 唤醒词模式 -->
                <div id="mode-wakeword" class="tab-content">
                    <div class="wakeword-mode-info">
                        <h4>👂 唤醒词监听模式</h4>
                        <p>开启后持续监听麦克风，说出唤醒词（如"你好小助"、"开始识别"）后自动开始录音。<br>
                        检测到唤醒词会有提示音，3秒静音后自动回到监听状态。</p>
                    </div>
                    
                    <div class="status-bar">
                        <div class="status">
                            <div id="wakeStatusDot" class="status-dot"></div>
                            <span id="wakeStatusText">未启动</span>
                        </div>
                        <div id="wakeDuration">00:00</div>
                    </div>
                    
                    <div class="visualizer" id="wakeVisualizer"></div>
                    
                    <div class="controls">
                        <button id="startWakeBtn" class="btn btn-warning" onclick="startWakewordMode()">
                            👂 开始监听
                        </button>
                        <button id="stopWakeBtn" class="btn btn-danger" onclick="stopWakewordMode()" disabled>
                            ⏹️ 停止监听
                        </button>
                    </div>
                    
                    <div class="subtitle-box">
                        <span class="subtitle-label">识别结果</span>
                        <div id="wakeSubtitle">说出唤醒词开始识别...</div>
                    </div>
                    
                    <div class="keyword-section">
                        <h4>🎯 当前唤醒词</h4>
                        <div class="keyword-tags" id="keywordTags"></div>
                    </div>
                </div>
                
                <!-- 文件上传 -->
                <div id="mode-upload" class="tab-content">
                    <div class="upload-area" onclick="document.getElementById('fileInput').click()">
                        <input type="file" id="fileInput" accept="audio/*" onchange="uploadFile(this)">
                        <p style="font-size:2rem;margin-bottom:0.5rem;">📤</p>
                        <p>点击或拖拽音频文件到此处</p>
                        <p style="color:var(--text-secondary);font-size:0.75rem;margin-top:0.4rem;">支持 WAV, MP3, FLAC 格式</p>
                    </div>
                    
                    <div id="uploadResult" style="margin-top:0.75rem;display:none;">
                        <div class="subtitle-box">
                            <span class="subtitle-label">识别结果</span>
                            <div id="uploadText"></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- 右侧面板 - 完整记录 -->
        <div class="side-panel">
            <div class="side-header">
                <h3>📝 识别记录</h3>
                <div>
                    <button class="copy-btn" onclick="copyFullText()">复制</button>
                    <button class="clear-btn" onclick="clearTranscripts()">清空</button>
                </div>
            </div>
            
            <div class="transcript-list" id="transcriptList">
                <div style="color:var(--text-secondary);text-align:center;padding:1.5rem;font-size:0.85rem;">
                    开始录音后，识别结果将显示在这里
                </div>
            </div>
            
            <div class="side-footer">
                <div class="full-text-label">
                    <span>完整文本</span>
                    <span class="char-count" id="charCount">0 字</span>
                </div>
                <div class="full-text-box" id="fullText">-</div>
            </div>
        </div>
    </div>
    
    <script>
        // 状态
        let ws = null;
        let mediaRecorder = null;
        let audioContext = null;
        let analyser = null;
        let processor = null;
        let mediaStream = null;
        let isRecording = false;
        let startTime = null;
        let durationInterval = null;
        let currentMode = 'normal';
        let transcripts = [];
        let fullText = '';
        
        // 唤醒词模式状态
        let isWakewordMode = false;
        let isActivated = false;
        
        // 创建音频可视化
        function createVisualizer(containerId) {
            const container = document.getElementById(containerId);
            container.innerHTML = '';
            for (let i = 0; i < 35; i++) {
                const bar = document.createElement('div');
                bar.className = 'bar';
                bar.style.height = '4px';
                container.appendChild(bar);
            }
        }
        createVisualizer('visualizer');
        createVisualizer('wakeVisualizer');
        
        // 模式切换
        function setMode(mode) {
            if (isRecording) stopRecording();
            if (isWakewordMode) stopWakewordMode();
            
            currentMode = mode;
            document.querySelectorAll('.mode-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.mode === mode);
            });
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.toggle('active', content.id === 'mode-' + mode);
            });
        }
        
        // 更新可视化
        function updateVisualizer(containerId) {
            if (!analyser) return;
            
            const dataArray = new Uint8Array(analyser.frequencyBinCount);
            analyser.getByteFrequencyData(dataArray);
            
            const bars = document.querySelectorAll(`#${containerId} .bar`);
            const step = Math.floor(dataArray.length / bars.length);
            
            bars.forEach((bar, i) => {
                const value = dataArray[i * step];
                const height = Math.max(4, (value / 255) * 45);
                bar.style.height = height + 'px';
            });
            
            if (isRecording || isWakewordMode) {
                requestAnimationFrame(() => updateVisualizer(containerId));
            }
        }
        
        // 更新时长
        function updateDuration(elementId) {
            if (!startTime) return;
            const elapsed = Math.floor((Date.now() - startTime) / 1000);
            const mins = Math.floor(elapsed / 60).toString().padStart(2, '0');
            const secs = (elapsed % 60).toString().padStart(2, '0');
            document.getElementById(elementId).textContent = `${mins}:${secs}`;
        }
        
        // 显示提示
        function showAlert(msg, type = 'success') {
            const alert = document.getElementById('alert');
            alert.textContent = msg;
            alert.className = `alert ${type} show`;
            setTimeout(() => alert.classList.remove('show'), 3000);
        }
        
        // 添加到记录
        function addTranscript(text, isFinal, keywordDetected) {
            if (!text || !text.trim()) return;
            
            const item = {
                text: text,
                time: new Date().toLocaleTimeString(),
                isFinal: isFinal,
                keyword: keywordDetected
            };
            
            transcripts.push(item);
            if (isFinal) {
                fullText += text;
            }
            updateTranscriptUI();
        }
        
        // 更新记录UI
        function updateTranscriptUI() {
            const list = document.getElementById('transcriptList');
            const fullTextEl = document.getElementById('fullText');
            const charCountEl = document.getElementById('charCount');
            
            if (transcripts.length === 0) {
                list.innerHTML = '<div style="color:var(--text-secondary);text-align:center;padding:1.5rem;font-size:0.85rem;">开始录音后，识别结果将显示在这里</div>';
                fullTextEl.textContent = '-';
                charCountEl.textContent = '0 字';
                return;
            }
            
            // 只显示最近20条
            const recentTranscripts = transcripts.slice(-20);
            list.innerHTML = recentTranscripts.map((item, i) => `
                <div class="transcript-item ${item.keyword ? 'has-keyword' : ''} ${!item.isFinal ? 'interim' : ''}">
                    <div class="transcript-time">
                        ${item.time}
                        ${item.keyword ? `<span class="keyword-badge">🎯 ${item.keyword.keyword}</span>` : ''}
                        ${!item.isFinal ? '<span style="color:var(--warning);font-size:0.65rem;margin-left:0.3rem;">(识别中)</span>' : ''}
                    </div>
                    <div class="transcript-text">${item.text}</div>
                </div>
            `).join('');
            
            list.scrollTop = list.scrollHeight;
            fullTextEl.textContent = fullText || '-';
            charCountEl.textContent = fullText.length + ' 字';
        }
        
        // 清空记录
        function clearTranscripts() {
            transcripts = [];
            fullText = '';
            updateTranscriptUI();
            // 同时清空后端
            fetch('/api/history', { method: 'DELETE' });
        }
        
        // 复制全部文本
        function copyFullText() {
            if (fullText) {
                navigator.clipboard.writeText(fullText);
                showAlert('已复制到剪贴板');
            }
        }
        
        // 播放提示音
        function playBeep() {
            const ctx = new AudioContext();
            const oscillator = ctx.createOscillator();
            const gain = ctx.createGain();
            oscillator.connect(gain);
            gain.connect(ctx.destination);
            oscillator.frequency.value = 880;
            gain.gain.setValueAtTime(0.3, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.15);
            oscillator.start(ctx.currentTime);
            oscillator.stop(ctx.currentTime + 0.15);
        }
        
        // ==================== 普通录音模式 ====================
        async function startRecording() {
            try {
                mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                
                audioContext = new AudioContext({ sampleRate: 16000 });
                const source = audioContext.createMediaStreamSource(mediaStream);
                analyser = audioContext.createAnalyser();
                analyser.fftSize = 256;
                source.connect(analyser);
                
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                ws = new WebSocket(`${protocol}//${window.location.host}/ws/stream`);
                
                ws.onopen = () => {
                    updateStatus('connected', '已连接');
                    ws.send(JSON.stringify({ action: 'start', config: {} }));
                };
                
                ws.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    console.log('WS received:', data);
                    
                    if (data.type === 'result') {
                        const text = data.data.text;
                        const isFinal = data.data.is_final;
                        const kw = data.data.keyword_detected;
                        
                        if (text) {
                            document.getElementById('subtitle').textContent = text;
                            document.getElementById('subtitle').className = isFinal ? '' : 'interim';
                            
                            // 添加到记录
                            addTranscript(text, isFinal, kw);
                            
                            if (kw) {
                                showAlert(`🎯 检测到唤醒词: ${kw.keyword}`, 'warning');
                            }
                        }
                    }
                };
                
                ws.onerror = (e) => {
                    console.error('WS error:', e);
                    updateStatus('', '连接错误');
                };
                ws.onclose = () => updateStatus('', '已断开');
                
                // 音频处理
                processor = audioContext.createScriptProcessor(4096, 1, 1);
                source.connect(processor);
                processor.connect(audioContext.destination);
                
                processor.onaudioprocess = (e) => {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        const inputData = e.inputBuffer.getChannelData(0);
                        const pcm = new Int16Array(inputData.length);
                        for (let i = 0; i < inputData.length; i++) {
                            pcm[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32768));
                        }
                        const base64 = btoa(String.fromCharCode(...new Uint8Array(pcm.buffer)));
                        ws.send(JSON.stringify({ action: 'audio', audio_data: base64 }));
                    }
                };
                
                isRecording = true;
                startTime = Date.now();
                durationInterval = setInterval(() => updateDuration('duration'), 1000);
                
                updateStatus('recording', '录音中');
                document.getElementById('startBtn').disabled = true;
                document.getElementById('stopBtn').disabled = false;
                document.getElementById('subtitle').textContent = '正在聆听...';
                
                updateVisualizer('visualizer');
                
            } catch (error) {
                console.error('Error:', error);
                showAlert('无法访问麦克风: ' + error.message, 'warning');
            }
        }
        
        function stopRecording() {
            isRecording = false;
            
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ action: 'stop' }));
                ws.close();
            }
            
            if (processor) {
                processor.disconnect();
                processor = null;
            }
            
            if (audioContext) {
                audioContext.close();
                audioContext = null;
            }
            
            if (mediaStream) {
                mediaStream.getTracks().forEach(track => track.stop());
                mediaStream = null;
            }
            
            if (durationInterval) {
                clearInterval(durationInterval);
            }
            
            updateStatus('', '已停止');
            document.getElementById('startBtn').disabled = false;
            document.getElementById('stopBtn').disabled = true;
            
            document.querySelectorAll('#visualizer .bar').forEach(bar => bar.style.height = '4px');
        }
        
        function updateStatus(status, text) {
            document.getElementById('statusDot').className = 'status-dot ' + status;
            document.getElementById('statusText').textContent = text;
        }
        
        // ==================== 唤醒词模式 ====================
        async function startWakewordMode() {
            try {
                mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                
                audioContext = new AudioContext({ sampleRate: 16000 });
                const source = audioContext.createMediaStreamSource(mediaStream);
                analyser = audioContext.createAnalyser();
                analyser.fftSize = 256;
                source.connect(analyser);
                
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                ws = new WebSocket(`${protocol}//${window.location.host}/ws/wakeword`);
                
                ws.onopen = () => {
                    updateWakeStatus('listening', '监听唤醒词中...');
                    ws.send(JSON.stringify({ action: 'start_listening' }));
                };
                
                ws.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    console.log('Wakeword WS received:', data);
                    
                    if (data.type === 'wakeword_detected') {
                        playBeep();
                        isActivated = true;
                        updateWakeStatus('recording', `已唤醒: ${data.data.keyword}`);
                        showAlert(`🎯 唤醒词触发: ${data.data.keyword}`, 'warning');
                        document.getElementById('wakeSubtitle').textContent = '请说话...';
                        addTranscript(`[唤醒词: ${data.data.keyword}]`, true, data.data);
                    }
                    else if (data.type === 'result') {
                        const text = data.data.text;
                        const isFinal = data.data.is_final;
                        
                        if (text) {
                            document.getElementById('wakeSubtitle').textContent = text;
                            addTranscript(text, isFinal, null);
                        }
                    }
                    else if (data.type === 'listening_text') {
                        // 监听模式下的识别文本（用于调试/显示）
                        const text = data.data.text;
                        if (text) {
                            document.getElementById('wakeSubtitle').textContent = `监听中: ${text}`;
                        }
                    }
                    else if (data.type === 'back_to_listening') {
                        isActivated = false;
                        updateWakeStatus('listening', '监听唤醒词中...');
                        document.getElementById('wakeSubtitle').textContent = '说出唤醒词开始识别...';
                    }
                };
                
                ws.onerror = (e) => {
                    console.error('Wakeword WS error:', e);
                    updateWakeStatus('', '连接错误');
                };
                ws.onclose = () => updateWakeStatus('', '已断开');
                
                // 音频处理
                processor = audioContext.createScriptProcessor(4096, 1, 1);
                source.connect(processor);
                processor.connect(audioContext.destination);
                
                processor.onaudioprocess = (e) => {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        const inputData = e.inputBuffer.getChannelData(0);
                        const pcm = new Int16Array(inputData.length);
                        for (let i = 0; i < inputData.length; i++) {
                            pcm[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32768));
                        }
                        const base64 = btoa(String.fromCharCode(...new Uint8Array(pcm.buffer)));
                        ws.send(JSON.stringify({ action: 'audio', audio_data: base64 }));
                    }
                };
                
                isWakewordMode = true;
                startTime = Date.now();
                durationInterval = setInterval(() => updateDuration('wakeDuration'), 1000);
                
                document.getElementById('startWakeBtn').disabled = true;
                document.getElementById('stopWakeBtn').disabled = false;
                
                updateVisualizer('wakeVisualizer');
                
            } catch (error) {
                console.error('Error:', error);
                showAlert('无法访问麦克风: ' + error.message, 'warning');
            }
        }
        
        function stopWakewordMode() {
            isWakewordMode = false;
            isActivated = false;
            
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ action: 'stop' }));
                ws.close();
            }
            
            if (processor) {
                processor.disconnect();
                processor = null;
            }
            
            if (audioContext) {
                audioContext.close();
                audioContext = null;
            }
            
            if (mediaStream) {
                mediaStream.getTracks().forEach(track => track.stop());
                mediaStream = null;
            }
            
            if (durationInterval) {
                clearInterval(durationInterval);
            }
            
            updateWakeStatus('', '已停止');
            document.getElementById('startWakeBtn').disabled = false;
            document.getElementById('stopWakeBtn').disabled = true;
            
            document.querySelectorAll('#wakeVisualizer .bar').forEach(bar => bar.style.height = '4px');
        }
        
        function updateWakeStatus(status, text) {
            document.getElementById('wakeStatusDot').className = 'status-dot ' + status;
            document.getElementById('wakeStatusText').textContent = text;
        }
        
        // ==================== 文件上传 ====================
        async function uploadFile(input) {
            const file = input.files[0];
            if (!file) return;
            
            const formData = new FormData();
            formData.append('file', file);
            
            document.getElementById('uploadResult').style.display = 'block';
            document.getElementById('uploadText').textContent = '正在识别...';
            
            try {
                const response = await fetch('/api/transcribe/file', {
                    method: 'POST',
                    body: formData
                });
                
                const result = await response.json();
                
                if (result.success) {
                    const text = result.data.text || '(无法识别)';
                    document.getElementById('uploadText').textContent = text;
                    addTranscript(text, true, result.data.keyword_detected);
                    showAlert('✅ 识别完成');
                } else {
                    document.getElementById('uploadText').textContent = '识别失败: ' + result.message;
                }
            } catch (error) {
                document.getElementById('uploadText').textContent = '上传失败';
            }
            
            input.value = '';
        }
        
        // 加载唤醒词配置
        async function loadKeywords() {
            try {
                const response = await fetch('/api/keyword/config');
                const result = await response.json();
                if (result.success) {
                    const tags = document.getElementById('keywordTags');
                    tags.innerHTML = result.data.keywords.map(kw => 
                        `<span class="keyword-tag">${kw}</span>`
                    ).join('');
                }
            } catch (e) {
                console.error('Failed to load keywords:', e);
            }
        }
        loadKeywords();
        
        // 清理
        window.addEventListener('beforeunload', () => {
            if (ws) ws.close();
            if (audioContext) audioContext.close();
            if (mediaStream) mediaStream.getTracks().forEach(track => track.stop());
        });
    </script>
</body>
</html>
'''


# ==================== 启动事件 ====================
@app.on_event("startup")
async def startup_event():
    logger.info("正在初始化 Fun-ASR-Nano 服务...")
    models.load_models(Config.DEVICE)
    logger.info(f"服务已启动: http://{Config.HOST}:{Config.PORT}")


# ==================== HTTP API ====================
@app.get("/", response_class=HTMLResponse)
async def root():
    return WEB_HTML


@app.get("/health")
async def health_check():
    return make_response(True, 0, "healthy", {
        "models_loaded": models._loaded,
        "device": Config.DEVICE,
        "model": "Fun-ASR-Nano-2512 (0.8B)"
    })


@app.post("/api/transcribe")
async def transcribe_json(audio_input: AudioInput):
    if not models._loaded:
        return make_response(False, 503, "模型尚未加载完成")
    
    start_time = time.time()
    try:
        audio_data = decode_audio_base64(audio_input.audio_data, audio_input.format)
        duration = len(audio_data) / audio_input.sample_rate
        
        text = ""
        kw_result = None
        
        # 1. 检查音频能量是否足够（过滤静音/噪音）
        if not check_audio_energy(audio_data, threshold=0.02):
            logger.debug("音频能量不足，跳过识别")
            return make_response(True, 0, "success", {
                "text": "",
                "duration": duration,
                "processing_time": time.time() - start_time,
                "keyword_detected": None,
                "skipped": "low_energy"
            })
        
        # 2. 执行语音识别（VAD 已集成到模型中，会自动过滤无语音段）
        text = models.transcribe(audio_data)
        
        # 3. 过滤重复文本
        if text and is_repetitive_text(text):
            text = filter_repetitive_text(text)
        
        # 4. 过滤 ASR 幻觉（如 "对。"）
        if text and is_asr_hallucination(text):
            logger.debug(f"过滤 ASR 幻觉: '{text}'")
            text = ""
        
        processing_time = time.time() - start_time
        
        # 5. 检测唤醒词
        if text and keyword_config["enabled"]:
            kw_result = check_keyword_in_text(text, keyword_config["keywords"], keyword_config["threshold"])
        
        # 只有有效文本才记录
        if text:
            transcript_store.add_transcript(text, True, kw_result)
        
        return make_response(True, 0, "success", {
            "text": text,
            "duration": duration,
            "processing_time": processing_time,
            "keyword_detected": kw_result
        })
    except Exception as e:
        logger.error(f"转写错误: {str(e)}")
        return make_response(False, 500, f"转写失败: {str(e)}")


@app.post("/api/transcribe/file")
async def transcribe_file(file: UploadFile = File(...)):
    if not models._loaded:
        return make_response(False, 503, "模型尚未加载完成")
    
    start_time = time.time()
    try:
        content = await file.read()
        
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(file.filename)[1], delete=False) as tmp_file:
            tmp_file.write(content)
            tmp_path = tmp_file.name
        
        try:
            text = models.transcribe(tmp_path)
            processing_time = time.time() - start_time
            
            try:
                audio_info = sf.info(tmp_path)
                duration = audio_info.duration
            except:
                duration = 0.0
            
            kw_result = None
            if keyword_config["enabled"]:
                kw_result = check_keyword_in_text(text, keyword_config["keywords"], keyword_config["threshold"])
            
            transcript_store.add_transcript(text, True, kw_result)
            
            return make_response(True, 0, "success", {
                "text": text,
                "duration": duration,
                "processing_time": processing_time,
                "keyword_detected": kw_result
            })
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
    except Exception as e:
        logger.error(f"转写错误: {str(e)}")
        return make_response(False, 500, f"转写失败: {str(e)}")


@app.get("/api/keyword/config")
async def get_keyword_config():
    return make_response(True, 0, "success", keyword_config)


@app.post("/api/keyword/config")
async def set_keyword_config(config: KeywordConfig):
    global keyword_config
    if config.keywords:
        keyword_config["keywords"] = config.keywords
    if config.threshold:
        keyword_config["threshold"] = config.threshold
    return make_response(True, 0, "success", keyword_config)


@app.get("/api/history")
async def get_history(limit: int = 50):
    return make_response(True, 0, "success", {
        "history": transcript_store.get_history(limit),
        "session_transcript": transcript_store.get_session_transcript(),
        "full_text": transcript_store.get_full_text()
    })


@app.delete("/api/history")
async def clear_history():
    transcript_store.clear_history()
    return make_response(True, 0, "success", {"message": "历史已清空"})


# ==================== WebSocket 普通流式识别 ====================
@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket连接已建立")
    
    if not models._loaded:
        await websocket.send_json(make_response(False, 503, "模型尚未加载完成"))
        await websocket.close()
        return
    
    audio_buffer = np.array([], dtype=np.float32)
    buffer_duration = 2.0
    buffer_samples = int(Config.SAMPLE_RATE * buffer_duration)
    last_text = ""
    segment_count = 0
    
    transcript_store.start_session()
    
    try:
        while True:
            data = await websocket.receive()
            
            if "text" in data:
                try:
                    message = json.loads(data["text"])
                    action = message.get("action", "")
                    
                    if action == "start":
                        audio_buffer = np.array([], dtype=np.float32)
                        last_text = ""
                        segment_count = 0
                        await websocket.send_json({
                            "type": "event",
                            "data": {"event": "session_started"},
                            "timestamp": time.time()
                        })
                        
                    elif action == "audio":
                        audio_base64 = message.get("audio_data", "")
                        if audio_base64:
                            audio_data = decode_audio_base64(audio_base64)
                            audio_buffer = np.concatenate([audio_buffer, audio_data])
                            
                            if len(audio_buffer) >= buffer_samples:
                                # 检查是否有足够的音频能量（过滤静音/噪音）
                                if check_audio_energy(audio_buffer, threshold=0.02):
                                    # 使用集成 VAD 的模型进行识别
                                    text = models.transcribe(audio_buffer)
                                    
                                    # 过滤重复文本
                                    if text and is_repetitive_text(text):
                                        text = filter_repetitive_text(text)
                                    
                                    # 过滤 ASR 幻觉（如 "对。"）
                                    if text and is_asr_hallucination(text):
                                        text = ""
                                else:
                                    text = ""
                                    
                                if text and text != last_text and len(text) > 2:
                                        kw_result = None
                                        if keyword_config["enabled"]:
                                            kw_result = check_keyword_in_text(text, keyword_config["keywords"], keyword_config["threshold"])
                                        
                                        # 发送实时结果
                                        await websocket.send_json({
                                            "type": "result",
                                            "data": {
                                                "text": text,
                                                "is_final": False,
                                                "keyword_detected": kw_result,
                                                "segment": segment_count
                                            },
                                            "timestamp": time.time()
                                        })
                                        last_text = text
                                
                                # 保留最后1秒音频
                                audio_buffer = audio_buffer[-Config.SAMPLE_RATE:]
                    
                    elif action == "stop":
                        if len(audio_buffer) > Config.SAMPLE_RATE // 2:
                            text = models.transcribe(audio_buffer)
                            
                            if text:
                                try:
                                    punc_result = models.punc_model.generate(input=text)
                                    if punc_result and len(punc_result) > 0:
                                        text = punc_result[0].get('text', text)
                                except:
                                    pass
                                
                                kw_result = None
                                if keyword_config["enabled"]:
                                    kw_result = check_keyword_in_text(text, keyword_config["keywords"], keyword_config["threshold"])
                                
                                transcript_store.add_transcript(text, True, kw_result)
                                
                                await websocket.send_json({
                                    "type": "result",
                                    "data": {
                                        "text": text,
                                        "is_final": True,
                                        "keyword_detected": kw_result
                                    },
                                    "timestamp": time.time()
                                })
                        
                        await websocket.send_json({
                            "type": "event",
                            "data": {"event": "session_completed"},
                            "timestamp": time.time()
                        })
                        break
                except json.JSONDecodeError:
                    pass
    except WebSocketDisconnect:
        logger.info("WebSocket连接已断开")
    except Exception as e:
        logger.error(f"WebSocket错误: {str(e)}")


# ==================== WebSocket 唤醒词模式 ====================
@app.websocket("/ws/wakeword")
async def websocket_wakeword(websocket: WebSocket):
    """唤醒词监听模式：持续监听，检测到唤醒词后开始正式识别"""
    await websocket.accept()
    logger.info("唤醒词模式 WebSocket连接已建立")
    
    if not models._loaded:
        await websocket.send_json(make_response(False, 503, "模型尚未加载完成"))
        await websocket.close()
        return
    
    mode = "listening"
    audio_buffer = np.array([], dtype=np.float32)
    wakeword_buffer = np.array([], dtype=np.float32)
    
    wakeword_check_interval = 1.5
    wakeword_samples = int(Config.SAMPLE_RATE * wakeword_check_interval)
    
    active_buffer_duration = 2.0
    active_buffer_samples = int(Config.SAMPLE_RATE * active_buffer_duration)
    
    silence_timeout = 3.0
    last_speech_time = time.time()
    last_text = ""
    
    transcript_store.start_session()
    
    try:
        while True:
            data = await websocket.receive()
            
            if "text" in data:
                try:
                    message = json.loads(data["text"])
                    action = message.get("action", "")
                    
                    if action == "start_listening":
                        mode = "listening"
                        wakeword_buffer = np.array([], dtype=np.float32)
                        audio_buffer = np.array([], dtype=np.float32)
                        logger.info("开始监听唤醒词...")
                        await websocket.send_json({
                            "type": "event",
                            "data": {"event": "listening_started", "keywords": keyword_config["keywords"]},
                            "timestamp": time.time()
                        })
                        
                    elif action == "audio":
                        audio_base64 = message.get("audio_data", "")
                        if audio_base64:
                            audio_data = decode_audio_base64(audio_base64)
                            
                            if mode == "listening":
                                wakeword_buffer = np.concatenate([wakeword_buffer, audio_data])
                                
                                if len(wakeword_buffer) >= wakeword_samples:
                                    # 只有有足够音量时才识别
                                    if check_audio_energy(wakeword_buffer, threshold=0.02):
                                        # 使用集成 VAD 的模型进行识别
                                        text = models.transcribe(wakeword_buffer)
                                        
                                        # 过滤重复文本
                                        if text and is_repetitive_text(text):
                                            text = filter_repetitive_text(text)
                                        
                                        # 过滤 ASR 幻觉
                                        if text and is_asr_hallucination(text):
                                            text = ""
                                    else:
                                        text = ""
                                        
                                    if text and len(text) > 2:
                                            logger.info(f"监听识别: '{text}'")
                                            
                                            # 发送监听中的文本（调试用）
                                            await websocket.send_json({
                                                "type": "listening_text",
                                                "data": {"text": text},
                                                "timestamp": time.time()
                                            })
                                            
                                            kw_result = check_keyword_in_text(text, keyword_config["keywords"], keyword_config["threshold"])
                                            
                                            if kw_result:
                                                logger.info(f"🎯 检测到唤醒词: {kw_result}")
                                                mode = "active"
                                                audio_buffer = np.array([], dtype=np.float32)
                                                last_text = ""
                                                last_speech_time = time.time()
                                                
                                                await websocket.send_json({
                                                    "type": "wakeword_detected",
                                                    "data": kw_result,
                                                    "timestamp": time.time()
                                                })
                                    
                                    wakeword_buffer = wakeword_buffer[-int(Config.SAMPLE_RATE * 0.5):]
                            
                            else:  # active mode
                                audio_buffer = np.concatenate([audio_buffer, audio_data])
                                
                                if len(audio_buffer) >= active_buffer_samples:
                                    # 检查音量
                                    if check_audio_energy(audio_buffer, threshold=0.02):
                                        # 使用集成 VAD 的模型进行识别
                                        text = models.transcribe(audio_buffer)
                                        
                                        # 过滤重复文本
                                        if text and is_repetitive_text(text):
                                            text = filter_repetitive_text(text)
                                        
                                        # 过滤 ASR 幻觉
                                        if text and is_asr_hallucination(text):
                                            text = ""
                                    else:
                                        text = ""
                                    
                                    if text and text != last_text and len(text) > 2:
                                            last_speech_time = time.time()
                                            
                                            await websocket.send_json({
                                                "type": "result",
                                                "data": {
                                                    "text": text,
                                                    "is_final": False
                                                },
                                                "timestamp": time.time()
                                            })
                                            last_text = text
                                    
                                    audio_buffer = audio_buffer[-Config.SAMPLE_RATE:]
                                
                                # 检查静音超时
                                if time.time() - last_speech_time > silence_timeout:
                                    if last_text:
                                        try:
                                            punc_result = models.punc_model.generate(input=last_text)
                                            if punc_result and len(punc_result) > 0:
                                                final_text = punc_result[0].get('text', last_text)
                                            else:
                                                final_text = last_text
                                        except:
                                            final_text = last_text
                                        
                                        transcript_store.add_transcript(final_text, True, None)
                                        
                                        await websocket.send_json({
                                            "type": "result",
                                            "data": {
                                                "text": final_text,
                                                "is_final": True
                                            },
                                            "timestamp": time.time()
                                        })
                                    
                                    mode = "listening"
                                    wakeword_buffer = np.array([], dtype=np.float32)
                                    audio_buffer = np.array([], dtype=np.float32)
                                    last_text = ""
                                    
                                    logger.info("返回监听模式")
                                    await websocket.send_json({
                                        "type": "back_to_listening",
                                        "data": {"message": "返回唤醒词监听模式"},
                                        "timestamp": time.time()
                                    })
                    
                    elif action == "stop":
                        await websocket.send_json({
                            "type": "event",
                            "data": {"event": "session_completed"},
                            "timestamp": time.time()
                        })
                        break
                        
                except json.JSONDecodeError:
                    pass
                    
    except WebSocketDisconnect:
        logger.info("唤醒词模式 WebSocket连接已断开")
    except Exception as e:
        logger.error(f"唤醒词模式错误: {str(e)}")


# ==================== 主入口 ====================
def main():
    uvicorn.run(
        "server:app",
        host=Config.HOST,
        port=Config.PORT,
        reload=False,
        log_level="info"
    )


if __name__ == "__main__":
    main()
