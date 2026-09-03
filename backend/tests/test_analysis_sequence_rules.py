import unittest

from backend.routers.analysis import (
    _apply_surgical_sequence_rules,
    _bounded_window_end,
    _bipolar_forceps_evidence,
    _build_deterministic_clinical_report,
    _build_surgical_sequence_state,
    _compact_local_summary_text,
    _compact_clinical_summary_records,
    _normalize_retraction_summary,
    _expert_snapshot_summary,
    _expand_vague_operation_language,
    _focused_clip_action_confirmed,
    _resolve_clip_applier_scissors_conflict,
    _resolve_bipolar_hook_conflict,
    _select_key_event_nodes,
    _should_review_visibility_candidate,
    _strip_nonprogress_idle_applier_claim,
    _strip_focused_scissors_instrument_conflicts,
    _strip_focused_visibility_conflicts,
    _strip_visual_rejected_clip_claims,
    _strip_visual_rejected_scissors_claims,
    _strip_unsupported_stapler_wording,
    _strip_unverified_target_specific_clip_claims,
)
from backend.services.expert_fusion import _detect_blue_bipolar_forceps
import numpy as np


class AnalysisSequenceRulesTest(unittest.TestCase):
    def test_focused_active_applier_can_start_clipping_before_phase_model(self):
        visual = {
            "clip_secondary_review": {
                "success": True,
                "classification": "clip_applier",
                "confidence": 0.99,
                "applier_active": True,
                "clamped_on_tissue": True,
            },
            "clip_applier": {"visible": True, "active": True, "confidence": 0.99},
        }
        self.assertTrue(_focused_clip_action_confirmed(visual))
        prior = {
            "cvs_achieved": False,
            "clipped": set(),
            "cut": set(),
            "reached_phases": {"Preparation", "CalotTriangleDissection"},
            "last_phase": "CalotTriangleDissection",
            "max_phase_order": 1,
            "packaging_seen": False,
            "post_retrieval_review": False,
            "formal_started": True,
        }
        summary, phase, rules = _apply_surgical_sequence_rules(
            "当前处于肝胆三角解剖，钛夹钳正在夹闭胆囊管。",
            "CalotTriangleDissection",
            prior,
            visual,
        )
        self.assertEqual(phase, "ClippingCutting")
        self.assertIn("钛夹钳正在夹闭胆囊管", summary)
        self.assertIn("focused_clip_starts_clipping_phase", rules)

    def test_idle_applier_does_not_override_calot_phase(self):
        visual = {
            "clip_secondary_review": {
                "success": True,
                "classification": "clip_applier",
                "confidence": 0.99,
                "applier_active": False,
                "clamped_on_tissue": False,
            }
        }
        self.assertFalse(_focused_clip_action_confirmed(visual))

    def test_phase_does_not_regress_after_clipping_started(self):
        prior = {
            "cvs_achieved": False,
            "clipped": {"cystic_duct"},
            "cut": set(),
            "reached_phases": {"Preparation", "CalotTriangleDissection", "ClippingCutting"},
            "last_phase": "ClippingCutting",
            "max_phase_order": 2,
            "packaging_seen": False,
            "post_retrieval_review": False,
            "formal_started": True,
        }
        summary, phase, rules = _apply_surgical_sequence_rules(
            "当前处于肝胆三角解剖，抓钳牵拉胆囊颈以暴露操作区。CVS安全视野确认中。",
            "CalotTriangleDissection",
            prior,
        )
        self.assertEqual(phase, "ClippingCutting")
        self.assertIn("当前处于夹闭切断", summary)
        self.assertIn("CVS处于夹闭前后安全核查中", summary)
        self.assertIn("phase_no_backward_regression", rules)

    def test_sequence_state_ignores_saved_phase_regression(self):
        state = _build_surgical_sequence_state([
            {"window_id": 0, "phase": "CalotTriangleDissection", "summary": "肝胆三角解剖。"},
            {"window_id": 1, "phase": "ClippingCutting", "summary": "夹子夹闭胆囊管。"},
            {"window_id": 2, "phase": "CalotTriangleDissection", "summary": "抓钳牵拉胆囊颈。"},
        ])
        self.assertEqual(state["last_phase"], "ClippingCutting")
        self.assertEqual(state["max_phase_order"], 2)

    def test_sequence_state_ignores_unconfirmed_retrieval_prose(self):
        state = _build_surgical_sequence_state([
            {"window_id": 0, "phase": "CalotTriangleDissection", "summary": "肝胆三角解剖。"},
            {"window_id": 1, "phase": "ClippingCutting", "summary": "夹子夹闭胆囊管。"},
            {"window_id": 2, "phase": "GallbladderDissection", "summary": "分离胆囊床。"},
            {
                "window_id": 3,
                "phase": "CleaningCoagulation",
                "summary": "胆囊装袋取出后，镜头重新进入腹腔，进行术野复查。",
            },
        ])
        self.assertFalse(state["packaging_seen"])
        self.assertFalse(state["post_retrieval_review"])

    def test_sequence_state_distinguishes_reentry_from_completed_retrieval(self):
        base_rows = [
            {"window_id": 0, "phase": "CalotTriangleDissection", "summary": "肝胆三角解剖。"},
            {"window_id": 1, "phase": "ClippingCutting", "summary": "夹子夹闭胆囊管。"},
            {"window_id": 2, "phase": "GallbladderDissection", "summary": "分离胆囊床。"},
            {
                "window_id": 3,
                "phase": "GallbladderPackaging",
                "summary": "将胆囊装入标本袋并准备取出。",
            },
        ]
        before_retraction = _build_surgical_sequence_state(base_rows + [{
            "window_id": 4,
            "phase": "CleaningCoagulation",
            "summary": "镜头重新进入腹腔，进行术野复查。",
        }])
        self.assertTrue(before_retraction["packaging_seen"])
        self.assertTrue(before_retraction["post_packaging_reentry"])
        self.assertFalse(before_retraction["post_retrieval_review"])

        after_retraction = _build_surgical_sequence_state(base_rows + [
            {
                "window_id": 4,
                "phase": "GallbladderRetraction",
                "summary": "牵拉装有胆囊的标本袋经切口取出。",
            },
            {
                "window_id": 5,
                "phase": "CleaningCoagulation",
                "summary": "镜头重新进入腹腔，进行术野复查。",
            },
        ])
        self.assertTrue(after_retraction["post_retrieval_review"])

    def test_post_packaging_review_does_not_claim_retrieval_too_early(self):
        prior = {
            "cvs_achieved": False,
            "clipped": {"cystic_duct"},
            "cut": set(),
            "reached_phases": {
                "CalotTriangleDissection",
                "ClippingCutting",
                "GallbladderDissection",
                "GallbladderPackaging",
            },
            "last_phase": "GallbladderPackaging",
            "max_phase_order": 4,
            "packaging_seen": True,
            "post_packaging_reentry": False,
            "post_retrieval_review": False,
            "formal_started": True,
        }
        summary, phase, _ = _apply_surgical_sequence_rules(
            "胆囊装袋取出后，镜头重新进入腹腔，进行术野复查。",
            "CleaningCoagulation",
            prior,
        )
        self.assertEqual(phase, "CleaningCoagulation")
        self.assertNotIn("取出后", summary)
        self.assertIn("镜头重新进入腹腔", summary)

        prior["post_packaging_reentry"] = True
        repeated, _, _ = _apply_surgical_sequence_rules(
            "胆囊装袋取出后，镜头重新进入腹腔，进行术野复查。",
            "CleaningCoagulation",
            prior,
        )
        self.assertNotIn("取出后", repeated)
        self.assertNotIn("镜头重新进入腹腔", repeated)
        self.assertIn("清理胆囊床并复查术野", repeated)

        prior["reached_phases"].add("GallbladderRetraction")
        prior["post_retrieval_review"] = False
        after_retraction, _, _ = _apply_surgical_sequence_rules(
            "胆囊装袋取出后，镜头重新进入腹腔，进行术野复查。",
            "CleaningCoagulation",
            prior,
        )
        self.assertIn("胆囊装袋取出后", after_retraction)

    def test_focused_specimen_bag_starts_packaging(self):
        prior = {
            "cvs_achieved": False,
            "clipped": {"cystic_duct"},
            "cut": set(),
            "reached_phases": {
                "CalotTriangleDissection",
                "ClippingCutting",
                "GallbladderDissection",
                "CleaningCoagulation",
            },
            "last_phase": "CleaningCoagulation",
            "max_phase_order": 4,
            "packaging_seen": False,
            "post_retrieval_review": False,
            "formal_started": True,
        }
        visual = {
            "visibility_secondary_review": {
                "success": True,
                "classification": "specimen_bag_inside",
                "confidence": 0.99,
            }
        }
        summary, phase, rules = _apply_surgical_sequence_rules(
            "当前处于清洁凝血，双极电凝钳夹持局部组织。",
            "CleaningCoagulation",
            prior,
            visual,
        )
        self.assertEqual(phase, "GallbladderPackaging")
        self.assertIn("将胆囊装入标本袋", summary)
        self.assertIn("focused_bag_starts_packaging_phase", rules)

    def test_prepackaging_cleaning_does_not_claim_active_bleeding(self):
        prior = {
            "cvs_achieved": False,
            "clipped": {"cystic_duct"},
            "cut": set(),
            "reached_phases": {
                "CalotTriangleDissection",
                "ClippingCutting",
                "GallbladderDissection",
                "CleaningCoagulation",
            },
            "last_phase": "CleaningCoagulation",
            "max_phase_order": 4,
            "packaging_seen": False,
            "post_retrieval_review": False,
            "formal_started": True,
        }
        summary, phase, rules = _apply_surgical_sequence_rules(
            "胆囊装袋取出后，镜头重新进入腹腔，进行术野复查。",
            "CleaningCoagulation",
            prior,
        )
        self.assertEqual(phase, "CleaningCoagulation")
        self.assertIn("清理胆囊床并复查术野", summary)
        self.assertNotIn("活动性出血", summary)
        self.assertIn("pre_packaging_cleaning_suppresses_retrieval_language", rules)

        summary, phase, rules = _apply_surgical_sequence_rules(
            "当前处于清洁凝血，钛夹钳夹闭胆囊管。抓钳牵拉胆囊颈部并抬起胆囊体，"
            "以扩大肝胆三角及待处理结构暴露。",
            "CleaningCoagulation",
            prior,
        )
        self.assertEqual(phase, "CleaningCoagulation")
        self.assertIn("清理胆囊床并复查术野", summary)
        self.assertNotIn("夹闭胆囊管", summary)
        self.assertNotIn("肝胆三角", summary)
        self.assertIn("pre_packaging_suppresses_stale_calot_and_clip", rules)

    def test_gallbladder_dissection_removes_calot_context(self):
        prior = {
            "cvs_achieved": False,
            "clipped": {"cystic_duct"},
            "cut": set(),
            "reached_phases": {
                "CalotTriangleDissection",
                "ClippingCutting",
                "GallbladderDissection",
            },
            "last_phase": "GallbladderDissection",
            "max_phase_order": 3,
            "packaging_seen": False,
            "post_retrieval_review": False,
            "formal_started": True,
        }
        summary, phase, rules = _apply_surgical_sequence_rules(
            "当前处于胆囊分离，电凝钩沿肝胆三角解剖层次分离纤维脂肪组织，"
            "以逐步扩大关键结构暴露。CVS安全视野确认中。",
            "GallbladderDissection",
            prior,
        )
        self.assertEqual(phase, "GallbladderDissection")
        self.assertIn("胆囊壁与肝床间隙", summary)
        self.assertNotIn("肝胆三角", summary)
        self.assertNotIn("CVS", summary)
        self.assertIn("gallbladder_dissection_suppresses_calot_context", rules)

    def test_gallbladder_dissection_drops_active_clipping(self):
        prior = {
            "cvs_achieved": False,
            "clipped": set(),
            "cut": set(),
            "reached_phases": {
                "CalotTriangleDissection",
                "ClippingCutting",
                "GallbladderDissection",
            },
            "last_phase": "GallbladderDissection",
            "max_phase_order": 3,
            "packaging_seen": False,
            "post_retrieval_review": False,
            "formal_started": True,
        }
        summary, phase, rules = _apply_surgical_sequence_rules(
            "当前处于胆囊分离，钛夹钳夹闭胆囊管。",
            "GallbladderDissection",
            prior,
        )
        self.assertEqual(phase, "GallbladderDissection")
        self.assertIn("胆囊床", summary)
        self.assertNotIn("夹闭胆囊管", summary)
        self.assertIn("gallbladder_dissection_drops_active_clipping", rules)

    def test_active_applier_removes_conflicting_bipolar_wording(self):
        summary = _strip_focused_scissors_instrument_conflicts(
            "当前处于夹闭切断，双极电凝钳反复开合夹持并分离组织，"
            "抓钳牵拉胆囊颈。钛夹钳正在夹闭胆囊动脉。",
            {
                "clip_secondary_review": {
                    "success": True,
                    "classification": "clip_applier",
                    "confidence": 0.99,
                    "applier_active": True,
                }
            },
        )
        self.assertNotIn("双极电凝钳", summary)
        self.assertIn("抓钳牵拉胆囊颈", summary)
        self.assertIn("钛夹钳正在夹闭胆囊动脉", summary)

    def test_scissors_morphology_removes_conflicting_hook_wording(self):
        summary = _strip_focused_scissors_instrument_conflicts(
            "当前处于夹闭切断，电凝钩分离局部组织。剪刀在操作区域内活动。",
            {
                "clip_secondary_review": {
                    "success": True,
                    "classification": "scissors",
                    "confidence": 0.99,
                }
            },
        )
        self.assertNotIn("电凝钩", summary)
        self.assertIn("剪刀在操作区域内活动", summary)

    def test_blue_bipolar_forceps_overrides_hook_false_positive(self):
        experts = {
            "phase": {"label": "calot_triangle_dissection"},
            "yolo": {"tools": [
                {"label": "bipolar", "frames_seen": 11},
                {"label": "hook", "frames_seen": 8},
                {"label": "grasper", "frames_seen": 7},
            ]},
            "triplet": {
                "instrument": [
                    {"label": "clipper", "confidence": 0.996},
                    {"label": "bipolar", "confidence": 0.994},
                    {"label": "hook", "confidence": 0.001},
                ],
                "triplet": [
                    {"label": "[bipolar]-[coagulate]-[liver]", "confidence": 0.999},
                    {"label": "[bipolar]-[retract]-[liver]", "confidence": 0.991},
                ],
            },
            "blue_bipolar_forceps": {
                "detected": True,
                "confidence": 0.93,
                "frames_seen": 11,
                "frames_analyzed": 12,
            },
        }
        self.assertTrue(_bipolar_forceps_evidence(experts)["resolved"])
        summary = _expert_snapshot_summary(experts, 330.0, 335.0, 12)
        self.assertIn("双极电凝钳反复开合夹持并分离肝胆三角内纤维脂肪组织", summary)
        self.assertNotIn("电凝钩", summary)

    def test_blue_jaw_cue_rejects_white_hook_shape(self):
        blue = np.zeros((240, 420, 3), dtype=np.uint8)
        blue[:, :] = (35, 45, 110)
        blue[80:100, 210:350] = (255, 120, 70)
        blue[125:145, 210:350] = (255, 120, 70)
        white = np.zeros((240, 420, 3), dtype=np.uint8)
        white[:, :] = (35, 45, 110)
        white[95:115, 210:350] = (230, 230, 230)
        self.assertTrue(_detect_blue_bipolar_forceps([blue] * 6)["detected"])
        self.assertFalse(_detect_blue_bipolar_forceps([white] * 6)["detected"])

    def test_true_hook_is_not_renamed_without_bipolar_evidence(self):
        experts = {
            "yolo": {"tools": [{"label": "hook", "frames_seen": 12}]},
            "triplet": {
                "instrument": [{"label": "bipolar", "confidence": 0.05}],
                "triplet": [{"label": "[bipolar]-[coagulate]-[liver]", "confidence": 0.08}],
            },
        }
        self.assertFalse(_bipolar_forceps_evidence(experts)["resolved"])
        original = "当前处于肝胆三角解剖，电凝钩分离肝胆三角组织。"
        self.assertEqual(_resolve_bipolar_hook_conflict(original, experts), original)

    def test_vague_hook_dissection_is_expanded_without_guessing_target_structure(self):
        summary = _expand_vague_operation_language(
            "当前处于肝胆三角解剖，电凝钩分离肝胆三角组织。CVS安全视野确认中。",
            "CalotTriangleDissection",
        )
        self.assertIn("沿肝胆三角解剖层次分离纤维脂肪组织", summary)
        self.assertIn("扩大关键结构暴露", summary)
        self.assertNotIn("胆囊管", summary)
        self.assertNotIn("胆囊动脉", summary)

    def test_vague_gallbladder_bed_observation_is_made_clinically_specific(self):
        summary = _expand_vague_operation_language(
            "当前处于胆囊分离，画面以胆囊床分离为主，重点关注组织层面和止血情况。",
            "GallbladderDissection",
        )
        self.assertIn("胆囊壁与肝床间隙", summary)
        self.assertIn("活动性出血", summary)
        self.assertNotIn("重点关注", summary)

    def test_empty_clipping_window_describes_the_safety_check(self):
        summary = _expand_vague_operation_language(
            "当前处于夹闭切断。",
            "ClippingCutting",
        )
        self.assertIn("核查夹体位置", summary)
        self.assertNotIn("当前处于", summary)

    def test_generic_hook_fallback_is_removed_after_packaging(self):
        summary = _expand_vague_operation_language(
            "当前处于胆囊取出与装袋，将胆囊装入标本袋并准备取出。电凝钩分离局部纤维组织并扩大操作间隙。",
            "GallbladderPackaging",
        )
        self.assertIn("胆囊装入标本袋", summary)
        self.assertNotIn("电凝钩", summary)
        self.assertNotIn("并扩大操作间隙", summary)

    def test_irrigation_summary_is_canonical_and_not_duplicated(self):
        summary = _expand_vague_operation_language(
            "当前处于胆囊分离，使用冲吸器清理术野内液体，以恢复局部观察。",
            "GallbladderDissection",
        )
        self.assertIn("冲吸器清理术野内液体和组织碎屑", summary)
        self.assertEqual(summary.count("恢复局部观察"), 1)

    def test_gallbladder_bed_expansion_removes_residual_duplicate_object(self):
        summary = _expand_vague_operation_language(
            "当前处于胆囊分离，电凝钩沿胆囊壁与肝床间隙分离粘连组织，逐步扩大剥离范围与胆囊床粘连组织。",
            "GallbladderDissection",
        )
        self.assertIn("逐步扩大胆囊床剥离范围", summary)
        self.assertNotIn("剥离范围与", summary)

    def test_specimen_bag_review_removes_false_fog(self):
        summary = _strip_focused_visibility_conflicts(
            "当前处于胆囊取出与装袋，将胆囊装入标本袋并准备取出。镜头起雾，手术视野受遮挡。",
            {
                "visibility_secondary_review": {
                    "success": True,
                    "classification": "specimen_bag_inside",
                    "confidence": 0.99,
                }
            },
        )
        self.assertIn("胆囊装入标本袋", summary)
        self.assertNotIn("起雾", summary)

    def test_external_review_overrides_intra_abdominal_action(self):
        summary = _strip_focused_visibility_conflicts(
            "当前处于肝胆三角解剖，双极电凝牵拉胆囊。",
            {
                "visibility_secondary_review": {
                    "success": True,
                    "classification": "external_body",
                    "confidence": 0.99,
                }
            },
        )
        self.assertEqual(summary, "镜头移出体外，画面切换至套管口或腹壁外场景。")

    def test_scope_exit_candidate_is_reviewed_before_packaging(self):
        visual = {
            "visibility": {
                "status": "foggy",
                "fog": True,
                "out_of_body": False,
                "confidence": 0.95,
            }
        }
        local_cue = {
            "out_of_body_candidate": True,
            "out_of_body_confidence": 0.64,
            "inner_tissue": 0.214,
            "annulus_bright": 0.526,
            "overall_bright": 0.426,
        }
        self.assertTrue(_should_review_visibility_candidate(visual, local_cue))

    def test_idle_applier_is_hidden_after_clipping_phase(self):
        summary = _strip_nonprogress_idle_applier_claim(
            "当前处于胆囊分离，电凝钩分离胆囊床组织。钛夹钳在操作区域内调整。",
            "GallbladderDissection",
        )
        self.assertIn("电凝钩分离胆囊床组织", summary)
        self.assertNotIn("钛夹钳", summary)

    def test_idle_applier_is_kept_during_clipping_phase(self):
        summary = _strip_nonprogress_idle_applier_claim(
            "当前处于夹闭切断。钛夹钳在操作区域内调整。",
            "ClippingCutting",
        )
        self.assertIn("钛夹钳在操作区域内调整", summary)

    def test_confirmed_clip_with_weak_target_uses_generic_anatomy(self):
        visual = {
            "generic_clip": {"placed": True, "confidence": 0.99},
            "target_structure": {"label": "cystic_artery", "confidence": 0.0},
            "clip_secondary_review": {
                "success": True,
                "classification": "clip",
                "confidence": 0.99,
                "independent_from_instrument": True,
                "clamped_on_tissue": True,
            },
        }
        experts = {
            "triplet": {
                "target": [{"label": "cystic_artery", "confidence": 0.009}]
            }
        }
        summary = _strip_unverified_target_specific_clip_claims(
            "当前处于夹闭切断，钛夹钳夹闭胆囊动脉。",
            visual,
            experts,
        )
        self.assertIn("夹子已夹闭目标组织", summary)
        self.assertNotIn("胆囊动脉", summary)

    def test_confirmed_clip_keeps_high_confidence_triplet_target(self):
        visual = {
            "generic_clip": {"placed": True, "confidence": 0.99},
            "target_structure": {"label": "cystic_artery", "confidence": 0.0},
        }
        experts = {
            "triplet": {
                "target": [{"label": "cystic_artery", "confidence": 0.82}]
            }
        }
        summary = _strip_unverified_target_specific_clip_claims(
            "当前处于夹闭切断，钛夹钳夹闭胆囊动脉。",
            visual,
            experts,
        )
        self.assertIn("钛夹钳夹闭胆囊动脉", summary)

    def test_clinical_report_keeps_complete_phase_timeline(self):
        phases = [
            "Preparation",
            "CalotTriangleDissection",
            "ClippingCutting",
            "GallbladderDissection",
            "GallbladderPackaging",
            "CleaningCoagulation",
            "GallbladderRetraction",
        ]
        records = [
            {
                "window_id": index,
                "start_time": index * 60,
                "end_time": (index + 1) * 60,
                "phase": phase,
                "visibility": "clear",
                "summary": "阶段摘要",
            }
            for index, phase in enumerate(phases)
        ]
        report = _build_deterministic_clinical_report(
            "test.mp4", records, [], "zh"
        )
        self.assertIn("0:00-1:00：准备阶段", report)
        self.assertIn("5:00-6:00：清洁凝血", report)
        self.assertIn("6:00-7:00：标本袋牵拉取出", report)

    def test_clinical_report_includes_detailed_merged_instrument_progress(self):
        records = [
            {
                "window_id": index,
                "start_time": 325 + index * 5,
                "end_time": 330 + index * 5,
                "phase": "CalotTriangleDissection",
                "visibility": "clear",
                "summary": (
                    "当前处于肝胆三角解剖，双极电凝钳反复开合夹持并分离"
                    "肝胆三角内纤维脂肪组织，抓钳配合牵拉以扩大关键结构暴露。"
                    "CVS安全视野确认中。"
                ),
            }
            for index in range(4)
        ]
        report = _build_deterministic_clinical_report("test.mp4", records, [], "zh")
        self.assertIn("**主要解剖操作**", report)
        self.assertIn("双极电凝钳解剖（5:25-5:45）", report)
        self.assertIn("## 分阶段详细复盘", report)
        self.assertIn("**器械与操作区间**", report)
        self.assertIn("累计约 0:20", report)
        self.assertIn("双极电凝钳解剖", report)
        self.assertIn("扩大关键结构暴露", report)
        self.assertNotIn("肝胆三角或胆囊床", report)

    def test_clinical_report_adds_context_around_key_action(self):
        records = [
            {
                "window_id": 0,
                "start_time": 0,
                "end_time": 5,
                "phase": "ClippingCutting",
                "visibility": "clear",
                "summary": "当前处于夹闭切断，钛夹钳在目标结构旁调整夹体位置。",
            },
            {
                "window_id": 1,
                "start_time": 5,
                "end_time": 10,
                "phase": "ClippingCutting",
                "visibility": "clear",
                "summary": "当前处于夹闭切断，夹子已夹闭目标组织，具体目标需回看确认。",
            },
            {
                "window_id": 2,
                "start_time": 10,
                "end_time": 15,
                "phase": "ClippingCutting",
                "visibility": "clear",
                "summary": "当前处于夹闭切断，电凝钩分离肝胆三角内纤维脂肪组织。",
            },
        ]
        events = [{
            "id": "clip-placement",
            "type": "action",
            "severity": "important",
            "title": "夹子放置",
            "summary": "夹子已夹闭目标组织，具体目标需回看原片确认。",
            "window_ids": [1],
            "representative_window_id": 1,
            "start_time": 5,
            "end_time": 10,
        }]
        report = _build_deterministic_clinical_report("test.mp4", records, events, "zh")
        self.assertIn("## 关键操作与上下文", report)
        self.assertIn("**操作前**：钛夹钳在目标结构旁调整夹体位置", report)
        self.assertIn("**节点记录**：夹子已夹闭目标组织", report)
        self.assertIn("**后续状态**：电凝钩分离肝胆三角内纤维脂肪组织", report)

    def test_clip_applier_review_wins_false_scissors_without_detector_support(self):
        visual = {
            "clip_applier": {"visible": False, "active": False, "confidence": 0.0},
            "scissors": {"visible": True, "cutting": False, "confidence": 0.90},
            "clip_secondary_review": {
                "success": True,
                "classification": "clip_applier",
                "confidence": 0.98,
                "clamped_on_tissue": False,
            },
            "scissors_secondary_review": {
                "success": True,
                "instrument": "scissors",
                "scissors_visible": True,
                "scissors_cutting": False,
                "confidence": 0.90,
            },
        }
        resolved = _resolve_clip_applier_scissors_conflict(
            visual,
            {"yolo": {"tools": [{"label": "clipper", "frames_seen": 11}]}},
        )
        self.assertFalse(resolved["scissors"]["visible"])
        self.assertTrue(resolved["clip_applier"]["visible"])
        self.assertFalse(resolved["clip_applier"]["active"])
        self.assertEqual(
            resolved["scissors_secondary_review"]["conflict_resolved_as"],
            "clip_applier",
        )

    def test_detector_supported_scissors_is_not_overridden(self):
        visual = {
            "clip_applier": {"visible": True, "active": False, "confidence": 0.98},
            "scissors": {"visible": True, "cutting": True, "confidence": 0.90},
            "clip_secondary_review": {
                "success": True,
                "classification": "clip_applier",
                "confidence": 0.98,
                "clamped_on_tissue": False,
            },
            "scissors_secondary_review": {
                "success": True,
                "instrument": "scissors",
                "scissors_visible": True,
                "scissors_cutting": True,
                "confidence": 0.90,
            },
        }
        resolved = _resolve_clip_applier_scissors_conflict(
            visual,
            {"yolo": {"tools": [{"label": "scissors", "frames_seen": 9}]}},
        )
        self.assertTrue(resolved["scissors"]["visible"])
        self.assertTrue(resolved["scissors"]["cutting"])

    def test_event_cap_never_drops_scissors_risk(self):
        events = [
            {
                "id": f"event_visibility_out_of_body_{index}",
                "type": "visibility",
                "severity": "important",
                "title": "镜头移出体外",
                "start_time": 100 + index * 10,
                "representative_window_id": 20 + index,
                "window_ids": [20 + index],
            }
            for index in range(11)
        ]
        events.extend([
            {
                "id": "event_required_scissors_before_cvs",
                "type": "risk",
                "severity": "critical",
                "title": "CVS未达成时出现剪刀操作",
                "start_time": 20,
                "representative_window_id": 9,
                "window_ids": [3, 7, 9],
            },
            {
                "id": "event_required_cvs_status",
                "type": "cvs",
                "severity": "safety",
                "title": "CVS安全评估",
                "start_time": 10,
                "representative_window_id": 5,
                "window_ids": [1, 5],
            },
        ])
        selected = _select_key_event_nodes(events, 10)
        by_id = {event["id"]: event for event in selected}
        self.assertEqual(len(selected), 10)
        self.assertIn("event_required_scissors_before_cvs", by_id)
        self.assertEqual(by_id["event_required_scissors_before_cvs"]["window_ids"], [3, 7, 9])
        self.assertIn("event_required_cvs_status", by_id)

    def test_partial_tail_window_is_clamped_to_media_duration(self):
        self.assertEqual(_bounded_window_end(1730, 5, 1733), 1733)
        self.assertEqual(_bounded_window_end(100, 5, None), 105)

    def test_scope_exit_overrides_terminal_retraction_template(self):
        summary = _normalize_retraction_summary(
            "当前处于标本袋牵拉取出。",
            {"visibility": {"status": "out_of_body", "out_of_body": True, "confidence": 0.99}},
        )
        self.assertIn("镜头移出体外", summary)
        self.assertNotIn("牵拉装有胆囊", summary)

    def test_scope_exit_does_not_append_routine_cvs_wording(self):
        summary = _strip_visual_rejected_scissors_claims(
            "镜头移出体外，画面切换至套管口或腹壁外场景。",
            {
                "visibility": {"status": "out_of_body", "out_of_body": True, "confidence": 0.99},
                "scissors": {"visible": False, "cutting": False},
            },
            phase="CalotTriangleDissection",
        )
        self.assertEqual(summary, "镜头移出体外，画面切换至套管口或腹壁外场景。")
        self.assertNotIn("CVS", summary)

    def test_long_prompt_compaction_keeps_short_critical_event(self):
        rows = [
            {
                "window_id": index,
                "start_time": index * 5,
                "end_time": index * 5 + 5,
                "phase": "CalotTriangleDissection",
                "visibility": "clear",
                "scissors": "",
                "scissors_review": "rejected",
                "summary": "当前处于肝胆三角解剖。",
            }
            for index in range(500)
        ]
        rows[237]["summary"] = "检测到大量活动性出血。"
        compacted = _compact_clinical_summary_records(rows, 180)
        self.assertEqual(len(compacted), 180)
        self.assertIn(237, {row["window_id"] for row in compacted})

    def test_specific_hook_action_removes_generic_duplicate(self):
        summary = _compact_local_summary_text(
            "当前处于胆囊分离，电凝钩分离胆囊床组织。电凝钩分离局部纤维组织。"
        )
        self.assertEqual(summary, "当前处于胆囊分离，电凝钩分离胆囊床组织。")

    def test_anatomical_area_synonym_is_deduplicated(self):
        summary = _compact_local_summary_text(
            "当前处于肝胆三角解剖，电凝钩分离肝胆三角组织。"
            "电凝钩分离肝胆三角区域组织。"
        )
        self.assertEqual(summary.count("电凝钩分离肝胆三角"), 1)

    def test_target_clipping_removes_generic_clip_and_hook_fragments(self):
        summary = _compact_local_summary_text(
            "当前处于夹闭切断，电凝钩分离局部纤维组织。"
            "CVS处于夹闭前后安全核查中。钛夹钳夹闭胆囊动脉。可见已释放夹子。"
        )
        self.assertIn("钛夹钳夹闭胆囊动脉", summary)
        self.assertNotIn("可见已释放夹子", summary)
        self.assertNotIn("电凝钩分离局部纤维组织", summary)

    def test_specific_scissors_warning_removes_generic_activity(self):
        summary = _compact_local_summary_text(
            "当前处于夹闭切断。剪刀在胆囊动脉邻近区域操作，"
            "CVS尚未达成，需核查后再剪断。剪刀在操作区域内活动。"
        )
        self.assertIn("剪刀在胆囊动脉邻近区域操作", summary)
        self.assertNotIn("剪刀在操作区域内活动", summary)

    def test_gallbladder_dissection_hides_stale_released_clip(self):
        summary = _compact_local_summary_text(
            "当前处于胆囊分离，电凝钩分离胆囊床组织。可见已释放夹子。"
        )
        self.assertIn("电凝钩分离胆囊床组织", summary)
        self.assertNotIn("可见已释放夹子", summary)

    def test_hook_cut_triplet_is_not_described_as_target_division(self):
        prior = {
            "cvs_achieved": False,
            "clipped": set(),
            "cut": set(),
            "reached_phases": {"Preparation", "CalotTriangleDissection"},
            "last_phase": "CalotTriangleDissection",
            "max_phase_order": 1,
            "packaging_seen": False,
            "post_retrieval_review": False,
            "formal_started": True,
        }
        summary, phase, _ = _apply_surgical_sequence_rules(
            "当前处于肝胆三角解剖，电凝钩切断胆囊动脉。CVS安全视野确认中。",
            "CalotTriangleDissection",
            prior,
        )
        self.assertEqual(phase, "CalotTriangleDissection")
        self.assertIn("电凝钩分离肝胆三角纤维组织", summary)
        self.assertNotIn("切断胆囊动脉", summary)
        self.assertNotIn("再剪断", summary)

    def test_vague_hook_gallbladder_dissection_is_phase_specific(self):
        prior = {
            "cvs_achieved": False,
            "clipped": set(),
            "cut": set(),
            "reached_phases": {"Preparation", "CalotTriangleDissection", "ClippingCutting"},
            "last_phase": "ClippingCutting",
            "max_phase_order": 2,
            "packaging_seen": False,
            "post_retrieval_review": False,
            "formal_started": True,
        }
        summary, _, _ = _apply_surgical_sequence_rules(
            "当前处于夹闭切断，电凝钩分离胆囊。电凝钩分离局部纤维组织。",
            "ClippingCutting",
            prior,
        )
        self.assertIn("电凝钩分离肝胆三角组织", summary)
        self.assertNotIn("电凝钩分离胆囊。", summary)
        self.assertNotIn("电凝钩分离局部纤维组织", summary)

    def test_gallbladder_surrounding_tissue_is_normalized_to_bed(self):
        prior = {
            "cvs_achieved": False,
            "clipped": {"cystic_duct", "cystic_artery"},
            "cut": set(),
            "reached_phases": {
                "Preparation",
                "CalotTriangleDissection",
                "ClippingCutting",
                "GallbladderDissection",
            },
            "last_phase": "GallbladderDissection",
            "max_phase_order": 3,
            "packaging_seen": False,
            "post_retrieval_review": False,
            "formal_started": True,
        }
        summary, _, _ = _apply_surgical_sequence_rules(
            "当前处于胆囊分离，电凝钩分离胆囊床组织。"
            "电凝钩分离胆囊周围组织。",
            "GallbladderDissection",
            prior,
        )
        self.assertEqual(summary.count("电凝钩分离胆囊床组织"), 1)
        self.assertNotIn("胆囊周围组织", summary)

    def test_scope_exit_does_not_advance_to_packaging(self):
        rows = [
            {"window_id": 0, "phase": "Preparation", "summary": "当前处于准备阶段。"},
            {"window_id": 1, "phase": "CalotTriangleDissection", "summary": "当前处于肝胆三角解剖。"},
            {
                "window_id": 2,
                "phase": "GallbladderPackaging",
                "summary": "镜头移出体外，画面切换至套管口或腹壁外场景。",
            },
            {"window_id": 3, "phase": "CalotTriangleDissection", "summary": "当前处于肝胆三角解剖。"},
        ]
        state = _build_surgical_sequence_state(rows)
        self.assertEqual(state["last_phase"], "CalotTriangleDissection")
        self.assertEqual(state["max_phase_order"], 1)
        self.assertFalse(state["packaging_seen"])
        self.assertNotIn("GallbladderPackaging", state["reached_phases"])

    def test_scope_exit_preserves_prior_phase_during_current_window(self):
        prior = _build_surgical_sequence_state([
            {"window_id": 0, "phase": "Preparation", "summary": "当前处于准备阶段。"},
            {"window_id": 1, "phase": "CalotTriangleDissection", "summary": "当前处于肝胆三角解剖。"},
        ])
        text, phase, rules = _apply_surgical_sequence_rules(
            "镜头移出体外，画面切换至套管口或腹壁外场景。",
            "GallbladderPackaging",
            prior,
            visual={
                "visibility": {
                    "status": "out_of_body",
                    "out_of_body": True,
                    "confidence": 0.99,
                    "evidence_source": "visibility_secondary_review",
                }
            },
        )
        self.assertEqual(phase, "CalotTriangleDissection")
        self.assertIn("镜头移出体外", text)
        self.assertIn("scope_exit_preserves_surgical_phase", rules)

    def test_invalid_late_stage1_removes_bagging_prose(self):
        prior = _build_surgical_sequence_state([
            {"window_id": 0, "phase": "Preparation", "summary": "当前处于准备阶段。"},
            {"window_id": 1, "phase": "CalotTriangleDissection", "summary": "当前处于肝胆三角解剖。"},
        ])
        summary, phase, rules = _apply_surgical_sequence_rules(
            "当前处于胆囊取出与装袋，将胆囊装入标本袋并准备取出。电凝钩分离局部纤维组织。",
            "GallbladderPackaging",
            prior,
        )
        self.assertEqual(phase, "CalotTriangleDissection")
        self.assertNotIn("装袋", summary)
        self.assertNotIn("标本袋", summary)
        self.assertIn("电凝钩分离局部纤维组织", summary)
        self.assertIn("late_phase_requires_clipping_and_dissection", rules)

    def test_legitimate_packaging_requires_prior_workflow(self):
        rows = [
            {"window_id": 0, "phase": "Preparation", "summary": "准备。"},
            {"window_id": 1, "phase": "CalotTriangleDissection", "summary": "肝胆三角解剖。"},
            {"window_id": 2, "phase": "ClippingCutting", "summary": "夹闭切断。"},
            {"window_id": 3, "phase": "GallbladderDissection", "summary": "胆囊床分离。"},
            {"window_id": 4, "phase": "GallbladderPackaging", "summary": "将胆囊装入标本袋。"},
        ]
        state = _build_surgical_sequence_state(rows)
        self.assertTrue(state["packaging_seen"])
        self.assertIn("GallbladderPackaging", state["reached_phases"])

    def test_visual_veto_removes_inactive_target_clip_claim(self):
        visual = {
            "clip_applier": {"visible": True, "active": False},
            "clip_secondary_review": {
                "success": True,
                "classification": "clip_applier",
                "confidence": 0.99,
                "clamped_on_tissue": False,
            },
        }
        summary = _strip_visual_rejected_clip_claims(
            "当前处于胆囊分离，钛夹钳夹闭胆囊管。",
            visual,
            "GallbladderDissection",
        )
        self.assertNotIn("夹闭胆囊管", summary)
        self.assertIn("胆囊床", summary)

    def test_no_clip_review_removes_active_target_clip_claim(self):
        visual = {
            "clip_applier": {"visible": False, "active": False},
            "clip_secondary_review": {
                "success": True,
                "classification": "no_clip",
                "confidence": 0.99,
                "clamped_on_tissue": False,
            },
        }
        summary = _strip_visual_rejected_clip_claims(
            "当前处于夹闭切断，钛夹钳夹闭胆囊动脉。",
            visual,
            "ClippingCutting",
        )
        self.assertNotIn("夹闭胆囊动脉", summary)
        self.assertIn("观察胆囊管和胆囊动脉处理区域", summary)

    def test_idle_clip_applier_vetoes_stapler_hallucination(self):
        visual = {
            "clip_applier": {"visible": True, "active": False},
            "clip_secondary_review": {
                "success": True,
                "classification": "clip_applier",
                "confidence": 0.99,
                "clamped_on_tissue": False,
            },
        }
        summary = _strip_unsupported_stapler_wording(
            "当前处于夹闭切断，冲吸器清理术野。"
            "CVS处于夹闭前后安全核查中。"
            "自动缝合器正在对胆囊动脉残端进行缝合操作。",
            visual,
        )
        self.assertIn("冲吸器清理术野", summary)
        self.assertNotIn("缝合器", summary)
        self.assertNotIn("缝合操作", summary)

    def test_repeated_target_clip_is_removed_after_dissection(self):
        prior = {
            "cvs_achieved": True,
            "clipped": {"cystic_duct", "cystic_artery"},
            "cut": set(),
            "reached_phases": {
                "Preparation",
                "CalotTriangleDissection",
                "ClippingCutting",
                "GallbladderDissection",
            },
            "last_phase": "GallbladderDissection",
            "max_phase_order": 3,
            "packaging_seen": False,
            "post_retrieval_review": False,
            "formal_started": True,
        }
        summary, phase, rules = _apply_surgical_sequence_rules(
            "当前处于胆囊分离，钛夹钳夹闭胆囊管。钛夹钳夹闭胆囊动脉。",
            "GallbladderDissection",
            prior,
        )
        self.assertEqual(phase, "GallbladderDissection")
        self.assertNotIn("钛夹钳夹闭", summary)
        self.assertIn("胆囊床", summary)
        self.assertIn("drop_repeated_target_clip_after_dissection_cystic_duct", rules)

    def test_preparation_does_not_claim_cystic_duct_dissection(self):
        prior = {
            "cvs_achieved": False,
            "clipped": set(),
            "cut": set(),
            "reached_phases": {"Preparation"},
            "last_phase": "Preparation",
            "max_phase_order": 0,
            "packaging_seen": False,
            "post_retrieval_review": False,
            "formal_started": False,
        }
        summary, phase, rules = _apply_surgical_sequence_rules(
            "当前处于准备阶段，双极电凝分离胆囊管。",
            "Preparation",
            prior,
        )
        self.assertEqual(phase, "Preparation")
        self.assertNotIn("分离胆囊管", summary)
        self.assertIn("分离局部粘连组织", summary)
        self.assertIn("preparation_suppresses_target_specific_action", rules)


if __name__ == "__main__":
    unittest.main()
