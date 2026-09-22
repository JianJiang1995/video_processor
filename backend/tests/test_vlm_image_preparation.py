"""Tests for provider-independent VLM image sizing."""

import unittest

from PIL import Image

from backend.routers.analysis import _fit_image_for_vlm


class VlmImagePreparationTests(unittest.TestCase):
    def test_downscales_full_4k_frame_without_cropping(self) -> None:
        source = Image.new("RGBA", (3840, 2160), (10, 20, 30, 255))

        fitted = _fit_image_for_vlm(source, 640)

        self.assertEqual((640, 360), fitted.size)
        self.assertEqual("RGB", fitted.mode)
        self.assertEqual((3840, 2160), source.size)

    def test_does_not_upscale_smaller_frame(self) -> None:
        source = Image.new("RGB", (320, 180), (10, 20, 30))

        fitted = _fit_image_for_vlm(source, 640)

        self.assertEqual((320, 180), fitted.size)
        self.assertIsNot(source, fitted)


if __name__ == "__main__":
    unittest.main()
