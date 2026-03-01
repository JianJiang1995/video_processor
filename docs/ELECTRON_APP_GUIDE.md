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

### 构建

```bash
cd video_stream_app/frontend
npm run electron:build:linux   # Linux（生成 AppImage + deb）
npm run electron:build:win     # Windows（生成 nsis 安装包）
```

产物位于 `frontend/dist-electron/` 目录。

### 运行

AppImage 需要 FUSE 支持。如果系统没有 FUSE（`dlopen(): error loading libfuse.so.2`），先解压再运行：

```bash
cd video_stream_app/frontend/dist-electron

# 方式1: 直接运行（需要系统已安装 libfuse2）
chmod +x "Surg-R1手术助手-1.0.0.AppImage"
./"Surg-R1手术助手-1.0.0.AppImage"

# 方式2: 无 FUSE 环境，先解压再运行
./"Surg-R1手术助手-1.0.0.AppImage" --appimage-extract
cd squashfs-root
./video-stream-analyzer --no-sandbox
```

> **远程服务器注意**：Electron 是 GUI 应用，需要图形显示环境。
> 通过 SSH 连接时，请使用支持 X11 转发的客户端（如 MobaXterm），
> 或在 SSH 命令中加 `-X` 参数：`ssh -X user@server`。

### 预览模式（不打包，直接运行）

如果只想快速运行生产优化版本而不生成安装包：

```bash
cd video_stream_app/frontend
npm run electron:preview   # vite build + electron .
```

性能与打包版一致，适合本地验证。

首次启动需配置后端地址。后端服务需单独部署。

---

## 开发注意事项

### 开发模式 vs 打包模式的网络请求差异

这是最关键的架构差异，开发时务必注意：

| | 开发模式 (`electron:dev`) | 打包模式 (`electron:build`) |
|---|---|---|
| 前端加载方式 | Vite dev server (`http://localhost:5174`) | 本地文件 (`file://...dist/index.html`) |
| API 请求路径 | `/api/*` 相对路径 | 需要完整 URL `http://127.0.0.1:8001/api/*` |
| 代理机制 | Vite proxy 自动转发到 `localhost:8001` | **无代理**，必须设置 axios baseURL |

前端代码中有三类网络请求，打包后都会因为缺少代理而失败：

| 请求方式 | 示例 | 解决方案 |
|----------|------|---------|
| `axios` | `axios.post('/api/video/connect-stream')` | `main.js` 中设置 `axios.defaults.baseURL` |
| `EventSource` | `new EventSource('/api/analysis/stream-summaries/...')` | 用 `apiUrl()` 包裹路径 |
| `fetch` / `sendBeacon` | `fetch('/api/analysis/export-clips/...')` | 用 `apiUrl()` 包裹路径 |

**解决方案 1：axios 请求**（在 `src/main.js` 中，app 挂载前 await）：

```javascript
import { isElectron, initBackendUrl } from '@/utils/electronBridge'

async function bootstrap() {
  if (isElectron()) {
    const url = await initBackendUrl()
    if (url) axios.defaults.baseURL = url
  }
  const app = createApp(App)
  app.use(createPinia())
  app.mount('#app')
}
bootstrap()
```

**解决方案 2：非 axios 请求**（EventSource / fetch / sendBeacon）用 `apiUrl()` 包裹：

```javascript
import { apiUrl } from '@/utils/electronBridge'

// 开发模式下 apiUrl('/api/xxx') 返回 '/api/xxx'（走 Vite proxy）
// 打包模式下 apiUrl('/api/xxx') 返回 'http://127.0.0.1:8001/api/xxx'
new EventSource(apiUrl(`/api/analysis/stream-summaries/${sessionId}`))
fetch(apiUrl(`/api/analysis/export-clips/${sessionId}`), { method: 'POST' })
navigator.sendBeacon(apiUrl(`/api/analysis/stop-surgr1-continuous/${sessionId}`), '')
```

> **开发时新增 API 调用规则**：
> - `axios` 调用：直接用相对路径（如 `/api/xxx`），baseURL 机制自动处理
> - `EventSource` / `fetch` / `sendBeacon`：**必须用 `apiUrl()` 包裹**

### 相关文件

| 文件 | 作用 |
|------|------|
| `frontend/vite.config.js` | 开发模式代理配置（`server.proxy`） |
| `frontend/src/main.js` | app 挂载前 await 解析后端地址，设 `axios.defaults.baseURL` |
| `frontend/src/utils/electronBridge.js` | `initBackendUrl()` 初始化、`apiUrl()` 路径拼接、`getBackendUrl()` 异步获取 |
| `frontend/electron/main.cjs` | 主进程，`loadAppConfig()` 加载 config.json 确定后端端口 |

---

## 常见问题

- **SurgR1超时**: vLLM加载模型需1-2分钟，默认超时120秒
- **切换Gemini/GLM**: 修改 `config.json` 的 `window_analysis.provider`
- **Windows构建**: `bash build_standalone.sh --win`（需wine）或在Windows上直接构建
- **AppImage FUSE 报错**: 见上方"前端分发模式 > 运行"中的方式2
- **Missing X server**: 需要图形显示环境，远程连接请开启 X11 转发
- **打包后"网络错误，请检查后端服务"**: 见上方"开发注意事项"，确认 `main.js` 中有 axios baseURL 设置

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
