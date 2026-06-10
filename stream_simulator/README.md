# Stream Simulator

多协议视频流模拟器，用于测试 `video_stream_app` 的实时视频流能力。

## 支持的流类型

| 类型 | 默认端口 | URL 格式 | 说明 |
|------|----------|----------|------|
| **RTSP** | 8554 | `rtsp://localhost:8554/stream` | RTSP 模拟流 |
| **HTTP/MJPEG** | 9001 | `http://localhost:9001/stream` | Motion JPEG 流 |
| **WebRTC** | 9002 | `http://localhost:9002` | WebRTC 测试页 |

## 视频文件查找顺序

如果没有显式传入 `--video`，程序会按以下顺序寻找可播放视频：

1. 环境变量 `STREAM_SIMULATOR_VIDEO`
2. `config.json` 中的 `video_path`
3. `stream_simulator/media/sample.mp4`
4. `stream_simulator/media/` 下找到的第一个视频文件
5. `stream_simulator/uploads/` 下找到的第一个视频文件

这意味着把目录压缩后拷到其他机器时，只要把测试视频放到 `media/sample.mp4`，就可以直接运行。

## 快速开始

### 使用 pip

```bash
pip install -r requirements.txt
./start_all.sh /path/to/video.mp4
```

### 使用 conda

```bash
chmod +x install_conda_env.sh
./install_conda_env.sh
conda activate stream-simulator
./start_all.sh /path/to/video.mp4
```

### 不传视频时直接运行

```bash
mkdir -p media
cp /path/to/video.mp4 media/sample.mp4
./start_all.sh
```

## 单独启动

```bash
# HTTP/MJPEG
python http_server.py --video /path/to/video.mp4 --port 9001

# WebRTC
python webrtc_server.py --video /path/to/video.mp4 --port 9002

# RTSP
python rtsp_server.py --video /path/to/video.mp4 --port 8554
```

也可以使用统一入口：

```bash
python run.py http --video /path/to/video.mp4
python run.py webrtc --video /path/to/video.mp4
python run.py rtsp --video /path/to/video.mp4
python run.py all --video /path/to/video.mp4
```

## 便携打包

生成一个可发给其他机器的压缩包：

```bash
chmod +x package_portable.sh
./package_portable.sh
```

如果希望压缩包里直接带一个可运行视频：

```bash
./package_portable.sh /path/to/video.mp4
```

压缩包内会包含：

- 所有运行代码
- `requirements.txt`
- `environment.yml`
- `install_conda_env.sh`
- `start_all.sh`
- `media/` 目录

## 配置

`config.json` 主要用于统一入口 `run.py`：

```json
{
  "video_path": "media/sample.mp4",
  "streams": {
    "rtsp": { "enabled": true, "port": 8554 },
    "http": { "enabled": true, "port": 9001 },
    "webrtc": { "enabled": true, "port": 9002 }
  }
}
```

## 在 `video_stream_app` 中使用

1. 启动模拟器
2. 打开 `video_stream_app` 前端
3. 选择“实时视频流”模式
4. 输入地址，例如 `http://localhost:9001/stream`

## 依赖说明

- Python 3.10 推荐
- OpenCV
- aiohttp
- aiortc
- ffmpeg





