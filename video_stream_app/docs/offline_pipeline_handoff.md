# Offline Video Analysis Pipeline — Handoff Document

> **目标读者**：接手实现 offline pipeline 的 agent / 开发者
> **当前状态**：实时流 pipeline 已完成并验证通过。本文档描述需要新建的 offline batch 处理流程。
> **日期**：2026-04-15

---

## 1. 背景与目标

### 1.1 已有实时流 Pipeline（参考，不要修改）

```
Live Stream (RTSP/HTTP/MJPEG)
    │
    ├── Frame Capture (25 FPS)          → sessions/{sid}/frames/
    ├── YOLO26s (实时 bbox overlay)     → MJPEG proxy
    ├── SurgR1 (Qwen2.5-VL 问答, 1 fps) → surgical_phase + surgical_action
    └── Gemini 3.1 Pro (15s 窗口)       → 中文总结 + 出血标注
           │
           └── Embedding (gemini-embedding-2-preview) → 语义搜索
```

**关键文件**：
- `backend/routers/analysis.py` — `surgr1_continuous_task()` 是实时流核心
- `backend/services/gemini_client.py` — Gemini 调用逻辑
- `backend/services/yolo_service.py` — YOLO 工具检测
- `backend/services/embedding_service.py` — embedding 存储/搜索

### 1.2 Offline Pipeline 需求

**输入**：已录制好的手术视频文件（mp4/mov/avi 等）
**输出**：与实时流相同结构的分析结果（存入 MySQL），但额外包含更丰富的专家模型输出

**核心思路**：
1. **不走 SurgR1（太慢）** → 改用 3 个轻量级 Cholec 专家模型替代
2. **Gemini 用 Batch API**（比实时调用便宜 50%，吞吐更高）
3. 复用实时流的窗口总结 + embedding 逻辑

---

## 2. 选定的专家模型（仅 Cholec 系列）

所有模型已训练完成，权重就在本机。详见 `/data4/jj/proj/surg_agent/docs/SurgAgent_Expert_Results.md`。

### 2.1 Tool Expert — YOLO26s
**已经集成到实时流**，offline 直接复用 `backend/services/yolo_service.py`。

- **权重**：`/data4/jj/proj/surg_agent/detection_expert/runs/yolo26s_cholec_tool/weights/best.pt`
- **类别（8）**：`bipolar, clipper, grasper, hook, irrigator, scissors, snare, specimen_bag`
- **性能**：mAP@0.5 = 0.975
- **速度**：~5ms/frame GPU

### 2.2 Phase Expert — ResNet-18 + Gaussian 平滑
**Offline 比实时流效果更好**（可用 Gaussian σ=3 做非因果时序平滑，实时只能用因果平滑）。

- **权重**：`/data4/jj/proj/surg_agent/phase_expert/runs/cholec_phase/best.pt`
- **类别（7）**：`Preparation, CalotTriangleDissection, ClippingCutting, GallbladderDissection, GallbladderPackaging, CleaningCoagulation, GallbladderRetraction`
- **性能**：单帧 97.1% → 加 Gaussian σ=3 时序平滑后 **98.5%**
- **速度**：400 FPS（1080Ti）
- **输入**：224×224 RGB

**加载代码**：
```python
import torch, timm
from torchvision import transforms

ckpt = torch.load("/data4/jj/proj/surg_agent/phase_expert/runs/cholec_phase/best.pt",
                  weights_only=False)
model = timm.create_model(ckpt["model_name"], pretrained=False, num_classes=ckpt["num_classes"])
model.load_state_dict(ckpt["model_state_dict"])
model.eval().cuda()

transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
])
# Inference: batch of frames
phase_idx = model(batch_tensor).argmax(1).cpu()
phases = [ckpt["classes"][i] for i in phase_idx]
```

**时序平滑（offline 必用）**：
```python
from scipy.ndimage import gaussian_filter1d
# phase_probs shape: [N_frames, 7]
smoothed = gaussian_filter1d(phase_probs, sigma=3, axis=0, mode='reflect')
final_phases = smoothed.argmax(1)
```

### 2.3 Triplet Expert — LAM-Lite
**新增能力**（实时流没有）：识别 instrument-verb-target 三元组动作。

- **权重**：`/data4/jj/proj/surg_agent/triplet_expert/LAM/weights/LAM/CholecTriplet2021/LAM_Lite/model_best.pth.tar`
- **输出**：
  - I (6 类工具)
  - V (10 类动作)
  - T (15 类目标组织)
  - IVT (100 种三元组组合)
