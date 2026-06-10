# 数据集 & 评估参考文档

> 用途：批量评估 offline pipeline 的输入 / 真值 / 评估代码索引。
> 涵盖 Cholec80 和 CholecT50 两个数据集。

---

## 1. 数据集存放位置

### 1.1 Cholec80（80 个完整手术视频，25 FPS）

```
/data3/tos_copy/cholec80/cholec80/
├── videos/                    # video01.mp4 ... video80.mp4 (25 FPS, 854×480)
├── phase_annotations/         # video{NN}-phase.txt   每帧 phase（25 FPS）
├── tool_annotations/          # video{NN}-tool.txt    每秒 tool 二值（1 FPS）
├── frames/                    # 提取好的帧（不一定全有，验证用）
├── frames_1fps/               # 1 FPS 子集（验证用）
└── README.txt
```

- 视频命名：`video01.mp4` ... `video80.mp4`（80 个）
- 编号 1–40 是 finetuning 子集，41–80 是 evaluation 子集（EndoNet 划分）

### 1.2 CholecT50（50 个手术，**已采样为 1 FPS PNG**）

```
/data3/tos_copy/CholecT50/CholecT50/
├── videos/                    # VID01/ VID02/ ... 每个目录下 000000.png 起的 PNG
├── labels/                    # VID{NN}.json   phase + 100 类 IVT triplet
├── label_mapping.txt          # 100 个 IVT 三元组与 I/V/T 的映射
├── README.md
└── LICENSE
```

- 视频命名：`VID01`, `VID02`, `VID04`, `VID05`, ... （编号不连续，跳过部分）
- 每个 `VIDxx/` 下是 PNG 序列，**已经是 1 FPS**（VID01 有 1734 张 = 1734 秒 ≈ 28.9 分钟）
- **VID 编号与 Cholec80 video 编号同一台手术**（README 明确：「video IDs are consistent across datasets」）。例：`CholecT50/VID01` ≡ `Cholec80/video01`，PNG 第 i 张 = Cholec80 源帧 i×25

---

## 2. 真值格式与解析方法

### 2.1 Cholec80 — Phase

**文件**：`phase_annotations/video{NN}-phase.txt`

**格式**（tab 分隔，含表头）：
```
Frame   Phase
0       Preparation
1       Preparation
...
43325   GallbladderRetraction
```

- **每帧一行**（视频 25 FPS，video01 共 43326 行）
- Phase 类（7）：`Preparation, CalotTriangleDissection, ClippingCutting, GallbladderDissection, GallbladderPackaging, CleaningCoagulation, GallbladderRetraction`
- 我们 phase_expert 输出 `lowercase_with_underscores` → 评估时对两侧做 `lower().replace("_","")` 归一化对齐

**解析代码**：`scripts/eval_cholec80.py::load_phase_gt(video_id) -> List[str]`

### 2.2 Cholec80 — Tool

**文件**：`tool_annotations/video{NN}-tool.txt`

**格式**（tab 分隔，含表头）：
```
Frame   Grasper Bipolar Hook    Scissors        Clipper Irrigator       SpecimenBag
0       1       0       0       0       0       0       0
25      1       0       0       0       0       0       0
50      1       0       0       0       0       0       0
...
```

- **1 FPS 采样**（Frame 列是源帧索引：0, 25, 50, ...）
- 7 个工具列，0/1 二值
- 与 offline pipeline `--sample-fps 1` 输出**天然帧对齐**（pred 帧 i ↔ src 帧 i×25 ↔ GT row i）
- YOLO 类名 → GT 列名映射（注意 YOLO 多了 `snare`，评估时忽略）：
  ```python
  YOLO_TO_GT = {"grasper":"Grasper","bipolar":"Bipolar","hook":"Hook",
                "scissors":"Scissors","clipper":"Clipper","irrigator":"Irrigator",
                "specimen_bag":"SpecimenBag"}
  ```

**解析代码**：`scripts/eval_cholec80.py::load_tool_gt(video_id) -> (indices, [N,7] np.int8)`

### 2.3 CholecT50 — Phase + Triplet IVT（合一文件）

**文件**：`labels/VID{NN}.json`

**JSON 结构**：
```json
{
  "annotations": {
    "0":  [[7, 0, 1, -1, -1, -1, -1, 0, 0, 1, -1, -1, -1, -1, 0]],
    "1":  [[7, 0, 1, -1, -1, -1, -1, 0, 0, 1, -1, -1, -1, -1, 0]],
    "20": [[7, 0, 1, -1, -1, -1, -1, 0, 0, 1, -1, -1, -1, -1, 0],
           [96, 2, 1, -1, -1, -1, -1, 9, 14, 1, -1, -1, -1, -1, 0]],
    ...
  }
}
```

