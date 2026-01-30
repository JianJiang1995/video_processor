# Video Stream Analyzer

A real-time surgical video analysis application with local VLM model (vLLM), GPT summarization, SAM2 segmentation, and TTS output.

## Features

- 🎬 **Video Playback**: Modern player with pause, resume, seek
- 📡 **Live Stream**: Support for RTSP/HTTP video streams
- 📊 **N=5s Window Analysis**: Processes video in 5-second windows
- 🤖 **Local VLM Model**: Swift-deployed Qwen2.5-VL via vLLM
- 📝 **GPT Summarization**: AI-generated narrative summaries
- 🎯 **SAM2 Integration**: Surgical instrument segmentation
- 🔊 **TTS Audio**: Text-to-speech for summaries
- 💾 **SQLite Database**: Persistent storage of all results

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Frontend      │────▶│    Backend      │────▶│  Model Service  │
│   (Vue.js)      │     │   (FastAPI)     │     │  (Swift/vLLM)   │
│   Port: 5174    │     │   Port: 8001    │     │   Port: 9000    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                              │
                              ▼
                        ┌─────────────────┐
                        │  OpenAI API     │
                        │  (GPT & TTS)    │
                        └─────────────────┘
```

```
video_stream_app/
├── config.json              # 🔧 Main configuration file
├── deploy_model.sh          # 🚀 Start model service
├── run_backend.sh           # 🚀 Start backend
├── run_frontend.sh          # 🚀 Start frontend
├── backend/
│   ├── main.py
│   ├── config.py
│   ├── database/
│   ├── services/
│   │   ├── model_service.py     # VLM API client
│   │   ├── video_processor.py
│   │   ├── gpt_summarizer.py
│   │   ├── sam2_service.py
│   │   └── tts_service.py
│   └── routers/
│       ├── video.py
│       ├── analysis.py
│       └── model.py             # Model API endpoints
└── frontend/
    └── src/
        ├── App.vue
        └── components/
            ├── ModeSelector.vue     # Local/Stream mode
            ├── VideoPlayer.vue
            ├── ControlBar.vue
            └── SummaryPanel.vue
```

## Quick Start

### 1. Install Dependencies

```bash
cd video_stream_app
pip install -r requirements.txt

# Frontend
cd frontend && npm install && cd ..
```

### 2. Configure

Edit `config.json` to customize:
- Model path and parameters
- Window duration (N=5s default)
- Service ports

### 3. Start Model Service (Terminal 1)

```bash
# Requires: ms-swift, vllm installed
./deploy_model.sh
```

This starts the VLM model at **http://localhost:9000**

### 4. Start Backend (Terminal 2)

```bash
./run_backend.sh
```

Backend API at **http://localhost:8001**
API Docs: http://localhost:8001/api/docs

### 5. Start Frontend (Terminal 3)

```bash
./run_frontend.sh
```

Frontend at **http://localhost:5174**

## API Endpoints

### Video Management

- `POST /api/video/upload` - Upload video file
- `POST /api/video/load` - Load video from path
- `GET /api/video/sessions` - List all sessions
- `GET /api/video/stream/{session_id}` - Stream video
- `POST /api/video/control/{session_id}` - Control playback

### Analysis

- `POST /api/analysis/analyze-window` - Analyze single window
- `POST /api/analysis/process-video` - Process entire video
- `GET /api/analysis/summaries/{session_id}` - Get all summaries
- `GET /api/analysis/summary-at/{session_id}` - Get summary for timestamp
- `GET /api/analysis/stream-summaries/{session_id}` - SSE summary stream

### SAM2 & TTS

- `POST /api/analysis/sam2/segment` - Segment frame
- `GET /api/analysis/sam2/status` - Check SAM2 availability
- `POST /api/analysis/tts/synthesize` - Convert text to speech
- `POST /api/analysis/tts/summary/{session_id}/{window_id}` - TTS for summary

### VLM Model Service

- `GET /api/model/status` - Check model status
- `POST /api/model/load` - Load model manually
- `POST /api/model/unload` - Unload model (free GPU)
- `POST /api/model/infer` - Single image inference
- `POST /api/model/infer-batch` - Batch inference
- `POST /api/model/analyze-frame` - Analyze surgical frame

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key | Required (for GPT/TTS) |
| `GPT_MODEL` | GPT model for summarization | `gpt-4o` |
| `TTS_VOICE` | TTS voice | `alloy` |
| `WINDOW_DURATION` | Analysis window duration | `5.0` seconds |
| `SAMPLE_INTERVAL` | Frame sampling interval | `1.0` second |
| `VLM_MODEL_PATH` | Local VLM model path | See below |
| `VLM_PRELOAD` | Preload model on startup | `false` |
| `SAM2_MODEL_PATH` | Path to SAM2 checkpoint | Optional |

### Local VLM Model

The application uses a local Qwen2.5-VL model for surgical frame analysis:

```
Model Path: /data/jj/proj/Laparo/last_cot_qwen2.5/round36_cholec/v0-20251116-000731/checkpoint-12042-merged
```

The model service uses **vLLM** for efficient inference with:
- Batch inference support (up to 16 concurrent requests)
- 90% GPU memory utilization
- Automatic model loading on first request

## Usage

1. **Upload/Load Video**: Drag & drop or enter video path
2. **Click "Analyze Video"**: Starts processing with GPT
3. **View Summaries**: Right panel shows current segment summary
4. **Playback Control**: Use controls to navigate, pause/resume
5. **Listen to Summary**: Click "Listen" button for TTS playback
6. **Detect Tools**: Click "Detect Tools" for SAM2 segmentation

## Database Schema

### VideoSession
- session_id, video_path, duration, fps, status

### FrameAnalysis
- frame_idx, timestamp, tool_localization, surgical_action, surgical_phase

### WindowSummary
- window_id, start_time, end_time, summary_text, tts_audio_path

## License

MIT License

