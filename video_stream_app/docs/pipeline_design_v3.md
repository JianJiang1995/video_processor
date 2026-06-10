# Surg-R1 Surgical Video Analysis Pipeline v3

## Architecture Overview

```
                          ┌──────────────────────────────────────────┐
                          │           Electron App (v36)             │
                          │  ┌──────┬──────────────┬──────────────┐  │
                          │  │ Nav  │  Video +     │  Right Panel │  │
                          │  │ Rail │  Controls +  │  Analysis /  │  │
                          │  │      │  Card Strip  │  Chat tabs   │  │
                          │  └──────┴──────┬───────┴──────┬───────┘  │
                          └────────────────┼──────────────┼──────────┘
                                           │              │
                              MJPEG stream │    REST/SSE  │
                            (w/ YOLO bbox) │              │
                          ┌────────────────┴──────────────┴──────────┐
                          │         FastAPI Backend (:8001)           │
                          │                                          │
                          │  ┌──────────┐  ┌───────────┐  ┌───────┐ │
                          │  │  YOLO26s │  │ Embedding │  │ Frame │ │
                          │  │  Service │  │  Service  │  │Storage│ │
                          │  └────┬─────┘  └─────┬─────┘  └───┬───┘ │
                          └───────┼──────────────┼────────────┼─────┘
                                  │              │            │
              ┌───────────────────┼──────────────┼────────────┼────────┐
              │                   │              │            │        │
     ┌────────▼──────┐  ┌────────▼──────┐  ┌────▼────┐  ┌───▼────┐   │
     │   SurgR1      │  │   Gemini API  │  │  MySQL  │  │  SAM3  │   │
     │   :9003       │  │  (Cloud)      │  │  :3306  │  │  :9004 │   │
     │               │  │               │  │         │  │        │   │
     │ Phase+Action  │  │ • Flash 3.0   │  │ Session │  │ Mask   │   │
     │ (2 questions) │  │ • Embed-2     │  │ Frames  │  │ Segm.  │   │
     │               │  │ • Pro 2.5     │  │ Summary │  │        │   │
     └───────────────┘  └───────────────┘  └─────────┘  └────────┘   │
              │                                                       │
     ┌────────▼──────┐  ┌───────────────┐  ┌───────────────┐         │
     │   GLM/Qwen3   │  │   TTS         │  │   ASR         │         │
     │   :8000       │  │   :50000      │  │   :8765       │         │
     │               │  │               │  │               │         │
     │ Window Summary│  │ CosyVoice    │  │ FunASR        │         │
     │ (Qwen3-VL-8B) │  │ Chinese      │  │ Wakeword      │         │
     └───────────────┘  └───────────────┘  └───────────────┘         │
              └──────────────────────────────────────────────────────┘
```

---

## Real-time Analysis Pipeline

### Data Flow

```
Video Source (RTSP / HTTP / File / Capture Device)
        │
        ├──→ [Frame Capture Service] ──→ 25 FPS JPEG → sessions/{sid}/frames/
        │         (independent thread, never blocked by analysis)
        │
        ├──→ [MJPEG Proxy] ──→ YOLO26s bbox overlay ──→ Frontend <img>
        │         (real-time streams only, ~5ms/frame)
        │
        └──→ [SurgR1 Continuous Task] @ 1 frame / 3s
                    │
                    ├─ SurgR1 API (surgical_phase, surgical_action)
                    │     └─ 2 questions only (tool_localization removed)
                    │
                    ├─ YOLO26s Tool Detection
                    │     └─ 8 classes → SAM3 bboxes + DB storage
                    │
                    └─ SAM3 Mask Segmentation (optional)
                          └─ Consistency-aware propagation
                    │
                    ▼
        [Window Aggregation] (15-second windows)
                    │
                    ├─ Collect frame analyses
                    ├─ Phase voting / transition validation
                    └─ Send to GLM/Gemini for summarization
                    │
                    ▼
        [Gemini/GLM Window Summary]
                    │
                    ├─ Narrative text (中文, ≤300 chars)
                    ├─ Phase classification
                    ├─ [others] metadata: bleeding, gauze, hem_loc, blur
                    └─ SSE push to frontend
                    │
                    ▼
        [Embedding Generation] (async, non-blocking)
                    │
                    ├─ gemini-embedding-2-preview
                    ├─ In-memory store + disk persistence
                    └─ Enables semantic search
```

