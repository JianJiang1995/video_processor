# SDI 本机预览优化与实测（2026-09-24）

## 结论和边界

- 实际输入：DeckLink Mini Recorder 4K，SDI，手动 `1080p50`，1920×1080，逐行 50fps。
- 本机预览已从逐帧 JPEG 编解码改为独立发送线程、独立浏览器 Worker、WebGL2 绘制；输入尺寸保留，模型采样不变。
- 最后一轮完整 Electron + 模型分析的 120 秒测试：平均接收 **49.47fps**，平均绘制提交 **23.76fps**。自动录屏起初开启，随后因停顿保护自动停止。
- 当前 HDMI 显示输出是 **4096×2160、24Hz**。因此不能声称实现了屏幕实际显示 50fps。
- 最后一轮各两秒区间的“采集读帧到绘制提交”P95 最大 **46.02ms**。这不包含采集卡之前的设备处理、GPU 命令完成、显卡扫描输出及显示器延迟，不是端到端玻璃到玻璃延迟。
- 最后 120 秒仍有四次超过 200ms 的绘制间隔，约 **334、294、251、333ms**。检测结果整体搬回 CPU 没有消除所有长停顿，不能把该改动视为已验证的最终根因修复。
- 首次一分钟测试测到一次 **333.9ms** 绘制间隔，另一次 81.6ms。后续关闭录屏的测试仍有约 250–374ms 的停顿，不能归因于录屏，也不能声称完全没有卡顿。
- 自动录屏遇到超过 200ms 的绘制间隔即停止；连续三个低帧率区间也停止，当前会话不自行反复重启录屏。手动录屏不被这个自动策略强制关闭。修复了 `ScreenRecorder` 没有保留 `automatic` 标记、导致原保护判断不生效的问题。
- 当前源画面包含体外设备、灰蓝背景及相机移动。本次验证预览传输、绘制和交互，不是手术模型准确性验收。

## 显卡分配

实际检测到两张 RTX 4090：

| 物理卡 | PCI | 当前任务 |
| --- | --- | --- |
| GPU0 | `98:00.0` | Qwen3-VL-8B 本地 VLM |
| GPU1 | `b1:00.0` | 已连接显示器的桌面、Electron、SurgR1、YOLO/Phase/Triplet/Clip 专家 |

原来 Qwen 和桌面同处 GPU1，Qwen 会持续占满 GPU。现在把持续负载迁到 GPU0；GPU1 仍会因 SurgR1 推理出现负载峰值，并非显示专用卡。

`config.json` 的 `services.surgr1.gpu` 设为 `1`。桌面启动器已有排除 SurgR1 所在显卡的 VLM 分配逻辑，因此选择 GPU0；专家按照模型加载后剩余显存选择 GPU1。显式环境变量仍可覆盖选择。打包版本的 ServiceManager 也读取 SurgR1 的 GPU 配置。

试过 PRIME 跨卡渲染，实际发生 EGL 初始化错误，未将这些环境变量或强制 Vulkan 标志加入正式启动。没有改 Xorg 驱动配置，也没有关闭垂直同步来虚增帧数。

## 预览通路

```text
DeckLink/GStreamer 单个共享读取器
    ├─ 原有模型/历史采样：5fps，历史缩略图 2fps
    └─ DisplayStreamState：跟随输入帧率和尺寸，只保留最新帧
         └─ 独立 asyncio 线程的 loopback WebSocket
              └─ 浏览器 Worker 接收，单个最新帧槽
                   └─ OffscreenCanvas + WebGL2，按显示刷新绘制最新帧
```

### 后端

- `backend/services/local_preview_server.py`：在后端同一进程内启动独立事件循环线程，绑定 `127.0.0.1` 的动态空闲端口。避免 HTTP 分析接口阻塞预览发送。
- 通过 `/api/video/preview-endpoint/{session_id}` 发现端口；地址携带本次启动的随机 token。仅允许采集卡会话，不能拿它播放任意文件。
- 与模型、帧存储共用 `DeckLinkCapture` 的设备注册表，不另开硬件采集进程。
- 每个连接只允许一个尚未确认的网络包；确认后取最新帧，不排队补播旧帧。断开、无信号时断开、停止服务均释放自己的采集引用。
- `backend/services/preview_pixels.py`：32 字节小端头部加像素，记录序号、宽高、读帧时间、源帧率和负载长度。
- 首选 I420（BT.601、limited range），1080p 每帧约 3.11MB；BGR24 为约 6.22MB。I420 保留 1920×1080 亮度尺寸，色度采用 4:2:0，**不是逐像素 RGB 无损传输**。不会对模型输入做此转换。奇数尺寸裁剪自动用 BGR24，避免为了格式切掉画面。
- 仍保留原 8001 端口的 WebSocket 和 MJPEG 兼容通路；普通浏览器、远端访问或 GPU Worker 不可用时可回退。

