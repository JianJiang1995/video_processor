# Clip/Hem-o-lok 专家训练审计

日期：2026-07-04

## 结论

当前这批由 GPT 自动标注得到的训练图片不能直接作为可靠专家模型上线。

主要原因：

- `datasets/surgical_objects_merged_v3` 的伪标注污染明显。很多 Hem-o-lok、钛夹、施夹器框实际落在组织、反光、普通器械、蓝色背景结构或画面边缘上。
- `datasets/surgical_clips_curated_v4` 经过高置信规则过滤后干净很多，但正样本太少，只有 Hem-o-lok 12 个、钛夹 22 个原始目标，不足以训练稳定检测器。
- `datasets/surgical_clips_curated_v5_balanced` 虽然通过过采样做了平衡，但本质仍是少量正样本重复，验证集表现不稳定，不能接入主分析流程。
- 出血和纱布样本不足。`surgical_objects_gpt_v3` 中 `active_bleeding` 为 0，`gauze` 只有 3 个，不能训练对应专家。

因此，`config.json` 仍保持使用原来的 `yolo_clip_v1`，没有切换到 v2/v3/v5 新模型。

## 数据集检查

### GPT 候选框标注 v2

路径：`datasets/clip_detector_gpt_v2`

统计：

- 图片：131
- 空标签图：67
- Hem-o-lok：12
- 钛夹：67
- 施夹器：22
- 反光：3
- 其他器械：3

问题：

- 该版本基于候选框再分类。候选框来源本身不够干净，GPT 只能在候选框内做分类，无法纠正框选位置错误。
- 钛夹有一定信号，但 Hem-o-lok 和施夹器标签混淆严重。

训练结果：

- 模型：`models/clip_detector/yolo_clip_gpt_v2/weights/best.pt`
- 最好 mAP50：0.231
- 最好 recall：0.135

判断：不能上线。

### GPT 全帧标注 v3

路径：`datasets/surgical_objects_gpt_v3`

统计：

- 图片：113
- 空标签图：22
- Hem-o-lok：43
- 钛夹：25
- 施夹器：32
- 纱布：3
- 活动性出血：0
- 反光：2
- 其他器械：66

问题：

- GPT-4.1 全帧标注比候选框版本更像真实目标，但仍有明显错标。
- Hem-o-lok 中白色/象牙色夹子多数可信，蓝色结构相关标签不可信。
- 施夹器类别过宽，普通抓钳、电凝钩、器械杆都可能被标成施夹器。
- 纱布和出血样本量不足。

判断：可以作为预标注来源，但不能直接训练上线。

### 合并数据 v3

路径：`datasets/surgical_objects_merged_v3`

统计：

- 图片：244
- 空标签图：89
- Hem-o-lok：55
- 钛夹：92
- 施夹器：54
- 纱布：3
- 活动性出血：0
- 反光：5
- 其他器械：69

视觉审计图：

- `tmp/label_audit_merged_v3/class_0_hemolok_clip.jpg`
- `tmp/label_audit_merged_v3/class_1_titanium_clip.jpg`
- `tmp/label_audit_merged_v3/class_2_clip_applier.jpg`
- `tmp/label_audit_merged_v3/class_3_gauze.jpg`
- `tmp/label_audit_merged_v3/class_6_other_instrument.jpg`

问题：

- 合并后把候选框污染也带进来了。
- Hem-o-lok 类别里混入大量蓝色结构、暗区、组织边缘和普通器械。
- 钛夹类别里有真实金属夹，但也混入器械杆、阴影、蓝色结构和反光。
- 施夹器类别不能稳定代表“正在夹闭钛夹/Hem-o-lok”的操作器械。

训练结果：

- 模型：`models/clip_detector/yolo_surgical_objects_gpt_v3/weights/best.pt`
- 最好 mAP50：0.124
- 最好 recall：0.088

判断：不能上线。

### 高置信清洗数据 v4/v5

路径：

- `datasets/surgical_clips_curated_v4`
- `datasets/surgical_clips_curated_v5_balanced`

视觉审计图：

- `tmp/label_audit_curated_v4/hemolok_clip.jpg`
- `tmp/label_audit_curated_v4/titanium_clip.jpg`
- `tmp/label_audit_curated_v4/all_positive.jpg`

清洗后统计：

- Hem-o-lok：12
- 钛夹：22

训练结果：

- v4 模型：`models/clip_detector/yolo_surgical_clips_curated_v4/weights/best.pt`
- v5 模型：`models/clip_detector/yolo_surgical_clips_curated_v5/weights/best.pt`
- v5 最好 mAP50：0.028
- v5 最后验证：precision/recall/mAP 均为 0

判断：

- 标注质量比 merged v3 好，但样本太少。
- 过采样不能代替真实样本多样性。
- 模型在验证图上基本漏检，不能上线。

## 是否真的能靠 GPT 标注训练专家

可以，但需要把 GPT 定位为“预标注器”，不是最终标注真值。

当前实验说明：

- GPT-4.1 能识别部分白色 Hem-o-lok 和金属钛夹。
- GPT-4.1 对小目标边界框不稳定，尤其在反光、血液、蓝色夹、器械交叠时会混淆。
- 单轮 GPT 标注直接训练会把错误放大到模型里。

推荐流水线：

1. 使用强 GPT/VLM 做预标注，不开思考模式，输出结构化 JSON。
2. 做自动 QA：颜色、尺寸、长宽比、位置、类别词证据、重复框、过大框过滤。
3. 对高风险类别做二次审查：Hem-o-lok、钛夹、纱布、活动性出血。
4. 每类至少积累数百个可信框后再训练检测器。
5. 训练后必须看预测图，不只看指标。

## 下一步建议

优先级：

1. 先补真实标注数据：Hem-o-lok、钛夹、纱布、活动性出血分别独立建类。
2. 每个类别先做 300 到 500 个高质量框，覆盖不同视频、角度、亮度、烟雾、血液、器械遮挡。
3. 对 Hem-o-lok 和钛夹分别加入强 prompt 特征：
   - Hem-o-lok：白色/象牙色/蓝色聚合物夹，通常较厚，U 形或夹闭后平行臂，非金属反光。
   - 钛夹：银色/灰白金属小夹，细窄，强高光，通常成 V/U 形，尺寸明显小于施夹器。
4. 对活动性出血单独采样，必须包含“持续流动/喷涌/迅速扩散”的正例和普通血染负例。
5. 等本地 VLM 替代外部 GPT 后，仍保留同样的 QA 流程，避免本地模型批量错标。

## 当前上线状态

没有把 v2/v3/v5 新模型接入主流程。

当前 `config.json` 仍指向：

`models/clip_detector/yolo_clip_v1/weights/best.pt`

