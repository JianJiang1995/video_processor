# 手术视频分析 Pipeline：SurgR1 + Gemini

> 本文档描述 VIDEO_PROCESSOR 中 SurgR1（帧级分析）和 Gemini（窗口级摘要）的完整处理流程，供其他项目参考。

---

## 架构总览

```
视频输入（本地文件 / RTSP 流）
    │
    ▼
┌──────────────────────────┐
│  帧提取 (VideoProcessor) │   window_duration=15s, sample_interval=3s → 每窗口约 5 帧
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│  SurgR1（逐帧分析）       │   每帧 3 个问题：phase / action / tool_localization
│  vLLM + LoRA 微调模型     │   输入：JPEG 840×476
└────────────┬─────────────┘
             │
             │  R1 的全部输出（phase + action + tool_localization）
             │  作为 Gemini 的输入参考
             ▼
┌──────────────────────────────────────────────────────────┐
│  Gemini（窗口级摘要）                                      │
│  Cloud API                                                │
│                                                           │
│  输入：                                                    │
│    ① R1 帧标注文本（phase / action / tools，格式化后传入）  │
│    ② 采样图片（最多 10 张，640px 宽）                       │
│    ③ 历史上下文（全局阶段进展 + 最近 3 个窗口详情）          │
│    ④ 阶段约束提醒（基于历史自动注入）                       │
│                                                           │
│  输出：阶段标签 + 叙事描述 + 结构化 [others] 数据            │
└──────────────────────────────────────────────────────────┘
```

**核心设计：SurgR1 的结果是 Gemini 的输入，不是各自独立工作。**
SurgR1 负责逐帧识别（phase、action、tool bbox），这些结果被格式化为「R1帧标注」文本，连同原始图片一起传给 Gemini。Gemini 综合 R1 标注、图片内容和历史上下文，生成窗口级的叙事摘要。R1 没有跨窗口的历史信息，阶段顺序约束完全通过 Gemini 的 Prompt 来实现。

---

## 1. SurgR1 — 帧级分析

### 1.1 模型与部署

| 项目 | 值 |
|------|------|
| 模型 | Qwen2.5-VL-7B + LoRA（胆囊切除术微调） |
| 推理引擎 | vLLM（离线批量推理） |
| 服务脚本 | `SurgR1_api/run.sh` |
| API 端口 | `http://localhost:9100` |
| 启动命令 | `python main.py --port 9100 --model_path <path> --lora_path <path>` |

### 1.2 API 接口

**`POST /analyze`**

请求体：

```json
{
  "image_paths": ["/tmp/frame_001.jpg", "/tmp/frame_002.jpg"],
  "questions": null
}
```

- `image_paths`：JPEG 文件路径列表（由客户端将 PIL/base64 图片写为临时文件）
- `questions`：可选，默认使用内置的 3 个问题

内置问题（`SurgR1_api/main.py`）：

| 问题 ID | Prompt |
|---------|--------|
| `surgical_phase` | "What is the surgical phase in this image? Answer with one of: Preparation, CalotTriangleDissection, ClippingCutting, GallbladderDissection, GallbladderRetraction, CleaningCoagulation, GallbladderPackaging" |
| `surgical_action` | "What surgical actions are being performed? Describe briefly." |
| `tool_localization` | "Identify and locate all surgical tools visible. For each tool, provide the tool name and bounding box as name(x1,y1),(x2,y2)." |

响应体：

```json
{
  "results": [
    {
      "image_path": "/tmp/frame_001.jpg",
      "responses": {
        "surgical_phase": "GallbladderDissection",
        "surgical_action": "Dissecting gallbladder from liver bed using hook",
        "tool_localization": "grasper(328,0),(466,112) hook(516,0),(853,287)"
      }
    }
  ]
}
```

### 1.3 图像预处理

```python
TARGET_SIZE = (840, 476)  # 匹配训练数据分辨率（CholecInstanceSeg）
pil_image = pil_image.resize(TARGET_SIZE, Image.LANCZOS)
```

### 1.4 批量处理

- API 内部将 N 张图片 × 3 个问题展开为 3N 个 vLLM 推理样本，一次批量完成
- 客户端（`surgr1_client.py`）支持动态 batch size（2~15 帧），超时 3 秒自动发送
- 流模式下最多 3 个并行 R1 任务（`MAX_PARALLEL_R1_TASKS = 3`）

