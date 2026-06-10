# 实时分析延迟治理 v1.1

## 当前实时流程

1. Electron/Vue 负责播放实时源，HTTP/HTTPS MJPEG 源优先由 `/api/video/mjpeg-proxy/{session_id}` 字节透传。
2. 进入实时会话后，`/api/analysis/start-surgr1-continuous/{session_id}` 启动两条后台路径：
   - `surgr1_continuous_task`：按 `SAMPLE_INTERVAL` 采样，批量请求 SurgR1，并把帧级 R1 结果写入 MySQL。
   - `frame_capture_service`：独立线程 25fps 保存原始帧和 preview 帧，供回放、缩略图和窗口多模态分析使用。
3. 用户点击开始分析后，`/api/analysis/start-glm-summarization` 启动 `glm_summarization_task`：
   - Stage 1：YOLO / Phase / Triplet 专家文本 → Gemini text-only，先给实时初稿。
   - Stage 2：专家结果 + SurgR1 CoT + 窗口图片 → Gemini 多模态精修，覆盖同一 window。
4. 前端 SSE `/api/analysis/stream-summaries/{session_id}` 接收同一个 `window_id` 的 `stage=1` 和后续 `stage=2` 更新。

## v1.1 修复点

问题：Stage 1 设计上应该是快速路径，但旧实现先轮询 R1 帧数据库，再处理窗口。因此即使专家模型和 Gemini 足够快，初稿仍被 SurgR1 的 10-30s 慢路径门控。

修复：实时模式下，`glm_summarization_task` 改为从 `frame_capture_service` 的已保存帧判断 Stage 1 窗口是否就绪：

- 第一个窗口在已有至少 3 秒保存帧时可先出初稿。
- 后续窗口在窗口结束且已有至少 3 张保存帧时触发 Stage 1。
- Stage 1 保存后只加入 `stage1_processed_windows`，不加入 `processed_windows`。
- R1 帧级结果稍后到达后，同一窗口仍会进入 Stage 2，并通过 MySQL window upsert 覆盖 Stage 1。
- 如果 Stage 2 失败，会尽量保留已存在的 Stage 1 摘要，避免把实时初稿覆盖成错误文本。

## 预期效果

- 实时初稿延迟从“等待 R1 批量完成 + Gemini”变为“窗口保存帧就绪 + 三专家 + Gemini”。
- 前端协议不变：仍然通过 SSE 收到 `stage=1` 初稿和 `stage=2` 精修。
- R1 仍保留为精修路径，不影响最终深度推理结果。

