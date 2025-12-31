#!/usr/bin/env python3
"""
WebRTC Video Stream Server

Provides WebRTC video streaming from a local video file.
Includes a built-in test page for browser testing.

Usage:
    python webrtc_server.py --video /path/to/video.mp4 --port 8088
    
Open http://localhost:8088 in browser to test.
"""

import argparse
import asyncio
import json
import logging
import time
import fractions
import os
from pathlib import Path
from typing import Optional, Set

import cv2
import numpy as np
from aiohttp import web

# Upload directory
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    from av import VideoFrame
    AIORTC_AVAILABLE = True
except ImportError:
    AIORTC_AVAILABLE = False
    print("Warning: aiortc not installed. Run: pip install aiortc")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Active peer connections
pcs: Set["RTCPeerConnection"] = set()


class VideoFileTrack(VideoStreamTrack if AIORTC_AVAILABLE else object):
    """
    WebRTC video track that reads from a video file.
    Streams frames in real-time as if it were a live camera.
    """
    
    kind = "video"
    
    def __init__(self, video_path: str, loop: bool = True, fps_override: Optional[float] = None):
        if AIORTC_AVAILABLE:
            super().__init__()
        
        self.video_path = video_path
        self.loop = loop
        
        # Open video
        self.cap = cv2.VideoCapture(video_path)
        if not self.cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")
        
        # Video properties
        self.original_fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps_override if fps_override else self.original_fps
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.duration = self.total_frames / self.original_fps if self.original_fps > 0 else 0
        
        logger.info(f"Video: {Path(video_path).name} ({self.width}x{self.height} @ {self.fps:.1f}fps)")
        
        # Timing
        self._start_time = None
        self._pts = 0
        self._frame_idx = 0
        self._time_base = fractions.Fraction(1, int(self.fps * 1000))
    
    def _read_frame(self) -> Optional[np.ndarray]:
        ret, frame = self.cap.read()
        if not ret:
            if self.loop:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self._frame_idx = 0
                ret, frame = self.cap.read()
                if not ret:
                    return None
            else:
                return None
        self._frame_idx += 1
        return frame
    
    async def recv(self):
        if not AIORTC_AVAILABLE:
            raise RuntimeError("aiortc not available")
        
        if self._start_time is None:
            self._start_time = time.time()
        
        # Timing control
        frame_time = self._pts / (self.fps * 1000)
        current_time = time.time() - self._start_time
        wait_time = frame_time - current_time
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        
        # Read frame
        bgr_frame = self._read_frame()
        if bgr_frame is None:
            bgr_frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        
        # Convert BGR to RGB
        rgb_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        
        # Create VideoFrame
        frame = VideoFrame.from_ndarray(rgb_frame, format="rgb24")
        frame.pts = self._pts
        frame.time_base = self._time_base
        
        self._pts += int(1000 / self.fps) * 1000
        
        return frame
    
    def stop(self):
        if self.cap:
            self.cap.release()


# Global state
video_path: str = ""


