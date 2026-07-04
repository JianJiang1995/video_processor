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

## Cholec80 扩展尝试

数据源：`/data/cholec80/cholec80/videos`

新增脚本：

- `scripts/sample_cholec80_clip_phase.py`
  - 读取 Cholec80 `*-timestamp.txt` 分期文件
  - 默认抽取 `ClippingCutting` 阶段，并在阶段前后增加时间 margin
  - 输出抽帧图片、`frames.jsonl` 和 `summary.json`

- `scripts/gpt_label_surgical_objects.py`
  - 新增 `--frames-jsonl`，可以直接复用已抽好的帧和时间戳
  - 这样 Cholec80 分期抽样与 GPT 标注可以分离，便于反复调 prompt 和阈值

抽帧命令：

```bash
/home/user/proj/video_processor/.venv/bin/python scripts/sample_cholec80_clip_phase.py \
  --video-dir /data/cholec80/cholec80/videos \
  --output datasets/cholec80_clipping_samples_v1 \
  --phase ClippingCutting \
  --margin-sec 40 \
  --interval-sec 4 \
  --max-per-range 32 \
  --max-total 240 \
  --clean
```

抽帧结果：

- 覆盖 Cholec80 80 个视频
- 80 个视频均存在 `ClippingCutting` 阶段
- 写出 240 帧
- 抽帧目录：`datasets/cholec80_clipping_samples_v1`
- 抽帧审计图：`datasets/cholec80_clipping_samples_v1/contact_sheet.jpg`

GPT-5.5 标注命令：

```bash
/home/user/proj/video_processor/.venv/bin/python scripts/gpt_label_surgical_objects.py \
  --model gpt-5.5 \
  --api-mode responses \
  --reasoning-effort none \
  --image-detail high \
  --frames-jsonl datasets/cholec80_clipping_samples_v1/frames.jsonl \
  --output datasets/cholec80_gpt55_clip_phase_v1 \
  --max-total-frames 240 \
  --min-confidence 0.50 \
  --min-verified-confidence 0.60 \
  --verify \
  --write-audit \
  --val-every 5 \
  --clean
```

Cholec80 GPT-5.5 保留结果：

- Hem-o-lok / polymer clip：3
- 钛夹：6
- 施夹器：70
- 纱布：2
- 活动性出血：24
- 反光：13
- 其他器械：207

Cholec80 GPT-5.5 拒绝结果：

- Hem-o-lok / polymer clip：8
- 钛夹：29
- 反光：113
- 其他器械：30

判断：

- Cholec80 可以补充一些夹闭阶段画面，尤其是小金属夹和施夹器场景。
- GPT-5.5 对小钛夹仍偏保守，很多视觉上可能正确的小夹子被二次复核拒绝。
- 这批数据对训练有帮助，但不能直接解决 Hem-o-lok 和钛夹识别不稳定的问题。
- 随机网页图片没有纳入训练集。原因是来源授权、域差异和手术画面上下文都不可控，容易污染检测器。

## 合并 Cholec80 后的数据集

### White-only 高精合并集

路径：`datasets/surgical_clips_gpt55_cholec80_high_precision_v1`

来源：

- `datasets/surgical_objects_gpt55_v1`
- `datasets/surgical_objects_gpt55_clips_dense_v1`
- `datasets/cholec80_gpt55_clip_phase_v1`

结果：

- 图片：66
- 正样本图片：22
- 负样本图片：44
- Hem-o-lok：19
- 钛夹：14
- 审计图：`datasets/surgical_clips_gpt55_cholec80_high_precision_v1/audit_sheet.jpg`

判断：

- 比原来的 white-only 集合有明显增加。
- 但每类数量仍不足以训练可靠检测器。
- 可以作为高精种子集，不建议直接训练上线。

### Polymer candidate 合并候选集

路径：`datasets/surgical_clips_gpt55_cholec80_candidate_v1`

结果：

- 图片：145
- 正样本图片：58
- 负样本图片：87
- Hem-o-lok / polymer clip 候选：60
- 钛夹：14
- 审计图：`datasets/surgical_clips_gpt55_cholec80_candidate_v1/audit_sheet.jpg`

判断：