### SurgR1 Analysis (2 Questions)

After removing `tool_localization` (now handled by YOLO), SurgR1 only processes:

| Question | Purpose | Output |
|----------|---------|--------|
| `surgical_action` | Describe tool-action-tissue triplet | Free text |
| `surgical_phase` | Classify into 7 phases | Phase enum |

**7 Surgical Phases:**
1. Preparation
2. CalotTriangleDissection
3. ClippingCutting
4. GallbladderDissection
5. GallbladderPackaging
6. CleaningCoagulation
7. GallbladderRetraction

### YOLO26s Tool Detection

| Property | Value |
|----------|-------|
| Model | YOLO26s (9.5M params, 20.7B FLOPs) |
| Checkpoint | `yolo26s_cholec_tool/weights/best.pt` |
| Training Data | CholecInstanceSeg + CholecTrack20 |
| Input | 640×640 (auto-resized) |
| Inference | ~5ms/frame on GPU |
| mAP50 | 0.904 |
| mAP50-95 | 0.738 |

**8 Tool Classes:**

| ID | Class | Color (BGR) |
|----|-------|-------------|
| 0 | bipolar | Orange |
| 1 | clipper | Spring Green |
| 2 | grasper | Cyan |
| 3 | hook | Light Blue |
| 4 | irrigator | Magenta |
| 5 | scissors | Yellow |
| 6 | snare | Purple |
| 7 | specimen_bag | Pink |

**Integration Points:**
- **MJPEG Proxy**: Real-time bbox overlay on live streams (`/api/video/mjpeg-proxy/{sid}?show_yolo=true`)
- **Continuous Task**: Replaces SurgR1 tool_localization, feeds SAM3
- **DB Storage**: YOLO detections stored as JSON in `tools` column

---

## Semantic Search Architecture

### Embedding Pipeline

```
Window Summary (text)
        │
        ▼
[gemini-embedding-2-preview]
        │
        ▼
768-dim vector
        │
        ├──→ In-memory: {session_id: {window_id: {embedding, text, metadata}}}
        └──→ Disk: sessions/{sid}/embeddings.json
```

### Search Modes

| Mode | Endpoint | Input | Algorithm |
|------|----------|-------|-----------|
| Semantic Search | `POST /search/semantic` | Natural language query | Embed query → cosine similarity |
| Similar Windows | `GET /search/similar-window/{sid}/{wid}` | Window ID | Compare source embedding vs all |
| Text Search | `POST /search/text` | Keyword string | Case-insensitive substring match |

### API Examples

```bash
# Semantic search
curl -X POST http://localhost:8001/api/analysis/search/semantic \
  -H "Content-Type: application/json" \
  -d '{"session_id": "abc123", "query": "clipping of cystic artery", "top_k": 5}'

# Find similar windows
curl http://localhost:8001/api/analysis/search/similar-window/abc123/6?top_k=3

# Text search
curl -X POST http://localhost:8001/api/analysis/search/text \
  -H "Content-Type: application/json" \
  -d '{"session_id": "abc123", "query": "bleeding"}'
```

---

## Bleeding Detection Pipeline (Offline)

### Purpose

Generate YOLO-format training data for bleeding detection by leveraging Gemini Pro's visual understanding.

### Flow

```
Session Frames (25 FPS JPEG)
        │
        ▼
[Sample every N frames] (default: every 5th)
        │
        ▼
[Gemini 2.5 Pro Vision API]
        │  Prompt: detect active bleeding areas
        │  Output: normalized bboxes
        │  Conservative: only confident detections
        ▼
[Post-processing]
        │
        ├──→ images/train/{frame}.jpg      (source frames)
        ├──→ labels/train/{frame}.txt      (YOLO format: class x_c y_c w h)
        ├──→ data.yaml                     (dataset config)
        ├──→ annotation_stats.json         (summary stats)
        └──→ visualizations/{frame}_vis.jpg (QA review, optional)
```