### 1.5 客户端调用

```python
# video_stream_app/backend/services/surgr1_client.py
result = await surgr1_client.analyze_frames_batch(
    frames=[
        {"image": pil_image, "frame_idx": 0, "timestamp": 0.0},
        {"image": pil_image, "frame_idx": 1, "timestamp": 3.0},
    ]
)
# result: [{"surgical_phase": "...", "surgical_action": "...", "tool_localization": "..."}]
```

---

## 2. Gemini — 窗口级多模态摘要

### 2.1 模型与配置

配置文件：`video_stream_app/config.json`

```json
{
    "window_analysis": {
        "provider": "gemini",
        "history_window_count": 3,
        "max_output_chars": 300
    },
    "services": {
        "gemini": {
            "enabled": true,
            "api_key_env": "GEMINI_API_KEY",
            "model_name": "gemini-3-flash-preview",
            "thinking_level": "none",
            "max_tokens": 2048
        }
    }
}
```

- API Key 从环境变量 `GEMINI_API_KEY` 读取
- VLM 提供商通过 `vlm_factory.py` 选择（`gemini` / `glm` / `qwen`）

### 2.2 核心方法：`integrate_analysis_results`

```
gemini_client.py :: GeminiClient.integrate_analysis_results(
    frame_analyses,    # SurgR1 逐帧分析结果（phase / action / tool_localization）
    images,            # 窗口内的 PIL 图片列表
    history_context,   # 历史上下文字符串（由 WindowHistoryManager 构建）
    temperature,       # 默认 0.9（本地），0.7（流模式）
    max_tokens         # 默认 1500
)
```

**处理流程（R1 结果如何喂给 Gemini）：**

```
1. 一致性分析         frame_analyses → 统计阶段分布、主导阶段、工具出现频率
2. 构建 R1 帧标注文本  frame_analyses → 每帧的 phase / action / tools 格式化为文本
                      ↓
3. 加载 System Prompt  从 background.txt [GLM_SYSTEM_PROMPT_START]...[END] 提取
4. 构建 User Prompt    拼接以下内容（按顺序）：
                        ├── 历史上下文（全局阶段进展 + 最近窗口详情）
                        ├── 阶段约束提醒（如"胆囊取出后禁止胆囊分离"）
                        ├── 【R1帧标注】← SurgR1 全部输出在这里传入
                        └── 分析请求
5. 多模态调用         采样图片（最多 10 张）+ 上述文本 prompt → Gemini API
6. 解析输出           提取叙事摘要 + [others] 结构化数据
```

Gemini 收到的 User Prompt 中，R1 帧标注部分示例：

```
【R1帧标注】（器械检测通常准确，请在叙事中体现）
帧 1 (0.0s): phase=GallbladderPackaging, action=Placing gallbladder into bag, tools=grasper(328,0),(466,112)
帧 2 (3.0s): phase=GallbladderPackaging, action=Closing specimen bag, tools=grasper(200,50),(400,200)
...
```

Gemini 根据这些 R1 标注 + 自身对图片的理解，综合生成叙事描述。Prompt 中明确要求「器械检测通常准确，请在叙事中体现」，即 R1 的工具检测结果应被采纳。

### 2.3 System Prompt

来源：`glm_api/background.txt`，在 `[GLM_SYSTEM_PROMPT_START]` 和 `[GLM_SYSTEM_PROMPT_END]` 标记之间。

核心规则：

| 规则类别 | 内容 |
|---------|------|
| 角色 | 腹腔镜胆囊切除术分析系统 |
| 输出禁止 | 不提及医生/术者；不描述光线反射、水珠、阴影等视觉噪声 |
| 异常描述 | 出血/烟雾/模糊仅在实际发生时描述，禁止描述"无出血"等正常状态 |
| blur 判断 | 极其严格：标本袋半透明/反光/局部过曝均不算 blur |
| 必须关注 | Hem-o-lok 数量、纱布出现 |
| 输出语言 | 全中文，禁止英文 |

输出格式：

```
【xxx】（阶段名，如【清洁凝血】【胆囊分离】）
简洁操作描述（50-100字）
[others]hem_loc=N,gauze=Y/N,bleeding=Y/N,blur=Y/N,out_of_body=Y/N
```

### 2.4 阶段名称映射

