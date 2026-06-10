# Surg-R1 Startup Guide

## GPU 分配

系统有 8x A100-80GB。启动脚本会**自动检测空闲 GPU** 并分配：

| Service | 显存需求 | 说明 |
|---------|----------|------|
| SurgR1 (Qwen2.5-VL) | ~60GB | 自动选空闲最大的 GPU |
| YOLO26s | ~1GB | 自动选另一块空闲 GPU（不与 SurgR1 同卡）|
| Gemini Pro 3.1 | — | Cloud API，无本地 GPU |
| Backend / Frontend / Simulator | — | CPU only |

---

## Step 0: 首次安装 / 迁移到新机器

### 0.1 系统依赖

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y \
    mysql-server \
    python3-pip \
    ffmpeg \
    fonts-noto-color-emoji fonts-symbola fonts-noto-cjk \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libnss3 libnspr4 libdrm2 libxss1 \
    libgtk-3-0 libatspi2.0-0 libpango-1.0-0 libcairo2 libcairo-gobject2
```

### 0.2 MySQL 初始化

```bash
sudo systemctl start mysql
sudo systemctl enable mysql     # 开机自启

# 创建数据库和用户（backend 使用 user=jj, password 空）
sudo mysql <<EOF
CREATE DATABASE IF NOT EXISTS video_analyzer
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'jj'@'localhost' IDENTIFIED BY '';
GRANT ALL PRIVILEGES ON video_analyzer.* TO 'jj'@'localhost';
FLUSH PRIVILEGES;
EOF
```

### 0.3 Conda 环境

```bash
# 创建 vllm 环境（SurgR1 + Backend 共用）
conda create -n vllm python=3.10 -y
conda activate vllm

cd /path/to/video_processor/video_stream_app
pip install -r requirements.txt
pip install ultralytics google-genai pymysql
```

### 0.4 环境变量（.env）

```bash
# /data2/jj/proj/video_processor/.env
GEMINI_API_KEY=AIzaSyCOtraBROBrwOjaVl7uNe0K62sVYH3UoLA
OPENAI_API_KEY=...   # 可选，备用 TTS
```

### 0.5 前端依赖

```bash
cd video_stream_app/frontend
npm install
```

### 0.6 迁移需要打包的文件/目录

从旧机器迁移到新机器时，**必须**复制：

| 路径 | 说明 |
|------|------|
| `video_stream_app/` | 整个项目代码 |
| `SurgR1_api/` | SurgR1 服务代码 |
| `stream_simulator/` | 视频流模拟器 |
| `.env` | API keys |
| `/data4/jj/proj/surg_agent/detection_expert/runs/yolo26s_cholec_tool/` | YOLO26s 权重 |
| SurgR1 模型路径（见 `SurgR1_api/config.json` 的 `model.path`） | Qwen2.5-VL 权重 |

**可选**（如果要保留历史数据）：
- `video_stream_app/sessions/` — 历史分析 session
- MySQL 数据库 dump：`mysqldump -u jj video_analyzer > video_analyzer.sql`

**新机器恢复数据库：**
```bash
mysql -u jj video_analyzer < video_analyzer.sql
```

### 0.7 迁移后检查清单

```bash
# 1. MySQL 能连
mysql -u jj video_analyzer -e "SHOW TABLES;"

# 2. Gemini API 可达（通过代理）
https_proxy=http://127.0.0.1:7897 \
  curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY" | head -5

# 3. GPU 可用
nvidia-smi

# 4. YOLO 模型存在
ls /data4/jj/proj/surg_agent/detection_expert/runs/yolo26s_cholec_tool/weights/best.pt

# 5. SurgR1 模型存在
python3 -c "import json; print(json.load(open('SurgR1_api/config.json'))['model']['path'])"
```

---

## Step 1: Stream Simulator（视频流源）

提供 MJPEG/WebRTC 视频流给系统分析。

```bash
cd /data2/jj/proj/video_processor/stream_simulator

# 基本用法（播放一次）
./start_all.sh /path/to/surgical_video.mp4

# 循环播放（推荐测试用）
./start_all.sh /path/to/surgical_video.mp4 loop

# 指定帧率
./start_all.sh /path/to/surgical_video.mp4 loop 25
```

**端口：**
- HTTP/MJPEG: `http://localhost:9001/stream`
- WebRTC: `http://localhost:9002`

**验证：** 浏览器打开 `http://localhost:9001/stream` 应看到视频流。

---

## Step 2: SurgR1 + YOLO（一键启动）

使用 `start_surgr1_yolo.sh` 一键启动，脚本会自动：
1. 检测 MySQL 服务并尝试启动（如未运行）
2. 验证数据库连接
3. 扫描所有 GPU，打印显存使用情况
4. 为 SurgR1 选择空闲最大的 GPU（需 ≥60GB）
5. 为 YOLO 选择另一块不同的 GPU（需 ≥2GB）
6. 启动 SurgR1 并等待模型加载完毕
7. 验证 YOLO 模型能正常推理

```bash
cd /data2/jj/proj/video_processor/video_stream_app

# 自动检测 GPU（推荐）
bash start_surgr1_yolo.sh

# 自动检测 + 运行检测测试
bash start_surgr1_yolo.sh --test

# 手动指定 GPU
bash start_surgr1_yolo.sh --surgr1-gpu 2 --yolo-gpu 4
```

