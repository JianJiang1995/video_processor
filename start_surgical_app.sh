#!/usr/bin/env bash

# Start the complete local analysis stack and the Electron desktop client.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="$ROOT_DIR/../.venv/bin/python"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8001}"
VLM_URL="${VLM_URL:-http://127.0.0.1:8010/v1}"
VLM_PROXY_URL="${VLM_PROXY_URL:-http://127.0.0.1:8012/v1}"
SURGR1_URL="http://127.0.0.1:9003"
LOG_DIR="$ROOT_DIR/logs/desktop_launcher"
RUNTIME_DIR="$ROOT_DIR/.runtime"
LOCK_FILE="$RUNTIME_DIR/surgical_app.lock"

RUN_PREFLIGHT=0
SERVICES_ONLY=0
KEEP_SERVICES=0
STATUS_ONLY=0
STOP_MODE=0
STARTED_NAMES=()
STARTED_PIDS=()
CLEANUP_STARTED=0

usage() {
    cat <<'EOF'
Usage: ./start_surgical_app.sh [option]

Options:
  --preflight       Test the DeckLink input before starting.
  --services-only   Start model services and backend without Electron.
  --keep-services   Leave services running after Electron closes.
  --status          Print the current service status and exit.
  --stop            Stop services previously started by this launcher.
  -h, --help        Show this help.
EOF
}

for argument in "$@"; do
    case "$argument" in
        --preflight) RUN_PREFLIGHT=1 ;;
        --services-only) SERVICES_ONLY=1; KEEP_SERVICES=1 ;;
        --keep-services) KEEP_SERVICES=1 ;;
        --status) STATUS_ONLY=1 ;;
        --stop) STOP_MODE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $argument" >&2; usage; exit 2 ;;
    esac
done

mkdir -p "$LOG_DIR" "$RUNTIME_DIR"

http_ready() {
    curl --noproxy '*' -fsS --max-time 3 "$1" >/dev/null 2>&1
}

tcp_listening() {
    local port="$1"
    ss -ltn 2>/dev/null | grep -qE "[:.]${port}[[:space:]]"
}

service_line() {
    local label="$1"
    local url="$2"
    if http_ready "$url"; then
        printf '  [READY] %-24s %s\n' "$label" "$url"
    else
        printf '  [OFF]   %-24s %s\n' "$label" "$url"
    fi
}

show_status() {
    echo "SurgR1 local runtime status"
    echo ""
    if mysqladmin ping -h 127.0.0.1 --silent >/dev/null 2>&1; then
        printf '  [READY] %-24s %s\n' "MySQL" "127.0.0.1:3306"
    else
        printf '  [OFF]   %-24s %s\n' "MySQL" "127.0.0.1:3306"
    fi
    service_line "Qwen3-VL (vLLM)" "$VLM_URL/models"
    service_line "VLM local proxy" "$VLM_PROXY_URL/models"
    service_line "SurgR1 tissue/action" "$SURGR1_URL/health"
    service_line "FastAPI backend" "$BACKEND_URL/api/health"
    echo ""
    echo "Optional integrations (not installed on this machine):"
    service_line "SAM3 segmentation" "http://127.0.0.1:9004/health"
    service_line "CosyVoice TTS" "http://127.0.0.1:50000/health"
    if tcp_listening 8765; then
        printf '  [READY] %-24s %s\n' "FunASR" "127.0.0.1:8765"
    else
        printf '  [OFF]   %-24s %s\n' "FunASR" "127.0.0.1:8765"
    fi
}

pid_file() {
    printf '%s/%s.pid' "$RUNTIME_DIR" "$1"
}

stop_pid_group() {
    local name="$1"
    local pid="$2"

    if ! kill -0 -- "-$pid" 2>/dev/null && ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$(pid_file "$name")"
        return
    fi

    echo "Stopping $name (PID $pid)..."
    kill -INT -- "-$pid" 2>/dev/null || kill -INT "$pid" 2>/dev/null || true
    for _ in $(seq 1 40); do
        if ! kill -0 -- "-$pid" 2>/dev/null && ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$(pid_file "$name")"
            return
        fi
        sleep 0.25
    done
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
        if ! kill -0 -- "-$pid" 2>/dev/null && ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$(pid_file "$name")"
            return
        fi
        sleep 0.25
    done
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    rm -f "$(pid_file "$name")"
}

stop_managed_services() {
    local name file pid
    for name in backend surgr1 vlm_proxy vlm; do
        file="$(pid_file "$name")"
        if [ -s "$file" ]; then
            pid="$(cat "$file")"
            if [[ "$pid" =~ ^[0-9]+$ ]]; then
                stop_pid_group "$name" "$pid"
            else
                rm -f "$file"
            fi
        fi
    done
}

if [ "$STATUS_ONLY" = "1" ]; then
    show_status
    exit 0
fi

if [ "$STOP_MODE" = "1" ]; then
    stop_managed_services
    exit 0
fi

