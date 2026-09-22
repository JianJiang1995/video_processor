# 网格回放、智能问答与重新分析

更新：2026-09-22。

## 操作入口

1. 正常连接模拟源或采集卡，等待至少一个窗口生成分析。
2. 点击下方“网格视图”，再点击任意窗口缩略图。
3. 弹窗左侧独立循环播放该窗口；右侧显示对应的分析摘要与“智能问答”。
4. 每条分析右侧的“重新分析”调用本机视觉模型重新读取该窗口图像。
5. 成功后替换当前分析；失败保留原文并显示可重试的错误提示。

播放/暂停只控制弹窗视频。关闭回看或返回主界面不会对主视频执行暂停、跳转或重置。
问答中的时间戳可以继续打开对应回放。跨多个窗口的时间段按窗口分别提供重新分析按钮。

## 实现

### 界面

- `frontend/src/components/VideoReviewModal.vue`：独立回放、分析列表、片段问答、复核按钮与状态。
- `frontend/src/components/WindowOverview.vue`：网格入口与事件转发；显示编号从 1 开始，数据库仍从 0 开始。
- `frontend/src/App.vue`：持有复核请求状态，成功后更新共享窗口结果。关闭弹窗不取消正在保存的复核；切换会话不会把旧请求结果写到新会话。较旧的自动结果和较旧的人工触发复核响应不能覆盖新复核。
- `frontend/src/i18n.js`：中英文按钮、状态和错误提示。复核摘要仍走现有英文翻译流程，旧摘要的迟到翻译不会覆盖新摘要。

### 本地视觉复核

接口：`POST /api/analysis/reanalyze-window/{session_id}/{window_id}`。

- 后端从数据库取得原窗口时间范围，不接受客户端提供的摘要或任意时间覆盖。
- 优先从会话保存的图像中均匀选择最多 4 帧，严格限制在 `[start, end)`。
- 图像最长边缩至 640 像素；已按四分图配置裁剪的缓存帧不会再裁一次。
- 普通本地文件及 `filesim://` 可以用独立解码器抽帧，遵循输入布局配置。解码在工作线程，最多使用 4 个 FFmpeg 解码线程，不接触主采集进程。
- 对采集卡或 `simulator://` 设备，如果历史帧已不存在，明确报错。不会打开实时设备，更不会用模拟器当前绑定的新视频冒充历史视频。
- 使用 `LOCAL_VISUAL_PROMPT`，不附旧摘要作为答案提示；需要重新观察器械、操作、组织、出血、CVS、视野等证据。
- 只允许本机 loopback 地址的 GLM-compatible 模型服务，目前是 `Qwen3-VL-8B-Instruct`。没有外部 GPT 回退。
- 同时最多一个手动复核任务，模型调用超时为 90 秒，不大量挤占实时分析请求。

相关代码：`backend/routers/review.py`、`backend/services/window_reanalysis.py`。

### 保存与一致性

`backend/services/mysql_service.py` 在数据库事务中锁定原窗口行，保存：

- 最新分析摘要与结构化视觉结果；
- `others.manual_review`：修订 ID、模型名、完成时间、采样帧数及实际时间戳；
- `others.review_history`：被替换的摘要、视觉结果及前一次复核信息。

原始专家输出仍保留，历史窗口不删除，不改变窗口号及时间范围。
该历史窗口经用户触发复核后，后台迟到的自动阶段更新不会再覆盖它。
再次点“重新分析”仍可产生新修订。模型复核不是人工确认，也不保证判断正确。

片段问答每次从数据库重新读取重叠窗口，使用更新后的摘要。问答本身不是再次看图；
需要重新看图时使用“重新分析”。本功能没有重新运行全部专家、回算之后所有窗口或自动重写已导出的 Markdown 报告。

## 实测记录

测试采用 Electron 中实际运行的采集卡模拟源，背后文件：

`/home/user/proj/video_processor/stream_simulator/media/vid001_capture_30fps.mp4`

1920×1080、30 fps、648.567 秒。不是预先制作的分析动画，也不是仅测试 API。
选取网格第二个窗口（00:05–00:10），实际调用本地模型进行两次复核和一次片段问答。

首轮观察：

- 首次重新分析约 7.15 秒；第二次约 6.79 秒（服务端日志）。
- 弹窗循环播放可观察到时间回到片段开头，暂停后画面时间保持不变。
- 约 36.96 秒的操作期间主视频推进约 36.87 秒，源地址未变化，没有播放控制请求导致跳转。
- 复核期间关闭再打开，任务继续完成，数据库与弹窗结果一致。
- 单独模拟 503 错误验证原文保留，随后撤除模拟并实际重试成功。
- 390×844 小屏无水平溢出，分析按钮、问答输入框可见；桌面截图和录屏抽帧已查看。

这些结果验证功能链路，不代表临床识别准确率评测或采集卡现场验收。

最终版本另测第 9 个窗口（00:40–00:45），会话 `61501999`：

- 首次复核 6.80 秒；采样时间为 40.0、41.6、43.2、44.8 秒，全部来自该会话保存的画面。
- 完整交互 39.43 秒，主视频同步推进 39.43 秒，无重置；片段问答的时间链接实际点击通过。
- 本轮过程中发现并修复了内存会话缓存缺少 `storage_path` 的问题，复核现直接读取数据库会话信息。
- 35 项后端测试、14 项前端测试与前端构建通过。

**内容限制**：第 9 个窗口的复核文案写了“画面中出现手部”。逐一查看四张实际采样帧后，
不能确认该物体是手部，最后一帧的前景有明显模糊遮挡。这是本地 VLM 仍存在的描述误判，
不是已经通过准确率验证的结论。问答会引用窗口结果，因此也会承接该误判。
此次没有用特定窗口号、时间戳规则或手工改文案掩盖它，保留了原始返回供后续模型评估。

最终录屏、截图与机器可读测试记录：

`/home/user/proj/video_processor/video_stream_app/recordings/grid_review_20260922_final/`

- `grid_review_demo.mp4`：整个软件窗口的交互录屏。
- `grid_review_before.png`、`grid_review_pending.png`、`grid_review_after.png`。
- `mobile_review.png`、`main_after_review.png`。
- `verification.json`：模型返回、问答范围、时间推进、回放循环采样和浏览器错误。

首轮第 2 个窗口的测试资料仍保存在相邻的 `grid_review_20260922/`。
录屏为功能交互测试，约 40 秒，不是完整手术视频录制；其中包含专门注入的失败重试测试。

## 回归命令

```bash
cd /home/user/proj/video_processor/video_stream_app
../.venv/bin/python -m unittest \
  backend.tests.test_window_reanalysis \
  backend.tests.test_clip_chat_context \
  backend.tests.test_chat_context \
  backend.tests.test_summary_stream_completion -v
cd frontend
node --test tests/*.test.js
npm run build
```

实际 Electron 已启动并连接模拟源，且启用本机 CDP 9222 时：

```bash
cd /home/user/proj/video_processor/video_stream_app
../.venv/bin/python scripts/check_window_review.py \
  --window-index 8 --output recordings/grid_review_20260922_final
```

该脚本会真实写入所选测试会话的复核记录并录制软件窗口，应在测试会话运行。
