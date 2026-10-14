# 视频理解应用：一键启动与模型清单

更新时间：2026-10-14
项目目录：`/home/user/proj/video_processor/video_stream_app`

## 1. 桌面一键启动

Ubuntu 桌面已有快捷方式：

```text
/home/user/Desktop/SurgR1视频理解.desktop
```

双击后会按顺序启动并检查：

1. MySQL 数据库；
2. 本地 Qwen3-VL vLLM 服务（8010）；
3. 本地 VLM 请求代理（8012）；
4. SurgR1 异步窗口复核服务（9003）；
5. FastAPI 后端（8001），并预加载 Phase、Triplet、器械 YOLO、夹子 YOLO；
6. Electron 前端。

脚本会等待模型加载成功后再打开 Electron。重复双击不会再启动一套重复进程。关闭
Electron 后，默认只停止本次快捷方式启动的服务；原本已经运行的服务会被复用，不会被误杀。

首次加载 17 GB 的 VLM 权重通常需要一段时间，启动终端会持续显示状态。日志目录：

```text
/home/user/proj/video_processor/video_stream_app/logs/desktop_launcher
```

命令行等价入口：

```bash
cd /home/user/proj/video_processor/video_stream_app
./start_surgical_app.sh
```

状态与停止命令：

```bash
./start_surgical_app.sh --status
./start_surgical_app.sh --stop
```

## 2. 当前生产链路及模型

| 服务/模块 | 端口 | 当前模型 | 主要用途 | 权重或实现位置 |
|---|---:|---|---|---|
| 本地视觉语言服务 | 8010 | **Qwen3-VL-8B-Instruct**，vLLM 0.22 | 每 5 秒窗口的多帧视觉判断、夹子/剪刀/出血/CVS/起雾/体外/装袋复核、智能问答、翻译、关键事件与临床总结 | `models/local_vlm/qwen3-vl-8b-instruct/` |
| VLM 请求代理 | 8012 | 无独立模型，转发到 8010 | 给通用分析和各专项复核提供统一 OpenAI-compatible 接口 | `scripts/local_vlm_balancer.py` |
| SurgR1 窗口复核 | 9003 | SurgR1，手术微调的 Qwen2.5-VL | 异步复核器械、动作和组织，不阻塞专家初稿 | `scripts/serve_surgr1_local.py` 加载相邻 `../SurgR1_api/main.py`；该依赖源码现备份于 `deployment/runtime_sources/` |
| 手术阶段专家 | 后端内置 | **ResNet-18 Phase Expert**，7 类 | 识别准备、Calot 三角分离、夹闭切断、胆囊分离、装袋、清理凝血、牵拉等阶段 | `/home/user/proj/surg_agent/phase_expert/runs/cholec_phase/best.pt` |
| 手术三元组专家 | 后端内置 | **LAM-Lite / ResNet-18，CholecTriplet2021** | 输出器械-动作-目标三元组；映射含 6 类器械、10 类动作、15 类目标、100 类三元组 | `/home/user/proj/surg_agent/triplet_expert/LAM/weights/LAM/CholecTriplet2021/LAM_Lite/model_best.pth.tar` |
| 通用器械检测 | 后端内置 | **YOLO26s**，8 类 | 检测 bipolar、clipper、grasper、hook、irrigator、scissors、snare、specimen bag | `/home/user/proj/surg_agent/detection_expert/runs/yolo26s_cholec_tool/weights/best.pt` |
| 夹子检测 | 后端内置 | **YOLO corrected-v2**，2 类 | 检测 Hem-o-lok 和 titanium clip 候选；产品文案统一显示为“夹子”，再由 VLM 复核 | `models/clip_detector/yolo_clip_corrected_v2/weights/best.pt` |
| 数据与历史记录 | 3306 | MySQL `video_analyzer` | 保存会话、5 秒窗口分析、智能问答与压缩上下文 | `config.json` 的 `database.mysql` |
| 桌面界面 | 5133（开发模式） | Electron 36 + Vue 3 | 本地硬件加速播放、采集卡接入、分析与报告展示 | `frontend/` |

## 3. GPU 分配

当前机器检测到两张 RTX 4090。启动器不再使用旧文档里不存在的 `GPU 2`，而是每次启动时
自动分配（2026-10-14 更新）：

