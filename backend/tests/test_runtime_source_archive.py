import ast
import hashlib
import json
from pathlib import Path
import unittest


class RuntimeSourceArchiveTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[2] / 'deployment/runtime_sources'

    def test_archive_matches_recorded_checksums_and_contains_parseable_source(self):
        manifest = json.loads((self.ROOT / 'manifest.json').read_text())
        self.assertEqual(len(manifest['files']), 3)
        for entry in manifest['files']:
            with self.subTest(path=entry['path']):
                path = self.ROOT / entry['path']
                self.assertTrue(path.resolve().is_relative_to(self.ROOT.resolve()))
                data = path.read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
                if path.suffix == '.py':
                    ast.parse(data, filename=entry['path'])
                else:
                    self.assertIsInstance(json.loads(data), dict)

    def test_required_imported_model_classes_are_present_without_loading_gpu(self):
        for relative, required in (
            ('SurgR1_api/main.py', {'SurgR1Model', 'ImageAnalysisRequest'}),
            ('surg_agent/triplet_expert/eval_lam_on_our_val.py', {'LAMLite'}),
        ):
            tree = ast.parse((self.ROOT / relative).read_text())
            classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
            self.assertTrue(required <= classes)


if __name__ == '__main__':
    unittest.main()
