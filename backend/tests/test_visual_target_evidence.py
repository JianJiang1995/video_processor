import unittest
from backend.services.local_visual_prompt import validate_visibility_review
from backend.services.clip_review import (
    review_rejects_active_application, detector_instruments,
    needs_tool_inventory_review, confirmed_empty_tool_inventory,
)

from backend.routers.analysis import (
    _expert_snapshot_summary,
    _normalize_visual_target,
    _remove_negative_visual_checklist,
    _retain_reviewed_clip_fact,
    _strip_clipping_noise,
    _stable_expert_phase,
    _suppress_unsupported_tool_actions,
    _normalize_svo_wording,
    _strip_unverified_target_specific_clip_claims,
    _should_review_visibility_candidate,
    _visibility_flags_from_visual,
)


class VisualTargetEvidenceTests(unittest.TestCase):
    def test_unnamed_tool_action_still_requires_inventory_confirmation(self):
        visual = {'observations': [{'instrument': 'unknown', 'action': '接触', 'target': '脂肪'}]}
        experts = {'yolo': {'tools': []}}
        self.assertTrue(needs_tool_inventory_review(visual, experts))
        visual['clip_secondary_review'] = {'success': True, 'confidence': .95,
            'visible_instruments': [], 'classification': 'clip', 'motion': 'static',
            'independent_from_instrument': True, 'images': 3,
            'image_selection': 'uniform_tool_inventory'}
        self.assertTrue(confirmed_empty_tool_inventory(visual, experts))
        self.assertNotIn('未知器械', _suppress_unsupported_tool_actions('未知器械接触脂肪。', visual, experts))
        self.assertEqual(visual['observations'], [])
        self.assertNotIn('被接触', _suppress_unsupported_tool_actions('腹腔内脂肪组织被接触。', visual, experts))

    def test_weak_hit_requires_independent_full_window_review_not_automatic_veto(self):
        experts = {'yolo': {'tools': [{'label': 'grasper', 'frames_seen': 1}]}}
        visual = {'observations': [{'instrument': '抓钳', 'action': '牵拉'}]}
        self.assertTrue(needs_tool_inventory_review(visual, experts))
        self.assertFalse(confirmed_empty_tool_inventory(visual, experts))
        visual['clip_secondary_review'] = {'success': True, 'confidence': .95,
            'classification': 'no_clip', 'visible_instruments': [], 'images': 3,
            'image_selection': 'uniform_tool_inventory'}
        self.assertTrue(confirmed_empty_tool_inventory(visual, experts))
        visual['clip_secondary_review']['visible_instruments'] = ['grasper']
        self.assertFalse(confirmed_empty_tool_inventory(visual, experts))

    def test_missing_detector_or_invalid_counts_are_not_negative_evidence(self):
        for yolo in (None, {}, 'bad', {'tools': [{'label': 'grasper', 'frames_seen': 'bad'}]},
                     {'tools': [{'label': 'grasper', 'frames_seen': float('nan')}]},
                     {'tools': [{'label': 'grasper', 'frames_seen': True}]}):
            self.assertIsNone(detector_instruments({'yolo': yolo}))
        self.assertEqual(detector_instruments({'yolo': {'tools': [{'label': 'hook'}]}}), {'hook'})
        self.assertEqual(detector_instruments({'yolo': {'tools': []}}), set())

    def test_generic_expert_tissue_is_never_upgraded_to_cystic_identity(self):
        from backend.routers.analysis import _target_hint_from_triplet, _triplet_operation_phrases
        from backend.services.expert_fusion import _triplet_target_hint, _triplet_operation_phrases as expert_phrases
        triplet = {'target': [{'label': 'blood_vessel', 'confidence': .99}], 'triplet': [
            {'label': '[clipper]-[clip]-[blood_vessel]', 'confidence': .95},
            {'label': '[grasper]-[retract]-[liver]', 'confidence': .9},
        ]}
        self.assertEqual(_target_hint_from_triplet(triplet)['label'], 'unknown')
        self.assertEqual(_triplet_target_hint(triplet)['label'], 'unknown')
        for phrases in (_triplet_operation_phrases(triplet), expert_phrases(triplet)):
            self.assertIn('抓钳牵拉肝脏', ' '.join(phrases))
            self.assertNotIn('胆囊动脉', ' '.join(phrases))

    def test_blur_occlusion_and_external_haze_are_not_intra_abdominal_fog(self):
        for status in ('blurred', 'blocked'):
            self.assertFalse(_visibility_flags_from_visual({'visibility': {
                'status': status, 'fog': False, 'confidence': .9}})['fog_active'])
        flags = _visibility_flags_from_visual({'visibility': {
            'status': 'out_of_body', 'fog': True, 'confidence': .9}})
        self.assertFalse(flags['fog_active'])
        self.assertTrue(flags['out_of_body'])
        self.assertTrue(_visibility_flags_from_visual({'visibility': {
            'status': 'foggy', 'confidence': .9}})['fog_active'])

    def test_scene_observations_trigger_review_without_deciding_scene(self):
        visual = {'visibility': {'status': 'clear', 'confidence': .9},
                  'observations': [{'instrument': '器械', 'target': '皮肤切口'}]}
        self.assertTrue(_should_review_visibility_candidate(visual, {}))
        self.assertEqual(visual['visibility']['status'], 'clear')
        visual['observations'] = [{'instrument': '抓钳', 'action': '夹持标本袋'}]
        self.assertTrue(_should_review_visibility_candidate(visual, {}))
        visual['observations'] = [{'appearance': '透明塑料袋膜，有袋口结构'}]
        self.assertTrue(_should_review_visibility_candidate(visual, {}))
        visual['observations'] = [{'target': '胆囊颈部', 'action': '未见标本袋'}]
        self.assertFalse(_should_review_visibility_candidate(visual, {}))
        visual['observations'] = []
        self.assertTrue(_should_review_visibility_candidate(visual, {}))
        self.assertFalse(_should_review_visibility_candidate({'visibility': {'status': 'clear'}}, {}))
        self.assertTrue(_should_review_visibility_candidate({'visibility': {'status': 'foggy'}}, {}))

    def test_static_clip_review_vetoes_new_application_not_clip_presence(self):
        review = {'success': True, 'confidence': .95, 'classification': 'clip',
                  'visible_instruments': [], 'motion': 'static',
                  'independent_from_instrument': True, 'applier_active': False}
        visual = {'clip_secondary_review': review}
        self.assertTrue(review_rejects_active_application(visual))
        text = '施夹器正在夹闭目标组织。夹子留置在组织上。'
        self.assertEqual(_suppress_unsupported_tool_actions(text, visual, {}), '夹子留置在组织上。')
        review['visible_instruments'] = ['clip_applier']
        self.assertFalse(review_rejects_active_application(visual))
        self.assertEqual(_suppress_unsupported_tool_actions(text, visual, {}), text)
        review.pop('visible_instruments')
        self.assertFalse(review_rejects_active_application(visual))

    def test_local_wording_drops_incomplete_actions_without_inventing_material(self):
        text = '器械正在夹持并。紫色夹子留置在组织上。钛夹钳正在夹闭目标组织。'
        self.assertEqual(_suppress_unsupported_tool_actions(text, {}, {}),
                         '紫色夹子留置在组织上。施夹器正在夹闭目标组织。')
        complete = '抓钳夹持并牵拉组织。'
        self.assertEqual(_suppress_unsupported_tool_actions(complete, {}, {}), complete)

    def test_corrected_phase_rebuilds_actions_without_mutating_raw_expert(self):
        pack = {'phase': {'label': 'GallbladderRetraction', 'confidence': .99},
                'yolo': {'tools': []}, 'triplet': {}}
        original = _expert_snapshot_summary(pack, 0, 5, 12)
        corrected = _expert_snapshot_summary(
            {**pack, 'phase': {**pack['phase'], 'label': 'CalotTriangleDissection'}}, 0, 5, 12)
        self.assertIn('标本袋', original)
        self.assertNotIn('标本袋', corrected)
        self.assertNotIn('取出', corrected)
        self.assertEqual(pack['phase']['label'], 'GallbladderRetraction')

    def test_anatomical_neck_is_not_rewritten_as_phase_based_tissue(self):
        self.assertIn('胆囊颈部', _normalize_svo_wording('电凝钩分离胆囊颈部粘连组织。', 'CalotTriangleDissection'))

    def test_clear_applier_review_removes_unsupported_hook_but_keeps_coexistence(self):
        review = {'success': True, 'confidence': .95, 'classification': 'clip_applier',
                  'visible_instruments': ['clip_applier'], 'tip_geometry': 'broad_parallel_jaws', 'images': 3}
        visual = {'clip_secondary_review': review}
        text = '电凝钩在旁。施夹器正在夹闭目标组织。'
        self.assertEqual(_suppress_unsupported_tool_actions(text, visual, {'yolo': {'tools': []}}),
                         '施夹器正在夹闭目标组织。')
        self.assertIn('电凝钩', _suppress_unsupported_tool_actions(text, visual,
                      {'yolo': {'tools': [{'label': 'hook'}]}}))

    def test_no_tool_consensus_suppresses_hallucinated_hook_but_missing_inventory_does_not(self):
        visual = {'clip_secondary_review': {'success': True, 'confidence': .95,
                  'classification': 'no_clip', 'visible_instruments': [], 'images': 3}}
        text = '电凝钩在组织间分离。CVS安全视野确认中。'
        self.assertNotIn('电凝钩', _suppress_unsupported_tool_actions(text, visual, {'yolo': {'tools': []}}))
        self.assertEqual(_suppress_unsupported_tool_actions(text, visual, {}), text)
        self.assertEqual(_suppress_unsupported_tool_actions(text, visual, {'yolo': {'tools': [{'label': 'hook'}]}}), text)
        visual['clip_secondary_review'].pop('visible_instruments')
        self.assertEqual(_suppress_unsupported_tool_actions(text, visual, {'yolo': {'tools': []}}), text)

    def test_tool_rejection_removes_dangling_pronoun_action(self):
        visual = {'clip_secondary_review': {'success': True, 'confidence': .95,
                  'classification': 'no_clip', 'visible_instruments': [], 'images': 3}}
        result = _suppress_unsupported_tool_actions(
            '器械尖端不可辨。仅见其在组织表面移动。暴露区域为腹腔内组织。',
            visual, {'yolo': {'tools': []}})
        self.assertEqual(result, '暴露区域为腹腔内组织。')

    def test_no_tool_review_removes_structured_action_as_well_as_summary(self):
        from backend.services.clip_review import needs_tool_inventory_review
        experts = {'yolo': {'tools': []}}
        visual = {'observations': [
            {'instrument': '抓钳', 'action': '牵拉', 'target': '脂肪'},
            {'instrument': 'unknown', 'target': '腹膜'}],
            'clip_secondary_review': {'success': True, 'confidence': .95,
                'classification': 'no_clip', 'visible_instruments': [],
                'images': 3, 'image_selection': 'uniform_tool_inventory'}}
        self.assertTrue(needs_tool_inventory_review(visual, experts))
        text = _suppress_unsupported_tool_actions('抓钳牵拉脂肪组织。视野内可见腹膜。', visual, experts)
        self.assertEqual(text, '视野内可见腹膜。')
        self.assertEqual(visual['observations'], [{'instrument': 'unknown', 'target': '腹膜'}])
        self.assertEqual(visual['rejected_observations'][0]['rejection_reason'], 'confirmed_empty_tool_inventory')
        self.assertFalse(needs_tool_inventory_review(visual, experts))

    def test_sparse_or_clip_targeted_review_cannot_veto_whole_window_tools(self):
        from backend.services.clip_review import confirmed_empty_tool_inventory, needs_tool_inventory_review
        for count, selection in [(2, 'uniform_tool_inventory'), (3, 'clip_detector_frames')]:
            visual = {'clip_secondary_review': {'success': True, 'confidence': .99,
                'classification': 'no_clip', 'visible_instruments': [],
                'images': count, 'image_selection': selection}}
            self.assertFalse(confirmed_empty_tool_inventory(visual, {'yolo': {'tools': []}}))
        visual = {'observations': [{'instrument': '抓钳'}]}
        self.assertFalse(needs_tool_inventory_review(visual, {}))
        self.assertFalse(needs_tool_inventory_review(visual, {'yolo': {'tools': [{'label': 'grasper'}]}}))

    def test_isolated_phase_prediction_cannot_lock_irreversible_history(self):
        def row(wid, raw, confidence=.9):
            return {'window_id': wid, 'surgical_phase': 'CalotTriangleDissection',
                    'others': {'experts': {'phase': {'label': raw, 'confidence': confidence}}}}
        candidate = {'label': 'clipping_cutting', 'confidence': .9}
        self.assertEqual(_stable_expert_phase(candidate, [row(0, 'calot_triangle_dissection')], 1),
                         'CalotTriangleDissection')
        history = [row(0, 'clipping_cutting'), row(1, 'clipping_cutting')]
        self.assertEqual(_stable_expert_phase(candidate, history, 2), 'ClippingCutting')
        candidate['vote_counts'] = {'clipping_cutting': 4, 'calot_triangle_dissection': 8}
        self.assertEqual(_stable_expert_phase(candidate, history, 2), 'CalotTriangleDissection')
        candidate.pop('vote_counts')
        history[1]['others']['experts']['phase']['confidence'] = .54
        self.assertEqual(_stable_expert_phase(candidate, history, 2), 'CalotTriangleDissection')

    def test_startup_preparation_calot_tie_prefers_preparation_in_either_vote_order(self):
        for labels in [('calot_triangle_dissection', 'preparation'),
                       ('Preparation', 'CalotTriangleDissection')]:
            candidate = {'label': 'calot_triangle_dissection', 'confidence': .619,
                         'vote_counts': dict.fromkeys(labels, 6)}
            original = dict(candidate)
            self.assertEqual(_stable_expert_phase(candidate, [], 0), 'Preparation')
            self.assertEqual(candidate, original)

    def test_sustained_phase_uses_combined_votes_without_requiring_each_sixty_percent(self):
        def vote(n):
            return {'label': 'calot_triangle_dissection', 'confidence': .85,
                    'vote_counts': {'calot_triangle_dissection': n, 'preparation': 12 - n}}
        def rows(counts):
            return [{'window_id': i, 'surgical_phase': 'Preparation',
                     'others': {'experts': {'phase': vote(n)}}} for i, n in enumerate(counts)]
        self.assertEqual(_stable_expert_phase(vote(11), rows([6, 11]), 2), 'CalotTriangleDissection')
        self.assertEqual(_stable_expert_phase(vote(6), rows([6, 6]), 2), 'Preparation')
        self.assertEqual(_stable_expert_phase(vote(12), rows([5, 12]), 2), 'Preparation')

    def test_startup_tie_rule_does_not_force_clear_or_late_phases_to_preparation(self):
        for label, counts, expected in [
            ('calot_triangle_dissection', {'calot_triangle_dissection': 7, 'preparation': 5}, 'CalotTriangleDissection'),
            ('gallbladder_dissection', {'gallbladder_dissection': 12}, 'GallbladderDissection'),
            ('gallbladder_packaging', {'gallbladder_packaging': 8, 'preparation': 2, 'calot_triangle_dissection': 2}, 'GallbladderPackaging'),
            ('Unknown', {}, 'Unknown'),
        ]:
            with self.subTest(label=label):
                self.assertEqual(_stable_expert_phase({'label': label, 'vote_counts': counts}, [], 0), expected)

    def test_startup_tie_rule_does_not_reset_an_established_phase(self):
        candidate = {'label': 'calot_triangle_dissection', 'confidence': .7,
                     'vote_counts': {'calot_triangle_dissection': 6, 'preparation': 6}}
        history = [{'window_id': 0, 'surgical_phase': 'CalotTriangleDissection'}]
        self.assertEqual(_stable_expert_phase(candidate, history, 1), 'CalotTriangleDissection')

    def test_external_scene_needs_distinct_frames_and_explicit_evidence(self):
        review = {'classification': 'external_body', 'confidence': .99,
                  'external_evidence_type': 'none', 'external_frame_indices': [0, 1]}
        self.assertEqual(validate_visibility_review(review, 3)['classification'], 'uncertain')
        review['external_evidence_type'] = 'skin_external'
        review['external_frame_indices'] = [0, 0, 5]
        self.assertEqual(validate_visibility_review(review, 3)['classification'], 'uncertain')
        review['external_frame_indices'] = [0, 2]
        self.assertEqual(validate_visibility_review(review, 3)['classification'], 'external_body')

    def test_gauze_cannot_be_promoted_to_bag_by_scene_review_alone(self):
        review = {'classification': 'specimen_bag_inside', 'confidence': .98}
        visual = {'observations': [{'appearance': '白色网状纱布带蓝线', 'frames': [1, 2, 3]}]}
        self.assertEqual(validate_visibility_review(review, 3, visual)['classification'], 'uncertain')
        visual['observations'] = [{'appearance': '透明塑料袋膜，有袋口结构', 'frames': [1, 2]}]
        self.assertEqual(validate_visibility_review(review, 3, visual)['classification'], 'specimen_bag_inside')
        visual['observations'][0]['frames'] = [1, 1, 99]
        self.assertEqual(validate_visibility_review(review, 3, visual)['classification'], 'uncertain')

    def test_negative_clauses_never_turn_into_positive_findings(self):
        text = _remove_negative_visual_checklist(
            '电凝钩接触组织，未见夹子、剪刀或出血。未见标本袋或装袋动作。')
        self.assertEqual(text, '电凝钩接触组织')
        self.assertEqual(_remove_negative_visual_checklist('未见凝血处理；抓钳夹持组织'), '抓钳夹持组织')
        self.assertEqual(_remove_negative_visual_checklist('夹子已留在组织上。'), '夹子已留在组织上')
        self.assertNotIn('剪刀', _strip_clipping_noise('施夹器夹闭目标组织，未见剪刀切断。'))

    def test_phase_text_does_not_erase_confirmed_clip(self):
        visual = {'clip_secondary_review': {'success': True, 'confidence': .97,
                  'classification': 'clip', 'independent_from_instrument': True,
                  'clamped_on_tissue': True}}
        text = _retain_reviewed_clip_fact('当前处于清洁凝血。', visual)
        self.assertIn('夹子留置', text)
        self.assertNotIn('正在夹闭', text)
        visual['clip_secondary_review']['classification'] = 'electrocautery_hook'
        self.assertEqual(_retain_reviewed_clip_fact('电凝钩分离组织。', visual), '电凝钩分离组织。')

    def test_unknown_and_generic_anatomy_are_not_promoted(self):
        for label in ('unknown', '', 'blood_vessel', 'cystic_pedicle', 'cystic_duct_or_artery_uncertain'):
            target = _normalize_visual_target({'label': label, 'confidence': .99})
            self.assertEqual(target['label'], 'unknown')
            self.assertEqual(target['confidence'], 0)
        self.assertEqual(_normalize_visual_target(None)['label'], 'unknown')

    def test_specific_visual_evidence_is_preserved_without_prior_inflation(self):
        target = {'label': 'cystic_artery', 'confidence': .78, 'evidence': 'visual morphology'}
        self.assertEqual(_normalize_visual_target(target), target)
        self.assertEqual(_normalize_visual_target({'label': 'cystic_duct'})['confidence'], 0)

    def test_visual_unknown_overrides_even_strong_triplet_prior(self):
        visual = {'generic_clip': {'placed': True, 'confidence': .99},
                  'target_structure': _normalize_visual_target({'label': 'unknown'})}
        experts = {'triplet': {'target': [{'label': 'cystic_duct', 'confidence': .99}]}}
        text = _strip_unverified_target_specific_clip_claims('夹子已夹闭胆囊管。', visual, experts)
        self.assertNotIn('胆囊管', text)
        self.assertIn('夹子已夹闭目标组织', text)


if __name__ == '__main__':
    unittest.main()
