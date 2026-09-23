import json
import unittest

from backend.services.local_visual_prompt import build_local_visual_prompt, reviewed_scene_summary, normalize_local_review


class LocalPromptTests(unittest.TestCase):
    def test_frame_ids_are_not_source_seconds(self):
        prompt = build_local_visual_prompt({'surgr1': {'success': True, 'observations': [
            {'timestamp': 754.2, 'action': 'hook dissect adhesion'}]}}, frame_count=4)
        self.assertNotIn('754.2', prompt)
        self.assertEqual(json.loads(prompt.splitlines()[-1])['allowed_frame_indices'], [1, 2, 3, 4])

    def test_anatomy_consistency_does_not_promote_unverified_duct(self):
        raw = {'summary': 'grasper牵拉胆囊管。', 'visual': {
            'observations': [{'instrument': 'grasper', 'target': 'gallbladder', 'frames': [120, True, 1],
                              'target_evidence': '囊状'}],
            'target_structure': {'label': 'cystic_duct', 'confidence': 0, 'evidence': ''}}}
        result = normalize_local_review(raw, 4)
        self.assertEqual(result['summary'], '抓钳牵拉局部组织。')
        self.assertEqual(result['visual']['observations'][0]['frames'], [1])
        self.assertEqual(result['visual']['observations'][0]['target'], '胆囊')
        self.assertEqual(raw['visual']['observations'][0]['frames'], [120, True, 1])

    def test_supported_anatomy_survives_without_forced_binary_target(self):
        raw = {'summary': '抓钳牵拉胆囊。电凝钩分离胆囊管表面组织。', 'visual': {
            'observations': [{'target': '胆囊管表面组织', 'frames': [1, 3], 'target_evidence': '连接胆囊颈'}],
            'target_structure': {'label': 'unknown'}}}
        self.assertEqual(normalize_local_review(raw, 4)['summary'], raw['summary'])

    def test_tissues_survive_tool_conflict_without_inventing_an_action(self):
        prompt = build_local_visual_prompt({'yolo': {'tools': [{'label': 'hook'}]}, 'triplet': {
            'triplet': [{'label': '[grasper]-[retract]-[gallbladder]', 'confidence': .9}],
            'target': [{'label': 'gallbladder', 'confidence': .94, 'class_id': 0},
                       {'label': 'liver', 'confidence': .4}, {'label': 'null_target', 'confidence': .99},
                       {'label': 'cystic_duct', 'confidence': .01}],
        }})
        context = json.loads(prompt.splitlines()[-1])
        self.assertEqual(context['operation_candidates'], [])
        self.assertEqual([c['label'] for c in context['tissue_candidates']], ['gallbladder', 'liver'])
        self.assertIn('背景器官和器械实际接触对象', prompt)

    def test_surgr1_only_passes_completed_observations_not_reasoning(self):
        expert = {'surgr1': {'success': True, 'raw_results': 'PRIVATE REASONING',
                  'observations': [{'timestamp': 7.2, 'action': 'grasper retracts gallbladder'}]}}
        prompt = build_local_visual_prompt(expert)
        self.assertNotIn('PRIVATE REASONING', prompt)
        self.assertIn('grasper retracts gallbladder', prompt)
        expert['surgr1']['success'] = False
        self.assertNotIn('surgr1_observations', build_local_visual_prompt(expert))

    def test_scene_display_uses_accepted_type_not_raw_model_guesses(self):
        visual = {'visibility_secondary_review': {'classification': 'external_body',
                  'confidence': .9, 'external_evidence_type': 'room_furniture',
                  'evidence': 'Image 1: guessed brand and robot type'}}
        self.assertEqual(reviewed_scene_summary(visual), '镜头移出体外。画面显示手术室设备。')
        visual['visibility_secondary_review']['classification'] = 'trocar_transition'
        self.assertNotIn('移出体外', reviewed_scene_summary(visual))
        visual['visibility_secondary_review']['confidence'] = .3
        self.assertIsNone(reviewed_scene_summary(visual))

    def test_raw_candidates_not_previous_narrative_or_morphology_guess(self):
        prompt = build_local_visual_prompt({
            "text": "Do not reuse this previous narrative",
            "phase": {"label": "clipping_cutting", "confidence": .8, "per_frame": [1] * 100},
            "yolo": {"tools": [{"label": "clipper", "frames_seen": 8}]},
            "hemlok_clip": {"description": "Do not copy a heuristic description"},
            "open_vlm": {"summary": "Do not reuse this previous narrative"},
            "triplet": {"triplet": [{"label": "[clipper]-[clip]-[blood_vessel]", "confidence": .7},
                                    {"label": "zero_confidence_guess", "confidence": 0}]},
        }, "ClippingCutting")
        context = json.loads(prompt.splitlines()[-1])
        self.assertEqual(context["operation_candidates"][0]["label"], "[clipper]-[clip]-[blood_vessel]")
        self.assertEqual(context["tools_candidates"][0]["frames_seen"], 8)
        self.assertEqual(context["display_phase"], "ClippingCutting")
        for forbidden in ("zero_confidence_guess", "per_frame", "Do not reuse", "Do not copy"):
            self.assertNotIn(forbidden, prompt)
        self.assertIn("候选与图像冲突时以图像为准", prompt)
        self.assertIn("CVS状态仅填visual.cvs", prompt)

    def test_bounded_candidates_and_empty_context(self):
        prompt = build_local_visual_prompt({"triplet": {"triplet": [
            {"label": str(i), "confidence": i / 20} for i in range(20)
        ]}})
        candidates = json.loads(prompt.splitlines()[-1])["operation_candidates"]
        self.assertEqual(len(candidates), 4)
        self.assertEqual(candidates[0]["label"], "19")
        empty = json.loads(build_local_visual_prompt().splitlines()[-1])
        self.assertEqual(empty["operation_candidates"], [])
        self.assertEqual(empty["tools_candidates"], [])

    def test_conflicting_instrument_and_null_action_do_not_prime_summary(self):
        prompt = build_local_visual_prompt({
            "yolo": {"tools": [{"label": "grasper", "frames_seen": 8}]},
            "triplet": {"triplet": [
                {"label": "[bipolar]-[grasp]-[liver]", "confidence": .99},
                {"label": "[grasper]-[null_verb]-[null_target]", "confidence": .99},
                {"label": "[grasper]-[retract]-[gallbladder]", "confidence": .7},
            ]},
        })
        candidates = json.loads(prompt.splitlines()[-1])["operation_candidates"]
        self.assertEqual([item["label"] for item in candidates], ["[grasper]-[retract]-[gallbladder]"])