### Usage

```bash
cd /data2/jj/proj/video_processor/video_stream_app

# Basic usage
python backend/scripts/bleeding_annotation.py \
  --session-dir sessions/20260119_195502_2443f16a_stream \
  --output-dir /data4/jj/proj/surg_agent/detection_expert/datasets/bleeding

# With visualization and limited frames
python backend/scripts/bleeding_annotation.py \
  --session-dir sessions/20260119_195502_2443f16a_stream \
  --output-dir /data4/bleeding_dataset \
  --sample-interval 10 \
  --max-frames 200 \
  --visualize

# Then train YOLO on the generated dataset
cd /data4/jj/proj/surg_agent/detection_expert
python train_yolo26s.py --task bleeding --device 0
```

### Output Format (YOLO)

```
bleeding_dataset/
├── images/
│   └── train/
│       ├── frame_000100_ts4_00.jpg
│       └── ...
├── labels/
│   └── train/
│       ├── frame_000100_ts4_00.txt   # "0 0.45 0.32 0.08 0.06"
│       └── ...
├── data.yaml                          # nc: 1, names: [bleeding]
├── annotation_stats.json
└── visualizations/  (optional)
```

---

## Frontend Layout (E3 Design)

### Color Theme: Warm Slate + Amber

```css
--bg-primary:      #161616    /* Base background */
--bg-secondary:    #1e1e1e    /* Cards, panels */
--bg-tertiary:     #2a2a2a    /* Elevated surfaces */
--accent-primary:  #f0a030    /* Amber accent */
--text-primary:    #e8e6e3    /* Main text */
--text-secondary:  #a8a5a0    /* Secondary text */
```

### Layout Structure

```
┌──────┬───────────────────────────────────────┬──────────────┐
│      │              HEADER                    │              │
│      │  Logo │ Mode │ Session ID │ Video Name │              │
│      ├───────────────────────────────────────┤              │
│ NAV  │                                       │  RIGHT PANEL │
│ RAIL │         VIDEO PLAYER                  │              │
│ 56px │         (MJPEG + YOLO overlay)        │  [Analysis]  │
│      │                                       │  [Chat]      │
│ 📹  │─────────────────────────────────────── │              │
│ 💬  │         CONTROL BAR                    │  Full window │
│ ⊞   │  ◄◄ ◄ ▶ ► ►►  00:35/01:42            │  text, phase │
│ ─── │  [SurgR1●][GLM●][SAM3●][TTS●][ASR○]  │  metadata    │
│ ▶   │─────────────────────────────────────── │              │
│      │                                       │  OR          │
│ ⚙   │  BOTTOM CARD STRIP                    │              │
│      │  [#7|Recon] [#6|Diss] [#5|Exp] →     │  Chat with   │
│      │                                       │  voice input │
│      │                         [Grid Overview]│  + similarity│
└──────┴───────────────────────────────────────┴──────────────┘
```

### Component Hierarchy

```
App.vue
├── ModeSelector          (view: 'select')
├── StreamInput           (view: 'stream-input')
├── WindowOverview        (view: 'overview')
│   ├── Grid cards with exact + semantic search
│   ├── Find Similar (Text / Video / Both)
│   └── Right panel with Detail + Chat tabs
│
└── Main View             (view: 'main')
    ├── NavRail           (left 56px)
    │   ├── Analysis / Chat / Overview buttons
    │   ├── Analyze start/stop button
    │   └── Settings
    ├── Header            (session ID, mode badge)
    ├── VideoPlayer       (MJPEG with YOLO overlay)
    ├── ControlBar        (timeline, service badges)
    ├── BottomCardStrip   (horizontal scroll of window cards)
    ├── RightPanel        (380px)
    │   ├── Tab: Analysis (full window text, phase, metadata)
    │   └── Tab: ChatPanel
    │       ├── Text input
    │       ├── Voice input (microphone)
    │       ├── Message history
    │       └── Similarity search results
    └── FrameAnalysisPopup (on timeline drag)
```

