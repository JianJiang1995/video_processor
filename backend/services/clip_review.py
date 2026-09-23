"""Morphology evidence required before a visual review can assert clipping."""

CLIP_REVIEW_PROMPT = """观察按时间排列的同一短视频窗口全图。先检查每件器械的远端结构，
然后判断是否有施夹动作或留置夹子。不要根据手术阶段猜测，不要把图像角落的图标、文字、水印当器械。
先列出全图中实际可见的所有器械visible_instruments，允许为空；不要因为题目询问夹子就假设夹子存在。
湿润组织表面的亮白长线、膜状反光和组织褶皱不是夹子：夹子必须有完整的独立实体边界，不能只凭一个亮点或弧线。
电凝钩：单根杆，末端一段白色陶瓷绝缘套，最尖端只有一个细小弯钩/L形金属头。
白色陶瓷套即使看起来像尖嘴，也不是两个钳口；单钩接触、推拉组织不等于夹闭。
抓钳：两个有孔/齿状的钳口，开合夹持组织，不等于施夹器。
双极电凝钳：两个可开合的电极钳口；蓝色绝缘套与器械相连，不是独立蓝色夹子。颜色本身不能判定器械类型。
施夹器：必须看到金属长杆/腕部与两个分离的宽厚平行钳口连续相连，且有容纳夹子的结构。
器械杆可以大部分位于画面外，只需钳口基部与金属器械相连；不要求整根长杆、完整腕部都入镜。
留在组织上的紫色、蓝色、白色小夹体不是施夹器；相邻两个夹体及其间空隙不是器械的两个钳口。
只有画面确实仅剩独立夹体、完全没有相连的金属器械时，才排除clip_applier和施夹动作。
但独立留置夹体本身仍是clip：visible_instruments可以为空，同时classification=clip、motion=static；不要把“没有器械”写成“没有夹子”。
施夹器钳口也可能有槽孔，不能仅因有孔判为抓钳；若钳口内载着独立夹体，应结合载夹结构和闭合过程判断施夹器。
剪刀：两个细薄交叉切刃，不能把一个弯钩叫剪刀。
已放置夹子：独立于器械的小型C/U形或锁扣结构，留在组织上；不区分材质。
已有夹体静止留置用motion=static，不要编造释放动作。没有器械和独立夹体时用no_clip、unclear。
只有连续帧看到施夹器两个钳口对同一组织由张开到闭合，或释放夹体，才设applier_active=true。
仅接触组织、尖端抵住组织、单帧闭合外观均不能证明正在施夹。
若没有独立夹子，请根据主要操作器械选择electrocautery_hook、grasper、scissors或other。
对夹闭目标，只有看清与胆囊颈连续的管腔结构才判cystic_duct，明确细血管分支才判cystic_artery，
看不清则unknown。输出JSON，先填写观察依据，再填写结论：
{"visible_instruments":[],
"tip_geometry":"single_hook|fenestrated_jaws|broad_parallel_jaws|crossing_blades|independent_clip|unclear",
"motion":"static|contact|traction|dissection|jaw_closure_on_tissue|clip_release|cutting|unclear",
"reason":"一句具体形态和动作依据",
"classification":"clip|clip_applier|scissors|electrocautery_hook|grasper|no_clip|other",
"confidence":0.0,"count":0,"independent_from_instrument":false,"clamped_on_tissue":false,
"applier_active":false,"scissors_cutting":false,"target":"cystic_duct|cystic_artery|unknown",
"target_confidence":0.0}。
visible_instruments只能包含electrocautery_hook、grasper、bipolar、clip_applier、scissors、irrigator、other。"""


def reviewed_instruments(visual: dict) -> set[str] | None:
    """Missing inventory is not negative evidence (including saved older runs)."""
    review = (visual or {}).get("clip_secondary_review") or {}
    values = review.get("visible_instruments")
    allowed = {"electrocautery_hook", "grasper", "bipolar", "clip_applier",
               "scissors", "irrigator", "other"}
    if (not review.get("success") or float(review.get("confidence") or 0) < .85
            or not isinstance(values, list)
            or any(not isinstance(v, str) or v not in allowed for v in values)):
        return None
    return set(values)


def review_rejects_active_application(visual: dict) -> bool:
    """A retained clip without an applier cannot establish a new placement."""
    review = (visual or {}).get('clip_secondary_review') or {}
    inventory = reviewed_instruments(visual)
    return bool(inventory is not None and 'clip_applier' not in inventory
                and review.get('classification') in {'clip', 'unconfirmed'}
                and review.get('independent_from_instrument')
                and review.get('motion') == 'static'
                and review.get('applier_active') is False)


