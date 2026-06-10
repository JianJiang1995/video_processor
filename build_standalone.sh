#!/bin/bash
# ============================================
# Video Analyzer - Full Standalone Build
# 编译所有服务 + 打包 Electron 应用
#
# 服务列表:
#   - backend (FastAPI)     → PyInstaller
#   - surgr1 (vLLM)         → PyInstaller
#   - sam3 (Segmentation)   → PyInstaller
#   - tts (CosyVoice)       → PyInstaller
#   - asr (FunASR)          → PyInstaller
#   - gemini / GLM           → Cloud API / 手动启动 (不打包)
# ============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR"
FRONTEND_DIR="$APP_DIR/frontend"
PROJECT_ROOT="$(dirname "$APP_DIR")"
DIST_SERVICES="$APP_DIR/dist-services"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       Video Analyzer - Full Standalone Build                ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# ============================================
# Parse arguments
# ============================================
TARGET="linux"
SKIP_BACKEND=false
SKIP_SERVICES=false
SERVICES_TO_BUILD="all"  # all, or comma-separated: backend,surgr1,glm

for arg in "$@"; do
  case $arg in
    --win) TARGET="win" ;;
    --linux) TARGET="linux" ;;
    --all) TARGET="all" ;;
    --skip-backend) SKIP_BACKEND=true ;;
    --skip-services) SKIP_SERVICES=true ;;
    --services=*) SERVICES_TO_BUILD="${arg#*=}" ;;
    --help)
      echo "Usage: $0 [OPTIONS]"
      echo ""
      echo "Platform:"
      echo "  --linux              Build for Linux (default)"
      echo "  --win                Build for Windows"
      echo "  --all                Build for all platforms"
      echo ""
      echo "Build control:"
      echo "  --skip-backend       Skip PyInstaller backend build"
      echo "  --skip-services      Skip all service builds (use existing)"
      echo "  --services=LIST      Only build specific services (comma-separated)"
      echo "                       e.g. --services=backend,surgr1"
      echo ""
      echo "Available services: backend, surgr1, sam3, tts, asr"
      exit 0
      ;;
  esac
done

# ============================================
# Check prerequisites
# ============================================
echo -e "${CYAN}Checking prerequisites...${NC}"

if ! command -v pyinstaller &> /dev/null; then
  echo -e "${RED}Error: PyInstaller not found. Install: pip install pyinstaller${NC}"
  exit 1
fi

if ! command -v node &> /dev/null; then
  echo -e "${RED}Error: Node.js not found${NC}"
  exit 1
fi

echo -e "${GREEN}  PyInstaller: $(pyinstaller --version)${NC}"
echo -e "${GREEN}  Node.js: $(node --version)${NC}"
echo -e "${GREEN}  Python: $(python --version)${NC}"
echo ""

# ============================================
# Helper: Build a service with PyInstaller
# ============================================
build_service() {
  local SERVICE_NAME="$1"
  local SPEC_FILE="$2"
  local WORK_DIR="$3"
  local OUTPUT_NAME="$4"

  echo -e "${YELLOW}  Building ${SERVICE_NAME}...${NC}"

  if [ ! -f "$SPEC_FILE" ]; then
    echo -e "${RED}    Spec file not found: ${SPEC_FILE}${NC}"
    echo -e "${YELLOW}    Skipping ${SERVICE_NAME}${NC}"
    return 1
  fi

  cd "$WORK_DIR"

  pyinstaller "$SPEC_FILE" \
    --distpath "$DIST_SERVICES" \
    --workpath "$APP_DIR/build-services/${SERVICE_NAME}" \
    --clean -y 2>&1 | tail -5

  if [ -d "$DIST_SERVICES/$OUTPUT_NAME" ]; then
    local SIZE=$(du -sh "$DIST_SERVICES/$OUTPUT_NAME" | cut -f1)
    echo -e "${GREEN}    ${SERVICE_NAME} built (${SIZE})${NC}"
  else
    echo -e "${RED}    ${SERVICE_NAME} build failed${NC}"
    return 1
  fi
}

should_build() {
  local service="$1"
  if [ "$SERVICES_TO_BUILD" = "all" ]; then
    return 0
  fi
  echo "$SERVICES_TO_BUILD" | grep -q "$service"
}

# ============================================
# Step 1: Create PyInstaller spec files
# ============================================

# Backend spec already exists at build_backend.spec
# Create specs for other services if they don't exist

