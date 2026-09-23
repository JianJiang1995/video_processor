import inspect
import unittest

import cv2
import numpy as np

from backend.services import expert_fusion


class ComponentRoiTests(unittest.TestCase):
    def test_bounding_box_pixels_preserve_full_image_component_result(self):
        source = inspect.getsource(expert_fusion._detect_hemlok_clip_action)
        reference_source = source.replace(
            'small[y : y + bh, x : x + bw][component_mask]',
            'small[labels == comp_id]',
        )
        self.assertNotEqual(reference_source, source)
        namespace = dict(vars(expert_fusion))
        exec(reference_source, namespace)
        reference = namespace['_detect_hemlok_clip_action']
        rng = np.random.default_rng(43)
        frame = np.zeros((540, 960, 3), dtype=np.uint8)
        for _ in range(100):
            x, y = rng.integers(100, 840), rng.integers(30, 330)
            cv2.ellipse(frame, (int(x), int(y)), (8, 5), 0, 0, 360, (160, 185, 230), -1)
        frames = [frame, np.roll(frame, 3, axis=1)]
        expected = reference(frames)
        self.assertTrue(expected['hits'], 'Fixture must exercise candidate color checks')
        self.assertEqual(expert_fusion._detect_hemlok_clip_action(frames), expected)
