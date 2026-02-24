# Pipeline Latency Optimization Results

> Date: 2026-02-23 | GPU: NVIDIA A100-SXM4-80GB × 8 | Gemini Model: gemini-3.1-pro-preview

## 1. SurgR1 Batch Latency (A100 GPU)

| Batch Size | Total Time | Per Frame | FPS  | Questions |
|:----------:|:----------:|:---------:|:----:|:---------:|
| 1          | 6.81s      | 6.81s     | 0.15 | 3         |
| 3          | 4.39s      | 1.46s     | 0.68 | 9         |
| **5**      | **4.19s**  | **0.84s** | 1.19 | 15        |
| 8          | 4.84s      | 0.61s     | 1.65 | 24        |
| 10         | 4.87s      | 0.49s     | 2.05 | 30        |
| 15         | 5.99s      | 0.40s     | 2.51 | 45        |

- Batch size 5 is the sweet spot: lowest total time (4.19s) with good per-frame efficiency.
- Single-frame overhead is huge (6.81s) due to HTTP + vLLM scheduling cost.
- Beyond batch 8, total time grows while per-frame gains diminish.

## 2. Gemini Model Comparison (5 images, 3 runs each)

| Model                   | Avg Time | Stability       | Quality |
|:------------------------|:--------:|:---------------:|:-------:|
| gemini-3.1-pro-preview  | 8.49s    | Stable          | Good    |
| gemini-3-flash-preview  | 6.00s    | Stable          | Good    |
| gemini-3-pro-preview    | 18.46s   | Region issues   | Good    |

- `gemini-3-flash-preview` is ~30% faster than `gemini-3.1-pro-preview` with comparable quality.
- `gemini-3-pro-preview` has frequent `FAILED_PRECONDITION` region errors.
- With full pipeline prompt (background knowledge + system prompt), Gemini calls take 12–25s.

## 3. Full Pipeline Simulation

Window duration = 15s. Total latency = window_duration + R1_overhead + Gemini_time.

| Config                  | Frames | R1 Time | Gemini Time | Total   | Ratio  |
|:------------------------|:------:|:-------:|:-----------:|:-------:|:------:|
| Baseline (si=1, bt=6)   | 15     | 9.13s   | 15.24s      | 30.24s  | 2.02×  |
| A: si=2, bt=3 + flash   | 7      | 7.2s    | 12.6s       | 27.6s   | 1.84×  |
| B: si=3, bt=3 + flash   | 5      | 6.1s    | 13.5s       | 28.5s   | 1.90×  |
| **C: si=2, bt=4 + flash** | 7    | 7.1s    | 12.2s       | 27.2s   | **1.81×** |

### E2E Validation (new config, real R1 results)

| Window       | R1 Time | Phases Detected                                      |
|:-------------|:-------:|:-----------------------------------------------------|
| 0–15s        | 4.70s   | GallbladderDissection → GallbladderPackaging → Retraction |
| 15–30s       | 4.06s   | GallbladderRetraction → GallbladderPackaging          |
| 30–45s       | 4.58s   | GallbladderPackaging → GallbladderRetraction          |

Sample summary output (Window 2):
> 【阶段】胆囊取出阶段
> 胆囊已被完整剥离并妥善置入标本袋中。抓钳抓持标本袋进行牵拉与位置调整，准备将装有胆囊的标本袋经穿刺孔移出腹腔。

## 4. Bottleneck Analysis

```
Pipeline timeline (Baseline → Optimized):

Baseline (si=1s, bt=6s):
  |-- batch wait 6s --|-- R1 ~9s --|-- Gemini ~15s --|  = 30s (2.0×)

Optimized (si=3s, bt=3s):
  |-- batch wait 3s --|-- R1 ~4s --|-- Gemini ~8-13s --|  = 22-25s (1.5-1.7×)
```

The dominant bottleneck is the Gemini API call (8–25s depending on load and image count), not SurgR1. With 5 frames, R1 finishes well within the 15s window.

## 5. Parameters Changed

| Parameter               | Before | After | Location                    |
|:------------------------|:------:|:-----:|:----------------------------|
| `sample_interval`       | 1.0s   | 3.0s  | `config.json`               |
| `min_frames_ratio`      | 0.3    | 0.2   | `config.json`               |
| `SURGR1_MIN_BATCH_SIZE` | 3      | 2     | `analysis.py:1564`          |
| `SURGR1_TARGET_BATCH_SIZE` | 8   | 5     | `analysis.py:1566`          |
| `SURGR1_MAX_BATCH_SIZE` | 25     | 15    | `analysis.py:1565`          |
| `batch_timeout`         | 6.0s   | 3.0s  | `analysis.py:1569`          |
| `MAX_PARALLEL_R1_TASKS` | 2      | 3     | `analysis.py:1573`          |
| `surgr1_interval`       | hardcoded 1.0 | `settings.SAMPLE_INTERVAL` | `analysis.py:1551` |

