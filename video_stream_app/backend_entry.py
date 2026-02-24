#!/usr/bin/env python3
"""
Standalone backend entry point for PyInstaller packaging.
This file is the single entry that PyInstaller compiles into an executable.
"""
import sys
import os
import signal

# When running as PyInstaller bundle, adjust paths
if getattr(sys, 'frozen', False):
    # Running as compiled executable
    BASE_DIR = os.path.dirname(sys.executable)
    os.environ.setdefault('FROZEN_APP', '1')
    # Ensure the bundled data directory is on the path
    bundle_dir = getattr(sys, '_MEIPASS', BASE_DIR)
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Set working directory
os.chdir(BASE_DIR)

# Ensure PYTHONPATH includes our base
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main():
    import uvicorn

    # Parse port from args or env
    port = int(os.environ.get('BACKEND_PORT', '8001'))
    host = os.environ.get('BACKEND_HOST', '127.0.0.1')

    for arg in sys.argv[1:]:
        if arg.startswith('--port='):
            port = int(arg.split('=')[1])
        elif arg.startswith('--host='):
            host = arg.split('=')[1]

    print(f"[Backend] Starting on {host}:{port}")
    print(f"[Backend] Base dir: {BASE_DIR}")
    print(f"[Backend] Frozen: {getattr(sys, 'frozen', False)}")

    # Graceful shutdown
    def handle_signal(signum, frame):
        print(f"\n[Backend] Received signal {signum}, shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
        workers=1,
    )


if __name__ == "__main__":
    main()
