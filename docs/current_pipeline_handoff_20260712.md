# 本地视频分析交接文档（2026-07-13 验收版）

项目目录：`/home/user/proj/video_processor/video_stream_app`
本文档取代 `docs/current_pipeline_handoff_20260708.md`（旧文档保留作为历史记录，其第 1-15 节部分信息已过时，第 16-18 节是 07-08/07-09 两轮修复的详细记录）。

---

## 1. 系统定位（一句话）

本地 Electron 腹腔镜胆囊切除术视频分析工具：模拟/真实采集卡输入 → 5 秒窗口实时摘要（本地专家模型 + 本地 VLM 复核）→ 关键事件节点 / 窗口一览 / 智能问答 / 临床总结报告。全链路本地推理，无手术数据外流。

## 2. 当前服务拓扑（3×RTX 4090）

| 服务 | 启动方式 | 端口 | GPU |
|---|---|---|---|
| 后端 FastAPI | `DISABLE_EXTERNAL_AI=1 DISABLE_EMBEDDINGS=1 bash run_backend.sh` | 8001 | phase/triplet GPU0，YOLO/clip GPU1 |
| 本地 VLM 主实例（vLLM） | `bash scripts/run_local_vlm_vllm.sh` | 8010 | GPU2，12K context |
| 本地 VLM 批量辅助实例 | `vllm serve ... --port 8011 --max-model-len 4096` | 8011 | GPU1，4K context |
| 本地 VLM 负载均衡器 | `scripts/local_vlm_balancer.py` | 8012 | 图像请求按在途数分配；长文本固定到 8010 |
| Electron 前端 | 在本机 Ubuntu 桌面执行 `export DISPLAY=<本地显示号> && bash run_electron_local.sh` | Vite 5133 | Electron GPU 加速 |
| 模拟采集卡（按需） | `../.venv/bin/python ../stream_simulator/http_server.py --video <mp4> --port 9001` | 9001 | — |
| 旧 SurgR1 API | 已停用（`services.surgr1.enabled=false`） | 9003 不需要启动 | — |

Electron、后端、模拟采集卡和 VLM 都在本机运行。`DISPLAY` 只是本地 Ubuntu 桌面的显示号，不涉及远程桌面或 X11 转发。

**重要变更（2026-07-09）**：本地 VLM 不再用 `scripts/local_openai_vlm_server.py`（transformers 单并发，是历史上 VLM 复核全部超时的根因）。现用 vLLM（`scripts/run_local_vlm_vllm.sh`，同端口同模型名 Qwen3-VL-8B-Instruct，config 无需改动）。单图 ~0.6-0.7s，原生并发。

**完整长视频批量验收补充（2026-07-13）**：可额外在 GPU1 启动 8011 辅助实例，并令后端带 `GLM_API_URL=http://127.0.0.1:8012/v1` 启动。8012 仅把视觉请求分流到两张卡；事件节点和临床报告的长文本请求固定发往 12K context 的 8010，避免 4K 辅助实例因上下文长度失败。8010 短暂断连时均衡器会自动重试 8011。

健康检查：
```bash
curl -s http://127.0.0.1:8001/api/health
curl -s http://127.0.0.1:8010/v1/models
curl -s http://127.0.0.1:8011/v1/models
```

### vLLM 运维三个坑（必读）

1. 环境：脚本内已处理 `LD_LIBRARY_PATH=../.venv/lib/python3.10/site-packages/nvidia/cu13/lib` 和 `VLLM_USE_FLASHINFER_SAMPLER=0`（flashinfer 采样器在 warmup 崩溃）。
2. 杀进程：`pgrep -f "vllm serve" | xargs kill -9` 之后**必须再杀 `VLLM::EngineCore` 子进程**，且显存异步释放需数十秒——重启前轮询 `nvidia-smi` 等显存归零，否则下一个实例报 OOM。
3. vLLM 0.22 完全支持 InternVL（`InternVLChatModel` 在注册表）——不要相信"vLLM 不支持 InternVL"的旧说法。

## 3. 核心配置（config.json 当前生效值）

