"""Image-first instructions for the local model; no video-specific priors."""
import json
import math
import re


LOCAL_VISUAL_PROMPT = """审阅按时间排序的手术图像，输出当前窗口的视觉事实JSON。
先比较各帧的器械尖端、接触对象和组织变化，再填写结果。summary是手术进程摘要，不是组织外观清单。
用1至3句中文优先写清“器械名称 + 实际动作 + 可辨认的组织/区域”，然后写操作进展或重要视野变化。
有多个独立且有图像依据的操作时都保留，不把夹闭、剪断、装袋或活动性出血省略成笼统的组织描述。
动作主体不能省略；能确认器械类别才用具体名称，尖端不可辨时保留“器械”并描述看清的移动或接触。
牵拉需看见夹持后组织移位，分离需看见组织层间被打开；不能把器械在附近移动写成已完成操作。
确实没有可辨认操作时才简述暴露的区域和视野，不强行补动作，也不写“暂无明确操作变化”。
阶段由界面单独展示，summary不重复阶段标题；CVS状态仅填visual.cvs，不在摘要反复写。
即使没有夹子、出血等关键事件，也要描述当前画面，不要返回空摘要，不要复制字段定义。
看不清的动作或组织不要猜，不依赖手术通常顺序推断已经发生的操作。
摘要只描述肯定看见的事实，不罗列“未见夹子、未见剪刀、未见出血”等否定检查清单。
摘要必须与visual一致，不要一边说没有施夹，一边把active设为true。

器械名称必须依据图中尖端结构确认，不能根据常见手术器械清单补全。颜色和反光不能证明器械类别。
夹子必须是独立留在组织上的完整夹体，不能是器械尖端或组织反光。
只有跨帧看到钳口闭合夹住组织或释放夹体，才是施夹；夹子静止存在不是正在施夹。
剪刀接近组织不是剪断，切刃闭合且组织离断才是剪断。不因安全步骤缺失而隐瞒实际可见动作。
能确认具体解剖组织才命名胆囊管或胆囊动脉，否则target为unknown。
电凝钩接触和分离不等于正在凝血；只有明确的电凝作用证据才写凝血处理。
仅白色、亮色或反光不能证明标本袋。必须有柔软袋膜、袋口且包裹标本才写装袋。
腹腔内组织仍可见不是移出体外；体外需皮肤、手术巾、套管外口或手术室等证据。
绿色荧光成像、颜色改变不是起雾，雾需遮挡和降低组织边界清晰度。
红色组织或少量血染不是大量活动性出血；持续新鲜血液涌出才上报。
controlled仅用于看见活动性出血从有到无的过程，正常无出血应为false；
fog_cleared仅用于同一组图像从有雾到清晰的过程，一直清晰应为false。
CVS达成需同时看清肝胆三角清理、胆囊下三分之一与胆囊板分离、仅两条结构进入胆囊；
看不全只能评估中，不因为出现施夹或剪刀就判达成。

输出顺序：先visual.observations填写至多3项实际看见的器械形态、动作和对象，再填写visual的其余字段，最后写summary。
observations不是推理过程，只记录可直接定位的视觉事实；器械不明填unknown，不得为了凑具体名称猜类别。
summary只能整合这些观察，不得新增观察中没有的器械或动作。不要写时间、窗口号、图片序号、或/或者二选一。
除JSON字段名和指定枚举值外，所有器械名称、组织名称和描述必须使用中文。
visual各字段的类型如下（自行根据图片填写值，不是答案示例）：
observations: [{instrument:器械名称或unknown, appearance:可见尖端形态, action:实际运动或接触变化, target:可辨认组织或区域, frames:有依据的图片序号列表从1开始}]
generic_clip: {visible:bool, placed:bool, count:int, confidence:0到1}
clip_applier: {visible:bool, active:bool, confidence:0到1}
scissors: {visible:bool, cutting:bool, target:字符串, confidence:0到1}
target_structure: {label:cystic_duct或cystic_artery或unknown, confidence:0到1, evidence:图像依据}
gauze: {visible:bool, manipulated:bool, confidence:0到1}
bleeding: {active:bool, severity:none或minor或moderate或severe, controlled:bool, confidence:0到1}
cvs: {status:not_applicable或assessing或partial或achieved, confidence:0到1}
visibility: {status:clear或foggy或blurred或blocked或out_of_body, fog:bool, fog_cleared:bool, out_of_body:bool, confidence:0到1, evidence:图像依据}
不要附加解释，只输出JSON。"""