def validate_clip_review(review: dict, primary: dict | None = None, experts: dict | None = None) -> dict:
    """Keep observations, but reject conclusions unsupported by their own evidence."""
    result = dict(review)
    label = str(result.get("classification") or "").lower()
    geometry = str(result.get("tip_geometry") or "unclear").lower()
    motion = str(result.get("motion") or "unclear").lower()
    if label == "clip_applier":
        valid_shape = geometry == "broad_parallel_jaws"
        result["action_evidence_supported"] = valid_shape and motion in {
            "jaw_closure_on_tissue", "clip_release",
        }
        if not valid_shape:
            result["classification"] = {
                "single_hook": "electrocautery_hook",
                "fenestrated_jaws": "grasper",
                "crossing_blades": "scissors",
            }.get(geometry, "other")
            result["evidence_rejection"] = "applier_without_two_broad_jaws"
        if not result["action_evidence_supported"]:
            result.update(applier_active=False, clamped_on_tissue=False,
                          target="unknown", target_confidence=0.0)
        if valid_shape and primary is not None:
            applier = primary.get('clip_applier') or {}
            primary_support = bool(applier.get('visible') or applier.get('active'))
            detector_support = any(
                isinstance(tool, dict) and tool.get('label') in {'clipper', 'clip_applier'}
                and int(tool.get('frames_seen') or 0) >= 2
                for tool in ((experts or {}).get('yolo') or {}).get('tools', [])
            )
            frame_indices = result.get('clip_frame_indices') or []
            frame_indices = frame_indices if isinstance(frame_indices, list) else []
            evidence_frames = {i for i in frame_indices if type(i) is int
                               and 0 <= i < int(result.get('images') or 0)}
            delivery_support = (result.get('loaded_clip_visible') is True
                                and len(evidence_frames) >= 2)
            if not (primary_support or detector_support or delivery_support):
                # Re-prompting the same VLM is not independent confirmation.
                # Bare jaw closure also describes ordinary grasping forceps.
                result.update(proposed_classification='clip_applier', classification='unconfirmed',
                              accepted=False, action_evidence_supported=False,
                              applier_active=False, clamped_on_tissue=False,
                              target='unknown', target_confidence=0.0,
                              evidence_rejection='uncorroborated_applier')
    elif label == "clip":
        if geometry != "independent_clip" or not result.get("independent_from_instrument"):
            result.update(classification="other", count=0, clamped_on_tissue=False,
                          target="unknown", target_confidence=0.0,
                          evidence_rejection="clip_not_independent_from_instrument")
        elif primary is not None and not any(
            (primary.get(name) or {}).get("visible") or (primary.get(name) or {}).get("placed")
            for name in ("generic_clip", "hemolok", "titanium_clip")
        ):
            # A second prompt on the same small model is not an independent
            # detector. Do not let a lone positive overwrite the primary veto.
            result.update(proposed_classification="clip", classification="unconfirmed",
                          action_evidence_supported=False, accepted=False,
                          evidence_rejection="primary_and_review_disagree")
    return result


def scissors_morphology_confirmed(visual: dict) -> bool:
    """A risk needs reviewed blades, never a summary keyword or YOLO alone."""
    morphology = visual.get("clip_secondary_review") or {}
    focused = visual.get("scissors_secondary_review") or {}
    if morphology.get("success") and float(morphology.get("confidence") or 0) >= 0.8:
        if morphology.get("classification") in {"electrocautery_hook", "grasper", "clip_applier"}:
            inventory = reviewed_instruments(visual)
            if inventory is None or "scissors" not in inventory:
                return False
    if focused.get("success") and float(focused.get("confidence") or 0) >= 0.85:
        if focused.get("instrument") != "scissors":
            return False
        indices = focused.get("blade_frame_indices") or []
        supported = {i for i in indices if isinstance(i, int) and not isinstance(i, bool)
                     and 0 <= i < int(focused.get("images") or 0)}
        return bool(focused.get("scissors_visible") and len(supported) >= 2)
    return bool(morphology.get("success")
                and float(morphology.get("confidence") or 0) >= 0.85
                and morphology.get("classification") == "scissors"
                and morphology.get("tip_geometry") == "crossing_blades")
