# Video Analyzer Electron 应用指南

三种运行模式：**开发模式**、**前端分发模式**、**独立打包模式（全服务一体）**。

---

## 模式对比

| 特性 | 开发模式 | 前端分发 | 独立打包 |
|------|----------|---------|---------|
| 后端 | 手动启动 | 需单独部署 | **内嵌自动启动** |
| SurgR1 | 手动启动 | 需单独部署 | **内嵌自动启动** |
| VLM总结 | 手动GLM或Gemini | 需单独部署 | **Gemini云端API** |
| 需Python | 是 | 否 | **否** |

### 服务说明

| 服务 | 端口 | 打包方式 |
|------|------|---------|
| FastAPI Backend | 8001 | PyInstaller 内嵌 |
| SurgR1 | 9003 | PyInstaller 内嵌 |
| Gemini | - | 云端API Key |
| GLM/Qwen | 8000 | 不打包，手动启动 |
| SAM3 | 9004 | PyInstaller [可选] |
| TTS | 50000 | 手动启动 |
| ASR | 8765 | 手动启动 |

> Gemini和GLM通过 `config.json` 的 `window_analysis.provider` 切换。

---

## 独立打包模式

### 构建

```bash
cd video_stream_app

bash build_standalone.sh --linux        # Linux
bash build_standalone.sh --win          # Windows
bash build_standalone.sh --all          # 全平台
bash build_standalone.sh --skip-services  # 跳过服务编译
```

### 运行

```bash
chmod +x Surg-R1手术助手-1.0.0.AppImage
./Surg-R1手术助手-1.0.0.AppImage
```

ServiceManager 自动管理：启动时拉起所有服务，退出时关闭。

### 前端API

```javascript
import { getAllServiceStatuses, restartService, onServiceStatusChanged } from '@/utils/electronBridge'
const statuses = await getAllServiceStatuses()
await restartService('surgr1')
```

---

## 开发模式

```bash
# 终端1: 后端
cd video_stream_app && bash run_backend.sh
# 终端2: SurgR1
cd SurgR1_api && bash run.sh
# 终端3: Electron
cd video_stream_app/frontend && npm run electron:dev
```

---

## 前端分发模式

```bash
npm run electron:build:linux
npm run electron:build:win
```

首次启动需配置后端地址。

---

## 常见问题

- **SurgR1超时**: vLLM加载模型需1-2分钟，默认超时120秒
- **切换Gemini/GLM**: 修改 `config.json` 的 `window_analysis.provider`
- **Windows构建**: `bash build_standalone.sh --win`（需wine）或在Windows上直接构建

---

## 文件结构

```
video_stream_app/
  frontend/electron/
    main.cjs            # 主进程 + ServiceManager
    preload.cjs         # 服务管理API
    serviceManager.cjs  # 服务进程管理器
  backend/              # FastAPI源码
  backend_entry.py      # PyInstaller入口
  build_backend.spec    # PyInstaller配置
  build_standalone.sh   # 一键打包脚本
  dist-services/        # 编译输出
  config.json
```
