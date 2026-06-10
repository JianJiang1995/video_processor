# Run Log — video01.mp4 (Cholec80)

## 设置

| 项 | 值 |
|---|---|
| **视频** | `/data3/tos_copy/cholec80/cholec80/videos/video01.mp4` |
| **视频元信息** | 25 FPS / 43326 帧 / 28.9 分钟 / 854×480 |
| **采样 FPS** | 1（全程预计 ~1733 帧） |
| **窗口长度** | 15 秒 → 预计 ~116 个窗口 |
| **GPU** | `cuda:7`（A100-80GB；R1 进程已 kill，PID 1057551） |
| **专家执行模式** | **3 专家并发**（ThreadPoolExecutor max_workers=3） |
| **Gemini** | 关闭（`--skip-batch`） |
| **Embedding / MySQL** | 跳过（占位 stub） |
| **Python 环境** | `/data2/jj/miniconda3/envs/vllm` |

## 关键代码改动（与初版骨架的差异）

1. **去掉 ffmpeg 依赖** — `extract_frames` 改用 OpenCV `VideoCapture`，按 stride 取帧（系统无 ffmpeg）
2. **3 专家并发** — `run_experts` 用 `ThreadPoolExecutor(3)`，每线程独立加载并跑一个专家
3. **Triplet 概率** — 改成 `sigmoid`（多标签）而非 softmax；和 `eval_lam_on_our_val.py:307` 一致
4. **Triplet label_mapping** — 解析 `class_mappings.json`（CholecT50 官方），输出形如 `[grasper]-[grasp]-[gallbladder]`
5. **sessions_root** — 改成绝对路径 `offline_pipeline/jobs`

## 运行命令

```bash
cd /data2/jj/proj/video_processor
/data2/jj/miniconda3/envs/vllm/bin/python -m offline_pipeline.scripts.run_offline \
  --video /data3/tos_copy/cholec80/cholec80/videos/video01.mp4 \
  --sample-fps 1 --window 15 --skip-batch -v
```

## 结果

### 总耗时

| 阶段 | 耗时 | 备注 |
|---|---|---|
| 抽帧 (OpenCV, 25→1 FPS) | **52 s** | 43326 src → 1734 输出，I/O bound（/data3 tos mount） |
| 3 专家并发推理 | **24 s** | 1734 帧，GPU 7，3 线程同时加载+推理 |
| 聚合 + 落盘 | <1 s | 116 个 15s 窗口 |
| **总计** | **~80 s** | 处理 29 分钟视频 |

### 产物（`offline_pipeline/jobs/b4009181/`）

```
frames/                    1734 张 JPG
frames_results.json        1.4 MB  每帧 tools/phase_label/phase_probs/triplets
windows.json               169 KB  116 个窗口
embeddings.json            11 KB   stub（无 Gemini，无文本）
gemini_summaries.json      2 B     空（已跳过）
job.json                   status=done
```

### Phase 序列抽样（Gaussian σ=3 平滑后）

| win | 时间段 | dominant_phase | 主工具 | 代表 triplet |
|---|---|---|---|---|
| 0 | 0–15 s | **preparation** | grasper | [bipolar]-[coagulate]-[liver] |
| 20 | 300–315 s | **calot_triangle_dissection** | irrigator, grasper | [irrigator]-[irrigate]-[liver] |
| 50 | 750–765 s | **clipping_cutting** | clipper, grasper | [bipolar]-[coagulate]-[liver] |
| 80 | 1200–1215 s | **gallbladder_dissection** | hook, grasper | [hook]-[coagulate]-[liver] |
| 115 | 1725–1733 s | **gallbladder_retraction** | grasper, hook | [bipolar]-[dissect]-[cystic_duct] |

✅ Phase 时序与 Cholec80 胆囊切除标准流程完全吻合（prep → calot → clip → dissection → retraction），说明 ResNet-18 phase + σ=3 Gaussian 平滑正常工作。
✅ YOLO 工具检测和阶段匹配（clipping 期主要是 clipper，dissection 期主要是 hook）。
⚠️ Triplet 顶类置信度偏高（常 >0.99），多标签 sigmoid 输出显示模型过度自信，后续如需用作主要信号要加阈值 / top-K + IoU 去重。

### 运行期间的问题与修正

1. **ultralytics 改 `CUDA_VISIBLE_DEVICES`**：首次运行时 YOLO 线程偷偷把环境变量改成本线程 device，导致 Phase 线程 `cuda:7` → `invalid device ordinal`。  
   **解决**：外部用 `CUDA_VISIBLE_DEVICES=7`，config 里改用 `cuda:0`。切卡时只改环境变量。
