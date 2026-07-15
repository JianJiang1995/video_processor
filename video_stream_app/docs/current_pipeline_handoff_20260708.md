# Surg-R1 Electron 本地视频分析交接文档

> **⚠️ 本文档已被 `docs/current_pipeline_handoff_20260712.md` 取代，请以新文档为准。**
> 本文第 1-15 节部分信息已过时（本地 VLM 已从 local_openai_vlm_server.py 切换为 vLLM；clip 检测器已换 yolo_clip_corrected_v2 权重；多处超时配置已调整；11.1 节的 W14-16"误报"后经抽帧核实为真夹子）。
> 第 16-18 节是 2026-07-08/07-09 两轮修复的详细记录，仍具参考价值。

更新时间：2026-07-08  
项目目录：`/home/user/proj/video_processor/video_stream_app`

## 1. 当前目标与系统定位

这个项目是一个本地 Electron 手术视频分析工具，目标是在本地采集卡/本地模拟视频流上流畅播放腹腔镜胆囊切除术视频，并实时输出：

- 右侧「分析」窗口：最新窗口、上一窗口和历史窗口的手术进程摘要。
- 底部「关键事件节点」：把原来的历史窗口分析 UI 改成关键事件节点视图，但窗口级分析结果仍然保存。
- 窗口一览：保留所有 5 秒分析窗口，支持查看缩略图、摘要和循环播放。
- 智能问答：基于当前会话所有窗口摘要回答医生问题。
- 临床总结报告：对一个已分析视频生成 Markdown 精要报告，按视频单独生成。

当前部署方向已经从外部 GPT/Gemini VLM 逐步切换到本地 VLM，避免手术数据外流。现在主要使用本地 `Qwen3-VL-8B-Instruct` 风格的 OpenAI-compatible 服务作为 GLM/VLM 后端，实时路径尽量由本地专家模型先出结果，VLM 只做候选窗口复核。

## 2. 当前进程状态

写本文档时，机器上有两个关键进程在跑：

- 后端：`../.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001`
- 本地 VLM：`scripts/local_openai_vlm_server.py --model-path models/local_vlm/qwen3-vl-8b-instruct --served-model-name Qwen3-VL-8B-Instruct --host 127.0.0.1 --port 8010`

注意：标准启动不要直接用系统 `python3 -m uvicorn`，系统 Python 没有 `uvicorn`。要走 `bash run_backend.sh`，脚本会优先激活 conda `vllm`，否则激活项目上一层的 `../.venv`。

## 3. 启动方式

### 3.1 后端

```bash
cd /home/user/proj/video_processor/video_stream_app
bash run_backend.sh
```

后端端口：`8001`  
主要健康检查入口：`http://127.0.0.1:8001/api/health`

`run_backend.sh` 做了几件重要事情：

- 清理 `8001` 上的旧进程。
- 激活 `vllm` conda 环境或 `../.venv`。
- 设置 `PYTHONPATH`。
- 设置 `SURG_AGENT_ROOT`。
- 读取 `.env.yolo` 里的 YOLO 设备配置。
- 设置本机代理环境变量，供外部 API 备用。
- 用非 reload 模式启动 uvicorn，避免 MJPEG/SSE 长连接被 reload 干扰。

### 3.2 Electron 前端

```bash
cd /home/user/proj/video_processor/video_stream_app
export DISPLAY=:1
bash run_electron_local.sh
```

如果现场是物理桌面或其他 display，按实际情况改 `DISPLAY`。  
`run_electron_local.sh` 默认：

- 后端地址：`http://127.0.0.1:8001`
- 前端 Vite：`http://127.0.0.1:5133`
- 默认数据源：`capture`
- 自动打开流：`VITE_AUTO_OPEN_STREAM=1`
- 自动连接采集卡/模拟采集卡：`VITE_AUTO_CONNECT_CAPTURE=1`

### 3.3 本地 VLM 服务

当前配置把 GLM/OpenVision/翻译/关键事件/临床报告都指向：

```text
http://127.0.0.1:8010/v1
model: Qwen3-VL-8B-Instruct
```

对应本地脚本：

```bash
cd /home/user/proj/video_processor/video_stream_app
../.venv/bin/python scripts/local_openai_vlm_server.py \
  --model-path models/local_vlm/qwen3-vl-8b-instruct \
  --served-model-name Qwen3-VL-8B-Instruct \
  --host 127.0.0.1 \
  --port 8010 \
  --max-concurrent 1
```

## 4. 核心配置

主配置文件：`config.json`

关键配置如下：

- `services.backend.port = 8001`
- `services.glm.api_url = http://127.0.0.1:8010/v1`
- `services.glm.model_name = Qwen3-VL-8B-Instruct`
- `services.realtime_open_vision.provider = glm`
- `services.realtime_open_vision.candidate_only = true`
- `services.realtime_open_vision.max_images = 2`
- `services.realtime_open_vision.timeout = 14.0`
- `services.realtime_open_vision.thinking_level = none`
- `services.event_nodes.provider = glm`
- `services.event_nodes.timeout = 8.0`
- `services.clinical_summary.provider = glm`
- `video_processing.window_duration = 5.0`
- `video_processing.sample_interval = 1.0`
- `video_processing.frame_storage.storage_base = /home/user/proj/video_processor/video_stream_app/sessions`

