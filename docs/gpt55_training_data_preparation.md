# GPT-5.5 训练数据准备记录

日期：2026-07-04

## 目标

用更强的 GPT 视觉模型重新准备 Hem-o-lok、钛夹等手术夹检测训练数据，避免上一轮 GPT-4.1 / candidate-box 标注中出现的大量错标污染。

## 本轮方法

新增脚本：

- `scripts/gpt_label_surgical_objects.py`
  - 默认模型改为 `gpt-5.5`
  - 使用 Responses API
  - 使用 JSON Schema 结构化输出
  - `reasoning.effort=none`，降低标注延迟
  - 支持二次视觉复核：`--verify`
  - 支持自动审计图：`--write-audit`

- `scripts/curate_surgical_gpt_labels.py`
  - 将 GPT 结果筛成 clips-only YOLO 数据集
  - 支持 white/ivory Hem-o-lok 高精过滤
  - 支持 titanium metallic clip 过滤
  - 保留负样本
  - 输出 summary 和 audit 图

## 原始 GPT-5.5 标注集

### 全视频抽样

路径：`datasets/surgical_objects_gpt55_v1`

设置：

- 模型：`gpt-5.5`
- 视觉细节：`high`
- reasoning：`none`
- 二次复核：开启
- 样本：132 帧

保留结果：

- Hem-o-lok：36
- 钛夹：2
- 施夹器：22
- 纱布：7
- 活动性出血：3
- 反光：4
- 其他器械：76

被复核/规则拒绝：

- Hem-o-lok：30
- 钛夹：4
- 反光：70
- 其他类别合计：32

审计图路径：`datasets/surgical_objects_gpt55_v1/audit`

### 夹子视频密集抽样

路径：`datasets/surgical_objects_gpt55_clips_dense_v1`

设置：

- 模型：`gpt-5.5`
- 视觉细节：`high`
- reasoning：`none`
- 二次复核：开启
- 样本：180 帧
- 采样间隔：3 秒

保留结果：

- Hem-o-lok / 聚合物夹：31
- 钛夹：7
- 施夹器：39
- 活动性出血：15
- 其他器械：92

被复核/规则拒绝：

- Hem-o-lok / 聚合物夹：79
- 钛夹：24
- 反光：54
- 其他类别合计：56

审计图路径：`datasets/surgical_objects_gpt55_clips_dense_v1/audit`

## Curated 训练集

### White-only 高精训练集

路径：`datasets/surgical_clips_gpt55_high_precision_v1`

筛选规则：

- Hem-o-lok 只保留 reason 中明确 white/ivory/opaque/plastic/locking 且不包含 blue/purple/green 的目标
- Hem-o-lok 置信度 >= 0.82
- 钛夹要求 silver/gray/metal/metallic/titanium 视觉证据
- 钛夹置信度 >= 0.74
- 过滤过大、过细长的框

结果：

- 图片：39
- 正样本图片：13
- 负样本图片：26
- Hem-o-lok：13
- 钛夹：6

审计拼图：`datasets/surgical_clips_gpt55_high_precision_v1/audit_sheet.jpg`

判断：

- 质量明显好于上一轮伪标注。
- 数量仍远远不足以训练稳定专家。
- 可以作为种子数据和标注规范样例，不建议直接训练上线。

### Polymer candidate 候选集

路径：`datasets/surgical_clips_gpt55_polymer_candidate_v1`

筛选规则：

- 允许 blue/purple/green 聚合物夹进入候选集
- Hem-o-lok / polymer clip 置信度 >= 0.72
- 钛夹置信度 >= 0.70

结果：

- 图片：125
- 正样本图片：50
- 负样本图片：75
- 聚合物夹 / Hem-o-lok 候选：57
- 钛夹：7

审计拼图：`datasets/surgical_clips_gpt55_polymer_candidate_v1/audit_sheet.jpg`

判断：

- 可作为候选池继续审查。
- 不能直接等同于“白色 Hem-o-lok”训练真值。
- 如果业务目标是“所有已部署聚合物夹”，可以考虑在 ontology 中新增 `polymer_clip`，不要强行全塞进 `hemolok_clip`。

## 结论

更强 GPT 模型可以显著改善训练数据准备流程，但目前还不能完全自动产出可上线训练集。

本轮已经做到：

- 稳定调用 GPT-5.5 视觉标注
- 结构化输出
- 二次视觉复核
- 自动拒绝明显不适合训练的框
- 产出两套可追溯数据集和审计图

仍然不足：

- White-only Hem-o-lok 只有 13 个高精框
- 钛夹只有 6 到 7 个可用框
- 纱布和活动性出血仍不稳定，不能直接训练专家
- 蓝色聚合物夹是否归入 Hem-o-lok 需要业务定义，否则会污染类别边界

建议下一步：

1. 先确认 ontology：白色 Hem-o-lok 与蓝色/紫色聚合物夹是否同类。
2. 用 `polymer_candidate` 做人工或 GPT-5.5 Pro 二次抽检，筛出真正可用样本。
3. 每类至少积累 300 到 500 个高质量框后再训练检测器。
4. 暂时不要把本轮模型或数据直接接入主流程。

