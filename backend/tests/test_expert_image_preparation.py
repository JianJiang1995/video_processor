import tempfile
import unittest
from pathlib import Path

from PIL import Image

from backend.services.expert_image_preparation import prepare_expert_frame


class ExpertImageTests(unittest.TestCase):
    def test_jpeg_private_decoder_preserves_source_size_and_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'frame.jpg'
            Image.new('RGB', (3840, 2160), (230, 20, 10)).save(path)
            with Image.open(path) as source:
                source.info['source_timestamp'] = 45.6
                frame = prepare_expert_frame(source)
                self.assertEqual(frame.shape, (720, 1280, 3))
                self.assertEqual(source.size, (3840, 2160))
                self.assertEqual(source.info['source_timestamp'], 45.6)
                self.assertGreater(int(frame[0, 0, 2]), int(frame[0, 0, 0]))

    def test_no_upscale_or_crop_and_memory_input_unchanged(self):
        for size, expected in [((320, 240), (240, 320, 3)), ((2160, 3840), (1280, 720, 3))]:
            source = Image.new('RGBA', size, (10, 20, 30, 255))
            result = prepare_expert_frame(source)
            self.assertEqual(result.shape, expected)
            self.assertEqual(source.size, size)
            self.assertEqual(source.mode, 'RGBA')
