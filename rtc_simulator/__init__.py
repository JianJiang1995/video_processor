"""
Stream Simulator Package

Multi-protocol video stream simulator for testing video_stream_app.

Supported protocols:
- HTTP/MJPEG: Motion JPEG streaming
- WebRTC: Real-time P2P streaming
- RTSP: (via FFmpeg/MediaMTX)

Usage:
    from rtc_simulator import VideoSource, HTTPStreamServer, WebRTCServer
"""

from .video_source import VideoSource, VideoInfo, encode_frame_jpeg, encode_frame_png

__version__ = "1.0.0"
__all__ = ["VideoSource", "VideoInfo", "encode_frame_jpeg", "encode_frame_png"]





