# 本地夹子定位模型调研与实测

日期：2026-07-02

## 目标

评估 `nv-community/LocateAnything-3B` 以及同类开源定位模型，判断它们是否能在完整腹腔镜帧中定位 Hem-o-lok、钛夹、纱布等小目标，减少外部 GPT/VLM 对隐私数据的依赖。

重点约束：
- 不硬编码视频时间或帧。
- 不依赖文件名标签。
- 不人工裁剪 ROI 后再喂模型。
- 尽量单帧完整输入，后续可做候选框融合。

## 已完成环境

模型权重：

```text
/home/user/models/LocateAnything-3B
```

为避免影响现有 Electron/分析服务的 Python 环境，`LocateAnything-3B` 使用隔离依赖目录覆盖 `transformers==4.57.1`：

```text
/home/user/models/locateanything_pydeps
```

原因：当前项目 venv 中的 `transformers==5.10.1` 与 LocateAnything remote code 不兼容，会报：

```text
LocateAnythingPreTrainedModel._check_and_adjust_attn_implementation() got an unexpected keyword argument 'allow_all_kernels'
```

补充依赖已安装到项目 venv：

```text
decord==0.6.0
lmdb==1.7.5
peft
```

测试脚本：

```text
tmp/test_locateanything_clips.py
```

结果与可视化：

```text
tmp/locateanything_clip_test/
tmp/locateanything_clip_test/locateanything_clip_test_results.jsonl
```

运行方式：

```bash
CUDA_VISIBLE_DEVICES=1 \
PYTHONPATH=/home/user/models/locateanything_pydeps:$PYTHONPATH \
/home/user/proj/video_processor/.venv/bin/python tmp/test_locateanything_clips.py
```

## LocateAnything-3B 实测结论

模型可以在单张完整手术帧上输出 `<box>` 坐标。使用 RTX 4090 单卡、SDPA fallback，不安装 MagiAttention/flash-attn 时，单图推理约 `0.22s-1.18s`，显存可放进单张 24GB GPU。

但它更适合作为“候选区域定位器”，暂不适合直接作为最终分类器。

### 有效信号

1. 蓝紫色 Hem-o-lok 帧能被定位。

示例：

```text
tmp/locateanything_clip_test/hemolok_miccai1_2_80s__all_clips.jpg
```

结果框落在蓝紫色聚合物夹区域，位置可用于后续复核。

2. 疑似钛夹/小白色短条目标能被找出。

短正向 prompt：

```text
Locate all small thin metal ligating clips clamped on tissue.
```

示例：

```text
tmp/locateanything_clip_test/short_prompt2/titanium_video2_64s__metal_clip.jpg
tmp/locateanything_clip_test/short_prompt2/titanium_video7_88s__metal_clip.jpg
```

模型能把组织中心的小短条/高亮夹片候选框出来，比普通 VLM 直接描述更适合做小目标候选池。

3. 负例在短正向 prompt 下有所改善。

示例：

```text
tmp/locateanything_clip_test/short_prompt2/negative_video12_220s__deployed_clip.jpg
tmp/locateanything_clip_test/short_prompt2/negative_video12_220s__metal_clip.jpg
```

`deployed surgical clips` 和 `thin metal ligating clips` 没有框白色器械，说明 prompt 可部分抑制器械误检。

### 主要问题

1. 类别不稳定。

同一张蓝紫色 Hem-o-lok 帧，单独问 `titanium metal ligating clip` 时也可能框到同一批 Hem-o-lok 区域。因此 LocateAnything 的 query label 不能直接当最终类别。

2. 容易把白色器械头误认为白色 polymer/Hem-o-lok。

示例：

```text
tmp/locateanything_clip_test/short_prompt2/negative_video12_220s__polymer_clip.jpg
```

`polymer locking clips` prompt 仍会框到白色器械头。

3. 长否定 prompt 会变差。

例如把 `Do not locate clip appliers, forceps, instrument shafts...` 塞进 prompt 后，模型会把 `forceps / hooks / scissors` 等否定词也当作 ref 片段解析，反而产生杂框。Grounding 模型不适合复杂否定 prompt。

4. Video12 施夹器场景会出现大框。

示例：

```text
tmp/locateanything_clip_test/video12_clip_600s__all_clips.jpg
```

