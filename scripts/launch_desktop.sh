#!/usr/bin/env bash
set -uo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs/desktop_launcher"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/launch_$(date +%Y%m%d_%H%M%S)_$$.log"

bash "$ROOT_DIR/start_surgical_app.sh" "$@" 2>&1 | tee "$LOG_FILE"
result=${PIPESTATUS[0]}
if [ "$result" -ne 0 ]; then
    printf '\nSurgR1 startup failed (%s). Log: %s\n' "$result" "$LOG_FILE" >&2
    if [ -t 0 ]; then
        read -r -p 'Press Enter to close this window.' || true
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send -u critical 'SurgR1 startup failed' "See $LOG_FILE" || true
    fi
fi
exit "$result"
