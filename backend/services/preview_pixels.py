"""Uncompressed preview packets for an acknowledged loopback-only display."""
import struct
import cv2


HEADER = struct.Struct("<4sIIIdfI")


def pack_bgr_frame(frame, sequence, read_at_ms, fps):
    height, width, channels = frame.shape
    if channels != 3 or frame.dtype.name != "uint8":
        raise ValueError("Preview requires uint8 BGR pixels")
    pixels = frame.tobytes(order="C")
    return HEADER.pack(
        b"SRP1", sequence & 0xFFFFFFFF, width, height,
        read_at_ms, fps, len(pixels),
    ) + pixels


def pack_i420_frame(frame, sequence, read_at_ms, fps):
    height, width = frame.shape[:2]
    if width % 2 or height % 2:
        return pack_bgr_frame(frame, sequence, read_at_ms, fps)
    # OpenCV I420 is BT.601 limited-range. The shader uses the same matrix.
    pixels = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420).tobytes()
    return HEADER.pack(b'SRP2', sequence & 0xFFFFFFFF, width, height,
                       read_at_ms, fps, len(pixels)) + pixels
