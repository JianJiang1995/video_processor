#!/usr/bin/env python3
"""
RTSP Stream Simulator

Simulates an RTSP video stream from a local video file.
Uses FFmpeg to stream the video as RTSP.

Usage:
    python rtsp_server.py --video /path/to/video.mp4 --port 8554
    
Connect with:
    ffplay rtsp://localhost:8554/stream
    vlc rtsp://localhost:8554/stream
"""

import argparse
import subprocess
import signal
import sys
import os
import logging
from pathlib import Path

from path_utils import require_video_path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class RTSPServer:
    """
    RTSP Server using FFmpeg.
    
    Streams a video file as RTSP using ffmpeg's RTSP server capabilities.
    For a full-featured RTSP server, consider using GStreamer or MediaMTX.
    """
    
    def __init__(
        self,
        video_path: str,
        port: int = 8554,
        stream_path: str = "/stream",
        loop: bool = False  # Default: stop at video end
    ):
        self.video_path = video_path
        self.port = port
        self.stream_path = stream_path
        self.loop = loop
        self.process = None
        
        if not Path(video_path).exists():
            raise FileNotFoundError(f"Video not found: {video_path}")
    
    @property
    def rtsp_url(self) -> str:
        return f"rtsp://localhost:{self.port}{self.stream_path}"
    
    def start(self):
        """Start the RTSP server using FFmpeg"""
        
        # FFmpeg command to stream as RTSP
        # Note: FFmpeg doesn't have a built-in RTSP server, so we use mediamtx or similar
        # Here we provide a fallback using TCP streaming
        
        logger.info(f"Starting RTSP stream simulation...")
        logger.info(f"Video: {self.video_path}")
        logger.info(f"URL: {self.rtsp_url}")
        
        # Check if mediamtx is available
        mediamtx_available = self._check_command("mediamtx")
        
        if mediamtx_available:
            self._start_with_mediamtx()
        else:
            # Fallback: Use FFmpeg with TCP (not true RTSP but works for testing)
            self._start_with_ffmpeg_tcp()
    
    def _check_command(self, cmd: str) -> bool:
        """Check if command is available"""
        try:
            subprocess.run([cmd, "--help"], capture_output=True, timeout=5)
            return True
        except (subprocess.SubprocessError, FileNotFoundError):
            return False
    
    def _start_with_mediamtx(self):
        """Start using MediaMTX RTSP server"""
        logger.info("Using MediaMTX for RTSP...")
        
        # Start MediaMTX in background
        # Then push stream using FFmpeg
        
        # This requires MediaMTX to be running
        # For now, we'll use the FFmpeg approach
        self._start_with_ffmpeg_tcp()
    
    def _start_with_ffmpeg_tcp(self):
        """
        Stream using FFmpeg to a TCP port.
        
        Not a true RTSP server, but can be used for testing.
        Connect using: ffplay tcp://localhost:8554
        """
        
        loop_args = ["-stream_loop", "-1"] if self.loop else []
        
        cmd = [
            "ffmpeg",
            *loop_args,
            "-re",  # Real-time (simulate live stream)
            "-i", self.video_path,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-f", "mpegts",
            f"tcp://0.0.0.0:{self.port}?listen=1"
        ]
        
        logger.info(f"Starting FFmpeg TCP stream on port {self.port}")
        logger.info(f"Connect with: ffplay tcp://localhost:{self.port}")
        logger.info(f"Or use HTTP server for MJPEG: http://localhost:8080/stream")
        
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Wait for process
            self.process.wait()
            
        except KeyboardInterrupt:
            self.stop()
        except FileNotFoundError:
            logger.error("FFmpeg not found. Please install: apt install ffmpeg")
            raise
    
    def stop(self):
        """Stop the RTSP server"""
        if self.process:
            self.process.terminate()
            self.process.wait()
            logger.info("RTSP server stopped")


def print_rtsp_alternatives():
    """Print alternative RTSP solutions"""
    print("""
╔════════════════════════════════════════════════════════════════════╗
║                    RTSP Streaming Options                          ║
╠════════════════════════════════════════════════════════════════════╣
║                                                                    ║
║  For a full RTSP server, consider these options:                   ║
║                                                                    ║
║  1. MediaMTX (recommended)                                         ║
║     Install: Download from github.com/bluenviron/mediamtx          ║
║     Usage:   mediamtx &                                            ║
║              ffmpeg -re -i video.mp4 -f rtsp rtsp://localhost:8554 ║
║                                                                    ║
║  2. GStreamer RTSP Server                                          ║
║     Install: apt install gstreamer1.0-rtsp                         ║
║                                                                    ║
║  3. VLC Streaming                                                  ║
║     vlc video.mp4 --sout '#rtp{sdp=rtsp://:8554/stream}'           ║
║                                                                    ║
║  For testing video_stream_app, use HTTP/MJPEG instead:             ║
║     python http_server.py --video video.mp4                        ║
║     Connect: http://localhost:8080/stream                          ║
║                                                                    ║
╚════════════════════════════════════════════════════════════════════╝
""")


def main():
    parser = argparse.ArgumentParser(description="RTSP Stream Simulator")
    parser.add_argument(
        "--video",
        type=str,
        default=None,
        help="Path to video file (defaults to media/sample.mp4 if available)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8554,
        help="RTSP port (default: 8554)"
    )
    parser.add_argument(
        "--path",
        type=str,
        default="/stream",
        help="Stream path (default: /stream)"
    )
    parser.add_argument(
        "--no-loop",
        action="store_true",
        help="Don't loop the video"
    )
    
    args = parser.parse_args()
    
    print_rtsp_alternatives()
    
    try:
        video_path = require_video_path(args.video)
        server = RTSPServer(
            video_path=str(video_path),
            port=args.port,
            stream_path=args.path,
            loop=not args.no_loop
        )
        server.start()
    except KeyboardInterrupt:
        print("\nStopped.")
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()





