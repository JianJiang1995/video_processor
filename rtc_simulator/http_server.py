#!/usr/bin/env python3
"""
HTTP Video Stream Server

Provides multiple HTTP streaming endpoints:
- /stream     - Motion JPEG (MJPEG) stream
- /stream.jpg - Single JPEG frame (snapshot)
- /info       - Video information JSON

Usage:
    python http_server.py --video /path/to/video.mp4 --port 8080

Connect with:
    Browser: http://localhost:8080/stream
    OpenCV:  cv2.VideoCapture("http://localhost:8080/stream")
    VLC:     vlc http://localhost:8080/stream
"""

import argparse
import asyncio
import logging
import json
import time
import os
import shutil
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from aiohttp import web

from video_source import VideoSource, encode_frame_jpeg

# Upload directory
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class HTTPStreamServer:
    """
    HTTP streaming server with MJPEG support.
    
    This is the most compatible streaming format for testing
    with video_stream_app, as it works with OpenCV's VideoCapture
    and most browsers.
    """
    
    def __init__(
        self,
        video_path: str,
        port: int = 8080,
        host: str = "0.0.0.0",
        jpeg_quality: int = 80,
        loop: bool = True
    ):
        self.video_path = video_path
        self.port = port
        self.host = host
        self.jpeg_quality = jpeg_quality
        self.loop = loop
        
        self.video_source: Optional[VideoSource] = None
        self.app = web.Application()
        self._setup_routes()
        
        # Active stream connections
        self.active_streams = 0
    
    def _setup_routes(self):
        """Setup HTTP routes"""
        self.app.router.add_get("/", self.index)
        self.app.router.add_get("/stream", self.mjpeg_stream)
        self.app.router.add_get("/stream.jpg", self.snapshot)
        self.app.router.add_get("/info", self.video_info)
        self.app.router.add_get("/health", self.health)
        self.app.router.add_post("/upload", self.upload_video)
        self.app.router.add_post("/restart", self.restart_stream)
        self.app.router.add_get("/videos", self.list_videos)
    
    async def index(self, request: web.Request) -> web.Response:
        """Serve the stream test page"""
        html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>HTTP Stream Server</title>
    <style>
        :root {
            --bg: #0d0d12;
            --card: #16161d;
            --accent: #22d3ee;
            --accent2: #a78bfa;
            --success: #10b981;
            --text: #f0f0f5;
            --muted: #6b6b80;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'SF Mono', 'Fira Code', monospace;
            background: var(--bg);
            color: var(--text);
            padding: 2rem;
            min-height: 100vh;
            background-image: 
                radial-gradient(circle at 20% 30%, rgba(34, 211, 238, 0.05) 0%, transparent 50%),
                radial-gradient(circle at 80% 70%, rgba(167, 139, 250, 0.05) 0%, transparent 50%);
        }
        h1 {
            font-size: 1.5rem;
            font-weight: 500;
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.5rem;
        }
        .subtitle {
            color: var(--muted);
            margin-bottom: 2rem;
        }
        .container {
            max-width: 1000px;
            margin: 0 auto;
        }
        .main-grid {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 1.5rem;
            margin-bottom: 1.5rem;
        }
        .stream-container {
            background: var(--card);
            border-radius: 12px;
            padding: 1rem;
            border: 1px solid rgba(255,255,255,0.05);
        }
        img.stream {
            width: 100%;
            border-radius: 8px;
            background: #000;
            aspect-ratio: 16/9;
            object-fit: contain;
        }
        .stream-controls {
            display: flex;
            gap: 0.75rem;
            margin-top: 1rem;
        }
        .sidebar {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }
        .panel {
            background: var(--card);
            border-radius: 12px;
            padding: 1.25rem;
            border: 1px solid rgba(255,255,255,0.05);
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
            background: rgba(34, 211, 238, 0.05);
        }
        .upload-zone.dragover {
            border-color: var(--accent);
            background: rgba(34, 211, 238, 0.1);
        }
        .upload-icon {
            font-size: 2rem;
            margin-bottom: 0.5rem;
        }
        .upload-text {
            font-size: 0.85rem;
            color: var(--muted);
        }
        .upload-input {
            display: none;
        }
        .video-list {
            max-height: 200px;
            overflow-y: auto;
        }
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
        .video-item:hover {
            background: rgba(255,255,255,0.05);
        }
        .video-item.active {
            background: rgba(34, 211, 238, 0.15);
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
        button {
            padding: 0.6rem 1rem;
            border: none;
            border-radius: 6px;
            font-family: inherit;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-primary {
            background: linear-gradient(135deg, var(--accent), #0891b2);
            color: #000;
            font-weight: 600;
            flex: 1;
        }
        .btn-primary:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 15px rgba(34, 211, 238, 0.3);
        }
        .btn-secondary {
            background: rgba(255,255,255,0.05);
            color: var(--text);
            border: 1px solid rgba(255,255,255,0.1);
        }
        .btn-secondary:hover {
            border-color: var(--accent);
        }
        .endpoints {
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }
        .endpoint {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.5rem 0;
            font-size: 0.85rem;
        }
        .endpoint-url a {
            color: var(--accent);
            text-decoration: none;
        }
        .endpoint-url a:hover {
            text-decoration: underline;
        }
        .info-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1rem;
            margin-top: 1.5rem;
        }
        .info-item {
            background: var(--card);
            border-radius: 8px;
            padding: 1rem;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.05);
        }
        .info-label {
            font-size: 0.7rem;
            color: var(--muted);
            margin-bottom: 0.5rem;
        }
        .info-value {
            font-size: 1.1rem;
            color: var(--accent);
        }
        .status-msg {
            margin-top: 0.75rem;
            font-size: 0.8rem;
            padding: 0.5rem;
            border-radius: 4px;
            text-align: center;
        }
        .status-msg.success {
            background: rgba(16, 185, 129, 0.2);
            color: var(--success);
        }
        .status-msg.error {
            background: rgba(239, 68, 68, 0.2);
            color: #ef4444;
        }
        .status-msg.info {
            background: rgba(34, 211, 238, 0.2);
            color: var(--accent);
        }
        .hidden { display: none; }
        .stream-url-box {
            background: linear-gradient(135deg, rgba(34, 211, 238, 0.1), rgba(167, 139, 250, 0.1));
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
            font-size: 0.9rem;
            color: var(--accent);
            outline: none;
        }
        .stream-url-input:focus {
            border-color: var(--accent);
        }
        .btn-copy {
            background: var(--accent);
            color: #000;
            padding: 0.6rem 1rem;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.85rem;
            transition: all 0.2s;
        }
        .btn-copy:hover {
            transform: translateY(-1px);
            box-shadow: 0 2px 10px rgba(34, 211, 238, 0.4);
        }
        .btn-copy.copied {
            background: var(--success);
        }
        @media (max-width: 768px) {
            .main-grid {
                grid-template-columns: 1fr;
            }
            .info-grid {
                grid-template-columns: repeat(2, 1fr);
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📡 HTTP Stream Server</h1>
        <p class="subtitle">Motion JPEG Video Stream with Upload Support</p>
        
        <div class="stream-url-box">
            <h3>🔗 视频流地址 (用于 video_stream_app)</h3>
            <div class="stream-url-row">
                <input type="text" class="stream-url-input" id="streamUrl" readonly>
                <button class="btn-copy" id="copyBtn" onclick="copyStreamUrl()">📋 复制</button>
            </div>
        </div>
        
        <div class="main-grid">
            <div class="stream-container">
                <img class="stream" id="streamImg" src="/stream" alt="Video Stream">
                <div class="stream-controls">
                    <button class="btn-primary" onclick="restartStream()">🔄 重新开始</button>
                    <button class="btn-secondary" onclick="refreshStream()">刷新画面</button>
                </div>
            </div>
            
            <div class="sidebar">
                <div class="panel">
                    <h2>📤 上传视频</h2>
                    <div class="upload-zone" id="uploadZone">
                        <div class="upload-icon">📁</div>
                        <div class="upload-text">点击或拖拽视频文件</div>
                        <div class="upload-text" style="font-size:0.75rem;margin-top:0.5rem;">支持 MP4, AVI, MKV</div>
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
                    <h2>API 端点</h2>
                    <div class="endpoints">
                        <div class="endpoint">
                            <span>MJPEG</span>
                            <span class="endpoint-url"><a href="/stream">/stream</a></span>
                        </div>
                        <div class="endpoint">
                            <span>快照</span>
                            <span class="endpoint-url"><a href="/stream.jpg">/stream.jpg</a></span>
                        </div>
                        <div class="endpoint">
                            <span>信息</span>
                            <span class="endpoint-url"><a href="/info">/info</a></span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="info-grid" id="info"></div>
    </div>
    
    <script>
        const uploadZone = document.getElementById('uploadZone');
        const fileInput = document.getElementById('fileInput');
        const uploadStatus = document.getElementById('uploadStatus');
        const videoList = document.getElementById('videoList');
        const streamImg = document.getElementById('streamImg');
        const streamUrlInput = document.getElementById('streamUrl');
        const copyBtn = document.getElementById('copyBtn');
        
        // Set stream URL
        const streamUrl = window.location.origin + '/stream';
        streamUrlInput.value = streamUrl;
        
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
        
        // Load info and video list
        function loadInfo() {
            fetch('/info')
                .then(r => r.json())
                .then(info => {
                    document.getElementById('info').innerHTML = `
                        <div class="info-item">
                            <div class="info-label">分辨率</div>
                            <div class="info-value">${info.width}×${info.height}</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">帧率</div>
                            <div class="info-value">${info.fps.toFixed(1)} fps</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">时长</div>
                            <div class="info-value">${(info.duration / 60).toFixed(1)} min</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">活跃连接</div>
                            <div class="info-value">${info.active_streams}</div>
                        </div>
                    `;
                });
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
        
        uploadZone.ondragover = (e) => {
            e.preventDefault();
            uploadZone.classList.add('dragover');
        };
        uploadZone.ondragleave = () => uploadZone.classList.remove('dragover');
        uploadZone.ondrop = (e) => {
            e.preventDefault();
            uploadZone.classList.remove('dragover');
            if (e.dataTransfer.files.length) {
                uploadFile(e.dataTransfer.files[0]);
            }
        };
        
        fileInput.onchange = () => {
            if (fileInput.files.length) {
                uploadFile(fileInput.files[0]);
            }
        };
        
        function showStatus(msg, type) {
            uploadStatus.textContent = msg;
            uploadStatus.className = 'status-msg ' + type;
            uploadStatus.classList.remove('hidden');
            if (type !== 'info') {
                setTimeout(() => uploadStatus.classList.add('hidden'), 3000);
            }
        }
        
        async function uploadFile(file) {
            showStatus('正在上传...', 'info');
            
            const formData = new FormData();
            formData.append('video', file);
            
            try {
                const resp = await fetch('/upload', {
                    method: 'POST',
                    body: formData
                });
                const data = await resp.json();
                
                if (data.success) {
                    showStatus(`上传成功: ${data.filename}`, 'success');
                    loadVideos();
                } else {
                    showStatus('上传失败: ' + data.error, 'error');
                }
            } catch (err) {
                showStatus('上传失败: ' + err.message, 'error');
            }
        }
        
        async function switchVideo(path) {
            showStatus('正在切换...', 'info');
            try {
                const resp = await fetch('/restart', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ video_path: path })
                });
                const data = await resp.json();
                if (data.success) {
                    showStatus('已切换视频', 'success');
                    setTimeout(() => {
                        refreshStream();
                        loadInfo();
                        loadVideos();
                    }, 500);
                } else {
                    showStatus('切换失败: ' + data.error, 'error');
                }
            } catch (err) {
                showStatus('切换失败: ' + err.message, 'error');
            }
        }
        
        function restartStream() {
            fetch('/restart', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            }).then(() => {
                refreshStream();
            });
        }
        
        function refreshStream() {
            streamImg.src = '/stream?' + Date.now();
        }
        
        // Initial load
        loadInfo();
        loadVideos();
        setInterval(loadInfo, 5000);
    </script>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")
    
    async def mjpeg_stream(self, request: web.Request) -> web.StreamResponse:
        """
        Stream video as Motion JPEG.
        
        This format is widely supported:
        - Works in browsers with <img src="/stream">
        - Works with OpenCV VideoCapture
        - Works with VLC, ffplay, etc.
        """
        
        # Create response with multipart content type
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "multipart/x-mixed-replace; boundary=frame",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Access-Control-Allow-Origin": "*"
            }
        )
        await response.prepare(request)
        
        self.active_streams += 1
        logger.info(f"Stream started (active: {self.active_streams})")
        
        # Create video source for this stream
        source = VideoSource(self.video_path, loop=self.loop)
        
        try:
            async for frame in source.frames_async(realtime=True):
                # Encode frame as JPEG
                jpeg_data = encode_frame_jpeg(frame, self.jpeg_quality)
                
                # Write multipart frame
                await response.write(
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg_data)).encode() + b"\r\n"
                    b"\r\n" + jpeg_data + b"\r\n"
                )
                
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            pass
        finally:
            source.close()
            self.active_streams -= 1
            logger.info(f"Stream ended (active: {self.active_streams})")
        
        return response
    
    async def snapshot(self, request: web.Request) -> web.Response:
        """Return a single JPEG frame"""
        
        source = VideoSource(self.video_path, loop=False)
        frame = source.read_frame()
        source.close()
        
        if frame is None:
            raise web.HTTPInternalServerError(text="Cannot read frame")
        
        jpeg_data = encode_frame_jpeg(frame, self.jpeg_quality)
        
        return web.Response(
            body=jpeg_data,
            content_type="image/jpeg",
            headers={
                "Cache-Control": "no-cache",
                "Access-Control-Allow-Origin": "*"
            }
        )
    
    async def video_info(self, request: web.Request) -> web.Response:
        """Return video information as JSON"""
        
        source = VideoSource(self.video_path)
        info = source.info
        source.close()
        
        return web.json_response({
            "video_path": info.path,
            "video_name": Path(info.path).name,
            "width": info.width,
            "height": info.height,
            "fps": info.fps,
            "duration": info.duration,
            "total_frames": info.total_frames,
            "active_streams": self.active_streams,
            "stream_url": f"http://localhost:{self.port}/stream"
        })
    
    async def health(self, request: web.Request) -> web.Response:
        """Health check endpoint"""
        return web.json_response({
            "status": "healthy",
            "active_streams": self.active_streams
        })
    
    async def upload_video(self, request: web.Request) -> web.Response:
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
    
    async def restart_stream(self, request: web.Request) -> web.Response:
        """Restart stream with a different video"""
        try:
            data = await request.json()
            new_video = data.get("video_path")
            
            if new_video:
                # Check if file exists
                if not Path(new_video).exists():
                    return web.json_response({"error": f"Video not found: {new_video}"}, status=404)
                self.video_path = new_video
                logger.info(f"Switched video to: {new_video}")
            
            # Note: Active streams will pick up the new video on next loop
            logger.info("Stream restart requested")
            
            return web.json_response({
                "success": True,
                "video_path": self.video_path,
                "message": "Stream will restart with new video"
            })
            
        except Exception as e:
            logger.error(f"Restart error: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def list_videos(self, request: web.Request) -> web.Response:
        """List available videos"""
        videos = []
        
        # Current video
        videos.append({
            "name": Path(self.video_path).name,
            "path": self.video_path,
            "current": True
        })
        
        # Uploaded videos
        for f in UPLOAD_DIR.glob("*.mp4"):
            if str(f) != self.video_path:
                videos.append({
                    "name": f.name,
                    "path": str(f),
                    "current": False
                })
        for f in UPLOAD_DIR.glob("*.avi"):
            if str(f) != self.video_path:
                videos.append({
                    "name": f.name,
                    "path": str(f),
                    "current": False
                })
        for f in UPLOAD_DIR.glob("*.mkv"):
            if str(f) != self.video_path:
                videos.append({
                    "name": f.name,
                    "path": str(f),
                    "current": False
                })
        
        return web.json_response({"videos": videos})
    
    def run(self):
        """Start the HTTP server"""
        
        logger.info("=" * 50)
        logger.info("  HTTP Video Stream Server")
        logger.info("=" * 50)
        logger.info(f"  Video:     {self.video_path}")
        logger.info(f"  Server:    http://{self.host}:{self.port}")
        logger.info("")
        logger.info("  Endpoints:")
        logger.info(f"    MJPEG:   http://localhost:{self.port}/stream")
        logger.info(f"    Snap:    http://localhost:{self.port}/stream.jpg")
        logger.info(f"    Info:    http://localhost:{self.port}/info")
        logger.info("=" * 50)
        
        web.run_app(self.app, host=self.host, port=self.port, print=None)


def main():
    parser = argparse.ArgumentParser(description="HTTP Video Stream Server")
    parser.add_argument(
        "--video",
        type=str,
        default="/data2/jj/proj/video_processor/test_data/2024-12-24_225315_VID002.mp4",
        help="Path to video file"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Server port (default: 8080)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Server host (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=80,
        help="JPEG quality 1-100 (default: 80)"
    )
    parser.add_argument(
        "--no-loop",
        action="store_true",
        help="Don't loop the video"
    )
    
    args = parser.parse_args()
    
    if not Path(args.video).exists():
        logger.error(f"Video not found: {args.video}")
        return
    
    server = HTTPStreamServer(
        video_path=args.video,
        port=args.port,
        host=args.host,
        jpeg_quality=args.quality,
        loop=not args.no_loop
    )
    
    try:
        server.run()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()



