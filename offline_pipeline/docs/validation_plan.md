# Offline Pipeline — 专家链路验证计划

> 目标：在**不调用 Gemini** 的前提下，用 Cholec80 真实视频验证 3 个 Cholec 专家
> 能端到端跑通并产出合理结果。

## 0. 目标视频与资源

- **视频**：`/data3/tos_copy/cholec80/cholec80/videos/video01.mp4`
  - 25 FPS / 43326 帧 / 28.9 分钟 / 854×480 / 胆囊切除术
- **GPU**：`cuda:7`（即 "8 号 GPU"，需要先释放）
- **采样 FPS**：**1**（见 README §"FPS 选型"，Cholec80 标准协议就是 1 FPS，且 LAM-Lite
  的 10 帧 clip 在 1 FPS 下正好 = 10 秒上下文，与动作时长匹配）
- **预期产物**：
  - `jobs/{sid}/frames/` ~1733 张 JPG
  - `jobs/{sid}/frames_results.json` 每帧 tool/phase/triplet
  - `jobs/{sid}/windows.json` ~116 个 15 秒窗口
  - `jobs/{sid}/job.json` 运行状态

## 1. 环境准备

### 1.1 关闭 SurgR1 腾出 GPU 7

当前 GPU 7 被 `VLLM::EngineCore` PID 1057551（69 GB）占用。

```bash
# 1) 先确认进程
ps -p 1057551 -o pid,user,cmd --no-headers

# 2) 关闭（优雅）
kill 1057551
# 若 30s 内未退出再：
kill -9 1057551

# 3) 验证 GPU 7 已空
nvidia-smi --id=7 --query-gpu=memory.used --format=csv
```

### 1.2 依赖安装（在 `vllm` conda 环境里）

```bash
conda activate vllm
pip install google-genai ultralytics timm scipy tqdm
# opencv、torch、torchvision 已在 vllm 环境中
```

### 1.3 确认专家权重存在

```bash
ls -la /data4/jj/proj/surg_agent/detection_expert/runs/yolo26s_cholec_tool/weights/best.pt
ls -la /data4/jj/proj/surg_agent/phase_expert/runs/cholec_phase/best.pt
ls -la /data4/jj/proj/surg_agent/triplet_expert/LAM/weights/LAM/CholecTriplet2021/LAM_Lite/model_best.pth.tar
# 关键：确认 IVT label 映射文件真实路径
find /data4/jj/proj/surg_agent/triplet_expert -name "label_mapping*" -o -name "*maps.txt" 2>/dev/null
```

若 label_mapping 路径与 `config.json` 不符，按实际路径更新 `experts.triplet.label_mapping`。

## 2. 配置：全部钉到 cuda:7

修改 `offline_pipeline/config.json`：
```json
{
  "experts": {
    "yolo":    { "device": "cuda:7" },
    "phase":   { "device": "cuda:7" },
    "triplet": { "device": "cuda:7" }
  },
  "sampling": { "target_fps": 1, "window_duration_sec": 15.0 }
}
```

3 个专家同进程顺序执行（先 YOLO → 再 Phase → 再 Triplet），每一步都是 GPU batch，
显存峰值在 Triplet（clip × batch_size × 10 帧 × 224² × 3 × 4B ≈ 1 GB），完全无竞争。

> 为什么不并发：handoff §1.1 未要求。同卡并发会增加显存碎片风险，顺序跑在
> 1 FPS 下本来就快（预计全流程 < 3 分钟/29 分钟视频）。

## 3. 分阶段验证（逐级放量）

### Stage A — 60 秒片段 smoke test（~2 分钟能出结果）

```bash
mkdir -p /tmp/offline_smoke
ffmpeg -y -ss 300 -t 60 -i /data3/tos_copy/cholec80/cholec80/videos/video01.mp4 \
  -c copy /tmp/offline_smoke/video01_60s.mp4

cd /data2/jj/proj/video_processor
conda activate vllm
CUDA_VISIBLE_DEVICES=7 python -m offline_pipeline.scripts.run_offline \
  --video /tmp/offline_smoke/video01_60s.mp4 \
  --sample-fps 1 --window 15 --skip-batch -v
```

