# 手术视频智能分析系统

## 功能演示与技术方案汇报

---

# 目录

1. 项目概述
2. 系统架构
3. 核心功能模块
4. 技术实现方案
5. 数据流设计
6. 接口规范
7. 部署方案
8. 总结与展望

---

# 1. 项目概述

## 1.1 项目背景

- 手术过程需要实时监控与智能辅助
- 术中操作记录与分析需求日益增长
- 语音交互可解放术者双手

## 1.2 项目目标

- 实现手术视频的实时智能分析
- 提供多维度手术信息识别（阶段、动作、器械）
- 支持语音交互查询手术历史
- 生成结构化手术记录

---

# 2. 系统架构

## 2.1 整体架构图

```
+------------------+     +------------------+     +------------------+
|                  |     |                  |     |                  |
|    前端应用      | <-> |    后端服务      | <-> |   AI模型服务     |
|   (Vue.js)       |     |   (FastAPI)      |     |   (vLLM)         |
|                  |     |                  |     |                  |
+------------------+     +------------------+     +------------------+
        |                        |                        |
        v                        v                        v
+------------------+     +------------------+     +------------------+
|  视频播放/控制   |     |   业务逻辑处理   |     |  SurgR1 图像分析 |
|  语音交互面板    |     |   数据持久化     |     |  GLM 文本总结    |
|  分析结果展示    |     |   API路由管理    |     |  SAM3 图像分割   |
+------------------+     +------------------+     +------------------+
```

## 2.2 分层设计

| 层次 | 组件 | 职责 |
|------|------|------|
| 表现层 | Vue.js前端 | 用户界面、交互逻辑 |
| 接口层 | FastAPI Routers | API路由、参数校验 |
| 业务层 | Services | 业务逻辑、模型调用 |
| 数据层 | MySQL/SQLite | 数据持久化存储 |
| 模型层 | vLLM服务 | AI模型推理 |

---

# 3. 核心功能模块

## 3.1 手术图像分析 (SurgR1)

### 功能描述
- 输入：手术视频帧图像
- 输出：三维度分析结果

### 分析维度

| 维度 | 说明 | 输出格式 |
|------|------|----------|
| 工具定位 | 识别手术器械位置 | Bounding Box坐标 |
| 动作描述 | 描述当前手术操作 | 自然语言文本 |
| 阶段识别 | 判断手术所处阶段 | 阶段标签 |

### 支持的手术阶段
- Preparation（准备）
- CalotTriangleDissection（Calot三角解剖）
- ClippingCutting（夹闭切断）
- GallbladderDissection（胆囊分离）
- GallbladderPackaging（胆囊装袋）
- CleaningCoagulation（清洁止血）
- GallbladderRetraction（胆囊牵拉）

---

## 3.2 多帧分析整合 (GLM-4.6V-Flash)

### 功能描述
- 整合5秒时间窗口内的多帧分析结果
- 生成连贯的手术过程总结

### 处理流程

```
帧1分析结果 ─┐
帧2分析结果 ─┼─> GLM-4.6V-Flash ─> 窗口总结文本
帧3分析结果 ─┤
帧4分析结果 ─┤
帧5分析结果 ─┘
```

### 优化措施
- 禁用思考模式（Thinking Mode）加速响应
- 批量处理减少API调用次数

---

## 3.3 图像分割 (SAM3)

### 功能描述
- 输入：原始图像 + Bounding Box坐标
- 输出：精确的分割掩码图像

### 应用场景
- 手术器械精确定位
- 解剖结构标注
- 手术区域高亮显示

### API接口

```
POST /sam3
{
    "image_input_path": "/path/to/image.jpg",
    "bboxes": [
        {"x1": 100, "y1": 100, "x2": 200, "y2": 200, "label": "grasper"}
    ],
    "output_dir": "/path/to/output"
}
```

---

## 3.4 语音识别 (ASR-FunASR)

### 功能模式

| 模式 | 说明 |
|------|------|
| 普通模式 | 直接语音转文本 |
| 唤醒词模式 | 持续监控，唤醒后激活 |

### 唤醒词机制

```
┌────────────┐    唤醒词触发    ┌────────────┐
│  监控模式  │ ─────────────> │  激活模式  │
│ (低功耗)   │                │  (录音)    │
└────────────┘ <───────────── └────────────┘
                 静默超时返回
```

### 默认唤醒词
- "你好小助"
- "小助小助"
- "开始识别"

---

## 3.5 语音合成 (TTS-CosyVoice)

### 功能描述
- 将文本转换为自然语音
- 支持中文女声输出

### 应用场景
- 分析结果语音播报
- 对话响应语音输出

### API接口

```
POST /inference_sft
{
    "tts_text": "当前手术阶段为胆囊分离",
    "spk_id": "中文女"
}
```

---

## 3.6 智能对话服务