专家模型设备分配：

- YOLO 工具检测：`/home/user/proj/surg_agent/detection_expert/runs/yolo26s_cholec_tool/weights/best.pt`，`cuda:1`
- 夹子检测 YOLO：`models/clip_detector/yolo_clip_reviewed_seed_plus_gptimage2_imagebg_100_v1/weights/best.pt`，`cuda:1`
- Phase Expert：`/home/user/proj/surg_agent/phase_expert/runs/cholec_phase/best.pt`，`cuda:0`
- Triplet Expert：`/home/user/proj/surg_agent/triplet_expert/LAM/weights/LAM/CholecTriplet2021/LAM_Lite/model_best.pth.tar`，`cuda:0`

## 5. 数据位置

### 5.1 输入视频

常用测试视频：

- `/home/user/proj/video_processor/test_data/2024-12-24_225315_VID001.mp4`
- `/data/cholec80/cholec80/videos`
- `/home/user/proj/video_processor/video_stream_app/recordings/validation/video12_focus_clip_bag_outbody_validation.mp4`
- `/home/user/proj/video_processor/video_stream_app/recordings/validation/video12_clip_scissors_outbody_validation.mp4`

当前短视频验证主要用了：

```text
recordings/validation/video12_focus_clip_bag_outbody_validation.mp4
```

当前长视频验证主要用了：

```text
recordings/validation/video12_clip_scissors_outbody_validation.mp4
```

### 5.2 会话帧与预览图

所有会话帧在：

```text
/home/user/proj/video_processor/video_stream_app/sessions
```

典型结构：

```text
sessions/20260708_131014_7738e34c_手术室采集卡模拟源/
  frames/      # 原始/分析采样帧
  preview/     # 预览缩略图
  analyzed/    # 可视化/分析相关输出
```

当前目录体量约：

- `sessions`: 约 974M
- `recordings`: 约 15G
- `models`: 约 133G
- `datasets`: 约 3.8G
- `tmp`: 约 1.3G

### 5.3 录屏文件

录屏保存在：

```text
/home/user/proj/video_processor/video_stream_app/recordings
```

近期重要录屏：

- `recordings/electron_short_rerun_no_tail_118s_20260708_131025.mp4`
- `recordings/electron_short_rerun_no_stale_clip_20260708_125852.mp4`
- `recordings/electron_long_validation_180s_20260708_130434.mp4`
- `recordings/electron_clip_cvs_visibility_validation_final4_20260707_064106.mp4`
- `recordings/complete_yolo_only_no_external_video12_20260705_214003.mp4`

近期截图检查位置：

- `recordings/validation/window_24_26_review/source_115_130_tile.jpg`
- `recordings/validation/window_24_26_review/ui_recording_t115.jpg`
- `recordings/validation/short_no_tail_review/t100.png`
- `recordings/validation/short_no_tail_review/t115.png`

### 5.4 临床总结报告

默认报告输出：

```text
docs/clinical_summaries
```

也有部分报告临时放在：

```text
recordings
```

生成报告接口支持传 `output_dir`，所以后续建议统一输出到和录屏同级目录：

```text
recordings/clinical_reports
```

### 5.5 MySQL 数据

配置在 `config.json`：

```json
"database": {
  "mysql": {
    "host": "localhost",
    "port": 3306,
    "user": "jj",
    "password": "",
    "database": "video_analyzer"
  }
}
```

主要表由 `backend/services/mysql_service.py` 定义：

- `video_sessions`: 视频会话信息。
- `analysis_results`: 帧级分析和窗口级摘要，窗口摘要通过 `analysis_type="window"` 区分。
- `chat_history`: 智能问答历史。
- `compressed_summaries`: 压缩后的历史摘要，用于聊天上下文压缩。

注意：`backend/database/crud.py` 仍保留 in-memory session cache 兼容层，但窗口摘要和帧分析实际通过 `get_mysql_service().save_analysis()` 写入 MySQL。

## 6. 后端分析链路

### 6.1 视频输入与播放

主要代码：`backend/routers/video.py`

关键接口：

- `POST /api/video/load`: 加载本地视频路径。
- `POST /api/video/connect-capture`: 连接采集卡或模拟采集卡。
- `POST /api/video/connect-stream`: 连接流地址。
- `POST /api/video/control/{session_id}`: 播放、暂停、seek 等控制。
- `GET /api/video/stream/{session_id}`: 普通视频流。
- `GET /api/video/display-mjpeg/{session_id}`: Electron 显示用 MJPEG。
- `WS /api/video/ws-display/{session_id}`: WebSocket 显示通道。
- `GET /api/video/thumbnail/{session_id}`: 窗口缩略图。

采集卡环境文档：

```text
docs/remote_ubuntu_usage.md
```

现场真实采集卡信息：

