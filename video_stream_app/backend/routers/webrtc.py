"""
WebRTC Streaming API Routes

Provides WebRTC signaling endpoints for video streaming.
This allows the frontend to receive video streams via WebRTC protocol.

For the full WebRTC stream simulator, see:
    /data2/jj/proj/video_processor/stream_simulator/
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Set
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import httpx

# Note: aiortc requires separate installation
try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    from av import VideoFrame
    AIORTC_AVAILABLE = True
except ImportError:
    AIORTC_AVAILABLE = False
    RTCPeerConnection = None

import cv2
import numpy as np
import fractions
import time

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webrtc", tags=["webrtc"])

# External stream simulator URL
STREAM_SIMULATOR_URL = "http://localhost:8088"

# Store active peer connections
active_pcs: Set["RTCPeerConnection"] = set()

# Active video sources
video_sources: dict = {}


class VideoFileTrack(VideoStreamTrack if AIORTC_AVAILABLE else object):
    """
    Video track that reads from a file and streams via WebRTC.
    Simulates a live video stream by reading frames at real-time speed.
    """
    
    kind = "video"
    
    def __init__(self, video_path: str, loop: bool = True, fps_override: Optional[float] = None):
        if AIORTC_AVAILABLE:
            super().__init__()
        
        self.video_path = video_path
        self.loop = loop
        self.fps_override = fps_override
        
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
        
        # Timing
        self._start_time = None
        self._pts = 0
        self._frame_idx = 0
        self._time_base = fractions.Fraction(1, int(self.fps * 1000))
        
        logger.info(f"VideoFileTrack created: {video_path} ({self.width}x{self.height} @ {self.fps}fps)")
    
    def _read_frame(self) -> Optional[np.ndarray]:
        ret, frame = self.cap.read()
        if not ret:
            if self.loop:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self._frame_idx = 0
                ret, frame = self.cap.read()
                if not ret:
                    return None
                logger.debug("Video looped")
            else:
                return None
        self._frame_idx += 1
        return frame
    
    async def recv(self):
        if not AIORTC_AVAILABLE:
            raise RuntimeError("aiortc not installed")
        
        if self._start_time is None:
            self._start_time = time.time()
        
        # Calculate timing
        frame_time = self._pts / (self.fps * 1000)
        current_time = time.time() - self._start_time
        wait_time = frame_time - current_time
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        
        # Read frame
        bgr_frame = self._read_frame()
        if bgr_frame is None:
            bgr_frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        
        # Convert to RGB
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


class WebRTCOffer(BaseModel):
    sdp: str
    type: str
    video_path: Optional[str] = None
    session_id: Optional[str] = None


class WebRTCAnswer(BaseModel):
    sdp: str
    type: str


@router.get("/status")
async def webrtc_status():
    """Check WebRTC availability and status"""
    
    # Check external stream simulator
    external_available = False
    external_info = None
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{STREAM_SIMULATOR_URL}/info")
            if resp.status_code == 200:
                external_available = True
                external_info = resp.json()
    except Exception:
        pass
    
    return {
        "available": AIORTC_AVAILABLE,
        "active_connections": len(active_pcs),
        "active_sources": list(video_sources.keys()),
        "external_simulator": {
            "url": STREAM_SIMULATOR_URL,
            "available": external_available,
            "info": external_info
        },
        "message": "aiortc is available" if AIORTC_AVAILABLE else "aiortc not installed - run: pip install aiortc",
        "help": "For full stream simulation, run: cd /data2/jj/proj/video_processor/stream_simulator && python run.py all"
    }


@router.post("/offer", response_model=WebRTCAnswer)
async def handle_offer(offer: WebRTCOffer):
    """
    Handle WebRTC offer from client.
    
    The client sends an SDP offer, and we respond with an SDP answer
    containing the video stream.
    """
    if not AIORTC_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="WebRTC not available. Install aiortc: pip install aiortc"
        )
    
    # Determine video source
    video_path = offer.video_path
    if not video_path and offer.session_id:
        # Get video path from session
        from ..database import get_db, get_video_session
        db = next(get_db())
        session = get_video_session(db, offer.session_id)
        if session:
            video_path = session["video_path"]
    
    if not video_path:
        # Default test video
        video_path = "/data2/jj/proj/video_processor/test_data/2024-12-24_225315_VID002.mp4"
    
    if not Path(video_path).exists():
        raise HTTPException(404, f"Video not found: {video_path}")
    
    # Create peer connection
    pc = RTCPeerConnection()
    active_pcs.add(pc)
    
    logger.info(f"New WebRTC connection (total: {len(active_pcs)})")
    
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"Connection state: {pc.connectionState}")
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            active_pcs.discard(pc)
    
    # Create video track
    video_track = VideoFileTrack(video_path, loop=True)
    pc.addTrack(video_track)
    
    # Handle the offer
    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=offer.sdp, type=offer.type)
    )
    
    # Create answer
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    
    return WebRTCAnswer(
        sdp=pc.localDescription.sdp,
        type=pc.localDescription.type
    )


@router.post("/close/{connection_id}")
async def close_connection(connection_id: str):
    """Close a specific WebRTC connection"""
    # Note: In a real implementation, we'd track connections by ID
    return {"status": "ok", "message": "Connection closed"}


@router.get("/test-page")
async def webrtc_test_page():
    """Return a simple WebRTC test page HTML"""
    from fastapi.responses import HTMLResponse
    
    html = """<!DOCTYPE html>
