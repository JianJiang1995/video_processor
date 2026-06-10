# 视频播放性能治理

针对"视频播放卡顿"根因治理的记录。背景排查见 offline 报告（只存在本次 agent 会话中，未落盘）。

## v1.0 — P0+P1+P2 一次性治理 (2026-04-21, 757638e)

**变更摘要**: 系统性修复后端 asyncio 事件循环被同步 `cap.read` / YOLO 推理阻塞、前端直播时间更新过于频繁、历史窗口缩略图无限流并发请求、底部卡片列表每条 SSE 消息都 O(n log n) 全量排序、SAM3 双通道触发 fetch 等五处会叠加放大播放卡顿的问题。

### 根因定位（offline 排查结论）

1. **后端 asyncio loop 阻塞**（最关键）：`surgr1_continuous_task` 协程内直接 `cap.read()` + 完成 R1 batch 后同步 `yolo_svc.detect(...)`，HTTP MJPEG 读帧会阻塞到下一帧到达，GPU 推理也是同步几十 ms。由于 FastAPI 的 MJPEG 代理、SSE、`/api/config` 等请求都共用这条 loop，分析任务一旦运行就会把**整个后端**拖慢，前端 `<img>` 直观表现为卡顿。`backend/main.py` 注释里过去已经记录过"专家模型阻塞 loop 导致 MJPEG 代理卡死"，这次把剩余的同步调用一并清理。
2. **前端直播 `currentTime` 100ms 刷新**：驱动 `ControlBar.progressPercent` / `currentSummary` / 底部卡片列表等一串 computed 重算，与 MJPEG 解码争主线程。
3. **WindowOverview 缩略图无限流**：一次性对全部 summaries 并发请求 `frame-at-timestamp`，响应是 base64 data URL，主线程长任务严重。
4. **底部 `sortedSummaries` 每条 SSE 双重 sort**：SSE 回调里 `sort((a,b)=>a.start_time-b.start_time)`，`sortedSummaries` computed 再 `sort((a,b)=>b.window_id-a.window_id)`，两次 O(n log n)。
5. **SAM3 双通道 fetch**：`setInterval(250ms)` + `watch(currentTime)` 叠加，播放时实际请求频率约 2 倍。

### 修改内容

#### P0 — 后端同步阻塞调用全部放入 executor
- `video_stream_app/backend/routers/analysis.py`
  - `surgr1_continuous_task`：
    - `cap = open_video_source(video_path)` → `await loop.run_in_executor(None, open_video_source, video_path)`
    - 主循环 `ret, bgr = cap.read()` → `await loop.run_in_executor(None, _blocking_read, cap)`
    - 本地文件 EOF 回绕 `cap.set(cv2.CAP_PROP_POS_FRAMES, 0)` → 放 executor
    - `cv2.cvtColor` + `Image.fromarray` 打包成 `_bgr_to_pil` 闭包，放 executor
    - R1 batch 完成后 `yolo_svc.detect(last_batch_frame["image"])` → `await loop.run_in_executor(None, yolo_svc.detect, ...)`
    - `cap.release()`（正常结束与 finally 两处）→ 放 executor
    - 循环尾部 `await asyncio.sleep(0.01)` 改为：实时流 `asyncio.sleep(0)`（只让一跳），本地文件按名义 fps 节流避免 100% CPU
  - `frame_capture_for_playback`（当前未被启动但防将来启用）：同样把 `open_video_source` / `cap.read` / `cap.set` / `cap.release` 全放 executor
  - `/sam3/mjpeg-stream` 端点（`generate_frames` 异步生成器）：`VideoCapture` 构造 / `cap.read` / `cvtColor` + PIL / `cap.release` 全放 executor
  - `/frame-at-timestamp` 的 live-stream fallback：把 `cv2.VideoCapture(video_path)` + 读 3 帧 + `cv2.imencode` + base64 编码整体封装进 `_grab_live_frame` 闭包，交给 `run_in_executor`

#### P1 — 前端高频触发降频
- `video_stream_app/frontend/src/App.vue`
  - 直播 `streamTimerInterval`：更新 `currentTime` / `duration` 的 `setInterval` 从 **100ms → 250ms**。时间显示精度到 250ms 肉眼无感（只显示到秒），但 Vue 响应式联动成本下降约 60%。
- `video_stream_app/frontend/src/components/WindowOverview.vue`
  - 重写缩略图加载逻辑：
    - 新增 `IntersectionObserver` 观察每张 `card-thumb` 元素，`rootMargin: 200px 0px` 保证滚动时提前预加载
    - 卡片首次进入视口时再入队 `thumbQueue`，并用 `MAX_CONCURRENT_THUMBS = 3` 做并发限流
    - 函数式 ref `:ref="(el) => registerCardThumb(el, s.window_id)"` 替代原来的 `ref="thumbRefs"` 字符串数组，避免 window_id 与元素对齐失败
    - `onUnmounted` 时 `disconnect` observer，清空队列与两个 Map/Set

#### P2 — 小热点清理
- `video_stream_app/frontend/src/App.vue`
  - SSE 新窗口入库从 `push + sort(O(n log n))` 改为 **二分 + splice** 的有序插入（常态下新 summary 的 start_time >= 末尾项，直接 push；仅乱序时做 O(n) 插入）
  - `sortedSummaries` computed 从 `[...summaries].sort((a,b)=>b.window_id-a.window_id)` 改为 **`summaries.value.slice().reverse()`**（依赖 P2 改动后的 summaries 已按 start_time 升序维护）
