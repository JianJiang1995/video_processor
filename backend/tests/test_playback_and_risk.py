import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import video
from backend.routers.analysis import _confirmed_scissors_risk_ids, _ensure_required_event_nodes, _normalize_event_nodes
from backend.services.clip_review import scissors_morphology_confirmed, validate_clip_review
from backend.services.playback_proxy import preview_format


class PlaybackAndRiskTests(unittest.TestCase):
    def test_4k50_preview_preserves_regular_cadence(self):
        self.assertEqual(preview_format({'width': 3840, 'height': 2160, 'fps': 50}),
                         {'width': 1920, 'height': 1080, 'fps': 25})
        self.assertEqual(preview_format({'width': 1280, 'height': 720, 'fps': 29.97})['fps'], 29.97)

    def test_hook_cannot_become_active_applier_even_at_high_confidence(self):
        result = validate_clip_review({'classification': 'clip_applier', 'confidence': .99,
                                      'tip_geometry': 'single_hook', 'motion': 'contact',
                                      'applier_active': True, 'clamped_on_tissue': True,
                                      'target': 'cystic_duct'})
        self.assertEqual(result['classification'], 'electrocautery_hook')
        self.assertFalse(result['applier_active'])
        self.assertEqual(result['target'], 'unknown')

    def test_confirmed_applier_closure_and_independent_clip_survive(self):
        result = validate_clip_review({'classification': 'clip_applier', 'tip_geometry': 'broad_parallel_jaws',
                                      'motion': 'jaw_closure_on_tissue', 'applier_active': True})
        self.assertTrue(result['action_evidence_supported'])
        self.assertTrue(result['applier_active'])
        clip = validate_clip_review({'classification': 'clip', 'tip_geometry': 'independent_clip',
                                     'independent_from_instrument': True, 'count': 2})
        self.assertEqual(clip['classification'], 'clip')
        self.assertEqual(clip['count'], 2)
        disputed = validate_clip_review(clip, primary={'generic_clip': {'visible': False}})
        self.assertEqual(disputed['classification'], 'unconfirmed')
        self.assertFalse(disputed['accepted'])
        agreed = validate_clip_review(clip, primary={'generic_clip': {'visible': True}})
        self.assertEqual(agreed['classification'], 'clip')

    def test_scissors_need_distinct_reviewed_frames_and_no_hook_veto(self):
        visual = {'scissors_secondary_review': {'success': True, 'confidence': .95,
                  'instrument': 'scissors', 'scissors_visible': True, 'images': 3,
                  'blade_frame_indices': [0, 0, 99]}}
        self.assertFalse(scissors_morphology_confirmed(visual))
        visual['scissors_secondary_review']['blade_frame_indices'] = [0, 2]
        self.assertTrue(scissors_morphology_confirmed(visual))
        visual['clip_secondary_review'] = {'success': True, 'confidence': .95,
                                          'classification': 'electrocautery_hook'}
        self.assertFalse(scissors_morphology_confirmed(visual))

    def test_risk_requires_adjacent_confirmations_and_respects_cvs(self):
        def row(i, verified=True, summary='剪刀操作，CVS尚未达成'):
            return {'window_id': i, 'start_time': i * 5, 'end_time': (i + 1) * 5,
                    'phase': 'CalotTriangleDissection', 'summary': summary,
                    'scissors': 'scissors_visible', 'scissors_verified': verified}
        self.assertEqual(_confirmed_scissors_risk_ids([row(1)]), set())
        self.assertEqual(_confirmed_scissors_risk_ids([row(1), row(3)]), set())
        self.assertEqual(_confirmed_scissors_risk_ids([row(1), row(2, False)]), set())
        self.assertEqual(_confirmed_scissors_risk_ids([row(1), row(2)]), {1, 2})
        self.assertEqual(_confirmed_scissors_risk_ids([row(0, False, 'CVS已达成'), row(1), row(2)]), set())
        events = _ensure_required_event_nodes([], [row(1), row(2, False)], 'zh')
        self.assertFalse(any(e['type'] == 'risk' for e in events))
        events = _ensure_required_event_nodes([], [row(1), row(2)], 'zh')
        self.assertTrue(any(e['type'] == 'risk' for e in events))

    def test_capture_websocket_waits_for_consumption_and_then_sends_latest(self):
        class State:
            latest = (1, b'first')
            snapshots = 0
            def __init__(self, *args): pass
            def start(self): pass
            def release(self): pass
            def snapshot(self):
                State.snapshots += 1
                return State.latest
        app = FastAPI()
        app.include_router(video.router)
        app.dependency_overrides[video.get_db] = lambda: None
        with patch.object(video, 'get_video_session', return_value={'video_path': 'decklink://0'}), \
                patch.object(video, 'DisplayStreamState', State), patch.object(video, 'display_streams', {}):
            with TestClient(app) as client, client.websocket_connect('/api/video/ws-display/test?ack=true') as ws:
                self.assertEqual(ws.receive_bytes(), b'first')
                initial = State.snapshots
                State.latest = (2, b'stale')
                time.sleep(.05)
                self.assertEqual(State.snapshots, initial)
                State.latest = (3, b'latest')
                ws.send_text('ready')
                self.assertEqual(ws.receive_bytes(), b'latest')

    def test_llm_cannot_bypass_scissors_confirmation_by_changing_event_type(self):
        row = {'window_id': 1, 'start_time': 5, 'end_time': 10,
               'phase': 'CalotTriangleDissection', 'summary': '电凝钩分离组织',
               'scissors_verified': False}
        for kind, severity in [('risk', 'normal'), ('action', 'critical'), ('action', 'safety')]:
            events = _normalize_event_nodes([{'window_ids': [1], 'type': kind,
                'severity': severity, 'title': '剪刀操作需警惕', 'summary': '剪刀切断组织'}],
                [row], 'zh', 'test')
            self.assertEqual(events, [])


if __name__ == '__main__':
    unittest.main()
