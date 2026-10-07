"""Bound expert preprocessing cost without changing source frames or clocks."""
import cv2
import numpy as np
from PIL import Image
from .analysis_image_regions import prepare_analysis_image, valid_crop_right


def expert_warmup_sizes(config):
    """Warm both unmodified and configured post-crop detector tensor shapes."""
    profiles = config.get('analysis_input_crop_right') or {}
    fractions = {0.0} | {valid_crop_right(value) for value in profiles.values()}
    return sorted({(max(1, int(1280 * (1 - fraction))), height)
                   for fraction in fractions for height in (960, 720)})


def prepare_expert_frame(source, max_edge=1280):
    edge = max(1, int(max_edge))
    filename = getattr(source, 'filename', None)
    if filename and source.format == 'JPEG' and max(source.size) > edge:
        # A private JPEG decoder can downsample before allocating the full RGB
        # image. Never mutate the shared image used later by SurgR1/the VLM.
        with Image.open(filename) as image:
            ratio = min(1, edge / max(image.size))
            image.draft('RGB', (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))))
            image.thumbnail((edge, edge), Image.Resampling.BILINEAR)
            image.info.update(source.info)
            rgb = np.asarray(prepare_analysis_image(image.convert('RGB')))
    else:
        image = source.copy()
        image.thumbnail((edge, edge), Image.Resampling.BILINEAR)
        rgb = np.asarray(prepare_analysis_image(image.convert('RGB')))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
