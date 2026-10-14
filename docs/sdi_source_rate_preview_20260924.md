# Source-rate SDI preview (2026-09-24)

## Scope

Remove the preview-only frame-rate and size restrictions without increasing
model sampling. Diagnose manual 1080/50 input selection using the attached
DeckLink Mini Recorder 4K, not a simulator. Preserve browser rendering, vertical
synchronization, source timestamps, capture ownership and the analysis pipeline.

## Findings and Changes

1. The UI requested 30 fps and a maximum width of 1280 for its WebSocket preview.
   The backend also imposed a 30 fps ceiling. Its integer decimation turned a
   50 fps source into 25 fps. The MJPEG fallback had additional lower defaults.
2. Both display endpoints and UI URLs now use `fps=0` and `max_width=0`: follow
   source arrival and preserve source dimensions after any selected input-layout
   crop. Fractional rates remain fractional. An explicit lower limit is still
   available to API clients, but the normal UI no longer requests one. The
   original WebSocket JPEG quality of 78 is retained.
3. New JPEGs wake subscribed clients immediately instead of waiting for a
   periodic send loop. Each shared display worker stores only the latest JPEG.
   Acknowledgements still permit only one frame in flight per browser client;
   slow consumers skip stale frames instead of accumulating playback delay.
   Disconnects are also consumed when the source has no signal.
4. WebSocket per-message deflate compressed JPEGs a second time. In an isolated
   full-HD test this limited receive/decode throughput to about 22 fps. Disable
   it in `run_backend.sh`, `start_server.sh` and the direct Python entry point.
   The verified HTTP upgrade response has no compression extension even when
   the client offers one. This does not remove JPEG compression.
5. Manual mode selection did not distinguish frame rate from field rate clearly.
   Current input is **1920x1080 progressive, 50 fps**. Select **1080p50**.
   **1080i50** means 25 interlaced frames / 50 fields per second and is a different
   wire format. It cannot lock this progressive input. Labels now spell out
   progressive/interlaced and frame/field rates in both languages, and list
   progressive modes first. A no-signal message points to mode/scan mismatch.
   There is no silent fallback from a requested manual mode to auto.
6. Before input lock, the device card used to display a fabricated 1080p30
   descriptor. It now states that signal format is detected after connection.

The earlier physical-card handoff and no-signal disconnect fixes remain intact;
see `sdi_desktop_startup_fix_20260924.md`.

## Model Sampling Is Unchanged

No changes were made to `config.json` or `frame_capture_service.py` for this task.
Relevant existing settings are:

| Setting | Existing value |
| --- | --- |
| Analysis window | 5 seconds |
| Analysis sample interval | 1 second |
| Live frame storage | 5 fps, JPEG quality 68 |
| Stored live thumbnails | 2 fps |
| SurgR1 window-review image budget | 2 images |
| Local visual-observation image budget | 3 images |

The 5 fps storage stream is not the display stream, and not every stored frame
is sent to each model. The application's recorder also retains its separate
settings; this change does not increase its recording rate.

The real UI session `c2f743c6` logged display `fps=50.0, max_width=source` while
the capture service logged `capture_fps=5, preview_fps=2, quality=68`. Frames
continued to be saved and window analyses continued to update during testing.

## Verification

- Backend regression suite: **333 tests passed**. Added source-rate, native-size,
  notification, latest-frame and endpoint tests; retained slow-client and
  no-signal disconnect regressions.
- Frontend: **46 tests passed**, including both preview URL variants and
  progressive/interlaced labels. Production build, Python compilation and shell
  syntax checks passed.
- Same-process hardware sequence: SDI/auto locked 1080p50 in 0.471 s; manual
  1080p50 succeeded in 0.873 s; deliberate 1080i50 reported no signal; returning
  to 1080p50 succeeded in 1.006 s. Successful modes had zero reconnects.
- Actual Electron UI manually selected SDI/1080p50, displayed the source and
  automatically started analysis. Final reopened session: `f1a3f59e`.

Final isolated preview benchmark, after warm-up, 10-second measurement, JPEG
quality 78, 1920x1080 source, no concurrent model analysis:

| Measurement | Result |
| --- | --- |
| WS receive plus OpenCV JPEG decode | 50.00 fps |
| Transport inter-frame interval, median / P95 | 20.02 / 22.48 ms |
| Real `VideoPlayer.vue` in headless Chromium, decode plus canvas draw | 41.16 fps |
| Browser inter-draw interval, median / P95 | 23.80 / 27.80 ms |
| Browser decode duration, P95 | 21.60 ms |
| Canvas backing dimensions | 1920x1080 |
| Browser errors | None |

These numbers describe separate stages, not guaranteed physical-screen frame
rate. Earlier concurrent analysis/recording/browser experiments had lower draw
rates (about 29-38 fps at quality 85). Removing software caps does **not** prove
stable 50 fps screen presentation under all loads. The rendering alternatives
tested did not consistently improve throughput, so no replacement decoder,
extra queued frame or forced software/GPU rendering change was introduced.

Frame intervals and decode times are not optical input-to-screen latency.
This test did not measure optical latency or a 4K/50 input. Software cannot
make this card accept unsupported input timings. At measurement time, the source
was a blue-gray endoscope view with overlay icons, so this validates transport,
not anatomical recognition or clinical safety.

## Evidence and Operations

Evidence directory:

`/home/user/proj/video_processor/video_stream_app/recordings/sdi_live_20260924/`

- `final_source_rate_benchmark.json`: final transport/browser measurements and
  locked input status.
- `final_source_rate_browser.png`: actual component rendering screenshot.
- `manual_1080p50_selection.png`: Electron manual-mode selection.
- `after_source_rate_preview.png`: final live Electron analysis view.
- `final_source_rate_live_status.json`: final active capture/analysis status.
- `source_rate_browser.json`: earlier concurrent-load experiment, not the final
  isolated benchmark.

Backend test log: `logs/source_rate_preview_tests_20260924.log`.

Use the desktop shortcut as before. For the currently attached source, choose
SDI and 1080p50 (progressive 50 fps), or auto-detection. Do not choose 1080i50.
Close the current session before changing the physical mode. Do not run an
independent GStreamer capture process while the application owns the card.
