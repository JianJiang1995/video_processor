import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import torch

from backend.services.yolo_service import YOLOService
from backend.services.clip_detector_service import ClipDetectorService
from backend.services.phase_service import PhaseService


class ExpertCpuResultsTests(unittest.TestCase):
    def boxes(self):
        box = SimpleNamespace(xyxy=torch.tensor([[10., 20., 30., 40.]]),
                              cls=torch.tensor([0.]), conf=torch.tensor([0.875]))
        class CpuBoxes(list):
            id = torch.tensor([12.])
        # Only the CPU result can be iterated, catching per-box GPU reads.
        device_boxes = Mock()
        device_boxes.cpu.return_value = CpuBoxes([box])
        return device_boxes

    def test_yolo_cpu_transfer_preserves_coordinates_and_tracking(self):
        service = YOLOService.__new__(YOLOService)
        service.conf_threshold = 0.25
        service.device = 'cpu'
        service._frame_counter = 0
        service._smooth_class = Mock(return_value='grasper')
        boxes = self.boxes()
        service.model = Mock(names={0: 'grasper'})
        service.model.track.return_value = [SimpleNamespace(boxes=boxes)]
        result = service.detect(np.zeros((100, 100, 3), dtype=np.uint8))
        self.assertEqual(result, [dict(x1=10, y1=20, x2=30, y2=40,
                                     label='grasper', raw_label='grasper',
                                     confidence=0.875, class_id=0, track_id=12)])
        boxes.cpu.assert_called_once()
        service._smooth_class.assert_called_once_with(12, 'grasper')

    def test_clip_cpu_transfer_preserves_filtering_and_confidence(self):
        service = ClipDetectorService.__new__(ClipDetectorService)
        service.conf_threshold = 0.25
        service.max_area_ratio = 0.12
        service.imgsz = 960
        service.device = 'cpu'
        boxes = self.boxes()
        service.model = Mock(names={0: 'titanium_clip'},
                             return_value=[SimpleNamespace(boxes=boxes)])
        result = service.detect(np.zeros((100, 100, 3), dtype=np.uint8))
        self.assertEqual(result[0]['label'], 'surgical_clip')
        self.assertEqual(result[0]['raw_label'], 'titanium_clip')
        self.assertEqual(result[0]['confidence'], 0.875)
        self.assertEqual(result[0]['area_ratio'], 0.04)
        boxes.cpu.assert_called_once()

    def test_single_frame_phase_matches_batch_probabilities(self):
        service = PhaseService.__new__(PhaseService)
        service.device = 'cpu'
        service.classes = ['Preparation', 'ClippingCutting']
        service.transform = lambda _: torch.zeros((3, 16, 16))
        service.model = lambda batch: torch.tensor([[0.2, 0.8]]).repeat(len(batch), 1)
        frame = np.zeros((16, 16, 3), dtype=np.uint8)
        self.assertEqual(service.classify(frame), service.classify_batch([frame])[0])


if __name__ == '__main__':
    unittest.main()
