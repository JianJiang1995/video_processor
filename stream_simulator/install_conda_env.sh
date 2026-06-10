#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="${1:-stream-simulator}"

if ! command -v conda >/dev/null 2>&1; then
    echo "conda is not installed. Install Miniconda or Anaconda first."
    exit 1
fi

if conda env list | awk '{print $1}' | awk '$1 != "#" && $1 != "" {print $1}' | awk -v env_name="${ENV_NAME}" '$1 == env_name {found=1} END {exit !found}'; then
    echo "Updating existing conda environment: ${ENV_NAME}"
    conda env update --name "${ENV_NAME}" --file "${SCRIPT_DIR}/environment.yml" --prune
else
    echo "Creating conda environment: ${ENV_NAME}"
    conda env create --name "${ENV_NAME}" --file "${SCRIPT_DIR}/environment.yml"
fi

echo
echo "Done. Activate with:"
echo "  conda activate ${ENV_NAME}"
