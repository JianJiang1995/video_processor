# 手术阶段顺序约束修复记录

> 创建日期: 2026-01-07
> 相关文件: `backend/services/glm_client.py`, `backend/services/glm_prompts.json`

## 问题描述

### 现象
在视频流分析过程中，GLM 输出的手术阶段顺序违反了腹腔镜胆囊切除术的逻辑顺序：
- 窗口4 (00:45-01:00): 【胆囊取出阶段】
- 窗口5 (01:00-01:15): 【准备阶段】 ← **错误！不应回退**
- 窗口6 (01:15-01:30): 【胆囊分离阶段】

### 根本原因
1. **历史上下文未正确传递**: GLM 在处理每个窗口时没有获取到之前窗口的阶段信息
2. **R1 模型误判**: SurgR1 模型在某些帧上输出了错误的阶段（如在手术后期输出 `Preparation`）
3. **缺乏后处理验证**: 即使 prompt 中有约束，GLM 仍可能输出违规阶段，但系统没有验证和纠正机制

## 阶段顺序约束规则

腹腔镜胆囊切除术的阶段顺序约束：

| 规则 | 描述 | 说明 |
|-----|------|-----|
| **规则1** | 准备阶段不可回退 | 一旦进入任何非准备阶段，永远不能再回到准备阶段 |
| **规则2** | 胆囊取出后不能出现胆囊牵拉 | 胆囊取出是手术后期阶段，此时胆囊已分离完毕 |
| **规则3** | 清洁凝血可在取出后出现 | 这是允许的，用于取出后的止血和清理 |

### 阶段顺序参考

```
准备阶段 → 肝胆三角解剖 → 夹闭切断 → 胆囊分离 → 胆囊牵拉 → 清洁凝血 → 胆囊取出
                                        ↑
                                 (可能穿插于分离过程中)
```

## 代码修改

### 1. `backend/services/glm_prompts.json`

在 `system_prompt` 中添加了阶段顺序约束规则：

```json
"system_prompt": "...\n\n## ⚠️ 手术阶段顺序约束（必须严格遵守！）\n\n**重要：你必须根据历史窗口分析来判断当前阶段，严禁违反以下规则：**\n\n### 规则1：准备阶段不可回退\n- 一旦手术从「准备阶段」进入任何其他阶段，就**永远不能**再回到「准备阶段」\n\n### 规则2：胆囊取出后不能出现胆囊牵拉\n- 一旦进入「胆囊取出阶段」，就**不能**再出现「胆囊牵拉阶段」\n\n### 规则3：清洁凝血可在取出后出现（允许）\n- 「清洁凝血阶段」可以出现在「胆囊取出阶段」之后\n\n..."
```

### 2. `backend/services/glm_client.py`

#### 2.1 增强调试日志

在 `summarize_windows_concurrent` 函数中添加详细日志：

```python
# 获取当前历史记录数量
current_history = await history_manager.get_history()
logger.info(f"[GLMClient] Processing window {window_id} with {frame_count} frames, history_count={len(current_history)}")

# 显示历史中最后一个阶段
if history_context:
    last_phase = current_history[-1].dominant_phase if current_history else "N/A"
    logger.info(f"[GLMClient] Window {window_id} history: {len(current_history)} windows, last_phase={last_phase}")
else:
    logger.info(f"[GLMClient] Window {window_id} has NO history context (first window or empty)")
```

#### 2.2 在 `integrate_analysis_results` 中添加日志

```python
if history_context:
    logger.info(f"[GLMClient] integrate_analysis_results: history_context length = {len(history_context)} chars")
    # ... 添加约束提醒 ...
    if phase_constraints:
        logger.info(f"[GLMClient] Added phase constraints: {phase_constraints}")
else:
    logger.info("[GLMClient] integrate_analysis_results: NO history_context provided")
```

#### 2.3 阶段约束后处理机制

在 GLM 输出后验证并强制纠正违规阶段：