### Electron / 前端

- `LiveCapturePreview.vue` 管理 Worker 生命周期。暂停关闭预览连接但保留最后的画布；继续播放重新连接，直接取最新帧。
- `liveCapture.worker.js` 直接上传 Y/U/V 纹理，由 GPU 执行颜色转换，不再逐帧 JPEG 解码。
- 接收与显示解耦：50fps 输入到 24Hz 屏幕时只绘制最新帧，不积压 26 帧/秒。没有固定 20/25/30fps 软件上限。
- 每两秒上报一次接收帧率、绘制次数、实际显示器刷新率、帧间隔和读帧至提交耗时。没有每帧触发 Vue 更新。
- Electron 从 `screen.getDisplayMatching()` 提供显示器刷新率，自动录屏以 `min(输入帧率, 屏幕刷新率)` 为目标，不能把过载后下降的 rAF 帧率误当成屏幕能力。
- 网络连续失败或 WebGL 不可用时回退 JPEG；不应无限重连已失效的动态端口并一直黑屏。
- `ELECTRON_REMOTE_DEBUGGING_PORT` 只用于本次诊断，默认不启用；绑定 loopback，不打开右侧 DevTools 界面。

## 不变的分析配置

- `video_processing.window_duration = 5` 秒。
- `sample_interval = 1` 秒。
- `frame_storage.live_fps = 5`，`live_preview_fps = 2`，质量与保留时间不变。
- SurgR1 继续异步更新窗口；图像数量、图像边长、推理并发没有因预览提帧而增加。
- 当前 `1080p50` 手动输入已实际锁定，不能混同 `1080i50`；后者是隔行 25 帧/50 场，信号不匹配时仍应报无信号。

## 长时间运行时的历史解析负担

会话运行约 15 分钟时，190 个窗口的 `others_data` 共约 4.88MB。采样发现 `_patch_open_vlm_window` 多次调用 `get_summaries_by_session`，其中 JSON 解析占据大量持有 GIL 的样本。这是定位线索，不代表采样百分比等于真实墙钟时间比例；采样器自身开销较高，已经停止，性能验收不在采样期间进行。

- VLM 后更新中，原先分别为读取当前窗口、可见性历史、阶段顺序读取三次完整历史，现在只取一次快照并复用。
- 该读取和初稿阶段的历史读取使用 `asyncio.to_thread`；CRUD 方法自身创建并关闭数据库会话，传入 `None`，不在线程间共用 SQLAlchemy Session。
- 不删除历史，不缩短临床上下文，不改阶段或组织判断规则。此优化不能等同于把整个分析流程移到独立进程，Python GIL 和共享 GPU 仍需关注。
- YOLO、夹子检测的结果先整体 `.cpu()`，再提取每个框的坐标、类别、分数及追踪 ID；单帧阶段分类也先把概率搬回 CPU。避免在 Python 标量/列表转换过程中逐框等待 GPU；阈值、类别平滑、框过滤和概率均不变。回归测试核对追踪 ID、坐标、夹子结果和单帧/批量阶段分类一致性。

## 验证证据

最新版本的证据目录：

`/home/user/proj/video_processor/video_stream_app/recordings/sdi_live_20260924/final_cpu_results/`

- `preview_metrics.json`：实际 Electron 连续 120 秒的指标，会话 `79f6ff57`；原始尺寸 1920×1080、输入 50fps。
- `electron_preview.png`：已检查实际界面，视频画布非空，分析窗口持续更新。
- 录屏保护保存的文件：`/home/user/proj/video_processor/video_stream_app/recordings/ui/2026-09-24/2026-09-24T04-15-05-232Z_79f6ff57_2fb501e5.webm`。
- 此轮包括历史快照复用、线程读取及检测结果 CPU 解析改动。仍有四次长间隔，其中三次发生在录屏已停止之后。

首次完整链路及交互验证目录：

`/home/user/proj/video_processor/video_stream_app/recordings/sdi_live_20260924/final_i420_24hz/`

