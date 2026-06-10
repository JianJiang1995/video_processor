# 设计决策：SpecimenBag 由 Gemini 兜底（online + offline 一致）

> 触发：2026-04-15 video01 全量评估发现 YOLO 对 SpecimenBag 的 F1=0
> （108 个真值正例，conf>0.25 下 0 检测）。详见
> `run_log_video01.md` §"Cholec80 真值评估"。

## 决策

**不再依赖 YOLO 检测 SpecimenBag**。改由 Gemini 同时承担：
- **识别**：SpecimenBag 是否存在
- **定位**：输出 bounding box（归一化坐标）

在 **offline 和 online 两个 pipeline 中行为一致**，避免双套逻辑。

## 为什么

| 选项 | 评估 |
|---|---|
| 重训 YOLO 加强 SpecimenBag | 成本高；该类视觉特征（半透明袋 + 内含组织）和其他工具差异大，单靠检测器很难稳定 |
| 调低 conf 阈值 | 会引入大量其他类 FP，得不偿失 |
| **Gemini 兜底** ✅ | Gemini 的 VLM 对"袋子里装了东西"这种语义场景很擅长；output bbox 虽不像 YOLO 精确，但对该类够用 |

## 影响范围

### Offline (`offline_pipeline/`)
- `services/yolo_expert.py`：保留 specimen_bag 类，但下游评估/聚合时**忽略**
- Gemini batch prompt 里加入指令：识别 specimen_bag 并返回 bbox（归一化 0–1）
- `WindowContext.tool_frequencies` 聚合时，specimen_bag 来源切到 Gemini 输出
- 评估脚本 `eval_cholec80.py`：SpecimenBag 行的预测来源标注为 "gemini"

### Online (`video_stream_app/backend/`)
- 实时流目前 YOLO 每帧跑，Gemini 15 秒窗口跑
- SpecimenBag 检测**不需要每帧**（袋子出现后会持续 10+ 秒），15 秒窗口粒度完全够
- Gemini window prompt 里加入与 offline **完全相同的** specimen_bag 指令
- 前端工具 overlay：YOLO 叠 7 类 bbox，Gemini 输出的 specimen_bag bbox 单独叠一层（颜色区分）

### 数据流融合
- `tools` 字段统一 schema：每条 detection 加 `source: "yolo" | "gemini"`
- 下游（embedding / summary / 数据库）按 `source` 字段做去重 / 优先级

## 待办（不在本次改动范围）

- [ ] 写 prompt fragment（offline + online 共用）— "若画面中出现 specimen_bag（标本袋），请输出 `{label, bbox: [x1,y1,x2,y2]}`，坐标 0–1 归一化"
- [ ] `WindowContext.to_prompt_context()` 显式提示"YOLO 已检测的工具：…，请你额外补充 specimen_bag 检测"
- [ ] 实时流的 ControlBar / 工具列表组件区分 source
- [ ] 评估脚本支持读取 Gemini 输出的 specimen_bag 重新跑 Tool macro F1