- 键是 PNG 帧号（字符串），值是该帧所有 detection 行的列表
- 每行 **15 个字段**，关键位：
  - **`row[0]`** = IVT 类 id（0–99，**`-1` 表示无 triplet**）
  - `row[1]` = instrument id（0–5）
  - `row[2]` = verb id（0–9）
  - `row[3]` = target id（0–14）
  - `row[14]` = **phase id（0–6）** ← 全行共享，取第一行即可
- 每帧的 IVT 真值集合 = `{int(r[0]) for r in rows if int(r[0]) >= 0}`（多标签）

**Phase id → 我们模型的标签名映射**：
```python
PHASE_ID_TO_NAME = {
    0: "preparation",
    1: "calot_triangle_dissection",
    2: "clipping_cutting",
    3: "gallbladder_dissection",
    4: "gallbladder_packaging",
    5: "cleaning_coagulation",
    6: "gallbladder_retraction",
}
```

**IVT label 字符串**（用于人类阅读）：来自
`/data4/jj/proj/surg_agent/triplet_expert/datasets/cholect50_triplet/class_mappings.json`
里的 `triplets[ivt_id]`，形如 `[hook]-[coagulate]-[liver]`。

**解析代码**：`scripts/eval_cholect50.py::load_gt(video_id) -> Dict[int, {"phase": int, "ivt": set[int]}]`

### 2.4 找到这些真值的过程（备注）

1. **Cholec80**：`README.txt` 直接说明了 phase / tool 标注文件位置和格式
2. **CholecT50**：
   - JSON 结构靠观察样本推断（README 没写每个字段含义）
   - Phase 字段（`row[14]`）通过对比 VID01 的 phase 分布和已知 Cholec80 video01 的 phase 序列**反向验证**：22/652/214/583/98/73/92 ≈ Cholec80 的 21/652/214/583/98/73/93，吻合 → 确认 row[14] 是 phase id 0–6
   - IVT 字段（`row[0]`）通过 `label_mapping.txt` 确认（首列为 IVT，0–99）

---

## 3. 评估代码索引

所有评估脚本都在 `offline_pipeline/scripts/`，都接受 `--session <pipeline_run_dir>` 和 `--out <json_report>` 参数。

### 3.1 Cholec80 评估

**脚本**：`offline_pipeline/scripts/eval_cholec80.py`

```bash
cd /data2/jj/proj/video_processor
python -m offline_pipeline.scripts.eval_cholec80 \
  --session offline_pipeline/jobs/<session_id> \
  --video-id 1 \
  --out offline_pipeline/jobs/<session_id>/eval_cholec80.json
```

输出：
- **Phase**：帧级准确率 + 7 类 per-class accuracy + top confusions
- **Tool**：7 类 multi-label P/R/F1/acc + macro 指标 + TP/FP/FN
- 工具评估**忽略 YOLO 的 snare 类**（Cholec80 不含）

### 3.2 CholecT50 评估

**脚本**：`offline_pipeline/scripts/eval_cholect50.py`

```bash
python -m offline_pipeline.scripts.eval_cholect50 \
  --session offline_pipeline/jobs/<session_id> \
  --video-id VID01 \
  --ivt-threshold 0.5 \
  --topk 5 \
  --out offline_pipeline/jobs/<session_id>/eval_cholect50.json
```

输出：
- **Phase**：与 Cholec80 同套
- **Triplet IVT**：
  - **mAP-IVT**：sklearn `average_precision_score`，每类 AP 后求平均（仅在 GT 中出现的类，避免 0 支持）
  - **F1@threshold**：multi-label 二值化后 P/R/F1
  - **Top-K precision/recall**：每帧取 Top-K 预测，对比 GT 集合
  - Top-10 高频类的 per-class AP

> ⚠️ 该脚本**依赖 `frames_results.json` 中包含 `ivt_probs` 字段**（100 维 sigmoid 概率）。这要求用 2026-04-15 之后的 pipeline 版本（`triplet_expert.infer_paths` 已改成返回 `(top-K, full_probs)`）。

### 3.3 视频合成（仅 CholecT50 用）

**脚本**：`offline_pipeline/scripts/build_cholect50_video.py`

```bash
python -m offline_pipeline.scripts.build_cholect50_video \
  --vid-dir /data3/tos_copy/CholecT50/CholecT50/videos/VID01 \
  --out /tmp/CholecT50_VID01.mp4 \
  --fps 1
```

