"""Capture configuration remains available independently of hardware detection."""
from pathlib import Path


DECKLINK_MODES = (
    "auto",
    "1080p5994", "1080p60", "1080p50", "1080p30", "1080p2997", "1080p25", "1080p24",
    "1080i5994", "1080i60", "1080i50",
    "720p60", "720p5994", "720p50",
    "2160p30", "2160p2997", "2160p25", "2160p24",
)


def decklink_device_profile(device_number=0, device_path="/dev/blackmagic/io0", *, available=True):
    return {
        "device_id": device_number,
        "device_name": "DeckLink Mini Recorder 4K",
        "device_path": device_path,
        "width": 1920, "height": 1080, "fps": 30.0,
        "backend": "decklink",
        "default_mode": "auto",
        "supported_modes": list(DECKLINK_MODES),
        "default_connection": "hdmi",
        "supported_connections": ["hdmi", "sdi", "auto"],
        "hardware_ready": available,
        "available": available,
    }


def unavailable_decklink_profiles(devices, *, pci_root=Path("/sys/bus/pci/devices"),
                                 device_root=Path("/dev/blackmagic")):
    if any(device.get("backend") == "decklink" for device in devices):
        return []
    reason = "decklinkUnavailable"
    try:
        if pci_root.is_dir():
            present = any(path.read_text().strip().lower() == "0xbdbd"
                          for path in pci_root.glob("*/vendor"))
            if not present:
                reason = "decklinkPciMissing"
            elif not list(device_root.glob("io*")):
                reason = "decklinkDriverMissing"
    except OSError:
        pass
    return [{**decklink_device_profile(available=False), "unavailable_reason": reason}]
