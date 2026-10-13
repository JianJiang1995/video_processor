#!/usr/bin/env python3
"""Install the GPU/capture diagnostic launcher on the desktop and app menu."""
import configparser
import os
from pathlib import Path
import shutil
import subprocess
from install_desktop_shortcut import ROOT, desktop_directory, exec_argument


def install():
    desktop = desktop_directory()
    applications = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'applications'
    entry = configparser.ConfigParser(interpolation=None)
    entry.optionxform = str
    entry['Desktop Entry'] = {
        'Version': '1.0', 'Type': 'Application',
        'Name': 'GPU与采集卡检测', 'Name[en]': 'GPU and Capture Diagnostics',
        'Comment': '快速检查 GPU、CUDA 和采集卡驱动；可选输入信号检测并保存报告',
        'Exec': f'/usr/bin/bash {exec_argument(ROOT / "scripts/launch_hardware_diagnostics.sh")} --no-signal',
        'Icon': 'utilities-system-monitor', 'Path': str(ROOT),
        'Terminal': 'true', 'Categories': 'System;Monitor;', 'StartupNotify': 'false',
    }
    for folder in (desktop, applications):
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / 'surgr1-hardware-diagnostics.desktop'
        with target.open('w', encoding='utf-8') as file:
            entry.write(file, space_around_delimiters=False)
        target.chmod(0o755)
        if shutil.which('desktop-file-validate'):
            subprocess.run(['desktop-file-validate', str(target)], check=True)
        if folder == desktop and shutil.which('gio'):
            result = subprocess.run(['gio', 'set', str(target), 'metadata::trusted', 'true'], capture_output=True, text=True)
            if result.returncode:
                print('桌面信任标记未设置，请右键此图标选择“允许启动”。')
        print(target)
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', str(applications)], check=False)


if __name__ == '__main__':
    install()