async def index(request: web.Request) -> web.Response:
    """Serve WebRTC test page"""
    
    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WebRTC Stream Test</title>
    <style>
        :root {
            --bg-dark: #0a0a0f;
            --bg-card: #12121a;
            --accent: #06b6d4;
            --accent2: #a78bfa;
            --accent-glow: rgba(6, 182, 212, 0.3);
            --success: #10b981;
            --error: #ef4444;
            --text: #f0f0f5;
            --muted: #6b6b80;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'SF Mono', 'JetBrains Mono', monospace;
            background: var(--bg-dark);
            color: var(--text);
            min-height: 100vh;
            padding: 2rem;
            background-image: 
                radial-gradient(circle at 30% 20%, rgba(6, 182, 212, 0.05) 0%, transparent 50%),
                radial-gradient(circle at 70% 80%, rgba(167, 139, 250, 0.05) 0%, transparent 50%);
        }
        h1 {
            font-size: 1.5rem;
            font-weight: 500;
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.5rem;
            text-align: center;
        }
        .subtitle { color: var(--muted); margin-bottom: 2rem; font-size: 0.9rem; text-align: center; }
        .container { max-width: 1100px; margin: 0 auto; }
        .main-grid {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 1.5rem;
        }
        .video-container {
            background: var(--bg-card);
            border-radius: 16px;
            padding: 1.5rem;
            box-shadow: 0 4px 40px rgba(0, 0, 0, 0.4);
            border: 1px solid rgba(255, 255, 255, 0.05);
        }
        video {
            width: 100%;
            aspect-ratio: 16/9;
            background: #000;
            border-radius: 12px;
        }
        .controls {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-top: 1rem;
            padding-top: 1rem;
            border-top: 1px solid rgba(255, 255, 255, 0.1);
        }
        .status {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }
        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--muted);
            transition: all 0.3s;
        }
        .status-dot.connected { background: var(--success); box-shadow: 0 0 12px var(--success); }
        .status-dot.connecting { background: #f59e0b; animation: pulse 1s infinite; }
        .status-dot.error { background: var(--error); }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .buttons { display: flex; gap: 0.5rem; flex-wrap: wrap; }
        button {
            padding: 0.6rem 1rem;
            border: none;
            border-radius: 8px;
            font-family: inherit;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-primary {
            background: linear-gradient(135deg, var(--accent), #0891b2);
            color: #000;
            font-weight: 600;
        }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 4px 20px var(--accent-glow); }
        .btn-secondary {
            background: rgba(255, 255, 255, 0.05);
            color: var(--text);
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        .btn-secondary:hover { border-color: var(--accent); }
        button:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .sidebar {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }
        .panel {
            background: var(--bg-card);
            border-radius: 12px;
            padding: 1.25rem;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }
        .panel h2 {
            font-size: 0.8rem;
            color: var(--muted);
            margin-bottom: 1rem;
            font-weight: 400;
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }
        .upload-zone {
            border: 2px dashed rgba(255,255,255,0.15);
            border-radius: 8px;
            padding: 1.5rem;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s;
        }
        .upload-zone:hover {
            border-color: var(--accent);
            background: rgba(6, 182, 212, 0.05);
        }
        .upload-zone.dragover {
            border-color: var(--accent);
            background: rgba(6, 182, 212, 0.1);
        }
        .upload-icon { font-size: 2rem; margin-bottom: 0.5rem; }
        .upload-text { font-size: 0.85rem; color: var(--muted); }
        .upload-input { display: none; }
        .video-list { max-height: 180px; overflow-y: auto; }
        .video-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.6rem;
            border-radius: 6px;
            margin-bottom: 0.5rem;
            background: rgba(255,255,255,0.02);
            cursor: pointer;
            transition: all 0.2s;
            font-size: 0.85rem;
        }
        .video-item:hover { background: rgba(255,255,255,0.05); }
        .video-item.active {
            background: rgba(6, 182, 212, 0.15);
            border: 1px solid var(--accent);
        }
        .video-name {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            flex: 1;
        }
        .video-badge {
            font-size: 0.7rem;
            padding: 0.2rem 0.5rem;
            background: var(--accent);
            color: #000;
            border-radius: 4px;
            margin-left: 0.5rem;
        }
        .info-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 0.75rem;
        }
        .info-item { text-align: center; padding: 0.5rem; }
        .info-label { font-size: 0.7rem; color: var(--muted); margin-bottom: 0.25rem; }
        .info-value { font-size: 0.9rem; color: var(--accent); }
        .console {
            margin-top: 1.5rem;
            background: #000;
            border-radius: 8px;
            padding: 1rem;
            max-height: 120px;
            overflow-y: auto;
            font-size: 0.75rem;
        }
        .log-line { margin: 0.25rem 0; color: var(--muted); }
        .log-line.info { color: var(--accent); }
        .log-line.success { color: var(--success); }
        .log-line.error { color: var(--error); }
        .status-msg {
            margin-top: 0.75rem;
            font-size: 0.8rem;
            padding: 0.5rem;
            border-radius: 4px;
            text-align: center;
        }
        .status-msg.success { background: rgba(16, 185, 129, 0.2); color: var(--success); }
        .status-msg.error { background: rgba(239, 68, 68, 0.2); color: #ef4444; }
        .status-msg.info { background: rgba(6, 182, 212, 0.2); color: var(--accent); }
        .hidden { display: none; }
        .stream-url-box {
            background: linear-gradient(135deg, rgba(6, 182, 212, 0.1), rgba(167, 139, 250, 0.1));
            border: 1px solid var(--accent);
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 1.5rem;
        }
        .stream-url-box h3 {
            font-size: 0.75rem;
            color: var(--muted);
            margin-bottom: 0.5rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }
        .stream-url-row {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .stream-url-input {
            flex: 1;
            background: rgba(0,0,0,0.3);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 6px;
            padding: 0.6rem 0.8rem;
            font-family: inherit;
            font-size: 0.85rem;
            color: var(--accent);
            outline: none;
        }
        .stream-url-input:focus { border-color: var(--accent); }
        .btn-copy {
            background: var(--accent);
            color: #000;
            padding: 0.6rem 1rem;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.85rem;
        }
        .btn-copy:hover { box-shadow: 0 2px 10px rgba(6, 182, 212, 0.4); }
        .btn-copy.copied { background: var(--success); }
        @media (max-width: 768px) {
            .main-grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ WebRTC Stream Test</h1>
        <p class="subtitle">Real-time video streaming via WebRTC</p>
        
        <div class="stream-url-box">
            <h3>🔗 WebRTC 服务地址 (用于 video_stream_app)</h3>
            <div class="stream-url-row">
                <input type="text" class="stream-url-input" id="streamUrl" readonly>
                <button class="btn-copy" id="copyBtn" onclick="copyStreamUrl()">📋 复制</button>
            </div>
        </div>
        
        <div class="main-grid">
            <div class="video-container">
                <video id="video" autoplay playsinline muted></video>
                <div class="controls">
                    <div class="status">
                        <div class="status-dot" id="statusDot"></div>
                        <span id="statusText">Ready</span>
                    </div>
                    <div class="buttons">
                        <button class="btn-primary" id="connectBtn">▶ Connect</button>
                        <button class="btn-secondary" id="disconnectBtn" disabled>⏹ Disconnect</button>
                        <button class="btn-secondary" id="restartBtn">🔄 重新开始</button>
                    </div>
                </div>
                <div class="console" id="console"></div>
            </div>
            
            <div class="sidebar">
                <div class="panel">
                    <h2>📤 上传视频</h2>
                    <div class="upload-zone" id="uploadZone">
                        <div class="upload-icon">📁</div>
                        <div class="upload-text">点击或拖拽视频文件</div>
                        <div class="upload-text" style="font-size:0.7rem;margin-top:0.5rem;">支持 MP4, AVI, MKV</div>
                    </div>
                    <input type="file" id="fileInput" class="upload-input" accept="video/*">
                    <div id="uploadStatus" class="status-msg hidden"></div>
                </div>
                
                <div class="panel">
                    <h2>🎬 视频列表</h2>
                    <div class="video-list" id="videoList">
                        <div class="video-item">加载中...</div>
                    </div>
                </div>
                
                <div class="panel">
                    <h2>视频信息</h2>
                    <div class="info-grid">
                        <div class="info-item">
                            <div class="info-label">视频</div>
                            <div class="info-value" id="videoName">-</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">分辨率</div>
                            <div class="info-value" id="resolution">-</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">帧率</div>
                            <div class="info-value" id="fps">-</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">ICE状态</div>
                            <div class="info-value" id="iceState">-</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const video = document.getElementById('video');
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        const connectBtn = document.getElementById('connectBtn');
        const disconnectBtn = document.getElementById('disconnectBtn');
        const restartBtn = document.getElementById('restartBtn');
        const consoleEl = document.getElementById('console');
        const uploadZone = document.getElementById('uploadZone');
        const fileInput = document.getElementById('fileInput');
        const uploadStatus = document.getElementById('uploadStatus');
        const videoList = document.getElementById('videoList');
        const streamUrlInput = document.getElementById('streamUrl');
        const copyBtn = document.getElementById('copyBtn');
        
        // Set WebRTC server URL
        const serverUrl = window.location.origin;
        streamUrlInput.value = serverUrl;
        
        function copyStreamUrl() {
            streamUrlInput.select();
            document.execCommand('copy');
            copyBtn.textContent = '✓ 已复制';
            copyBtn.classList.add('copied');
            setTimeout(() => {
                copyBtn.textContent = '📋 复制';
                copyBtn.classList.remove('copied');
            }, 2000);
        }
        
        let pc = null;
        
        function log(msg, type = '') {
            const line = document.createElement('div');
            line.className = 'log-line ' + type;
            line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
            consoleEl.appendChild(line);
            consoleEl.scrollTop = consoleEl.scrollHeight;
        }
        
        function setStatus(status, text) {
            statusDot.className = 'status-dot ' + status;
            statusText.textContent = text;
        }
        
        function showUploadStatus(msg, type) {
            uploadStatus.textContent = msg;
            uploadStatus.className = 'status-msg ' + type;
            uploadStatus.classList.remove('hidden');
            if (type !== 'info') {
                setTimeout(() => uploadStatus.classList.add('hidden'), 3000);
            }
        }
        
        function loadVideos() {
            fetch('/videos')
                .then(r => r.json())
                .then(data => {
                    videoList.innerHTML = data.videos.map(v => `
                        <div class="video-item ${v.current ? 'active' : ''}" 
                             onclick="switchVideo('${v.path}')" title="${v.path}">
                            <span class="video-name">${v.name}</span>
                            ${v.current ? '<span class="video-badge">当前</span>' : ''}
                        </div>
                    `).join('');
                });
        }
        
        // Upload handling
        uploadZone.onclick = () => fileInput.click();
        uploadZone.ondragover = (e) => { e.preventDefault(); uploadZone.classList.add('dragover'); };
        uploadZone.ondragleave = () => uploadZone.classList.remove('dragover');
        uploadZone.ondrop = (e) => {
            e.preventDefault();
            uploadZone.classList.remove('dragover');
            if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
        };
        fileInput.onchange = () => { if (fileInput.files.length) uploadFile(fileInput.files[0]); };
        
        async function uploadFile(file) {
            showUploadStatus('正在上传...', 'info');
            const formData = new FormData();
            formData.append('video', file);
            try {
                const resp = await fetch('/upload', { method: 'POST', body: formData });
                const data = await resp.json();
                if (data.success) {
                    showUploadStatus(`上传成功: ${data.filename}`, 'success');
                    loadVideos();
                } else {
                    showUploadStatus('上传失败: ' + data.error, 'error');
                }
            } catch (err) {
                showUploadStatus('上传失败: ' + err.message, 'error');
            }
        }
        
        async function switchVideo(path) {
            showUploadStatus('正在切换...', 'info');
            try {
                const resp = await fetch('/restart', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ video_path: path })
                });
                const data = await resp.json();
                if (data.success) {
                    showUploadStatus('已切换视频，请重新连接', 'success');
                    disconnect();
                    loadVideos();
                    log('Video switched, reconnect to view', 'info');
                } else {
                    showUploadStatus('切换失败: ' + data.error, 'error');
                }
            } catch (err) {
                showUploadStatus('切换失败: ' + err.message, 'error');
            }
        }
        
        async function restartStream() {
            log('Restarting stream...', 'info');
            try {
                await fetch('/restart', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                });
                disconnect();
                setTimeout(() => connect(), 500);
            } catch (err) {
                log('Restart failed: ' + err.message, 'error');
            }
        }
        
        async function connect() {
            try {
                setStatus('connecting', 'Connecting...');
                log('Creating RTCPeerConnection...', 'info');
                
                pc = new RTCPeerConnection({
                    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
                });
                
                pc.oniceconnectionstatechange = () => {
                    const state = pc.iceConnectionState;
                    document.getElementById('iceState').textContent = state;
                    log('ICE: ' + state, state === 'connected' ? 'success' : 'info');
                    
                    if (state === 'connected' || state === 'completed') {
                        setStatus('connected', 'Connected');
                    } else if (state === 'failed' || state === 'disconnected') {
                        setStatus('error', 'Disconnected');
                    }
                };
                
                pc.ontrack = (e) => {
                    log('Received video track', 'success');
                    video.srcObject = e.streams[0];
                    video.onloadedmetadata = () => {
                        document.getElementById('resolution').textContent = 
                            video.videoWidth + '×' + video.videoHeight;
                    };
                };
                
                pc.addTransceiver('video', { direction: 'recvonly' });
                
                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);
                
                await new Promise(resolve => {
                    if (pc.iceGatheringState === 'complete') resolve();
                    else pc.onicegatheringstatechange = () => {
                        if (pc.iceGatheringState === 'complete') resolve();
                    };
                });
                
                log('Sending offer...', 'info');
                
                const resp = await fetch('/offer', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        sdp: pc.localDescription.sdp,
                        type: pc.localDescription.type
                    })
                });
                
                const answer = await resp.json();
                if (answer.error) throw new Error(answer.error);
                
                log('Got answer', 'success');
                await pc.setRemoteDescription(new RTCSessionDescription(answer));
                
                const info = await (await fetch('/info')).json();
                document.getElementById('videoName').textContent = info.video_name;
                document.getElementById('fps').textContent = info.fps.toFixed(1) + ' fps';
                
                connectBtn.disabled = true;
                disconnectBtn.disabled = false;
                
            } catch (err) {
                log('Error: ' + err.message, 'error');
                setStatus('error', 'Failed');
                disconnect();
            }
        }
        
        function disconnect() {
            if (pc) { pc.close(); pc = null; }
            video.srcObject = null;
            setStatus('', 'Disconnected');
            connectBtn.disabled = false;
            disconnectBtn.disabled = true;
            log('Disconnected', 'info');
        }
        
        connectBtn.onclick = connect;
        disconnectBtn.onclick = disconnect;
        restartBtn.onclick = restartStream;
        
        log('WebRTC test page ready', 'info');
        loadVideos();
    </script>