- Blackmagic Desktop Video 16.0.1
- DeckLink Mini Recorder 4K
- `/dev/blackmagic/io0`

现场预览命令：

```bash
gst-launch-1.0 decklinkvideosrc device-number=0 mode=1080p30 ! videoconvert ! autovideosink
```

当前非现场测试走模拟采集卡逻辑，视频源表现为：

```text
simulator://capture-card/0
```

### 6.2 实时窗口分析

主要代码：`backend/routers/analysis.py`

重要函数：

- `_expert_snapshot_summary()`：本地专家第一阶段摘要。
- `_apply_surgical_sequence_rules()`：胆囊切除术时序规则和不可逆动作约束。
- `_normalize_packaging_summary()`：进入装袋/取出后抑制过期分离和夹闭描述。
- `_normalize_post_packaging_cleaning_summary()`：取出后尾段文案清理。
- `_local_visibility_cue_from_bgr_frames()`：OpenCV 起雾/体外场景/视野状态提示。
- `stream_summaries()`：SSE 推送窗口摘要给前端。

当前窗口粒度是 5 秒。实时链路会先尽快生成本地专家摘要，然后如果命中候选条件，再调用本地 VLM 做复核/覆盖。

### 6.3 本地专家融合

主要代码：`backend/services/expert_fusion.py`

输入：一个窗口内的 BGR frames。  
输出：`expert_pack`，包括：

- `phase`: Phase Expert 的阶段识别。
- `triplet`: Triplet Expert 的器械-动作-组织三元组。
- `yolo`: 工具 YOLO 检测，如 grasper、hook、bipolar、clipper、scissors。
- `clip_detector`: 专门训练的已释放夹子检测。
- `short_action`: OpenCV 亮白细长器械/尖端短时接触启发式。
- `hemlok_clip`: 早期启发式 Hem-o-lok 候选，目前不应作为强证据。
- `local_visibility`: 起雾、镜头移出体外等本地视觉启发式。

### 6.4 夹子检测

主要代码：`backend/services/clip_detector_service.py`

这个服务是单独的 YOLO 检测器，目标是检测已经释放在组织上的夹体，而不是检测钛夹钳/施夹器。运行时会把 Hem-o-lok 和 titanium subtype 统一折叠为：

```text
surgical_clip / 夹子
```

当前模型：

```text
models/clip_detector/yolo_clip_reviewed_seed_plus_gptimage2_imagebg_100_v1/weights/best.pt
```

当前配置：

```json
"confidence_threshold": 0.05,
"imgsz": 1280,
"max_area_ratio": 0.12
```

注意：`confidence_threshold=0.05` 是检测服务层阈值，用于保留候选；摘要层又有二次门控。2026-07-08 最新修改把摘要层的夹子门控调严：

- 必须 `frames_seen >= 3`
- 必须 `max_confidence >= 0.20`
- 必须处于 `ClippingCutting` 或 `GallbladderDissection`
- 包装/清洁尾段不允许因为亮斑自动生成“胆囊管残端已由夹子闭合”

这个修改是针对短视频里 W14-W16 低置信度夹子误报，以及 W24-W26 尾段不应继续复述夹子闭合的问题。该修改已通过 `python3 -m py_compile` 早前检查，但还需要重新跑短视频完整验证。

### 6.5 时序规则

主要代码：`backend/routers/analysis.py::_apply_surgical_sequence_rules`

当前规则重点：

- 准备阶段进入正式手术后不能回退。
- 胆囊管/胆囊动脉剪断必须满足 CVS 达成和同目标夹闭证据。
- 如果 CVS 尚未达成却出现剪刀切断，要降级成“剪刀在目标邻近区域操作，CVS尚未达成，需核查后再剪断”。
- 已剪断的同一目标后续不再写“正在夹闭”，只能写残端状态。
- 进入装袋/取出后，不再回到肝胆三角解剖、夹闭切断、胆囊分离等阶段。
- 取出后的腹腔内尾段不能默认写“清理术野并确认出血控制”，除非确有冲洗、双极电凝、纱布、活动性出血/止血等证据。

### 6.6 本地 VLM 复核

配置：`services.realtime_open_vision`

目标：

- Hem-o-lok/钛夹/夹子候选。
- 钛夹钳/剪刀/切断动作。
- 标本袋装袋。
- 纱布。
- 活动性出血。
- CVS。
- 起雾/雾解除。
- 镜头移出体外。

当前为了降低延迟：

- `candidate_only = true`
- `max_images = 2`
- `thinking_level = none`
- `timeout = 14s`

本地 VLM 并不是每个窗口都调用。只有本地专家发现关键候选时才调用。

## 7. 前端结构

主要代码：

- `frontend/src/App.vue`: 主界面、会话状态、摘要流、关键事件节点、窗口一览、报告页入口。
- `frontend/src/components/VideoPlayer.vue`: 视频显示、MJPEG/WebSocket/loop window 等。
- `frontend/src/components/ControlBar.vue`: 播放控制、分析控制、进度条窗口标记。
- `frontend/src/components/RightPanel.vue`: 右侧分析/智能问答面板。
- `frontend/src/components/WindowOverview.vue`: 网格一览。
- `frontend/src/components/ClinicalReportView.vue`: 临床报告页。
- `frontend/src/i18n.js`: 中英文 UI 文案。
- `frontend/src/styles/main.css`: 主样式。

