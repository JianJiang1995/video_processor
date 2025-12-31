# GLM-4.6V-Flash vLLM API 服务

本目录提供 GLM-4.6V-Flash 多模态大模型的 vLLM 服务部署。

## 目录结构

```
glm_api/
├── config.yaml      # 配置文件
├── config.py        # 配置加载模块
├── server.py        # 服务器启动程序
├── client.py        # Python 客户端
├── start.sh         # 启动脚本
├── stop.sh          # 停止脚本
├── setup_env.sh     # 环境配置脚本
├── test_chat.py     # 聊天测试
├── test_vision.py   # 图像理解测试
└── README.md        # 本文件
```

## 环境要求

- **vLLM >= 0.12.0**
- **transformers >= 5.0.0rc0**
- CUDA 兼容 GPU (建议 >= 24GB 显存)

## 快速开始

### 1. 配置环境（首次使用）

```bash
cd /data2/jj/proj/video_processor/glm_api
bash setup_env.sh
```

### 2. 修改配置

编辑 `config.yaml`：

```yaml
# GPU设备
gpu:
  device_ids: "1"  # 使用GPU 1

# 服务端口
server:
  port: 8000

# 显存利用率
vllm:
  gpu_memory_utilization: 0.9
```

### 3. 启动服务

```bash
# 激活环境
conda activate glm46v

# 启动（自动检查并关闭已有服务）
./start.sh

# 或者后台运行
nohup ./start.sh > logs/server.log 2>&1 &
```

### 4. 停止服务

```bash
./stop.sh
```

## API 使用

### REST API (OpenAI 兼容)

服务地址: `http://localhost:8000/v1`

#### 文本聊天

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "GLM-4.6V-Flash",
    "messages": [{"role": "user", "content": "你好"}],
    "temperature": 0.8,
    "max_tokens": 1000
  }'
```

#### 图像理解

```bash
# 图片转base64
BASE64_IMAGE=$(base64 -w0 /path/to/image.jpg)

curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "GLM-4.6V-Flash",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,'$BASE64_IMAGE'"}},
        {"type": "text", "text": "描述这张图片"}
      ]
    }],
    "max_tokens": 1000
  }'
```

### Python 客户端

```python
import asyncio
from glm_api.client import GLMClient

async def main():
    async with GLMClient() as client:
        # 检查服务
        if not await client.is_available():
            print("服务不可用")
            return
        
        # 文本聊天
        reply = await client.chat("你好，介绍一下你自己")
        print(reply)
        
        # 图像分析
        result = await client.analyze_image(
            "/path/to/image.jpg",
            "这张图片里有什么？"
        )
        print(result)
        
        # 多图分析
        result = await client.analyze_images(
            ["/path/to/img1.jpg", "/path/to/img2.jpg"],
            "比较这两张图片的区别"
        )
        print(result)

asyncio.run(main())
```

### 快捷函数

```python
from glm_api.client import chat, analyze_image
import asyncio

# 快速聊天
result = asyncio.run(chat("你好"))

# 快速分析图片
result = asyncio.run(analyze_image("/path/to/image.jpg", "描述图片"))
```

## 测试

```bash
# 文本聊天测试
python test_chat.py -m "你好，介绍一下你自己"

# 图像理解测试
python test_vision.py -i /path/to/image.jpg -q "这是什么？"
```

## 官方推荐参数

```yaml
temperature: 0.8
top_p: 0.6
top_k: 2
repetition_penalty: 1.1
max_tokens: 4096
```

## 参考

- [ModelScope GLM-4.6V-Flash](https://modelscope.cn/models/ZhipuAI/GLM-4.6V-Flash)
- [GLM-V GitHub](https://github.com/zai-org/GLM-V)
- [vLLM Documentation](https://docs.vllm.ai/)