```python
# ========== 阶段约束后处理：检查并纠正违规阶段 ==========
if dominant_phase and current_history:
    last_phase = current_history[-1].dominant_phase if current_history else None
    history_phases = [h.dominant_phase for h in current_history]
    
    # 规则1：准备阶段不可回退
    if dominant_phase == "Preparation" and any(p != "Preparation" for p in history_phases):
        logger.warning(f"[GLMClient] Window {window_id} PHASE VIOLATION: 准备阶段不可回退！")
        dominant_phase = last_phase
        summary = summary.replace("【准备阶段】", f"【{WindowHistoryManager.PHASE_CN_NAMES.get(last_phase, last_phase)}】")
    
    # 规则2：胆囊取出后不能出现胆囊牵拉
    if dominant_phase == "GallbladderRetraction" and "GallbladderPackaging" in history_phases:
        logger.warning(f"[GLMClient] Window {window_id} PHASE VIOLATION: 胆囊取出后不能出现胆囊牵拉！")
        dominant_phase = last_phase if last_phase else "CleaningCoagulation"
        summary = summary.replace("【胆囊牵拉阶段】", f"【...】")
```

## 测试验证

### 测试步骤

1. **启动后端服务**
   ```bash
   cd /data2/jj/proj/video_processor/video_stream_app
   bash run_backend.sh
   ```

2. **启动流模拟器**（如需要）
   ```bash
   cd /data2/jj/proj/video_processor/stream_simulator
   ./start_all.sh
   ```

3. **连接视频流并观察日志**

### 验证要点

1. **检查日志中的历史上下文信息**
   ```
   [GLMClient] Processing window X with Y frames, history_count=Z
   [GLMClient] Window X history: Z windows, last_phase=...
   ```

2. **检查是否有阶段违规被纠正**
   ```
   [GLMClient] Window X PHASE VIOLATION: 准备阶段不可回退！
   [GLMClient] Corrected to phase: ...
   ```

3. **验证前端显示的阶段顺序是否合理**
   - 准备阶段应该只出现在视频开始
   - 胆囊取出后不应出现胆囊牵拉

### 日志文件位置

- 分析日志: `/data2/jj/proj/video_processor/video_stream_app/logs/analysis/`
- API 日志: `/data2/jj/proj/video_processor/video_stream_app/logs/api_*.log`

## 已知问题与后续改进

### 1. 历史上下文在后端重启后丢失

**问题**: `_session_history_managers` 字典存储在内存中，后端重启后会丢失所有历史

**可能的改进**:
- 将历史上下文持久化到 Redis 或数据库
- 在会话开始时从数据库加载已有的窗口摘要

### 2. R1 模型阶段识别不准确

**问题**: SurgR1 模型在某些帧上可能输出明显错误的阶段

**可能的改进**:
- 在 R1 分析层面增加阶段过滤逻辑
- 使用多帧投票机制确定主导阶段

### 3. 循环视频导致的阶段跳跃

**问题**: 如果视频模拟器使用循环播放，视频从后期跳回前期时会触发阶段违规

**可能的改进**:
- 在检测到视频循环时重置历史上下文
- 添加视频时间戳不连续检测

## 相关代码文件

| 文件 | 说明 |
|-----|------|
| `backend/services/glm_client.py` | GLM 客户端，包含历史管理和阶段约束 |
| `backend/services/glm_prompts.json` | GLM 提示词配置 |
| `backend/routers/analysis.py` | 分析路由，调用 `summarize_windows_concurrent` |
| `backend/services/analysis_logger.py` | 分析日志记录 |

## 调试命令

```bash
# 查看最新的分析日志
tail -100 /data2/jj/proj/video_processor/video_stream_app/logs/analysis/*.log | grep -E "\[GLM\]|VIOLATION|history"

# 查看 API 日志中的 GLMClient 信息
grep -i "GLMClient" /data2/jj/proj/video_processor/video_stream_app/logs/api_*.log | tail -50

# 检查后端进程
ps aux | grep "python main.py" | grep -v grep
```