- 样本数比高精集合更多。
- 包含蓝色/紫色聚合物夹，不能直接等同于业务里的白色 Hem-o-lok。
- 如果要使用，需要先在 ontology 中明确 `hemolok_clip`、`polymer_clip`、`titanium_clip` 的边界。

## YOLO 试训结果

试训命令：

```bash
/home/user/proj/video_processor/.venv/bin/python scripts/train_clip_detector.py \
  --data datasets/surgical_clips_gpt55_cholec80_candidate_v1/data.yaml \
  --base-model models/clip_detector/pretrained/yolo11n.pt \
  --project models/clip_detector \
  --name yolo_surgical_clips_gpt55_cholec80_candidate_v1 \
  --epochs 80 \
  --imgsz 960 \
  --batch 8 \
  --device 1 \
  --workers 4 \
  --patience 25
```

结果：

- Early stop：epoch 49
- Best：epoch 24
- overall precision：0.614
- overall recall：0.0868
- overall mAP50：0.0747
- overall mAP50-95：0.0398
- Hem-o-lok precision：0.229
- Hem-o-lok recall：0.174
- Hem-o-lok mAP50：0.149
- 钛夹 precision：1.000
- 钛夹 recall：0.000
- 钛夹 mAP50：0.000

模型路径：`models/clip_detector/yolo_surgical_clips_gpt55_cholec80_candidate_v1/weights/best.pt`

预测审计图：`runs/detect/runs/detect/audit/yolo_surgical_clips_gpt55_cholec80_candidate_v1_val_sheet.jpg`

结论：

- 这个试训模型不可用，不能接入主流程。
- 主要问题是有效框数量少、类别边界仍混杂、小目标标注过难。
- 钛夹验证 recall 为 0，说明现有候选集无法支撑钛夹专家。
- 当前 `config.json` 不应指向该权重。

## Clip-focused 标注与 VID001 局部密采

日期：2026-07-04

新增能力：

- `scripts/gpt_label_surgical_objects.py`
  - 新增 `--prompt-profile clip-focused`
  - 只关注已释放夹体，减少普通器械、出血、组织描述对 GPT 注意力的干扰
  - 当前定义中，`hemolok_clip` 只表示白色/象牙色 Hem-o-lok 锁扣夹
  - 蓝色/紫色/绿色聚合物夹不再自动归并成 `hemolok_clip`

- `scripts/curate_surgical_gpt_labels.py`
  - 新增 `--min-positive-gap-sec`
  - 用于按视频和标签稀疏连续正样本，避免 1 秒级连续帧把训练集虚高

- `scripts/sample_cholec80_clip_phase.py`
  - 新增自动 `contact_sheet.jpg`，每轮抽样后可以直接肉眼审计样本区域

### Cholec80 v2 抽样

路径：`datasets/cholec80_clipping_samples_v2`

命令：

```bash
/home/user/proj/video_processor/.venv/bin/python scripts/sample_cholec80_clip_phase.py \
  --video-dir /data/cholec80/cholec80/videos \
  --output datasets/cholec80_clipping_samples_v2 \
  --phase ClippingCutting \
  --margin-sec 45 \
  --interval-sec 2 \
  --max-per-range 80 \
  --max-total 1000 \
  --contact-sheet-max 80 \
  --clean
```

结果：

- 覆盖 80 个 Cholec80 视频
- 写出 1000 帧
- 审计拼图：`datasets/cholec80_clipping_samples_v2/contact_sheet.jpg`

判断：

- 样本区域基本正确，集中在胆囊管/胆囊动脉处理、夹闭和切断附近。
- Cholec80 对白色 Hem-o-lok 帮助有限；probe 中 Hem-o-lok 保留为 0。
- Cholec80 可以提供少量钛夹候选，但小钛夹仍容易和高光/组织边缘混淆。

### Clip-focused probe

Cholec80 80 帧 probe：

- 路径：`datasets/cholec80_gpt55_clip_phase_v2_clipfocused_probe`
- Hem-o-lok：0
- 钛夹：5
- specular_highlight：63
- rejected Hem-o-lok：3
- rejected 钛夹：13

判断：

