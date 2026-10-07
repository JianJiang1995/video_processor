#!/usr/bin/env python3
"""Install the local app launcher in the desktop and application menu."""
import argparse
import configparser
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FILENAME = "surgr1-video-analyzer.desktop"


def exec_argument(value):
    value = str(value).replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    return '"' + value + '"'


def install_shortcuts(root, desktop, applications, trust=True):
    entry = configparser.ConfigParser(interpolation=None)
    entry.optionxform = str
    entry["Desktop Entry"] = {
        "Version": "1.0", "Type": "Application",
        "Name": "SurgR1 视频理解", "Name[en]": "SurgR1 Video Analysis",
        "Comment": "启动本地模型、分析服务和 Electron 界面",
        "Exec": f'/usr/bin/bash {exec_argument(root / "scripts/launch_desktop.sh")}',
        "Icon": str(root / "frontend/electron/icon.svg"),
        "Path": str(root), "Terminal": "true", "Categories": "Science;MedicalSoftware;",
        "StartupNotify": "true", "StartupWMClass": "video-stream-analyzer",
    }
    paths = []
    for folder in (desktop, applications):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / FILENAME
        with path.open("w", encoding="utf-8") as output:
            entry.write(output, space_around_delimiters=False)
        path.chmod(0o755)
        if shutil.which("desktop-file-validate"):
            subprocess.run(["desktop-file-validate", str(path)], check=True)
        paths.append(path)
    if trust and shutil.which("gio"):
        result = subprocess.run(["gio", "set", str(paths[0]), "metadata::trusted", "true"],
                                capture_output=True, text=True)
        if result.returncode:
            print("Desktop trust metadata unavailable; right-click the shortcut and allow launching.")
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(applications)], check=False)
    return paths


def desktop_directory():
    if shutil.which("xdg-user-dir"):
        result = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    return Path.home() / "Desktop"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop-dir", type=Path, default=desktop_directory())
    parser.add_argument("--applications-dir", type=Path,
                        default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "applications")
    args = parser.parse_args()
    for installed in install_shortcuts(ROOT, args.desktop_dir, args.applications_dir):
        print(installed)