### 7.1 底部关键事件节点

原来的「历史窗口分析」UI 已改成底部「关键事件节点」。  
但窗口级摘要没有删除，仍然保存在 MySQL，并且可以通过「全部窗口/窗口一览」查看。

主要前端逻辑：

- `requestEventNodes()`
- `sortedEventNodes`
- `handleEventNodeClick()`
- `enterOverview()`
- `startBottomScrollDrag()`
- `bottomStripHeight`

最新 UI 调整：

- 底部关键事件区域高度上调。
- 本地 localStorage key 改为 `surg_bottom_strip_height_v3`，避免旧高度缓存导致文字只显示一行半。
- 事件卡片摘要 line clamp 调整为 3 行。
- 缩略图高度略降，为文字让空间。

### 7.2 中英文切换

UI 文案由 `frontend/src/i18n.js` 控制。  
窗口摘要英文不是静态翻译，而是调用后端：

```text
POST /api/analysis/translate-summary
```

后端先用本地规则翻译常见术语，必要时走配置里的 `services.translation` 本地 VLM。

注意：中文版本是当前主要验收版本。英文摘要在低延迟情况下可能滞后，且医学术语翻译需要继续校对。

## 8. 关键事件节点与临床报告

### 8.1 关键事件节点

接口：

```text
POST /api/analysis/event-nodes/{session_id}
```

代码：

```text
backend/routers/analysis.py::get_event_nodes
```

输入：该 session 已保存的窗口摘要。  
输出：JSON key events，供底部 UI 展示。  
如果本地 VLM 不可用或超时，会走 fallback 规则：

- CVS 尚未/已达成。
- 夹闭/切断/装袋/取出。
- 大量活动性出血/出血已控制。
- 起雾/雾解除。
- 镜头移出体外。
- 危险剪刀事件。

当前配置里的 event node timeout 是 8 秒，所以实时 UI 不会长期阻塞。

### 8.2 临床精要总结

接口：

```text
POST /api/analysis/clinical-summary/{session_id}
```

代码：

```text
backend/routers/analysis.py::generate_clinical_video_summary
```

输入：

- 该视频的窗口摘要。
- 关键事件节点。

输出：

- 每个视频一个 Markdown 文档。
- 默认在 `docs/clinical_summaries`。
- 支持传 `output_dir` 指定到 `recordings`。

如果 LLM 不可用，接口不会直接失败，会使用 deterministic fallback 生成结构化报告，并在返回里标记：

```json
"source": "deterministic_fallback"
```

## 9. 训练数据与本地模型

### 9.1 本地 VLM 模型目录

```text
models/local_vlm
```

当前已经下载/尝试过的模型包括：

- `qwen3-vl-8b-instruct`
- `qwen3-vl-4b-instruct`
- `qwen3-vl-2b-instruct`
- `qwen3.5-9b`
- `qwen3.5-4b`
- `glm-4.6v-flash`
- `internvl3_5-2b`
- `internvl3_5-4b-instruct`
- `internvl3_5-8b`
- `jina-vlm`
- `minicpm-o-4.5`

当前在线服务用的是：

```text
models/local_vlm/qwen3-vl-8b-instruct
```

### 9.2 夹子检测训练数据

数据集目录：

```text
datasets
```

重要子目录：

- `datasets/cholec80_clipping_samples_dense_v4`
- 其他 GPT 标注、GPT Image 生成、event ROI 相关数据集在 `datasets/` 下。

夹子检测模型目录：

```text
models/clip_detector
```

重要模型：

- `models/clip_detector/yolo_clip_reviewed_seed_plus_gptimage2_imagebg_100_v1/weights/best.pt`
- `models/clip_detector/yolo_clip_eventroi_gpt55_real_plus_gptimage2_yolo11s_1280_stable_v1/weights/best.pt`
- `models/clip_detector/yolo_clip_gpt55_real_plus_gptimage2_positive_yolo11s_1536_v1/weights/best.pt`

相关脚本：

- `scripts/sample_cholec80_clip_phase.py`
- `scripts/sample_clip_positive_windows.py`
- `scripts/gpt_label_surgical_objects.py`
- `scripts/gpt_review_clip_temporal_candidates.py`
- `scripts/generate_clip_synthetic_gpt_image.py`
- `scripts/build_clip_detector_dataset.py`
- `scripts/build_clip_event_roi_dataset.py`
- `scripts/merge_yolo_datasets.py`
- `scripts/train_clip_detector.py`
- `scripts/benchmark_clip_vlm_images.py`
- `scripts/benchmark_local_vlm_candidates.py`

### 9.3 不使用人工标注的策略

当前方向是不做人工标注，采用：