- clip-focused prompt 能提升钛夹候选发现，但很多候选仍不适合训练。
- Cholec80 不是白色 Hem-o-lok 的主要来源。

本地夹子视频 100 帧 probe：

- 路径：`datasets/local_clip_videos_gpt55_clipfocused_probe`
- Hem-o-lok：0
- 钛夹：1
- specular_highlight：100

判断：

- 这批“夹子视频”里大量是蓝色/紫色聚合物夹或高光。
- 因为当前 white-only 定义不把 colored polymer 归入 Hem-o-lok，所以它们不能增加白色 Hem-o-lok 训练样本。

VID001 5 分钟附近 1 秒级密采：

- 视频：`/home/user/proj/video_processor/test_data/2024-12-24_225315_VID001.mp4`
- 区间：260-360 秒
- 路径：`datasets/vid001_5min_gpt55_clipfocused_probe`
- Hem-o-lok：79
- 钛夹：1
- specular_highlight：29
- rejected Hem-o-lok：28

判断：

- 这是本轮最有效的数据来源。
- Hem-o-lok audit 中多数框对准白色锁扣夹体，质量明显高于泛采样。
- 唯一保留的钛夹是误标，不能纳入钛夹训练真值。

### 新 high-precision 数据集

路径：`datasets/surgical_clips_gpt55_vid0015min_high_precision_v1`

来源：

- `datasets/surgical_objects_gpt55_v1`
- `datasets/surgical_objects_gpt55_clips_dense_v1`
- `datasets/cholec80_gpt55_clip_phase_v1`
- `datasets/vid001_5min_gpt55_clipfocused_probe`

筛选：

- `--white-hemolok-only`
- Hem-o-lok 置信度 >= 0.72
- 钛夹置信度 >= 0.68
- 正样本最小间隔：2 秒

结果：

- 图片：150
- 正样本图片：50
- 负样本图片：100
- Hem-o-lok：64
- 钛夹：14
- 审计拼图：`datasets/surgical_clips_gpt55_vid0015min_high_precision_v1/audit_sheet.jpg`

判断：

- Hem-o-lok 样本显著增加，但仍然多来自单个 VID001 时间段，域内多样性不足。
- 钛夹仍只有 14 个，而且部分质量偏弱。
- 这套数据可以继续作为实验集，不是最终训练集。

### 新 YOLO 试训结果

训练集：`datasets/surgical_clips_gpt55_vid0015min_high_precision_v1`

模型路径：`models/clip_detector/yolo_surgical_clips_gpt55_vid0015min_high_precision_v1/weights/best.pt`

结果：

- Early stop：epoch 50
- Best：epoch 25
- overall precision：0.678
- overall recall：0.125
- overall mAP50：0.128
- overall mAP50-95：0.0706
- Hem-o-lok precision：0.356
- Hem-o-lok recall：0.250
- Hem-o-lok mAP50：0.255
- Hem-o-lok mAP50-95：0.141
- 钛夹 precision：1.000
- 钛夹 recall：0.000
- 钛夹 mAP50：0.000

预测审计图：`runs/detect/runs/detect/audit/yolo_surgical_clips_gpt55_vid0015min_high_precision_v1_val_sheet.jpg`

结论：

- Hem-o-lok 比上一轮明显改善，但 recall 仍过低。
- 预测图显示模型会在白色夹附近产生低置信候选和重复框，尚不能作为稳定专家。
- 钛夹仍不可用。
- 这个权重不应接入主流程。

## 后续建议

1. Hem-o-lok：继续围绕 VID001/VID002 和完整手术视频里真实白色夹闭段做局部密采，避免只堆连续近重复帧。
2. 钛夹：继续使用 Cholec80 和其它真实钛夹片段，但必须做更严格的人工/GPT 复核；现有 14 个钛夹框不足以训练。
3. Colored polymer clip：不要再混入 `hemolok_clip`；如果业务需要识别，应新增 `polymer_clip` 类。
4. 每类累计至少 300 到 500 个审计通过且跨视频多样的框后，再训练正式专家。
5. 在正式专家可用前，实时流程里仍应让外部/本地 VLM 做一次合并判断，不要让未成熟 YOLO 专家覆盖分析结果。
