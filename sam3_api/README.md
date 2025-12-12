# SAM3 FastAPI 分割服务

独立的 SAM3 图像分割 API 服务，通过 HTTP 接口提供 bounding box 引导的图像分割功能。

## 功能特点

- 🎯 基于 bounding box 的精确分割
- 🖼️ 输出带 mask 可视化的结果图片
- 🎨 支持自定义透明度、边缘粗细、颜色
- 📦 支持 base64 编码返回（远程机器可直接获取图片）
- ⚙️ 可配置的默认参数 (config.yaml)
- 🚀 独立部署，不依赖外部模块
- 📡 RESTful API 设计

## 快速开始

### 1. 环境准备

确保已安装以下依赖：

```bash
pip install -r requirements.txt
```

### 2. 配置（可选）

编辑 `src/config.yaml` 自定义默认设置：

```yaml
# 默认可视化参数
visualization:
  alpha: 0.4                    # mask 透明度 (0.0-1.0)
  contour_thickness: 2          # 边缘粗细
  return_base64: false          # 是否默认返回 base64

# 手术工具颜色配置 (BGR格式)
tool_colors:
  grasper:      [0, 255, 127]     # 春绿色
  hook:         [0, 165, 255]     # 橙色
  # ... 更多配置
```

或使用环境变量覆盖：

```bash
export SAM3_CHECKPOINT=/path/to/sam3.pt
export HOST=0.0.0.0
export PORT=8000
```

### 3. 启动服务

```bash
# 方式1：使用启动脚本
chmod +x start.sh
./start.sh

# 方式2：直接运行
cd src && python main.py
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/config` | GET | 获取当前配置 |
| `/sam3` | POST | 图像分割 |
| `/download/{filename}` | GET | 下载结果图片 |

## 图像分割 API

### 基本请求

```bash
curl -X POST http://127.0.0.1:8000/sam3 \
     -H "Content-Type: application/json" \
     -d '{
           "image_input_path": "/path/to/your/image.png",
           "bboxes": [
               {"x1": 953, "y1": 36, "x2": 1590, "y2": 204, "label": "forceps"}
           ]
         }'
```

### 请求参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `image_input_path` | string | ✅ | - | 输入图片的绝对路径 |
| `bboxes` | array | ✅ | - | bounding box 列表 |
| `output_dir` | string | ❌ | 图片同目录 | 输出目录 |
| `alpha` | float | ❌ | 配置值 | mask 透明度 (0.0-1.0) |
| `contour_thickness` | int | ❌ | 配置值 | mask 边缘粗细 (0-10) |
| `colors` | array | ❌ | 自动配色 | 自定义颜色配置 |
| `return_base64` | bool | ❌ | 配置值 | 是否返回 base64 编码图片 |

### BBox 格式

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `x1` | int | ✅ | 左上角 x 坐标 |
| `y1` | int | ✅ | 左上角 y 坐标 |
| `x2` | int | ✅ | 右下角 x 坐标 |
| `y2` | int | ✅ | 右下角 y 坐标 |
| `label` | string | ❌ | 物体标签（默认 "object"） |

### 响应格式

```json
{
    "success": true,
    "output_path": "/path/to/image_masked.png",
    "num_objects": 1,
    "masks": [
        {
            "obj_id": 1,
            "label": "forceps",
            "area": 12345
        }
    ],
    "message": "分割成功",
    "image_base64": "iVBORw0KGgo...",  // 仅当 return_base64=true 时返回
    "image_format": "png"               // 仅当 return_base64=true 时返回
}
```

## 远程机器获取图片

### 方式 1: Base64 编码（推荐）

直接在响应中获取图片，无需额外请求：

```bash
curl -X POST http://服务器IP:8000/sam3 \
     -H "Content-Type: application/json" \
     -d '{
           "image_input_path": "/path/to/image.png",
           "bboxes": [{"x1": 100, "y1": 50, "x2": 300, "y2": 200, "label": "grasper"}],
           "return_base64": true
         }'
```

Python 解码示例：

```python
import base64
import json
import requests

# 发送请求
response = requests.post("http://服务器IP:8000/sam3", json={
    "image_input_path": "/path/to/image.png",
    "bboxes": [{"x1": 100, "y1": 50, "x2": 300, "y2": 200, "label": "grasper"}],
    "return_base64": True
})

result = response.json()

# 解码并保存图片
if result.get("image_base64"):
    image_data = base64.b64decode(result["image_base64"])
    with open("result.png", "wb") as f:
        f.write(image_data)
    print(f"图片已保存，大小: {len(image_data)} bytes")
```

### 方式 2: 下载 API

如果图片保存在服务器的 `output` 目录中：

```bash
# 下载结果图片
curl -O http://服务器IP:8000/download/image_masked.png
```

## 完整示例

### 多物体分割 + 自定义样式 + Base64 返回

```bash
curl -X POST http://127.0.0.1:8000/sam3 \
     -H "Content-Type: application/json" \
     -d '{
           "image_input_path": "/data/images/surgery.png",
           "bboxes": [
               {"x1": 100, "y1": 50, "x2": 300, "y2": 200, "label": "forceps"},
               {"x1": 400, "y1": 100, "x2": 600, "y2": 300, "label": "scissors"}
           ],
           "alpha": 0.4,
           "contour_thickness": 2,
           "colors": [
               {"label": "forceps", "color": [50, 205, 50]},
               {"label": "scissors", "color": [255, 255, 0]}
           ],
           "return_base64": true
         }'
```

## 内置工具颜色

如果不指定 `colors`，服务会为常见手术工具自动配色（可在 config.yaml 中修改）：

| 工具 | 颜色 (BGR) | 颜色名称 |
|------|------------|----------|
| grasper | (0, 255, 127) | 春绿色 |
| bipolar | (255, 0, 255) | 品红色 |
| hook | (0, 165, 255) | 橙色 |
| scissors | (255, 255, 0) | 青色 |
| clipper | (147, 20, 255) | 粉红色 |
| irrigator | (255, 191, 0) | 深天蓝色 |
| specimenbag | (0, 255, 255) | 黄色 |
| forceps | (50, 205, 50) | 酸橙绿 |
| needle | (180, 105, 255) | 热粉色 |
| suction | (250, 206, 135) | 天蓝色 |

## 文件结构

```
sam3_api/
├── ckpt/                  # 模型权重目录
│   └── sam3.pt
├── src/
│   ├── assets/            # 资源文件
│   │   └── bpe_simple_vocab_16e6.txt.gz
│   ├── sam3/              # SAM3 核心模型包
│   ├── config.yaml        # 配置文件
│   ├── config_loader.py   # 配置加载器
│   ├── main.py            # FastAPI 主程序
│   ├── sam3_model.py      # SAM3 模型封装
│   └── ...
├── output/                # 结果图片输出目录（供下载API使用）
├── start.sh               # 启动脚本
├── requirements.txt       # Python 依赖
└── README.md              # 说明文档
```

## 注意事项

1. **GPU 内存**: SAM3 模型需要较大的 GPU 内存，建议使用 16GB 以上显存的 GPU
2. **首次加载**: 首次请求时模型会自动加载，可能需要 30-60 秒
3. **文件路径**: 请确保输入图片路径是服务器可访问的绝对路径
4. **输出位置**: 默认输出文件保存在输入图片同目录，文件名添加 `_masked` 后缀
5. **Base64 大小**: Base64 编码会使数据增大约 33%，大图片请考虑带宽

## 错误处理

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误（如图片不存在、bboxes 为空） |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |

## API 文档

启动服务后，访问以下地址查看交互式 API 文档：

- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
