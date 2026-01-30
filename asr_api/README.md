# FunASR 中文实时语音识别 API

基于阿里达摩院 [FunASR](https://github.com/modelscope/FunASR) 的中文实时语音识别服务。

## 特性

- 🚀 **实时流式识别**: 基于 Paraformer-streaming 模型，低延迟实时转写
- 📁 **离线文件转写**: 支持长音频/视频文件的高精度转写
- 🎯 **VAD 语音端点检测**: 自动检测语音开始和结束
- ✍️ **标点恢复**: 自动为识别结果添加标点符号
- 🔥 **热词支持**: 可自定义热词提升特定词语识别率
- 🎤 **关键词唤醒**: 支持自定义唤醒词，实现语音助手功能
- ⚡ **GPU 加速**: 支持 CUDA 加速推理
- 📋 **统一JSON格式**: 所有API统一使用JSON格式输入输出

## 模型

| 模型 | 用途 | 来源 |
|------|------|------|
| paraformer-zh-streaming | 实时流式语音识别 | ModelScope |
| paraformer-zh | 离线高精度语音识别 | ModelScope |
| fsmn-vad | 语音端点检测 | ModelScope |
| ct-punc | 标点恢复 | ModelScope |
| speech_charctc_kws | 关键词唤醒 | ModelScope |

## 快速开始

### 启动服务

```bash
# 使用启动脚本
./start_asr_server.sh

# 或直接运行
conda activate asr
python server.py
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| ASR_DEVICE | cuda:0 | 计算设备 |
| ASR_HOST | 0.0.0.0 | 监听地址 |
| ASR_PORT | 8765 | 监听端口 |

## API 文档

启动服务后访问: http://localhost:8765/docs

### API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 服务信息 |
| `/health` | GET | 健康检查 |
| `/api/transcribe` | POST | JSON格式语音转写 |
| `/api/transcribe/file` | POST | 文件上传转写 |
| `/api/keyword/detect` | POST | 关键词检测 |
| `/api/keyword/config` | GET/POST | 关键词配置 |
| `/ws/stream` | WebSocket | 实时流式识别 |
| `/ws/stream/kws` | WebSocket | 带唤醒词的流式识别 |

## JSON 格式说明

### 统一响应格式

```json
{
    "success": true,
    "code": 0,
    "message": "success",
    "data": { ... },
    "timestamp": 1234567890.123
}
```

### HTTP API - JSON 转写

**请求:**
```json
POST /api/transcribe
{
    "audio_data": "Base64编码的音频数据",
    "sample_rate": 16000,
    "format": "pcm",
    "hotword": "可选热词"
}
```

**响应:**
```json
{
    "success": true,
    "code": 0,
    "message": "success",
    "data": {
        "text": "识别的文本内容",
        "segments": [[880, 1120], [1120, 1360]],
        "duration": 5.5,
        "processing_time": 1.2,
        "keyword_detected": {
            "detected": true,
            "keyword": "你好小助",
            "confidence": 0.95
        }
    }
}
```

### 关键词配置

**获取配置:**
```bash
curl http://localhost:8765/api/keyword/config
```

**设置配置:**
```json
POST /api/keyword/config
{
    "keywords": ["你好小助", "开始识别", "语音助手"],
    "threshold": 0.6
}
```

### WebSocket - 流式识别

**连接:** `ws://localhost:8765/ws/stream`

**客户端发送:**
```json
// 开始会话
{"action": "start", "config": {"hotword": ""}}

// 发送音频
{"action": "audio", "audio_data": "Base64编码的PCM数据"}

// 结束会话
{"action": "stop"}
```

**服务端响应:**
```json
// 识别结果
{
    "type": "result",
    "data": {
        "text": "识别的文本",
        "is_final": false,
        "keyword_detected": null
    },
    "timestamp": 1234567890.123
}

// 事件通知
{
    "type": "event",
    "data": {"event": "session_completed"},
    "timestamp": 1234567890.123
}
```

### WebSocket - 关键词唤醒模式

**连接:** `ws://localhost:8765/ws/stream/kws`

工作流程:
1. **待机模式**: 持续检测唤醒词
2. **激活模式**: 检测到唤醒词后开始正式识别
3. **超时返回**: 静音超时后自动返回待机模式

**服务端响应:**
```json
// 唤醒词检测
{
    "type": "keyword",
    "data": {
        "detected": true,
        "keyword": "你好小助",
        "confidence": 0.95,
        "match_type": "exact"
    }
}

// 模式切换
{
    "type": "event",
    "data": {
        "event": "mode_changed",
        "mode": "active"
    }
}
```

## Python 客户端示例

### HTTP API

```python
import base64
import requests
import numpy as np
import soundfile as sf

# 读取音频并编码
audio, sr = sf.read("audio.wav", dtype='float32')
audio_int16 = (audio * 32768).astype(np.int16)
audio_base64 = base64.b64encode(audio_int16.tobytes()).decode()

# 发送请求
response = requests.post(
    "http://localhost:8765/api/transcribe",
    json={
        "audio_data": audio_base64,
        "sample_rate": 16000,
        "format": "pcm"
    }
)

result = response.json()
print(result["data"]["text"])
```

### WebSocket 流式识别

```python
import asyncio
import websockets
import json
import base64

async def stream_asr():
    async with websockets.connect("ws://localhost:8765/ws/stream") as ws:
        # 开始会话
        await ws.send(json.dumps({"action": "start", "config": {}}))
        
        # 发送音频块
        audio_chunk = b"..."  # PCM 数据
        await ws.send(json.dumps({
            "action": "audio",
            "audio_data": base64.b64encode(audio_chunk).decode()
        }))
        
        # 接收结果
        response = await ws.recv()
        data = json.loads(response)
        print(data["data"]["text"])
        
        # 结束
        await ws.send(json.dumps({"action": "stop"}))

asyncio.run(stream_asr())
```

### 关键词唤醒模式

```python
async def kws_asr():
    async with websockets.connect("ws://localhost:8765/ws/stream/kws") as ws:
        # 持续发送音频
        while True:
            audio_chunk = record_audio()  # 录音
            await ws.send(json.dumps({
                "action": "audio",
                "audio_data": base64.b64encode(audio_chunk).decode()
            }))
            
            # 检查响应
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=0.1)
                data = json.loads(response)
                
                if data["type"] == "keyword":
                    print(f"检测到唤醒词: {data['data']['keyword']}")
                elif data["type"] == "result":
                    print(f"识别结果: {data['data']['text']}")
            except asyncio.TimeoutError:
                pass
```

## 测试

```bash
# 测试健康状态
python test_client.py --mode health

# 测试关键词配置
python test_client.py --mode keyword

# 测试JSON转写
python test_client.py --audio test.wav --mode json

# 测试WebSocket流式
python test_client.py --audio test.wav --mode ws

# 测试唤醒词模式
python test_client.py --audio test.wav --mode kws

# 测试所有功能
python test_client.py --audio test.wav --mode all
```

## 音频格式要求

- **推荐**: 16kHz, 16bit, 单声道 PCM
- **JSON API**: Base64 编码的 PCM/WAV/MP3 数据
- **WebSocket**: Base64 编码的 16kHz 16bit PCM 数据

## 性能

- 流式识别延迟: ~300-600ms
- 单路 RTF: ~0.1 (GPU)
- 支持多路并发
- 唤醒词检测延迟: ~200ms

## 参考

- [FunASR 官方文档](https://github.com/modelscope/FunASR)
- [Paraformer 论文](https://arxiv.org/abs/2206.08317)
- [ModelScope 模型库](https://modelscope.cn/models)