- `services.glm / translation / event_nodes / clinical_summary / realtime_open_vision / clip_vlm_review / scissors_vlm_review / visibility_vlm_review` 全部指向 `http://127.0.0.1:8010/v1`，模型 `Qwen3-VL-8B-Instruct`
- `realtime_open_vision.timeout = 90`；三个低频专项复核 timeout 均为 60 秒。并发时允许排队，最终导出前必须等待 `pending-refinements=0`
- 每个完整 5 秒窗口固定做一次通用结构化 VLM；夹子、剪刀、体外/标本袋只在候选窗口追加专项复核。实际每窗 1-4 次本地调用，不调用外部 GPT/Gemini
- `window_analysis.live_stage2_enabled=false`：实时路径只保留“本地专家即时结果 → 结构化本地 VLM 覆盖”两步，禁止第二个摘要模型异步覆盖已验证结果
- `services.surgr1.enabled=false`：只用本地 phase/triplet/YOLO/VLM；旧 9003 API 不再检查、不再重复解码、不再产生连接失败日志
- `services.embedding.enabled=false`：本地部署不向 Gemini embedding 发送摘要
- `event_nodes.timeout = 8`（UI 实时路径超时走规则聚合；批量验收传 120 秒走本地 LLM，再由确定性证据规则收口）
- `services.clip_detector.model_path = models/clip_detector/yolo_clip_corrected_v2/weights/best.pt`（07-09 用修正标签重训），`confidence_threshold=0.05`，`imgsz=1280`，device cuda:1
- `video_processing.window_duration=5.0`，`sample_interval=1.0`；只处理完整窗口，有限视频 EOF 时从已保存帧恢复最后完整窗口，禁止分析越过视频时长
- 帧存储：`sessions/`；MySQL 库 `video_analyzer`（表见 `backend/services/mysql_service.py`）

## 4. 夹子识别链路（当前设计，请勿回退成"调阈值"）

```
clip YOLO (conf 0.05 保候选)
  → 窗口聚合 (detections_total / frames_seen / max_confidence)
    → 直接断言门控：frames>=3 && conf>=0.20 && 阶段∈{ClippingCutting, GallbladderDissection}
    → 持续候选门控：total>=8 && frames>=5 && conf>=0.07（同阶段）→ 送 VLM 复核
      → clip_vlm_review（Qwen3-VL-8B, 全图 640px, 2 张）确认后才写入摘要
```

背景事实（详见 memory 与旧文档 16-18 节）：
- 真实聚合物夹（蓝/白 Hem-o-lok）在旧模型下置信度只有 0.07-0.13，与亮斑误检重叠——**置信度阈值无法区分真假夹**，能区分的是跨帧持续性 + VLM 视觉确认。
- 重训后（yolo_clip_corrected_v2）真夹置信度约翻倍（0.09-0.19），装袋亮斑 0.4-0.6 级高置信误报消失，但 FP 尾部仍到 ~0.16，VLM 确认环节不可去掉。
- 评测证明 Qwen3-VL-8B 是唯一"小蓝夹 7/7 + 亮斑/体外 13/14"双优的本地模型；更大的 30B-A3B-AWQ 和 InternVL3.5-8B 在 640px 全图下小夹子全漏（更大≠更好）；检测框裁剪模式对 8B 无增益。对比数据：`runs/clip_vlm_binary_benchmark_v2/rescored_summary.json`。

## 5. 数据集与标签污染（接手前必须知道）

**`datasets/clip_detector_reviewed_seed_v1` 的 98 张 reject_* 负样本中至少 64 张实际含真夹子**（GPT 自动审核系统性漏检小而暗的金属钛夹/深蓝聚合物夹）。修正表：`datasets/clip_detector_reviewed_seed_v1/label_corrections_20260709.json`。

- 任何用该数据集或同一 GPT 审核管线产出数据做训练/评测，先应用修正表；重评分脚本 `scripts/rescore_clip_benchmark.py`。
- 当前训练集：`datasets/clip_detector_corrected_v2`（train 205 = 146 正/59 负；负样本含 48 张"ClippingCutting 开始前"阶段保证无夹的自动挖掘负样本）。
- 框恢复管线：`scripts/recover_clip_boxes.py`（旧检测器低阈值候选框 → VLM 裁剪确认 → 渲染预览人工抽审）。注意 VLM 在小裁剪上会把高光/器械杆误确认为夹子（49 张恢复里 16 张含坏框被剔除），**恢复结果必须目检预览**。
- 人工核实的评测硬样本：`datasets/clip_binary_hard_v1/`（7 正=video12 蓝夹帧，14 负=亮斑/体外/戳卡，heldout_neg/ 20 张留出视频负样本）。
- 训练：`scripts/train_clip_detector.py --data datasets/clip_detector_corrected_v2/data.yaml --base-model yolo11s.pt --imgsz 1280 --epochs 80 --device 1`（约 10 分钟）。
- 新旧对比工具：`scripts/compare_clip_detectors.py --extra-neg-dir datasets/clip_binary_hard_v1/heldout_neg`。

