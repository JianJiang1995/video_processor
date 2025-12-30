#!/bin/bash
# Run Video Stream Analyzer Backend

cd "$(dirname "$0")"

# Activate virtual environment if exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Set environment variables (customize as needed)
export PYTHONPATH="${PYTHONPATH}:$(pwd)"

# Run with uvicorn
echo "=========================================="
echo "  Video Stream Analyzer Backend"
echo "=========================================="
echo "API: http://localhost:8001"
echo "Docs: http://localhost:8001/api/docs"
echo ""

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload

