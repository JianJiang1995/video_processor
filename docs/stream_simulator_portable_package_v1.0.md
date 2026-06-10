## v1.0 (757638e) — stream_simulator 便携打包支持 (2026-03-11)

**变更摘要**: 为 `stream_simulator` 增加跨机器分发能力，去除本机绝对路径依赖，并补充 `conda` 环境与一键打包脚本。

### 修改内容
- `stream_simulator/path_utils.py`: 新增统一视频路径解析逻辑，支持命令行参数、环境变量、相对配置路径和打包目录中的示例视频。
- `stream_simulator/run.py`: 统一入口改为使用便携化视频解析逻辑，并同步默认端口配置。
- `stream_simulator/http_server.py`: 去掉硬编码视频路径，改为运行时自动解析默认视频。
- `stream_simulator/webrtc_server.py`: 去掉硬编码视频路径，改为运行时自动解析默认视频。
- `stream_simulator/rtsp_server.py`: 去掉硬编码视频路径，改为运行时自动解析默认视频。
- `stream_simulator/start_all.sh`: 启动脚本支持从环境变量或 `media/` 目录自动寻找默认视频。
- `stream_simulator/config.json`: 默认视频路径改为相对路径，HTTP/WebRTC 端口调整为便携包默认端口。
- `stream_simulator/environment.yml`: 新增 `conda` 环境定义文件。
- `stream_simulator/install_conda_env.sh`: 新增 `conda` 环境安装/更新脚本。
- `stream_simulator/package_portable.sh`: 新增一键生成便携压缩包脚本，可选携带视频文件。
- `stream_simulator/README.md`: 补充跨机器运行、打包方式和环境安装说明。

### 设计决策
采用“代码包 + 环境描述 + 可选示例视频”的分发方式，而不是直接绑定当前机器路径或导出整个运行环境。这样既能保证在其他机器上可重建运行环境，也避免默认压缩包被本地大视频文件强绑定。

### 影响范围
- `stream_simulator` 的全部启动入口
- 跨机器部署与测试流程
- `conda` 环境创建方式