## 6. 验证工具链

### 6.1 并行无界面批量验证（首选，07-08 新增）

任意本地视频可当"有限实时模拟采集卡"并行分析（`POST /api/video/load?paced=true` 存成 `filesim://` 源，实时限速、EOF 停止）：

```bash
../.venv/bin/python scripts/batch_record_analysis.py \
  --spec recordings/batch_reviews/<spec>.json \
  --out-dir recordings/batch_reviews/<run_name> \
  --parallel 2 --postprocess-workers 1 --render-workers 1
```

spec 条目：`{"video": "<abs path>", "start": 秒, "duration": 秒, "label": "名字"}`（start/duration 为 0 表示整段）。
产物：`<label>_review.mp4`（底部窗口摘要+顶部事件标签字幕烧录）、`<label>.summaries.json`（含 `others.experts` 完整专家原始输出）、`<label>.events.json`、每视频独立临床报告、`quality_report.json` 和 `batch_results.json`。
选段可用 `/home/user/data/cholec80/cholec80/videos/videoNN-timestamp.txt`（帧级阶段标注；`ClippingCutting` 起点前的帧保证无夹）。

**并发结论**：一张 4090 上的 Qwen3-VL-8B 在每窗通用分析并叠加专项复核时，`--parallel 2` 能保持实时覆盖；4 路会让 VLM 请求超过旧 timeout，不适合作为质量验收默认值。若只跑 phase/triplet/YOLO 可提高并发；若要 4 路完整 VLM，需要启动第二个独立 VLM worker。正式 Electron UI 仍一次展示/录制一个会话，批量质量测试使用本脚本并行生成多个复查视频，不让多个桌面录屏抢 GPU 编码与窗口焦点。

批量跑完整 Cholec80 时，把多个条目写入同一个 spec，保持 `--parallel 2`。脚本先以真实时间节奏并行分析，所有会话结束后才进入事件/报告和 NVENC 复查视频渲染阶段；因此每条视频都有独立录屏式 MP4、JSON 和 Markdown 报告，同时不会让桌面捕获与 VLM 推理争抢实时资源。需要覆盖 80 条整视频时按时长拆成多批顺序执行即可；不要直接起 80 个 Electron 窗口。

脚本内置质量门禁 `scripts/validate_batch_review.py`，检查：窗口连续覆盖、VLM 是否全部收敛、剪刀/体外/夹子结构化结论与文案是否矛盾、CVS 风险是否有视觉证据、事件是否重复、报告是否出现运行时诊断、复查视频时长是否匹配。任一错误返回码为 2。

### 6.2 Electron UI 录屏验证（正式验收格式）

1. 起模拟采集卡挂目标视频（见第 2 节；**换视频必须重启模拟器**，流位置是全局的）
2. `DISPLAY=:1 bash run_electron_local.sh`（自动连接+自动开始分析）
3. 会话出现后录屏：`ffmpeg -y -f x11grab -draw_mouse 0 -framerate 30 -video_size 3770x2084 -i :1+70,76 -t <秒> -c:v h264_nvenc -preset p4 -cq:v 21 -pix_fmt yuv420p recordings/<名字>.mp4`
   （可参考流程脚本样例：录制前先 poll `GET /api/video/sessions` 等新 processing 会话出现）
4. 完毕后杀干净 electron/vite/simulator，否则下轮端口冲突

有限模拟采集卡自然到 EOF 时，前端只停止播放并把位置固定到源时长，不调用用户主动停止接口；后端会先补齐最后一个完整 5 秒窗口，再将会话置为 `completed`。100 秒视频的验收条件是位置 `100.0s`、窗口 id `0..19` 连续、播放栏与右侧均显示“窗口 20”。

### 6.3 结果核对接口

