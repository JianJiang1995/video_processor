"""Exclude configured device overlays from inference, never from playback."""
import math

from .local_video_source import split_input_layout


REGIONS_KEY = "analysis_exclusion_regions"
CROP_RIGHT_KEY = "analysis_crop_right_fraction"
APPLIED_CROP_RIGHT_KEY = "analysis_applied_crop_right_fraction"


def valid_regions(regions):
    result = []
    for region in regions if isinstance(regions, (list, tuple)) else ():
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            continue
        try:
            x1, y1, x2, y2 = map(float, region)
        except (TypeError, ValueError):
            continue
        if 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1:
            result.append((x1, y1, x2, y2))
    return result


def regions_for_source(video_source, config):
    _, layout = split_input_layout(video_source)
    profiles = config.get("analysis_overlay_exclusions") or {}
    return valid_regions(profiles.get(layout, []))


def valid_crop_right(value):
    try:
        fraction = float(value)
    except (TypeError, ValueError):
        return 0.0
    # Device overlays occupy a narrow edge; malformed settings must not remove
    # most of the operative field.
    return fraction if math.isfinite(fraction) and 0 <= fraction <= .25 else 0.0


def crop_right_for_source(video_source, config):
    _, layout = split_input_layout(video_source)
    profiles = config.get("analysis_input_crop_right") or {}
    return valid_crop_right(profiles.get(layout, 0))


def mask_analysis_image(image, regions=None):
    """Mask a private, resized inference image; coordinates are post-layout ratios."""
    regions = valid_regions(image.info.get(REGIONS_KEY) if regions is None else regions)
    if not regions:
        return image
    result = image.copy()
    width, height = result.size
    for x1, y1, x2, y2 in regions:
        result.paste(0, (math.floor(x1 * width), math.floor(y1 * height),
                         math.ceil(x2 * width), math.ceil(y2 * height)))
    result.info[REGIONS_KEY] = regions
    return result


def prepare_analysis_image(image):
    """Consume inference-only geometry once, preserving the shared source image."""
    regions = valid_regions(image.info.get(REGIONS_KEY))
    fraction = valid_crop_right(image.info.get(CROP_RIGHT_KEY))
    if not regions and not fraction:
        return image
    result = mask_analysis_image(image, regions)
    if fraction:
        width = max(1, math.floor(result.width * (1 - fraction)))
        result = result.crop((0, 0, width, result.height))
        result.info[APPLIED_CROP_RIGHT_KEY] = fraction
    # PIL preserves info through copies/resizes. Clear pending transforms so
    # nested VLM encoders and history -> SurgR1 paths cannot crop twice.
    result.info.pop(REGIONS_KEY, None)
    result.info.pop(CROP_RIGHT_KEY, None)
    return result
