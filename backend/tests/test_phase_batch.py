import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import torch
from torchvision import transforms as T

from backend.services.phase_service import PhaseService


class PhaseBatchTests(unittest.TestCase):
    def test_batch_matches_single_frame_order_values_and_input_formats(self):
        service = PhaseService.__new__(PhaseService)
        service.device = torch.device('cpu')
        service.classes = ['a', 'b', 'c']
        service.transform = T.Compose([T.Resize((8, 8)), T.ToTensor()])
        service.model = torch.nn.Sequential(torch.nn.AdaptiveAvgPool2d(1), torch.nn.Flatten())
        images = [np.full((16, 12, 3), (i * 7, i * 11, 150), dtype=np.uint8) for i in range(19)]
        images[2] = Image.fromarray(images[2])
        expected = [service.classify(im) for im in images]
        with patch.object(service.model, 'forward', wraps=service.model.forward) as forward:
            actual = service.classify_batch(images)
            self.assertEqual(forward.call_count, 2)
        self.assertEqual(actual, expected)
        self.assertEqual(service.classify_batch([]), [])
