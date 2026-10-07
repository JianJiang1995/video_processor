import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from backend.routers import video
from backend.services.capture_device_info import decklink_device_profile, unavailable_decklink_profiles


class CaptureDiscoveryTests(unittest.TestCase):
    def test_detected_card_keeps_manual_1080p50_and_sdi(self):
        # Exercise the actual enumerator, not just the mode parser or a UI fixture.
        cap = Mock()
        cap.isOpened.return_value = False
        paths = lambda pattern: ["/dev/blackmagic/io0"] if "blackmagic" in pattern else []
        with patch.object(video.glob, "glob", side_effect=paths), \
                patch.object(video.subprocess, "run", return_value=Mock(returncode=0)), \
                patch.object(video.cv2, "VideoCapture", return_value=cap), \
                patch.object(video, "_simulator_capture_device", return_value={"backend": "simulator"}):
            devices = video._list_capture_devices()
        self.assertEqual(devices[0]["backend"], "decklink")
        self.assertTrue(devices[0]["hardware_ready"])
        self.assertIn("1080p50", devices[0]["supported_modes"])
        self.assertIn("1080i50", devices[0]["supported_modes"])
        self.assertIn("sdi", devices[0]["supported_connections"])
        self.assertEqual(unavailable_decklink_profiles(devices), [])

    def test_missing_card_is_configuration_not_detected_hardware(self):
        with TemporaryDirectory() as folder:
            profiles = unavailable_decklink_profiles([], pci_root=Path(folder))
        self.assertFalse(profiles[0]["available"])
        self.assertEqual(profiles[0]["unavailable_reason"], "decklinkPciMissing")
        self.assertIn("1080p50", profiles[0]["supported_modes"])

    def test_pci_present_without_device_node_reports_driver(self):
        with TemporaryDirectory() as folder:
            pci = Path(folder) / "pci" / "17:00.0"
            pci.mkdir(parents=True)
            (pci / "vendor").write_text("0xbdbd\n")
            profiles = unavailable_decklink_profiles([], pci_root=pci.parent,
                                                     device_root=Path(folder) / "missing")
        self.assertEqual(profiles[0]["unavailable_reason"], "decklinkDriverMissing")

    def test_device_count_does_not_include_offline_profiles(self):
        with patch.object(video, "_list_capture_devices", return_value=[]):
            result = asyncio.run(video.list_capture_devices())
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)
        self.assertFalse(result["unavailable_devices"][0]["hardware_ready"])

    def test_scan_failure_is_not_reported_as_success(self):
        with patch.object(video, "_list_capture_devices", side_effect=asyncio.TimeoutError):
            result = asyncio.run(video.list_capture_devices())
        self.assertFalse(result["success"])
        self.assertEqual(result["scan_error"], "timeout")
        self.assertEqual(result["unavailable_devices"], [])

    def test_mode_profiles_are_independent(self):
        first = decklink_device_profile()
        first["supported_modes"].clear()
        self.assertIn("1080p50", decklink_device_profile()["supported_modes"])