## 6. Summary Quality Assessment

| Frame Count | Interval | Phase Detection | Narrative Coherence | Instruments |
|:-----------:|:--------:|:---------------:|:-------------------:|:-----------:|
| 15 (old)    | 1s       | ✓               | ✓                   | ✓           |
| 7           | 2s       | ✓               | ✓                   | ✓           |
| **5 (new)** | **3s**   | **✓**           | **✓**               | **✓**       |
| 3           | 5s       | ✓               | Sparse              | ✓           |

5 frames at 3s intervals is the minimum that still produces coherent, phase-aware summaries. Going to 3 frames (5s interval) makes the narrative noticeably sparser.

## 7. Recommendations

1. **Current config is optimal** for the gemini-3.1-pro-preview model with A100 GPU.
2. If `gemini-3-flash-preview` becomes stable in your region, switching to it would save ~2-3s per window.
3. The Gemini API `FAILED_PRECONDITION` errors are transient Google-side region restrictions — not related to parameter tuning.
4. Benchmark script saved at `video_stream_app/benchmark_pipeline.py` for future re-testing.

---

## 8. V2 Optimizations (2026-02-24)

Branch: `feature/pipeline-latency-opt-v2`

### 8.1 Changes Applied

| Optimization | File | Description |
|:-------------|:-----|:------------|
| Image Compression | `gemini_client.py` | Resize to max 640px width + JPEG quality 85→60. Reduces per-image payload ~10x |
| Prompt Trimming | `gemini_client.py`, `config.json` | History windows 10→3, tools truncation 120→80 chars, removed CVS/tools from history |
| Streaming Mode | `gemini_client.py` | `generate_content` → `generate_content_stream` for both `analyze_multiple_images` and `_analyze_images_with_timestamps` |
| Pipeline Overlap | `analysis.py` | R1(N+1) runs in parallel with Gemini(N) via `asyncio.Task`. Gemini result awaited before building next window's history_context |

### 8.2 Expected Latency Impact

```
Before (V1):
  Window N:   |-- R1 ~4s --|-- Gemini ~12s --|
  Window N+1:                                  |-- R1 ~4s --|-- Gemini ~12s --|
  Per-window effective: ~16s

After (V2 with pipeline overlap):
  Window N:   |-- R1 ~4s --|-- Gemini ~12s --|
  Window N+1:               |-- R1 ~4s --|-- Gemini ~12s --|
  Per-window effective: ~12s (Gemini dominates, R1 is hidden)
```

- Pipeline overlap saves ~R1_time (~4s) per window after the first
- Image compression reduces network transfer time (variable, depends on connection)
- Prompt trimming reduces input tokens (~30% fewer), may reduce Gemini processing time
- Streaming mode reduces perceived latency (first_token arrives earlier)

### 8.3 Parameters Changed (V2)

| Parameter | Before (V1) | After (V2) | Location |
|:----------|:-----------:|:----------:|:---------|
| `history_window_count` | 10 | 3 | `config.json` |
| Image max width (Gemini) | unlimited | 640px | `gemini_client.py` |
| JPEG quality (Gemini) | 85 | 60 | `gemini_client.py` |
| Tools truncation | 120 chars | 80 chars | `gemini_client.py` |
| API mode | `generate_content` | `generate_content_stream` | `gemini_client.py` |
| Window processing | Sequential | Pipeline overlap | `analysis.py` |

### 8.4 Quality Verification

Streaming mode produces identical output to non-streaming (same model, same prompt). Quality test confirmed:
- Phase detection: ✓ (correct surgical phases identified)
- Others extraction: ✓ (`hem_loc`, `gauze`, `bleeding`, `blur`, `out_of_body` all correctly parsed)
- Finish reason: `STOP` (no truncation)
- History context with 3 windows is sufficient for phase continuity

### 8.5 Notes

- Gemini API latency varies significantly by time of day (8-55s observed for same config)
- Pipeline overlap benefit is most visible in multi-window sessions (saves ~4s per window after first)
- SurgR1 service stability should be monitored — service went down during extended benchmark runs
