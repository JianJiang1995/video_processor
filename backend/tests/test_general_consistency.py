import unittest

from backend.services.visibility_consistency import apply_visibility_review, validate_fog_transition
from backend.services.local_visual_prompt import normalize_local_review
from backend.routers.analysis import (
    _visibility_flags_from_visual, _visibility_summary_for_window,
    _strip_focused_visibility_conflicts, _triplet_operation_phrases,
    _build_deterministic_clinical_report,
)


class GeneralConsistencyTests(unittest.TestCase):
    def test_bag_disagreement_abstains_instead_of_forcing_gauze(self):
        from backend.services.local_visual_prompt import bag_observation_frames, validate_visibility_review
        from backend.routers.analysis import _specimen_bag_evidence
        for frames in ([3], [1, 2]):
            raw = {'summary': '抓钳夹持标本袋。', 'visual': {
                'observations': [{'instrument': '抓钳', 'action': '夹持', 'target': '标本袋',
                                  'target_evidence': '透明塑料袋口及折叠边', 'frames': frames}],
                'visibility_secondary_review': {'success': True, 'classification': 'intra_abdominal',
                                                 'confidence': .98, 'bag_geometry': 'woven_fabric'}}}
            self.assertFalse(bag_observation_frames(raw['visual'], 3))
            self.assertFalse(_specimen_bag_evidence(raw['summary'], raw['visual'], require_visual=True))
            checked = normalize_local_review(raw, 3)
            self.assertEqual(checked['summary'], '抓钳夹持局部材料。')
            self.assertNotIn('纱布', checked['summary'])
            contradictory = validate_visibility_review({'classification': 'specimen_bag_inside',
                'confidence': .95, 'bag_geometry': 'woven_fabric'}, 3, raw['visual'])
            self.assertEqual(contradictory['classification'], 'uncertain')

    def test_woven_bag_target_is_corrected_without_inventing_a_new_action(self):
        raw = {'summary': '抓钳夹住纱布和标本袋。', 'visual': {
            'observations': [{'instrument': '抓钳', 'action': '夹住', 'target': '标本袋',
                              'target_evidence': '白色网状，有蓝色缝线', 'frames': [2]}],
            'visibility_secondary_review': {'success': True, 'confidence': .95, 'bag_geometry': 'woven_fabric'}}}
        checked = normalize_local_review(raw, 3)
        self.assertEqual(checked['summary'], '抓钳夹住纱布。')
        self.assertEqual(checked['visual']['observations'][0]['target'], '纱布')
        self.assertEqual(checked['visual']['observations'][0]['unconfirmed_target'], '标本袋')
        self.assertEqual(raw['visual']['observations'][0]['target'], '标本袋')

    def test_retrieval_phase_cannot_invent_an_incision_removal_action(self):
        from backend.routers.analysis import _ensure_required_event_nodes
        row = {'window_id': 1, 'start_time': 5, 'end_time': 10, 'phase': 'GallbladderRetraction',
               'summary': '当前处于标本袋牵拉取出。电凝钩接触脂肪。', 'packaging_verified': False}
        event = next(e for e in _ensure_required_event_nodes([], [row], 'zh')
                     if e['id'] == 'event_required_retrieval_bag_retraction')
        self.assertEqual(event['type'], 'phase')
        self.assertNotIn('经切口', event['summary'])
        row.update(summary='抓钳牵拉标本袋。', packaging_verified=True)
        event = next(e for e in _ensure_required_event_nodes([], [row], 'zh')
                     if e['id'] == 'event_required_retrieval_bag_retraction')
        self.assertEqual(event['type'], 'action')
        self.assertEqual(event['summary'], row['summary'])
        self.assertNotIn('胆囊', event['summary'])

    def test_quality_review_preserves_stricter_confirmation_threshold(self):
        visual = {'visibility': {'status': 'uncertain', 'confidence': 0}}
        review = {'success': True, 'classification': 'external_body', 'confidence': .8}
        self.assertEqual(apply_visibility_review(visual, review, min_confidence=.9), visual)
        self.assertTrue(apply_visibility_review(visual, review)['visibility']['out_of_body'])

    def test_rejected_bag_geometry_does_not_discard_quality_disagreement(self):
        visual = {'visibility': {'status': 'foggy', 'fog': True, 'confidence': 1}}
        review = {'success': True, 'classification': 'uncertain', 'confidence': 0,
                  'proposed_classification': 'specimen_bag_inside', 'proposed_confidence': .85,
                  'rejected_reason': 'bag_requires_corroborating_visual_observations',
                  'image_quality': 'blurred'}
        result = apply_visibility_review(visual, review)
        self.assertEqual(result['visibility']['evidence_source'], 'disputed_image_quality')
        self.assertFalse(result['visibility']['fog'])
        self.assertFalse(result['visibility']['fog_cleared'])
        self.assertEqual(result['visibility_before_quality_dispute'], visual['visibility'])
        result['visibility_secondary_review'] = review
        text = _strip_focused_visibility_conflicts('袋内组织被包裹。视野有雾状遮挡。镜头起雾。', result)
        self.assertEqual(text, '袋内组织被包裹。')

    def test_only_credible_quality_disagreement_withholds_fog(self):
        from backend.services.visibility_consistency import fog_quality_disagreement
        visual = {'visibility': {'status': 'foggy', 'fog': True, 'confidence': .9}}
        review = {'success': True, 'classification': 'intra_abdominal', 'confidence': .9, 'image_quality': 'clear'}
        self.assertTrue(fog_quality_disagreement(visual, review))
        self.assertEqual(apply_visibility_review(visual, {**review, 'classification': 'foggy_inside'})['visibility']['status'], 'uncertain')
        for change in [{'confidence': .4}, {'confidence': float('nan')}, {'success': False},
                       {'image_quality': 'foggy'}, {'classification': 'external_body'}]:
            self.assertFalse(fog_quality_disagreement(visual, {**review, **change}))

    def test_equal_order_late_phases_also_require_sustained_evidence(self):
        from backend.routers.analysis import _stable_expert_phase
        vote = {'label': 'CleaningCoagulation', 'confidence': .9, 'vote_counts': {'CleaningCoagulation': 10}}
        def row(wid, candidate):
            return {'window_id': wid, 'surgical_phase': 'GallbladderPackaging',
                    'others': {'experts': {'phase': candidate}}}
        previous = [row(0, {'label': 'GallbladderPackaging'})]
        self.assertEqual(_stable_expert_phase(vote, previous, 1), 'GallbladderPackaging')
        self.assertEqual(_stable_expert_phase(vote, [row(0, vote), row(1, vote)], 2), 'CleaningCoagulation')

    def test_bright_surface_cannot_override_rejected_scene_evidence(self):
        from backend.routers.analysis import _fuse_packaging_scope_exit_evidence
        review = {'success': True, 'classification': 'uncertain', 'confidence': 0,
                  'proposed_classification': 'external_body', 'external_frame_indices': [0],
                  'rejected_reason': 'external_scene_requires_distinct_frame_evidence'}
        visual = {'visibility_secondary_review': review, 'visibility': {'status': 'uncertain', 'confidence': 0}}
        cue = {'out_of_body_candidate': True, 'inner_tissue': 0, 'annulus_bright': 1,
               'overall_bright': 1, 'low_sat_bright': 1, 'out_of_body_confidence': .99}
        result = _fuse_packaging_scope_exit_evidence(visual, cue, 'GallbladderPackaging')
        self.assertFalse(result['visibility']['out_of_body'])
        self.assertEqual(result['visibility']['status'], 'uncertain')

    def test_removed_summary_gets_no_invented_action_fallback(self):
        for summary in ['', '  ', '。；']:
            result = normalize_local_review({'summary': summary, 'visual': {'observations': []}}, 3)
            self.assertEqual(result['summary'], '当前画面为术野观察。')
            self.assertTrue(result['visual']['summary_empty_after_consistency_review'])
            self.assertEqual(result['visual']['observations'], [])

    def test_event_input_excludes_analysis_revision_not_surgical_phase(self):
        from backend.routers.analysis import _build_event_nodes_prompt
        records = [{'window_id': i, 'display_window': i + 1, 'start_time': i * 5,
                    'end_time': (i + 1) * 5, 'phase': 'GallbladderDissection',
                    'stage': stage, 'summary': '电凝钩分离组织。'} for i, stage in enumerate([2, 1])]
        prompt = _build_event_nodes_prompt(records, 'zh')
        self.assertNotIn('stage=', prompt)
        self.assertEqual(prompt.count('phase=GallbladderDissection'), 2)
        self.assertIn('are not surgical phases', prompt)
        self.assertIn('not equivalent to lens fog', prompt)
        self.assertEqual([r['stage'] for r in records], [2, 1])

    def test_local_fog_cue_only_requests_review_not_a_draft_warning(self):
        from unittest.mock import patch
        from backend.routers.analysis import _expert_snapshot_summary, _expert_pack_has_key_event_candidate
        pack = {'phase': {'label': 'GallbladderDissection'}, 'local_visibility': {'fog': True, 'confidence': .9}}
        with patch('backend.routers.analysis.get_summarization_provider', return_value='glm'):
            self.assertNotIn('起雾', _expert_snapshot_summary(pack, 0, 5, 12))
        self.assertTrue(_expert_pack_has_key_event_candidate(pack))
        self.assertTrue(pack['local_visibility']['fog'])

    def test_recent_exterior_abstains_without_claiming_the_current_scene(self):
        from backend.routers.analysis import _draft_scene_review_guard
        def row(wid, status):
            return {'window_id': wid, 'start_time': wid * 5, 'end_time': (wid + 1) * 5,
                    'others': {'stage': 2, 'visual_gpt': {'visibility': {
                        'status': status, 'out_of_body': status == 'out_of_body', 'confidence': .95}}}}
        previous = [row(1, 'out_of_body')]
        self.assertEqual(_draft_scene_review_guard(previous, 4, 20)['evidence_window_id'], 1)
        self.assertIsNone(_draft_scene_review_guard(previous, 20, 100))
        self.assertIsNone(_draft_scene_review_guard(previous + [row(2, 'clear')], 4, 20))
        self.assertIsNone(_draft_scene_review_guard(previous + [row(2, 'uncertain')], 4, 20))
        self.assertIsNone(_draft_scene_review_guard(previous, 1, 5))

    def test_plastic_bag_alias_and_target_geometry_reach_visual_review(self):
        from backend.services.local_visual_prompt import scene_observations_need_review, validate_visibility_review
        from backend.routers.analysis import _specimen_bag_evidence
        visual = {'observations': [{'instrument': '未知器械', 'appearance': '金属尖端',
            'action': '接触', 'target': '透明塑料袋', 'target_evidence': '袋状结构包裹组织', 'frames': [1, 2, 3]}]}
        self.assertTrue(scene_observations_need_review(visual))
        self.assertTrue(_specimen_bag_evidence('', visual, require_visual=True))
        review = {'classification': 'specimen_bag_inside', 'confidence': .95}
        self.assertEqual(validate_visibility_review(review, 3, visual)['classification'], 'specimen_bag_inside')
        visual['observations'][0]['frames'] = [1, 1, 99]
        rejected = validate_visibility_review(review, 3, visual)
        self.assertEqual(rejected['classification'], 'uncertain')
        self.assertEqual(rejected['proposed_confidence'], .95)

    def test_bag_alias_does_not_bypass_gauze_or_absent_geometry(self):
        from backend.services.local_visual_prompt import bag_observation_frames
        for appearance in ['白色网状纱布', '不是透明塑料袋', '疑似半透明袋口', '白色反光']:
            visual = {'observations': [{'target': '标本袋', 'appearance': appearance, 'frames': [1, 2]}]}
            self.assertEqual(bag_observation_frames(visual, 3), set())

    def test_focused_bag_geometry_can_correct_a_primary_tissue_label(self):
        from backend.services.local_visual_prompt import validate_visibility_review, scene_observations_need_review
        primary = {'observations': [{'target': '网膜', 'frames': [1, 2]}]}
        candidate = {'yolo': {'tools': [{'label': 'specimen_bag', 'frames_seen': 3}]}}
        self.assertTrue(scene_observations_need_review(primary, experts=candidate))
        review = {'classification': 'specimen_bag_inside', 'confidence': .95,
                  'bag_geometry': 'bag_mouth_and_film', 'bag_frame_indices': [0, 2]}
        self.assertEqual(validate_visibility_review(review, 3, primary)['classification'], 'specimen_bag_inside')
        for change in ({'bag_geometry': 'woven_fabric'}, {'bag_frame_indices': [2, 2, 9]}, {'confidence': .6}):
            self.assertEqual(validate_visibility_review({**review, **change}, 3, primary)['classification'], 'uncertain')

    def test_verified_room_description_survives_generic_scene_templates(self):
        visual = {'visibility': {'status': 'out_of_body', 'out_of_body': True, 'confidence': .95},
                  'visibility_secondary_review': {'success': True, 'classification': 'external_body',
                     'confidence': .95, 'external_evidence_type': 'room_furniture'}}
        result = normalize_local_review({'summary': '镜头移出体外，画面切换至套管口或腹壁外场景。',
                                        'visual': visual}, 3)
        self.assertEqual(result['summary'], '镜头移出体外。画面显示手术室设备。')

    def test_bag_detector_candidate_waits_for_visual_review(self):
        from backend.routers.analysis import _expert_snapshot_summary
        pack = {'phase': {'label': 'gallbladder_dissection'}, 'yolo': {'tools': [
            {'label': 'specimen_bag', 'frames_seen': 12}]}}
        self.assertNotIn('标本袋', _expert_snapshot_summary(pack, 0, 5, 12))
        self.assertEqual(pack['yolo']['tools'][0]['label'], 'specimen_bag')

    def test_detector_bag_prose_cannot_commit_a_phase_or_event(self):
        from backend.routers.analysis import (_apply_surgical_sequence_rules, _build_surgical_sequence_state,
            _normalize_summary_for_event_nodes, _ensure_required_event_nodes, _specimen_bag_evidence)
        prior = _build_surgical_sequence_state([{'window_id': 0, 'phase': 'GallbladderDissection',
                                                'summary': '电凝钩接触组织。'}])
        text, phase, rules = _apply_surgical_sequence_rules('当前处于胆囊取出与装袋。术野内可见标本袋。',
            'GallbladderPackaging', prior, preserve_anatomy=True)
        self.assertEqual(phase, 'GallbladderDissection')
        self.assertIn('packaging_requires_visual_bag_evidence', rules)
        record = _normalize_summary_for_event_nodes({'window_id': 1, 'phase': phase,
            'summary': text, 'others': {'stage': 1}})
        self.assertFalse(record['packaging_verified'])
        self.assertFalse(any(e['id'] == 'event_required_specimen_bagging'
                             for e in _ensure_required_event_nodes([], [record], 'zh')))
        self.assertTrue(_specimen_bag_evidence('抓钳夹持标本袋。', {'observations': [{
            'target': '标本袋', 'appearance': '半透明袋膜及袋口', 'frames': [1, 2]}]}, require_visual=True))
        self.assertFalse(_specimen_bag_evidence('抓钳夹持标本袋。', {'visibility_secondary_review': {
            'proposed_classification': 'specimen_bag_inside', 'classification': 'uncertain',
            'rejected_reason': 'bag_requires_corroborating_visual_observations'}}))

    def test_static_clip_evidence_is_not_a_new_placement_action(self):
        from backend.routers.analysis import _ensure_required_event_nodes
        events = _ensure_required_event_nodes([], [{'window_id': 0, 'start_time': 0, 'end_time': 5,
            'summary': '可见夹子留置在组织上。', 'phase': 'CalotTriangleDissection'}], 'zh')
        clip = next(event for event in events if event['id'] == 'event_required_clip_generic')
        self.assertEqual(clip['title'], '留置夹子')
        self.assertNotIn('放置', clip['summary'])

    def test_uncertain_review_cannot_retain_previous_exterior_claim(self):
        visual = {'scene': {'classification': 'external_body'},
                  'visibility': {'status': 'blurred', 'out_of_body': False}}
        review = {'success': True, 'classification': 'uncertain', 'image_quality': 'blurred',
                  'confidence': .95, 'external_frame_indices': []}
        visual['visibility_secondary_review'] = review
        checked = apply_visibility_review(visual, review)
        self.assertEqual(checked['visibility']['status'], 'blurred')
        self.assertFalse(checked['visibility']['out_of_body'])
        text = _strip_focused_visibility_conflicts('镜头移出体外。电凝钩接触胆囊。', checked)
        self.assertNotIn('体外', text)
        self.assertNotIn('电凝钩', text)
        self.assertIn('场景尚未确认', text)

    def test_gauze_phase_candidate_cannot_establish_bagging(self):
        from backend.routers.analysis import _build_surgical_sequence_state, _apply_surgical_sequence_rules
        previous = [{'window_id': 0, 'phase': 'GallbladderDissection', 'summary': '电凝钩分离粘连组织。'}]
        prior = _build_surgical_sequence_state(previous)
        gauze = '当前处于胆囊取出与装袋，抓钳夹持带蓝色显影线的网状纱布。'
        for preserve in (False, True):
            text, phase, rules = _apply_surgical_sequence_rules(gauze, 'GallbladderPackaging', prior,
                {'observations': [{'instrument': '抓钳', 'action': '夹持', 'target': '纱布'}]}, preserve_anatomy=preserve)
            self.assertEqual(phase, 'GallbladderDissection')
            self.assertIn('纱布', text)
            self.assertNotIn('装袋', text)
            self.assertIn('packaging_requires_visual_bag_evidence', rules)
        state = _build_surgical_sequence_state(previous + [
            {'window_id': 1, 'phase': 'GallbladderPackaging', 'summary': gauze}])
        self.assertFalse(state['packaging_seen'])
        self.assertNotIn('GallbladderPackaging', state['reached_phases'])

    def test_bag_evidence_is_not_a_phase_or_negative_mention(self):
        from backend.routers.analysis import _specimen_bag_evidence
        for text in ['当前处于胆囊取出与装袋。', '纱布有网格和蓝色显影线，不是标本袋。',
                     '未见标本袋，尚未装袋。', '疑似抓钳夹持标本袋。']:
            self.assertFalse(_specimen_bag_evidence(text), text)
        for text in ['抓钳夹持标本袋袋口。', '可见纱布。将胆囊装入标本袋。']:
            self.assertTrue(_specimen_bag_evidence(text), text)

    def test_bag_presence_cannot_generate_insertion_or_extraction(self):
        from backend.routers.analysis import _normalize_packaging_summary, _ensure_required_event_nodes
        visual = {'visibility_secondary_review': {'success': True, 'classification': 'specimen_bag_inside', 'confidence': .95}}
        text = _normalize_packaging_summary('抓钳接触局部组织。', visual)
        self.assertIn('可见标本袋', text)
        self.assertNotIn('装入', text)
        self.assertNotIn('取出', text)
        record = {'window_id': 1, 'start_time': 5, 'end_time': 10, 'phase': 'GallbladderPackaging',
                  'summary': '抓钳夹持标本袋袋口。', 'packaging_verified': True}
        events = _ensure_required_event_nodes([], [record], 'zh')
        bag = next(e for e in events if e['id'] == 'event_required_specimen_bagging')
        self.assertNotIn('将胆囊装入', bag['summary'])
        self.assertNotIn('准备取出', bag['summary'])

    def test_phase_only_bag_event_is_removed(self):
        from backend.routers.analysis import _ensure_required_event_nodes
        records = [{'window_id': 1, 'start_time': 5, 'end_time': 10, 'phase': 'GallbladderPackaging',
                    'summary': '抓钳夹持纱布。'}]
        events = [{'type': kind, 'title': '胆囊装袋取出', 'summary': '将胆囊装入标本袋。', 'window_ids': [1]}
                  for kind in ('phase', 'action')]
        self.assertFalse(any('袋' in e.get('title', '') for e in _ensure_required_event_nodes(events, records, 'zh')))

    def test_repeated_fog_report_range_includes_last_supported_window(self):
        from backend.routers.analysis import _visibility_status_events
        records = [{'window_id': 3, 'start_time': 15, 'end_time': 20, 'summary': '镜头起雾。'},
                   {'window_id': 7, 'start_time': 35, 'end_time': 40, 'summary': '镜头起雾。'}]
        event = _visibility_status_events(records, 'zh')[0]
        self.assertEqual((event['start_time'], event['end_time']), (15, 40))
        self.assertEqual(event['evidence_intervals'], [[15, 20], [35, 40]])

    def test_blur_cannot_become_fog_in_events_or_report(self):
        from backend.routers.analysis import _normalize_summary_for_event_nodes, _visibility_status_events, _merge_visibility_status_events, _fallback_event_nodes
        record = _normalize_summary_for_event_nodes({'window_id': 4, 'start_time': 20, 'end_time': 25,
            'summary': '当前视野模糊。', 'surgical_phase': 'Preparation',
            'others': {'stage': 2, 'visual_gpt': {'visibility': {'status': 'blurred', 'fog': False, 'confidence': .95}}}})
        self.assertEqual(record['visibility'], 'blurred')
        self.assertEqual(_visibility_status_events([record], 'zh'), [])
        self.assertFalse(any(event['type'] == 'visibility' for event in _fallback_event_nodes([record], 'zh')))
        self.assertEqual(_merge_visibility_status_events([
            {'type': 'visibility', 'title': '视野起雾', 'window_ids': [4]}], [record], 'zh'), [])
        self.assertEqual(_merge_visibility_status_events([
            {'type': 'visibility', 'title': '雾已去除', 'window_ids': [4]}], [record], 'zh'), [])

    def test_invisible_unknown_tip_cannot_assert_tissue_contact(self):
        result = normalize_local_review({'summary': '未知器械接触脂肪组织，同时接触肝脏表面。',
            'visual': {'observations': [{'instrument': '未知器械', 'appearance': '尖端不可见',
                'action': '接触', 'target': '脂肪组织', 'frames': [1, 2, 3]}]}}, 3)
        self.assertEqual(result['visual']['observations'], [])
        self.assertEqual(result['summary'], '当前画面为术野观察。')
        self.assertEqual(result['visual']['rejected_observations'][0]['rejection_reason'], 'no_visible_tool_action_evidence')

    def test_ambiguous_anatomy_is_not_forced_into_either_candidate(self):
        for target in ['脂肪或粘连组织', '胆囊管/胆囊动脉', '肝脏或者胆囊']:
            result = normalize_local_review({'summary': '抓钳牵拉' + target + '。', 'visual': {
                'observations': [{'instrument': '抓钳', 'action': '牵拉', 'target': target,
                    'frames': [1, 2], 'target_evidence': '连接关系尚不清楚'}]}}, 3)
            self.assertEqual(result['summary'], '抓钳牵拉局部组织。')
            self.assertEqual(result['visual']['observations'][0]['unconfirmed_target'], target)
            self.assertEqual(result['visual']['observations'][0]['target'], '局部组织')

    def test_blur_and_occlusion_cannot_be_normalized_into_fog(self):
        from backend.services.visibility_consistency import normalize_quality_flags
        for status in ['blurred', 'blocked', 'uncertain', 'out_of_body']:
            result = normalize_quality_flags({'status': status, 'fog': True, 'fog_cleared': True})
            self.assertFalse(result['fog'])
            self.assertFalse(result['fog_cleared'])
    def test_scene_conflict_cannot_assert_abdominal_action(self):
        review = {'success': True, 'classification': 'foggy_inside', 'confidence': .95}
        visual = {'scene': {'classification': 'external_body'},
                  'visibility': {'status': 'clear', 'confidence': .95},
                  'visibility_secondary_review': review}
        result = apply_visibility_review(visual, review)
        self.assertEqual(result['visibility']['evidence_source'], 'disputed_scene')
        self.assertNotIn('电凝钩', _strip_focused_visibility_conflicts('电凝钩接触皮肤。', result))
    def test_warmup_includes_configured_crop_shapes_without_source_names(self):
        from backend.services.expert_image_preparation import expert_warmup_sizes
        self.assertIn((1139, 960), expert_warmup_sizes({'analysis_input_crop_right': {'quad_right': .11}}))
        self.assertEqual(expert_warmup_sizes({'analysis_input_crop_right': {'full': float('nan')}}),
                         [(1280, 720), (1280, 960)])
    def test_inside_scene_does_not_establish_clear_quality(self):
        primary = {'visibility': {'status': 'foggy', 'fog': True, 'confidence': .9}}
        review = {'success': True, 'classification': 'intra_abdominal', 'confidence': .95}
        after = apply_visibility_review(primary, review)
        self.assertTrue(after['visibility']['fog'])
        self.assertEqual(after['visibility']['status'], 'foggy')
        self.assertEqual(primary['visibility']['confidence'], .9)
        review['image_quality'] = 'blurred'
        after = apply_visibility_review(primary, review)
        self.assertEqual(after['visibility']['status'], 'blurred')
        self.assertFalse(after['visibility']['fog_cleared'])

    def test_explicit_clear_quality_does_not_invent_a_recovery_event(self):
        review = {'success': True, 'classification': 'intra_abdominal', 'confidence': .95,
                  'image_quality': 'clear'}
        after = apply_visibility_review({'visibility': {'fog': True}}, review)
        self.assertFalse(after['visibility']['fog'])
        self.assertFalse(after['visibility']['fog_cleared'])
        self.assertEqual(_visibility_summary_for_window(after, []), '')

    def test_active_fog_and_exterior_cannot_simultaneously_be_fog_cleared(self):
        for payload in ({'status': 'foggy', 'fog': True}, {'status': 'out_of_body', 'out_of_body': True}):
            payload.update(confidence=.9, fog_cleared=True)
            flags = _visibility_flags_from_visual({'visibility': payload})
            self.assertFalse(flags['fog_resolved'])

    def test_fog_recovery_requires_ordered_indices_not_a_model_boolean(self):
        payload = {'status': 'clear', 'fog': False, 'fog_cleared': True}
        self.assertFalse(validate_fog_transition(payload, 3)['fog_cleared'])
        payload.update(fog_frame_indices=[1], clear_frame_indices=[2, 3])
        self.assertTrue(validate_fog_transition(payload, 3)['fog_cleared'])
        payload['fog_frame_indices'] = [3]
        self.assertFalse(validate_fog_transition(payload, 3)['fog_cleared'])

    def test_recovery_requires_two_confident_clear_windows(self):
        def row(wid, status, text):
            return {'window_id': wid, 'summary': text, 'others': {'visual_gpt': {
                'visibility': {'status': status, 'confidence': .95, 'fog': status == 'foggy'}}}}
        history = [row(0, 'foggy', '镜头起雾。')]
        clear = {'visibility': {'status': 'clear', 'confidence': .95}}
        self.assertEqual(_visibility_summary_for_window(clear, history, 1), '')
        history.append(row(1, 'clear', '视野内可见肝脏。'))
        self.assertIn('雾已去除', _visibility_summary_for_window(clear, history, 2))
        clear['visibility']['confidence'] = 0
        self.assertEqual(_visibility_summary_for_window(clear, history, 2), '')

    def test_natural_fog_wording_cannot_survive_conflicting_review(self):
        visual = {'visibility': {'status': 'foggy', 'fog': True, 'confidence': .9},
                  'visibility_secondary_review': {'success': True, 'confidence': .95,
                      'classification': 'intra_abdominal', 'image_quality': 'clear'}}
        text = _strip_focused_visibility_conflicts('抓钳牵拉胆囊。存在雾状遮挡。镜头起雾。雾已去除。腹腔视野恢复。', visual)
        self.assertEqual(text, '抓钳牵拉胆囊。')
        visual['visibility_secondary_review']['image_quality'] = 'foggy'
        text = _strip_focused_visibility_conflicts('抓钳牵拉胆囊。雾已去除。腹腔视野恢复。', visual)
        self.assertIn('镜头起雾', text)
        self.assertNotIn('已去除', text)

    def test_specific_draft_target_requires_noncompeting_head_evidence(self):
        data = {'triplet': [{'label': '[hook]-[dissect]-[cystic_duct]', 'confidence': .554}],
                'target': [{'label': 'cystic_duct', 'confidence': .697},
                           {'label': 'gallbladder', 'confidence': .558}]}
        self.assertEqual(_triplet_operation_phrases(data, draft=True), ['电凝钩分离局部组织'])
        data['triplet'][0]['confidence'] = .95
        data['target'] = [{'label': 'cystic_duct', 'confidence': .96}, {'label': 'gallbladder', 'confidence': .1}]
        self.assertEqual(_triplet_operation_phrases(data, draft=True), ['电凝钩分离胆囊管'])

    def test_empty_tool_veto_also_removes_omitted_subject_action(self):
        from backend.routers.analysis import _suppress_unsupported_tool_actions
        visual = {'observations': [], 'clip_secondary_review': {
            'success': True, 'confidence': .99, 'classification': 'no_clip',
            'visible_instruments': [], 'images': 3, 'image_selection': 'uniform_tool_inventory'}}
        experts = {'yolo': {'tools': []}}
        result = _suppress_unsupported_tool_actions(
            '未知器械接触脂肪组织；同时接触肝脏表面。肝脏表面血管纹理清晰。', visual, experts)
        self.assertEqual(result, '肝脏表面血管纹理清晰。')
        self.assertNotIn('接触', _suppress_unsupported_tool_actions('同时接触肝脏表面。', visual, experts))
        visual['clip_secondary_review'].update(classification='other',
            independent_from_instrument=True, motion='static', evidence_rejection='clip_not_independent_from_instrument')
        self.assertNotIn('接触', _suppress_unsupported_tool_actions('未知器械接触脂肪组织。', visual, experts))

    def test_tissue_names_are_not_globally_erased(self):
        for target, name in [('gallbladder', '胆囊'), ('liver', '肝脏'), ('omentum', '网膜')]:
            data = {'triplet': [{'label': f'[grasper]-[retract]-[{target}]', 'confidence': .9}]}
            self.assertEqual(_triplet_operation_phrases(data, draft=True), ['抓钳牵拉' + name])

    def test_specific_vlm_target_requires_multiframe_connection_evidence(self):
        raw = {'summary': '电凝钩分离胆囊管。', 'visual': {
            'observations': [{'target': '胆囊管', 'frames': [1], 'target_evidence': '黄色组织'}],
            'target_structure': {'label': 'cystic_duct', 'confidence': .95, 'evidence': '黄色组织'}}}
        result = normalize_local_review(raw, 3)
        self.assertNotIn('胆囊管', result['summary'])
        self.assertEqual(result['visual']['target_structure']['label'], 'unknown')
        raw['visual']['observations'][0].update(frames=[1, 2], target_evidence='连接胆囊颈的管状结构')
        self.assertIn('胆囊管', normalize_local_review(raw, 3)['summary'])

    def test_report_uses_observed_preparation_actions_and_does_not_invent_completion(self):
        records = [{'window_id': i, 'start_time': i * 5, 'end_time': (i + 1) * 5,
                    'phase': 'Preparation', 'summary': '电凝钩分离粘连组织。抓钳牵拉胆囊。'} for i in range(3)]
        report = _build_deterministic_clinical_report('arbitrary-source.mp4', records, [], 'zh')
        self.assertNotIn('完成初始暴露', report)
        self.assertNotIn('窗口记录未形成', report)
        self.assertIn('电凝钩分离粘连组织', report)
        self.assertIn('抓钳牵拉胆囊', report)
        self.assertIn('对应分析窗口共 0:15', report)
        self.assertNotIn('胆囊颈部', report)

    def test_report_does_not_merge_techniques_across_phase_boundaries(self):
        records = [{'window_id': i, 'start_time': i * 5, 'end_time': (i + 1) * 5,
                    'phase': phase, 'summary': '电凝钩分离粘连组织。'}
                   for i, phase in enumerate(['Preparation', 'CalotTriangleDissection'])]
        report = _build_deterministic_clinical_report('any.mp4', records, [], 'zh')
        self.assertNotIn('电凝钩解剖（0:00-0:10）', report)
        self.assertIn('0:00-0:05', report)
        self.assertIn('0:05-0:10', report)

    def test_report_does_not_count_unobserved_gaps_as_action_duration(self):
        records = [{'window_id': i, 'start_time': i * 5, 'end_time': (i + 1) * 5,
                    'phase': 'Preparation', 'summary': text}
                   for i, text in enumerate(['电凝钩分离粘连组织。', '术野观察。', '电凝钩分离粘连组织。'])]
        report = _build_deterministic_clinical_report('another.mp4', records, [], 'zh')
        self.assertIn('对应分析窗口共 0:10', report)
        self.assertNotIn('对应分析窗口共 0:15', report)

    def test_rejected_recovery_is_not_restored_from_free_text(self):
        visual = {'visibility': {'status': 'foggy', 'fog': True, 'fog_cleared': False}}
        text = _strip_focused_visibility_conflicts('镜头起雾。雾已去除。腹腔视野恢复。', visual)
        self.assertEqual(text, '镜头起雾。')

    def test_invalid_confidence_cannot_override_visibility(self):
        for value in ['bad', float('nan'), 5]:
            visual = {'visibility': {'status': 'foggy'}}
            self.assertEqual(apply_visibility_review(visual, {
                'success': True, 'confidence': value, 'classification': 'external_body'}), visual)

    def test_conflicting_quality_review_is_not_a_new_fog_alert(self):
        primary = {'visibility': {'status': 'clear', 'fog': False, 'confidence': .95}}
        review = {'success': True, 'confidence': .95, 'classification': 'intra_abdominal', 'image_quality': 'foggy'}
        result = apply_visibility_review(primary, review)
        self.assertEqual(result['visibility']['status'], 'uncertain')
        self.assertFalse(result['visibility']['fog'])
        self.assertEqual(apply_visibility_review(result, review), result)

    def test_negated_exterior_scene_evidence_does_not_trigger_review(self):
        from backend.services.local_visual_prompt import scene_observations_need_review
        visual = {'scene': {'classification': 'intra_abdominal', 'evidence': '可见腹膜，无体外皮肤或手术巾。'},
                  'visibility': {'status': 'clear'}, 'observations': [{'target': '腹膜'}]}
        self.assertFalse(scene_observations_need_review(visual))
        visual['scene']['evidence'] = '切口周围可见无菌巾，无腹腔组织。'
        self.assertTrue(scene_observations_need_review(visual))


if __name__ == '__main__':
    unittest.main()
