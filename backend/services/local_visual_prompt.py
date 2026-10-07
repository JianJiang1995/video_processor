"""Image-first instructions for the local model; no video-specific priors."""
import json
import math
import re
from copy import deepcopy
from .clip_review import detector_instruments
from .visibility_consistency import validate_fog_transition

def _schema_object(**fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


_BOOL = {'type': 'boolean'}
_SCORE = {'type': 'number', 'minimum': 0, 'maximum': 1}
_TEXT = {'type': 'string', 'maxLength': 64}
_FRAMES = {'type': 'array', 'items': {'type': 'integer', 'minimum': 1, 'maximum': 8}, 'maxItems': 8}
LOCAL_VISUAL_SCHEMA = _schema_object(
    visual=_schema_object(
        scene=_schema_object(classification={'type': 'string', 'enum': [
            'intra_abdominal', 'external_body', 'trocar_transition', 'uncertain']}, evidence=_TEXT),
        observations={'type': 'array', 'maxItems': 3, 'items': _schema_object(
            instrument=_TEXT, appearance=_TEXT, action=_TEXT, target=_TEXT, target_evidence=_TEXT, frames=_FRAMES)},
        generic_clip=_schema_object(visible=_BOOL, placed=_BOOL, count={'type': 'integer', 'minimum': 0, 'maximum': 30}, confidence=_SCORE),
        clip_applier=_schema_object(visible=_BOOL, active=_BOOL, confidence=_SCORE),
        scissors=_schema_object(visible=_BOOL, cutting=_BOOL, target=_TEXT, confidence=_SCORE),
        target_structure=_schema_object(label={'type': 'string', 'enum': ['cystic_duct', 'cystic_artery', 'unknown']}, confidence=_SCORE, evidence=_TEXT),
        gauze=_schema_object(visible=_BOOL, manipulated=_BOOL, confidence=_SCORE),
        bleeding=_schema_object(active=_BOOL, severity={'type': 'string', 'enum': ['none', 'minor', 'moderate', 'severe']}, controlled=_BOOL, confidence=_SCORE),
        cvs=_schema_object(status={'type': 'string', 'enum': ['not_applicable', 'assessing', 'partial', 'achieved']}, confidence=_SCORE),
        visibility=_schema_object(status={'type': 'string', 'enum': [
            'clear', 'foggy', 'blurred', 'blocked', 'out_of_body', 'uncertain']}, fog=_BOOL,
            fog_cleared=_BOOL, out_of_body=_BOOL, confidence=_SCORE, evidence=_TEXT,
            fog_frame_indices=_FRAMES, clear_frame_indices=_FRAMES)),
    summary={'type': 'string', 'minLength': 1, 'maxLength': 160})

_ANATOMY_TERM = r'(?:胆囊动脉|胆囊管|胆囊|肝脏|腹膜|网膜|脂肪(?:组织)?|粘连(?:组织)?)'
_ALTERNATIVE_ANATOMY = re.compile(_ANATOMY_TERM + r'(?:\s*(?:或者|或|/)\s*' + _ANATOMY_TERM + r')+')
BAG_TERMS = r'标本袋|取物袋|胆囊袋|塑料袋|装袋器|袋膜|袋口|specimen.bag|retrieval.bag'


def bag_geometry_disputed(visual):
    review = (visual or {}).get('visibility_secondary_review') or {}
    confidence = review.get('confidence')
    if review.get('rejected_reason') == 'bag_requires_corroborating_visual_observations':
        confidence = review.get('proposed_confidence', confidence)
    try:
        confidence = float(confidence or 0)
    except (TypeError, ValueError):
        return False
    return bool(review.get('success') and .8 <= confidence <= 1
                and review.get('bag_geometry') == 'woven_fabric')


def bag_observation_frames(visual, image_count=None):
    """Require bag geometry, not a detector/phase label or gauze appearance."""
    frames = set()
    if bag_geometry_disputed(visual):
        return frames
    for item in (visual or {}).get('observations') or []:
        if not isinstance(item, dict):
            continue
        identity = ' '.join(str(item.get(k) or '') for k in ('instrument', 'target'))
        shape = ' '.join(str(item.get(k) or '') for k in ('appearance', 'target_evidence'))
        if re.search(r'未见|没有|不是|并非|疑似|\b(?:no|without)\b', identity + ' ' + shape, re.I):
            continue
        if not re.search(BAG_TERMS, identity + ' ' + shape, re.I):
            continue
        if not re.search(r'袋口|袋膜|半透明|透明塑料|透明.*袋|袋状.*包裹|柔软.*袋|折叠.*袋', shape):
            continue
        frames.update(i for i in item.get('frames') or []
                      if type(i) is int and i >= 1 and (image_count is None or i <= image_count))
    return frames


def parse_complete_visual_response(text, finish_reason=None):
    """Incomplete generations must not silently become default-negative facts."""
    if finish_reason == 'length':
        return None
    raw = str(text or '').strip()
    if raw.startswith('```') and raw.endswith('```') and '\n' in raw:
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if (isinstance(parsed, dict) and not parsed.get('summary')
            and isinstance(parsed.get('visual'), dict)
            and isinstance(parsed['visual'].get('summary'), str)):
        # Some local models put the complete narrative one level too deep.
        # Promote the supplied value; never invent missing facts or close JSON.
        parsed['summary'] = parsed['visual'].pop('summary')
    if (not isinstance(parsed, dict) or not isinstance(parsed.get('summary'), str)
            or not parsed['summary'].strip() or not isinstance(parsed.get('visual'), dict)
            or not parsed['visual']):
        return None
    return parsed


async def normalize_visual_with_retry(result, normalize, retry):
    normalized = await normalize(result)
    count = int(result.get('_external_call_count') or 1)
    if normalized.get('error') != 'incomplete_local_visual_result' or count >= 2:
        return normalized
    try:
        retried = await retry()
        retried['_external_call_count'] = count + 1
        normalized = await normalize(retried)
        normalized['retried_incomplete_response'] = True
        normalized['initial_finish_reason'] = result.get('finish_reason')
        return normalized
    except Exception as error:
        return {**normalized, 'error': 'incomplete_visual_retry_failed',
                'retry_error': f'{type(error).__name__}: {error}', 'external_call_count': count + 1}


LOCAL_VISUAL_PROMPT = """你是手术视频审阅者。结合连续图像和同窗专家候选，描述实际器械、操作和接触的解剖组织，输出JSON。所有描述用中文。

识别顺序：先确认腹腔内或体外，再识别器械尖端，最后沿器械接触位置辨认组织及跨帧动作。
专家中的gallbladder是胆囊，liver是肝脏，omentum是网膜，adhesion是粘连组织，cystic_duct才是胆囊管。
胆囊囊壁与胆囊管不是同一组织；脂肪或粘连覆盖在胆囊表面，不应仅凭位置写成胆囊管。
必须区分背景器官和器械实际接触对象。组织名称依据外形、连接关系和接触位置，不仅依据颜色。
器械接触覆盖于器官上的脂肪、腹膜或粘连时，操作对象就是该覆盖层；不能因为背景是肝脏或胆囊而改写成牵拉肝脏、分离胆囊管。
胆囊管或胆囊动脉必须在至少两帧看到同一结构的形态及连接依据；细长白色组织、阶段和专家标签本身不足以确认。
图像与SurgR1和Triplet共同支持一个名称时，应直接写该名称，不退回“黄色组织”“粉红组织”。
若只能识别脂肪、腹膜或粘连，写相应组织；连接关系不清时不猜胆囊管或胆囊动脉。
电凝钩需辨认单杆钩头，抓钳需有可开合的钳口，剪刀需有剪刃。白色绝缘部、反光不是夹子或剪刀。
动作依据连续帧：抓钳夹住并带动组织是牵拉，电凝钩打开组织层是分离；只触碰时写接触，不凭器械名推断凝血。
候选与图像冲突时以图像为准，SurgR1也可能在体外场景误报手术动作。
器械候选不是器械存在的证据。必须在图像中定位到器械杆或尖端，才能写器械操作。
unknown只表示图像中确有器械实体但类型不清，不能表示默认存在器械。没有可定位器械实体时observations必须为[]，summary仅描述实际组织外观，不编造“未知器械接触”。

重要事件：独立留在组织上的夹体才算夹子，钳口闭合或释放夹体才算施夹；剪刃闭合且组织离断才算剪断。
柔软袋膜和袋口包裹标本才是装袋。大量新鲜血液持续流出才是severe出血，红色组织不是出血。
操作对象也可以是纱布、标本袋，不要强迫填写解剖组织。透明袋的大片光滑塑料膜、折叠边和加强袋口不同于带血管的腹膜或网膜；袋内的组织与袋膜本身分别描述。
外部皮肤、手术巾、设备和房间可证明体外；灰白失焦不能单独证明体外或起雾。绿色荧光不是雾。
controlled、fog_cleared需同组帧证实从出血/起雾到消失，不能把一直正常判为已解决。
CVS达成需三要素同时可确认：肝胆三角清理、胆囊下三分之一与胆囊板分离、仅两条结构进入胆囊；否则assessing/partial。

先输出visual.scene:{classification:intra_abdominal或external_body或trocar_transition或uncertain,evidence:主体场景形态依据}。
必须先从主体画面核实空间场景，不能从专家的器械和组织候选倒推场景。大片完整皮肤、切口外缘、铺巾与从体表插入的套管是体外，套管不是电凝钩，皮肤不是脂肪或肝脏。
若连续图像跨越不同场景，scene描述最后可确认的场景；不同场景的器械和组织不能拼成一个操作。体外时不生成腹腔内器械操作，描述皮肤、切口或外露套管。
再输出visual.observations，最多3个操作，每项填写：
instrument（规范器械名或unknown）、appearance（尖端形态）、action（可见动作）、target（接触组织名称）、target_evidence（组织形态或连接关系）、frames（依据帧序号，1起始）。
不要把背景器官填为操作对象。没有器械操作时可以单独描述暴露区域。
然后填写其他visual字段：
generic_clip:{visible:bool,placed:bool,count:int,confidence:0到1}
clip_applier:{visible:bool,active:bool,confidence:0到1}
scissors:{visible:bool,cutting:bool,target:字符串,confidence:0到1}
target_structure:{label:cystic_duct或cystic_artery或unknown,confidence:0到1,evidence:结构连接依据}（该字段unknown不影响命名胆囊本体、肝脏等其他组织）
gauze:{visible:bool,manipulated:bool,confidence:0到1}
bleeding:{active:bool,severity:none或minor或moderate或severe,controlled:bool,confidence:0到1}
cvs:{status:not_applicable或assessing或partial或achieved,confidence:0到1}
visibility:{status:clear或foggy或blurred或blocked或out_of_body,fog:bool,fog_cleared:bool,out_of_body:bool,confidence:0到1,evidence:图像依据,fog_frame_indices:起雾帧编号列表,clear_frame_indices:清晰帧编号列表}（帧编号1起始；status描述最后可确认的视野，fog与fog_cleared不能同时为true）
最后输出summary，1-3句“具体器械+观察到的动作+具体接触组织”，多个操作分别保留。summary不能新增observations中不存在的组织或动作。
不要在摘要解释器官的定义或通常连接关系；target_evidence写本组图像实际可定位的形态，不能用解剖常识代替证据。
CVS状态仅填visual.cvs，阶段由界面显示；摘要不写时间、帧号、候选模型、否定检查清单，不写“或/或者”的二选一猜测。
只输出一个含visual和summary的JSON对象。
输出紧凑JSON，不缩进；每项evidence不超过40字，summary不超过100字。务必输出完整summary及闭合JSON，不重复解释候选。
summary必须与visual平级，放在JSON最外层，不要放进visual对象里面。
除JSON字段名和指定枚举值外，器械名称、组织名称和描述必须使用中文。
frames只填输入图像的帧编号，不是视频秒数；target_structure无证据时label必须为unknown。"""


def normalize_local_review(result, image_count):
    """Check evidence references and narrative consistency, not clinical truth."""
    result = deepcopy(result)
    visual = result.get("visual") or {}
    terms = {"cystic_duct": "胆囊管", "cystic_artery": "胆囊动脉",
             "gallbladder": "胆囊", "liver": "肝脏", "omentum": "网膜",
             "adhesion": "粘连组织", "grasper": "抓钳", "hook": "电凝钩",
             "scissors": "剪刀", "clipper": "施夹器", "bipolar": "双极电凝钳"}

    def chinese(text):
        return re.sub(r"(?<![a-z_])(" + "|".join(terms) + r")(?![a-z_])",
                      lambda m: terms[m[0].lower()], str(text or ""), flags=re.IGNORECASE)

    observations = visual.get("observations")
    observations = observations if isinstance(observations, list) else []
    unsupported_tools = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        indices = item.get("frames")
        indices = indices if isinstance(indices, list) else []
        valid = sorted({i for i in indices if type(i) is int and 1 <= i <= image_count})
        if len(valid) != len(indices):
            item["invalid_frame_references"] = indices
        item["frames"] = valid
        for key in ("instrument", "appearance", "action", "target", "target_evidence"):
            if key in item:
                item[key] = chinese(item[key])
        unknown_tool = str(item.get('instrument') or '').strip().lower() in {
            '', 'unknown', 'none', '未知', '未知器械', '无', '无器械'}
        if unknown_tool and re.search(r'未知|不可见|未见|不清|无器械|unknown|not visible',
                                      str(item.get('appearance') or ''), re.IGNORECASE):
            unsupported_tools.append({**item, 'rejection_reason': 'no_visible_tool_action_evidence'})
        if _ALTERNATIVE_ANATOMY.search(str(item.get('target') or '')):
            item['unconfirmed_target'] = item['target']
            item['target'] = _ALTERNATIVE_ANATOMY.sub('局部组织', item['target'])
            item['target_rejection_reason'] = 'ambiguous_alternative_anatomy'
    structure = visual.get("target_structure") or {}
    structure = structure if isinstance(structure, dict) else {}
    try:
        confidence = float(structure.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    if not math.isfinite(confidence) or confidence < .7 or not structure.get("evidence"):
        if structure.get("label") not in {None, "unknown"}:
            visual["unconfirmed_target_structure"] = dict(structure)
        structure = {"label": "unknown", "confidence": 0, "evidence": ""}
    visual["target_structure"] = structure
    if isinstance(visual.get('visibility'), dict):
        visual['visibility'] = validate_fog_transition(visual['visibility'], image_count)
    summary = chinese(result.get("summary"))
    if unsupported_tools:
        visual.setdefault('rejected_observations', []).extend(unsupported_tools)
        observations = [item for item in observations if not isinstance(item, dict) or not any(
            item.get('instrument') == rejected.get('instrument') and item.get('appearance') == rejected.get('appearance')
            for rejected in unsupported_tools)]
        visual['observations'] = observations
        visual['unverified_tool_wording'] = summary
        summary = '。'.join(clause for clause in re.split(r'[，,。；;]+', summary) if clause.strip()
            and not re.search(r'未知器械|unknown', clause, re.IGNORECASE)
            and (observations or not re.search(r'接触|牵拉|夹持|分离|切断|剪断|凝血', clause)))
        summary = summary.rstrip('。') + '。' if summary else '当前画面为术野观察。'
    if _ALTERNATIVE_ANATOMY.search(summary):
        visual['unresolved_anatomy_wording'] = summary
        summary = _ALTERNATIVE_ANATOMY.sub('局部组织', summary)
    rejected = []
    for label in ("cystic_duct", "cystic_artery"):
        name = terms[label]
        supported = any(
            name in str(item.get("target") or "") and len(item.get("frames") or []) >= 2
            and re.search(r'连接|延续|汇入|走行|连续|进入|胆囊颈|管腔|junction|continuity',
                          str(item.get("target_evidence") or ''), re.IGNORECASE)
            for item in observations if isinstance(item, dict))
        if structure.get('label') == label and not supported:
            visual['unconfirmed_target_structure'] = dict(structure)
            visual['target_structure'] = {'label': 'unknown', 'confidence': 0, 'evidence': ''}
        if not supported:
            for item in observations:
                if isinstance(item, dict) and name in str(item.get('target') or ''):
                    item['unconfirmed_target'] = item['target']
                    item['target'] = '局部组织'
                    item['target_rejection_reason'] = 'missing_multiframe_connection_evidence'
        if name in summary and not supported:
            clauses = re.split(r"[，。；]", summary)
            retained = [part for part in clauses if part.strip() and name not in part]
            summary = "。".join(retained) + "。" if retained else summary.replace(name, "局部组织")
            rejected.append(label)
    if rejected:
        visual["anatomy_consistency_review"] = {"unsupported_summary_targets": rejected,
                                                  "original_summary": result.get("summary")}
    if bag_geometry_disputed(visual):
        replacements = []
        for item in observations:
            if not isinstance(item, dict) or not re.search(BAG_TERMS, str(item.get('target') or ''), re.IGNORECASE):
                continue
            evidence = str(item.get('appearance') or '') + str(item.get('target_evidence') or '')
            woven = bool(re.search(r'网状|编织|织物|纱布|蓝色.{0,5}线', evidence))
            target = '纱布' if woven else '局部材料'
            item.update(unconfirmed_target=item['target'], target=target,
                        target_rejection_reason='woven_fabric_confirmed_not_bag' if woven else 'disputed_bag_geometry')
            replacements.append(target)
        if re.search(BAG_TERMS, summary, re.IGNORECASE):
            visual['unverified_bag_wording'] = summary
            target = '纱布' if replacements and set(replacements) == {'纱布'} else '局部材料'
            summary = re.sub(BAG_TERMS, target, summary, flags=re.IGNORECASE)
            summary = re.sub(r'纱布(?:和|与|及|、)纱布', '纱布', summary)
    if (visual.get('visibility') or {}).get('out_of_body'):
        summary = reviewed_scene_summary(visual) or '镜头移出体外。'
    if not summary.strip(' 。；,，;\n\t'):
        visual['summary_empty_after_consistency_review'] = True
        summary = '当前画面为术野观察。'
    result.update(summary=summary, visual=visual)
    return result


def build_local_visual_prompt(experts=None, phase=None, frame_count=None):
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
    aliases = {"clip_applier": "clipper", "electrocautery_hook": "hook"}
    detected = detector_instruments(experts)
    candidates = []
    for item in triplet.get("triplet", []) if isinstance(triplet, dict) else []:
        candidate = fields(item, ("label", "confidence"))
        confidence = candidate.get("confidence")
        labels = re.findall(r"\[([^\]]+)\]", str(candidate.get("label") or ""))
        # Conflicting instruments are not independent support for an action.
        # Keep raw records in storage, but don't prime the VLM with that claim.
        if "null_verb" in labels or "null_target" in labels:
            continue
        if detected is not None and (not labels or labels[0] not in detected):
            continue
        if isinstance(confidence, (int, float)) and confidence >= 0.12 and candidate.get("label"):
            candidates.append(candidate)
    candidates.sort(key=lambda item: item["confidence"], reverse=True)
    phase_hint = fields(experts.get("phase"), ("label", "confidence", "frame_count"))
    context = {
        "phase_candidate": phase_hint,
        "tools_candidates": [fields(item, ("label", "confidence", "frames_seen"))
                             for item in (yolo.get("tools") or [])[:8] if isinstance(item, dict)
                             and (detected is None or aliases.get(item.get("label"), item.get("label")) in detected)]
                            if isinstance(yolo, dict) else [],
        "operation_candidates": candidates[:4],
        "tissue_candidates": [],
    }
    for item in triplet.get("target", []) if isinstance(triplet, dict) else []:
        candidate = fields(item, ("label", "confidence", "class_id"))
        confidence = candidate.get("confidence")
        if (candidate.get("label") not in {None, "", "null_target"}
                and isinstance(confidence, (int, float)) and .12 <= confidence <= 1):
            context["tissue_candidates"].append(candidate)
    context["tissue_candidates"] = sorted(
        context["tissue_candidates"], key=lambda item: item["confidence"], reverse=True,
    )[:3]
    surgr1 = experts.get("surgr1") or {}
    # A weak or different detector label must not corroborate an R1 action.
    # This filters prompt candidates, not image evidence or the saved raw R1.
    if isinstance(surgr1, dict) and surgr1.get("success"):
        supported = []
        for item in surgr1.get("observations", [])[:3]:
            candidate = fields(item, ("action",))
            action = str(candidate.get("action") or "").lower()
            if not action or re.search(r"\b(null_verb|null_target|unknown_action|unknown_target|no_visible_instrument)\b", action):
                continue
            names = re.findall(r"\b(grasper|hook|bipolar|scissors|clipper|clip_applier|electrocautery_hook|irrigator)\b", action)
            named_tools = {aliases.get(name, name) for name in names}
            if detected is not None and (not named_tools or not named_tools.issubset(detected)):
                continue
            supported.append(candidate)
        if supported:
            context["surgr1_observations"] = supported
    if phase and phase != "Unknown":
        context["display_phase"] = str(phase)[:80]
    if frame_count is not None:
        context["allowed_frame_indices"] = list(range(1, frame_count + 1))
    return LOCAL_VISUAL_PROMPT + "\n\n" + (
        "以下是同一窗口专家的待核实候选，不是已经确认的事实。先观察图像，再逐项核对候选。\n"
        "置信度不等于视觉证据；阶段不能证明某动作已经发生。候选与图像冲突时以图像为准。\n"
        "只有单独的器械或组织候选，不能拼成一个动作；空候选也不表示没有器械。\n"
        "tissue_candidates来自独立组织分类头，不因器械冲突而丢弃；它们仍是待核实的组织候选，不是接触部位的定位结果。\n"
        "SurgR1给出的是逐帧候选描述，不是已确认事实；动态操作仍需本组连续图像验证，不照抄其器械或组织误判。\n"
        "不要照抄旧摘要；候选中的blood_vessel只表示血管，不能自动改成胆囊动脉，"
        "cystic_pedicle也不能自动改成胆囊管。\n"
        "摘要允许只有一条观察，不强求牵拉或分离；无操作时保留组织/视野事实。不要填写否定检查清单。\n"
        "frames只填allowed_frame_indices中的帧编号，不填秒数。target_evidence描述接触组织的形态或连接关系。\n"
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
标本袋可以是透明塑料，观察大片光滑反光袋膜与折叠边、加强袋口。不要把透明袋膜当成网膜，袋内组织不等于袋子本身。判为specimen_bag_inside时必须同时确认袋口和袋膜，并提供至少两帧依据；仅白色物品或单帧不够。
纱布的编织网格、纤维纹理和蓝色显影线不等于塑料袋膜。不能将网状纱布判为标本袋。
绿色荧光成像不是雾。雾需要组织边界受遮挡。
脂肪、腹膜的白色薄膜和浅色组织不等于镜头雾气；清晰的细血管、组织纹理和锐利边缘不能仅因组织发白就判起雾。
纱布、器械遮挡和运动失焦不等于起雾；仍能辨认腹腔组织时选intra_abdominal，无法判定时选uncertain。
透明袋膜贴近镜头造成的离焦、折射或局部遮挡，也不等于镜头表面起雾；不能只凭画面发白判断雾。
空间场景与成像质量独立判断：仍在腹腔内不等于图像已清晰；雾、运动模糊、器械遮挡必须区分。
返回classification、image_quality（最后一帧的clear/foggy/blurred/blocked/uncertain）、confidence（0到1）、evidence（简短具体图像证据）、
external_evidence_type（skin_external/trocar_outer_valve/room_furniture/staff_or_drape/trocar_lumen/none）、
external_frame_indices（明确体外或套管证据所在帧的0起始序号列表，无证据为空列表）。
另返回bag_geometry（bag_mouth_and_film/woven_fabric/unclear）、bag_frame_indices（明确袋口与袋膜证据帧，0起始，无证据为空）。
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


def scene_observations_need_review(visual, local_cue=None, experts=None):
    """Scene clues request image verification; they never decide a scene label."""
    visual = visual if isinstance(visual, dict) else {}
    cue = local_cue or {}
    pack = experts or {}
    if any(item.get('label') == 'specimen_bag' and int(item.get('frames_seen') or 0) >= 2
           for item in (pack.get('yolo') or {}).get('tools') or [] if isinstance(item, dict)):
        return True
    if cue.get('out_of_body_candidate') or cue.get('out_of_body'):
        return True
    scene = visual.get('scene') or {}
    if scene.get('classification') in {'external_body', 'trocar_transition', 'uncertain'}:
        return True
    visibility = visual.get("visibility") or {}
    if visibility.get("out_of_body") or visibility.get("status") == "out_of_body":
        return True
    if visibility.get('fog') or visibility.get('status') in {'foggy', 'blurred', 'blocked'}:
        return True
    observations = visual.get("observations") or []
    if visual.get('observations') == []:
        return True
    evidence = [str(visibility.get("evidence") or ""), str(scene.get('evidence') or '')]
    for item in observations if isinstance(observations, list) else []:
        if isinstance(item, dict):
            evidence.extend(str(item.get(key) or "") for key in ("instrument", "appearance", "action", "target", "target_evidence"))
    scene_terms = r"皮肤|体毛|手术巾|无菌巾|手术室|器械台|套管阀|腹壁|切口外|设备屏幕|触摸屏|连接管线|医疗设备|skin|drape|operating.room|" + BAG_TERMS
    for text in evidence:
        for clause in re.split(r"[。；;，,]", text):
            if re.search(r"未见|没有|未观察到|不含|(?:^|\s)无(?!菌)|\b(?:no|without|absent)\b", clause.strip(), re.IGNORECASE):
                continue
            if re.search(scene_terms, clause, re.IGNORECASE):
                return True
    return False


def validate_visibility_review(review: dict, image_count: int, primary=None) -> dict:
    result = dict(review)
    if result.get('classification') == 'specimen_bag_inside' and result.get('bag_geometry') == 'woven_fabric':
        result.update(proposed_classification='specimen_bag_inside', proposed_confidence=result.get('confidence'),
                      classification='uncertain', confidence=0.0,
                      rejected_reason='bag_requires_corroborating_visual_observations')
        return result
    if result.get('classification') == 'specimen_bag_inside' and isinstance(primary, dict):
        bag_frames = bag_observation_frames(primary, image_count)
        reviewed_frames = {i for i in result.get('bag_frame_indices') or []
                           if type(i) is int and 0 <= i < image_count}
        independent_geometry = (result.get('bag_geometry') == 'bag_mouth_and_film'
                                and float(result.get('confidence') or 0) >= .9
                                and len(reviewed_frames) >= 2)
        if len(bag_frames) < 2 and not independent_geometry:
            result.update(proposed_classification='specimen_bag_inside', proposed_confidence=result.get('confidence'),
                          classification='uncertain',
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
        result.update(proposed_classification=result.get('classification'),
                      proposed_confidence=result.get('confidence'),
                      classification='uncertain', confidence=0.0,
                      rejected_reason='external_scene_requires_distinct_frame_evidence')
    return result
