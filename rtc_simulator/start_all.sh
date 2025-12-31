#!/bin/bash
#
# Stream Simulator - Start All Servers
#
# Starts HTTP/MJPEG and WebRTC stream servers for testing video_stream_app
#
# Usage:
#   ./start_all.sh                    # Use default test video
#   ./start_all.sh /path/to/video.mp4 # Use specific video
#

set -e
cd "$(dirname "$0")"

# Default video path
VIDEO_PATH="${1:-/data2/jj/proj/video_processor/test_data/2024-12-24_225315_VID002.mp4}"

# Ports
HTTP_PORT=9001
WEBRTC_PORT=9002

# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║              Stream Simulator - Multi-Protocol               ║${NC}"
echo -e "${CYAN}╠══════════════════════════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  Video: ${GREEN}$(basename "$VIDEO_PATH")${NC}"
echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}HTTP/MJPEG${NC}  http://localhost:${HTTP_PORT}/stream              ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  ${YELLOW}WebRTC${NC}      http://localhost:${WEBRTC_PORT}                       ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}  Use in video_stream_app:                                    ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}    1. Select \"实时视频流\" mode                                ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}    2. Enter: http://localhost:${HTTP_PORT}/stream               ${CYAN}║${NC}"
echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Check if video exists
if [ ! -f "$VIDEO_PATH" ]; then
    echo -e "${YELLOW}Warning: Video not found: $VIDEO_PATH${NC}"
    echo "Please provide a valid video path."
    exit 1
fi

# Function to check and kill process on port
kill_port() {
    local port=$1
    local pid=$(lsof -t -i:$port 2>/dev/null)
    if [ -n "$pid" ]; then
        echo -e "${YELLOW}Port $port is in use (PID: $pid), stopping...${NC}"
        kill $pid 2>/dev/null || true
        sleep 1
        # Force kill if still running
        if kill -0 $pid 2>/dev/null; then
            kill -9 $pid 2>/dev/null || true
        fi
        echo -e "${GREEN}Port $port released.${NC}"
    fi
}

# Check and release ports if already in use
echo "Checking if ports are already in use..."
kill_port $HTTP_PORT
kill_port $WEBRTC_PORT

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "Stopping servers..."
    kill $HTTP_PID $WEBRTC_PID 2>/dev/null || true
    exit 0
}

trap cleanup SIGINT SIGTERM

# Start HTTP/MJPEG server
echo "Starting HTTP/MJPEG server on port $HTTP_PORT..."
python http_server.py --video "$VIDEO_PATH" --port $HTTP_PORT &
HTTP_PID=$!

# Wait a moment
sleep 1

# Start WebRTC server
echo "Starting WebRTC server on port $WEBRTC_PORT..."
python webrtc_server.py --video "$VIDEO_PATH" --port $WEBRTC_PORT &
WEBRTC_PID=$!

echo ""
echo -e "${GREEN}All servers started!${NC}"
echo "Press Ctrl+C to stop all servers."
echo ""

# Wait for any child to exit
wait