2. **缺 ffmpeg**：改成 OpenCV `VideoCapture` 直接按 stride 取帧。
3. **triplet 概率**：原骨架用 softmax，实际 LAM-Lite 是多标签 sigmoid 头（见 `eval_lam_on_our_val.py:307`），已修正。
4. **triplet label 映射**：原骨架期待 `LAM/label_mapping.txt` 不存在，改用 `cholect50_triplet/class_mappings.json`，输出形如 `[tool]-[verb]-[target]`。

## Cholec80 真值评估（video01）

数据源：
- Phase 真值：`cholec80/phase_annotations/video01-phase.txt`（25 FPS，43326 行，7 类）
- Tool 真值：`cholec80/tool_annotations/video01-tool.txt`（**1 FPS**，1733 行，7 类二值标签）

Tool GT 的 1 FPS 采样和我们的输出**天然帧对齐**（都对应源帧 0, 25, 50, ...）。

评估脚本：`offline_pipeline/scripts/eval_cholec80.py`
完整 JSON 报告：`jobs/b4009181/eval_video01.json`

### Phase — 帧级准确率 **99.88%** （n=1734）

| 阶段 | 准确率 | 帧数 |
|---|---|---|
| preparation | 100.00% | 21 |
| calot_triangle_dissection | 100.00% | 652 |
| clipping_cutting | 100.00% | 214 |
| gallbladder_dissection | 100.00% | 583 |
| gallbladder_packaging | 98.98% | 98 |
| cleaning_coagulation | 100.00% | 73 |
| gallbladder_retraction | 98.92% | 93 |

✅ **超过 handoff 文档承诺的 98.5%**（Gaussian σ=3 + ResNet-18）。
全视频仅 2 帧错分：`packaging→dissection` 1 帧、`retraction→cleaning_coagulation` 1 帧，都是相邻阶段边界的合理误差。

### Tool — 多标签二值分类（n=1733，1 FPS 与真值完全对齐）

| 工具 | Precision | Recall | F1 | Accuracy | 正例数 | TP | FP | FN |
|---|---|---|---|---|---|---|---|---|
| Grasper | 80.74% | 99.67% | **89.21%** | 87.48% | 900 | 897 | 214 | 3 |
| Bipolar | 95.20% | 100.00% | **97.54%** | 99.65% | 119 | 119 | 6 | 0 |
| Hook | 98.73% | 99.87% | **99.30%** | 99.37% | 781 | 780 | 10 | 1 |
| Scissors | 86.36% | 100.00% | **92.68%** | 99.83% | 19 | 19 | 3 | 0 |
| Clipper | 97.09% | 100.00% | **98.52%** | 99.83% | 100 | 100 | 3 | 0 |
| Irrigator | 95.20% | 100.00% | **97.54%** | 99.65% | 119 | 119 | 6 | 0 |
| **SpecimenBag** | **0.00%** | **0.00%** | **0.00%** | 93.77% | 108 | 0 | 0 | 108 |
| **Macro** | **79.05%** | **85.65%** | **82.11%** | **97.08%** | — | — | — | — |

观察：
- ✅ 6/7 个工具 F1 > 89%，其中 Hook/Clipper/Bipolar/Irrigator 都 ≥ 97%。
- ⚠️ **Grasper 假阳偏多**（FP=214）：YOLO 把一些非工具区域误判为 grasper。可以考虑提高 grasper 类的 conf 阈值，或加 NMS。
- ❌ **SpecimenBag 全程零检测**（108 GT 正例 / 1734 帧 = 6%）：检查发现 YOLO 模型类表里有 `specimen_bag`（class_id=7），但 conf>0.25 时一次都没触发。说明该类训练偏弱或在 video01 的视觉特征上严重欠拟合。后续可以单独跑一次 conf=0.05 的 ablation 验证。
- ⚠️ 评估时把 YOLO 的额外类 `snare` 忽略（Cholec80 没有该工具），不影响其他指标。

### 后续

- 把 Grasper FP 的代表帧导出，肉眼验证是否真的误检
- SpecimenBag conf 阈值 ablation
- 跑 video02..video10 看指标是否稳定
- Embedding 对接（复用 `video_stream_app/backend/services/embedding_service.py`）
- MySQL 写入（schema 加 `triplets`, `expert_source` 两列）
- Triplet 阈值调参（当前 top-K=3 + sigmoid 会全部接近 1）