| R1 英文标注 | Gemini 输出中文名 |
|-------------|------------------|
| Preparation | 准备阶段 |
| CalotTriangleDissection | 肝胆三角解剖 |
| ClippingCutting | 夹闭切断 |
| GallbladderDissection | 胆囊分离 |
| GallbladderRetraction | 胆囊牵拉 |
| CleaningCoagulation | 清洁凝血 |
| GallbladderPackaging | 胆囊取出 |

### 2.5 图片采样与压缩

| 场景 | 最大图片数 | 压缩 |
|------|-----------|------|
| 本地模式 | 10 张（均匀采样） | 640px 宽，JPEG quality=60 |
| 流模式（在线） | 3 张（均匀采样） | 同上 |

每张图片附带时间戳标记：`【图片1/5】时间：3.0秒`

### 2.6 `[others]` 结构化输出

每次窗口分析必须返回的结构化数据行：

```
[others]hem_loc=0,gauze=N,bleeding=N,blur=N,out_of_body=N
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `hem_loc` | int | 可见 Hem-o-lok 数量 |
| `gauze` | Y/N | 是否可见纱布 |
| `bleeding` | Y/N | 是否有明显出血 |
| `blur` | Y/N | 视野是否被大面积遮挡 |
| `out_of_body` | Y/N | 镜头是否移出体外 |

---

## 3. 历史上下文与阶段约束

### 3.1 WindowHistoryManager

管理窗口分析历史，为 Gemini 提供上下文。

```python
class WindowHistoryManager:
    _history: List[WindowSummary]     # 滑动窗口（最近 N 个窗口详情）
    _reached_phases: set              # 持久标记（所有曾出现过的阶段，不受滑动窗口限制）
```

### 3.2 `build_history_context` 输出格式

传给 Gemini 的历史上下文由两部分组成：

```
## 手术已经历的阶段（全局）：准备阶段→肝胆三角解剖阶段→夹闭切断阶段→胆囊分离阶段→清洁凝血阶段→胆囊取出阶段

## 最近窗口分析（按时间顺序）

### 窗口 18（4:30 - 4:45）
- 阶段：胆囊取出阶段
- 摘要：抓钳持标本袋...

### 窗口 19（4:45 - 5:00）
- 阶段：胆囊取出阶段
- 摘要：...
```

- **全局阶段进展**（`_reached_phases`）：始终完整，不因滑动窗口而丢失
- **最近 3 个窗口详情**（`_history[-3:]`）：提供当前操作的具体上下文

### 3.3 动态阶段约束

在 `integrate_analysis_results` 构建 User Prompt 时，根据历史上下文自动注入约束：

```python
if "胆囊取出" in history_context:
    # 历史已出现胆囊取出 → 仅允许 清洁凝血 或 胆囊取出
    # 禁止：胆囊分离、胆囊牵拉、夹闭切断、肝胆三角解剖、准备阶段

if any(正式阶段 in history_context):
    # 手术已进入正式阶段 → 禁止回退到准备阶段