**输出示例：**
```
[GPU] Scanning GPUs...

  GPU   Total(MB)     Used(MB)      Free(MB)      Status
  ──────────────────────────────────────────────────────
  0     81920         62171         19749         free (YOLO OK)
  2     81920         5421          76499         free (SurgR1 OK)
  3     81920         654           81266         free (SurgR1 OK)
  ...

  → Auto-selected GPU 3 for SurgR1 (most free memory)
  → Auto-selected GPU 2 for YOLO (different from SurgR1)
```

**安全检查：** 脚本会强制校验 SurgR1 和 YOLO 不在同一 GPU，否则报错退出。

**端口：** SurgR1 → `http://localhost:9003`

**注意：** SurgR1 模型加载需 1-3 分钟，脚本会自动等待并检测健康状态。

---

## Step 3: Backend API（核心服务）

```bash
cd /data2/jj/proj/video_processor/video_stream_app
bash run_backend.sh
```

**端口：** `http://localhost:8001`

**API 文档：** `http://localhost:8001/api/docs`

Backend 启动时会自动加载：
- YOLO26s 工具检测模型（使用 Step 2 配置的 GPU）
- Gemini Embedding 服务（需要 `GEMINI_API_KEY` 环境变量）
- Gemini Pro 3.1 窗口总结（Cloud API）

**验证：**
```bash
curl http://localhost:8001/api/config
curl http://localhost:8001/api/analysis/surgr1/status
```

---

## Step 4: Frontend（Electron 桌面应用）

```bash
cd /data2/jj/proj/video_processor/video_stream_app
bash run_frontend.sh

# 或手动：
cd frontend
npm run electron:dev
```

**Vite Dev Server:** `http://localhost:5133`

Electron 窗口会自动打开。如果没有，手动访问 `http://localhost:5133`。

---

## 一键启动（Backend + Frontend）

```bash
cd /data2/jj/proj/video_processor/video_stream_app
bash start_all.sh
```

日志输出在 `logs/` 目录。

---

## 快速检查所有服务

```bash
echo "=== Service Status ==="
echo -n "Simulator (9001): "; curl -s -o /dev/null -w "%{http_code}" http://localhost:9001/ 2>/dev/null || echo "DOWN"
echo -n "SurgR1    (9003): "; curl -s -o /dev/null -w "%{http_code}" http://localhost:9003/health 2>/dev/null || echo "DOWN"
echo -n "Backend   (8001): "; curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/api/config 2>/dev/null || echo "DOWN"
echo -n "Frontend  (5133): "; curl -s -o /dev/null -w "%{http_code}" http://localhost:5133/ 2>/dev/null || echo "DOWN"
```

---

## 可选服务

### SAM3（分割）
```bash
cd /data2/jj/proj/video_processor/sam3_api
bash run.sh  # Port 9004
```

### TTS（语音合成）
```bash
cd /data2/jj/proj/video_processor/tts_api
bash run.sh  # Port 50000
```

### ASR（语音识别）
```bash
cd /data2/jj/proj/video_processor/asr_api
bash run.sh  # Port 8765
```

---

## 停止所有服务

```bash
for port in 9001 9002 9003 8001 5133 5176; do
  PID=$(lsof -t -i:$port 2>/dev/null)
  if [ -n "$PID" ]; then
    echo "Stopping port $port (PID: $PID)"
    kill -9 $PID 2>/dev/null
  fi
done
```

---

## 远程访问（SSH 本地端口转发）

若服务器仅内网可达，可在 **你自己的 Windows/Linux 笔记本**上对 `ssh` 加 `-L`，把前端、后端、流端口转到本机 `127.0.0.1`，再用本机浏览器打开（比远程 X11 跑 Electron 更流畅）。可复制命令与 MobaXterm 说明见：[video_stream_ssh_tunnel_v1.0.md](../../docs/video_stream_ssh_tunnel_v1.0.md)。

---

## 端口速查

| Port | Service |
|------|---------|
| 5133 | Frontend (Vite dev) |
| 8001 | Backend (FastAPI) |
| 9001 | Stream Simulator (MJPEG) |
| 9002 | Stream Simulator (WebRTC) |
| 9003 | SurgR1 API |
| 9004 | SAM3（可选）|
| 50000 | TTS（可选）|
| 8765 | ASR（可选）|

---

## 常见问题

### Q: YOLO 没有检测到工具？
A: YOLO 只对腹腔镜手术工具训练过（8 类）。确认视频中有可见器械，且 `config.json` 中 `yolo.enabled = true`。

### Q: Gemini API 报错？
A: 检查 `.env` 文件中 `GEMINI_API_KEY` 是否设置，以及代理是否正常（默认 `http_proxy=http://127.0.0.1:7897`，也可以用 `CLASH_HTTP_PROXY` 覆盖）。

### Q: SurgR1 加载很慢？
A: Qwen2.5-VL 模型约 60GB，首次加载需要 1-3 分钟。脚本会自动等待，看到 `SurgR1 is ready!` 即可。

### Q: start_surgr1_yolo.sh 报 "No GPU with >=60000MB free"？
A: 所有 GPU 都被占满了。用 `nvidia-smi` 检查，释放一块 GPU 或用 `--surgr1-gpu X` 手动指定。

### Q: Electron 打开白屏？
A: 确认 Backend（`http://localhost:8001`）和 Vite Dev Server（`http://localhost:5133`）都已启动。
