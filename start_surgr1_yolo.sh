#!/bin/bash
# ============================================================================
#  SurgR1 + Experts (YOLO / Phase / Triplet) Startup Script
#
#  Auto-detects 4 distinct GPUs for the pipeline:
#    SurgR1    ~60 GB  → picks GPU with most free memory
#    YOLO       ~1 GB  → next free GPU
#    Phase     ~0.5 GB → another free GPU
#    Triplet    ~1 GB  → yet another free GPU
#
#  Usage:
#    bash start_surgr1_yolo.sh                               # auto
#    bash start_surgr1_yolo.sh --surgr1-gpu 3 --yolo-gpu 1 \
#                              --phase-gpu 2 --triplet-gpu 4
#    bash start_surgr1_yolo.sh --test
# ============================================================================

set -e

SURGR1_GPU=""
YOLO_GPU=""
PHASE_GPU=""
TRIPLET_GPU=""
RUN_TEST=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --surgr1-gpu)  SURGR1_GPU="$2";  shift 2 ;;
    --yolo-gpu)    YOLO_GPU="$2";    shift 2 ;;
    --phase-gpu)   PHASE_GPU="$2";   shift 2 ;;
    --triplet-gpu) TRIPLET_GPU="$2"; shift 2 ;;
    --test)        RUN_TEST=true;    shift ;;
    *)             echo "Unknown option: $1"; exit 1 ;;
  esac
done

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ROOT="$(cd "$(dirname "$0")" && pwd)"
export SURG_AGENT_ROOT="${SURG_AGENT_ROOT:-$(cd "${PROJECT_ROOT}/.." && pwd)/surg_agent}"
PROJECT_PYTHON="python3"
if [ -x "${PROJECT_ROOT}/.venv/bin/python" ]; then
    PROJECT_PYTHON="${PROJECT_ROOT}/.venv/bin/python"
elif [ -x "${APP_ROOT}/.venv/bin/python" ]; then
    PROJECT_PYTHON="${APP_ROOT}/.venv/bin/python"
fi

extend_python_cuda_lib_path() {
    local py="${1:-python}"
    local site_dir
    site_dir=$($py -c "import site; print(site.getsitepackages()[0])" 2>/dev/null || true)
    if [ -n "$site_dir" ] && [ -d "${site_dir}/nvidia" ]; then
        local cuda_libs
        cuda_libs=$(find "${site_dir}/nvidia" -path "*/lib" -type d 2>/dev/null | paste -sd: -)
        if [ -n "$cuda_libs" ]; then
            export LD_LIBRARY_PATH="${cuda_libs}:${LD_LIBRARY_PATH:-}"
        fi
    fi
}

