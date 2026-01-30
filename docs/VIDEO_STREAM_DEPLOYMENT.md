# 视频流系统设计与达芬奇机器人部署方案

> 本文档总结了视频流播放系统的架构设计，以及在手术室部署达芬奇手术机器人实时视频分析的方案。

## 目录

1. [当前系统架构](#当前系统架构)
2. [视频流数据流程](#视频流数据流程)
3. [循环播放机制](#循环播放机制)
4. [达芬奇机器人视频输出](#达芬奇机器人视频输出)
5. [实际部署方案](#实际部署方案)
6. [推荐硬件设备](#推荐硬件设备)
7. [部署步骤](#部署步骤)

---

## 当前系统架构

### 整体架构图

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│      视频源          │────▶│      后端处理         │────▶│      前端显示        │
│  (stream_simulator) │     │  (video_stream_app)  │     │      (Vue.js)       │
└─────────────────────┘     └──────────────────────┘     └─────────────────────┘
         │                           │                            │
         │                           ▼                            │
         │                  ┌─────────────────┐                   │
         │                  │   AI 分析服务    │                   │
         │                  │  - SurgR1 (相位/工具)               │
         │                  │  - SAM3 (分割)   │                   │
         │                  │  - GLM (总结)    │                   │
         │                  └─────────────────┘                   │
         │                           │                            │
         │                           ▼                            │
         │                  ┌─────────────────┐                   │
         │                  │    帧存储服务    │◀──────────────────┘
         │                  │ (frame_storage) │   (循环播放时加载)
         │                  └─────────────────┘
         │
         ▼
  支持的视频源格式:
  - http://  (HTTP MJPEG 流)
  - https:// (HTTPS 流)
  - rtsp://  (RTSP 流)
  - 本地文件 (.mp4, .avi 等)
```

### 核心组件

| 组件 | 路径 | 功能 |
|------|------|------|
| **stream_simulator** | `/stream_simulator/` | 模拟视频流服务器，将本地视频转为 HTTP MJPEG |
| **video_stream_app** | `/video_stream_app/backend/` | 后端 API，处理视频流、AI 分析、帧存储 |
| **前端** | `/video_stream_app/frontend/` | Vue.js 前端，视频显示、循环播放、分析结果展示 |
| **SurgR1 API** | `/SurgR1_api/` | 手术相位/工具/动作识别 |
| **SAM3 API** | `/sam3_api/` | 实时视频分割 |
| **GLM API** | `/glm_api/` | 窗口总结生成 |

---

## 视频流数据流程

### 实时视频流处理

```
1. 视频源输入
   │
   ▼
2. 后端采集 (analysis.py)
   ├── 每 100ms 采集一帧用于显示 (10 fps)
   ├── 每 1s 采集一帧送 SurgR1 分析
   └── 保存帧到 sessions/{session_id}/frames/
   │
   ▼
3. AI 分析流水线
   ├── SurgR1: 批量分析 (6帧/批次)
   │   └── 输出: 手术相位、工具定位、手术动作
   ├── SAM3: 实时分割 (10 fps)
   │   └── 输出: 工具分割掩码
   └── GLM: 窗口总结 (每15秒窗口)
       └── 输出: 中文手术进程描述
   │
   ▼
4. 前端显示
   ├── MJPEG 代理: 实时视频流
   ├── SAM3 叠加: 分割结果可视化
   └── 分析面板: 相位、工具、总结
```

### 帧存储结构

```
sessions/
└── {timestamp}_{session_id}_{video_name}/
    ├── frames/           # 原始帧 (高质量)
    │   ├── frame_000001_ts0_10.jpg
    │   ├── frame_000002_ts0_20.jpg
    │   └── ...
    ├── preview/          # 预览帧 (低质量，用于快速循环播放)
    │   ├── frame_000001_ts0_10.jpg
    │   └── ...
    ├── analyzed/         # 已分析的帧
    └── frames_index.json # 帧索引 (加速查询)
```

---

## 循环播放机制

### 设计目标

- 用户点击时间窗口后，循环播放该窗口内的视频片段
- 使用预存储的帧，而非实时流（避免 seek 问题）
- 流畅的播放体验，帧率自适应

### 帧率计算逻辑

```javascript
// 计算帧率 (VideoPlayer.vue)
const windowDuration = loopWindow.end_time - loopWindow.start_time
const frameCount = loopFrameCache.length

// 保护措施
const safeWindowDuration = Math.max(windowDuration, 0.5)  // 最小 0.5 秒

// 帧率限制: 1-30 fps
const naturalFps = frameCount / safeWindowDuration
let targetFps = Math.max(1, Math.min(naturalFps, 30))

// 最小循环周期: 2 秒 (避免太快循环)
const MIN_LOOP_DURATION_MS = 2000
if ((frameCount / targetFps) * 1000 < MIN_LOOP_DURATION_MS) {
    targetFps = (frameCount * 1000) / MIN_LOOP_DURATION_MS
}
```

### 播放示例

| 场景 | 帧数 | 窗口时长 | 自然帧率 | 实际帧率 | 循环周期 |
|------|------|----------|----------|----------|----------|
| 正常 | 150帧 | 15秒 | 10 fps | 10 fps | 15秒 |
| 帧多 | 300帧 | 10秒 | 30 fps | 30 fps | 10秒 |
| 帧少 | 5帧 | 5秒 | 1 fps | 2.5 fps | 2秒 |
| 窗口短 | 50帧 | 0.3秒 | 166 fps | 25 fps | 2秒 |

---

## 达芬奇机器人视频输出

### 接口类型 (根据实物照片)

达芬奇手术机器人提供以下视频输出接口：

```
┌─────────────────────────────────────────────────────────┐
│                    达芬奇机器人后面板                     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   [SDI]     [SDI]          [Line Out]                  │
│    ○         ○               ○                         │
│                                                         │
│  [S-Video] [S-Video]       [Line In]                   │
│    ◎         ◎               ○                         │
│                                                         │
│   [DVI]    [DVI]    [DVI]    [DVI]    [Headset]       │
│   ▭        ▭       (SXGA)   (SXGA)      ○             │
│                      ▭        ▭                        │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 接口对比

| 接口 | 类型 | 分辨率 | 推荐度 | 说明 |
|------|------|--------|--------|------|
| **SDI** | BNC 同轴 | 1080p/4K | ⭐⭐⭐⭐⭐ | **首选！** 专业视频接口，无压缩，低延迟 |
| **DVI (SXGA)** | 数字视频 | 1280×1024 | ⭐⭐⭐⭐ | 高质量数字输出 |
| **DVI** | 数字视频 | 可变 | ⭐⭐⭐ | 通用数字视频 |
| **S-Video** | 模拟视频 | 480i | ⭐⭐ | 质量较差，不推荐 |

### 关键差异：机器人输出 vs 当前模拟

| 特性 | 达芬奇机器人 | 当前模拟系统 |
|------|--------------|--------------|
| **输出类型** | 物理接口 (SDI/DVI) | 网络流 (HTTP/RTSP) |
| **信号格式** | 原始视频信号 | 压缩视频流 |
| **连接方式** | 线缆直连 | 网络传输 |
| **需要设备** | 采集卡 | 无 |

> ✅ **已实现直接采集卡支持！** 系统现已支持直接从采集卡读取视频，无需 FFmpeg 转流。

---

## 实际部署方案

### 方案架构

```
┌─────────────────┐
│  达芬奇机器人   │
│   SDI/DVI 输出  │
└────────┬────────┘
         │ SDI/DVI 线缆
         ▼
┌─────────────────┐
│   视频采集卡    │  Blackmagic DeckLink / Magewell
│   (服务器内)    │
└────────┬────────┘
         │ PCIe / USB
         ▼
┌─────────────────┐
│  采集服务器     │
│  ├── FFmpeg     │──────┐
│  └── 采集软件   │      │
└─────────────────┘      │
                         │ RTSP/HTTP 流
                         ▼
              ┌─────────────────────┐
              │  视频分析系统       │
              │  (video_stream_app) │
              │  ├── 后端 API       │
              │  ├── AI 分析        │
              │  └── 前端显示       │
              └─────────────────────┘
```

### 方案 A: FFmpeg 转流 (推荐)

使用 FFmpeg 将采集卡信号转换为网络流：

```bash
# Linux 系统
# 1. 查看可用设备
v4l2-ctl --list-devices

# 2. 采集卡 → RTSP 流
ffmpeg -f v4l2 -i /dev/video0 \
       -c:v libx264 -preset ultrafast -tune zerolatency \
       -f rtsp rtsp://localhost:8554/surgery

# 3. 采集卡 → HTTP MJPEG 流
ffmpeg -f v4l2 -i /dev/video0 \
       -c:v mjpeg -q:v 5 \
       -f mjpeg http://localhost:8080/stream.mjpeg
```

```bash
# Windows 系统
# 1. 查看可用设备
ffmpeg -list_devices true -f dshow -i dummy

# 2. 采集卡 → RTSP 流
ffmpeg -f dshow -i video="Blackmagic DeckLink" ^
       -c:v libx264 -preset ultrafast -tune zerolatency ^
       -f rtsp rtsp://localhost:8554/surgery
```

### 方案 B: 直接采集卡支持 ✅ (已实现！推荐)

系统已支持直接从采集卡读取视频，**无需 FFmpeg 转流**！

**优势：**
- 延迟更低 (50-100ms vs 200-500ms)
- 无需额外的转流进程
- 前端有设备选择 UI

**使用方法：**

1. 安装采集卡和驱动
2. 打开前端，选择 "实时视频流" 模式
3. 切换到 "本地采集卡" 标签页
4. 点击 "刷新" 扫描可用设备
5. 选择采集卡并连接

**API 端点：**

```bash
# 列出可用采集设备
GET /api/video/capture-devices

# 返回示例
{
  "success": true,
  "devices": [
    {
      "device_id": 0,
      "device_name": "Capture Device 0",
      "width": 1920,
      "height": 1080,
      "fps": 30.0,
      "backend": "v4l2"
    }
  ]
}

# 连接采集卡
POST /api/video/connect-capture
{
  "device_id": 0,
  "auto_analyze": true
}
```

**内部 URL 格式：**

```
device://0        # 按设备索引
device://DeckLink # 按设备名称 (Windows)
```

### 方案 C: OBS Studio 中转

适合快速测试，无需编程：

```
1. 安装 OBS Studio
2. 添加采集卡作为视频源
3. 安装 obs-websocket 或 RTSP 插件
4. 配置输出为 RTSP 流
5. 在我们系统中使用 RTSP URL
```

---

## 推荐硬件设备

### 采集卡推荐

| 设备 | 价格 (USD) | 接口 | 特点 |
|------|------------|------|------|
| **Blackmagic DeckLink Mini Recorder** | ~$150 | SDI + HDMI | 专业级，低延迟，Linux 支持好 |
| **Magewell Pro Capture SDI** | ~$400 | SDI | 高端，企业级稳定性 |
| **Magewell USB Capture SDI** | ~$300 | SDI (USB) | 免驱动，即插即用 |
| **AVerMedia Live Gamer 4K** | ~$200 | HDMI | 性价比高，需 DVI→HDMI 转接 |
| **Elgato Cam Link 4K** | ~$130 | HDMI (USB) | 便携，需转接头 |

### 推荐配置

**手术室部署推荐：**

```
采集卡: Blackmagic DeckLink Mini Recorder ($150)
├── 输入: SDI (BNC)
├── 输出: PCIe 到服务器
└── 优点: 专业级、低延迟、稳定

服务器: 工作站级 PC
├── CPU: Intel i7 或更高
├── GPU: NVIDIA RTX 3080+ (用于 AI 推理)
├── RAM: 32GB+
├── PCIe: 空闲槽位用于采集卡
└── 网络: 千兆以太网
```

---

## 部署步骤

### 步骤 1: 硬件安装

```bash
1. 关闭服务器电源
2. 安装 PCIe 采集卡到空闲槽位
3. 连接 SDI 线缆:
   达芬奇机器人 [SDI Out] ──SDI线缆──▶ [SDI In] 采集卡
4. 开机
```

### 步骤 2: 驱动安装

```bash
# Linux (以 Blackmagic 为例)
wget https://www.blackmagicdesign.com/...desktop-video-linux.tar.gz
tar -xzf desktop-video-linux.tar.gz
cd Blackmagic_Desktop_Video_Linux
sudo ./install.sh
sudo reboot

# 验证安装
BlackmagicFirmwareUpdater status

# 检查设备是否被识别
ls -la /dev/video*
```

### 步骤 3: 测试采集

```bash
# 方法 A: 使用 FFmpeg 测试
ffmpeg -f v4l2 -list_devices 1 -i dummy
ffplay -f v4l2 -i /dev/video0

# 方法 B: 使用我们的系统测试 (推荐)
# 直接在前端选择 "本地采集卡" 标签页，点击刷新扫描设备
```

### 步骤 4: 连接分析系统 (直接采集方式) ✅ 推荐

**无需 FFmpeg 转流！直接使用前端连接：**

```
1. 启动后端服务: cd video_stream_app && bash run_backend.sh
2. 启动前端服务: cd video_stream_app && bash run_frontend.sh
3. 打开浏览器访问前端
4. 选择 "实时视频流" → "本地采集卡"
5. 点击 "刷新" 扫描设备
6. 选择采集卡并点击 "连接采集卡"
```

### 步骤 4 (备选): 使用 FFmpeg 转流方式

如果直接采集方式有问题，可以使用传统的 FFmpeg 转流：

```bash
# 创建 systemd 服务 (Linux)
sudo cat > /etc/systemd/system/surgery-stream.service << 'EOF'
[Unit]
Description=Surgery Video Stream
After=network.target

[Service]
ExecStart=/usr/bin/ffmpeg -f v4l2 -i /dev/video0 \
          -c:v libx264 -preset ultrafast -tune zerolatency \
          -f rtsp rtsp://localhost:8554/surgery
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable surgery-stream
sudo systemctl start surgery-stream

# 然后在前端使用 "网络视频流" 模式连接:
# rtsp://localhost:8554/surgery
```

---

## 常见问题

### Q1: 延迟多少是可接受的？

| 场景 | 可接受延迟 | 说明 |
|------|------------|------|
| 实时手术辅助 | < 500ms | 需要低延迟采集卡 + ultrafast 编码 |
| 手术记录分析 | < 2s | 标准配置即可 |
| 离线分析 | 无限制 | 可以录制后处理 |

### Q2: 4K 视频是否支持？

支持，但需要：
- 4K 采集卡 (如 DeckLink 4K)
- 高性能 GPU (用于实时分析)
- 建议下采样到 1080p 进行分析，保留 4K 用于存档

### Q3: 多路视频是否支持？

可以支持，需要：
- 多块采集卡或多路采集卡
- 修改系统支持多会话
- 更高的服务器配置

---

## 附录

### A. 当前系统支持的视频源格式

```python
# video_stream_app/backend/routers/video.py

SUPPORTED_SOURCES = {
    # 网络流
    "http://": "HTTP MJPEG 流",
    "https://": "HTTPS 加密流",
    "rtsp://": "RTSP 流 (推荐用于低延迟)",
    
    # 本地文件
    "file://": "本地视频文件 (.mp4, .avi, .mov 等)",
    
    # ✅ 本地采集卡 (已支持!)
    "device://N": "按设备索引连接采集卡 (如 device://0)",
    "device://name": "按设备名称连接 (Windows, 如 device://DeckLink)",
}
```

**API 端点总结：**

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/video/capture-devices` | GET | 列出可用采集设备 |
| `/api/video/connect-capture` | POST | 连接本地采集卡 |
| `/api/video/connect-stream` | POST | 连接网络视频流 |
| `/api/video/mjpeg-proxy/{session_id}` | GET | MJPEG 代理流 |

### B. 相关配置文件

```
video_stream_app/
├── config.json                 # 主配置 (窗口时长、采样间隔等)
├── backend/config.py           # 后端配置
└── frontend/src/config.js      # 前端配置 (如果有)

stream_simulator/
└── config.json                 # 模拟器配置 (fps、端口等)
```

### C. 联系与支持

如需帮助实现直接采集卡支持或部署问题，请联系开发团队。

---

*文档版本: 1.0*  
*最后更新: 2026-01-16*