```

由于全局阶段进展始终存在于 `history_context` 中，约束检测不会因为滑动窗口滑过而失效。

### 3.4 阶段转换规则表

background.txt 中定义的阶段转换规则（供 Gemini 参考）：

| 当前阶段 | 允许转换到 |
|---------|-----------|
| 准备阶段 | 肝胆三角解剖、胆囊牵拉 |
| 肝胆三角解剖 | 夹闭切断、胆囊牵拉、准备阶段 |
| 夹闭切断 | 胆囊分离、肝胆三角解剖 |
| 胆囊分离 | 清洁凝血、胆囊牵拉、夹闭切断 |
| 胆囊牵拉 | 肝胆三角解剖、胆囊分离、清洁凝血 |
| 清洁凝血 | 胆囊取出、胆囊分离 |
| 胆囊取出 | **仅允许清洁凝血** |

---

## 4. 处理管线（两条路径）

### 4.1 本地视频 — Pipeline Overlap

```
analysis.py :: process_video_surgr1_glm_task()
```

```
Window N                          Window N+1
┌──────────────────┐              ┌──────────────────┐
│ 1. 提取帧         │              │ 1. 提取帧         │
│ 2. SurgR1 批量分析 │              │ 2. SurgR1 批量分析 │  ← 与上一个窗口的 Gemini 并行
│ 3. 等待上个 Gemini │              │ ...               │
│ 4. 启动本窗口 Gemini│──(并行)──→  │                    │
└──────────────────┘              └──────────────────┘
```

- 当前窗口的 SurgR1 和上一窗口的 Gemini 并行执行
- 必须等上一窗口 Gemini 完成后再构建当前窗口的 `history_context`

### 4.2 流模式 — 连续 SurgR1 + GLM 轮询

```
┌────────────────────────────┐     ┌─────────────────────────┐
│ surgr1_continuous_task      │     │ glm_summarization_task  │
│                            │     │                         │
│ 读帧 → 动态batch → SurgR1  │────→│ 轮询 DB → 按窗口分组     │
│ 结果存入 MySQL/SQLite      │     │ 加载帧图片 → VLM 分析    │
│                            │     │ 摘要存入 DB              │
│ 帧捕获: 25fps 存储到磁盘   │     │                         │
└────────────────────────────┘     └─────────────────────────┘
```

- SurgR1 和 Gemini 分析为两个独立的异步任务
- GLM 任务轮询 R1 结果，当窗口帧数达到 `min_frames_ratio`（默认 0.2）× 预期帧数时开始分析

---

## 5. 阶段提取优先级

存入 `WindowHistoryManager` 的 `dominant_phase` 提取优先级：

```
1. 优先：从 Gemini 摘要文本中的【xxx】标签提取（如【胆囊取出】→ GallbladderPackaging）
2. 回退：R1 的 consistency_analysis.图像级一致性.主导阶段
```

之所以 Gemini 优先：R1 是逐帧独立推理，没有跨窗口的历史信息，可能在手术后期仍输出早期阶段（如胆囊取出后仍输出胆囊分离）。而 Gemini 通过 Prompt 中的历史上下文和阶段约束提醒，能做出符合手术进程的阶段判断。R1 的阶段结果仅在 Gemini 未输出阶段标签时作为补充。

需要注意的是：**R1 的全部输出（phase / action / tool_localization）始终作为 Gemini 的输入参考**，R1 的阶段识别能力并没有被丢弃，而是被 Gemini 综合历史上下文后进行了二次校验。

---

## 6. 关键文件索引

| 组件 | 文件路径 |
|------|---------|
| **SurgR1 API 服务** | `SurgR1_api/main.py` |
| **SurgR1 启动脚本** | `SurgR1_api/run.sh` |
| **SurgR1 客户端** | `video_stream_app/backend/services/surgr1_client.py` |
| **Gemini 客户端** | `video_stream_app/backend/services/gemini_client.py` |
| **GLM 客户端** | `video_stream_app/backend/services/glm_client.py` |
| **VLM 工厂** | `video_stream_app/backend/services/vlm_factory.py` |
| **System Prompt** | `glm_api/background.txt`（`[GLM_SYSTEM_PROMPT_START]`…`[END]`） |
| **分析路由** | `video_stream_app/backend/routers/analysis.py` |
| **视频处理器** | `video_stream_app/backend/services/video_processor.py` |
| **帧存储服务** | `video_stream_app/backend/services/frame_storage_service.py` |
| **全局配置** | `video_stream_app/config.json` |
| **后端配置** | `video_stream_app/backend/config.py` |

---

## 7. 关键配置参数

| 配置路径 | 参数 | 默认值 | 说明 |
|---------|------|--------|------|
| `video_processing.window_duration` | 窗口时长 | 15.0s | 每个分析窗口的时间跨度 |
| `video_processing.sample_interval` | 采样间隔 | 3.0s | 窗口内帧采样间隔 |
| `window_analysis.provider` | VLM 提供商 | `"gemini"` | `gemini` / `glm` / `qwen` |
| `window_analysis.history_window_count` | 历史窗口数 | 3 | 传给 VLM 的最近窗口详情数量 |
| `window_analysis.max_output_chars` | 最大输出字数 | 300 | Gemini 摘要输出字数限制 |
| `window_analysis.min_frames_ratio` | 最小帧比 | 0.2 | 流模式下窗口就绪所需的最低帧数比例 |
| `services.gemini.model_name` | Gemini 模型 | `"gemini-3-flash-preview"` | |
| `services.gemini.thinking_level` | 思考级别 | `"none"` | |
| `services.gemini.max_tokens` | 最大 token | 2048 | |
| `services.surgr1.api_url` | SurgR1 地址 | `"http://localhost:9100"` | |
| `services.surgr1.max_concurrent` | 最大并发 | 3 | SurgR1 并发请求数 |
