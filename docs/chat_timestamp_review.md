# 智能问答时间戳与片段回看

## 使用方式

- 主界面和窗口一览的智能问答中，回答里的有效视频时间戳显示为链接。
- 支持 `MM:SS`、`HH:MM:SS`、中文冒号及时间范围。单点时间打开包含该时间的分析窗口；时间范围保持原区间。
- 点击后打开独立回看弹窗：左侧循环播放，右上显示对应窗口的完整分析记录，右下提供片段智能问答。
- 弹窗内回答的时间戳同样可点击，切换片段时同步更新分析与问答范围。
- 暂停按钮和点击视频只控制回看片段；关闭按钮或 Esc 退出。主视频不因回看而暂停、跳转或回零。
- 底部“全部窗口”和窗口一览的窗口卡片使用同一个回看组件。

## 实现边界

- `frontend/src/utils/chatTimestamps.js` 解析时间并校验已有窗口覆盖范围。越界、格式错误、时间覆盖有缺口的引用保留为普通文本，不生成失效链接。
- `ChatMessageText.vue` 使用 Vue 文本节点渲染，不将模型文本作为 HTML 执行。
- `VideoReviewModal.vue` 持有独立播放器、循环范围与播放状态，不调用主视频 seek/pause 接口。暂停状态只由用户操作改变，不由片尾内部媒体事件反向触发。
- `ChatPanel.vue` 为弹窗请求附加 `review_context: {start_time, end_time, window_id?}`。片段切换、会话切换或关闭时取消该组件的未完成请求，防止旧回复显示到新片段。
- 后端验证有限、非负且递增的时间范围。仅从该会话数据库提取与范围重叠的分析记录，不接受前端上传摘要作为证据，不套用整段视频的快捷总结。
- 片段问答使用当前配置的本地 Qwen3-VL-8B 服务。回答基于已有分析记录，**不是重新看图或重新识别视频**。无记录时明确说明证据不足；原始分析的识别准确率不由本功能保证。

## 验证

```bash
cd /home/user/proj/video_processor/video_stream_app
../.venv/bin/python -m unittest backend.tests.test_clip_chat_context backend.tests.test_chat_context -v
cd frontend
node --test tests/*.test.js
npm run build
```

后端 20 项、前端 14 项测试通过，构建通过。在运行中的 Electron 上，使用已有分析会话完成了真实本地模型问答、点击引用、循环、暂停恢复、弹窗内再次点击引用、网格入口、片尾循环和窄窗口布局测试。

测试脚本（会操作当前 Electron，并发送测试提问）：

```bash
../.venv/bin/python scripts/check_chat_review.py \
  --session <已有会话ID> --output recordings/chat_review_check
```

2026-09-21 的验证文件保存在：

`/home/user/proj/video_processor/video_stream_app/recordings/chat_review_20260921/`

- `verification.json`：真实请求回复、循环时间采样、主视频前后状态、错误检查。
- `desktop_review.png`：桌面弹窗和真实片段问答。
- `overview_review.png`：窗口一览入口。
- `mobile_review.png`：390×844 窄窗口验证。

主视频在一次 26.43 秒的回看测试中前进了约 26.43 秒，没有收到回看引起的 seek/pause 请求。最后一个片段跨片尾重复循环，18 次采样全部保持播放状态。此验证不等于临床识别准确率评测，也不是整段手术的重新录制。
