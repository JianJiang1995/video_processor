"""Bound expert preprocessing cost without changing source frames or clocks."""
import cv2
import numpy as np
from PIL import Image


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
            rgb = np.asarray(image.convert('RGB'))
    else:
        image = source.copy()
        image.thumbnail((edge, edge), Image.Resampling.BILINEAR)
        rgb = np.asarray(image.convert('RGB'))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