# Keep a second desktop click from creating another Electron/Vite stack.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "The SurgR1 launcher is already running."
    if command -v xdotool >/dev/null 2>&1; then
        xdotool search --onlyvisible --class '^video-stream-analyzer$' \
            windowactivate 2>/dev/null || true
    fi
    echo "Use ./start_surgical_app.sh --status to inspect it."
    exit 0
fi

register_process() {
    local name="$1"
    local pid="$2"
    STARTED_NAMES+=("$name")
    STARTED_PIDS+=("$pid")
    printf '%s\n' "$pid" >"$(pid_file "$name")"
}

cleanup() {
    local index
    if [ "$KEEP_SERVICES" = "1" ] || [ "$CLEANUP_STARTED" = "1" ]; then
        return
    fi
    CLEANUP_STARTED=1
    trap - EXIT INT TERM
    set +e
    for ((index=${#STARTED_PIDS[@]}-1; index>=0; index--)); do
        stop_pid_group "${STARTED_NAMES[$index]}" "${STARTED_PIDS[$index]}"
    done
}

handle_signal() {
    local exit_code="$1"
    cleanup
    exit "$exit_code"
}

trap cleanup EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM

wait_for_http() {
    local label="$1"
    local url="$2"
    local timeout="$3"
    local pid="${4:-}"
    local log_file="${5:-}"
    local elapsed=0

    while [ "$elapsed" -lt "$timeout" ]; do
        if http_ready "$url"; then
            echo "[READY] $label"
            return 0
        fi
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            echo "ERROR: $label exited during startup." >&2
            [ -n "$log_file" ] && tail -n 40 "$log_file" 2>/dev/null || true
            return 1
        fi
        sleep 1
        elapsed=$((elapsed + 1))
        if [ $((elapsed % 15)) -eq 0 ]; then
            echo "  Waiting for $label (${elapsed}s)..."
        fi
    done

    echo "ERROR: $label did not become ready within ${timeout}s." >&2
    [ -n "$log_file" ] && tail -n 40 "$log_file" 2>/dev/null || true
    return 1
}

require_free_port() {
    local port="$1"
    local label="$2"
    if tcp_listening "$port"; then
        echo "ERROR: port $port is occupied by an unhealthy/non-$label process." >&2
        lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
        exit 5
    fi
}

best_free_gpu() {
    nvidia-smi --query-gpu=index,memory.free --format=csv,noheader,nounits 2>/dev/null \
        | awk -F',' '{gsub(/ /, "", $1); gsub(/ /, "", $2); print $1, $2}' \
        | sort -k2,2nr \
        | awk -v excluded="${1:-}" '$1 != excluded {print $1; exit}'
}

if [ "$SERVICES_ONLY" != "1" ] && [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    echo "ERROR: launch this from the local Ubuntu desktop session." >&2
    exit 3
fi

echo "============================================================"
echo "  SurgR1 local video understanding application"
echo "============================================================"

if [ "$RUN_PREFLIGHT" = "1" ]; then
    echo "Running DeckLink HDMI/auto preflight..."
    "$ROOT_DIR/scripts/decklink_preflight.sh" --connection hdmi --mode auto --wait 6 || {
        echo "WARN: no supported input frame was detected; the app will wait for input."
    }
fi

if ! mysqladmin ping -h 127.0.0.1 --silent >/dev/null 2>&1; then
    echo "Starting MySQL..."
    systemctl start mysql 2>/dev/null || true
    for _ in $(seq 1 15); do
        mysqladmin ping -h 127.0.0.1 --silent >/dev/null 2>&1 && break
        sleep 1
    done
fi
if ! mysqladmin ping -h 127.0.0.1 --silent >/dev/null 2>&1; then
    echo "ERROR: MySQL is unavailable. Start it with: sudo systemctl start mysql" >&2
    exit 4
fi
echo "[READY] MySQL"

if [ ! -x "$PYTHON_BIN" ]; then
    echo "ERROR: project Python environment is missing: $PYTHON_BIN" >&2
    exit 4
fi

SURGR1_ENABLED="$($PYTHON_BIN -c 'import json,sys; c=json.load(open(sys.argv[1]))["services"]["surgr1"]; print(int(c.get("enabled",False)))' "$ROOT_DIR/config.json")"
SURGR1_GPU="${SURGR1_GPU:-$($PYTHON_BIN -c 'import json,sys; print(json.load(open(sys.argv[1]))["services"]["surgr1"].get("gpu",0))' "$ROOT_DIR/config.json")}"

if http_ready "$VLM_URL/models"; then
    echo "[READY] Reusing Qwen3-VL at $VLM_URL"
else
    require_free_port 8010 "VLM"
    if [ "$SURGR1_ENABLED" = "1" ]; then
        VLM_GPU="${VLM_GPU:-$(best_free_gpu "$SURGR1_GPU")}"
    else
        VLM_GPU="${VLM_GPU:-$(best_free_gpu)}"
    fi
    if [ -z "$VLM_GPU" ]; then
        echo "ERROR: no NVIDIA GPU was detected." >&2
        exit 4
    fi
    VLM_LOG="$LOG_DIR/vlm.log"
    echo "Starting Qwen3-VL-8B-Instruct on GPU $VLM_GPU; log: $VLM_LOG"
    setsid env VLM_GPU="$VLM_GPU" bash "$ROOT_DIR/scripts/run_local_vlm_vllm.sh" \
        >"$VLM_LOG" 2>&1 9>&- &
    VLM_PID=$!
    register_process vlm "$VLM_PID"
    wait_for_http "Qwen3-VL (vLLM)" "$VLM_URL/models" 300 "$VLM_PID" "$VLM_LOG"
fi

if http_ready "$VLM_PROXY_URL/models"; then
    echo "[READY] Reusing VLM proxy at $VLM_PROXY_URL"
else
    require_free_port 8012 "VLM proxy"
    VLM_PROXY_LOG="$LOG_DIR/vlm_proxy.log"
    echo "Starting local VLM proxy; log: $VLM_PROXY_LOG"
    setsid "$PYTHON_BIN" "$ROOT_DIR/scripts/local_vlm_balancer.py" \
        --host 127.0.0.1 --port 8012 --upstream "$VLM_URL" \
        >"$VLM_PROXY_LOG" 2>&1 9>&- &
    VLM_PROXY_PID=$!
    register_process vlm_proxy "$VLM_PROXY_PID"
    wait_for_http "VLM local proxy" "$VLM_PROXY_URL/models" 30 "$VLM_PROXY_PID" "$VLM_PROXY_LOG"
fi

if [ "$SURGR1_ENABLED" = "1" ]; then
    if http_ready "$SURGR1_URL/health"; then
        echo "[READY] Reusing local SurgR1 at $SURGR1_URL"
    else
        require_free_port 9003 "SurgR1"
        SURGR1_LOG="$LOG_DIR/surgr1.log"
        echo "Starting SurgR1 on GPU $SURGR1_GPU; log: $SURGR1_LOG"
        setsid env SURGR1_GPU="$SURGR1_GPU" \
            LD_LIBRARY_PATH="$ROOT_DIR/../.venv/lib/python3.10/site-packages/nvidia/cu13/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
            "$PYTHON_BIN" "$ROOT_DIR/scripts/serve_surgr1_local.py" >"$SURGR1_LOG" 2>&1 9>&- &
        SURGR1_PID=$!
        register_process surgr1 "$SURGR1_PID"
        wait_for_http "SurgR1" "$SURGR1_URL/health" 300 "$SURGR1_PID" "$SURGR1_LOG"
    fi
fi

if http_ready "$BACKEND_URL/api/health"; then
    echo "[READY] Reusing FastAPI backend at $BACKEND_URL"
else
    require_free_port 8001 "backend"
    EXPERT_GPU="${EXPERT_GPU:-$(best_free_gpu)}"
    if [ -z "$EXPERT_GPU" ]; then
        echo "ERROR: no NVIDIA GPU is available for expert models." >&2
        exit 4
    fi
    BACKEND_LOG="$LOG_DIR/backend.log"
    echo "Starting expert models on GPU $EXPERT_GPU and backend; log: $BACKEND_LOG"
    setsid env \
        DISABLE_EXTERNAL_AI=1 \
        DISABLE_EMBEDDINGS=1 \
        GLM_API_URL="$VLM_PROXY_URL" \
        YOLO_DEVICE="cuda:$EXPERT_GPU" \
        CLIP_DETECTOR_DEVICE="cuda:$EXPERT_GPU" \
        PHASE_DEVICE="cuda:$EXPERT_GPU" \
        TRIPLET_DEVICE="cuda:$EXPERT_GPU" \
        bash "$ROOT_DIR/run_backend.sh" >"$BACKEND_LOG" 2>&1 9>&- &
    BACKEND_PID=$!
    register_process backend "$BACKEND_PID"
    wait_for_http "FastAPI backend" "$BACKEND_URL/api/health" 120 "$BACKEND_PID" "$BACKEND_LOG"

    expert_wait=0
    while [ "$expert_wait" -lt 90 ]; do
        if grep -Fq "[Startup] Experts preloaded" "$BACKEND_LOG" 2>/dev/null; then
            expert_line="$(grep -F "[Startup] Experts preloaded" "$BACKEND_LOG" | tail -n 1)"
            echo "$expert_line"
            if echo "$expert_line" | grep -Fq "False"; then
                echo "ERROR: one or more expert models failed to load." >&2
                exit 6
            fi
            break
        fi
        if grep -Fq "[Startup] Expert preload failed" "$BACKEND_LOG" 2>/dev/null; then
            tail -n 40 "$BACKEND_LOG" >&2
            exit 6
        fi
        sleep 1
        expert_wait=$((expert_wait + 1))
    done
    if [ "$expert_wait" -ge 90 ]; then
        echo "ERROR: expert model preload did not complete within 90 seconds." >&2
        tail -n 40 "$BACKEND_LOG" >&2
        exit 6
    fi
fi

echo ""
show_status
echo ""
echo "Logs: $LOG_DIR"

if [ "$SERVICES_ONLY" = "1" ]; then
    echo "Services will remain running. Stop them with: $0 --stop"
    exit 0
fi

echo "Starting Electron..."
bash "$ROOT_DIR/run_electron_local.sh"