- **性能**：mAP-IVT = 0.838
- **速度**：1365 FPS（A100, 136 clips/s）
- **输入**：**10 帧的 clip**（224×224），不是单帧

**架构代码**：`/data4/jj/proj/surg_agent/triplet_expert/eval_lam_on_our_val.py` 里有 `LAMLite` class

**加载代码**：
```python
import torch, sys
sys.path.insert(0, "/data4/jj/proj/surg_agent/triplet_expert")
from eval_lam_on_our_val import LAMLite

ckpt = torch.load(
    "/data4/jj/proj/surg_agent/triplet_expert/LAM/weights/LAM/CholecTriplet2021/LAM_Lite/model_best.pth.tar",
    map_location="cpu", weights_only=False)
model = LAMLite(hidden_dim=512, num_frames=10)
model.load_state_dict(ckpt["state_dict"], strict=False)
model.eval().cuda()

# Input: [B, 10, 3, 224, 224]
with torch.no_grad():
    out = model(clip.cuda())
# out["i"]: [B, 6]    instrument logits
# out["v"]: [B, 10]   verb logits
# out["t"]: [B, 15]   target logits
# out["ivt"]: [B, 100] triplet logits

# IVT 的 100 类标签映射在 CholecT50 的 label_mapping.txt
```

**IVT 标签映射**：`/data4/jj/proj/surg_agent/triplet_expert/LAM/` 下应该有 `label_mapping.txt` 或类似文件。

---

## 3. Offline Pipeline 架构设计

### 3.1 整体数据流

```
输入：mp4 视频文件
  │
  ▼
[Stage 1] 帧提取
  - ffmpeg / OpenCV 抽帧到 sessions/{sid}/frames/
  - 保持 25 FPS 或降到 10 FPS（offline 不用那么高）
  │
  ▼
[Stage 2] 三专家并行推理（GPU batch）
  ├─ Tool Expert (YOLO26s)      每帧     → bboxes + labels
  ├─ Phase Expert (ResNet-18)    每帧     → phase logits [N, 7]
  │    └─ 后处理: Gaussian σ=3 时序平滑 → 稳定 phase 序列
  └─ Triplet Expert (LAM-Lite)   每10帧   → I/V/T/IVT logits
  │
  ▼
[Stage 3] 窗口聚合
  - 每 15 秒一个 window（与实时流对齐）
  - 每窗口收集：主导 phase、三元组时序、工具出现次数、代表帧
  │
  ▼
[Stage 4] Gemini Batch API 调用
  - 构造 batch JSONL 任务文件
  - 每个 request = 1 个窗口（系统 prompt + 专家结果 + 代表帧）
  - 上传到 Gemini Batch API → 等待完成 → 下载结果
  - 结果写入 MySQL analysis_results 表
  │
  ▼
[Stage 5] Embedding 生成（复用实时流逻辑）
  - 每窗口总结 → embedding_service.add_window_embedding()
  - 持久化到 sessions/{sid}/embeddings.json
  │
  ▼
[Stage 6] 出血标注（可选）
  - 复用 backend/scripts/bleeding_annotation.py
  - Gemini batch 模式（offline 应该改写 bleeding_annotation 支持 batch）
```

### 3.2 为什么用 Gemini Batch API

- **价格**：input/output 都是实时价的 **50%**
- **吞吐**：支持数千请求并发，不受 RPM 限制
- **延迟**：延迟高（~分钟级），但 offline 不在乎
- **适用场景**：一次性处理完一整段视频

**Gemini Batch API 文档**：https://ai.google.dev/gemini-api/docs/batch-mode

核心 API 调用：
```python
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

# 1. Build JSONL request file
requests_file = "batch_requests.jsonl"
with open(requests_file, "w") as f:
    for window_id, window_data in windows.items():
        request = {
            "request": {
                "contents": [{
                    "parts": [
                        {"text": system_prompt + build_context(window_data)},
                        # Images as inline_data (base64) or reference uploaded files
                        *[{"inline_data": {"mime_type": "image/jpeg",
                                           "data": img_b64}} for img_b64 in window_data["images"]]
                    ]
                }],
                "generationConfig": {
                    "temperature": 0.1,
                    "maxOutputTokens": 2048,
                    "thinkingConfig": {"thinkingBudget": 512}
                }
            },
            "metadata": {"window_id": window_id}
        }
        f.write(json.dumps(request) + "\n")

# 2. Upload batch file
uploaded = client.files.upload(file=requests_file, config={"display_name": "surgical_windows_batch"})

# 3. Create batch job
batch_job = client.batches.create(
    model="gemini-3.1-pro-preview",
    src=uploaded.name,
    config={"display_name": "surg_batch_" + session_id}
)

# 4. Poll until done (can take minutes to hours)
while batch_job.state.name in ("JOB_STATE_PENDING", "JOB_STATE_RUNNING"):
    time.sleep(30)
    batch_job = client.batches.get(name=batch_job.name)

# 5. Download results
if batch_job.state.name == "JOB_STATE_SUCCEEDED":
    results = client.files.download(file=batch_job.dest.file_name)
    # Parse JSONL, match by metadata.window_id
```

