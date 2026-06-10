# 远端 Ubuntu 测试机使用说明

远端项目目录：

```bash
cd /home/user/proj/video_processor/video_stream_app
```

## Codex

Codex 已配置为最大权限模式：

```toml
approval_policy = "never"
sandbox_mode = "danger-full-access"
```

配置文件位置：

```bash
~/.codex/config.toml
```

如果已经打开了一个旧 Codex 会话，旧进程不一定会重新读取新配置。推荐退出旧会话，然后恢复最近一次会话：

```bash
cd /home/user/proj/video_processor/video_stream_app
codex resume --last
```

如果要看所有可恢复会话：

```bash
codex resume --all
```

如果只是验证 Codex 能联网并且不再问权限：

```bash
cd /home/user/proj/video_processor/video_stream_app
codex exec "reply OK"
```

## 代理

Clash Verge/mihomo 当前代理端口：

```bash
http://127.0.0.1:7897
```

终端环境已写入：

```bash
~/.bashrc
~/.profile
~/.codex/.env
~/.config/environment.d/99-codex-proxy.conf
```

如果是已经打开的旧终端，执行一次：

```bash
source ~/.bashrc
```

## 采集卡

已安装 Blackmagic Desktop Video 驱动：

```bash
dpkg -l | grep desktopvideo
```

当前设备：

```bash
/dev/blackmagic/io0
```

设备型号：

```bash
DeckLink Mini Recorder 4K
```

驱动服务：

```bash
systemctl status DesktopVideoHelper.service
```

固件/驱动状态检查：

```bash
BlackmagicFirmwareUpdater status
```

SDK 位置：

```bash
/home/user/downloads/Blackmagic_DeckLink_SDK_16.0/Blackmagic DeckLink SDK 16.0
/home/user/downloads/Blackmagic_DeckLink_SDK_16.0/Blackmagic DeckLink SDK 16.0/Linux/include/DeckLinkAPI.h
```

GStreamer 已有 DeckLink 插件：

```bash
gst-inspect-1.0 decklink
gst-device-monitor-1.0 Video/Source
```

注意：当前系统 `ffmpeg` 没有编译 DeckLink 输入支持，所以采集卡测试先用 GStreamer。

### 预览采集卡画面

现场接好 HDMI/SDI 输入后，在图形桌面终端运行：

```bash
gst-launch-1.0 decklinkvideosrc device-number=0 mode=1080p30 ! videoconvert ! autovideosink
```

如果源是 1080p60：

```bash
gst-launch-1.0 decklinkvideosrc device-number=0 mode=1080p60 ! videoconvert ! autovideosink
```

如果源是 4K30：

```bash
gst-launch-1.0 decklinkvideosrc device-number=0 mode=2160p30 ! videoconvert ! autovideosink
```

如果没有画面，先确认输入源分辨率/帧率和 `mode` 一致。

### 后端/Electron 测试

当前服务端口：

```bash
SurgR1:    http://localhost:9003/health
Backend:   http://localhost:8001/api/health
Simulator: http://localhost:9001/info
```

启动 Electron：

```bash
cd /home/user/proj/video_processor/video_stream_app
./run_electron_local.sh
```
