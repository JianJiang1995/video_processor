# 本地 VLM 手术夹识别 Prompt 测试

## Prompt 文件

- `prompts/surgical_clip_detection_prompt_zh.txt`

该 prompt 面向完整腹腔镜帧输入，不依赖裁剪。核心流程是先在完整视野中定位候选夹子，再根据颜色、材质、形状和空间关系判断 Hem-o-lok 或钛夹。

## 视觉判别要点

Hem-o-lok / 聚合物锁扣夹：
- 常见蓝紫色、深蓝色、紫色，也可能是白色、乳白色或绿色。
- 材质偏塑料，金属反光弱。
- 形态更粗，常见两条近似平行夹臂、倒 V 形、U 形、短条状或门形结构。
- 常夹在胆囊管或胆囊动脉上，放置后相对组织稳定。

钛夹 / 金属夹：
- 银灰色、银白色或灰白色，局部金属高光明显。
- 通常比 Hem-o-lok 更细小，呈短线、V 形、U 形或微弯金属片。
- 可见多枚平行短金属夹片时，应优先判定为钛夹。
- 如果银白色 U 形或 V 形金属夹片正在被钛夹钳压闭在管状结构上，即使部分遮挡，也判定为钛夹。

负例：
- 白色施夹器头、白色电凝钩、强反光、烟雾、纱布、组织高光不算 Hem-o-lok。
- 剪刀刃、抓钳齿、钳口、吸引器口、电凝钩尖端不算钛夹。

## 参考帧测试结果

模型：`GLM-4.6V-Flash-AWQ-4bit`

输入：单张完整帧，不裁剪，`temperature=0`，`enable_thinking=false`，JSON 输出。

| 样本 | 期望 | 输出 | 结果 |
|---|---:|---:|---:|
| `hemolok_miccai1_3_144s.jpg` | hemolok | hemolok | 通过 |
| `hemolok_miccai1_2_80s.jpg` | hemolok | hemolok | 通过 |
| `hemolok_miccia1_1_80s.jpg` | hemolok | hemolok | 通过 |
| `titanium_video2_64s.jpg` | titanium | titanium | 通过 |
| `titanium_video7_88s.jpg` | titanium | titanium | 通过 |
| `video12_clip_600s.jpg` | titanium | titanium | 通过 |
| `negative_video12_220s.jpg` | none | none | 通过 |

平均单帧完整图延迟约 `1.54s`。当前结论是：在该 prompt 下，本地 GLM-V 可以在完整帧上区分蓝紫色 Hem-o-lok、银色钛夹和明确负例。

## 整段视频自动扫描测试

为避免硬编码某一帧或某个时间点，额外做了整段视频扫描：
- 固定每 `10s` 取一个窗口。
- 每个窗口取 `+0s/+2s/+4s/+6s` 四张完整帧。
- 不裁剪，不传入样本名、期望类别或已知事件时间。
- 只把图像和通用 prompt 发给本地 VLM。

### VID001 实际手术测试

视频：`/home/user/proj/video_processor/test_data/2024-12-24_225315_VID001.mp4`

结果文件：
- `tmp/local_vlm_clip_scan/2024-12-24_225315_VID001_glm_clip_scan_10s_summary.json`
- `tmp/local_vlm_clip_scan/contact_sheets/2024-12-24_225315_VID001_glm_clip_scan_10s_bbox_confirmed.jpg`

统计：
- 总窗口：`65`
- 平均窗口延迟：`1.43s`
- 高置信夹子窗口：`8`
- uncertain 候选窗口：`8`

高置信窗口集中在 `310s-450s`。人工查看截图后，这些窗口确实能看到白色或银白色夹子结构，说明模型不是凭空报夹子。但同一批白色夹在不同窗口会被判为 Hem-o-lok 或 titanium，类型细分还不稳定。

### 蓝紫色 Hem-o-lok 专项视频

视频：`/home/user/proj/video_processor/test_data/夹子视频/miccai1-3/miccai1-3-1.mp4`

统计：
- 总窗口：`19`
- 平均窗口延迟：`1.80s`
- 高置信 Hem-o-lok 窗口：`4`

人工查看 bbox 图后，模型能定位到蓝紫色聚合物夹，Hem-o-lok 判断较稳定。

### 钛夹专项视频

视频：`/home/user/proj/video_processor/test_data/夹子视频/video2-1/video2-1-1.mp4`

统计：
- 总窗口：`11`
- 平均窗口延迟：`1.80s`
- 高置信 titanium 窗口：`4`

人工查看 bbox 图后，模型能定位到银白色金属夹片，钛夹判断在明确金属夹场景下可用。

## 当前结论

可以实现：
- 完整帧输入下识别蓝紫色 Hem-o-lok。
- 完整帧输入下识别银白色钛夹。
- 作为实时分析的额外 VLM 模块输出夹闭事件和候选框。

暂不应直接作为唯一最终结论：
- 白色夹子在 Hem-o-lok 和 titanium 之间仍会混淆。
- bbox 可用于辅助展示和人工复核，但不能当作像素级检测框。
- `uncertain` 只能作为候选提示，不能作为已确认夹闭事件。

推荐接入策略：
- 只把 `confidence >= 0.8` 且 `clip_type` 为 `hemolok` 或 `titanium` 的结果写入右侧分析。
- 连续相邻窗口同类命中时合并成一个夹闭事件节点。
- `uncertain` 只进入内部候选池，不直接展示为确定结论。
- 与 Triplet/phase 规则做后处理：夹闭切断阶段附近优先触发本地 VLM；剪断胆囊管或胆囊动脉前必须先有同目标夹闭证据。