```bash
curl -s http://127.0.0.1:8001/api/analysis/summaries/<session_id>       # 窗口摘要（含 others.experts）
curl -s -X POST http://127.0.0.1:8001/api/analysis/event-nodes/<sid> -H 'Content-Type: application/json' \
  -d '{"language":"zh","force":true,"max_windows":120,"timeout":90}'    # timeout 90 走 LLM，8 走 fallback
curl -s -X POST http://127.0.0.1:8001/api/analysis/clinical-summary/<sid> -H 'Content-Type: application/json' \
  -d '{"language":"zh","force":true,"output_dir":"recordings/clinical_reports"}'
```

## 7. 时序规则要点（backend/routers/analysis.py）

- `_build_surgical_sequence_state()` 从已存窗口重建不可逆状态；`_apply_surgical_sequence_rules()` 应用。
- 阶段不可逆：已进入夹闭切断、胆囊床分离、装袋、标本袋牵拉取出后，不允许回退成早期肝胆三角分离。
- 剪刀判断采用专项三帧全图复核，明确区分双刃剪刀、电凝钩白色陶瓷头、抓钳和施夹器。CVS 未达成时确认剪刀活动，生成红色风险节点；模型未确认实际剪断时只写“剪刀在操作区域内活动/需核查”，不虚构不可逆切断。
- 夹子只统一称“夹子”；主 VLM 判断后再做 deployed-clip 专项复核。只看到施夹器、白色长杆、陶瓷头或高光不能写“已释放夹子”。
- 体外场景采用通用 VLM + 候选窗口专项复核，明确区分腹壁外皮肤/套管/器械盘、腹腔内标本袋和镜头起雾；视觉融合不使用视频名、时间戳或固定帧号。
- 关键事件保留底层全部 5 秒历史窗口，但 UI 只呈现 phase、CVS、夹闭/剪刀风险、装袋/取出、明显出血、起雾/解除和体外等高价值节点。相同语义的阶段/操作节点会去重；非连续证据窗口在复查视频里只于实际命中窗口显示标签。
- `post_retrieval_review` 状态（07-08 新增）：**累计 >=3 个装袋窗口后**再出现移出体外/复查文本，才认定"取出完成"，此后 packaging 阶段抖动改写为术野复查。⚠️ 单个装袋窗口后短暂移出体外 ≠ 取出完成（那是镜头退出让标本袋经戳卡进入，装袋还在后面——video12 短验证视频 85-110s 就是该模式，改这里前先跑它回归）。
- `_normalize_post_packaging_cleaning_summary()`：取出后尾段清理过期文案（夹闭句、剪刀邻近警告句、CVS 核查句都在 stale_patterns 里）；有双极电凝/冲洗/纱布/出血证据的具体文案会保留。
- 剪断必须 CVS 达成 + 同目标已夹闭，否则降级为"邻近区域操作，需核查后再剪断"。

## 8. 已知问题与建议下一步

1. Cholec80 没有逐帧 CVS 三要素金标；当前只有图像三要素都高置信成立才显示绿色“CVS已达成”，否则保持安全评估状态。保守不误报优先，不能把“进入夹闭阶段”当作 CVS 达成。
2. 胆囊管/胆囊动脉目标仍受 Triplet 与单窗 VLM 可见度限制。报告保留“需医生回看目标与夹体状态”，不得把低置信二选一写成确定临床事实。
3. 起雾启发式对近距离白色标本袋敏感；现已用专项 VLM 复核否决该误报。更换本地 VLM 后必须重跑 `scope_exit_validation_v2`。
4. 事件节点 UI 实时请求默认 8 秒，超时会走确定性规则；离线报告用 120 秒本地 LLM 后再走证据收口。两者节点内容应一致，但文字措辞可能不同。
5. 英文 UI 基础翻译链路存在，但本轮正式验收范围是中文；英文医学术语仍应单独做医生审核。
6. 30B AWQ 已下载（`models/local_vlm/qwen3-vl-30b-a3b-awq`，17GB）但小目标夹子基准不如当前 8B；不要仅因参数更多替换实时模型。
7. **工作树包含多轮未提交代码与大量生成产物**。提交时只纳入源代码、配置、脚本和文档；`models/`、`recordings/`、`sessions/`、数据集和日志不要进 git。

## 9. 关键产物索引

