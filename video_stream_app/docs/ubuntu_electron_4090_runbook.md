# Ubuntu 4090 服务器 Electron 本地运行说明

本文档用于把 `video_stream_app` 移到三卡 4090 Ubuntu 服务器，并在那台机器上直接启动 Electron 做真实采集卡测试。

## 1. 先确认服务器 IP

在 4090 服务器上执行：

```bash
hostname -I
ip route get 8.8.8.8 | awk '{print $7; exit}'
ip -4 addr
```

通常第二条会给出当前默认网卡对外访问的 IP。把这个 IP 发给开发侧即可用于 SSH、文件同步和端口访问。

如果已经从本机 SSH 到服务器，也可以看：

```bash
echo "$SSH_CONNECTION"
who
```

注意：当前这台开发机的内网访问地址是 `10.10.41.22`，这不是 4090 服务器的地址。

## 2. 拷贝代码到 4090 服务器

推荐使用 `rsync`，保留文件结构并跳过依赖目录、缓存和运行产物：

```bash
rsync -av --progress \
  --exclude 'node_modules' \
  --exclude 'frontend/node_modules' \
  --exclude 'frontend/dist' \
  --exclude 'data/frames' \
  --exclude 'logs' \
  /data2/jj/proj/video_processor/video_stream_app/ \
  USER@SERVER_IP:/data2/jj/proj/video_processor/video_stream_app/
```

如果服务器上还没有父目录：

```bash
ssh USER@SERVER_IP 'mkdir -p /data2/jj/proj/video_processor/video_stream_app'
```

也可以直接在服务器上用 Git 拉取项目，然后只同步本地未提交的配置和模型路径。

## 3. 服务器基础依赖

在 4090 服务器上安装系统依赖：

```bash
sudo apt update
sudo apt install -y git curl lsof ffmpeg v4l-utils python3-venv python3-pip nodejs npm
```

确认 GPU 和采集卡：

```bash
nvidia-smi
ls /dev/video*
v4l2-ctl --list-devices
ffmpeg -f v4l2 -list_formats all -i /dev/video0
```

如果是 Blackmagic DeckLink 且没有 `/dev/video*`，通常需要安装 Blackmagic Desktop Video 驱动。DeckLink 可能需要走 `ffmpeg -f decklink` 或先转成本地 RTSP/MJPEG，再接入本系统。

## 4. 启动后端和模型服务

进入项目目录：

```bash
cd /data2/jj/proj/video_processor/video_stream_app
```

启动 SurgR1 / YOLO / phase / triplet 相关服务：

```bash
bash start_surgr1_yolo.sh
```

另开一个终端启动后端：

```bash
cd /data2/jj/proj/video_processor/video_stream_app
bash run_backend.sh
```

健康检查：

```bash
curl http://127.0.0.1:8001/api/health
curl http://127.0.0.1:8001/api/analysis/surgr1/status
curl http://127.0.0.1:8001/api/analysis/vlm/status
```

重要限制：SurgR1 当前文档里更像是按单卡大显存部署设计。4090 单卡通常是 24GB，三卡不能自动等价于一张大显存卡。如果 SurgR1 无法在单张 4090 上加载，需要改为量化/小模型、多卡张量并行，或者让后端继续指向现有可用的 SurgR1 服务。YOLO、phase、triplet 这类专家模型可以放在 4090 上跑。

## 5. 本地 Electron 运行

不要通过浏览器访问时，在 4090 服务器的 Ubuntu 桌面环境里运行：

```bash
cd /data2/jj/proj/video_processor/video_stream_app
./run_electron_local.sh
```

该脚本会：

- 使用 Electron 打开本机 `http://127.0.0.1:5133`
- 默认连接本机后端 `http://127.0.0.1:8001`
- 默认进入“本地采集卡”模式
- 没有 `DISPLAY` 时直接报错，避免在纯 SSH shell 中启动出不可见窗口

如果需要临时用模拟器流测试：

```bash
VITE_DEFAULT_SOURCE=stream ./run_electron_local.sh
```

如果模拟器或采集桥接流不是默认端口：

```bash
VITE_DEFAULT_SOURCE=stream \
VITE_DEFAULT_STREAM_URL=http://127.0.0.1:9001/stream \
./run_electron_local.sh
```

如果是通过 SSH 登录，不建议直接跑 Electron，除非已经配置 X11 forwarding。实际测试推荐使用服务器本机显示器、VNC 或 NoMachine。

## 6. 采集卡和模拟器怎么选

真实手术室/达芬奇输出接入时，优先用采集卡，不需要启动 simulator：

1. 在 Electron 里选择“实时视频流”
2. 进入后默认显示“本地采集卡”
3. 点“刷新”
4. 选择 `/dev/videoN` 对应的采集设备
5. 点“连接采集卡”

只有在没有真实视频源时才启动 simulator：

```bash
cd /data2/jj/proj/video_processor/stream_simulator
./start_all.sh ./media/sample.mp4 loop 25
```

然后在 Electron 中使用网络视频流：

```text
http://127.0.0.1:9001/stream
```

如果采集卡不能被 OpenCV/V4L2 直接枚举，建议先做一个采集桥接层，把采集卡输出转成本地 MJPEG/RTSP，再按普通网络流接入。这样主分析链路不需要变化，simulator 也不必承担真实采集卡驱动适配。

