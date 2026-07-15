# Local VLM 4090 Benchmark

Date: 2026-07-07

This benchmark path evaluates newer local VLMs for the surgical visual-review
module. Qwen2.5-VL is intentionally excluded.

## Candidates

Candidate profile:

`config_profiles/local_vlm_4090_candidates_20260707.json`

Default candidates:

- `qwen3-vl-8b-instruct`
- `minicpm-o-4.5`
- `glm-4.6v-flash-9b`
- `qwen3-vl-30b-a3b-instruct-awq` as offline quality reference only

Realtime deployment should use one RTX 4090 24GB for the selected VLM. A second
4090 may be used for parallel benchmark runs or offline batch processing, but
not as the default realtime path.

## Start One Local VLM Server

Download with ModelScope if needed:

```bash
modelscope download --model Qwen/Qwen3-VL-8B-Instruct --local_dir /data/models/Qwen3-VL-8B-Instruct
```

Start one OpenAI-compatible local server on a single GPU:

```bash
cd /home/user/proj/video_processor/video_stream_app
CUDA_VISIBLE_DEVICES=2 /home/user/proj/video_processor/.venv/bin/python scripts/local_openai_vlm_server.py \
  --model-path /data/models/Qwen3-VL-8B-Instruct \
  --served-model-name Qwen3-VL-8B-Instruct \
  --port 8010 \
  --max-concurrent 1
```

Use the matching `openai_base_url` and `served_model_name` from the profile for
other candidates.

## Run Benchmark

Example for the Video12 late-window failure region:

```bash
cd /home/user/proj/video_processor/video_stream_app
/home/user/proj/video_processor/.venv/bin/python scripts/benchmark_local_vlm_candidates.py \
  --video /data/cholec80/cholec80/videos/video12.mp4 \
  --windows 185-195 \
  --candidate qwen3-vl-8b-instruct
```

Example for known clip/scissors/CVS windows:

```bash
/home/user/proj/video_processor/.venv/bin/python scripts/benchmark_local_vlm_candidates.py \
  --video /data/cholec80/cholec80/videos/video12.mp4 \
  --windows 76,185-195 \
  --candidate qwen3-vl-8b-instruct
```

Outputs are written under:

`runs/local_vlm_benchmark/<timestamp>/`

Important files:

- `results.jsonl`: per-window raw and parsed model outputs
- `summary.md`: success rate, strict JSON rate, average latency

## Acceptance Focus

- Window 191/192 region should detect `visibility.status="out_of_body"` when
  the scope leaves the abdomen.
- Clip windows should avoid unstable Hem-o-lok/titanium switching.
- Scissors before CVS should be identified as a safety risk, not as confirmed
  cystic duct/artery division.
- Routine traction, exposure, irrigation and ordinary dissection should not
  become bottom key-event nodes.