1. 从 Cholec80 和测试视频里自动采样候选片段。
2. 使用强 GPT/VLM 做初筛或框标注。
3. 用 GPT Image 生成一部分夹子合成数据。
4. 训练 YOLO 夹子检测器。
5. 再通过本地 VLM/规则做时序复核。

风险：合成数据和 GPT 标注容易引入偏差，夹子检测现在仍有低置信度亮斑误报，所以摘要层必须有二次门控，不能直接相信 detector 输出。

## 10. 近期验证记录

### 10.1 短视频 no-tail 录屏

文件：

```text
recordings/electron_short_rerun_no_tail_118s_20260708_131025.mp4
```

对应 session：

```text
7738e34c
```

用户指出的问题：

- 没看到夹子，却出现“胆囊管残端已由夹子闭合”。
- W24/W25/W26 写“清理术野并确认出血控制”，但画面实际不是清理/出血控制。
- 短视频最后几秒像卡在几帧。

我抽了源视频 115-130 秒 tile：

```text
recordings/validation/window_24_26_review/source_115_130_tile.jpg
```

肉眼判断：

- 前面是镜头/套管口/体外场景切换。
- 后面是重新进入腹腔后的复查视野。
- 未看到明确“清理术野”或“确认出血控制”动作。

因此最新代码已改：

- 取出后尾段无明确证据时，写“胆囊装袋取出后，镜头重新进入腹腔，进行术野复查。”
- 不再默认写“清理术野并确认出血控制。”
- 低置信度夹子不再进入摘要。

这批修改仍需重新跑短视频验证。

### 10.2 长视频 180 秒录屏

文件：

```text
recordings/electron_long_validation_180s_20260708_130434.mp4
```

对应 session：

```text
334cedb6
```

已观察到：

- CVS 节点出现。
- 装袋/取出节点出现。
- 镜头移出体外节点出现。
- 部分出血/凝血状态出现。

未充分验证：

- 剪刀危险事件。
- 起雾/雾解除。
- 真正的已释放夹子识别准确率。

## 11. 当前已知问题

### 11.1 夹子误报

当前主要问题是夹子 detector 对亮白结构有低置信度误报。之前 W14-W16 证据如下：

- W14: `max_confidence=0.093`
- W15: `max_confidence=0.095`
- W16: `max_confidence=0.136`

这些不应该写成“残端已由夹子闭合”。最新摘要层阈值已调高到 0.20，但还没有完成重新录屏验证。

### 11.2 取出后尾段阶段文案

取出/装袋后，phase expert 可能在 `gallbladder_retraction`、`cleaning_coagulation` 间抖动。旧规则把取出后的非包装阶段统一映射成“清洁凝血”，导致无证据地写“确认出血控制”。

最新规则改为：

- 有明确双极电凝、冲洗、纱布、活动性出血/止血证据时，才写凝血/清理。
- 否则写腹腔复查。

### 11.3 本地 VLM 对细粒度夹子/Hem-o-lok/钛夹不稳定

本地 VLM 在 Hem-o-lok 和 titanium clip 之间会跳。当前产品文案先统一为“夹子”，避免误导 subtype。

### 11.4 关键事件缩略图

关键事件节点有时缩略图加载慢或落到代表窗口的早期帧。前端做了 lazy loading 和更高底部区域，但缩略图代表帧选择仍可优化。

### 11.5 短视频结尾卡帧

之前录制 128 秒版本碰到了源视频结束页，用户看到像卡住。后来改成 118 秒 no-tail 录制，但用户仍指出末尾似乎显示某几帧不动。需要下一轮用修改后代码重新录 115-118 秒尾部，确认是录屏切点、源视频尾段还是播放器状态。

## 12. 近期未提交/需谨慎的代码状态

当前工作树不是干净状态，包含大量历史改动和生成文件。不要随便 `git reset --hard` 或回滚。

主要已修改文件包括：

- `backend/routers/analysis.py`
- `backend/database/crud.py`
- `backend/services/clip_detector_service.py`
- `backend/services/expert_fusion.py`
- `config.json`
- `frontend/src/App.vue`
- `frontend/src/styles/main.css`
- `frontend/src/i18n.js`
- 多个训练/采样脚本

新增/未跟踪目录包括：

- `datasets/`
- `models/`
- `recordings/`
- `runs/`
- `tmp/`
- `tmp_model_configs/`
- `docs/archive/`

最新 2026-07-08 针对用户反馈做的改动集中在：

- `backend/routers/analysis.py`
  - 把 `CleaningCoagulation` 的默认视觉描述从“清理术野、凝血和确认出血控制”改为“胆囊取出后的腹腔视野复查”。
  - `_normalize_post_packaging_cleaning_summary()` 改为无明确证据时输出“胆囊装袋取出后，镜头重新进入腹腔，进行术野复查。”
  - 夹子摘要层门控改为 `frames_seen >= 3` 且 `max_confidence >= 0.20`，并限制阶段。

这个最新改动尚未重新完整录屏验收。

## 13. 推荐下一步验证流程

### 13.1 重跑短视频

1. 确保后端和本地 VLM 正常：

