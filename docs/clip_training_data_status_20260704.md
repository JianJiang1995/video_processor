# Hem-o-lok / Titanium Clip Training Data Status

Date: 2026-07-04

## Current conclusion

The current bottleneck is label quality, not GPU capacity.

Hem-o-lok already has usable signal after temporal review. Titanium clip remains under-sampled: the clean seed set has too few true titanium clip positives for a deployable detector.

## Data generated

### Temporal review candidate set

Path: `datasets/clip_temporal_review_candidates_v1`

- Candidates: 162
- Hem-o-lok initial candidates: 43
- Titanium initial candidates: 119
- Artifacts:
  - `candidates.jsonl`
  - `index.html`
  - `sequences/`
  - `crops/`

This set renders `-1s / 0s / +1s` temporal review sheets and local crops, so GPT or a human reviewer can reject one-frame glare and instrument edges.

### GPT-5.5 temporal review

Titanium initial candidates:

- Reviewed: 119
- Kept as titanium clip: 20
- Relabeled as Hem-o-lok: 8
- Rejected: 91

Hem-o-lok initial candidates:

- Reviewed: 43
- Kept as Hem-o-lok: 28
- Relabeled as titanium clip: 1
- Rejected: 14

Trainable high-precision positives after requiring `use_for_training=true`:

- Hem-o-lok: 28
- Titanium clip: 14

Key finding: many old `titanium_clip` candidates were actually blue/colored polymer locking clips, instrument edges, tissue, or specular highlights.

### YOLO seed dataset

Path: `datasets/clip_detector_reviewed_seed_v1`

- Images: 126
- Positive images: 42
- Hard-negative images: 84
- Objects:
  - `hemolok_clip`: 28
  - `titanium_clip`: 14
- Audit sheets:
  - `datasets/clip_detector_reviewed_seed_v1/audit_hemolok_clip.jpg`
  - `datasets/clip_detector_reviewed_seed_v1/audit_titanium_clip.jpg`

### Neighbor-frame expansion pool

Path: `datasets/clip_reviewed_seed_window_samples_v1`

- Frames: 473
- Source positive objects:
  - `hemolok_clip`: 28
  - `titanium_clip`: 14
- Video/label time groups: 14
- Sampling: +/- 4 seconds, 0.5-second interval

This is not final labeled training data. It is the next high-value pool for expansion review.

## Baseline training result

Model path:

`models/clip_detector/yolo_clip_detector_reviewed_seed_v1/weights/best.pt`

Training:

- Base: `yolo11n.pt`
- Images: 126
- Epochs completed: 62, early stopped
- Best epoch: 37

Validation result:

- All: P 0.818, R 0.354, mAP50 0.358, mAP50-95 0.175
- Hem-o-lok: P 0.636, R 0.709, mAP50 0.715, mAP50-95 0.349
- Titanium clip: P 1.0, R 0.0, mAP50 0.0, mAP50-95 0.0

Interpretation:

- Hem-o-lok is learnable with the current reviewed data.
- Titanium clip still has too few clean positives and too few validation instances.
- This model should not be deployed as the final expert.

## Next step

Use `datasets/clip_reviewed_seed_window_samples_v1` for another temporal review pass, focused on true titanium clip expansion. The goal should be at least 100-200 clean titanium positives plus hard negatives before training another detector.

Avoid training on unreviewed full-frame GPT labels. The previous failure mode was exactly that: titanium labels were polluted by glare, applier jaws, and colored polymer clips.
