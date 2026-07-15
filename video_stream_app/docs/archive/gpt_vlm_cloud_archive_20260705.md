# GPT/Gemini VLM Cloud Route Archive

Archived at: 2026-07-05

This archive preserves the previous external Gemini/GPT visual-analysis route so
the current local YOLO/local-VLM wording rules can evolve independently.

## What Is Archived

- Config profile: `config_profiles/gpt_vlm_cloud_legacy.json`
- Source baseline: `ff16adf:config.json`
- Main providers:
  - Window analysis: Gemini
  - Chat assistant: Gemini
  - Realtime open vision: Gemini with GPT fallback
  - Translation, clinical summary, event nodes: Gemini
  - Embeddings: Gemini embedding

## Restore Steps

1. Merge `config_profiles/gpt_vlm_cloud_legacy.json` into `config.json`.
2. Unset local-only blockers:
   - `DISABLE_EXTERNAL_AI`
   - `DISABLE_REALTIME_OPEN_VISION`
   - `DISABLE_EMBEDDINGS`
   - `DISABLE_SUMMARY_COMPRESSION`
3. Export the required API keys:
   - `GEMINI_API_KEY`
   - `OPENAI_API_KEY` if GPT fallback is enabled
4. Restart backend and Electron.

## Deployment Note

This route can send surgical images and text summaries to external services. It
should stay disabled for private/local deployment unless data governance approves
external API use.

## Current Local Route

The active local route keeps the video-analysis wording in
`backend/routers/analysis.py`:

- `_polish_summary_wording`
- `_compact_local_summary_text`
- `_strip_clipping_noise`
- `_expert_snapshot_summary`
- `_apply_surgical_sequence_rules`

Those rules are tuned for local YOLO, phase, triplet and clip-detector outputs.
They should not be treated as the cloud GPT prompt behavior.
