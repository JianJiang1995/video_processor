import json
import unittest

from backend.services.local_visual_prompt import build_local_visual_prompt, reviewed_scene_summary


class LocalPromptTests(unittest.TestCase):
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