```bash
curl -s http://127.0.0.1:8001/api/health
curl -s http://127.0.0.1:8010/v1/models
```

2. 打开 Electron：

```bash
export DISPLAY=:1
bash run_electron_local.sh
```

3. 加载/连接短视频模拟源：

```text
recordings/validation/video12_focus_clip_bag_outbody_validation.mp4
```

4. 从头开始分析并录屏，建议录 118 秒，避免碰到视频结束页：

```bash
ffmpeg -y -f x11grab -framerate 30 -video_size 3770x2084 -i :1.0+70,76 \
  -t 118 -c:v libx264 -preset veryfast -crf 21 -pix_fmt yuv420p \
  recordings/electron_short_after_tail_review_fix_$(date +%Y%m%d_%H%M%S).mp4
```

5. 验证窗口 14-16 是否还出现无证据“夹子闭合”。
6. 验证窗口 24-26 是否改为体外/腹腔复查，而不是“清理术野并确认出血控制”。
7. 抽尾部截图：

```bash
ffmpeg -y -ss 00:01:55 -i <录屏文件> -frames:v 1 recordings/validation/short_tail_after_fix.jpg
```

### 13.2 重跑长视频

使用：

```text
recordings/validation/video12_clip_scissors_outbody_validation.mp4
```

重点看：

- CVS 节点是否只出现一次持续状态，不反复刷屏。
- 夹子/夹闭是否少误报。
- 装袋和取出是否能进入关键事件。
- 镜头移出体外是否能识别。
- 如果真实有剪刀且 CVS 尚未达成，是否出现红色危险事件。

### 13.3 验证接口

查看 session 列表：

```bash
curl -s http://127.0.0.1:8001/api/video/sessions
```

查看窗口摘要：

```bash
curl -s http://127.0.0.1:8001/api/analysis/summaries/<session_id>
```

生成事件节点：

```bash
curl -s -X POST http://127.0.0.1:8001/api/analysis/event-nodes/<session_id> \
  -H 'Content-Type: application/json' \
  -d '{"language":"zh","force":true,"max_windows":120}'
```

生成临床总结：

```bash
curl -s -X POST http://127.0.0.1:8001/api/analysis/clinical-summary/<session_id> \
  -H 'Content-Type: application/json' \
  -d '{"language":"zh","force":true,"output_dir":"recordings/clinical_reports"}'
```

## 14. 常见排查

### 14.1 后端起不来

不要直接：

```bash
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8001
```

系统 Python 可能缺依赖。用：

```bash
bash run_backend.sh
```

或手动：

```bash
source ../.venv/bin/activate
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8001
```

### 14.2 Electron 看不到变化

确认：

- 后端是不是 `8001`。
- 前端是不是 `5133`。
- `DISPLAY` 是否正确。
- 旧 Electron/Vite 进程是否还在。

查看：

```bash
pgrep -af "electron|vite|uvicorn|local_openai_vlm"
```

### 14.3 分析结果还是旧的

原因通常是：

- 前端还在旧 session。
- MySQL 里旧窗口摘要还在。
- SSE 缓存/前端状态没清。
- Electron 没重启。

建议重新开新的 session，不要在旧 session 上判断新规则。

### 14.4 智能问答

聊天服务走：

```text
backend/services/conversation_service.py
```

上下文来自 MySQL 里的窗口摘要和压缩摘要。之前 SSL 报错多半来自外部 API 或代理链路；当前应优先使用本地 GLM/VLM。

## 15. 给下一个接手人的重点建议

1. 先不要继续扩大功能，先把短视频 W14-W16、W24-W26 的误报修正跑通。
2. 夹子识别不要再只靠 VLM 口头判断，当前更可靠的路线是“YOLO 候选 + 高阈值 + 手术时序规则 + 关键窗口本地 VLM 复核”。
3. 对“出血控制”“凝血处理”要非常保守，只在画面看到双极电凝、冲洗/吸引、纱布压迫或明确出血变化时写。
4. “胆囊管残端已由夹子闭合”属于高置信结论，不能由低置信亮斑或历史状态自动推出。
5. 取出/装袋后的视频尾段经常是体外场景、套管口、重新入腹复查，不要默认写清洁止血。
6. 所有验证都要录屏并抽帧，单看截图很难发现播放卡顿和右侧分析时序问题。
7. 当前工作树很脏，提交前务必分清代码改动、模型数据、录屏产物，不要把 100G 模型和录屏直接纳入普通代码提交。


---

## 16. 2026-07-08 下午更新（第二轮修复与并行验证）

### 16.1 重要事实修正：W14-W16 不是误报

抽帧核查发现，短视频 70-74s（W14-W15）和长视频 99-113s（W20-W22）画面里**确实有一枚蓝色 Hem-o-lok 夹子**留在组织上（很小，容易肉眼漏看）。此前把它们当作"低置信亮斑误报"并把摘要层阈值提到 0.20 的做法，实际把真夹子也压掉了。

