import configparser
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("install_shortcut", Path(__file__).resolve().parents[2]
                                            / "scripts/install_desktop_shortcut.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class DesktopShortcutTests(unittest.TestCase):
    def test_installs_executable_entries_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "project with spaces"
            for _ in range(2):
                paths = installer.install_shortcuts(root, Path(folder) / "Desktop",
                                                    Path(folder) / "applications", trust=False)
                self.assertEqual(len(paths), 2)
                for path in paths:
                    entry = configparser.ConfigParser(interpolation=None)
                    entry.read(path)
                    self.assertTrue(path.stat().st_mode & 0o111)
                    self.assertIn('"' + str(root / "scripts/launch_desktop.sh") + '"',
                                  entry["Desktop Entry"]["Exec"])
                    self.assertEqual(entry["Desktop Entry"]["StartupWMClass"], "video-stream-analyzer")
