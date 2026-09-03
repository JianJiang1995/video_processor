# 2026-07-25 手术室视频采集现场手册

适用设备：`DeckLink Mini Recorder 4K`，设备节点 `/dev/blackmagic/io0`。

## 1. 明天携带的现有物品

| 物品 | 明天的用途 | 限制 |
| --- | --- | --- |
| 5 米 DVI 转 HDMI 线 | 达芬奇 `DVI (SXGA) Video Out L` 到 DeckLink `HDMI IN` 的兼容性测试 | 只转换接头，不转换 1280×1024 到 1080p |
| 两头 BNC 的 HD-SDI 线 | 仅连接经设备科确认的 `HD-SDI OUT` 到 DeckLink `SDI IN` | 照片上方 TilePro SDI 是输入，不能使用 |
| HDMI 到 HDMI 线两条 | 一条用于 NVIDIA 显卡输出到本地屏幕；另一条作为电脑 HDMI 信号测试/备用 | 显卡 HDMI 只能输出，不能接收达芬奇视频 |

不要拔除达芬奇上已经连接的 DVI 或 SDI 线。只在设备科确认后使用空闲输出口。

## 2. 首选现场接法

```text
达芬奇 Video Out L（DVI/SXGA）
  -> 5 米 DVI 转 HDMI 线
  -> DeckLink Mini Recorder 4K 的 HDMI IN

服务器 NVIDIA 显卡 HDMI OUT
  -> HDMI 到 HDMI 线
  -> 本地显示器 HDMI IN
```

软件中选择：

```text
采集设备：DeckLink Mini Recorder 4K
采集卡输入接口：HDMI 输入
输入模式：自动检测（推荐）
连接后自动开始分析：开启
```

只要 DeckLink 能锁定信号，软件会自动处理以下差异：

- 自动识别 720p、1080i、1080p 和对应帧率；
- 1080i 自动去隔行，避免运动边缘出现横向梳齿；
- 显示时保持输入宽高比，4:3 不拉伸、不裁掉手术画面；
- 显示、存帧和分析共享同一个采集管线，只保留最新帧；
- 拔线、源制式变化或管线错误后自动重新连接；
- 无信号、有效信号但全黑、输入停滞和重连状态直接显示在软件中。
- 无信号时保持 `00:00` 且不生成伪分析窗口；首个有效帧到达后自动开始分析。

## 3. 软件无法替代硬件缩放的边界

`DVI (SXGA)` 很可能固定输出 `1280×1024 @ 60 Hz`。DeckLink 当前驱动支持标准
SD/HD/UHD 广播制式，但没有 1280×1024 模式。

普通 DVI 转 HDMI 线不会改变时序。如果 DeckLink 无法锁定，软件和 GPU 都收不到像素，
因此无法在采集之后再缩放。此时只有三种有效处理：

1. 请设备工程师把达芬奇辅助输出改为 720p59.94、1080i59.94 或 1080p；
2. 改用明确支持 SXGA 输入的 `Magewell USB Capture DVI Plus (P/N 32080)`；
3. 使用能把 SXGA 主动缩放到 720p/1080p 的转换器。

不要用反复重启 Electron、增加 GPU 或切换播放器来处理“DeckLink 根本没有锁定信号”的问题。

## 4. 到现场后的执行顺序

### A. 软件启动前检查

在本地 Ubuntu 桌面的终端运行：

```bash
cd /home/user/proj/video_processor/video_stream_app
./scripts/decklink_preflight.sh --connection hdmi --mode auto --wait 8 --json
```

成功时会输出 `RESULT: PASS`，并保存一张实际采集截图：

```text
/home/user/proj/video_processor/video_stream_app/tmp/decklink_preflight.jpg
```

自动模式没有画面，但设备科确认输出是标准 HD 制式时，可以依次尝试常见模式：

```bash
./scripts/decklink_preflight.sh --connection hdmi --mode auto --wait 4 --scan-common
```

预检结束后再启动应用，避免预检程序和应用同时占用 DeckLink。

### B. 启动本地应用

终端 1：

```bash
cd /home/user/proj/video_processor/video_stream_app
bash run_backend.sh
```

终端 2（必须在服务器本地 Ubuntu 图形桌面内运行，不是远程转发）：

```bash
cd /home/user/proj/video_processor/video_stream_app
bash run_electron_local.sh
```

Electron 使用本地桌面仍会有 `DISPLAY` 环境变量，这是 Linux 图形会话本身，不代表 X11
远程转发。

### C. 确认应用状态

连接后至少观察 2 分钟：

1. 画面持续更新，无旧帧积压；
2. 快速运动时没有隔行梳齿；
3. 软件显示的输入分辨率和帧率稳定；
4. 自动分析默认启动，最新窗口与实时画面的差距不超过设计阈值；
5. 短暂拔插测试后能自动恢复，不需要重建会话。

## 5. 如果找到了真正的 SDI 输出

只有接口明确标为 `HD-SDI OUT`、`Video Out` 或经设备科确认是输出时，才使用：

```text
达芬奇 HD-SDI OUT
  -> 现有两头 BNC 的 75 欧姆 HD-SDI 线
  -> DeckLink SDI IN
```

软件改选：

```text
采集卡输入接口：SDI 输入
输入模式：自动检测（推荐）
```

命令行预检改为：

```bash
./scripts/decklink_preflight.sh --connection sdi --mode auto --wait 8 --json
```

## 6. 状态与处理对照表

| 软件状态 | 说明 | 立即处理 |
| --- | --- | --- |
| 未检测到输入信号 | DeckLink 没有锁定输入 | 检查是否接到 `IN`、接口选择、源是否启动；SXGA 时准备硬件缩放方案 |
| 输入信号有效但画面接近全黑 | 时序已锁定，源画面本身接近黑色 | 检查达芬奇输出视图、待机状态和内镜是否在体外 |
| 输入信号已卡住 | 有信号但暂时没有新帧 | 等待自动重连；检查线缆接触和源制式是否切换 |
| 正在重新连接 | 软件检测到拔线、EOS 或 GStreamer 错误 | 不重启应用，先等待 2 至 10 秒 |
| 有画面但严重横向梳齿 | 隔行源未正确识别 | 查看实际模式；显式选择 `1080i5994` 或 `1080i50` |
| 画面被拉伸或裁切 | 显示比例异常 | DeckLink 模式保持自动；应用现在默认完整显示并保留宽高比 |
| 预检报驱动未就绪 | 驱动节点或 GStreamer 插件缺失 | 检查 `/dev/blackmagic/io0`、重启驱动/服务器，不要先排查 AI 模型 |

## 7. 三条现场底线

1. 不拔达芬奇原有视频线，不影响医生主显示链路。
2. 不把达芬奇输入口接到 DeckLink 输入口；输入接输入一定没有画面。
3. 任何临床使用前都先确认原始视频显示稳定，再启动 AI 分析；分析软件不能作为唯一手术显示器。