**验收标准**：
- [ ] 4 个窗口，每窗口 15 帧（最后一个可能不足）
- [ ] `frames_results.json`：每帧都有 `tools`（可空）、`phase_label`（7 类之一）、`triplets`（至少 1 条）
- [ ] `windows.json` 每窗口 `dominant_phase` 合理（短片段里多半就一个阶段）
- [ ] 无异常栈；GPU 7 峰值内存 < 5 GB（`watch -n1 nvidia-smi --id=7`）

### Stage B — 5 分钟片段（~8 分钟出结果）

```bash
ffmpeg -y -ss 300 -t 300 -i /data3/tos_copy/cholec80/cholec80/videos/video01.mp4 \
  -c copy /tmp/offline_smoke/video01_5min.mp4
CUDA_VISIBLE_DEVICES=7 python -m offline_pipeline.scripts.run_offline \
  --video /tmp/offline_smoke/video01_5min.mp4 --skip-batch -v
```

**验收重点**：phase 时序平滑是否生效
- 肉眼检查 `windows.json` 里 `dominant_phase` 序列，应满足 Cholec 流程单调性
  （Preparation → CalotTriangleDissection → … → GallbladderRetraction）
- 对比 Gaussian 平滑前后（在 `phase_expert.py` 里临时打印），预期噪声帧被抹平

### Stage C — 完整 video01（~30 分钟视频 → 预计 < 10 分钟处理）

```bash
CUDA_VISIBLE_DEVICES=7 python -m offline_pipeline.scripts.run_offline \
  --video /data3/tos_copy/cholec80/cholec80/videos/video01.mp4 \
  --sample-fps 1 --skip-batch -v 2>&1 | tee /tmp/offline_smoke/full.log
```

**验收标准**：
- [ ] ~116 个窗口
- [ ] 7 种 phase 大部分都出现过
- [ ] 所有 8 种 YOLO 工具类别至少有一次检测
- [ ] triplets Top-K 中 `grasper-*`, `hook-*`, `clipper-*` 大类都出现
- [ ] 全流程 wall-clock < 15 分钟

## 4. 横向对照（可选但推荐）

Cholec80 提供了**人工标注的 phase 真值**：
```
/data3/tos_copy/cholec80/cholec80/phase_annotations/video01-phase.txt  (若存在)
```
写一个小脚本读真值 vs. `frames_results.json` 计算帧级准确率。Handoff 说
Phase Expert + Gaussian σ=3 在 Cholec80 上是 **98.5%**；我们在同一视频上应该至少 > 95%。
若 < 90% → 大概率是 label_mapping 或 class order 错位。

## 5. 失败排查清单

| 现象 | 首查 |
|------|------|
| `ImportError: eval_lam_on_our_val` | 确认 `experts.triplet.source_dir` 指向 triplet_expert 根 |
| `phase_label` 全是同一个类 | `ckpt["classes"]` 顺序 vs. Cholec80 标注是否对齐 |
| triplet 全是 `ivt_000` | label_mapping 文件没加载上 |
| `CUDA out of memory` | 先 `nvidia-smi --id=7` 确认没残留进程；减小 `batch_size` |
| ffmpeg 抽帧极慢 | video01 文件在 /data3（tos 挂载）可能慢；先 `cp` 到 /tmp |

## 6. 完成验收后再开启 Gemini Batch

只有 Stage C 验收全部过了，才在 `config.json` 里 `gemini.skip_batch=false`
并设置 `GEMINI_API_KEY`，用同一段 60s smoke 先跑一轮小 batch。

## 7. 待办（本计划覆盖不了的 P1/P2）

- Embedding 接入（目前只生成 stub）
- MySQL 写入
- Cholec80 80 个视频的批量跑 + 评估脚本
- 前端 "本地视频分析" 模式联调