模型会框施夹器、夹闭区域，甚至给出大范围 gauze/全画面框。需要面积过滤和二次判断。

## 推荐 Prompt

优先使用短正向 prompt，不加入长否定列表。

候选夹子：

```text
Locate all small deployed surgical clips clamped on tissue.
```

钛夹候选：

```text
Locate all small thin metal ligating clips clamped on tissue.
```

Hem-o-lok / 聚合物夹候选：

```text
Locate all small thick polymer locking clips clamped on tissue.
```

不推荐：

```text
Do not locate clip appliers, forceps, instrument shafts...
```

## 接入建议

### 不建议直接替换 GPT/VLM 分类

LocateAnything-3B 的优势是定位，不是可靠区分 Hem-o-lok vs titanium。直接把它输出的 `<ref>` 当成业务结论，会继续出现 Hem-o-lok 和 titanium 之间跳变。

### 建议作为候选框前端

推荐流水线：

1. 每个分析窗口抽 2-4 帧完整图。
2. LocateAnything 用短 prompt 生成候选框。
3. 过滤异常框：
   - 过滤面积过大的框，例如超过画面 `10%-15%`。
   - 过滤贴边或覆盖器械杆的大框。
   - 过滤连续帧不稳定、只出现一次的小高光。
4. 对候选框周围做轻量裁剪，仅作为二次分类输入，不作为人工硬编码。
5. 本地 VLM 或专门分类器判断：
   - Hem-o-lok：粗、宽、聚合物、蓝紫/白/乳白、锁扣夹。
   - 钛夹：细、小、金属银灰、短 V/U/平行夹片。
   - 器械/高光/纱布/不确定。
6. 与手术 phase/动作规则融合：
   - CVS 前不能直接认定剪断胆囊管/胆囊动脉。
   - 剪断前应先有对应夹闭证据。
   - 夹闭事件应在相邻窗口合并，而不是每 5 秒重复写一次。

### 更可靠的生产路线

如果要做到医生可用的稳定性，建议采集少量标注数据后训练小检测器：

类别：

```text
hemolok_clip
titanium_clip
clip_applier
gauze
electrocautery_hook
scissors
forceps
specular_highlight
```

优先方案：
- 用 LocateAnything / GroundingDINO 自动生成初始候选框。
- 人工快速修正 200-500 张关键帧。
- 微调 YOLO/RT-DETR 类小目标检测器。
- 线上由检测器实时跑，VLM 只做低频复核和摘要。

这样比完全依赖开源通用 VLM 更稳定，也更容易控制误检。

## 其他模型调研

### Grounding DINO / GroundingDINO 1.5

优势：
- 成熟的开词表检测模型。
- 输入图像和文本 prompt，输出候选框。
- 适合自动标注和快速建立初始数据集。

风险：
- 通用模型对腹腔镜小目标、反光、血液/组织背景可能不稳。
- 对 Hem-o-lok vs titanium 的材质分类仍需要二次分类或微调。

### Florence-2 large/ft

优势：
- 轻量，支持 object detection / phrase grounding。
- 部署成本低，适合作为快速 baseline。

风险：
- 对微小手术夹的召回通常不如专门 detector。
- 更适合辅助验证，不建议直接作为主路径。

### OWLv2 / OWL-ViT

优势：
- 开词表检测老牌 baseline，部署简单。

风险：
- 对小物体和强域外场景通常较弱，不作为首选。

### 专门微调 YOLO/RT-DETR

优势：
- 实时性最好。
- 类别稳定、可控，便于上线。
- 对 24GB GPU 压力小，可与现有 YOLO/专家模型并行。

风险：
- 需要标注数据。

## 当前判断

`LocateAnything-3B` 可以保留为本地候选定位模块，但不能单独决定“这是 Hem-o-lok 还是钛夹”。

近期最优策略：

```text
LocateAnything 候选框
  -> 面积/位置/时序过滤
  -> 本地 VLM 或小分类器判断材质和器械
  -> phase/动作规则融合
  -> 写入右侧分析和关键事件
```

中期最优策略：

```text
用 LocateAnything/GroundingDINO 辅助标注
  -> 人工修正小样本
  -> 微调 YOLO/RT-DETR clip detector
  -> VLM 只做低频复核和临床语言总结
```