---

## Service Configuration

### config.json Structure

```jsonc
{
  "window_analysis": {
    "provider": "gemini",           // gemini | glm | qwen
    "history_window_count": 3,      // context window count
    "max_output_chars": 300         // max summary length
  },
  "services": {
    "surgr1":    { "port": 9003, "max_concurrent": 3  },
    "glm":       { "port": 8000, "model": "Qwen3-VL-8B", "max_concurrent": 16 },
    "sam3":      { "port": 9004 },
    "tts":       { "port": 50000, "speaker": "中文女" },
    "asr":       { "port": 8765, "keywords": ["你好小助", "小助小助"] },
    "gemini":    { "model": "gemini-3-flash-preview", "max_concurrent": 8 },
    "yolo":      { "model_path": "...yolo26s.../best.pt", "device": "cuda:0", "conf": 0.25 },
    "embedding": { "model": "gemini-embedding-2-preview" }
  },
  "video_processing": {
    "window_duration": 15.0,        // seconds per analysis window
    "sample_interval": 3.0,         // seconds between SurgR1 samples
    "frame_storage": { "fps": 25 }  // capture rate (independent of analysis)
  },
  "analysis": {
    "questions": {
      "surgical_action": "...",     // tool-action-tissue description
      "surgical_phase": "..."       // 7-phase classification
      // tool_localization: REMOVED (handled by YOLO26s)
    }
  }
}
```

### Port Map

| Port | Service | Required |
|------|---------|----------|
| 5176 | Frontend (Vite dev) | Yes |
| 8001 | Backend (FastAPI) | Yes |
| 9003 | SurgR1 (Qwen2.5-VL) | Yes |
| 8000 | GLM (Qwen3-VL-8B) | Yes |
| 9004 | SAM3 Segmentation | No |
| 50000 | TTS (CosyVoice) | No |
| 8765 | ASR (FunASR) | No |
| 3306 | MySQL | Yes |

---

## API Reference

### Search APIs (New)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/analysis/search/semantic` | Semantic search via Gemini embeddings |
| GET | `/api/analysis/search/similar-window/{sid}/{wid}` | Find similar windows |
| POST | `/api/analysis/search/text` | Exact text substring search |
| GET | `/api/analysis/search/embedding-stats/{sid}` | Embedding statistics |

### Core Analysis APIs

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/analysis/start-surgr1-continuous/{sid}` | Start real-time analysis |
| POST | `/api/analysis/stop-surgr1-continuous/{sid}` | Stop analysis |
| GET | `/api/analysis/surgr1-continuous-status/{sid}` | Analysis status |
| POST | `/api/analysis/start-glm-summarization` | Start window summarization |
| GET | `/api/analysis/stream-summaries/{sid}` | SSE stream of summaries |
| GET | `/api/analysis/all-window-summaries/{sid}` | All window summaries |

### Video APIs

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/video/mjpeg-proxy/{sid}?show_yolo=true` | MJPEG stream with YOLO overlay |
| POST | `/api/video/upload` | Upload video file |
| POST | `/api/video/connect-stream` | Connect to live stream |

---

## Storage Layout

```
video_stream_app/
├── sessions/
│   └── {timestamp}_{session_id}_{video_name}/
│       ├── frames/                    # 25 FPS JPEG captures
│       │   ├── frame_000000_ts0_00.jpg
│       │   └── ...
│       ├── preview/                   # 10 FPS low-quality previews
│       ├── frames_index.json          # O(1) frame lookup index
│       ├── embeddings.json            # Gemini embedding vectors
│       └── metadata.json
├── logs/
│   └── api_YYYYMMDD_HHMMSS.log
└── config.json
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| v1 | 2025-12 | Initial: SurgR1 + GPT summarization |
| v2 | 2026-01 | Added: GLM/Gemini, SAM3, TTS/ASR, Electron |
| **v3** | **2026-04** | **YOLO26s tool detection, Gemini embeddings, semantic search, bleeding annotation pipeline, E3 UI redesign (Warm Slate + Amber), Electron 36** |
