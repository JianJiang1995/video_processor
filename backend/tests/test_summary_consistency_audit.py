import unittest
from scripts.audit_summary_consistency import audit


class SummaryConsistencyAuditTests(unittest.TestCase):
    def test_bag_claim_conflicts_with_its_own_woven_target_evidence(self):
        row = {'window_id': 0, 'summary': '抓钳夹住纱布和标本袋。', 'others': {'visual_gpt': {
            'observations': [{'target': '标本袋', 'target_evidence': '白色网状，有蓝色缝线'}],
            'visibility_secondary_review': {'success': True, 'confidence': .95, 'bag_geometry': 'woven_fabric'}}}}
        self.assertEqual([item['kind'] for item in audit([row])['conflicts']], ['bag_claim_despite_woven_evidence'])

    def test_fog_claim_cannot_bypass_a_rejected_bag_quality_review(self):
        row = {'window_id': 0, 'summary': '镜头起雾。', 'others': {'visual_gpt': {
            'visibility': {'status': 'foggy', 'fog': True}, 'visibility_secondary_review': {
                'success': True, 'classification': 'uncertain', 'confidence': 0,
                'proposed_confidence': .85, 'image_quality': 'blurred',
                'rejected_reason': 'bag_requires_corroborating_visual_observations'}}}}
        self.assertEqual([item['kind'] for item in audit([row])['conflicts']], ['fog_conflicts_with_quality_review'])

    def test_empty_saved_summary_cannot_hide_behind_an_old_draft(self):
        row = {'window_id': 5, 'summary': '', 'glm_summary': '旧初稿'}
        self.assertEqual([item['kind'] for item in audit([row])['conflicts']], ['empty_final_summary'])

    def test_explicit_uncertain_review_rejects_retained_exterior_text(self):
        row = {'window_id': 0, 'summary': '镜头移出体外。', 'others': {'visual_gpt': {
            'visibility_secondary_review': {'success': True, 'classification': 'uncertain',
                'confidence': .95, 'image_quality': 'blurred'}}}}
        self.assertEqual([c['kind'] for c in audit([row])['conflicts']],
                         ['unconfirmed_scene_with_confirmed_scene_or_action'])

    def test_fog_and_empty_inventory_conflicts(self):
        row = {'window_id': 0, 'summary': '抓钳牵拉脂肪。雾已去除。', 'others': {
            'experts': {'yolo': {'tools': []}}, 'visual_gpt': {
                'visibility': {'status': 'foggy', 'fog': True},
                'clip_secondary_review': {'success': True, 'confidence': .95,
                    'classification': 'no_clip', 'visible_instruments': [], 'images': 3}}}}
        self.assertEqual({item['kind'] for item in audit([row])['conflicts']},
                         {'active_fog_and_recovery', 'tool_claim_after_empty_inventory_confirmation'})

    def test_raw_r1_disagreement_is_not_a_final_claim_failure(self):
        row = {'window_id': 1, 'summary': '腹膜覆盖于器官表面。', 'others': {
            'experts': {'yolo': {'tools': []}, 'surgr1': {
                'observations': [{'action': 'grasper retract gallbladder'}]}}}}
        result = audit([row])
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(result['raw_r1_with_empty_detector_windows'], [2])
