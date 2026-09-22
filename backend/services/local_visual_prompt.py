"""Image-first instructions for the local model; no video-specific priors."""


LOCAL_VISUAL_PROMPT = """审阅按时间排序的手术图像，输出当前窗口的视觉事实JSON。
先观察图像，再填写结果。用1至3句中文描述当前画面，只有器械清晰可见时才描述其动作；
没有器械时只描述组织与视野，不要为了描述操作而编造器械存在。
即使没有夹子、出血等关键事件，也要描述当前画面，不要返回空摘要，不要复制字段定义。
看不清的动作或组织不要猜，不依赖手术通常顺序推断已经发生的操作。
摘要只描述肯定看见的事实，不罗列“未见夹子、未见剪刀、未见出血”等否定检查清单。
摘要必须与visual一致，不要一边说没有施夹，一边把active设为true。

区分器械：单杆白色陶瓷套末端的L形尖端是电凝钩；两个有孔钳口是抓钳；
剪刀需要两片交叉切刃，施夹器有宽而平行的钳口。颜色和反光本身不能证明器械类别。
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

返回对象含summary和visual；summary不写时间、窗口号、或/或者二选一。
visual各字段的类型如下（自行根据图片填写值，不是答案示例）：
generic_clip: {visible:bool, placed:bool, count:int, confidence:0到1}
clip_applier: {visible:bool, active:bool, confidence:0到1}
scissors: {visible:bool, cutting:bool, target:字符串, confidence:0到1}
target_structure: {label:cystic_duct或cystic_artery或unknown, confidence:0到1, evidence:图像依据}
gauze: {visible:bool, manipulated:bool, confidence:0到1}
bleeding: {active:bool, severity:none或minor或moderate或severe, controlled:bool, confidence:0到1}
cvs: {status:not_applicable或assessing或partial或achieved, confidence:0到1}
visibility: {status:clear或foggy或blurred或blocked或out_of_body, fog:bool, fog_cleared:bool, out_of_body:bool, confidence:0到1, evidence:图像依据}
不要附加解释，只输出JSON。"""


VISIBILITY_REVIEW_PROMPT = """观察同一个窗口的连续手术图像，只判断当前视野，输出JSON。
classification取值：external_body（体外）、trocar_transition（套管内部）、
specimen_bag_inside（腹腔内标本袋）、foggy_inside（视野模糊受遮挡）、intra_abdominal（腹腔内）、uncertain。
体外必须清楚看到真实腹壁外侧皮肤、外露套管阀门、手术巾/手套、手术室设备；
套管内部必须看到可辨认的管壁和管腔。仅红色满屏、灰白模糊、低饱和、过曝都不足以判断体外。
屏幕角落的标志、字幕、边框、设备UI、图标不是手术室设备或外露阀门。
无法看清场景时应判断模糊受遮挡或uncertain，不猜已移出体外。
腹腔组织仍可见且仅前景有镜鞘边缘，不算移出体外。
标本袋需看清柔软袋膜和袋口，组织薄膜/白色反光不是标本袋。
绿色荧光成像不是雾。雾需要组织边界受遮挡。
返回classification、confidence（0到1）、evidence（具体图像证据）、
external_evidence_type（skin_external/trocar_outer_valve/room_furniture/staff_or_drape/trocar_lumen/none）、
external_frame_indices（明确体外或套管证据所在帧的0起始序号列表，无证据为空列表）。
只输出JSON。"""


def validate_visibility_review(review: dict, image_count: int) -> dict:
    result = dict(review)
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