---

## 4. 需要实现的文件

### 4.1 推荐新建文件

```
backend/services/
├── phase_expert.py          # NEW: Phase Expert 封装（类 YOLOService）
├── triplet_expert.py        # NEW: Triplet Expert 封装
└── gemini_batch_client.py   # NEW: Gemini Batch API 封装

backend/pipelines/
└── offline_pipeline.py       # NEW: 主调度脚本（orchestrator）

backend/routers/
└── offline.py               # NEW: REST API endpoints
    - POST /api/offline/process-video  (body: {video_path})
    - GET  /api/offline/status/{job_id}
    - GET  /api/offline/result/{job_id}

backend/scripts/
└── run_offline.py           # CLI 入口（给批量处理用）
```

### 4.2 API Endpoint 设计

```python
# POST /api/offline/process-video
Request:
{
  "video_path": "/path/to/video.mp4",
  "sample_fps": 10,           // 每秒采样多少帧走专家
  "window_duration": 15.0,    // 跟实时流对齐
  "enable_bleeding": false    // 可选的 Gemini 出血标注
}

Response:
{
  "job_id": "offline_20260415_abc123",
  "session_id": "abc123",
  "status": "queued",
  "estimated_time_sec": 1800,
  "expected_windows": 240
}

# GET /api/offline/status/{job_id}
Response:
{
  "job_id": "offline_...",
  "status": "running",  // queued | extracting | experts | batch_pending | embedding | done | failed
  "progress": {
    "frames_extracted": 90000,
    "frames_total": 90000,
    "experts_done": 1.0,      // 0-1
    "batch_state": "RUNNING",
    "windows_done": 120,
    "windows_total": 240
  },
  "error": null
}
```

### 4.3 关键数据结构

```python
@dataclass
class FrameExpertResult:
    frame_idx: int
    timestamp: float
    # From YOLO
    tools: List[Dict]  # [{label, conf, bbox_xyxy}]
    # From Phase Expert
    phase_probs: np.ndarray  # [7] — use raw probs, smoothing happens after
    phase_label: str         # 平滑后的最终标签
    # From Triplet Expert (last 10-frame clip result)
    triplets: List[Dict]     # [{ivt_label, conf}]

@dataclass
class WindowContext:
    window_id: int
    start_time: float
    end_time: float
    dominant_phase: str           # 最频繁的 phase
    phase_transitions: List       # 窗口内阶段变化
    tool_frequencies: Dict[str, int]  # 每种工具出现帧数
    triplet_summary: List[str]    # Top-K 三元组
    representative_frames: List[str]  # 3-5 张代表帧路径
```

---

## 5. 构造 Gemini Prompt 的建议

复用实时流的 prompt 模板（在 `gemini_client.py` 里），但**加入专家结果作为额外 context**：

```
你是腹腔镜手术分析系统。基于以下信息生成 15 秒窗口的简洁中文总结。

【专家检测结果】
- 识别阶段 (置信度): GallbladderDissection (0.94)
- 出现工具: grasper (35 帧), hook (22 帧), clipper (5 帧)
- 主要动作三元组: hook-dissect-gallbladder, grasper-retract-liver

【R1/VLM 帧分析】
[如果还跑 SurgR1，附带这里；如果不跑 SurgR1，跳过]

【历史上下文】
前 3 个窗口的总结...

【输出要求】
- 纯中文
- 300 字以内
- 包含阶段、器械动作、组织状态
- [others] 行标注 hem_loc/gauze/bleeding/blur/out_of_body
```

**关键决策**：offline 里**可以去掉 SurgR1**（因为 3 专家已经覆盖了 phase + tool + action）。这能省一大块算力，且让 offline pipeline 不依赖 vLLM 服务。

---

## 6. 性能与成本预估

### 6.1 1 小时视频

