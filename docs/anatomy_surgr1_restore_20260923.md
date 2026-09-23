# 组织识别与 SurgR1 后更新恢复记录

日期：2026-09-23。本文是工程验证记录，不是经医生确认的手术报告。

## 1. 回滚点与结论

- 修改前已提交 `b042bbb`：`checkpoint: verified replay import, timeline review and analysis workflow`。
- SurgR1 先前在 `config.json` 中被禁用；所谓本地专家可用，并不等于 SurgR1 在推理。
- 本次恢复真实 SurgR1 推理，并接回“专家初稿 -> SurgR1 逐帧候选 -> 本地 VLM 看图核实 -> 更新同一个窗口”的链路。
- 发现并修复 Triplet 权重与标签编号顺序不一致的问题。这是组织名称错误的独立原因，不是只改提示词能解决的。
- 本次小样本实测证明更新链路工作、部分描述更具体，**不代表所有组织和动作已识别准确**。胆囊本体、胆囊管及背景肝脏仍可能混淆。
- 没有按视频文件名、窗口号或时间点写识别答案。下文窗口号仅用于复现实验。

## 2. Triplet 标签映射修复

当前模型权重：

```text
/home/user/proj/surg_agent/triplet_expert/LAM/weights/LAM/CholecTriplet2021/LAM_Lite/model_best.pth.tar
```

旧配置使用 `datasets/cholect50_triplet/class_mappings.json`，其类别顺序不对应官方预训练权重。例如：

| 输出头 / 编号 | 旧解释 | 官方解释 |
|---|---|---|
| instrument / 0 | bipolar | grasper |
| target / 0 | abdominal_wall_cavity | gallbladder |
| target / 10 | liver | omentum |
| triplet / 17 | bipolar grasp liver | grasper retract gallbladder |

新映射在 `backend/data/cholect50_official_labels.json`，版本为 `cholect50-official-v1`。
已逐项核对 6 个器械、10 个动作、15 个组织和 100 个三元组，而非只核对类别数量。
权重加载改为 `strict=True`，实际加载成功。推理结果保存 `class_id`、`label_schema` 和模型名。

