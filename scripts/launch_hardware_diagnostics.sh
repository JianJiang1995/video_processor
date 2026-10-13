#!/usr/bin/env bash
set -uo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONUNBUFFERED=1
python3 "$ROOT_DIR/scripts/hardware_diagnostics.py" "$@"
result=$?
printf '\n'
if [ -t 0 ]; then
    while read -r -p '按 S 检测输入信号，按 R 打开报告目录，直接回车关闭：' choice; do
        case "$choice" in
            s|S)
                printf '\n请先停止软件中的采集或独立预览；检测不会主动停止其他服务。\n'
                args=()
                for arg in "$@"; do
                    [[ "$arg" == '--no-signal' ]] || args+=("$arg")
                done
                python3 "$ROOT_DIR/scripts/hardware_diagnostics.py" "${args[@]}"
                result=$?
                ;;
            r|R)
                xdg-open "$ROOT_DIR/logs/hardware_diagnostics" >/dev/null 2>&1 || printf '无法打开报告目录。\n'
                ;;
            '') break ;;
            *) printf '请输入 S、R 或直接回车。\n' ;;
        esac
    done
fi
exit "$result"
