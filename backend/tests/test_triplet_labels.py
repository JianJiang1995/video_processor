import json
import tempfile
import unittest
from pathlib import Path

from backend.services.triplet_service import load_label_maps, OFFICIAL_MAPPINGS, LABEL_SCHEMA, TripletService


class TripletLabelTests(unittest.TestCase):
    def test_official_head_and_triplet_ids_not_alphabetical(self):
        labels = load_label_maps(OFFICIAL_MAPPINGS)
        self.assertEqual(labels['instruments'][0], 'grasper')
        self.assertEqual(labels['targets'][0], 'gallbladder')
        self.assertEqual(labels['targets'][10], 'omentum')
        self.assertEqual(labels['targets'][14], 'null_target')
        self.assertEqual(labels['triplets'][17], '[grasper]-[retract]-[gallbladder]')
        self.assertEqual(labels['triplets'][79], '[clipper]-[clip]-[cystic_duct]')
        self.assertEqual(labels['triplets'][97], '[scissors]-[null_verb]-[null_target]')

    def test_reordered_map_rejected_even_with_correct_dimensions(self):
        data = json.loads(OFFICIAL_MAPPINGS.read_text())
        data['targets']['0'], data['targets']['7'] = data['targets']['7'], data['targets']['0']
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'labels.json'
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'class order'):
                load_label_maps(path)
        with self.assertRaises(ValueError):
            load_label_maps(OFFICIAL_MAPPINGS, 'other-checkpoint')

    def test_predictions_preserve_raw_ids_and_schema(self):
        import torch
        service = object.__new__(TripletService)
        for key, value in load_label_maps(OFFICIAL_MAPPINGS).items():
            setattr(service, key, value)
        service.label_schema = LABEL_SCHEMA
        service.device = torch.device('cpu')
        service._to_tensor = lambda image: torch.zeros(3, 2, 2)
        def predict(_):
            heads = {k: torch.full((1, n), -20.) for k, n in [('i', 6), ('v', 10), ('t', 15), ('ivt', 100)]}
            for key, index in [('i', 0), ('v', 1), ('t', 0), ('ivt', 17)]:
                heads[key][0, index] = 10
            return heads
        service.model = predict
        result = service.recognize_clip([None] * 10)
        self.assertEqual(result['label_schema'], LABEL_SCHEMA)
        self.assertEqual(result['triplet'][0]['class_id'], 17)
        self.assertEqual(result['target'][0]['label'], 'gallbladder')
