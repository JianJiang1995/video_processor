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

## 本地 GLM-V 测试结果

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
