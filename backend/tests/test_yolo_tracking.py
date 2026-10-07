import unittest
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.services.yolo_service import YOLOService


class YoloTrackingTests(unittest.TestCase):
    def test_warmup_registers_persistent_tracking_and_resets_dummy_frame(self):
        model = Mock()
        tracker = Mock()
        model.predictor.trackers = [tracker]
        with patch.dict(sys.modules, {'ultralytics': SimpleNamespace(YOLO=Mock(return_value=model))}):
            service = YOLOService('/unused/test.pt', 'cpu')
        self.assertTrue(model.track.call_args.kwargs['persist'])
        tracker.reset.assert_called_once()
        self.assertEqual(service._frame_counter, 0)
        self.assertEqual(service._class_votes, {})

    def test_reset_discards_previous_id_votes(self):
        model = Mock()
        model.predictor.trackers = [Mock()]
        with patch.dict(sys.modules, {'ultralytics': SimpleNamespace(YOLO=Mock(return_value=model))}):
            service = YOLOService('/unused/test.pt', 'cpu')
        self.assertEqual(service._smooth_class(1, 'grasper'), 'grasper')
        service.reset_tracker()
        self.assertEqual(service._smooth_class(1, 'bipolar'), 'bipolar')
