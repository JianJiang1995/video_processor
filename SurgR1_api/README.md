# SurgR1 API - Surgical Image Analysis Service

基于 vLLM 和 FastAPI 的手术图像分析服务，提供高效的批量推理能力。

## 功能特性

- **批量图像分析**: 每次可处理一批图片，每张图片自动回答三个问题
- **三类分析任务**:
  1. **工具定位** (Tool Localization): 检测并定位手术器械，返回 bbox 坐标
  2. **手术动作** (Surgical Action): 描述当前手术动作（工具-动作-组织）
  3. **手术阶段** (Surgical Phase): 识别当前手术阶段
- **高效推理**: 使用 vLLM 进行批量推理，显著提升吞吐量
- **RESTful API**: 基于 FastAPI，支持异步请求

## 快速开始

### 1. 环境准备

```bash
# 创建 conda 环境
conda create -n vllm python=3.10
conda activate vllm

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置

编辑 `config.json` 修改模型路径和服务端口:

```json
{
    "model": {
        "path": "/path/to/your/model",
        "gpu_memory_utilization": 0.85
    },
    "server": {
        "port": 9001
    }
}
```

### 3. 启动服务

```bash
./run.sh
```

或直接运行:

```bash
conda activate vllm
python main.py
```

## API 接口

### 健康检查

```bash
curl http://localhost:9001/health
```

### 批量图像分析

```bash
curl -X POST http://localhost:9001/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "image_paths": [
      "/path/to/image1.jpg",
      "/path/to/image2.jpg"
    ]
  }'
```

**请求参数**:
- `image_paths`: 图片路径列表 (必需)
- `questions`: 自定义问题列表 (可选，默认使用三个标准问题)
- `temperature`: 采样温度 (可选)
- `max_tokens`: 最大生成 token 数 (可选)

**响应格式**:

```json
{
  "results": [
    {
      "image_path": "/path/to/image1.jpg",
      "responses": {
        "tool_localization": "<think>...</think><answer>...</answer>",
        "surgical_action": "<think>...</think><answer>...</answer>",
        "surgical_phase": "<think>...</think><answer>...</answer>"
      }
    }
  ],
  "total_images": 2,
  "total_questions": 6
}
```

### 单图分析

```bash
curl -X POST "http://localhost:9001/analyze_single?image_path=/path/to/image.jpg"
```

## 三个标准问题

1. **Tool Localization**:
   > Given the laparoscopic surgical image, locate all the tools in the format of bbox (x1,y1), (x2,y2).

2. **Surgical Action**:
   > Given the laparoscopic surgical image, describe the complete surgical action in terms of tool, action, and tissue.

3. **Surgical Phase**:
   > Given the laparoscopic surgical image, which surgical phase does this frame belong to? Choose from: Preparation, CalotTriangleDissection, ClippingCutting, GallbladderDissection, GallbladderPackaging, CleaningCoagulation, GallbladderRetraction.

## 模型信息

- **模型权重**: `/data/jj/proj/Laparo/last_cot_qwen2.5/round35/v4-20251112-181725/checkpoint-10609-merged`
- **基础模型**: Qwen2.5-VL with Surgical CoT fine-tuning
- **推理引擎**: vLLM

## 目录结构

```
SurgR1_api/
├── main.py           # FastAPI 主程序
├── config.json       # 配置文件
├── requirements.txt  # 依赖列表
├── run.sh           # 启动脚本
└── README.md        # 本文档
```