# ============================================================================
# Auto-detect GPUs
# ============================================================================
auto_detect_gpus() {
    echo "[GPU] Scanning GPUs..."
    echo ""
    local gpu_info
    gpu_info=$(nvidia-smi --query-gpu=index,memory.total,memory.used,memory.free --format=csv,noheader,nounits 2>/dev/null)
    if [ -z "$gpu_info" ]; then
        echo "  ✗ nvidia-smi failed. Specify --surgr1-gpu --yolo-gpu --phase-gpu --triplet-gpu manually."
        exit 1
    fi

    printf "  %-4s  %-12s  %-12s  %-12s  %s\n" "GPU" "Total(MB)" "Used(MB)" "Free(MB)" "Status"
    echo "  ──────────────────────────────────────────────────────"

    local SURGR1_MIN_FREE=60000
    local EXPERT_MIN_FREE=2000
    local surgr1_candidates=()
    local expert_candidates=()  # "idx:free"

    while IFS=', ' read -r idx total used free; do
        idx=$(echo "$idx" | tr -d ' '); total=$(echo "$total" | tr -d ' ')
        used=$(echo "$used" | tr -d ' '); free=$(echo "$free" | tr -d ' ')
        local status="busy"
        if [ "$free" -ge "$SURGR1_MIN_FREE" ]; then
            status="free (SurgR1 OK)"
            surgr1_candidates+=("$idx:$free")
            expert_candidates+=("$idx:$free")
        elif [ "$free" -ge "$EXPERT_MIN_FREE" ]; then
            status="free (expert OK)"
            expert_candidates+=("$idx:$free")
        fi
        printf "  %-4s  %-12s  %-12s  %-12s  %s\n" "$idx" "$total" "$used" "$free" "$status"
    done <<< "$gpu_info"
    echo ""

    # Pick SurgR1: most free, prefers >=60GB. On 24GB cards such as RTX 4090,
    # allow a best-effort fallback so the rest of the local pipeline can start.
    if [ -z "$SURGR1_GPU" ]; then
        if [ ${#surgr1_candidates[@]} -eq 0 ]; then
            if [ ${#expert_candidates[@]} -eq 0 ]; then
                echo "  ✗ No CUDA GPU with >=${EXPERT_MIN_FREE}MB free!"; exit 1
            fi
            SURGR1_GPU=$(printf '%s\n' "${expert_candidates[@]}" | sort -t: -k2 -nr | head -1 | cut -d: -f1)
            echo "  ⚠ No GPU with >=${SURGR1_MIN_FREE}MB free for SurgR1; best-effort fallback to GPU ${SURGR1_GPU}"
            echo "    If SurgR1 OOMs on 24GB cards, use a smaller/quantized model or tensor parallel serving."
        else
            SURGR1_GPU=$(printf '%s\n' "${surgr1_candidates[@]}" | sort -t: -k2 -nr | head -1 | cut -d: -f1)
            echo "  → Auto-selected GPU ${SURGR1_GPU} for SurgR1 (most free)"
        fi
    fi

    # Sort expert candidates by free mem desc
    local sorted_experts
    sorted_experts=$(printf '%s\n' "${expert_candidates[@]}" | sort -t: -k2 -nr)

    # Pick 3 distinct GPUs (not SurgR1_GPU, not each other) for YOLO / Phase / Triplet
    pick_expert_gpu() {
        local target_var="$1"; shift
        local used_gpus="$*"  # space-separated used GPU IDs
        if [ -n "${!target_var}" ]; then
            echo "  → Using forced GPU ${!target_var} for ${target_var%_GPU}"
            return
        fi
        for entry in $sorted_experts; do
            local gid=$(echo "$entry" | cut -d: -f1)
            local skip=false
            for u in $used_gpus; do
                [ "$gid" = "$u" ] && { skip=true; break; }
            done
            if [ "$skip" = false ]; then
                eval "$target_var=\"$gid\""
                echo "  → Auto-selected GPU ${gid} for ${target_var%_GPU}"
                return
            fi
        done
        for entry in $sorted_experts; do
            local gid=$(echo "$entry" | cut -d: -f1)
            eval "$target_var=\"$gid\""
            echo "  ⚠ Sharing GPU ${gid} for ${target_var%_GPU} (not enough distinct GPUs)"
            return
        done
        echo "  ✗ No available GPU for ${target_var%_GPU}"; exit 1
    }

    pick_expert_gpu YOLO_GPU    "$SURGR1_GPU"
    pick_expert_gpu PHASE_GPU   "$SURGR1_GPU" "$YOLO_GPU"
    pick_expert_gpu TRIPLET_GPU "$SURGR1_GPU" "$YOLO_GPU" "$PHASE_GPU"

    # Uniqueness check. Four distinct GPUs are ideal; three-card local rigs may
    # share the small experts.
    local gpus=("$SURGR1_GPU" "$YOLO_GPU" "$PHASE_GPU" "$TRIPLET_GPU")
    local uniq=$(printf '%s\n' "${gpus[@]}" | sort -u | wc -l)
    if [ "$uniq" -ne 4 ]; then
        echo "  ⚠ Using shared GPU assignment: ${gpus[*]}"
    fi
}

auto_detect_gpus

# ============================================================================
# MySQL check
# ============================================================================
echo ""
echo "[MySQL] Checking database service..."
if ! mysqladmin ping -h localhost --silent 2>/dev/null; then
    echo "  ⚠ MySQL not running, attempting to start..."
    if systemctl start mysql 2>/dev/null; then echo "  ✓ MySQL started"
    elif sudo -n systemctl start mysql 2>/dev/null; then echo "  ✓ MySQL started (sudo)"
    else echo "  ✗ Failed to start MySQL. Run: sudo systemctl start mysql"; exit 1; fi
    for i in 1 2 3 4 5; do mysqladmin ping -h localhost --silent 2>/dev/null && break; sleep 1; done
fi
${PROJECT_PYTHON} -c "
import pymysql, json, sys
with open('${APP_ROOT}/config.json') as f: cfg = json.load(f)
db = cfg['database']['mysql']
try:
    conn = pymysql.connect(host=db['host'], user=db['user'], password=db['password'], database=db['database'])
    print(f'  ✓ Connected as {db[\"user\"]}@{db[\"host\"]} (db={db[\"database\"]})'); conn.close()
except Exception as e:
    print(f'  ✗ MySQL connection failed: {e}'); sys.exit(1)
" || exit 1

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         SurgR1 + Experts Startup (Pipeline v4)              ║"
echo "╠══════════════════════════════════════════════════════════════╣"
printf "║  SurgR1 GPU:   %-3s (Qwen2.5-VL,  ~60 GB)                 ║\n" "$SURGR1_GPU"
printf "║  YOLO GPU:     %-3s (YOLO26s,      ~1 GB)                 ║\n" "$YOLO_GPU"
printf "║  Phase GPU:    %-3s (ResNet-18,   ~0.5 GB)                ║\n" "$PHASE_GPU"
printf "║  Triplet GPU:  %-3s (LAM-Lite,     ~1 GB)                 ║\n" "$TRIPLET_GPU"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ============================================================================
# Configure expert GPUs via env vars
# ============================================================================
echo "[1/5] Writing expert GPU assignments..."
ENV_FILE="${APP_ROOT}/.env.experts"
cat > "${ENV_FILE}" <<EOF
YOLO_DEVICE=cuda:${YOLO_GPU}
PHASE_DEVICE=cuda:${PHASE_GPU}
TRIPLET_DEVICE=cuda:${TRIPLET_GPU}
EOF
export YOLO_DEVICE="cuda:${YOLO_GPU}"
export PHASE_DEVICE="cuda:${PHASE_GPU}"
export TRIPLET_DEVICE="cuda:${TRIPLET_GPU}"
# Back-compat
cp "${ENV_FILE}" "${APP_ROOT}/.env.yolo"
echo "  ✓ ${ENV_FILE} (source before run_backend.sh)"

# Update config.json device fields (best effort)
python3 -c "
import json, sys
p = '${APP_ROOT}/config.json'
try:
    with open(p) as f: cfg = json.load(f)
    cfg['services']['yolo']['device']    = 'cuda:${YOLO_GPU}'
    cfg['services']['phase']['device']   = 'cuda:${PHASE_GPU}'
    cfg['services']['triplet']['device'] = 'cuda:${TRIPLET_GPU}'
    with open(p, 'w') as f: json.dump(cfg, f, indent=4, ensure_ascii=False)
    print('  ✓ config.json updated')
except Exception as e:
    print(f'  ⚠ config.json update failed: {e} (env vars still set)')
" 2>&1 || true

# ============================================================================
# Start SurgR1 API
# ============================================================================
echo ""
echo "[2/5] Starting SurgR1 API on GPU ${SURGR1_GPU}..."
SURGR1_DIR="${PROJECT_ROOT}/SurgR1_api"
[ ! -d "$SURGR1_DIR" ] && { echo "  ✗ SurgR1 dir not found: $SURGR1_DIR"; exit 1; }
SURGR1_PORT=9003
if lsof -i :${SURGR1_PORT} >/dev/null 2>&1; then
    echo "  Killing existing SurgR1 on port ${SURGR1_PORT}..."
    kill -9 $(lsof -t -i:${SURGR1_PORT}) 2>/dev/null || true
    sleep 2
fi
if pgrep -f "VLLM::EngineCore" >/dev/null 2>&1; then
    echo "  Killing orphaned vLLM EngineCore processes..."
    pkill -9 -f "VLLM::EngineCore" 2>/dev/null || true
    sleep 2
fi
cd "$SURGR1_DIR"
mkdir -p "${APP_ROOT}/logs"
SURGR1_LOG="${APP_ROOT}/logs/surgr1_$(date +%Y%m%d_%H%M%S).log"
echo "  Starting SurgR1 (log: ${SURGR1_LOG})..."
(
    if command -v conda >/dev/null 2>&1 && conda env list | awk '{print $1}' | grep -qx "vllm"; then
        eval "$(conda shell.bash hook)"
        conda activate vllm
    elif [ -f "${PROJECT_ROOT}/.venv/bin/activate" ]; then
        source "${PROJECT_ROOT}/.venv/bin/activate"
    elif [ -f "${APP_ROOT}/.venv/bin/activate" ]; then
        source "${APP_ROOT}/.venv/bin/activate"
    else
        echo "  ✗ No conda env 'vllm' or local .venv found"; exit 1
    fi
    extend_python_cuda_lib_path python
    export CUDA_VISIBLE_DEVICES="${SURGR1_GPU}"
    python main.py >> "${SURGR1_LOG}" 2>&1
) &
SURGR1_PID=$!
echo "  ✓ SurgR1 started (PID: ${SURGR1_PID})"

# ============================================================================
# Wait for SurgR1
# ============================================================================
echo ""
echo "[3/5] Waiting for SurgR1 to load (Qwen2.5-VL ~60GB, 1-3 min)..."
MAX_WAIT=300
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    if curl -s http://localhost:${SURGR1_PORT}/health >/dev/null 2>&1; then
        echo "  ✓ SurgR1 is ready! (${ELAPSED}s)"; break
    fi
    if ! kill -0 $SURGR1_PID 2>/dev/null; then
        echo "  ✗ SurgR1 process died. Log: ${SURGR1_LOG}"; tail -20 "${SURGR1_LOG}"; exit 1
    fi
    sleep 5; ELAPSED=$((ELAPSED + 5)); echo "  ... waiting (${ELAPSED}s)"
done
[ $ELAPSED -ge $MAX_WAIT ] && { echo "  ✗ Timeout"; tail -20 "${SURGR1_LOG}"; exit 1; }

# ============================================================================
# Verify experts load on assigned GPUs
# ============================================================================
echo ""
echo "[4/5] Verifying YOLO26s on cuda:${YOLO_GPU}..."
cd "$APP_ROOT"
if command -v conda >/dev/null 2>&1 && conda env list | awk '{print $1}' | grep -qx "vllm"; then
    eval "$(conda shell.bash hook)"
    conda activate vllm 2>/dev/null || true
elif [ -f "${PROJECT_ROOT}/.venv/bin/activate" ]; then
    source "${PROJECT_ROOT}/.venv/bin/activate"
elif [ -f "${APP_ROOT}/.venv/bin/activate" ]; then
    source "${APP_ROOT}/.venv/bin/activate"
fi
extend_python_cuda_lib_path python

python -c "
from backend.services.yolo_service import YOLOService
import numpy as np
import json
with open('config.json') as f:
    cfg = json.load(f)
svc = YOLOService(
    cfg['services']['yolo']['model_path'],
    device='cuda:${YOLO_GPU}')
_ = svc.detect(np.zeros((480, 640, 3), dtype=np.uint8))
print(f'  ✓ YOLO ready on cuda:${YOLO_GPU} — classes: {list(svc.model.names.values())}')
"

echo ""
echo "[5/5] Verifying Phase + Triplet experts..."
python3 -c "
import sys; sys.path.insert(0, '${APP_ROOT}')
import os
os.environ['PHASE_DEVICE']   = 'cuda:${PHASE_GPU}'
os.environ['TRIPLET_DEVICE'] = 'cuda:${TRIPLET_GPU}'
from backend.services.phase_service import get_phase_service
from backend.services.triplet_service import get_triplet_service
import numpy as np

p = get_phase_service()
if p is None:
    print('  ✗ Phase Expert failed to initialize'); sys.exit(1)
r = p.classify(np.zeros((480, 640, 3), dtype=np.uint8))
print(f'  ✓ Phase ready on cuda:${PHASE_GPU} — {len(p.classes)} classes, sample out: {r[\"label\"]} (conf {r[\"confidence\"]})')

t = get_triplet_service()
if t is None:
    print('  ✗ Triplet Expert failed to initialize'); sys.exit(1)
frames = [np.zeros((480, 640, 3), dtype=np.uint8) for _ in range(10)]
r = t.recognize_clip(frames)
print(f'  ✓ Triplet ready on cuda:${TRIPLET_GPU} — sample top-1 I/V/T: '
      f'{r[\"instrument\"][0][\"label\"]}/{r[\"verb\"][0][\"label\"]}/{r[\"target\"][0][\"label\"]}')
"

# ============================================================================
# Summary
# ============================================================================
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  All services ready (Pipeline v4: SurgR1 + 3 Experts)       ║"
echo "╠══════════════════════════════════════════════════════════════╣"
printf "║  SurgR1:   http://localhost:9003   (GPU %-3s)              ║\n" "$SURGR1_GPU"
printf "║  YOLO:     cuda:%-3s (embedded)                            ║\n" "$YOLO_GPU"
printf "║  Phase:    cuda:%-3s (embedded)                            ║\n" "$PHASE_GPU"
printf "║  Triplet:  cuda:%-3s (embedded)                            ║\n" "$TRIPLET_GPU"
echo "║                                                              ║"
echo "║  Next:                                                       ║"
echo "║    source .env.experts && bash run_backend.sh                ║"
echo "║    bash run_frontend.sh                                      ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "Press Ctrl+C to stop SurgR1..."
wait $SURGR1_PID