- `video_stream_app/frontend/src/components/VideoPlayer.vue`
  - 新增墙钟时间戳 `_lastSam3FetchTs`，`fetchSam3Frame` 成功进入请求时更新，`watch(currentTime)` 在 400ms 内不再重复触发，保证 watch + 250ms interval 合起来不会超过 ~2.5 Hz

### 设计决策

- **为什么用默认 ThreadPoolExecutor 而不是 `asyncio.to_thread` / 专用 pool**：`loop.run_in_executor(None, ...)` 使用的就是 asyncio 默认线程池（大小随 Python 版本变化但够用），不需要额外生命周期管理；`asyncio.to_thread` 本质等价，但需要 3.9+ 并且可读性对这一大段既有同步代码不如显式 executor。未来若需要把 YOLO 推理隔离到专用线程池避免与 I/O 混用，可以在 startup 时挂一个 `concurrent.futures.ThreadPoolExecutor(max_workers=2)` 再传给 `run_in_executor` 的第一个参数。
- **为什么不直接共享一路 `VideoCapture`（单读多消费）**：虽然 offline 报告里把它列为理想方案，但现有 `frame_capture_service` 是**线程版**，独立自己的 `cap`；`surgr1_continuous_task` 是**协程版**，两者生命周期和错误处理都差别很大，统一到同一个 producer 需要相当的重构。这次只做 **"先不阻塞 loop"** 的最小修复，带宽压力留给后续 v1.1 再做。simulator 端其实是共享 broadcaster（`stream_simulator/http_server.py`），同一机器多客户端的代价主要在本地 TCP + JPEG 解码，不算灾难性。
- **为什么直播时间从 100ms 到 250ms 而不是 500ms**：控制条下方的"进度百分比"在 250ms 下视觉上仍然连续；500ms 会有肉眼可见的"一跳一跳"，体感会变差。
- **为什么 SSE 仍然在前端按 start_time 升序维护，而不是直接维护降序给底部条用**：前端其他多处（`currentSummary` 按 `Math.floor(currentTime/windowDuration)` 查找、`RightPanel` 段落合并等）都假设/更习惯按时间升序。保持升序 + 底部条 `slice().reverse()` 是改动面最小的选择。
- **WindowOverview 缩略图 `rootMargin` 为什么是 200px**：典型卡片高度约 200–220px，rootMargin 200px 相当于屏幕外提前约 1 张卡片触发请求，滚动速度正常时用户基本看不到 spinner。
- **为什么把 `frame_capture_for_playback` 也一起改了（虽然它没被调用）**：函数仍然 `async def` 且写法上明显是协程模式，未来任何一次被重新启用都会把 loop 阻塞拖下来。这种"预警性"修复几乎无风险，比留个雷强。

### 影响范围

- **后端**：
  - 受影响路径：`surgr1_continuous_task`（分析开始时自动启动）、`/api/analysis/sam3/mjpeg-stream`（可选可视化端点）、`/api/analysis/frame-at-timestamp`（WindowOverview 缩略图 + 拖拽预览调用）
  - 语义**不变**：所有 `run_in_executor` 都对应 `await`，调用顺序和异常语义等价
  - 对 CPU / 线程池压力：额外在默认 executor 里跑 `cap.read` / `cvtColor` / `yolo.detect`，默认线程池在 Python 3.10 上是 `min(32, os.cpu_count()+4)`，单台机器单 session 场景完全够用
- **前端**：
  - `App.vue`：直播时间显示刷新 100ms → 250ms；SSE 新 summary 插入逻辑从"push+全排"改为"有序插入"
  - `WindowOverview.vue`：缩略图加载策略完全重写，首屏请求数从"全部 summary 并发"降到"视口附近 ≤ 3 个并发"
  - `VideoPlayer.vue`：SAM3 开启时的请求频率降到 ~2.5 Hz
- **数据层 / 模型层**：无改动，MySQL schema / SurgR1 / Gemini / YOLO 权重路径全部未动
- **兼容性**：现有 session、历史 summary、MJPEG 代理 URL、SSE 格式全部保持不变，**无需数据迁移**

### 后续建议（未在本次 v1.0 处理）

- **v1.1 待办**：
  - 单读多消费：`surgr1_continuous_task` 和 `frame_capture_service` 共享一路 `VideoCapture`，通过 `asyncio.Queue` 或 `queue.Queue` 把帧 fan-out 给分析与磁盘录制
  - `get_dynamic_batch_size(...)` 里 `video_elapsed_time` 参数没被用到，只是占位
  - 后端 YOLO 推理如果和 SurgR1 在同一 GPU 有 VRAM 争用，考虑把 yolo 放独立线程池并合并连续请求
- **profiling 建议（如仍有卡顿）**：
  - Chrome DevTools Performance 录 10s 播放：看主线程是否仍有 > 50ms 长任务
  - 后端 `logs/api_*.log` 找 "RES ... ms" 大于 200ms 的接口
  - `py-spy dump --pid <fastapi_pid>` 看协程栈是否还有意外的同步调用