- `preview_metrics.json`：一分钟完整 UI + 分析 + 自动录屏指标。
- `electron_preview.png`：指标结束后的实际 Electron 截图。
- `controls.json`、`paused.png`、`returned.png`：暂停、恢复、网格往返记录和截图。
- 暂停期间 UI 时间固定为 182.8649 秒，分析窗口从 36 增至 37；恢复后显示最新采集帧；网格返回后分析窗口增至 40，预览继续。

自动录制的窗口视频：

`/home/user/proj/video_processor/video_stream_app/recordings/ui/2026-09-24/2026-09-24T03-48-53-216Z_430f71c4_562f85fc.webm`

这是 15fps 的软件窗口录屏，**不是证明实时预览帧率的依据**。高帧率判断采用实际 Worker 指标和采集状态。

其他对照：`final_i420_60hz/`、恢复原显示设置后的 `final_restored/`。720p59.94 热切换时，Electron 原有 Worker 的 rAF 仍约 24Hz；这次热切换不能作为成功显示 50fps 的证明。已恢复原 4096×2160/24Hz，没有强制添加驱动未列出的 1080p50/60 模式。

`final_restored/` 的 30 秒测试没有录屏：平均接收 47.57fps、绘制 22.82fps，两秒区间 P95 最大 50.63ms，最大绘制间隔 298.7ms。随后 `final_recording_guard/` 的 35 秒测试也未开启录屏：平均绘制 21.44fps，最差区间 17.50fps，最大间隔 373.9ms。这两个测试发生在上述历史读取优化之前，说明长时间运行的退化不能忽略，也不是自动停止录屏就能完全解决。

`final_history_fix/`：历史读取优化后的新会话 `5eaf8e53`，实际 UI 连续测试 90 秒，平均接收 49.55fps、绘制 23.79fps，两秒区间 P95 最大 43.38ms。存在两次长间隔 375.9ms、334.4ms；第一次触发自动录屏保护，第二次录屏已停止。这不是与原 190 窗口会话等时长的对照，也不能证明全部停顿已消除。

自动录屏保护的实际证据：`recordings/ui/2026-09-24/2026-09-24T04-09-07-449Z_5eaf8e53_026f41b5.webm`，保存原因 `preview_overload`，录屏时长约 28.49 秒，之后同会话预览及分析继续。

不要拿目录中的早期 BGR/同步 GPU fence/调试抓包实验当最终版本；它们包含失败或无有效指标的对照。

自动化测试：346 项后端、52 项前端通过；前端 production build 和 `git diff --check` 通过。包括像素包长度与颜色矩阵、奇数裁剪回退、消息确认背压、无信号断开、token 校验、线程停止、检测结果 CPU 解析及自动录屏标记回归。

## 复测

正常使用仍双击原桌面快捷方式，或执行：

```bash
cd /home/user/proj/video_processor/video_stream_app
bash start_surgical_app.sh
```

需要仪表复测时，在尚未启动 App 的情况下：

```bash
ELECTRON_REMOTE_DEBUGGING_PORT=9222 bash start_surgical_app.sh
../.venv/bin/python scripts/verify_live_preview.py \
  --seconds 60 --output recordings/sdi_live_20260924/recheck
```

脚本只连接已经运行的 Electron，不再另开视频源。使用 Runtime-only CDP，避免 Playwright 自动启用 Worker 的 Network 抓包后，把每帧几 MB 的像素复制到调试通道，反过来拖慢被测 App。

## 剩余限制

1. 真正 50fps 显示需要显示输出、屏幕与合成器都运行在至少 50Hz。当前模式列表未提供 1080p50/60，只提供 1080p29.97/25，以及 720p59.94/50 等模式；不能通过增加模型 GPU 配额解决 24Hz 输出限制。需检查显示器、线材或转接链路，这不是采集卡输入被限制成 24fps。
2. 要验证完整 1080p50 实际显示，应先解决显示输出模式，再冷启动 Electron 测量，而不是仅热切换后看接收计数。
3. 最后版本不录屏仍出现过 251–333ms 停顿。更长时间动态场景与实际摄像机到显示器的延迟仍需单独验证，短时间复测不能代替长时间验证。后续需进一步定位共享 Python 进程、GPU 竞争和采集源本身的贡献，不能仅凭当前采样就断言具体剩余根因。
4. 本次不修改临床识别文案或组织判断；灰蓝/体外画面下的模型误识别不因显示提速自动解决。