<html>
<head>
    <title>WebRTC Stream Test</title>
    <style>
        body {
            font-family: 'Segoe UI', system-ui, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            min-height: 100vh;
            margin: 0;
            padding: 2rem;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        h1 { color: #00d4ff; margin-bottom: 1rem; }
        .container {
            background: rgba(255,255,255,0.05);
            border-radius: 16px;
            padding: 2rem;
            max-width: 900px;
            width: 100%;
        }
        video {
            width: 100%;
            background: #000;
            border-radius: 12px;
            margin: 1rem 0;
        }
        .controls {
            display: flex;
            gap: 1rem;
            margin-bottom: 1rem;
        }
        button {
            padding: 0.75rem 2rem;
            border: none;
            border-radius: 8px;
            font-size: 1rem;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-primary {
            background: linear-gradient(135deg, #00d4ff, #0099cc);
            color: #000;
            font-weight: 600;
        }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 4px 20px rgba(0,212,255,0.4); }
        .btn-secondary {
            background: rgba(255,255,255,0.1);
            color: #fff;
        }
        .status {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            margin-bottom: 1rem;
        }
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #666;
        }
        .status-dot.connected { background: #00ff88; box-shadow: 0 0 10px #00ff88; }
        .status-dot.connecting { background: #ffaa00; animation: pulse 1s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        input {
            flex: 1;
            padding: 0.75rem;
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 8px;
            background: rgba(0,0,0,0.3);
            color: #fff;
            font-size: 1rem;
        }
        .log {
            background: #000;
            border-radius: 8px;
            padding: 1rem;
            max-height: 150px;
            overflow-y: auto;
            font-family: monospace;
            font-size: 0.85rem;
        }
        .log-line { margin: 0.25rem 0; color: #888; }
        .log-line.info { color: #00d4ff; }
        .log-line.success { color: #00ff88; }
        .log-line.error { color: #ff4466; }
    </style>
</head>
<body>
    <h1>🎬 WebRTC Video Stream Test</h1>
    <div class="container">
        <div class="controls">
            <input type="text" id="videoPath" 
                   value="/data2/jj/proj/video_processor/test_data/2024-12-24_225315_VID002.mp4" 
                   placeholder="Video file path...">
        </div>
        <div class="controls">
            <button class="btn-primary" id="connectBtn">▶ Connect</button>
            <button class="btn-secondary" id="disconnectBtn" disabled>⏹ Disconnect</button>
        </div>
        <div class="status">
            <div class="status-dot" id="statusDot"></div>
            <span id="statusText">Ready to connect</span>
        </div>
        <video id="video" autoplay playsinline muted></video>
        <div class="log" id="log"></div>
    </div>
    
    <script>
        const video = document.getElementById('video');
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        const connectBtn = document.getElementById('connectBtn');
        const disconnectBtn = document.getElementById('disconnectBtn');
        const videoPathInput = document.getElementById('videoPath');
        const logEl = document.getElementById('log');
        
        let pc = null;
        
        function log(msg, type = '') {
            const line = document.createElement('div');
            line.className = 'log-line ' + type;
            line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
            logEl.appendChild(line);
            logEl.scrollTop = logEl.scrollHeight;
        }
        
        async function connect() {
            try {
                statusDot.className = 'status-dot connecting';
                statusText.textContent = 'Connecting...';
                log('Creating peer connection...', 'info');
                
                pc = new RTCPeerConnection({
                    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
                });
                
                pc.oniceconnectionstatechange = () => {
                    log(`ICE: ${pc.iceConnectionState}`, pc.iceConnectionState === 'connected' ? 'success' : 'info');
                    if (pc.iceConnectionState === 'connected') {
                        statusDot.className = 'status-dot connected';
                        statusText.textContent = 'Connected';
                    }
                };
                
                pc.ontrack = (e) => {
                    log('Video track received!', 'success');
                    video.srcObject = e.streams[0];
                };
                
                pc.addTransceiver('video', { direction: 'recvonly' });
                
                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);
                
                // Wait for ICE gathering
                await new Promise(resolve => {
                    if (pc.iceGatheringState === 'complete') resolve();
                    else pc.onicegatheringstatechange = () => {
                        if (pc.iceGatheringState === 'complete') resolve();
                    };
                });
                
                log('Sending offer...', 'info');
                
                const resp = await fetch('/api/webrtc/offer', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        sdp: pc.localDescription.sdp,
                        type: pc.localDescription.type,
                        video_path: videoPathInput.value
                    })
                });
                
                const answer = await resp.json();
                if (answer.detail) throw new Error(answer.detail);
                
                log('Got answer, setting remote description...', 'success');
                await pc.setRemoteDescription(new RTCSessionDescription(answer));
                
                connectBtn.disabled = true;
                disconnectBtn.disabled = false;
                
            } catch (err) {
                log('Error: ' + err.message, 'error');
                statusDot.className = 'status-dot';
                statusText.textContent = 'Connection failed';
                disconnect();
            }
        }
        
        function disconnect() {
            if (pc) { pc.close(); pc = null; }
            video.srcObject = null;
            statusDot.className = 'status-dot';
            statusText.textContent = 'Disconnected';
            connectBtn.disabled = false;
            disconnectBtn.disabled = true;
            log('Disconnected', 'info');
        }
        
        connectBtn.onclick = connect;
        disconnectBtn.onclick = disconnect;
        
        log('Page loaded. Enter video path and click Connect.', 'info');
    </script>
</body>
</html>"""
    return HTMLResponse(content=html)


async def cleanup_connections():
    """Cleanup all active connections (call on shutdown)"""
    coros = [pc.close() for pc in active_pcs]
    await asyncio.gather(*coros)
    active_pcs.clear()
    logger.info("All WebRTC connections closed")