| 内容 | 位置 |
|---|---|
| 2026-07-13 三条 Cholec80 完整长视频复查、JSON、SRT、独立报告 | `recordings/final_long_video_tests_20260713/` |
| 2026-07-13 Electron 最终验收录屏（100 秒完整流程） | `recordings/electron_cholec80_final_acceptance_20260713.mp4` |
| 2026-07-13 Cholec80 四段并行验收（视频、JSON、独立报告、质量门禁） | `recordings/batch_reviews/cholec80_final_acceptance_20260713/` |
| 体外/标本袋/起雾专项最终回归 | `recordings/batch_reviews/scope_exit_validation_v3_optimized/` |
| 剪刀候选帧优先复核（真剪刀/电凝钩/短暂候选） | `recordings/batch_reviews/scissors_candidate_priority_20260713/` |
| 最终验收录屏（07-08 修复后） | `recordings/electron_short_final2_20260708_153204.mp4`、`recordings/electron_long_final_20260708_154119.mp4` |
| 新 YOLO 管线复跑复查视频 | `recordings/batch_reviews/short_yolo_v2_review.mp4`、`long_yolo_v2_review.mp4` |
| cholec80 批量复查视频（4 段基线 + v2 验证段） | `recordings/batch_reviews/` |
| VLM 选型对比（修正标签后） | `runs/clip_vlm_binary_benchmark_v2/rescored_summary.json` |
| 新旧 YOLO 对比 | `runs/clip_yolo_binary_benchmark/compare_old_new.json` |
| 标签修正表 | `datasets/clip_detector_reviewed_seed_v1/label_corrections_20260709.json` |
| 新训练权重 | `models/clip_detector/yolo_clip_corrected_v2/weights/best.pt`（已上线） |
| 历史详细记录 | `docs/current_pipeline_handoff_20260708.md` 第 16-18 节 |

## 10. 常见排查

- 后端起不来：别用系统 python，用 `bash run_backend.sh`（自动激活 `../.venv` 或 conda vllm 环境）。
- VLM 超时/失败：先 `curl :8010/v1/models`；日志里空错误信息=超时（已修为带异常类型，但老日志如此）。
- 分析结果像旧的：开新 session，别在旧 session 上判断新规则；确认后端进程启动时间晚于代码修改时间。
- 模拟采集卡播放位置不对：重启 http_server.py（流位置全局，不按连接重置）。
- 残留进程清理：`pgrep -af "electron|vite --host|http_server.py|vllm serve|VLLM::EngineCore"`。

## 11. 代码与数据接手索引

### 11.1 每个 5 秒窗口的实际处理顺序

1. `backend/services/frame_capture_service.py` 以采集卡节奏保存帧；有限本地视频通过 `filesim://` 包装成不循环、EOF 可检测的实时源。
2. `backend/routers/analysis.py::glm_summarization_task()` 只在一个完整 5 秒窗口结束后取样，不提前生成同一窗口的半成品；EOF 后从帧存储恢复最后完整窗口。
3. `run_experts_on_window()` 同步运行 Phase Expert、Triplet Expert、器械 YOLO、夹子 YOLO 和 OpenCV 可见性线索，先写入低延迟 Stage 1。
4. `_open_vlm_realtime_hint()` 对 3 张全图调用本地 Qwen3-VL-8B，统一判断夹子、施夹器、剪刀、目标结构、标本袋、纱布、活动性出血、CVS、起雾和体外场景。
5. 仅在候选窗口追加三类全图专项复核：deployed clip、剪刀形态、体外皮肤/套管/标本袋/雾。任何专项否决都必须覆盖通用 VLM 或 YOLO 的冲突文案。
6. `_apply_surgical_sequence_rules()` 应用不可逆阶段、CVS 前剪刀风险、先夹闭后剪断、装袋后不回退等规则，再覆盖同一个窗口记录。
7. `_ensure_required_event_nodes()` 从所有已保存窗口重建关键节点并去重；底层历史窗口不删除。`_build_deterministic_clinical_report()` 生成医生复盘 Markdown，一条视频对应一份文档。

### 11.2 关键代码

