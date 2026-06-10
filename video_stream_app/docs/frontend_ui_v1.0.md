# Frontend UI 变更记录

记录前端界面的视觉 / 文案 / 布局调整。仅记录实质性 UI 变更，纯格式调整不纳入。

## v1.0 — 历史窗口分析卡片汉化与字体放大 (2026-04-21, 757638e)

**变更摘要**: 优化主界面底部"历史窗口分析"横向卡片条：去除英文 phase 标签、窗口编号汉字化、整体字号放大，提升可读性并减少每帧渲染的 DOM 节点数（利于降低播放卡顿）。

### 修改内容
- `video_stream_app/frontend/src/App.vue`:
  - `bottom-card-strip` 模板中移除 `<div v-if="s.phase" class="bcs-card-phase">{{ s.phase }}</div>`（即黄色 "Preparation" / "ClippingCutting" 等英文标签）。
  - 窗口编号展示由 `#{{ s.window_id + 1 }}` 改为 `窗口{{ toChineseNumeral(s.window_id + 1) }}`，得到 `窗口一` / `窗口二` / ... / `窗口二十` 等汉字编号。
  - 新增工具函数 `toChineseNumeral(n)`，支持 0–99 的中文数字渲染（超过 99 回退为阿拉伯数字）。
- `video_stream_app/frontend/src/styles/main.css`:
  - `.bottom-card-strip` 高度 200px → 210px，配合字号放大后的两行正文仍可完整展示。
  - `.bcs-card` 宽度 220px → 240px，容纳更长的 `窗口二十` 型标题和更大正文。
  - `.bcs-card-win` 字号 14px → 15px，去掉等宽字体（因不再是 `#1` 型编号），加 0.5px 字间距。
  - `.bcs-card-time` 字号 10.5px → 12px。
  - `.bcs-card-text` 字号 12px → 14px，`line-height` 1.5 → 1.55。
  - 移除 `.bcs-card-phase` 样式规则（模板中已不再使用）。

### 设计决策
- **为什么直接移除 phase 标签而不是汉化**：用户明确要求"移除英文标识"，且右侧 Analysis 面板已经有 `PHASE_CN` 映射的中文章节名，底部横条上的重复标签删除后信息不丢失。同时减少每张卡片约 1 个 DOM 节点 + 1 次样式计算，在窗口数量大时对播放帧率有帮助。
- **为什么用 `toChineseNumeral` 而非简单映射数组**：窗口数量无上限，手写数组容易漏。实现上覆盖 0–99 已足够绝大多数手术场景（>100 个 5s 窗口 = >8 分钟），>99 回退阿拉伯数字以保证不出现渲染空白。
- **为什么卡片宽度 240px 而非保持 220px**：字号放大 + 中文窗口名（`窗口一`比`#1`宽）会挤压右上角的时间码，240px 是在屏幕宽度利用率和单屏卡片数之间的折中。

### 影响范围
- 仅影响主界面底部"历史窗口分析"横向卡片条（`App.vue` 中 `bottom-card-strip`）。
- 不影响：
  - 点击卡片后进入的"窗口一览"网格页（`WindowOverview.vue`，独立组件）。
  - 右侧 Analysis 面板 / Chat 面板（`RightPanel.vue`）。
  - 视频播放、ControlBar、时间轴等其他 UI。
- 不改动后端 / 数据层，`s.phase` 字段仍然由后端正常返回，仅前端不展示。
