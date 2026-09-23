import json
from pathlib import Path
import tempfile
import unittest

from scripts.record_electron_replays import make_recording_spec


class ReplaySpecTests(unittest.TestCase):
    def test_copy_preserves_relative_asset_locations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input' / 'analysis.replay.json'
            source.parent.mkdir()
            source.write_text(json.dumps({'summaries_path': 'summaries.json',
                'events_path': 'events.json', 'video_path': '/absolute/video.mp4',
                'report_path': '../summary.md', 'session': {'video_path': 'source.mp4'}}))
            spec = make_recording_spec(source, root / 'output' / 'spec.json')
            self.assertEqual(spec['summaries_path'], str(source.parent / 'summaries.json'))
            self.assertEqual(spec['events_path'], str(source.parent / 'events.json'))
            self.assertEqual(spec['video_path'], '/absolute/video.mp4')
            self.assertEqual(spec['report_path'], str(root / 'summary.md'))
            self.assertEqual(spec['session']['video_path'], str(source.parent / 'source.mp4'))
            self.assertFalse(spec['auto_play'])