### 功能描述
- 整合ASR语音输入
- 结合MySQL历史分析记录
- 调用GLM生成上下文相关回答
- TTS输出语音响应

### 对话示例

```
用户: "刚才做了什么操作？"

系统查询MySQL获取最近分析记录:
- 时间: 120.5秒, 阶段: 胆囊分离, 动作: 使用电钩分离胆囊床
- 时间: 115.0秒, 阶段: 胆囊分离, 动作: 牵拉胆囊暴露解剖层面

GLM响应: "根据最近的手术记录，术者正在进行胆囊分离阶段。
         主要操作包括使用电钩分离胆囊床，并通过牵拉胆囊
         暴露解剖层面，确保安全的解剖平面。"
```

---

# 4. 技术实现方案

## 4.1 后端架构

### 技术选型

| 组件 | 技术 | 说明 |
|------|------|------|
| Web框架 | FastAPI | 高性能异步框架 |
| ORM | SQLAlchemy | 数据库抽象层 |
| HTTP客户端 | httpx | 异步HTTP请求 |
| WebSocket | websockets | 实时双向通信 |

### 目录结构

```
backend/
├── main.py              # 应用入口
├── config.py            # 配置管理
├── database.py          # 数据库连接
├── routers/             # API路由
│   ├── video.py         # 视频管理
│   ├── analysis.py      # 分析处理
│   ├── voice.py         # 语音交互
│   └── webrtc.py        # WebRTC流
└── services/            # 业务服务
    ├── surgr1_client.py # SurgR1客户端
    ├── glm_client.py    # GLM客户端
    ├── sam3_client.py   # SAM3客户端
    ├── asr_funasr_client.py    # ASR客户端
    ├── tts_cosyvoice_client.py # TTS客户端
    ├── mysql_service.py        # MySQL服务
    └── conversation_service.py # 对话服务
```

---

## 4.2 数据库设计

### MySQL表结构

#### surgr1_analysis (SurgR1分析结果表)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT | 主键 |
| session_id | VARCHAR(64) | 会话标识 |
| image_path | VARCHAR(512) | 图像路径 |
| frame_idx | INT | 帧索引 |
| timestamp | FLOAT | 视频时间戳 |
| tool_localization | TEXT | 工具定位结果 |
| surgical_action | TEXT | 手术动作描述 |
| surgical_phase | TEXT | 手术阶段识别 |
| created_at | DATETIME | 创建时间 |
| processing_time | FLOAT | 处理耗时 |

#### glm_summary (GLM总结表)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT | 主键 |
| session_id | VARCHAR(64) | 会话标识 |
| window_id | INT | 时间窗口ID |
| start_time | FLOAT | 窗口起始时间 |
| end_time | FLOAT | 窗口结束时间 |
| summary_text | TEXT | 总结文本 |
| summary_type | VARCHAR(32) | 类型(window/conversation) |
| user_query | TEXT | 用户问题(对话时) |
| thinking_disabled | BOOLEAN | 是否禁用思考模式 |
| created_at | DATETIME | 创建时间 |

#### conversation_history (对话历史表)

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT | 主键 |
| session_id | VARCHAR(64) | 会话标识 |
| role | VARCHAR(16) | 角色(user/assistant) |
| content | TEXT | 消息内容 |
| has_audio | BOOLEAN | 是否有音频 |
| audio_duration | FLOAT | 音频时长 |
| created_at | DATETIME | 创建时间 |

---

## 4.3 API接口设计

### 视频分析接口

```
POST /api/analysis/analyze-window-vlm
{
    "session_id": "session_001",
    "window_id": 1,
    "use_glm": true
}

Response:
{
    "window_id": 1,
    "frame_count": 5,
    "frame_analyses": [...],
    "summary": "在该时间窗口内，术者使用抓钳牵拉胆囊...",
    "model": "SurgR1 + GLM-4.6V-Flash"
}
```

### 语音交互接口

```
WebSocket /api/voice/ws/conversation

Client -> Server:
{"action": "start", "session_id": "session_001"}
{"action": "audio", "audio_data": "base64..."}

Server -> Client:
{"type": "mode_change", "mode": "monitoring"}
{"type": "wakeword_detected", "keyword": "你好小助"}
{"type": "transcript", "text": "刚才做了什么", "is_final": true}
{"type": "response", "text": "根据记录...", "audio_base64": "..."}
```

---

# 5. 数据流设计

## 5.1 视频分析数据流

```
┌─────────────────────────────────────────────────────────────────┐
│                        视频分析流程                              │
└─────────────────────────────────────────────────────────────────┘

    [视频源]                          [分析服务]
        │                                 │
        v                                 v
   ┌─────────┐                      ┌─────────┐
   │ WebRTC  │ ──── 视频帧 ───────> │ SurgR1  │
   │ 流输入  │                      │ 分析    │
   └─────────┘                      └────┬────┘
                                         │
                                    分析结果
                                         │
                            ┌────────────┼────────────┐
                            v            v            v
                       ┌────────┐  ┌────────┐  ┌────────┐
                       │ MySQL  │  │  GLM   │  │  SAM3  │
                       │ 存储   │  │  整合  │  │  分割  │
                       └────────┘  └────┬───┘  └────────┘
                                        │
                                   总结文本
                                        │
                                        v
                                   ┌────────┐
                                   │ 前端   │
                                   │ 展示   │
                                   └────────┘
```

