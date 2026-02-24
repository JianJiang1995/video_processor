# Windows 构建与部署指南

## 为什么不能在 Linux 上交叉编译？

- PyInstaller 不支持交叉编译（Linux 上只能编译 Linux 可执行文件）
- electron-builder 交叉编译 Windows 需要 wine（当前服务器未安装）
- 因此 Windows 版本必须在 Windows 机器上构建

---

## 第一步：准备 Windows 环境

### 必装软件

1. **Python 3.10+**
   - 下载: https://www.python.org/downloads/
   - 安装时勾选 "Add Python to PATH"

2. **Node.js 18+**
   - 下载: https://nodejs.org/
   - 安装 LTS 版本

3. **Git**
   - 下载: https://git-scm.com/download/win

4. **NVIDIA GPU 驱动 + CUDA**（SurgR1 需要）
   - 下载: https://developer.nvidia.com/cuda-downloads

### 安装 Python 依赖

```powershell
# 创建虚拟环境（推荐）
python -m venv venv
.\venv\Scripts\activate

# 安装后端依赖
pip install -r video_stream_app\requirements.txt

# 安装 PyInstaller
pip install pyinstaller

# 安装 vLLM（SurgR1 需要，需要 CUDA）
pip install vllm
```

---

## 第二步：拷贝项目到 Windows

### 方式 A：Git clone（推荐）

```powershell
git clone <your-repo-url> video_processor
cd video_processor
git checkout feature/chat-async-tts
```

### 方式 B：直接拷贝

把整个 `video_processor/` 目录拷贝到 Windows，需要包含：

```
video_processor/
  .env                    # API Keys（重要！）
  video_stream_app/
    backend/              # FastAPI 后端源码
    frontend/             # Electron + Vue 前端
    config.json
    backend_entry.py
    build_backend.spec
    build_standalone_win.bat   # Windows 构建脚本
    requirements.txt
  SurgR1_api/             # SurgR1 源码
  glm_api/
    background.txt        # 背景知识文件
```

---

## 第三步：一键构建

```powershell
cd video_processor\video_stream_app

# 双击或命令行运行
build_standalone_win.bat
```

脚本会自动：
1. PyInstaller 编译 FastAPI 后端 → `dist-services\backend\`
2. PyInstaller 编译 SurgR1 → `dist-services\surgr1\`
3. Vite 构建前端 → `frontend\dist\`
4. electron-builder 打包 → `frontend\dist-electron\`

### 构建产物

```
frontend\dist-electron\
  Surg-R1手术助手 Setup 1.0.0.exe    # NSIS 安装包
  Surg-R1手术助手 1.0.0.exe          # Portable 免安装版
```

---

## 第四步：部署到目标 Windows 机器

### 安装版（推荐）

1. 拷贝 `Surg-R1手术助手 Setup 1.0.0.exe` 到目标机器
2. 双击运行安装程序
3. 安装完成后从桌面快捷方式启动

### 免安装版

1. 拷贝 `Surg-R1手术助手 1.0.0.exe` 到目标机器
2. 直接双击运行

### 目标机器要求

- Windows 10/11 64-bit
- NVIDIA GPU + 驱动（SurgR1 需要）
- 网络连接（Gemini API 需要）
- 无需安装 Python、Node.js

---

## 第五步：配置

### API Key

`.env` 文件已打包进应用。如需修改：

安装版路径：
```
C:\Users\<用户名>\AppData\Local\Programs\surg-r1手术助手\resources\backend\.env
```

Portable 版解压后：
```
resources\backend\.env
```

### SurgR1 模型路径

SurgR1 的 `config.json` 中 `model.path` 需要指向 Windows 上的模型路径：

```json
{
  "model": {
    "path": "D:\\models\\SurgR1\\checkpoint-12042-merged"
  }
}
```

修改位置：`resources\surgr1\config.json`

### Gemini / GLM 切换

修改 `resources\backend\config.json` 中的 `window_analysis.provider`：
- `"gemini"` — 默认，云端 API
- `"glm"` — 本地 GLM（需手动启动）

---

## 常见问题

### Q: 构建时 vLLM 安装失败？

vLLM 在 Windows 上支持有限。如果 SurgR1 编译失败：
- 可以先只构建 backend：修改 bat 脚本跳过 SurgR1 步骤
- SurgR1 在目标机器上通过 WSL2 或 Docker 单独运行
- 在 `config.json` 中配置 SurgR1 的远程地址

### Q: 目标机器没有 GPU？

- SurgR1 需要 GPU，没有 GPU 则 SurgR1 服务无法启动
- 后端和 Gemini 不需要 GPU，可以正常使用
- 可以在 `config.json` 中将 SurgR1 设为 disabled

### Q: 打包后体积太大？

- 使用干净的 venv 只装必要依赖
- 在 spec 文件的 `excludes` 中排除不需要的库
- vLLM + CUDA 本身就很大（~2GB+）

### Q: 应用启动后白屏？

- 后端可能还在启动中，等待 30 秒
- 检查是否有防火墙阻止 localhost:8001
- 用 `--enable-logging` 参数启动查看日志