</body>
</html>"""
    
    return web.Response(text=html, content_type="text/html")


async def offer(request: web.Request) -> web.Response:
    """Handle WebRTC offer"""
    global video_path
    
    if not AIORTC_AVAILABLE:
        return web.json_response({"error": "aiortc not installed"}, status=503)
    
    params = await request.json()
    offer_sdp = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    
    pc = RTCPeerConnection()
    pcs.add(pc)
    
    logger.info(f"New WebRTC connection (total: {len(pcs)})")
    
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"Connection state: {pc.connectionState}")
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)
    
    # Add video track
    track = VideoFileTrack(video_path, loop=True)
    pc.addTrack(track)
    
    await pc.setRemoteDescription(offer_sdp)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    
    return web.json_response({
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    })


async def info(request: web.Request) -> web.Response:
    """Return video info"""
    global video_path
    
    cap = cv2.VideoCapture(video_path)
    data = {
        "video_path": video_path,
        "video_name": Path(video_path).name,
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": cap.get(cv2.CAP_PROP_FPS),
        "total_frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        "duration": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) / cap.get(cv2.CAP_PROP_FPS),
        "active_connections": len(pcs)
    }
    cap.release()
    
    return web.json_response(data)


async def upload_video(request: web.Request) -> web.Response:
    """Handle video file upload"""
    try:
        reader = await request.multipart()
        field = await reader.next()
        
        if field is None or field.name != 'video':
            return web.json_response({"error": "No video file provided"}, status=400)
        
        filename = field.filename
        if not filename:
            return web.json_response({"error": "No filename"}, status=400)
        
        # Sanitize filename
        safe_filename = "".join(c for c in filename if c.isalnum() or c in '._-')
        filepath = UPLOAD_DIR / safe_filename
        
        # Save file
        size = 0
        with open(filepath, 'wb') as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                f.write(chunk)
        
        # Verify it's a valid video
        cap = cv2.VideoCapture(str(filepath))
        if not cap.isOpened():
            os.remove(filepath)
            return web.json_response({"error": "Invalid video file"}, status=400)
        cap.release()
        
        logger.info(f"Uploaded video: {safe_filename} ({size / 1024 / 1024:.1f} MB)")
        
        return web.json_response({
            "success": True,
            "filename": safe_filename,
            "path": str(filepath),
            "size": size
        })
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def restart_stream(request: web.Request) -> web.Response:
    """Restart stream with a different video"""
    global video_path
    
    try:
        data = await request.json()
        new_video = data.get("video_path")
        
        if new_video:
            # Check if file exists
            if not Path(new_video).exists():
                return web.json_response({"error": f"Video not found: {new_video}"}, status=404)
            video_path = new_video
            logger.info(f"Switched video to: {new_video}")
        
        # Close existing connections to force restart
        for pc in list(pcs):
            await pc.close()
        pcs.clear()
        
        logger.info("Stream restart requested")
        
        return web.json_response({
            "success": True,
            "video_path": video_path,
            "message": "Stream restarted, reconnect to see new video"
        })
        
    except Exception as e:
        logger.error(f"Restart error: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def list_videos(request: web.Request) -> web.Response:
    """List available videos"""
    global video_path
    videos = []
    
    # Current video
    videos.append({
        "name": Path(video_path).name,
        "path": video_path,
        "current": True
    })
    
    # Uploaded videos
    for ext in ["*.mp4", "*.avi", "*.mkv"]:
        for f in UPLOAD_DIR.glob(ext):
            if str(f) != video_path:
                videos.append({
                    "name": f.name,
                    "path": str(f),
                    "current": False
                })
    
    return web.json_response({"videos": videos})


async def on_shutdown(app: web.Application):
    """Cleanup on shutdown"""
    logger.info("Shutting down...")
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()


def main():
    global video_path
    
    parser = argparse.ArgumentParser(description="WebRTC Video Stream Server")
    parser.add_argument(
        "--video",
        type=str,
        default="/data2/jj/proj/video_processor/test_data/2024-12-24_225315_VID002.mp4",
        help="Path to video file"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8088,
        help="Server port (default: 8088)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Server host (default: 0.0.0.0)"
    )
    
    args = parser.parse_args()
    
    if not Path(args.video).exists():
        logger.error(f"Video not found: {args.video}")
        return
    
    if not AIORTC_AVAILABLE:
        logger.error("aiortc not installed. Run: pip install aiortc")
        return
    
    video_path = args.video
    
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.router.add_get("/info", info)
    app.router.add_post("/upload", upload_video)
    app.router.add_post("/restart", restart_stream)
    app.router.add_get("/videos", list_videos)
    app.on_shutdown.append(on_shutdown)
    
    logger.info("=" * 50)
    logger.info("  WebRTC Video Stream Server")
    logger.info("=" * 50)
    logger.info(f"  Video:   {video_path}")
    logger.info(f"  Server:  http://{args.host}:{args.port}")
    logger.info(f"  Test:    http://localhost:{args.port}")
    logger.info("=" * 50)
    
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()



