#!/usr/bin/env python3
"""Preflight the exact DeckLink capture path used by the application."""

import argparse
import importlib.util
import json
import shutil
import subprocess
import time
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "backend" / "services" / "decklink_capture.py"
MODULE_SPEC = importlib.util.spec_from_file_location("decklink_capture_preflight", MODULE_PATH)
DECKLINK_MODULE = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(DECKLINK_MODULE)
DeckLinkCapture = DECKLINK_MODULE.DeckLinkCapture
build_decklink_uri = DECKLINK_MODULE.build_decklink_uri


COMMON_MODES = (
    "auto",
    "1080i5994",
    "1080i60",
    "1080i50",
    "1080p5994",
    "1080p60",
    "1080p50",
    "1080p30",
    "720p5994",
    "720p60",
    "720p50",
)


def _command_check(command, contains=None):
    executable = shutil.which(command[0])
    if not executable:
        return False, f"command not found: {command[0]}"
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=8)
    except Exception as exc:
        return False, str(exc)
    output = (result.stdout + "\n" + result.stderr).strip()
    ok = result.returncode == 0 and (not contains or contains.lower() in output.lower())
    return ok, output


def _print_check(ok, label, detail=""):
    marker = "OK" if ok else "FAIL"
    print(f"[{marker}] {label}")
    if detail:
        useful_lines = [line.strip() for line in detail.splitlines() if line.strip()]
        for line in useful_lines[-4:]:
            print(f"       {line}")


def _try_capture(device, connection, mode, wait_seconds, output_path):
    uri = build_decklink_uri(device, mode=mode, connection=connection)
    capture = None
    deadline = time.monotonic() + wait_seconds
    final_status = None
    try:
        capture = DeckLinkCapture(uri)
        if not capture.isOpened():
            return False, capture.status() or {"phase": "error", "last_error": "pipeline did not open"}

        while time.monotonic() < deadline:
            remaining = max(0.1, min(1.0, deadline - time.monotonic()))
            ok, frame = capture.read(timeout=remaining)
            final_status = capture.status()
            if not ok or frame is None:
                continue

            output_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(output_path), frame)
            final_status = capture.status()
            final_status["snapshot"] = str(output_path.resolve())
            return True, final_status
        return False, final_status or capture.status() or {"phase": "unknown"}
    finally:
        if capture is not None:
            capture.release()
        time.sleep(0.35)


def main():
    parser = argparse.ArgumentParser(
        description="Check DeckLink driver, GStreamer plugin, input signal, format, and black-frame status."
    )
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--connection", choices=("hdmi", "sdi", "auto"), default="hdmi")
    parser.add_argument("--mode", default="auto")
    parser.add_argument("--wait", type=float, default=6.0, help="seconds to wait per mode")
    parser.add_argument("--scan-common", action="store_true", help="try common HD modes after auto detection")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "tmp" / "decklink_preflight.jpg",
    )
    parser.add_argument("--json", action="store_true", help="print final status as JSON")
    args = parser.parse_args()

    print("DeckLink capture preflight")
    print(f"Device {args.device} | {args.connection.upper()} | mode={args.mode}")
    print()

    node = Path(f"/dev/blackmagic/io{args.device}")
    node_ok = node.exists()
    _print_check(node_ok, f"driver node {node}")

    plugin_ok, plugin_output = _command_check(["gst-inspect-1.0", "decklinkvideosrc"], "Decklink Video Source")
    _print_check(plugin_ok, "GStreamer decklinkvideosrc plugin", "" if plugin_ok else plugin_output)

    firmware_ok, firmware_output = _command_check(["BlackmagicFirmwareUpdater", "status"], "OK")
    _print_check(firmware_ok, "Blackmagic firmware/driver status", firmware_output)

    if not node_ok or not plugin_ok:
        print("\nRESULT: DRIVER_NOT_READY")
        return 3

    modes = [args.mode]
    if args.scan_common:
        modes.extend(mode for mode in COMMON_MODES if mode not in modes)

    final_status = None
    for mode in modes:
        print(f"\n[TRY] {args.connection.upper()} / {mode} ({args.wait:.1f}s)")
        ok, status = _try_capture(args.device, args.connection, mode, args.wait, args.output)
        final_status = status
        if ok:
            resolution = f"{status.get('width')}x{status.get('height')} @ {status.get('fps')} fps"
            _print_check(True, f"input locked: {resolution}")
            if status.get("near_black"):
                _print_check(False, "valid timing but image is nearly black")
            _print_check(True, f"snapshot: {status.get('snapshot')}")
            if args.json:
                print(json.dumps(status, ensure_ascii=False, indent=2))
            print("\nRESULT: PASS")
            return 0

        detail = status.get("last_error") or "No supported input signal or no frames received"
        _print_check(False, f"no frame with {args.connection.upper()} / {mode}", detail)

    if args.json and final_status:
        print(json.dumps(final_status, ensure_ascii=False, indent=2))
    print("\nRESULT: NO_SUPPORTED_SIGNAL")
    print("Check DeckLink IN and the selected connector. A 1280x1024 SXGA source requires a scaler to 1080p/720p.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