create_surgr1_spec() {
  cat > "$APP_DIR/build_surgr1.spec" << 'SPECEOF'
# -*- mode: python ; coding: utf-8 -*-
import os
block_cipher = None
APP_DIR = os.path.abspath('.')
SURGR1_DIR = os.path.join(os.path.dirname(APP_DIR), 'SurgR1_api')

a = Analysis(
    [os.path.join(SURGR1_DIR, 'main.py')],
    pathex=[SURGR1_DIR],
    datas=[
        (os.path.join(SURGR1_DIR, 'config.json'), '.'),
    ],
    hiddenimports=[
        'uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'fastapi', 'starlette', 'pydantic',
        'vllm', 'PIL', 'numpy',
    ],
    excludes=['tkinter', 'matplotlib', 'scipy'],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name='surgr1-server', console=True)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas,
               name='surgr1')
SPECEOF
}

if [ "$SKIP_SERVICES" = false ]; then
  echo -e "${YELLOW}[1/3] Building services with PyInstaller...${NC}"
  echo ""

  mkdir -p "$DIST_SERVICES"
  mkdir -p "$APP_DIR/build-services"

  # Backend
  if should_build "backend"; then
    build_service "backend" "$APP_DIR/build_backend.spec" "$APP_DIR" "video-analyzer-backend"
    # Rename to match serviceManager expectations
    if [ -d "$DIST_SERVICES/video-analyzer-backend" ]; then
      rm -rf "$DIST_SERVICES/backend"
      mv "$DIST_SERVICES/video-analyzer-backend" "$DIST_SERVICES/backend"
      # Copy config
      cp "$APP_DIR/config.json" "$DIST_SERVICES/backend/"
      [ -f "$PROJECT_ROOT/.env" ] && cp "$PROJECT_ROOT/.env" "$DIST_SERVICES/backend/"
    fi
  fi

  # SurgR1
  if should_build "surgr1"; then
    create_surgr1_spec
    build_service "surgr1" "$APP_DIR/build_surgr1.spec" "$APP_DIR" "surgr1" || true
  fi


  # SAM3
  if should_build "sam3"; then
    create_sam3_spec
    build_service "sam3" "$APP_DIR/build_sam3.spec" "$APP_DIR" "sam3" || true
  fi

  # TTS and ASR are complex (CosyVoice, FunASR) - skip PyInstaller for now
  # They can be added later or run as conda environments
  if should_build "tts"; then
    echo -e "${YELLOW}  TTS (CosyVoice): Complex dependency, skipping PyInstaller${NC}"
    echo -e "${YELLOW}    → Will use conda environment at runtime${NC}"
  fi

  if should_build "asr"; then
    echo -e "${YELLOW}  ASR (FunASR): Complex dependency, skipping PyInstaller${NC}"
    echo -e "${YELLOW}    → Will use conda environment at runtime${NC}"
  fi

  echo ""
  echo -e "${GREEN}  Services build complete${NC}"
  echo ""

  # Show summary
  echo -e "${CYAN}  Built services:${NC}"
  for dir in "$DIST_SERVICES"/*/; do
    if [ -d "$dir" ]; then
      local_name=$(basename "$dir")
      local_size=$(du -sh "$dir" | cut -f1)
      echo -e "    ${local_name}: ${local_size}"
    fi
  done
  echo ""
else
  echo -e "${YELLOW}[1/3] Skipping service builds (--skip-services)${NC}"
  echo ""
fi

# ============================================
# Step 3: Build Frontend (Vite)
# ============================================
echo -e "${YELLOW}[2/3] Building frontend with Vite...${NC}"

cd "$FRONTEND_DIR"

if [ ! -d "node_modules" ]; then
  echo "  Installing npm dependencies..."
  npm install
fi

npm run build
echo -e "${GREEN}  Frontend built${NC}"
echo ""

# ============================================
# Step 4: Package with electron-builder
# ============================================
echo -e "${YELLOW}[3/3] Packaging Electron app (target: ${TARGET})...${NC}"

case $TARGET in
  linux)
    npx electron-builder --linux
    ;;
  win)
    npx electron-builder --win
    ;;
  all)
    npx electron-builder --linux --win
    ;;
esac

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Build Complete!                          ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Output: ${BLUE}${FRONTEND_DIR}/dist-electron/${NC}"
echo ""

if [ -d "$FRONTEND_DIR/dist-electron" ]; then
  echo "Generated files:"
  find "$FRONTEND_DIR/dist-electron" -maxdepth 1 \( -name "*.AppImage" -o -name "*.deb" -o -name "*.exe" -o -name "*.tar.gz" \) -exec ls -lh {} \; 2>/dev/null
fi


echo ""
echo -e "${GREEN}This app includes embedded services:${NC}"
echo -e "  ✅ FastAPI Backend (port 8001)"
echo -e "  ✅ SurgR1 vLLM (port 9003) — requires GPU"
echo -e "  ☁️  Gemini / GLM — cloud API or 手动启动 (vlm_factory 切换)"
echo -e "  ⚡ SAM3, TTS, ASR — if built"
echo ""
echo -e "Users double-click to run. All services start automatically."
