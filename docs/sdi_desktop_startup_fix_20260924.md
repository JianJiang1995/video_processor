# Desktop SDI startup fix (2026-09-24)

## Root Causes

1. `StreamInput.vue` initialized every capture form with the device descriptor's
   HDMI default. The selected SDI connector and mode were not persisted. The
   desktop launcher enabled auto-connect, previously only 250 ms after discovery,
   so a fresh desktop launch could open HDMI despite an attached SDI source.
2. `decklink_capture.py` shared pipelines by full URI, not by physical device.
   After a connection probe released its reader, the pipeline stayed open for a
   five-second grace period. Switching connector or video mode during that grace
   period attempted a second pipeline on the same card. Errors included failed
   video-input enablement and "GStreamer rejected the DeckLink pipeline". This
   could look like an auto-format detection failure, but SDI auto-detection itself
   worked once the old pipeline was released.
3. The preview WebSocket only called `receive_text()` after sending a frame. With
   an incorrect mode or missing signal, no frame was sent, so the server never
   consumed the browser's disconnect event. After leaving the UI, that preview
   retained a reader indefinitely, preventing a subsequent configuration change.

## Changes

- Persist the last successful capture request in local storage under
  `surgr1_capture_preferences_v1`, with separate settings for each backend/device.
  Restore connector, video mode, full/quad-right layout, and simulator path.
- Prefer the previously selected device. Validate saved settings against the
  current device's advertised modes/connectors. Missing or invalid preferences
  require manual connection; they never silently auto-start a substituted port.
- Automatic reconnection is restricted to a configured physical capture device.
  A simulator file is only started manually. Returning to source selection in the
  same renderer does not automatically connect again.
- Manual source/settings interaction cancels pending automatic connection.
  Block double-click connection requests and disable settings while connecting.
  Ignore aborted responses and isolate cleanup to its own request controller.
- Reuse the same pipeline for preview and analysis using identical settings.
  Canonicalize equivalent DeckLink URIs. Before changing configuration on the same
  device, stop an idle pipeline and wait for its worker/teardown to finish. Do not
  open a replacement if teardown times out, or if the old pipeline has readers.
- Keep input negotiation on `auto`; no fixed resolution, frame rate, timestamp,
  or video-specific rule was added. Keep useful GStreamer bus errors on startup
  failure rather than always replacing them with a generic message.
- Consume WebSocket disconnect events while waiting for the first/next frame as
  well. The last DeckLink preview client stops its display worker immediately;
  use a 250 ms no-frame read timeout so it releases promptly. Other video sources
  retain their existing preview reconnect grace, and per-frame acknowledgements
  still limit the browser to one in-flight frame.

## Verification

- Backend: 327 tests passed, including 16 DeckLink configuration/ownership tests
  and 6 preview-disconnect/lifetime tests.
- Frontend: 43 tests passed, including 7 capture-preference tests; Vite build passed.
- Real hardware, with the application stopped: release HDMI/auto and immediately
  open SDI/auto in one process. First SDI frame arrived after 0.981 seconds, with
  1920x1080 progressive at 50 fps, zero reconnects and no input error.
- While SDI was active, attempting HDMI was rejected as busy. SDI sequence advanced
  from 1 to 52 across the following second, confirming the active input survived.
- Actual Electron UI connection: session `b7b0c30f`, SDI/auto/full, 1920x1080p50,
  zero reconnects. Preview and analysis shared two readers. Subsequent windows
  were generated without manually starting analysis. The source image at this
  time was blue-gray with endoscope overlay icons, not a useful anatomical view;
  this verifies input transport, not clinical recognition quality.
- Following a full application restart, session `bfb7d6b5` connected on SDI/auto
  again (HTTP connect request 447 ms). No forced HDMI reset was observed.
- Real no-signal regression: deliberately select SDI/1080i50 against the current
  progressive source, open a preview socket, observe no frames, then close the
  socket. The preview reader dropped to zero within 0.119 seconds. An immediate
  SDI/auto connection produced a JPEG frame at 1920x1080p50 with zero reconnects.
  Evidence: `recordings/sdi_live_20260924/startup_disconnect_verification.json`.
- Final running desktop session after all fixes: `0e053715`, SDI/auto/full,
  1920x1080p50, two readers, zero reconnects. Four windows had reached 20 seconds,
  with both initial and subsequent updated summaries present. UI screenshot:
  `recordings/sdi_live_20260924/after_desktop_startup_fix.png`.

## Operating Notes

On first use, select the actual physical connector (SDI for today's cable),
automatic video mode, and the appropriate full/quad-right layout. Connect once;
subsequent desktop launches restore that configuration. The layout is independent
of SDI vs HDMI: do not crop a single-view source as a four-view source.

The registry coordinates consumers inside one backend process. Do not run an
independent `gst-launch` or hardware preflight process while the app is capturing;
those tools bypass the registry and also need exclusive access to the card.
The desktop launcher already prevents duplicate launcher instances.

Unsupported source timings cannot be fixed by software scaling before capture
lock. This change does not add unsupported input formats to the hardware.
