#!/usr/bin/env python3
"""
Stream Simulator - Unified CLI

Run different stream servers from a single command.

Usage:
    python run.py http --video /path/to/video.mp4
    python run.py webrtc --video /path/to/video.mp4
    python run.py all --video /path/to/video.mp4
"""

import argparse
import asyncio
import json
import os
import signal
import sys
import subprocess
from pathlib import Path

from path_utils import require_video_path

# Load config
CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config():
    """Load configuration from config.json"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {
        "video_path": "media/sample.mp4",
        "streams": {
            "http": {"port": 9001},
            "webrtc": {"port": 9002},
            "rtsp": {"port": 8554}
        }
    }


def run_http(args):
    """Run HTTP/MJPEG server"""
    from http_server import HTTPStreamServer
    
    config = load_config()
    port = args.port or config["streams"]["http"]["port"]
    video = str(require_video_path(args.video, config.get("video_path")))
    
    server = HTTPStreamServer(
        video_path=video,
        port=port,
        loop=not args.no_loop
    )
    server.run()


def run_webrtc(args):
    """Run WebRTC server"""
    from webrtc_server import main as webrtc_main
    
    config = load_config()
    port = args.port or config["streams"]["webrtc"]["port"]
    video = str(require_video_path(args.video, config.get("video_path")))
    
    # Override sys.argv for webrtc_server
    sys.argv = ["webrtc_server.py", "--video", video, "--port", str(port)]
    webrtc_main()


def run_rtsp(args):
    """Run RTSP server"""
    from rtsp_server import main as rtsp_main
    
    config = load_config()
    port = args.port or config["streams"]["rtsp"]["port"]
    video = str(require_video_path(args.video, config.get("video_path")))
    
    sys.argv = ["rtsp_server.py", "--video", video, "--port", str(port)]
    if args.no_loop:
        sys.argv.append("--no-loop")
    
    rtsp_main()


def run_all(args):
    """Run all servers"""
    config = load_config()
    video = str(require_video_path(args.video, config.get("video_path")))
    
    script_dir = Path(__file__).parent
    
    print("\n" + "=" * 60)
    print("  Stream Simulator - All Protocols")
    print("=" * 60)
    print(f"  Video: {video}")
    print()
    
    processes = []
    
    # Start HTTP server
    http_port = config["streams"]["http"]["port"]
    print(f"  Starting HTTP/MJPEG on port {http_port}...")
    p1 = subprocess.Popen(
        [sys.executable, str(script_dir / "http_server.py"), "--video", video, "--port", str(http_port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    processes.append(p1)
    
    # Start WebRTC server
    webrtc_port = config["streams"]["webrtc"]["port"]
    print(f"  Starting WebRTC on port {webrtc_port}...")
    p2 = subprocess.Popen(
        [sys.executable, str(script_dir / "webrtc_server.py"), "--video", video, "--port", str(webrtc_port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    processes.append(p2)
    
    print()
    print("  Endpoints:")
    print(f"    HTTP/MJPEG: http://localhost:{http_port}/stream")
    print(f"    WebRTC:     http://localhost:{webrtc_port}")
    print()
    print("  Press Ctrl+C to stop all servers.")
    print("=" * 60 + "\n")
    
    def cleanup(signum, frame):
        print("\nStopping servers...")
        for p in processes:
            p.terminate()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    
    # Wait for processes
    for p in processes:
        p.wait()


def main():
    parser = argparse.ArgumentParser(
        description="Stream Simulator - Multi-protocol video streaming",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py http                    # Start HTTP/MJPEG server
  python run.py webrtc                  # Start WebRTC server  
  python run.py all                     # Start all servers
  python run.py http --video /path.mp4  # Custom video file
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Server type")
    
    # Common arguments
    def add_common_args(p):
        p.add_argument("--video", type=str, help="Video file path")
        p.add_argument("--port", type=int, help="Server port")
        p.add_argument("--no-loop", action="store_true", help="Don't loop video")
    
    # HTTP subcommand
    http_parser = subparsers.add_parser("http", help="HTTP/MJPEG stream server")
    add_common_args(http_parser)
    
    # WebRTC subcommand
    webrtc_parser = subparsers.add_parser("webrtc", help="WebRTC stream server")
    add_common_args(webrtc_parser)
    
    # RTSP subcommand
    rtsp_parser = subparsers.add_parser("rtsp", help="RTSP stream server")
    add_common_args(rtsp_parser)
    
    # All subcommand
    all_parser = subparsers.add_parser("all", help="Start all servers")
    all_parser.add_argument("--video", type=str, help="Video file path")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Change to script directory
    os.chdir(Path(__file__).parent)
    
    try:
        if args.command == "http":
            run_http(args)
        elif args.command == "webrtc":
            run_webrtc(args)
        elif args.command == "rtsp":
            run_rtsp(args)
        elif args.command == "all":
            run_all(args)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()





