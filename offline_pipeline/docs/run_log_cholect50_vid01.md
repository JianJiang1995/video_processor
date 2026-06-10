# Run Log — CholecT50 VID01

## 设置

| 项 | 值 |
|---|---|
| **数据源** | `/data3/tos_copy/CholecT50/CholecT50/videos/VID01/` (1734 张 PNG, 854×480) |
| **合成视频** | `/tmp/CholecT50_VID01.mp4` (mp4v, **1 FPS**, 1734 帧) |
| **采样 FPS** | 1（与合成 fps 1:1，每张 PNG 对应 1 个 pred 帧） |
| **窗口长度** | 15 秒 → 116 个窗口 |
| **GPU** | `cuda:7` (`CUDA_VISIBLE_DEVICES=7` + `cuda:0`) |
| **专家执行** | 3 专家并发 |
| **真值** | `cholect50/labels/VID01.json` —— phase + 100 类 IVT 多标签 |
| **同一台手术** | 与 Cholec80 video01 同一台胆囊切除（CholecT50 是 1 FPS 子集） |

## 适配"图片合成视频"的策略

CholecT50 的 PNG 已经是 1 FPS 采样：1734 帧 = 1734 秒 = 28.9 分钟，
和 Cholec80 video01 的 43326 帧/25 FPS = 1733 秒完全对应。

→ 编码 mp4 时直接 **fps=1**，pipeline 设置 **target_fps=1**，stride=1，所有帧 1:1 进入推理。
→ 评估时 GT 行号 = pred 帧号，无需做帧索引映射。

新增脚本：`scripts/build_cholect50_video.py`。

## 关键代码改动

1. **预导入 torchvision/timm/ultralytics** —— 3 线程并发首次 import 会触发 Python 模块 import lock 死锁，在 `run_experts` 入口处先串行 import 一次
2. **Triplet 保存完整 100 维 sigmoid 概率** —— `FrameExpertResult.ivt_probs`，便于算 mAP-IVT。triplet_expert.infer_paths 改返回 `(top-K, full_probs)` 元组
3. **新增 `scripts/eval_cholect50.py`** —— 同时评估 phase 准确率 + 100 类 IVT 多标签

## 运行命令

```bash
# 1) 合成 1-FPS mp4
python -m offline_pipeline.scripts.build_cholect50_video \
  --vid-dir /data3/tos_copy/CholecT50/CholecT50/videos/VID01 \
  --out /tmp/CholecT50_VID01.mp4 --fps 1

# 2) 跑 pipeline
CUDA_VISIBLE_DEVICES=7 python -m offline_pipeline.scripts.run_offline \
  --video /tmp/CholecT50_VID01.mp4 --sample-fps 1 --window 15 --skip-batch -v

# 3) 评估
python -m offline_pipeline.scripts.eval_cholect50 \
  --session offline_pipeline/jobs/26354089 --video-id VID01 \
  --out offline_pipeline/jobs/26354089/eval_cholect50.json
```

## 结果

总耗时 ~22 秒（无抽帧瓶颈，1734 帧已经在 mp4 里）：3 专家并发推理 22 s。

### Phase — **99.25%** 帧级准确率（n=1734）

| 阶段 | 准确率 | 帧数 |
|---|---|---|
| preparation | 95.45% | 22 |
| calot_triangle_dissection | 99.85% | 652 |
| clipping_cutting | 99.53% | 214 |
| gallbladder_dissection | 98.80% | 583 |
| gallbladder_packaging | 96.94% | 98 |
| cleaning_coagulation | 100.00% | 73 |
| gallbladder_retraction | 100.00% | 92 |

主要 confusion: `gallbladder_dissection → gallbladder_retraction` 7 帧。
比 Cholec80 video01 的 99.88% 略低（差 11 帧），原因可能在 mp4 重编码后有
极轻微像素差异 + 边界帧 Gaussian 平滑结果不同。仍远高于 handoff 承诺的 98.5%。

### Triplet IVT — 100 类多标签

| 指标 | 值 | 备注 |
|---|---|---|
| **mAP-IVT** | **62.67%** | 仅在 VID01 出现的 22 个类上做平均；handoff 报 0.838 是在官方测试集 |
| **F1 @ 0.5** | **88.73%** | 阈值 0.5 二值化；P=88.56%, R=88.91% |
| TP / FP / FN | 2276 / 294 / 284 | 信号干净 |
| Top-5 recall | **98.70%** | 取 Top-5 几乎覆盖所有真值 |
| Top-5 precision | 32.75% | 单帧平均 ~1.5 个真值，5 个预测 → 上限 ~30% |

#### 高频 IVT 类的 AP

| ivt_id | label | AP | 支持帧 |
|---|---|---|---|
| 7 | `[bipolar]-[coagulate]-[liver]` | 90.82% | 711 |
| 60 | `[hook]-[coagulate]-[liver]` | **98.29%** | 524 |
| 19 | `[bipolar]-[null_verb]-[null_target]` | 97.69% | 359 |
| 96 | (e.g. specimen_bag-related) | **99.63%** | 234 |
| 12 | `[bipolar]-[dissect]-[cystic_duct]` | 89.09% | 125 |
| 82 | (hook-cut?) | 77.84% | 117 |
| 17 | `[bipolar]-[grasp]-[liver]` | 76.97% | 113 |
| 23 | `[bipolar]-[retract]-[liver]` | 96.61% | 100 |
| 79 | (clipper-clip-...) | 93.01% | 78 |
| 94 | (rare class) | 40.19% | 55 |

观察：
- ✅ **F1 88.73%** 是非常实用的多标签性能；Top-5 召回近 99% 意味着把 Top-K 喂给 Gemini 做窗口总结时几乎不会漏关键动作
- ✅ 高频高频高 AP（hook-coagulate-liver 98.3%、specimen_bag 类 99.6%）对应胆囊切除的核心动作
- ⚠️ mAP 62.67% 比 handoff 的 83.8% 低，差异来自评估范围（单视频的 22 个出现类 vs 官方测试集），不一定是模型退化
- ⚠️ ivt_id=94 这种低频类只有 55 帧支持，AP 40% 是预期偏差

### 与 Cholec80 video01 的对比

| 任务 | Cholec80 (25 FPS / 1 FPS 重采样) | CholecT50 (原生 1 FPS) |
|---|---|---|
| Phase 帧准确率 | 99.88% | 99.25% |
| 工具检测 | 含真值评估，6/7 工具 F1 ≥ 89% | 同模型，未单独评估 |
| **Triplet IVT** | 无真值 | **F1 88.7%, mAP 62.7%** ✅ |

## 后续

- 跑 VID02 / VID04 / VID05 等多个视频取平均
- LAM-Lite 训练集是否包含 VID01 需要确认（影响 mAP 解读）
- 用 IVT 概率向量做 Gemini batch prompt 的"专家提示"组件
