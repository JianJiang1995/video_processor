# Offline Video Analysis Pipeline

离线视频分析流水线，按 `video_stream_app/docs/offline_pipeline_handoff.md` 实现。
独立于实时流 `video_stream_app`，直接部署在项目根目录。

## 架构

```
mp4 → Frame Extract → [YOLO26s | Phase-ResNet18 | LAM-Lite] → Window Aggregate → Gemini Batch API → MySQL + Embeddings
```

## 目录

```
offline_pipeline/
├── config.json                # 模型路径 / 采样率 / Gemini 配置
├── data_models.py             # FrameExpertResult, WindowContext dataclass
├── services/
│   ├── yolo_expert.py         # YOLO26s 工具检测 (5ms/frame)
│   ├── phase_expert.py        # ResNet-18 + Gaussian σ=3 非因果平滑 (98.5%)
│   ├── triplet_expert.py      # LAM-Lite 10 帧 clip 三元组 (mAP-IVT=0.838)
│   └── gemini_batch_client.py # Gemini Batch API (50% off, 24h SLA)
├── pipelines/
│   └── offline_pipeline.py    # 6 阶段主调度
├── routers/
│   └── offline.py             # FastAPI: /api/offline/{process-video,status,result}
├── scripts/
│   └── run_offline.py         # CLI 入口
└── jobs/                      # 运行时 job 状态 / batch jsonl / 结果
```

## 快速开始

```bash
# 1) 环境依赖
pip install google-genai ultralytics timm scipy opencv-python-headless tqdm

# 2) 配置
cp config.json.example config.json  # 编辑 GEMINI_API_KEY 和模型路径

# 3) 跑 CLI
python -m offline_pipeline.scripts.run_offline \
  --video /data2/jj/proj/video_processor/stream_simulator/media/sample.mp4 \
  --sample-fps 10 --window 15

# 4) 起 API（可选）
uvicorn offline_pipeline.routers.offline:app --port 8100
```

## P0/P1/P2

参见 handoff 文档 §9。当前骨架覆盖 P0，TODO 标注待实现项。
