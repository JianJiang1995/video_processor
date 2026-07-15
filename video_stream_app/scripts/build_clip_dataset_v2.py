#!/usr/bin/env python3
"""Build the corrected clip detector dataset (v2).

Starting from clip_detector_reviewed_seed_plus_gptimage2_imagebg_100_v1:
  - drop ALL images listed in label_corrections_20260709.json (poisoned
    negatives + uncertain) from train/val
  - re-add the audited subset of recovered-positive images with their
    recovered YOLO labels (datasets/clip_box_recovery_v1), keeping each
    image's original split
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

SRC = Path("datasets/clip_detector_reviewed_seed_plus_gptimage2_imagebg_100_v1")
DST = Path("datasets/clip_detector_corrected_v2")
CORR = Path("datasets/clip_detector_reviewed_seed_v1/label_corrections_20260709.json")
RECOVERY = Path("datasets/clip_box_recovery_v1")
SEED = Path("datasets/clip_detector_reviewed_seed_v1")

# Audited 2026-07-09: recovered images with at least one wrong/dubious box
# (glare, instrument shaft, applier jaw, oversized). Excluded entirely.
BAD_RECOVERED = {
    "reject_video43_1080.04_0087",
    "reject_video70_660.04_0110",
    "reject_video05_1200.04_0093",
    "reject_video12_590.04_0099",
    "reject_video2-1-1_36.00_0064",
    "reject_video2-1-1_40.00_0065",
    "reject_video21_774.04_0100",
    "reject_video24_875.04_0105",
    "reject_video24_880.04_0104",
    "reject_video30_1277.04_0106",
    "reject_video33_381.04_0107",
    "reject_video43_1078.04_0088",
    "reject_video43_1143.04_0089",
    "reject_video7-1-1_140.00_0070",
    "reject_video70_666.04_0111",
    "reject_video78_422.04_0112",
}


def main():
    corr = json.loads(CORR.read_text())
    drop_stems = {Path(c).stem for c in corr["relabel_to_clip"]} | {Path(c).stem for c in corr["exclude"]}

    if DST.exists():
        shutil.rmtree(DST)
    dropped = 0
    for split in ("train", "val"):
        (DST / "images" / split).mkdir(parents=True)
        (DST / "labels" / split).mkdir(parents=True)
        for img in sorted((SRC / "images" / split).glob("*")):
            if any(stem in img.name for stem in drop_stems):
                dropped += 1
                continue
            shutil.copy2(img, DST / "images" / split / img.name)
            lbl = SRC / "labels" / split / f"{img.stem}.txt"
            if lbl.exists():
                shutil.copy2(lbl, DST / "labels" / split / lbl.name)
            else:
                (DST / "labels" / split / f"{img.stem}.txt").write_text("")

    # find original split of each recovered image in the seed dataset
    added = {"train": 0, "val": 0}
    for lbl in sorted((RECOVERY / "labels").glob("*.txt")):
        stem = lbl.stem
        if stem in BAD_RECOVERED:
            continue
        seed_hits = list((SEED / "images").glob(f"*/{stem}.jpg"))
        if not seed_hits:
            continue
        split = seed_hits[0].parent.name
        new_name = f"recovered_{stem}"
        shutil.copy2(seed_hits[0], DST / "images" / split / f"{new_name}.jpg")
        shutil.copy2(lbl, DST / "labels" / split / f"{new_name}.txt")
        added[split] += 1

    (DST / "data.yaml").write_text(
        f"path: {DST.resolve()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n  0: hemolok_clip\n  1: titanium_clip\n"
    )
    for split in ("train", "val"):
        imgs = list((DST / "images" / split).glob("*"))
        pos = sum(1 for i in imgs if (DST / "labels" / split / f"{i.stem}.txt").read_text().strip())
        print(f"{split}: {len(imgs)} images ({pos} with boxes, {len(imgs)-pos} negatives)")
    print(f"dropped {dropped} poisoned/uncertain; re-added recovered: {added}")


if __name__ == "__main__":
    main()
