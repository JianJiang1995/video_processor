# Qwen3-VL-8B API 服务

基于 vLLM 部署的 Qwen3-VL-8B-Instruct 多模态视觉语言模型 API。

## 快速开始

### 1. 启动服务

```bash
bash /data2/jj/proj/video_processor/qwen_vl_api/start.sh
```

服务将在 `http://localhost:8000` 启动，提供 OpenAI 兼容的 API。

### 2. 测试服务

```bash
python /data2/jj/proj/video_processor/qwen_vl_api/test_api.py
```

### 3. 切换模型

编辑 `video_stream_app/config.json`:

```json
"glm": {
    "model_name": "Qwen3-VL-8B",  // 或 "GLM-4.6V-Flash"
    ...
}
```

## 配置说明

配置文件: `config.yaml`

关键配置项:
- `model.path`: 模型路径
- `gpu.device_ids`: 使用的 GPU（如 "0" 或 "1"）
- `server.port`: API 端口（默认 8000）
- `vllm.gpu_memory_utilization`: GPU 显存利用率
- `vllm.max_model_len`: 最大上下文长度

## API 使用

与 OpenAI API 兼容:

```python
import httpx

response = httpx.post(
    "http://localhost:8000/v1/chat/completions",
    json={
        "model": "Qwen3-VL-8B",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}},
                    {"type": "text", "text": "描述这张图片"}
                ]
            }
        ]
    }
)
```

## 与 GLM 切换

Qwen3-VL 和 GLM-4.6V-Flash 使用相同端口（8000），同一时间只能运行一个。

切换步骤：
1. 停止当前运行的模型服务
2. 修改 `video_stream_app/config.json` 中的 `model_name`
3. 启动对应的服务脚本

```bash
# 使用 Qwen3-VL
bash qwen_vl_api/start.sh

# 使用 GLM-4.6V-Flash  
bash glm_api/start.sh
```



