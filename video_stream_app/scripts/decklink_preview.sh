#!/bin/bash

set -euo pipefail

CONNECTION="${1:-hdmi}"
MODE="${2:-auto}"
DEVICE="${DECKLINK_DEVICE:-0}"

case "$CONNECTION" in
    hdmi|sdi|auto) ;;
    *)
        echo "ERROR: connection must be hdmi, sdi, or auto"
        echo "Usage: $0 [hdmi|sdi|auto] [mode]"
        exit 2
        ;;
esac

case "$MODE" in
    auto|ntsc|ntsc2398|pal|ntsc-p|pal-p|1080p2398|1080p24|1080p25|1080p2997|1080p30|1080i50|1080i5994|1080i60|1080p50|1080p5994|1080p60|720p50|720p5994|720p60|2160p2398|2160p24|2160p25|2160p2997|2160p30|2160p50|2160p5994|2160p60) ;;
    *)
        echo "ERROR: unsupported DeckLink mode: $MODE"
        exit 2
        ;;
esac

if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    echo "ERROR: no local graphical session was detected."
    echo "Run this in a terminal opened on the server's Ubuntu desktop."
    exit 3
fi

if [ ! -e "/dev/blackmagic/io${DEVICE}" ]; then
    echo "ERROR: /dev/blackmagic/io${DEVICE} does not exist."
    exit 3
fi

echo "DeckLink standalone preview"
echo "Device:     $DEVICE"
echo "Connection: ${CONNECTION^^}"
echo "Mode:       $MODE"
echo "Close the preview window or press Ctrl+C in this terminal to stop."
echo "Do not run this preview and the SurgR1 app at the same time."
echo

gst-launch-1.0 -v \
    decklinkvideosrc \
        device-number="$DEVICE" \
        connection="$CONNECTION" \
        mode="$MODE" \
        buffer-size=2 \
        drop-no-signal-frames=true \
    ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 \
    ! deinterlace mode=auto method=linear fields=all \
    ! videoconvert n-threads=4 \
    ! glimagesink sync=false qos=false force-aspect-ratio=true
