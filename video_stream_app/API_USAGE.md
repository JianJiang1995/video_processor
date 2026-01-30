# API 使用说明

## 新增 API 端点

### 1. 图片分析 API (`POST /api/analysis/analyze-images`)

独立的图片分析端点，用于分析视频窗口中的帧。

**请求体：**
```json
{
  "session_id": "session_123",
  "start_time": 10.5,
  "analysis_type": "all"  // "all", "phase", "action", "tools"
}
```

**响应：**
```json
{
  "window_id": 2,
  "start_time": 10.0,
  "end_time": 15.0,
  "frame_count": 5,
  "frame_analyses": [
    {
      "frame_idx": 300,
      "timestamp": 10.0,
      "phase": "CalotTriangleDissection",
      "action": "Dissecting tissue plane",
      "tools": "Grasper at gallbladder, Hook dissecting"
    },
    ...
  ],
  "analysis_type": "all"
}
```

### 2. 分析结果整合 API (`POST /api/analysis/integrate-analysis`)

将帧分析结果整合成连贯的摘要，使用 GLM-4.6V-Flash（如果可用，否则回退到 GPT）。

**请求体：**
```json
{
  "session_id": "session_123",
  "start_time": 10.5,
  "use_glm": true  // 使用 GLM-4.6V-Flash，false 则使用 GPT
}
```

**响应：**
```json
{
  "window_id": 2,
  "start_time": 10.0,
  "end_time": 15.0,
  "frame_count": 5,
  "frame_analyses": [...],
  "summary": "This segment shows the Calot Triangle Dissection phase...",
  "summary_id": 42,
  "model": "GLM-4.6V-Flash"  // 或 "GPT"
}
```

### 3. GLM 服务状态检查 (`GET /api/analysis/glm/status`)

检查 GLM-4.6V-Flash 服务是否可用。

**响应：**
```json
{
  "available": true,
  "api_url": "http://localhost:8000/v1",
  "model_name": "GLM-4.6V-Flash"
}
```

## 使用流程

### 方式一：分步调用（推荐）

1. **先分析图片**：
```bash
curl -X POST "http://localhost:8001/api/analysis/analyze-images" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "session_123",
    "start_time": 10.5,
    "analysis_type": "all"
  }'
```

2. **再整合结果**：
```bash
curl -X POST "http://localhost:8001/api/analysis/integrate-analysis" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "session_123",
    "start_time": 10.5,
    "use_glm": true
  }'
```

### 方式二：一步到位

使用现有的 `analyze-window-vlm` 端点（已更新为使用 GLM）：
```bash
curl -X POST "http://localhost:8001/api/analysis/analyze-window-vlm" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "session_123",
    "start_time": 10.5
  }'
```

## GLM-4.6V-Flash 配置

在 `config.json` 中配置 GLM 服务：

```json
{
  "services": {
    "glm": {
      "port": 8000,
      "host": "0.0.0.0",
      "api_url": "http://localhost:8000/v1",
      "model_name": "GLM-4.6V-Flash",
      "temperature": 0.7,
      "max_tokens": 1000
    }
  },
  "glm_model": {
    "path": "/data2/ckpt/GLM-4.6V-Flash"
  }
}
```

## 启动 GLM-4.6V-Flash vLLM Server

使用 vLLM 启动 GLM-4.6V-Flash 服务：

```bash
# 示例启动命令（根据实际情况调整）
python -m vllm.entrypoints.openai.api_server \
  --model /data2/ckpt/GLM-4.6V-Flash \
  --port 8000 \
  --host 0.0.0.0 \
  --trust-remote-code
```

确保服务运行在 `http://localhost:8000/v1`（或配置的地址）。

## 注意事项

1. **GLM 服务不可用时的回退**：如果 GLM-4.6V-Flash 服务不可用，系统会自动回退到 GPT（如果配置了 OpenAI API Key）。

2. **数据库存储**：分析结果会自动保存到数据库，后续调用 `integrate-analysis` 时会优先使用已存储的分析结果。

3. **性能优化**：`analyze-images` 和 `integrate-analysis` 可以分开调用，便于：
   - 批量分析多个窗口
   - 后续单独整合结果
   - 缓存分析结果

