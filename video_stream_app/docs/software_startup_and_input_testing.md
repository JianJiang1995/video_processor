# SurgR1 软件启动与视频输入测试手册

更新时间：2026-07-24

本手册用于服务器本地 Ubuntu 桌面，覆盖完整软件启动、DeckLink 快速检测、独立预览、
输入制式排查、日志位置和现场常见故障。

## 一、最常用的三个命令

所有命令都在服务器本地 Ubuntu 图形桌面的终端中运行。

### 1. 一条命令启动完整软件

```bash
cd /home/user/proj/video_processor/video_stream_app
./start_surgical_app.sh
```

这个脚本会：

1. 检查本地图形桌面；
2. 复用已经健康的后端，或自动启动后端；
3. 等待后端健康检查通过；
4. 启动 Electron；
5. Electron 关闭后，只停止由本次脚本启动的后端。

后端启动日志在：

```text
/home/user/proj/video_processor/video_stream_app/logs/onsite_backend.log
```

### 2. 先测输入，再启动完整软件

```bash
cd /home/user/proj/video_processor/video_stream_app
./start_surgical_app.sh --preflight
```

预检没有信号时，完整软件仍会打开并等待后续接线，不会因为先开软件而失败。

### 3. 只看采集卡原始画面，不启动 AI

```bash
cd /home/user/proj/video_processor/video_stream_app
./scripts/decklink_preview.sh hdmi auto
```

它会打开一个独立的本地 OpenGL 预览窗口，使用与完整软件一致的低延迟、去隔行和保留
宽高比设置。关闭窗口或在终端按 `Ctrl+C` 即可退出。

独立预览和完整软件不能同时运行，因为 DeckLink 输入应只由一个采集管线占用。

## 二、推荐的现场检查顺序

### 第 1 步：检查驱动、插件、固件和实际帧

```bash
cd /home/user/proj/video_processor/video_stream_app
./scripts/decklink_preflight.sh --connection hdmi --mode auto --wait 8 --json
```

预检会检查：

- `/dev/blackmagic/io0` 是否存在；
- `gst-inspect-1.0 decklinkvideosrc` 是否可用；
- `BlackmagicFirmwareUpdater status` 是否为 `OK`；
- HDMI 输入是否有 DeckLink 支持的有效时序；
- 实际协商出的分辨率、帧率和扫描方式；
- 连续画面是否接近全黑；
- 是否能保存真实采集截图。

成功结果：

```text
RESULT: PASS
```

截图绝对路径：

```text
/home/user/proj/video_processor/video_stream_app/tmp/decklink_preflight.jpg
```

没有有效输入时：

```text
RESULT: NO_SUPPORTED_SIGNAL
```

这不等于软件故障。它表示采集卡没有收到可解码的标准视频时序，优先检查线缆、接口方向、
达芬奇输出状态和 SXGA 兼容性。

### 第 2 步：用独立预览确认动态画面

```bash
./scripts/decklink_preview.sh hdmi auto
```

观察至少 30 秒：

1. 画面是否持续运动；
2. 快速运动边缘是否有横向梳齿；
3. 画面比例是否正确；
4. 是否有周期性冻结或黑屏。

如果设备工程师明确告知输入制式，可显式测试：

```bash
./scripts/decklink_preview.sh hdmi 1080i5994
./scripts/decklink_preview.sh hdmi 1080p5994
./scripts/decklink_preview.sh hdmi 720p5994
```

测试 SDI 时改为：

```bash
./scripts/decklink_preview.sh sdi auto
```

### 第 3 步：关闭独立预览，启动完整软件

```bash
./start_surgical_app.sh
```

在连接页选择：

```text
DeckLink Mini Recorder 4K
采集卡输入接口：HDMI 输入（或已确认的 SDI 输入）
输入模式：自动检测（推荐）
连接后自动开始分析：勾选
```

## 三、手工启动方式

一键脚本不可用时，使用两个终端。

终端 1：

```bash
cd /home/user/proj/video_processor/video_stream_app
bash run_backend.sh
```

看到以下地址后保持终端运行：

```text
API: http://localhost:8001
```

终端 2：

```bash
cd /home/user/proj/video_processor/video_stream_app
bash run_electron_local.sh
```

这里的 `DISPLAY` 是 Ubuntu 本地图形桌面所需变量，不是远程桌面或 X11 转发。

## 四、不同输入源的快速测试命令

### DVI 转 HDMI 线接 DeckLink HDMI IN