- 全部模型冷启动时，优先将 Qwen3-VL-8B 放在没有显示输出的卡上；部分服务已运行时结合空闲显存选择，不搬迁运行中的模型；
- SurgR1 和专家共享另一张卡；本机复测为 VLM=GPU1，SurgR1/专家=GPU0，显示也在 GPU0；
- Electron 保持 Chromium GPU 加速。

可在启动前通过 `VLM_GPU` 和 `EXPERT_GPU` 覆盖自动选择，例如：

```bash
VLM_GPU=1 EXPERT_GPU=0 ./start_surgical_app.sh
```

## 4. 当前未进入生产链路的服务

| 服务 | 当前状态 | 说明 |
|---|---|---|
| Gemini/OpenAI | 桌面启动时禁用 | 启动器设置 `DISABLE_EXTERNAL_AI=1`，手术图像不发送到外部服务。 |
| Gemini Embedding | 已停用 | `services.embedding.enabled=false`，避免摘要外发。 |
| SAM3（9004） | 可选、当前未安装服务端 | 仓库只有客户端；当前主分析不依赖它。 |
| FunASR（8765） | 可选、当前未安装服务端 | 文字智能问答可用，语音识别需后续安装 FunASR 服务端和模型。 |
| CosyVoice（50000） | 可选、当前未安装服务端 | 当前没有本地 TTS 服务端与权重，不影响文字分析和问答。 |

因此，“一键启动所有服务”指当前可运行且生产流程必需的完整本地链路。可选语音和分割服务
不会被显示成已启动；安装对应服务端后再纳入同一启动器。

## 5. GitHub 源码与完整部署的区别

主应用代码、启动脚本、配置、前端依赖锁文件、后端依赖列表及测试均已纳入版本管理。
2026-10-14 检查发现两处生产代码依赖原本在仓库外，现将当前使用版本逐字备份到
`deployment/runtime_sources/`，没有切换本机运行路径或改变推理行为：

| 归档文件 | 当前启动代码期望的位置 |
|---|---|
| `SurgR1_api/main.py`、`SurgR1_api/config.json` | 本仓库上级的 `SurgR1_api/` |
| `surg_agent/triplet_expert/eval_lam_on_our_val.py` | `$SURG_AGENT_ROOT/triplet_expert/`；默认 `/home/user/proj/surg_agent/triplet_expert/` |

文件来源、SHA-256 和本机主要 Python 包版本见 `deployment/runtime_sources/manifest.json`。
Triplet 服务直接导入该评估文件中的 `LAMLite` 类，因此它的 sklearn 等顶层依赖也需安装。
归档保留原文件中的归属说明，不包含权重或数据集。版本清单是本机环境证据，不是跨 CUDA
版本通用的 pip 锁文件，不能保证只安装 `requirements.txt` 就能运行所有本地模型。

新机器采用当前目录布局时，可从仓库根目录恢复这些小型依赖源码（不覆盖已有文件）：

```bash
mkdir -p ../SurgR1_api /home/user/proj/surg_agent/triplet_expert
cp -an deployment/runtime_sources/SurgR1_api/. ../SurgR1_api/
cp -an deployment/runtime_sources/surg_agent/triplet_expert/. /home/user/proj/surg_agent/triplet_expert/
```

仍需单独准备，不能宣称仅克隆仓库即可完整运行：

- Qwen3-VL、SurgR1、YOLO、夹子、Phase、Triplet 的权重及配套模型配置；权重位置见上表和 `config.json`，SurgR1 默认路径还需核对归档的 API 配置。
- 项目上级 `.venv/` 对应的 Python/CUDA 环境；前端运行 `npm ci` 安装锁定依赖。
- NVIDIA、Blackmagic Desktop Video、GStreamer DeckLink 插件、FFmpeg、MySQL 等系统组件。
- 本机 `.env`、数据库账户及需保留的分析数据库、会话图片、报告和原片。这些属于受控本地数据，不随本次 Git 推送上传。

`master` 分支以应用目录为仓库根；远程旧 `main` 分支是较早的整个工程目录结构。不要混用
两个分支的相对路径，也不要为部署方便把患者视频、数据库或私钥加入 Git。