| 功能 | 文件/入口 |
|---|---|
| 分析主链路、事件、报告、Chat API | `backend/routers/analysis.py` |
| 单一帧采集与 EOF 状态 | `backend/services/frame_capture_service.py` |
| 本地有限视频实时模拟 | `backend/services/local_video_source.py` |
| 器械 YOLO | `backend/services/yolo_service.py` |
| 夹子 YOLO | `backend/services/clip_detector_service.py` |
| Electron 窗口/GPU | `frontend/electron/main.cjs` |
| 主界面与关键事件状态节点 | `frontend/src/App.vue` |
| 最新/上一窗口与历史合并展示 | `frontend/src/components/RightPanel.vue` |
| 报告独立页面 | `frontend/src/components/ClinicalReportView.vue` |
| 并行分析、报告及复查视频生成 | `scripts/batch_record_analysis.py` |
| 自动质量门禁 | `scripts/validate_batch_review.py` |
| 双 VLM 图像负载均衡 | `scripts/local_vlm_balancer.py` |
| 已保存结果的剪刀/视野补审 | `scripts/backfill_scissors_review.py`、`scripts/backfill_visibility_review.py` |
| 最终摘要证据门控与重渲染 | `scripts/sanitize_saved_summaries.py`、`scripts/rerender_saved_reviews.py` |
| 多批结果合并 | `scripts/merge_batch_results.py` |

### 11.3 数据和持久化

- Cholec80 视频：`/home/user/data/cholec80/cholec80/videos/videoNN.mp4`
- Cholec80 阶段标注：同目录 `videoNN-timestamp.txt`
- 项目测试视频：`/home/user/proj/video_processor/test_data/`
- 实时保存帧：`/home/user/proj/video_processor/video_stream_app/sessions/`
- 分析数据库：MySQL `video_analyzer`；窗口摘要、会话和 Chat 历史的表结构/访问在 `backend/services/mysql_service.py`
- 复查视频、JSON、报告和 Electron 录屏：`recordings/`
- 夹子检测训练/评测数据：`datasets/clip_detector_corrected_v2/`、`datasets/clip_binary_hard_v1/`
- 模型：`models/`；该目录体积很大，不进入 git

### 11.4 CVS 与报告验收结论

- CVS UI 机制已实现：进入相关阶段即生成置顶持久节点；未达成显示红色，确认达成显示绿色；剪刀在未达成状态出现时独立生成红色风险节点。
- 当前 Cholec80 回归段没有可信逐帧 CVS 三要素金标，模型结果为 `assessing/partial`，因此本轮没有人为强制制造绿色节点。绿色分支代码存在，但“CVS 阳性识别准确率”仍需带医生标注的阳性/阴性片段单独验收。
- Summary 功能已实现为每视频独立 Markdown 流水线；报告只保留阶段、关键操作、CVS/安全、明显出血与视野事件、医生回看点，不逐窗口记流水账，也不输出模型/provider/异常诊断。
- 2026-07-13 最终 Electron 会话 `bf894cd6` 实际跑满 100 秒：状态 `completed`，窗口 `0..19` 连续，播放栏和右侧最新窗口均停在窗口 20；视频区域冻结检测在 `100.33s` 自然 EOF 前没有大于 1 秒的新增冻结。
- 同轮 Cholec80 批量验收覆盖 video02、video14、video23 的夹闭/分离、装袋/取出、体外、起雾恢复等 4 个片段，`quality_report.json` 全部为 `pass`。`40-45s` 器械冲突经十帧复核没有可靠剪刀双刃证据，专项 VLM 否决 YOLO 剪刀候选，避免生成 CVS 前剪刀误警。

### 11.5 三条完整长视频验收（2026-07-13）

- 最终目录：`recordings/final_long_video_tests_20260713/`，含 video01、video05、video09 三条完整复查 MP4、每窗 JSON、关键事件 JSON/SRT、每视频独立 Markdown、总质检和阶段对齐报告。
- 总覆盖 1:52:59.12，共 1,357 个窗口；三条 `quality_report.json` 均为 `pass`，无 error/warning。
- 阶段有效准确率：video01 99.71%，video05 100%，video09 100%；三条均无缺失阶段。
- 复查 MP4 的分辨率、25 FPS、时长和总帧数与源视频逐项一致，没有因字幕烧录引入丢帧或变速。
- 新增证据门控：目标结构置信度不足时只写“夹子已夹闭目标组织，具体目标需回看确认”；进入胆囊分离后不再展示未激活的施夹器；体外/套管/手术室复核在所有阶段启用，不再只限装袋尾段；专项场景分类最终覆盖冲突的起雾/体外文案。