def build_local_visual_prompt(experts=None, phase=None):
    """Bounded raw candidates, not previously generated narrative or guessed anatomy."""
    experts = experts if isinstance(experts, dict) else {}

    def fields(value, keys):
        if not isinstance(value, dict):
            return {}
        return {key: value[key] for key in keys if isinstance(value.get(key), (str, int, float))
                and not isinstance(value[key], bool)
                and (not isinstance(value[key], float) or math.isfinite(value[key]))}

    triplet = experts.get("triplet") or {}
    yolo = experts.get("yolo") or {}
    tools = yolo.get("tools") if isinstance(yolo, dict) else None
    aliases = {"clip_applier": "clipper", "electrocautery_hook": "hook"}
    detected = {aliases.get(item.get("label"), item.get("label")) for item in tools or []
                if isinstance(item, dict)}
    candidates = []
    for item in triplet.get("triplet", []) if isinstance(triplet, dict) else []:
        candidate = fields(item, ("label", "confidence"))
        confidence = candidate.get("confidence")
        labels = re.findall(r"\[([^\]]+)\]", str(candidate.get("label") or ""))
        # Conflicting instruments are not independent support for an action.
        # Keep raw records in storage, but don't prime the VLM with that claim.
        if "null_verb" in labels or "null_target" in labels:
            continue
        if isinstance(tools, list) and (not labels or labels[0] not in detected):
            continue
        if isinstance(confidence, (int, float)) and confidence >= 0.12 and candidate.get("label"):
            candidates.append(candidate)
    candidates.sort(key=lambda item: item["confidence"], reverse=True)
    phase_hint = fields(experts.get("phase"), ("label", "confidence", "frame_count"))
    context = {
        "phase_candidate": phase_hint,
        "tools_candidates": [fields(item, ("label", "confidence", "frames_seen"))
                             for item in (yolo.get("tools") or [])[:8] if isinstance(item, dict)]
                            if isinstance(yolo, dict) else [],
        "operation_candidates": candidates[:4],
    }
    if phase and phase != "Unknown":
        context["display_phase"] = str(phase)[:80]
    return LOCAL_VISUAL_PROMPT + "\n\n" + (
        "以下是同一窗口专家的待核实候选，不是已经确认的事实。先观察图像，再逐项核对候选。\n"
        "置信度不等于视觉证据；阶段不能证明某动作已经发生。候选与图像冲突时以图像为准。\n"
        "只有单独的器械或组织候选，不能拼成一个动作；空候选也不表示没有器械。\n"
        "不要照抄旧摘要；候选中的blood_vessel只表示血管，不能自动改成胆囊动脉，"
        "cystic_pedicle也不能自动改成胆囊管。\n"
        "摘要允许只有一条观察，不强求牵拉或分离；无操作时保留组织/视野事实。不要填写否定检查清单。\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


VISIBILITY_REVIEW_PROMPT = """观察同一个窗口的连续手术图像，只判断当前视野，输出JSON。
classification取值：external_body（体外）、trocar_transition（套管内部）、
specimen_bag_inside（腹腔内标本袋）、foggy_inside（镜头雾气或烟雾遮挡）、intra_abdominal（腹腔内）、uncertain。
体外必须清楚看到真实腹壁外侧皮肤、外露套管阀门、手术巾/手套、手术室设备；
套管内部必须看到可辨认的管壁和管腔。仅红色满屏、灰白模糊、低饱和、过曝都不足以判断体外。
屏幕角落的标志、字幕、边框、设备UI、图标不是手术室设备或外露阀门。
先判断空间场景，再判断成像质量，两者不能混为一谈。真实房间的设备外壳、支架、线缆、墙面或天花板
在主体画面中占据空间、随镜头产生透视变化时，是体外场景；不是角落叠加的设备UI。
即使上述体外设备透过雾气、水滴或模糊可见，仍应选external_body，不能因发灰或失焦改为foggy_inside。
foggy_inside要求仍有可辨认的腹腔组织作为体内依据；既无腹腔依据又无明确体外依据时选uncertain。
无法看清场景时应判断模糊受遮挡或uncertain，不猜已移出体外。
腹腔组织仍可见且仅前景有镜鞘边缘，不算移出体外。
标本袋需看清柔软袋膜和袋口，组织薄膜/白色反光不是标本袋。
纱布的编织网格、纤维纹理和蓝色显影线不等于塑料袋膜。不能将网状纱布判为标本袋。
绿色荧光成像不是雾。雾需要组织边界受遮挡。
纱布、器械遮挡和运动失焦不等于起雾；仍能辨认腹腔组织时选intra_abdominal，无法判定时选uncertain。
返回classification、confidence（0到1）、evidence（具体图像证据）、
external_evidence_type（skin_external/trocar_outer_valve/room_furniture/staff_or_drape/trocar_lumen/none）、
external_frame_indices（明确体外或套管证据所在帧的0起始序号列表，无证据为空列表）。
只输出JSON。"""


def reviewed_scene_summary(visual):
    """Render accepted scene facts without exposing frame indices or raw guesses."""
    scene = (visual or {}).get('visibility_secondary_review') or {}
    if float(scene.get('confidence') or 0) < .75:
        return None
    if scene.get('classification') == 'trocar_transition':
        return '镜头位于套管内部，腹腔术野暂不可见。'
    if scene.get('classification') != 'external_body':
        return None
    descriptions = {
        'skin_external': '画面显示腹壁外侧皮肤。',
        'trocar_outer_valve': '画面显示体外套管阀门。',
        'room_furniture': '画面显示手术室设备。',
        'staff_or_drape': '画面显示体外人员或手术巾。',
    }
    return '镜头移出体外。' + descriptions.get(scene.get('external_evidence_type'), '画面为体外场景。')


def scene_observations_need_review(visual):
    """Scene clues request image verification; they never decide a scene label."""
    visual = visual if isinstance(visual, dict) else {}
    visibility = visual.get("visibility") or {}
    if visibility.get("out_of_body") or visibility.get("status") == "out_of_body":
        return True
    if visibility.get('fog') or visibility.get('status') in {'foggy', 'blurred', 'blocked'}:
        return True
    observations = visual.get("observations") or []
    if visual.get('observations') == [] and not visibility.get('evidence'):
        return True
    evidence = [str(visibility.get("evidence") or "")]
    for item in observations if isinstance(observations, list) else []:
        if isinstance(item, dict):
            evidence.extend(str(item.get(key) or "") for key in ("instrument", "appearance", "action", "target"))
    scene_terms = r"皮肤|体毛|手术巾|无菌巾|手术室|器械台|套管阀|腹壁外|标本袋|取物袋|袋膜|袋口|specimen.bag|retrieval.bag|skin|drape|operating.room"
    for text in evidence:
        for clause in re.split(r"[。；;，,]", text):
            if re.search(r"未见|没有|未观察到|不含|\b(?:no|without|absent)\b", clause, re.IGNORECASE):
                continue
            if re.search(scene_terms, clause, re.IGNORECASE):
                return True
    return False


def validate_visibility_review(review: dict, image_count: int, primary=None) -> dict:
    result = dict(review)
    if result.get('classification') == 'specimen_bag_inside' and isinstance(primary, dict):
        bag_frames = set()
        for item in primary.get('observations') or []:
            if not isinstance(item, dict):
                continue
            text = ' '.join(str(item.get(key) or '') for key in ('instrument', 'appearance', 'action', 'target'))
            if re.search(r'未见|没有|\b(?:no|without)\b', text, re.IGNORECASE):
                continue
            if re.search(r'标本袋|取物袋|袋膜|袋口|specimen.bag|retrieval.bag', text, re.IGNORECASE):
                bag_frames.update(i for i in item.get('frames', [])
                                  if type(i) is int and 1 <= i <= image_count)
        if len(bag_frames) < 2:
            result.update(proposed_classification='specimen_bag_inside', classification='uncertain',
                          confidence=0.0, rejected_reason='bag_requires_corroborating_visual_observations')
        return result
    if result.get('classification') not in {'external_body', 'trocar_transition'}:
        return result
    allowed = {'skin_external', 'trocar_outer_valve', 'room_furniture', 'staff_or_drape'}
    if result.get('classification') == 'trocar_transition':
        allowed = {'trocar_lumen'}
    indices = result.get('external_frame_indices')
    indices = indices if isinstance(indices, list) else []
    valid = {i for i in indices if type(i) is int and 0 <= i < image_count}
    if result.get('external_evidence_type') not in allowed or len(valid) < 2:
        result.update(classification='uncertain', confidence=0.0,
                      rejected_reason='external_scene_requires_distinct_frame_evidence')
    return result
