# Video Stream Analyzer — SSH 本地端口转发

## v1.0 — Windows / MobaXterm 访问内网前端 (2026-05-27)

**文档目的**：在服务端（例如 `10.10.41.22`）上已跑通 Frontend / Backend / Stream 的前提下，从你本机浏览器通过 `localhost` 访问，避免依赖 MobaXterm X11 跑 Electron。

### 服务端端口（默认值）

| 服务 | 端口 | 转发后在浏览器中的地址示例 |
|------|------|---------------------------|
| Frontend (Vite) | **5134**（或脚本默认 **5133**） | `http://127.0.0.1:5134/` |
| Backend (FastAPI) | **8001** | （通常由前端 dev proxy 间接访问） |
| Stream (MJPEG) | **9001** | `http://127.0.0.1:9001/stream` |

若你前端实际监听 **5133**，把下面命令里的 `5134` 改成 `5133` 即可。

### 在你的 Windows 本机执行（OpenSSH）

在 **PowerShell、cmd、Git Bash 或 MobaXterm 本地终端** 中连接服务器时附带本地转发：

```bash
ssh -N \
  -L 5134:127.0.0.1:5134 \
  -L 8001:127.0.0.1:8001 \
  -L 9001:127.0.0.1:9001 \
  你的用户名@10.10.41.22
```

说明：

- `-N`：不登录 shell，只占着隧道（需要交互登录时可去掉 `-N`）。
- `-L 本地端口:127.0.0.1:远程端口`：把本机端口映射到**服务器上的** loopback。
- `127.0.0.1` 要求服务在服务端监听 localhost 或可经 loopback 访问；若你只绑定了其它网卡，需把中间的 `127.0.0.1` 换成服务器实际监听地址（少见）。

然后在 **Windows 本地浏览器** 打开：

- 页面：`http://127.0.0.1:5134/`
- 实时流输入框：`http://127.0.0.1:9001/stream`

### MobaXterm 图形界面

**Tools → MobaSSHTunnel**（或等价隧道菜单）：

- 类型：Local port forwarding  
- Remote server：`127.0.0.1`，Remote port：`5134` → Local port：`5134`（另建两条：`8001`→`8001`、`9001`→`9001`）  
- SSH 跳板：填 `10.10.41.22` 与你的账号  

### Cursor / VS Code Remote SSH

工作区根目录已有 **`.vscode/settings.json`**：

- **`remote.restoreForwardedPorts`**: `true` —— 本轮连接里你在 **PORTS** 里加过的转发，下次用 Remote SSH 打开同一窗口时通常会恢复。
- **`remote.autoForwardPorts`**: `true` —— 远程上已有进程监听时，编辑器会尝试自动发现并提示转发（不保证每次都命中）。
- **`remote.portsAttributes`** —— 对上述端口打标签，`onAutoForward` 设为 `silent` 减少打断。

**第一次在 Cursor 里加端口：** 底部面板切换到 **PORTS**（或命令面板：`Ports: Focus on Ports View`）→ **Forward a Port**，依次填 **`5134`**（若实际是 5133 则填 5133）、**`8001`**、**`9001`**。本地浏览器用 `http://127.0.0.1:5134/`（或映射后的本地端口）和流地址 `http://127.0.0.1:9001/stream`。

**希望「连上就自动有你本机 localhost 隧道」：** 不靠 Ports 面板，而在 **你自己的电脑**（Windows/Linux）编辑 `~/.ssh/config`，给该主机加上 `LocalForward`（微软 Remote SSH [官方文档](https://code.visualstudio.com/docs/remote/ssh) 推荐的常驻做法），例如：

```sshconfig
Host video-stream-dev
    HostName 10.10.41.22
    User 你的用户名
    LocalForward 127.0.0.1:5134 127.0.0.1:5134
    LocalForward 127.0.0.1:8001 127.0.0.1:8001
    LocalForward 127.0.0.1:9001 127.0.0.1:9001
```

随后在 Cursor / VS Code 里 **Remote-SSH: Connect to Host…** 选择 `video-stream-dev`（或把上述 `LocalForward` 合并进你已存在的 `Host ...` 块）。连接成功后，在本机浏览器即可直接使用 `127.0.0.1` 上的端口。

Windows 用户配置文件路径通常为：`C:\Users\<你>\.ssh\config`。

### 常见问题

**Q: 本机端口已被占用**  
A: 可把本地改成别的端口，例如 `-L 15134:127.0.0.1:5134`，浏览器则用 `http://127.0.0.1:15134/`。流地址同理映射 `9001`。

**Q: 隧道建起后浏览器仍打不开**  
A: 确认 SSH 会话未断；在服务器上执行 `ss -tlnp | grep -E '5134|8001|9001'` 查看服务是否真的在监听。

---

## v1.1 — Cursor 工作区 Ports 配置与 SSH LocalForward (2026-05-27)

**变更摘要**: 在仓库内启用 Remote SSH 友好的端口转发相关工作区配置，并补充 Cursor Ports 初次操作与 SSH `LocalForward` 常驻写法。

### 修改内容
- `.vscode/settings.json`：`remote.autoForwardPorts`、`remote.restoreForwardedPorts`、`remote.portsAttributes`（5133/5134/8001/9001）
- `docs/video_stream_ssh_tunnel_v1.0.md`：`Cursor / VS Code Remote SSH` 章节与本节变更记录

### 设计决策
- Cursor/VS Code 的 Remote SSH 无法仅靠仓库一键预创建三条隧道；最稳的长期方案是 **`~/.ssh/config` 的 `LocalForward`**；工作区 `.vscode/settings.json` 用于恢复 Ports 与会话内自动探测。

### 影响范围
- 仅克隆仓库到本地且不连 Remote SSH 的用户可忽略 `.vscode/settings.json`。