关键数据：夹子 YOLO 对真实聚合物夹的置信度天然只有 0.07-0.13；而误检亮斑（如包装阶段）反而可达 0.4-0.6。**置信度阈值无法区分真假夹子，能区分的是跨帧持续性 + VLM 视觉确认。**

### 16.2 本轮代码修改

- `backend/routers/analysis.py`
  - 夹子候选门控新增持续性分支：`detections_total>=8 && frames_seen>=5 && conf>=0.07`（阶段限 ClippingCutting/GallbladderDissection），命中后送本地 VLM 复核确认，确认后才进摘要。直接断言仍保持 0.20 严格门控。
  - 新增 `post_retrieval_review` 时序状态：装袋窗口累计 >=3 个后再出现移出体外/复查文本，才认定"取出已完成"；此后 phase expert 抖回 GallbladderPackaging 时不再回写"将胆囊装入标本袋并准备取出"，改写为术野复查。注意：**单个装袋窗口后短暂移出体外 ≠ 取出完成**（那是镜头退出让标本袋从戳卡进入，装袋还在后面——短视频 85-110s 就是这个模式）。
  - `_normalize_post_packaging_cleaning_summary` 增补尾段过期文案：剪刀邻近操作降级句、"CVS尚未达成/需核查后再剪断/CVS安全核查中"、钛夹钳夹闭句。
  - 事件节点接口支持请求级 `timeout` 覆盖（批量离线可传 90s）；超时错误信息带异常类型（原来 asyncio.TimeoutError 的 str 为空，日志看起来像无错）。
- `config.json`：`realtime_open_vision.timeout` 14→30s；`clip_vlm_review.timeout` 5→20s。根因：本地 Qwen3-VL-8B 单并发下这些调用普遍超过原超时，导致 VLM 复核**从未生效**（170 次调用全部超时静默失败）。
- `backend/services/local_video_source.py` + `backend/routers/video.py`：新增 `filesim://` 源与 `POST /api/video/load?paced=true`——把任意本地文件当"有限实时模拟采集卡"（实时限速、EOF 停止），支持多会话并行分析不同视频。
- 新增 `scripts/batch_record_analysis.py`：并行无界面批量验证编排（切段→paced load→连续分析+摘要→事件节点/临床报告→窗口摘要+事件标题烧录成复查视频）。

### 16.3 验证结果（全部录屏在 recordings/）

- `electron_short_final2_20260708_153204.mp4`（session f1618ae3）：W15 出现"可见1枚夹子已夹闭胆囊管"（VLM 确认真夹子）；W17-21 装袋正确；W22 移出体外；W23-25 术野复查；无卡帧。
- `electron_long_final_20260708_154119.mp4`（session bc843176）：装袋 115-155、复查/体外 155-180、W36 不再回写装袋；无卡帧。注意 W20/21 的真夹子这次 VLM 未确认（单并发延迟波动，属已知限制）。
- `recordings/batch_reviews/`：cholec80 video01/05/18 夹闭段 + video09 尾段的并行批量复查视频与 summaries/events JSON（第一轮为旧代码基线，*_v2 为修复后验证）。
- 事件节点在 timeout=90 时可走通 LLM 路径（source: llm），8s 实时超时仍走 fallback 规则。

### 16.4 遗留问题

1. 本地 VLM 单并发延迟波动：候选窗口的夹子确认成功率不稳定（短视频成功、长视频同一场景未确认）。可选方向：clip_vlm_review 换更小模型（InternVL3.5-2B 已下载）、VLM 服务提高并发、或对持续候选做跨窗口重试。
2. 夹闭段"钛夹钳夹闭胆囊管/胆囊动脉"目标在相邻窗口间跳变（triplet 目标倾向不稳），且连续 20+ 窗口重复"正在夹闭"；建议后续加"同目标已夹闭则改写残端状态"的窗口间去重。
3. cholec80 夹闭段从未出现"CVS达成"与剪刀切断确认（保守方向正确，但漏报真实剪断事件）。
4. SurgR1 外部 API 一直连接失败（日志有 All connection attempts failed），当前全靠本地专家兜底，行为正常但日志噪音大。

---

## 17. 2026-07-09 VLM 夹子识别选型与服务切换

### 17.1 结论

- **生产 VLM 保留 Qwen3-VL-8B-Instruct，但服务从自制 transformers 服务器切换为 vLLM**（`scripts/run_local_vlm_vllm.sh`，GPU2、端口 8010、模型名不变，config.json 零改动）。单张 640px 图推理 ~0.6-0.7s（原 1.3-3s），且原生并发：4 并发请求 0.76s 完成——实时链路的 VLM 排队超时问题在服务层根治。
- 修正标签后的二分类对比（147 样本 = 种子集 + 硬样本集 `datasets/clip_binary_hard_v1`，生产 prompt，`runs/clip_vlm_binary_benchmark_v2/rescored_summary.json`）：Qwen3-VL-8B 是唯一"小蓝夹 7/7 找到 + 亮斑/体外 13/14 正确拒绝"的模型（recall 0.77 / spec 0.97）。internvl3.5-4b recall 更高（0.885）但会把体外亮斑判成夹子；InternVL3.5-8B 和 Qwen3-VL-30B-A3B-AWQ 在 640px 全图下小夹子全漏（更大不更好）；qwen3.5 系是思考模型出不来 JSON 且 7s+；2B 级两个模型分别是全判有/全判无。
- **vLLM 0.22 完全支持 InternVL**（此前"vLLM 不支持 InternVL"的结论不成立）。两个坑：需 `VLLM_USE_FLASHINFER_SAMPLER=0`（flashinfer 采样器 warmup 崩溃）和 LD_LIBRARY_PATH 指向 `../.venv/lib/python3.10/site-packages/nvidia/cu13/lib`；杀 vLLM 必须连 `VLLM::EngineCore` 子进程一起杀，显存异步释放需等数十秒。
- 检测框裁剪送 VLM（crop 模式）对 Qwen3-VL-8B 无增益（0.686 vs 全图 0.77），维持全图 2 张 640px 的现有方式。