假设：
- 25 FPS 原始 → 降到 10 FPS 采样 = 36000 frames
- 专家推理：~5ms/frame × 3 专家 = ~15ms/frame × 36000 = **9 分钟 GPU**
- Gemini Batch：240 windows × ~9K input tokens + 800 output tokens
  - Batch 价格（50% off）:
    - Input: 2.16M × $0.625/M = $1.35
    - Output: 192K × $5/M = $0.96
    - **总计: ~$2.30/小时视频**（比实时的 $4.60 便宜一半）

### 6.2 Batch API 延迟
- 官方承诺：**24 小时内完成**
- 实测：通常 5-30 分钟（视负载）

---

## 7. 测试数据

现成可用的测试视频：
- `/data2/jj/proj/video_processor/stream_simulator/media/sample.mp4`（如果存在）
- 或从任一历史 session 的 frames 重建：`sessions/20260414_234938_e7638500_stream/frames/*.jpg`

建议先用 1-2 分钟的短视频跑通整个 pipeline，再扩展到完整手术视频。

---

## 8. 关键依赖与环境

```bash
# 已有依赖（vllm 环境里都有）
pip install google-genai ultralytics timm scipy

# 可能需要新增
pip install tqdm  # 进度条
```

**GPU 分配建议**：
- 跟实时流 pipeline **共用** GPU 或 **错峰使用**（offline 可以跑在闲置的 GPU 上）
- 3 专家加起来显存 < 5GB，很轻

---

## 9. 接下来实现的优先级

**P0（必须）**：
1. `phase_expert.py` + `triplet_expert.py` 封装
2. `offline_pipeline.py` orchestrator（先跑本地版本，不用 API）
3. `gemini_batch_client.py` 封装 Batch API
4. 把专家结果 + Gemini 总结写入 MySQL（复用现有 schema）

**P1（重要）**：
5. `POST /api/offline/process-video` 异步任务 + 状态查询
6. 前端 "本地视频分析" 模式用新 API
7. 时序平滑 post-processing

**P2（可选增强）**：
8. Batch bleeding annotation（把现有 `bleeding_annotation.py` 改成 batch 模式）
9. Web UI 显示 triplet 时序
10. Offline 特有：整个视频的 phase transition 时间线可视化

---

## 10. 已知陷阱与注意事项

1. **Phase Expert 时序平滑不能用 causal 版本**（offline 场景下 non-causal 更准，+1.4pp）
2. **LAM-Lite 需要 10 帧 clip**，不是单帧 — 需要滑窗输入，不是逐帧
3. **Gemini Batch 的 image input** 超过 20MB 的 JSONL 要拆分，或用 file upload 模式
4. **Triplet label 解码**：100 类 IVT 的 mapping 必须跟训练时一致（见 CholecT50 `label_mapping.txt`）
5. **MySQL 表**：已有 `analysis_results` 表可以复用，但可能需要加 `triplets`, `expert_source` 列
6. **Session 管理**：offline session 应该有独立的 `session_type` 字段（`live` vs `offline`）
7. **Gemini Pro 3.1 强制要求 thinking mode**（`thinkingBudget` 必须 > 0）
8. **窗口数过多时**（>1000）可能要拆多个 batch 任务

---

## 11. 参考资源

| 资源 | 路径 |
|------|------|
| 专家模型汇总 | `/data4/jj/proj/surg_agent/docs/SurgAgent_Expert_Results.md` |
| Phase Expert 代码 | `/data4/jj/proj/surg_agent/phase_expert/` |
| Triplet Expert 代码 | `/data4/jj/proj/surg_agent/triplet_expert/` |
| 实时流 pipeline 参考 | `backend/routers/analysis.py::surgr1_continuous_task` |
| Gemini 客户端（可复用） | `backend/services/gemini_client.py` |
| Embedding 服务（可复用） | `backend/services/embedding_service.py` |
| Bleeding 标注（参考批量模式改造）| `backend/scripts/bleeding_annotation.py` |
| Pipeline 设计文档 | `docs/pipeline_design_v3.md` |
| 启动指南 | `docs/startup_guide.md` |

---

## 12. 总结

✅ **要做的事**：实现 offline 模式，3 个 Cholec 专家 + Gemini Batch API，复用实时流的 DB/Embedding 层
❌ **不要做的事**：不要改动实时流 pipeline，不要引入 SurgR1 依赖（offline 不需要）
🎯 **验收标准**：能把一个 mp4 文件处理成和实时流相同结构的 MySQL 记录 + `sessions/{sid}/embeddings.json`

Good luck!
