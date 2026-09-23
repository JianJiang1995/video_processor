# 首页历史分析记录

## 使用

启动 Electron 后，首页直接显示“历史分析记录”。顶部保留本地视频、实时视频流和导入分析回放入口。

- 点击文件名或“打开分析”，加载已保存的视频、窗口分析和关键事件。默认暂停，不重新分析、不启动采集卡。
- 每条记录右侧的文档图标直接进入 Markdown 报告，没有报告时禁用。
- 支持按视频名、会话 ID 搜索、刷新和分页，每页 8 条。
- 同一会话的多份导出合并为一条，优先可用且更新时间较新的副本。不同分析会话保留，即使源视频相同，避免把旧版结果误当新版。
- 列表时间是归档文件的保存时间，不冒称手术发生时间。窗口数仅使用已保存的元数据，不用视频时长推算。
- 回放文件缺失时禁用打开按钮，不自动回到直播或重新分析。

当前桌面源视频目录实际有两个文件：`/home/user/Desktop/2026-09-17/`。早期第三个 `10-09-07.mp4` 仅 1.72 秒，目前源目录里已不存在，但独立回放文件仍保留。

## 两个长视频的真实状态

| 视频 | 时长 | 完整窗口 | 结果版本 |
|---|---:|---:|---|
| 09-40-30.mp4 | 18:08.14 | 218 / 218 | 新版延迟与 SurgR1 修复后完整重跑，会话 d401f70c |
| 09-58-39.mp4 | 10:23.12 | 125 / 125 | 旧版完整长跑，会话 229daa5b；未完成本次新版全程复测 |

两段旧版长跑的 `completion.json` 均记录 EOF、待更新任务为 0。新版第一段的 `verification.json` 记录完整初稿和 218 次 R1 后更新。完成工程长跑不等于医学识别结果全部正确。

当前可回看的两个目录：

```text
/home/user/proj/video_processor/video_stream_app/recordings/anatomy_latency_20260923/long_verified/
/home/user/proj/video_processor/video_stream_app/recordings/actual_videos_20260923/09-58-39/
```

第一段成片 `complete.mp4`；第二段旧版成片 `first_video_complete.mp4`。每个目录都包含 `analysis.replay.json` 和 `summary.md`。旧版录屏不包含后来进行的离线窗口复核，导入回放则使用已保存的修订结果，见对应 `export_manifest.json`。

## 索引机制与边界

- 源码运行时自动扫描项目 `recordings/` 下的 `*.replay.json`；打包版扫描系统视频目录 `SurgR1/recordings/`。
- 手动导入过的回放路径保存在 Electron 本机配置 `analysisHistory` 中，位于扫描目录以外也会记住。只保存索引，不复制视频。
- 首页只读取回放清单、最多 128 KiB 的导出元数据和文件状态；不解码视频、不读取庞大的窗口 JSON、不调用模型。完整内容沿用现有 `load-replay-bundle`，点击时才加载。
- 扫描不跟随符号链接，最多 10 层、2500 个目录。达到边界会提示；已登记的手动导入项不受目录扫描深度限制。
- 目录内新导出的结果回到首页或点击刷新后自动出现。新分析若尚未导出独立回放包，其数据库记录仍在下方“其他数据库会话”，本次没有自动把所有数据库会话批量导出。
- 不删除原有会话或分析结果，不改模型、提示词、分析时序，也不按视频名或时间段指定识别结果。

## 实现与验证

- `frontend/electron/analysisHistory.cjs`：有限目录发现、相对路径解析、可用性检查、去重和导入索引。
- `frontend/electron/main.cjs` / `preload.cjs`：本机历史 IPC，成功导入后记住路径。
- `frontend/src/components/AnalysisHistory.vue`：首页列表、搜索、分页和加载状态。
- `ModeSelector.vue`：紧凑首页入口与原有数据库会话。
- `App.vue`：历史报告入口复用回放加载后导航，不再走直播恢复逻辑。

单元测试覆盖相对路径、目录外持久化、缺失文件、同会话去重、损坏清单隔离、符号链接及有界扫描。真实 Electron 验证使用上表两个会话，检查媒体可解码并播放、报告可打开、返回与刷新后记录保留、无分析启动请求，并检查 1280 和 390 像素视口无水平溢出。

```bash
node --test frontend/tests/*.test.js
../.venv/bin/python scripts/verify_home_history.py \
  --sessions d401f70c 229daa5b --output recordings/home_history_20260923
```

UI 验证脚本会导航当前 Electron 页面，应在未进行分析时运行。截图与检查结果保存在上述输出目录。