```bash
./scripts/decklink_preflight.sh --connection hdmi --mode auto --wait 8 --json
./scripts/decklink_preview.sh hdmi auto
```

如果达芬奇固定输出 1280×1024 SXGA，普通线只改变接口，不改变分辨率。DeckLink 无法
锁定时，任何软件缩放都无法开始，因为系统没有收到像素。此时需要改变达芬奇输出制式或
使用支持 SXGA 的采集/缩放设备。

### 真正的 HD-SDI 输出接 DeckLink SDI IN

```bash
./scripts/decklink_preflight.sh --connection sdi --mode auto --wait 8 --json
./scripts/decklink_preview.sh sdi auto
```

照片中 TilePro 区域的 SDI 是输入口，不能用于这条测试。必须使用经设备科确认的输出口。

### 用另一台电脑模拟标准 HDMI 视频源

1. 另一台电脑用 HDMI 到 HDMI 线接 DeckLink HDMI IN；
2. 另一台电脑设置 `1920×1080 @ 60 Hz` 或 `1280×720 @ 60 Hz`；
3. 全屏播放手术测试视频或 OBS 预览；
4. 服务器执行：

```bash
./scripts/decklink_preflight.sh --connection hdmi --mode auto --wait 8
./scripts/decklink_preview.sh hdmi auto
```

这能验证服务器、DeckLink、线缆和软件，但不能证明 DeckLink 支持达芬奇的 SXGA 输出。

## 五、自动处理的内容

只要采集卡已经锁定输入，软件会自动：

- 协商常见的 720p、1080i、1080p 和 2160p 输入；
- 对隔行输入去隔行；
- 保持原始宽高比，4:3 画面完整显示；
- 只保留最新帧，避免播放越积越慢；
- 让显示、存帧和分析共用一个 DeckLink 管线；
- 输入拔插、格式变化或 EOS 后自动重新建立管线；
- 区分无信号、信号停滞、正在重连和有效信号但全黑。

DeckLink 会话的时间轴由实际采集帧驱动：无信号时保持 `00:00 / 窗口1`，不会生成分析
窗口；首个有效帧到达后自动启动分析，掉线时冻结时间，恢复后继续。

软件不能在采集卡没有锁定任何帧时，将不支持的 SXGA 时序“用 GPU 转成 1080p”。这一步
必须发生在 DeckLink 之前。

## 六、常见故障

### 1. 软件显示“未检测到采集卡输入信号”

按顺序检查：

1. 线是否接在 DeckLink 的 `IN`；
2. 软件选择的是 HDMI 还是 SDI；
3. 达芬奇接口是否真的是 `Video Out`；
4. 源设备是否已经开启视频输出；
5. 是否是 DeckLink 不支持的 1280×1024 SXGA。

### 2. 有效信号，但画面全黑

说明连接和时序通常正常。检查达芬奇当前输出的是手术画面、待机画面，还是内镜已经移出
体外。预检截图可用于判断黑色来自源端还是 UI。

### 3. 有横向梳齿或运动断面

显式尝试正确的隔行模式：

```bash
./scripts/decklink_preview.sh hdmi 1080i5994
```

应用管线会执行去隔行。不要把 1080i59.94 误选成 1080p59.94。

### 4. 预览器打不开窗口

确认命令是在服务器本地桌面终端执行：

```bash
echo "$DISPLAY"
echo "$WAYLAND_DISPLAY"
```

至少应有一个变量非空。不要通过 SSH X11 转发运行现场预览。

### 5. 提示设备正在使用

关闭独立预览、旧 Electron 和旧后端，再只启动一套。快速查看相关进程：

```bash
ps -eo pid,cmd | grep -E 'decklink|gst-launch|uvicorn|electron' | grep -v grep
```

不要同时运行 `decklink_preview.sh`、预检脚本和完整应用。

## 七、记录问题时保留这些信息

出现现场问题时保存：

```bash
BlackmagicFirmwareUpdater status
gst-inspect-1.0 decklinkvideosrc
./scripts/decklink_preflight.sh --connection hdmi --mode auto --wait 8 --json
```

并保留：

- `/home/user/proj/video_processor/video_stream_app/tmp/decklink_preflight.jpg`
- `/home/user/proj/video_processor/video_stream_app/logs/onsite_backend.log`
- 软件界面显示的接口、模式、分辨率和错误文本

现场接线的完整安全顺序另见：

[`or_capture_day_runbook_20260725.md`](./or_capture_day_runbook_20260725.md)
