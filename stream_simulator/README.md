# Stream Simulator

多协议视频流模拟器，用于测试 `video_stream_app` 的实时视频流功能。

## 支持的流类型

| 类型 | 端口 | URL 格式 | 说明 |
|------|------|----------|------|
| **RTSP** | 8554 | `rtsp://localhost:8554/stream` | 标准 RTSP 流 (GStreamer) |
| **HTTP/MJPEG** | 8080 | `http://localhost:8080/stream` | Motion JPEG 流 |
| **HTTP/FLV** | 8080 | `http://localhost:8080/stream.flv` | FLV 流 |
| **HLS** | 8080 | `http://localhost:8080/stream.m3u8` | HLS 分片流 |
| **WebRTC** | 8088 | `http://localhost:8088` (测试页) | WebRTC P2P 流 |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动所有模拟器

```bash
./start_all.sh
# 或指定视频文件:
./start_all.sh /path/to/video.mp4
```

### 3. 单独启动特定类型

```bash
# RTSP 流
python rtsp_server.py --video /path/to/video.mp4

# HTTP/MJPEG 流
python http_server.py --video /path/to/video.mp4

# WebRTC 流
python webrtc_server.py --video /path/to/video.mp4
```

## 配置

编辑 `config.json` 自定义端口和参数：

```json
{
  "video_path": "/data2/jj/proj/video_processor/test_data/2024-12-24_225315_VID002.mp4",
  "streams": {
    "rtsp": { "enabled": true, "port": 8554 },
    "http": { "enabled": true, "port": 8080 },
    "webrtc": { "enabled": true, "port": 8088 }
  }
}
```

## 在 video_stream_app 中使用

1. 启动模拟器
2. 打开 video_stream_app 前端
3. 选择 "实时视频流" 模式
4. 输入对应的流地址，例如：
   - RTSP: `rtsp://localhost:8554/stream`
   - HTTP: `http://localhost:8080/stream`
   - WebRTC: 通过 `/api/webrtc/offer` 端点

## 依赖

- Python 3.8+
- OpenCV
- aiohttp
- aiortc (WebRTC)
- ffmpeg (RTSP/HLS)