核对资料：[LAM 官方实现](https://github.com/Lycus99/LAM)、[CholecT50 官方数据格式](https://github.com/CAMMA-public/cholect50/blob/master/docs/README-Format.md)、[官方类别关系实现](https://github.com/CAMMA-public/ivtmetrics/blob/main/ivtmetrics/disentangle.py)。此外直接读取官方验证集 `VID68.json` 的类别表完成全量对照。

校验产物：

```text
/home/user/proj/video_processor/video_stream_app/recordings/anatomy_surgr1_20260923/label_map_validation.json
```

## 3. 现在实际运行的模型

| 服务 | 模型 / 用途 | 设备 / 地址 |
|---|---|---|
| SurgR1 | 已安装的手术微调 Qwen2.5-VL 7B，逐帧器械、动作、组织候选 | GPU 0 / `127.0.0.1:9003` |
| 主视觉整合 | Qwen3-VL-8B-Instruct，本地看图整合 | GPU 1 / `127.0.0.1:8010` |
| 本地代理 | VLM 接口、请求适配 | `127.0.0.1:8012` |
| 专家 | 原有 YOLO、夹子检测、Phase、LAM-Lite Triplet | GPU 0，共享剩余显存 |
| 应用后端 | 保存帧、窗口分析、问答、SSE | `127.0.0.1:8001` |

SurgR1 实际权重：

```text
/home/user/proj/Laparo/last_cot_qwen2.5/round36_cholec/v0-20251116-000731/checkpoint-12042-merged
```

这是恢复原有 SurgR1 专家，不是将主视觉模型换回通用 Qwen2.5。
SurgR1 保留训练时的推理格式，不能宣称已关闭其思考；只把完整 `<answer>` 内容交给整合模块，截断的思考文本不能当事实。

本机实测为两张 RTX 4090，每张 24GB。SurgR1 配置 80% 显存预算、4096 上下文、最多 4 个序列。整条测试链路使用本地模型，没有调用外部 GPT。

## 4. 分析代码与数据流

1. `frame_capture_service.py` 独立保存采集帧及真实源时间戳。模型不能推进播放时间。
2. `analysis.py::glm_summarization_task` 按原有 5 秒窗口从保存帧产生专家初稿，先推送界面。
3. `surgr1_window_review.py::review_surgr1_window` 从同一窗口选最多 2 张图，覆盖所选帧的首尾，最长边限制 840。它不另开视频解码器，不裁剪器械区域。
4. 每窗口一次 `/analyze` HTTP 批请求，内含两张图各自的一个动作问题。不要将“一次 HTTP”误说为“只分析一张图”。
5. 问题使用 SurgR1 训练格式：`describe the complete surgical action in terms of tool, action, and tissue`。
6. `local_visual_prompt.py` 向本地 VLM 提供原图、Triplet 三元组候选、独立组织头前 3 个候选及 SurgR1 最终答案。
7. `_patch_open_vlm_window` 更新原 `window_id`，阶段升至 2，通过现有 SSE 通知界面，不额外创建新窗口。
8. 原始候选与调用信息保存在窗口 `others.experts.surgr1`、`others.model_calls.surgr1`，包括源时间戳、耗时、状态及原始返回。

SurgR1 最多并发一个窗口，排队超过 3 秒会记录 `busy`，模型请求超时 30 秒。不可用时不阻塞视频和专家初稿，仍可执行本地 VLM。这里是有界降级，不是保证过载时每窗口都有 SurgR1 结果。
服务启动增加合成空白图预热，预热输出丢弃，不写入数据库；首次内核编译发生在服务对外就绪前。

独立组织头不再因 YOLO 器械冲突被一起丢掉。词义也作了纠正：`blood_vessel` 只能译为“血管”，不能自动升级成“胆囊动脉”；`cystic_pedicle` 不直接等于“胆囊管”。看见背景器官不能证明器械在操作该器官。

## 5. 旧记录如何处理

没有批量覆盖旧视频的已存结果，也没有删除原回放包。
因此导入旧 `analysis.replay.json` 仍会看到旧摘要，这是保留历史数据，不是新模型没有启用。

`window_reanalysis.py` 会在手动重新分析时检查 Triplet schema，旧编号结果从该窗口图像重算，再执行 SurgR1 和本地 VLM。
`mysql_service.py` 保留更新前的摘要及旧专家结果到 review history，新记录保存新专家结果。

用户原回放仍位于：

```text
/home/user/proj/video_processor/video_stream_app/recordings/actual_videos_20260923/09-40-30/analysis.replay.json
```

## 6. 真实图像抽查，不把模型答案当金标

针对三个已有视频保存帧选取 6 个窗口，实际调用 Triplet、SurgR1 和本地 VLM，并查看保存图像。
这里的窗口号为界面的一基编号，脚本 `--case` 参数使用零基编号。

| 会话 / 窗口 | 观察到的改进或问题 |
|---|---|
| `479d3aec` / 51 | 旧为“胆囊区域黄色组织”；新描述为胆囊表面粘连组织及另一器械牵拉胆囊，更具体 |
| `479d3aec` / 25 | 仍有“黄色颗粒状组织”，补充了肝脏和脂肪组织，尚未彻底解决模糊表达 |
| `479d3aec` / 141 | SurgR1 候选是胆囊/肝脏牵拉，本地 VLM 仍提“胆囊管区域”，存在组织混淆风险 |
| `229daa5b` / 20 | SurgR1 返回电凝钩分离胆囊，但整合结果仍偏泛化，不能视为已解决 |
| `229daa5b` / 70 | 实际体外设备场景。SurgR1 错猜抓钳/缝线；本地场景复核保留“镜头移出体外”，没有照抄手术动作 |
| `89226490` / 1 | 体外场景保持正确；SurgR1 的 null 动作不作为手术证据 |

当前提示词的六样本结果：

```text
/home/user/proj/video_processor/video_stream_app/recordings/anatomy_surgr1_20260923/revision2/review.md
/home/user/proj/video_processor/video_stream_app/recordings/anatomy_surgr1_20260923/revision2/results.json
```

每个子目录有实际输入帧和 `result.json`。抽查不会修改原会话数据库。
额外试过一轮独立组织复核调用，结果有受候选诱导、证据索引不合法的问题，还增加延迟，所以**没有接入生产流程**。`revision3` 是该失败实验记录，不能当作已部署效果。

延迟：六样本热态 SurgR1 两图约 3.1-4.2 秒；SurgR1 加本地视觉整合总耗时约 10.5-13.9 秒，未含现场排队。冷态首次请求更慢。专家初稿与最终修订必须分别衡量，不能宣称最终组织核实 5 秒内完成。

## 7. Electron 端到端验证与 EOF 修复

`scripts/verify_surgr1_updates.py` 连接真实 Electron，启动有限实时模拟源，录制整个应用窗口，轮询真实数据库结果，验证：

- 先有初稿，后有同一窗口的 SurgR1 修订。
- 30 秒输入应有 6 个窗口，不多、不漏。
- SurgR1 使用的源时间戳落在对应窗口范围。
- 界面收到全部窗口；最终后台任务正常结束；无页面异常。

测试素材是第一个实际视频的 240-270 秒片段，仅用于快速集成验证。它不是完整长视频录屏，片段在测试会话内从 0 秒计时；生产识别逻辑没有使用这段源偏移。

第一次测试发现 `/api/video/load?paced=true` 在两处保存了不同的源 URI，MySQL 路径丢失模拟源标记，普通文件又在 EOF 自动循环。修复为两处一致的 `simulator://file...`，普通有限文件也在 EOF 结束。分析器继续处理保存的尾部帧，旧式逐帧数据库为空时也能支持 SurgR1 窗口后更新。

严格时间戳断言还发现底层帧查询包含区间终点，会使第 5 秒那一帧同时进入前后两个窗口。本次在窗口就绪判断、尾部补齐和图片加载处统一过滤为 `[start, end)`，不修改回放接口原有范围语义。

最终通过的会话为 `8b6ff7bd`，产物如下：

```text
/home/user/proj/video_processor/video_stream_app/recordings/anatomy_surgr1_20260923/live_ui_aligned/window_updates.mp4
/home/user/proj/video_processor/video_stream_app/recordings/anatomy_surgr1_20260923/live_ui_aligned/verification.json
/home/user/proj/video_processor/video_stream_app/recordings/anatomy_surgr1_20260923/live_ui_aligned/complete.png
```

录屏为整个 Electron 客户区，H.264、2490x1364、15fps、48.334 秒。30 秒素材播完后继续录到异步分析完成。
6/6 窗口的 SurgR1 状态为 `completed`，单窗口 SurgR1 两图耗时 3.270-3.775 秒；所有最终摘要与界面状态完全一致。
SurgR1 取帧时间依次为 `[0, 4.8]`、`[5, 9.8]`、`[10, 14.8]`、`[15, 19.8]`、`[20, 24.8]`、`[25, 29.8]` 秒。
189 项后端回归测试通过。已查看录屏抽样画面和结束截图：视频有变化、最新窗口递增到 6、最终停止于 00:30，未见黑屏或布局覆盖。

以脚本启动时刻近似对齐播放，专家初稿在窗口结束后约 2.6-3.4 秒保存，最终修订约 17.1-20.1 秒保存。这包含整合调用与调度，不能用 SurgR1 单次推理的 3 秒冒充最终延迟。
画面质量的抽查不是播放帧率/撕裂的专项验证。录屏仍能看到窗口 5/6 用“黄色颗粒状组织/物质”，组织命名并未全面达到要求。

此前的 `live_ui`、`live_ui_verified`、`live_ui_final` 是失败的迭代测试记录，不是推荐查看的最终结果；保留供审计。

## 8. 启动、检查与复现

```bash
cd /home/user/proj/video_processor/video_stream_app
bash start_surgical_app.sh
# 仅启动服务
bash start_surgical_app.sh --services-only
# 查看状态
bash start_surgical_app.sh --status
curl http://127.0.0.1:9003/health
curl http://127.0.0.1:8001/api/analysis/surgr1/status
```

启动脚本先为主 VLM 选择不同于 SurgR1 的 GPU，管理各自 PID，不使用旧脚本中会杀掉其他 vLLM 的全局 kill。
日志在 `logs/desktop_launcher/`；服务进程文件在 `.runtime/`。

```bash
../.venv/bin/python -m unittest discover -s backend/tests -q
../.venv/bin/python scripts/audit_window_anatomy.py \
  --case 479d3aec:50 --case 229daa5b:69 \
  --output recordings/anatomy_surgr1_followup
DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority \
  ../.venv/bin/python scripts/verify_surgr1_updates.py \
  --source recordings/anatomy_surgr1_20260923/smoke_input_30s.mp4 \
  --output recordings/anatomy_surgr1_followup_ui
```

## 9. 尚未完成的验收

- 尚未用修改后版本完整重跑三个长视频；本次是六窗口真实推理抽查和短片真实 Electron 集成测试。
- 组织与动作准确率没有医生金标，不能给出可靠百分比，也不能当临床已验证系统。
- 旧记录中已有的组织泛化/误判不会凭启动模型自动修正，需要重新分析。
- 仍需改进胆囊本体与胆囊管区分、器械规范命名、初稿断言强度及最终核实延迟。
- 下一轮应固定跨视频评测集，比较修订前后的器械、动作、接触组织、体外场景和错误具体化率，不能仅凭文案变具体判断准确率提升。