CholecT50 的 PNG 已经是 1 FPS，所以编码 fps=1 + pipeline `--sample-fps 1` → 每张 PNG 1:1 进入推理。

---

## 4. 端到端流程模板（单视频）

### 4.1 Cholec80

```bash
cd /data2/jj/proj/video_processor

# Pipeline
CUDA_VISIBLE_DEVICES=7 /data2/jj/miniconda3/envs/vllm/bin/python \
  -m offline_pipeline.scripts.run_offline \
  --video /data3/tos_copy/cholec80/cholec80/videos/video01.mp4 \
  --sample-fps 1 --window 15 --skip-batch -v

# 拿到 session_id 后评估
SID=<session_id>
/data2/jj/miniconda3/envs/vllm/bin/python \
  -m offline_pipeline.scripts.eval_cholec80 \
  --session offline_pipeline/jobs/$SID --video-id 1 \
  --out offline_pipeline/jobs/$SID/eval_cholec80.json
```

### 4.2 CholecT50

```bash
# 1) 合成 mp4
/data2/jj/miniconda3/envs/vllm/bin/python \
  -m offline_pipeline.scripts.build_cholect50_video \
  --vid-dir /data3/tos_copy/CholecT50/CholecT50/videos/VID01 \
  --out /tmp/CholecT50_VID01.mp4 --fps 1

# 2) Pipeline
CUDA_VISIBLE_DEVICES=7 /data2/jj/miniconda3/envs/vllm/bin/python \
  -m offline_pipeline.scripts.run_offline \
  --video /tmp/CholecT50_VID01.mp4 \
  --sample-fps 1 --window 15 --skip-batch -v

# 3) 评估
SID=<session_id>
/data2/jj/miniconda3/envs/vllm/bin/python \
  -m offline_pipeline.scripts.eval_cholect50 \
  --session offline_pipeline/jobs/$SID --video-id VID01 \
  --out offline_pipeline/jobs/$SID/eval_cholect50.json
```

---

## 5. 批量评估的设计建议（待实现）

未来批量跑应该写一个 driver 脚本（建议位置 `offline_pipeline/scripts/batch_eval.py`），输入：

```yaml
- dataset: cholec80
  video_ids: [1, 2, 3, ..., 10]   # or "all"
- dataset: cholect50
  video_ids: [VID01, VID02, VID04, ...]
```

driver 应该：

1. 对每个视频：合成 mp4（仅 CholecT50）→ 跑 pipeline → 拿 session_id → 跑对应评估
2. 把所有 eval JSON 收集起来，输出汇总：
   - Phase: 每视频准确率 + 跨视频 mean / std
   - Cholec80 Tool: 每视频每工具 F1 + 全数据集汇总（micro/macro）
   - CholecT50 Triplet: 每视频 mAP-IVT + F1@0.5，跨视频取 mean
3. **复用 GPU 7 上的预热权重**：让 driver 在同一进程里跑多视频，避免每次重载（当前 `run_offline` CLI 是冷启动；批量时应改成 import `run_pipeline` 直接调用）
4. 关注**重复评估的视频**：CholecT50 VIDxx 与 Cholec80 videoxx 是同一台手术，phase 任务的两个数据集结果应该高度一致；triplet 任务只在 CholecT50 有
5. **GPU 调度**：当前 cuda:7 单卡，未来批量时可以按视频 round-robin 多卡（修改 `CUDA_VISIBLE_DEVICES`）

---

## 6. 已知限制与坑

1. **YOLO 的 specimen_bag 类全程 0 检测**（详见 `specimen_bag_handoff_to_gemini.md`）—— 评估时该类 F1=0 是模型问题，不是数据问题。计划交给 Gemini 兜底
2. **YOLO 的 snare 类不在 Cholec80**：评估时直接忽略
3. **CholecT50 VID01 ≡ Cholec80 video01**：批量评估汇总时不要把同一台手术的 phase 结果重复计入"独立样本"
4. **mAP-IVT 在单视频上偏低（VID01 = 62.7%）**：handoff 报的 83.8% 是官方测试集；单视频只覆盖 100 类中的 22 类，分母小
5. **LAM-Lite 训练集是否包含本次评估视频未知**：批量评估前需要查一下 CholecT50 官方 split（`https://arxiv.org/abs/2204.05235`），避免在训练集上"测试"
6. **`ivt_probs` 字段只在 2026-04-15 之后的 pipeline 输出里有**：早期 session 重跑评估前需先重跑 pipeline
