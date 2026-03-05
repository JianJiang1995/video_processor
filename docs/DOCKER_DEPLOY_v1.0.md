# Docker 部署指南

## v1.3 — 分发包 (tar.gz) 独立部署验证通过 (2026-03-05)

**变更摘要**: 在独立 test_deploy 目录中，从零使用 `video-analyzer-dist.tar.gz` 分发包完成完整部署流程验证。

### 测试内容
- 解压 `video-analyzer-dist.tar.gz`（含 docker-compose.yml、.env.example、install.sh、README.md、AppImage 前端）
- 配置 `.env`（GEMINI_API_KEY、MODELSCOPE_API_TOKEN）
- 执行 `bash install.sh` 一键部署

### 测试结果
| 服务 | 端口 | 状态 | 验证方式 |
|------|------|------|---------|
| MySQL 8.0 | 3307 | ✅ healthy | `SHOW DATABASES` 包含 video_analyzer |
| Backend API | 8001 | ✅ healthy | `/api/health` 返回 `{"status":"healthy"}` |
| SurgR1 (vLLM) | 9003 | ✅ healthy | `/health` 返回 `{"status":"healthy","model_loaded":true}` |

### 时间线
- 镜像拉取：< 5s（本地已有缓存；全新环境需从 DockerHub 拉取约 5-10 分钟）
- MySQL 就绪：~10s
- Backend 启动：~15s（等待 MySQL healthy）
- SurgR1 模型下载（16GB from ModelScope）：~4 分钟
- SurgR1 vLLM 引擎初始化（模型加载 + CUDA graph 编译）：~40s
- 总启动时间（含模型下载）：约 5 分钟

### 注意事项
- 当前机器 Docker 需要 sudo 权限（用户未加入 docker 组）
- SurgR1 模型占用 ~15.6 GiB GPU 显存
- 模型缓存在 Docker named volume `test_deploy_model_cache`，后续重启无需重新下载

---

## v1.2 — 全链路测试通过 (2026-03-04, aada329)

**变更摘要**: 修复多个构建和启动问题，完成三服务全链路测试。

### 修改内容
- `docker/Dockerfile.backend`: 换阿里云 apt 镜像（Debian Trixie DEB822 格式 `URIs:` 字段）；改用 `opencv-python-headless` 避免拉入 Mesa/LLVM 大包
- `docker/docker-compose.yml`: MySQL 宿主机端口改 3307（避免与本机 MySQL 的 3306 冲突）；surgr1 和 backend 增加 `image:` 字段（告知 compose 使用预构建镜像，避免重复构建）
- `docker/surgr1_entrypoint.sh`, `docker/Dockerfile.backend`: 重建（之前被意外丢失）
- `.dockerignore`: 补充排除 `video_stream_app/sessions/`（44GB）、`tts_api/`（15GB），build context 降至 6.6MB

### 测试结果（本机验证）
| 服务 | 状态 | 验证方式 |
|------|------|---------|
| mysql | ✅ healthy | docker ps healthcheck |
| backend | ✅ `{"status":"running"}` | `curl http://localhost:8001/` |
| surgr1 | ✅ 自动下载模型 | docker logs 显示 `Downloading from ModelScope` |
| GPU 透传 | ✅ 8× A100 可见 | `docker run --gpus all nvidia-smi` |

### 构建命令（目标机）
```bash
# 1. 构建两个镜像
docker build -f docker/Dockerfile.backend -t video-analyzer-backend:latest .
docker build -f docker/Dockerfile.surgr1 -t video-analyzer-surgr1:latest .

# 2. 启动所有服务
docker compose -f docker/docker-compose.yml --env-file docker/.env up -d

# 3. 查看 surgr1 模型下载进度（首次约 10-20 分钟）
docker logs -f video-analyzer-surgr1
```

---

## v1.1 — 修复构建问题，完善 GPU 支持 (2026-03-04, aada329)

**变更摘要**: 参照已验证的 `/data4/jj/Laparo/docker/` 配置，修复 Dockerfile 和 docker-compose 多处问题；上传模型到 ModelScope 私有仓库实现一键分发。

### 修改内容
- `docker/Dockerfile.surgr1`: 改用 Miniconda + conda env；base image 用 `nvidia/cuda:12.8.0-devel-ubuntu22.04`（对应宿主机 CUDA 12.8）
- `docker/surgr1_requirements.txt`: 新增，vLLM 0.10.1.1 + transformers 4.55.4 + modelscope，对齐宿主机版本
- `docker/docker-compose.yml`: GPU 配置改用 `deploy.resources.reservations.devices`（参照 Laparo 已验证方案）；新增 `ipc: host`、`shm_size: 32gb`（vLLM 必须）；新增 `NVIDIA_DRIVER_CAPABILITIES=compute,utility`
- `.dockerignore`: 新增，排除 sessions/（44GB）、tts_api/（15GB）、node_modules 等，build context 从 47GB 降至 6.6MB
- `docker/Dockerfile.backend`: `libgl1-mesa-glx` 改为 `libgl1`（Debian Trixie 包名变更）
- `docker/surgr1_entrypoint.sh`: 首次启动自动从 ModelScope 下载模型（lonsirky/SurgR1），缓存到 Docker named volume

### ModelScope 模型
- 模型已上传至私有仓库 `lonsirky/SurgR1`（16GB，4 个 safetensors 分片）
- 下载需要 `MODELSCOPE_API_TOKEN`（设置在 `.env` 文件）

### 设计决策
- GPU 使用 `deploy.resources` 而非 `runtime: nvidia`，更符合 Docker Compose v3 规范
- `ipc: host` + `shm_size: 32gb` 是 vLLM 多进程/张量并行必须的配置
- 模型权重不打入镜像（16GB 镜像不实际），而是首次启动从 ModelScope 下载并缓存到 named volume

### 目标机前置准备（一次性）

```bash
# 1. 安装 Docker
curl -fsSL https://get.docker.com | sh

# 2. 安装 nvidia-container-toolkit
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey -o /tmp/nvidia-gpgkey
cat /tmp/nvidia-gpgkey | sudo gpg --dearmor \
    -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -sL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 3. 如 Docker Hub 拉取镜像需要代理
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/http-proxy.conf << EOF
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:23279"
Environment="HTTPS_PROXY=http://127.0.0.1:23279"
Environment="NO_PROXY=localhost,127.0.0.1"
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker
```

### 使用方法

```bash
# 复制项目代码到目标机（无需模型权重）
rsync -avz --exclude='.git' --exclude='node_modules' \
    --exclude='video_stream_app/sessions' \
    --exclude='tts_api/' \
    /data2/jj/proj/video_processor/ user@target:/opt/video_processor/

cd /opt/video_processor/docker
cp .env.example .env
# 编辑 .env：填入 GEMINI_API_KEY、MODELSCOPE_API_TOKEN

# 构建镜像（surgr1 约 30-60 分钟）
sudo docker compose build

# 启动所有服务
sudo docker compose up -d

# 首次启动 surgr1 会下载 16GB 模型，查看进度：
sudo docker compose logs -f surgr1
```

### 影响范围
- 新增 `docker/` 目录（Dockerfile.backend、Dockerfile.surgr1、surgr1_entrypoint.sh、surgr1_requirements.txt、docker-compose.yml、.env.example）
- 新增 `.dockerignore`（项目根目录）
- 不影响现有开发模式和 AppImage 分发流程
