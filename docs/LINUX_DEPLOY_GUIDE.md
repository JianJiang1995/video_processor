# Linux 双机部署指南

## 架构

```
+---------------------------+          +---------------------------+
|   GPU 服务器 (机器 A)      |          |   目标机器 (机器 B)        |
|                           |          |                           |
|  SurgR1 vLLM  :9003  ----+---HTTP---+-->  Electron AppImage     |
|  FastAPI 后端  :8001  ----+---HTTP---+-->  (纯前端, 104MB)       |
|  Gemini (云端 API)        |          |                           |
|                           |          |  无需 Python/GPU          |
|  需要: GPU + conda        |          |  需要: 桌面环境            |
+---------------------------+          +---------------------------+
```

SurgR1 通过文件路径读取帧图片，所以后端和 R1 必须在同一台机器上。
Electron 前端通过 HTTP 连接后端，可以在任意机器上运行。

---

## 机器 A：GPU 服务器

### 1. 启动服务

```bash
cd /data2/jj/proj/video_processor/video_stream_app

# 一键启动 backend + SurgR1
bash start_server.sh

# 指定 SurgR1 使用 GPU 3
bash start_server.sh --surgr1-gpu=3

# 只启动后端（不启动 SurgR1）
bash start_server.sh --backend-only

# 停止所有服务
bash start_server.sh --stop
```

启动后会显示服务器 IP 和端口，记下来。

### 2. 确认服务正常

```bash
# 检查后端
curl http://localhost:8001/api/health

# 检查 SurgR1
curl http://localhost:9003/health

# 查看日志
tail -f logs/backend.log
tail -f logs/surgr1.log
```

### 3. 防火墙

确保端口 8001 和 9003 对目标机器开放：

```bash
# 如果有 firewalld
sudo firewall-cmd --add-port=8001/tcp --permanent
sudo firewall-cmd --add-port=9003/tcp --permanent
sudo firewall-cmd --reload

# 如果有 iptables
sudo iptables -A INPUT -p tcp --dport 8001 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 9003 -j ACCEPT

# 或者直接测试（从机器 B）
curl http://<GPU服务器IP>:8001/api/health
```

---

## 机器 B：目标 Linux 机器

### 1. 拷贝 AppImage

```bash
# 从 GPU 服务器拷贝
scp <user>@<GPU服务器IP>:/data2/jj/proj/video_processor/video_stream_app/frontend/dist-electron/Surg-R1手术助手-1.0.0.AppImage ~/

# 添加执行权限
chmod +x ~/Surg-R1手术助手-1.0.0.AppImage
```

### 2. 运行

```bash
./Surg-R1手术助手-1.0.0.AppImage
```

如果报 FUSE 错误：
```bash
# 方式 1: 安装 FUSE
sudo apt install fuse libfuse2

# 方式 2: 解压运行（不需要 FUSE）
./Surg-R1手术助手-1.0.0.AppImage --appimage-extract
./squashfs-root/surg-r1手术助手
```

### 3. 配置后端地址

首次启动后，在应用的设置界面中配置：

- 后端地址: `http://<GPU服务器IP>:8001`

配置会保存在: `~/.config/video-stream-analyzer/config.json`

如果应用没有设置界面，手动创建配置：

```bash
mkdir -p ~/.config/video-stream-analyzer
cat > ~/.config/video-stream-analyzer/config.json << EOF
{
  "backendUrl": "http://<GPU服务器IP>:8001"
}
EOF
```

### 4. 验证连接

在应用中应该能看到：
- 视频流分析功能正常
- SurgR1 分析结果（bbox、action、phase）正常返回
- Gemini 总结正常（需要 GPU 服务器上有 GEMINI_API_KEY）

---

## 文件清单

```
GPU 服务器上需要的文件:
  video_stream_app/
    start_server.sh          # 一键启动脚本
    backend/                 # FastAPI 后端
    config.json              # 配置文件
  SurgR1_api/
    main.py                  # SurgR1 服务
    config.json              # SurgR1 配置
  .env                       # API Keys (GEMINI_API_KEY)
  模型文件:
    /data4/jj/Laparo/.../checkpoint-12042-merged/  (16GB)

目标机器上需要的文件:
  Surg-R1手术助手-1.0.0.AppImage   (104MB, 单文件)
```

---

## 常见问题

### Q: Electron 连不上后端？

1. 确认 GPU 服务器上服务在跑: `curl http://<IP>:8001/api/health`
2. 确认防火墙开放了端口
3. 确认 Electron 配置了正确的后端地址
4. 检查两台机器网络是否互通: `ping <GPU服务器IP>`

### Q: SurgR1 加载模型很慢？

正常，vLLM 首次加载 16GB 模型需要 1-2 分钟。`start_server.sh` 会等待并显示进度。

### Q: 如何更新前端？

在 GPU 服务器上重新构建 AppImage，拷贝到目标机器覆盖即可：

```bash
# GPU 服务器上
cd video_stream_app/frontend
npm run build
npx electron-builder --linux AppImage -c electron-builder.yml

# 拷贝到目标机器
scp dist-electron/Surg-R1手术助手-1.0.0.AppImage <user>@<目标机器>:~/
```

### Q: 想在同一台机器上跑？

直接在 GPU 服务器上运行 AppImage 即可（需要桌面环境或 X11 转发）：

```bash
bash start_server.sh
./frontend/dist-electron/Surg-R1手术助手-1.0.0.AppImage
```

后端地址配置为 `http://localhost:8001`。