### 17.2 重大数据发现：种子数据集负样本污染

人工目检发现 `datasets/clip_detector_reviewed_seed_v1` 的 98 张 reject_* 负样本中**至少 64 张实际含清晰可见的已释放夹子**（银灰钛夹、深蓝聚合物夹为主——GPT 自动审核系统性漏检小而暗的夹体）。修正表：`datasets/clip_detector_reviewed_seed_v1/label_corrections_20260709.json`；重评分脚本：`scripts/rescore_clip_benchmark.py`。

影响：① 夹子 YOLO 训练时大量真夹被当负样本，直接解释了真夹置信度只有 0.07-0.13 的现象；② 此前所有以该数据集为基准的"特异度/误报率"结论不可信。

### 17.3 建议下一步

1. **用修正标签重训夹子 YOLO**（含同一 GPT 管线产出的其他数据集也应先审核），预期真夹置信度显著上升后，可回调摘要层 0.20 阈值与持续性候选门控。
2. 30B AWQ 模型已下载在 `models/local_vlm/qwen3-vl-30b-a3b-awq`（17GB，单卡可跑，0.33s/图），可作为离线批量审核/自动标注的高吞吐选项。
3. 运维：VLM 服务启动改用 `bash scripts/run_local_vlm_vllm.sh`（替代 local_openai_vlm_server.py）。

---

## 18. 2026-07-09 夹子 YOLO 重训（修正标签）

### 18.1 数据修正与重训

- 框恢复：`scripts/recover_clip_boxes.py`（旧检测器 conf 0.005 出候选框 → 裁剪 → vLLM 确认+材质分类 → 渲染预览人工抽审）。49/64 张恢复出框，目检剔除 16 张含坏框（高光/器械杆/钳口被 VLM 误确认——小裁剪缺乏上下文是主要误确认原因），保留 33 张干净正样本。
- v2 数据集：`scripts/build_clip_dataset_v2.py` → `datasets/clip_detector_corrected_v2`（剔除全部 68 张污染/不确定图；加回 33 张恢复正样本；另用 cholec80 阶段标注从未用于评测的视频挖了 48 张"ClippingCutting 开始前"的保证无夹负样本）。train 205（146 正/59 负）、val 39。
- 训练：`scripts/train_clip_detector.py --base-model yolo11s.pt --imgsz 1280 --epochs 80`（GPU1，9 分钟）→ `models/clip_detector/yolo_clip_corrected_v2/weights/best.pt`，config.json 已切换。

### 18.2 新旧对比（runs/clip_yolo_binary_benchmark/compare_old_new.json）

- 硬正样本（7 张已核实蓝夹帧）：置信度约翻倍（0.03-0.07 → 0.03-0.14），0.10 阈值下命中 4/7（旧 0/7）。
- 装袋亮斑高置信误报消失（旧 0.15-0.61 → 新 0.06-0.07）。
- 负样本尾部两者相当（个别 0.15-0.16 FP），阈值仍无法单独区分真假——**VLM 确认仍是必要环节**，链路为"持续候选 → VLM 确认 → 写入摘要"。
- 注意：修正 val 集上旧模型框级 mAP 更高（新模型框更多更噪），但管线关心的是候选召回+置信分离，以管线复跑为准。

### 18.3 管线复跑结果（recordings/batch_reviews/*yolo_v2*）

- 短视频（session 640a1f07）：W14"可见1枚夹子已夹闭胆囊管"保持 ✓；W16"胆囊管残端已由夹子闭合"（cd 0.222 直接断言，且上一窗口刚有 VLM 确认的夹子，证据链完整）；装袋/体外/复查时间线正确。
- 长视频（session 9764bf48）：**W20/W21 首次报出"可见1枚夹子已夹闭胆囊管"**（此前所有运行都漏），检测持续性 16-18 次/12 帧、conf 0.12-0.19 走持续候选→VLM 确认；尾段无回退无过期文案。
- 遗留观察：长视频 W14-18（肝胆三角期）检测器有 0.21-0.31 的检测（阶段门控挡住未进文案）；短视频 W0/W5 同类情况。下一轮数据迭代可针对性挖负样本。