## 5.2 语音交互数据流

```
┌─────────────────────────────────────────────────────────────────┐
│                        语音交互流程                              │
└─────────────────────────────────────────────────────────────────┘

状态机:

    ┌──────────────────────────────────────────────────────────┐
    │                                                          │
    v                                                          │
┌────────┐  唤醒词   ┌────────┐  语音输入  ┌────────┐          │
│ 监控   │ ───────> │ 激活   │ ─────────> │ 处理   │          │
│ 模式   │          │ 模式   │            │ 模式   │          │
└────────┘          └────────┘            └───┬────┘          │
    ^                   ^                     │               │
    │                   │                     v               │
    │                   │               ┌────────┐            │
    │                   └───────────────│ 响应   │────────────┘
    │                     继续对话      │ 输出   │  静默超时
    │                                   └────────┘
    │                                        │
    └────────────────────────────────────────┘
                   待机超时
```

---

# 6. 接口规范

## 6.1 服务端口分配

| 服务名称 | 端口 | 协议 |
|----------|------|------|
| 前端开发服务 | 5176 | HTTP |
| 后端API服务 | 8001 | HTTP/WS |
| GLM模型服务 | 8000 | HTTP |
| SurgR1模型服务 | 9003 | HTTP |
| SAM3分割服务 | 9004 | HTTP |
| TTS语音合成 | 50000 | HTTP |
| ASR语音识别 | 8765 | HTTP/WS |
| RTC模拟器 | 9001/9002 | HTTP/WebRTC |
| MySQL数据库 | 3306 | TCP |

## 6.2 错误码规范

| 错误码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |
| 503 | 服务不可用 |

---

# 7. 部署方案

## 7.1 环境要求

### 硬件要求

| 组件 | 最低配置 | 推荐配置 |
|------|----------|----------|
| CPU | 8核 | 16核+ |
| 内存 | 32GB | 64GB+ |
| GPU | RTX 3090 | A100 40GB |
| 存储 | 100GB SSD | 500GB NVMe |

### 软件要求

| 组件 | 版本 |
|------|------|
| Python | 3.10+ |
| Node.js | 18+ |
| MySQL | 8.0+ |
| CUDA | 11.8+ |

## 7.2 启动顺序

```
1. 启动MySQL数据库
   $ systemctl start mysql

2. 启动AI模型服务
   $ ./SurgR1_api/run.sh
   $ ./sam3_api/start.sh
   $ ./tts_api/start.sh
   $ ./asr_api/start.sh
   $ ./glm_api/start.sh

3. 启动后端服务
   $ cd video_stream_app/backend
   $ uvicorn main:app --host 0.0.0.0 --port 8001

4. 启动前端服务
   $ cd video_stream_app/frontend
   $ npm run dev

5. (可选) 启动视频流模拟器
   $ ./rtc_simulator/start_all.sh
```

## 7.3 配置文件

主配置文件: `video_stream_app/config.json`

```json
{
    "services": {
        "backend": {"port": 8001},
        "surgr1": {"port": 9003, "api_url": "http://localhost:9003"},
        "sam3": {"port": 9004, "api_url": "http://localhost:9004"},
        "glm": {"port": 8000, "api_url": "http://localhost:8000/v1"},
        "tts_cosyvoice": {"port": 50000},
        "asr_funasr": {"port": 8765}
    },
    "database": {
        "mysql": {
            "host": "localhost",
            "port": 3306,
            "database": "video_analyzer"
        }
    },
    "video_processing": {
        "window_duration": 5.0,
        "sample_interval": 1.0
    }
}
```

---

# 8. 总结与展望

## 8.1 项目成果

- 实现了手术视频的实时智能分析
- 集成了多个AI模型形成完整分析链路
- 提供了语音交互能力，解放术者双手
- 建立了完整的数据存储与追溯机制

## 8.2 技术亮点

| 特性 | 说明 |
|------|------|
| 微服务架构 | 各AI服务独立部署，便于扩展维护 |
| 异步处理 | 全链路异步，提升并发处理能力 |
| 实时通信 | WebSocket支持实时语音交互 |
| 智能加速 | GLM禁用思考模式，响应更快 |

## 8.3 后续规划

1. **性能优化**
   - 模型量化加速
   - 批处理优化

2. **功能扩展**
   - 多语言支持
   - 更多手术类型适配

3. **系统增强**
   - 分布式部署支持
   - 容器化改造

---

# 谢谢

如有问题，欢迎讨论



