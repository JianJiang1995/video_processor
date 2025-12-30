#!/bin/bash
# Run Video Stream Analyzer Frontend

cd "$(dirname "$0")/frontend"

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo "Installing dependencies..."
    npm install
fi

echo "=========================================="
echo "  Video Stream Analyzer Frontend"
echo "=========================================="
echo "App: http://localhost:5174"
echo ""

npm run dev

