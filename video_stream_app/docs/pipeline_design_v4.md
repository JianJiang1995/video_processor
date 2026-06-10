# Pipeline Design v4 — Three-Expert + SurgR1 两阶段实时集成

**状态：** 设计中（2026-04-20）
**作者：** —
**前置：** pipeline_design_v3.md

---

## 1. 目标

让每个窗口的分析既**实时**又**带推理深度**，同时把 YOLO/Phase/Triplet 三个专家模型的专业判断和 SurgR1 的思维链推理都整合进最终输出。

---

## 2. 专家模型分工

| Expert | 任务 | 模型 | GPU | 时延 | 输出 |
|---|---|---|---|---|---|
| **YOLO Expert** | 工具检测 bbox | YOLO26s (9.5M) | 独立 GPU (~1 GB) | ~5 ms/帧 | `[{x1,y1,x2,y2,label,conf}]` |
| **Phase Expert** | 手术阶段识别 | ResNet-18 (11M) | 独立 GPU (~300 MB) | ~2 ms/帧 | `{phase, confidence}` |
| **Triplet Expert** | (Instrument, Verb, Target) 三元组 | LAM-Lite (13M) | 独立 GPU (~1 GB) | ~7 ms/10帧 clip | `{instrument, verb, target, triplet, conf}` |
| **SurgR1** | Phase 深度推理（CoT）| Qwen2.5-VL (60 GB) | 独立 GPU | 2-5 s/帧 | `{phase, reasoning_cot}` — 只问 Phase |

**关键变化：SurgR1 从 3 问题（phase/action/tools）缩减为 1 问题（phase），原来的 action/tools 由 Phase Expert + Triplet Expert + YOLO Expert 代替并行回答。**

---

## 3. 每个窗口的两阶段流程

```
┌─ 窗口 i 触发 ────────────────────────────────────────┐
│                                                      │
│  ① 三专家并行采样（每窗口 3~5 帧）                  │
│     ├─ YOLO → bbox list                              │
│     ├─ Phase → phase name                            │
│     └─ Triplet → (I, V, T) 组合                      │
│                                                      │
│  ② Stage 1 — Gemini 快速整合（纯文本，无图）        │
│     input:  3 expert 文本结果 + history              │
│     output: 3-5 秒得到初稿 summary                   │
│     emit:   SSE event="stage1"                       │
│                                                      │
│  ③ SurgR1 继续跑（10-30 秒）                        │
│                                                      │
│  ④ Stage 2 — Gemini 多模态精修                      │
│     input:  3 expert 结果 + SurgR1 phase+CoT + images│
│             + Stage 1 summary                        │
│     output: 冲突裁决后的最终 summary + reasoning     │
│     emit:   SSE event="stage2"                       │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### Stage 1 prompt 示意

```
你是手术分析助手。下面是三个专家对当前 15 秒窗口（采样 5 帧）的判断：

[Phase Expert] cholec_phase → "CalotTriangleDissection" (conf 0.94)
[YOLO Expert]  frames [0,3,6,9,12]: [grasper, hook], [hook], ... 
[Triplet]      top-1 per clip: (grasper, dissect, cystic_duct), ...

请在 2-3 句内总结此窗口正在发生什么手术动作。
```

### Stage 2 prompt 示意

```
Stage 1 已给出：
"正在进行 Calot 三角解剖，使用 grasper 固定胆囊管..."

现在拿到 SurgR1 的深度推理：
[SurgR1] phase = "CalotTriangleDissection"
[CoT] "从图像可见白色胆囊管暴露中，Hartmann 囊被 grasper 牵拉向上..."

以及附带的 3 张代表帧图像：<image>...

请做冲突核对与合并：
- 如 SurgR1 推理与三专家有冲突，说明取舍原因。
- 如一致，把 CoT 里的关键视觉描述融进最终 summary。
- 保留 <reasoning> 原文为展开字段。
```

---

## 4. 数据契约（SSE 事件）

```jsonc
// 事件 1：stage1_ready
{
  "event": "stage1",
  "window_id": 2,
  "start_time": 30.0,
  "end_time": 45.0,
  "summary": "...",             // 初稿
  "phase": "CalotTriangleDissection",
  "experts": {
    "yolo":    [{...}, {...}],
    "phase":   {"label": "...", "conf": 0.94},
    "triplet": [{"i":"grasper","v":"dissect","t":"cystic_duct","conf":0.81}]
  }
}

// 事件 2：stage2_ready（可能比 stage1 晚 10-30 秒）
{
  "event": "stage2",
  "window_id": 2,
  "summary": "...",                // 精修版
  "phase": "CalotTriangleDissection",
  "surgr1_reasoning": "...(CoT 原文)",
  "conflicts": []                  // 若专家与 SurgR1 有冲突时的裁决记录
}
```

前端 RightPanel 收到 stage1 立刻填入、显示 "⚡ 快速初稿" 角标；收到 stage2 替换 `summary`、切换为 "✓ SurgR1 精修"，可展开查看 `surgr1_reasoning`。

---

## 5. 实现位置

| 模块 | 文件 | 状态 |
|---|---|---|
| Phase Expert 服务 | `backend/services/phase_service.py` | 本次新增 |
| Triplet Expert 服务 | `backend/services/triplet_service.py` | 本次新增 |
| 配置 | `config.json` — `services.phase`, `services.triplet` | 本次新增 |
| SurgR1 配置 | `SurgR1_api/config.json` — 移除 action/tools 问题 | 本次改 |
| SurgR1 客户端 | `backend/services/surgr1_client.py` — `question_map` 精简 | 本次改 |
| 启动脚本 | `start_surgr1_yolo.sh` → 演化为 `start_experts.sh`，分配 4 GPU | 本次改 |
| 分析管线 | `backend/routers/analysis.py` — 两阶段 Gemini 调用 | **下一步** |
| Gemini 客户端 | `backend/services/gemini_client.py` — 添加文本-only 整合方法 | **下一步** |
| 前端 SSE | `frontend/src/App.vue` — 区分 stage1/stage2 | **下一步** |
| 前端 UI | `frontend/src/components/RightPanel.vue` — 两阶段指示器 + CoT 展开 | **下一步** |

---

## 6. GPU 分配（A100×8）

```
GPU 0: 保留（Electron / dev）
GPU 1: YOLO Expert       (~1  GB)
GPU 2: Phase Expert      (~0.3 GB)
GPU 3: Triplet Expert    (~1  GB)
GPU 4-7: SurgR1 (自动选空闲最大)
```

启动脚本会扫描 nvidia-smi，按空闲度分配。每块小专家独占一卡避免相互挤压；SurgR1 自动挑空闲最多的卡（需 ≥60 GB）。
