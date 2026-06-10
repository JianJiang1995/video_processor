#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${SCRIPT_DIR}/dist"
BUILD_NAME="stream_simulator_portable"
BUILD_DIR="${DIST_DIR}/${BUILD_NAME}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
ARCHIVE_PATH="${DIST_DIR}/${BUILD_NAME}_${TIMESTAMP}.zip"
VIDEO_SOURCE="${1:-}"

mkdir -p "${DIST_DIR}"
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}/media" "${BUILD_DIR}/uploads"

copy_required_files() {
    local files=(
        "README.md"
        "__init__.py"
        "config.json"
        "environment.yml"
        "http_server.py"
        "install_conda_env.sh"
        "package_portable.sh"
        "path_utils.py"
        "requirements.txt"
        "rtsp_server.py"
        "run.py"
        "start_all.sh"
        "video_source.py"
        "webrtc_server.py"
    )

    local file
    for file in "${files[@]}"; do
        cp "${SCRIPT_DIR}/${file}" "${BUILD_DIR}/${file}"
    done
}

copy_optional_media() {
    if [ -d "${SCRIPT_DIR}/media" ]; then
        cp -R "${SCRIPT_DIR}/media/." "${BUILD_DIR}/media/" 2>/dev/null || true
    fi

    if [ -n "${VIDEO_SOURCE}" ]; then
        if [ ! -f "${VIDEO_SOURCE}" ]; then
            echo "Video file does not exist: ${VIDEO_SOURCE}"
            exit 1
        fi
        cp "${VIDEO_SOURCE}" "${BUILD_DIR}/media/sample$(basename "${VIDEO_SOURCE}" | sed 's/.*\(\.[^.]*\)$/\1/')"
        return 0
    fi

    return 0
}

copy_required_files
copy_optional_media

chmod +x "${BUILD_DIR}/start_all.sh" "${BUILD_DIR}/install_conda_env.sh" "${BUILD_DIR}/package_portable.sh"

(
    cd "${DIST_DIR}"
    zip -qr "$(basename "${ARCHIVE_PATH}")" "${BUILD_NAME}"
)

printf '%s\n' "${ARCHIVE_PATH}"
