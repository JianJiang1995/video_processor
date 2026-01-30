# Video Analyzer Electron 应用指南

本文档介绍 Video Analyzer 的两种运行模式：**开发模式** 和 **生产模式（可分发应用）**。

---

## 目录

- [概念说明](#概念说明)
- [开发模式](#开发模式)
- [生产模式（可分发应用）](#生产模式可分发应用)
- [X11 远程测试](#x11-远程测试)
- [常见问题](#常见问题)

---

## 概念说明

### 两种模式对比

| 特性 | 开发模式 | 生产模式 |
|------|----------|----------|
| 用途 | 开发调试 | 用户部署 |
| 启动命令 | `npm run electron:dev` | 双击 AppImage 或安装 deb |
| 代码更新 | 实时热更新 | 需要重新打包 |
| 性能 | 较慢（有调试开销） | 最优 |
| 文件大小 | 源码形式 | 压缩打包（~100MB） |
| DevTools | 自动打开 | 不包含 |

### 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        开发模式                                  │
│  ┌──────────┐     ┌──────────────┐     ┌──────────────┐        │
│  │ 源代码    │ ──→ │ Vite 服务器  │ ←── │ Electron     │        │
│  │ .vue/.js │     │ :5174        │     │ 窗口         │        │
│  └──────────┘     └──────────────┘     └──────────────┘        │
│       ↑                                                         │
│   修改代码 → 自动刷新                                            │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                        生产模式                                  │
│  ┌──────────────┐     ┌──────────────────────────────┐         │
│  │ .AppImage    │ ──→ │ 内置 dist/ 静态文件           │         │
│  │ 或 .deb      │     │ + Electron 运行时             │         │
│  └──────────────┘     └──────────────────────────────┘         │
│                              ↓                                  │
│                       直接运行，无需安装 Node.js                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 开发模式

开发模式适用于：
- 开发新功能
- 调试问题
- 测试代码修改

### 环境要求

- Node.js >= 18.x
- npm >= 9.x
- Linux 桌面环境（或 X11 转发）

### 安装依赖

```bash
cd /data2/jj/proj/video_processor/video_stream_app/frontend
npm install
```

### 启动开发模式

```bash
# 方式 1: 同时启动 Vite + Electron（推荐）
npm run electron:dev
 
# 方式 2: 仅启动 Vite 服务器（浏览器访问）
npm run dev                            
# 然后在浏览器打开 http://localhost:5174
```

### 开发模式特点

1. **热模块替换 (HMR)**
   - 修改 Vue 组件后自动刷新
   - 无需手动重启应用

2. **开发者工具**
   - Electron DevTools 自动打开
   - 可以在控制台查看日志和调试

3. **API 代理**
   - Vite 自动代理 `/api` 和 `/sessions` 请求到后端
   - 配置在 `vite.config.js` 中

### 可用脚本

| 命令 | 说明 |
|------|------|
| `npm run dev` | 仅启动 Vite 开发服务器 |
| `npm run electron:dev` | Vite + Electron 开发模式 |
| `npm run build` | 仅构建前端静态文件 |
| `npm run electron:preview` | 构建后预览 Electron 应用 |

---

## 生产模式（可分发应用）

生产模式用于：
- 部署到医院等实际环境
- 分发给最终用户
- 无需安装开发环境即可运行

### 构建应用

```bash
cd /data2/jj/proj/video_processor/video_stream_app/frontend

# 构建 Linux 版本
npm run electron:build:linux

# 构建 Windows 版本（需要在 Windows 上或安装 wine）
npm run electron:build:win

# 构建所有平台
npm run electron:build
```

### 构建产物

构建完成后，在 `dist-electron/` 目录下生成：

```
dist-electron/
├── VideoAnalyzer-1.0.0.AppImage     # Linux 免安装包（推荐）
├── video-stream-analyzer_1.0.0_amd64.deb  # Debian/Ubuntu 安装包
└── linux-unpacked/                  # 解压后的目录版本
```

### 分发格式说明

#### AppImage（推荐）

**特点**：
- 单文件，免安装
- 跨 Linux 发行版兼容
- 用户双击即可运行

**使用方法**：
```bash
# 添加执行权限
chmod +x VideoAnalyzer-1.0.0.AppImage

# 直接运行
./VideoAnalyzer-1.0.0.AppImage
```

#### DEB 安装包

**特点**：
- 适用于 Debian/Ubuntu 系统
- 集成到系统菜单
- 支持自动更新

**使用方法**：
```bash
# 安装
sudo dpkg -i video-stream-analyzer_1.0.0_amd64.deb

# 如果有依赖问题
sudo apt-get install -f

# 卸载
sudo dpkg -r video-stream-analyzer
```

#### Windows 安装包

构建后生成 `.exe` 安装程序：
```
dist-electron/
└── VideoAnalyzer Setup 1.0.0.exe
```

双击安装，按向导完成即可。

### 配置后端地址

生产模式下，应用需要连接到后端服务器。首次启动时：

1. 打开应用
2. 进入设置界面
3. 配置后端服务器地址（如 `http://192.168.1.100:8001`）
4. 点击测试连接
5. 保存设置

配置会持久化保存到：
- Linux: `~/.config/video-stream-analyzer/config.json`
- Windows: `%APPDATA%/video-stream-analyzer/config.json`

---

## X11 远程测试

在无显示器的 GPU 服务器上测试 Electron 应用。

> ⚠️ **重要提示**：X11 转发需要在支持 X11 的终端中运行，**Cursor/VSCode 内置终端不支持 X11 转发**。
> 请使用 MobaXterm（推荐）或其他支持 X11 的 SSH 客户端。

### 服务器端配置

```bash
# 确保 SSH 服务器允许 X11 转发
sudo vim /etc/ssh/sshd_config

# 确认以下配置：
# X11Forwarding yes
# X11DisplayOffset 10

sudo systemctl restart sshd
```

### 客户端配置

#### Windows - MobaXterm（推荐）

MobaXterm 自带 X Server，X11 转发开箱即用：

1. 下载 [MobaXterm](https://mobaxterm.mobatek.net/download.html)（免费版即可）
2. 打开 MobaXterm → **Session** → **SSH**
3. 配置连接：
   - Remote host: `服务器地址`
   - Port: `SSH端口`
   - Username: `用户名`
4. 点击 OK 连接

#### Windows - VcXsrv + PuTTY

1. 安装 [VcXsrv](https://sourceforge.net/projects/vcxsrv/)
2. 启动 XLaunch：
   - 选择 "Multiple windows"
   - 选择 "Start no client"
   - **勾选 "Disable access control"**（重要！）
3. 使用 PuTTY 连接：
   - Connection → SSH → X11 → **勾选 "Enable X11 forwarding"**
   - X display location 填：`localhost:0`

#### Linux/Mac

- Linux 自带 X Server，直接使用 `ssh -X` 或 `ssh -Y`
- Mac 需安装 [XQuartz](https://www.xquartz.org/)

### 连接并测试

**在 MobaXterm 终端中执行**（不是 Cursor 终端）：

```bash
# 验证 X11 是否工作
echo $DISPLAY        # 应该显示 localhost:10.0 或类似值
xclock               # 应该弹出时钟窗口，Ctrl+C 关闭

# 如果提示 .Xauthority 不存在，先创建：
touch ~/.Xauthority

# 测试开发模式
cd /data2/jj/proj/video_processor/video_stream_app/frontend
npm run electron:dev

# 测试生产模式
./dist-electron/VideoAnalyzer-1.0.0.AppImage
```

### X11 清晰度优化

通过 X11 转发显示的应用可能在高分屏上显示模糊，可以尝试以下方法：

#### 方法 1：设置 Electron 缩放因子

```bash
# 强制使用 1x 缩放（适合 1080p 显示器）
GDK_SCALE=1 npm run electron:dev

# 强制使用 2x 缩放（适合 4K 显示器）
GDK_SCALE=2 npm run electron:dev
```

#### 方法 2：设置 DPI

```bash
# 设置更高的 DPI
GDK_DPI_SCALE=1.5 npm run electron:dev
```

#### 方法 3：Electron 启动参数

```bash
# 强制设备缩放因子
npm run electron:dev -- --force-device-scale-factor=1.5
```

#### 方法 4：MobaXterm 设置

1. 打开 MobaXterm → **Settings** → **X11**
2. 调整 **DPI** 设置（如 144 或 192）
3. 重新连接

### X11 性能优化

如果延迟较高：

```bash
# 使用压缩（在 MobaXterm 外使用 ssh 命令时）
ssh -X -C user@gpu-server

# 或使用更快的加密算法
ssh -X -c aes128-ctr user@gpu-server
```

MobaXterm 设置中可以开启压缩：**Settings** → **SSH** → **Compression**

---

## 常见问题

### Q: 开发模式启动后白屏？

**A**: 可能是 Vite 服务器还没启动完成。等待几秒后刷新，或检查终端是否有错误。

### Q: 构建时报错 "Please specify project homepage"？

**A**: `package.json` 缺少必要字段。确保包含：
```json
{
  "description": "...",
  "author": {
    "name": "...",
    "email": "..."
  },
  "homepage": "https://..."
}
```

### Q: AppImage 双击无法运行？

**A**: 需要添加执行权限：
```bash
chmod +x VideoAnalyzer-1.0.0.AppImage
```

### Q: 如何更新已部署的应用？

**A**: 
1. 重新构建：`npm run electron:build:linux`
2. 将新的 AppImage 发送到目标机器
3. 替换旧文件即可（配置会保留）

### Q: 后端连接失败？

**A**:
1. 确认后端服务正在运行
2. 检查防火墙是否允许端口访问
3. 在设置中确认后端地址正确
4. 使用 `curl http://backend:8001/api/health` 测试连接

### Q: 构建时间太长？

**A**: 首次构建需要下载 Electron 二进制文件（~100MB）。后续构建会使用缓存，约 1-2 分钟。

### Q: 报错 "Missing X server or $DISPLAY"？

**A**: 这是因为没有 X11 显示环境。有两种情况：

1. **在 Cursor/VSCode 终端运行**：内置终端不支持 X11 转发，请使用 MobaXterm 等支持 X11 的 SSH 客户端。

2. **DISPLAY 变量为空**：检查 `echo $DISPLAY`，如果为空说明 X11 转发未建立：
   ```bash
   # 使用 MobaXterm 连接，或用 ssh -Y 连接
   ssh -Y user@server
   ```

### Q: X11 转发后应用显示模糊？

**A**: 这是 DPI 缩放问题，尝试设置缩放因子：
```bash
GDK_SCALE=1 GDK_DPI_SCALE=1.5 npm run electron:dev
# 或
npm run electron:dev -- --force-device-scale-factor=1.5
```

### Q: 报错 "require is not defined in ES module scope"？

**A**: 这是 ES Module 和 CommonJS 冲突。确保 Electron 文件使用 `.cjs` 扩展名：
- `electron/main.cjs`
- `electron/preload.cjs`
- `electron/frameCache.cjs`

并在 `package.json` 中设置 `"main": "electron/main.cjs"`。

---

## 文件结构

```
video_stream_app/frontend/
├── electron/
│   ├── main.cjs         # Electron 主进程（CommonJS 格式）
│   ├── preload.cjs      # 预加载脚本（CommonJS 格式）
│   ├── frameCache.cjs   # 帧缓存模块（CommonJS 格式）
│   └── icon.svg         # 应用图标
├── src/
│   ├── utils/
│   │   └── electronBridge.js  # Electron API 封装
│   ├── components/      # Vue 组件
│   ├── App.vue          # 主应用组件
│   └── main.js          # Vue 入口
├── dist/                # Vite 构建输出
├── dist-electron/       # Electron 打包输出
├── package.json         # 项目配置（type: module）
├── vite.config.js       # Vite 配置
└── electron-builder.yml # 打包配置
```

> **注意**：Electron 文件使用 `.cjs` 扩展名是因为 `package.json` 设置了 `"type": "module"`，
> 而 Electron 主进程需要使用 CommonJS 语法。`.cjs` 扩展名告诉 Node.js 以 CommonJS 模式解析这些文件。

---

## 参考链接

- [Electron 官方文档](https://www.electronjs.org/docs)
- [Vite 官方文档](https://vitejs.dev/)
- [electron-builder 文档](https://www.electron.build/)
- [Vue 3 文档](https://vuejs.org/)
